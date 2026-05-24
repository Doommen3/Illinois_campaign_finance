"""Lobbying data routes."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import re

from flask import Blueprint, render_template, request, current_app, abort, jsonify
from webapp.utils.search_normalize import normalize_search_query
from webapp.utils.time_filter import get_active_period, period_to_date_window

lobbying_bp = Blueprint('lobbying', __name__)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _scalar(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return default
    if not row:
        return default
    if hasattr(row, "keys"):
        first_key = next(iter(row.keys()), None)
        if first_key is None:
            return default
        value = row[first_key]
    else:
        value = row[0] if len(row) else default
    return default if value is None else value


def _resolved_time_window() -> tuple[str | None, str | None]:
    period = get_active_period()
    explicit_from = request.args.get("date_from", "", type=str).strip()
    explicit_to = request.args.get("date_to", "", type=str).strip()
    return period_to_date_window(period, explicit_from, explicit_to)


def _bulk_donor_key_sql(alias: str = "r") -> str:
    return (
        f"LOWER(TRIM("
        f"COALESCE({alias}.first_name, '') || '|' || COALESCE({alias}.last_or_business_name, '') || '|' || "
        f"COALESCE({alias}.address_line_1, '') || '|' || COALESCE({alias}.address_line_2, '') || '|' || "
        f"COALESCE({alias}.city, '') || '|' || COALESCE({alias}.state, '') || '|' || COALESCE({alias}.postal_code, '')"
        f"))"
    )


def _normalize_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _stable_entity_key(*values: str | None) -> str:
    normalized = "|".join(_normalize_name(value) for value in values)
    if not normalized:
        return "unknown"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


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


def _parse_date_text(value: str | None):
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _month_key(value: str | None) -> str:
    parsed = _parse_date_text(value)
    if parsed:
        return parsed.strftime("%Y-%m")
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text
    if len(text) >= 7 and re.fullmatch(r"\d{4}-\d{2}", text[:7]):
        return text[:7]
    return ""


def _confidence_band(score: float) -> str:
    if score >= 0.92:
        return "high"
    if score >= 0.84:
        return "medium"
    return "low"


def _empty_flow_payload() -> dict:
    return {
        "nodes": [],
        "links": [],
        "summary": {
            "node_count": 0,
            "link_count": 0,
            "total_flow_amount": 0.0,
        },
    }


def _ensure_lobbying_flow_indexes(conn) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lobbying_donor_matches_donor_score
        ON lobbying_donor_matches(donor_key, score DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lobbying_donor_matches_client_name
        ON lobbying_donor_matches(client_name)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analytics_donor_committee_source_donor
        ON analytics_donor_committee_agg(source, donor_key)
        """
    )


def _get_federal_match_lookup(conn, donor_keys: list[str]) -> dict[str, dict]:
    if not donor_keys or not _table_exists(conn, "fec_local_donor_matches"):
        return {}

    placeholders = ",".join(["?"] * len(donor_keys))
    rows = conn.execute(
        f"""
        SELECT
            local_donor_key,
            federal_donor_entity_key,
            confidence_score,
            match_method
        FROM fec_local_donor_matches
        WHERE local_donor_key IN ({placeholders})
        ORDER BY confidence_score DESC
        """,
        donor_keys,
    ).fetchall()

    output: dict[str, dict] = {}
    for row in rows:
        key = (row["local_donor_key"] or "").strip()
        if not key or key in output:
            continue
        output[key] = {
            "federal_donor_entity_key": (row["federal_donor_entity_key"] or "").strip(),
            "confidence_score": float(row["confidence_score"] or 0.0),
            "match_method": (row["match_method"] or "").strip(),
        }
    return output


def _enrich_donor_matches(conn, donor_match_rows) -> list[dict]:
    donor_matches = []
    donor_keys = sorted({(row["donor_key"] or "").strip() for row in donor_match_rows if row["donor_key"]})
    federal_lookup = _get_federal_match_lookup(conn, donor_keys)

    for row in donor_match_rows:
        donor_key = (row["donor_key"] or "").strip()
        if not donor_key:
            continue
        score = float(row["score"] or 0.0)
        federal = federal_lookup.get(donor_key, {})
        federal_key = (federal.get("federal_donor_entity_key") or "").strip()
        donor_matches.append(
            {
                "client_id": row["client_id"] if "client_id" in row.keys() else None,
                "client_name": row["client_name"] if "client_name" in row.keys() else None,
                "donor_key": donor_key,
                "donor_name": (row["donor_name"] or donor_key).strip(),
                "score": score,
                "method": (row["method"] or "").strip() or None,
                "confidence_band": _confidence_band(score),
                "federal_donor_entity_key": federal_key or None,
                "federal_confidence_score": float(federal.get("confidence_score") or 0.0) if federal_key else None,
                "federal_match_method": (federal.get("match_method") or "").strip() or None,
                "has_federal_match": bool(federal_key),
            }
        )

    donor_matches.sort(key=lambda item: (item["score"], item["donor_name"]), reverse=True)
    return donor_matches


def _donor_band_summary(donor_matches: list[dict]) -> dict[str, int]:
    bands = {"high": 0, "medium": 0, "low": 0}
    for row in donor_matches:
        bands[row["confidence_band"]] = bands.get(row["confidence_band"], 0) + 1
    return bands


def _candidate_destinations(
    conn,
    donor_keys: list[str],
    limit: int = 10,
    committee_totals: list[dict] | None = None,
) -> list[dict]:
    if committee_totals and _table_exists(conn, "bulk_cmte_candidate_links_clean"):
        committee_ids = [
            int(row["committee_id"])
            for row in committee_totals
            if row.get("committee_id") is not None
        ]
        if not committee_ids:
            return []
        committee_lookup = {
            int(row["committee_id"]): {
                "total_amount": float(row.get("total_amount") or 0.0),
                "contribution_count": float(row.get("contribution_count") or 0.0),
            }
            for row in committee_totals
            if row.get("committee_id") is not None
        }
        placeholders = ",".join(["?"] * len(committee_ids))
        rows = conn.execute(
            f"""
            SELECT
                l.committee_id_sbe,
                l.candidate_id,
                COALESCE(MAX(c.candidate_full_name), 'Candidate ' || l.candidate_id) AS candidate_name
            FROM bulk_cmte_candidate_links_clean l
            LEFT JOIN bulk_candidates_clean c
              ON c.candidate_id = l.candidate_id
            WHERE l.candidate_id IS NOT NULL
              AND l.committee_id_sbe IN ({placeholders})
            GROUP BY l.committee_id_sbe, l.candidate_id
            """,
            committee_ids,
        ).fetchall()
        committee_to_candidates: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for row in rows:
            committee_to_candidates[int(row["committee_id_sbe"])].append(
                (int(row["candidate_id"]), row["candidate_name"] or f"Candidate {row['candidate_id']}")
            )

        candidate_totals: dict[int, dict] = {}
        for committee_id, metrics in committee_lookup.items():
            candidates = committee_to_candidates.get(committee_id, [])
            if not candidates:
                continue
            split = float(len(candidates))
            amount_share = metrics["total_amount"] / split if split > 0 else 0.0
            count_share = metrics["contribution_count"] / split if split > 0 else 0.0
            for candidate_id, candidate_name in candidates:
                bucket = candidate_totals.setdefault(
                    candidate_id,
                    {
                        "candidate_id": candidate_id,
                        "candidate_name": candidate_name,
                        "total_amount": 0.0,
                        "contribution_count": 0.0,
                    },
                )
                bucket["total_amount"] += amount_share
                bucket["contribution_count"] += count_share

        ranked = sorted(
            candidate_totals.values(),
            key=lambda row: (float(row["total_amount"]), float(row["contribution_count"])),
            reverse=True,
        )[:limit]
        return [
            {
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"],
                "total_amount": round(float(row["total_amount"] or 0.0), 2),
                "contribution_count": int(round(float(row["contribution_count"] or 0.0))),
            }
            for row in ranked
        ]

    if (
        not donor_keys
        or not _table_exists(conn, "analytics_donor_committee_agg")
        or not _table_exists(conn, "bulk_cmte_candidate_links_clean")
    ):
        return []

    placeholders = ",".join(["?"] * len(donor_keys))
    rows = conn.execute(
        f"""
        WITH committee_candidate_counts AS (
            SELECT committee_id_sbe, COUNT(DISTINCT candidate_id) AS candidate_count
            FROM bulk_cmte_candidate_links_clean
            WHERE candidate_id IS NOT NULL
            GROUP BY committee_id_sbe
        )
        SELECT
            l.candidate_id,
            COALESCE(MAX(c.candidate_full_name), 'Candidate ' || l.candidate_id) AS candidate_name,
            COALESCE(
                SUM(
                    COALESCE(a.total_amount, 0)
                    / CASE
                        WHEN COALESCE(cc.candidate_count, 0) > 0 THEN cc.candidate_count
                        ELSE 1
                      END
                ),
                0
            ) AS total_amount,
            COALESCE(
                SUM(
                    COALESCE(a.contribution_count, 0)
                    / CASE
                        WHEN COALESCE(cc.candidate_count, 0) > 0 THEN cc.candidate_count
                        ELSE 1
                      END
                ),
                0
            ) AS contribution_count
        FROM analytics_donor_committee_agg a
        JOIN bulk_cmte_candidate_links_clean l
          ON CAST(l.committee_id_sbe AS TEXT) = a.committee_id
        LEFT JOIN bulk_candidates_clean c
          ON c.candidate_id = l.candidate_id
        LEFT JOIN committee_candidate_counts cc
          ON cc.committee_id_sbe = l.committee_id_sbe
        WHERE a.source = 'bulk_receipts'
          AND l.candidate_id IS NOT NULL
          AND a.donor_key IN ({placeholders})
        GROUP BY l.candidate_id
        ORDER BY total_amount DESC, contribution_count DESC
        LIMIT ?
        """,
        [*donor_keys, limit],
    ).fetchall()

    return [
        {
            "candidate_id": row["candidate_id"],
            "candidate_name": row["candidate_name"] or f"Candidate {row['candidate_id']}",
            "total_amount": round(float(row["total_amount"] or 0.0), 2),
            "contribution_count": int(round(float(row["contribution_count"] or 0.0))),
        }
        for row in rows
    ]


def _get_donor_profiles(conn, donor_keys: list[str], fallback_lookup: dict[str, str]) -> dict[str, dict]:
    profiles = {
        donor_key: {"donor_name": donor_name or donor_key}
        for donor_key, donor_name in fallback_lookup.items()
        if donor_key
    }
    if not donor_keys or not _table_exists(conn, "analytics_donor_summary"):
        return profiles

    placeholders = ",".join(["?"] * len(donor_keys))
    rows = conn.execute(
        f"""
        SELECT donor_key, donor_name, donor_address, donor_city, donor_state
        FROM analytics_donor_summary
        WHERE source = 'bulk_receipts'
          AND donor_key IN ({placeholders})
        """,
        donor_keys,
    ).fetchall()
    for row in rows:
        donor_key = (row["donor_key"] or "").strip()
        if not donor_key:
            continue
        profiles[donor_key] = {
            "donor_name": row["donor_name"] or donor_key,
            "donor_address": row["donor_address"] or "",
            "donor_city": row["donor_city"] or "",
            "donor_state": row["donor_state"] or "",
        }
    return profiles


def _get_receipt_activity(
    conn,
    donor_profiles: dict[str, dict],
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    if not donor_profiles or not _table_exists(conn, "bulk_receipts_clean"):
        return {
            "total_amount": 0.0,
            "contribution_count": 0,
            "first_donation_date": None,
            "last_donation_date": None,
            "monthly_series": [],
            "max_monthly_amount": 0.0,
            "committee_totals": [],
        }

    donor_key_set = set(donor_profiles.keys())
    names = sorted({_normalize_name(meta.get("donor_name")) for meta in donor_profiles.values() if meta.get("donor_name")})
    names_set = set(names)
    if not names:
        return {
            "total_amount": 0.0,
            "contribution_count": 0,
            "first_donation_date": None,
            "last_donation_date": None,
            "monthly_series": [],
            "max_monthly_amount": 0.0,
            "committee_totals": [],
        }

    placeholders = ",".join(["?"] * len(names))
    rows = conn.execute(
        f"""
        SELECT
            first_name,
            last_or_business_name,
            address_line_1,
            address_line_2,
            city,
            state,
            postal_code,
            committee_id_sbe,
            received_date,
            amount,
            is_archived
        FROM bulk_receipts_clean
        WHERE COALESCE(amount, 0) > 0
          AND LOWER(
                TRIM(
                    COALESCE(NULLIF(TRIM(first_name), ''), '')
                    || CASE
                        WHEN COALESCE(NULLIF(TRIM(first_name), ''), '') != ''
                         AND COALESCE(NULLIF(TRIM(last_or_business_name), ''), '') != '' THEN ' '
                        ELSE ''
                       END
                    || COALESCE(NULLIF(TRIM(last_or_business_name), ''), '')
                )
          ) IN ({placeholders})
        """,
        names,
    ).fetchall()

    monthly_totals: dict[str, float] = defaultdict(float)
    total_amount = 0.0
    contribution_count = 0
    first_date = None
    last_date = None
    committee_totals: dict[int, dict] = {}
    range_start = _parse_date_text(date_from)
    range_end = _parse_date_text(date_to)

    for row in rows:
        if int(row["is_archived"] or 0) != 0:
            continue
        donor_name = _build_bulk_donor_name(row["first_name"], row["last_or_business_name"])
        donor_address = _build_bulk_address(
            row["address_line_1"],
            row["address_line_2"],
            row["city"],
            row["state"],
            row["postal_code"],
        )
        donor_key = _stable_entity_key(donor_name, donor_address)
        if donor_key_set:
            donor_name_key = _normalize_name(donor_name)
            if donor_key not in donor_key_set and donor_name_key not in names_set:
                continue
        elif _normalize_name(donor_name) not in names_set:
            continue

        parsed_date = _parse_date_text(row["received_date"])
        if range_start and (parsed_date is None or parsed_date < range_start):
            continue
        if range_end and (parsed_date is None or parsed_date > range_end):
            continue

        amount = float(row["amount"] or 0.0)
        if amount <= 0:
            continue
        contribution_count += 1
        total_amount += amount

        if parsed_date:
            if first_date is None or parsed_date < first_date:
                first_date = parsed_date
            if last_date is None or parsed_date > last_date:
                last_date = parsed_date

        month = _month_key(row["received_date"])
        if month:
            monthly_totals[month] += amount

        committee_id = row["committee_id_sbe"]
        if committee_id is not None:
            cid = int(committee_id)
            bucket = committee_totals.setdefault(
                cid,
                {"committee_id": cid, "committee_name": f"Committee {cid}", "total_amount": 0.0, "contribution_count": 0},
            )
            bucket["total_amount"] += amount
            bucket["contribution_count"] += 1

    if committee_totals and _table_exists(conn, "bulk_committees_clean"):
        placeholders = ",".join(["?"] * len(committee_totals))
        name_rows = conn.execute(
            f"""
            SELECT committee_id_sbe, committee_name
            FROM bulk_committees_clean
            WHERE committee_id_sbe IN ({placeholders})
            """,
            list(committee_totals.keys()),
        ).fetchall()
        for row in name_rows:
            committee_id = int(row["committee_id_sbe"])
            if committee_id in committee_totals and row["committee_name"]:
                committee_totals[committee_id]["committee_name"] = row["committee_name"]

    monthly_series = [
        {"month": month, "amount": round(amount, 2)}
        for month, amount in sorted(monthly_totals.items())
    ]
    committee_series = sorted(
        committee_totals.values(),
        key=lambda row: (float(row["total_amount"]), int(row["contribution_count"])),
        reverse=True,
    )

    return {
        "total_amount": round(total_amount, 2),
        "contribution_count": contribution_count,
        "first_donation_date": first_date.isoformat() if first_date else None,
        "last_donation_date": last_date.isoformat() if last_date else None,
        "monthly_series": monthly_series[-18:],
        "max_monthly_amount": round(max(monthly_totals.values()) if monthly_totals else 0.0, 2),
        "committee_totals": committee_series,
    }


def _build_money_destinations(
    conn,
    donor_matches: list[dict],
    top_limit: int = 10,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    donor_keys = sorted({(row.get("donor_key") or "").strip() for row in donor_matches if row.get("donor_key")})
    if not donor_keys:
        return {
            "committee_destinations": [],
            "candidate_destinations": [],
            "total_amount": 0.0,
            "contribution_count": 0,
            "first_donation_date": None,
            "last_donation_date": None,
            "monthly_series": [],
        }

    committee_destinations = []
    has_time_window = bool((date_from or "").strip() or (date_to or "").strip())
    if _table_exists(conn, "analytics_donor_committee_agg") and not has_time_window:
        placeholders = ",".join(["?"] * len(donor_keys))
        committee_rows = conn.execute(
            f"""
            SELECT
                committee_id,
                COALESCE(MAX(committee_name), 'Committee ' || committee_id) AS committee_name,
                COALESCE(SUM(total_amount), 0) AS total_amount,
                COALESCE(SUM(contribution_count), 0) AS contribution_count
            FROM analytics_donor_committee_agg
            WHERE source = 'bulk_receipts'
              AND donor_key IN ({placeholders})
            GROUP BY committee_id
            ORDER BY total_amount DESC, contribution_count DESC
            LIMIT ?
            """,
            [*donor_keys, top_limit],
        ).fetchall()
        committee_destinations = [
            {
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"] or f"Committee {row['committee_id']}",
                "total_amount": round(float(row["total_amount"] or 0.0), 2),
                "contribution_count": int(row["contribution_count"] or 0),
            }
            for row in committee_rows
        ]

    fallback_lookup = {
        row["donor_key"]: row.get("donor_name") or row["donor_key"]
        for row in donor_matches
        if row.get("donor_key")
    }
    donor_profiles = _get_donor_profiles(conn, donor_keys, fallback_lookup=fallback_lookup)
    activity = _get_receipt_activity(conn, donor_profiles, date_from=date_from, date_to=date_to)
    if has_time_window and activity.get("committee_totals"):
        committee_destinations = activity["committee_totals"][:top_limit]

    if not activity["total_amount"] and committee_destinations:
        activity["total_amount"] = round(sum(row["total_amount"] for row in committee_destinations), 2)
        activity["contribution_count"] = int(sum(row["contribution_count"] for row in committee_destinations))

    return {
        "committee_destinations": committee_destinations,
        "candidate_destinations": _candidate_destinations(
            conn,
            donor_keys,
            limit=top_limit,
            committee_totals=committee_destinations if has_time_window else None,
        ),
        "total_amount": activity["total_amount"],
        "contribution_count": activity["contribution_count"],
        "first_donation_date": activity["first_donation_date"],
        "last_donation_date": activity["last_donation_date"],
        "monthly_series": activity["monthly_series"],
        "max_monthly_amount": activity.get("max_monthly_amount", 0.0),
    }


@lobbying_bp.route('/')
def list_entities():
    """List lobbying entities with client counts and match counts."""
    conn = current_app.get_database()

    if not _table_exists(conn, "lobbying_entities"):
        return render_template('lobbying/list.html', entities=[], total=0,
                               page=1, total_pages=1, query='')

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    raw_query = request.args.get('q', '').strip()
    query = normalize_search_query(raw_query) or raw_query.strip()

    where_clause = ""
    params = []
    if query:
        where_clause = "WHERE e.entity_name LIKE ?"
        params.append(f"%{query}%")

    total = _scalar(
        conn,
        f"SELECT COUNT(*) FROM lobbying_entities e {where_clause}",
        params,
        default=0,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    entities = conn.execute(
        f"""
        SELECT
            e.entity_id,
            e.entity_name,
            e.reg_year,
            COUNT(DISTINCT ec.client_id) AS client_count
        FROM lobbying_entities e
        LEFT JOIN lobbying_entity_clients ec ON ec.entity_id = e.entity_id
        {where_clause}
        GROUP BY e.entity_id, e.entity_name, e.reg_year
        ORDER BY client_count DESC, e.entity_name ASC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    ).fetchall()

    return render_template('lobbying/list.html',
                           entities=entities, total=total,
                           page=page, total_pages=total_pages, query=query)


@lobbying_bp.route('/suggest')
def suggest_entities():
    """Return up to 10 entity name suggestions for autocomplete."""
    conn = current_app.get_database()
    raw = request.args.get('q', '').strip()
    q = normalize_search_query(raw)
    if len(q) < 2 or not _table_exists(conn, "lobbying_entities"):
        return jsonify([])
    rows = conn.execute(
        """
        SELECT entity_id AS value, entity_name AS label
        FROM lobbying_entities
        WHERE entity_name LIKE ?
        ORDER BY entity_name
        LIMIT 10
        """,
        (f"%{q}%",),
    ).fetchall()
    return jsonify([{"label": r["label"], "value": r["value"]} for r in rows])


@lobbying_bp.route('/flows/suggest')
def suggest_flow_clients():
    """Return up to 10 client name suggestions for the money flows filter."""
    conn = current_app.get_database()
    raw = request.args.get('q', '').strip()
    q = normalize_search_query(raw)
    if len(q) < 2 or not _table_exists(conn, "lobbying_donor_matches"):
        return jsonify([])
    rows = conn.execute(
        """
        SELECT DISTINCT client_name AS label
        FROM lobbying_donor_matches
        WHERE client_name LIKE ? AND score >= 0.80
        ORDER BY client_name
        LIMIT 10
        """,
        (f"%{q}%",),
    ).fetchall()
    return jsonify([{"label": r["label"], "value": r["label"]} for r in rows])


@lobbying_bp.route('/<int:entity_id>')
def entity_detail(entity_id):
    """Entity detail: clients, matched donors, money destinations, matched payees."""
    conn = current_app.get_database()
    date_from, date_to = _resolved_time_window()

    if not _table_exists(conn, "lobbying_entities"):
        abort(404)

    entity = conn.execute(
        "SELECT entity_id, entity_name, reg_year FROM lobbying_entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if not entity:
        abort(404)

    clients = conn.execute(
        """
        SELECT c.client_id, c.client_name, ec.reg_year
        FROM lobbying_entity_clients ec
        JOIN lobbying_clients c ON c.client_id = ec.client_id
        WHERE ec.entity_id = ?
        ORDER BY c.client_name
        """,
        (entity_id,),
    ).fetchall()

    donor_matches = []
    if _table_exists(conn, "lobbying_donor_matches") and _table_exists(conn, "lobbying_entity_clients"):
        donor_match_rows = conn.execute(
            """
            SELECT
                ldm.client_id,
                lc.client_name,
                ldm.donor_key,
                ldm.donor_name,
                ldm.score,
                ldm.method
            FROM lobbying_donor_matches ldm
            JOIN lobbying_entity_clients ec
              ON ec.client_id = ldm.client_id
            LEFT JOIN lobbying_clients lc
              ON lc.client_id = ldm.client_id
            WHERE ec.entity_id = ?
            ORDER BY ldm.score DESC, ldm.donor_name ASC
            LIMIT 120
            """,
            (entity_id,),
        ).fetchall()
        donor_matches = _enrich_donor_matches(conn, donor_match_rows)

    donor_band_summary = _donor_band_summary(donor_matches)
    money_destinations = _build_money_destinations(
        conn,
        donor_matches,
        top_limit=10,
        date_from=date_from,
        date_to=date_to,
    )

    # Expenditure matches for this entity
    expenditure_matches = []
    if _table_exists(conn, "lobbying_expenditure_matches"):
        expenditure_matches = conn.execute(
            """
            SELECT source_type, source_name, payee_name, committee_id_sbe, score
            FROM lobbying_expenditure_matches
            WHERE source_type = 'entity' AND source_id = ?
            ORDER BY score DESC
            LIMIT 50
            """,
            (entity_id,),
        ).fetchall()

    return render_template('lobbying/entity_detail.html',
                           entity=entity, clients=clients,
                           donor_matches=donor_matches,
                           donor_band_summary=donor_band_summary,
                           money_destinations=money_destinations,
                           expenditure_matches=expenditure_matches)


@lobbying_bp.route('/client/<int:client_id>')
def client_detail(client_id):
    """Client detail: entities, donor links, money destinations, payee and 527 matches."""
    conn = current_app.get_database()
    date_from, date_to = _resolved_time_window()

    if not _table_exists(conn, "lobbying_clients"):
        abort(404)

    client = conn.execute(
        "SELECT client_id, client_name FROM lobbying_clients WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if not client:
        abort(404)

    # Entities that represent this client
    entities = conn.execute(
        """
        SELECT e.entity_id, e.entity_name, ec.reg_year
        FROM lobbying_entity_clients ec
        JOIN lobbying_entities e ON e.entity_id = ec.entity_id
        WHERE ec.client_id = ?
        ORDER BY e.entity_name
        """,
        (client_id,),
    ).fetchall()

    # Donor matches
    donor_matches = []
    if _table_exists(conn, "lobbying_donor_matches"):
        donor_match_rows = conn.execute(
            """
            SELECT client_id, client_name, donor_key, donor_name, score, method
            FROM lobbying_donor_matches
            WHERE client_id = ?
            ORDER BY score DESC
            LIMIT 120
            """,
            (client_id,),
        ).fetchall()
        donor_matches = _enrich_donor_matches(conn, donor_match_rows)

    donor_band_summary = _donor_band_summary(donor_matches)
    money_destinations = _build_money_destinations(
        conn,
        donor_matches,
        top_limit=10,
        date_from=date_from,
        date_to=date_to,
    )

    # Expenditure matches
    expenditure_matches = []
    if _table_exists(conn, "lobbying_expenditure_matches"):
        expenditure_matches = conn.execute(
            """
            SELECT source_type, source_name, payee_name, committee_id_sbe, score
            FROM lobbying_expenditure_matches
            WHERE source_type = 'client' AND source_id = ?
            ORDER BY score DESC
            LIMIT 50
            """,
            (client_id,),
        ).fetchall()

    # 527 matches
    org_527_matches = []
    if _table_exists(conn, "lobbying_527_matches"):
        org_527_matches = conn.execute(
            """
            SELECT client_name, ein, org_name, score
            FROM lobbying_527_matches
            WHERE client_id = ?
            ORDER BY score DESC
            LIMIT 50
            """,
            (client_id,),
        ).fetchall()

    return render_template('lobbying/client_detail.html',
                           client=client, entities=entities,
                           donor_matches=donor_matches,
                           donor_band_summary=donor_band_summary,
                           money_destinations=money_destinations,
                           expenditure_matches=expenditure_matches,
                           org_527_matches=org_527_matches)


@lobbying_bp.route('/flows')
def flows():
    """Sankey diagram page: lobbying client -> committee money flows via matched donors."""
    raw_client = request.args.get('client', '').strip()
    client_filter = normalize_search_query(raw_client) or raw_client.strip()
    return render_template('lobbying/flows.html', client_filter=client_filter)


@lobbying_bp.route('/flows/data')
def flows_data():
    """JSON for Sankey diagram."""
    conn = current_app.get_database()
    date_from, date_to = _resolved_time_window()
    has_time_window = bool((date_from or "").strip() or (date_to or "").strip())

    if not _table_exists(conn, "lobbying_donor_matches"):
        return jsonify(_empty_flow_payload())
    if has_time_window and not _table_exists(conn, "bulk_receipts_clean"):
        return jsonify(_empty_flow_payload())
    if not has_time_window and not _table_exists(conn, "analytics_donor_committee_agg"):
        return jsonify(_empty_flow_payload())

    _ensure_lobbying_flow_indexes(conn)

    raw_client = request.args.get('client', '').strip()
    client_filter = normalize_search_query(raw_client) or raw_client.strip()
    where = ["score >= 0.80"]
    params = []
    if client_filter:
        where.append("client_name LIKE ?")
        params.append(f"%{client_filter}%")

    if has_time_window:
        date_clause = ""
        date_params: list[object] = []
        # Compare the underlying date column directly rather than wrapping
        # it in DATE(...) — wrapping defeats the index on
        # isbe_condensed_receipts(received_date) that this scan would
        # otherwise use, which is why /lobbying/flows/data?period=... was
        # timing out at 30s on prod. The compat view exposes received_date
        # as text in ISO format, so lexicographic comparison is correct.
        if date_from:
            date_clause += " AND r.received_date >= ?"
            date_params.append(date_from)
        if date_to:
            date_clause += " AND r.received_date <= ?"
            date_params.append(date_to)
        archived_clause = " AND NOT COALESCE(r.is_archived::boolean, FALSE)" if _column_exists(conn, "bulk_receipts_clean", "is_archived") else ""
        rows = conn.execute(
            f"""
            WITH filtered_matches AS (
                SELECT donor_key, COALESCE(client_name, 'Unknown Client') AS client_name
                FROM lobbying_donor_matches
                WHERE {' AND '.join(where)}
            ),
            donor_committee AS (
                SELECT
                    {_bulk_donor_key_sql('r')} AS donor_key,
                    r.committee_id_sbe,
                    COALESCE(SUM(r.amount), 0) AS total_amount
                FROM bulk_receipts_clean r
                WHERE COALESCE(r.amount, 0) > 0
                  {archived_clause}
                  {date_clause}
                GROUP BY donor_key, r.committee_id_sbe
            )
            SELECT
                fm.client_name AS client_name,
                COALESCE(MAX(c.committee_name), 'Committee ' || dc.committee_id_sbe) AS committee_name,
                SUM(dc.total_amount) AS flow_amount
            FROM filtered_matches fm
            JOIN donor_committee dc
              ON dc.donor_key = fm.donor_key
            LEFT JOIN bulk_committees_clean c
              ON c.committee_id_sbe = dc.committee_id_sbe
            GROUP BY fm.client_name, dc.committee_id_sbe
            HAVING SUM(dc.total_amount) >= 1000
            ORDER BY flow_amount DESC
            LIMIT 100
            """,
            [*params, *date_params],
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            WITH filtered_matches AS (
                SELECT donor_key, COALESCE(client_name, 'Unknown Client') AS client_name
                FROM lobbying_donor_matches
                WHERE {' AND '.join(where)}
            )
            SELECT
                fm.client_name AS client_name,
                COALESCE(a.committee_name, 'Unknown Committee') AS committee_name,
                SUM(a.total_amount) AS flow_amount
            FROM filtered_matches fm
            JOIN analytics_donor_committee_agg a
              ON a.donor_key = fm.donor_key
             AND a.source = 'bulk_receipts'
            GROUP BY fm.client_name, a.committee_name
            HAVING SUM(a.total_amount) >= 1000
            ORDER BY flow_amount DESC
            LIMIT 100
            """,
            params,
        ).fetchall()

    if not rows:
        return jsonify(_empty_flow_payload())

    nodes = []
    node_index = {}

    def _ensure_node(name: str, node_type: str) -> int:
        key = (node_type, name)
        if key in node_index:
            return node_index[key]
        node_index[key] = len(nodes)
        nodes.append({"name": name, "type": node_type})
        return node_index[key]

    links = []
    total_flow_amount = 0.0
    for r in rows:
        client_name = r["client_name"] or "Unknown Client"
        committee_name = r["committee_name"] or "Unknown Committee"
        value = float(r["flow_amount"] or 0.0)
        if value <= 0:
            continue
        source = _ensure_node(client_name, "client")
        target = _ensure_node(committee_name, "committee")
        links.append({"source": source, "target": target, "value": value})
        total_flow_amount += value

    if not links:
        return jsonify(_empty_flow_payload())

    return jsonify(
        {
            "nodes": nodes,
            "links": links,
            "summary": {
                "node_count": len(nodes),
                "link_count": len(links),
                "total_flow_amount": round(total_flow_amount, 2),
            },
        }
    )
