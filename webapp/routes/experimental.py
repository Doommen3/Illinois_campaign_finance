"""Experimental visualization lab routes (local prototype gallery)."""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, url_for

from database.analytics import (
    compute_advanced_network_metrics,
    get_network_graph,
    get_state_race_analytics,
)
from webapp.utils.time_filter import get_active_period, period_to_date_window

experimental_bp = Blueprint("experimental", __name__)

_VIZ_CACHE: dict[str, dict] = {}
_VIZ_CACHE_LOCK = threading.Lock()
_VIZ_CACHE_MAX_ENTRIES = 128
_VIZ_CACHE_DEFAULT_TTL_SECONDS = 180


def _feature_enabled() -> bool:
    return bool(current_app.config.get("EXPERIMENTAL_VIZ_LAB_ENABLED", False))


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


def _cache_enabled() -> bool:
    return bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))


def _cache_ttl_seconds() -> int:
    return max(30, int(current_app.config.get("EXPERIMENTAL_VIZ_CACHE_TTL_SECONDS", _VIZ_CACHE_DEFAULT_TTL_SECONDS)))


def _cache_get(cache_key: str) -> dict | None:
    now = time.monotonic()
    with _VIZ_CACHE_LOCK:
        entry = _VIZ_CACHE.get(cache_key)
        if not entry:
            return None
        if float(entry.get("expires_at", 0.0)) <= now:
            _VIZ_CACHE.pop(cache_key, None)
            return None
        payload = dict(entry.get("payload") or {})
    payload["cache_hit"] = True
    if isinstance(payload.get("graph_meta"), dict):
        payload["graph_meta"]["cache_hit"] = True
    return payload


def _cache_set(cache_key: str, payload: dict) -> None:
    now = time.monotonic()
    expires_at = now + float(_cache_ttl_seconds())
    with _VIZ_CACHE_LOCK:
        _VIZ_CACHE[cache_key] = {
            "payload": dict(payload),
            "expires_at": expires_at,
            "updated_at": now,
        }
        if len(_VIZ_CACHE) > _VIZ_CACHE_MAX_ENTRIES:
            stale_keys = [
                key
                for key, value in _VIZ_CACHE.items()
                if float(value.get("expires_at", 0.0)) <= now
            ]
            for key in stale_keys:
                _VIZ_CACHE.pop(key, None)
            if len(_VIZ_CACHE) > _VIZ_CACHE_MAX_ENTRIES:
                oldest_keys = sorted(
                    _VIZ_CACHE.keys(),
                    key=lambda key: float(_VIZ_CACHE[key].get("updated_at", 0.0)),
                )[: len(_VIZ_CACHE) - _VIZ_CACHE_MAX_ENTRIES]
                for key in oldest_keys:
                    _VIZ_CACHE.pop(key, None)


def _cached_payload(prototype_key: str, cache_params: dict, builder) -> dict:
    cache_key = json.dumps(
        {"v": 1, "prototype": prototype_key, **cache_params},
        sort_keys=True,
        separators=(",", ":"),
    )
    if _cache_enabled():
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    start = time.perf_counter()
    payload = builder()
    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
    response = {
        **payload,
        "prototype_key": prototype_key,
        "query_ms": elapsed_ms,
        "cache_hit": False,
    }
    if isinstance(response.get("graph_meta"), dict):
        response["graph_meta"]["cache_hit"] = False
    if _cache_enabled():
        _cache_set(cache_key, response)
    return response


def _is_true_arg(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolved_date_window() -> tuple[dict, str | None, str | None]:
    period = get_active_period()
    explicit_from = (request.args.get("date_from", "", type=str) or "").strip()
    explicit_to = (request.args.get("date_to", "", type=str) or "").strip()
    date_from, date_to = period_to_date_window(period, explicit_from, explicit_to)
    return period, date_from, date_to


def _parse_pagination() -> tuple[int, int]:
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(request.args.get("per_page", 10, type=int) or 10, 5), 40)
    return page, per_page


def _paginate(rows: list[dict], page: int, per_page: int) -> tuple[list[dict], dict]:
    total_rows = len(rows)
    total_pages = max(1, (total_rows + per_page - 1) // per_page)
    clamped_page = max(1, min(page, total_pages))
    offset = (clamped_page - 1) * per_page
    return (
        rows[offset : offset + per_page],
        {
            "page": clamped_page,
            "per_page": per_page,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "has_prev": clamped_page > 1,
            "has_next": clamped_page < total_pages,
        },
    )


def _prototype_unavailable(reason: str, *, supports_date_window: bool) -> dict:
    return {
        "available": False,
        "reason": reason,
        "supports_date_window": supports_date_window,
        "chart_rows": [],
        "table_rows": [],
        "pagination": {
            "page": 1,
            "per_page": 10,
            "total_rows": 0,
            "total_pages": 1,
            "has_prev": False,
            "has_next": False,
        },
    }


def _build_race_money_pressure(
    conn,
    *,
    date_from: str | None,
    date_to: str | None,
    page: int,
    per_page: int,
) -> dict:
    rows = get_state_race_analytics(conn, limit=80, date_from=date_from, date_to=date_to)
    if not rows:
        return _prototype_unavailable(
            "Required state race analytics tables are missing or there is no data in the selected range.",
            supports_date_window=True,
        )

    chart_rows = [
        {
            "race_label": row["race_label"],
            "total_amount": float(row["total_amount"] or 0.0),
            "outside_spending_total": float(row["outside_spending_total"] or 0.0),
            "donor_count": int(row["donor_count"] or 0),
            "candidate_count": int(row["candidate_count"] or 0),
            "outside_pressure_ratio": float(row["outside_pressure_ratio"] or 0.0),
        }
        for row in rows
    ]
    table_rows, pagination = _paginate(chart_rows, page, per_page)
    return {
        "available": True,
        "supports_date_window": True,
        "chart_rows": chart_rows,
        "table_rows": table_rows,
        "pagination": pagination,
        "summary": {
            "race_count": len(chart_rows),
            "total_receipts": round(sum(row["total_amount"] for row in chart_rows), 2),
            "total_outside_spending": round(sum(row["outside_spending_total"] for row in chart_rows), 2),
        },
        "notes": {
            "sql_source": "database.analytics.get_state_race_analytics()",
            "known_limitations": [
                "Outside spending relies on candidate-name matching in part-9 expenditure rows.",
                "Race-level donor counts are de-duplicated within race, not statewide across races.",
            ],
            "validate": [
                "Check top races against /analytics overview race table for the same date window.",
                "Confirm outside-pressure outliers map to known high-spend races.",
            ],
        },
    }


def _build_race_concentration(
    conn,
    *,
    date_from: str | None,
    date_to: str | None,
    page: int,
    per_page: int,
) -> dict:
    rows = get_state_race_analytics(conn, limit=80, date_from=date_from, date_to=date_to)
    if not rows:
        return _prototype_unavailable(
            "Required state race analytics tables are missing or there is no data in the selected range.",
            supports_date_window=True,
        )

    chart_rows = [
        {
            "race_label": row["race_label"],
            "donor_count": int(row["donor_count"] or 0),
            "top_candidate_share_pct": round(float(row["top_candidate_share"] or 0.0) * 100.0, 2),
            "total_amount": float(row["total_amount"] or 0.0),
            "outside_pressure_ratio_pct": round(float(row["outside_pressure_ratio"] or 0.0) * 100.0, 2),
        }
        for row in rows
    ]
    chart_rows.sort(key=lambda row: (row["total_amount"], row["top_candidate_share_pct"]), reverse=True)
    table_rows, pagination = _paginate(chart_rows, page, per_page)
    return {
        "available": True,
        "supports_date_window": True,
        "chart_rows": chart_rows,
        "table_rows": table_rows,
        "pagination": pagination,
        "summary": {
            "race_count": len(chart_rows),
            "avg_top_candidate_share_pct": round(
                sum(row["top_candidate_share_pct"] for row in chart_rows) / max(1, len(chart_rows)),
                2,
            ),
        },
        "notes": {
            "sql_source": "database.analytics.get_state_race_analytics() (top_candidate_share metric)",
            "known_limitations": [
                "This prototype uses candidate concentration (top candidate share), not direct top-donor share.",
                "Donor breadth and concentration should be interpreted alongside total amount and donor count.",
            ],
            "validate": [
                "Spot-check high-concentration races against candidate detail pages.",
                "Confirm donor_count trend changes when period/date range changes.",
            ],
        },
    }


def _build_cumulative_inflow(
    conn,
    *,
    date_from: str | None,
    date_to: str | None,
    page: int,
    per_page: int,
) -> dict:
    required = {"bulk_receipts_clean", "bulk_candidate_committee_finance_agg"}
    if not all(_table_exists(conn, table_name) for table_name in required):
        return _prototype_unavailable(
            "Missing required tables: bulk_receipts_clean and/or bulk_candidate_committee_finance_agg.",
            supports_date_window=True,
        )

    if not _column_exists(conn, "bulk_receipts_clean", "received_date"):
        return _prototype_unavailable(
            "bulk_receipts_clean.received_date is required for cumulative inflow prototype.",
            supports_date_window=True,
        )
    required_candidate_columns = {"committee_id_sbe", "office_sought", "district_type", "district"}
    if not all(_column_exists(conn, "bulk_candidate_committee_finance_agg", column) for column in required_candidate_columns):
        return _prototype_unavailable(
            "bulk_candidate_committee_finance_agg is missing required district/race columns.",
            supports_date_window=True,
        )
    if not _column_exists(conn, "bulk_receipts_clean", "committee_id_sbe"):
        return _prototype_unavailable(
            "bulk_receipts_clean.committee_id_sbe is required for race linkage.",
            supports_date_window=True,
        )

    race_limit = min(max(request.args.get("race_limit", 8, type=int) or 8, 4), 18)
    month_limit = min(max(request.args.get("month_limit", 18, type=int) or 18, 6), 48)
    receipt_filters = [
        "COALESCE(r.amount, 0.0) > 0",
        "r.received_date IS NOT NULL",
        "LENGTH(r.received_date) >= 7",
    ]
    if _column_exists(conn, "bulk_receipts_clean", "is_archived"):
        receipt_filters.append("NOT COALESCE(r.is_archived::boolean, FALSE)")
    if _column_exists(conn, "bulk_receipts_clean", "d2_part_code"):
        receipt_filters.append("COALESCE(r.d2_part_code, '') LIKE '1%'")
    receipt_filters.append("(? IS NULL OR r.received_date >= ?)")
    receipt_filters.append("(? IS NULL OR r.received_date <= ?)")
    receipt_where = " AND ".join(receipt_filters)

    rows = conn.execute(
        f"""
        WITH candidate_map AS (
            SELECT DISTINCT
                CAST(committee_id_sbe AS TEXT) AS committee_id,
                TRIM(COALESCE(office_sought, 'Unknown Office')) AS office_sought,
                TRIM(COALESCE(district_type, 'Unknown District')) AS district_type,
                TRIM(COALESCE(district, '')) AS district
            FROM bulk_candidate_committee_finance_agg
            WHERE committee_id_sbe IS NOT NULL
        ),
        race_monthly AS (
            SELECT
                cm.office_sought,
                cm.district_type,
                cm.district,
                SUBSTR(r.received_date, 1, 7) AS month_key,
                COALESCE(SUM(COALESCE(r.amount, 0.0)), 0.0) AS month_amount
            FROM bulk_receipts_clean r
            JOIN candidate_map cm
              ON CAST(r.committee_id_sbe AS TEXT) = cm.committee_id
            WHERE {receipt_where}
            GROUP BY cm.office_sought, cm.district_type, cm.district, month_key
        ),
        top_races AS (
            SELECT office_sought, district_type, district, SUM(month_amount) AS race_total
            FROM race_monthly
            GROUP BY office_sought, district_type, district
            ORDER BY race_total DESC
            LIMIT ?
        ),
        cumulative AS (
            SELECT
                rm.office_sought,
                rm.district_type,
                rm.district,
                rm.month_key,
                rm.month_amount,
                SUM(rm.month_amount) OVER (
                    PARTITION BY rm.office_sought, rm.district_type, rm.district
                    ORDER BY rm.month_key
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative_amount
            FROM race_monthly rm
            JOIN top_races tr
              ON tr.office_sought = rm.office_sought
             AND tr.district_type = rm.district_type
             AND tr.district = rm.district
        ),
        limited AS (
            SELECT
                office_sought,
                district_type,
                district,
                month_key,
                month_amount,
                cumulative_amount,
                ROW_NUMBER() OVER (
                    PARTITION BY office_sought, district_type, district
                    ORDER BY month_key DESC
                ) AS rn_recent
            FROM cumulative
        )
        SELECT
            office_sought,
            district_type,
            district,
            month_key,
            month_amount,
            cumulative_amount
        FROM limited
        WHERE rn_recent <= ?
        ORDER BY office_sought, district_type, district, month_key
        """,
        [date_from, date_from, date_to, date_to, race_limit, month_limit],
    ).fetchall()

    if not rows:
        return _prototype_unavailable(
            "No monthly inflow rows found for current filters.",
            supports_date_window=True,
        )

    series_by_race: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        race_label = (
            f"{row['office_sought']} - {row['district_type']}"
            f"{(' ' + row['district']) if (row['district'] or '').strip() else ''}"
        )
        series_by_race[race_label].append(
            {
                "month": row["month_key"],
                "month_amount": round(float(row["month_amount"] or 0.0), 2),
                "cumulative_amount": round(float(row["cumulative_amount"] or 0.0), 2),
            }
        )

    chart_rows = []
    summary_rows = []
    for race_label, points in series_by_race.items():
        points.sort(key=lambda point: point["month"])
        latest = points[-1]
        chart_rows.append({"race_label": race_label, "points": points})
        summary_rows.append(
            {
                "race_label": race_label,
                "latest_month": latest["month"],
                "latest_cumulative_amount": latest["cumulative_amount"],
                "latest_month_amount": latest["month_amount"],
                "month_points": len(points),
            }
        )

    summary_rows.sort(key=lambda row: row["latest_cumulative_amount"], reverse=True)
    table_rows, pagination = _paginate(summary_rows, page, per_page)
    return {
        "available": True,
        "supports_date_window": True,
        "chart_rows": chart_rows,
        "table_rows": table_rows,
        "pagination": pagination,
        "summary": {
            "race_count": len(chart_rows),
            "month_limit": month_limit,
            "race_limit": race_limit,
        },
        "notes": {
            "sql_source": "Set-based SQL CTE over bulk_receipts_clean + bulk_candidate_committee_finance_agg.",
            "known_limitations": [
                "Committee-to-race linkage assumes committee IDs map cleanly to candidate finance rows.",
                "Recent month truncation (month_limit) is intentional for local render speed.",
            ],
            "validate": [
                "Confirm cumulative lines move when date_from/date_to changes.",
                "Check top cumulative races align with race scatter totals for the same window.",
            ],
        },
    }


def _build_payee_dominance(
    conn,
    *,
    date_from: str | None,
    date_to: str | None,
    page: int,
    per_page: int,
) -> dict:
    required = {"bulk_expenditures_clean", "bulk_candidate_committee_finance_agg"}
    if not all(_table_exists(conn, table_name) for table_name in required):
        return _prototype_unavailable(
            "Missing required tables: bulk_expenditures_clean and/or bulk_candidate_committee_finance_agg.",
            supports_date_window=True,
        )

    if not _column_exists(conn, "bulk_expenditures_clean", "payee_last_or_business_name"):
        return _prototype_unavailable(
            "bulk_expenditures_clean.payee_last_or_business_name is required for payee dominance prototype.",
            supports_date_window=True,
        )
    required_candidate_columns = {"committee_id_sbe", "office_sought", "district_type", "district"}
    if not all(_column_exists(conn, "bulk_candidate_committee_finance_agg", column) for column in required_candidate_columns):
        return _prototype_unavailable(
            "bulk_candidate_committee_finance_agg is missing required district/race columns.",
            supports_date_window=True,
        )
    if not _column_exists(conn, "bulk_expenditures_clean", "committee_id_sbe"):
        return _prototype_unavailable(
            "bulk_expenditures_clean.committee_id_sbe is required for race linkage.",
            supports_date_window=True,
        )

    has_expended_date = _column_exists(conn, "bulk_expenditures_clean", "expended_date")
    if (date_from or date_to) and not has_expended_date:
        return _prototype_unavailable(
            "Date window requested but bulk_expenditures_clean.expended_date is not available.",
            supports_date_window=False,
        )

    race_limit = min(max(request.args.get("race_limit", 12, type=int) or 12, 4), 24)
    payee_per_race = min(max(request.args.get("payee_per_race", 5, type=int) or 5, 3), 12)
    top_payee_count = min(max(request.args.get("top_payees", 20, type=int) or 20, 10), 40)
    exp_filters = [
        "COALESCE(e.amount, 0.0) > 0",
        "TRIM(COALESCE(e.payee_last_or_business_name, '')) <> ''",
    ]
    if _column_exists(conn, "bulk_expenditures_clean", "is_archived"):
        exp_filters.append("NOT COALESCE(e.is_archived::boolean, FALSE)")
    params: list[object] = []
    if has_expended_date:
        exp_filters.append("(? IS NULL OR e.expended_date >= ?)")
        exp_filters.append("(? IS NULL OR e.expended_date <= ?)")
        params.extend([date_from, date_from, date_to, date_to])
    exp_where = " AND ".join(exp_filters)

    rows = conn.execute(
        f"""
        WITH candidate_map AS (
            SELECT DISTINCT
                CAST(committee_id_sbe AS TEXT) AS committee_id,
                TRIM(COALESCE(office_sought, 'Unknown Office')) AS office_sought,
                TRIM(COALESCE(district_type, 'Unknown District')) AS district_type,
                TRIM(COALESCE(district, '')) AS district
            FROM bulk_candidate_committee_finance_agg
            WHERE committee_id_sbe IS NOT NULL
        ),
        payee_by_race AS (
            SELECT
                cm.office_sought,
                cm.district_type,
                cm.district,
                UPPER(TRIM(REPLACE(REPLACE(REPLACE(COALESCE(e.payee_last_or_business_name, ''), '.', ''), ',', ''), '  ', ' '))) AS payee_key,
                MIN(TRIM(COALESCE(e.payee_last_or_business_name, ''))) AS payee_name,
                COALESCE(SUM(COALESCE(e.amount, 0.0)), 0.0) AS total_amount,
                COUNT(*) AS txn_count
            FROM bulk_expenditures_clean e
            JOIN candidate_map cm
              ON CAST(e.committee_id_sbe AS TEXT) = cm.committee_id
            WHERE {exp_where}
            GROUP BY cm.office_sought, cm.district_type, cm.district, payee_key
        ),
        top_races AS (
            SELECT office_sought, district_type, district, SUM(total_amount) AS race_total
            FROM payee_by_race
            GROUP BY office_sought, district_type, district
            ORDER BY race_total DESC
            LIMIT ?
        ),
        ranked AS (
            SELECT
                pbr.office_sought,
                pbr.district_type,
                pbr.district,
                pbr.payee_key,
                pbr.payee_name,
                pbr.total_amount,
                pbr.txn_count,
                ROW_NUMBER() OVER (
                    PARTITION BY pbr.office_sought, pbr.district_type, pbr.district
                    ORDER BY pbr.total_amount DESC, pbr.payee_name ASC
                ) AS rn
            FROM payee_by_race pbr
            JOIN top_races tr
              ON tr.office_sought = pbr.office_sought
             AND tr.district_type = pbr.district_type
             AND tr.district = pbr.district
        )
        SELECT
            office_sought,
            district_type,
            district,
            payee_key,
            payee_name,
            total_amount,
            txn_count
        FROM ranked
        WHERE rn <= ?
        ORDER BY total_amount DESC
        """,
        [*params, race_limit, payee_per_race],
    ).fetchall()

    if not rows:
        return _prototype_unavailable(
            "No payee dominance rows found for current filters.",
            supports_date_window=True,
        )

    table_rows = []
    payee_totals: dict[str, float] = defaultdict(float)
    payee_display: dict[str, str] = {}
    for row in rows:
        race_label = (
            f"{row['office_sought']} - {row['district_type']}"
            f"{(' ' + row['district']) if (row['district'] or '').strip() else ''}"
        )
        payee_key = (row["payee_key"] or "").strip()
        payee_name = (row["payee_name"] or "").strip() or payee_key or "Unknown Payee"
        amount = round(float(row["total_amount"] or 0.0), 2)
        payee_totals[payee_key] += amount
        payee_display[payee_key] = payee_name
        table_rows.append(
            {
                "race_label": race_label,
                "payee_name": payee_name,
                "total_amount": amount,
                "txn_count": int(row["txn_count"] or 0),
            }
        )

    table_rows.sort(key=lambda row: row["total_amount"], reverse=True)
    page_rows, pagination = _paginate(table_rows, page, per_page)

    payee_chart_rows = sorted(
        [
            {"payee_name": payee_display.get(payee_key, payee_key), "total_amount": round(total, 2)}
            for payee_key, total in payee_totals.items()
            if payee_key
        ],
        key=lambda row: row["total_amount"],
        reverse=True,
    )[:top_payee_count]

    return {
        "available": True,
        "supports_date_window": True,
        "chart_rows": payee_chart_rows,
        "table_rows": page_rows,
        "pagination": pagination,
        "summary": {
            "race_count": len({row["race_label"] for row in table_rows}),
            "payee_rows": len(table_rows),
            "chart_payee_count": len(payee_chart_rows),
        },
        "notes": {
            "sql_source": "Set-based SQL CTE over bulk_expenditures_clean + bulk_candidate_committee_finance_agg.",
            "known_limitations": [
                "Payee normalization is lightweight (punctuation/case trim) and may still split aliases.",
                "Current prototype emphasizes top payees per top races for render speed.",
            ],
            "validate": [
                "Confirm top payees remain stable across repeated loads for the same window.",
                "Compare one race/payee against raw expenditure search for spot validation.",
            ],
        },
    }


def _build_entity_resolution(
    conn,
    *,
    page: int,
    per_page: int,
) -> dict:
    if not _table_exists(conn, "analytics_donor_summary"):
        return _prototype_unavailable(
            "Missing required table: analytics_donor_summary.",
            supports_date_window=False,
        )

    source_row = conn.execute(
        """
        SELECT source, COUNT(*) AS row_count
        FROM analytics_donor_summary
        GROUP BY source
        ORDER BY row_count DESC
        LIMIT 1
        """
    ).fetchone()
    source = (source_row["source"] if source_row else "bulk_receipts") or "bulk_receipts"
    row_limit = min(max(request.args.get("row_limit", 250, type=int) or 250, 50), 500)

    canonical_expr = (
        "TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE("
        "UPPER(COALESCE(donor_name, '')), '.', ''), ',', ''), ' LLC', ''), ' INC', ''), "
        "' CORPORATION', ''), ' CORP', ''))"
    )
    grouped_rows = conn.execute(
        f"""
        WITH normalized AS (
            SELECT
                donor_key,
                donor_name,
                COALESCE(total_amount, 0.0) AS total_amount,
                {canonical_expr} AS canonical_name
            FROM analytics_donor_summary
            WHERE source = ?
              AND COALESCE(total_amount, 0.0) > 0
        )
        SELECT
            canonical_name,
            COUNT(DISTINCT donor_key) AS variant_count,
            COALESCE(SUM(total_amount), 0.0) AS amount_at_risk,
            COALESCE(MAX(total_amount), 0.0) AS top_variant_amount
        FROM normalized
        WHERE canonical_name <> ''
        GROUP BY canonical_name
        HAVING COUNT(DISTINCT donor_key) >= 2
        ORDER BY amount_at_risk DESC
        LIMIT ?
        """,
        [source, row_limit],
    ).fetchall()

    if not grouped_rows:
        return _prototype_unavailable(
            "No entity-variant groups found in analytics_donor_summary for current snapshot.",
            supports_date_window=False,
        )

    all_rows = [
        {
            "canonical_name": row["canonical_name"],
            "variant_count": int(row["variant_count"] or 0),
            "amount_at_risk": round(float(row["amount_at_risk"] or 0.0), 2),
            "top_variant_amount": round(float(row["top_variant_amount"] or 0.0), 2),
        }
        for row in grouped_rows
    ]

    page_rows, pagination = _paginate(all_rows, page, per_page)
    canonical_names = [row["canonical_name"] for row in page_rows if row["canonical_name"]]

    variant_examples: dict[str, list[str]] = defaultdict(list)
    if canonical_names:
        placeholders = ",".join("?" * len(canonical_names))
        variant_rows = conn.execute(
            f"""
            WITH normalized AS (
                SELECT
                    donor_name,
                    COALESCE(total_amount, 0.0) AS total_amount,
                    {canonical_expr} AS canonical_name
                FROM analytics_donor_summary
                WHERE source = ?
                  AND COALESCE(total_amount, 0.0) > 0
            ),
            ranked AS (
                SELECT
                    canonical_name,
                    donor_name,
                    total_amount,
                    ROW_NUMBER() OVER (
                        PARTITION BY canonical_name
                        ORDER BY total_amount DESC, donor_name ASC
                    ) AS rn
                FROM normalized
                WHERE canonical_name IN ({placeholders})
            )
            SELECT canonical_name, donor_name, total_amount
            FROM ranked
            WHERE rn <= 3
            ORDER BY canonical_name, total_amount DESC
            """,
            [source, *canonical_names],
        ).fetchall()
        for row in variant_rows:
            label = f"{row['donor_name']} (${float(row['total_amount'] or 0.0):,.0f})"
            variant_examples[row["canonical_name"]].append(label)

    for row in page_rows:
        row["variant_examples"] = variant_examples.get(row["canonical_name"], [])

    chart_rows = all_rows[:20]
    return {
        "available": True,
        "supports_date_window": False,
        "chart_rows": chart_rows,
        "table_rows": page_rows,
        "pagination": pagination,
        "summary": {
            "source": source,
            "group_count": len(all_rows),
            "amount_at_risk_total": round(sum(row["amount_at_risk"] for row in all_rows), 2),
        },
        "notes": {
            "sql_source": "analytics_donor_summary canonical-name rollup (snapshot-level).",
            "known_limitations": [
                "Date window is not yet supported for this panel (uses snapshot aggregate table).",
                "Canonicalization is heuristic and intentionally conservative.",
            ],
            "validate": [
                "Review top canonical groups for obvious false-positive merges/splits.",
                "Use this panel to prioritize alias normalization work before production charts.",
            ],
        },
    }


def _build_network_slice(
    conn,
    *,
    date_from: str | None,
    date_to: str | None,
    page: int,
    per_page: int,
) -> dict:
    mode = (request.args.get("mode", "fast", type=str) or "fast").strip().lower()
    if mode not in {"fast", "safe"}:
        mode = "fast"
    mode_profiles = {
        "fast": {"max_nodes": 160, "max_edges": 700, "default_k": 32},
        "safe": {"max_nodes": 260, "max_edges": 1200, "default_k": 64},
    }
    profile = mode_profiles[mode]

    edge_threshold_raw = request.args.get("edge_threshold", default=None, type=float)
    if edge_threshold_raw is None:
        min_edge_amount = max(request.args.get("min_edge_amount", 5000.0, type=float) or 5000.0, 0.0)
    else:
        min_edge_amount = max(float(edge_threshold_raw), 0.0)

    edge_limit = min(max(request.args.get("edge_limit", 600, type=int) or 600, 100), 2000)
    node_cap = min(max(request.args.get("node_cap", 150, type=int) or 150, 40), 600)
    node_type = (request.args.get("node_type", "all", type=str) or "all").strip().lower()
    search_text = (request.args.get("search", "", type=str) or "").strip().lower()
    compute_advanced = _is_true_arg(request.args.get("compute_advanced", "", type=str))
    compute_communities_flag = _is_true_arg(request.args.get("compute_communities", "1", type=str))
    weight_mode = (request.args.get("weight_mode", "weighted", type=str) or "weighted").strip().lower()
    if weight_mode not in {"weighted", "unweighted"}:
        weight_mode = "weighted"
    k = request.args.get("k", profile["default_k"], type=int) or profile["default_k"]
    k = min(max(int(k), 8), 128)

    if node_type not in {"all", "donor", "committee", "candidate"}:
        node_type = "all"

    graph = get_network_graph(
        conn,
        min_edge_amount=min_edge_amount,
        limit=edge_limit,
        date_from=date_from,
        date_to=date_to,
    )
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes or not edges:
        return _prototype_unavailable(
            "Network graph returned no rows for current filters.",
            supports_date_window=True,
        )

    initial_node_lookup = {
        str(node.get("id") or "").strip(): {
            "label": node.get("label") or node.get("id") or "Unknown",
            "node_type": (node.get("node_type") or "unknown").strip().lower(),
            "system": (node.get("system") or "").strip() or None,
        }
        for node in nodes
        if str(node.get("id") or "").strip()
    }
    initial_degree_map: dict[str, int] = defaultdict(int)
    initial_weighted_degree_map: dict[str, float] = defaultdict(float)
    for edge in edges:
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if source not in initial_node_lookup or target not in initial_node_lookup:
            continue
        weight = float(edge.get("weight") or 0.0)
        initial_degree_map[source] += 1
        initial_degree_map[target] += 1
        initial_weighted_degree_map[source] += weight
        initial_weighted_degree_map[target] += weight

    ranked_nodes = []
    for node_id, meta in initial_node_lookup.items():
        row_type = meta["node_type"]
        label = (meta["label"] or "").strip()
        if node_type != "all" and row_type != node_type:
            continue
        if search_text and search_text not in label.lower():
            continue
        ranked_nodes.append(
            {
                "node_id": node_id,
                "label": label or node_id,
                "node_type": row_type or "unknown",
                "weighted_degree": round(float(initial_weighted_degree_map.get(node_id, 0.0)), 2),
                "degree": int(initial_degree_map.get(node_id, 0)),
            }
        )

    ranked_nodes.sort(
        key=lambda row: (row["weighted_degree"], row["degree"], row["label"]),
        reverse=True,
    )
    kept_nodes = {row["node_id"] for row in ranked_nodes[:node_cap] if row.get("node_id")}
    if not kept_nodes:
        return _prototype_unavailable(
            "No nodes remain after node-type/search filters.",
            supports_date_window=True,
        )

    filtered_edges = [
        {
            "source": edge.get("source"),
            "target": edge.get("target"),
            "edge_type": edge.get("edge_type"),
            "weight": round(float(edge.get("weight") or 0.0), 2),
        }
        for edge in edges
        if edge.get("source") in kept_nodes and edge.get("target") in kept_nodes
    ]
    filtered_edges.sort(key=lambda row: row["weight"], reverse=True)
    filtered_edges = filtered_edges[:edge_limit]

    node_lookup = {
        node.get("id"): {
            "label": node.get("label") or node.get("id") or "Unknown",
            "node_type": (node.get("node_type") or "unknown").strip().lower(),
            "system": (node.get("system") or "").strip() or None,
        }
        for node in nodes
        if node.get("id") in kept_nodes
    }

    actual_nodes = len(node_lookup)
    actual_edges = len(filtered_edges)
    edge_type_set = sorted({edge["edge_type"] for edge in filtered_edges if edge.get("edge_type")})
    exceeds_caps = actual_nodes > profile["max_nodes"] or actual_edges > profile["max_edges"]
    if compute_advanced and exceeds_caps:
        unavailable_payload = _prototype_unavailable(
            (
                "Advanced metrics request is too large for current safety caps. "
                "Tighten filters (higher edge threshold, lower node cap/edge limit, or lower k) and retry."
            ),
            supports_date_window=True,
        )
        unavailable_payload["graph_meta"] = {
            "compute_ms": 0.0,
            "k": k,
            "mode": mode,
            "weight_mode": weight_mode,
            "compute_advanced": True,
            "compute_communities": compute_communities_flag,
            "edge_type_set": edge_type_set,
            "cache_hit": False,
            "caps": {
                "max_nodes": profile["max_nodes"],
                "max_edges": profile["max_edges"],
                "actual_nodes": actual_nodes,
                "actual_edges": actual_edges,
                "node_cap_requested": node_cap,
                "edge_limit_requested": edge_limit,
                "within_caps": False,
                "refused": True,
            },
            "warnings": [
                "Advanced compute refused by hard caps before metric execution.",
                "Suggested defaults: mode=fast, k=32, node_cap<=120, edge_limit<=500.",
            ],
            "community_method": "not_run",
        }
        unavailable_payload["node_metrics"] = []
        unavailable_payload["community_summary"] = []
        unavailable_payload["edge_rows"] = []
        unavailable_payload["notes"] = {
            "sql_source": "database.analytics.get_network_graph() bounded by endpoint filters.",
            "known_limitations": [
                "Advanced metrics are intentionally blocked for oversized graph slices.",
            ],
            "validate": [
                "Increase edge threshold or reduce node cap before retrying advanced metrics.",
            ],
        }
        return unavailable_payload

    degree_map: dict[str, int] = defaultdict(int)
    weighted_degree_map: dict[str, float] = defaultdict(float)
    edge_rows = []
    for edge in filtered_edges:
        source_meta = node_lookup.get(edge["source"], {})
        target_meta = node_lookup.get(edge["target"], {})
        degree_map[str(edge["source"])] += 1
        degree_map[str(edge["target"])] += 1
        weighted_degree_map[str(edge["source"])] += float(edge["weight"] or 0.0)
        weighted_degree_map[str(edge["target"])] += float(edge["weight"] or 0.0)
        edge_rows.append(
            {
                "source_label": source_meta.get("label", edge["source"]),
                "source_type": source_meta.get("node_type", "unknown"),
                "target_label": target_meta.get("label", edge["target"]),
                "target_type": target_meta.get("node_type", "unknown"),
                "edge_type": edge["edge_type"] or "unknown",
                "weight": edge["weight"],
            }
        )

    node_metrics_map: dict[str, dict] = {}
    for node_id, meta in node_lookup.items():
        node_metrics_map[node_id] = {
            "node_id": node_id,
            "label": meta["label"],
            "node_type": meta["node_type"],
            "system": meta.get("system"),
            "degree": int(degree_map.get(node_id, 0)),
            "weighted_degree": round(float(weighted_degree_map.get(node_id, 0.0)), 2),
            "betweenness_approx": None,
            "community_id": None,
            "bridge_ratio": None,
        }

    compute_ms = 0.0
    community_method = "disabled"
    warnings: list[str] = []
    resolved_k = k
    if compute_advanced:
        metric_nodes = [
            {
                "id": node_id,
                "label": meta["label"],
                "node_type": meta["node_type"],
                "system": meta.get("system"),
            }
            for node_id, meta in node_lookup.items()
        ]
        metric_start = time.perf_counter()
        advanced_payload = compute_advanced_network_metrics(
            metric_nodes,
            filtered_edges,
            k=k,
            seed=42,
            weight_mode=weight_mode,
            compute_communities_flag=compute_communities_flag,
        )
        compute_ms = round((time.perf_counter() - metric_start) * 1000.0, 2)
        advanced_map = advanced_payload.get("node_metrics") or {}
        for node_id, metric_row in advanced_map.items():
            if node_id in node_metrics_map:
                node_metrics_map[node_id].update(metric_row)
        meta = advanced_payload.get("meta") or {}
        community_method = meta.get("community_method") or "not_run"
        warnings = list(meta.get("warnings") or [])
        resolved_k = int(meta.get("k") or resolved_k)

    node_metrics = sorted(
        node_metrics_map.values(),
        key=lambda row: (
            float(row.get("weighted_degree") or 0.0),
            float(row.get("betweenness_approx") or 0.0),
            int(row.get("degree") or 0),
            row.get("label") or "",
        ),
        reverse=True,
    )

    community_summary: list[dict] = []
    if compute_advanced and compute_communities_flag:
        community_buckets: dict[int, list[dict]] = defaultdict(list)
        for row in node_metrics:
            community_id = row.get("community_id")
            if community_id is None:
                continue
            community_buckets[int(community_id)].append(row)
        for community_id in sorted(community_buckets):
            members = community_buckets[community_id]
            members.sort(
                key=lambda row: (
                    float(row.get("betweenness_approx") or 0.0),
                    float(row.get("weighted_degree") or 0.0),
                ),
                reverse=True,
            )
            community_summary.append(
                {
                    "community_id": community_id,
                    "size": len(members),
                    "top_nodes": [
                        {
                            "label": member.get("label") or member.get("node_id"),
                            "betweenness_approx": member.get("betweenness_approx"),
                            "weighted_degree": member.get("weighted_degree"),
                        }
                        for member in members[:3]
                    ],
                }
            )

    table_page, pagination = _paginate(node_metrics, page, per_page)
    chart_rows = node_metrics[:25]
    return {
        "available": True,
        "supports_date_window": True,
        "chart_rows": chart_rows,
        "table_rows": table_page,
        "edge_rows": edge_rows[:120],
        "node_metrics": node_metrics,
        "community_summary": community_summary,
        "pagination": pagination,
        "graph_meta": {
            "compute_ms": compute_ms,
            "k": resolved_k if compute_advanced else None,
            "mode": mode,
            "weight_mode": weight_mode,
            "compute_advanced": compute_advanced,
            "compute_communities": bool(compute_advanced and compute_communities_flag),
            "edge_type_set": edge_type_set,
            "community_method": community_method,
            "cache_hit": False,
            "caps": {
                "max_nodes": profile["max_nodes"],
                "max_edges": profile["max_edges"],
                "actual_nodes": actual_nodes,
                "actual_edges": actual_edges,
                "node_cap_requested": node_cap,
                "edge_limit_requested": edge_limit,
                "within_caps": not exceeds_caps,
                "refused": False,
            },
            "warnings": warnings,
        },
        "summary": {
            "node_count": len(kept_nodes),
            "edge_count": len(filtered_edges),
            "min_edge_amount": min_edge_amount,
            "node_type_filter": node_type,
            "search": search_text,
            "edge_limit": edge_limit,
            "mode": mode,
            "advanced_metrics": compute_advanced,
        },
        "notes": {
            "sql_source": "database.analytics.get_network_graph() with top-N/node filters in API layer.",
            "known_limitations": [
                "Bridge ratio uses existing node system labels and may be null when labels are missing.",
                "Mixed semantics across edge types require context from edge_type label.",
            ],
            "validate": [
                "Use min-edge slider to confirm graph contraction/expansion behavior.",
                "Search and node-type filters should reduce node/edge counts deterministically.",
                "Advanced metrics require explicit compute action and remain cap-bounded.",
            ],
        },
    }


PROTOTYPE_META = [
    {
        "key": "race_money_pressure",
        "title": "Race Money vs Outside Pressure",
        "description": "Scatter plot of race receipts versus outside-spending pressure for rapid race triage.",
        "question": "Which races are simultaneously money-heavy and outside-pressure-heavy?",
        "data_source": "bulk_candidate_committee_finance_agg + bulk_receipts_clean + bulk_expenditures_clean",
    },
    {
        "key": "race_concentration",
        "title": "Race Funding Concentration Quadrant",
        "description": "Quadrant view of donor breadth versus top-candidate receipt concentration.",
        "question": "Which races are broad-based versus concentrated among top contenders?",
        "data_source": "bulk_candidate_committee_finance_agg + bulk_receipts_clean",
    },
    {
        "key": "cumulative_inflow",
        "title": "Cumulative Inflow by Top Races",
        "description": "Multi-line cumulative inflow trends for the top races in the selected window.",
        "question": "How quickly is money entering competitive races over time?",
        "data_source": "bulk_receipts_clean + bulk_candidate_committee_finance_agg",
    },
    {
        "key": "payee_dominance",
        "title": "Payee Dominance Across Races",
        "description": "Top payees by aggregate spending with race-level breakdown rows.",
        "question": "Which payees dominate campaign spending across high-volume races?",
        "data_source": "bulk_expenditures_clean + bulk_candidate_committee_finance_agg",
    },
    {
        "key": "entity_resolution",
        "title": "Entity Resolution Risk Panel",
        "description": "Canonical-name rollup showing donor variant clusters and dollars at risk.",
        "question": "Where are naming variants likely distorting downstream analytics?",
        "data_source": "analytics_donor_summary (snapshot aggregate)",
    },
    {
        "key": "network_slice",
        "title": "Constrained Network Slice",
        "description": "Top-N filtered donor-committee-candidate network with threshold and search controls.",
        "question": "Who are the highest-connectivity nodes within a constrained graph slice?",
        "data_source": "analytics donor->committee flow + committee->candidate linkage graph",
    },
]

PROTOTYPE_BUILDERS = {
    "race_money_pressure": _build_race_money_pressure,
    "race_concentration": _build_race_concentration,
    "cumulative_inflow": _build_cumulative_inflow,
    "payee_dominance": _build_payee_dominance,
    "entity_resolution": _build_entity_resolution,
    "network_slice": _build_network_slice,
}


@experimental_bp.before_request
def _guard_feature_flag():
    if not _feature_enabled():
        abort(404)


@experimental_bp.route("/viz-lab")
@experimental_bp.route("/viz-lab/<prototype_key>")
def viz_lab(prototype_key: str | None = None):
    period, date_from, date_to = _resolved_date_window()
    active_key = prototype_key if prototype_key in PROTOTYPE_BUILDERS else None
    prototype_payload = []
    for item in PROTOTYPE_META:
        prototype_payload.append(
            {
                **item,
                "endpoint": url_for("experimental.viz_lab_data", prototype_key=item["key"]),
                "detail_url": url_for("experimental.viz_lab", prototype_key=item["key"]),
            }
        )

    return render_template(
        "experimental/viz_lab.html",
        active_prototype=active_key,
        prototypes=prototype_payload,
        period_key=period.get("key"),
        date_from=date_from or "",
        date_to=date_to or "",
    )


@experimental_bp.route("/viz-lab/data/<prototype_key>")
def viz_lab_data(prototype_key: str):
    builder = PROTOTYPE_BUILDERS.get(prototype_key)
    if not builder:
        return jsonify({"error": "unknown_prototype"}), 404

    conn = current_app.get_database()
    period, date_from, date_to = _resolved_date_window()
    page, per_page = _parse_pagination()
    cache_params = {
        "period": period.get("key"),
        "date_from": date_from,
        "date_to": date_to,
        "page": page,
        "per_page": per_page,
        "prototype_key": prototype_key,
        "race_limit": request.args.get("race_limit"),
        "month_limit": request.args.get("month_limit"),
        "payee_per_race": request.args.get("payee_per_race"),
        "top_payees": request.args.get("top_payees"),
        "min_edge_amount": request.args.get("min_edge_amount"),
        "edge_limit": request.args.get("edge_limit"),
        "node_cap": request.args.get("node_cap"),
        "node_type": request.args.get("node_type"),
        "search": request.args.get("search"),
        "compute_advanced": request.args.get("compute_advanced"),
        "compute_communities": request.args.get("compute_communities"),
        "mode": request.args.get("mode"),
        "k": request.args.get("k"),
        "weight_mode": request.args.get("weight_mode"),
        "edge_threshold": request.args.get("edge_threshold"),
        "row_limit": request.args.get("row_limit"),
    }

    def _run():
        if prototype_key == "entity_resolution":
            payload = builder(conn, page=page, per_page=per_page)
        else:
            payload = builder(
                conn,
                date_from=date_from,
                date_to=date_to,
                page=page,
                per_page=per_page,
            )
        payload["window"] = {
            "period_key": period.get("key"),
            "date_from": date_from,
            "date_to": date_to,
        }
        return payload

    payload = _cached_payload(prototype_key, cache_params, _run)
    return jsonify(payload)
