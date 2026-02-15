"""Analytics service functions for campaign finance insights."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Optional


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


_MONTH_KEY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(value: str | None):
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%y", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_month_key(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if _MONTH_KEY_RE.fullmatch(text):
        return text
    if len(text) >= 10 and _ISO_DATE_RE.fullmatch(text[:10]):
        parsed = _parse_date(text[:10])
    else:
        parsed = _parse_date(text)
    if not parsed:
        return ""
    return parsed.strftime("%Y-%m")


def _normalize_date_iso(value: str | None) -> str | None:
    parsed = _parse_date(value)
    if not parsed:
        return None
    return parsed.strftime("%Y-%m-%d")


def _month_bounds(month_key: str) -> tuple[date, date]:
    start = datetime.strptime(month_key, "%Y-%m").date()
    last_day = calendar.monthrange(start.year, start.month)[1]
    return start, date(start.year, start.month, last_day)


def _normalize_date_range(date_from: str | None, date_to: str | None) -> tuple[date | None, date | None]:
    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if start and end and start > end:
        start, end = end, start
    return start, end


def _date_value_overlaps_range(value: str | None, start: date | None, end: date | None) -> bool:
    if start is None and end is None:
        return True

    text = (value or "").strip()
    if not text:
        return False

    if _MONTH_KEY_RE.fullmatch(text):
        value_start, value_end = _month_bounds(text)
    else:
        parsed = _parse_date(text)
        if parsed:
            value_start = parsed
            value_end = parsed
        else:
            month_key = _normalize_month_key(text)
            if not month_key:
                return False
            value_start, value_end = _month_bounds(month_key)

    if start is not None and value_end < start:
        return False
    if end is not None and value_start > end:
        return False
    return True


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


_EDGE_TYPE_SEMANTICS: dict[str, dict[str, str]] = {
    "donor_committee": {
        "label": "Donor -> committee contributions",
        "description": "Total itemized contributions from a donor into a committee.",
        "weight_unit": "usd",
        "metric_label": "Contribution amount (USD)",
    },
    "committee_candidate": {
        "label": "Committee receipts linked to candidate",
        "description": "Committee-level reported receipts tied to a candidate via committee linkage (not a direct transfer edge).",
        "weight_unit": "usd",
        "metric_label": "Linked committee receipts (USD)",
        "caveat": "This edge reflects committee fundraising totals associated with the candidate, not direct committee-to-candidate payments.",
    },
    "committee_vendor": {
        "label": "Committee -> vendor spending",
        "description": "Committee expenditures paid to a vendor/payee.",
        "weight_unit": "usd",
        "metric_label": "Expenditure amount (USD)",
    },
    "donor_local": {
        "label": "Donor -> state committee",
        "description": "Matched donor contributions into Illinois state-level committees.",
        "weight_unit": "usd",
        "metric_label": "Contribution amount (USD)",
    },
    "donor_federal": {
        "label": "Donor -> federal committee",
        "description": "Matched donor contributions into federal committees (FEC).",
        "weight_unit": "usd",
        "metric_label": "Contribution amount (USD)",
    },
    "client_entity": {
        "label": "Lobbying client -> entity relationship",
        "description": "Registered client-to-entity association from lobbying registrations.",
        "weight_unit": "count",
        "metric_label": "Registration linkage count",
    },
    "client_donor_match": {
        "label": "Lobbying client -> matched donor",
        "description": "Name-matching relationship between lobbying client and donor identity.",
        "weight_unit": "score",
        "metric_label": "Name-match confidence score",
    },
    "client_payee_match": {
        "label": "Lobbying client -> matched payee",
        "description": "Name-matching relationship between lobbying client and campaign payee/vendor.",
        "weight_unit": "score",
        "metric_label": "Name-match confidence score",
    },
    "entity_payee_match": {
        "label": "Lobbying entity -> matched payee",
        "description": "Name-matching relationship between lobbying entity and campaign payee/vendor.",
        "weight_unit": "score",
        "metric_label": "Name-match confidence score",
    },
    "payee_committee_match": {
        "label": "Payee -> committee spending link",
        "description": "Observed committee spending to a payee matched to lobbying data.",
        "weight_unit": "usd",
        "metric_label": "Matched committee spend (USD)",
    },
    "donor_committee_flow": {
        "label": "Matched donor -> committee flow",
        "description": "Observed donor contribution flow for donor identities matched from external datasets.",
        "weight_unit": "usd",
        "metric_label": "Contribution amount (USD)",
    },
    "org_committee_match": {
        "label": "527 organization -> committee match",
        "description": "Name-match relationship between IRS 527 organization and Illinois committee.",
        "weight_unit": "score",
        "metric_label": "Name-match confidence score",
    },
    "org_recipient_match": {
        "label": "527 organization -> recipient match",
        "description": "Matched 527 expenditure recipients to campaign-finance entities or targets.",
        "weight_unit": "usd",
        "metric_label": "Matched 527 expenditure amount (USD)",
    },
    "org_director": {
        "label": "527 organization -> director",
        "description": "Director listed on IRS 527 filing linked to the organization.",
        "weight_unit": "score",
        "metric_label": "Linkage score",
    },
    "director_donor_match": {
        "label": "Director -> matched donor",
        "description": "Name-matching relationship between a 527 director and donor identity.",
        "weight_unit": "score",
        "metric_label": "Name-match confidence score",
    },
}


def _edge_semantics(
    edge_type: str,
    *,
    weight_unit: str | None = None,
    metric_label: str | None = None,
    source_table: str | None = None,
    caveat: str | None = None,
) -> dict[str, str]:
    base = _EDGE_TYPE_SEMANTICS.get(edge_type, {})
    payload = {
        "edge_label": base.get("label", edge_type.replace("_", " ").title()),
        "edge_description": base.get("description", "Graph relationship edge."),
        "weight_unit": weight_unit or base.get("weight_unit", "value"),
        "metric_label": metric_label or base.get("metric_label", "Graph weight"),
        "source_table": source_table,
        "edge_caveat": caveat or base.get("caveat"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int((len(sorted_vals) - 1) * p)))
    return float(sorted_vals[idx])


def _percentile_rank(values: list[float], value: float) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    count = 0
    for item in sorted_vals:
        if item <= value:
            count += 1
        else:
            break
    return round((count / len(sorted_vals)) * 100.0, 2)


def _amount_distribution_markers(conn: sqlite3.Connection, source: str) -> dict:
    if source == "bulk_receipts" and _table_exists(conn, "bulk_receipts_clean"):
        row = conn.execute(
            f"""
            WITH ordered AS (
                SELECT
                    r.amount AS amount,
                    ROW_NUMBER() OVER (ORDER BY r.amount) AS rn,
                    COUNT(*) OVER () AS cnt
                FROM bulk_receipts_clean r
                WHERE COALESCE(r.amount, 0) > 0
                  AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
            )
            SELECT
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.50) AS INTEGER) + 1 THEN amount END) AS p50,
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.90) AS INTEGER) + 1 THEN amount END) AS p90,
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.95) AS INTEGER) + 1 THEN amount END) AS p95,
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.99) AS INTEGER) + 1 THEN amount END) AS p99,
                MAX(amount) AS max_amount,
                MAX(cnt) AS count_rows
            FROM ordered
            """
        ).fetchone()
    else:
        row = conn.execute(
            """
            WITH ordered AS (
                SELECT
                    amount,
                    ROW_NUMBER() OVER (ORDER BY amount) AS rn,
                    COUNT(*) OVER () AS cnt
                FROM contributions
                WHERE COALESCE(amount, 0) > 0
            )
            SELECT
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.50) AS INTEGER) + 1 THEN amount END) AS p50,
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.90) AS INTEGER) + 1 THEN amount END) AS p90,
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.95) AS INTEGER) + 1 THEN amount END) AS p95,
                MAX(CASE WHEN rn = CAST(((cnt - 1) * 0.99) AS INTEGER) + 1 THEN amount END) AS p99,
                MAX(amount) AS max_amount,
                MAX(cnt) AS count_rows
            FROM ordered
            """
        ).fetchone()

    if not row:
        return {
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max_amount": 0.0,
            "count_rows": 0,
        }

    return {
        "p50": float(row["p50"] or 0.0),
        "p90": float(row["p90"] or 0.0),
        "p95": float(row["p95"] or 0.0),
        "p99": float(row["p99"] or 0.0),
        "max_amount": float(row["max_amount"] or 0.0),
        "count_rows": int(row["count_rows"] or 0),
    }


def _estimate_amount_percentile(value: float, markers: dict) -> float | None:
    if not markers or markers.get("count_rows", 0) <= 0:
        return None
    p99 = float(markers.get("p99") or 0.0)
    p95 = float(markers.get("p95") or 0.0)
    p90 = float(markers.get("p90") or 0.0)
    p50 = float(markers.get("p50") or 0.0)
    if value >= p99 and p99 > 0:
        return 99.0
    if value >= p95 and p95 > 0:
        return 95.0
    if value >= p90 and p90 > 0:
        return 90.0
    if value >= p50 and p50 > 0:
        return 50.0
    return 10.0


def _gini(values: list[float]) -> float:
    if not values:
        return 0.0
    total = sum(values)
    if total <= 0:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    numerator = 0.0
    for i, value in enumerate(sorted_vals, start=1):
        numerator += i * value
    return float((2 * numerator) / (n * total) - (n + 1) / n)


def _extract_city_state(address: str | None) -> tuple[Optional[str], Optional[str]]:
    if not address:
        return None, None
    text = " ".join(address.strip().split())
    if not text:
        return None, None

    m = re.search(r",\s*([^,]+)\s*,\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    m = re.search(r",\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?$", text)
    if m:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        city = parts[-2] if len(parts) >= 2 else None
        return city, m.group(1).strip()

    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 2:
        tail_tokens = parts[-1].split()
        if tail_tokens and len(tail_tokens[0]) == 2 and tail_tokens[0].isalpha():
            return parts[-2], tail_tokens[0].upper()

    return None, None


def _city_key(city: str | None) -> str:
    if not city:
        return ""
    normalized = re.sub(r"[^a-z0-9 ]+", "", city.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _city_display(city: str | None) -> str:
    city_key = _city_key(city)
    if not city_key:
        return ""
    return " ".join(part.capitalize() for part in city_key.split())


_COLLAR_CITIES = {
    _city_key(name)
    for name in [
        "Aurora",
        "Naperville",
        "Joliet",
        "Elgin",
        "Waukegan",
        "Evanston",
        "Schaumburg",
        "Arlington Heights",
        "Bolingbrook",
        "Palatine",
        "Skokie",
        "Des Plaines",
        "Orland Park",
        "Tinley Park",
        "Oak Park",
    ]
}

_CENTRAL_CITIES = {
    _city_key(name)
    for name in [
        "Springfield",
        "Peoria",
        "Bloomington",
        "Normal",
        "Champaign",
        "Urbana",
        "Decatur",
        "Danville",
    ]
}

_SOUTHERN_CITIES = {
    _city_key(name)
    for name in [
        "Carbondale",
        "Belleville",
        "East St Louis",
        "Edwardsville",
        "Marion",
        "Alton",
        "Granite City",
    ]
}


def _classify_region(city: str | None, state: str | None) -> str:
    state_norm = (state or "").strip().upper()
    city_norm = _city_key(city)
    if not state_norm:
        return "Unknown"
    if state_norm != "IL":
        return "Out of State"
    if city_norm == _city_key("Chicago"):
        return "Chicago Metro"
    if city_norm in _COLLAR_CITIES:
        return "Collar Counties"
    if city_norm in _CENTRAL_CITIES:
        return "Central Illinois"
    if city_norm in _SOUTHERN_CITIES:
        return "Southern Illinois"
    return "Other Illinois"


NLP_CATEGORY_KEYWORDS = {
    "media_advertising": [
        "advertis",
        "media",
        "digital",
        "facebook",
        "google",
        "youtube",
        "radio",
        "television",
        "tv",
        "mail",
        "mailer",
        "billboard",
    ],
    "consulting_professional": [
        "consult",
        "strateg",
        "advis",
        "poll",
        "research",
        "analytics",
        "data",
        "professional service",
    ],
    "legal_compliance": [
        "legal",
        "attorney",
        "law",
        "compliance",
        "filing",
        "accounting",
        "audit",
        "treasurer",
    ],
    "travel_events": [
        "travel",
        "hotel",
        "flight",
        "airfare",
        "uber",
        "lyft",
        "mileage",
        "event",
        "venue",
        "catering",
    ],
    "payroll_staff": [
        "salary",
        "payroll",
        "wage",
        "stipend",
        "staff",
        "contract labor",
        "compensation",
    ],
    "fundraising": [
        "fundraising",
        "fundraiser",
        "donation platform",
        "ticket",
        "actblue",
    ],
    "printing_postage": [
        "print",
        "printing",
        "postage",
        "brochure",
        "flyer",
    ],
    "office_admin": [
        "rent",
        "office",
        "supplies",
        "phone",
        "internet",
        "utilities",
        "software",
        "license",
        "subscription",
    ],
}


def categorize_spending_text(text: str | None) -> str:
    """Categorize spending description text with simple keyword NLP."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return "uncategorized"

    for category, keywords in NLP_CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in normalized:
                return category
    return "uncategorized"


BULK_RECEIPTS_MATERIALIZATION_VERSION = 2
CONTRIBUTIONS_MATERIALIZATION_VERSION = 1

_BULK_DONOR_RECEIPT_FILTER_SQL = (
    "COALESCE(r.is_archived, 0) = 0 AND "
    "COALESCE(r.d2_part_code, '') LIKE '1%' AND "
    "EXISTS ("
    "SELECT 1 "
    "FROM bulk_d2_totals_clean d2 "
    "WHERE d2.filed_doc_id = r.filed_doc_id "
    "  AND COALESCE(d2.is_archived, 0) = 0"
    ")"
)

_MATERIALIZED_SOURCE_TABLES = {
    "analytics_donor_committee_agg",
    "analytics_committee_monthly_totals",
    "analytics_large_contributions",
    "analytics_donor_summary",
}


def _has_bulk_receipts_donor_data(conn: sqlite3.Connection) -> bool:
    required = ["bulk_receipts_clean", "bulk_committees_clean", "bulk_d2_totals_clean"]
    if not all(_table_exists(conn, table_name) for table_name in required):
        return False
    row = conn.execute(
        f"""
        SELECT 1
        FROM bulk_receipts_clean r
        WHERE COALESCE(r.amount, 0) > 0
          AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _donor_flow_source(conn: sqlite3.Connection) -> str:
    return "bulk_receipts" if _has_bulk_receipts_donor_data(conn) else "contributions"


def _build_bulk_donor_name(first_name: str | None, last_or_business_name: str | None) -> str:
    first = (first_name or "").strip()
    last = (last_or_business_name or "").strip()
    if first and last:
        return f"{first} {last}".strip()
    return first or last or "Unknown Donor"


def _build_bulk_address(
    address_line_1: str | None,
    address_line_2: str | None,
    city: str | None,
    state: str | None,
    postal_code: str | None,
) -> str:
    parts = [
        (address_line_1 or "").strip(),
        (address_line_2 or "").strip(),
        (city or "").strip(),
        (state or "").strip(),
        (postal_code or "").strip(),
    ]
    return ", ".join(part for part in parts if part)


def _stable_entity_key(*values: str | None) -> str:
    normalized = "|".join(_normalize_name(value) for value in values)
    if not normalized:
        return "unknown"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _materialization_version_for_source(source: str) -> int:
    if source == "bulk_receipts":
        return BULK_RECEIPTS_MATERIALIZATION_VERSION
    return CONTRIBUTIONS_MATERIALIZATION_VERSION


def _materialized_source_is_current(conn: sqlite3.Connection, source: str) -> bool:
    if not _table_exists(conn, "analytics_materialized_meta"):
        return False
    # Legacy DBs without version tracking are treated as stale for bulk rows.
    if not _column_exists(conn, "analytics_materialized_meta", "materialization_version"):
        return source != "bulk_receipts"
    row = conn.execute(
        """
        SELECT materialization_version
        FROM analytics_materialized_meta
        WHERE source = ?
        LIMIT 1
        """,
        (source,),
    ).fetchone()
    if not row:
        return False
    try:
        version = int(row["materialization_version"] or 0)
    except (TypeError, ValueError):
        return False
    return version >= _materialization_version_for_source(source)


def _rows_for_source_exist(conn: sqlite3.Connection, table_name: str, source: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    if table_name in _MATERIALIZED_SOURCE_TABLES and not _materialized_source_is_current(conn, source):
        return False
    row = conn.execute(
        f"SELECT 1 FROM {table_name} WHERE source = ? LIMIT 1",
        (source,),
    ).fetchone()
    return row is not None


def _get_donor_committee_rows(
    conn: sqlite3.Connection,
    min_edge_amount: float = 0.0,
    limit: Optional[int] = None,
) -> tuple[list[dict], str]:
    source = _donor_flow_source(conn)
    min_edge_amount = float(min_edge_amount or 0.0)

    if _rows_for_source_exist(conn, "analytics_donor_committee_agg", source):
        query = """
            SELECT
                donor_key,
                donor_name,
                donor_address,
                donor_city,
                donor_state,
                committee_id,
                committee_name,
                total_amount,
                contribution_count
            FROM analytics_donor_committee_agg
            WHERE source = ?
              AND total_amount >= ?
            ORDER BY total_amount DESC
        """
        params: list[object] = [source, min_edge_amount]
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))

        rows = conn.execute(query, params).fetchall()
        output = []
        for row in rows:
            committee_id = row["committee_id"]
            if isinstance(committee_id, str) and committee_id.isdigit():
                committee_id = int(committee_id)
            output.append(
                {
                    "donor_key": row["donor_key"],
                    "donor_name": row["donor_name"] or "Unknown Donor",
                    "donor_address": row["donor_address"] or "",
                    "donor_city": row["donor_city"],
                    "donor_state": row["donor_state"],
                    "committee_id": committee_id,
                    "committee_name": row["committee_name"] or "Unknown Committee",
                    "total_amount": float(row["total_amount"] or 0.0),
                    "contribution_count": int(row["contribution_count"] or 0),
                }
            )
        return output, source

    if source == "bulk_receipts":
        query = f"""
            SELECT
                r.first_name AS first_name,
                r.last_or_business_name AS last_or_business_name,
                r.address_line_1 AS address_line_1,
                r.address_line_2 AS address_line_2,
                r.city AS donor_city,
                r.state AS donor_state,
                r.postal_code AS postal_code,
                COALESCE(c.committee_id_sbe, r.committee_id_sbe) AS committee_id,
                COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe) AS committee_name,
                COALESCE(SUM(r.amount), 0) AS total_amount,
                COUNT(*) AS contribution_count
            FROM bulk_receipts_clean r
            LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe
            WHERE COALESCE(r.amount, 0) > 0
              AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
            GROUP BY
                r.first_name,
                r.last_or_business_name,
                r.address_line_1,
                r.address_line_2,
                r.city,
                r.state,
                r.postal_code,
                COALESCE(c.committee_id_sbe, r.committee_id_sbe),
                COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe)
            HAVING COALESCE(SUM(r.amount), 0) >= ?
            ORDER BY total_amount DESC
        """
        params: list[object] = [min_edge_amount]
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))

        rows = conn.execute(query, params).fetchall()
        output: list[dict] = []
        for row in rows:
            donor_name = _build_bulk_donor_name(row["first_name"], row["last_or_business_name"])
            donor_address = _build_bulk_address(
                row["address_line_1"],
                row["address_line_2"],
                row["donor_city"],
                row["donor_state"],
                row["postal_code"],
            )
            donor_key = _stable_entity_key(donor_name, donor_address)
            committee_name = row["committee_name"] or "Unknown Committee"
            committee_id = row["committee_id"]
            if committee_id is None:
                committee_id = _stable_entity_key(committee_name)

            output.append(
                {
                    "donor_key": donor_key,
                    "donor_name": donor_name,
                    "donor_address": donor_address,
                    "donor_city": row["donor_city"],
                    "donor_state": row["donor_state"],
                    "committee_id": committee_id,
                    "committee_name": committee_name,
                    "total_amount": float(row["total_amount"] or 0.0),
                    "contribution_count": int(row["contribution_count"] or 0),
                }
            )
        return output, source

    query = """
        SELECT
            d.id AS donor_id,
            d.name AS donor_name,
            d.address AS donor_address,
            c.id AS committee_id,
            c.name AS committee_name,
            COALESCE(SUM(ct.amount), 0) AS total_amount,
            COUNT(ct.id) AS contribution_count
        FROM contributions ct
        JOIN donors d ON d.id = ct.donor_id
        JOIN reports r ON r.id = ct.report_id
        JOIN committees c ON c.id = r.committee_id
        GROUP BY d.id, c.id
        HAVING COALESCE(SUM(ct.amount), 0) >= ?
        ORDER BY total_amount DESC
    """
    params = [min_edge_amount]
    if limit is not None:
        query += " LIMIT ?"
        params.append(int(limit))

    rows = conn.execute(query, params).fetchall()
    output = []
    for row in rows:
        output.append(
            {
                "donor_key": f"donor:{row['donor_id']}",
                "donor_name": row["donor_name"] or f"Donor {row['donor_id']}",
                "donor_address": row["donor_address"] or "",
                "donor_city": None,
                "donor_state": None,
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"] or f"Committee {row['committee_id']}",
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
            }
        )
    return output, source


def _committee_donor_provenance(
    conn: sqlite3.Connection,
    committee_ids: set[str],
    source: str,
    fallback_rows: list[dict],
    top_n: int = 5,
) -> dict[str, dict]:
    normalized_ids = sorted({str(cid).strip() for cid in committee_ids if str(cid or "").strip()})
    if not normalized_ids:
        return {}

    top_n = max(1, min(int(top_n), 10))
    totals_map: dict[str, float] = {}
    donor_count_map: dict[str, int] = {}
    top_donor_map: dict[str, list[dict]] = defaultdict(list)

    if _rows_for_source_exist(conn, "analytics_donor_committee_agg", source):
        placeholders = ",".join(["?"] * len(normalized_ids))
        totals_rows = conn.execute(
            f"""
            SELECT
                committee_id,
                COALESCE(SUM(total_amount), 0) AS committee_total,
                COUNT(*) AS donor_count
            FROM analytics_donor_committee_agg
            WHERE source = ?
              AND committee_id IN ({placeholders})
            GROUP BY committee_id
            """,
            [source, *normalized_ids],
        ).fetchall()
        for row in totals_rows:
            committee_id = str(row["committee_id"])
            totals_map[committee_id] = float(row["committee_total"] or 0.0)
            donor_count_map[committee_id] = int(row["donor_count"] or 0)

        ranked_rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT
                    committee_id,
                    donor_key,
                    donor_name,
                    total_amount,
                    ROW_NUMBER() OVER (
                        PARTITION BY committee_id
                        ORDER BY total_amount DESC, donor_name ASC
                    ) AS rn
                FROM analytics_donor_committee_agg
                WHERE source = ?
                  AND committee_id IN ({placeholders})
            )
            SELECT committee_id, donor_key, donor_name, total_amount
            FROM ranked
            WHERE rn <= ?
            ORDER BY committee_id, total_amount DESC
            """,
            [source, *normalized_ids, top_n],
        ).fetchall()
        for row in ranked_rows:
            committee_id = str(row["committee_id"])
            top_donor_map[committee_id].append(
                {
                    "donor_key": row["donor_key"],
                    "donor_name": row["donor_name"] or row["donor_key"] or "Unknown Donor",
                    "total_amount": float(row["total_amount"] or 0.0),
                }
            )
    elif source == "contributions":
        placeholders = ",".join(["?"] * len(normalized_ids))
        totals_rows = conn.execute(
            f"""
            WITH donor_committee AS (
                SELECT
                    CAST(c.id AS TEXT) AS committee_id,
                    d.id AS donor_id,
                    d.name AS donor_name,
                    COALESCE(SUM(ct.amount), 0) AS total_amount
                FROM contributions ct
                JOIN donors d ON d.id = ct.donor_id
                JOIN reports r ON r.id = ct.report_id
                JOIN committees c ON c.id = r.committee_id
                WHERE CAST(c.id AS TEXT) IN ({placeholders})
                GROUP BY c.id, d.id, d.name
            )
            SELECT
                committee_id,
                COALESCE(SUM(total_amount), 0) AS committee_total,
                COUNT(*) AS donor_count
            FROM donor_committee
            GROUP BY committee_id
            """,
            normalized_ids,
        ).fetchall()
        for row in totals_rows:
            committee_id = str(row["committee_id"])
            totals_map[committee_id] = float(row["committee_total"] or 0.0)
            donor_count_map[committee_id] = int(row["donor_count"] or 0)

        ranked_rows = conn.execute(
            f"""
            WITH donor_committee AS (
                SELECT
                    CAST(c.id AS TEXT) AS committee_id,
                    d.id AS donor_id,
                    d.name AS donor_name,
                    COALESCE(SUM(ct.amount), 0) AS total_amount
                FROM contributions ct
                JOIN donors d ON d.id = ct.donor_id
                JOIN reports r ON r.id = ct.report_id
                JOIN committees c ON c.id = r.committee_id
                WHERE CAST(c.id AS TEXT) IN ({placeholders})
                GROUP BY c.id, d.id, d.name
            ),
            ranked AS (
                SELECT
                    committee_id,
                    donor_id,
                    donor_name,
                    total_amount,
                    ROW_NUMBER() OVER (
                        PARTITION BY committee_id
                        ORDER BY total_amount DESC, donor_name ASC
                    ) AS rn
                FROM donor_committee
            )
            SELECT committee_id, donor_id, donor_name, total_amount
            FROM ranked
            WHERE rn <= ?
            ORDER BY committee_id, total_amount DESC
            """,
            [*normalized_ids, top_n],
        ).fetchall()
        for row in ranked_rows:
            committee_id = str(row["committee_id"])
            donor_key = f"donor:{row['donor_id']}"
            top_donor_map[committee_id].append(
                {
                    "donor_key": donor_key,
                    "donor_name": row["donor_name"] or donor_key,
                    "total_amount": float(row["total_amount"] or 0.0),
                }
            )

    fallback_committee_donors: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in fallback_rows:
        committee_id = str(row.get("committee_id") or "").strip()
        if committee_id not in normalized_ids:
            continue
        donor_key = str(row.get("donor_key") or "").strip()
        donor_name = row.get("donor_name") or donor_key or "Unknown Donor"
        amount = float(row.get("total_amount") or 0.0)
        if amount <= 0 or not donor_key:
            continue
        donor_bucket = fallback_committee_donors[committee_id].setdefault(
            donor_key,
            {"donor_key": donor_key, "donor_name": donor_name, "total_amount": 0.0},
        )
        donor_bucket["total_amount"] += amount

    output: dict[str, dict] = {}
    for committee_id in normalized_ids:
        donors = list(top_donor_map.get(committee_id) or [])
        fallback_donors = list(fallback_committee_donors.get(committee_id, {}).values())
        if not donors and fallback_donors:
            fallback_donors.sort(key=lambda row: float(row.get("total_amount") or 0.0), reverse=True)
            donors = fallback_donors[:top_n]

        if not donors:
            continue

        committee_total = float(totals_map.get(committee_id) or 0.0)
        if committee_total <= 0:
            committee_total = sum(float(row.get("total_amount") or 0.0) for row in fallback_donors) or sum(
                float(row.get("total_amount") or 0.0) for row in donors
            )
        donor_count = int(donor_count_map.get(committee_id) or len(fallback_donors) or len(donors))

        normalized_donors: list[dict] = []
        donor_share_sum = 0.0
        for donor in donors:
            donor_amount = float(donor.get("total_amount") or 0.0)
            donor_share = (donor_amount / committee_total) if committee_total > 0 else 0.0
            donor_share_sum += donor_share
            normalized_donors.append(
                {
                    "donor_key": donor.get("donor_key"),
                    "donor_name": donor.get("donor_name") or donor.get("donor_key") or "Unknown Donor",
                    "total_amount": round(donor_amount, 2),
                    "donor_share_of_committee": round(donor_share, 6),
                }
            )

        output[committee_id] = {
            "committee_total_receipts": round(committee_total, 2),
            "donor_count": donor_count,
            "top_donors": normalized_donors,
            "top_donor_share": round(min(1.0, donor_share_sum), 6),
            "source": source,
        }

    return output


def get_donor_committee_rows(
    conn: sqlite3.Connection,
    min_edge_amount: float = 0.0,
    limit: Optional[int] = None,
) -> tuple[list[dict], str]:
    """Get donor->committee aggregate rows and active source."""
    return _get_donor_committee_rows(conn, min_edge_amount=min_edge_amount, limit=limit)


def get_network_graph(
    conn: sqlite3.Connection,
    min_edge_amount: float = 0,
    limit: int = 300,
    donor_committee_rows: Optional[list[dict]] = None,
    donor_source: Optional[str] = None,
) -> dict:
    """Build a donor->committee->candidate weighted graph and centrality scores."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    committee_name_to_node: dict[str, str] = {}
    committee_region_weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    if donor_committee_rows is None:
        donor_committee_rows, donor_source = _get_donor_committee_rows(
            conn,
            min_edge_amount=float(min_edge_amount),
            limit=int(limit),
        )
    else:
        donor_source = donor_source or _donor_flow_source(conn)
        filtered_rows = [
            row
            for row in donor_committee_rows
            if float(row.get("total_amount") or 0.0) >= float(min_edge_amount or 0.0)
        ]
        donor_committee_rows = filtered_rows[: max(1, int(limit))]

    has_materialized_donor_source = bool(
        donor_source and _rows_for_source_exist(conn, "analytics_donor_committee_agg", donor_source)
    )
    if has_materialized_donor_source:
        donor_edge_source_table = "analytics_donor_committee_agg"
    elif donor_source == "bulk_receipts":
        donor_edge_source_table = "bulk_receipts_clean"
    else:
        donor_edge_source_table = "contributions"

    for row in donor_committee_rows:
        donor_city = row.get("donor_city")
        donor_state = row.get("donor_state")
        if not donor_city and not donor_state:
            donor_city, donor_state = _extract_city_state(row.get("donor_address"))

        donor_region = _classify_region(donor_city, donor_state)
        donor_node_id = f"donor:{row['donor_key']}"
        committee_node_id = f"committee:{row['committee_id']}"

        nodes.setdefault(
            donor_node_id,
            {
                "id": donor_node_id,
                "label": row["donor_name"] or "Unknown Donor",
                "node_type": "donor",
                "region": donor_region,
            },
        )
        nodes.setdefault(
            committee_node_id,
            {
                "id": committee_node_id,
                "label": row["committee_name"] or f"Committee {row['committee_id']}",
                "node_type": "committee",
                "region": "Unknown",
            },
        )
        committee_name_to_node[_normalize_name(row["committee_name"])] = committee_node_id

        edge_weight = float(row.get("total_amount") or 0)
        committee_region_weights[committee_node_id][donor_region] += edge_weight
        edge_payload = {
            "source": donor_node_id,
            "target": committee_node_id,
            "edge_type": "donor_committee",
            "weight": edge_weight,
            "count": int(row.get("contribution_count") or 0),
        }
        edge_payload.update(
            _edge_semantics(
                "donor_committee",
                source_table=donor_edge_source_table,
            )
        )
        edges.append(edge_payload)

    if _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        committee_candidate_rows = conn.execute(
            """
            SELECT
                committee_id_sbe AS committee_id,
                committee_name,
                candidate_id,
                candidate_full_name,
                COALESCE(SUM(sum_total_receipts), 0) AS edge_weight,
                COALESCE(SUM(filing_count), 0) AS filing_count
            FROM bulk_candidate_committee_finance_agg
            WHERE committee_name IS NOT NULL
            GROUP BY committee_id_sbe, committee_name, candidate_id, candidate_full_name
            ORDER BY edge_weight DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

        provenance_committee_ids = {
            str(row["committee_id"]).strip()
            for row in committee_candidate_rows
            if str(row["committee_id"] or "").strip()
        }
        committee_provenance = _committee_donor_provenance(
            conn,
            provenance_committee_ids,
            donor_source or _donor_flow_source(conn),
            donor_committee_rows,
            top_n=5,
        )

        for row in committee_candidate_rows:
            normalized_committee = _normalize_name(row["committee_name"])
            committee_node_id = committee_name_to_node.get(normalized_committee)
            if not committee_node_id:
                committee_node_id = f"committee:bulk:{normalized_committee or 'unknown'}"
                nodes.setdefault(
                    committee_node_id,
                    {
                        "id": committee_node_id,
                        "label": row["committee_name"] or "Unknown Committee",
                        "node_type": "committee",
                        "region": "Unknown",
                    },
                )

            candidate_suffix = row["candidate_id"] if row["candidate_id"] is not None else _normalize_name(
                row["candidate_full_name"]
            )
            candidate_node_id = f"candidate:{candidate_suffix}"
            nodes.setdefault(
                candidate_node_id,
                {
                    "id": candidate_node_id,
                    "label": row["candidate_full_name"] or f"Candidate {candidate_suffix}",
                    "node_type": "candidate",
                    "region": "Unknown",
                },
            )

            edge_weight = float(row["edge_weight"] or 0)
            committee_id_key = str(row["committee_id"]).strip() if row["committee_id"] is not None else ""
            provenance = committee_provenance.get(committee_id_key, {})
            committee_total_receipts = float(provenance.get("committee_total_receipts") or 0.0)
            estimated_top_donors: list[dict] = []
            top_share_sum = 0.0
            for donor in provenance.get("top_donors", []):
                donor_share = float(donor.get("donor_share_of_committee") or 0.0)
                top_share_sum += donor_share
                estimated_top_donors.append(
                    {
                        "donor_key": donor.get("donor_key"),
                        "donor_node_id": f"donor:{donor.get('donor_key')}" if donor.get("donor_key") else None,
                        "donor_label": donor.get("donor_name") or donor.get("donor_key") or "Unknown Donor",
                        "donor_amount_to_committee": round(float(donor.get("total_amount") or 0.0), 2),
                        "donor_share_of_committee": round(donor_share, 6),
                        "estimated_amount_to_candidate_receipts": round(edge_weight * donor_share, 2),
                    }
                )

            edge_payload = {
                "source": committee_node_id,
                "target": candidate_node_id,
                "edge_type": "committee_candidate",
                "weight": edge_weight,
                "count": int(row["filing_count"] or 0) or 1,
                "is_direct_transfer": False,
                "disaggregation_method": "proportional_share_of_committee_receipts",
                "disaggregation_confidence": "estimated",
            }
            edge_payload.update(
                _edge_semantics(
                    "committee_candidate",
                    source_table="bulk_candidate_committee_finance_agg",
                )
            )
            if committee_total_receipts > 0:
                edge_payload["committee_total_receipts"] = round(committee_total_receipts, 2)
                edge_payload["committee_donor_count"] = int(provenance.get("donor_count") or 0)
            if estimated_top_donors:
                clipped_share = min(1.0, max(0.0, top_share_sum))
                edge_payload["estimated_top_donors"] = estimated_top_donors
                edge_payload["estimated_top_donor_share"] = round(clipped_share, 6)
                edge_payload["estimated_unattributed_amount"] = round(
                    max(0.0, edge_weight * (1.0 - clipped_share)),
                    2,
                )
            edges.append(edge_payload)

    for node_id, node in nodes.items():
        if node.get("node_type") != "committee":
            continue
        weights = committee_region_weights.get(node_id, {})
        if weights:
            node["region"] = max(weights.items(), key=lambda item: item[1])[0]

    candidate_region_weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for edge in edges:
        if edge["edge_type"] != "committee_candidate":
            continue
        source_region = nodes.get(edge["source"], {}).get("region", "Unknown")
        candidate_region_weights[edge["target"]][source_region] += float(edge["weight"] or 0)

    for node_id, node in nodes.items():
        if node.get("node_type") != "candidate":
            continue
        weights = candidate_region_weights.get(node_id, {})
        if weights:
            node["region"] = max(weights.items(), key=lambda item: item[1])[0]

    weighted_degree: dict[str, float] = defaultdict(float)
    edge_degree: dict[str, int] = defaultdict(int)
    for edge in edges:
        weighted_degree[edge["source"]] += float(edge["weight"] or 0)
        weighted_degree[edge["target"]] += float(edge["weight"] or 0)
        edge_degree[edge["source"]] += 1
        edge_degree[edge["target"]] += 1

    centrality = []
    for node_id, node in nodes.items():
        centrality.append(
            {
                "node_id": node_id,
                "label": node["label"],
                "node_type": node["node_type"],
                "region": node.get("region", "Unknown"),
                "weighted_degree": round(weighted_degree.get(node_id, 0.0), 2),
                "degree": edge_degree.get(node_id, 0),
            }
        )
    centrality.sort(key=lambda x: (x["weighted_degree"], x["degree"]), reverse=True)

    region_counts: dict[str, int] = defaultdict(int)
    for node in nodes.values():
        region_counts[node.get("region", "Unknown")] += 1
    edge_types_present = {edge.get("edge_type", "") for edge in edges if edge.get("edge_type")}
    disaggregated_committee_edges = sum(
        1 for edge in edges if edge.get("edge_type") == "committee_candidate" and edge.get("estimated_top_donors")
    )

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "centrality": centrality[:100],
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "donor_committee_edges": sum(1 for e in edges if e["edge_type"] == "donor_committee"),
            "committee_candidate_edges": sum(1 for e in edges if e["edge_type"] == "committee_candidate"),
            "donor_committee_source": donor_source,
            "region_counts": dict(sorted(region_counts.items(), key=lambda item: item[0])),
            "edge_type_definitions": {
                edge_type: _edge_semantics(edge_type) for edge_type in sorted(edge_types_present)
            },
            "committee_candidate_disaggregation_edges": disaggregated_committee_edges,
        },
    }


def get_donor_concentration(
    conn: sqlite3.Connection,
    limit: int = 50,
    donor_committee_rows: Optional[list[dict]] = None,
) -> list[dict]:
    """Compute donor concentration metrics by committee (HHI, Gini, top shares)."""
    if donor_committee_rows is None:
        rows, _source = _get_donor_committee_rows(conn, min_edge_amount=0.0, limit=None)
    else:
        rows = donor_committee_rows
    per_committee: dict[tuple[object, str], dict] = {}
    for row in rows:
        committee_id = row["committee_id"]
        committee_name = row["committee_name"] or f"Committee {committee_id}"
        committee_key = (committee_id, committee_name)
        entry = per_committee.setdefault(
            committee_key,
            {
                "committee_id": committee_id,
                "committee_name": committee_name,
                "donor_totals": defaultdict(float),
            },
        )
        donor_key = row["donor_key"]
        entry["donor_totals"][donor_key] += float(row["total_amount"] or 0.0)

    output = []
    for committee in per_committee.values():
        donor_amounts = sorted([amount for amount in committee["donor_totals"].values() if amount > 0], reverse=True)
        if not donor_amounts:
            continue
        total_amount = sum(donor_amounts)
        donor_count = len(donor_amounts)
        shares = [amt / total_amount for amt in donor_amounts]
        hhi = sum((share**2) for share in shares) * 10000
        output.append(
            {
                "committee_id": committee["committee_id"],
                "committee_name": committee["committee_name"],
                "total_amount": round(total_amount, 2),
                "donor_count": donor_count,
                "top1_share": round(sum(shares[:1]), 4),
                "top5_share": round(sum(shares[:5]), 4),
                "top10_share": round(sum(shares[:10]), 4),
                "hhi": round(hhi, 2),
                "gini": round(_gini(donor_amounts), 4),
                "top_donor_amount": round(donor_amounts[0], 2),
            }
        )

    output.sort(key=lambda row: (row["hhi"], row["total_amount"]), reverse=True)
    return output[: max(1, int(limit))]


def get_anomaly_flags(
    conn: sqlite3.Connection,
    limit: int = 100,
    precomputed_concentration: Optional[list[dict]] = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Generate anomaly/risk flags from contribution and concentration data."""
    flags: list[dict] = []
    source = _donor_flow_source(conn)
    amount_markers = _amount_distribution_markers(conn, source)
    range_start, range_end = _normalize_date_range(date_from, date_to)
    committee_month_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    use_materialized = (
        _rows_for_source_exist(conn, "analytics_large_contributions", source)
        and _rows_for_source_exist(conn, "analytics_committee_monthly_totals", source)
    )

    if use_materialized:
        threshold_row = conn.execute(
            """
            SELECT large_threshold
            FROM analytics_materialized_meta
            WHERE source = ?
            LIMIT 1
            """,
            (source,),
        ).fetchone()
        large_threshold = float(threshold_row["large_threshold"] or 0.0) if threshold_row else 0.0
        if large_threshold <= 0:
            large_threshold = 5000.0

        large_rows = conn.execute(
            """
            SELECT committee_name, donor_name, event_date, amount, large_threshold
            FROM analytics_large_contributions
            WHERE source = ?
            ORDER BY amount DESC
            LIMIT ?
            """,
            (source, max(200, int(limit) * 5)),
        ).fetchall()

        for row in large_rows:
            amount = float(row["amount"] or 0.0)
            baseline = float(row["large_threshold"] or large_threshold or 0.0)
            if baseline <= 0:
                baseline = large_threshold
            event_date = _normalize_date_iso(row["event_date"]) or _normalize_month_key(row["event_date"]) or None
            percentile = _estimate_amount_percentile(amount, amount_markers)
            flags.append(
                {
                    "flag_type": "large_single_contribution",
                    "severity": round(amount / baseline, 2) if baseline > 0 else 0,
                    "committee_name": row["committee_name"],
                    "donor_name": row["donor_name"] or "Unknown Donor",
                    "event_date": event_date,
                    "value": round(amount, 2),
                    "baseline": round(baseline, 2),
                    "threshold": round(baseline, 2),
                    "percentile": percentile,
                    "details": "Contribution amount exceeds dynamic large-transaction threshold.",
                    "explainability": {
                        "rule": "large_single_contribution",
                        "why_flagged": "Contribution amount is above the dynamic large contribution threshold.",
                        "threshold": round(baseline, 2),
                        "baseline": round(baseline, 2),
                        "percentile": percentile,
                        "distribution_markers": amount_markers,
                    },
                }
            )

        monthly_rows = conn.execute(
            """
            SELECT committee_name, month_key, month_total
            FROM analytics_committee_monthly_totals
            WHERE source = ?
            """,
            (source,),
        ).fetchall()
        for row in monthly_rows:
            month_key = _normalize_month_key(row["month_key"])
            if not month_key or month_key == "0000-00":
                continue
            committee_name = row["committee_name"] or "Unknown Committee"
            committee_month_totals[committee_name][month_key] = float(row["month_total"] or 0.0)

    elif source == "bulk_receipts":
        p95_row = conn.execute(
            f"""
            WITH ordered AS (
                SELECT
                    r.amount AS amount,
                    ROW_NUMBER() OVER (ORDER BY r.amount) AS rn,
                    COUNT(*) OVER () AS cnt
                FROM bulk_receipts_clean r
                WHERE COALESCE(r.amount, 0) > 0
                  AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
            )
            SELECT amount
            FROM ordered
            WHERE rn = CAST(((cnt - 1) * 0.95) AS INTEGER) + 1
            LIMIT 1
            """
        ).fetchone()
        p95 = float(p95_row["amount"] or 0.0) if p95_row else 0.0
        large_threshold = max(5000.0, p95 * 2.0)

        large_rows = conn.execute(
            f"""
            SELECT
                COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe) AS committee_name,
                r.received_date AS event_date,
                r.amount AS amount,
                TRIM(
                    COALESCE(r.first_name, '')
                    || CASE
                        WHEN COALESCE(r.first_name, '') <> '' AND COALESCE(r.last_or_business_name, '') <> ''
                        THEN ' '
                        ELSE ''
                       END
                    || COALESCE(r.last_or_business_name, '')
                ) AS donor_name
            FROM bulk_receipts_clean r
            LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe
            WHERE COALESCE(r.amount, 0) >= ?
              AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
            ORDER BY r.amount DESC
            LIMIT ?
            """,
            (float(large_threshold), max(200, int(limit) * 5)),
        ).fetchall()

        for row in large_rows:
            amount = float(row["amount"] or 0.0)
            event_date = _normalize_date_iso(row["event_date"]) or _normalize_month_key(row["event_date"]) or None
            percentile = _estimate_amount_percentile(amount, amount_markers)
            flags.append(
                {
                    "flag_type": "large_single_contribution",
                    "severity": round(amount / large_threshold, 2) if large_threshold > 0 else 0,
                    "committee_name": row["committee_name"],
                    "donor_name": row["donor_name"] or "Unknown Donor",
                    "event_date": event_date,
                    "value": round(amount, 2),
                    "baseline": round(large_threshold, 2),
                    "threshold": round(large_threshold, 2),
                    "percentile": percentile,
                    "details": "Receipt amount exceeds dynamic large-transaction threshold.",
                    "explainability": {
                        "rule": "large_single_contribution",
                        "why_flagged": "Receipt amount is above the dynamic large contribution threshold.",
                        "threshold": round(large_threshold, 2),
                        "baseline": round(large_threshold, 2),
                        "percentile": percentile,
                        "distribution_markers": amount_markers,
                    },
                }
            )

        monthly_rows = conn.execute(
            f"""
            SELECT
                COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe) AS committee_name,
                SUBSTR(r.received_date, 1, 7) AS month_key,
                COALESCE(SUM(r.amount), 0) AS month_total
            FROM bulk_receipts_clean r
            LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe
            WHERE COALESCE(r.amount, 0) > 0
              AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
              AND r.received_date IS NOT NULL
              AND LENGTH(r.received_date) >= 7
            GROUP BY committee_name, month_key
            """
        ).fetchall()

        for row in monthly_rows:
            month_key = _normalize_month_key(row["month_key"])
            if not month_key or month_key == "0000-00":
                continue
            committee_name = row["committee_name"] or "Unknown Committee"
            committee_month_totals[committee_name][month_key] = float(row["month_total"] or 0.0)

    else:
        rows = conn.execute(
            """
            SELECT
                ct.id AS contribution_id,
                ct.amount AS amount,
                ct.transaction_date AS transaction_date,
                r.filed_date AS filed_date,
                d.name AS donor_name,
                c.name AS committee_name
            FROM contributions ct
            JOIN donors d ON d.id = ct.donor_id
            JOIN reports r ON r.id = ct.report_id
            JOIN committees c ON c.id = r.committee_id
            WHERE ct.amount IS NOT NULL AND ct.amount > 0
            ORDER BY ct.amount DESC
            """
        ).fetchall()

        if not rows:
            return []

        amounts = [float(row["amount"] or 0.0) for row in rows]
        p95 = _percentile(amounts, 0.95)
        large_threshold = max(5000.0, p95 * 2.0)

        for row in rows:
            amount = float(row["amount"] or 0.0)
            if amount < large_threshold:
                break
            event_date = _normalize_date_iso(row["transaction_date"]) or _normalize_date_iso(row["filed_date"])
            percentile = _percentile_rank(amounts, amount)
            flags.append(
                {
                    "flag_type": "large_single_contribution",
                    "severity": round(amount / large_threshold, 2) if large_threshold > 0 else 0,
                    "committee_name": row["committee_name"],
                    "donor_name": row["donor_name"],
                    "event_date": event_date,
                    "value": round(amount, 2),
                    "baseline": round(large_threshold, 2),
                    "threshold": round(large_threshold, 2),
                    "percentile": percentile,
                    "details": "Contribution amount exceeds dynamic large-transaction threshold.",
                    "explainability": {
                        "rule": "large_single_contribution",
                        "why_flagged": "Contribution amount is above the dynamic large contribution threshold.",
                        "threshold": round(large_threshold, 2),
                        "baseline": round(large_threshold, 2),
                        "percentile": percentile,
                        "distribution_markers": amount_markers,
                    },
                }
            )

        for row in rows:
            event_date = _parse_date(row["transaction_date"]) or _parse_date(row["filed_date"])
            if not event_date:
                continue
            month_key = event_date.strftime("%Y-%m")
            committee_name = row["committee_name"] or "Unknown Committee"
            committee_month_totals[committee_name][month_key] += float(row["amount"] or 0.0)

    for committee_name, month_map in committee_month_totals.items():
        months = sorted(month_map.keys())
        if len(months) < 4:
            continue
        monthly_values = [month_map[m] for m in months if month_map[m] > 0]
        for i in range(3, len(months)):
            current_month = months[i]
            current_total = month_map[current_month]
            baseline_months = months[i - 3 : i]
            baseline_values = [month_map[m] for m in baseline_months]
            baseline_avg = sum(baseline_values) / len(baseline_values)
            if baseline_avg <= 0:
                continue
            ratio = current_total / baseline_avg
            if ratio >= 3 and (current_total - baseline_avg) >= 2000:
                percentile = _percentile_rank(monthly_values, current_total)
                flags.append(
                    {
                        "flag_type": "monthly_spike",
                        "severity": round(ratio, 2),
                        "committee_name": committee_name,
                        "donor_name": None,
                        "event_date": current_month,
                        "value": round(current_total, 2),
                        "baseline": round(baseline_avg, 2),
                        "threshold": 3.0,
                        "percentile": percentile,
                        "details": "Monthly receipts are at least 3x trailing 3-month average.",
                        "explainability": {
                            "rule": "monthly_spike",
                            "why_flagged": "Current month exceeded both ratio and absolute delta thresholds.",
                            "threshold": 3.0,
                            "baseline": round(baseline_avg, 2),
                            "percentile": percentile,
                            "ratio": round(ratio, 2),
                            "min_abs_delta": 2000.0,
                            "baseline_months": baseline_months,
                        },
                    }
                )

    concentration = (
        precomputed_concentration
        if precomputed_concentration is not None
        else get_donor_concentration(conn, limit=5000)
    )
    concentration_hhi_values = [float(row["hhi"] or 0.0) for row in concentration if row.get("donor_count", 0) >= 3]
    for row in concentration:
        if row["hhi"] >= 4500 and row["donor_count"] >= 3:
            percentile = _percentile_rank(concentration_hhi_values, float(row["hhi"] or 0.0))
            flags.append(
                {
                    "flag_type": "high_donor_concentration",
                    "severity": round(row["hhi"] / 2500.0, 2),
                    "committee_name": row["committee_name"],
                    "donor_name": None,
                    "event_date": None,
                    "value": row["hhi"],
                    "baseline": 2500.0,
                    "threshold": 4500.0,
                    "percentile": percentile,
                    "details": "Committee donor base is highly concentrated by HHI.",
                    "explainability": {
                        "rule": "high_donor_concentration",
                        "why_flagged": "HHI exceeds high concentration threshold for competitive donor diversity.",
                        "threshold": 4500.0,
                        "baseline": 2500.0,
                        "percentile": percentile,
                        "top1_share": row["top1_share"],
                        "top5_share": row["top5_share"],
                        "top10_share": row["top10_share"],
                    },
                }
            )

    if range_start is not None or range_end is not None:
        flags = [
            row
            for row in flags
            if _date_value_overlaps_range(row.get("event_date"), start=range_start, end=range_end)
        ]

    flags.sort(key=lambda row: row["severity"], reverse=True)
    return flags[: max(1, int(limit))]


def get_time_series(
    conn: sqlite3.Connection,
    months: int = 24,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Get monthly contribution totals and trend metrics."""
    month_totals: dict[str, float] = defaultdict(float)
    month_counts: dict[str, int] = defaultdict(int)
    source = _donor_flow_source(conn)
    range_start, range_end = _normalize_date_range(date_from, date_to)

    if _rows_for_source_exist(conn, "analytics_committee_monthly_totals", source):
        rows = conn.execute(
            """
            SELECT
                month_key,
                COALESCE(SUM(month_total), 0) AS total_amount,
                COALESCE(SUM(contribution_count), 0) AS contribution_count
            FROM analytics_committee_monthly_totals
            WHERE source = ?
            GROUP BY month_key
            ORDER BY month_key
            """,
            (source,),
        ).fetchall()
        for row in rows:
            month_key = _normalize_month_key(row["month_key"])
            if not month_key:
                continue
            if not _date_value_overlaps_range(month_key, start=range_start, end=range_end):
                continue
            month_totals[month_key] += float(row["total_amount"] or 0.0)
            month_counts[month_key] += int(row["contribution_count"] or 0)

    elif source == "bulk_receipts":
        rows = conn.execute(
            f"""
            SELECT
                SUBSTR(r.received_date, 1, 7) AS month_key,
                COALESCE(SUM(r.amount), 0) AS total_amount,
                COUNT(*) AS contribution_count
            FROM bulk_receipts_clean r
            WHERE COALESCE(r.amount, 0) > 0
              AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
              AND r.received_date IS NOT NULL
              AND LENGTH(r.received_date) >= 7
            GROUP BY month_key
            """
        ).fetchall()
        for row in rows:
            month_key = _normalize_month_key(row["month_key"])
            if not month_key:
                continue
            if not _date_value_overlaps_range(month_key, start=range_start, end=range_end):
                continue
            month_totals[month_key] += float(row["total_amount"] or 0.0)
            month_counts[month_key] += int(row["contribution_count"] or 0)
    else:
        rows = conn.execute(
            """
            SELECT
                ct.amount AS amount,
                ct.transaction_date AS transaction_date,
                r.filed_date AS filed_date
            FROM contributions ct
            JOIN reports r ON r.id = ct.report_id
            WHERE ct.amount IS NOT NULL AND ct.amount > 0
            """
        ).fetchall()
        for row in rows:
            event_date = _parse_date(row["transaction_date"]) or _parse_date(row["filed_date"])
            if not event_date:
                continue
            month_key = event_date.strftime("%Y-%m")
            if not _date_value_overlaps_range(month_key, start=range_start, end=range_end):
                continue
            month_totals[month_key] += float(row["amount"] or 0.0)
            month_counts[month_key] += 1

    ordered_months = sorted(month_totals.keys())
    if months > 0:
        ordered_months = ordered_months[-int(months) :]

    output = []
    for idx, month_key in enumerate(ordered_months):
        total_amount = month_totals[month_key]
        contribution_count = month_counts[month_key]

        trailing_keys = ordered_months[max(0, idx - 2) : idx + 1]
        trailing_avg = sum(month_totals[k] for k in trailing_keys) / len(trailing_keys)

        previous_total = month_totals[ordered_months[idx - 1]] if idx > 0 else None
        if previous_total is None or previous_total == 0:
            mom_change_pct = None
        else:
            mom_change_pct = ((total_amount - previous_total) / previous_total) * 100

        output.append(
            {
                "month": month_key,
                "total_amount": round(total_amount, 2),
                "contribution_count": contribution_count,
                "moving_avg_3": round(trailing_avg, 2),
                "mom_change_pct": round(mom_change_pct, 2) if mom_change_pct is not None else None,
            }
        )

    return output


def get_geo_summary(
    conn: sqlite3.Connection,
    limit_states: int = 20,
    limit_cities: int = 30,
    donor_committee_rows: Optional[list[dict]] = None,
    donor_source: Optional[str] = None,
) -> dict:
    """Summarize contributions geographically from donor addresses (state/city)."""
    if donor_committee_rows is None:
        donor_committee_rows, donor_source = _get_donor_committee_rows(conn, min_edge_amount=0.0, limit=None)

    source = donor_source or _donor_flow_source(conn)
    states: dict[str, dict] = {}
    cities: dict[tuple[str, str], dict] = {}

    for row in donor_committee_rows:
        city = (row.get("donor_city") or "").strip()
        state = (row.get("donor_state") or "").strip().upper()

        if not city and not state:
            parsed_city, parsed_state = _extract_city_state(row.get("donor_address"))
            city = city or (parsed_city or "")
            state = state or ((parsed_state or "").upper())

        if not state:
            continue

        amount = float(row.get("total_amount") or 0.0)
        contribution_count = int(row.get("contribution_count") or 0)
        donor_key = str(row.get("donor_key") or "")
        if not donor_key:
            donor_key = _stable_entity_key(row.get("donor_name"), row.get("donor_address"), source)

        state_bucket = states.setdefault(
            state,
            {"state": state, "total_amount": 0.0, "contribution_count": 0, "donor_keys": set()},
        )
        state_bucket["total_amount"] += amount
        state_bucket["contribution_count"] += contribution_count
        state_bucket["donor_keys"].add(donor_key)

        if city:
            normalized_city = _city_display(city)
            if not normalized_city:
                continue
            city_key = (_city_key(normalized_city), state)
            city_bucket = cities.setdefault(
                city_key,
                {
                    "city": normalized_city,
                    "state": state,
                    "total_amount": 0.0,
                    "contribution_count": 0,
                    "donor_keys": set(),
                },
            )
            city_bucket["total_amount"] += amount
            city_bucket["contribution_count"] += contribution_count
            city_bucket["donor_keys"].add(donor_key)

    state_rows = [
        {
            "state": state,
            "total_amount": round(bucket["total_amount"], 2),
            "contribution_count": bucket["contribution_count"],
            "donor_count": len(bucket["donor_keys"]),
        }
        for state, bucket in states.items()
    ]
    state_rows.sort(key=lambda row: row["total_amount"], reverse=True)

    city_rows = [
        {
            "city": bucket["city"],
            "state": bucket["state"],
            "total_amount": round(bucket["total_amount"], 2),
            "contribution_count": bucket["contribution_count"],
            "donor_count": len(bucket["donor_keys"]),
        }
        for bucket in cities.values()
    ]
    city_rows.sort(key=lambda row: row["total_amount"], reverse=True)

    return {
        "states": state_rows[: max(1, int(limit_states))],
        "cities": city_rows[: max(1, int(limit_cities))],
    }


def get_reconciliation_outliers(
    conn: sqlite3.Connection,
    limit: int = 25,
    min_abs_diff: float = 1000.0,
) -> list[dict]:
    """Return top D2-vs-receipts filing mismatches by absolute difference."""
    if not _table_exists(conn, "bulk_d2_receipts_recon"):
        return []

    rows = conn.execute(
        """
        SELECT
            committee_id_sbe,
            committee_name,
            filed_doc_id,
            d2_total_receipts,
            receipts_amount_sum,
            receipts_minus_d2_total,
            receipt_row_count,
            first_receipt_date,
            last_receipt_date,
            is_archived
        FROM bulk_d2_receipts_recon
        WHERE ABS(COALESCE(CAST(receipts_minus_d2_total AS DOUBLE PRECISION), 0.0)) >= ?
        ORDER BY ABS(COALESCE(CAST(receipts_minus_d2_total AS DOUBLE PRECISION), 0.0)) DESC
        LIMIT ?
        """,
        (float(min_abs_diff), max(1, int(limit))),
    ).fetchall()

    return [
        {
            "committee_id_sbe": row["committee_id_sbe"],
            "committee_name": row["committee_name"],
            "filed_doc_id": row["filed_doc_id"],
            "d2_total_receipts": round(float(row["d2_total_receipts"] or 0.0), 2),
            "receipts_amount_sum": round(float(row["receipts_amount_sum"] or 0.0), 2),
            "receipts_minus_d2_total": round(float(row["receipts_minus_d2_total"] or 0.0), 2),
            "abs_diff": round(abs(float(row["receipts_minus_d2_total"] or 0.0)), 2),
            "receipt_row_count": int(row["receipt_row_count"] or 0),
            "first_receipt_date": row["first_receipt_date"],
            "last_receipt_date": row["last_receipt_date"],
            "is_archived": int(row["is_archived"]) if row["is_archived"] is not None else None,
        }
        for row in rows
    ]


def get_state_race_analytics(
    conn: sqlite3.Connection,
    *,
    limit: int = 12,
    date_from: str | None = None,
    date_to: str | None = None,
    election_cycle: int | None = None,
) -> list[dict]:
    """Return state race-level analytics using bulk ISBE tables when available."""
    required_tables = {
        "bulk_candidate_committee_finance_agg",
        "bulk_receipts_clean",
        "bulk_expenditures_clean",
    }
    if not all(_table_exists(conn, table_name) for table_name in required_tables):
        return []

    has_cycle = _column_exists(conn, "bulk_candidate_committee_finance_agg", "election_cycle")
    resolved_cycle = election_cycle
    if has_cycle and resolved_cycle is None:
        cycle_row = conn.execute(
            """
            SELECT MAX(election_cycle) AS max_cycle
            FROM bulk_candidate_committee_finance_agg
            WHERE election_cycle IS NOT NULL
            """
        ).fetchone()
        if cycle_row and cycle_row["max_cycle"] is not None:
            try:
                resolved_cycle = int(cycle_row["max_cycle"])
            except (TypeError, ValueError):
                resolved_cycle = None

    where_parts: list[str] = []
    where_params: list[object] = []
    if has_cycle and resolved_cycle is not None:
        where_parts.append("election_cycle = ?")
        where_params.append(int(resolved_cycle))
    candidate_where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    candidate_rows = conn.execute(
        f"""
        SELECT
            CAST(candidate_id AS TEXT) AS candidate_id,
            TRIM(COALESCE(candidate_full_name, '')) AS candidate_full_name,
            TRIM(COALESCE(office_sought, '')) AS office_sought,
            TRIM(COALESCE(district_type, '')) AS district_type,
            TRIM(COALESCE(district, '')) AS district,
            CAST(committee_id_sbe AS TEXT) AS committee_id_sbe,
            COALESCE(SUM(CAST(sum_total_receipts AS DOUBLE PRECISION)), 0.0) AS candidate_receipts_total
        FROM bulk_candidate_committee_finance_agg
        {candidate_where}
        GROUP BY candidate_id, candidate_full_name, office_sought, district_type, district, committee_id_sbe
        """,
        where_params,
    ).fetchall()

    if not candidate_rows:
        return []

    races: dict[str, dict] = {}
    committee_to_races: dict[str, set[str]] = defaultdict(set)
    candidate_name_to_races: dict[str, set[str]] = defaultdict(set)

    for row in candidate_rows:
        office_sought = row["office_sought"] or "Unknown Office"
        district_type = row["district_type"] or "Unknown District Type"
        district = row["district"] or ""
        race_key = f"{office_sought}|{district_type}|{district}"
        race_label = f"{office_sought} - {district_type}{(' ' + district) if district else ''}"

        race = races.setdefault(
            race_key,
            {
                "race_label": race_label,
                "candidate_ids": set(),
                "candidate_receipts": defaultdict(float),
                "candidate_receipts_total": 0.0,
                "donor_count": 0,
                "contribution_count": 0,
                "total_amount": 0.0,
                "outside_spending_total": 0.0,
            },
        )

        candidate_id = (row["candidate_id"] or "").strip()
        candidate_name = (row["candidate_full_name"] or "").strip()
        if candidate_id:
            race["candidate_ids"].add(candidate_id)

        receipts_total = float(row["candidate_receipts_total"] or 0.0)
        if candidate_id:
            race["candidate_receipts"][candidate_id] += receipts_total
        else:
            fallback_key = candidate_name or "unknown"
            race["candidate_receipts"][fallback_key] += receipts_total
        race["candidate_receipts_total"] += receipts_total

        committee_id = (row["committee_id_sbe"] or "").strip()
        if committee_id:
            committee_to_races[committee_id].add(race_key)

        if candidate_name:
            candidate_name_to_races[candidate_name.upper()].add(race_key)

    receipts_date_column_exists = _column_exists(conn, "bulk_receipts_clean", "received_date")
    donor_name_candidates: list[str] = []
    if _column_exists(conn, "bulk_receipts_clean", "contributed_by"):
        donor_name_candidates.append("NULLIF(TRIM(r.contributed_by), '')")
    first_name_exists = _column_exists(conn, "bulk_receipts_clean", "first_name")
    last_name_exists = _column_exists(conn, "bulk_receipts_clean", "last_or_business_name")
    if first_name_exists and last_name_exists:
        donor_name_candidates.append(
            "NULLIF(TRIM(COALESCE(r.first_name, '') || "
            "CASE WHEN COALESCE(r.first_name, '') <> '' AND COALESCE(r.last_or_business_name, '') <> '' THEN ' ' ELSE '' END || "
            "COALESCE(r.last_or_business_name, '')), '')"
        )
    elif last_name_exists:
        donor_name_candidates.append("NULLIF(TRIM(r.last_or_business_name), '')")
    elif first_name_exists:
        donor_name_candidates.append("NULLIF(TRIM(r.first_name), '')")
    if _column_exists(conn, "bulk_receipts_clean", "bulk_row_id"):
        donor_name_candidates.append("CAST(r.bulk_row_id AS TEXT)")
    donor_key_expr = "COALESCE(" + ", ".join(donor_name_candidates + ["'unknown'"]) + ")"

    receipt_where_parts = ["r.committee_id_sbe IS NOT NULL"]
    receipt_params: list[object] = []
    if receipts_date_column_exists and date_from:
        receipt_where_parts.append("r.received_date >= ?")
        receipt_params.append(date_from)
    if receipts_date_column_exists and date_to:
        receipt_where_parts.append("r.received_date <= ?")
        receipt_params.append(date_to)

    receipt_rows = conn.execute(
        f"""
        SELECT
            CAST(r.committee_id_sbe AS TEXT) AS committee_id_sbe,
            COUNT(*) AS contribution_count,
            COUNT(DISTINCT {donor_key_expr}) AS donor_count,
            COALESCE(SUM(COALESCE(r.amount, 0.0)), 0.0) AS total_amount
        FROM bulk_receipts_clean r
        WHERE {' AND '.join(receipt_where_parts)}
        GROUP BY r.committee_id_sbe
        """,
        receipt_params,
    ).fetchall()

    for row in receipt_rows:
        committee_id = (row["committee_id_sbe"] or "").strip()
        if not committee_id:
            continue
        for race_key in committee_to_races.get(committee_id, set()):
            race = races.get(race_key)
            if not race:
                continue
            race["contribution_count"] += int(row["contribution_count"] or 0)
            race["total_amount"] += float(row["total_amount"] or 0.0)
            race.setdefault("_donor_count_by_race", 0)

    race_donor_counts: dict[str, int] = defaultdict(int)
    donor_rows = conn.execute(
        f"""
        SELECT
            CAST(r.committee_id_sbe AS TEXT) AS committee_id_sbe,
            {donor_key_expr} AS donor_key
        FROM bulk_receipts_clean r
        WHERE {' AND '.join(receipt_where_parts)}
        """,
        receipt_params,
    ).fetchall()
    race_donor_sets: dict[str, set[str]] = defaultdict(set)
    for row in donor_rows:
        committee_id = (row["committee_id_sbe"] or "").strip()
        donor_key = (row["donor_key"] or "").strip()
        if not committee_id or not donor_key:
            continue
        for race_key in committee_to_races.get(committee_id, set()):
            race_donor_sets[race_key].add(donor_key)
    for race_key, donors in race_donor_sets.items():
        race_donor_counts[race_key] = len(donors)

    expenditure_part_exists = _column_exists(conn, "bulk_expenditures_clean", "d2_part_code")
    expenditure_amount_exists = _column_exists(conn, "bulk_expenditures_clean", "amount")
    expenditure_candidate_exists = _column_exists(conn, "bulk_expenditures_clean", "candidate_name")
    if expenditure_part_exists and expenditure_amount_exists and expenditure_candidate_exists:
        is_archived_exists = _column_exists(conn, "bulk_expenditures_clean", "is_archived")
        expended_date_exists = _column_exists(conn, "bulk_expenditures_clean", "expended_date")
        exp_where_parts = ["COALESCE(e.d2_part_code, '') LIKE '9%'"]
        exp_params: list[object] = []
        if is_archived_exists:
            exp_where_parts.append("COALESCE(e.is_archived, 0) = 0")
        if expended_date_exists and date_from:
            exp_where_parts.append("e.expended_date >= ?")
            exp_params.append(date_from)
        if expended_date_exists and date_to:
            exp_where_parts.append("e.expended_date <= ?")
            exp_params.append(date_to)

        outside_rows = conn.execute(
            f"""
            SELECT
                UPPER(TRIM(COALESCE(e.candidate_name, ''))) AS candidate_name_key,
                COALESCE(SUM(COALESCE(e.amount, 0.0)), 0.0) AS total_amount
            FROM bulk_expenditures_clean e
            WHERE {' AND '.join(exp_where_parts)}
            GROUP BY candidate_name_key
            """,
            exp_params,
        ).fetchall()
        for row in outside_rows:
            candidate_name_key = (row["candidate_name_key"] or "").strip()
            if not candidate_name_key:
                continue
            for race_key in candidate_name_to_races.get(candidate_name_key, set()):
                race = races.get(race_key)
                if race:
                    race["outside_spending_total"] += float(row["total_amount"] or 0.0)

    output: list[dict] = []
    for race_key, race in races.items():
        total_amount = float(race["total_amount"] or 0.0)
        outside_spending_total = float(race["outside_spending_total"] or 0.0)
        candidate_receipts_total = float(race["candidate_receipts_total"] or 0.0)
        top_candidate_amount = max((float(v or 0.0) for v in race["candidate_receipts"].values()), default=0.0)

        outside_pressure_ratio = (outside_spending_total / total_amount) if total_amount > 0 else 0.0
        top_candidate_share = (top_candidate_amount / candidate_receipts_total) if candidate_receipts_total > 0 else 0.0

        output.append(
            {
                "race_label": race["race_label"],
                "candidate_count": len(race["candidate_ids"]),
                "donor_count": int(race_donor_counts.get(race_key, 0)),
                "contribution_count": int(race["contribution_count"] or 0),
                "total_amount": round(total_amount, 2),
                "outside_spending_total": round(outside_spending_total, 2),
                "outside_pressure_ratio": round(outside_pressure_ratio, 4),
                "top_candidate_share": round(top_candidate_share, 4),
            }
        )

    output.sort(
        key=lambda row: (
            float(row["total_amount"] or 0.0),
            int(row["contribution_count"] or 0),
            row["race_label"],
        ),
        reverse=True,
    )
    return output[: max(1, int(limit))]


def get_analytics_data_sources(conn: sqlite3.Connection) -> dict:
    """Describe analytics inputs currently available/used."""
    donor_source = _donor_flow_source(conn)
    has_recon = _table_exists(conn, "bulk_d2_receipts_recon")
    recon_rows = 0
    if has_recon:
        recon_rows = int(
            conn.execute("SELECT COUNT(*) AS count FROM bulk_d2_receipts_recon").fetchone()["count"]
        )

    return {
        "donor_flow_source": donor_source,
        "reconciliation_available": has_recon,
        "reconciliation_rows": recon_rows,
    }


def _refresh_materialized_bulk(conn: sqlite3.Connection) -> dict:
    donor_inserted = 0
    donor_summary_inserted = 0
    monthly_inserted = 0
    large_inserted = 0

    conn.execute("DELETE FROM analytics_donor_committee_agg WHERE source = 'bulk_receipts'")
    conn.execute("DELETE FROM analytics_committee_monthly_totals WHERE source = 'bulk_receipts'")
    conn.execute("DELETE FROM analytics_large_contributions WHERE source = 'bulk_receipts'")
    conn.execute("DELETE FROM analytics_donor_summary WHERE source = 'bulk_receipts'")

    conn.execute(
        f"""
        INSERT INTO analytics_donor_committee_agg (
            source, donor_key, donor_name, donor_address, donor_city, donor_state,
            occupation, employer, committee_id, committee_name, total_amount, contribution_count, updated_at
        )
        SELECT
            'bulk_receipts' AS source,
            LOWER(TRIM(
                COALESCE(r.first_name, '') || '|' || COALESCE(r.last_or_business_name, '') || '|' ||
                COALESCE(r.address_line_1, '') || '|' || COALESCE(r.address_line_2, '') || '|' ||
                COALESCE(r.city, '') || '|' || COALESCE(r.state, '') || '|' || COALESCE(r.postal_code, '')
            )) AS donor_key,
            MAX(TRIM(
                COALESCE(r.first_name, '')
                || CASE
                    WHEN COALESCE(r.first_name, '') <> '' AND COALESCE(r.last_or_business_name, '') <> ''
                    THEN ' '
                    ELSE ''
                   END
                || COALESCE(r.last_or_business_name, '')
            )) AS donor_name,
            MAX(TRIM(
                COALESCE(r.address_line_1, '')
                || CASE WHEN COALESCE(r.address_line_2, '') <> '' THEN ', ' || r.address_line_2 ELSE '' END
                || CASE WHEN COALESCE(r.city, '') <> '' THEN ', ' || r.city ELSE '' END
                || CASE WHEN COALESCE(r.state, '') <> '' THEN ', ' || r.state ELSE '' END
                || CASE WHEN COALESCE(r.postal_code, '') <> '' THEN ', ' || r.postal_code ELSE '' END
            )) AS donor_address,
            MAX(r.city) AS donor_city,
            MAX(r.state) AS donor_state,
            MAX(NULLIF(TRIM(r.occupation), '')) AS occupation,
            MAX(NULLIF(TRIM(r.employer), '')) AS employer,
            CAST(COALESCE(c.committee_id_sbe, r.committee_id_sbe) AS TEXT) AS committee_id,
            COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe) AS committee_name,
            COALESCE(SUM(r.amount), 0) AS total_amount,
            COUNT(*) AS contribution_count,
            CURRENT_TIMESTAMP
        FROM bulk_receipts_clean r
        LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe
        WHERE COALESCE(r.amount, 0) > 0
          AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
        GROUP BY
            LOWER(TRIM(
                COALESCE(r.first_name, '') || '|' || COALESCE(r.last_or_business_name, '') || '|' ||
                COALESCE(r.address_line_1, '') || '|' || COALESCE(r.address_line_2, '') || '|' ||
                COALESCE(r.city, '') || '|' || COALESCE(r.state, '') || '|' || COALESCE(r.postal_code, '')
            )),
            CAST(COALESCE(c.committee_id_sbe, r.committee_id_sbe) AS TEXT),
            COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe)
        """
    )
    donor_inserted = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM analytics_donor_committee_agg
            WHERE source = 'bulk_receipts'
            """
        ).fetchone()["count"]
    )

    conn.execute(
        f"""
        INSERT INTO analytics_donor_summary (
            source, donor_key, local_donor_id, donor_name, donor_address,
            donor_city, donor_state, occupation, employer, total_amount,
            contribution_count, committee_count, updated_at
        )
        SELECT
            'bulk_receipts' AS source,
            a.donor_key,
            NULL AS local_donor_id,
            MAX(a.donor_name) AS donor_name,
            MAX(a.donor_address) AS donor_address,
            MAX(a.donor_city) AS donor_city,
            MAX(a.donor_state) AS donor_state,
            MAX(NULLIF(TRIM(a.occupation), '')) AS occupation,
            MAX(NULLIF(TRIM(a.employer), '')) AS employer,
            COALESCE(SUM(a.total_amount), 0) AS total_amount,
            COALESCE(SUM(a.contribution_count), 0) AS contribution_count,
            COUNT(DISTINCT a.committee_id) AS committee_count,
            CURRENT_TIMESTAMP
        FROM analytics_donor_committee_agg a
        WHERE a.source = 'bulk_receipts'
        GROUP BY a.donor_key
        """
    )
    donor_summary_inserted = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM analytics_donor_summary
            WHERE source = 'bulk_receipts'
            """
        ).fetchone()["count"]
    )

    conn.execute(
        f"""
        INSERT INTO analytics_committee_monthly_totals (
            source, committee_name, month_key, month_total, contribution_count, updated_at
        )
        SELECT
            'bulk_receipts' AS source,
            COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe) AS committee_name,
            SUBSTR(r.received_date, 1, 7) AS month_key,
            COALESCE(SUM(r.amount), 0) AS month_total,
            COUNT(*) AS contribution_count,
            CURRENT_TIMESTAMP
        FROM bulk_receipts_clean r
        LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe
        WHERE COALESCE(r.amount, 0) > 0
          AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
          AND r.received_date IS NOT NULL
          AND LENGTH(r.received_date) >= 7
        GROUP BY
            COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe),
            SUBSTR(r.received_date, 1, 7)
        """
    )
    monthly_inserted = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM analytics_committee_monthly_totals
            WHERE source = 'bulk_receipts'
            """
        ).fetchone()["count"]
    )

    p95_row = conn.execute(
        f"""
        WITH ordered AS (
            SELECT
                r.amount AS amount,
                ROW_NUMBER() OVER (ORDER BY r.amount) AS rn,
                COUNT(*) OVER () AS cnt
            FROM bulk_receipts_clean r
            WHERE COALESCE(r.amount, 0) > 0
              AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
        )
        SELECT amount
        FROM ordered
        WHERE rn = CAST(((cnt - 1) * 0.95) AS INTEGER) + 1
        LIMIT 1
        """
    ).fetchone()
    p95 = float(p95_row["amount"] or 0.0) if p95_row else 0.0
    large_threshold = max(5000.0, p95 * 2.0)

    conn.execute(
        f"""
        INSERT INTO analytics_large_contributions (
            source, committee_name, donor_name, event_date, amount, large_threshold
        )
        SELECT
            'bulk_receipts' AS source,
            COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe) AS committee_name,
            TRIM(
                COALESCE(r.first_name, '')
                || CASE
                    WHEN COALESCE(r.first_name, '') <> '' AND COALESCE(r.last_or_business_name, '') <> ''
                    THEN ' '
                    ELSE ''
                   END
                || COALESCE(r.last_or_business_name, '')
            ) AS donor_name,
            r.received_date AS event_date,
            r.amount AS amount,
            ? AS large_threshold
        FROM bulk_receipts_clean r
        LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe
        WHERE COALESCE(r.amount, 0) >= ?
          AND {_BULK_DONOR_RECEIPT_FILTER_SQL}
        """,
        (float(large_threshold), float(large_threshold)),
    )
    large_inserted = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM analytics_large_contributions
            WHERE source = 'bulk_receipts'
            """
        ).fetchone()["count"]
    )

    conn.execute(
        """
        INSERT INTO analytics_materialized_meta (
            source, donor_row_count, monthly_row_count, large_row_count,
            large_threshold, materialization_version, materialization_notes, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source) DO UPDATE SET
            donor_row_count = excluded.donor_row_count,
            monthly_row_count = excluded.monthly_row_count,
            large_row_count = excluded.large_row_count,
            large_threshold = excluded.large_threshold,
            materialization_version = excluded.materialization_version,
            materialization_notes = excluded.materialization_notes,
            refreshed_at = CURRENT_TIMESTAMP
        """,
        (
            "bulk_receipts",
            donor_inserted,
            monthly_inserted,
            large_inserted,
            float(large_threshold),
            BULK_RECEIPTS_MATERIALIZATION_VERSION,
            "active_non_archived_d2_part1_with_active_d2_filing",
        ),
    )

    return {
        "source": "bulk_receipts",
        "donor_rows": donor_inserted,
        "donor_summary_rows": donor_summary_inserted,
        "monthly_rows": monthly_inserted,
        "large_rows": large_inserted,
        "large_threshold": round(float(large_threshold), 2),
        "materialization_version": BULK_RECEIPTS_MATERIALIZATION_VERSION,
    }


def _refresh_materialized_contributions(conn: sqlite3.Connection) -> dict:
    donor_inserted = 0
    donor_summary_inserted = 0
    monthly_inserted = 0
    large_inserted = 0

    conn.execute("DELETE FROM analytics_donor_committee_agg WHERE source = 'contributions'")
    conn.execute("DELETE FROM analytics_committee_monthly_totals WHERE source = 'contributions'")
    conn.execute("DELETE FROM analytics_large_contributions WHERE source = 'contributions'")
    conn.execute("DELETE FROM analytics_donor_summary WHERE source = 'contributions'")

    donor_rows = conn.execute(
        """
        SELECT
            d.id AS donor_id,
            d.name AS donor_name,
            d.address AS donor_address,
            c.id AS committee_id,
            c.name AS committee_name,
            COALESCE(SUM(ct.amount), 0) AS total_amount,
            COUNT(ct.id) AS contribution_count
        FROM contributions ct
        JOIN donors d ON d.id = ct.donor_id
        JOIN reports r ON r.id = ct.report_id
        JOIN committees c ON c.id = r.committee_id
        GROUP BY d.id, c.id
        """
    ).fetchall()
    donor_payload = [
        (
            "contributions",
            f"donor:{row['donor_id']}",
            row["donor_name"] or f"Donor {row['donor_id']}",
            row["donor_address"] or "",
            None,
            None,
            None,
            None,
            str(row["committee_id"]),
            row["committee_name"] or f"Committee {row['committee_id']}",
            float(row["total_amount"] or 0.0),
            int(row["contribution_count"] or 0),
        )
        for row in donor_rows
    ]
    if donor_payload:
        conn.executemany(
            """
            INSERT INTO analytics_donor_committee_agg (
                source, donor_key, donor_name, donor_address, donor_city, donor_state,
                occupation, employer, committee_id, committee_name, total_amount, contribution_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            donor_payload,
        )
        donor_inserted = len(donor_payload)

    conn.execute(
        """
        INSERT INTO analytics_donor_summary (
            source, donor_key, local_donor_id, donor_name, donor_address,
            donor_city, donor_state, occupation, employer, total_amount,
            contribution_count, committee_count, updated_at
        )
        SELECT
            'contributions' AS source,
            'donor:' || d.id AS donor_key,
            d.id AS local_donor_id,
            d.name AS donor_name,
            d.address AS donor_address,
            NULL AS donor_city,
            NULL AS donor_state,
            NULLIF(TRIM(d.occupation), '') AS occupation,
            NULLIF(TRIM(d.employer), '') AS employer,
            COALESCE(SUM(c.amount), 0) AS total_amount,
            COUNT(c.id) AS contribution_count,
            COUNT(DISTINCT r.committee_id) AS committee_count,
            CURRENT_TIMESTAMP
        FROM donors d
        LEFT JOIN contributions c ON c.donor_id = d.id
        LEFT JOIN reports r ON c.report_id = r.id
        GROUP BY d.id
        """
    )
    donor_summary_inserted = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM analytics_donor_summary
            WHERE source = 'contributions'
            """
        ).fetchone()["count"]
    )

    monthly_totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    rows = conn.execute(
        """
        SELECT
            ct.amount AS amount,
            ct.transaction_date AS transaction_date,
            r.filed_date AS filed_date,
            c.name AS committee_name
        FROM contributions ct
        JOIN reports r ON r.id = ct.report_id
        JOIN committees c ON c.id = r.committee_id
        WHERE ct.amount IS NOT NULL AND ct.amount > 0
        """
    ).fetchall()

    amounts = []
    large_candidate_rows = []
    for row in rows:
        amount = float(row["amount"] or 0.0)
        amounts.append(amount)

        event_date = _parse_date(row["transaction_date"]) or _parse_date(row["filed_date"])
        if event_date:
            month_key = event_date.strftime("%Y-%m")
            key = (row["committee_name"] or "Unknown Committee", month_key)
            monthly_totals[key][0] += amount
            monthly_totals[key][1] += 1

        large_candidate_rows.append(
            {
                "committee_name": row["committee_name"] or "Unknown Committee",
                "event_date": row["transaction_date"] or row["filed_date"],
                "amount": amount,
                "donor_name": None,
            }
        )

    monthly_payload = [
        (
            "contributions",
            committee_name,
            month_key,
            round(total_amount, 2),
            int(contribution_count),
        )
        for (committee_name, month_key), (total_amount, contribution_count) in monthly_totals.items()
    ]
    if monthly_payload:
        conn.executemany(
            """
            INSERT INTO analytics_committee_monthly_totals (
                source, committee_name, month_key, month_total, contribution_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            monthly_payload,
        )
        monthly_inserted = len(monthly_payload)

    p95 = _percentile(amounts, 0.95) if amounts else 0.0
    large_threshold = max(5000.0, p95 * 2.0)
    large_payload = [
        (
            "contributions",
            row["committee_name"],
            row["donor_name"] or "Unknown Donor",
            row["event_date"],
            float(row["amount"] or 0.0),
            large_threshold,
        )
        for row in large_candidate_rows
        if float(row["amount"] or 0.0) >= large_threshold
    ]
    if large_payload:
        conn.executemany(
            """
            INSERT INTO analytics_large_contributions (
                source, committee_name, donor_name, event_date, amount, large_threshold
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            large_payload,
        )
        large_inserted = len(large_payload)

    conn.execute(
        """
        INSERT INTO analytics_materialized_meta (
            source, donor_row_count, monthly_row_count, large_row_count,
            large_threshold, materialization_version, materialization_notes, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source) DO UPDATE SET
            donor_row_count = excluded.donor_row_count,
            monthly_row_count = excluded.monthly_row_count,
            large_row_count = excluded.large_row_count,
            large_threshold = excluded.large_threshold,
            materialization_version = excluded.materialization_version,
            materialization_notes = excluded.materialization_notes,
            refreshed_at = CURRENT_TIMESTAMP
        """,
        (
            "contributions",
            donor_inserted,
            monthly_inserted,
            large_inserted,
            float(large_threshold),
            CONTRIBUTIONS_MATERIALIZATION_VERSION,
            "legacy_contributions_table",
        ),
    )

    return {
        "source": "contributions",
        "donor_rows": donor_inserted,
        "donor_summary_rows": donor_summary_inserted,
        "monthly_rows": monthly_inserted,
        "large_rows": large_inserted,
        "large_threshold": round(float(large_threshold), 2),
        "materialization_version": CONTRIBUTIONS_MATERIALIZATION_VERSION,
    }


def refresh_analytics_materialized(conn: sqlite3.Connection) -> dict:
    """Rebuild materialized analytics aggregate tables for the active donor-flow source."""
    source = _donor_flow_source(conn)
    if source == "bulk_receipts":
        stats = _refresh_materialized_bulk(conn)
    else:
        stats = _refresh_materialized_contributions(conn)
    conn.commit()
    return stats


def _full_dashboard_snapshot_key(params: dict) -> str:
    normalized = json.dumps(params, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return f"dashboard_full:{digest}"


def get_dashboard_snapshot(
    conn: sqlite3.Connection,
    params: dict,
    ttl_seconds: int = 900,
) -> dict:
    """Fetch cached full-dashboard snapshot metadata and payload."""
    cache_key = _full_dashboard_snapshot_key(params)
    if not _table_exists(conn, "analytics_snapshots"):
        return {
            "cache_key": cache_key,
            "status": "empty",
            "payload": None,
            "completed_at": None,
            "updated_at": None,
            "age_seconds": None,
            "is_stale": True,
            "is_fresh": False,
        }

    row = conn.execute(
        """
        SELECT cache_key, status, payload_json, error_message, completed_at, updated_at
        FROM analytics_snapshots
        WHERE cache_key = ?
        LIMIT 1
        """,
        (cache_key,),
    ).fetchone()
    if not row:
        return {
            "cache_key": cache_key,
            "status": "empty",
            "payload": None,
            "error_message": None,
            "completed_at": None,
            "updated_at": None,
            "age_seconds": None,
            "is_stale": True,
            "is_fresh": False,
        }

    payload = json.loads(row["payload_json"]) if row["payload_json"] else None
    completed_at = row["completed_at"]
    age_seconds = None
    is_fresh = False
    if completed_at:
        try:
            completed_dt = datetime.strptime(completed_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (datetime.now(timezone.utc) - completed_dt).total_seconds())
            is_fresh = row["status"] == "completed" and age_seconds <= float(max(1, ttl_seconds))
        except ValueError:
            age_seconds = None

    return {
        "cache_key": row["cache_key"],
        "status": row["status"],
        "payload": payload,
        "error_message": row["error_message"],
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"],
        "age_seconds": age_seconds,
        "is_stale": not is_fresh,
        "is_fresh": is_fresh,
    }


def save_dashboard_snapshot(
    conn: sqlite3.Connection,
    params: dict,
    status: str,
    payload: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> dict:
    """Persist full-dashboard snapshot state/payload."""
    cache_key = _full_dashboard_snapshot_key(params)
    payload_json = json.dumps(payload) if payload is not None else None
    params_json = json.dumps(params, sort_keys=True)
    conn.execute(
        """
        INSERT INTO analytics_snapshots (
            cache_key, snapshot_type, params_json, payload_json, status, error_message,
            created_at, updated_at, completed_at
        )
        VALUES (
            ?, 'dashboard_full', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
            CASE WHEN ? = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END
        )
        ON CONFLICT(cache_key) DO UPDATE SET
            snapshot_type = excluded.snapshot_type,
            params_json = excluded.params_json,
            payload_json = CASE
                WHEN excluded.payload_json IS NOT NULL THEN excluded.payload_json
                ELSE analytics_snapshots.payload_json
            END,
            status = excluded.status,
            error_message = excluded.error_message,
            updated_at = CURRENT_TIMESTAMP,
            completed_at = CASE
                WHEN excluded.status = 'completed' THEN CURRENT_TIMESTAMP
                ELSE analytics_snapshots.completed_at
            END
        """,
        (cache_key, params_json, payload_json, status, error_message, status),
    )
    conn.commit()
    return get_dashboard_snapshot(conn, params=params, ttl_seconds=1)


def build_dashboard_full_snapshot(
    conn: sqlite3.Connection,
    params: dict,
    rebuild_materialized: bool = False,
) -> dict:
    """Build full analytics dashboard payload for caching/display."""
    if rebuild_materialized or not _rows_for_source_exist(conn, "analytics_donor_committee_agg", _donor_flow_source(conn)):
        refresh_analytics_materialized(conn)

    min_edge_amount = float(params.get("min_edge_amount", 1000))
    network_limit = int(params.get("network_limit", 200))
    anomaly_limit = int(params.get("anomaly_limit", 25))
    concentration_limit = int(params.get("concentration_limit", 25))
    months = int(params.get("months", 24))
    geo_state_limit = int(params.get("geo_state_limit", 15))
    geo_city_limit = int(params.get("geo_city_limit", 25))
    nlp_limit = int(params.get("nlp_limit", 20))
    recon_limit = int(params.get("recon_limit", 20))
    recon_min_abs_diff = float(params.get("recon_min_abs_diff", 1000))
    date_from = (params.get("date_from") or "").strip() or None
    date_to = (params.get("date_to") or "").strip() or None

    donor_rows, donor_source = _get_donor_committee_rows(conn, min_edge_amount=0.0, limit=None)
    concentration_window = max(concentration_limit, 5000)
    concentration_all = get_donor_concentration(
        conn,
        limit=concentration_window,
        donor_committee_rows=donor_rows,
    )

    payload = {
        "network": get_network_graph(
            conn,
            min_edge_amount=min_edge_amount,
            limit=network_limit,
            donor_committee_rows=donor_rows,
            donor_source=donor_source,
        ),
        "anomalies": get_anomaly_flags(
            conn,
            limit=anomaly_limit,
            precomputed_concentration=concentration_all,
            date_from=date_from,
            date_to=date_to,
        ),
        "concentration": concentration_all[: max(1, concentration_limit)],
        "time_series": get_time_series(
            conn,
            months=months,
            date_from=date_from,
            date_to=date_to,
        ),
        "geo_summary": get_geo_summary(
            conn,
            limit_states=geo_state_limit,
            limit_cities=geo_city_limit,
            donor_committee_rows=donor_rows,
            donor_source=donor_source,
        ),
        "nlp_summary": get_nlp_spending_summary(conn, limit=nlp_limit),
        "reconciliation": get_reconciliation_outliers(
            conn,
            limit=recon_limit,
            min_abs_diff=recon_min_abs_diff,
        ),
        "data_sources": get_analytics_data_sources(conn),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    return payload


def get_nlp_spending_summary(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Categorize spending-related text using keyword NLP heuristics."""
    rows = conn.execute(
        """
        SELECT
            COALESCE(ct.description, '') AS text_value,
            COALESCE(ct.amount, 0) AS amount
        FROM contributions ct
        WHERE ct.amount IS NOT NULL AND ct.amount > 0
        """
    ).fetchall()

    if _table_exists(conn, "d2_itemized_entries"):
        d2_rows = conn.execute(
            """
            SELECT
                COALESCE(description, purpose_beneficiary, '') AS text_value,
                COALESCE(amount, 0) AS amount
            FROM d2_itemized_entries
            WHERE amount IS NOT NULL AND amount > 0
            """
        ).fetchall()
        rows = list(rows) + list(d2_rows)

    grouped: dict[str, dict] = {}
    for row in rows:
        text_value = row["text_value"] or ""
        amount = float(row["amount"] or 0.0)
        category = categorize_spending_text(text_value)
        bucket = grouped.setdefault(
            category,
            {"category": category, "total_amount": 0.0, "entry_count": 0, "sample_text": None},
        )
        bucket["total_amount"] += amount
        bucket["entry_count"] += 1
        if not bucket["sample_text"] and text_value:
            bucket["sample_text"] = text_value[:120]

    output = []
    for bucket in grouped.values():
        avg_amount = bucket["total_amount"] / bucket["entry_count"] if bucket["entry_count"] else 0.0
        output.append(
            {
                "category": bucket["category"],
                "total_amount": round(bucket["total_amount"], 2),
                "entry_count": bucket["entry_count"],
                "avg_amount": round(avg_amount, 2),
                "sample_text": bucket["sample_text"] or "",
            }
        )

    output.sort(key=lambda row: row["total_amount"], reverse=True)
    return output[: max(1, int(limit))]


def _compute_graph_centrality(nodes: list[dict], edges: list[dict], limit: int = 120) -> list[dict]:
    weighted_degree: dict[str, float] = defaultdict(float)
    edge_degree: dict[str, int] = defaultdict(int)
    node_map = {str(node.get("id")): node for node in nodes if node.get("id")}

    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_map or target not in node_map:
            continue
        weight = float(edge.get("weight") or 0.0)
        weighted_degree[source] += weight
        weighted_degree[target] += weight
        edge_degree[source] += 1
        edge_degree[target] += 1

    output: list[dict] = []
    for node_id, node in node_map.items():
        output.append(
            {
                "node_id": node_id,
                "label": node.get("label") or node_id,
                "node_type": node.get("node_type") or "node",
                "system": node.get("system"),
                "weighted_degree": round(weighted_degree.get(node_id, 0.0), 2),
                "degree": int(edge_degree.get(node_id, 0)),
            }
        )

    output.sort(key=lambda row: (row["weighted_degree"], row["degree"], row["label"]), reverse=True)
    return output[: max(1, int(limit))]


def _empty_relationship_graph(**summary_fields) -> dict:
    summary = {"node_count": 0, "edge_count": 0, **summary_fields}
    return {
        "nodes": [],
        "edges": [],
        "centrality": [],
        "summary": summary,
    }


def get_donor_cogiving_network(
    conn: sqlite3.Connection,
    donor_limit: int = 800,
    edge_limit: int = 1200,
    min_shared_amount: float = 5000.0,
    min_shared_targets: int = 2,
) -> dict:
    """Build donor-to-donor co-giving network from shared committee/candidate targets."""
    donor_limit = max(50, min(int(donor_limit), 5000))
    edge_limit = max(50, min(int(edge_limit), 10000))
    min_shared_amount = max(0.0, float(min_shared_amount))
    min_shared_targets = max(1, int(min_shared_targets))

    source = _donor_flow_source(conn)
    if source == "bulk_receipts" and _table_exists(conn, "analytics_donor_committee_agg"):
        donor_rows = []
        if _table_exists(conn, "analytics_donor_summary"):
            donor_rows = conn.execute(
                """
                SELECT donor_key, donor_name, donor_city, donor_state, total_amount, committee_count
                FROM analytics_donor_summary
                WHERE source = 'bulk_receipts'
                ORDER BY total_amount DESC
                LIMIT ?
                """,
                (donor_limit,),
            ).fetchall()

        if not donor_rows:
            donor_rows = conn.execute(
                """
                SELECT
                    donor_key,
                    MAX(donor_name) AS donor_name,
                    MAX(donor_city) AS donor_city,
                    MAX(donor_state) AS donor_state,
                    COALESCE(SUM(total_amount), 0) AS total_amount,
                    COUNT(DISTINCT committee_id) AS committee_count
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                GROUP BY donor_key
                ORDER BY total_amount DESC
                LIMIT ?
                """,
                (donor_limit,),
            ).fetchall()

        donor_keys = [row["donor_key"] for row in donor_rows if row["donor_key"]]
        if not donor_keys:
            return _empty_relationship_graph(
                donor_pool_size=0,
                source=source,
                min_shared_amount=min_shared_amount,
                min_shared_targets=min_shared_targets,
                candidate_component_available=False,
            )

        donor_lookup = {
            row["donor_key"]: {
                "donor_name": row["donor_name"] or row["donor_key"],
                "donor_city": row["donor_city"] or "",
                "donor_state": row["donor_state"] or "",
                "total_amount": float(row["total_amount"] or 0.0),
                "committee_count": int(row["committee_count"] or 0),
            }
            for row in donor_rows
        }

        placeholders = ",".join(["?"] * len(donor_keys))

        committee_pairs = conn.execute(
            f"""
            WITH donor_edges AS (
                SELECT donor_key, committee_id AS target_id, COALESCE(total_amount, 0) AS donor_amount
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                  AND committee_id IS NOT NULL
                  AND donor_key IN ({placeholders})
            )
            SELECT
                e1.donor_key AS donor_a,
                e2.donor_key AS donor_b,
                COUNT(*) AS shared_count,
                COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) AS shared_amount
            FROM donor_edges e1
            JOIN donor_edges e2
              ON e1.target_id = e2.target_id
             AND e1.donor_key < e2.donor_key
            GROUP BY e1.donor_key, e2.donor_key
            HAVING COUNT(*) >= ?
               AND COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) >= ?
            ORDER BY shared_amount DESC, shared_count DESC
            LIMIT ?
            """,
            [*donor_keys, min_shared_targets, min_shared_amount, edge_limit * 4],
        ).fetchall()

        candidate_pairs = []
        candidate_component_available = _table_exists(conn, "bulk_cmte_candidate_links_clean")
        if candidate_component_available:
            candidate_pairs = conn.execute(
                f"""
                WITH committee_candidate_counts AS (
                    SELECT committee_id_sbe, COUNT(DISTINCT candidate_id) AS candidate_count
                    FROM bulk_cmte_candidate_links_clean
                    WHERE candidate_id IS NOT NULL
                    GROUP BY committee_id_sbe
                ),
                donor_candidate_edges AS (
                    SELECT
                        a.donor_key,
                        l.candidate_id AS target_id,
                        COALESCE(
                            SUM(
                                COALESCE(a.total_amount, 0)
                                / CASE
                                      WHEN COALESCE(cc.candidate_count, 0) > 0 THEN cc.candidate_count
                                      ELSE 1
                                  END
                            ),
                            0
                        ) AS donor_amount
                    FROM analytics_donor_committee_agg a
                    JOIN bulk_cmte_candidate_links_clean l
                      ON l.committee_id_sbe = a.committee_id
                    LEFT JOIN committee_candidate_counts cc
                      ON cc.committee_id_sbe = l.committee_id_sbe
                    WHERE a.source = 'bulk_receipts'
                      AND a.donor_key IN ({placeholders})
                      AND l.candidate_id IS NOT NULL
                    GROUP BY a.donor_key, l.candidate_id
                )
                SELECT
                    e1.donor_key AS donor_a,
                    e2.donor_key AS donor_b,
                    COUNT(*) AS shared_count,
                    COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) AS shared_amount
                FROM donor_candidate_edges e1
                JOIN donor_candidate_edges e2
                  ON e1.target_id = e2.target_id
                 AND e1.donor_key < e2.donor_key
                GROUP BY e1.donor_key, e2.donor_key
                HAVING COUNT(*) >= ?
                   AND COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) >= ?
                ORDER BY shared_amount DESC, shared_count DESC
                LIMIT ?
                """,
                [*donor_keys, min_shared_targets, min_shared_amount, edge_limit * 4],
            ).fetchall()

        merged: dict[tuple[str, str], dict] = {}
        for row in committee_pairs:
            key = (row["donor_a"], row["donor_b"])
            merged[key] = {
                "donor_a": row["donor_a"],
                "donor_b": row["donor_b"],
                "shared_targets": int(row["shared_count"] or 0),
                "shared_committees": int(row["shared_count"] or 0),
                "shared_candidates": 0,
                "shared_amount": float(row["shared_amount"] or 0.0),
            }

        for row in candidate_pairs:
            key = (row["donor_a"], row["donor_b"])
            bucket = merged.setdefault(
                key,
                {
                    "donor_a": row["donor_a"],
                    "donor_b": row["donor_b"],
                    "shared_targets": 0,
                    "shared_committees": 0,
                    "shared_candidates": 0,
                    "shared_amount": 0.0,
                },
            )
            bucket["shared_targets"] += int(row["shared_count"] or 0)
            bucket["shared_candidates"] += int(row["shared_count"] or 0)
            bucket["shared_amount"] += float(row["shared_amount"] or 0.0)

        ranked = sorted(
            merged.values(),
            key=lambda row: (row["shared_amount"], row["shared_targets"]),
            reverse=True,
        )[:edge_limit]

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for row in ranked:
            donor_a = row["donor_a"]
            donor_b = row["donor_b"]
            donor_a_node = f"donor:{donor_a}"
            donor_b_node = f"donor:{donor_b}"
            donor_a_meta = donor_lookup.get(donor_a, {})
            donor_b_meta = donor_lookup.get(donor_b, {})

            nodes.setdefault(
                donor_a_node,
                {
                    "id": donor_a_node,
                    "label": donor_a_meta.get("donor_name") or donor_a,
                    "node_type": "donor",
                    "city": donor_a_meta.get("donor_city") or None,
                    "state": donor_a_meta.get("donor_state") or None,
                    "total_amount": round(float(donor_a_meta.get("total_amount") or 0.0), 2),
                    "committee_count": int(donor_a_meta.get("committee_count") or 0),
                },
            )
            nodes.setdefault(
                donor_b_node,
                {
                    "id": donor_b_node,
                    "label": donor_b_meta.get("donor_name") or donor_b,
                    "node_type": "donor",
                    "city": donor_b_meta.get("donor_city") or None,
                    "state": donor_b_meta.get("donor_state") or None,
                    "total_amount": round(float(donor_b_meta.get("total_amount") or 0.0), 2),
                    "committee_count": int(donor_b_meta.get("committee_count") or 0),
                },
            )
            edges.append(
                {
                    "source": donor_a_node,
                    "target": donor_b_node,
                    "edge_type": "donor_cogiving",
                    "weight": round(float(row["shared_amount"] or 0.0), 2),
                    "shared_targets": int(row["shared_targets"] or 0),
                    "shared_committees": int(row["shared_committees"] or 0),
                    "shared_candidates": int(row["shared_candidates"] or 0),
                    "source_label": donor_a_meta.get("donor_name") or donor_a,
                    "target_label": donor_b_meta.get("donor_name") or donor_b,
                }
            )

        centrality = _compute_graph_centrality(list(nodes.values()), edges, limit=150)
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "centrality": centrality,
            "summary": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "source": source,
                "donor_pool_size": len(donor_keys),
                "min_shared_amount": min_shared_amount,
                "min_shared_targets": min_shared_targets,
                "candidate_component_available": candidate_component_available,
            },
        }

    # Legacy fallback (donor->committee only from scraped contributions).
    donor_rows = conn.execute(
        """
        WITH donor_totals AS (
            SELECT
                d.id AS donor_id,
                d.name AS donor_name,
                d.address AS donor_address,
                COALESCE(SUM(ct.amount), 0) AS total_amount
            FROM contributions ct
            JOIN donors d ON d.id = ct.donor_id
            GROUP BY d.id, d.name, d.address
            ORDER BY total_amount DESC
            LIMIT ?
        )
        SELECT * FROM donor_totals
        """,
        (donor_limit,),
    ).fetchall()
    donor_ids = [row["donor_id"] for row in donor_rows]
    if not donor_ids:
        return _empty_relationship_graph(
            donor_pool_size=0,
            source=source,
            min_shared_amount=min_shared_amount,
            min_shared_targets=min_shared_targets,
            candidate_component_available=False,
        )

    donor_lookup = {
        int(row["donor_id"]): {
            "donor_name": row["donor_name"] or f"Donor {row['donor_id']}",
            "donor_address": row["donor_address"] or "",
            "total_amount": float(row["total_amount"] or 0.0),
        }
        for row in donor_rows
    }
    placeholders = ",".join(["?"] * len(donor_ids))
    pair_rows = conn.execute(
        f"""
        WITH donor_edges AS (
            SELECT
                ct.donor_id,
                r.committee_id AS target_id,
                COALESCE(SUM(ct.amount), 0) AS donor_amount
            FROM contributions ct
            JOIN reports r ON r.id = ct.report_id
            WHERE ct.donor_id IN ({placeholders})
            GROUP BY ct.donor_id, r.committee_id
        )
        SELECT
            e1.donor_id AS donor_a,
            e2.donor_id AS donor_b,
            COUNT(*) AS shared_count,
            COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) AS shared_amount
        FROM donor_edges e1
        JOIN donor_edges e2
          ON e1.target_id = e2.target_id
         AND e1.donor_id < e2.donor_id
        GROUP BY e1.donor_id, e2.donor_id
        HAVING COUNT(*) >= ?
           AND COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) >= ?
        ORDER BY shared_amount DESC, shared_count DESC
        LIMIT ?
        """,
        [*donor_ids, min_shared_targets, min_shared_amount, edge_limit],
    ).fetchall()

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for row in pair_rows:
        donor_a = int(row["donor_a"])
        donor_b = int(row["donor_b"])
        donor_a_node = f"donor:{donor_a}"
        donor_b_node = f"donor:{donor_b}"
        donor_a_meta = donor_lookup.get(donor_a, {})
        donor_b_meta = donor_lookup.get(donor_b, {})
        nodes.setdefault(
            donor_a_node,
            {
                "id": donor_a_node,
                "label": donor_a_meta.get("donor_name") or f"Donor {donor_a}",
                "node_type": "donor",
                "total_amount": round(float(donor_a_meta.get("total_amount") or 0.0), 2),
            },
        )
        nodes.setdefault(
            donor_b_node,
            {
                "id": donor_b_node,
                "label": donor_b_meta.get("donor_name") or f"Donor {donor_b}",
                "node_type": "donor",
                "total_amount": round(float(donor_b_meta.get("total_amount") or 0.0), 2),
            },
        )
        edges.append(
            {
                "source": donor_a_node,
                "target": donor_b_node,
                "edge_type": "donor_cogiving",
                "weight": round(float(row["shared_amount"] or 0.0), 2),
                "shared_targets": int(row["shared_count"] or 0),
                "shared_committees": int(row["shared_count"] or 0),
                "shared_candidates": 0,
                "source_label": donor_a_meta.get("donor_name") or f"Donor {donor_a}",
                "target_label": donor_b_meta.get("donor_name") or f"Donor {donor_b}",
            }
        )

    centrality = _compute_graph_centrality(list(nodes.values()), edges, limit=150)
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "source": source,
            "donor_pool_size": len(donor_ids),
            "min_shared_amount": min_shared_amount,
            "min_shared_targets": min_shared_targets,
            "candidate_component_available": False,
        },
    }


def get_committee_similarity_network(
    conn: sqlite3.Connection,
    committee_limit: int = 500,
    edge_limit: int = 1200,
    min_shared_donors: int = 3,
    min_shared_amount: float = 10000.0,
) -> dict:
    """Build committee-to-committee similarity network from shared donors."""
    committee_limit = max(50, min(int(committee_limit), 5000))
    edge_limit = max(50, min(int(edge_limit), 10000))
    min_shared_donors = max(1, int(min_shared_donors))
    min_shared_amount = max(0.0, float(min_shared_amount))
    # Constrain the donor universe for the committee self-join path so query time
    # stays bounded on large production datasets.
    committee_donor_cap = min(max(int(committee_limit) * 6, 600), 6000)
    source = _donor_flow_source(conn)

    if source == "bulk_receipts" and _table_exists(conn, "analytics_donor_committee_agg"):
        committee_rows = conn.execute(
            """
            SELECT
                committee_id,
                MAX(committee_name) AS committee_name,
                COALESCE(SUM(total_amount), 0) AS total_amount,
                COUNT(DISTINCT donor_key) AS donor_count
            FROM analytics_donor_committee_agg
            WHERE source = 'bulk_receipts'
              AND committee_id IS NOT NULL
            GROUP BY committee_id
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            (committee_limit,),
        ).fetchall()
        committee_ids = [row["committee_id"] for row in committee_rows]
        if not committee_ids:
            return _empty_relationship_graph(
                source=source,
                committee_pool_size=0,
                min_shared_donors=min_shared_donors,
                min_shared_amount=min_shared_amount,
            )

        committee_lookup = {
            row["committee_id"]: {
                "committee_name": row["committee_name"] or f"Committee {row['committee_id']}",
                "total_amount": float(row["total_amount"] or 0.0),
                "donor_count": int(row["donor_count"] or 0),
            }
            for row in committee_rows
        }
        committee_top_donor_cte = """
            top_committee_donors AS (
                SELECT donor_key
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                  AND donor_key IS NOT NULL
                GROUP BY donor_key
                ORDER BY SUM(total_amount) DESC
                LIMIT ?
            ),
        """
        if _rows_for_source_exist(conn, "analytics_donor_summary", "bulk_receipts"):
            committee_top_donor_cte = """
            top_committee_donors AS (
                SELECT donor_key
                FROM analytics_donor_summary
                WHERE source = 'bulk_receipts'
                  AND donor_key IS NOT NULL
                ORDER BY total_amount DESC
                LIMIT ?
            ),
            """
        pair_rows = conn.execute(
            f"""
            WITH top_committees AS (
                SELECT committee_id
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                  AND committee_id IS NOT NULL
                GROUP BY committee_id
                ORDER BY SUM(total_amount) DESC
                LIMIT ?
            ),
            {committee_top_donor_cte}
            donor_edges AS (
                SELECT
                    a.donor_key,
                    a.committee_id,
                    COALESCE(a.total_amount, 0) AS donor_amount
                FROM analytics_donor_committee_agg a
                JOIN top_committees tc ON tc.committee_id = a.committee_id
                JOIN top_committee_donors td ON td.donor_key = a.donor_key
                WHERE a.source = 'bulk_receipts'
            )
            SELECT
                e1.committee_id AS committee_a,
                e2.committee_id AS committee_b,
                COUNT(*) AS shared_donor_count,
                COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) AS shared_amount
            FROM donor_edges e1
            JOIN donor_edges e2
              ON e1.donor_key = e2.donor_key
             AND e1.committee_id < e2.committee_id
            GROUP BY e1.committee_id, e2.committee_id
            HAVING COUNT(*) >= ?
               AND COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) >= ?
            ORDER BY shared_amount DESC, shared_donor_count DESC
            LIMIT ?
            """,
            [committee_limit, committee_donor_cap, min_shared_donors, min_shared_amount, edge_limit],
        ).fetchall()

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for row in pair_rows:
            committee_a = row["committee_a"]
            committee_b = row["committee_b"]
            committee_a_node = f"committee:{committee_a}"
            committee_b_node = f"committee:{committee_b}"
            committee_a_meta = committee_lookup.get(committee_a, {})
            committee_b_meta = committee_lookup.get(committee_b, {})
            donor_a = int(committee_a_meta.get("donor_count") or 0)
            donor_b = int(committee_b_meta.get("donor_count") or 0)
            shared_donor_count = int(row["shared_donor_count"] or 0)
            denominator = donor_a + donor_b - shared_donor_count
            jaccard = (shared_donor_count / denominator) if denominator > 0 else 0.0
            min_total = min(
                float(committee_a_meta.get("total_amount") or 0.0),
                float(committee_b_meta.get("total_amount") or 0.0),
            )
            shared_amount = float(row["shared_amount"] or 0.0)
            dollar_overlap = (shared_amount / min_total) if min_total > 0 else 0.0

            nodes.setdefault(
                committee_a_node,
                {
                    "id": committee_a_node,
                    "label": committee_a_meta.get("committee_name") or f"Committee {committee_a}",
                    "node_type": "committee",
                    "total_amount": round(float(committee_a_meta.get("total_amount") or 0.0), 2),
                    "donor_count": donor_a,
                },
            )
            nodes.setdefault(
                committee_b_node,
                {
                    "id": committee_b_node,
                    "label": committee_b_meta.get("committee_name") or f"Committee {committee_b}",
                    "node_type": "committee",
                    "total_amount": round(float(committee_b_meta.get("total_amount") or 0.0), 2),
                    "donor_count": donor_b,
                },
            )
            edges.append(
                {
                    "source": committee_a_node,
                    "target": committee_b_node,
                    "edge_type": "committee_similarity",
                    "weight": round(shared_amount, 2),
                    "shared_donor_count": shared_donor_count,
                    "jaccard": round(jaccard, 4),
                    "dollar_overlap_ratio": round(dollar_overlap, 4),
                    "source_label": committee_a_meta.get("committee_name") or f"Committee {committee_a}",
                    "target_label": committee_b_meta.get("committee_name") or f"Committee {committee_b}",
                }
            )

        centrality = _compute_graph_centrality(list(nodes.values()), edges, limit=150)
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "centrality": centrality,
            "summary": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "source": source,
                "committee_pool_size": len(committee_ids),
                "committee_donor_cap": committee_donor_cap,
                "min_shared_donors": min_shared_donors,
                "min_shared_amount": min_shared_amount,
            },
        }

    # Legacy fallback from scraped contributions.
    committee_rows = conn.execute(
        """
        SELECT
            c.id AS committee_id,
            c.name AS committee_name,
            COALESCE(SUM(ct.amount), 0) AS total_amount,
            COUNT(DISTINCT ct.donor_id) AS donor_count
        FROM contributions ct
        JOIN reports r ON r.id = ct.report_id
        JOIN committees c ON c.id = r.committee_id
        GROUP BY c.id, c.name
        ORDER BY total_amount DESC
        LIMIT ?
        """,
        (committee_limit,),
    ).fetchall()
    committee_ids = [row["committee_id"] for row in committee_rows]
    if not committee_ids:
        return _empty_relationship_graph(
            source=source,
            committee_pool_size=0,
            min_shared_donors=min_shared_donors,
            min_shared_amount=min_shared_amount,
        )

    committee_lookup = {
        int(row["committee_id"]): {
            "committee_name": row["committee_name"] or f"Committee {row['committee_id']}",
            "total_amount": float(row["total_amount"] or 0.0),
            "donor_count": int(row["donor_count"] or 0),
        }
        for row in committee_rows
    }
    placeholders = ",".join(["?"] * len(committee_ids))
    pair_rows = conn.execute(
        f"""
        WITH donor_edges AS (
            SELECT
                ct.donor_id,
                r.committee_id,
                COALESCE(SUM(ct.amount), 0) AS donor_amount
            FROM contributions ct
            JOIN reports r ON r.id = ct.report_id
            WHERE r.committee_id IN ({placeholders})
            GROUP BY ct.donor_id, r.committee_id
        )
        SELECT
            e1.committee_id AS committee_a,
            e2.committee_id AS committee_b,
            COUNT(*) AS shared_donor_count,
            COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) AS shared_amount
        FROM donor_edges e1
        JOIN donor_edges e2
          ON e1.donor_id = e2.donor_id
         AND e1.committee_id < e2.committee_id
        GROUP BY e1.committee_id, e2.committee_id
        HAVING COUNT(*) >= ?
           AND COALESCE(SUM(MIN(e1.donor_amount, e2.donor_amount)), 0) >= ?
        ORDER BY shared_amount DESC, shared_donor_count DESC
        LIMIT ?
        """,
        [*committee_ids, min_shared_donors, min_shared_amount, edge_limit],
    ).fetchall()

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for row in pair_rows:
        committee_a = int(row["committee_a"])
        committee_b = int(row["committee_b"])
        committee_a_node = f"committee:{committee_a}"
        committee_b_node = f"committee:{committee_b}"
        committee_a_meta = committee_lookup.get(committee_a, {})
        committee_b_meta = committee_lookup.get(committee_b, {})
        donor_a = int(committee_a_meta.get("donor_count") or 0)
        donor_b = int(committee_b_meta.get("donor_count") or 0)
        shared_donor_count = int(row["shared_donor_count"] or 0)
        denominator = donor_a + donor_b - shared_donor_count
        jaccard = (shared_donor_count / denominator) if denominator > 0 else 0.0
        min_total = min(
            float(committee_a_meta.get("total_amount") or 0.0),
            float(committee_b_meta.get("total_amount") or 0.0),
        )
        shared_amount = float(row["shared_amount"] or 0.0)
        dollar_overlap = (shared_amount / min_total) if min_total > 0 else 0.0

        nodes.setdefault(
            committee_a_node,
            {
                "id": committee_a_node,
                "label": committee_a_meta.get("committee_name") or f"Committee {committee_a}",
                "node_type": "committee",
                "total_amount": round(float(committee_a_meta.get("total_amount") or 0.0), 2),
                "donor_count": donor_a,
            },
        )
        nodes.setdefault(
            committee_b_node,
            {
                "id": committee_b_node,
                "label": committee_b_meta.get("committee_name") or f"Committee {committee_b}",
                "node_type": "committee",
                "total_amount": round(float(committee_b_meta.get("total_amount") or 0.0), 2),
                "donor_count": donor_b,
            },
        )
        edges.append(
            {
                "source": committee_a_node,
                "target": committee_b_node,
                "edge_type": "committee_similarity",
                "weight": round(shared_amount, 2),
                "shared_donor_count": shared_donor_count,
                "jaccard": round(jaccard, 4),
                "dollar_overlap_ratio": round(dollar_overlap, 4),
                "source_label": committee_a_meta.get("committee_name") or f"Committee {committee_a}",
                "target_label": committee_b_meta.get("committee_name") or f"Committee {committee_b}",
            }
        )

    centrality = _compute_graph_centrality(list(nodes.values()), edges, limit=150)
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "source": source,
            "committee_pool_size": len(committee_ids),
            "min_shared_donors": min_shared_donors,
            "min_shared_amount": min_shared_amount,
        },
    }


def _candidate_competition_from_rows(
    rows: list[dict],
    edge_limit: int,
    min_shared_donors: int,
    min_shared_amount: float,
    max_candidates_per_donor: int = 20,
) -> dict:
    if not rows:
        return _empty_relationship_graph(
            min_shared_donors=min_shared_donors,
            min_shared_amount=min_shared_amount,
        )

    donor_targets: dict[str, list[dict]] = defaultdict(list)
    nodes: dict[str, dict] = {}
    for row in rows:
        donor_key = str(row.get("donor_key") or "").strip()
        candidate_id = str(row.get("candidate_node_id") or "").strip()
        if not donor_key or not candidate_id:
            continue
        amount = float(row.get("donor_amount") or 0.0)
        if amount <= 0:
            continue

        donor_targets[donor_key].append(
            {
                "candidate_node_id": candidate_id,
                "candidate_label": row.get("candidate_label") or candidate_id,
                "system": row.get("system") or "unknown",
                "donor_amount": amount,
            }
        )
        node = nodes.setdefault(
            candidate_id,
            {
                "id": candidate_id,
                "label": row.get("candidate_label") or candidate_id,
                "node_type": "candidate",
                "system": row.get("system") or "unknown",
                "total_amount": 0.0,
                "donor_count": 0,
                "_donors": set(),
            },
        )
        node["total_amount"] += amount
        node["_donors"].add(donor_key)

    pair_map: dict[tuple[str, str], dict] = {}
    for targets in donor_targets.values():
        dedup: dict[str, dict] = {}
        for row in targets:
            existing = dedup.get(row["candidate_node_id"])
            if not existing or float(row["donor_amount"]) > float(existing["donor_amount"]):
                dedup[row["candidate_node_id"]] = row
        ranked = sorted(
            dedup.values(),
            key=lambda row: float(row["donor_amount"]),
            reverse=True,
        )[: max(2, int(max_candidates_per_donor))]
        for idx, left in enumerate(ranked):
            for right in ranked[idx + 1:]:
                left_id = left["candidate_node_id"]
                right_id = right["candidate_node_id"]
                if left_id == right_id:
                    continue
                source = min(left_id, right_id)
                target = max(left_id, right_id)
                shared_amount = min(float(left["donor_amount"]), float(right["donor_amount"]))
                key = (source, target)
                bucket = pair_map.setdefault(
                    key,
                    {
                        "source": source,
                        "target": target,
                        "edge_type": "candidate_competition",
                        "weight": 0.0,
                        "shared_donor_count": 0,
                        "shared_state_amount": 0.0,
                        "shared_federal_amount": 0.0,
                        "shared_cross_amount": 0.0,
                    },
                )
                bucket["weight"] += shared_amount
                bucket["shared_donor_count"] += 1
                systems = {left.get("system"), right.get("system")}
                if systems == {"state"}:
                    bucket["shared_state_amount"] += shared_amount
                elif systems == {"federal"}:
                    bucket["shared_federal_amount"] += shared_amount
                else:
                    bucket["shared_cross_amount"] += shared_amount

    edges: list[dict] = []
    for row in pair_map.values():
        if int(row["shared_donor_count"]) < min_shared_donors:
            continue
        if float(row["weight"]) < min_shared_amount:
            continue
        source_node = nodes.get(row["source"], {})
        target_node = nodes.get(row["target"], {})
        source_system = source_node.get("system")
        target_system = target_node.get("system")
        if source_system == target_system:
            system_mix = source_system
        else:
            system_mix = "cross_system"
        edges.append(
            {
                **row,
                "weight": round(float(row["weight"] or 0.0), 2),
                "shared_state_amount": round(float(row["shared_state_amount"] or 0.0), 2),
                "shared_federal_amount": round(float(row["shared_federal_amount"] or 0.0), 2),
                "shared_cross_amount": round(float(row["shared_cross_amount"] or 0.0), 2),
                "source_label": source_node.get("label") or row["source"],
                "target_label": target_node.get("label") or row["target"],
                "system_mix": system_mix,
            }
        )
    edges.sort(
        key=lambda row: (
            float(row["weight"]),
            int(row["shared_donor_count"]),
            row.get("source_label") or "",
        ),
        reverse=True,
    )
    edges = edges[: max(1, int(edge_limit))]

    used_nodes: dict[str, dict] = {}
    for edge in edges:
        for node_id in (edge["source"], edge["target"]):
            node = nodes.get(node_id)
            if not node:
                continue
            used_nodes[node_id] = node

    final_nodes: list[dict] = []
    for node in used_nodes.values():
        final_nodes.append(
            {
                "id": node["id"],
                "label": node["label"],
                "node_type": "candidate",
                "system": node.get("system"),
                "total_amount": round(float(node.get("total_amount") or 0.0), 2),
                "donor_count": len(node.get("_donors") or []),
            }
        )
    final_nodes.sort(key=lambda row: (row["total_amount"], row["donor_count"]), reverse=True)

    centrality = _compute_graph_centrality(final_nodes, edges, limit=150)
    return {
        "nodes": final_nodes,
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(final_nodes),
            "edge_count": len(edges),
            "candidate_pool_size": len(nodes),
            "donor_pool_size": len(donor_targets),
            "min_shared_donors": min_shared_donors,
            "min_shared_amount": min_shared_amount,
        },
    }


def get_candidate_competition_networks(
    conn: sqlite3.Connection,
    candidate_limit: int = 250,
    edge_limit: int = 1200,
    min_shared_donors: int = 2,
    min_shared_amount: float = 2500.0,
) -> dict:
    """Build state, federal, and combined candidate competition networks."""
    candidate_limit = max(50, min(int(candidate_limit), 2000))
    edge_limit = max(50, min(int(edge_limit), 10000))
    min_shared_donors = max(1, int(min_shared_donors))
    min_shared_amount = max(0.0, float(min_shared_amount))
    candidate_donor_cap = min(max(int(candidate_limit) * 25, 1500), 8000)

    state_rows: list[dict] = []
    state_available = _table_exists(conn, "analytics_donor_committee_agg") and _table_exists(
        conn, "bulk_cmte_candidate_links_clean"
    )
    # Resolve candidate names: prefer bulk_candidates_clean table (has candidate_full_name),
    # fall back to columns on the links table, then to raw candidate_id.
    _has_candidates_table = _table_exists(conn, "bulk_candidates_clean")
    if _has_candidates_table:
        state_candidate_name_expr = (
            "COALESCE(NULLIF(TRIM(bc.candidate_full_name), ''), 'Candidate ' || l.candidate_id)"
        )
    elif _column_exists(conn, "bulk_cmte_candidate_links_clean", "candidate_full_name"):
        state_candidate_name_expr = (
            "COALESCE(NULLIF(TRIM(l.candidate_full_name), ''), 'Candidate ' || l.candidate_id)"
        )
    elif _column_exists(conn, "bulk_cmte_candidate_links_clean", "candidate_name"):
        state_candidate_name_expr = (
            "COALESCE(NULLIF(TRIM(l.candidate_name), ''), 'Candidate ' || l.candidate_id)"
        )
    else:
        state_candidate_name_expr = "'Candidate ' || l.candidate_id"
    state_top_donor_cte = """
                top_state_donors AS (
                    SELECT donor_key
                    FROM analytics_donor_committee_agg
                    WHERE source = 'bulk_receipts'
                      AND donor_key IS NOT NULL
                    GROUP BY donor_key
                    ORDER BY SUM(total_amount) DESC
                    LIMIT ?
                ),
    """
    if _rows_for_source_exist(conn, "analytics_donor_summary", "bulk_receipts"):
        state_top_donor_cte = """
                top_state_donors AS (
                    SELECT donor_key
                    FROM analytics_donor_summary
                    WHERE source = 'bulk_receipts'
                      AND donor_key IS NOT NULL
                    ORDER BY total_amount DESC
                    LIMIT ?
                ),
        """
    if state_available:
        state_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                WITH committee_candidate_counts AS (
                    SELECT committee_id_sbe, COUNT(DISTINCT candidate_id) AS candidate_count
                    FROM bulk_cmte_candidate_links_clean
                    WHERE candidate_id IS NOT NULL
                    GROUP BY committee_id_sbe
                ),
                {state_top_donor_cte}
                donor_candidate AS (
                    SELECT
                        l.candidate_id,
                        MAX({state_candidate_name_expr}) AS candidate_name,
                        a.donor_key,
                        COALESCE(
                            SUM(
                                COALESCE(a.total_amount, 0)
                                / CASE
                                      WHEN COALESCE(cc.candidate_count, 0) > 0 THEN cc.candidate_count
                                      ELSE 1
                                  END
                            ),
                            0
                        ) AS donor_amount
                    FROM analytics_donor_committee_agg a
                    JOIN top_state_donors td
                      ON td.donor_key = a.donor_key
                    JOIN bulk_cmte_candidate_links_clean l
                      ON l.committee_id_sbe = a.committee_id
                    {"LEFT JOIN bulk_candidates_clean bc ON bc.candidate_id = l.candidate_id" if _has_candidates_table else ""}
                    LEFT JOIN committee_candidate_counts cc
                      ON cc.committee_id_sbe = l.committee_id_sbe
                    WHERE a.source = 'bulk_receipts'
                      AND l.candidate_id IS NOT NULL
                    GROUP BY l.candidate_id, a.donor_key
                ),
                top_candidates AS (
                    SELECT candidate_id
                    FROM donor_candidate
                    GROUP BY candidate_id
                    ORDER BY SUM(donor_amount) DESC
                    LIMIT ?
                )
                SELECT
                    dc.candidate_id,
                    dc.candidate_name,
                    dc.donor_key,
                    dc.donor_amount
                FROM donor_candidate dc
                JOIN top_candidates tc ON tc.candidate_id = dc.candidate_id
                WHERE dc.donor_amount > 0
                """,
                (candidate_donor_cap, candidate_limit),
            ).fetchall()
        ]
    for row in state_rows:
        row["candidate_node_id"] = f"state:{row['candidate_id']}"
        row["candidate_label"] = f"{row.get('candidate_name') or row['candidate_id']} (State)"
        row["system"] = "state"

    federal_rows: list[dict] = []
    federal_available = _table_exists(conn, "fec_schedule_a_contributions")
    if federal_available:
        federal_rows = [
            dict(row)
            for row in conn.execute(
                """
                WITH top_federal_donors AS (
                    SELECT
                        COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) AS donor_key
                    FROM fec_schedule_a_contributions sa
                    WHERE sa.candidate_id IS NOT NULL
                    GROUP BY donor_key
                    ORDER BY SUM(sa.contribution_receipt_amount) DESC
                    LIMIT ?
                ),
                donor_candidate AS (
                    SELECT
                        COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) AS donor_key,
                        sa.candidate_id AS candidate_id,
                        COALESCE(MAX(sa.candidate_name), 'Candidate ' || sa.candidate_id) AS candidate_name,
                        COALESCE(SUM(sa.contribution_receipt_amount), 0) AS donor_amount
                    FROM fec_schedule_a_contributions sa
                    JOIN top_federal_donors td
                      ON td.donor_key = COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id)
                    WHERE sa.candidate_id IS NOT NULL
                    GROUP BY
                        COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id),
                        sa.candidate_id
                ),
                top_candidates AS (
                    SELECT candidate_id
                    FROM donor_candidate
                    GROUP BY candidate_id
                    ORDER BY SUM(donor_amount) DESC
                    LIMIT ?
                )
                SELECT
                    dc.candidate_id,
                    dc.candidate_name,
                    dc.donor_key,
                    dc.donor_amount
                FROM donor_candidate dc
                JOIN top_candidates tc ON tc.candidate_id = dc.candidate_id
                WHERE dc.donor_amount > 0
                """,
                (candidate_donor_cap, candidate_limit),
            ).fetchall()
        ]
    for row in federal_rows:
        row["candidate_node_id"] = f"federal:{row['candidate_id']}"
        row["candidate_label"] = f"{row.get('candidate_name') or row['candidate_id']} (Federal)"
        row["system"] = "federal"

    state_network = _candidate_competition_from_rows(
        state_rows,
        edge_limit=edge_limit,
        min_shared_donors=min_shared_donors,
        min_shared_amount=min_shared_amount,
    )
    state_network["summary"]["available"] = state_available
    state_network["summary"]["candidate_rows"] = len(state_rows)

    federal_network = _candidate_competition_from_rows(
        federal_rows,
        edge_limit=edge_limit,
        min_shared_donors=min_shared_donors,
        min_shared_amount=min_shared_amount,
    )
    federal_network["summary"]["available"] = federal_available
    federal_network["summary"]["candidate_rows"] = len(federal_rows)

    local_to_federal: dict[str, str] = {}
    if _table_exists(conn, "fec_local_donor_matches"):
        match_rows = conn.execute(
            """
            SELECT local_donor_key, federal_donor_entity_key, MAX(confidence_score) AS confidence_score
            FROM fec_local_donor_matches
            WHERE local_donor_key IS NOT NULL
              AND federal_donor_entity_key IS NOT NULL
            GROUP BY local_donor_key, federal_donor_entity_key
            ORDER BY confidence_score DESC
            """
        ).fetchall()
        for row in match_rows:
            local_key = (row["local_donor_key"] or "").strip()
            federal_key = (row["federal_donor_entity_key"] or "").strip()
            if local_key and federal_key and local_key not in local_to_federal:
                local_to_federal[local_key] = federal_key

    combined_rows: list[dict] = []
    for row in state_rows:
        local_key = (row.get("donor_key") or "").strip()
        if not local_key:
            continue
        mapped_federal = local_to_federal.get(local_key)
        bridge_key = f"federal:{mapped_federal}" if mapped_federal else f"local:{local_key}"
        combined_rows.append(
            {
                "candidate_node_id": row["candidate_node_id"],
                "candidate_label": row["candidate_label"],
                "donor_key": bridge_key,
                "donor_amount": row.get("donor_amount"),
                "system": "state",
            }
        )
    for row in federal_rows:
        donor_key = (row.get("donor_key") or "").strip()
        if not donor_key:
            continue
        combined_rows.append(
            {
                "candidate_node_id": row["candidate_node_id"],
                "candidate_label": row["candidate_label"],
                "donor_key": f"federal:{donor_key}",
                "donor_amount": row.get("donor_amount"),
                "system": "federal",
            }
        )

    combined_network = _candidate_competition_from_rows(
        combined_rows,
        edge_limit=edge_limit,
        min_shared_donors=min_shared_donors,
        min_shared_amount=min_shared_amount,
    )
    combined_network["summary"]["available"] = bool(state_rows or federal_rows)
    combined_network["summary"]["bridge_match_count"] = len(local_to_federal)
    combined_network["summary"]["candidate_rows"] = len(combined_rows)

    return {
        "state": state_network,
        "federal": federal_network,
        "combined": combined_network,
        "summary": {
            "candidate_limit": candidate_limit,
            "candidate_donor_cap": candidate_donor_cap,
            "edge_limit": edge_limit,
            "min_shared_donors": min_shared_donors,
            "min_shared_amount": min_shared_amount,
            "state_available": state_available,
            "federal_available": federal_available,
            "bridge_match_count": len(local_to_federal),
        },
    }


def get_lobbying_influence_graph(
    conn: sqlite3.Connection,
    client_limit: int = 120,
    edge_limit: int = 1500,
) -> dict:
    """Build lobbying client/entity/donor/payee/committee influence graph."""
    client_limit = max(20, min(int(client_limit), 2000))
    edge_limit = max(50, min(int(edge_limit), 10000))
    required = {
        "lobbying_clients": _table_exists(conn, "lobbying_clients"),
        "lobbying_entities": _table_exists(conn, "lobbying_entities"),
        "lobbying_entity_clients": _table_exists(conn, "lobbying_entity_clients"),
    }
    if not all(required.values()):
        return _empty_relationship_graph(
            required_tables=required,
            has_donor_matches=_table_exists(conn, "lobbying_donor_matches"),
            has_payee_matches=_table_exists(conn, "lobbying_expenditure_matches"),
            has_donor_committee_edges=_table_exists(conn, "analytics_donor_committee_agg"),
        )

    client_rows = conn.execute(
        """
        WITH donor_match_counts AS (
            SELECT client_id, COUNT(*) AS donor_match_count
            FROM lobbying_donor_matches
            GROUP BY client_id
        )
        SELECT
            c.client_id,
            c.client_name,
            COUNT(DISTINCT ec.entity_id) AS entity_count,
            COALESCE(MAX(dmc.donor_match_count), 0) AS donor_match_count
        FROM lobbying_clients c
        LEFT JOIN lobbying_entity_clients ec ON ec.client_id = c.client_id
        LEFT JOIN donor_match_counts dmc ON dmc.client_id = c.client_id
        GROUP BY c.client_id, c.client_name
        ORDER BY donor_match_count DESC, entity_count DESC, c.client_name ASC
        LIMIT ?
        """,
        (client_limit,),
    ).fetchall()
    client_ids = [row["client_id"] for row in client_rows]
    if not client_ids:
        return _empty_relationship_graph(
            required_tables=required,
            has_donor_matches=_table_exists(conn, "lobbying_donor_matches"),
            has_payee_matches=_table_exists(conn, "lobbying_expenditure_matches"),
            has_donor_committee_edges=_table_exists(conn, "analytics_donor_committee_agg"),
            client_pool_size=0,
        )

    client_placeholders = ",".join(["?"] * len(client_ids))
    entity_rows = conn.execute(
        f"""
        SELECT
            ec.client_id,
            ec.entity_id,
            e.entity_name,
            COUNT(*) AS reg_year_count
        FROM lobbying_entity_clients ec
        JOIN lobbying_entities e ON e.entity_id = ec.entity_id
        WHERE ec.client_id IN ({client_placeholders})
        GROUP BY ec.client_id, ec.entity_id, e.entity_name
        ORDER BY reg_year_count DESC, e.entity_name ASC
        """,
        client_ids,
    ).fetchall()
    entity_ids = sorted({row["entity_id"] for row in entity_rows})

    donor_match_rows = []
    has_donor_matches = _table_exists(conn, "lobbying_donor_matches")
    if has_donor_matches:
        donor_match_rows = conn.execute(
            f"""
            SELECT
                client_id,
                donor_key,
                donor_name,
                score
            FROM lobbying_donor_matches
            WHERE client_id IN ({client_placeholders})
            ORDER BY score DESC
            LIMIT ?
            """,
            [*client_ids, edge_limit * 2],
        ).fetchall()

    has_payee_matches = _table_exists(conn, "lobbying_expenditure_matches")
    expenditure_rows = []
    if has_payee_matches:
        query = f"""
            SELECT
                source_type,
                source_id,
                source_name,
                payee_name,
                committee_id_sbe,
                score
            FROM lobbying_expenditure_matches
            WHERE (
                source_type = 'client' AND source_id IN ({client_placeholders})
            )
        """
        params: list[object] = [*client_ids]
        if entity_ids:
            entity_placeholders = ",".join(["?"] * len(entity_ids))
            query += f" OR (source_type = 'entity' AND source_id IN ({entity_placeholders}))"
            params.extend(entity_ids)
        query += " ORDER BY score DESC LIMIT ?"
        params.append(edge_limit * 2)
        expenditure_rows = conn.execute(query, params).fetchall()

    payee_committee_amounts: dict[tuple[str, str], dict[str, float | int]] = {}
    if expenditure_rows and _table_exists(conn, "bulk_expenditures_clean"):
        matched_pair_keys = {
            (str(row["committee_id_sbe"]), _normalize_name(row["payee_name"]))
            for row in expenditure_rows
            if row["committee_id_sbe"] is not None and (row["payee_name"] or "").strip()
        }
        committee_ids = sorted({pair[0] for pair in matched_pair_keys})
        if committee_ids:
            placeholders = ",".join(["?"] * len(committee_ids))
            spend_rows = conn.execute(
                f"""
                SELECT
                    committee_id_sbe,
                    payee_last_or_business_name AS payee_name,
                    COALESCE(SUM(amount), 0) AS total_amount,
                    COUNT(*) AS txn_count
                FROM bulk_expenditures_clean
                WHERE amount > 0
                  AND CAST(committee_id_sbe AS TEXT) IN ({placeholders})
                  AND payee_last_or_business_name IS NOT NULL
                  AND TRIM(payee_last_or_business_name) != ''
                GROUP BY committee_id_sbe, payee_last_or_business_name
                """,
                committee_ids,
            ).fetchall()
            for row in spend_rows:
                key = (str(row["committee_id_sbe"]), _normalize_name(row["payee_name"]))
                if key not in matched_pair_keys:
                    continue
                payee_committee_amounts[key] = {
                    "total_amount": float(row["total_amount"] or 0.0),
                    "txn_count": int(row["txn_count"] or 0),
                }

    donor_committee_rows = []
    donor_keys = sorted({(row["donor_key"] or "").strip() for row in donor_match_rows if row["donor_key"]})
    has_donor_committee_edges = _table_exists(conn, "analytics_donor_committee_agg")
    if donor_keys and has_donor_committee_edges:
        donor_placeholders = ",".join(["?"] * len(donor_keys))
        donor_committee_rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT
                    donor_key,
                    committee_id,
                    committee_name,
                    total_amount,
                    ROW_NUMBER() OVER (
                        PARTITION BY donor_key
                        ORDER BY total_amount DESC, committee_name ASC
                    ) AS rn
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                  AND donor_key IN ({donor_placeholders})
            )
            SELECT donor_key, committee_id, committee_name, total_amount
            FROM ranked
            WHERE rn <= 5
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            [*donor_keys, edge_limit * 2],
        ).fetchall()

    nodes: dict[str, dict] = {}
    edge_map: dict[tuple[str, str, str], dict] = {}

    def add_node(node_id: str, label: str, node_type: str) -> None:
        if not node_id:
            return
        nodes.setdefault(
            node_id,
            {
                "id": node_id,
                "label": label or node_id,
                "node_type": node_type,
            },
        )

    def add_edge(
        source: str,
        target: str,
        edge_type: str,
        weight: float,
        source_label: str,
        target_label: str,
        *,
        extra: dict | None = None,
    ) -> None:
        if not source or not target or source == target:
            return
        key = (source, target, edge_type)
        bucket = edge_map.setdefault(
            key,
            {
                "source": source,
                "target": target,
                "edge_type": edge_type,
                "weight": 0.0,
                "row_count": 0,
                "source_label": source_label,
                "target_label": target_label,
            },
        )
        bucket["weight"] += float(weight or 0.0)
        bucket["row_count"] += 1
        if extra:
            for field, value in extra.items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    bucket[field] = value
                elif isinstance(value, (int, float)):
                    if field.endswith("_max"):
                        bucket[field] = max(float(bucket.get(field) or 0.0), float(value))
                    else:
                        bucket[field] = float(bucket.get(field) or 0.0) + float(value)
                else:
                    bucket[field] = value

    client_label = {row["client_id"]: row["client_name"] or f"Client {row['client_id']}" for row in client_rows}
    entity_label = {row["entity_id"]: row["entity_name"] or f"Entity {row['entity_id']}" for row in entity_rows}

    for row in client_rows:
        add_node(f"client:{row['client_id']}", client_label[row["client_id"]], "lobbying_client")

    for row in entity_rows:
        client_node = f"client:{row['client_id']}"
        entity_node = f"entity:{row['entity_id']}"
        add_node(entity_node, entity_label[row["entity_id"]], "lobbying_entity")
        add_edge(
            client_node,
            entity_node,
            "client_entity",
            float(row["reg_year_count"] or 1.0),
            client_label.get(row["client_id"], client_node),
            entity_label.get(row["entity_id"], entity_node),
            extra={
                **_edge_semantics(
                    "client_entity",
                    source_table="lobbying_entity_clients",
                ),
            },
        )

    for row in donor_match_rows:
        client_node = f"client:{row['client_id']}"
        donor_key = (row["donor_key"] or "").strip()
        if not donor_key:
            continue
        donor_node = f"donor:{donor_key}"
        donor_label = row["donor_name"] or donor_key
        add_node(donor_node, donor_label, "matched_donor")
        add_edge(
            client_node,
            donor_node,
            "client_donor_match",
            float(row["score"] or 0.0),
            client_label.get(row["client_id"], client_node),
            donor_label,
            extra={
                **_edge_semantics(
                    "client_donor_match",
                    source_table="lobbying_donor_matches",
                ),
                "match_score_sum": float(row["score"] or 0.0),
                "match_score_max": float(row["score"] or 0.0),
            },
        )

    for row in expenditure_rows:
        source_type = (row["source_type"] or "").strip().lower()
        source_id = row["source_id"]
        payee_name = (row["payee_name"] or "").strip()
        if not payee_name:
            continue
        committee_id = row["committee_id_sbe"]
        pair_key = (
            str(committee_id),
            _normalize_name(payee_name),
        ) if committee_id is not None else None
        pair_amount = float(payee_committee_amounts.get(pair_key, {}).get("total_amount") or 0.0) if pair_key else 0.0
        pair_txn_count = int(payee_committee_amounts.get(pair_key, {}).get("txn_count") or 0) if pair_key else 0

        payee_node = f"payee:{_stable_entity_key(payee_name)}"
        add_node(payee_node, payee_name, "matched_payee")

        if source_type == "client":
            source_node = f"client:{source_id}"
            source_label = client_label.get(source_id, source_node)
            edge_type = "client_payee_match"
        else:
            source_node = f"entity:{source_id}"
            source_label = entity_label.get(source_id, source_node)
            edge_type = "entity_payee_match"
        score = float(row["score"] or 0.0)
        add_edge(
            source_node,
            payee_node,
            edge_type,
            score,
            source_label,
            payee_name,
            extra={
                **_edge_semantics(
                    edge_type,
                    source_table="lobbying_expenditure_matches",
                ),
                "match_score_sum": score,
                "match_score_max": score,
                "linked_committee_spend_amount": pair_amount,
                "linked_committee_spend_transactions": pair_txn_count,
            },
        )

        if committee_id is not None:
            committee_node = f"committee:{committee_id}"
            committee_label = f"Committee {committee_id}"
            add_node(committee_node, committee_label, "committee")
            payee_committee_weight = pair_amount if pair_amount > 0 else score
            payee_committee_unit = "usd" if pair_amount > 0 else "score"
            payee_metric_label = "Matched committee spend (USD)" if pair_amount > 0 else "Name-match confidence score"
            add_edge(
                payee_node,
                committee_node,
                "payee_committee_match",
                payee_committee_weight,
                payee_name,
                committee_label,
                extra={
                    **_edge_semantics(
                        "payee_committee_match",
                        weight_unit=payee_committee_unit,
                        metric_label=payee_metric_label,
                        source_table="lobbying_expenditure_matches + bulk_expenditures_clean",
                    ),
                    "match_score_sum": score,
                    "match_score_max": score,
                    "matched_amount": pair_amount,
                    "matched_transaction_count": pair_txn_count,
                },
            )

    for row in donor_committee_rows:
        donor_key = (row["donor_key"] or "").strip()
        committee_id = row["committee_id"]
        if not donor_key or committee_id is None:
            continue
        donor_node = f"donor:{donor_key}"
        committee_node = f"committee:{committee_id}"
        donor_label_value = nodes.get(donor_node, {}).get("label") or donor_key
        committee_label_value = row["committee_name"] or f"Committee {committee_id}"
        add_node(donor_node, donor_label_value, "matched_donor")
        add_node(committee_node, committee_label_value, "committee")
        add_edge(
            donor_node,
            committee_node,
            "donor_committee_flow",
            float(row["total_amount"] or 0.0),
            donor_label_value,
            committee_label_value,
            extra={
                **_edge_semantics(
                    "donor_committee_flow",
                    source_table="analytics_donor_committee_agg",
                ),
            },
        )

    edges = sorted(
        edge_map.values(),
        key=lambda row: (float(row["weight"] or 0.0), int(row["row_count"] or 0)),
        reverse=True,
    )[:edge_limit]

    used_node_ids = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    final_nodes = [node for node_id, node in nodes.items() if node_id in used_node_ids]
    centrality = _compute_graph_centrality(final_nodes, edges, limit=150)

    return {
        "nodes": final_nodes,
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(final_nodes),
            "edge_count": len(edges),
            "required_tables": required,
            "has_donor_matches": has_donor_matches,
            "has_payee_matches": has_payee_matches,
            "has_donor_committee_edges": has_donor_committee_edges,
            "client_pool_size": len(client_ids),
            "edge_type_definitions": {
                edge_type: _edge_semantics(edge_type)
                for edge_type in sorted({edge["edge_type"] for edge in edges if edge.get("edge_type")})
            },
        },
    }


def get_irs527_ecosystem_graph(
    conn: sqlite3.Connection,
    org_limit: int = 150,
    edge_limit: int = 1800,
) -> dict:
    """Build 527 ecosystem graph across org/committee/recipient/director/donor links."""
    org_limit = max(20, min(int(org_limit), 2000))
    edge_limit = max(50, min(int(edge_limit), 10000))

    required = {"irs527_organizations": _table_exists(conn, "irs527_organizations")}
    if not required["irs527_organizations"]:
        return _empty_relationship_graph(
            required_tables=required,
            has_committee_matches=_table_exists(conn, "irs527_committee_matches"),
            has_recipient_matches=_table_exists(conn, "irs527_expenditure_recipient_matches"),
            has_director_donor_matches=_table_exists(conn, "irs527_director_donor_matches"),
            has_donor_committee_edges=_table_exists(conn, "analytics_donor_committee_agg"),
        )

    has_committee_matches = _table_exists(conn, "irs527_committee_matches")
    has_recipient_matches = _table_exists(conn, "irs527_expenditure_recipient_matches")
    has_director_donor_matches = _table_exists(conn, "irs527_director_donor_matches")

    committee_rows = []
    if has_committee_matches:
        committee_rows = conn.execute(
            """
            SELECT ein, org_name, committee_id_sbe, committee_name, score
            FROM irs527_committee_matches
            ORDER BY score DESC
            LIMIT ?
            """,
            (edge_limit * 2,),
        ).fetchall()

    recipient_rows = []
    if has_recipient_matches:
        recipient_rows = conn.execute(
            """
            SELECT ein, org_name, recipient_name, matched_type, matched_id, matched_name, score
            FROM irs527_expenditure_recipient_matches
            ORDER BY score DESC
            LIMIT ?
            """,
            (edge_limit * 2,),
        ).fetchall()

    recipient_amounts: dict[tuple[str, str], dict[str, float | int]] = {}
    if recipient_rows and _table_exists(conn, "irs527_expenditures"):
        recipient_keys = {
            ((row["ein"] or "").strip(), _normalize_name(row["recipient_name"]))
            for row in recipient_rows
            if (row["ein"] or "").strip() and (row["recipient_name"] or "").strip()
        }
        keep_ein_keys = sorted({ein for ein, _ in recipient_keys if ein})
        if keep_ein_keys:
            placeholders = ",".join(["?"] * len(keep_ein_keys))
            amount_rows = conn.execute(
                f"""
                SELECT
                    ein,
                    recipient_name,
                    COALESCE(SUM(amount), 0) AS total_amount,
                    COUNT(*) AS txn_count
                FROM irs527_expenditures
                WHERE ein IN ({placeholders})
                  AND COALESCE(amount, 0) > 0
                  AND recipient_name IS NOT NULL
                  AND TRIM(recipient_name) != ''
                GROUP BY ein, recipient_name
                """,
                keep_ein_keys,
            ).fetchall()
            for row in amount_rows:
                key = ((row["ein"] or "").strip(), _normalize_name(row["recipient_name"]))
                if key not in recipient_keys:
                    continue
                recipient_amounts[key] = {
                    "total_amount": float(row["total_amount"] or 0.0),
                    "txn_count": int(row["txn_count"] or 0),
                }

    director_rows = []
    if has_director_donor_matches:
        director_rows = conn.execute(
            """
            SELECT ein, org_name, director_name, donor_key, donor_name, score
            FROM irs527_director_donor_matches
            ORDER BY score DESC
            LIMIT ?
            """,
            (edge_limit * 2,),
        ).fetchall()

    org_scores: dict[str, float] = defaultdict(float)
    for row in committee_rows:
        org_scores[(row["ein"] or "").strip()] += float(row["score"] or 0.0)
    for row in recipient_rows:
        org_scores[(row["ein"] or "").strip()] += float(row["score"] or 0.0)
    for row in director_rows:
        org_scores[(row["ein"] or "").strip()] += float(row["score"] or 0.0)

    ranked_orgs = sorted(org_scores.items(), key=lambda item: item[1], reverse=True)[:org_limit]
    keep_eins = {ein for ein, _score in ranked_orgs if ein}
    if not keep_eins:
        org_rows = conn.execute(
            """
            SELECT ein, MAX(org_name) AS org_name
            FROM irs527_organizations
            GROUP BY ein
            ORDER BY org_name ASC
            LIMIT ?
            """,
            (org_limit,),
        ).fetchall()
        keep_eins = {(row["ein"] or "").strip() for row in org_rows if row["ein"]}

    committee_rows = [row for row in committee_rows if (row["ein"] or "").strip() in keep_eins]
    recipient_rows = [row for row in recipient_rows if (row["ein"] or "").strip() in keep_eins]
    director_rows = [row for row in director_rows if (row["ein"] or "").strip() in keep_eins]

    nodes: dict[str, dict] = {}
    edge_map: dict[tuple[str, str, str], dict] = {}

    def add_node(node_id: str, label: str, node_type: str) -> None:
        if not node_id:
            return
        nodes.setdefault(
            node_id,
            {
                "id": node_id,
                "label": label or node_id,
                "node_type": node_type,
            },
        )

    def add_edge(
        source: str,
        target: str,
        edge_type: str,
        weight: float,
        source_label: str,
        target_label: str,
        *,
        extra: dict | None = None,
    ) -> None:
        if not source or not target or source == target:
            return
        key = (source, target, edge_type)
        bucket = edge_map.setdefault(
            key,
            {
                "source": source,
                "target": target,
                "edge_type": edge_type,
                "weight": 0.0,
                "row_count": 0,
                "source_label": source_label,
                "target_label": target_label,
            },
        )
        bucket["weight"] += float(weight or 0.0)
        bucket["row_count"] += 1
        if extra:
            for field, value in extra.items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    bucket[field] = value
                elif isinstance(value, (int, float)):
                    if field.endswith("_max"):
                        bucket[field] = max(float(bucket.get(field) or 0.0), float(value))
                    else:
                        bucket[field] = float(bucket.get(field) or 0.0) + float(value)
                else:
                    bucket[field] = value

    for ein in keep_eins:
        if not ein:
            continue
        org_label_row = conn.execute(
            """
            SELECT MAX(org_name) AS org_name
            FROM irs527_organizations
            WHERE ein = ?
            """,
            (ein,),
        ).fetchone()
        org_label = (org_label_row["org_name"] if org_label_row else None) or f"527 {ein}"
        add_node(f"org:{ein}", org_label, "irs527_org")

    for row in committee_rows:
        ein = (row["ein"] or "").strip()
        if not ein:
            continue
        org_node = f"org:{ein}"
        org_label = nodes.get(org_node, {}).get("label") or row["org_name"] or f"527 {ein}"
        committee_id = row["committee_id_sbe"]
        committee_node = f"committee:{committee_id}"
        committee_label = row["committee_name"] or f"Committee {committee_id}"
        add_node(committee_node, committee_label, "committee")
        add_edge(
            org_node,
            committee_node,
            "org_committee_match",
            float(row["score"] or 0.0),
            org_label,
            committee_label,
            extra={
                **_edge_semantics(
                    "org_committee_match",
                    source_table="irs527_committee_matches",
                ),
                "match_score_sum": float(row["score"] or 0.0),
                "match_score_max": float(row["score"] or 0.0),
            },
        )

    for row in recipient_rows:
        ein = (row["ein"] or "").strip()
        if not ein:
            continue
        org_node = f"org:{ein}"
        org_label = nodes.get(org_node, {}).get("label") or row["org_name"] or f"527 {ein}"
        target_name = (row["matched_name"] or "").strip() or (row["recipient_name"] or "").strip()
        matched_type = (row["matched_type"] or "").strip().lower() or "target"
        matched_id = (row["matched_id"] or "").strip()
        if matched_type == "committee" and matched_id:
            target_node = f"committee:{matched_id}"
            target_type = "committee"
        else:
            stable = _stable_entity_key(target_name, matched_type, matched_id)
            target_node = f"recipient_target:{stable}"
            target_type = "recipient_target"
        target_label = target_name or target_node
        add_node(target_node, target_label, target_type)
        recipient_key = (ein, _normalize_name(row["recipient_name"]))
        recipient_amount = float(recipient_amounts.get(recipient_key, {}).get("total_amount") or 0.0)
        recipient_txn_count = int(recipient_amounts.get(recipient_key, {}).get("txn_count") or 0)
        score = float(row["score"] or 0.0)
        edge_weight = recipient_amount if recipient_amount > 0 else score
        edge_weight_unit = "usd" if recipient_amount > 0 else "score"
        metric_label = "Matched 527 expenditure amount (USD)" if recipient_amount > 0 else "Name-match confidence score"
        add_edge(
            org_node,
            target_node,
            "org_recipient_match",
            edge_weight,
            org_label,
            target_label,
            extra={
                **_edge_semantics(
                    "org_recipient_match",
                    weight_unit=edge_weight_unit,
                    metric_label=metric_label,
                    source_table="irs527_expenditure_recipient_matches + irs527_expenditures",
                ),
                "match_score_sum": score,
                "match_score_max": score,
                "matched_amount": recipient_amount,
                "matched_transaction_count": recipient_txn_count,
            },
        )

    donor_keys: set[str] = set()
    for row in director_rows:
        ein = (row["ein"] or "").strip()
        if not ein:
            continue
        org_node = f"org:{ein}"
        org_label = nodes.get(org_node, {}).get("label") or row["org_name"] or f"527 {ein}"
        director_name = (row["director_name"] or "").strip() or "Unknown Director"
        director_node = f"director:{_stable_entity_key(director_name, ein)}"
        add_node(director_node, director_name, "director")
        add_edge(
            org_node,
            director_node,
            "org_director",
            float(row["score"] or 0.0),
            org_label,
            director_name,
            extra={
                **_edge_semantics(
                    "org_director",
                    source_table="irs527_director_donor_matches",
                ),
                "match_score_sum": float(row["score"] or 0.0),
                "match_score_max": float(row["score"] or 0.0),
            },
        )
        donor_key = (row["donor_key"] or "").strip()
        donor_name = (row["donor_name"] or "").strip() or donor_key
        if donor_key:
            donor_node = f"donor:{donor_key}"
            add_node(donor_node, donor_name or donor_key, "matched_donor")
            add_edge(
                director_node,
                donor_node,
                "director_donor_match",
                float(row["score"] or 0.0),
                director_name,
                donor_name or donor_key,
                extra={
                    **_edge_semantics(
                        "director_donor_match",
                        source_table="irs527_director_donor_matches",
                    ),
                    "match_score_sum": float(row["score"] or 0.0),
                    "match_score_max": float(row["score"] or 0.0),
                },
            )
            donor_keys.add(donor_key)

    has_donor_committee_edges = _table_exists(conn, "analytics_donor_committee_agg")
    if donor_keys and has_donor_committee_edges:
        donor_placeholders = ",".join(["?"] * len(donor_keys))
        donor_committee_rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT
                    donor_key,
                    committee_id,
                    committee_name,
                    total_amount,
                    ROW_NUMBER() OVER (
                        PARTITION BY donor_key
                        ORDER BY total_amount DESC, committee_name ASC
                    ) AS rn
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                  AND donor_key IN ({donor_placeholders})
            )
            SELECT donor_key, committee_id, committee_name, total_amount
            FROM ranked
            WHERE rn <= 4
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            [*sorted(donor_keys), edge_limit * 2],
        ).fetchall()
        for row in donor_committee_rows:
            donor_key = (row["donor_key"] or "").strip()
            committee_id = row["committee_id"]
            if not donor_key or committee_id is None:
                continue
            donor_node = f"donor:{donor_key}"
            committee_node = f"committee:{committee_id}"
            donor_label = nodes.get(donor_node, {}).get("label") or donor_key
            committee_label = row["committee_name"] or f"Committee {committee_id}"
            add_node(committee_node, committee_label, "committee")
            add_edge(
                donor_node,
                committee_node,
                "donor_committee_flow",
                float(row["total_amount"] or 0.0),
                donor_label,
                committee_label,
                extra={
                    **_edge_semantics(
                        "donor_committee_flow",
                        source_table="analytics_donor_committee_agg",
                    ),
                },
            )

    edges = sorted(
        edge_map.values(),
        key=lambda row: (float(row["weight"] or 0.0), int(row["row_count"] or 0)),
        reverse=True,
    )[:edge_limit]
    used_node_ids = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    final_nodes = [node for node_id, node in nodes.items() if node_id in used_node_ids]
    centrality = _compute_graph_centrality(final_nodes, edges, limit=150)

    return {
        "nodes": final_nodes,
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(final_nodes),
            "edge_count": len(edges),
            "required_tables": required,
            "has_committee_matches": has_committee_matches,
            "has_recipient_matches": has_recipient_matches,
            "has_director_donor_matches": has_director_donor_matches,
            "has_donor_committee_edges": has_donor_committee_edges,
            "org_pool_size": len(keep_eins),
            "edge_type_definitions": {
                edge_type: _edge_semantics(edge_type)
                for edge_type in sorted({edge["edge_type"] for edge in edges if edge.get("edge_type")})
            },
        },
    }


def get_vendor_expenditure_network(
    conn: sqlite3.Connection,
    committee_limit: int = 60,
    vendor_limit: int = 100,
    edge_limit: int = 800,
    min_amount: float = 1000.0,
) -> dict:
    """Build committee->vendor expenditure network from bulk_expenditures_clean."""
    committee_limit = max(10, min(int(committee_limit), 500))
    vendor_limit = max(10, min(int(vendor_limit), 500))
    edge_limit = max(50, min(int(edge_limit), 10000))
    min_amount = max(0.0, float(min_amount))

    if not _table_exists(conn, "bulk_expenditures_clean"):
        return _empty_relationship_graph(
            required_tables={"bulk_expenditures_clean": False},
        )

    has_committees = _table_exists(conn, "bulk_committees_clean")
    has_lobbying_matches = _table_exists(conn, "lobbying_expenditure_matches")

    committee_rows = conn.execute(
        """
        SELECT committee_id_sbe, SUM(amount) AS total_spent, COUNT(*) AS txn_count
        FROM bulk_expenditures_clean
        WHERE amount > 0
        GROUP BY committee_id_sbe
        ORDER BY total_spent DESC
        LIMIT ?
        """,
        (committee_limit,),
    ).fetchall()

    keep_committees = {str(row["committee_id_sbe"]) for row in committee_rows}
    if not keep_committees:
        return _empty_relationship_graph(
            required_tables={"bulk_expenditures_clean": True},
        )

    committee_names: dict[str, str] = {}
    if has_committees:
        placeholders = ",".join("?" * len(keep_committees))
        if _column_exists(conn, "bulk_committees_clean", "committee_id_sbe"):
            committee_id_column = "committee_id_sbe"
        elif _column_exists(conn, "bulk_committees_clean", "id"):
            committee_id_column = "id"
        else:
            committee_id_column = None

        if _column_exists(conn, "bulk_committees_clean", "committee_name"):
            committee_name_column = "committee_name"
        elif _column_exists(conn, "bulk_committees_clean", "name"):
            committee_name_column = "name"
        else:
            committee_name_column = None

        if committee_id_column and committee_name_column:
            name_rows = conn.execute(
                f"""
                SELECT
                    {committee_id_column} AS committee_id_value,
                    {committee_name_column} AS committee_name_value
                FROM bulk_committees_clean
                WHERE {committee_id_column} IN ({placeholders})
                """,
                list(keep_committees),
            ).fetchall()
            for row in name_rows:
                committee_names[str(row["committee_id_value"])] = row["committee_name_value"]

    placeholders = ",".join("?" * len(keep_committees))
    edge_rows = conn.execute(
        f"""
        SELECT committee_id_sbe, payee_last_or_business_name,
               SUM(amount) AS total_amount, COUNT(*) AS txn_count
        FROM bulk_expenditures_clean
        WHERE committee_id_sbe IN ({placeholders})
          AND amount > 0
          AND payee_last_or_business_name IS NOT NULL
          AND TRIM(payee_last_or_business_name) != ''
        GROUP BY committee_id_sbe, payee_last_or_business_name
        HAVING total_amount >= ?
        ORDER BY total_amount DESC
        LIMIT ?
        """,
        [*list(keep_committees), min_amount, edge_limit],
    ).fetchall()

    lobbying_matched_payees: set[str] = set()
    if has_lobbying_matches:
        lm_rows = conn.execute(
            "SELECT DISTINCT payee_name FROM lobbying_expenditure_matches WHERE payee_name IS NOT NULL"
        ).fetchall()
        lobbying_matched_payees = {_normalize_name(row["payee_name"]) for row in lm_rows}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for row in edge_rows:
        cid = str(row["committee_id_sbe"])
        payee = (row["payee_last_or_business_name"] or "").strip()
        if not payee:
            continue
        vendor_key = f"vendor:{_stable_entity_key(payee)}"
        committee_key = f"committee:{cid}"

        if committee_key not in nodes:
            nodes[committee_key] = {
                "id": committee_key,
                "label": committee_names.get(cid, f"Committee {cid}"),
                "node_type": "committee",
            }
        is_lobbying_match = _normalize_name(payee) in lobbying_matched_payees
        if vendor_key not in nodes:
            nodes[vendor_key] = {
                "id": vendor_key,
                "label": payee,
                "node_type": "vendor",
                "is_lobbying_match": is_lobbying_match,
            }

        edges.append(
            {
                "source": committee_key,
                "target": vendor_key,
                "edge_type": "committee_vendor",
                "weight": float(row["total_amount"]),
                "count": int(row["txn_count"]),
                **_edge_semantics(
                    "committee_vendor",
                    source_table="bulk_expenditures_clean",
                ),
            }
        )

    vendor_totals: dict[str, float] = defaultdict(float)
    for edge in edges:
        vendor_totals[edge["target"]] += edge["weight"]
    top_vendors = sorted(vendor_totals, key=lambda k: vendor_totals[k], reverse=True)[:vendor_limit]
    top_vendor_set = set(top_vendors)
    edges = [e for e in edges if e["target"] in top_vendor_set]
    used_ids = {e["source"] for e in edges} | {e["target"] for e in edges}
    final_nodes = [n for nid, n in nodes.items() if nid in used_ids]
    centrality = _compute_graph_centrality(final_nodes, edges, limit=100)

    return {
        "nodes": final_nodes,
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(final_nodes),
            "edge_count": len(edges),
            "required_tables": {
                "bulk_expenditures_clean": True,
                "bulk_committees_clean": has_committees,
                "lobbying_expenditure_matches": has_lobbying_matches,
            },
            "has_lobbying_matches": has_lobbying_matches,
            "edge_type_definitions": {
                edge_type: _edge_semantics(edge_type)
                for edge_type in sorted({edge["edge_type"] for edge in edges if edge.get("edge_type")})
            },
        },
    }


def get_state_federal_overlap_graph(
    conn: sqlite3.Connection,
    donor_limit: int = 100,
    edge_limit: int = 600,
) -> dict:
    """Build state-federal donor overlap graph from fec_local_donor_matches."""
    donor_limit = max(10, min(int(donor_limit), 1000))
    edge_limit = max(50, min(int(edge_limit), 5000))

    if not _table_exists(conn, "fec_local_donor_matches"):
        return _empty_relationship_graph(
            required_tables={"fec_local_donor_matches": False},
        )

    has_local_agg = _table_exists(conn, "analytics_donor_committee_agg")
    has_federal = _table_exists(conn, "fec_schedule_a_contributions")

    donor_rows = conn.execute(
        """
        SELECT federal_donor_entity_key, local_donor_key, primary_local_donor_key,
               federal_donor_name, local_donor_name,
               federal_total_amount, local_total_amount,
               confidence_score
        FROM fec_local_donor_matches
        WHERE confidence_score >= 0.5
        ORDER BY (federal_total_amount + local_total_amount) DESC
        LIMIT ?
        """,
        (donor_limit,),
    ).fetchall()

    if not donor_rows:
        return _empty_relationship_graph(
            required_tables={"fec_local_donor_matches": True},
        )

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    local_donor_keys: set[str] = set()
    federal_donor_keys: set[str] = set()
    donor_key_map: dict[str, str] = {}

    for row in donor_rows:
        donor_key = f"donor:{row['federal_donor_entity_key']}:{row['local_donor_key']}"
        label = row["federal_donor_name"] or row["local_donor_name"] or "Unknown"
        if donor_key not in nodes:
            nodes[donor_key] = {
                "id": donor_key,
                "label": label,
                "node_type": "donor",
                "federal_amount": float(row["federal_total_amount"] or 0),
                "local_amount": float(row["local_total_amount"] or 0),
            }
        local_dk = row["primary_local_donor_key"] or row["local_donor_key"]
        local_donor_keys.add(local_dk)
        federal_donor_keys.add(row["federal_donor_entity_key"])
        donor_key_map[local_dk] = donor_key
        donor_key_map[f"fed:{row['federal_donor_entity_key']}"] = donor_key

    if has_local_agg and local_donor_keys:
        placeholders = ",".join("?" * len(local_donor_keys))
        local_flows = conn.execute(
            f"""
            SELECT donor_key, committee_id, committee_name, total_amount
            FROM analytics_donor_committee_agg
            WHERE source = 'bulk_receipts' AND donor_key IN ({placeholders})
            ORDER BY total_amount DESC
            """,
            list(local_donor_keys),
        ).fetchall()

        for flow in local_flows:
            cid = f"local_committee:{flow['committee_id']}"
            if cid not in nodes:
                nodes[cid] = {
                    "id": cid,
                    "label": flow["committee_name"] or f"Committee {flow['committee_id']}",
                    "node_type": "local_committee",
                }
            mapped = donor_key_map.get(flow["donor_key"])
            if mapped:
                edges.append(
                    {
                        "source": mapped,
                        "target": cid,
                        "edge_type": "donor_local",
                        "weight": float(flow["total_amount"]),
                        **_edge_semantics(
                            "donor_local",
                            source_table="analytics_donor_committee_agg",
                        ),
                    }
                )

    if has_federal and federal_donor_keys:
        placeholders = ",".join("?" * len(federal_donor_keys))
        federal_flows = conn.execute(
            f"""
            SELECT contributor_id, committee_id, committee_name,
                   SUM(contribution_receipt_amount) AS total_amount,
                   COUNT(*) AS txn_count
            FROM fec_schedule_a_contributions
            WHERE contributor_id IN ({placeholders})
            GROUP BY contributor_id, committee_id
            ORDER BY total_amount DESC
            """,
            list(federal_donor_keys),
        ).fetchall()

        for flow in federal_flows:
            cid = f"federal_committee:{flow['committee_id']}"
            if cid not in nodes:
                nodes[cid] = {
                    "id": cid,
                    "label": flow["committee_name"] or f"FEC {flow['committee_id']}",
                    "node_type": "federal_committee",
                }
            mapped = donor_key_map.get(f"fed:{flow['contributor_id']}")
            if mapped:
                edges.append(
                    {
                        "source": mapped,
                        "target": cid,
                        "edge_type": "donor_federal",
                        "weight": float(flow["total_amount"] or 0),
                        **_edge_semantics(
                            "donor_federal",
                            source_table="fec_schedule_a_contributions",
                        ),
                    }
                )

    edges.sort(key=lambda e: e["weight"], reverse=True)
    edges = edges[:edge_limit]
    used_ids = {e["source"] for e in edges} | {e["target"] for e in edges}
    final_nodes = [n for nid, n in nodes.items() if nid in used_ids]
    centrality = _compute_graph_centrality(final_nodes, edges, limit=100)

    return {
        "nodes": final_nodes,
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(final_nodes),
            "edge_count": len(edges),
            "required_tables": {
                "fec_local_donor_matches": True,
                "analytics_donor_committee_agg": has_local_agg,
                "fec_schedule_a_contributions": has_federal,
            },
            "donor_count": len(donor_rows),
            "edge_type_definitions": {
                edge_type: _edge_semantics(edge_type)
                for edge_type in sorted({edge["edge_type"] for edge in edges if edge.get("edge_type")})
            },
        },
    }
