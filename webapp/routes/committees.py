"""Committee routes."""
from dataclasses import replace

from flask import Blueprint, render_template, request, current_app, abort, redirect, url_for, jsonify

from database.committee_families import accepted_family_map, family_members
from database.models import Committee, Report, Contribution, Donor, receipts_filter_sql
from webapp.utils.data_through import latest_data_date
from webapp.utils.time_scope import (
    TimeScope, current_cycle, cycle_window, resolve_time_scope, scope_params, scope_where,
)
from database.name_normalize import name_match_sql, name_predicates, substring_fallback_sql

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


def _has_columns(conn, table_name: str, columns: tuple[str, ...]) -> bool:
    """``table_name`` exists and has every one of ``columns`` (one catalog read for all of them)."""
    if not _table_exists(conn, table_name):
        return False
    present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    return set(columns) <= present


def _in_isbe_committees(conn, committee_id: int) -> bool:
    """True when ``isbe_committees`` has this id: its ISBE profile then wins over a legacy page (J-S8)."""
    if not _table_exists(conn, "isbe_committees"):
        return False
    return conn.execute(
        "SELECT 1 FROM isbe_committees WHERE id = ? LIMIT 1", (committee_id,)
    ).fetchone() is not None


def _isbe_committee_name(conn, committee_id_sbe: int) -> str | None:
    """Name of the ISBE committee with this id (``isbe_committees``, else the bulk loader's
    ``bulk_committees_clean``); None when neither has it. The legacy scraped ``committees`` table is
    never read, so a legacy row sharing the id cannot shadow the ISBE committee (J-S8)."""
    for table, id_column, name_column in (("isbe_committees", "id", "name"),
                                          ("bulk_committees_clean", "committee_id_sbe", "committee_name")):
        if _table_exists(conn, table):
            row = conn.execute(
                f"SELECT {name_column} AS name FROM {table} WHERE {id_column} = ? LIMIT 1", (committee_id_sbe,)
            ).fetchone()
            if row:
                return (row["name"] or "").strip() or f"Committee {committee_id_sbe}"
    return None


# ---------------------------------------------------------------------------------------------
# Committee donor / payee rollups (plan P1; decisions J-P3, J-P4, J-S2). One feeder, two
# consumers: the profile's Top-20 tables are the head (limit=20) of the full lists.
# Both read the bulk_*_clean compat views, filter on committee_id_sbe first (the matviews'
# committee indexes), and return []/0 when the view or a column they read is missing.
# Group keys are COLLATE "C": the same groups (the default collation is deterministic) but a
# byte-order sort, 242 -> 149 ms (donors) and 93 -> 62 ms (payees) warm for committee 1126 all
# time locally, where the row estimate is far too low and the planner sorts the keys.
# ---------------------------------------------------------------------------------------------

_DONOR_ROLLUP_COLUMNS = (
    "committee_id_sbe", "received_date", "amount", "is_archived", "d2_part_code",
    "first_name", "last_or_business_name", "address_line_1", "address_line_2", "city", "state", "postal_code",
)
_PAYEE_ROLLUP_COLUMNS = (
    "committee_id_sbe", "expended_date", "amount", "is_archived", "payee_first_name", "payee_last_or_business_name",
)
_PAYEE_NAME_SQL = (
    "TRIM(COALESCE(NULLIF(TRIM(e.payee_first_name), ''), '')"
    " || CASE WHEN COALESCE(NULLIF(TRIM(e.payee_first_name), ''), '') != ''"
    " AND COALESCE(NULLIF(TRIM(e.payee_last_or_business_name), ''), '') != '' THEN ' ' ELSE '' END"
    " || COALESCE(NULLIF(TRIM(e.payee_last_or_business_name), ''), ''))"
)


def _donor_rollup_source(conn, committee_id_sbe: int, scope: TimeScope):
    """(grouped SELECT, params): the committee's donors in ``scope``, one row per site ``donor_key``."""
    if not _has_columns(conn, "bulk_receipts_clean", _DONOR_ROLLUP_COLUMNS):
        return None
    window, window_params = scope_where(scope, "DATE(r.received_date)")
    return f"""
        SELECT {Donor._bulk_donor_key_sql("r")} COLLATE "C" AS donor_key,
               MODE() WITHIN GROUP (ORDER BY {Donor._bulk_donor_name_sql("r")}) AS donor_name,
               MODE() WITHIN GROUP (ORDER BY NULLIF(TRIM(r.city), '')) AS city,
               MODE() WITHIN GROUP (ORDER BY NULLIF(TRIM(r.state), '')) AS state,
               SUM(r.amount) AS total_amount,
               COUNT(*) AS contribution_count,
               MIN(DATE(r.received_date)) AS first_date,
               MAX(DATE(r.received_date)) AS last_date
        FROM bulk_receipts_clean r
        WHERE r.committee_id_sbe = ? AND {receipts_filter_sql("r")}{window}
        GROUP BY donor_key
    """, [committee_id_sbe, *window_params]


def _payee_rollup_source(conn, committee_id_sbe: int, scope: TimeScope):
    """(grouped SELECT, params): the committee's payees in ``scope``, one row per normalized name."""
    if not _has_columns(conn, "bulk_expenditures_clean", _PAYEE_ROLLUP_COLUMNS):
        return None
    window, window_params = scope_where(scope, "DATE(e.expended_date)")
    return f"""
        SELECT TRIM(REGEXP_REPLACE(LOWER({_PAYEE_NAME_SQL}), '[^[:alnum:]]+', ' ', 'g')) COLLATE "C" AS payee_key,
               MODE() WITHIN GROUP (ORDER BY {_PAYEE_NAME_SQL}) AS payee_name,
               SUM(e.amount) AS total_amount,
               COUNT(*) AS transaction_count,
               MIN(DATE(e.expended_date)) AS first_date,
               MAX(DATE(e.expended_date)) AS last_date
        FROM bulk_expenditures_clean e
        WHERE e.committee_id_sbe = ?
          AND COALESCE(e.amount, 0) > 0 AND NOT COALESCE(e.is_archived::boolean, FALSE){window}
        GROUP BY payee_key
    """, [committee_id_sbe, *window_params]


def _rollup_from(source, name: str, q: str | None):
    """FROM clause over the grouped rows, with ``q`` as a bound case-insensitive substring of ``name``."""
    sql, params = source
    needle = (q or "").strip()
    if not needle:
        return f"({sql}) rolled", list(params)
    return f"({sql}) rolled WHERE STRPOS(LOWER(rolled.{name}), LOWER(?)) > 0", [*params, needle]


def _rollup_page(conn, source, *, key, name, count, limit, offset, sort_by, sort_dir, q):
    """One page of grouped rows. ``sort_by``/``sort_dir`` only pick from a fixed vocabulary (never SQL).

    Every row carries ``total_rows``: how many grouped rows ``q`` keeps before LIMIT/OFFSET
    (``COUNT(*) OVER ()``), so a caller that needs N reads it here instead of running a count query.
    """
    order = {"total": "total_amount", "count": count, "name": f"LOWER({name})", "last": "last_date"}.get(
        sort_by, "total_amount"
    )
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    from_sql, params = _rollup_from(source, name, q)
    return conn.execute(
        f"SELECT *, COUNT(*) OVER () AS total_rows FROM {from_sql} "
        f"ORDER BY {order} {direction} NULLS LAST, total_amount DESC, {key} ASC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    ).fetchall()


def _rollup_count(conn, source, name: str, q: str | None) -> int:
    if source is None:
        return 0
    from_sql, params = _rollup_from(source, name, q)
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {from_sql}", tuple(params)).fetchone()
    return int(row["n"] or 0) if row else 0


def _rollup_summary(conn, source, name: str, q: str | None) -> tuple[int, float]:
    """(row count, sum of total_amount) of the grouped rows ``q`` keeps: the full-list pages' summary line."""
    if source is None:
        return 0, 0.0
    from_sql, params = _rollup_from(source, name, q)
    row = conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(total_amount), 0) AS total FROM {from_sql}", tuple(params)
    ).fetchone()
    return (int(row["n"] or 0), float(row["total"] or 0.0)) if row else (0, 0.0)


def committee_donor_rollup(conn, committee_id_sbe: int, scope: TimeScope, *, limit: int = 50, offset: int = 0,
                           sort_by: str = "total", sort_dir: str = "desc", q: str | None = None) -> list[dict]:
    """The committee's donors in ``scope``, one row per ``donor_key`` (J-P3), sorted and paged.

    ``donor_key`` is ``Donor._bulk_donor_key_sql`` verbatim and the predicate is
    ``receipts_filter_sql(parts="contributions")``, so ``/donors/key/<donor_key>?source=bulk_receipts``
    resolves and its windowed total equals ``total_amount``. donor_name / city / state are the most
    common filed spelling. ``sort_by`` in total | count | name | last (anything else: total);
    ``q`` filters on donor_name before paging. Each row also carries ``total_rows``, the number of rows
    the whole list holds for this scope and ``q`` (what ``committee_donor_rollup_count`` returns).
    """
    source = _donor_rollup_source(conn, committee_id_sbe, scope)
    if source is None:
        return []
    rows = _rollup_page(conn, source, key="donor_key", name="donor_name", count="contribution_count",
                        limit=limit, offset=offset, sort_by=sort_by, sort_dir=sort_dir, q=q)
    return [
        {
            "donor_key": row["donor_key"],
            "donor_name": (row["donor_name"] or "").strip() or "Unknown Donor",
            "city": row["city"] or "",
            "state": row["state"] or "",
            "total_amount": float(row["total_amount"] or 0.0),
            "contribution_count": int(row["contribution_count"] or 0),
            "first_date": row["first_date"],
            "last_date": row["last_date"],
            "total_rows": int(row["total_rows"]),
        }
        for row in rows
    ]


def committee_donor_rollup_count(conn, committee_id_sbe: int, scope: TimeScope, *, q: str | None = None) -> int:
    """Number of rows ``committee_donor_rollup`` pages through for the same scope and ``q``."""
    return _rollup_count(conn, _donor_rollup_source(conn, committee_id_sbe, scope), "donor_name", q)


def committee_payee_rollup(conn, committee_id_sbe: int, scope: TimeScope, *, limit: int = 50, offset: int = 0,
                           sort_by: str = "total", sort_dir: str = "desc", q: str | None = None) -> list[dict]:
    """The committee's payees in ``scope``, one row per normalized name (J-P4: lower case, runs of
    whitespace/punctuation collapsed), displayed as the most common filed spelling; not archived,
    amount > 0, any D-2 part. Same sort vocabulary, paging, ``q`` (on payee_name) and ``total_rows`` as the
    donor rollup.
    """
    source = _payee_rollup_source(conn, committee_id_sbe, scope)
    if source is None:
        return []
    rows = _rollup_page(conn, source, key="payee_key", name="payee_name", count="transaction_count",
                        limit=limit, offset=offset, sort_by=sort_by, sort_dir=sort_dir, q=q)
    return [
        {
            "payee_name": (row["payee_name"] or "").strip() or "Unknown Payee",
            "total_amount": float(row["total_amount"] or 0.0),
            "transaction_count": int(row["transaction_count"] or 0),
            "first_date": row["first_date"],
            "last_date": row["last_date"],
            "total_rows": int(row["total_rows"]),
        }
        for row in rows
    ]


def committee_payee_rollup_count(conn, committee_id_sbe: int, scope: TimeScope, *, q: str | None = None) -> int:
    """Number of rows ``committee_payee_rollup`` pages through for the same scope and ``q``."""
    return _rollup_count(conn, _payee_rollup_source(conn, committee_id_sbe, scope), "payee_name", q)


# ---------------------------------------------------------------------------------------------
# Filing history (plan P8 / L11). The profile lists the newest filings only; the full list is
# /committees/<id>/filings, 100 per page. Both read through _committee_filings and render through
# committees/_filing_table.html, so a row is the same on either page.
# ---------------------------------------------------------------------------------------------

_PROFILE_FILING_LIMIT = 25
_FILINGS_PAGE_SIZE = 100


def _filed_date(received) -> str:
    """YYYY-MM-DD of a ``received_datetime`` (a datetime on production, text in older fixtures); "" when NULL."""
    if not received:
        return ""
    return received.strftime("%Y-%m-%d") if hasattr(received, "strftime") else str(received)[:10]


def _filing_summary(conn, committee_id_sbe: int) -> tuple[int, str]:
    """(number of filings, date of the earliest Statement of Organization or "") over ALL the committee's
    filings, however few of them a page lists: the profile's count line and its Founded / Re-activated dates."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS filing_count,
               MIN(received_datetime) FILTER (WHERE doc_name = 'Statement of Organization') AS founded
        FROM isbe_filed_docs
        WHERE committee_id = ?
        """,
        (committee_id_sbe,),
    ).fetchone()
    return int(row["filing_count"] or 0), _filed_date(row["founded"])


def _committee_filings(conn, committee_id_sbe: int, *, limit: int, offset: int = 0) -> list[dict]:
    """The committee's filings newest first: by received time, those without one last, the higher id first
    among equal times, so a page boundary never splits a tie differently on the next request."""
    rows = conn.execute(
        """
        SELECT id, doc_name, received_datetime, reporting_period_begin, reporting_period_end
        FROM isbe_filed_docs
        WHERE committee_id = ?
        ORDER BY received_datetime DESC NULLS LAST, id DESC
        LIMIT ? OFFSET ?
        """,
        (committee_id_sbe, limit, offset),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "doc_name": row["doc_name"] or "",
            "received_datetime": str(row["received_datetime"]) if row["received_datetime"] else "",
            "filed_date": _filed_date(row["received_datetime"]),
            "period_begin": row["reporting_period_begin"] or "",
            "period_end": row["reporting_period_end"] or "",
        }
        for row in rows
    ]


def _bulk_committee_profile(conn, committee_id_sbe: int, scope: TimeScope) -> dict | None:
    # Same filters as the scope-windowed totals, but over the CURRENT cycle, whatever the scope is.
    cycle_start, cycle_end = cycle_window(current_cycle())
    cycle_scope = replace(scope, start=cycle_start, end=cycle_end)

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
    # Determine table and committee column based on source.
    if use_isbe_receipts:
        rcpt_table = "isbe_condensed_receipts"
        rcpt_cmte_col = "committee_id"
    elif has_bulk_receipts:
        rcpt_table = "bulk_receipts_clean"
        rcpt_cmte_col = "committee_id_sbe"
    else:
        rcpt_table = None
        rcpt_cmte_col = None

    receipts_filter = ""
    receipts_params: list = []
    if rcpt_table:
        receipts_filter = f"WHERE {rcpt_cmte_col} = ?"
        receipts_params = [committee_id_sbe]
        # Archived-row exclusion; the compat view takes the shared all-parts predicate (J-S20:
        # amount > 0, not archived), qualified by the table name since these queries are unaliased.
        if use_isbe_receipts:
            receipts_filter += " AND archived = FALSE"
        else:
            receipts_filter += f" AND {receipts_filter_sql(rcpt_table, parts='all')}"
        cycle_clause, cycle_plist = scope_where(cycle_scope, "DATE(received_date)")
        receipts_cycle_filter = receipts_filter + cycle_clause
        receipts_cycle_params = receipts_params + cycle_plist
        rcpt_clause, rcpt_plist = scope_where(scope, "DATE(received_date)")
        if rcpt_clause:
            receipts_filter += rcpt_clause
            receipts_params.extend(rcpt_plist)

    receipts_summary = {
        "contribution_count": 0,
        "total_amount": 0.0,
        "first_date": None,
        "last_date": None,
        "cycle_total": 0.0,
    }
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
                "cycle_total": 0.0,
            }

        cycle_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0.0) AS cycle_total
            FROM {rcpt_table}
            {receipts_cycle_filter}
            """,
            tuple(receipts_cycle_params),
        ).fetchone()
        if cycle_row:
            receipts_summary["cycle_total"] = float(cycle_row["cycle_total"] or 0.0)

    top_donors = committee_donor_rollup(conn, committee_id_sbe, scope, limit=20)
    # "See all N donors" (P2): N rides on the Top-20 query (COUNT(*) OVER ()), no second count query.
    donor_count = top_donors[0]["total_rows"] if top_donors else 0

    # --- Expenditures query ---
    if use_isbe_expenditures:
        exp_table = "isbe_condensed_expenditures"
        exp_cmte_col = "committee_id"
    elif has_bulk_expenditures:
        exp_table = "bulk_expenditures_clean"
        exp_cmte_col = "committee_id_sbe"
    else:
        exp_table = None
        exp_cmte_col = None

    expenditures_filter = ""
    expenditures_params: list = []
    if exp_table:
        expenditures_filter = f"WHERE {exp_cmte_col} = ?"
        expenditures_params = [committee_id_sbe]
        if use_isbe_expenditures:
            expenditures_filter += " AND archived = FALSE"
        elif _column_exists(conn, exp_table, "is_archived"):
            expenditures_filter += " AND NOT COALESCE(is_archived::boolean, FALSE)"
        cycle_clause, cycle_plist = scope_where(cycle_scope, "DATE(expended_date)")
        expenditures_cycle_filter = expenditures_filter + cycle_clause
        expenditures_cycle_params = expenditures_params + cycle_plist
        exp_clause, exp_plist = scope_where(scope, "DATE(expended_date)")
        if exp_clause:
            expenditures_filter += exp_clause
            expenditures_params.extend(exp_plist)

    expenditures_summary = {
        "transaction_count": 0,
        "total_amount": 0.0,
        "first_date": None,
        "last_date": None,
        "cycle_total": 0.0,
    }
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
                "cycle_total": 0.0,
            }

        cycle_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0.0) AS cycle_total
            FROM {exp_table}
            {expenditures_cycle_filter}
            """,
            tuple(expenditures_cycle_params),
        ).fetchone()
        if cycle_row:
            expenditures_summary["cycle_total"] = float(cycle_row["cycle_total"] or 0.0)

    top_payees = committee_payee_rollup(conn, committee_id_sbe, scope, limit=20)
    payee_count = top_payees[0]["total_rows"] if top_payees else 0

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
    filing_count = 0
    if has_filed_docs:
        filing_count, founding_date = _filing_summary(conn, committee_id_sbe)
        filing_history = _committee_filings(conn, committee_id_sbe, limit=_PROFILE_FILING_LIMIT)
        # Original founding date: the earliest Statement of Organization of ALL the filings (usually
        # the oldest one, beyond the rows listed).
        if founding_date:
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
        "filing_count": filing_count,
        "top_donors": top_donors,
        "top_payees": top_payees,
        "donor_count": donor_count,
        "payee_count": payee_count,
    }


def _windowed_receipts_cte(conn, scope: TimeScope):
    """CTE (sql, params) of per-committee receipt totals inside ``scope``, or None without a receipts source.

    Prefers ``isbe_condensed_receipts`` (native date column, indexed: ~35 ms warm
    for a cycle window locally) over the ``bulk_receipts_clean`` compat view, whose
    text-cast ``received_date`` forces a 3.7M-row scan (~1.4 s). Archived rows are
    excluded either way, as in ``_bulk_committee_profile``.
    """
    if _table_exists(conn, "isbe_condensed_receipts"):
        table, cmte_col, archived = "isbe_condensed_receipts", "committee_id", "archived = FALSE"
    elif _table_exists(conn, "bulk_receipts_clean"):
        table, cmte_col = "bulk_receipts_clean", "committee_id_sbe"
        archived = (
            "NOT COALESCE(is_archived::boolean, FALSE)"
            if _column_exists(conn, table, "is_archived") else "TRUE"
        )
    else:
        return None
    clause, params = scope_where(scope, "DATE(received_date)")
    return (
        f"WITH windowed AS (SELECT {cmte_col} AS committee_id, SUM(amount) AS total "
        f"FROM {table} WHERE {archived}{clause} GROUP BY {cmte_col})"
    ), params


# ---------------------------------------------------------------------------------------------
# Committee families on public pages (S-C8 part 2, J-S9 / J-S18): ACCEPTED families only; pending
# and rejected proposals never show. Group, never merge: per-committee pages and numbers stay.
# ---------------------------------------------------------------------------------------------

def _accepted_family(conn, committee_id: int) -> dict | None:
    """Profile banner data: label, size, active count and sibling committees of the accepted family
    this committee belongs to; None when it is in none."""
    if not _table_exists(conn, "committee_family_member"):
        return None
    found = accepted_family_map(conn, [committee_id]).get(committee_id)
    if not found:
        return None
    members = family_members(conn, found[0])
    return {
        "label": found[1],
        "size": len(members),
        "active": sum(1 for member in members if member["active"]),
        "siblings": [member for member in members if member["id"] != committee_id],
    }


def _committee_rows_by_family(conn, scope: TimeScope, query: str, sort_by: str, direction: str,
                              limit: int, offset: int) -> tuple[list[dict], int]:
    """``/committees/?by=family``: the members of an accepted family collapse into one row with the
    family label, the member count and the members' summed totals (windowed like the default list);
    other committees keep their own row. A family row links to its active (else newest) member and
    matches ``q`` when the label or any member name does. Returns (one page of rows, row count)."""
    bounded = bool(scope.start or scope.end)
    windowed = _windowed_receipts_cte(conn, scope) if bounded else None
    params: list = []
    if windowed:
        with_sql, params = windowed[0] + ",", list(windowed[1])
        money_join, money = "LEFT JOIN windowed w ON w.committee_id = c.id", "COALESCE(w.total, 0)"
    elif not bounded and _table_exists(conn, "isbe_committee_money"):
        with_sql, money_join, money = "WITH", "LEFT JOIN isbe_committee_money w ON w.committee_id = c.id", "COALESCE(w.total, 0)"
    else:
        with_sql, money_join, money = "WITH", "", "0"
    # A family row matches when the label or any member name does: by token, else (no group at all) by the one substring
    # fallback, as /search (database/name_normalize.py).
    havings = [("", [])]
    if query:
        havings = [
            (f"HAVING BOOL_OR({member[0]}) OR {label[0]}", member[1] + label[1])
            for member, label in (
                (rule("c.name", query), rule("MIN(fam.label)", query)) for rule in (name_match_sql, substring_fallback_sql)
            )
            if member is not None
        ] or [("HAVING FALSE", [])]
    base_params = params
    for having, having_params in havings:
        params = base_params + having_params
        grouped = f"""
            {with_sql} fam AS (
                SELECT DISTINCT ON (m.committee_id) m.committee_id, f.id AS family_id, f.label
                FROM committee_family_member m
                JOIN committee_family f ON f.id = m.family_id
                WHERE f.status = 'accepted'
                ORDER BY m.committee_id, f.id
            ),
            grouped AS (
                SELECT (ARRAY_AGG(c.id ORDER BY c.active DESC NULLS LAST, c.id DESC))[1] AS id,
                       COALESCE(MIN(fam.label), MIN(c.name)) AS name,
                       MIN(c.type) AS type,
                       CASE WHEN MIN(c.party) = MAX(c.party) THEN MIN(c.party) END AS party,
                       BOOL_OR(c.active) AS active,
                       MIN(fam.family_id) AS family_id,
                       COUNT(*) AS member_count,
                       SUM({money}) AS total_contributions
                FROM isbe_committees c
                LEFT JOIN fam ON fam.committee_id = c.id
                {money_join}
                GROUP BY fam.family_id, CASE WHEN fam.family_id IS NULL THEN c.id END
                {having}
            )
        """
        count_row = conn.execute(f"{grouped} SELECT COUNT(*) AS n FROM grouped", tuple(params)).fetchone()
        if count_row and count_row["n"]:
            break
    order = "total_contributions" if sort_by == "total_contributions" else "name"
    rows = conn.execute(
        f"{grouped} SELECT * FROM grouped ORDER BY {order} {direction}, id ASC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "type": f"Committee family ({row['member_count']} committees)" if row["family_id"] else row["type"],
            "party": row["party"],
            "active": row["active"],
            "total_contributions": row["total_contributions"],
        }
        for row in rows
    ], int(count_row["n"] or 0) if count_row else 0


@committees_bp.route('/')
def list_committees():
    """List all committees from ISBE data."""
    conn = current_app.get_database()
    scope = resolve_time_scope(default="cycle:current", axis="received", data_through=latest_data_date("isbe"))
    scope_context = dict(
        time_scope=scope,
        scope_default_key=f"cycle:{current_cycle()}",
        time_scope_source_label="ISBE",
        scope_query=scope_params(scope),
    )

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    query = request.args.get('q', '').strip()

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
    by_family = has_isbe and request.args.get('by') == 'family' and _table_exists(conn, "committee_family_member")

    if by_family:
        # committees/list.html builds its search form, sort and pager links from scope_query: carry the opt-in there.
        scope_context["scope_query"] = {**scope_context["scope_query"], "by": "family"}
        rows, total = _committee_rows_by_family(conn, scope, query, sort_by, direction, per_page, offset)
    elif has_isbe:
        # By name token, else the one substring fallback when the token count is zero (database/name_normalize.py).
        variants = [("", [])]
        if query:
            variants = [(f"WHERE {sql}", list(sql_params)) for sql, sql_params in name_predicates("c.name", query)]
            variants = variants or [("WHERE FALSE", [])]
        for where_clause, params in variants:
            count_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM isbe_committees c {where_clause}",
                tuple(params),
            ).fetchone()
            total = count_row["count"] if count_row else 0
            if total:
                break

        bounded = bool(scope.start or scope.end)
        windowed = _windowed_receipts_cte(conn, scope) if bounded else None
        if windowed:
            cte_sql, cte_params = windowed
            rows = conn.execute(
                f"""
                {cte_sql}
                SELECT c.id, c.name, c.type, c.party, c.active,
                       COALESCE(w.total, 0) AS total_contributions
                FROM isbe_committees c
                LEFT JOIN windowed w ON w.committee_id = c.id
                {where_clause}
                ORDER BY {sort_field} {direction}, c.id ASC
                LIMIT ? OFFSET ?
                """,
                tuple(cte_params + params + [per_page, offset]),
            ).fetchall()
        elif has_money and not bounded:
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
        date_from = scope.start.isoformat() if scope.start else None
        date_to = scope.end.isoformat() if scope.end else None
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
                               use_isbe=False,
                               **scope_context)

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
                           use_isbe=True,
                           **scope_context)


@committees_bp.route('/suggest')
def suggest_committees():
    """Return up to 10 committee name suggestions for autocomplete."""
    conn = current_app.get_database()
    q = request.args.get('q', '').strip()
    if len(q) < 2 or not _table_exists(conn, "isbe_committees"):
        return jsonify([])
    rows = []
    for where, params in name_predicates("name", q):
        rows = conn.execute(
            f"SELECT id AS value, name AS label FROM isbe_committees WHERE {where} ORDER BY name LIMIT 10",
            tuple(params),
        ).fetchall()
        if rows:
            break
    return jsonify([{"label": r["label"], "value": r["value"]} for r in rows])


@committees_bp.route('/<int:committee_id>')
def committee_detail(committee_id):
    """Committee detail page: the ISBE profile whenever ``isbe_committees`` has the id (J-S8);
    the legacy scraped-committee page only for ids ISBE lacks (its own URL: /committees/legacy/<id>)."""
    conn = current_app.get_database()
    data_through = latest_data_date("isbe")

    committee = None if _in_isbe_committees(conn, committee_id) else Committee.get_by_id(conn, committee_id)
    if not committee:
        # Treat committee_id as an ISBE SBE committee ID
        return _isbe_profile_page(conn, committee_id, data_through)

    return _legacy_committee_page(conn, committee, data_through)


def _isbe_profile_page(conn, committee_id_sbe: int, data_through):
    """The ISBE committee profile (``committees/detail_sbe.html``); 404 when nothing is known about the id."""
    scope = resolve_time_scope(default="all", axis="received", data_through=data_through)
    profile = _bulk_committee_profile(conn, committee_id_sbe, scope)
    if not profile:
        abort(404)
    return render_template(
        'committees/detail_sbe.html',
        committee_id_sbe=committee_id_sbe,
        profile=profile,
        family=_accepted_family(conn, committee_id_sbe),
        time_scope=scope,
        scope_default_key="all",
        time_scope_source_label="ISBE",
        scope_query=scope_params(scope),
    )


@committees_bp.route('/legacy/<int:committee_id>')
def legacy_committee_detail(committee_id):
    """Legacy scraped-committee page (``committees`` table) at its own URL, since ISBE ids win
    /committees/<id> (J-S19); links built from legacy ids point here."""
    conn = current_app.get_database()
    committee = Committee.get_by_id(conn, committee_id)
    if not committee:
        abort(404)
    return _legacy_committee_page(conn, committee, latest_data_date("isbe"))


def _legacy_committee_page(conn, committee, data_through):
    committee_id = committee.id
    scope = resolve_time_scope(default="all", axis="transaction", data_through=data_through)
    date_from = scope.start.isoformat() if scope.start else None
    date_to = scope.end.isoformat() if scope.end else None

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
                           contrib_dir=contrib_dir,
                           time_scope=scope,
                           scope_default_key="all",
                           time_scope_source_label="ISBE",
                           scope_query=scope_params(scope))


@committees_bp.route('/sbe/<int:committee_id_sbe>')
def committee_detail_by_sbe(committee_id_sbe):
    """ISBE-id entry: the ISBE profile whenever ``isbe_committees`` has the id (J-S8); otherwise a
    legacy committee carrying this SBE id redirects to its legacy page (J-S19)."""
    conn = current_app.get_database()
    committee = None if _in_isbe_committees(conn, committee_id_sbe) else Committee.get_by_sbe_id(conn, committee_id_sbe)
    if not committee:
        return _isbe_profile_page(conn, committee_id_sbe, latest_data_date("isbe"))
    target = url_for('committees.legacy_committee_detail', committee_id=committee.id)
    query = request.query_string.decode()
    return redirect(f"{target}?{query}" if query else target)


# ---------------------------------------------------------------------------------------------
# Full donor / payee lists (plan P3, decision J-P6): every rollup row of the ISBE committee in the
# page window, 50 per page; the profile's Top Donors / Top Payees are the head of these lists.
# ---------------------------------------------------------------------------------------------

_LIST_PAGE_SIZE = 50
_LIST_SORTS = {"total": "desc", "count": "desc", "name": "asc", "last": "desc"}  # sort -> direction it opens with
_FULL_LISTS = {  # kind -> (rows, grouped source, name column, bar axis)
    "donors": (committee_donor_rollup, _donor_rollup_source, "donor_name", "received"),
    "payees": (committee_payee_rollup, _payee_rollup_source, "payee_name", "transaction"),
}


@committees_bp.route('/<int:committee_id>/donors')
def committee_donors(committee_id):
    """Every donor of the ISBE committee in the page window (one row per donor_key)."""
    return _full_list_page(committee_id, "donors")


@committees_bp.route('/<int:committee_id>/payees')
def committee_payees(committee_id):
    """Every payee of the ISBE committee in the page window (one row per normalized name)."""
    return _full_list_page(committee_id, "payees")


def _full_list_page(committee_id: int, kind: str):
    conn = current_app.get_database()
    committee_name = _isbe_committee_name(conn, committee_id)
    if committee_name is None:
        abort(404)
    rollup, source_for, name_column, axis = _FULL_LISTS[kind]
    scope = resolve_time_scope(default="all", axis=axis, data_through=latest_data_date("isbe"))
    q = request.args.get("q", "").strip()
    sort_by = request.args.get("sort", "total")
    if sort_by not in _LIST_SORTS:
        sort_by = "total"
    sort_dir = request.args.get("dir", "")
    if sort_dir not in ("asc", "desc"):
        sort_dir = _LIST_SORTS[sort_by]

    count, total = _rollup_summary(conn, source_for(conn, committee_id, scope), name_column, q)
    total_pages = max(1, (count + _LIST_PAGE_SIZE - 1) // _LIST_PAGE_SIZE)
    page = min(max(request.args.get("page", 1, type=int), 1), total_pages)
    rows = rollup(conn, committee_id, scope, limit=_LIST_PAGE_SIZE, offset=(page - 1) * _LIST_PAGE_SIZE,
                  sort_by=sort_by, sort_dir=sort_dir, q=q)

    # "This cycle" beside the window total, as on the profile: the same rows over the current cycle.
    cycle_start, cycle_end = cycle_window(current_cycle())
    if (scope.start, scope.end) == (cycle_start, cycle_end):
        cycle_total = total
    else:
        cycle_scope = replace(scope, start=cycle_start, end=cycle_end)
        cycle_total = _rollup_summary(conn, source_for(conn, committee_id, cycle_scope), name_column, q)[1]

    return render_template(
        f"committees/{kind}.html",
        committee_id=committee_id,
        committee_name=committee_name,
        rows=rows,
        count=count,
        total=total,
        cycle_total=cycle_total,
        q=q,
        sort_by=sort_by,
        sort_dir=sort_dir,
        list_sorts=_LIST_SORTS,
        page=page,
        total_pages=total_pages,
        time_scope=scope,
        scope_default_key="all",
        time_scope_source_label="ISBE",
        scope_query=scope_params(scope),
    )


@committees_bp.route('/<int:committee_id>/filings')
def committee_filings(committee_id):
    """Every filing of the ISBE committee, newest first, 100 per page (``?page=``); the profile lists the newest 25.
    All time whatever the page window: the window is only carried through to the profile link. A page past
    the last is an empty list, not the last page."""
    conn = current_app.get_database()
    committee_name = _isbe_committee_name(conn, committee_id)
    if committee_name is None:
        abort(404)
    scope = resolve_time_scope(default="all", axis="received")
    total = _filing_summary(conn, committee_id)[0] if _table_exists(conn, "isbe_filed_docs") else 0
    total_pages = max(1, (total + _FILINGS_PAGE_SIZE - 1) // _FILINGS_PAGE_SIZE)
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * _FILINGS_PAGE_SIZE
    filings = _committee_filings(conn, committee_id, limit=_FILINGS_PAGE_SIZE, offset=offset) if offset < total else []
    return render_template(
        "committees/filings.html",
        committee_id=committee_id,
        committee_name=committee_name,
        filings=filings,
        total=total,
        page=page,
        total_pages=total_pages,
        scope_query=scope_params(scope),
    )


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
