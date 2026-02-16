"""Main routes for dashboard, search, and compare views."""
from collections import defaultdict, OrderedDict
import csv
from datetime import datetime
import inspect
from io import StringIO
import sqlite3
import threading
import time

from flask import Blueprint, Response, render_template, request, current_app

from database.models import Committee, Report, Donor, Contribution

main_bp = Blueprint('main', __name__)

_dashboard_insights_cache = {
    "value": None,
    "expires_at": 0.0,
    "key": None,
}
_dashboard_insights_cache_lock = threading.Lock()
_candidate_stats_cache = {
    "value": None,
    "expires_at": 0.0,
    "key": None,
}
_candidate_stats_cache_lock = threading.Lock()
_top_donors_cache = {
    "value": None,
    "expires_at": 0.0,
    "key": None,
}
_top_donors_cache_lock = threading.Lock()
_search_results_cache: OrderedDict[str, dict] = OrderedDict()
_search_results_cache_lock = threading.Lock()

SEARCH_TYPES = {
    "all",
    "committees",
    "donors",
    "candidates",
    "reports",
    "filed_docs",
    "donor_keys",
}
COMPARE_MODES = {"candidate", "committee"}


def _sanitize_search_query(raw_query: str, max_length: int) -> tuple[str, bool]:
    text = " ".join((raw_query or "").strip().split())
    sanitized = text.replace("%", " ").replace("_", " ")
    sanitized = " ".join(sanitized.split())
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].strip()
    return sanitized, sanitized != text


def _run_query_with_timeout(conn, query_fn, timeout_ms: int) -> tuple[object, float, bool]:
    """Execute sqlite work with a progress-handler timeout guardrail."""
    start = time.perf_counter()
    if timeout_ms <= 0:
        return query_fn(), (time.perf_counter() - start) * 1000.0, False

    deadline = start + (float(timeout_ms) / 1000.0)

    def _progress_handler():
        return 1 if time.perf_counter() >= deadline else 0

    conn.set_progress_handler(_progress_handler, 2000)
    timed_out = False
    try:
        result = query_fn()
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            result = []
            timed_out = True
        else:
            raise
    finally:
        conn.set_progress_handler(None, 0)

    return result, (time.perf_counter() - start) * 1000.0, timed_out


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    for row in rows:
        if row["name"] == column_name:
            return True
    return False


def _sql_date_expr(column: str) -> str:
    text_expr = f"TRIM(COALESCE(CAST({column} AS TEXT), ''))"
    first_slash_expr = f"INSTR({text_expr}, '/')"
    remainder_expr = f"SUBSTR({text_expr}, {first_slash_expr} + 1)"
    second_slash_expr = f"INSTR({remainder_expr}, '/')"
    month_expr = f"SUBSTR({text_expr}, 1, {first_slash_expr} - 1)"
    day_expr = f"SUBSTR({remainder_expr}, 1, {second_slash_expr} - 1)"
    year_expr = f"SUBSTR({remainder_expr}, {second_slash_expr} + 1, 4)"
    year_padded = (
        f"SUBSTR('0000' || CAST({year_expr} AS TEXT), "
        f"LENGTH('0000' || CAST({year_expr} AS TEXT)) - 3, 4)"
    )
    month_padded = (
        f"SUBSTR('00' || CAST({month_expr} AS TEXT), "
        f"LENGTH('00' || CAST({month_expr} AS TEXT)) - 1, 2)"
    )
    day_padded = (
        f"SUBSTR('00' || CAST({day_expr} AS TEXT), "
        f"LENGTH('00' || CAST({day_expr} AS TEXT)) - 1, 2)"
    )
    return f"""(
        CASE
            WHEN {text_expr} = '' THEN NULL
            WHEN {first_slash_expr} > 0 AND {second_slash_expr} > 0 THEN
                DATE(
                    {year_padded} || '-' || {month_padded} || '-' || {day_padded}
                )
            ELSE DATE(SUBSTR({text_expr}, 1, 10))
        END
    )"""


def _apply_date_window_clauses(
    where_clauses: list[str],
    params: list[object],
    date_expr: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> None:
    start = (date_from or "").strip()
    if start:
        where_clauses.append(f"{date_expr} >= DATE(?)")
        params.append(start[:10])
    end = (date_to or "").strip()
    if end:
        where_clauses.append(f"{date_expr} <= DATE(?)")
        params.append(end[:10])


def _text_date_window_clause(
    column: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    prefix: str = "AND",
) -> tuple[str, list[object]]:
    start_iso = (date_from or "").strip()[:10]
    end_iso = (date_to or "").strip()[:10]
    if not start_iso and not end_iso:
        return "", []

    iso_parts: list[str] = []
    iso_params: list[object] = []
    compact_parts: list[str] = []
    compact_params: list[object] = []

    if start_iso:
        iso_parts.append(f"{column} >= ?")
        iso_params.append(start_iso)
        compact_parts.append(f"{column} >= ?")
        compact_params.append(start_iso.replace("-", ""))
    if end_iso:
        iso_parts.append(f"{column} <= ?")
        iso_params.append(end_iso)
        compact_parts.append(f"{column} <= ?")
        compact_params.append(end_iso.replace("-", ""))

    if not iso_parts:
        return "", []

    clause = f" {prefix} (({' AND '.join(iso_parts)}) OR ({' AND '.join(compact_parts)}))"
    return clause, iso_params + compact_params


def _scalar(conn, sql: str, params=(), default=0):
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


def _csv_response(rows: list[list], headers: list[str], filename: str) -> Response:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


def _period_cache_token(period) -> str:
    from webapp.utils.time_filter import period_cache_key

    key = period_cache_key(period)
    if not period:
        return key
    return f"{key}:{period.get('start_date') or ''}:{period.get('end_date') or ''}"


def _search_results_cache_enabled() -> bool:
    return bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))


def _search_results_cache_ttl_seconds() -> int:
    return max(15, int(current_app.config.get("SEARCH_RESULTS_CACHE_TTL_SECONDS", 120)))


def _search_results_cache_max_entries() -> int:
    return max(16, int(current_app.config.get("SEARCH_RESULTS_CACHE_MAX_ENTRIES", 256)))


def _is_filed_doc_query(query: str) -> bool:
    text = (query or "").strip()
    return bool(text) and text.isdigit()


def _is_donor_key_query(query: str) -> bool:
    text = (query or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if "|" in lowered or ":" in lowered:
        return True
    digit_count = sum(1 for ch in lowered if ch.isdigit())
    return digit_count >= 3


def _search_results_cache_key(period, query: str, search_type: str) -> str:
    period_key = _period_cache_token(period)
    return f"{period_key}:{search_type}:{query.strip().lower()}"


def _get_cached_search_results(period, query: str, search_type: str) -> dict | None:
    if not _search_results_cache_enabled():
        return None
    key = _search_results_cache_key(period, query, search_type)
    now = time.monotonic()
    with _search_results_cache_lock:
        entry = _search_results_cache.get(key)
        if not entry:
            return None
        if float(entry.get("expires_at", 0.0)) <= now:
            _search_results_cache.pop(key, None)
            return None
        _search_results_cache.move_to_end(key)
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            return None
        return payload


def _set_cached_search_results(period, query: str, search_type: str, payload: dict) -> None:
    if not _search_results_cache_enabled():
        return
    key = _search_results_cache_key(period, query, search_type)
    now = time.monotonic()
    max_entries = _search_results_cache_max_entries()
    with _search_results_cache_lock:
        _search_results_cache.pop(key, None)
        _search_results_cache[key] = {
            "payload": payload,
            "expires_at": now + float(_search_results_cache_ttl_seconds()),
        }
        while len(_search_results_cache) > max_entries:
            _search_results_cache.popitem(last=False)


def _call_build_dashboard_insights(conn, period=None) -> dict:
    """Call _build_dashboard_insights with backward-compatible signature handling."""
    build_fn = _build_dashboard_insights
    try:
        signature = inspect.signature(build_fn)
    except (TypeError, ValueError):
        signature = None

    if signature and "period" in signature.parameters:
        return build_fn(conn, period=period)
    return build_fn(conn)


def _build_dashboard_insights(conn, period=None) -> dict:
    from webapp.utils.time_filter import period_qmark_date_clause

    insights = {
        "donor_dependent_committees": [],
        "lobbying_donor_overlap": [],
        "director_candidates": [],
        "dark_money_totals": {"total_amount": 0.0, "match_count": 0},
    }

    if _table_exists(conn, "analytics_donor_committee_agg"):
        insights["donor_dependent_committees"] = conn.execute(
            """
            WITH committee_totals AS (
                SELECT committee_id, committee_name, SUM(total_amount) AS committee_total
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                GROUP BY committee_id, committee_name
            ),
            ranked AS (
                SELECT a.committee_id, a.committee_name, a.donor_key, a.donor_name,
                       a.total_amount AS donor_amount,
                       t.committee_total,
                       (a.total_amount * 1.0) / NULLIF(t.committee_total, 0) AS pct_of_total,
                       ROW_NUMBER() OVER (PARTITION BY a.committee_id ORDER BY a.total_amount DESC) AS rn
                FROM analytics_donor_committee_agg a
                JOIN committee_totals t ON t.committee_id = a.committee_id
                WHERE a.source = 'bulk_receipts'
            )
            SELECT committee_id, committee_name, donor_key, donor_name, donor_amount, committee_total, pct_of_total
            FROM ranked
            WHERE rn = 1 AND pct_of_total >= 0.30
            ORDER BY pct_of_total DESC
            LIMIT 5
            """
        ).fetchall()

    if _table_exists(conn, "lobbying_donor_matches") and _table_exists(conn, "analytics_donor_summary"):
        insights["lobbying_donor_overlap"] = conn.execute(
            """
            SELECT ldm.client_id, ldm.client_name, ldm.donor_key, ldm.donor_name, ldm.score,
                   ads.total_amount
            FROM lobbying_donor_matches ldm
            LEFT JOIN analytics_donor_summary ads
              ON ads.donor_key = ldm.donor_key AND ads.source = 'bulk_receipts'
            WHERE ldm.score >= 0.80
            ORDER BY (ads.total_amount IS NULL) ASC, ads.total_amount DESC, ldm.score DESC
            LIMIT 5
            """
        ).fetchall()

    if _table_exists(conn, "irs527_director_candidate_matches"):
        insights["director_candidates"] = conn.execute(
            """
            SELECT ein, org_name, director_name, candidate_id, candidate_name, candidate_source, score
            FROM irs527_director_candidate_matches
            WHERE score >= 0.80
            ORDER BY score DESC
            LIMIT 10
            """
        ).fetchall()

    if _table_exists(conn, "irs527_expenditures") and _table_exists(conn, "irs527_expenditure_recipient_matches"):
        tx_clause, tx_params = period_qmark_date_clause("e.date", period) if period else ("", [])
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(e.amount), 0) AS total_amount, COUNT(*) AS match_count
            FROM irs527_expenditures e
            JOIN irs527_expenditure_recipient_matches m
              ON m.ein = e.ein AND m.recipient_name = e.recipient_name
            WHERE m.score >= 0.80
            {tx_clause}
            """,
            tuple(tx_params),
        ).fetchone()
        if row:
            insights["dark_money_totals"] = {
                "total_amount": float(row["total_amount"] or 0.0),
                "match_count": int(row["match_count"] or 0),
            }

    return insights


def _get_dashboard_insights(conn, period=None) -> dict:
    cache_key = _period_cache_token(period)
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    if not cache_enabled:
        return _call_build_dashboard_insights(conn, period=period)

    ttl_seconds = max(15, int(current_app.config.get("DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS", 180)))
    now = time.monotonic()
    with _dashboard_insights_cache_lock:
        if (
            _dashboard_insights_cache.get("value") is not None
            and _dashboard_insights_cache.get("key") == cache_key
            and float(_dashboard_insights_cache.get("expires_at", 0.0)) > now
        ):
            return _dashboard_insights_cache["value"]

    insights = _call_build_dashboard_insights(conn, period=period)
    with _dashboard_insights_cache_lock:
        _dashboard_insights_cache["value"] = insights
        _dashboard_insights_cache["expires_at"] = now + float(ttl_seconds)
        _dashboard_insights_cache["key"] = cache_key
    return insights


def warm_dashboard_insights_cache(database_path: str, ttl_seconds: int = 180) -> None:
    if ttl_seconds <= 0:
        return
    from database.connection import get_db

    conn = get_db(database_path)
    try:
        insights = _call_build_dashboard_insights(conn)
        with _dashboard_insights_cache_lock:
            _dashboard_insights_cache["value"] = insights
            _dashboard_insights_cache["expires_at"] = time.monotonic() + float(ttl_seconds)
            _dashboard_insights_cache["key"] = "2026cycle"
    finally:
        conn.close()


def warm_dashboard_home_cache(
    database_path: str,
    insights_ttl_seconds: int = 180,
    candidate_stats_ttl_seconds: int = 180,
    top_donors_ttl_seconds: int = 180,
) -> None:
    from database.connection import get_db

    conn = get_db(database_path)
    try:
        insights = _call_build_dashboard_insights(conn)
        stats = _get_candidate_stats(conn)
        if _table_exists(conn, "analytics_donor_summary"):
            donors = conn.execute(
                """
                SELECT
                    donor_name AS name,
                    donor_key,
                    source,
                    total_amount,
                    contribution_count,
                    NULL AS entity_id,
                    NULL AS id
                FROM analytics_donor_summary
                WHERE source = 'bulk_receipts'
                ORDER BY total_amount DESC
                LIMIT 8
                """
            ).fetchall()
        else:
            donors = Donor.get_all_with_totals(conn, limit=8, sort_by='total_amount')
        now = time.monotonic()
        with _dashboard_insights_cache_lock:
            _dashboard_insights_cache["value"] = insights
            _dashboard_insights_cache["expires_at"] = now + float(max(15, insights_ttl_seconds))
            _dashboard_insights_cache["key"] = "2026cycle"
        with _candidate_stats_cache_lock:
            _candidate_stats_cache["value"] = stats
            _candidate_stats_cache["expires_at"] = now + float(max(15, candidate_stats_ttl_seconds))
            _candidate_stats_cache["key"] = "2026cycle"
        with _top_donors_cache_lock:
            _top_donors_cache["value"] = donors
            _top_donors_cache["expires_at"] = now + float(max(15, top_donors_ttl_seconds))
            _top_donors_cache["key"] = "2026cycle"
    finally:
        conn.close()


def _get_candidate_stats(conn, period=None):
    """Return (stats_dict, freshness_dict) for state and federal candidate data.

    If period is provided (from time_filter.get_active_period()), date-bearing
    queries are filtered to the given window.
    """
    from webapp.utils.time_filter import period_cycles, period_qmark_clause

    federal_cycles = period_cycles(period) if period else None

    def _cycle_clause(date_column: str) -> tuple[str, list[object]]:
        if federal_cycles is None:
            return "", []
        if not federal_cycles:
            return " AND 1=0", []
        placeholders = ",".join(["?"] * len(federal_cycles))
        return f" AND {date_column} IN ({placeholders})", [int(cycle) for cycle in federal_cycles]

    stats = {
        'local_candidate_rows': 0,
        'local_candidates': 0,
        'local_committees': 0,
        'local_total_receipts': 0.0,
        'local_total_expenditures': 0.0,
        'local_archived_filings': 0,
        'federal_candidates': 0,
        'federal_contributions': 0,
        'federal_total_amount': 0.0,
        'federal_disbursements': 0,
        'federal_disbursement_total': 0.0,
        'federal_independent_expenditures': 0,
        'federal_independent_expenditure_total': 0.0,
        'federal_matched_donors': 0,
        'openbook_vendors_matched': 0,
        'openbook_contracts_total': 0,
        'openbook_award_total': 0.0,
    }

    if _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT CASE WHEN candidate_id IS NOT NULL THEN candidate_id END) AS candidate_count,
                COUNT(DISTINCT CASE WHEN committee_id_sbe IS NOT NULL THEN committee_id_sbe END) AS committee_count
            FROM bulk_candidate_committee_finance_agg
            """
        ).fetchone()
        if row:
            stats['local_candidate_rows'] = int(row["row_count"] or 0)
            stats['local_candidates'] = int(row["candidate_count"] or 0)
            stats['local_committees'] = int(row["committee_count"] or 0)

    if _table_exists(conn, "bulk_d2_totals_clean"):
        # D2 totals: filter by reporting_period_end via isbe_filed_docs
        d2_period_join = ""
        d2_clause = ""
        d2_period_params = ()
        if period and period.get("start_date"):
            d2_clause, d2_plist = period_qmark_clause("fd.reporting_period_end", period)
            if d2_clause:
                d2_period_join = " JOIN isbe_filed_docs fd ON fd.id = d2.filed_doc_id"
                d2_period_params = tuple(d2_plist)

        totals_row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(d2.total_receipts), 0) AS total_receipts,
                COALESCE(SUM(d2.total_expenditures), 0) AS total_expenditures
            FROM bulk_d2_totals_clean d2{d2_period_join}
            WHERE COALESCE(d2.is_archived, 0) = 0{d2_clause if d2_period_join else ''}
            """,
            d2_period_params,
        ).fetchone()
        if totals_row:
            stats['local_total_receipts'] = float(totals_row["total_receipts"] or 0.0)
            stats['local_total_expenditures'] = float(totals_row["total_expenditures"] or 0.0)
        stats['local_archived_filings'] = int(
            _scalar(
                conn,
                "SELECT COUNT(*) AS count FROM bulk_d2_totals_clean WHERE COALESCE(is_archived, 0) = 1",
                default=0,
            )
        )

    if _table_exists(conn, "fec_candidate_match"):
        cycle_clause, cycle_params = _cycle_clause("cycle")
        stats['federal_candidates'] = int(
            _scalar(
                conn,
                f"""
                SELECT COUNT(DISTINCT fec_candidate_id) AS count
                FROM fec_candidate_match
                WHERE fec_candidate_id IS NOT NULL{cycle_clause}
                """,
                params=tuple(cycle_params),
                default=0,
            )
        )

    fec_cycle_clause, fec_cycle_params = _cycle_clause("cycle")
    if _table_exists(conn, "fec_schedule_a_contributions"):
        fec_a_where = "WHERE 1=1" + fec_cycle_clause
        stats['federal_contributions'] = int(
            _scalar(
                conn,
                f"SELECT COUNT(*) AS count FROM fec_schedule_a_contributions {fec_a_where}",
                params=tuple(fec_cycle_params),
                default=0,
            )
        )
    if _table_exists(conn, "fec_schedule_b_disbursements"):
        fec_b_where = "WHERE 1=1" + fec_cycle_clause
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(disbursement_amount), 0) AS total
            FROM fec_schedule_b_disbursements {fec_b_where}
            """,
            tuple(fec_cycle_params),
        ).fetchone()
        if row:
            stats['federal_disbursements'] = int(row["count"] or 0)
            stats['federal_disbursement_total'] = float(row["total"] or 0.0)
    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        fec_e_where = "WHERE 1=1" + fec_cycle_clause
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(expenditure_amount), 0) AS total
            FROM fec_schedule_e_independent_expenditures {fec_e_where}
            """,
            tuple(fec_cycle_params),
        ).fetchone()
        if row:
            stats['federal_independent_expenditures'] = int(row["count"] or 0)
            stats['federal_independent_expenditure_total'] = float(row["total"] or 0.0)
    if _table_exists(conn, "fec_candidate_cycle_totals"):
        totals_where = "WHERE 1=1" + fec_cycle_clause
        totals_rows = int(
            _scalar(
                conn,
                f"SELECT COUNT(*) AS count FROM fec_candidate_cycle_totals {totals_where}",
                params=tuple(fec_cycle_params),
                default=0,
            )
        )
        if totals_rows > 0:
            stats['federal_total_amount'] = float(
                _scalar(
                    conn,
                    f"SELECT COALESCE(SUM(receipts), 0) AS total FROM fec_candidate_cycle_totals {totals_where}",
                    params=tuple(fec_cycle_params),
                    default=0.0,
                )
            )
        elif _table_exists(conn, "fec_schedule_a_contributions"):
            stats['federal_total_amount'] = float(
                _scalar(
                    conn,
                    f"SELECT COALESCE(SUM(contribution_receipt_amount), 0) AS total FROM fec_schedule_a_contributions {fec_a_where}",
                    params=tuple(fec_cycle_params),
                    default=0.0,
                )
            )
    elif _table_exists(conn, "fec_schedule_a_contributions"):
        stats['federal_total_amount'] = float(
            _scalar(
                conn,
                f"SELECT COALESCE(SUM(contribution_receipt_amount), 0) AS total FROM fec_schedule_a_contributions {fec_a_where}",
                params=tuple(fec_cycle_params),
                default=0.0,
            )
        )

    if _table_exists(conn, "fec_local_donor_matches"):
        stats['federal_matched_donors'] = int(
            _scalar(
                conn,
                """
                SELECT COUNT(DISTINCT federal_donor_entity_key || '|' || local_donor_key) AS count
                FROM fec_local_donor_matches
                """,
                default=0,
            )
        )
    if _table_exists(conn, "openbook_vendor_match"):
        stats['openbook_vendors_matched'] = int(
            _scalar(
                conn,
                """
                SELECT COUNT(DISTINCT openbook_vendor_key) AS count
                FROM openbook_vendor_match
                WHERE match_method != 'no_match' AND openbook_vendor_key != ''
                """,
                default=0,
            )
        )
    if _table_exists(conn, "openbook_contracts_raw"):
        stats['openbook_contracts_total'] = int(
            _scalar(conn, "SELECT COUNT(*) AS count FROM openbook_contracts_raw", default=0)
        )
        stats['openbook_award_total'] = float(
            _scalar(
                conn,
                "SELECT COALESCE(SUM(award_amount), 0) AS total FROM openbook_contracts_raw",
                default=0,
            )
        )

    freshness = {
        'local_receipt_date': None,
        'federal_receipt_date': None,
        'federal_disbursement_date': None,
        'federal_independent_expenditure_date': None,
        'federal_sync_updated_at': None,
    }
    if _table_exists(conn, "bulk_receipts_clean"):
        freshness['local_receipt_date'] = _scalar(
            conn,
            "SELECT MAX(received_date) AS max_date FROM bulk_receipts_clean WHERE COALESCE(is_archived, 0) = 0",
            default=None,
        )
    if _table_exists(conn, "fec_schedule_a_contributions"):
        freshness['federal_receipt_date'] = _scalar(
            conn,
            "SELECT MAX(contribution_receipt_date) AS max_date FROM fec_schedule_a_contributions",
            default=None,
        )
    if _table_exists(conn, "fec_schedule_b_disbursements"):
        freshness['federal_disbursement_date'] = _scalar(
            conn,
            "SELECT MAX(disbursement_date) AS max_date FROM fec_schedule_b_disbursements",
            default=None,
        )
    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        freshness['federal_independent_expenditure_date'] = _scalar(
            conn,
            "SELECT MAX(expenditure_date) AS max_date FROM fec_schedule_e_independent_expenditures",
            default=None,
        )
    if _table_exists(conn, "fec_candidate_cycle_totals"):
        freshness['federal_sync_updated_at'] = _scalar(
            conn,
            "SELECT MAX(updated_at) AS max_updated_at FROM fec_candidate_cycle_totals",
            default=None,
        )
    if not freshness['federal_sync_updated_at'] and _table_exists(conn, "raw_extractions"):
        freshness['federal_sync_updated_at'] = _scalar(
            conn,
            """
            SELECT MAX(updated_at) AS max_updated_at
            FROM raw_extractions
            WHERE source_type = 'fec_api:schedules_schedule_a'
               OR source_type = 'fec_api:schedules_schedule_b'
               OR source_type = 'fec_api:schedules_schedule_e'
               OR source_type LIKE 'fec_api:candidate_%_totals'
            """,
            default=None,
        )
    if not freshness['federal_sync_updated_at'] and _table_exists(conn, "fec_schedule_a_contributions"):
        freshness['federal_sync_updated_at'] = _scalar(
            conn,
            "SELECT MAX(updated_at) AS max_updated_at FROM fec_schedule_a_contributions",
            default=None,
        )

    return stats, freshness


def _get_candidate_stats_cached(conn, period=None):
    cache_key = _period_cache_token(period)
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    if not cache_enabled:
        return _get_candidate_stats(conn, period=period)

    ttl_seconds = max(15, int(current_app.config.get("DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS", 180)))
    now = time.monotonic()
    with _candidate_stats_cache_lock:
        if (
            _candidate_stats_cache.get("value") is not None
            and _candidate_stats_cache.get("key") == cache_key
            and float(_candidate_stats_cache.get("expires_at", 0.0)) > now
        ):
            return _candidate_stats_cache["value"]

    value = _get_candidate_stats(conn, period=period)
    with _candidate_stats_cache_lock:
        _candidate_stats_cache["value"] = value
        _candidate_stats_cache["expires_at"] = now + float(ttl_seconds)
        _candidate_stats_cache["key"] = cache_key
    return value


def _get_top_donors_cached(conn, period=None):
    from webapp.utils.time_filter import period_qmark_clause

    cache_key = _period_cache_token(period)

    def _query_top_donors():
        if period and period.get("key") != "all" and _table_exists(conn, "bulk_receipts_clean"):
            td_clause, td_params = period_qmark_clause("received_date", period)
            base_filter = _bulk_receipts_base_filter(conn, "r")
            return conn.execute(
                f"""
                SELECT
                    {_bulk_donor_name_sql('r')} AS name,
                    {_bulk_donor_key_sql('r')} AS donor_key,
                    'bulk_receipts' AS source,
                    COALESCE(SUM(r.amount), 0) AS total_amount,
                    COUNT(*) AS contribution_count,
                    NULL AS entity_id,
                    NULL AS id
                FROM bulk_receipts_clean r
                WHERE {base_filter}{td_clause}
                GROUP BY name, donor_key
                ORDER BY total_amount DESC
                LIMIT 8
                """,
                tuple(td_params),
            ).fetchall()

        if _table_exists(conn, "analytics_donor_summary"):
            return conn.execute(
                """
                SELECT
                    donor_name AS name,
                    donor_key,
                    source,
                    total_amount,
                    contribution_count,
                    NULL AS entity_id,
                    NULL AS id
                FROM analytics_donor_summary
                WHERE source = 'bulk_receipts'
                ORDER BY total_amount DESC
                LIMIT 8
                """
            ).fetchall()
        return Donor.get_all_with_totals(conn, limit=8, sort_by='total_amount')

    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    if not cache_enabled:
        return _query_top_donors()

    ttl_seconds = max(15, int(current_app.config.get("DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS", 180)))
    now = time.monotonic()
    with _top_donors_cache_lock:
        if (
            _top_donors_cache.get("value") is not None
            and _top_donors_cache.get("key") == cache_key
            and float(_top_donors_cache.get("expires_at", 0.0)) > now
        ):
            return _top_donors_cache["value"]

    value = _query_top_donors()
    with _top_donors_cache_lock:
        _top_donors_cache["value"] = value
        _top_donors_cache["expires_at"] = now + float(ttl_seconds)
        _top_donors_cache["key"] = cache_key
    return value


def _normalize_month_key(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) >= 7 and text[4] == "-" and text[5:7].isdigit():
        return text[:7]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%Y-%m")
        except ValueError:
            continue
    return None


def _bulk_receipts_base_filter(conn, alias: str = "r") -> str:
    clauses = [f"COALESCE({alias}.amount, 0) > 0"]
    if _column_exists(conn, "bulk_receipts_clean", "is_archived"):
        clauses.append(f"COALESCE({alias}.is_archived, 0) = 0")
    if _column_exists(conn, "bulk_receipts_clean", "d2_part_code"):
        clauses.append(
            f"(COALESCE({alias}.d2_part_code, '') = '' OR COALESCE({alias}.d2_part_code, '') LIKE '1%')"
        )
    return " AND ".join(clauses)


def _bulk_donor_key_sql(alias: str = "r") -> str:
    return (
        f"LOWER(TRIM("
        f"COALESCE({alias}.first_name, '') || '|' || COALESCE({alias}.last_or_business_name, '') || '|' || "
        f"COALESCE({alias}.address_line_1, '') || '|' || COALESCE({alias}.address_line_2, '') || '|' || "
        f"COALESCE({alias}.city, '') || '|' || COALESCE({alias}.state, '') || '|' || COALESCE({alias}.postal_code, '')"
        f"))"
    )


def _bulk_donor_name_sql(alias: str = "r") -> str:
    return (
        "TRIM("
        f"COALESCE({alias}.first_name, '')"
        " || CASE "
        f"WHEN COALESCE({alias}.first_name, '') <> '' AND COALESCE({alias}.last_or_business_name, '') <> '' "
        "THEN ' ' ELSE '' END"
        f" || COALESCE({alias}.last_or_business_name, '')"
        ")"
    )


def _search_local_candidates(conn, query: str, limit: int = 30) -> list[dict]:
    output = []
    found_ids = set()

    # Primary: search via agg table (candidates with committees + financials)
    if _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        has_office = _column_exists(conn, "bulk_candidate_committee_finance_agg", "office_sought")
        has_period_end = _column_exists(conn, "bulk_candidate_committee_finance_agg", "period_end_date")
        office_expr = "MAX(COALESCE(office_sought, '')) AS office_sought" if has_office else "NULL AS office_sought"
        latest_expr = "MAX(period_end_date) AS latest_period_end" if has_period_end else "NULL AS latest_period_end"

        rows = conn.execute(
            f"""
            SELECT
                candidate_id,
                candidate_full_name,
                {office_expr},
                COUNT(DISTINCT committee_id_sbe) AS committee_count,
                COALESCE(SUM(CAST(NULLIF(TRIM(CAST(sum_total_receipts AS TEXT)), '') AS REAL)), 0) AS total_receipts,
                {latest_expr}
            FROM bulk_candidate_committee_finance_agg
            WHERE
                COALESCE(candidate_full_name, '') LIKE ?
                OR COALESCE(CAST(candidate_id AS TEXT), '') LIKE ?
            GROUP BY candidate_id, candidate_full_name
            ORDER BY total_receipts DESC, candidate_full_name ASC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()

        for row in rows:
            candidate_name = row["candidate_full_name"] or f"Candidate {row['candidate_id']}"
            found_ids.add(row["candidate_id"])
            output.append(
                {
                    "scope": "local",
                    "candidate_id": row["candidate_id"],
                    "candidate_name": candidate_name,
                    "office": row["office_sought"] or "",
                    "committee_count": int(row["committee_count"] or 0),
                    "total_receipts": float(row["total_receipts"] or 0.0),
                    "latest_period_end": row["latest_period_end"],
                    "provenance": {
                        "source_table": "bulk_candidate_committee_finance_agg",
                        "source_filing_link": None,
                        "import_batch": "ISBE bulk import aggregate",
                        "sync_timestamp": row["latest_period_end"] or "not available",
                        "normalization_notes": "Candidate names are deduped by candidate_id in aggregate rows.",
                    },
                }
            )

    # Fallback: search bulk_candidates_clean for candidates without committees
    if len(output) < limit and _table_exists(conn, "bulk_candidates_clean"):
        remaining = limit - len(output)
        fallback_rows = conn.execute(
            """
            SELECT
                candidate_id,
                candidate_full_name,
                office_sought
            FROM bulk_candidates_clean
            WHERE
                COALESCE(candidate_full_name, '') LIKE ?
                OR COALESCE(CAST(candidate_id AS TEXT), '') LIKE ?
            ORDER BY candidate_full_name ASC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", remaining + len(found_ids)),
        ).fetchall()

        for row in fallback_rows:
            if row["candidate_id"] in found_ids:
                continue
            if len(output) >= limit:
                break
            candidate_name = row["candidate_full_name"] or f"Candidate {row['candidate_id']}"
            output.append(
                {
                    "scope": "local",
                    "candidate_id": row["candidate_id"],
                    "candidate_name": candidate_name,
                    "office": row["office_sought"] or "",
                    "committee_count": 0,
                    "total_receipts": 0.0,
                    "latest_period_end": None,
                    "provenance": {
                        "source_table": "bulk_candidates_clean",
                        "source_filing_link": None,
                        "import_batch": "ISBE sunshine import",
                        "sync_timestamp": "not available",
                        "normalization_notes": "Candidate without committee link; no financial data available.",
                    },
                }
            )

    return output


def _search_federal_candidates(conn, query: str, limit: int = 20) -> list[dict]:
    if not _table_exists(conn, "fec_candidate_match"):
        return []
    rows = conn.execute(
        """
        SELECT
            candidate_name,
            fec_candidate_id,
            fec_name,
            office,
            district,
            party,
            cycle,
            match_status,
            updated_at
        FROM fec_candidate_match
        WHERE
            COALESCE(candidate_name, '') LIKE ?
            OR COALESCE(fec_name, '') LIKE ?
            OR COALESCE(fec_candidate_id, '') LIKE ?
        ORDER BY COALESCE(cycle, 0) DESC, updated_at DESC
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", f"%{query}%", limit),
    ).fetchall()

    output = []
    for row in rows:
        output.append(
            {
                "scope": "federal",
                "candidate_id": row["fec_candidate_id"],
                "candidate_name": row["candidate_name"] or row["fec_name"] or "Unknown Candidate",
                "office": row["office"] or "",
                "district": row["district"] or "",
                "party": row["party"] or "",
                "cycle": row["cycle"],
                "match_status": row["match_status"],
                "fec_name": row["fec_name"],
                "provenance": {
                    "source_table": "fec_candidate_match",
                    "source_filing_link": None,
                    "import_batch": "FEC candidate sync",
                    "sync_timestamp": row["updated_at"] or "not available",
                    "normalization_notes": "Matched to FEC candidates using deterministic and fuzzy name alignment.",
                },
            }
        )
    return output


def _search_reports(
    conn,
    query: str,
    limit: int = 30,
    filed_date_from: str | None = None,
    filed_date_to: str | None = None,
) -> list[dict]:
    if not _table_exists(conn, "reports"):
        return []
    where_clauses = [
        """
        (
            c.name LIKE ?
            OR COALESCE(r.report_type, '') LIKE ?
            OR COALESCE(r.reporting_period, '') LIKE ?
            OR COALESCE(r.filed_date, '') LIKE ?
            OR CAST(r.id AS TEXT) LIKE ?
        )
        """
    ]
    params: list[object] = [f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"]
    _apply_date_window_clauses(
        where_clauses,
        params,
        _sql_date_expr("r.filed_date"),
        date_from=filed_date_from,
        date_to=filed_date_to,
    )
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT
            r.id,
            r.report_type,
            r.reporting_period,
            r.filed_date,
            r.detail_url,
            r.source_identifier,
            r.updated_at,
            c.name AS committee_name
        FROM reports r
        JOIN committees c ON c.id = r.committee_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY r.updated_at DESC, r.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    output = []
    for row in rows:
        output.append(
            {
                "id": row["id"],
                "committee_name": row["committee_name"],
                "report_type": row["report_type"],
                "reporting_period": row["reporting_period"],
                "filed_date": row["filed_date"],
                "detail_url": row["detail_url"],
                "provenance": {
                    "source_table": "reports",
                    "source_filing_link": row["detail_url"],
                    "import_batch": "legacy scrape report ingest",
                    "sync_timestamp": row["updated_at"] or "not available",
                    "normalization_notes": (
                        f"source_identifier={row['source_identifier']}" if row["source_identifier"] else "none"
                    ),
                },
            }
        )
    return output


def _search_filed_docs(
    conn,
    query: str,
    limit: int = 30,
    filed_date_from: str | None = None,
    filed_date_to: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
    query_text = (query or "").strip()
    is_numeric_query = _is_filed_doc_query(query_text)
    has_date_window = bool((filed_date_from or "").strip() or (filed_date_to or "").strip())
    has_filed_docs = _table_exists(conn, "isbe_filed_docs")
    filed_doc_date_expr = None
    if has_filed_docs:
        if _column_exists(conn, "isbe_filed_docs", "received_datetime"):
            filed_doc_date_expr = _sql_date_expr("fd.received_datetime")
        elif _column_exists(conn, "isbe_filed_docs", "filed_date"):
            filed_doc_date_expr = _sql_date_expr("fd.filed_date")
        elif _column_exists(conn, "isbe_filed_docs", "reporting_period_end"):
            filed_doc_date_expr = _sql_date_expr("fd.reporting_period_end")

    if _table_exists(conn, "bulk_d2_receipts_recon"):
        d2_join_sql = "JOIN isbe_filed_docs fd ON fd.id = d.filed_doc_id" if filed_doc_date_expr else ""
        if is_numeric_query:
            d2_where = ["d.filed_doc_id = ?"]
            d2_params: list[object] = [int(query_text)]
        else:
            d2_where = ["COALESCE(d.committee_name, '') LIKE ?"]
            d2_params = [f"%{query_text}%"]
        if has_date_window:
            if filed_doc_date_expr:
                _apply_date_window_clauses(
                    d2_where,
                    d2_params,
                    filed_doc_date_expr,
                    date_from=filed_date_from,
                    date_to=filed_date_to,
                )
            elif _column_exists(conn, "bulk_d2_receipts_recon", "last_receipt_date"):
                _apply_date_window_clauses(
                    d2_where,
                    d2_params,
                    _sql_date_expr("d.last_receipt_date"),
                    date_from=filed_date_from,
                    date_to=filed_date_to,
                )
        d2_params.append(limit)
        d2_rows = conn.execute(
            f"""
            SELECT
                d.filed_doc_id,
                d.committee_id_sbe,
                d.committee_name,
                ABS(COALESCE(CAST(NULLIF(TRIM(CAST(receipts_minus_d2_total AS TEXT)), '') AS REAL), 0)) AS abs_diff,
                d.first_receipt_date,
                d.last_receipt_date
            FROM bulk_d2_receipts_recon d
            {d2_join_sql}
            WHERE {' AND '.join(d2_where)}
            ORDER BY abs_diff DESC, d.filed_doc_id DESC
            LIMIT ?
            """,
            d2_params,
        ).fetchall()
        for row in d2_rows:
            rows.append(
                {
                    "filed_doc_id": row["filed_doc_id"],
                    "committee_id_sbe": row["committee_id_sbe"],
                    "committee_name": row["committee_name"],
                    "amount": None,
                    "receipt_row_count": None,
                    "first_receipt_date": row["first_receipt_date"],
                    "last_receipt_date": row["last_receipt_date"],
                    "source_table": "bulk_d2_receipts_recon",
                    "import_batch": "ISBE bulk reconciliation build",
                    "sync_timestamp": row["last_receipt_date"] or "not available",
                    "normalization_notes": "Filed doc IDs are reconciled against itemized receipts.",
                }
            )

    if _table_exists(conn, "bulk_receipts_clean") and is_numeric_query:
        receipts_join_sql = "JOIN isbe_filed_docs fd ON fd.id = r.filed_doc_id" if filed_doc_date_expr else ""
        receipts_where = [
            "r.filed_doc_id = ?",
        ]
        receipt_params: list[object] = [int(query_text)]
        if has_date_window:
            if filed_doc_date_expr:
                _apply_date_window_clauses(
                    receipts_where,
                    receipt_params,
                    filed_doc_date_expr,
                    date_from=filed_date_from,
                    date_to=filed_date_to,
                )
            else:
                _apply_date_window_clauses(
                    receipts_where,
                    receipt_params,
                    _sql_date_expr("r.received_date"),
                    date_from=filed_date_from,
                    date_to=filed_date_to,
                )
        receipt_params.append(limit)
        receipt_rows = conn.execute(
            f"""
            SELECT
                r.filed_doc_id,
                r.committee_id_sbe,
                COUNT(*) AS receipt_row_count,
                COALESCE(SUM(r.amount), 0) AS total_amount,
                MIN(r.received_date) AS first_receipt_date,
                MAX(r.received_date) AS last_receipt_date
            FROM bulk_receipts_clean r
            {receipts_join_sql}
            WHERE {' AND '.join(receipts_where)}
            GROUP BY r.filed_doc_id, r.committee_id_sbe
            ORDER BY total_amount DESC, r.filed_doc_id DESC
            LIMIT ?
            """,
            receipt_params,
        ).fetchall()
        for row in receipt_rows:
            rows.append(
                {
                    "filed_doc_id": row["filed_doc_id"],
                    "committee_id_sbe": row["committee_id_sbe"],
                    "committee_name": f"Committee {row['committee_id_sbe']}",
                    "amount": float(row["total_amount"] or 0.0),
                    "receipt_row_count": int(row["receipt_row_count"] or 0),
                    "first_receipt_date": row["first_receipt_date"],
                    "last_receipt_date": row["last_receipt_date"],
                    "source_table": "bulk_receipts_clean",
                    "import_batch": "ISBE bulk receipts import",
                    "sync_timestamp": row["last_receipt_date"] or "not available",
                    "normalization_notes": "Filed doc IDs are grouped from raw itemized receipt rows.",
                }
            )

    merged: dict[str, dict] = {}
    for row in rows:
        key = str(row["filed_doc_id"])
        existing = merged.get(key)
        if not existing:
            merged[key] = row
            continue
        if row["source_table"] == "bulk_d2_receipts_recon":
            existing["committee_name"] = row["committee_name"] or existing.get("committee_name")
        if row.get("amount") is not None:
            existing["amount"] = row["amount"]
        if row.get("receipt_row_count") is not None:
            existing["receipt_row_count"] = row["receipt_row_count"]
        existing["first_receipt_date"] = existing.get("first_receipt_date") or row.get("first_receipt_date")
        existing["last_receipt_date"] = row.get("last_receipt_date") or existing.get("last_receipt_date")
        existing["normalization_notes"] = row["normalization_notes"]

    output = list(merged.values())
    output.sort(
        key=lambda row: (
            float(row["amount"] or 0.0),
            int(row["receipt_row_count"] or 0),
            int(row["filed_doc_id"] or 0),
        ),
        reverse=True,
    )
    return output[:limit]


def _search_donor_keys(
    conn,
    query: str,
    limit: int = 30,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    has_date_window = bool((date_from or "").strip() or (date_to or "").strip())
    if (
        has_date_window
        and _table_exists(conn, "bulk_receipts_clean")
        and _column_exists(conn, "bulk_receipts_clean", "first_name")
        and _column_exists(conn, "bulk_receipts_clean", "last_or_business_name")
        and _column_exists(conn, "bulk_receipts_clean", "address_line_1")
        and _column_exists(conn, "bulk_receipts_clean", "address_line_2")
        and _column_exists(conn, "bulk_receipts_clean", "city")
        and _column_exists(conn, "bulk_receipts_clean", "state")
        and _column_exists(conn, "bulk_receipts_clean", "postal_code")
    ):
        donor_key_expr = _bulk_donor_key_sql("r")
        donor_name_expr = _bulk_donor_name_sql("r")
        where_clauses = [
            _bulk_receipts_base_filter(conn, "r"),
            f"{donor_key_expr} <> ''",
            f"({donor_key_expr} LIKE ? OR {donor_name_expr} LIKE ?)",
        ]
        params: list[object] = [f"%{query}%", f"%{query}%"]
        _apply_date_window_clauses(
            where_clauses,
            params,
            _sql_date_expr("r.received_date"),
            date_from=date_from,
            date_to=date_to,
        )
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT
                'bulk_receipts' AS source,
                {donor_key_expr} AS donor_key,
                {donor_name_expr} AS donor_name,
                COALESCE(NULLIF(TRIM(r.city), ''), NULL) AS donor_city,
                COALESCE(NULLIF(TRIM(r.state), ''), NULL) AS donor_state,
                COALESCE(SUM(r.amount), 0) AS total_amount,
                COUNT(*) AS contribution_count,
                COUNT(DISTINCT r.committee_id_sbe) AS committee_count,
                MAX(r.received_date) AS updated_at
            FROM bulk_receipts_clean r
            WHERE {' AND '.join(where_clauses)}
            GROUP BY donor_key, donor_name, donor_city, donor_state
            ORDER BY total_amount DESC, donor_name ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    else:
        if not _table_exists(conn, "analytics_donor_summary"):
            return []
        rows = []
        seen_keys: set[tuple[str, str]] = set()
        query_text = (query or "").strip()
        prefix_pattern = f"{query_text}%"
        contains_pattern = f"%{query_text}%"
        key_like_query = _is_donor_key_query(query_text)

        def _fetch_from_source(source_name: str, pattern: str):
            return conn.execute(
                """
                SELECT
                    source,
                    donor_key,
                    donor_name,
                    donor_city,
                    donor_state,
                    total_amount,
                    contribution_count,
                    committee_count,
                    updated_at
                FROM analytics_donor_summary
                WHERE source = ?
                  AND (donor_key LIKE ? OR donor_name LIKE ?)
                ORDER BY total_amount DESC, donor_name ASC
                LIMIT ?
                """,
                (source_name, pattern, pattern, limit),
            ).fetchall()

        def _append_rows(batch_rows):
            for row in batch_rows:
                row_key = (str(row["source"] or ""), str(row["donor_key"] or ""))
                if row_key in seen_keys:
                    continue
                seen_keys.add(row_key)
                rows.append(row)
                if len(rows) >= limit:
                    return

        _append_rows(_fetch_from_source("bulk_receipts", prefix_pattern))
        if len(rows) < limit:
            _append_rows(_fetch_from_source("contributions", prefix_pattern))

        if key_like_query and len(rows) < limit:
            _append_rows(_fetch_from_source("bulk_receipts", contains_pattern))
            if len(rows) < limit:
                _append_rows(_fetch_from_source("contributions", contains_pattern))

    output = []
    for row in rows:
        output.append(
            {
                "source": row["source"],
                "donor_key": row["donor_key"],
                "donor_name": row["donor_name"] or "Unknown Donor",
                "donor_city": row["donor_city"],
                "donor_state": row["donor_state"],
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
                "committee_count": int(row["committee_count"] or 0),
                "provenance": {
                    "source_table": "analytics_donor_summary",
                    "source_filing_link": None,
                    "import_batch": f"{row['source']} materialized analytics refresh",
                    "sync_timestamp": row["updated_at"] or "not available",
                    "normalization_notes": "donor_key is a stable normalized donor identity key.",
                },
            }
        )
    return output


def _list_compare_candidates(conn, limit: int = 250) -> list[dict]:
    if not _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        return []
    rows = conn.execute(
        """
        SELECT
            candidate_id,
            candidate_full_name,
            MAX(COALESCE(office_sought, '')) AS office_sought,
            COALESCE(SUM(sum_total_receipts), 0) AS total_receipts
        FROM bulk_candidate_committee_finance_agg
        WHERE candidate_id IS NOT NULL
        GROUP BY candidate_id, candidate_full_name
        ORDER BY total_receipts DESC, candidate_full_name ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    output = []
    for row in rows:
        output.append(
            {
                "value": str(row["candidate_id"]),
                "label": row["candidate_full_name"] or f"Candidate {row['candidate_id']}",
                "meta": row["office_sought"] or "",
                "source": "bulk",
            }
        )
    return output


def _list_compare_committees(conn, limit: int = 250) -> list[dict]:
    options: list[dict] = []
    if _table_exists(conn, "bulk_receipts_clean"):
        name_expr = (
            "COALESCE(c.committee_name, 'Committee ' || r.committee_id_sbe)"
            if _table_exists(conn, "bulk_committees_clean")
            else "'Committee ' || r.committee_id_sbe"
        )
        join_sql = (
            "LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe"
            if _table_exists(conn, "bulk_committees_clean")
            else ""
        )
        rows = conn.execute(
            f"""
            SELECT
                r.committee_id_sbe AS committee_id,
                {name_expr} AS committee_name,
                COALESCE(SUM(r.amount), 0) AS total_amount
            FROM bulk_receipts_clean r
            {join_sql}
            WHERE {_bulk_receipts_base_filter(conn, alias='r')}
              AND r.committee_id_sbe IS NOT NULL
            GROUP BY r.committee_id_sbe, committee_name
            ORDER BY total_amount DESC, committee_name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            options.append(
                {
                    "value": f"bulk:{row['committee_id']}",
                    "label": row["committee_name"] or f"Committee {row['committee_id']}",
                    "meta": "Bulk receipts",
                    "source": "bulk",
                }
            )

    if _table_exists(conn, "committees"):
        rows = conn.execute(
            """
            SELECT id, name
            FROM committees
            ORDER BY name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            options.append(
                {
                    "value": f"legacy:{row['id']}",
                    "label": row["name"] or f"Committee {row['id']}",
                    "meta": "Legacy contributions",
                    "source": "legacy",
                }
            )
    return options


def _load_candidate_compare_profile(conn, candidate_id: int) -> dict | None:
    if not _table_exists(conn, "bulk_committee_candidate_links") or not _table_exists(conn, "bulk_receipts_clean"):
        return None

    candidate_row = None
    if _table_exists(conn, "bulk_candidates_clean"):
        candidate_row = conn.execute(
            """
            SELECT
                candidate_id,
                candidate_full_name,
                office_sought,
                district_type,
                district
            FROM bulk_candidates_clean
            WHERE candidate_id = ?
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
    if candidate_row is None and _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        candidate_row = conn.execute(
            """
            SELECT
                candidate_id,
                candidate_full_name,
                MAX(COALESCE(office_sought, '')) AS office_sought,
                MAX(COALESCE(district_type, '')) AS district_type,
                MAX(COALESCE(district, '')) AS district
            FROM bulk_candidate_committee_finance_agg
            WHERE candidate_id = ?
            GROUP BY candidate_id, candidate_full_name
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
    if candidate_row is None:
        return None

    donor_key_sql = _bulk_donor_key_sql(alias="r")
    donor_name_sql = _bulk_donor_name_sql(alias="r")
    filter_sql = _bulk_receipts_base_filter(conn, alias="r")

    trend_rows = conn.execute(
        f"""
        SELECT
            SUBSTR(r.received_date, 1, 7) AS month_key,
            COALESCE(SUM(r.amount), 0) AS total_amount,
            COUNT(*) AS contribution_count
        FROM bulk_receipts_clean r
        WHERE r.committee_id_sbe IN (
            SELECT committee_id_sbe
            FROM bulk_committee_candidate_links
            WHERE candidate_id = ?
        )
          AND {filter_sql}
          AND r.received_date IS NOT NULL
          AND LENGTH(r.received_date) >= 7
        GROUP BY month_key
        ORDER BY month_key
        """,
        (candidate_id,),
    ).fetchall()

    donor_rows = conn.execute(
        f"""
        SELECT
            {donor_key_sql} AS donor_key,
            MAX({donor_name_sql}) AS donor_name,
            COALESCE(SUM(r.amount), 0) AS total_amount,
            COUNT(*) AS contribution_count
        FROM bulk_receipts_clean r
        WHERE r.committee_id_sbe IN (
            SELECT committee_id_sbe
            FROM bulk_committee_candidate_links
            WHERE candidate_id = ?
        )
          AND {filter_sql}
        GROUP BY donor_key
        ORDER BY total_amount DESC
        """,
        (candidate_id,),
    ).fetchall()

    committees_count = int(
        _scalar(
            conn,
            """
            SELECT COUNT(DISTINCT committee_id_sbe) AS count
            FROM bulk_committee_candidate_links
            WHERE candidate_id = ?
            """,
            (candidate_id,),
            default=0,
        )
    )

    trend = [
        {
            "month": row["month_key"],
            "total_amount": float(row["total_amount"] or 0.0),
            "contribution_count": int(row["contribution_count"] or 0),
        }
        for row in trend_rows
        if row["month_key"]
    ]
    donors = [
        {
            "donor_key": row["donor_key"],
            "donor_name": row["donor_name"] or "Unknown Donor",
            "total_amount": float(row["total_amount"] or 0.0),
            "contribution_count": int(row["contribution_count"] or 0),
        }
        for row in donor_rows
    ]
    total_amount = sum(row["total_amount"] for row in donors)
    total_contributions = sum(row["contribution_count"] for row in donors)

    # Election history from candidacies
    candidacies = []
    if _table_exists(conn, "isbe_candidacies"):
        candidacy_rows = conn.execute(
            """
            SELECT election_type, election_year, race_type, outcome
            FROM isbe_candidacies
            WHERE candidate_id = ?
            ORDER BY election_year DESC, election_type
            """,
            (candidate_id,),
        ).fetchall()
        candidacies = [
            {
                "election_type": row["election_type"] or "",
                "election_year": row["election_year"],
                "race_type": row["race_type"] or "",
                "outcome": row["outcome"] or "",
            }
            for row in candidacy_rows
        ]

    return {
        "entity_type": "candidate",
        "entity_key": str(candidate_id),
        "label": candidate_row["candidate_full_name"] or f"Candidate {candidate_id}",
        "meta": " ".join(
            part for part in [candidate_row["office_sought"], candidate_row["district_type"], candidate_row["district"]] if part
        ).strip(),
        "total_amount": round(total_amount, 2),
        "contribution_count": total_contributions,
        "counterparty_count": committees_count,
        "donor_count": len(donors),
        "candidacies": candidacies,
        "trend": trend,
        "donors": donors,
        "top_donors": donors[:12],
        "provenance": {
            "source_table": "bulk_receipts_clean + bulk_committee_candidate_links",
            "source_filing_link": None,
            "import_batch": "ISBE bulk import",
            "sync_timestamp": max([row["month"] for row in trend], default="not available"),
            "normalization_notes": "Donors grouped by normalized name/address token key.",
        },
    }


def _load_committee_compare_profile(conn, selection_value: str) -> dict | None:
    if ":" not in selection_value:
        return None
    source, entity_id_text = selection_value.split(":", 1)
    if source not in {"bulk", "legacy"}:
        return None
    try:
        entity_id = int(entity_id_text)
    except ValueError:
        return None

    if source == "bulk":
        if not _table_exists(conn, "bulk_receipts_clean"):
            return None
        label_row = None
        if _table_exists(conn, "bulk_committees_clean"):
            label_row = conn.execute(
                """
                SELECT committee_name
                FROM bulk_committees_clean
                WHERE committee_id_sbe = ?
                LIMIT 1
                """,
                (entity_id,),
            ).fetchone()
        label = label_row["committee_name"] if label_row and label_row["committee_name"] else f"Committee {entity_id}"

        donor_key_sql = _bulk_donor_key_sql(alias="r")
        donor_name_sql = _bulk_donor_name_sql(alias="r")
        filter_sql = _bulk_receipts_base_filter(conn, alias="r")

        trend_rows = conn.execute(
            f"""
            SELECT
                SUBSTR(r.received_date, 1, 7) AS month_key,
                COALESCE(SUM(r.amount), 0) AS total_amount,
                COUNT(*) AS contribution_count
            FROM bulk_receipts_clean r
            WHERE r.committee_id_sbe = ?
              AND {filter_sql}
              AND r.received_date IS NOT NULL
              AND LENGTH(r.received_date) >= 7
            GROUP BY month_key
            ORDER BY month_key
            """,
            (entity_id,),
        ).fetchall()

        donor_rows = conn.execute(
            f"""
            SELECT
                {donor_key_sql} AS donor_key,
                MAX({donor_name_sql}) AS donor_name,
                COALESCE(SUM(r.amount), 0) AS total_amount,
                COUNT(*) AS contribution_count
            FROM bulk_receipts_clean r
            WHERE r.committee_id_sbe = ?
              AND {filter_sql}
            GROUP BY donor_key
            ORDER BY total_amount DESC
            """,
            (entity_id,),
        ).fetchall()

        trend = [
            {
                "month": row["month_key"],
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
            }
            for row in trend_rows
            if row["month_key"]
        ]
        donors = [
            {
                "donor_key": row["donor_key"],
                "donor_name": row["donor_name"] or "Unknown Donor",
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
            }
            for row in donor_rows
        ]
        return {
            "entity_type": "committee",
            "entity_key": f"bulk:{entity_id}",
            "label": label,
            "meta": f"SBE Committee ID {entity_id}",
            "total_amount": round(sum(row["total_amount"] for row in donors), 2),
            "contribution_count": int(sum(row["contribution_count"] for row in donors)),
            "counterparty_count": len(donors),
            "donor_count": len(donors),
            "trend": trend,
            "donors": donors,
            "top_donors": donors[:12],
            "provenance": {
                "source_table": "bulk_receipts_clean",
                "source_filing_link": None,
                "import_batch": "ISBE bulk receipts import",
                "sync_timestamp": max([row["month"] for row in trend], default="not available"),
                "normalization_notes": "Donors grouped by normalized name/address token key.",
            },
        }

    if not _table_exists(conn, "committees"):
        return None

    committee_row = conn.execute(
        """
        SELECT id, name, detail_url, source_identifier, updated_at
        FROM committees
        WHERE id = ?
        LIMIT 1
        """,
        (entity_id,),
    ).fetchone()
    if committee_row is None:
        return None

    contribution_rows = conn.execute(
        """
        SELECT
            ct.amount,
            ct.transaction_date,
            r.filed_date,
            d.id AS donor_id,
            d.name AS donor_name
        FROM contributions ct
        JOIN reports r ON r.id = ct.report_id
        JOIN donors d ON d.id = ct.donor_id
        WHERE r.committee_id = ?
          AND COALESCE(ct.amount, 0) > 0
        """,
        (entity_id,),
    ).fetchall()

    trend_map: dict[str, dict] = {}
    donor_map: dict[str, dict] = {}
    for row in contribution_rows:
        month_key = _normalize_month_key(row["transaction_date"]) or _normalize_month_key(row["filed_date"])
        if month_key:
            bucket = trend_map.setdefault(
                month_key,
                {"month": month_key, "total_amount": 0.0, "contribution_count": 0},
            )
            bucket["total_amount"] += float(row["amount"] or 0.0)
            bucket["contribution_count"] += 1

        donor_key = f"legacy-donor:{row['donor_id']}"
        donor_bucket = donor_map.setdefault(
            donor_key,
            {
                "donor_key": donor_key,
                "donor_name": row["donor_name"] or "Unknown Donor",
                "total_amount": 0.0,
                "contribution_count": 0,
            },
        )
        donor_bucket["total_amount"] += float(row["amount"] or 0.0)
        donor_bucket["contribution_count"] += 1

    trend = [trend_map[key] for key in sorted(trend_map.keys())]
    donors = sorted(donor_map.values(), key=lambda row: row["total_amount"], reverse=True)

    return {
        "entity_type": "committee",
        "entity_key": f"legacy:{entity_id}",
        "label": committee_row["name"] or f"Committee {entity_id}",
        "meta": f"Legacy Committee ID {entity_id}",
        "total_amount": round(sum(row["total_amount"] for row in donors), 2),
        "contribution_count": int(sum(row["contribution_count"] for row in donors)),
        "counterparty_count": len(donors),
        "donor_count": len(donors),
        "trend": trend,
        "donors": donors,
        "top_donors": donors[:12],
        "provenance": {
            "source_table": "reports + contributions",
            "source_filing_link": committee_row["detail_url"],
            "import_batch": "legacy scrape + manual entry",
            "sync_timestamp": committee_row["updated_at"] or "not available",
            "normalization_notes": (
                f"source_identifier={committee_row['source_identifier']}" if committee_row["source_identifier"] else "none"
            ),
        },
    }


def _build_overlap(left_profile: dict | None, right_profile: dict | None) -> dict:
    if not left_profile or not right_profile:
        return {
            "donor_overlap_count": 0,
            "shared_amount": 0.0,
            "rows": [],
            "month_rows": [],
        }

    left_donors = {row["donor_key"]: row for row in left_profile.get("donors", [])}
    right_donors = {row["donor_key"]: row for row in right_profile.get("donors", [])}
    overlap_keys = sorted(set(left_donors.keys()) & set(right_donors.keys()))

    rows = []
    shared_amount = 0.0
    for donor_key in overlap_keys:
        left_row = left_donors[donor_key]
        right_row = right_donors[donor_key]
        shared = min(float(left_row["total_amount"]), float(right_row["total_amount"]))
        shared_amount += shared
        rows.append(
            {
                "donor_key": donor_key,
                "donor_name": left_row["donor_name"] or right_row["donor_name"],
                "left_amount": float(left_row["total_amount"]),
                "right_amount": float(right_row["total_amount"]),
                "shared_amount": shared,
            }
        )
    rows.sort(key=lambda row: row["shared_amount"], reverse=True)

    trend_by_month: dict[str, dict] = defaultdict(
        lambda: {"month": "", "left_total": 0.0, "right_total": 0.0}
    )
    for row in left_profile.get("trend", []):
        month = row["month"]
        trend_by_month[month]["month"] = month
        trend_by_month[month]["left_total"] = float(row["total_amount"] or 0.0)
    for row in right_profile.get("trend", []):
        month = row["month"]
        trend_by_month[month]["month"] = month
        trend_by_month[month]["right_total"] = float(row["total_amount"] or 0.0)

    month_rows = [trend_by_month[key] for key in sorted(trend_by_month.keys())]

    return {
        "donor_overlap_count": len(overlap_keys),
        "shared_amount": round(shared_amount, 2),
        "rows": rows[:20],
        "month_rows": month_rows,
    }


def _recent_local_candidate_donations(conn, limit: int = 75) -> list[dict]:
    required_tables = {"bulk_receipts_clean", "bulk_committee_candidate_links"}
    if not all(_table_exists(conn, table_name) for table_name in required_tables):
        return []

    has_candidates = _table_exists(conn, "bulk_candidates_clean")
    has_committees = _table_exists(conn, "bulk_committees_clean")
    has_receipt_id = _column_exists(conn, "bulk_receipts_clean", "receipt_record_id")
    has_city = _column_exists(conn, "bulk_receipts_clean", "city")
    has_state = _column_exists(conn, "bulk_receipts_clean", "state")
    has_filed_doc = _column_exists(conn, "bulk_receipts_clean", "filed_doc_id")

    receipt_id_expr = "r.receipt_record_id" if has_receipt_id else "NULL"
    donor_city_expr = "r.city" if has_city else "NULL"
    donor_state_expr = "r.state" if has_state else "NULL"
    filed_doc_expr = "r.filed_doc_id" if has_filed_doc else "NULL"
    candidate_name_expr = (
        "COALESCE(cand.candidate_full_name, 'Candidate ' || link.candidate_id)"
        if has_candidates
        else "'Candidate ' || link.candidate_id"
    )
    committee_name_expr = (
        "COALESCE(cm.committee_name, 'Committee ' || r.committee_id_sbe)"
        if has_committees
        else "'Committee ' || r.committee_id_sbe"
    )
    join_candidates = (
        "LEFT JOIN bulk_candidates_clean cand ON cand.candidate_id = link.candidate_id"
        if has_candidates
        else ""
    )
    join_committees = (
        "LEFT JOIN bulk_committees_clean cm ON cm.committee_id_sbe = r.committee_id_sbe"
        if has_committees
        else ""
    )

    rows = conn.execute(
        f"""
        SELECT
            {receipt_id_expr} AS row_id,
            r.received_date,
            link.candidate_id,
            {candidate_name_expr} AS candidate_name,
            r.committee_id_sbe,
            {committee_name_expr} AS committee_name,
            {_bulk_donor_name_sql(alias='r')} AS donor_name,
            {donor_city_expr} AS donor_city,
            {donor_state_expr} AS donor_state,
            {filed_doc_expr} AS filed_doc_id,
            COALESCE(r.amount, 0) AS amount
        FROM bulk_receipts_clean r
        JOIN bulk_committee_candidate_links link ON link.committee_id_sbe = r.committee_id_sbe
        {join_candidates}
        {join_committees}
        WHERE {_bulk_receipts_base_filter(conn, alias='r')}
        ORDER BY r.received_date DESC, row_id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()

    output = []
    for row in rows:
        output.append(
            {
                "received_date": row["received_date"],
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"],
                "committee_id_sbe": row["committee_id_sbe"],
                "committee_name": row["committee_name"],
                "donor_name": row["donor_name"] or "Unknown Donor",
                "donor_city": row["donor_city"],
                "donor_state": row["donor_state"],
                "filed_doc_id": row["filed_doc_id"],
                "amount": float(row["amount"] or 0.0),
            }
        )
    return output


def _recent_federal_candidate_donations(conn, limit: int = 75) -> list[dict]:
    if not _table_exists(conn, "fec_schedule_a_contributions"):
        return []

    rows = conn.execute(
        """
        SELECT
            sub_id,
            cycle,
            contribution_receipt_date,
            candidate_id,
            candidate_name,
            committee_name,
            contributor_name,
            contributor_city,
            contributor_state,
            contribution_receipt_amount
        FROM fec_schedule_a_contributions
        WHERE COALESCE(contribution_receipt_amount, 0) > 0
        ORDER BY contribution_receipt_date DESC, updated_at DESC, sub_id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()

    output = []
    for row in rows:
        output.append(
            {
                "contribution_receipt_date": row["contribution_receipt_date"],
                "cycle": int(row["cycle"] or 0),
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"] or row["candidate_id"] or "Unknown Candidate",
                "committee_name": row["committee_name"],
                "donor_name": row["contributor_name"] or "Unknown Donor",
                "donor_city": row["contributor_city"],
                "donor_state": row["contributor_state"],
                "amount": float(row["contribution_receipt_amount"] or 0.0),
            }
        )
    return output


def _recent_federal_schedule_b_disbursements(conn, limit: int = 75) -> list[dict]:
    if not _table_exists(conn, "fec_schedule_b_disbursements"):
        return []

    rows = conn.execute(
        """
        SELECT
            sub_id,
            cycle,
            disbursement_date,
            candidate_id,
            candidate_name,
            committee_name,
            recipient_name,
            recipient_city,
            recipient_state,
            disbursement_amount,
            category_code_full,
            disbursement_type_desc
        FROM fec_schedule_b_disbursements
        WHERE COALESCE(disbursement_amount, 0) > 0
        ORDER BY disbursement_date DESC, updated_at DESC, sub_id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()

    output = []
    for row in rows:
        output.append(
            {
                "disbursement_date": row["disbursement_date"],
                "cycle": int(row["cycle"] or 0),
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"] or row["candidate_id"] or "Unknown Candidate",
                "committee_name": row["committee_name"],
                "recipient_name": row["recipient_name"] or "Unknown Recipient",
                "recipient_city": row["recipient_city"],
                "recipient_state": row["recipient_state"],
                "amount": float(row["disbursement_amount"] or 0.0),
                "category": row["category_code_full"] or row["disbursement_type_desc"] or "-",
            }
        )
    return output


def _recent_federal_schedule_e_expenditures(conn, limit: int = 75) -> list[dict]:
    if not _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        return []

    rows = conn.execute(
        """
        SELECT
            sub_id,
            cycle,
            expenditure_date,
            candidate_id,
            candidate_name,
            committee_name,
            payee_name,
            payee_city,
            payee_state,
            support_oppose_indicator,
            expenditure_amount,
            category_code_full
        FROM fec_schedule_e_independent_expenditures
        WHERE COALESCE(expenditure_amount, 0) > 0
        ORDER BY expenditure_date DESC, updated_at DESC, sub_id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()

    output = []
    for row in rows:
        output.append(
            {
                "expenditure_date": row["expenditure_date"],
                "cycle": int(row["cycle"] or 0),
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"] or row["candidate_id"] or "Unknown Candidate",
                "committee_name": row["committee_name"],
                "payee_name": row["payee_name"] or "Unknown Payee",
                "payee_city": row["payee_city"],
                "payee_state": row["payee_state"],
                "support_oppose_indicator": row["support_oppose_indicator"] or "",
                "amount": float(row["expenditure_amount"] or 0.0),
                "category": row["category_code_full"] or "-",
            }
        )
    return output


def _latest_month_with_large_contributions(conn) -> str:
    latest = _scalar(
        conn,
        """
        SELECT SUBSTR(MAX(event_date), 1, 7) AS latest_month
        FROM analytics_large_contributions
        """,
        default="",
    )
    text = (latest or "").strip()
    if len(text) == 7 and text[4] == "-":
        return text
    return datetime.utcnow().strftime("%Y-%m")


@main_bp.route('/investigate')
def investigate_workspace():
    """Guided investigation workspace for novice users."""
    conn = current_app.get_database()
    tab = request.args.get('tab', 'investigate').strip().lower()
    if tab not in {'investigate', 'flow', 'patterns'}:
        tab = 'investigate'

    query = request.args.get('q', '').strip()
    cycle = request.args.get('cycle', '2026').strip() or '2026'
    month = request.args.get('month', '').strip()
    show_advanced = request.args.get('advanced', '').strip() == '1'
    export_format = request.args.get('format', '').strip().lower()

    like_param = f"%{query}%" if query else ""
    entity_rows: list[dict] = []
    matched_rows: list[dict] = []
    donor_committee_rows: list[dict] = []
    client_donor_rows: list[dict] = []
    unusual_rows: list[dict] = []

    if query and _table_exists(conn, "analytics_donor_summary"):
        rows = conn.execute(
            """
            SELECT donor_key, donor_name, total_amount, contribution_count, committee_count
            FROM analytics_donor_summary
            WHERE source = 'bulk_receipts' AND donor_name LIKE ?
            ORDER BY total_amount DESC
            LIMIT 8
            """,
            (like_param,),
        ).fetchall()
        for row in rows:
            entity_rows.append(
                {
                    "source_domain": "State donor",
                    "entity_name": row["donor_name"] or row["donor_key"],
                    "entity_key": row["donor_key"],
                    "total_amount": float(row["total_amount"] or 0.0),
                    "record_count": int(row["contribution_count"] or 0),
                    "extra": f"{int(row['committee_count'] or 0)} committees",
                }
            )

    if query and _table_exists(conn, "fec_schedule_a_contributions"):
        rows = conn.execute(
            """
            SELECT
                donor_entity_key,
                COALESCE(MAX(contributor_name), donor_entity_key) AS contributor_name,
                COALESCE(SUM(contribution_receipt_amount), 0) AS total_amount,
                COUNT(*) AS contribution_count
            FROM fec_schedule_a_contributions
            WHERE contributor_name LIKE ?
            GROUP BY donor_entity_key
            ORDER BY total_amount DESC
            LIMIT 8
            """,
            (like_param,),
        ).fetchall()
        for row in rows:
            entity_rows.append(
                {
                    "source_domain": "Federal donor",
                    "entity_name": row["contributor_name"] or row["donor_entity_key"],
                    "entity_key": row["donor_entity_key"],
                    "total_amount": float(row["total_amount"] or 0.0),
                    "record_count": int(row["contribution_count"] or 0),
                    "extra": f"cycle {cycle}",
                }
            )

    if query and _table_exists(conn, "lobbying_clients"):
        rows = conn.execute(
            """
            SELECT client_id, client_name
            FROM lobbying_clients
            WHERE client_name LIKE ?
            ORDER BY client_name ASC
            LIMIT 8
            """,
            (like_param,),
        ).fetchall()
        for row in rows:
            entity_rows.append(
                {
                    "source_domain": "Lobbying client",
                    "entity_name": row["client_name"],
                    "entity_key": str(row["client_id"]),
                    "total_amount": None,
                    "record_count": None,
                    "extra": "lobbying registry",
                }
            )

    if query and _table_exists(conn, "irs527_organizations"):
        rows = conn.execute(
            """
            SELECT ein, COALESCE(MAX(org_name), ein) AS org_name
            FROM irs527_organizations
            WHERE org_name LIKE ?
            GROUP BY ein
            ORDER BY org_name ASC
            LIMIT 8
            """,
            (like_param,),
        ).fetchall()
        for row in rows:
            entity_rows.append(
                {
                    "source_domain": "IRS 527 organization",
                    "entity_name": row["org_name"] or row["ein"],
                    "entity_key": row["ein"],
                    "total_amount": None,
                    "record_count": None,
                    "extra": "IRS 8871/8872 filings",
                }
            )

    if query and _table_exists(conn, "lobbying_donor_matches"):
        matched_rows = [
            {
                "client_name": row["client_name"] or "Unknown Client",
                "donor_name": row["donor_name"] or row["donor_key"] or "Unknown Donor",
                "score": float(row["score"] or 0.0),
            }
            for row in conn.execute(
                """
                SELECT client_name, donor_name, donor_key, score
                FROM lobbying_donor_matches
                WHERE client_name LIKE ? OR donor_name LIKE ?
                ORDER BY score DESC, client_name ASC
                LIMIT 12
                """,
                (like_param, like_param),
            ).fetchall()
        ]

    if query and _table_exists(conn, "analytics_donor_committee_agg"):
        donor_committee_rows = [
            {
                "donor_name": row["donor_name"] or row["donor_key"],
                "committee_name": row["committee_name"] or row["committee_id"],
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
            }
            for row in conn.execute(
                """
                SELECT donor_key, donor_name, committee_id, committee_name, total_amount, contribution_count
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts' AND (donor_name LIKE ? OR donor_key LIKE ?)
                ORDER BY total_amount DESC
                LIMIT 15
                """,
                (like_param, like_param),
            ).fetchall()
        ]

    if query and _table_exists(conn, "lobbying_donor_matches"):
        client_donor_rows = [
            {
                "client_name": row["client_name"] or "Unknown Client",
                "donor_name": row["donor_name"] or row["donor_key"] or "Unknown Donor",
                "score": float(row["score"] or 0.0),
            }
            for row in conn.execute(
                """
                SELECT client_name, donor_name, donor_key, score
                FROM lobbying_donor_matches
                WHERE donor_name LIKE ? OR client_name LIKE ?
                ORDER BY score DESC, client_name ASC
                LIMIT 15
                """,
                (like_param, like_param),
            ).fetchall()
        ]

    available_months = []
    if _table_exists(conn, "analytics_large_contributions"):
        available_months = [
            row["month_key"]
            for row in conn.execute(
                """
                SELECT DISTINCT SUBSTR(event_date, 1, 7) AS month_key
                FROM analytics_large_contributions
                WHERE event_date IS NOT NULL
                ORDER BY month_key DESC
                LIMIT 12
                """
            ).fetchall()
            if row["month_key"]
        ]

    month_key = month if month else (_latest_month_with_large_contributions(conn) if _table_exists(conn, "analytics_large_contributions") else datetime.utcnow().strftime("%Y-%m"))
    if month_key and _table_exists(conn, "analytics_large_contributions"):
        unusual_rows = [
            {
                "event_date": row["event_date"],
                "committee_name": row["committee_name"] or "Unknown Committee",
                "donor_name": row["donor_name"] or "Unknown Donor",
                "amount": float(row["amount"] or 0.0),
                "large_threshold": float(row["large_threshold"] or 0.0),
                "source": row["source"] or "unknown",
            }
            for row in conn.execute(
                """
                SELECT event_date, committee_name, donor_name, amount, large_threshold, source
                FROM analytics_large_contributions
                WHERE SUBSTR(event_date, 1, 7) = ?
                ORDER BY amount DESC
                LIMIT 20
                """,
                (month_key,),
            ).fetchall()
        ]

    if tab == 'investigate':
        narrative_summary = (
            f"For '{query or 'your selected scope'}', the workspace found {len(entity_rows)} cross-dataset entity hits "
            f"and {len(matched_rows)} confidence-scored lobbying↔donor links. Start with high-confidence links first, then expand to likely/possible matches."
        )
    elif tab == 'flow':
        total_flow = sum(row["total_amount"] for row in donor_committee_rows)
        narrative_summary = (
            f"For '{query or 'your selected scope'}', we identified {len(donor_committee_rows)} donor→committee flow edges totaling "
            f"${total_flow:,.2f}, plus {len(client_donor_rows)} lobbying client↔donor links that can explain potential influence pathways."
        )
    else:
        flagged_total = sum(row["amount"] for row in unusual_rows)
        narrative_summary = (
            f"In {month_key}, there are {len(unusual_rows)} unusually large contributions totaling ${flagged_total:,.2f}. "
            "Use this as a lead list for reporting, then verify each row in source filings."
        )

    methodology_notes = {
        "investigate": (
            "Investigate person/organization\n"
            "How calculated: name-based lookup across state donors, federal donors, lobbying clients, and 527 organizations.\n"
            "Confidence: lobbying-donor rows include score-based confidence labels from cross-matching outputs.\n"
            "Caveats: name collisions and legal-entity aliases can create ambiguous matches."
        ),
        "flow": (
            "Trace money flow\n"
            "How calculated: donor-to-committee totals come from analytics_donor_committee_agg (bulk_receipts source).\n"
            "Confidence: lobbying client-donor edges use cross-match scores.\n"
            "Caveats: donor-to-candidate paths are inferred through committee channels, not always direct transfers."
        ),
        "patterns": (
            "Find unusual patterns this month\n"
            "How calculated: rows come from analytics_large_contributions for the selected month key.\n"
            "Threshold: each row includes its computed large-threshold comparator.\n"
            "Caveats: anomaly flags are statistical leads, not evidence of wrongdoing."
        ),
    }

    if export_format == 'notes':
        return Response(
            methodology_notes[tab] + "\n",
            mimetype='text/plain',
            headers={"Content-Disposition": f"attachment; filename=investigation_{tab}_methodology.txt"},
        )

    if export_format == 'csv':
        if tab == 'investigate':
            headers = ["source_domain", "entity_name", "entity_key", "total_amount", "record_count", "extra"]
            rows = [
                [
                    row["source_domain"],
                    row["entity_name"],
                    row["entity_key"],
                    row["total_amount"],
                    row["record_count"],
                    row["extra"],
                ]
                for row in entity_rows
            ]
        elif tab == 'flow':
            headers = ["donor_name", "committee_name", "total_amount", "contribution_count"]
            rows = [
                [
                    row["donor_name"],
                    row["committee_name"],
                    row["total_amount"],
                    row["contribution_count"],
                ]
                for row in donor_committee_rows
            ]
        else:
            headers = ["event_date", "committee_name", "donor_name", "amount", "large_threshold", "source"]
            rows = [
                [
                    row["event_date"],
                    row["committee_name"],
                    row["donor_name"],
                    row["amount"],
                    row["large_threshold"],
                    row["source"],
                ]
                for row in unusual_rows
            ]
        return _csv_response(rows, headers, filename=f"investigation_{tab}.csv")

    return render_template(
        'investigate.html',
        tab=tab,
        query=query,
        cycle=cycle,
        month_key=month_key,
        show_advanced=show_advanced,
        available_months=available_months,
        entity_rows=entity_rows,
        matched_rows=matched_rows,
        donor_committee_rows=donor_committee_rows,
        client_donor_rows=client_donor_rows,
        unusual_rows=unusual_rows,
        narrative_summary=narrative_summary,
    )


@main_bp.route('/')
def index():
    """Bulk-first dashboard with local/federal finance entry points."""
    from webapp.utils.time_filter import get_active_period, period_to_date_window
    conn = current_app.get_database()
    period = get_active_period()
    date_from, date_to = period_to_date_window(period)

    def _safe_count(table_name: str) -> int:
        if not _table_exists(conn, table_name):
            return 0
        return int(_scalar(conn, f"SELECT COUNT(*) AS count FROM {table_name}", default=0))

    stats, freshness = _get_candidate_stats_cached(conn, period=period)
    stats['legacy_reports'] = _safe_count("reports")
    stats['legacy_committees'] = _safe_count("committees")
    stats['legacy_donors'] = _safe_count("donors")

    # Lobbying counts
    if _table_exists(conn, "lobbying_entities"):
        stats['lobbying_entities'] = int(_scalar(conn, "SELECT COUNT(*) FROM lobbying_entities", default=0))
    else:
        stats['lobbying_entities'] = 0
    if _table_exists(conn, "lobbying_clients"):
        stats['lobbying_clients'] = int(_scalar(conn, "SELECT COUNT(*) FROM lobbying_clients", default=0))
    else:
        stats['lobbying_clients'] = 0

    # IRS 527 counts
    if _table_exists(conn, "irs527_organizations"):
        stats['irs527_orgs'] = int(_scalar(conn, "SELECT COUNT(DISTINCT ein) FROM irs527_organizations", default=0))
    else:
        stats['irs527_orgs'] = 0

    if _table_exists(conn, "irs527_expenditures"):
        exp_clause, exp_params = _text_date_window_clause(
            "date",
            date_from=date_from,
            date_to=date_to,
        )
        stats['irs527_total_expenditures'] = float(
            _scalar(
                conn,
                f"SELECT COALESCE(SUM(amount), 0) FROM irs527_expenditures WHERE amount > 0{exp_clause}",
                params=tuple(exp_params),
                default=0,
            )
        )
    elif _table_exists(conn, "irs527_reports"):
        stats['irs527_total_expenditures'] = float(_scalar(
            conn, "SELECT COALESCE(SUM(total_expenditures), 0) FROM irs527_reports", default=0
        ))
    else:
        stats['irs527_total_expenditures'] = 0

    if _table_exists(conn, "irs527_director_donor_matches"):
        stats['irs527_director_donor_matches'] = int(_scalar(
            conn, "SELECT COUNT(*) FROM irs527_director_donor_matches", default=0
        ))
    else:
        stats['irs527_director_donor_matches'] = 0

    if _table_exists(conn, "irs527_contributions"):
        contrib_clause, contrib_params = _text_date_window_clause(
            "date",
            date_from=date_from,
            date_to=date_to,
        )
        stats['irs527_total_contributions_received'] = float(
            _scalar(
                conn,
                f"SELECT COALESCE(SUM(amount), 0) FROM irs527_contributions WHERE amount > 0{contrib_clause}",
                params=tuple(contrib_params),
                default=0,
            )
        )
        stats['irs527_contribution_records'] = int(
            _scalar(
                conn,
                f"SELECT COUNT(*) FROM irs527_contributions WHERE amount > 0{contrib_clause}",
                params=tuple(contrib_params),
                default=0,
            )
        )
    elif _table_exists(conn, "irs527_contribution_rollup"):
        stats['irs527_total_contributions_received'] = float(_scalar(
            conn, "SELECT COALESCE(SUM(total_amount), 0) FROM irs527_contribution_rollup", default=0
        ))
        stats['irs527_contribution_records'] = int(_scalar(
            conn, "SELECT COALESCE(SUM(contribution_count), 0) FROM irs527_contribution_rollup", default=0
        ))
    else:
        stats['irs527_total_contributions_received'] = 0
        stats['irs527_contribution_records'] = 0

    top_donors = _get_top_donors_cached(conn, period=period)
    insights = _get_dashboard_insights(conn, period=period)

    return render_template('index.html',
                           stats=stats,
                           top_donors=top_donors,
                           freshness=freshness,
                           insights=insights)


@main_bp.route('/candidates')
def candidates():
    """Unified candidates landing page — state and federal entry points."""
    from webapp.utils.time_filter import get_active_period

    conn = current_app.get_database()
    period = get_active_period()
    stats, freshness = _get_candidate_stats_cached(conn, period=period)
    return render_template('candidates.html', stats=stats, freshness=freshness)


@main_bp.route('/legacy')
def legacy():
    """Legacy scrape-era pages kept for QA and historical lookup."""
    conn = current_app.get_database()
    return render_template(
        'legacy.html',
        legacy_counts={
            'committees': Committee.count(conn),
            'reports': Report.count(conn),
            'donors': Donor.count(conn),
            'contributions': Contribution.count(conn),
        },
    )


@main_bp.route('/search')
def search():
    """Global search."""
    from webapp.utils.time_filter import get_active_period, period_to_date_window

    conn = current_app.get_database()
    period = get_active_period()
    date_from, date_to = period_to_date_window(period)
    raw_query = request.args.get('q', '')
    max_query_len = max(8, int(current_app.config.get('SEARCH_MAX_QUERY_LENGTH', 64)))
    min_query_len = max(1, int(current_app.config.get('SEARCH_MIN_QUERY_LENGTH', 2)))
    query_timeout_ms = max(50, int(current_app.config.get('SEARCH_QUERY_TIMEOUT_MS', 700)))
    slow_query_ms = max(50, int(current_app.config.get('SEARCH_SLOW_QUERY_MS', 400)))

    query, query_was_normalized = _sanitize_search_query(raw_query, max_query_len)
    search_type = request.args.get('type', 'all').strip()
    if search_type not in SEARCH_TYPES:
        search_type = "all"

    is_short_query = bool(query) and len(query) < min_query_len and not query.isdigit()

    search_meta = {
        'query_was_normalized': query_was_normalized,
        'min_query_length': min_query_len,
        'max_query_length': max_query_len,
        'query_too_short': is_short_query,
        'cache_hit': False,
        'skipped_sections': [],
        'timed_out_sections': [],
        'errored_sections': [],
        'slow_sections': [],
        'duration_ms': 0.0,
        'timeout_ms': query_timeout_ms,
    }

    results = {
        'committees': [],
        'donors': [],
        'candidates': [],
        'reports': [],
        'filed_docs': [],
        'donor_keys': [],
        'query': query,
        'type': search_type,
        'search_meta': search_meta,
    }

    search_start = time.perf_counter()

    if query and not is_short_query:
        cached_payload = _get_cached_search_results(period, query, search_type)
        if cached_payload is not None:
            for section_name in ("committees", "donors", "candidates", "reports", "filed_docs", "donor_keys"):
                results[section_name] = cached_payload.get(section_name, [])
            search_meta["cache_hit"] = True
            search_meta['duration_ms'] = round((time.perf_counter() - search_start) * 1000.0, 2)
            return render_template('search.html', **results)

        short_mode = len(query) < 4
        committee_limit = 30 if short_mode else 50
        donor_limit = 30 if short_mode else 50
        candidate_local_limit = 20 if short_mode else 30
        candidate_federal_limit = 15 if short_mode else 20
        report_limit = 20 if short_mode else 30
        filed_doc_limit = 20 if short_mode else 30
        donor_key_limit = 20 if short_mode else 30
        allow_filed_docs = search_type == "filed_docs" or (search_type == "all" and _is_filed_doc_query(query))
        allow_donor_keys = search_type == "donor_keys" or (search_type == "all" and _is_donor_key_query(query))

        if search_type in ('all', 'filed_docs') and not allow_filed_docs:
            search_meta["skipped_sections"].append("filed_docs")
        if search_type in ('all', 'donor_keys') and not allow_donor_keys:
            search_meta["skipped_sections"].append("donor_keys")

        def _run_section(section_name: str, callback):
            try:
                payload, elapsed_ms, timed_out = _run_query_with_timeout(
                    conn,
                    callback,
                    timeout_ms=query_timeout_ms,
                )
            except Exception:
                current_app.logger.exception("Search section failed: %s", section_name)
                search_meta['errored_sections'].append(section_name)
                return []

            if timed_out:
                search_meta['timed_out_sections'].append(section_name)
                current_app.logger.warning(
                    "Search section timed out (%s) q=%r timeout_ms=%s",
                    section_name,
                    query,
                    query_timeout_ms,
                )
                return []

            if elapsed_ms >= float(slow_query_ms):
                search_meta['slow_sections'].append(section_name)
                current_app.logger.warning(
                    "Slow search section (%s) q=%r elapsed_ms=%.2f",
                    section_name,
                    query,
                    elapsed_ms,
                )
            return payload

        if search_type in ('all', 'committees'):
            results['committees'] = _run_section(
                'committees',
                lambda: Committee.search(
                    conn,
                    query,
                    limit=committee_limit,
                    transaction_date_from=date_from,
                    transaction_date_to=date_to,
                ),
            )

        if search_type in ('all', 'donors'):
            results['donors'] = _run_section(
                'donors',
                lambda: Donor.search(
                    conn,
                    query,
                    limit=donor_limit,
                    transaction_date_from=date_from,
                    transaction_date_to=date_to,
                ),
            )

        if search_type in ('all', 'candidates'):
            local_candidates = _run_section(
                'candidates_local',
                lambda: _search_local_candidates(conn, query, limit=candidate_local_limit),
            )
            federal_candidates = _run_section(
                'candidates_federal',
                lambda: _search_federal_candidates(conn, query, limit=candidate_federal_limit),
            )
            results['candidates'] = (local_candidates or []) + (federal_candidates or [])

        if search_type in ('all', 'reports'):
            results['reports'] = _run_section(
                'reports',
                lambda: _search_reports(
                    conn,
                    query,
                    limit=report_limit,
                    filed_date_from=date_from,
                    filed_date_to=date_to,
                ),
            )

        if search_type in ('all', 'filed_docs') and allow_filed_docs:
            results['filed_docs'] = _run_section(
                'filed_docs',
                lambda: _search_filed_docs(
                    conn,
                    query,
                    limit=filed_doc_limit,
                    filed_date_from=date_from,
                    filed_date_to=date_to,
                ),
            )

        if search_type in ('all', 'donor_keys') and allow_donor_keys:
            results['donor_keys'] = _run_section(
                'donor_keys',
                lambda: _search_donor_keys(
                    conn,
                    query,
                    limit=donor_key_limit,
                    date_from=date_from,
                    date_to=date_to,
                ),
            )

        if not search_meta["timed_out_sections"] and not search_meta["errored_sections"]:
            _set_cached_search_results(
                period,
                query,
                search_type,
                {
                    "committees": results["committees"],
                    "donors": results["donors"],
                    "candidates": results["candidates"],
                    "reports": results["reports"],
                    "filed_docs": results["filed_docs"],
                    "donor_keys": results["donor_keys"],
                },
            )

    search_meta['duration_ms'] = round((time.perf_counter() - search_start) * 1000.0, 2)
    if search_meta['duration_ms'] >= float(slow_query_ms) and query and not is_short_query:
        current_app.logger.warning(
            "Slow search request q=%r type=%s duration_ms=%.2f timeouts=%s",
            query,
            search_type,
            search_meta['duration_ms'],
            ",".join(search_meta['timed_out_sections']) if search_meta['timed_out_sections'] else "none",
        )

    return render_template('search.html', **results)


@main_bp.route('/person-intelligence')
def person_intelligence():
    """Unified person lookup across all datasets (donors, candidates, 527 directors, lobbying)."""
    conn = current_app.get_database()
    query = request.args.get('q', '').strip()

    def _donor_group_key(donor_name: str) -> str:
        """Heuristic normalization for grouping near-identical donor names."""
        if not donor_name:
            return ""
        s = " ".join(donor_name.replace(".", "").strip().upper().split())
        if "," in s:
            last, rest = s.split(",", 1)
            rest = rest.strip()
            parts = rest.split()
            if len(parts) >= 2 and len(parts[-1]) == 1:
                rest = " ".join(parts[:-1])
            return f"{last.strip()}, {rest}".strip().lower()
        parts = [p for p in s.split() if len(p) != 1]
        return " ".join(parts).strip().lower()

    results = {
        'query': query,
        'donors': [],
        'donor_groups': [],
        'candidates': [],
        'directors_527': [],
        'lobbying_entities': [],
        'lobbying_clients': [],
        'fec_contributors': [],
    }

    if query and len(query) >= 2:
        like_pattern = f"%{query}%"

        # Donors
        if _table_exists(conn, "analytics_donor_summary"):
            raw_donors = conn.execute(
                """
                SELECT donor_key, donor_name, donor_city, donor_state,
                       total_amount, contribution_count, committee_count
                FROM analytics_donor_summary
                WHERE source = 'bulk_receipts' AND donor_name LIKE ?
                ORDER BY total_amount DESC
                LIMIT 100
                """,
                (like_pattern,),
            ).fetchall()
            results['donors'] = raw_donors

            # Group by normalized name for dedup display
            groups = OrderedDict()
            for d in raw_donors:
                key = _donor_group_key(d['donor_name'] or '')
                if key not in groups:
                    groups[key] = {
                        'donor_name': d['donor_name'],
                        'total_amount': 0.0,
                        'contribution_count': 0,
                        'committee_count': 0,
                        'sub_rows': [],
                    }
                groups[key]['total_amount'] += float(d['total_amount'] or 0)
                groups[key]['contribution_count'] += int(d['contribution_count'] or 0)
                groups[key]['committee_count'] += int(d['committee_count'] or 0)
                groups[key]['sub_rows'].append(d)

            results['donor_groups'] = [
                {**g, 'has_multiple': len(g['sub_rows']) > 1}
                for g in groups.values()
            ]

        # State candidates (enriched with committee links and candidacies)
        if _table_exists(conn, "bulk_candidates_clean"):
            candidate_rows = conn.execute(
                """
                SELECT candidate_id, candidate_full_name
                FROM bulk_candidates_clean
                WHERE candidate_full_name LIKE ?
                ORDER BY candidate_full_name
                LIMIT 25
                """,
                (like_pattern,),
            ).fetchall()

            candidate_ids = [row["candidate_id"] for row in candidate_rows if row["candidate_id"] is not None]
            committee_lookup: dict[int, list[dict]] = defaultdict(list)
            candidacy_lookup: dict[int, list[dict]] = defaultdict(list)

            has_links = _table_exists(conn, "bulk_committee_candidate_links")
            has_committees = _table_exists(conn, "bulk_committees_clean")
            has_candidacies = _table_exists(conn, "isbe_candidacies")

            if candidate_ids and has_links:
                placeholders = ",".join(["?"] * len(candidate_ids))
                if has_committees:
                    committee_rows = conn.execute(
                        f"""
                        SELECT
                            l.candidate_id,
                            l.committee_id_sbe,
                            COALESCE(c.committee_name, 'Committee ' || l.committee_id_sbe) AS committee_name
                        FROM bulk_committee_candidate_links l
                        LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = l.committee_id_sbe
                        WHERE l.candidate_id IN ({placeholders})
                        ORDER BY l.candidate_id, committee_name
                        """,
                        tuple(candidate_ids),
                    ).fetchall()
                else:
                    committee_rows = conn.execute(
                        f"""
                        SELECT
                            candidate_id,
                            committee_id_sbe,
                            'Committee ' || committee_id_sbe AS committee_name
                        FROM bulk_committee_candidate_links
                        WHERE candidate_id IN ({placeholders})
                        ORDER BY candidate_id, committee_name
                        """,
                        tuple(candidate_ids),
                    ).fetchall()
                for row in committee_rows:
                    if row["candidate_id"] is None:
                        continue
                    committee_lookup[int(row["candidate_id"])].append(
                        {
                            "committee_id_sbe": row["committee_id_sbe"],
                            "committee_name": row["committee_name"],
                        }
                    )

            if candidate_ids and has_candidacies:
                placeholders = ",".join(["?"] * len(candidate_ids))
                candidacy_rows = conn.execute(
                    f"""
                    WITH ranked AS (
                        SELECT
                            candidate_id,
                            election_type,
                            election_year,
                            race_type,
                            outcome,
                            ROW_NUMBER() OVER (
                                PARTITION BY candidate_id
                                ORDER BY election_year DESC, election_type
                            ) AS rn
                        FROM isbe_candidacies
                        WHERE candidate_id IN ({placeholders})
                    )
                    SELECT candidate_id, election_type, election_year, race_type, outcome
                    FROM ranked
                    WHERE rn <= 5
                    ORDER BY candidate_id, election_year DESC, election_type
                    """,
                    tuple(candidate_ids),
                ).fetchall()
                for row in candidacy_rows:
                    if row["candidate_id"] is None:
                        continue
                    candidacy_lookup[int(row["candidate_id"])].append(
                        {
                            "election_type": row["election_type"] or "",
                            "election_year": row["election_year"],
                            "race_type": row["race_type"] or "",
                            "outcome": row["outcome"] or "",
                        }
                    )

            enriched_candidates = []
            for cand in candidate_rows:
                cand_dict = dict(cand)
                candidate_id_value = cand_dict.get("candidate_id")
                cid = int(candidate_id_value) if candidate_id_value is not None else None
                cand_dict["committees"] = committee_lookup.get(cid, []) if cid is not None else []
                cand_dict["candidacies"] = candidacy_lookup.get(cid, []) if cid is not None else []
                enriched_candidates.append(cand_dict)
            results['candidates'] = enriched_candidates

        # 527 directors
        if _table_exists(conn, "irs527_directors"):
            results['directors_527'] = conn.execute(
                """
                SELECT DISTINCT ein, org_name, person_name, title, city, state
                FROM irs527_directors
                WHERE person_name LIKE ?
                ORDER BY person_name
                LIMIT 25
                """,
                (like_pattern,),
            ).fetchall()

        # Lobbying entities
        if _table_exists(conn, "lobbying_entities"):
            results['lobbying_entities'] = conn.execute(
                """
                SELECT entity_id, entity_name
                FROM lobbying_entities
                WHERE entity_name LIKE ?
                ORDER BY entity_name
                LIMIT 25
                """,
                (like_pattern,),
            ).fetchall()

        # Lobbying clients
        if _table_exists(conn, "lobbying_clients"):
            results['lobbying_clients'] = conn.execute(
                """
                SELECT client_id, client_name
                FROM lobbying_clients
                WHERE client_name LIKE ?
                ORDER BY client_name
                LIMIT 25
                """,
                (like_pattern,),
            ).fetchall()

        # FEC contributors
        if _table_exists(conn, "fec_schedule_a_contributions"):
            results['fec_contributors'] = conn.execute(
                """
                SELECT DISTINCT contributor_name, contributor_city, contributor_state,
                       SUM(contribution_receipt_amount) AS total_amount,
                       COUNT(*) AS contribution_count
                FROM fec_schedule_a_contributions
                WHERE contributor_name LIKE ?
                GROUP BY contributor_name, contributor_city, contributor_state
                ORDER BY total_amount DESC
                LIMIT 25
                """,
                (like_pattern,),
            ).fetchall()

    return render_template('person_intelligence.html', **results)


@main_bp.route('/live-feed')
def live_feed():
    """Recent local donations and federal Schedule A/B/E activity feed."""
    conn = current_app.get_database()

    output_format = request.args.get('format', 'html', type=str).strip().lower()
    export_table = request.args.get('table', '', type=str).strip().lower()
    local_limit = min(max(request.args.get('local_limit', 75, type=int), 10), 300)
    federal_limit = min(max(request.args.get('federal_limit', 75, type=int), 10), 300)
    schedule_b_limit = min(max(request.args.get('schedule_b_limit', 75, type=int), 10), 300)
    schedule_e_limit = min(max(request.args.get('schedule_e_limit', 75, type=int), 10), 300)

    local_rows = _recent_local_candidate_donations(conn, limit=local_limit)
    federal_rows = _recent_federal_candidate_donations(conn, limit=federal_limit)
    schedule_b_rows = _recent_federal_schedule_b_disbursements(conn, limit=schedule_b_limit)
    schedule_e_rows = _recent_federal_schedule_e_expenditures(conn, limit=schedule_e_limit)

    local_latest_date = local_rows[0]["received_date"] if local_rows else None
    federal_latest_date = federal_rows[0]["contribution_receipt_date"] if federal_rows else None
    federal_schedule_b_latest_date = schedule_b_rows[0]["disbursement_date"] if schedule_b_rows else None
    federal_schedule_e_latest_date = schedule_e_rows[0]["expenditure_date"] if schedule_e_rows else None
    federal_latest_coverage_date = None
    if _table_exists(conn, "fec_candidate_cycle_totals"):
        federal_latest_coverage_date = _scalar(
            conn,
            """
            SELECT MAX(COALESCE(transaction_coverage_date, coverage_end_date)) AS max_coverage
            FROM fec_candidate_cycle_totals
            """,
            default=None,
        )

    if output_format == 'csv':
        if export_table == 'schedule_b':
            csv_rows = [
                [
                    row.get('disbursement_date'),
                    row.get('cycle'),
                    row.get('candidate_id'),
                    row.get('candidate_name'),
                    row.get('committee_name'),
                    row.get('recipient_name'),
                    row.get('recipient_city'),
                    row.get('recipient_state'),
                    row.get('category'),
                    row.get('amount'),
                ]
                for row in _recent_federal_schedule_b_disbursements(conn, limit=500000)
            ]
            return _csv_response(
                csv_rows,
                [
                    'disbursement_date',
                    'cycle',
                    'candidate_id',
                    'candidate_name',
                    'committee_name',
                    'recipient_name',
                    'recipient_city',
                    'recipient_state',
                    'category',
                    'amount',
                ],
                filename='live_feed_schedule_b.csv',
            )

        if export_table == 'schedule_e':
            csv_rows = [
                [
                    row.get('expenditure_date'),
                    row.get('cycle'),
                    row.get('candidate_id'),
                    row.get('candidate_name'),
                    row.get('support_oppose_indicator'),
                    row.get('committee_name'),
                    row.get('payee_name'),
                    row.get('payee_city'),
                    row.get('payee_state'),
                    row.get('category'),
                    row.get('amount'),
                ]
                for row in _recent_federal_schedule_e_expenditures(conn, limit=500000)
            ]
            return _csv_response(
                csv_rows,
                [
                    'expenditure_date',
                    'cycle',
                    'candidate_id',
                    'candidate_name',
                    'support_oppose_indicator',
                    'committee_name',
                    'payee_name',
                    'payee_city',
                    'payee_state',
                    'category',
                    'amount',
                ],
                filename='live_feed_schedule_e.csv',
            )

        return Response("unsupported csv export table\n", mimetype='text/plain', status=400)

    return render_template(
        'live_feed.html',
        local_rows=local_rows,
        federal_rows=federal_rows,
        schedule_b_rows=schedule_b_rows,
        schedule_e_rows=schedule_e_rows,
        local_limit=local_limit,
        federal_limit=federal_limit,
        schedule_b_limit=schedule_b_limit,
        schedule_e_limit=schedule_e_limit,
        local_latest_date=local_latest_date,
        federal_latest_date=federal_latest_date,
        federal_schedule_b_latest_date=federal_schedule_b_latest_date,
        federal_schedule_e_latest_date=federal_schedule_e_latest_date,
        federal_latest_coverage_date=federal_latest_coverage_date,
    )


@main_bp.route('/compare')
def compare():
    """Compare candidate vs candidate or committee vs committee side-by-side."""
    conn = current_app.get_database()
    mode = request.args.get("mode", "candidate").strip().lower()
    if mode not in COMPARE_MODES:
        mode = "candidate"

    left_value = (request.args.get("left") or "").strip()
    right_value = (request.args.get("right") or "").strip()

    options = _list_compare_candidates(conn) if mode == "candidate" else _list_compare_committees(conn)
    left_profile = None
    right_profile = None
    compare_error = None

    if left_value and right_value:
        if left_value == right_value:
            compare_error = "Select two different entities to compare."
        else:
            if mode == "candidate":
                try:
                    left_profile = _load_candidate_compare_profile(conn, int(left_value))
                    right_profile = _load_candidate_compare_profile(conn, int(right_value))
                except ValueError:
                    compare_error = "Candidate IDs must be numeric."
            else:
                left_profile = _load_committee_compare_profile(conn, left_value)
                right_profile = _load_committee_compare_profile(conn, right_value)
            if not compare_error and (left_profile is None or right_profile is None):
                compare_error = (
                    "Compare data is not available for one or both selections. "
                    "Load bulk receipts or legacy contributions first."
                )

    overlap = _build_overlap(left_profile, right_profile)
    return render_template(
        "compare.html",
        mode=mode,
        options=options,
        left_value=left_value,
        right_value=right_value,
        left_profile=left_profile,
        right_profile=right_profile,
        overlap=overlap,
        compare_error=compare_error,
    )
