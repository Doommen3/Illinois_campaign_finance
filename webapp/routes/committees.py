"""Committee routes."""
from flask import Blueprint, render_template, request, current_app, abort, redirect, url_for

from database.models import Committee, Report, Contribution

committees_bp = Blueprint('committees', __name__)


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


def _bulk_committee_profile(conn, committee_id_sbe: int) -> dict | None:
    has_bulk_committees = _table_exists(conn, "bulk_committees_clean")
    has_bulk_receipts = _table_exists(conn, "bulk_receipts_clean")
    has_bulk_expenditures = _table_exists(conn, "bulk_expenditures_clean")
    has_candidate_links = _table_exists(conn, "bulk_committee_candidate_links")
    has_candidates = _table_exists(conn, "bulk_candidates_clean")
    has_analytics_agg = _table_exists(conn, "analytics_donor_committee_agg")
    has_officers = _table_exists(conn, "isbe_officers")
    has_isbe_committees = _table_exists(conn, "isbe_committees")
    has_filed_docs = _table_exists(conn, "isbe_filed_docs")

    committee_name = ""
    committee_meta = {}
    if has_isbe_committees:
        row = conn.execute(
            """
            SELECT id, name, type, party, purpose, active,
                   status_date, creation_date, creation_amount,
                   city, state, zipcode
            FROM isbe_committees
            WHERE id = ?
            LIMIT 1
            """,
            (committee_id_sbe,),
        ).fetchone()
        if row:
            committee_name = (row["name"] or "").strip()
            committee_meta = {
                "committee_type": row["type"] or "",
                "party": row["party"] or "",
                "purpose": row["purpose"] or "",
                "active": bool(row["active"]) if row["active"] is not None else None,
                "status_date": row["status_date"],
                "creation_date": row["creation_date"],
                "creation_amount": float(row["creation_amount"]) if row["creation_amount"] else None,
                "city": row["city"] or "",
                "state": row["state"] or "",
                "zipcode": row["zipcode"] or "",
            }

    if not committee_name and has_bulk_committees:
        row = conn.execute(
            """
            SELECT committee_name
            FROM bulk_committees_clean
            WHERE committee_id_sbe = ?
            LIMIT 1
            """,
            (committee_id_sbe,),
        ).fetchone()
        if row:
            committee_name = (row["committee_name"] or "").strip()

    if not committee_name and has_analytics_agg:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(committee_name), '') AS committee_name
            FROM analytics_donor_committee_agg
            WHERE committee_id = ?
            """,
            (str(committee_id_sbe),),
        ).fetchone()
        if row:
            committee_name = (row["committee_name"] or "").strip()

    receipts_filter = "WHERE committee_id_sbe = ?"
    if has_bulk_receipts and _column_exists(conn, "bulk_receipts_clean", "is_archived"):
        receipts_filter += " AND COALESCE(is_archived, 0) = 0"

    receipts_summary = {
        "contribution_count": 0,
        "total_amount": 0.0,
        "first_date": None,
        "last_date": None,
    }
    top_donors: list[dict] = []
    if has_bulk_receipts:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS contribution_count,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                MIN(received_date) AS first_date,
                MAX(received_date) AS last_date
            FROM bulk_receipts_clean
            {receipts_filter}
            """,
            (committee_id_sbe,),
        ).fetchone()
        if row:
            receipts_summary = {
                "contribution_count": int(row["contribution_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
                "first_date": row["first_date"],
                "last_date": row["last_date"],
            }

        top_donor_rows = conn.execute(
            f"""
            SELECT
                TRIM(
                    COALESCE(NULLIF(TRIM(first_name), ''), '')
                    || CASE
                        WHEN COALESCE(NULLIF(TRIM(first_name), ''), '') != ''
                         AND COALESCE(NULLIF(TRIM(last_or_business_name), ''), '') != '' THEN ' '
                        ELSE ''
                      END
                    || COALESCE(NULLIF(TRIM(last_or_business_name), ''), '')
                ) AS donor_name,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                COUNT(*) AS contribution_count
            FROM bulk_receipts_clean
            {receipts_filter}
            GROUP BY donor_name
            ORDER BY total_amount DESC, contribution_count DESC, donor_name ASC
            LIMIT 20
            """,
            (committee_id_sbe,),
        ).fetchall()
        top_donors = [
            {
                "donor_name": (row["donor_name"] or "").strip() or "Unknown Donor",
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
            }
            for row in top_donor_rows
        ]

    expenditures_filter = "WHERE committee_id_sbe = ?"
    if has_bulk_expenditures and _column_exists(conn, "bulk_expenditures_clean", "is_archived"):
        expenditures_filter += " AND COALESCE(is_archived, 0) = 0"

    expenditures_summary = {
        "transaction_count": 0,
        "total_amount": 0.0,
        "first_date": None,
        "last_date": None,
    }
    top_payees: list[dict] = []
    if has_bulk_expenditures:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS transaction_count,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                MIN(expended_date) AS first_date,
                MAX(expended_date) AS last_date
            FROM bulk_expenditures_clean
            {expenditures_filter}
            """,
            (committee_id_sbe,),
        ).fetchone()
        if row:
            expenditures_summary = {
                "transaction_count": int(row["transaction_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
                "first_date": row["first_date"],
                "last_date": row["last_date"],
            }

        top_payee_rows = conn.execute(
            f"""
            SELECT
                TRIM(
                    COALESCE(NULLIF(TRIM(payee_first_name), ''), '')
                    || CASE
                        WHEN COALESCE(NULLIF(TRIM(payee_first_name), ''), '') != ''
                         AND COALESCE(NULLIF(TRIM(payee_last_or_business_name), ''), '') != '' THEN ' '
                        ELSE ''
                      END
                    || COALESCE(NULLIF(TRIM(payee_last_or_business_name), ''), '')
                ) AS payee_name,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                COUNT(*) AS transaction_count
            FROM bulk_expenditures_clean
            {expenditures_filter}
            GROUP BY payee_name
            ORDER BY total_amount DESC, transaction_count DESC, payee_name ASC
            LIMIT 20
            """,
            (committee_id_sbe,),
        ).fetchall()
        top_payees = [
            {
                "payee_name": (row["payee_name"] or "").strip() or "Unknown Payee",
                "total_amount": float(row["total_amount"] or 0.0),
                "transaction_count": int(row["transaction_count"] or 0),
            }
            for row in top_payee_rows
        ]

    candidate_links: list[dict] = []
    if has_candidate_links:
        if has_candidates:
            candidate_rows = conn.execute(
                """
                SELECT
                    l.candidate_id,
                    COALESCE(MAX(c.candidate_full_name), 'Candidate ' || l.candidate_id) AS candidate_name
                FROM bulk_committee_candidate_links l
                LEFT JOIN bulk_candidates_clean c ON c.candidate_id = l.candidate_id
                WHERE l.committee_id_sbe = ?
                  AND l.candidate_id IS NOT NULL
                GROUP BY l.candidate_id
                ORDER BY candidate_name ASC
                """,
                (committee_id_sbe,),
            ).fetchall()
        else:
            candidate_rows = conn.execute(
                """
                SELECT
                    candidate_id,
                    'Candidate ' || candidate_id AS candidate_name
                FROM bulk_committee_candidate_links
                WHERE committee_id_sbe = ?
                  AND candidate_id IS NOT NULL
                GROUP BY candidate_id
                ORDER BY candidate_name ASC
                """,
                (committee_id_sbe,),
            ).fetchall()
        candidate_links = [
            {
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"] or f"Candidate {row['candidate_id']}",
            }
            for row in candidate_rows
        ]

    has_any_data = (
        bool(committee_name)
        or receipts_summary["contribution_count"] > 0
        or expenditures_summary["transaction_count"] > 0
        or bool(candidate_links)
    )
    if not has_any_data:
        return None

    officers: list[dict] = []
    if has_officers:
        officer_rows = conn.execute(
            """
            SELECT first_name, last_name, title, current, city, state
            FROM isbe_officers
            WHERE committee_id = ?
              AND current = TRUE
            ORDER BY title, last_name
            """,
            (committee_id_sbe,),
        ).fetchall()
        officers = [
            {
                "name": " ".join(
                    part for part in [row["first_name"], row["last_name"]] if part
                ).strip() or "Unknown",
                "title": row["title"] or "",
                "city": row["city"] or "",
                "state": row["state"] or "",
            }
            for row in officer_rows
        ]

    filing_history: list[dict] = []
    if has_filed_docs:
        filing_rows = conn.execute(
            """
            SELECT id, doc_name, received_datetime,
                   reporting_period_begin, reporting_period_end
            FROM isbe_filed_docs
            WHERE committee_id = ?
            ORDER BY received_datetime DESC, id DESC
            """,
            (committee_id_sbe,),
        ).fetchall()
        filing_history = [
            {
                "id": row["id"],
                "doc_name": row["doc_name"] or "",
                "received_datetime": row["received_datetime"] or "",
                "period_begin": row["reporting_period_begin"] or "",
                "period_end": row["reporting_period_end"] or "",
            }
            for row in filing_rows
        ]
        # Derive original founding date from earliest Statement of Organization
        d1_dates = [
            f["received_datetime"]
            for f in filing_history
            if f["doc_name"] == "Statement of Organization" and f["received_datetime"]
        ]
        if d1_dates:
            founding_dt = min(d1_dates)
            # Extract date portion (YYYY-MM-DD) from datetime string
            founding_date = str(founding_dt)[:10]
            committee_meta["founding_date"] = founding_date
            # If founding_date differs from creation_date, flag as re-activated
            creation = committee_meta.get("creation_date")
            if creation and str(creation)[:10] != founding_date:
                committee_meta["reactivation_date"] = str(creation)[:10]

    return {
        "committee_id_sbe": committee_id_sbe,
        "committee_name": committee_name or f"Committee {committee_id_sbe}",
        "committee_meta": committee_meta,
        "receipts_summary": receipts_summary,
        "expenditures_summary": expenditures_summary,
        "candidate_links": candidate_links,
        "officers": officers,
        "filing_history": filing_history,
        "top_donors": top_donors,
        "top_payees": top_payees,
    }


@committees_bp.route('/')
def list_committees():
    """List all committees."""
    conn = current_app.get_database()

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')

    committees = Committee.get_all(conn, limit=per_page, offset=offset, sort_by=sort_by, sort_dir=sort_dir)
    total = Committee.count(conn)
    total_pages = (total + per_page - 1) // per_page

    return render_template('committees/list.html',
                           committees=committees,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           sort_by=sort_by,
                           sort_dir=sort_dir)


@committees_bp.route('/<int:committee_id>')
def committee_detail(committee_id):
    """Committee detail page."""
    conn = current_app.get_database()

    committee = Committee.get_by_id(conn, committee_id)
    if not committee:
        abort(404)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    report_sort = request.args.get('report_sort', 'filed_date')
    report_dir = request.args.get('report_dir', 'desc')
    contrib_sort = request.args.get('contrib_sort', 'filed_date')
    contrib_dir = request.args.get('contrib_dir', 'desc')

    # Get reports for this committee
    reports = Report.get_by_committee(
        conn,
        committee_id,
        limit=per_page,
        offset=offset,
        sort_by=report_sort,
        sort_dir=report_dir
    )

    # Get contributions for this committee
    contributions = Contribution.get_by_committee(
        conn,
        committee_id,
        limit=100,
        sort_by=contrib_sort,
        sort_dir=contrib_dir
    )

    # Get total
    total_amount = Contribution.total_by_committee(conn, committee_id)

    # Count reports
    report_count = Report.count_by_committee(conn, committee_id)
    total_pages = (report_count + per_page - 1) // per_page

    return render_template('committees/detail.html',
                           committee=committee,
                           reports=reports,
                           contributions=contributions,
                           total_amount=total_amount,
                           page=page,
                           total_pages=total_pages,
                           report_sort=report_sort,
                           report_dir=report_dir,
                           contrib_sort=contrib_sort,
                           contrib_dir=contrib_dir)


@committees_bp.route('/sbe/<int:committee_id_sbe>')
def committee_detail_by_sbe(committee_id_sbe):
    """Redirect from SBE committee ID to the canonical internal-ID route."""
    conn = current_app.get_database()
    committee = Committee.get_by_sbe_id(conn, committee_id_sbe)
    if not committee:
        profile = _bulk_committee_profile(conn, committee_id_sbe)
        if not profile:
            abort(404)
        return render_template(
            'committees/detail_sbe.html',
            committee_id_sbe=committee_id_sbe,
            profile=profile,
        )
    return redirect(url_for('committees.committee_detail', committee_id=committee.id))
