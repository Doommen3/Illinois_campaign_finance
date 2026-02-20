"""Committee routes."""
from flask import Blueprint, render_template, request, current_app, abort, redirect, url_for, jsonify

from database.models import Committee, Report, Contribution
from webapp.utils.time_filter import get_active_period, period_to_date_window
from webapp.utils.search_normalize import normalize_search_query

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
    from webapp.utils.time_filter import get_active_period, period_qmark_date_clause
    period = get_active_period()

    has_bulk_committees = _table_exists(conn, "bulk_committees_clean")
    has_bulk_receipts = _table_exists(conn, "bulk_receipts_clean")
    has_bulk_expenditures = _table_exists(conn, "bulk_expenditures_clean")
    has_isbe_receipts = _table_exists(conn, "isbe_condensed_receipts")
    has_isbe_expenditures = _table_exists(conn, "isbe_condensed_expenditures")
    has_candidate_links = _table_exists(conn, "bulk_committee_candidate_links")
    has_candidates = _table_exists(conn, "bulk_candidates_clean")
    has_analytics_agg = _table_exists(conn, "analytics_donor_committee_agg")
    has_officers = _table_exists(conn, "isbe_officers")
    has_isbe_committees = _table_exists(conn, "isbe_committees")
    has_filed_docs = _table_exists(conn, "isbe_filed_docs")

    # Decide which receipt/expenditure source to use:
    # Prefer bulk_*_clean compat views; fall back to isbe_condensed_* tables.
    use_isbe_receipts = not has_bulk_receipts and has_isbe_receipts
    use_isbe_expenditures = not has_bulk_expenditures and has_isbe_expenditures

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

    # --- Receipts query ---
    # Determine table, committee column, and last-name column based on source.
    if use_isbe_receipts:
        rcpt_table = "isbe_condensed_receipts"
        rcpt_cmte_col = "committee_id"
        rcpt_last_col = "last_name"
    elif has_bulk_receipts:
        rcpt_table = "bulk_receipts_clean"
        rcpt_cmte_col = "committee_id_sbe"
        rcpt_last_col = "last_or_business_name"
    else:
        rcpt_table = None
        rcpt_cmte_col = None
        rcpt_last_col = None

    receipts_filter = ""
    receipts_params: list = []
    if rcpt_table:
        receipts_filter = f"WHERE {rcpt_cmte_col} = ?"
        receipts_params = [committee_id_sbe]
        # Archived-row exclusion
        if use_isbe_receipts:
            receipts_filter += " AND archived = FALSE"
        elif _column_exists(conn, rcpt_table, "is_archived"):
            receipts_filter += " AND NOT COALESCE(is_archived::boolean, FALSE)"
        rcpt_clause, rcpt_plist = period_qmark_date_clause("received_date", period)
        if rcpt_clause:
            receipts_filter += rcpt_clause
            receipts_params.extend(rcpt_plist)

    receipts_summary = {
        "contribution_count": 0,
        "total_amount": 0.0,
        "first_date": None,
        "last_date": None,
    }
    top_donors: list[dict] = []
    if rcpt_table:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS contribution_count,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                MIN(received_date) AS first_date,
                MAX(received_date) AS last_date
            FROM {rcpt_table}
            {receipts_filter}
            """,
            tuple(receipts_params),
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
                         AND COALESCE(NULLIF(TRIM({rcpt_last_col}), ''), '') != '' THEN ' '
                        ELSE ''
                      END
                    || COALESCE(NULLIF(TRIM({rcpt_last_col}), ''), '')
                ) AS donor_name,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                COUNT(*) AS contribution_count
            FROM {rcpt_table}
            {receipts_filter}
            GROUP BY donor_name
            ORDER BY total_amount DESC, contribution_count DESC, donor_name ASC
            LIMIT 20
            """,
            tuple(receipts_params),
        ).fetchall()
        top_donors = [
            {
                "donor_name": (row["donor_name"] or "").strip() or "Unknown Donor",
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
            }
            for row in top_donor_rows
        ]

    # --- Expenditures query ---
    if use_isbe_expenditures:
        exp_table = "isbe_condensed_expenditures"
        exp_cmte_col = "committee_id"
        exp_last_col = "last_name"
        exp_first_col = "first_name"
    elif has_bulk_expenditures:
        exp_table = "bulk_expenditures_clean"
        exp_cmte_col = "committee_id_sbe"
        exp_last_col = "payee_last_or_business_name"
        exp_first_col = "payee_first_name"
    else:
        exp_table = None
        exp_cmte_col = None
        exp_last_col = None
        exp_first_col = None

    expenditures_filter = ""
    expenditures_params: list = []
    if exp_table:
        expenditures_filter = f"WHERE {exp_cmte_col} = ?"
        expenditures_params = [committee_id_sbe]
        if use_isbe_expenditures:
            expenditures_filter += " AND archived = FALSE"
        elif _column_exists(conn, exp_table, "is_archived"):
            expenditures_filter += " AND NOT COALESCE(is_archived::boolean, FALSE)"
        exp_clause, exp_plist = period_qmark_date_clause("expended_date", period)
        if exp_clause:
            expenditures_filter += exp_clause
            expenditures_params.extend(exp_plist)

    expenditures_summary = {
        "transaction_count": 0,
        "total_amount": 0.0,
        "first_date": None,
        "last_date": None,
    }
    top_payees: list[dict] = []
    if exp_table:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS transaction_count,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                MIN(expended_date) AS first_date,
                MAX(expended_date) AS last_date
            FROM {exp_table}
            {expenditures_filter}
            """,
            tuple(expenditures_params),
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
                    COALESCE(NULLIF(TRIM({exp_first_col}), ''), '')
                    || CASE
                        WHEN COALESCE(NULLIF(TRIM({exp_first_col}), ''), '') != ''
                         AND COALESCE(NULLIF(TRIM({exp_last_col}), ''), '') != '' THEN ' '
                        ELSE ''
                      END
                    || COALESCE(NULLIF(TRIM({exp_last_col}), ''), '')
                ) AS payee_name,
                COALESCE(SUM(amount), 0.0) AS total_amount,
                COUNT(*) AS transaction_count
            FROM {exp_table}
            {expenditures_filter}
            GROUP BY payee_name
            ORDER BY total_amount DESC, transaction_count DESC, payee_name ASC
            LIMIT 20
            """,
            tuple(expenditures_params),
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
    # Try bulk_committee_candidate_links first, then ISBE compat link table
    has_isbe_compat_links = (
        not has_candidate_links
        and _table_exists(conn, "isbe_bulk_cmte_candidate_links_clean_compat")
    )
    has_isbe_candidates_tbl = _table_exists(conn, "isbe_candidates")

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
    elif has_isbe_compat_links:
        if has_isbe_candidates_tbl:
            candidate_rows = conn.execute(
                """
                SELECT
                    l.candidate_id,
                    COALESCE(
                        MAX(TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, ''))),
                        'Candidate ' || l.candidate_id
                    ) AS candidate_name
                FROM isbe_bulk_cmte_candidate_links_clean_compat l
                LEFT JOIN isbe_candidates c ON c.id = l.candidate_id
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
                FROM isbe_bulk_cmte_candidate_links_clean_compat
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
        filing_history = []
        for row in filing_rows:
            rdt = row["received_datetime"]
            if rdt:
                filed_date = rdt.strftime("%Y-%m-%d") if hasattr(rdt, "strftime") else str(rdt)[:10]
            else:
                filed_date = ""
            filing_history.append({
                "id": row["id"],
                "doc_name": row["doc_name"] or "",
                "received_datetime": str(rdt) if rdt else "",
                "filed_date": filed_date,
                "period_begin": row["reporting_period_begin"] or "",
                "period_end": row["reporting_period_end"] or "",
            })
        # Derive original founding date from earliest Statement of Organization
        d1_dates = [
            f["filed_date"]
            for f in filing_history
            if f["doc_name"] == "Statement of Organization" and f["filed_date"]
        ]
        if d1_dates:
            founding_date = min(d1_dates)
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
    """List all committees from ISBE data."""
    conn = current_app.get_database()

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    raw_query = request.args.get('q', '').strip()
    query = normalize_search_query(raw_query) or raw_query.strip()

    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')

    sort_map = {
        "name": "c.name",
        "total_contributions": "total_contributions",
    }
    sort_field = sort_map.get(sort_by, "c.name")
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

    has_isbe = _table_exists(conn, "isbe_committees")
    has_money = has_isbe and _table_exists(conn, "isbe_committee_money")

    if has_isbe:
        where_clause = ""
        params: list = []
        if query:
            where_clause = "WHERE c.name LIKE ?"
            params.append(f"%{query}%")

        count_row = conn.execute(
            f"SELECT COUNT(*) AS count FROM isbe_committees c {where_clause}",
            tuple(params),
        ).fetchone()
        total = count_row["count"] if count_row else 0

        if has_money:
            rows = conn.execute(
                f"""
                SELECT c.id, c.name, c.type, c.party, c.active,
                       COALESCE(m.total, 0) AS total_contributions
                FROM isbe_committees c
                LEFT JOIN isbe_committee_money m ON m.committee_id = c.id
                {where_clause}
                ORDER BY {sort_field} {direction}, c.id ASC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [per_page, offset]),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT c.id, c.name, c.type, c.party, c.active,
                       0 AS total_contributions
                FROM isbe_committees c
                {where_clause}
                ORDER BY {sort_field} {direction}, c.id ASC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [per_page, offset]),
            ).fetchall()
    else:
        # Fallback to legacy committees table
        period = get_active_period()
        date_from, date_to = period_to_date_window(period)
        committees_legacy = Committee.get_all(
            conn,
            limit=per_page,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            transaction_date_from=date_from,
            transaction_date_to=date_to,
        )
        total = Committee.count(
            conn,
            transaction_date_from=date_from,
            transaction_date_to=date_to,
        )
        total_pages = (total + per_page - 1) // per_page
        return render_template('committees/list.html',
                               committees=committees_legacy,
                               page=page,
                               total_pages=total_pages,
                               total=total,
                               sort_by=sort_by,
                               sort_dir=sort_dir,
                               query=query,
                               use_isbe=False)

    committees = [
        {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"] or "",
            "party": row["party"] or "",
            "active": row["active"],
            "total_contributions": row["total_contributions"] or 0,
        }
        for row in rows
    ]

    total_pages = (total + per_page - 1) // per_page

    return render_template('committees/list.html',
                           committees=committees,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           sort_by=sort_by,
                           sort_dir=sort_dir,
                           query=query,
                           use_isbe=True)


@committees_bp.route('/suggest')
def suggest_committees():
    """Return up to 10 committee name suggestions for autocomplete."""
    conn = current_app.get_database()
    raw = request.args.get('q', '').strip()
    q = normalize_search_query(raw)
    if len(q) < 2 or not _table_exists(conn, "isbe_committees"):
        return jsonify([])
    rows = conn.execute(
        """
        SELECT id AS value, name AS label
        FROM isbe_committees
        WHERE name LIKE ?
        ORDER BY name
        LIMIT 10
        """,
        (f"%{q}%",),
    ).fetchall()
    return jsonify([{"label": r["label"], "value": r["value"]} for r in rows])


@committees_bp.route('/<int:committee_id>')
def committee_detail(committee_id):
    """Committee detail page."""
    conn = current_app.get_database()
    period = get_active_period()
    date_from, date_to = period_to_date_window(period)

    committee = Committee.get_by_id(conn, committee_id)
    if not committee:
        # Fallback: treat committee_id as an ISBE SBE committee ID
        profile = _bulk_committee_profile(conn, committee_id)
        if profile:
            return render_template(
                'committees/detail_sbe.html',
                committee_id_sbe=committee_id,
                profile=profile,
            )
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
        filed_date_from=date_from,
        filed_date_to=date_to,
        sort_by=report_sort,
        sort_dir=report_dir
    )

    # Get contributions for this committee
    contributions = Contribution.get_by_committee(
        conn,
        committee_id,
        limit=100,
        transaction_date_from=date_from,
        transaction_date_to=date_to,
        sort_by=contrib_sort,
        sort_dir=contrib_dir
    )

    # Get total
    total_amount = Contribution.total_by_committee(
        conn,
        committee_id,
        transaction_date_from=date_from,
        transaction_date_to=date_to,
    )

    # Count reports
    report_count = Report.count_by_committee(
        conn,
        committee_id,
        filed_date_from=date_from,
        filed_date_to=date_to,
    )
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


@committees_bp.route('/filing/<int:filed_doc_id>')
def filing_detail(filed_doc_id):
    """Filing document detail page showing metadata, receipts, and expenditures."""
    conn = current_app.get_database()

    if not _table_exists(conn, "isbe_filed_docs"):
        abort(404)

    doc = conn.execute(
        """
        SELECT d.id, d.committee_id, d.filed_doc_type, d.doc_name,
               d.amended, d.comment, d.pages, d.election_type, d.election_year,
               d.reporting_period_begin, d.reporting_period_end,
               d.received_at, d.received_datetime,
               d.signer_last_name, d.signer_first_name,
               d.submitter_last_name, d.submitter_first_name,
               d.archived, d.clarification,
               c.name AS committee_name
        FROM isbe_filed_docs d
        LEFT JOIN isbe_committees c ON c.id = d.committee_id
        WHERE d.id = ?
        LIMIT 1
        """,
        (filed_doc_id,),
    ).fetchone()
    if not doc:
        abort(404)

    filed_date = ""
    rdt = doc["received_datetime"]
    if rdt:
        filed_date = rdt.strftime("%Y-%m-%d") if hasattr(rdt, "strftime") else str(rdt)[:10]

    # Determine receipt/expenditure source tables
    has_isbe_receipts = _table_exists(conn, "isbe_condensed_receipts")
    has_bulk_receipts = _table_exists(conn, "bulk_receipts_clean")
    has_isbe_expenditures = _table_exists(conn, "isbe_condensed_expenditures")
    has_bulk_expenditures = _table_exists(conn, "bulk_expenditures_clean")

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    # --- Receipts for this filing ---
    receipts = []
    receipts_total = 0.0
    receipts_count = 0
    if has_isbe_receipts or has_bulk_receipts:
        if has_isbe_receipts:
            rcpt_tbl = "isbe_condensed_receipts"
            rcpt_last = "last_name"
        else:
            rcpt_tbl = "bulk_receipts_clean"
            rcpt_last = "last_or_business_name"

        agg = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0.0) AS total
            FROM {rcpt_tbl}
            WHERE filed_doc_id = ?
            """,
            (filed_doc_id,),
        ).fetchone()
        if agg:
            receipts_count = int(agg["cnt"] or 0)
            receipts_total = float(agg["total"] or 0.0)

        receipt_rows = conn.execute(
            f"""
            SELECT
                TRIM(
                    COALESCE(NULLIF(TRIM(first_name), ''), '')
                    || CASE
                        WHEN COALESCE(NULLIF(TRIM(first_name), ''), '') != ''
                         AND COALESCE(NULLIF(TRIM({rcpt_last}), ''), '') != '' THEN ' '
                        ELSE ''
                      END
                    || COALESCE(NULLIF(TRIM({rcpt_last}), ''), '')
                ) AS donor_name,
                amount,
                received_date,
                description
            FROM {rcpt_tbl}
            WHERE filed_doc_id = ?
            ORDER BY amount DESC
            LIMIT ? OFFSET ?
            """,
            (filed_doc_id, per_page, offset),
        ).fetchall()
        receipts = [
            {
                "donor_name": (r["donor_name"] or "").strip() or "Unknown",
                "amount": float(r["amount"] or 0),
                "received_date": r["received_date"] or "",
                "description": r["description"] or "",
            }
            for r in receipt_rows
        ]

    # --- Expenditures for this filing ---
    expenditures = []
    expenditures_total = 0.0
    expenditures_count = 0
    if has_isbe_expenditures or has_bulk_expenditures:
        if has_isbe_expenditures:
            exp_tbl = "isbe_condensed_expenditures"
            exp_last = "last_name"
            exp_first = "first_name"
        else:
            exp_tbl = "bulk_expenditures_clean"
            exp_last = "payee_last_or_business_name"
            exp_first = "payee_first_name"

        agg = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0.0) AS total
            FROM {exp_tbl}
            WHERE filed_doc_id = ?
            """,
            (filed_doc_id,),
        ).fetchone()
        if agg:
            expenditures_count = int(agg["cnt"] or 0)
            expenditures_total = float(agg["total"] or 0.0)

        exp_rows = conn.execute(
            f"""
            SELECT
                TRIM(
                    COALESCE(NULLIF(TRIM({exp_first}), ''), '')
                    || CASE
                        WHEN COALESCE(NULLIF(TRIM({exp_first}), ''), '') != ''
                         AND COALESCE(NULLIF(TRIM({exp_last}), ''), '') != '' THEN ' '
                        ELSE ''
                      END
                    || COALESCE(NULLIF(TRIM({exp_last}), ''), '')
                ) AS payee_name,
                amount,
                expended_date,
                purpose
            FROM {exp_tbl}
            WHERE filed_doc_id = ?
            ORDER BY amount DESC
            LIMIT ? OFFSET ?
            """,
            (filed_doc_id, per_page, offset),
        ).fetchall()
        expenditures = [
            {
                "payee_name": (r["payee_name"] or "").strip() or "Unknown",
                "amount": float(r["amount"] or 0),
                "expended_date": r["expended_date"] or "",
                "purpose": r["purpose"] or "",
            }
            for r in exp_rows
        ]

    total_items = max(receipts_count, expenditures_count)
    total_pages = (total_items + per_page - 1) // per_page if total_items > 0 else 1

    return render_template(
        'committees/filing_detail.html',
        doc=doc,
        filed_date=filed_date,
        receipts=receipts,
        receipts_count=receipts_count,
        receipts_total=receipts_total,
        expenditures=expenditures,
        expenditures_count=expenditures_count,
        expenditures_total=expenditures_total,
        page=page,
        total_pages=total_pages,
    )
