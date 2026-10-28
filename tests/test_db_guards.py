"""Unit P2 -- web-connection guards, a real search timeout, catalog-probe memo,
snapshot age in any time zone.

Plan: .claude/plans/load-times.md, rows L2 / L9 / L10, decisions LD3 / LD5 / LD6.
The decisions fix the behaviour; this file pins it.

Contract these tests pin (every name is matched exactly)
  config.py  WEB_STATEMENT_TIMEOUT_MS = 30000 and WEB_MAX_PARALLEL_WORKERS_PER_GATHER = 2,
             int module constants read from the same-named environment variables; create_app
             copies both into app.config, so create_app({...}) can override them.
  Web connection = whatever app.get_database() hands out (get_db(..., web=True), a connect
             option or a post-connect helper: the implementer's choice). For its whole life it
             has statement_timeout = the configured ms, max_parallel_workers_per_gather = the
             configured int and application_name = "ilcf-web"; the three survive rollbacks;
             they apply under TESTING too. database.connection.get_db(dsn) with no web flag
             (CLI, prewarm and warm builders) keeps the server defaults.
  database.pg_compat.statement_timeout_scope(conn, ms)  context manager. Inside it a statement
             that outlives ms raises psycopg.errors.QueryCanceled (a subclass is fine); on
             exit, normal or not, the budget is gone and the connection is usable.
  webapp.routes.main._run_query_with_timeout(conn, query_fn, timeout_ms)
             -> (result, elapsed_ms, timed_out); on QueryCanceled: ([], elapsed, True); any
             other error propagates; it uses statement_timeout_scope, not set_progress_handler.
  Catalog-probe memo (compat layer: the sqlite_master and PRAGMA table_info emulation that every
             _table_exists / _column_exists copy reaches): a probe repeated for the same
             (database, schema, name) issues no SQL statement at all, positive and negative
             answers alike, from ANY connection in the process. PRAGMA temp.table_info is never
             memoized. DDL run through the compat layer in this process (execute or
             executescript) is visible to the next probe without the hook.
  database.pg_compat.invalidate_catalog_memo()  no arguments; drops the whole memo AND the
             get_analytics_data_sources row count, so changes made outside this process show up.
  Catalog-memo staleness (review round R3, findings C7 / C19 / C8; plan load-times.md "Review fixes")
             database.pg_compat._CATALOG_MEMO_TTL_SECONDS == 300.0 and the new
             _CATALOG_MEMO_NEGATIVE_TTL_SECONDS == 5.0: schema_memo keeps a falsy value (False, [], 0)
             for the short ttl and any other value for the long one, so a table, view or column that
             another process creates shows up without the hook within seconds. The compat cursor's
             execute AND executemany call invalidate_catalog_memo() before their rollback when the
             statement raises psycopg.errors.UndefinedTable or UndefinedColumn (a stale "present"
             answer heals on the first failed statement); any other error leaves the memo alone.
             scripts/swap_bulk_to_isbe.py, run as a script the way `run.py sunshine-import` launches
             it, ends with flush_all_route_caches().
  database.analytics.get_analytics_data_sources(conn)  still returns exactly the keys
             donor_flow_source / reconciliation_available / reconciliation_rows, but runs the
             COUNT(*) over bulk_d2_receipts_recon once per (database, schema), whichever
             connection asks, until the hook.
  database.federal_fec.get_federal_view_snapshot  age_seconds (a float) and is_fresh come out
             right whatever the session time zone: UTC, America/Chicago (-5/-6) or Asia/Tokyo (+9).

Fixture facts (hand-computed). All durations are seconds old, ttl_seconds=600:
  10 s   -> fresh      540 s -> fresh (60 s inside the ttl)
  660 s  -> stale (60 s past the ttl)       7200 s -> stale
  Tolerance on age_seconds is +-30 s: the row is back-dated with CURRENT_TIMESTAMP inside the
  same database, so only the test's own runtime separates the written age from the read age.
  Schema A holds memo_shared(id, only_in_a); schema B holds memo_shared(id) and only_in_b(id);
  probes (table memo_shared, column only_in_a of memo_shared, table only_in_b) read
  (True, True, False) in A and (True, False, True) in B.
  Memo clock (the ttl tests): time.monotonic() is shifted ahead. A falsy entry (ttl 5 s) is probed at
  +2.5 s (still the old answer) and +10 s (re-read); a truthy one (ttl 300 s) at +150 s and +600 s.
  _column_exists memoizes the table's bool AND the column LIST, so only an absent TABLE is a falsy
  memo value: the column_added case (probe answers False, memo is the list [id]) ages out at 300 s.

Accepted holes (nothing here can see them):
  * the database (DSN) half of the memo key: the suite uses one database;
  * ambiguous naive timestamps in the one DST fall-back hour a year (inherent to LD5);
  * the timeout is per statement (LD3), so a multi-statement section can exceed its budget in
    total; nothing pins the cumulative case.
  * invalidate-BEFORE-rollback ordering (R3): nothing here makes rollback() itself raise;
  * falsy memo values other than False / [] / 0 (None, ""): no caller memoizes them;
  * a column-level "absent" answer lives the long ttl (the memo holds the column list), by design;
  * the swap's real SQL and a live Redis: the script-mode test runs main() on a fake connection;
    tests/test_search_perf.py (real schema) and tests/test_cache_lifecycle.py (Redis) cover those halves.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest

import config as config_module
import database.connection as connection_module
import webapp.app as app_module
from database.analytics import get_analytics_data_sources
from database.connection import get_db, init_db
from database.federal_fec import get_federal_view_snapshot, save_federal_view_snapshot
from database.pg_compat import PostgresCompatConnection
from webapp.app import create_app
from webapp.routes import main as main_routes

REPO_ROOT = Path(__file__).resolve().parent.parent
SWAP_SCRIPT = REPO_ROOT / "scripts" / "swap_bulk_to_isbe.py"

# Captured while pytest collects, before any autouse fixture swaps
# database.connection.get_db for the schema-isolated stand-in.
_REAL_GET_DB = connection_module.get_db

WEB_APPLICATION_NAME = "ilcf-web"
OVERRIDE_TIMEOUT_MS = 12345  # never a server default, and SHOW prints it as "12345ms"
SNAPSHOT_TYPE = "federal_overview"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dsn() -> str:
    # tests/conftest.py exports the test DSN as DATABASE_URL for every test.
    return os.environ["DATABASE_URL"]


def _api(module_name: str, attribute: str):
    """A product name that does not exist yet fails the test with a plain message."""
    module = importlib.import_module(module_name)
    if not hasattr(module, attribute):
        pytest.fail(f"{module_name}.{attribute} is not implemented yet", pytrace=False)
    return getattr(module, attribute)


def _setting(conn, name: str) -> str:
    return conn.execute(f"SHOW {name}").fetchone()[0]


def _raw_setting(name: str) -> str:
    with psycopg.connect(_dsn()) as raw:
        return raw.execute(f"SHOW {name}").fetchone()[0]


_UNIT_TO_MS = {"ms": 1, "s": 1000, "min": 60000, "h": 3600000, "d": 86400000}


def _ms(text) -> int:
    """'30s' -> 30000, '12345ms' -> 12345, '0' -> 0 (SHOW prints the largest exact unit)."""
    match = re.fullmatch(r"(\d+)\s*([a-z]*)", str(text).strip())
    assert match, f"unparseable duration setting {text!r}"
    amount, unit = match.groups()
    return int(amount) * _UNIT_TO_MS[unit or "ms"]


def _guard_values(conn) -> tuple[int, int, str]:
    timeout = _ms(_setting(conn, "statement_timeout"))
    parallel = int(_setting(conn, "max_parallel_workers_per_gather"))
    name = _setting(conn, "application_name")
    return timeout, parallel, name


def _set_session_timeout(conn, text: str) -> None:
    conn.execute(f"SET statement_timeout = '{text}'")
    conn.commit()


def _set_time_zone(conn, zone: str) -> None:
    conn.execute(f"SET TIME ZONE '{zone}'")
    conn.commit()


def _sql_text(query) -> str:
    if isinstance(query, bytes):
        return query.decode("utf-8", "replace")
    if isinstance(query, str):
        return query
    return repr(query)


@contextlib.contextmanager
def _recorded_statements():
    """Record the SQL text of every statement any psycopg cursor executes inside the block.

    Class-level, so it sees the compat layer, helper copies and any side connection alike.
    """
    statements: list[str] = []
    original = psycopg.Cursor.execute

    def recording_execute(self, query, params=None, **kwargs):
        statements.append(_sql_text(query))
        return original(self, query, params, **kwargs)

    psycopg.Cursor.execute = recording_execute
    try:
        yield statements
    finally:
        psycopg.Cursor.execute = original


def _brief(statements) -> list[str]:
    return [" ".join(text.split())[:80] for text in statements]


def _recon_counts(statements) -> list[str]:
    found = []
    for text in statements:
        lowered = text.lower()
        if "bulk_d2_receipts_recon" in lowered and "count(" in lowered:
            found.append(text)
    return found


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plain_conn(tmp_path):
    """A compat connection on this test's private schema (what get_db() returns here)."""
    connection = get_db(str(tmp_path / "db_guards.db"))
    yield connection
    connection.close()


@pytest.fixture
def full_schema_conn(tmp_path):
    path = str(tmp_path / "db_guards_schema.db")
    init_db(path)
    connection = get_db(path)
    yield connection
    connection.close()


@pytest.fixture
def admin(pg_test_schema):
    """Autocommit connection that changes the catalog behind the compat layer's back."""
    connection = psycopg.connect(_dsn(), autocommit=True)
    yield connection
    connection.close()


@pytest.fixture
def second_schema(pg_test_schema):
    name = f"{pg_test_schema}_b"
    with psycopg.connect(_dsn(), autocommit=True) as setup:
        setup.execute(f"CREATE SCHEMA {name}")
    yield name
    with psycopg.connect(_dsn(), autocommit=True) as teardown:
        teardown.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")


@pytest.fixture
def second_conn(second_schema):
    connection = PostgresCompatConnection(_dsn(), schema=second_schema)
    yield connection
    connection.close()  # before second_schema drops the schema, or the DROP waits on its locks


@pytest.fixture
def make_real_app(monkeypatch):
    """create_app on the REAL connection factory, against the test database's public schema.

    tests/conftest.py swaps get_db for a schema-isolated stand-in that returns a plain
    connection; guards on the web path are only visible through the real factory. The web
    values are set both in config.py and in the create_app dict, so the test holds whichever
    of the two the implementation reads. Only SHOW statements run on these connections.
    """
    dsn = _dsn()
    monkeypatch.setattr(connection_module, "_get_db_override", None)
    monkeypatch.setattr(connection_module, "get_db", _REAL_GET_DB)
    monkeypatch.setattr(app_module, "get_db", _REAL_GET_DB, raising=False)

    def make(overrides=None):
        overrides = dict(overrides or {})
        for key, value in overrides.items():
            monkeypatch.setattr(config_module, key, value, raising=False)
        settings = {
            "TESTING": True,
            "DATABASE_URL": dsn,
            "DASHBOARD_PREWARM_ENABLED": False,
            "ROUTE_PERF_CACHE_ENABLED": False,
        }
        settings.update(overrides)
        return create_app(settings)

    return make


@pytest.fixture
def web_overrides():
    """Guard values that differ from this server's own, so an unset guard cannot match."""
    server_parallel = int(_raw_setting("max_parallel_workers_per_gather"))
    parallel = 1
    if server_parallel == 1:
        parallel = 3
    return {
        "WEB_STATEMENT_TIMEOUT_MS": OVERRIDE_TIMEOUT_MS,
        "WEB_MAX_PARALLEL_WORKERS_PER_GATHER": parallel,
    }


@pytest.fixture
def memo_clock(monkeypatch):
    """Call it with N to make time.monotonic() -- the clock the catalog memo reads -- run N s ahead."""
    real_monotonic = time.monotonic
    shift = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + shift[0])

    def set_shift(seconds: float) -> None:
        shift[0] = seconds

    return set_shift


# ---------------------------------------------------------------------------
# (a) L2 / LD3 -- config keys and web-connection session guards
# ---------------------------------------------------------------------------

def _load_config_with_env(monkeypatch, env: dict):
    """Execute config.py afresh under a controlled environment (no reload of the live module)."""
    for key in ("WEB_STATEMENT_TIMEOUT_MS", "WEB_MAX_PARALLEL_WORKERS_PER_GATHER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("config_probe", REPO_ROOT / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_defaults_for_the_web_guards(monkeypatch):
    fresh = _load_config_with_env(monkeypatch, {})
    timeout = getattr(fresh, "WEB_STATEMENT_TIMEOUT_MS", None)
    parallel = getattr(fresh, "WEB_MAX_PARALLEL_WORKERS_PER_GATHER", None)
    assert timeout == 30000, "config.WEB_STATEMENT_TIMEOUT_MS must default to the int 30000"
    assert parallel == 2, "config.WEB_MAX_PARALLEL_WORKERS_PER_GATHER must default to the int 2"
    assert isinstance(timeout, int)
    assert isinstance(parallel, int)


def test_config_reads_the_web_guards_from_the_environment(monkeypatch):
    fresh = _load_config_with_env(
        monkeypatch,
        {"WEB_STATEMENT_TIMEOUT_MS": "4500", "WEB_MAX_PARALLEL_WORKERS_PER_GATHER": "1"},
    )
    timeout = getattr(fresh, "WEB_STATEMENT_TIMEOUT_MS", None)
    parallel = getattr(fresh, "WEB_MAX_PARALLEL_WORKERS_PER_GATHER", None)
    assert timeout == 4500
    assert parallel == 1


def test_web_connection_gets_the_configured_guards(make_real_app, web_overrides):
    app = make_real_app(web_overrides)
    with app.app_context():
        timeout, parallel, name = _guard_values(app.get_database())
    assert timeout == OVERRIDE_TIMEOUT_MS
    assert parallel == web_overrides["WEB_MAX_PARALLEL_WORKERS_PER_GATHER"]
    assert name == WEB_APPLICATION_NAME


def test_web_connection_defaults_without_overrides(make_real_app):
    app = make_real_app()
    with app.app_context():
        timeout, parallel, name = _guard_values(app.get_database())
    assert timeout == 30000
    assert parallel == 2
    assert name == WEB_APPLICATION_NAME


def test_every_new_web_connection_gets_the_guards(make_real_app, web_overrides):
    # A one-time flag, or guards on the first connection of the process only, passes the
    # tests above and fails here: every request opens a fresh connection.
    app = make_real_app(web_overrides)
    expected = (
        OVERRIDE_TIMEOUT_MS,
        web_overrides["WEB_MAX_PARALLEL_WORKERS_PER_GATHER"],
        WEB_APPLICATION_NAME,
    )
    seen = []
    for _ in range(3):
        with app.app_context():
            seen.append(_guard_values(app.get_database()))
    assert seen[0] == expected
    assert seen[1] == expected
    assert seen[2] == expected


def _disturb(conn, disturbance: str) -> None:
    if disturbance == "failed_statement":
        # the compat layer rolls the transaction back when a statement fails
        with pytest.raises(psycopg.errors.DivisionByZero):
            conn.execute("SELECT 1/0")
    elif disturbance == "explicit_rollback":
        conn.rollback()
    else:
        scope = _api("database.pg_compat", "statement_timeout_scope")
        with pytest.raises(psycopg.errors.QueryCanceled):
            with scope(conn, 100):
                conn.execute("SELECT pg_sleep(2)")


@pytest.mark.parametrize("disturbance", ["failed_statement", "explicit_rollback", "scope_timeout"])
def test_web_guards_survive_rollbacks(make_real_app, web_overrides, disturbance):
    # A SET that is never committed reads back right until the first rollback reverts it.
    app = make_real_app(web_overrides)
    with app.app_context():
        conn = app.get_database()
        _disturb(conn, disturbance)
        timeout, parallel, name = _guard_values(conn)
    assert timeout == OVERRIDE_TIMEOUT_MS
    assert parallel == web_overrides["WEB_MAX_PARALLEL_WORKERS_PER_GATHER"]
    assert name == WEB_APPLICATION_NAME


def test_plain_connection_keeps_the_server_defaults_unlike_the_web_connection(
    make_real_app, web_overrides
):
    server = {
        "statement_timeout": _raw_setting("statement_timeout"),
        "max_parallel_workers_per_gather": _raw_setting("max_parallel_workers_per_gather"),
        "application_name": _raw_setting("application_name"),
    }
    plain = _REAL_GET_DB(_dsn())
    try:
        plain_timeout = _setting(plain, "statement_timeout")
        plain_parallel = _setting(plain, "max_parallel_workers_per_gather")
        plain_name = _setting(plain, "application_name")
    finally:
        plain.close()

    app = make_real_app(web_overrides)
    with app.app_context():
        web_timeout, web_parallel, web_name = _guard_values(app.get_database())

    assert plain_timeout == server["statement_timeout"]
    assert plain_parallel == server["max_parallel_workers_per_gather"]
    assert plain_name == server["application_name"]
    assert web_timeout != _ms(plain_timeout)
    assert web_parallel != int(plain_parallel)
    assert web_name != plain_name


# ---------------------------------------------------------------------------
# (b) L2 -- a real per-statement timeout for search sections
# ---------------------------------------------------------------------------

def test_scope_cancels_a_slow_statement_with_query_canceled(plain_conn):
    scope = _api("database.pg_compat", "statement_timeout_scope")
    started = time.perf_counter()
    with pytest.raises(psycopg.errors.QueryCanceled):
        with scope(plain_conn, 100):
            plain_conn.execute("SELECT pg_sleep(2)").fetchall()
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5, "the 2 s statement must be cancelled near its 100 ms budget"
    assert elapsed >= 0.09, "cancelled before the budget elapsed: the budget is mis-scaled"


def test_scope_budget_does_not_outlive_a_normal_exit(plain_conn):
    # SET LOCAL inside a savepoint survives RELEASE SAVEPOINT until the transaction ends, so
    # a scope that just releases leaves its tiny budget on every later statement of the request.
    scope = _api("database.pg_compat", "statement_timeout_scope")
    _set_session_timeout(plain_conn, "5s")
    with scope(plain_conn, 100):
        inside = plain_conn.execute("SELECT 1 AS one").fetchone()
    after = _setting(plain_conn, "statement_timeout")
    later = plain_conn.execute("SELECT pg_sleep(0.4), 1 AS one").fetchone()
    assert inside["one"] == 1
    assert _ms(after) == 5000
    assert later["one"] == 1


def test_connection_is_usable_and_unbudgeted_after_a_timeout(plain_conn):
    scope = _api("database.pg_compat", "statement_timeout_scope")
    _set_session_timeout(plain_conn, "5s")
    with pytest.raises(psycopg.errors.QueryCanceled):
        with scope(plain_conn, 100):
            plain_conn.execute("SELECT pg_sleep(2)").fetchall()
    ping = plain_conn.execute("SELECT 1 AS one").fetchone()
    after = _setting(plain_conn, "statement_timeout")
    later = plain_conn.execute("SELECT pg_sleep(0.4), 1 AS one").fetchone()
    assert ping["one"] == 1
    assert _ms(after) == 5000
    assert later["one"] == 1


def test_run_query_with_timeout_reports_a_slow_query_as_timed_out(plain_conn):
    def slow():
        return plain_conn.execute("SELECT pg_sleep(2)").fetchall()

    started = time.perf_counter()
    result, elapsed_ms, timed_out = main_routes._run_query_with_timeout(
        plain_conn, slow, timeout_ms=100
    )
    wall = time.perf_counter() - started
    assert timed_out is True
    assert result == []
    assert isinstance(elapsed_ms, float)
    assert 50 <= elapsed_ms < 1500
    assert wall < 1.5
    ping = plain_conn.execute("SELECT 1 AS one").fetchone()
    assert ping["one"] == 1


def test_run_query_with_timeout_returns_rows_for_a_query_inside_its_budget(plain_conn):
    def within_budget():
        return plain_conn.execute("SELECT pg_sleep(0.3), 42 AS answer").fetchall()

    result, elapsed_ms, timed_out = main_routes._run_query_with_timeout(
        plain_conn, within_budget, timeout_ms=1500
    )
    assert timed_out is False
    assert len(result) == 1
    assert result[0]["answer"] == 42
    assert elapsed_ms >= 250


@pytest.mark.parametrize("failure", ["sql_error", "python_error"])
def test_run_query_with_timeout_does_not_mask_other_errors(plain_conn, failure):
    expected = psycopg.errors.DivisionByZero
    if failure == "python_error":
        expected = ValueError

    def broken():
        if failure == "sql_error":
            return plain_conn.execute("SELECT 1/0").fetchall()
        raise ValueError("not a timeout")

    with pytest.raises(expected):
        main_routes._run_query_with_timeout(plain_conn, broken, timeout_ms=700)
    ping = plain_conn.execute("SELECT 1 AS one").fetchone()
    assert ping["one"] == 1


def test_run_query_with_timeout_no_longer_relies_on_the_sqlite_progress_handler():
    source = inspect.getsource(main_routes._run_query_with_timeout)
    assert "set_progress_handler" not in source
    assert "statement_timeout_scope" in source


def test_search_section_over_budget_is_reported_as_timed_out(tmp_path, monkeypatch):
    db_path = str(tmp_path / "search_budget.db")
    init_db(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "DASHBOARD_PREWARM_ENABLED": False,
            "ROUTE_PERF_CACHE_ENABLED": False,
            "SEARCH_QUERY_TIMEOUT_MS": 100,
        }
    )

    def slow_committees(conn, query, **kwargs):
        conn.execute("SELECT pg_sleep(3)").fetchall()
        return []

    monkeypatch.setattr(main_routes, "_search_committees_source_aware", slow_committees)
    query = "budgetprobe" + uuid.uuid4().hex[:8]  # unique: a cached search would skip the sections

    started = time.perf_counter()
    response = app.test_client().get(f"/search?q={query}&type=all")
    wall = time.perf_counter() - started
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "timed out and were skipped: committees" in page
    # the sections that run after the timed-out one must find a healthy connection
    assert "failed and were skipped" not in page
    assert wall < 2.5


# ---------------------------------------------------------------------------
# (c) L9 -- catalog probes are memoized per process
# ---------------------------------------------------------------------------

TABLE_PROBE_MODULES = [
    "webapp.routes.main",
    "webapp.routes.analytics",
    "webapp.routes.committees",
    "webapp.app",
    "database.analytics",
    "database.federal_fec",
    "database.connection",
]
COLUMN_PROBE_MODULES = [
    "webapp.routes.main",
    "webapp.routes.committees",
    "webapp.app",
    "database.analytics",
    "database.federal_fec",
    "database.connection",
]


@pytest.mark.parametrize("module_name", TABLE_PROBE_MODULES)
def test_present_table_probe_is_memoized(plain_conn, admin, pg_test_schema, module_name):
    probe = getattr(importlib.import_module(module_name), "_table_exists")
    admin.execute(f"CREATE TABLE {pg_test_schema}.memo_present (id INTEGER)")
    with _recorded_statements() as statements:
        first = probe(plain_conn, "memo_present")
        issued_by_first = len(statements)
        second = probe(plain_conn, "memo_present")
        issued_by_second = len(statements) - issued_by_first
    assert first is True
    assert second is True
    assert issued_by_first >= 1, "the first probe must reach the database (is the recorder live?)"
    assert issued_by_second == 0, _brief(statements[issued_by_first:])


def test_absent_table_probe_is_memoized_too(plain_conn):
    with _recorded_statements() as statements:
        first = main_routes._table_exists(plain_conn, "memo_absent")
        issued_by_first = len(statements)
        second = main_routes._table_exists(plain_conn, "memo_absent")
        issued_by_second = len(statements) - issued_by_first
    assert first is False
    assert second is False
    assert issued_by_first >= 1
    assert issued_by_second == 0, _brief(statements[issued_by_first:])


@pytest.mark.parametrize("module_name", COLUMN_PROBE_MODULES)
def test_column_probe_is_memoized(plain_conn, admin, pg_test_schema, module_name):
    probe = getattr(importlib.import_module(module_name), "_column_exists")
    admin.execute(f"CREATE TABLE {pg_test_schema}.memo_cols (id INTEGER, label TEXT)")
    with _recorded_statements() as statements:
        present = probe(plain_conn, "memo_cols", "label")
        issued_by_first = len(statements)
        absent = probe(plain_conn, "memo_cols", "no_such_column")
        present_again = probe(plain_conn, "memo_cols", "label")
        absent_again = probe(plain_conn, "memo_cols", "no_such_column")
        issued_later = len(statements) - issued_by_first
    assert present is True
    assert absent is False
    assert present_again is True
    assert absent_again is False
    assert issued_by_first >= 1
    assert issued_later == 0, _brief(statements[issued_by_first:])


def test_pragma_table_info_is_memoized_and_keeps_its_row_shape(plain_conn, admin, pg_test_schema):
    admin.execute(
        f"CREATE TABLE {pg_test_schema}.memo_pragma (id INTEGER PRIMARY KEY, label TEXT NOT NULL)"
    )
    with _recorded_statements() as statements:
        first = [dict(row) for row in plain_conn.execute("PRAGMA table_info(memo_pragma)").fetchall()]
        issued_by_first = len(statements)
        second = [dict(row) for row in plain_conn.execute("PRAGMA table_info(memo_pragma)").fetchall()]
        issued_by_second = len(statements) - issued_by_first
    # id: position 0, integer, NOT NULL via the primary key, no default, pk=1
    # label: position 1, text, declared NOT NULL, no default, pk=0
    assert [row["name"] for row in first] == ["id", "label"]
    assert [row["cid"] for row in first] == [0, 1]
    assert [row["type"] for row in first] == ["integer", "text"]
    assert [row["notnull"] for row in first] == [1, 1]
    assert [row["dflt_value"] for row in first] == [None, None]
    assert [row["pk"] for row in first] == [1, 0]
    assert set(first[0]) == {"cid", "name", "type", "notnull", "dflt_value", "pk"}
    assert second == first
    assert issued_by_first >= 1
    assert issued_by_second == 0, _brief(statements[issued_by_first:])


def test_memo_is_shared_by_every_connection_of_the_schema(tmp_path, admin, pg_test_schema):
    # Every request opens its own connection (LD6 defers the pool); a memo that lives on the
    # connection object would save nothing on the web.
    first_conn = get_db(str(tmp_path / "memo_a.db"))
    second_conn = get_db(str(tmp_path / "memo_b.db"))
    assert first_conn is not second_conn
    admin.execute(f"CREATE TABLE {pg_test_schema}.memo_shared_conn (id INTEGER, label TEXT)")
    prime_table = main_routes._table_exists(first_conn, "memo_shared_conn")
    prime_column = main_routes._column_exists(first_conn, "memo_shared_conn", "label")
    with _recorded_statements() as statements:
        table_seen = main_routes._table_exists(second_conn, "memo_shared_conn")
        column_seen = main_routes._column_exists(second_conn, "memo_shared_conn", "label")
    assert prime_table is True
    assert prime_column is True
    assert table_seen is True
    assert column_seen is True
    assert statements == [], _brief(statements)


def test_memo_does_not_leak_between_schemas(plain_conn, second_conn, admin, pg_test_schema, second_schema):
    # A memo keyed on the name alone answers schema B with schema A's answers.
    admin.execute(f"CREATE TABLE {pg_test_schema}.memo_shared (id INTEGER, only_in_a TEXT)")
    admin.execute(f"CREATE TABLE {second_schema}.memo_shared (id INTEGER)")
    admin.execute(f"CREATE TABLE {second_schema}.only_in_b (id INTEGER)")

    def probes(conn):
        return (
            main_routes._table_exists(conn, "memo_shared"),
            main_routes._column_exists(conn, "memo_shared", "only_in_a"),
            main_routes._table_exists(conn, "only_in_b"),
        )

    in_a = probes(plain_conn)
    in_b = probes(second_conn)
    in_a_again = probes(plain_conn)
    assert in_a == (True, True, False)
    assert in_b == (True, False, True)
    assert in_a_again == (True, True, False)


CATALOG_CHANGES = {
    # name: (setup DDL, probe kind, answer before, outside-the-process DDL, answer after)
    "table_created": (None, "table", False, "CREATE TABLE {s}.memo_changing (id INTEGER)", True),
    "table_dropped": (
        "CREATE TABLE {s}.memo_changing (id INTEGER)", "table", True,
        "DROP TABLE {s}.memo_changing", False,
    ),
    "column_added": (
        "CREATE TABLE {s}.memo_changing (id INTEGER)", "column", False,
        "ALTER TABLE {s}.memo_changing ADD COLUMN extra TEXT", True,
    ),
    "column_dropped": (
        "CREATE TABLE {s}.memo_changing (id INTEGER, extra TEXT)", "column", True,
        "ALTER TABLE {s}.memo_changing DROP COLUMN extra", False,
    ),
}


@pytest.mark.parametrize("change", sorted(CATALOG_CHANGES))
def test_invalidation_hook_reveals_changes_made_outside_this_process(
    plain_conn, admin, pg_test_schema, change
):
    hook = _api("database.pg_compat", "invalidate_catalog_memo")
    setup_ddl, kind, before, outside_ddl, after = CATALOG_CHANGES[change]

    def probe():
        if kind == "table":
            return main_routes._table_exists(plain_conn, "memo_changing")
        return main_routes._column_exists(plain_conn, "memo_changing", "extra")

    if setup_ddl:
        admin.execute(setup_ddl.format(s=pg_test_schema))
    primed = probe()
    admin.execute(outside_ddl.format(s=pg_test_schema))
    hook()
    refreshed = probe()
    assert primed is before
    assert refreshed is after


DDL_THROUGH_THE_COMPAT_LAYER = {
    # name: (setup DDL or None, probe kind, answer before, DDL run on the probing connection, answer after)
    "create_table": (None, "table", False, "CREATE TABLE memo_inproc (id INTEGER)", True),
    "drop_table": (
        "CREATE TABLE memo_inproc (id INTEGER)", "table", True, "DROP TABLE memo_inproc", False,
    ),
    "create_view": (
        "CREATE TABLE memo_inproc_base (id INTEGER)", "table", False,
        "CREATE VIEW memo_inproc AS SELECT id FROM memo_inproc_base", True,
    ),
    "add_column": (
        "CREATE TABLE memo_inproc (id INTEGER)", "column", False,
        "ALTER TABLE memo_inproc ADD COLUMN extra TEXT", True,
    ),
    "executescript_create_table": (
        None, "table", False, "executescript:CREATE TABLE memo_inproc (id INTEGER);", True,
    ),
}


@pytest.mark.parametrize("name", sorted(DDL_THROUGH_THE_COMPAT_LAYER))
def test_ddl_run_through_the_compat_layer_is_visible_to_the_next_probe(plain_conn, name):
    # ensure_schema probes a table, runs the schema script, then probes again; the ETL drops and
    # recreates isbe_* tables and probes them in between. No hook call is allowed to be needed.
    setup_ddl, kind, before, ddl, after = DDL_THROUGH_THE_COMPAT_LAYER[name]

    def probe():
        if kind == "table":
            return main_routes._table_exists(plain_conn, "memo_inproc")
        return main_routes._column_exists(plain_conn, "memo_inproc", "extra")

    if setup_ddl:
        plain_conn.execute(setup_ddl)
        plain_conn.commit()
    primed = probe()
    if ddl.startswith("executescript:"):
        plain_conn.executescript(ddl[len("executescript:"):])
    else:
        plain_conn.execute(ddl)
        plain_conn.commit()
    refreshed = probe()
    assert primed is before
    assert refreshed is after


def test_temp_table_introspection_follows_a_redefinition(plain_conn):
    # cross_matching.py reads PRAGMA temp.table_info for staging tables it drops and recreates.
    # The redefinition goes through the raw psycopg connection of the same session, so no
    # DDL-invalidation rule of the compat layer can hide a memoized answer.
    raw = plain_conn._pg_conn
    raw.execute("CREATE TEMP TABLE memo_temp (a INTEGER)")
    first = [row["name"] for row in plain_conn.execute("PRAGMA temp.table_info(memo_temp)").fetchall()]
    raw.execute("DROP TABLE memo_temp")
    raw.execute("CREATE TEMP TABLE memo_temp (b INTEGER, c TEXT)")
    second = [row["name"] for row in plain_conn.execute("PRAGMA temp.table_info(memo_temp)").fetchall()]
    assert first == ["a"]
    assert second == ["b", "c"]


# ---------------------------------------------------------------------------
# (c2) R3 -- the memo must not outlive changes another process makes
#      (review round wf_54beaba4-9c9, findings C7 / C19 / C8). The weekly import drops and
#      recreates tables under a running web process: a stale "absent" answer cached empty
#      analytics payloads for 6 h, a stale "present" answer 500'd guarded pages.
# ---------------------------------------------------------------------------

def test_memo_ttl_constants_are_five_seconds_for_falsy_answers_and_five_minutes_for_the_rest():
    # The two numbers ARE the fix: without this pin a negative ttl equal to (or near) the positive
    # one, or one short ttl for everything, would satisfy every test below, which derive their
    # shifts from these constants. Exact on purpose (plan R3); relax to a range if tuning is expected.
    positive = _api("database.pg_compat", "_CATALOG_MEMO_TTL_SECONDS")
    negative = _api("database.pg_compat", "_CATALOG_MEMO_NEGATIVE_TTL_SECONDS")
    assert positive == 300.0
    assert negative == 5.0


# What the memo holds per CATALOG_CHANGES case BEFORE the outside change (see the module docstring):
CATALOG_CHANGE_MEMO_IS_FALSY = {
    "table_created": True,  # table absent -> memo False
    "table_dropped": False,  # table present -> memo True
    "column_added": False,  # table [id] -> memo True and [id]; the probe answers False for "extra"
    "column_dropped": False,  # table [id, extra] -> memo True and [id, extra]
}


@pytest.mark.parametrize("change", sorted(CATALOG_CHANGES))
def test_memo_ages_out_on_its_own_so_outside_changes_show_without_the_hook(
    plain_conn, admin, pg_test_schema, memo_clock, change
):
    # Both directions of the ttl, per case, and the hook is never called: still the primed answer at
    # half the ttl (a memo that forgets early, or never stores a falsy answer, fails here), the
    # post-change answer at twice the ttl (a memo that keeps a falsy answer for the long ttl fails).
    #   short ttl 5.0 s:   +2.5 s is inside it, +10 s past it
    #   long ttl 300.0 s:  +150 s is inside it, +600 s past it
    ttl = _api("database.pg_compat", "_CATALOG_MEMO_TTL_SECONDS")
    if CATALOG_CHANGE_MEMO_IS_FALSY[change]:
        ttl = _api("database.pg_compat", "_CATALOG_MEMO_NEGATIVE_TTL_SECONDS")
    setup_ddl, kind, before, outside_ddl, after = CATALOG_CHANGES[change]

    def probe():
        if kind == "table":
            return main_routes._table_exists(plain_conn, "memo_changing")
        return main_routes._column_exists(plain_conn, "memo_changing", "extra")

    if setup_ddl:
        admin.execute(setup_ddl.format(s=pg_test_schema))
    primed = probe()
    admin.execute(outside_ddl.format(s=pg_test_schema))
    memo_clock(ttl / 2)
    inside_ttl = probe()
    memo_clock(ttl * 2)
    past_ttl = probe()
    assert primed is before
    assert inside_ttl is before, f"{change}: the memo forgot its answer {ttl / 2} s into a {ttl} s ttl"
    assert past_ttl is after, (
        f"{change}: the outside change is still hidden {ttl * 2} s on, twice its {ttl} s ttl"
    )


MEMO_VALUE_KINDS = {
    # name: (value the memoized computation returns, is that value falsy)
    # False is the table probe's answer for an absent table, [] is PRAGMA table_info of an absent
    # table, 0 is the COUNT(*) of an empty table (get_analytics_data_sources); True, a one-column
    # PRAGMA table_info and a positive COUNT(*) are their truthy twins.
    "false": (False, True),
    "empty_list": ([], True),
    "zero": (0, True),
    "true": (True, False),
    "one_column_list": ([{"name": "id"}], False),
    "positive_count": (7, False),
}


@pytest.mark.parametrize("kind", sorted(MEMO_VALUE_KINDS))
def test_schema_memo_keeps_every_falsy_value_for_the_short_ttl_and_every_other_for_the_long_one(
    plain_conn, memo_clock, kind
):
    # schema_memo itself, one computation counter per kind: an `is False` check, a bool-only check
    # or "do not store falsy values" each fails a different row here.
    schema_memo = _api("database.pg_compat", "schema_memo")
    value, falsy = MEMO_VALUE_KINDS[kind]
    ttl = _api("database.pg_compat", "_CATALOG_MEMO_TTL_SECONDS")
    if falsy:
        ttl = _api("database.pg_compat", "_CATALOG_MEMO_NEGATIVE_TTL_SECONDS")
    computed = []

    def compute():
        computed.append(kind)
        return value

    key = ("ttl_rule", kind)
    first = schema_memo(plain_conn, key, compute)
    memo_clock(ttl / 2)
    inside_ttl = schema_memo(plain_conn, key, compute)
    computed_inside = len(computed)
    memo_clock(ttl * 2)
    past_ttl = schema_memo(plain_conn, key, compute)
    computed_past = len(computed)
    assert first == value
    assert inside_ttl == value
    assert past_ttl == value
    assert computed_inside == 1, f"{kind}: recomputed {ttl / 2} s into a {ttl} s ttl"
    assert computed_past == 2, f"{kind}: still served from the memo {ttl * 2} s on, twice its {ttl} s ttl"


SELF_HEAL = {
    # name: (probe kind, DDL run outside the memo's view, statement that then fails, run as, its error)
    "select_from_a_dropped_table": (
        "table", "DROP TABLE {s}.memo_heal", "SELECT * FROM memo_heal", "execute",
        psycopg.errors.UndefinedTable,
    ),
    "select_a_dropped_column": (
        "column", "ALTER TABLE {s}.memo_heal DROP COLUMN extra", "SELECT extra FROM memo_heal", "execute",
        psycopg.errors.UndefinedColumn,
    ),
    "insert_into_a_dropped_table": (
        "table", "DROP TABLE {s}.memo_heal", "INSERT INTO memo_heal (id) VALUES (?)", "executemany",
        psycopg.errors.UndefinedTable,
    ),
}


@pytest.mark.parametrize("name", sorted(SELF_HEAL))
def test_a_statement_that_hits_a_vanished_object_clears_the_stale_memo_answer(
    plain_conn, admin, pg_test_schema, name
):
    # No clock shift, no hook: the first failed statement is what teaches the memo (stale "present"
    # answers 500'd guarded pages for up to 300 s). Run on the cursor, the layer the contract names.
    kind, outside_ddl, statement, runner, error = SELF_HEAL[name]

    def probe():
        if kind == "table":
            return main_routes._table_exists(plain_conn, "memo_heal")
        return main_routes._column_exists(plain_conn, "memo_heal", "extra")

    admin.execute(f"CREATE TABLE {pg_test_schema}.memo_heal (id INTEGER, extra TEXT)")
    primed = probe()
    admin.execute(outside_ddl.format(s=pg_test_schema))
    stale = probe()  # the premise: the memo still says "present", so only the failed statement can fix it
    cursor = plain_conn.cursor()
    with pytest.raises(error):
        if runner == "execute":
            cursor.execute(statement)
        else:
            cursor.executemany(statement, [(1,), (2,)])
    healed = probe()  # also proves the failed statement left a usable connection
    assert primed is True
    assert stale is True, "premise broken: the probe re-read the catalog before any statement failed"
    assert healed is False, f"{name}: the memo still says the object exists after {error.__name__}"


SELF_HEAL_BYSTANDERS = {
    # name: (statement, error): failures that say nothing about a vanished table or column. Over-
    # invalidating on them (`except Exception`, psycopg.Error, ProgrammingError) turns every search
    # section that times out (the 700 ms budget is real since P2) into a whole-process memo flush.
    "statement_timeout": ("SELECT pg_sleep(2)", psycopg.errors.QueryCanceled),
    "undefined_function": ("SELECT memo_no_such_function(1)", psycopg.errors.UndefinedFunction),
}


@pytest.mark.parametrize("name", sorted(SELF_HEAL_BYSTANDERS))
def test_other_statement_failures_leave_the_memo_alone(plain_conn, admin, pg_test_schema, name):
    # (pin) green today; red the day the self-heal invalidates on every error.
    statement, error = SELF_HEAL_BYSTANDERS[name]
    admin.execute(f"CREATE TABLE {pg_test_schema}.memo_kept (id INTEGER)")
    primed = main_routes._table_exists(plain_conn, "memo_kept")
    _set_session_timeout(plain_conn, "100ms")
    with pytest.raises(error):
        plain_conn.cursor().execute(statement)
    with _recorded_statements() as statements:
        again = main_routes._table_exists(plain_conn, "memo_kept")
    assert primed is True
    assert again is True
    assert statements == [], _brief(statements)


def test_swap_script_source_calls_the_cache_flush_after_its_last_statement():
    # The swap drops and recreates the bulk_* compat views and the finance matview under the running
    # web app; every payload cached from the old objects must go when it finishes (finding C7).
    source = SWAP_SCRIPT.read_text(encoding="utf-8")
    last_statement = source.rfind("cur.execute(")
    last_flush = source.rfind("flush_all_route_caches()")
    assert last_statement != -1, "scripts/swap_bulk_to_isbe.py has no cur.execute( left: this pin is stale"
    assert last_flush != -1, (
        "scripts/swap_bulk_to_isbe.py never calls flush_all_route_caches(): the route caches stay "
        "warm with payloads built from the objects the swap just replaced"
    )
    assert last_flush > last_statement, (
        "flush_all_route_caches() must come after the swap's last cur.execute( : a flush before "
        "the DDL lets requests re-cache the old objects' payloads"
    )


# Program for `python -c`: runs the swap script the way `run.py sunshine-import` launches it, as a
# script (sys.path[0] is scripts/, NOT the repo root, and the cwd is not importable), against a fake
# psycopg connection that answers every statement with the row (5,) -- no database is touched. A
# profile hook records how many statements had run each time a function named
# flush_all_route_caches is entered. argv: the swap script, its directory.
SWAP_IN_SCRIPT_MODE = """
import json, runpy, sys

swap_script, scripts_dir = sys.argv[1], sys.argv[2]
sys.path[0] = scripts_dir
import psycopg

statements = []
flush_calls = []


class FakeCursor:
    def execute(self, sql, params=None):
        statements.append(sql)

    def fetchone(self):
        return (5,)

    def fetchall(self):
        return []


class FakeConnection:
    def cursor(self):
        return FakeCursor()

    def close(self):
        pass


def note_flush(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "flush_all_route_caches":
        flush_calls.append(len(statements))


psycopg.connect = lambda *args, **kwargs: FakeConnection()
sys.argv = [swap_script, "--db-url", "unused"]
sys.setprofile(note_flush)
runpy.run_path(swap_script, run_name="__main__")
sys.setprofile(None)
print(json.dumps({"statements": len(statements), "flush_calls": flush_calls}))
"""


def test_swap_script_run_as_a_script_flushes_the_caches_as_its_last_act(tmp_path):
    # `run.py sunshine-import` runs `python scripts/swap_bulk_to_isbe.py`: `webapp` is importable
    # there only if the swap puts the repo root on sys.path itself, as scripts/isbe_sunshine_etl.py
    # and scripts/warm_caches.py do. Under pytest the root is always importable, so only a fresh
    # interpreter sees "No module named 'webapp'" -- and it sees it AFTER the swap's DDL ran, as a
    # failed swap exit status for a swap that worked. A comment that merely names the call fails here too.
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    env["REDIS_URL"] = ""  # the real flush may run in the child: it must not reach a developer's Redis
    env["PYTHONIOENCODING"] = "utf-8"  # the swap prints check marks
    completed = subprocess.run(
        [sys.executable, "-c", SWAP_IN_SCRIPT_MODE, str(SWAP_SCRIPT), str(SWAP_SCRIPT.parent)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-1500:]
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    statements = report["statements"]
    flush_calls = report["flush_calls"]
    assert statements > 0, "the fake connection saw no statement: this harness no longer matches main()"
    assert flush_calls, "the swap never called flush_all_route_caches() when run as a script"
    assert flush_calls[-1] == statements, (
        f"the last flush ran after {flush_calls[-1]} of the swap's {statements} statements: "
        "it must be the swap's last act"
    )


# ---------------------------------------------------------------------------
# (d) L9 -- get_analytics_data_sources counts bulk_d2_receipts_recon once
# ---------------------------------------------------------------------------

RECON_DDL = "CREATE TABLE bulk_d2_receipts_recon (committee_id_sbe INTEGER, committee_name TEXT)"
DATA_SOURCES_KEYS = {"donor_flow_source", "reconciliation_available", "reconciliation_rows"}


def _fill_recon(conn, rows: int, first_id: int = 1000) -> None:
    for number in range(rows):
        conn.execute(
            "INSERT INTO bulk_d2_receipts_recon (committee_id_sbe, committee_name) VALUES (?, ?)",
            (first_id + number, f"Committee {first_id + number}"),
        )
    conn.commit()


def test_recon_row_count_runs_once_across_calls_and_connections(full_schema_conn, tmp_path):
    # The second connection stands for the next web request: a cache that lives on the
    # connection, or on the call, saves nothing there.
    other_conn = get_db(str(tmp_path / "other_request.db"))
    full_schema_conn.execute(RECON_DDL)
    _fill_recon(full_schema_conn, 3)
    with _recorded_statements() as statements:
        first = get_analytics_data_sources(full_schema_conn)
        counts_after_first = len(_recon_counts(statements))
        second = get_analytics_data_sources(full_schema_conn)
        counts_after_second = len(_recon_counts(statements))
        third = get_analytics_data_sources(other_conn)
        counts_after_third = len(_recon_counts(statements))
    assert set(first) == DATA_SOURCES_KEYS
    assert first["reconciliation_available"] is True
    assert first["reconciliation_rows"] == 3
    assert second == first
    assert third == first
    assert counts_after_first == 1
    assert counts_after_second == 1, _brief(_recon_counts(statements))
    assert counts_after_third == 1, _brief(_recon_counts(statements))


def test_recon_row_count_cache_is_scoped_to_the_schema(full_schema_conn, second_conn):
    full_schema_conn.execute(RECON_DDL)
    _fill_recon(full_schema_conn, 2)
    second_conn.execute(RECON_DDL)
    _fill_recon(second_conn, 5)
    in_a = get_analytics_data_sources(full_schema_conn)
    in_b = get_analytics_data_sources(second_conn)
    in_a_again = get_analytics_data_sources(full_schema_conn)
    assert in_a["reconciliation_rows"] == 2
    assert in_b["reconciliation_rows"] == 5
    assert in_a_again["reconciliation_rows"] == 2


def test_recon_row_count_is_recomputed_after_the_invalidation_hook(full_schema_conn):
    hook = _api("database.pg_compat", "invalidate_catalog_memo")
    full_schema_conn.execute(RECON_DDL)
    _fill_recon(full_schema_conn, 3)
    with _recorded_statements() as statements:
        before = get_analytics_data_sources(full_schema_conn)
        _fill_recon(full_schema_conn, 2, first_id=1003)  # ids 1003 and 1004 join 1000..1002
        hook()
        after = get_analytics_data_sources(full_schema_conn)
        counts = len(_recon_counts(statements))
    assert before["reconciliation_rows"] == 3
    assert after["reconciliation_rows"] == 5
    assert counts == 2


def test_missing_recon_table_costs_no_count_and_appears_after_the_hook(full_schema_conn, admin, pg_test_schema):
    hook = _api("database.pg_compat", "invalidate_catalog_memo")
    with _recorded_statements() as statements:
        absent = get_analytics_data_sources(full_schema_conn)
        counts_while_absent = len(_recon_counts(statements))
    admin.execute(
        f"CREATE TABLE {pg_test_schema}.bulk_d2_receipts_recon (committee_id_sbe INTEGER, committee_name TEXT)"
    )
    admin.execute(
        f"INSERT INTO {pg_test_schema}.bulk_d2_receipts_recon VALUES (1, 'A'), (2, 'B'), (3, 'C'), (4, 'D')"
    )
    hook()
    present = get_analytics_data_sources(full_schema_conn)
    assert set(absent) == DATA_SOURCES_KEYS
    assert absent["reconciliation_available"] is False
    assert absent["reconciliation_rows"] == 0
    assert counts_while_absent == 0
    assert present["reconciliation_available"] is True
    assert present["reconciliation_rows"] == 4


# ---------------------------------------------------------------------------
# (e) L10 / LD5 -- snapshot freshness is right in any session time zone
# ---------------------------------------------------------------------------

SNAPSHOT_ZONES = ["UTC", "America/Chicago", "Asia/Tokyo"]
SNAPSHOT_AGES = [(10, True), (540, True), (660, False), (7200, False)]


@pytest.mark.parametrize("zone", SNAPSHOT_ZONES)
@pytest.mark.parametrize("age_seconds, expect_fresh", SNAPSHOT_AGES)
def test_snapshot_freshness_does_not_depend_on_the_session_time_zone(
    full_schema_conn, zone, age_seconds, expect_fresh
):
    conn = full_schema_conn
    params = {"cycle": 2026}
    payload = {"rows": [1, 2, 3]}
    _set_time_zone(conn, zone)  # completed_at is a naive TIMESTAMP: the session zone's wall clock
    save_federal_view_snapshot(conn, SNAPSHOT_TYPE, params, "completed", payload)
    conn.execute(
        f"UPDATE analytics_snapshots SET completed_at = CURRENT_TIMESTAMP - INTERVAL '{age_seconds} seconds'"
    )
    conn.commit()

    snapshot = get_federal_view_snapshot(conn, SNAPSHOT_TYPE, params, ttl_seconds=600)

    assert snapshot["status"] == "completed"
    assert snapshot["payload"] == payload
    assert snapshot["is_fresh"] is expect_fresh
    assert snapshot["is_stale"] is (not expect_fresh)
    assert isinstance(snapshot["age_seconds"], float)
    assert abs(snapshot["age_seconds"] - age_seconds) < 30


def test_a_recent_error_snapshot_is_never_fresh(full_schema_conn):
    conn = full_schema_conn
    params = {"cycle": 2026}
    _set_time_zone(conn, "UTC")
    save_federal_view_snapshot(conn, SNAPSHOT_TYPE, params, "completed", {"rows": [1]})
    save_federal_view_snapshot(conn, SNAPSHOT_TYPE, params, "error", error_message="boom")
    snapshot = get_federal_view_snapshot(conn, SNAPSHOT_TYPE, params, ttl_seconds=600)
    assert snapshot["status"] == "error"
    assert snapshot["is_fresh"] is False
    assert snapshot["is_stale"] is True


def test_a_snapshot_that_never_completed_has_no_age_and_is_stale(full_schema_conn):
    conn = full_schema_conn
    params = {"cycle": 2026}
    _set_time_zone(conn, "UTC")
    save_federal_view_snapshot(conn, SNAPSHOT_TYPE, params, "in_progress")
    snapshot = get_federal_view_snapshot(conn, SNAPSHOT_TYPE, params, ttl_seconds=600)
    assert snapshot["status"] == "in_progress"
    assert snapshot["completed_at"] is None
    assert snapshot["age_seconds"] is None
    assert snapshot["is_fresh"] is False
    assert snapshot["is_stale"] is True


def test_snapshot_age_is_computed_by_the_database():
    # LD5: a Python-side fix would have to guess the writer's zone; the database cannot.
    source = " ".join(inspect.getsource(get_federal_view_snapshot).lower().split())
    markers = (
        "extract(epoch from",
        "now() - completed_at",
        "current_timestamp - completed_at",
        "clock_timestamp() - completed_at",
        "timestamptz",
    )
    found = [marker for marker in markers if marker in source]
    assert found, f"none of {markers} appears in get_federal_view_snapshot"
