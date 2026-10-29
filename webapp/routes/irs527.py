"""IRS 527 Political Organization routes."""
import threading
import time

from flask import Blueprint, jsonify, render_template, request, current_app, abort
from webapp.cache_backend import RouteCache
from database.name_normalize import name_predicates, normalized_substring_sql
from webapp.utils.text_dates import text_date_window_clause as _text_date_window_clause
from webapp.utils.time_scope import (
    TimeScope, current_cycle, cycle_window, resolve_time_scope, scope_cache_key, scope_params,
)

irs527_bp = Blueprint('irs527', __name__)

_dark_money_stats_cache = RouteCache("irs527_dark_money_stats")


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


def _resolved_transaction_window() -> tuple[TimeScope, str | None, str | None]:
    scope = resolve_time_scope(default="cycle:current", axis="transaction")
    date_from = scope.start.isoformat() if scope.start else None
    date_to = scope.end.isoformat() if scope.end else None
    return scope, date_from, date_to


def _scope_context(scope: TimeScope) -> dict:
    return {
        "time_scope": scope,
        "scope_default_key": f"cycle:{current_cycle()}",
        "time_scope_source_label": "IRS",
        "scope_query": scope_params(scope),
    }


# ---------------------------------------------------------------------------------------------
# An org's contributors / expenditure recipients (plan C4, decision J-P1). One builder, two
# consumers: the detail page's Top-10 tables are the head of /527/<ein>/contributors and
# /527/<ein>/recipients. One row per exact name, NULL and blank names left out; the window is the
# page's, on the text ``date`` column (ISO or compact, see _text_date_window_clause).
# ---------------------------------------------------------------------------------------------

_LEDGER_PAGE_SIZE = 50
_LEDGER_SORTS = {"total": "desc", "count": "desc", "name": "asc", "last": "desc"}  # sort -> direction it opens with
_LEDGERS = {"contributors": ("irs527_contributions", "contributor_name"),
            "recipients": ("irs527_expenditures", "recipient_name")}


def _ledger_source(conn, ein: str, kind: str, scope: TimeScope, q: str | None):
    """(grouped SELECT, params) for ``org_ledger``; None when the table is missing. ``q`` is a bound,
    case-insensitive literal substring of the name (``%`` and ``_`` are not wildcards)."""
    table, name = _LEDGERS[kind]
    if not _table_exists(conn, table):
        return None
    window, params = _text_date_window_clause(
        "date",
        date_from=scope.start.isoformat() if scope.start else None,
        date_to=scope.end.isoformat() if scope.end else None,
    )
    needle = (q or "").strip()
    match = f" AND STRPOS(LOWER({name}), LOWER(?)) > 0" if needle else ""
    return f"""
        SELECT {name} AS name, COALESCE(SUM(amount), 0) AS total_amount, COUNT(*) AS record_count,
               MIN(date) AS first_date, MAX(date) AS last_date
        FROM {table}
        WHERE ein = ? AND {name} IS NOT NULL AND TRIM({name}) != ''{window}{match}
        GROUP BY {name}
    """, [ein, *params, *([needle] if needle else [])]


def _display_day(value) -> str:
    """A text date shown as YYYY-MM-DD (the IRS files carry compact YYYYMMDD dates as well as ISO ones)."""
    text = str(value or "").strip()
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else text[:10]


def org_ledger(conn, ein: str, kind: str, scope: TimeScope, *, limit: int = 50, offset: int = 0,
               sort_by: str = "total", sort_dir: str = "desc", q: str | None = None) -> list[dict]:
    """One page of the org's contributors (``kind="contributors"``) or expenditure recipients
    (``"recipients"``) in ``scope``: name, total_amount, record_count, first_date, last_date.
    ``sort_by`` in total | count | name | last (anything else: total); ties break on total, then
    name, so pages never overlap or skip."""
    source = _ledger_source(conn, ein, kind, scope, q)
    if source is None:
        return []
    sql, params = source
    order = {"total": "total_amount", "count": "record_count", "name": "LOWER(name)", "last": "last_date"}.get(
        sort_by, "total_amount"
    )
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    rows = conn.execute(
        f"SELECT * FROM ({sql}) ledger "
        f"ORDER BY {order} {direction} NULLS LAST, total_amount DESC, name ASC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    ).fetchall()
    return [
        {
            "name": row["name"],
            "total_amount": float(row["total_amount"] or 0.0),
            "record_count": int(row["record_count"] or 0),
            "first_date": _display_day(row["first_date"]),
            "last_date": _display_day(row["last_date"]),
        }
        for row in rows
    ]


def org_ledger_summary(conn, ein: str, kind: str, scope: TimeScope, *, q: str | None = None) -> tuple[int, float]:
    """(unique names, summed total) of what ``org_ledger`` pages through for the same arguments."""
    source = _ledger_source(conn, ein, kind, scope, q)
    if source is None:
        return 0, 0.0
    sql, params = source
    row = conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(total_amount), 0) AS total FROM ({sql}) ledger", tuple(params)
    ).fetchone()
    return (int(row["n"] or 0), float(row["total"] or 0.0)) if row else (0, 0.0)


def _latest_org(conn, ein: str):
    """The org's most recent registration row (highest form_id_seq), or None."""
    if not _table_exists(conn, "irs527_organizations"):
        return None
    return conn.execute(
        """
        SELECT ein, org_name, address_1, address_2, city, state, zip,
               email, purpose, formation_date, custodian_name, contact_name
        FROM irs527_organizations
        WHERE ein = ?
        ORDER BY
            COALESCE(form_id_seq, 0) DESC,
            COALESCE(material_change_date, '') DESC,
            COALESCE(insert_datetime, '') DESC,
            COALESCE(form_id, 0) DESC
        LIMIT 1
        """,
        (ein,),
    ).fetchone()


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
        cached = _dark_money_stats_cache.get(cache_key)
        if cached is not None:
            return cached

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
        _dark_money_stats_cache.set(cache_key, contribution_stats, ttl_seconds)

    return contribution_stats


def _iso_text_date_sql(column: str) -> str:
    """A TEXT date column as ISO YYYY-MM-DD: an ISO (YYYY-MM-DD...) or compact (YYYYMMDD) value, the two shapes
    text_date_window_clause reads; NULL for anything else, so a malformed value ("1000", "995") cannot win a MIN / MAX
    over the text (MT-017, data dictionary A18)."""
    return (
        f"CASE WHEN {column} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' THEN SUBSTR({column}, 1, 10) "
        f"WHEN {column} ~ '^[0-9]{{8}}$' "
        f"THEN SUBSTR({column}, 1, 4) || '-' || SUBSTR({column}, 5, 2) || '-' || SUBSTR({column}, 7, 2) END"
    )


@irs527_bp.route('/')
def list_orgs():
    """List 527 organizations with totals and committee match status."""
    conn = current_app.get_database()
    scope, date_from, date_to = _resolved_transaction_window()

    if not _table_exists(conn, "irs527_organizations"):
        return render_template('irs527/list.html', orgs=[], total=0,
                               page=1, total_pages=1, query='', **_scope_context(scope))

    page = max(1, min(request.args.get('page', 1, type=int), 5000))
    per_page = 50
    offset = (page - 1) * per_page
    query = request.args.get('q', '').strip()

    where_clause = ""
    params = []
    if query:
        # By name token, else the one substring fallback when no organization has the tokens (database/name_normalize.py).
        variants = name_predicates("org_name", query) or [("FALSE", [])]
        for name_sql, name_params in variants:
            if conn.execute(
                f"SELECT 1 FROM irs527_organizations WHERE {name_sql} LIMIT 1", tuple(name_params)
            ).fetchone():
                break
        where_clause = f"WHERE {name_sql}"
        params.extend(name_params)

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

    # A bounded scope must not fall back to all-time report totals (irs527_reports has no dates).
    if date_from or date_to:
        total_contributions_sql = "COALESCE(ic.total_contributions, 0)"
        total_expenditures_sql = "COALESCE(ex.total_expenditures, 0)"
        received_contributions_sql = "COALESCE(ic.total_contributions, 0)"
        order_by_sql = "COALESCE(ex.total_expenditures, 0) DESC, o.org_name ASC"
    else:
        total_contributions_sql = "COALESCE(r.total_contributions, ic.total_contributions, 0)"
        total_expenditures_sql = "COALESCE(r.total_expenditures, ex.total_expenditures, 0)"
        received_contributions_sql = "COALESCE(ic.total_contributions, rc.received_contributions, 0)"
        order_by_sql = "COALESCE(r.total_expenditures, 0) DESC, o.org_name ASC"

    latest_orgs_cte = f"""
    WITH ranked_orgs AS (
        SELECT
            ein,
            org_name,
            city,
            state,
            formation_date,
            form_id,
            form_id_seq,
            material_change_date,
            insert_datetime,
            ROW_NUMBER() OVER (
                PARTITION BY ein
                ORDER BY
                    COALESCE(form_id_seq, 0) DESC,
                    COALESCE(material_change_date, '') DESC,
                    COALESCE(insert_datetime, '') DESC,
                    COALESCE(form_id, 0) DESC
            ) AS rn
        FROM irs527_organizations
        {where_clause}
    ),
    latest_orgs AS (
        SELECT ein, org_name, city, state, formation_date
        FROM ranked_orgs
        WHERE rn = 1
    )
    """

    total = _scalar(
        conn,
        f"""
        {latest_orgs_cte}
        SELECT COUNT(*) FROM latest_orgs
        """,
        params,
        default=0,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Get one latest row per EIN (form_id_seq is the record sequence key).
    orgs = conn.execute(
        f"""
        {latest_orgs_cte}
        SELECT
            o.ein,
            o.org_name,
            o.city,
            o.state,
            o.formation_date,
            {total_contributions_sql} AS total_contributions,
            {total_expenditures_sql} AS total_expenditures,
            {received_contributions_sql} AS received_contributions,
            CASE WHEN cm.ein IS NOT NULL THEN 1 ELSE 0 END AS has_committee_match
        FROM latest_orgs o
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
        ORDER BY {order_by_sql}
        LIMIT ? OFFSET ?
        """,
        params + expenditure_join_params + contribution_join_params + [per_page, offset],
    ).fetchall()

    return render_template('irs527/list.html', orgs=orgs, total=total,
                           page=page, total_pages=total_pages, query=query,
                           **_scope_context(scope))


@irs527_bp.route('/suggest')
def suggest():
    """Return up to 10 org name suggestions for autocomplete."""
    conn = current_app.get_database()
    q = request.args.get('q', '').strip()
    if len(q) < 2 or not _table_exists(conn, "irs527_organizations"):
        return jsonify([])
    rows = []
    for where, params in name_predicates("org_name", q):
        rows = conn.execute(
            f"SELECT DISTINCT org_name AS label, ein AS value FROM irs527_organizations WHERE {where} "
            "ORDER BY org_name LIMIT 10",
            tuple(params),
        ).fetchall()
        if rows:
            break
    return jsonify([{"label": r["label"], "value": r["value"]} for r in rows])


@irs527_bp.route('/<path:ein>')
def org_detail(ein):
    """527 org detail: directors, related orgs, expenditures, matches, financial summary."""
    conn = current_app.get_database()
    scope = resolve_time_scope(default="all", axis="transaction")
    date_from = scope.start.isoformat() if scope.start else None
    date_to = scope.end.isoformat() if scope.end else None

    org = _latest_org(conn, ein)
    if not org:
        abort(404)

    # Financial summary from reports: "Reports Filed" counts the reports whose period ends in the
    # window (undated ones only when unbounded); Filing History below keeps every report.
    report_window_clause, report_window_params = _text_date_window_clause(
        "period_end",
        date_from=date_from,
        date_to=date_to,
    )
    financial_row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS report_count,
            COALESCE(SUM(total_contributions), 0) AS total_contributions,
            COALESCE(SUM(total_expenditures), 0) AS total_expenditures,
            MIN({_iso_text_date_sql("period_start")}) AS earliest_period,
            MAX({_iso_text_date_sql("period_end")}) AS latest_period
        FROM irs527_reports
        WHERE ein = ?{report_window_clause}
        """,
        (ein, *report_window_params),
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

    # First / last transaction dates override the reports' period; only ISO or compact dates count (A11 / A18).
    transaction_dates: list[str] = []
    if _table_exists(conn, "irs527_expenditures"):
        exp_totals = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(amount), 0) AS total_amount,
                MIN({_iso_text_date_sql("date")}) AS first_date,
                MAX({_iso_text_date_sql("date")}) AS last_date
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
                MIN({_iso_text_date_sql("date")}) AS first_date,
                MAX({_iso_text_date_sql("date")}) AS last_date
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
    contrib_search = request.args.get("contrib_q", "").strip()
    if _table_exists(conn, "irs527_contributions"):
        contrib_search_clause = ""
        contrib_search_params: list[object] = []
        if contrib_search:
            # Transactional table: the normalized substring (decision P2-2), not the token predicate.
            predicate = normalized_substring_sql("contributor_name", contrib_search)
            contrib_search_clause = f" AND {predicate[0]}" if predicate else " AND FALSE"
            contrib_search_params = list(predicate[1]) if predicate else []

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

    # Top-10 tables = the head of /contributors and /recipients; "See all N" counts the names in the window.
    top_contributors = org_ledger(conn, ein, "contributors", scope, limit=10)
    contributor_count = (len(top_contributors) if len(top_contributors) < 10
                         else org_ledger_summary(conn, ein, "contributors", scope)[0])

    # Current-cycle subtotal shown beside the (default all-time) contributions total
    cycle_start, cycle_end = cycle_window(current_cycle())
    cycle_clause, cycle_clause_params = _text_date_window_clause(
        "date",
        date_from=cycle_start.isoformat(),
        date_to=cycle_end.isoformat(),
    )
    cycle_total = 0.0
    if _table_exists(conn, "irs527_contributions"):
        cycle_total = float(_scalar(
            conn,
            f"""
            SELECT COALESCE(SUM(amount), 0)
            FROM irs527_contributions
            WHERE ein = ? AND amount > 0
              {cycle_clause}
            """,
            (ein, *cycle_clause_params),
            default=0.0,
        ))

    # Top expenditure recipients
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

    top_recipients = org_ledger(conn, ein, "recipients", scope, limit=10)
    recipient_count = (len(top_recipients) if len(top_recipients) < 10
                       else org_ledger_summary(conn, ein, "recipients", scope)[0])

    return render_template('irs527/detail.html',
                           org=org, financial=financial,
                           directors=directors, related_orgs=related_orgs,
                           expenditures=expenditures,
                           contributions=contributions,
                           contribution_total=contribution_total,
                           contribution_count=contribution_count,
                           top_contributors=top_contributors,
                           contributor_count=contributor_count,
                           top_recipients=top_recipients,
                           recipient_count=recipient_count,
                           expenditure_count=expenditure_count,
                           contrib_search=contrib_search,
                           committee_matches=committee_matches,
                           director_donor_matches=director_donor_matches,
                           director_candidate_matches=director_candidate_matches,
                           reports=reports,
                           time_scope=scope,
                           scope_default_key="all",
                           time_scope_source_label="IRS",
                           scope_query=scope_params(scope),
                           cycle_total=cycle_total)


@irs527_bp.route('/<path:ein>/contributors')
def org_contributors(ein):
    """Every contributor of the org in the page window (one row per contributor name)."""
    return _ledger_page(ein, "contributors")


@irs527_bp.route('/<path:ein>/recipients')
def org_recipients(ein):
    """Every expenditure recipient of the org in the page window (one row per recipient name)."""
    return _ledger_page(ein, "recipients")


def _ledger_page(ein: str, kind: str):
    conn = current_app.get_database()
    org = _latest_org(conn, ein)
    if not org:
        abort(404)
    scope = resolve_time_scope(default="all", axis="transaction")
    q = request.args.get("q", "").strip()
    sort_by = request.args.get("sort", "total")
    if sort_by not in _LEDGER_SORTS:
        sort_by = "total"
    sort_dir = request.args.get("dir", "")
    if sort_dir not in ("asc", "desc"):
        sort_dir = _LEDGER_SORTS[sort_by]
    count, total = org_ledger_summary(conn, ein, kind, scope, q=q)
    total_pages = max(1, (count + _LEDGER_PAGE_SIZE - 1) // _LEDGER_PAGE_SIZE)
    page = min(max(request.args.get("page", 1, type=int), 1), total_pages)
    rows = org_ledger(conn, ein, kind, scope, limit=_LEDGER_PAGE_SIZE, offset=(page - 1) * _LEDGER_PAGE_SIZE,
                      sort_by=sort_by, sort_dir=sort_dir, q=q)
    return render_template(
        f"irs527/{kind}.html",
        org=org,
        rows=rows,
        count=count,
        total=total,
        q=q,
        sort_by=sort_by,
        sort_dir=sort_dir,
        ledger_sorts=_LEDGER_SORTS,
        page=page,
        total_pages=total_pages,
        time_scope=scope,
        scope_default_key="all",
        time_scope_source_label="IRS",
        scope_query=scope_params(scope),
    )


@irs527_bp.route('/dark-money')
def dark_money():
    """Dark money tracker: 527 expenditures flowing to IL committees/candidates."""
    conn = current_app.get_database()
    scope, date_from, date_to = _resolved_transaction_window()

    if not _table_exists(conn, "irs527_expenditure_recipient_matches"):
        return render_template('irs527/dark_money.html', matches=[], total=0,
                               page=1, total_pages=1, sort='score', sort_dir='desc',
                               contribution_stats={"total_amount": 0, "unique_contributors": 0, "row_count": 0, "top_contributors": []},
                               **_scope_context(scope))

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
        period_key=scope_cache_key(scope),
        date_from=date_from,
        date_to=date_to,
    )

    return render_template('irs527/dark_money.html', matches=matches, total=total,
                           page=page, total_pages=total_pages,
                           sort=sort, sort_dir=sort_dir,
                           contribution_stats=contribution_stats,
                           **_scope_context(scope))
