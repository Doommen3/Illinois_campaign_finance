"""Main routes for dashboard, search, and compare views."""
from collections import defaultdict
import csv
from datetime import datetime
from io import StringIO
import sqlite3
import time

from flask import Blueprint, Response, render_template, request, current_app

from database.models import Committee, Report, Donor, Contribution

main_bp = Blueprint('main', __name__)

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
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
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


def _scalar(conn, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return default
    if not row:
        return default
    keys = row.keys() if hasattr(row, "keys") else []
    if not keys:
        return default
    value = row[keys[0]]
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


def _get_candidate_stats(conn):
    """Return (stats_dict, freshness_dict) for state and federal candidate data."""
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
    }

    if _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        stats['local_candidate_rows'] = int(
            _scalar(conn, "SELECT COUNT(*) AS count FROM bulk_candidate_committee_finance_agg", default=0)
        )
        stats['local_candidates'] = int(
            _scalar(
                conn,
                "SELECT COUNT(DISTINCT candidate_id) AS count FROM bulk_candidate_committee_finance_agg WHERE candidate_id IS NOT NULL",
                default=0,
            )
        )
        stats['local_committees'] = int(
            _scalar(
                conn,
                "SELECT COUNT(DISTINCT committee_id_sbe) AS count FROM bulk_candidate_committee_finance_agg WHERE committee_id_sbe IS NOT NULL",
                default=0,
            )
        )

    if _table_exists(conn, "bulk_d2_totals_clean"):
        stats['local_total_receipts'] = float(
            _scalar(
                conn,
                "SELECT COALESCE(SUM(total_receipts), 0) AS total FROM bulk_d2_totals_clean WHERE COALESCE(is_archived, 0) = 0",
                default=0.0,
            )
        )
        stats['local_total_expenditures'] = float(
            _scalar(
                conn,
                "SELECT COALESCE(SUM(total_expenditures), 0) AS total FROM bulk_d2_totals_clean WHERE COALESCE(is_archived, 0) = 0",
                default=0.0,
            )
        )
        stats['local_archived_filings'] = int(
            _scalar(
                conn,
                "SELECT COUNT(*) AS count FROM bulk_d2_totals_clean WHERE COALESCE(is_archived, 0) = 1",
                default=0,
            )
        )

    if _table_exists(conn, "fec_candidate_match"):
        stats['federal_candidates'] = int(
            _scalar(
                conn,
                "SELECT COUNT(DISTINCT fec_candidate_id) AS count FROM fec_candidate_match WHERE fec_candidate_id IS NOT NULL",
                default=0,
            )
        )

    if _table_exists(conn, "fec_schedule_a_contributions"):
        stats['federal_contributions'] = int(
            _scalar(
                conn,
                "SELECT COUNT(*) AS count FROM fec_schedule_a_contributions",
                default=0,
            )
        )
    if _table_exists(conn, "fec_schedule_b_disbursements"):
        stats['federal_disbursements'] = int(
            _scalar(
                conn,
                "SELECT COUNT(*) AS count FROM fec_schedule_b_disbursements",
                default=0,
            )
        )
        stats['federal_disbursement_total'] = float(
            _scalar(
                conn,
                "SELECT COALESCE(SUM(disbursement_amount), 0) AS total FROM fec_schedule_b_disbursements",
                default=0.0,
            )
        )
    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        stats['federal_independent_expenditures'] = int(
            _scalar(
                conn,
                "SELECT COUNT(*) AS count FROM fec_schedule_e_independent_expenditures",
                default=0,
            )
        )
        stats['federal_independent_expenditure_total'] = float(
            _scalar(
                conn,
                "SELECT COALESCE(SUM(expenditure_amount), 0) AS total FROM fec_schedule_e_independent_expenditures",
                default=0.0,
            )
        )
    if _table_exists(conn, "fec_candidate_cycle_totals"):
        totals_rows = int(
            _scalar(
                conn,
                "SELECT COUNT(*) AS count FROM fec_candidate_cycle_totals",
                default=0,
            )
        )
        if totals_rows > 0:
            stats['federal_total_amount'] = float(
                _scalar(
                    conn,
                    "SELECT COALESCE(SUM(receipts), 0) AS total FROM fec_candidate_cycle_totals",
                    default=0.0,
                )
            )
        elif _table_exists(conn, "fec_schedule_a_contributions"):
            stats['federal_total_amount'] = float(
                _scalar(
                    conn,
                    "SELECT COALESCE(SUM(contribution_receipt_amount), 0) AS total FROM fec_schedule_a_contributions",
                    default=0.0,
                )
            )
    elif _table_exists(conn, "fec_schedule_a_contributions"):
        stats['federal_total_amount'] = float(
            _scalar(
                conn,
                "SELECT COALESCE(SUM(contribution_receipt_amount), 0) AS total FROM fec_schedule_a_contributions",
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
    if not _table_exists(conn, "bulk_candidate_committee_finance_agg"):
        return []

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
            COALESCE(SUM(sum_total_receipts), 0) AS total_receipts,
            {latest_expr}
        FROM bulk_candidate_committee_finance_agg
        WHERE
            COALESCE(candidate_full_name, '') LIKE ?
            OR CAST(COALESCE(candidate_id, '') AS TEXT) LIKE ?
        GROUP BY candidate_id, candidate_full_name
        ORDER BY total_receipts DESC, candidate_full_name ASC
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()

    output = []
    for row in rows:
        candidate_name = row["candidate_full_name"] or f"Candidate {row['candidate_id']}"
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


def _search_reports(conn, query: str, limit: int = 30) -> list[dict]:
    if not _table_exists(conn, "reports"):
        return []
    rows = conn.execute(
        """
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
        WHERE
            c.name LIKE ?
            OR COALESCE(r.report_type, '') LIKE ?
            OR COALESCE(r.reporting_period, '') LIKE ?
            OR COALESCE(r.filed_date, '') LIKE ?
            OR CAST(r.id AS TEXT) LIKE ?
        ORDER BY r.updated_at DESC, r.id DESC
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", limit),
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


def _search_filed_docs(conn, query: str, limit: int = 30) -> list[dict]:
    rows: list[dict] = []

    if _table_exists(conn, "bulk_d2_receipts_recon"):
        d2_rows = conn.execute(
            """
            SELECT
                filed_doc_id,
                committee_id_sbe,
                committee_name,
                ABS(COALESCE(receipts_minus_d2_total, 0)) AS abs_diff,
                first_receipt_date,
                last_receipt_date
            FROM bulk_d2_receipts_recon
            WHERE
                CAST(COALESCE(filed_doc_id, '') AS TEXT) LIKE ?
                OR COALESCE(committee_name, '') LIKE ?
            ORDER BY abs_diff DESC, filed_doc_id DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
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

    if _table_exists(conn, "bulk_receipts_clean"):
        receipt_rows = conn.execute(
            """
            SELECT
                filed_doc_id,
                committee_id_sbe,
                COUNT(*) AS receipt_row_count,
                COALESCE(SUM(amount), 0) AS total_amount,
                MIN(received_date) AS first_receipt_date,
                MAX(received_date) AS last_receipt_date
            FROM bulk_receipts_clean
            WHERE
                filed_doc_id IS NOT NULL
                AND CAST(filed_doc_id AS TEXT) LIKE ?
            GROUP BY filed_doc_id, committee_id_sbe
            ORDER BY total_amount DESC, filed_doc_id DESC
            LIMIT ?
            """,
            (f"%{query}%", limit),
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


def _search_donor_keys(conn, query: str, limit: int = 30) -> list[dict]:
    if not _table_exists(conn, "analytics_donor_summary"):
        return []
    rows = conn.execute(
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
        WHERE
            donor_key LIKE ?
            OR donor_name LIKE ?
        ORDER BY total_amount DESC, donor_name ASC
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()

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


@main_bp.route('/')
def index():
    """Bulk-first dashboard with local/federal finance entry points."""
    conn = current_app.get_database()

    stats, freshness = _get_candidate_stats(conn)
    stats['legacy_reports'] = Report.count(conn)
    stats['legacy_committees'] = Committee.count(conn)
    stats['legacy_donors'] = Donor.count(conn)

    top_donors = Donor.get_all_with_totals(conn, limit=8, sort_by='total_amount')

    return render_template('index.html',
                           stats=stats,
                           top_donors=top_donors,
                           freshness=freshness)


@main_bp.route('/candidates')
def candidates():
    """Unified candidates landing page — state and federal entry points."""
    conn = current_app.get_database()
    stats, freshness = _get_candidate_stats(conn)
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
    conn = current_app.get_database()
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
        short_mode = len(query) < 4
        committee_limit = 30 if short_mode else 50
        donor_limit = 30 if short_mode else 50
        candidate_local_limit = 20 if short_mode else 30
        candidate_federal_limit = 15 if short_mode else 20
        report_limit = 20 if short_mode else 30
        filed_doc_limit = 20 if short_mode else 30
        donor_key_limit = 20 if short_mode else 30

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
                lambda: Committee.search(conn, query, limit=committee_limit),
            )

        if search_type in ('all', 'donors'):
            results['donors'] = _run_section(
                'donors',
                lambda: Donor.search(conn, query, limit=donor_limit),
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
                lambda: _search_reports(conn, query, limit=report_limit),
            )

        if search_type in ('all', 'filed_docs'):
            results['filed_docs'] = _run_section(
                'filed_docs',
                lambda: _search_filed_docs(conn, query, limit=filed_doc_limit),
            )

        if search_type in ('all', 'donor_keys'):
            results['donor_keys'] = _run_section(
                'donor_keys',
                lambda: _search_donor_keys(conn, query, limit=donor_key_limit),
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
