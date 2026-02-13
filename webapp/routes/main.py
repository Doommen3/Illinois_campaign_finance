"""Main routes for dashboard and search."""
from flask import Blueprint, render_template, request, current_app

from database.models import Committee, Report, Donor, Contribution

main_bp = Blueprint('main', __name__)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


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
        freshness['federal_sync_updated_at'] = _scalar(
            conn,
            "SELECT MAX(updated_at) AS max_updated_at FROM fec_schedule_a_contributions",
            default=None,
        )

    return stats, freshness


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
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'all')

    results = {
        'committees': [],
        'donors': [],
        'query': query,
        'type': search_type
    }

    if query:
        if search_type in ('all', 'committees'):
            results['committees'] = Committee.search(conn, query, limit=50)

        if search_type in ('all', 'donors'):
            results['donors'] = Donor.search(conn, query, limit=50)

    return render_template('search.html', **results)
