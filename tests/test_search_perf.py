"""Load-times plan, unit P6 -- search speed (L6): materialized candidate aggregate, trigram index, shared search cache.

Plan: .claude/plans/load-times.md (row L6 of "Ranked fixes", unit P6).
The file starts red; every red test names the behaviour P6 has to add. The tests that already pass before P6 are
guards: they pin behaviour the change must NOT break (cache hit/miss semantics, --dry-run, the unchanged SELECT, the
plain-view control rows, the environment override of the TTL).

Contract pinned:

  (1) scripts/swap_bulk_to_isbe.py builds bulk_candidate_committee_finance_agg as a MATERIALIZED VIEW.
      CANDIDATE_FINANCE_AGG_VIEW_SQL stays the one statement that creates it, now
      "CREATE MATERIALIZED VIEW bulk_candidate_committee_finance_agg AS <the unchanged SELECT>"; main() also removes
      what was there before (a plain view, an older materialized view, or a base table), builds the indexes
      idx_bulk_candidate_committee_finance_agg_{candidate,committee,cycle} on (candidate_id), (committee_id_sbe),
      (election_cycle), then runs "ANALYZE bulk_candidate_committee_finance_agg". --dry-run still writes nothing.
  (2) database/schema.sql carries "CREATE EXTENSION IF NOT EXISTS pg_trgm;" and
      "CREATE INDEX IF NOT EXISTS idx_analytics_donor_summary_name_trgm ON analytics_donor_summary
       USING gin (donor_name gin_trgm_ops);" -- the extension above the index, the table above the index.
  (3) webapp.routes.main._search_results_cache is a RouteCache named "search_results", keyed by
      (scope_cache_key, type, lower-cased query), TTL = app.config SEARCH_RESULTS_CACHE_TTL_SECONDS (config.py default
      21600, environment override kept), honouring ROUTE_PERF_CACHE_ENABLED. Hits are read from the shared layer
      (Redis when REDIS_URL is set), so a second worker sees them and flush_all_route_caches() clears them.
  (4) With the aggregate a MATERIALIZED VIEW, GET /search still renders every section with complete candidate rows,
      and so does the aggregate's other big reader, GET /candidate-finance/ (cycle filter and period columns).

Facts the tests rest on (each measured on PostgreSQL 16 here, not assumed):

  * "DROP VIEW IF EXISTS x" RAISES when x is a materialized view, and "DROP MATERIALIZED VIEW IF EXISTS x" RAISES when x
    is a plain view or a table ('"x" is not a view' / '... is not a materialized view'); IF EXISTS only forgives an
    object that is absent. The two DROP statements the contract names therefore cannot both run unconditionally:
    whichever order, one raises on a database that holds the other kind. The scenario tests run main() against every
    prior state for exactly that reason.
  * information_schema.tables does not list materialized views, so main()'s "what is there now" probe is blind to a
    materialized view left by an earlier run (today's script then fails with DuplicateTable on the second run).
  * information_schema.columns does not list their columns either, so the compat layer's PRAGMA table_info(<matview>)
    is EMPTY (database/pg_compat.py _read_table_info), while _table_exists is true (it also reads pg_matviews).
    Two readers learn the aggregate's columns that way and degrade WITHOUT an error:
      - main.py _search_local_candidates (_column_exists "office_sought" / "period_end_date"): every office and
        "latest period end" disappears from the search candidates section;
      - database/models.py CandidateCommitteeFinanceAgg._column_names (the /candidate-finance/ list): the
        election_cycle filter is silently dropped (every cycle's rows shown under a one-cycle scope label) and
        period_year / election_cycle / period dates come back NULL.
    (experimental.py's Viz Lab prototypes probe the same way; they are local-only and not covered here.)
    Where the fix lives -- pg_compat's PRAGMA emulation covers all readers at once -- is the implementer's choice;
    the tests pin what the two pages show.

Interpretation choices (each flagged in the tester report; relax by editing one test):

  * main() is run end to end against a per-test schema. The harness redirects its connection to the schema
    (search_path), rewrites the script's hard-coded 'public' / public. references to that schema, stubs the compat-view
    step (SWAP_PAIRS = [], COMPAT_VIEWS_DDL = "SELECT 1") because P6 does not change it, and records every statement.
    It needs main() to keep (a) reading --db-url and --dry-run, (b) connecting through the module-level
    psycopg.connect, (c) importing COMPAT_VIEWS_DDL from isbe_sunshine_etl.
  * The unchanged-SELECT pin is a SHA-256 of the whitespace-collapsed body (GOLDEN_BODY_SHA256 says how to re-derive it).
  * Search-cache hit/miss is judged black-box: a donor row is inserted between two requests; a response that does not
    show it came from the cache, one that shows it was computed again. The cache tests use a fake Redis only where the
    shared layer itself is the subject; everywhere else Redis is forced off.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import types
import uuid
from pathlib import Path

import pytest

import webapp.cache_backend as cache_backend
import webapp.routes.main as main_routes
from database.connection import get_db, init_db
from webapp.app import create_app
from webapp.cache_backend import RouteCache

REPO_ROOT = Path(__file__).resolve().parents[1]
SWAP_SCRIPT = REPO_ROOT / "scripts" / "swap_bulk_to_isbe.py"
SCHEMA_SQL = REPO_ROOT / "database" / "schema.sql"

AGG = "bulk_candidate_committee_finance_agg"
AGG_LEGACY = "bulk_candidate_committee_finance_agg_legacy"

# The 19 columns every consumer (search, /candidate-finance/, the race builder, the API) reads, in view order.
AGG_COLUMNS = [
    "candidate_id", "candidate_full_name", "office_sought", "district_type", "district",
    "candidate_party_affiliation", "committee_id_sbe", "committee_name", "committee_type",
    "committee_party_affiliation", "period_year", "election_cycle", "filing_count",
    "sum_total_receipts", "sum_total_expenditures", "max_ending_funds_available",
    "archived_filing_count", "period_start_date", "period_end_date",
]

# The three indexes the contract names: name suffix -> indexed column.
AGG_INDEXES = {
    "idx_bulk_candidate_committee_finance_agg_candidate": "candidate_id",
    "idx_bulk_candidate_committee_finance_agg_committee": "committee_id_sbe",
    "idx_bulk_candidate_committee_finance_agg_cycle": "election_cycle",
}
# The fourth index (search program, decision S2): the GIN expression index /search's candidate-name predicate uses.
AGG_NAME_INDEX = "idx_bulk_cand_cmte_finance_agg_name_tsv"

# SHA-256 of the SELECT body of CANDIDATE_FINANCE_AGG_VIEW_SQL as committed before P6: the text from
# "WITH filing_periods AS (" to the end of the constant, whitespace collapsed to single spaces, trailing ";" dropped.
# Re-derive from the pre-P6 text:  git show 2301286:scripts/swap_bulk_to_isbe.py  -> take CANDIDATE_FINANCE_AGG_VIEW_SQL,
# apply _agg_body() below, SHA-256 the UTF-8 bytes (2141 characters). A legitimate change to the aggregate's SELECT means
# updating this hash on purpose, in the same commit that changes the SELECT.
GOLDEN_BODY_SHA256 = "326ba0be74b098140098d1ee7d68e3a6df98591da58bfefb77c48df2e3684468"
GOLDEN_BODY_LENGTH = 2141


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _collapse(text: str) -> str:
    return " ".join(text.split())


def _read_rows(conn, sql: str, params=()):
    """Run a read and END the transaction, so no AccessShareLock outlives the call (main() drops the relation)."""
    rows = conn.execute(sql, params).fetchall()
    conn.commit()
    return rows


def _agg_body(sql: str) -> str:
    """The SELECT body of the aggregate DDL: from 'WITH filing_periods AS (' on, whitespace collapsed, no trailing ';'."""
    start = sql.index("WITH filing_periods AS (")
    return _collapse(sql[start:]).rstrip(";").rstrip()


def _string_constants(path: Path) -> str:
    """Every string literal of a Python file (docstrings included, comments and code excluded), whitespace collapsed."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pieces = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    return _collapse(" ".join(pieces))


# ---------------------------------------------------------------------------
# The ISBE world the aggregate is computed from (hand-computed expectations)
# ---------------------------------------------------------------------------
#
# Source rows (production column types; only the columns the aggregate reads):
#   committees  10 Friends of Alder (D) | 20 Birch for Illinois (R) | 30 Alder Victory Fund (D, no D-2 at all)
#               | 40 Dale for Delegate (no party, only an ARCHIVED D-2)
#   candidates  1 Ada Alder (State Senator, Senate District 12, D) | 2 Bo Birch (Governor, Statewide, R)
#               | 3 Cy Cedar (State Representative) has NO committee link -> must not appear
#               | 4 Dee Dale (Treasurer, Statewide, no party)
#   links       (cand 1, cmte 10) (cand 1, cmte 30) (cand 2, cmte 20) (cand 4, cmte 40)
#   D-2 reports (id, cmte, filed_doc, receipts, expenditures, ending funds, archived)
#               901 10 5001 1000 400 600 no | 902 10 5002 250 100 750 no | 903 10 5001 999 999 999 YES (amended away)
#               911 20 6001 5000 1200 3800 no | 921 40 7001 777 555 333 YES
#   receipts    (cmte, filed_doc, date): (10,5001,2025-01-05) (10,5001,2025-03-15) (10,5002,2024-06-30)
#               (20,6001,2023-11-20) (40,7001,2022-02-02)
#
# The aggregate groups by (candidate, committee, period_year); period_year = year of the filing's latest receipt;
# election_cycle = period_year rounded UP to an even year; archived D-2s add nothing to any sum, count or maximum but are
# counted in archived_filing_count. Expected rows, in (candidate_id, committee_id_sbe, period_year) order:
#   R1 cand 1 / cmte 10 / 2024 -> cycle 2024: 1 filing, 250.0 / 100.0 / 750.0, 0 archived, 2024-06-30 .. 2024-06-30
#   R2 cand 1 / cmte 10 / 2025 -> cycle 2026: filing 5001 counted once (903 is archived): 1 filing, 1000.0 / 400.0 / 600.0,
#      1 archived, 2025-01-05 .. 2025-03-15
#   R3 cand 1 / cmte 30 / no filing -> period_year, cycle and both dates NULL, 0 filings, 0.0 / 0.0 / 0.0, 0 archived
#   R4 cand 2 / cmte 20 / 2023 -> cycle 2024: 1 filing, 5000.0 / 1200.0 / 3800.0, 0 archived, 2023-11-20 .. 2023-11-20
#   R5 cand 4 / cmte 40 / 2022 -> cycle 2022: archived-only, so 0 filings and 0.0 sums, 1 archived, 2022-02-02 .. 2022-02-02
# Candidate 3 contributes nothing. 5 rows in all.

ISBE_WORLD_SQL = [
    "CREATE TABLE isbe_committees (id INTEGER PRIMARY KEY, name TEXT, type TEXT, party TEXT)",
    """INSERT INTO isbe_committees (id, name, type, party) VALUES
        (10, 'Friends of Alder', 'Candidate', 'Democratic'),
        (20, 'Birch for Illinois', 'Candidate', 'Republican'),
        (30, 'Alder Victory Fund', 'Candidate', 'Democratic'),
        (40, 'Dale for Delegate', 'Candidate', NULL)""",
    """CREATE TABLE isbe_candidates (id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, office TEXT,
        district_type TEXT, district TEXT, party TEXT)""",
    """INSERT INTO isbe_candidates (id, first_name, last_name, office, district_type, district, party) VALUES
        (1, 'Ada', 'Alder', 'State Senator', 'Senate District', '12', 'Democratic'),
        (2, 'Bo', 'Birch', 'Governor', 'Statewide', NULL, 'Republican'),
        (3, 'Cy', 'Cedar', 'State Representative', 'House District', '5', 'Democratic'),
        (4, 'Dee', 'Dale', 'Treasurer', 'Statewide', NULL, NULL)""",
    "CREATE TABLE isbe_candidate_committees (id INTEGER PRIMARY KEY, committee_id INTEGER, candidate_id INTEGER)",
    "INSERT INTO isbe_candidate_committees (id, committee_id, candidate_id) VALUES (1, 10, 1), (2, 30, 1), (3, 20, 2), (4, 40, 4)",
    """CREATE TABLE isbe_d2_reports (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER,
        total_receipts DOUBLE PRECISION, total_expenditures DOUBLE PRECISION, end_funds_available DOUBLE PRECISION,
        archived BOOLEAN DEFAULT FALSE)""",
    """INSERT INTO isbe_d2_reports (id, committee_id, filed_doc_id, total_receipts, total_expenditures,
            end_funds_available, archived) VALUES
        (901, 10, 5001, 1000.0, 400.0, 600.0, FALSE),
        (902, 10, 5002, 250.0, 100.0, 750.0, FALSE),
        (903, 10, 5001, 999.0, 999.0, 999.0, TRUE),
        (911, 20, 6001, 5000.0, 1200.0, 3800.0, FALSE),
        (921, 40, 7001, 777.0, 555.0, 333.0, TRUE)""",
    "CREATE TABLE isbe_receipts (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, received_date DATE)",
    """INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, received_date) VALUES
        (1, 10, 5001, '2025-01-05'), (2, 10, 5001, '2025-03-15'), (3, 10, 5002, '2024-06-30'),
        (4, 20, 6001, '2023-11-20'), (5, 40, 7001, '2022-02-02')""",
    # Present only so main()'s "are the ISBE tables populated" probe passes and its candidacy view can be built.
    "CREATE TABLE isbe_expenditures (id INTEGER PRIMARY KEY, committee_id INTEGER)",
    "INSERT INTO isbe_expenditures (id, committee_id) VALUES (1, 10)",
    """CREATE TABLE isbe_candidacies (id INTEGER PRIMARY KEY, candidate_id INTEGER, election_type TEXT,
        election_year INTEGER, race_type TEXT, outcome TEXT)""",
    "INSERT INTO isbe_candidacies (id, candidate_id, election_type, election_year, race_type, outcome) VALUES "
    "(1, 1, 'General Election', 2026, 'incumbent', NULL)",
]

# (candidate_id, candidate_full_name, office_sought, district_type, district, candidate_party_affiliation,
#  committee_id_sbe, committee_name, committee_type, committee_party_affiliation, period_year, election_cycle,
#  filing_count, sum_total_receipts, sum_total_expenditures, max_ending_funds_available, archived_filing_count,
#  period_start_date, period_end_date)
EXPECTED_AGG_ROWS = [
    (1, "Ada Alder", "State Senator", "Senate District", "12", "Democratic", 10, "Friends of Alder", "Candidate",
     "Democratic", 2024, 2024, 1, 250.0, 100.0, 750.0, 0, "2024-06-30", "2024-06-30"),
    (1, "Ada Alder", "State Senator", "Senate District", "12", "Democratic", 10, "Friends of Alder", "Candidate",
     "Democratic", 2025, 2026, 1, 1000.0, 400.0, 600.0, 1, "2025-01-05", "2025-03-15"),
    (1, "Ada Alder", "State Senator", "Senate District", "12", "Democratic", 30, "Alder Victory Fund", "Candidate",
     "Democratic", None, None, 0, 0.0, 0.0, 0.0, 0, None, None),
    (2, "Bo Birch", "Governor", "Statewide", None, "Republican", 20, "Birch for Illinois", "Candidate",
     "Republican", 2023, 2024, 1, 5000.0, 1200.0, 3800.0, 0, "2023-11-20", "2023-11-20"),
    (4, "Dee Dale", "Treasurer", "Statewide", None, None, 40, "Dale for Delegate", "Candidate",
     None, 2022, 2022, 0, 0.0, 0.0, 0.0, 1, "2022-02-02", "2022-02-02"),
]


def _seed_isbe_world(conn) -> None:
    for statement in ISBE_WORLD_SQL:
        conn.execute(statement)
    conn.commit()


def _relkind(conn, name: str):
    """'m' materialized view, 'v' plain view, 'r' table, None when absent (in the test's own schema)."""
    rows = _read_rows(
        conn,
        "SELECT c.relkind AS kind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relname = ?",
        (name,),
    )
    return rows[0]["kind"] if rows else None


def _column_names(conn, name: str) -> list[str]:
    rows = _read_rows(
        conn,
        "SELECT a.attname AS name FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relname = ? AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY a.attnum",
        (name,),
    )
    return [row["name"] for row in rows]


def _agg_rows(conn) -> list[dict]:
    rows = _read_rows(
        conn,
        f"SELECT * FROM {AGG} ORDER BY candidate_id, committee_id_sbe, COALESCE(period_year, 0)",
    )
    return [{column: row[column] for column in AGG_COLUMNS} for row in rows]


def _expected_agg_rows() -> list[dict]:
    return [dict(zip(AGG_COLUMNS, row)) for row in EXPECTED_AGG_ROWS]


def _assert_agg_content(conn) -> None:
    """The relation is a populated materialized view with exactly the pinned columns and rows."""
    assert _relkind(conn, AGG) == "m", f"{AGG} must be a MATERIALIZED view, relkind is {_relkind(conn, AGG)!r}"
    populated = _read_rows(
        conn,
        "SELECT ispopulated AS populated FROM pg_matviews WHERE schemaname = current_schema() AND matviewname = ?",
        (AGG,),
    )
    assert [row["populated"] for row in populated] == [True], "the materialized view was created WITH NO DATA"
    assert _column_names(conn, AGG) == AGG_COLUMNS
    got = _agg_rows(conn)
    want = _expected_agg_rows()
    assert len(got) == len(want) == 5
    for got_row, want_row in zip(got, want):
        assert got_row == want_row, f"cand {want_row['candidate_id']} cmte {want_row['committee_id_sbe']}"


def _index_definitions(conn) -> dict[str, str]:
    rows = _read_rows(
        conn,
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = ?",
        (AGG,),
    )
    return {row["indexname"]: row["indexdef"] for row in rows}


def _stats_column_count(conn) -> int:
    rows = _read_rows(
        conn,
        "SELECT COUNT(*) AS n FROM pg_stats WHERE schemaname = current_schema() AND tablename = ?",
        (AGG,),
    )
    return rows[0]["n"]


# ---------------------------------------------------------------------------
# Harness: run scripts/swap_bulk_to_isbe.py main() against the test schema
# ---------------------------------------------------------------------------

WRITE_VERBS = {
    "DROP", "CREATE", "ALTER", "ANALYZE", "ANALYSE", "VACUUM", "REFRESH", "REINDEX", "CLUSTER", "TRUNCATE",
    "INSERT", "UPDATE", "DELETE", "GRANT", "REVOKE", "COMMENT",
}

PRIOR_STATES = {
    "nothing": [],
    "plain-view": ["CREATE VIEW {agg} AS SELECT 1 AS stale_marker"],
    "stale-materialized-view": ["CREATE MATERIALIZED VIEW {agg} AS SELECT 1 AS stale_marker"],
    "base-table": ["CREATE TABLE {agg} (stale_marker INTEGER)"],
    "base-table-with-legacy": [
        "CREATE TABLE {legacy} (stale_marker INTEGER)",
        "CREATE TABLE {agg} (stale_marker INTEGER)",
    ],
}


def _create_prior_state(conn, prior: str) -> None:
    for statement in PRIOR_STATES[prior]:
        conn.execute(statement.format(agg=AGG, legacy=AGG_LEGACY))
    conn.commit()


def _run_swap(monkeypatch, schema: str, *flags: str) -> list[str]:
    """Run swap_bulk_to_isbe.main() end to end inside ``schema``; return every statement it executed.

    Redirected: the connection (search_path = the test schema, lock_timeout so a stray lock fails instead of hanging)
    and the script's hard-coded ``'public'`` / ``public.`` references (rewritten to the test schema). Stubbed, because
    P6 does not touch it: the compat-view step (SWAP_PAIRS = [], COMPAT_VIEWS_DDL = a no-op SELECT).
    """
    import scripts.swap_bulk_to_isbe as swap

    real_psycopg = swap.psycopg
    dsn = os.environ["DATABASE_URL"]
    executed: list[str] = []
    opened: list = []

    class RecordingCursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def execute(self, sql, params=None):
            if isinstance(sql, str):
                sql = sql.replace("'public'", f"'{schema}'")
                sql = re.sub(r"\bpublic\.", f"{schema}.", sql)
                executed.append(sql)
            if params is None:
                self._cursor.execute(sql)
            else:
                self._cursor.execute(sql, params)
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self._cursor.close()
            return False

        def __getattr__(self, name):
            return getattr(self._cursor, name)

    class SchemaConnection:
        def __init__(self, connection):
            self._connection = connection

        def cursor(self, *args, **kwargs):
            return RecordingCursor(self._connection.cursor(*args, **kwargs))

        def execute(self, sql, params=None):
            return self.cursor().execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    class PsycopgProxy:
        def connect(self, conninfo=None, **kwargs):
            kwargs["options"] = f"-c search_path={schema} -c lock_timeout=10000"
            connection = real_psycopg.connect(dsn, **kwargs)
            opened.append(connection)
            return SchemaConnection(connection)

        def __getattr__(self, name):
            return getattr(real_psycopg, name)

    compat_stub = types.ModuleType("isbe_sunshine_etl")
    compat_stub.COMPAT_VIEWS_DDL = "SELECT 1;"

    monkeypatch.setattr(swap, "psycopg", PsycopgProxy())
    monkeypatch.setattr(swap, "SWAP_PAIRS", [])
    monkeypatch.setitem(sys.modules, "isbe_sunshine_etl", compat_stub)
    monkeypatch.setattr(sys, "path", list(sys.path))  # main() prepends scripts/ to sys.path
    monkeypatch.setattr(sys, "argv", ["swap_bulk_to_isbe.py", "--db-url", "unused-the-harness-connects-itself", *flags])
    try:
        swap.main()
    except SystemExit as exc:
        pytest.fail(f"swap main() exited with status {exc.code!r}", pytrace=False)
    except Exception as exc:  # noqa: BLE001 - the failure IS the finding
        pytest.fail(f"swap main() raised {type(exc).__name__}: {exc}", pytrace=False)
    finally:
        for connection in opened:
            connection.close()
    return executed


@pytest.fixture
def world(pg_test_schema):
    """(conn, schema): a connection to the per-test schema holding the ISBE world."""
    conn = get_db()
    _seed_isbe_world(conn)
    yield conn, pg_test_schema
    conn.close()


# ---------------------------------------------------------------------------
# (1) swap_bulk_to_isbe.py -- bulk_candidate_committee_finance_agg is a MATERIALIZED VIEW
# ---------------------------------------------------------------------------

SWAP_SOURCE_STRINGS = [
    "DROP VIEW IF EXISTS bulk_candidate_committee_finance_agg",
    "DROP MATERIALIZED VIEW IF EXISTS bulk_candidate_committee_finance_agg",
    "CREATE MATERIALIZED VIEW bulk_candidate_committee_finance_agg AS",
    "CREATE INDEX idx_bulk_candidate_committee_finance_agg_candidate ON bulk_candidate_committee_finance_agg (candidate_id);",
    "CREATE INDEX idx_bulk_candidate_committee_finance_agg_committee ON bulk_candidate_committee_finance_agg (committee_id_sbe);",
    "CREATE INDEX idx_bulk_candidate_committee_finance_agg_cycle ON bulk_candidate_committee_finance_agg (election_cycle);",
    "ANALYZE bulk_candidate_committee_finance_agg",
]


@pytest.mark.parametrize("fragment", SWAP_SOURCE_STRINGS, ids=lambda text: text[:70])
def test_swap_script_source_carries_the_contract_statements(fragment):
    """Pattern test (house style, like tests/test_perf_schema_patterns.py): the named statements are in the script's
    code, not only in a comment. The first fragment is green today; the scenario tests below prove they RUN."""
    assert fragment in _string_constants(SWAP_SCRIPT)


def test_the_aggregate_ddl_constant_creates_a_populated_materialized_view(world):
    """CANDIDATE_FINANCE_AGG_VIEW_SQL, executed alone against the ISBE world, yields the pinned matview.

    Kills: the constant left as CREATE VIEW while a second copy of the DDL is inlined in main() (this test executes
    the constant itself); WITH NO DATA (not populated); a changed SELECT (columns / rows differ); extra or missing
    columns (exact 19-name list); archived D-2s leaking into sums; the unlinked candidate 3 appearing."""
    from scripts.swap_bulk_to_isbe import CANDIDATE_FINANCE_AGG_VIEW_SQL

    conn, _schema = world
    header = _collapse(CANDIDATE_FINANCE_AGG_VIEW_SQL)[:80]
    assert header.startswith("CREATE MATERIALIZED VIEW bulk_candidate_committee_finance_agg AS"), header

    conn.execute(CANDIDATE_FINANCE_AGG_VIEW_SQL)
    conn.commit()

    _assert_agg_content(conn)


def test_the_aggregate_select_body_is_unchanged():
    """The contract says the SELECT is untouched: only the CREATE line changes. A SHA-256 of the collapsed body is the
    cheapest honest pin of that (readable diff: compare with `git show 2301286:scripts/swap_bulk_to_isbe.py`)."""
    from scripts.swap_bulk_to_isbe import CANDIDATE_FINANCE_AGG_VIEW_SQL

    body = _agg_body(CANDIDATE_FINANCE_AGG_VIEW_SQL)

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert digest == GOLDEN_BODY_SHA256, (
        f"the SELECT body of CANDIDATE_FINANCE_AGG_VIEW_SQL changed (now {len(body)} characters, expected "
        f"{GOLDEN_BODY_LENGTH}); P6 only swaps CREATE VIEW for CREATE MATERIALIZED VIEW"
    )


@pytest.mark.parametrize("prior", list(PRIOR_STATES))
def test_main_leaves_a_populated_materialized_view_whatever_was_there_before(world, monkeypatch, prior):
    """Kills: both DROPs run unconditionally (PostgreSQL raises on the wrong kind -- see the module docstring);
    only a plain view or only a matview is replaced; CREATE MATERIALIZED VIEW IF NOT EXISTS (the stale shape survives:
    the stale_marker column would remain); a rename-to-legacy that loses the old table; a base table swallowed
    instead of preserved as _legacy."""
    conn, schema = world
    _create_prior_state(conn, prior)

    _run_swap(monkeypatch, schema)

    _assert_agg_content(conn)
    if prior == "base-table":
        assert _relkind(conn, AGG_LEGACY) == "r", "the old base table must be kept as bulk_..._agg_legacy"
        assert "stale_marker" in _column_names(conn, AGG_LEGACY)
    if prior == "base-table-with-legacy":
        assert _relkind(conn, AGG_LEGACY) == "r", "an existing _legacy table must be left alone"
        assert _column_names(conn, AGG_LEGACY) == ["stale_marker"]


@pytest.mark.parametrize("prior", ["nothing", "plain-view"])
def test_main_builds_exactly_the_four_indexes_and_analyzes(world, monkeypatch, prior):
    """Three btree indexes plus the name GIN. Kills: a missing, misnamed or mis-targeted index (names and columns pinned exactly); an extra index (exact set);
    an index built on the OLD relation (it would vanish with it); no ANALYZE, or an ANALYZE of the old relation or
    before the CREATE (pg_stats holds one row per column only after ANALYZE ran on the NEW matview: 19)."""
    conn, schema = world
    _create_prior_state(conn, prior)

    _run_swap(monkeypatch, schema)

    definitions = _index_definitions(conn)
    assert set(definitions) == set(AGG_INDEXES) | {AGG_NAME_INDEX}
    for name, column in AGG_INDEXES.items():
        assert definitions[name].endswith(f" USING btree ({column})"), definitions[name]
    assert " USING gin (to_tsvector(" in definitions[AGG_NAME_INDEX], definitions[AGG_NAME_INDEX]
    assert "candidate_full_name" in definitions[AGG_NAME_INDEX], definitions[AGG_NAME_INDEX]
    assert _stats_column_count(conn) == len(AGG_COLUMNS) == 19


def test_main_rerun_rebuilds_from_the_current_source_rows(world, monkeypatch):
    """Second run on the first run's output, after the source changed (the manual re-run, or the weekly chain when the
    ETL did not cascade-drop the matview). Kills: an idempotent no-op / IF NOT EXISTS (stale numbers served as current
    D-2 money on /candidate-finance/ and in the race builder); a REFRESH-only path (the indexes then collide on the
    second run).

    Added source rows: D-2 912 (cmte 20, filed_doc 6002: 100 receipts, 40 expenditures, 60 ending funds) and a receipt
    (cmte 20, filed_doc 6002, 2023-12-01). Cand 2 / cmte 20 / 2023 now has 2 filings: receipts 5000 + 100 = 5100.0,
    expenditures 1200 + 40 = 1240.0, ending funds max(3800, 60) = 3800.0, period 2023-11-20 .. 2023-12-01."""
    conn, schema = world
    _run_swap(monkeypatch, schema)
    _assert_agg_content(conn)

    conn.execute(
        "INSERT INTO isbe_d2_reports (id, committee_id, filed_doc_id, total_receipts, total_expenditures, "
        "end_funds_available, archived) VALUES (912, 20, 6002, 100.0, 40.0, 60.0, FALSE)"
    )
    conn.execute("INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, received_date) VALUES (6, 20, 6002, '2023-12-01')")
    conn.commit()
    _run_swap(monkeypatch, schema)

    assert _relkind(conn, AGG) == "m"
    birch = [row for row in _agg_rows(conn) if row["candidate_id"] == 2]
    assert len(birch) == 1
    assert birch[0]["filing_count"] == 2
    assert birch[0]["sum_total_receipts"] == 5100.0
    assert birch[0]["sum_total_expenditures"] == 1240.0
    assert birch[0]["max_ending_funds_available"] == 3800.0
    assert birch[0]["period_start_date"] == "2023-11-20"
    assert birch[0]["period_end_date"] == "2023-12-01"
    assert len(_agg_rows(conn)) == 5
    assert set(_index_definitions(conn)) == set(AGG_INDEXES) | {AGG_NAME_INDEX}
    assert _stats_column_count(conn) == 19


def test_main_executes_the_module_constant_verbatim(world, monkeypatch):
    """main() must run CANDIDATE_FINANCE_AGG_VIEW_SQL itself (one source of truth), not a private copy of the DDL."""
    import scripts.swap_bulk_to_isbe as swap

    _conn, schema = world

    executed = _run_swap(monkeypatch, schema)

    assert _collapse(swap.CANDIDATE_FINANCE_AGG_VIEW_SQL) in _collapse(" ".join(executed))


@pytest.mark.parametrize("prior", ["plain-view", "stale-materialized-view", "base-table"])
def test_dry_run_reads_but_changes_nothing(world, monkeypatch, prior):
    """--dry-run is the safe preview: no write statement runs and the old object (plain view / matview / table) is
    untouched. Kills: the new statements (DROP MATERIALIZED, CREATE INDEX, ANALYZE, REFRESH) added without the dry-run
    guard."""
    conn, schema = world
    _create_prior_state(conn, prior)
    kind_before = _relkind(conn, AGG)
    # Step 6 of main() counts rows in isbe_race_candidacies; a dry run does not create it.
    conn.execute("CREATE VIEW isbe_race_candidacies AS SELECT 1 AS candidacy_id")
    conn.commit()

    executed = _run_swap(monkeypatch, schema, "--dry-run")

    writes = [text for text in executed if _collapse(text).split(" ")[0].upper() in WRITE_VERBS]
    assert writes == [], writes
    assert _relkind(conn, AGG) == kind_before
    assert _column_names(conn, AGG) == ["stale_marker"]
    assert _index_definitions(conn) == {}
    assert _stats_column_count(conn) == 0


# ---------------------------------------------------------------------------
# (2) database/schema.sql -- pg_trgm and the donor-name trigram index
# ---------------------------------------------------------------------------

EXTENSION_STATEMENT = "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
TRIGRAM_INDEX_NAME = "idx_analytics_donor_summary_name_trgm"
TRIGRAM_INDEX_STATEMENT = (
    "CREATE INDEX IF NOT EXISTS idx_analytics_donor_summary_name_trgm ON analytics_donor_summary "
    "USING gin (donor_name gin_trgm_ops);"
)
DONOR_TABLE_STATEMENT = "CREATE TABLE IF NOT EXISTS analytics_donor_summary ("


def _schema_code() -> str:
    """schema.sql without SQL comments, whitespace collapsed: a statement that only appears in a comment is not there."""
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    return _collapse(text)


def test_schema_enables_pg_trgm():
    assert EXTENSION_STATEMENT in _schema_code()


def test_schema_has_the_donor_name_trigram_index():
    """Pattern test: a GIN index with the trigram operator class on analytics_donor_summary.donor_name -- the column the
    search donors section filters with LIKE '%q%' (the compat layer runs it as ILIKE). Kills a btree index (cannot serve
    a leading-wildcard LIKE), an index on another column (donor_key, source), and a different index name."""
    assert TRIGRAM_INDEX_STATEMENT in _schema_code()


def test_schema_applies_the_extension_and_the_table_before_the_trigram_index():
    """schema.sql runs top to bottom as one script: gin_trgm_ops must exist when the index is created, and so must the
    table. Kills an index that is right in isolation but placed above the extension or above the table."""
    code = _schema_code()
    extension_at = code.find(EXTENSION_STATEMENT)
    table_at = code.find(DONOR_TABLE_STATEMENT)
    index_at = code.find(TRIGRAM_INDEX_STATEMENT)
    assert extension_at != -1, "no CREATE EXTENSION IF NOT EXISTS pg_trgm;"
    assert table_at != -1, "analytics_donor_summary is no longer created in schema.sql"
    assert index_at != -1, "no trigram index statement"
    assert extension_at < index_at, "CREATE EXTENSION must come before the gin_trgm_ops index"
    assert table_at < index_at, "the index must come after CREATE TABLE analytics_donor_summary"


# Search program Phase 2 (decision P2-2): the entity tables the lobbying / 527 list filters now match by token get the GIN
# expression index on the contract expression (NAME_TSVECTOR_SQL spelled out), created after their table.
PHASE2_NAME_TSVECTOR = "to_tsvector('simple', regexp_replace(upper(coalesce({expr}, '')), '[^A-Z0-9 ]', ' ', 'g'))"
PHASE2_NAME_INDEXES = [
    ("idx_lobbying_entities_name_tsv", "lobbying_entities", "entity_name"),
    ("idx_lobbying_clients_name_tsv", "lobbying_clients", "client_name"),
    ("idx_irs527_organizations_name_tsv", "irs527_organizations", "org_name"),
]


@pytest.mark.parametrize("name, table, column", PHASE2_NAME_INDEXES, ids=[spec[0] for spec in PHASE2_NAME_INDEXES])
def test_schema_has_the_phase2_name_index_after_its_table(name, table, column):
    """Pattern test: the exact statement (IF NOT EXISTS, GIN, the contract expression on the bare column). Kills a btree
    index, an index on lower(col) or to_tsvector(col) (the predicate could not use it), a misnamed index, an index above
    its CREATE TABLE (schema.sql runs top to bottom)."""
    code = _schema_code()
    statement = f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING gin ({PHASE2_NAME_TSVECTOR.format(expr=column)});"
    index_at = code.find(statement)
    table_at = code.find(f"CREATE TABLE IF NOT EXISTS {table} (")
    assert index_at != -1, f"schema.sql lacks: {statement}"
    assert table_at != -1 and table_at < index_at, f"{name} must come after CREATE TABLE {table}"


@pytest.mark.integration
def test_trigram_index_is_built_by_the_schema_and_serves_the_donor_name_search(pg_test_schema):
    """Needs pg_trgm in the test database (skipped where it cannot be had; CI runs `-m "not integration"`).

    Behaviour, not text: init_db() applies schema.sql to the test schema; the index exists as GIN + gin_trgm_ops on
    donor_name, and with sequential scans disabled the planner picks it for the exact statement shape of the search
    donors section. Kills an index the planner cannot use for that predicate (e.g. built on LOWER(donor_name))."""
    conn = get_db()
    available = _read_rows(conn, "SELECT 1 AS one FROM pg_available_extensions WHERE name = 'pg_trgm'")
    if not available:
        pytest.skip("pg_trgm is not available in the test database")
    init_db()

    rows = _read_rows(
        conn,
        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
        "AND tablename = 'analytics_donor_summary' AND indexname = ?",
        (TRIGRAM_INDEX_NAME,),
    )
    assert len(rows) == 1, f"{TRIGRAM_INDEX_NAME} was not created by schema.sql"
    assert re.search(r"USING gin \(donor_name (?:\w+\.)?gin_trgm_ops\)", rows[0]["indexdef"]), rows[0]["indexdef"]

    for number in range(3):
        conn.execute(
            "INSERT INTO analytics_donor_summary (source, donor_key, donor_name, total_amount, contribution_count, "
            "committee_count) VALUES ('bulk_receipts', ?, ?, 10.0, 1, 1)",
            (f"zebra-{number}", f"Zebra Donor {number}"),
        )
    conn.commit()
    conn.execute("SET enable_seqscan = off")
    plan = conn.execute(
        "EXPLAIN SELECT source, donor_key, donor_name FROM analytics_donor_summary WHERE donor_name LIKE ? "
        "ORDER BY total_amount DESC, donor_name ASC LIMIT 50",
        ("%zebra%",),
    ).fetchall()
    conn.commit()
    plan_text = " ".join(str(value) for row in plan for value in row.values())
    assert TRIGRAM_INDEX_NAME in plan_text, plan_text


# ---------------------------------------------------------------------------
# (3) the search-results cache is a shared RouteCache
# ---------------------------------------------------------------------------
#
# Every test uses its own random lower-case query token, so the process-wide cache can never carry an entry from one
# test into another. Hit or miss is read off the page: after the first request a donor named "<Token> Latecomer" is
# inserted; a response that omits it was served from the cache, one that shows it was computed again.

class FakeRedis:
    """The slice of redis-py that RouteCache uses; remembers the TTL each key was stored with."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def delete(self, *keys):
        removed = 0
        for key in keys:
            self.ttls.pop(key, None)
            if self.values.pop(key, None) is not None:
                removed += 1
        return removed

    def scan(self, cursor=0, match=None, count=None):
        prefix = (match or "*").rstrip("*")
        return 0, [key for key in self.values if key.startswith(prefix)]


SEARCH_KEY_PREFIX = "ilcf:cache:search_results:"


def _clear_search_cache() -> None:
    cache = main_routes._search_results_cache
    for method in ("invalidate", "clear"):
        if hasattr(cache, method):
            getattr(cache, method)()
            return


@pytest.fixture(autouse=True)
def _isolated_search_cache(monkeypatch):
    """Never touch a developer's Redis (same technique as tests/test_cache_backend.py) and start each test with an
    empty module-level search cache."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    _clear_search_cache()
    yield
    _clear_search_cache()


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: fake)
    return fake


@pytest.fixture
def sql_log(monkeypatch):
    """(sql, args, kwargs) of every statement the compat layer runs while the test runs."""
    from database.pg_compat import PostgresCompatCursor

    log: list[tuple] = []
    original = PostgresCompatCursor.execute

    def recording(self, sql, *args, **kwargs):
        log.append((sql, args, kwargs))
        return original(self, sql, *args, **kwargs)

    monkeypatch.setattr(PostgresCompatCursor, "execute", recording)
    return log


def _unique_token() -> str:
    """Ten random lower-case letters: no digits, so the query is neither a filed-doc id nor a donor-key lookup."""
    return "".join(chr(97 + int(char, 16) % 26) for char in uuid.uuid4().hex[:10])


def _display(token: str) -> str:
    return token.capitalize()


def _original(token: str) -> str:
    return f"{_display(token)} Original"


def _latecomer(token: str) -> str:
    return f"{_display(token)} Latecomer"


def _add_donor(conn, name: str, *, amount: float = 100.0) -> None:
    conn.execute(
        "INSERT INTO analytics_donor_summary (source, donor_key, donor_name, total_amount, contribution_count, "
        "committee_count) VALUES ('bulk_receipts', ?, ?, ?, 1, 1)",
        (f"key|{name.lower()}", name, amount),
    )
    conn.commit()


def _seed_search_world(conn, token: str) -> None:
    """One committee and one donor whose names contain the token."""
    conn.execute(
        "CREATE TABLE isbe_committees (id INTEGER PRIMARY KEY, name TEXT, type TEXT, party TEXT, city TEXT, "
        "state TEXT, status_date TEXT)"
    )
    conn.execute(
        "INSERT INTO isbe_committees (id, name, type, party, city, state) VALUES (?, ?, 'Candidate', 'Democratic', "
        "'Chicago', 'IL')",
        (4100, f"{_display(token)} Friends Committee"),
    )
    conn.commit()
    _add_donor(conn, _original(token))


def _app_config(db_path: str, **overrides) -> dict:
    config = {
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "DASHBOARD_PREWARM_ENABLED": False,
        "ROUTE_PERF_CACHE_ENABLED": True,
        "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": 0,
        # The 700 ms section budget is real since P2; a loaded machine must not turn a tiny query into a timed-out
        # section (which is never cached and would fail these tests for the wrong reason).
        "SEARCH_QUERY_TIMEOUT_MS": 30000,
    }
    config.update(overrides)
    return config


@pytest.fixture
def search_db(tmp_path):
    """Factory: a seeded database for one token; returns its path."""

    def build(token: str) -> str:
        db_path = str(tmp_path / "search_perf.db")
        init_db(db_path)
        conn = get_db(db_path)
        _seed_search_world(conn, token)
        conn.close()
        return db_path

    return build


@pytest.fixture
def make_app():
    """Factory: build an app on an existing database. Call it AFTER the test installed its monkeypatches."""

    def build(db_path: str, **overrides):
        return create_app(_app_config(db_path, **overrides))

    return build


def _insert_donor(db_path: str, name: str) -> None:
    conn = get_db(db_path)
    try:
        _add_donor(conn, name)
    finally:
        conn.close()


def _search(client, query: str, **params) -> str:
    response = client.get("/search", query_string={"q": query, **params})
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _statements_mentioning(log: list[tuple], token: str) -> list[tuple]:
    return [entry for entry in log if token in repr(entry).lower()]


def test_the_search_cache_is_a_route_cache_named_search_results():
    """The contract's object: shared through Redis when REDIS_URL is set, instead of a per-worker OrderedDict."""
    cache = main_routes._search_results_cache
    assert isinstance(cache, RouteCache), f"_search_results_cache is a {type(cache).__name__}, not a RouteCache"
    assert cache.name == "search_results"


HIT_VARIANTS = {
    "identical-request": lambda token: (token, {}),
    "upper-case-query": lambda token: (token.upper(), {}),
    "padded-query": lambda token: (f"  {token}  ", {}),
    "irrelevant-extra-parameter": lambda token: (token, {"utm_source": "newsletter"}),
}


@pytest.mark.parametrize("variant", list(HIT_VARIANTS))
def test_a_repeated_search_is_served_from_the_cache_without_running_the_sections(
    search_db, make_app, sql_log, variant
):
    """Cache key = (resolved scope, type, lower-cased query): the variants differ only in what the key ignores.

    Observed twice: the donor inserted after the first request is NOT on the second page, and the second request runs no
    section statement (none of its SQL mentions the query). Kills: no caching; a key that includes the raw URL, the
    case of the query, its padding or tracking parameters; a cache that stores but still recomputes."""
    token = _unique_token()
    db_path = search_db(token)
    client = make_app(db_path).test_client()

    first = _search(client, token)
    assert _original(token) in first, "control: the first request must find the seeded donor"
    first_statements = len(_statements_mentioning(sql_log, token))
    assert first_statements >= 2, "control: the SQL recorder must see the section statements of a cold request"
    _insert_donor(db_path, _latecomer(token))
    statements_before = len(_statements_mentioning(sql_log, token))

    query, params = HIT_VARIANTS[variant](token)
    second = _search(client, query, **params)

    assert _original(token) in second
    assert _latecomer(token) not in second, "recomputed: the new donor shows, so the cache was not used"
    assert len(_statements_mentioning(sql_log, token)) == statements_before, "a hit ran section SQL"


MISS_VARIANTS = {
    "shorter-query": lambda token: (token[:-1], {}),
    "other-type": lambda token: (token, {"type": "donors"}),
    "other-scope-cycle": lambda token: (token, {"cycle": "2022"}),
    "other-scope-all-time": lambda token: (token, {"period": "all"}),
}


@pytest.mark.parametrize("variant", list(MISS_VARIANTS))
def test_a_different_query_type_or_scope_is_a_cache_miss(search_db, make_app, variant):
    """Each variant changes exactly one key part against a cached default request for the same token. The shorter query
    also matches the later donor (it is a prefix of the token), so a stale answer is detectable. Kills: a key that
    ignores the query (one global slot), the type, or the resolved scope (metric cross-talk between windows)."""
    token = _unique_token()
    db_path = search_db(token)
    client = make_app(db_path).test_client()
    assert _original(token) in _search(client, token), "control: the first request must find the seeded donor"
    _insert_donor(db_path, _latecomer(token))

    query, params = MISS_VARIANTS[variant](token)
    second = _search(client, query, **params)

    assert _original(token) in second
    assert _latecomer(token) in second, "served the cached default payload for a different key"


def test_the_type_is_part_of_the_key_in_the_other_direction_too(search_db, make_app):
    """A donors-only payload (committees skipped, so cached as []) must not answer a type=all request. Kills a key
    without the type from the other side: type=donors first, then type=all must still list the committee."""
    token = _unique_token()
    db_path = search_db(token)
    client = make_app(db_path).test_client()
    donors_only = _search(client, token, type="donors")
    assert _original(token) in donors_only
    assert f"{_display(token)} Friends Committee" not in donors_only, "control: type=donors lists no committees"

    everything = _search(client, token, type="all")

    assert f"{_display(token)} Friends Committee" in everything
    assert _original(token) in everything


def test_a_disabled_route_cache_is_neither_read_nor_written(search_db, make_app):
    """ROUTE_PERF_CACHE_ENABLED=False (the TESTING default): requests always run the sections, and store nothing a
    later cache-enabled app could be served.

    Read side: the enabled app caches the default request; the donor added afterwards must show on the disabled app's
    answer for the same key. Write side: the disabled app answers the shorter query (donors Original + Latecomer),
    then a third donor is added; an enabled app asked the same shorter query must see all three. Kills: a cache that
    ignores the flag on read, or on write."""
    token = _unique_token()
    db_path = search_db(token)
    enabled = make_app(db_path, ROUTE_PERF_CACHE_ENABLED=True).test_client()
    disabled = make_app(db_path, ROUTE_PERF_CACHE_ENABLED=False).test_client()
    assert _original(token) in _search(enabled, token), "control: the enabled app answers and fills its cache"
    _insert_donor(db_path, _latecomer(token))

    assert _latecomer(token) in _search(disabled, token), "read side: the disabled app served a cached entry"

    prefix = token[:-1]
    assert _latecomer(token) in _search(disabled, prefix), "control: the shorter query matches both donors"
    _insert_donor(db_path, f"{_display(token)} Third")
    answer = _search(enabled, prefix)
    assert f"{_display(token)} Third" in answer, "write side: the disabled app stored its result"


def test_search_results_are_stored_in_the_shared_cache_with_the_configured_ttl(search_db, make_app, fake_redis):
    """With Redis present the entry lands under the RouteCache namespace "search_results" (ilcf:cache:search_results:*,
    so flush_all_route_caches() clears it after an import), exactly one entry per request, with the TTL taken from
    config. Kills: the per-worker dict (nothing reaches Redis); a private key namespace the import-time flush misses;
    a hard-coded TTL (120, 21600 or the 15 s floor); one key per section."""
    token = _unique_token()
    db_path = search_db(token)
    client = make_app(db_path, SEARCH_RESULTS_CACHE_TTL_SECONDS=4321).test_client()

    _search(client, token)

    entries = {key: value for key, value in fake_redis.values.items() if key.startswith(SEARCH_KEY_PREFIX)}
    assert len(entries) == 1, f"expected one entry under {SEARCH_KEY_PREFIX}*, Redis holds {sorted(fake_redis.values)}"
    (key, stored), = entries.items()
    assert fake_redis.ttls[key] == 4321
    assert _original(token) in stored, "the stored payload must hold the actual results"


def test_a_hit_is_read_from_the_shared_layer_not_from_process_local_state(search_db, make_app, fake_redis):
    """What another gunicorn worker stored must be what this worker serves. After the first request the stored entry is
    edited in Redis (the donor's name swapped for a marker, whatever the payload's shape); the next identical request
    must show the marker. Kills: a cache that writes to Redis but answers from a private per-process dict (3 workers,
    3 cold builds), which is the state this unit exists to leave."""
    token = _unique_token()
    db_path = search_db(token)
    client = make_app(db_path).test_client()
    assert _original(token) in _search(client, token)
    stored = [key for key in fake_redis.values if key.startswith(SEARCH_KEY_PREFIX)]
    assert len(stored) == 1, "the first request must have stored its results in the shared cache"
    planted_name = f"{_display(token)} Planted By Another Worker"
    assert _original(token) in fake_redis.values[stored[0]], "control: the stored payload names the donor"
    fake_redis.values[stored[0]] = fake_redis.values[stored[0]].replace(_original(token), planted_name)

    second = _search(client, token)

    assert planted_name in second
    assert _original(token) not in second


def test_the_import_time_flush_clears_cached_searches(search_db, make_app, fake_redis):
    """The six-hour TTL is only safe because the weekly refresh calls flush_all_route_caches() (everything under
    ilcf:cache:*). After that call the next identical request must be computed again. Kills: a private Redis namespace,
    or a process-local copy that outlives the flush -- either would serve last week's search results for six hours."""
    token = _unique_token()
    db_path = search_db(token)
    client = make_app(db_path).test_client()
    assert _original(token) in _search(client, token)
    assert [key for key in fake_redis.values if key.startswith(SEARCH_KEY_PREFIX)], "nothing reached the shared cache"
    _insert_donor(db_path, _latecomer(token))
    assert _latecomer(token) not in _search(client, token), "control: before the flush the answer is cached"

    deleted = cache_backend.flush_all_route_caches()

    assert deleted >= 1
    assert _latecomer(token) in _search(client, token)


def _results_block(page: str) -> str:
    match = re.search(r'<div class="search-results">(.*?)</div>', page, flags=re.S)
    assert match is not None, "the page has no search-results block"
    return match.group(1)


def test_a_page_rebuilt_from_the_shared_cache_renders_exactly_like_the_computed_one(search_db, make_app, fake_redis):
    """Redis stores JSON, so a hit hands the template dicts rebuilt by json.loads (floats, ints, strings, nested
    provenance) instead of the original objects. The second block must equal the first, character for character, and
    must really be a hit (the donor added in between is absent). Kills: a payload that does not survive the JSON round
    trip (datetimes/Decimals turned into strings that break format_currency, rows that are not plain dicts)."""
    token = _unique_token()
    db_path = search_db(token)
    conn = get_db(db_path)
    conn.execute(
        "CREATE TABLE bulk_candidate_committee_finance_agg (candidate_id INTEGER, candidate_full_name TEXT, "
        "office_sought TEXT, committee_id_sbe INTEGER, sum_total_receipts REAL, period_end_date TEXT)"
    )
    conn.execute(
        "INSERT INTO bulk_candidate_committee_finance_agg VALUES (8100, ?, 'State Senator', 4100, 1234.5, '2025-06-30')",
        (f"{_display(token)} Candidate",),
    )
    conn.commit()
    conn.close()
    client = make_app(db_path).test_client()

    computed = _results_block(_search(client, token))
    assert f"{_display(token)} Candidate" in computed, "control: the candidate section is part of the compared block"
    assert "$1,234.50" in computed
    assert [key for key in fake_redis.values if key.startswith(SEARCH_KEY_PREFIX)], "nothing reached the shared cache"
    _insert_donor(db_path, _latecomer(token))
    rebuilt = _results_block(_search(client, token))

    assert _latecomer(token) not in rebuilt, "the second request was recomputed, so nothing was compared"
    assert rebuilt == computed


TTL_ENV = "SEARCH_RESULTS_CACHE_TTL_SECONDS"


def _ttl_in_fresh_process(env_value):
    """config.py reads the environment at import time, so look at it in a fresh interpreter (as test_request_hygiene)."""
    env = {key: value for key, value in os.environ.items() if key != TTL_ENV}
    if env_value is not None:
        env[TTL_ENV] = env_value
    script = textwrap.dedent(
        """
        import json
        import config
        from webapp.app import create_app

        app = create_app({"TESTING": True, "DASHBOARD_PREWARM_ENABLED": False})
        print(json.dumps({
            "config_module": config.SEARCH_RESULTS_CACHE_TTL_SECONDS,
            "app_config": app.config["SEARCH_RESULTS_CACHE_TTL_SECONDS"],
        }))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_search_cache_ttl_defaults_to_six_hours():
    """21600 s, like the other hours-long route caches (DASHBOARD_*, ANALYTICS_*): the weekly import flushes Redis, so a
    long TTL costs no freshness. Today's default is 120 s."""
    values = _ttl_in_fresh_process(None)
    assert values["config_module"] == 21600
    assert values["app_config"] == 21600


def test_search_cache_ttl_still_honours_the_environment_variable():
    """Guard (green today): the default moves, the env override stays."""
    values = _ttl_in_fresh_process("123")
    assert values["config_module"] == 123
    assert values["app_config"] == 123


# ---------------------------------------------------------------------------
# (4) the candidates section reads a MATERIALIZED aggregate
# ---------------------------------------------------------------------------
#
# Aggregate rows (the 19 production columns; hand-computed expectations beside them). Query "quillfeather":
#   cand 7001 Odette Quillfeather, State Senator (District 12), 3 rows over committees 801 and 802:
#       801 / 2025: 1000.0   801 / 2023: 250.5   802 / 2025: 49.5      -> total 1000.0 + 250.5 + 49.5 = 1300.0
#       committees = COUNT(DISTINCT committee) = 2; latest period end = MAX of '2025-03-15', '2023-06-30', '2025-08-31'
#       = '2025-08-31'
#   cand 7002 Perry Quillfeather, Governor, committee 803, one row: total 5000.0, 1 committee, latest '2025-12-01'
#   cand 7003 Unrelated Person: does not match.
# Section order: total_receipts DESC -> Perry (5000.0) before Odette (1300.0). Page: "$5,000.00" / "$1,300.00".

AGG_SEED_ROWS = """
    (7001, 'Odette Quillfeather', 'State Senator', 'Senate District', '12', 'Democratic', 801, 'Friends of Quillfeather',
     'Candidate', 'Democratic', 2025, 2026, 2, 1000.0, 400.0, 600.0, 0, '2025-01-05', '2025-03-15'),
    (7001, 'Odette Quillfeather', 'State Senator', 'Senate District', '12', 'Democratic', 801, 'Friends of Quillfeather',
     'Candidate', 'Democratic', 2023, 2024, 1, 250.5, 100.0, 150.5, 0, '2023-02-01', '2023-06-30'),
    (7001, 'Odette Quillfeather', 'State Senator', 'Senate District', '12', 'Democratic', 802, 'Quillfeather Victory Fund',
     'Candidate', 'Democratic', 2025, 2026, 1, 49.5, 10.0, 39.5, 0, '2025-02-01', '2025-08-31'),
    (7002, 'Perry Quillfeather', 'Governor', 'Statewide', NULL, 'Republican', 803, 'Perry for Governor',
     'Candidate', 'Republican', 2025, 2026, 4, 5000.0, 2500.0, 2500.0, 1, '2025-04-01', '2025-12-01'),
    (7003, 'Unrelated Person', 'Mayor', 'City', NULL, NULL, 804, 'Unrelated Committee',
     'Candidate', NULL, 2025, 2026, 1, 10.0, 5.0, 5.0, 0, '2025-01-01', '2025-01-31')
"""

AGG_SEED_DDL = """
    CREATE TABLE agg_seed_rows (
        candidate_id INTEGER, candidate_full_name TEXT, office_sought TEXT, district_type TEXT, district TEXT,
        candidate_party_affiliation TEXT, committee_id_sbe INTEGER, committee_name TEXT, committee_type TEXT,
        committee_party_affiliation TEXT, period_year INTEGER, election_cycle INTEGER, filing_count BIGINT,
        sum_total_receipts REAL, sum_total_expenditures REAL, max_ending_funds_available REAL,
        archived_filing_count INTEGER, period_start_date TEXT, period_end_date TEXT
    )
"""

AGG_KINDS = {
    "plain-view-control": "VIEW",
    "materialized-view": "MATERIALIZED VIEW",
}


def _seed_aggregate(conn, kind_sql: str) -> None:
    conn.execute(AGG_SEED_DDL)
    conn.execute(f"INSERT INTO agg_seed_rows VALUES {AGG_SEED_ROWS}")
    conn.execute(f"CREATE {kind_sql} {AGG} AS SELECT * FROM agg_seed_rows")
    conn.commit()


@pytest.fixture(params=list(AGG_KINDS))
def aggregate_world(request, tmp_path):
    """(db_path, conn): a database whose candidate aggregate is a plain view (control) or a materialized view, plus one
    committee and one donor matching the query so the page has all three sections."""
    db_path = str(tmp_path / "search_matview.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_aggregate(conn, AGG_KINDS[request.param])
    conn.execute(
        "CREATE TABLE isbe_committees (id INTEGER PRIMARY KEY, name TEXT, type TEXT, party TEXT, city TEXT, "
        "state TEXT, status_date TEXT)"
    )
    conn.execute(
        "INSERT INTO isbe_committees (id, name, type, party, city, state) VALUES "
        "(801, 'Friends of Quillfeather', 'Candidate', 'Democratic', 'Chicago', 'IL')"
    )
    conn.commit()
    _add_donor(conn, "Quillfeather Donor", amount=75.0)
    yield db_path, conn
    conn.close()


def test_local_candidates_section_reads_the_aggregate_whatever_its_kind(aggregate_world):
    """Direct call of the section function, value by value.

    Fails today for the materialized view because the compat layer's PRAGMA table_info (behind _column_exists) is empty
    for a materialized view -- information_schema.columns leaves them out -- so the section drops office_sought and
    period_end_date (office '' and no latest period). The fix may live in pg_compat or in main.py; the behaviour is
    pinned here. Kills: a section that treats 'columns not found' as 'columns absent'."""
    _db_path, conn = aggregate_world

    found = main_routes._search_local_candidates(conn, "quillfeather", limit=30)

    assert [row["candidate_id"] for row in found] == [7002, 7001]
    perry, odette = found
    assert perry["candidate_name"] == "Perry Quillfeather"
    assert perry["office"] == "Governor"
    assert perry["committee_count"] == 1
    assert perry["total_receipts"] == 5000.0
    assert perry["latest_period_end"] == "2025-12-01"
    assert perry["provenance"]["sync_timestamp"] == "2025-12-01"
    assert odette["candidate_name"] == "Odette Quillfeather"
    assert odette["office"] == "State Senator"
    assert odette["committee_count"] == 2
    assert odette["total_receipts"] == 1300.0
    assert odette["latest_period_end"] == "2025-08-31"
    assert odette["provenance"]["sync_timestamp"] == "2025-08-31"
    assert odette["provenance"]["source_table"] == AGG
    assert perry["scope"] == odette["scope"] == "local"


def test_search_page_renders_every_section_and_complete_candidate_rows(aggregate_world, make_app):
    """End to end through GET /search: committees, donors and candidates all render, no section is reported failed or
    timed out, and each candidate row carries its own office, total, committee count and latest period."""
    db_path, _conn = aggregate_world
    client = make_app(db_path, ROUTE_PERF_CACHE_ENABLED=False).test_client()

    page = _search(client, "quillfeather", type="all")

    text = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    assert "Committees (1)" in text
    assert "Donors (1)" in text
    assert "Candidates (2)" in text
    assert "Some sections failed" not in text
    assert "timed out" not in text
    section = re.search(r"<h2>Candidates \(2\).*?</section>", page, flags=re.S)
    assert section is not None
    rows = re.findall(r"<tr>(.*?)</tr>", section.group(0), flags=re.S)[1:]  # [0] is the header row
    assert len(rows) == 2
    perry = " ".join(re.sub(r"<[^>]+>", " ", rows[0]).split())
    odette = " ".join(re.sub(r"<[^>]+>", " ", rows[1]).split())
    assert "Perry Quillfeather" in perry
    assert "Governor" in perry
    assert "$5,000.00" in perry
    assert "1 committees" in perry
    assert "Sync timestamp: 2025-12-01" in perry
    assert "Odette Quillfeather" in odette
    assert "State Senator" in odette
    assert "$1,300.00" in odette
    assert "2 committees" in odette
    assert "Sync timestamp: 2025-08-31" in odette


CANDIDATE_FINANCE_CYCLES = {
    # cycle 2024 holds ONE aggregate row: cand 7001 / committee 801 / period 2023 (250.5 receipts).
    "2024": [("7001", "801", "2023", "2024", 250.5, "2023-02-01", "2023-06-30")],
    # cycle 2026 holds four rows, ordered by receipts DESC (the page default): 7002/803 5000.0, 7001/801 1000.0,
    # 7001/802 49.5, 7003/804 10.0.
    "2026": [
        ("7002", "803", "2025", "2026", 5000.0, "2025-04-01", "2025-12-01"),
        ("7001", "801", "2025", "2026", 1000.0, "2025-01-05", "2025-03-15"),
        ("7001", "802", "2025", "2026", 49.5, "2025-02-01", "2025-08-31"),
        ("7003", "804", "2025", "2026", 10.0, "2025-01-01", "2025-01-31"),
    ],
}


@pytest.mark.parametrize("cycle", list(CANDIDATE_FINANCE_CYCLES))
def test_candidate_finance_list_keeps_its_cycle_filter_and_period_columns(aggregate_world, make_app, cycle):
    """/candidate-finance/ is the aggregate's other big reader (the plan lists it beside search). It learns which columns
    exist from PRAGMA table_info (CandidateCommitteeFinanceAgg._column_names) and, with none found, silently drops the
    election_cycle filter and blanks period_year / election_cycle / period dates -- the page would show every cycle's
    money under a single-cycle scope label. Checked through the CSV export (one parseable row per aggregate row)."""
    db_path, _conn = aggregate_world
    client = make_app(db_path, ROUTE_PERF_CACHE_ENABLED=False).test_client()

    response = client.get("/candidate-finance/", query_string={"cycle": cycle, "format": "csv"})

    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    want = CANDIDATE_FINANCE_CYCLES[cycle]
    assert len(rows) == len(want)
    for row, (candidate, committee, period_year, election_cycle, receipts, start, end) in zip(rows, want):
        assert row["candidate_id"] == candidate
        assert row["committee_id_sbe"] == committee
        assert row["period_year"] == period_year
        assert row["election_cycle"] == election_cycle
        assert float(row["sum_total_receipts"]) == receipts
        assert row["period_start_date"] == start
        assert row["period_end_date"] == end
