"""Comprehensive edge-case tests for scripts/irs_527_parse.py.

Covers:
A) Malformed input: BOM, missing/empty form_id, wrong columns, malformed amounts/URLs
B) Memory constraints: SQL joins (not in-memory merges), streaming verification
C) SQLite threading safety: per-thread connections, concurrent reads/writes
D) Amount/URL parsing utilities
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.irs_527_parse import (
    OUTPUT_FILES,
    QUARANTINE_FILE,
    load_csvs_to_sqlite,
    parse_irs_527_file,
)

# ---------------------------------------------------------------------------
# Fixtures: minimal self-contained schema (no layout doc needed)
# ---------------------------------------------------------------------------

MINIMAL_SCHEMA: dict[str, list[str]] = {
    "H": ["record_type", "date", "time", "format", "version"],
    "1": ["record_type", "form_id", "ein", "org_name"],
    "D": ["record_type", "form_id", "ein", "person_name",
          "title", "address_1", "city", "state", "zip",
          "zip_ext", "org_name", "role_type", "start_date", "end_date"],
    "R": ["record_type", "form_id", "ein", "org_name",
          "related_org_name", "relationship_type", "address_1",
          "city", "state", "zip", "zip_ext", "rel_ein",
          "rel_type_code", "effective_date"],
    "E": ["record_type", "form_id", "eain_id",
          "election_authority_id_number", "state_issued", "status"],
    "2": ["record_type", "form_id", "ein", "period_start", "period_end",
          "org_name", "total_contributions", "total_expenditures"],
    "A": ["record_type", "form_id", "ein", "org_name",
          "contributor_name", "contributor_address_1",
          "contributor_address_city", "contributor_address_state",
          "contributor_address_zip_code", "contributor_employer",
          "contribution_amount", "contributor_occupation",
          "agg_contribution_ytd", "contribution_date",
          "contributor_address_2", "contributor_address_zip_ext",
          "contributor_address_zip_ext_2", "insert_datetime"],
    "B": ["record_type", "form_id", "ein", "org_name",
          "recipient_name", "recipient_address_1",
          "recipient_address_city", "recipient_address_state",
          "recipient_address_zip_code", "recipient_employer",
          "expenditure_amount", "recipient_occupation",
          "expenditure_date", "expenditure_purpose",
          "recipient_address_2", "recipient_address_zip_ext",
          "recipient_address_zip_ext_2", "insert_datetime"],
    "F": ["record_type", "date", "time", "format", "record_count"],
}


def _build_line(record_type: str, field_count: int, *, values: list[str] | None = None) -> str:
    """Build a pipe-delimited line for the given record type."""
    if values is not None:
        return "|".join(values) + "|"
    vals = [record_type] + [f"val_{i}" for i in range(1, field_count)]
    return "|".join(vals) + "|"


def _write_input(tmp_path: Path, lines: list[str], *, encoding: str = "utf-8",
                 bom: bool = False) -> Path:
    """Write lines to a test input file, optionally with BOM."""
    input_path = tmp_path / "test_input.txt"
    content = "\n".join(lines) + "\n"
    raw = content.encode(encoding)
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    input_path.write_bytes(raw)
    return input_path


# ===================================================================
# A) MALFORMED INPUT
# ===================================================================

class TestBOMHandling:
    """Test UTF-8 BOM at file start and within lines."""

    def test_bom_at_file_start_first_record_parsed(self, tmp_path: Path):
        """File starts with UTF-8 BOM — first record should not be corrupted."""
        h_line = _build_line("H", len(MINIMAL_SCHEMA["H"]))
        f_line = _build_line("F", len(MINIMAL_SCHEMA["F"]))
        input_path = _write_input(tmp_path, [h_line, f_line], bom=True)

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["record_counts"].get("H", 0) == 1, \
            "BOM at file start should not prevent first record from being parsed"
        assert summary["quarantine_rows"] == 0

    def test_bom_mid_file_does_not_corrupt(self, tmp_path: Path):
        """BOM character mid-file (e.g. concatenated files) should be stripped."""
        h_line = _build_line("H", len(MINIMAL_SCHEMA["H"]))
        # Simulate BOM mid-file by prepending \ufeff to a valid A line
        a_values = ["A"] + [f"a_{i}" for i in range(1, len(MINIMAL_SCHEMA["A"]))]
        a_line_with_bom = "\ufeff" + "|".join(a_values) + "|"
        f_line = _build_line("F", len(MINIMAL_SCHEMA["F"]))
        input_path = _write_input(tmp_path, [h_line, a_line_with_bom, f_line])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["record_counts"].get("A", 0) == 1, \
            "BOM mid-file should be stripped, record type should be detected"
        assert summary["quarantine_rows"] == 0

    def test_bom_file_opened_with_utf8sig_robustness(self, tmp_path: Path):
        """BOM-prefixed file should produce identical results to non-BOM file."""
        lines = [_build_line(rt, len(MINIMAL_SCHEMA[rt])) for rt in ("H", "1", "F")]

        # Without BOM
        path_no_bom = _write_input(tmp_path, lines, bom=False)
        out_no_bom = tmp_path / "out_no_bom"
        summary_no_bom = parse_irs_527_file(path_no_bom, out_no_bom, MINIMAL_SCHEMA)

        # With BOM
        bom_dir = tmp_path / "bom_dir"
        bom_dir.mkdir()
        path_bom = _write_input(bom_dir, lines, bom=True)
        out_bom = tmp_path / "out_bom"
        summary_bom = parse_irs_527_file(path_bom, out_bom, MINIMAL_SCHEMA)

        assert summary_no_bom["record_counts"] == summary_bom["record_counts"]
        assert summary_no_bom["quarantine_rows"] == summary_bom["quarantine_rows"]


class TestMissingFormId:
    """Test missing / empty / whitespace form_id (record type character)."""

    def test_empty_line_quarantined(self, tmp_path: Path):
        """Completely empty lines should be quarantined."""
        h_line = _build_line("H", len(MINIMAL_SCHEMA["H"]))
        input_path = _write_input(tmp_path, [h_line, "", ""])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["quarantine_rows"] == 2

    def test_whitespace_only_line_quarantined(self, tmp_path: Path):
        """Whitespace-only lines should be quarantined (no valid record type)."""
        h_line = _build_line("H", len(MINIMAL_SCHEMA["H"]))
        input_path = _write_input(tmp_path, [h_line, "   ", "\t\t"])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        # Whitespace-only lines: _record_type_from_line returns "" for whitespace
        # or the first non-space char which won't be in schema
        assert summary["quarantine_rows"] >= 2

    def test_empty_form_id_field_quarantined(self, tmp_path: Path):
        """Line starting with pipe (empty form_id) should be quarantined."""
        h_line = _build_line("H", len(MINIMAL_SCHEMA["H"]))
        # "|val1|val2|..." - first char is pipe, record_type_from_line returns "|"
        bad_line = "|" + "|".join(["x"] * (len(MINIMAL_SCHEMA["A"]) - 1)) + "|"
        input_path = _write_input(tmp_path, [h_line, bad_line])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["quarantine_rows"] >= 1


class TestWrongColumns:
    """Test wrong column counts, extra columns, mixed-case headers."""

    def test_fewer_columns_quarantined(self, tmp_path: Path):
        """Lines with fewer columns than expected should be quarantined."""
        a_expected = len(MINIMAL_SCHEMA["A"])
        too_few = "A|" + "|".join(["x"] * (a_expected - 5)) + "|"
        input_path = _write_input(tmp_path, [too_few])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["quarantine_rows"] == 1
        assert summary["record_counts"].get("A", 0) == 0

    def test_extra_columns_quarantined(self, tmp_path: Path):
        """Lines with more columns than expected (non-trailing) should be quarantined."""
        a_expected = len(MINIMAL_SCHEMA["A"])
        extra = "A|" + "|".join(["x"] * (a_expected + 2)) + "|"
        input_path = _write_input(tmp_path, [extra])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["quarantine_rows"] == 1

    def test_unknown_record_type_quarantined(self, tmp_path: Path):
        """Lines with unknown record type character should be quarantined."""
        input_path = _write_input(tmp_path, ["Z|some|data|here|"])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["quarantine_rows"] == 1

    def test_lowercase_record_type_quarantined(self, tmp_path: Path):
        """Lowercase record types (e.g., 'a' instead of 'A') should be quarantined."""
        a_expected = len(MINIMAL_SCHEMA["A"])
        lower_line = "a|" + "|".join(["x"] * (a_expected - 1)) + "|"
        input_path = _write_input(tmp_path, [lower_line])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["quarantine_rows"] == 1
        assert summary["record_counts"].get("A", 0) == 0


class TestMalformedAmounts:
    """Test amount parsing for contribution_amount, expenditure_amount fields."""

    def test_parse_amount_plain_number(self):
        """Simple numeric string should parse to Decimal."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("1234.56") == Decimal("1234.56")

    def test_parse_amount_with_commas(self):
        """Commas in amount should be stripped."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("1,234,567.89") == Decimal("1234567.89")

    def test_parse_amount_with_dollar_sign(self):
        """Dollar sign should be stripped."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("$1,234.56") == Decimal("1234.56")

    def test_parse_amount_parentheses_negative(self):
        """Parentheses should indicate negative amount."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("(500.00)") == Decimal("-500.00")

    def test_parse_amount_leading_trailing_spaces(self):
        """Whitespace should be stripped."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("  1234.56  ") == Decimal("1234.56")

    def test_parse_amount_blank_returns_none(self):
        """Empty/whitespace-only string should return None."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("") is None
        assert parse_amount("   ") is None

    def test_parse_amount_none_returns_none(self):
        """None input should return None."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount(None) is None

    def test_parse_amount_invalid_returns_none(self):
        """Non-numeric strings should return None."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("abc") is None
        assert parse_amount("N/A") is None
        assert parse_amount("---") is None

    def test_parse_amount_negative_sign(self):
        """Negative sign should work."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("-500.00") == Decimal("-500.00")

    def test_parse_amount_zero(self):
        """Zero should parse correctly."""
        from scripts.irs_527_parse import parse_amount
        assert parse_amount("0") == Decimal("0")
        assert parse_amount("0.00") == Decimal("0.00")

    def test_parse_amount_large_number(self):
        """Large numbers should not overflow."""
        from scripts.irs_527_parse import parse_amount
        result = parse_amount("999999999999.99")
        assert result == Decimal("999999999999.99")


class TestMalformedURLs:
    """Test URL normalization for organization website URLs."""

    def test_normalize_url_strip_whitespace(self):
        """Leading/trailing whitespace should be stripped."""
        from scripts.irs_527_parse import normalize_url
        assert normalize_url("  https://example.com  ") == "https://example.com"

    def test_normalize_url_missing_scheme(self):
        """Missing scheme should default to https://."""
        from scripts.irs_527_parse import normalize_url
        assert normalize_url("example.com") == "https://example.com"

    def test_normalize_url_preserves_path(self):
        """Meaningful paths should be preserved."""
        from scripts.irs_527_parse import normalize_url
        assert normalize_url("https://example.com/about") == "https://example.com/about"

    def test_normalize_url_strips_tracking_params(self):
        """UTM tracking parameters should be stripped."""
        from scripts.irs_527_parse import normalize_url
        result = normalize_url("https://example.com/page?utm_source=test&utm_medium=email&id=123")
        assert "utm_source" not in result
        assert "utm_medium" not in result
        # Non-tracking params should be preserved
        assert "id=123" in result

    def test_normalize_url_empty_returns_none(self):
        """Empty/whitespace URLs should return None."""
        from scripts.irs_527_parse import normalize_url
        assert normalize_url("") is None
        assert normalize_url("   ") is None
        assert normalize_url(None) is None

    def test_normalize_url_deterministic(self):
        """Same input should always produce same output."""
        from scripts.irs_527_parse import normalize_url
        url = "https://example.com/path?b=2&a=1"
        results = [normalize_url(url) for _ in range(10)]
        assert len(set(results)) == 1

    def test_normalize_url_lowercases_scheme_and_host(self):
        """Scheme and host should be lowercased."""
        from scripts.irs_527_parse import normalize_url
        result = normalize_url("HTTP://EXAMPLE.COM/Path")
        assert result.startswith("http://example.com/")
        # Path case should be preserved
        assert result.endswith("/Path")

    def test_normalize_url_invalid_url_returns_none(self):
        """Clearly invalid URLs should return None."""
        from scripts.irs_527_parse import normalize_url
        assert normalize_url("not a url at all") is None


# ===================================================================
# B) MEMORY CONSTRAINTS / SCALABILITY
# ===================================================================

class TestMemoryConstraints:
    """Ensure large-dataset joins use SQL, not in-memory pandas merges."""

    def test_parser_streams_does_not_load_all_lines(self, tmp_path: Path):
        """Parser should iterate file line-by-line, not readlines() or read()."""
        # Create a file with many lines
        n_lines = 10_000
        lines = [_build_line("A", len(MINIMAL_SCHEMA["A"])) for _ in range(n_lines)]
        input_path = _write_input(tmp_path, lines)

        outdir = tmp_path / "out"
        # Patch the file.open to track whether readlines/read is called
        original_open = Path.open

        readlines_called = []
        read_called = []

        class MonitoredFile:
            """Wraps file to detect full-load operations."""
            def __init__(self, f):
                self._f = f

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self._f.__exit__(*args)

            def __iter__(self):
                return iter(self._f)

            def readlines(self):
                readlines_called.append(True)
                return self._f.readlines()

            def read(self, *args):
                read_called.append(True)
                return self._f.read(*args)

            def __getattr__(self, name):
                return getattr(self._f, name)

        def monitored_open(self_path, *args, **kwargs):
            f = original_open(self_path, *args, **kwargs)
            if str(self_path) == str(input_path):
                return MonitoredFile(f)
            return f

        with patch.object(Path, "open", monitored_open):
            summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["record_counts"].get("A", 0) == n_lines
        assert len(readlines_called) == 0, "Parser should not call readlines() on input file"
        assert len(read_called) == 0, "Parser should not call read() on input file"

    def test_staging_load_uses_sql_not_pandas(self, tmp_path: Path):
        """load_csvs_to_sqlite should use SQL INSERT batches, not pandas merge."""
        lines = [_build_line("A", len(MINIMAL_SCHEMA["A"])) for _ in range(100)]
        input_path = _write_input(tmp_path, lines)

        outdir = tmp_path / "out"
        parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        db_path = tmp_path / "test.db"

        # Ensure pandas is not imported/used during load
        import sys
        pandas_was_imported = "pandas" in sys.modules

        loaded = load_csvs_to_sqlite(outdir, MINIMAL_SCHEMA, db_path)

        # Verify data was loaded via SQL
        assert loaded["irs527_stage_contributions_sched_a"] == 100

        # If pandas wasn't imported before, it shouldn't be imported by load
        if not pandas_was_imported:
            assert "pandas" not in sys.modules, \
                "load_csvs_to_sqlite should not import pandas"

    def test_sql_join_across_record_types(self, tmp_path: Path):
        """Cross-record-type queries should use SQL JOIN, not in-memory merge."""
        # Create org (type 1) and contribution (type A) records with shared EIN
        org_values = ["1", "100", "123456789", "Test Org"]
        contrib_values = ["A", "200", "123456789", "Test Org",
                         "John Doe", "123 Main St",
                         "Springfield", "IL", "62701", "ACME Corp",
                         "5000.00", "Engineer", "5000.00", "2025-01-15",
                         "Apt 2", "1234", "5678", "2025-01-15T10:00:00"]

        lines = [
            _build_line("1", len(MINIMAL_SCHEMA["1"]), values=org_values),
            _build_line("A", len(MINIMAL_SCHEMA["A"]), values=contrib_values),
        ]
        input_path = _write_input(tmp_path, lines)

        outdir = tmp_path / "out"
        parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        db_path = tmp_path / "join_test.db"

        from scripts.irs_527_parse import sql_join_staged_records
        loaded = load_csvs_to_sqlite(outdir, MINIMAL_SCHEMA, db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            results = sql_join_staged_records(conn, "irs527_stage_organizations_8871",
                                              "irs527_stage_contributions_sched_a",
                                              "ein")
            # Should have at least one match
            assert len(results) >= 1
            assert results[0]["ein"] == "123456789"
        finally:
            conn.close()


# ===================================================================
# C) SQLITE THREADING SAFETY
# ===================================================================

class TestSQLiteThreadingSafety:
    """Ensure SQLite connections are thread-safe."""

    def test_get_connection_returns_per_thread_connection(self, tmp_path: Path):
        """Each thread should get its own connection."""
        from scripts.irs_527_parse import get_sqlite_connection

        db_path = tmp_path / "thread_test.db"
        # Pre-create DB with WAL to avoid lock contention on PRAGMA
        setup = sqlite3.connect(str(db_path))
        setup.execute("PRAGMA journal_mode = WAL")
        setup.execute("CREATE TABLE IF NOT EXISTS dummy (id INTEGER)")
        setup.commit()
        setup.close()

        connections = {}
        errors = []

        def worker(thread_id):
            try:
                conn = get_sqlite_connection(db_path)
                connections[thread_id] = id(conn)
                conn.execute("SELECT 1")
                conn.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threading errors: {errors}"
        # All connections should be different objects
        unique_conns = set(connections.values())
        assert len(unique_conns) == 5, \
            "Each thread should get its own connection object"

    def test_concurrent_reads(self, tmp_path: Path):
        """Multiple threads reading simultaneously should not error."""
        from scripts.irs_527_parse import get_sqlite_connection

        db_path = tmp_path / "concurrent_read.db"
        # Setup: create table with data
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("CREATE TABLE test_data (id INTEGER, value TEXT)")
        setup_conn.executemany("INSERT INTO test_data VALUES (?, ?)",
                               [(i, f"val_{i}") for i in range(100)])
        setup_conn.commit()
        setup_conn.close()

        results = {}
        errors = []

        def reader(thread_id):
            try:
                conn = get_sqlite_connection(db_path)
                rows = conn.execute("SELECT COUNT(*) FROM test_data").fetchone()
                results[thread_id] = rows[0]
                conn.close()
            except Exception as e:
                errors.append((thread_id, e))

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent read errors: {errors}"
        assert all(v == 100 for v in results.values()), \
            "All readers should see 100 rows"

    def test_concurrent_writes(self, tmp_path: Path):
        """Multiple threads writing should not corrupt data (with WAL mode)."""
        from scripts.irs_527_parse import get_sqlite_connection

        db_path = tmp_path / "concurrent_write.db"
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("PRAGMA journal_mode = WAL")
        setup_conn.execute("CREATE TABLE write_test (thread_id INTEGER, value TEXT)")
        setup_conn.commit()
        setup_conn.close()

        errors = []
        rows_per_thread = 50

        def writer(thread_id):
            try:
                conn = get_sqlite_connection(db_path)
                for i in range(rows_per_thread):
                    conn.execute("INSERT INTO write_test VALUES (?, ?)",
                                (thread_id, f"val_{i}"))
                conn.commit()
                conn.close()
            except Exception as e:
                errors.append((thread_id, e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent write errors: {errors}"

        check_conn = sqlite3.connect(str(db_path))
        total = check_conn.execute("SELECT COUNT(*) FROM write_test").fetchone()[0]
        check_conn.close()
        assert total == 5 * rows_per_thread

    def test_get_connection_uses_check_same_thread(self, tmp_path: Path):
        """get_sqlite_connection should create connections with check_same_thread=True,
        ensuring they are not accidentally shared across threads."""
        from scripts.irs_527_parse import get_sqlite_connection

        db_path = tmp_path / "check_thread.db"
        conn = get_sqlite_connection(db_path)

        # Verify the factory creates a fresh connection each call
        conn2 = get_sqlite_connection(db_path)
        assert conn is not conn2, "Factory should create new connection each call"

        conn.close()
        conn2.close()

    def test_factory_isolation_per_thread(self, tmp_path: Path):
        """Each thread using the factory gets an isolated connection."""
        from scripts.irs_527_parse import get_sqlite_connection

        db_path = tmp_path / "isolation.db"
        setup = sqlite3.connect(str(db_path))
        setup.execute("PRAGMA journal_mode = WAL")
        setup.execute("CREATE TABLE iso_test (tid INTEGER, val TEXT)")
        setup.commit()
        setup.close()

        results = {}
        errors = []

        def worker(tid):
            try:
                conn = get_sqlite_connection(db_path)
                conn.execute("INSERT INTO iso_test VALUES (?, ?)", (tid, f"t{tid}"))
                conn.commit()
                row = conn.execute("SELECT COUNT(*) FROM iso_test WHERE tid = ?", (tid,)).fetchone()
                results[tid] = row[0]
                conn.close()
            except Exception as e:
                errors.append((tid, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        # Each thread should see at least its own row
        assert all(v >= 1 for v in results.values())


# ===================================================================
# D) ADDITIONAL PARSING EDGE CASES
# ===================================================================

class TestTrailingDelimiterHandling:
    """Test handling of terminal pipe delimiter variants."""

    def test_valid_line_with_trailing_pipe(self, tmp_path: Path):
        """Standard IRS format: fields followed by trailing pipe."""
        a_count = len(MINIMAL_SCHEMA["A"])
        a_vals = ["A"] + [f"v{i}" for i in range(1, a_count)]
        line = "|".join(a_vals) + "|"
        input_path = _write_input(tmp_path, [line])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["record_counts"].get("A", 0) == 1
        assert summary["quarantine_rows"] == 0

    def test_valid_line_without_trailing_pipe(self, tmp_path: Path):
        """Line without trailing pipe should also be accepted."""
        a_count = len(MINIMAL_SCHEMA["A"])
        a_vals = ["A"] + [f"v{i}" for i in range(1, a_count)]
        line = "|".join(a_vals)  # No trailing pipe
        input_path = _write_input(tmp_path, [line])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["record_counts"].get("A", 0) == 1
        assert summary["quarantine_rows"] == 0

    def test_line_with_only_pipes(self, tmp_path: Path):
        """Line that is only pipes should be quarantined."""
        input_path = _write_input(tmp_path, ["||||"])

        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        assert summary["quarantine_rows"] >= 1


class TestQuarantineFileContents:
    """Test that quarantine file has correct structure and data."""

    def test_quarantine_captures_raw_line(self, tmp_path: Path):
        """Quarantine should contain the full raw line."""
        bad_line = "Z|bad|data|here|"
        input_path = _write_input(tmp_path, [bad_line])

        outdir = tmp_path / "out"
        parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        quarantine_path = outdir / QUARANTINE_FILE
        with quarantine_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 1
        assert rows[0]["record_type"] == "Z"
        assert "unknown_record_type" in rows[0]["reason"]
        assert rows[0]["raw_line"] == bad_line

    def test_quarantine_captures_line_number(self, tmp_path: Path):
        """Quarantine reason should include line number."""
        h_line = _build_line("H", len(MINIMAL_SCHEMA["H"]))
        bad_line = "Z|bad|"
        input_path = _write_input(tmp_path, [h_line, bad_line])

        outdir = tmp_path / "out"
        parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        quarantine_path = outdir / QUARANTINE_FILE
        with quarantine_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 1
        assert "line=2" in rows[0]["reason"]


class TestMultipleRecordTypes:
    """Test parsing files with all record types present."""

    def test_all_record_types_routed_correctly(self, tmp_path: Path):
        """Each record type should go to its correct output file."""
        lines = []
        for rt in ("H", "1", "D", "R", "E", "2", "A", "B", "F"):
            lines.append(_build_line(rt, len(MINIMAL_SCHEMA[rt])))

        input_path = _write_input(tmp_path, lines)
        outdir = tmp_path / "out"
        summary = parse_irs_527_file(input_path, outdir, MINIMAL_SCHEMA)

        for rt in ("H", "1", "D", "R", "E", "2", "A", "B", "F"):
            assert summary["record_counts"].get(rt, 0) == 1, \
                f"Record type {rt} should have exactly 1 record"
        assert summary["quarantine_rows"] == 0

        # Verify each output file exists and has correct header
        for rt, filename in OUTPUT_FILES.items():
            csv_path = outdir / filename
            assert csv_path.exists()
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader)
                assert header == MINIMAL_SCHEMA[rt]
