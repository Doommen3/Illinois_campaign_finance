"""IRS 527 Political Organization routes."""
import threading
import time

from flask import Blueprint, render_template, request, current_app, abort
from webapp.utils.time_filter import get_active_period, period_cache_key, period_to_date_window

irs527_bp = Blueprint('irs527', __name__)

_dark_money_stats_cache = {"value": None, "expires_at": 0.0, "key": None}
_dark_money_stats_cache_lock = threading.Lock()


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
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
    if hasattr(row, "keys"):
        first_key = next(iter(row.keys()), None)
        if first_key is None:
            return default
        value = row[first_key]
    else:
        value = row[0] if len(row) else default
    return default if value is None else value


def _resolved_transaction_window() -> tuple[dict, str | None, str | None]:
    period = get_active_period()
    explicit_from = request.args.get("date_from", "", type=str).strip()
    explicit_to = request.args.get("date_to", "", type=str).strip()
    date_from, date_to = period_to_date_window(period, explicit_from, explicit_to)
    return period, date_from, date_to


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


def _get_dark_money_contribution_stats(
    conn,
    *,
    period_key: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    if not cache_enabled:
        ttl_seconds = 0
    else:
        ttl_seconds = max(15, int(current_app.config.get("IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS", 180)))
    now = time.monotonic()
    cache_key = f"{period_key}:{date_from or ''}:{date_to or ''}"

    if cache_enabled:
        with _dark_money_stats_cache_lock:
            if (
                _dark_money_stats_cache.get("value") is not None
                and _dark_money_stats_cache.get("key") == cache_key
                and float(_dark_money_stats_cache.get("expires_at", 0.0)) > now
            ):
                return _dark_money_stats_cache["value"]

    contribution_stats = {"total_amount": 0, "unique_contributors": 0, "row_count": 0, "top_contributors": []}

    contribution_date_clause, contribution_date_params = _text_date_window_clause(
        "date",
        date_from=date_from,
        date_to=date_to,
    )

    if _table_exists(conn, "irs527_contributions"):
        contribution_stats["total_amount"] = float(_scalar(
            conn,
            f"SELECT COALESCE(SUM(amount), 0) FROM irs527_contributions WHERE amount > 0{contribution_date_clause}",
            params=tuple(contribution_date_params),
            default=0,
        ))
        contribution_stats["row_count"] = int(_scalar(
            conn,
            f"SELECT COUNT(*) FROM irs527_contributions WHERE amount > 0{contribution_date_clause}",
            params=tuple(contribution_date_params),
            default=0,
        ))
        contribution_stats["unique_contributors"] = int(_scalar(
            conn,
            f"SELECT COUNT(DISTINCT contributor_name) FROM irs527_contributions WHERE contributor_name IS NOT NULL AND contributor_name != ''{contribution_date_clause}",
            params=tuple(contribution_date_params),
            default=0,
        ))
        contribution_stats["top_contributors"] = conn.execute(
            f"""
            SELECT contributor_name, SUM(amount) AS total_amount, COUNT(*) AS cnt
            FROM irs527_contributions
            WHERE contributor_name IS NOT NULL AND contributor_name != ''
              AND amount > 0
              {contribution_date_clause}
            GROUP BY contributor_name
            ORDER BY total_amount DESC
            LIMIT 10
            """,
            tuple(contribution_date_params),
        ).fetchall()
    elif _table_exists(conn, "irs527_contribution_rollup"):
        contribution_stats["total_amount"] = float(_scalar(
            conn, "SELECT COALESCE(SUM(total_amount), 0) FROM irs527_contribution_rollup", default=0))
        contribution_stats["row_count"] = int(_scalar(
            conn, "SELECT COALESCE(SUM(contribution_count), 0) FROM irs527_contribution_rollup", default=0))
        if _table_exists(conn, "irs527_contributor_rollup"):
            contribution_stats["unique_contributors"] = int(_scalar(
                conn, "SELECT COUNT(*) FROM irs527_contributor_rollup", default=0))
            contribution_stats["top_contributors"] = conn.execute(
                """
                SELECT contributor_name, total_amount, contribution_count AS cnt
                FROM irs527_contributor_rollup
                ORDER BY total_amount DESC
                LIMIT 10
                """
            ).fetchall()

    if cache_enabled:
        with _dark_money_stats_cache_lock:
            _dark_money_stats_cache["value"] = contribution_stats
            _dark_money_stats_cache["expires_at"] = now + float(ttl_seconds)
            _dark_money_stats_cache["key"] = cache_key

    return contribution_stats


@irs527_bp.route('/')
def list_orgs():
    """List 527 organizations with totals and committee match status."""
    conn = current_app.get_database()
    _period, date_from, date_to = _resolved_transaction_window()

    if not _table_exists(conn, "irs527_organizations"):
        return render_template('irs527/list.html', orgs=[], total=0,
                               page=1, total_pages=1, query='')

    page = max(1, min(request.args.get('page', 1, type=int), 5000))
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

    expenditure_join_sql = ""
    expenditure_join_params: list[object] = []
    if _table_exists(conn, "irs527_expenditures"):
        expenditure_date_clause, expenditure_join_params = _text_date_window_clause(
            "date",
            date_from=date_from,
            date_to=date_to,
        )
        expenditure_join_sql = f"""
        LEFT JOIN (
            SELECT ein, COALESCE(SUM(amount), 0) AS total_expenditures
            FROM irs527_expenditures
            WHERE amount > 0
            {expenditure_date_clause}
            GROUP BY ein
        ) ex ON ex.ein = o.ein
        """

    contribution_join_sql = ""
    contribution_join_params: list[object] = []
    if _table_exists(conn, "irs527_contributions"):
        contribution_date_clause, contribution_join_params = _text_date_window_clause(
            "date",
            date_from=date_from,
            date_to=date_to,
        )
        contribution_join_sql = f"""
        LEFT JOIN (
            SELECT ein, COALESCE(SUM(amount), 0) AS total_contributions
            FROM irs527_contributions
            WHERE amount > 0
            {contribution_date_clause}
            GROUP BY ein
        ) ic ON ic.ein = o.ein
        """

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
            COALESCE(r.total_contributions, ic.total_contributions, 0) AS total_contributions,
            COALESCE(r.total_expenditures, ex.total_expenditures, 0) AS total_expenditures,
            COALESCE(ic.total_contributions, rc.received_contributions, 0) AS received_contributions,
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
        {expenditure_join_sql}
        {contribution_join_sql}
        LEFT JOIN (
            SELECT ein, total_amount AS received_contributions
            FROM irs527_contribution_rollup
        ) rc ON rc.ein = o.ein
        LEFT JOIN (
            SELECT DISTINCT ein FROM irs527_committee_matches
        ) cm ON cm.ein = o.ein
        ORDER BY COALESCE(r.total_expenditures, 0) DESC, o.org_name ASC
        LIMIT ? OFFSET ?
        """,
        params + expenditure_join_params + contribution_join_params + [per_page, offset],
    ).fetchall()

    return render_template('irs527/list.html', orgs=orgs, total=total,
                           page=page, total_pages=total_pages, query=query)


@irs527_bp.route('/<path:ein>')
def org_detail(ein):
    """527 org detail: directors, related orgs, expenditures, matches, financial summary."""
    conn = current_app.get_database()
    _period, date_from, date_to = _resolved_transaction_window()

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
    financial_row = conn.execute(
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
    financial = {
        "report_count": int(financial_row["report_count"] or 0) if financial_row else 0,
        "total_contributions": float(financial_row["total_contributions"] or 0) if financial_row else 0.0,
        "total_expenditures": float(financial_row["total_expenditures"] or 0) if financial_row else 0.0,
        "earliest_period": (financial_row["earliest_period"] if financial_row else None),
        "latest_period": (financial_row["latest_period"] if financial_row else None),
    }

    exp_date_clause, exp_date_window_params = _text_date_window_clause(
        "date",
        date_from=date_from,
        date_to=date_to,
    )
    exp_date_params: list[object] = [ein, *exp_date_window_params]

    contrib_date_clause, contrib_date_window_params = _text_date_window_clause(
        "date",
        date_from=date_from,
        date_to=date_to,
    )
    contrib_date_params: list[object] = [ein, *contrib_date_window_params]

    transaction_dates: list[str] = []
    if _table_exists(conn, "irs527_expenditures"):
        exp_totals = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(amount), 0) AS total_amount,
                MIN(date) AS first_date,
                MAX(date) AS last_date
            FROM irs527_expenditures
            WHERE ein = ?
              AND amount > 0
              {exp_date_clause}
            """,
            tuple(exp_date_params),
        ).fetchone()
        if exp_totals:
            financial["total_expenditures"] = float(exp_totals["total_amount"] or 0.0)
            if exp_totals["first_date"]:
                transaction_dates.append(str(exp_totals["first_date"])[:10])
            if exp_totals["last_date"]:
                transaction_dates.append(str(exp_totals["last_date"])[:10])

    if _table_exists(conn, "irs527_contributions"):
        contrib_totals = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(amount), 0) AS total_amount,
                MIN(date) AS first_date,
                MAX(date) AS last_date
            FROM irs527_contributions
            WHERE ein = ?
              AND amount > 0
              {contrib_date_clause}
            """,
            tuple(contrib_date_params),
        ).fetchone()
        if contrib_totals:
            financial["total_contributions"] = float(contrib_totals["total_amount"] or 0.0)
            if contrib_totals["first_date"]:
                transaction_dates.append(str(contrib_totals["first_date"])[:10])
            if contrib_totals["last_date"]:
                transaction_dates.append(str(contrib_totals["last_date"])[:10])

    if transaction_dates:
        financial["earliest_period"] = min(transaction_dates)
        financial["latest_period"] = max(transaction_dates)

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
        f"""
        SELECT recipient_name, city, state, amount, date, purpose
        FROM irs527_expenditures
        WHERE ein = ?
          {exp_date_clause}
        ORDER BY amount DESC
        LIMIT 100
        """,
        tuple(exp_date_params),
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

    # Individual reports timeline (8872 filings)
    reports = []
    if _table_exists(conn, "irs527_reports"):
        reports = conn.execute(
            """
            SELECT
                form_id, period_start, period_end,
                COALESCE(total_contributions, 0) AS total_contributions,
                COALESCE(total_expenditures, 0) AS total_expenditures,
                qtr_indicator, insert_datetime
            FROM irs527_reports
            WHERE ein = ?
            ORDER BY period_end DESC, period_start DESC
            """,
            (ein,),
        ).fetchall()

    # Contributions to this org (with search + aggregates)
    contributions = []
    contribution_total = 0.0
    contribution_count = 0
    top_contributors = []
    contrib_search = request.args.get("contrib_q", "").strip()
    if _table_exists(conn, "irs527_contributions"):
        contrib_search_clause = ""
        contrib_search_params: list[object] = []
        if contrib_search:
            contrib_search_clause = " AND contributor_name LIKE ?"
            contrib_search_params = [f"%{contrib_search}%"]

        contributions = conn.execute(
            f"""
            SELECT contributor_name, city, state, amount, date,
                   contributor_employer, contributor_occupation
            FROM irs527_contributions
            WHERE ein = ?
              {contrib_date_clause}
              {contrib_search_clause}
            ORDER BY amount DESC
            LIMIT 100
            """,
            tuple(contrib_date_params + contrib_search_params),
        ).fetchall()

        agg_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt
            FROM irs527_contributions
            WHERE ein = ? AND amount > 0
              {contrib_date_clause}
            """,
            tuple(contrib_date_params),
        ).fetchone()
        contribution_total = float(agg_row["total"] or 0) if agg_row else 0.0
        contribution_count = int(agg_row["cnt"] or 0) if agg_row else 0

        top_contributors = conn.execute(
            f"""
            SELECT contributor_name,
                   COALESCE(SUM(amount), 0) AS total_amount,
                   COUNT(*) AS contribution_count
            FROM irs527_contributions
            WHERE ein = ? AND contributor_name IS NOT NULL AND TRIM(contributor_name) != ''
              {contrib_date_clause}
            GROUP BY contributor_name
            ORDER BY total_amount DESC
            LIMIT 10
            """,
            tuple(contrib_date_params),
        ).fetchall()

    # Top expenditure recipients
    top_recipients = []
    expenditure_count = 0
    if _table_exists(conn, "irs527_expenditures"):
        exp_agg_row = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM irs527_expenditures
            WHERE ein = ?
              {exp_date_clause}
            """,
            tuple(exp_date_params),
        ).fetchone()
        expenditure_count = int(exp_agg_row["cnt"] or 0) if exp_agg_row else 0

        top_recipients = conn.execute(
            f"""
            SELECT recipient_name,
                   COALESCE(SUM(amount), 0) AS total_amount,
                   COUNT(*) AS expenditure_count
            FROM irs527_expenditures
            WHERE ein = ? AND recipient_name IS NOT NULL AND TRIM(recipient_name) != ''
              {exp_date_clause}
            GROUP BY recipient_name
            ORDER BY total_amount DESC
            LIMIT 10
            """,
            tuple(exp_date_params),
        ).fetchall()

    return render_template('irs527/detail.html',
                           org=org, financial=financial,
                           directors=directors, related_orgs=related_orgs,
                           expenditures=expenditures,
                           contributions=contributions,
                           contribution_total=contribution_total,
                           contribution_count=contribution_count,
                           top_contributors=top_contributors,
                           top_recipients=top_recipients,
                           expenditure_count=expenditure_count,
                           contrib_search=contrib_search,
                           committee_matches=committee_matches,
                           director_donor_matches=director_donor_matches,
                           director_candidate_matches=director_candidate_matches,
                           reports=reports)


@irs527_bp.route('/dark-money')
def dark_money():
    """Dark money tracker: 527 expenditures flowing to IL committees/candidates."""
    conn = current_app.get_database()
    period, date_from, date_to = _resolved_transaction_window()
    period_key = period_cache_key(period)

    if not _table_exists(conn, "irs527_expenditure_recipient_matches"):
        return render_template('irs527/dark_money.html', matches=[], total=0,
                               page=1, total_pages=1, sort='score', sort_dir='desc',
                               contribution_stats={"total_amount": 0, "unique_contributors": 0, "row_count": 0, "top_contributors": []})

    page = max(1, min(request.args.get('page', 1, type=int), 5000))
    per_page = 50
    offset = (page - 1) * per_page
    sort = request.args.get('sort', 'score')
    sort_dir = request.args.get('dir', 'desc')

    valid_sorts = {'score', 'recipient_name', 'org_name', 'matched_name'}
    if sort not in valid_sorts:
        sort = 'score'
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    has_expenditures = _table_exists(conn, "irs527_expenditures")
    filtered_by_date = has_expenditures and bool(date_from or date_to)
    if filtered_by_date:
        date_clause, date_params = _text_date_window_clause(
            "e.date",
            date_from=date_from,
            date_to=date_to,
        )
        total = int(
            _scalar(
                conn,
                f"""
                WITH filtered_matches AS (
                    SELECT m.match_id
                    FROM irs527_expenditure_recipient_matches m
                    JOIN irs527_expenditures e
                      ON e.ein = m.ein
                     AND e.recipient_name = m.recipient_name
                    WHERE 1=1
                    {date_clause}
                    GROUP BY m.match_id
                )
                SELECT COUNT(*) AS count FROM filtered_matches
                """,
                params=tuple(date_params),
                default=0,
            )
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        matches = conn.execute(
            f"""
            WITH filtered_matches AS (
                SELECT
                    m.match_id,
                    m.ein,
                    m.org_name,
                    m.recipient_name,
                    m.matched_type,
                    m.matched_id,
                    m.matched_name,
                    m.score
                FROM irs527_expenditure_recipient_matches m
                JOIN irs527_expenditures e
                  ON e.ein = m.ein
                 AND e.recipient_name = m.recipient_name
                WHERE 1=1
                {date_clause}
                GROUP BY
                    m.match_id,
                    m.ein,
                    m.org_name,
                    m.recipient_name,
                    m.matched_type,
                    m.matched_id,
                    m.matched_name,
                    m.score
            )
            SELECT
                ein,
                org_name,
                recipient_name,
                matched_type,
                matched_id,
                matched_name,
                score
            FROM filtered_matches
            ORDER BY {sort} {sort_dir}
            LIMIT ? OFFSET ?
            """,
            tuple(date_params + [per_page, offset]),
        ).fetchall()
    else:
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

    # Contribution stats
    contribution_stats = _get_dark_money_contribution_stats(
        conn,
        period_key=period_key,
        date_from=date_from,
        date_to=date_to,
    )

    return render_template('irs527/dark_money.html', matches=matches, total=total,
                           page=page, total_pages=total_pages,
                           sort=sort, sort_dir=sort_dir,
                           contribution_stats=contribution_stats)
