"""IRS 527 Political Organization routes."""
from flask import Blueprint, render_template, request, current_app, abort

irs527_bp = Blueprint('irs527', __name__)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _scalar(conn, sql, params=(), default=0):
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


@irs527_bp.route('/')
def list_orgs():
    """List 527 organizations with totals and committee match status."""
    conn = current_app.get_database()

    if not _table_exists(conn, "irs527_organizations"):
        return render_template('irs527/list.html', orgs=[], total=0,
                               page=1, total_pages=1, query='')

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    query = request.args.get('q', '').strip()

    where_clause_outer = ""
    where_clause_inner = ""
    params = []
    if query:
        where_clause_outer = "WHERE o.org_name LIKE ?"
        where_clause_inner = "WHERE org_name LIKE ?"
        params.append(f"%{query}%")

    total = _scalar(
        conn,
        f"SELECT COUNT(DISTINCT o.ein) FROM irs527_organizations o {where_clause_outer}",
        params,
        default=0,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Get one row per EIN with latest form_id
    orgs = conn.execute(
        f"""
        SELECT
            o.ein,
            o.org_name,
            o.city,
            o.state,
            o.formation_date,
            COALESCE(r.total_contributions, 0) AS total_contributions,
            COALESCE(r.total_expenditures, 0) AS total_expenditures,
            CASE WHEN cm.ein IS NOT NULL THEN 1 ELSE 0 END AS has_committee_match
        FROM (
            SELECT ein, MAX(form_id) AS max_form_id
            FROM irs527_organizations
            {where_clause_inner}
            GROUP BY ein
        ) latest
        JOIN irs527_organizations o ON o.ein = latest.ein AND o.form_id = latest.max_form_id
        LEFT JOIN (
            SELECT ein, SUM(total_contributions) AS total_contributions,
                   SUM(total_expenditures) AS total_expenditures
            FROM irs527_reports
            GROUP BY ein
        ) r ON r.ein = o.ein
        LEFT JOIN (
            SELECT DISTINCT ein FROM irs527_committee_matches
        ) cm ON cm.ein = o.ein
        ORDER BY COALESCE(r.total_expenditures, 0) DESC, o.org_name ASC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    ).fetchall()

    return render_template('irs527/list.html', orgs=orgs, total=total,
                           page=page, total_pages=total_pages, query=query)


@irs527_bp.route('/<path:ein>')
def org_detail(ein):
    """527 org detail: directors, related orgs, expenditures, matches, financial summary."""
    conn = current_app.get_database()

    if not _table_exists(conn, "irs527_organizations"):
        abort(404)

    org = conn.execute(
        """
        SELECT ein, org_name, address_1, address_2, city, state, zip,
               email, purpose, formation_date, custodian_name, contact_name
        FROM irs527_organizations
        WHERE ein = ?
        ORDER BY form_id DESC
        LIMIT 1
        """,
        (ein,),
    ).fetchone()
    if not org:
        abort(404)

    # Financial summary from reports
    financial = conn.execute(
        """
        SELECT
            COUNT(*) AS report_count,
            COALESCE(SUM(total_contributions), 0) AS total_contributions,
            COALESCE(SUM(total_expenditures), 0) AS total_expenditures,
            MIN(period_start) AS earliest_period,
            MAX(period_end) AS latest_period
        FROM irs527_reports
        WHERE ein = ?
        """,
        (ein,),
    ).fetchone()

    directors = conn.execute(
        """
        SELECT person_name, title, city, state
        FROM irs527_directors
        WHERE ein = ?
        ORDER BY person_name
        """,
        (ein,),
    ).fetchall()

    related_orgs = []
    if _table_exists(conn, "irs527_related_orgs"):
        related_orgs = conn.execute(
            """
            SELECT related_org_name, relationship_type, city, state
            FROM irs527_related_orgs
            WHERE ein = ?
            ORDER BY related_org_name
            """,
            (ein,),
        ).fetchall()

    # IL expenditures
    expenditures = conn.execute(
        """
        SELECT recipient_name, city, state, amount, date, purpose
        FROM irs527_expenditures
        WHERE ein = ?
        ORDER BY amount DESC
        LIMIT 100
        """,
        (ein,),
    ).fetchall()

    # Committee matches
    committee_matches = []
    if _table_exists(conn, "irs527_committee_matches"):
        committee_matches = conn.execute(
            """
            SELECT committee_id_sbe, committee_name, score, method
            FROM irs527_committee_matches
            WHERE ein = ?
            ORDER BY score DESC
            """,
            (ein,),
        ).fetchall()

    # Director-donor matches (keyed by director_name)
    director_donor_matches = {}
    if _table_exists(conn, "irs527_director_donor_matches"):
        ddm_rows = conn.execute(
            """
            SELECT director_name, donor_key, donor_name, score
            FROM irs527_director_donor_matches
            WHERE ein = ?
            ORDER BY score DESC
            """,
            (ein,),
        ).fetchall()
        for row in ddm_rows:
            director_donor_matches.setdefault(row["director_name"], []).append(row)

    # Director-candidate matches (keyed by director_name)
    director_candidate_matches = {}
    if _table_exists(conn, "irs527_director_candidate_matches"):
        dcm_rows = conn.execute(
            """
            SELECT director_name, candidate_id, candidate_name, candidate_source, score
            FROM irs527_director_candidate_matches
            WHERE ein = ?
            ORDER BY score DESC
            """,
            (ein,),
        ).fetchall()
        for row in dcm_rows:
            director_candidate_matches.setdefault(row["director_name"], []).append(row)

    # Contributions to this org
    contributions = []
    if _table_exists(conn, "irs527_contributions"):
        contributions = conn.execute(
            """
            SELECT contributor_name, city, state, amount, date
            FROM irs527_contributions
            WHERE ein = ?
            ORDER BY amount DESC
            LIMIT 100
            """,
            (ein,),
        ).fetchall()

    return render_template('irs527/detail.html',
                           org=org, financial=financial,
                           directors=directors, related_orgs=related_orgs,
                           expenditures=expenditures,
                           contributions=contributions,
                           committee_matches=committee_matches,
                           director_donor_matches=director_donor_matches,
                           director_candidate_matches=director_candidate_matches)


@irs527_bp.route('/dark-money')
def dark_money():
    """Dark money tracker: 527 expenditures flowing to IL committees/candidates."""
    conn = current_app.get_database()

    if not _table_exists(conn, "irs527_expenditure_recipient_matches"):
        return render_template('irs527/dark_money.html', matches=[], total=0,
                               page=1, total_pages=1)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    sort = request.args.get('sort', 'score')
    sort_dir = request.args.get('dir', 'desc')

    valid_sorts = {'score', 'recipient_name', 'org_name', 'matched_name'}
    if sort not in valid_sorts:
        sort = 'score'
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    total = _scalar(
        conn,
        "SELECT COUNT(*) FROM irs527_expenditure_recipient_matches",
        default=0,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    matches = conn.execute(
        f"""
        SELECT
            m.ein, m.org_name, m.recipient_name,
            m.matched_type, m.matched_id, m.matched_name, m.score
        FROM irs527_expenditure_recipient_matches m
        ORDER BY m.{sort} {sort_dir}
        LIMIT ? OFFSET ?
        """,
        (per_page, offset),
    ).fetchall()

    return render_template('irs527/dark_money.html', matches=matches, total=total,
                           page=page, total_pages=total_pages,
                           sort=sort, sort_dir=sort_dir)
