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


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int((len(sorted_vals) - 1) * p)))
    return float(sorted_vals[idx])


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


_BULK_DONOR_PART_FILTER_SQL = (
    "COALESCE(r.is_archived, 0) = 0 AND "
    "(COALESCE(r.d2_part_code, '') LIKE '1%' OR COALESCE(r.d2_part_code, '') LIKE '5%')"
)


def _has_bulk_receipts_donor_data(conn: sqlite3.Connection) -> bool:
    required = ["bulk_receipts_clean", "bulk_committees_clean"]
    if not all(_table_exists(conn, table_name) for table_name in required):
        return False
    row = conn.execute(
        f"""
        SELECT 1
        FROM bulk_receipts_clean r
        WHERE COALESCE(r.amount, 0) > 0
          AND {_BULK_DONOR_PART_FILTER_SQL}
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


def _rows_for_source_exist(conn: sqlite3.Connection, table_name: str, source: str) -> bool:
    if not _table_exists(conn, table_name):
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
              AND {_BULK_DONOR_PART_FILTER_SQL}
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
        edges.append(
            {
                "source": donor_node_id,
                "target": committee_node_id,
                "edge_type": "donor_committee",
                "weight": edge_weight,
                "count": int(row.get("contribution_count") or 0),
            }
        )

    if _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        committee_candidate_rows = conn.execute(
            """
            SELECT
                committee_name,
                candidate_id,
                candidate_full_name,
                COALESCE(SUM(sum_total_receipts), 0) AS edge_weight
            FROM bulk_candidate_committee_finance_agg
            WHERE committee_name IS NOT NULL
            GROUP BY committee_name, candidate_id, candidate_full_name
            ORDER BY edge_weight DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

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
            edges.append(
                {
                    "source": committee_node_id,
                    "target": candidate_node_id,
                    "edge_type": "committee_candidate",
                    "weight": edge_weight,
                    "count": 1,
                }
            )

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
            flags.append(
                {
                    "flag_type": "large_single_contribution",
                    "severity": round(amount / baseline, 2) if baseline > 0 else 0,
                    "committee_name": row["committee_name"],
                    "donor_name": row["donor_name"] or "Unknown Donor",
                    "event_date": event_date,
                    "value": round(amount, 2),
                    "baseline": round(baseline, 2),
                    "details": "Contribution amount exceeds dynamic large-transaction threshold.",
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
                  AND {_BULK_DONOR_PART_FILTER_SQL}
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
              AND {_BULK_DONOR_PART_FILTER_SQL}
            ORDER BY r.amount DESC
            LIMIT ?
            """,
            (float(large_threshold), max(200, int(limit) * 5)),
        ).fetchall()

        for row in large_rows:
            amount = float(row["amount"] or 0.0)
            event_date = _normalize_date_iso(row["event_date"]) or _normalize_month_key(row["event_date"]) or None
            flags.append(
                {
                    "flag_type": "large_single_contribution",
                    "severity": round(amount / large_threshold, 2) if large_threshold > 0 else 0,
                    "committee_name": row["committee_name"],
                    "donor_name": row["donor_name"] or "Unknown Donor",
                    "event_date": event_date,
                    "value": round(amount, 2),
                    "baseline": round(large_threshold, 2),
                    "details": "Receipt amount exceeds dynamic large-transaction threshold.",
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
              AND {_BULK_DONOR_PART_FILTER_SQL}
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
            flags.append(
                {
                    "flag_type": "large_single_contribution",
                    "severity": round(amount / large_threshold, 2) if large_threshold > 0 else 0,
                    "committee_name": row["committee_name"],
                    "donor_name": row["donor_name"],
                    "event_date": event_date,
                    "value": round(amount, 2),
                    "baseline": round(large_threshold, 2),
                    "details": "Contribution amount exceeds dynamic large-transaction threshold.",
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
                flags.append(
                    {
                        "flag_type": "monthly_spike",
                        "severity": round(ratio, 2),
                        "committee_name": committee_name,
                        "donor_name": None,
                        "event_date": current_month,
                        "value": round(current_total, 2),
                        "baseline": round(baseline_avg, 2),
                        "details": "Monthly receipts are at least 3x trailing 3-month average.",
                    }
                )

    concentration = (
        precomputed_concentration
        if precomputed_concentration is not None
        else get_donor_concentration(conn, limit=5000)
    )
    for row in concentration:
        if row["hhi"] >= 4500 and row["donor_count"] >= 3:
            flags.append(
                {
                    "flag_type": "high_donor_concentration",
                    "severity": round(row["hhi"] / 2500.0, 2),
                    "committee_name": row["committee_name"],
                    "donor_name": None,
                    "event_date": None,
                    "value": row["hhi"],
                    "baseline": 2500.0,
                    "details": "Committee donor base is highly concentrated by HHI.",
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
              AND {_BULK_DONOR_PART_FILTER_SQL}
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
        WHERE ABS(COALESCE(receipts_minus_d2_total, 0)) >= ?
        ORDER BY ABS(COALESCE(receipts_minus_d2_total, 0)) DESC
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
          AND {_BULK_DONOR_PART_FILTER_SQL}
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
          AND {_BULK_DONOR_PART_FILTER_SQL}
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
              AND {_BULK_DONOR_PART_FILTER_SQL}
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
          AND {_BULK_DONOR_PART_FILTER_SQL}
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
            source, donor_row_count, monthly_row_count, large_row_count, large_threshold, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source) DO UPDATE SET
            donor_row_count = excluded.donor_row_count,
            monthly_row_count = excluded.monthly_row_count,
            large_row_count = excluded.large_row_count,
            large_threshold = excluded.large_threshold,
            refreshed_at = CURRENT_TIMESTAMP
        """,
        ("bulk_receipts", donor_inserted, monthly_inserted, large_inserted, float(large_threshold)),
    )

    return {
        "source": "bulk_receipts",
        "donor_rows": donor_inserted,
        "donor_summary_rows": donor_summary_inserted,
        "monthly_rows": monthly_inserted,
        "large_rows": large_inserted,
        "large_threshold": round(float(large_threshold), 2),
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
            source, donor_row_count, monthly_row_count, large_row_count, large_threshold, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source) DO UPDATE SET
            donor_row_count = excluded.donor_row_count,
            monthly_row_count = excluded.monthly_row_count,
            large_row_count = excluded.large_row_count,
            large_threshold = excluded.large_threshold,
            refreshed_at = CURRENT_TIMESTAMP
        """,
        ("contributions", donor_inserted, monthly_inserted, large_inserted, float(large_threshold)),
    )

    return {
        "source": "contributions",
        "donor_rows": donor_inserted,
        "donor_summary_rows": donor_summary_inserted,
        "monthly_rows": monthly_inserted,
        "large_rows": large_inserted,
        "large_threshold": round(float(large_threshold), 2),
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
