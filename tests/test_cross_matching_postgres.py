"""Tests for PostgreSQL compatibility in cross-matching engine.

Covers:
  - Postgres-specific code paths in shadow/swap/merge helpers
  - Temp table schema introspection via PRAGMA translation
  - Identity column injection for NOT NULL match_id columns
  - Dedup of replace-target rows
  - _build_insert_sql for all tables (including donor address index)
  - Merge with empty rows, duplicate keys, and identity columns
  - _shadow_output_table_for_worker using WHERE FALSE (not WHERE 0)
  - BEGIN-inside-open-transaction handling
  - _table_fingerprint Postgres fallback (no rowid)
  - Full parallel pipeline mock with Postgres-like connection
  - Edge cases: malformed names, None values, BOM-prefixed text
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable
from unittest.mock import MagicMock, patch

import pytest

from database.cross_matching import (
    _build_insert_sql,
    _clear_table,
    _dedupe_replace_target_rows,
    _inject_required_surrogate_ids_for_postgres,
    _is_postgres_connection,
    _merge_rows_into_output_table,
    _normalize_name_tokens,
    _read_temp_output_rows,
    _REPLACE_TARGET_PRIMARY_KEYS,
    _safe_identifier,
    _shadow_output_table_for_worker,
    _table_fingerprint,
    _apply_postgres_session_tuning,
    _set_busy_timeout,
    _jaccard,
    _normalize_city,
    _normalize_zip5,
    _address_score,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight Postgres-like mock connection
# ---------------------------------------------------------------------------

class _FakeResult:
    """Mimics a cursor result set."""

    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []
        self._idx = 0

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self):
        remaining = self._rows[self._idx:]
        self._idx = len(self._rows)
        return remaining


# Use the actual class name so _is_postgres_connection (which checks
# conn.__class__.__name__) returns True.
class PostgresCompatConnection:
    """Simulates the real PostgresCompatConnection for unit tests.

    Records all SQL executed and returns pre-configured fake results
    for specific queries.
    """

    def __init__(
        self,
        *,
        info_schema_rows: list[dict[str, Any]] | None = None,
        pragma_temp_rows: list[dict[str, Any]] | None = None,
        select_star_rows: list[dict[str, Any]] | None = None,
    ):
        self.execute_log: list[tuple[str, Any]] = []
        self.executemany_log: list[tuple[str, list]] = []
        self._info_schema_rows = info_schema_rows or []
        self._pragma_temp_rows = pragma_temp_rows
        self._select_star_rows = select_star_rows or []
        self._committed = 0
        self._rolled_back = 0

    def execute(self, sql: str, params: Any = None) -> _FakeResult:
        self.execute_log.append((sql.strip(), params))
        stripped = sql.strip().upper()

        # information_schema.columns query
        if "INFORMATION_SCHEMA.COLUMNS" in stripped:
            return _FakeResult(self._info_schema_rows)

        # PRAGMA temp.table_info — should be intercepted by compat
        if "PRAGMA" in stripped and "TABLE_INFO" in stripped:
            if self._pragma_temp_rows is not None:
                return _FakeResult(self._pragma_temp_rows)
            # Fallback to info_schema rows if not set
            return _FakeResult(self._info_schema_rows)

        # SELECT * (for _read_temp_output_rows)
        if stripped.startswith("SELECT *"):
            return _FakeResult(self._select_star_rows)

        # SELECT COUNT
        if "COUNT(*)" in stripped:
            return _FakeResult([{"c": 0, "m": 0}])

        return _FakeResult([])

    def executemany(self, sql: str, seq_of_params) -> _FakeResult:
        self.executemany_log.append((sql.strip(), list(seq_of_params)))
        return _FakeResult([])

    def commit(self):
        self._committed += 1

    def rollback(self):
        self._rolled_back += 1

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Tests: _is_postgres_connection
# ---------------------------------------------------------------------------

class TestIsPostgresConnection:
    def test_real_class_name(self):
        class PostgresCompatConnection:
            pass
        assert _is_postgres_connection(PostgresCompatConnection())

    def test_sqlite_connection(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        assert not _is_postgres_connection(conn)
        conn.close()

    def test_fake_pg_connection(self):
        assert _is_postgres_connection(PostgresCompatConnection())


# ---------------------------------------------------------------------------
# Tests: _shadow_output_table_for_worker
# ---------------------------------------------------------------------------

class TestShadowOutputTableForWorker:
    def test_postgres_uses_where_false_not_where_zero(self):
        """WHERE 0 is invalid Postgres SQL; must use WHERE FALSE."""
        conn = PostgresCompatConnection()
        _shadow_output_table_for_worker(conn, "lobbying_donor_matches")

        all_sql = " ".join(s for s, _ in conn.execute_log)
        assert "WHERE FALSE" in all_sql
        assert "WHERE 0" not in all_sql

    def test_postgres_drops_pg_temp_schema(self):
        conn = PostgresCompatConnection()
        _shadow_output_table_for_worker(conn, "lobbying_donor_matches")

        drop_sqls = [s for s, _ in conn.execute_log if "DROP TABLE" in s]
        assert any("pg_temp.lobbying_donor_matches" in s for s in drop_sqls)

    def test_postgres_creates_temp_from_public(self):
        conn = PostgresCompatConnection()
        _shadow_output_table_for_worker(conn, "irs527_director_donor_matches")

        create_sqls = [s for s, _ in conn.execute_log if "CREATE TEMP TABLE" in s]
        assert any("irs527_director_donor_matches" in s for s in create_sqls)

    def test_sqlite_uses_where_zero(self):
        """SQLite path should use WHERE 0 (valid SQLite)."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE lobbying_donor_matches (x TEXT)")
        _shadow_output_table_for_worker(conn, "lobbying_donor_matches")
        # Verify temp table exists
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM temp.lobbying_donor_matches"
        ).fetchone()
        assert row["c"] == 0
        conn.close()


# ---------------------------------------------------------------------------
# Tests: _read_temp_output_rows on Postgres
# ---------------------------------------------------------------------------

class TestReadTempOutputRowsPostgres:
    def test_strips_null_identity_columns(self):
        conn = PostgresCompatConnection(
            pragma_temp_rows=[
                {"name": "match_id"},
                {"name": "ein"},
                {"name": "org_name"},
            ],
            select_star_rows=[
                {"match_id": None, "ein": "111", "org_name": "Org A"},
                {"match_id": None, "ein": "222", "org_name": "Org B"},
            ],
        )
        columns, rows = _read_temp_output_rows(conn, "test_table")
        assert "match_id" not in columns
        assert columns == ["ein", "org_name"]
        assert rows == [("111", "Org A"), ("222", "Org B")]

    def test_preserves_non_null_identity(self):
        conn = PostgresCompatConnection(
            pragma_temp_rows=[
                {"name": "match_id"},
                {"name": "ein"},
            ],
            select_star_rows=[
                {"match_id": 1, "ein": "111"},
                {"match_id": 2, "ein": "222"},
            ],
        )
        columns, rows = _read_temp_output_rows(conn, "test_table")
        assert "match_id" in columns
        assert len(rows) == 2

    def test_empty_result_set(self):
        conn = PostgresCompatConnection(
            pragma_temp_rows=[
                {"name": "match_id"},
                {"name": "ein"},
            ],
            select_star_rows=[],
        )
        columns, rows = _read_temp_output_rows(conn, "test_table")
        assert columns == ["match_id", "ein"]
        assert rows == []


# ---------------------------------------------------------------------------
# Tests: _build_insert_sql — Postgres ON CONFLICT coverage
# ---------------------------------------------------------------------------

class TestBuildInsertSqlPostgres:
    def _pg_conn(self):
        return PostgresCompatConnection()

    def test_lobbying_donor_matches_replace_uses_on_conflict(self):
        sql = _build_insert_sql(
            self._pg_conn(),
            "lobbying_donor_matches",
            ["client_id", "donor_key", "client_name", "donor_name", "score", "method"],
            replace=True,
        )
        assert "ON CONFLICT (client_id, donor_key)" in sql
        assert "DO UPDATE SET" in sql
        assert "INSERT OR REPLACE" not in sql

    def test_irs527_committee_matches_replace(self):
        sql = _build_insert_sql(
            self._pg_conn(),
            "irs527_committee_matches",
            ["ein", "committee_id_sbe", "org_name", "committee_name", "score", "method"],
            replace=True,
        )
        assert "ON CONFLICT (ein, committee_id_sbe)" in sql

    def test_lobbying_527_matches_replace(self):
        sql = _build_insert_sql(
            self._pg_conn(),
            "lobbying_527_matches",
            ["client_id", "ein", "client_name", "org_name", "score"],
            replace=True,
        )
        assert "ON CONFLICT (client_id, ein)" in sql

    def test_donor_address_index_replace_uses_on_conflict(self):
        """cross_matching_donor_address_index has donor_key PK.
        _build_insert_sql with replace=True must generate ON CONFLICT.
        If this test fails, Postgres upserts become plain INSERTs that crash
        on duplicate keys."""
        sql = _build_insert_sql(
            self._pg_conn(),
            "cross_matching_donor_address_index",
            ["donor_key", "donor_name", "donor_city", "donor_state",
             "donor_zip5", "norm_city", "norm_state", "donor_tokens"],
            replace=True,
        )
        assert "ON CONFLICT" in sql, (
            "cross_matching_donor_address_index missing from _REPLACE_TARGET_PRIMARY_KEYS"
        )

    def test_table_without_known_pk_gets_plain_insert(self):
        sql = _build_insert_sql(
            self._pg_conn(),
            "irs527_director_donor_matches",
            ["ein", "org_name", "director_name", "donor_key", "donor_name", "score"],
            replace=True,
        )
        # No known PK → plain INSERT (no ON CONFLICT)
        assert "ON CONFLICT" not in sql
        assert sql.startswith("INSERT INTO")

    def test_non_replace_always_plain_insert(self):
        sql = _build_insert_sql(
            self._pg_conn(),
            "lobbying_donor_matches",
            ["client_id", "donor_key", "client_name"],
            replace=False,
        )
        assert "ON CONFLICT" not in sql
        assert "INSERT INTO" in sql


# ---------------------------------------------------------------------------
# Tests: _dedupe_replace_target_rows
# ---------------------------------------------------------------------------

class TestDedupeReplaceTargetRows:
    def test_deduplicates_by_composite_pk(self):
        rows = [
            ("111", "Org A", 10, "Cmte", 0.85, "jaccard"),
            ("111", "Org Updated", 10, "Cmte", 0.90, "jaccard"),
            ("222", "Org B", 20, "Cmte2", 0.80, "jaccard"),
        ]
        columns = ["ein", "org_name", "committee_id_sbe", "committee_name", "score", "method"]
        result = _dedupe_replace_target_rows("irs527_committee_matches", columns, rows)
        assert len(result) == 2
        # Last-write wins
        eins = {r[0] for r in result}
        assert "111" in eins
        assert "222" in eins

    def test_no_dedup_for_unknown_table(self):
        rows = [(1, "a"), (1, "b"), (2, "c")]
        result = _dedupe_replace_target_rows("unknown_table", ["id", "name"], rows)
        assert len(result) == 3  # No dedup applied

    def test_empty_rows(self):
        result = _dedupe_replace_target_rows("lobbying_donor_matches", ["c", "d"], [])
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _inject_required_surrogate_ids_for_postgres
# ---------------------------------------------------------------------------

class TestInjectSurrogateIdsPostgres:
    def test_adds_match_id_when_not_null_no_default(self):
        conn = PostgresCompatConnection(
            info_schema_rows=[
                {"column_name": "match_id", "is_nullable": "NO",
                 "column_default": None, "ordinal_position": 1},
                {"column_name": "ein", "is_nullable": "YES",
                 "column_default": None, "ordinal_position": 2},
            ],
        )
        columns = ["ein", "org_name"]
        rows = [("111", "Org A"), ("222", "Org B")]
        new_cols, new_rows = _inject_required_surrogate_ids_for_postgres(
            conn, "test_table", columns, rows,
        )
        assert new_cols[0] == "match_id"
        assert new_rows[0][0] == 1
        assert new_rows[1][0] == 2

    def test_replaces_null_match_id_in_existing_column(self):
        conn = PostgresCompatConnection(
            info_schema_rows=[
                {"column_name": "match_id", "is_nullable": "NO",
                 "column_default": None, "ordinal_position": 1},
            ],
        )
        columns = ["match_id", "ein"]
        rows = [(None, "111"), (None, "222")]
        new_cols, new_rows = _inject_required_surrogate_ids_for_postgres(
            conn, "test_table", columns, rows,
        )
        assert new_cols == ["match_id", "ein"]
        assert new_rows[0][0] == 1
        assert new_rows[1][0] == 2

    def test_no_op_when_column_has_default(self):
        conn = PostgresCompatConnection(
            info_schema_rows=[
                {"column_name": "match_id", "is_nullable": "NO",
                 "column_default": "nextval('seq'::regclass)", "ordinal_position": 1},
            ],
        )
        columns = ["ein"]
        rows = [("111",)]
        new_cols, new_rows = _inject_required_surrogate_ids_for_postgres(
            conn, "test_table", columns, rows,
        )
        assert new_cols == ["ein"]
        assert new_rows == [("111",)]

    def test_no_op_for_sqlite(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        columns = ["ein"]
        rows = [("111",)]
        new_cols, new_rows = _inject_required_surrogate_ids_for_postgres(
            conn, "test_table", columns, rows,
        )
        assert new_cols == columns
        assert new_rows == rows
        conn.close()

    def test_no_op_for_empty_rows(self):
        conn = PostgresCompatConnection()
        columns = ["ein"]
        new_cols, new_rows = _inject_required_surrogate_ids_for_postgres(
            conn, "test_table", columns, [],
        )
        assert new_cols == columns
        assert new_rows == []


# ---------------------------------------------------------------------------
# Tests: _merge_rows_into_output_table — Postgres path
# ---------------------------------------------------------------------------

class TestMergeRowsPostgres:
    def test_truncate_and_insert_pattern(self):
        """Postgres merge should use TRUNCATE + INSERT (not swap-table)."""
        conn = PostgresCompatConnection()
        _merge_rows_into_output_table(
            conn,
            "lobbying_donor_matches",
            ["client_id", "donor_key", "client_name", "score", "method"],
            [(1, "k1", "Client", 0.95, "jaccard")],
        )
        all_sql = [s for s, _ in conn.execute_log]
        assert any("TRUNCATE TABLE" in s for s in all_sql), "Expected TRUNCATE"
        assert conn.executemany_log  # Should have INSERT

    def test_empty_rows_still_truncates(self):
        """Even with 0 rows, Postgres should TRUNCATE output table."""
        conn = PostgresCompatConnection(info_schema_rows=[])
        _merge_rows_into_output_table(
            conn,
            "irs527_director_donor_matches",
            ["ein", "donor_key"],
            [],
        )
        all_sql = [s for s, _ in conn.execute_log]
        assert any("TRUNCATE" in s for s in all_sql)
        # No executemany for empty rows
        assert conn.executemany_log == []

    def test_dedup_before_insert(self):
        conn = PostgresCompatConnection()
        _merge_rows_into_output_table(
            conn,
            "lobbying_donor_matches",
            ["client_id", "donor_key", "client_name", "score", "method"],
            [
                (1, "k1", "Old", 0.80, "jaccard"),
                (1, "k1", "New", 0.90, "jaccard"),
            ],
        )
        # Only 1 row after dedup
        assert conn.executemany_log
        _, rows = conn.executemany_log[0]
        assert len(rows) == 1
        assert rows[0][2] == "New"

    def test_rollback_on_failure(self):
        """If TRUNCATE fails, rollback should be called."""
        conn = PostgresCompatConnection()
        call_count = [0]
        original_execute = conn.execute

        def failing_execute(sql, params=None):
            call_count[0] += 1
            if "TRUNCATE" in sql:
                raise RuntimeError("truncate failure")
            return original_execute(sql, params)

        conn.execute = failing_execute
        with pytest.raises(RuntimeError, match="truncate failure"):
            _merge_rows_into_output_table(
                conn,
                "lobbying_donor_matches",
                ["client_id", "donor_key"],
                [(1, "k1")],
            )
        assert conn._rolled_back >= 1


class TestMergeRowsSQLite:
    def test_delete_and_insert(self, tmp_path):
        import sqlite3
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE lobbying_donor_matches ("
            "client_id INTEGER, donor_key TEXT, PRIMARY KEY(client_id, donor_key))"
        )
        conn.execute("INSERT INTO lobbying_donor_matches VALUES (1, 'old')")
        conn.commit()

        _merge_rows_into_output_table(
            conn,
            "lobbying_donor_matches",
            ["client_id", "donor_key"],
            [(2, "new")],
        )

        rows = conn.execute("SELECT * FROM lobbying_donor_matches").fetchall()
        assert len(rows) == 1
        assert rows[0]["client_id"] == 2
        conn.close()


# ---------------------------------------------------------------------------
# Tests: _table_fingerprint — Postgres rowid fallback
# ---------------------------------------------------------------------------

class TestTableFingerprintPostgres:
    def test_no_rowid_falls_back_to_count_only(self):
        """Postgres tables don't have implicit rowid. Fingerprint should
        fall back to COUNT(*) with m=0."""
        first_call = [True]

        def exec_handler(sql, params=None):
            if first_call[0] and "rowid" in sql.lower():
                first_call[0] = False
                raise Exception("column rowid does not exist")
            return _FakeResult([{"c": 42, "m": 0}])

        conn = PostgresCompatConnection()
        conn.execute = exec_handler
        fp = _table_fingerprint(conn, "some_table")
        assert fp == "42:0"

    def test_missing_table(self):
        conn = PostgresCompatConnection()
        # _table_exists will query sqlite_master → no rows on Postgres mock
        # but _table_fingerprint checks _table_exists first
        # Our mock returns empty for sqlite_master query, so table_exists → False
        fp = _table_fingerprint(conn, "nonexistent_table")
        assert fp == "missing"


# ---------------------------------------------------------------------------
# Tests: _set_busy_timeout and _apply_postgres_session_tuning
# ---------------------------------------------------------------------------

class TestPostgresSessionTuning:
    def test_busy_timeout_noop_on_postgres(self):
        conn = PostgresCompatConnection()
        _set_busy_timeout(conn, 60000)
        # Should not execute any PRAGMA
        assert not any("PRAGMA" in s for s, _ in conn.execute_log)

    def test_session_tuning_executes_settings(self):
        conn = PostgresCompatConnection()
        _apply_postgres_session_tuning(conn)
        sqls = [s for s, _ in conn.execute_log]
        assert "SET synchronous_commit TO OFF" in sqls
        assert any("work_mem" in s for s in sqls)

    def test_session_tuning_noop_on_sqlite(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        _apply_postgres_session_tuning(conn)
        conn.close()  # Should not raise


# ---------------------------------------------------------------------------
# Tests: _safe_identifier
# ---------------------------------------------------------------------------

class TestSafeIdentifier:
    def test_valid_name(self):
        assert _safe_identifier("lobbying_donor_matches") == "lobbying_donor_matches"

    def test_rejects_injection(self):
        with pytest.raises(ValueError):
            _safe_identifier("table; DROP TABLE users")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError):
            _safe_identifier("my table")


# ---------------------------------------------------------------------------
# Tests: Edge cases — normalization functions
# ---------------------------------------------------------------------------

class TestNormalizationEdgeCases:
    def test_normalize_city_none(self):
        assert _normalize_city(None) is None

    def test_normalize_city_empty(self):
        assert _normalize_city("") is None
        assert _normalize_city("   ") is None

    def test_normalize_city_strips_and_lowercases(self):
        assert _normalize_city("  Springfield  ") == "springfield"

    def test_normalize_zip5_standard(self):
        assert _normalize_zip5("62704") == "62704"

    def test_normalize_zip5_with_plus4(self):
        assert _normalize_zip5("62704-1234") == "62704"

    def test_normalize_zip5_short_padded(self):
        assert _normalize_zip5("123") == "00123"

    def test_normalize_zip5_none(self):
        assert _normalize_zip5(None) is None

    def test_normalize_zip5_empty(self):
        assert _normalize_zip5("") is None

    def test_address_score_same_zip_city_state(self):
        assert _address_score("Springfield", "IL", "62704",
                              "Springfield", "IL", "62704") == 1.0

    def test_address_score_same_zip_diff_city(self):
        assert _address_score("Springfield", "IL", "62704",
                              "Chatham", "IL", "62704") == 0.7

    def test_address_score_diff_state(self):
        assert _address_score("Springfield", "IL", "62704",
                              "Springfield", "MO", "65801") == 0.0

    def test_address_score_same_city_no_zip(self):
        assert _address_score("Springfield", "IL", None,
                              "Springfield", "IL", None) == 0.5


class TestTokenizationMalformedInput:
    def test_bom_prefix(self):
        assert _normalize_name_tokens("\ufeffAcme Corp") == ["acme"]

    def test_unicode_names(self):
        tokens = _normalize_name_tokens("Müller & Associés")
        assert "m" in tokens[0] or "ller" in tokens[0]  # depends on regex

    def test_all_stop_words(self):
        assert _normalize_name_tokens("the of for and") == []

    def test_numeric_only(self):
        tokens = _normalize_name_tokens("123 456")
        assert tokens == ["123", "456"]


# ---------------------------------------------------------------------------
# Tests: _clear_table — Postgres TRUNCATE, SQLite DELETE
# ---------------------------------------------------------------------------

class TestClearTable:
    def test_postgres_uses_truncate(self):
        conn = PostgresCompatConnection()
        _clear_table(conn, "lobbying_donor_matches")
        all_sql = [s for s, _ in conn.execute_log]
        assert any("TRUNCATE TABLE lobbying_donor_matches" in s for s in all_sql)

    def test_sqlite_uses_delete(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE lobbying_donor_matches (x TEXT)")
        _clear_table(conn, "lobbying_donor_matches")
        row = conn.execute("SELECT COUNT(*) AS c FROM lobbying_donor_matches").fetchone()
        assert row["c"] == 0
        conn.close()

    def test_rejects_unsafe_name(self):
        conn = PostgresCompatConnection()
        with pytest.raises(ValueError):
            _clear_table(conn, "table; DROP TABLE x")


# ---------------------------------------------------------------------------
# Tests: Postgres merge — transaction handling
# ---------------------------------------------------------------------------

class TestMergeTransactionHandling:
    def test_truncate_is_used_for_merge(self):
        """Verify TRUNCATE is used as part of the merge."""
        conn = PostgresCompatConnection()
        _merge_rows_into_output_table(
            conn,
            "lobbying_donor_matches",
            ["client_id", "donor_key"],
            [(1, "k1")],
        )
        all_sql = [s for s, _ in conn.execute_log]
        # TRUNCATE should be present instead of BEGIN + LOCK + RENAME
        assert any("TRUNCATE" in s for s in all_sql)

    def test_commit_after_merge(self):
        conn = PostgresCompatConnection()
        _merge_rows_into_output_table(
            conn,
            "lobbying_donor_matches",
            ["client_id", "donor_key"],
            [(1, "k1")],
        )
        assert conn._committed >= 1


# ---------------------------------------------------------------------------
# Tests: Replace target PKs completeness
# ---------------------------------------------------------------------------

class TestReplaceTargetPrimaryKeysCompleteness:
    def test_donor_address_index_in_replace_targets(self):
        """cross_matching_donor_address_index must be in _REPLACE_TARGET_PRIMARY_KEYS
        so Postgres upserts work correctly."""
        assert "cross_matching_donor_address_index" in _REPLACE_TARGET_PRIMARY_KEYS, (
            "Missing from _REPLACE_TARGET_PRIMARY_KEYS — Postgres upserts will fail"
        )

    def test_all_pk_tables_have_replace_key(self):
        """Tables with composite PKs used with replace=True must be registered."""
        pk_tables = {
            "lobbying_donor_matches": ("client_id", "donor_key"),
            "irs527_committee_matches": ("ein", "committee_id_sbe"),
            "lobbying_527_matches": ("client_id", "ein"),
            "cross_matching_donor_address_index": ("donor_key",),
        }
        for table, expected_pk in pk_tables.items():
            assert table in _REPLACE_TARGET_PRIMARY_KEYS, f"{table} missing"
            assert _REPLACE_TARGET_PRIMARY_KEYS[table] == expected_pk, (
                f"{table}: expected {expected_pk}, got {_REPLACE_TARGET_PRIMARY_KEYS.get(table)}"
            )


# ---------------------------------------------------------------------------
# Tests: Full integration — SQLite-based parallel pipeline
# ---------------------------------------------------------------------------

class TestParallelPipelineSQLite:
    """Run actual parallel cross-matching on SQLite to validate correctness."""

    def _setup(self, tmp_path):
        from database.connection import get_db, init_db
        db_path = str(tmp_path / "test_parallel.db")
        init_db(db_path)
        conn = get_db(db_path)
        # Seed test data
        conn.execute(
            "INSERT OR IGNORE INTO lobbying_clients (client_id, client_name) VALUES (1, 'Northwestern University')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO analytics_donor_summary "
            "(source, donor_key, donor_name, donor_city, donor_state, total_amount, contribution_count, committee_count) "
            "VALUES ('bulk_receipts', 'nw|uni', 'Northwestern University', 'Evanston', 'IL', 5000, 1, 1)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO irs527_organizations "
            "(ein, form_id, form_id_seq, org_name, state) VALUES ('123456789', 1, 0, 'Citizens for Springfield', 'IL')"
        )
        conn.commit()
        conn.close()
        return db_path

    def test_parallel_produces_all_keys(self, tmp_path):
        from database.cross_matching import run_all_cross_matching_parallel
        db_path = self._setup(tmp_path)
        results = run_all_cross_matching_parallel(db_path, threshold=0.80, max_workers=2)

        expected = {
            "lobbying_donors", "lobbying_expenditures",
            "527_committees", "527_expenditures", "527_directors",
            "527_director_candidates", "527_director_addresses",
            "527_org_addresses", "lobbying_527",
        }
        assert expected.issubset(results.keys())
        assert results["_errors"] == []

    def test_parallel_matches_sequential(self, tmp_path):
        from database.connection import get_db
        from database.cross_matching import run_all_cross_matching, run_all_cross_matching_parallel

        db_path = self._setup(tmp_path)
        conn = get_db(db_path)
        seq = run_all_cross_matching(conn, threshold=0.80)
        conn.close()

        par = run_all_cross_matching_parallel(db_path, threshold=0.80, max_workers=2)
        par.pop("_errors", None)

        for key in seq:
            assert seq[key].get("matches", 0) == par[key].get("matches", 0), (
                f"{key}: seq={seq[key]} != par={par[key]}"
            )

    def test_parallel_incremental_skips_unchanged(self, tmp_path):
        from database.cross_matching import run_all_cross_matching_parallel
        db_path = self._setup(tmp_path)

        # First run populates fingerprints
        r1 = run_all_cross_matching_parallel(db_path, threshold=0.80, max_workers=2, incremental=True)
        assert r1["_errors"] == []

        # Second run should skip unchanged
        r2 = run_all_cross_matching_parallel(db_path, threshold=0.80, max_workers=2, incremental=True)
        skipped = sum(
            1 for k, v in r2.items()
            if k != "_errors" and v.get("skipped") == "unchanged"
        )
        assert skipped >= 7  # Most jobs should be unchanged


# ---------------------------------------------------------------------------
# Tests: Merge with identity columns for different table types
# ---------------------------------------------------------------------------

class TestMergeIdentityColumnsAllTables:
    """Verify merge handles match_id properly for every output table."""

    @pytest.mark.parametrize("table_name", [
        "lobbying_expenditure_matches",
        "irs527_expenditure_recipient_matches",
        "irs527_director_donor_matches",
        "irs527_director_candidate_matches",
        "irs527_director_address_matches",
        "irs527_org_address_matches",
    ])
    def test_autoincrement_tables_inject_match_id(self, table_name):
        """Tables with match_id AUTOINCREMENT should get synthetic IDs on Postgres."""
        conn = PostgresCompatConnection(
            info_schema_rows=[
                {"column_name": "match_id", "is_nullable": "NO",
                 "column_default": None, "ordinal_position": 1},
                {"column_name": "ein", "is_nullable": "YES",
                 "column_default": None, "ordinal_position": 2},
                {"column_name": "score", "is_nullable": "NO",
                 "column_default": None, "ordinal_position": 3},
            ],
        )
        _merge_rows_into_output_table(
            conn, table_name,
            ["ein", "score"],
            [("111", 0.9), ("222", 0.8)],
        )
        assert conn.executemany_log
        _, rows = conn.executemany_log[0]
        assert len(rows) == 2
        # match_id should be injected as column 0
        assert rows[0][0] == 1
        assert rows[1][0] == 2


# ---------------------------------------------------------------------------
# Tests: pg_compat PRAGMA temp.table_info translation
# ---------------------------------------------------------------------------

class TestPragmaTempTableInfoTranslation:
    """Verify that PRAGMA temp.table_info() correctly resolves temp schema
    columns on Postgres, not the public schema columns."""

    def test_pragma_temp_regex_matches(self):
        """The pg_compat regex should match PRAGMA temp.table_info(name)."""
        from database.pg_compat import _PRAGMA_TABLE_INFO_RE
        sql = "PRAGMA temp.table_info(lobbying_donor_matches)"
        match = _PRAGMA_TABLE_INFO_RE.match(sql)
        assert match is not None
        assert match.group("table") == "lobbying_donor_matches"

    def test_pragma_without_temp_prefix(self):
        from database.pg_compat import _PRAGMA_TABLE_INFO_RE
        sql = "PRAGMA table_info(lobbying_donor_matches)"
        match = _PRAGMA_TABLE_INFO_RE.match(sql)
        assert match is not None


# ---------------------------------------------------------------------------
# Tests: Performance optimization — TRUNCATE for Postgres
# ---------------------------------------------------------------------------

class TestPostgresTruncate:
    def test_merge_uses_truncate_for_postgres(self):
        """On Postgres, full-rebuild merge should use TRUNCATE for faster clearing."""
        conn = PostgresCompatConnection()
        _merge_rows_into_output_table(
            conn, "lobbying_donor_matches",
            ["client_id", "donor_key"],
            [(1, "k1")],
        )
        all_sql = [s for s, _ in conn.execute_log]
        assert any("TRUNCATE" in s for s in all_sql)
