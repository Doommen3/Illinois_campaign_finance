"""PostgreSQL compatibility wrappers exposing a SQLite-like API surface.

This module enables incremental runtime migration by letting existing code call
`conn.execute(..., params)` with qmark placeholders and SQLite metadata probes
(`sqlite_master`, `PRAGMA table_info`) while using PostgreSQL underneath.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row


class _DualAccessRow(dict):
    """Dict subclass that also supports integer index access like sqlite3.Row."""

    __slots__ = ("_keys_list",)

    def __init__(self, mapping):
        super().__init__(mapping)
        self._keys_list = list(mapping.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys_list[key])
        return super().__getitem__(key)

    def keys(self):
        return self._keys_list


_SQLITE_TABLE_EXISTS_RE = re.compile(
    r"^\s*SELECT\s+\S+\s+FROM\s+sqlite_master\s+WHERE\s+type\s*(?:=\s*'table'|IN\s*\(\s*'table'\s*,\s*'view'\s*\))\s+AND\s+name\s*=\s*(?:\?|'(?P<inline_name>[^']+)')\s*$",
    re.IGNORECASE,
)
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+(?P<temp_prefix>temp\.)?table_info\((?P<table>[^)]+)\)\s*;?\s*$",
    re.IGNORECASE,
)
_PRINTF_DATE_RE = re.compile(
    r"PRINTF\(\s*'%04d-%02d-%02d'\s*,\s*(?P<year>[^,]+?)\s*,\s*(?P<month>[^,]+?)\s*,\s*(?P<day>[^)]+?)\s*\)",
    re.IGNORECASE | re.DOTALL,
)

_AUTOINCREMENT_PK_RE = re.compile(
    r"\b(?P<column>[A-Za-z_][A-Za-z0-9_]*)\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
    re.IGNORECASE,
)
_STRFTIME_YEAR_RE = re.compile(
    r"\bSTRFTIME\s*\(\s*'%Y'\s*,\s*(?P<col>(?:[^()]+|\([^()]*\))+?)\s*\)",
    re.IGNORECASE,
)
_INSERT_OR_REPLACE_RE = re.compile(
    r"\bINSERT\s+OR\s+REPLACE\b",
    re.IGNORECASE,
)
_INSERT_OR_IGNORE_RE = re.compile(
    r"\bINSERT\s+OR\s+IGNORE\b",
    re.IGNORECASE,
)


def _strip_identifier(value: str) -> str:
    text = value.strip()
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1]
    return text


def _qmark_to_percent_s(sql: str) -> str:
    """Translate qmark placeholders to psycopg `%s` placeholders.

    Only replaces bare `?` outside single/double-quoted strings.
    """
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        char = sql[i]

        if char == "'" and not in_double:
            in_single = not in_single
            out.append(char)
            i += 1
            continue

        if char == '"' and not in_single:
            in_double = not in_double
            out.append(char)
            i += 1
            continue

        if char == "%":
            out.append("%%")
            i += 1
            continue

        if char == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(char)
        i += 1

    return "".join(out)


_ID_INTEGER_PK_RE = re.compile(
    r"\bid\s+INTEGER\s+PRIMARY\s+KEY\b(?!\s+AUTOINCREMENT)",
    re.IGNORECASE,
)


def _adapt_ddl_for_postgres(sql: str) -> str:
    """Translate SQLite DDL idioms to PostgreSQL equivalents.

    Applied to CREATE TABLE statements and other DDL issued at runtime
    (e.g. from test fixtures).
    """
    translated = _AUTOINCREMENT_PK_RE.sub(r"\g<column> BIGSERIAL PRIMARY KEY", sql)
    translated = re.sub(r"\bAUTOINCREMENT\b", "", translated, flags=re.IGNORECASE)
    # SQLite "id INTEGER PRIMARY KEY" is an alias for rowid (auto-assigned).
    # PostgreSQL needs BIGSERIAL for auto-increment.
    translated = _ID_INTEGER_PK_RE.sub("id BIGSERIAL PRIMARY KEY", translated)
    return translated


def _adapt_dml_for_postgres(sql: str) -> str:
    """Translate SQLite DML idioms (INSERT OR REPLACE/IGNORE) to PostgreSQL.

    - INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    - INSERT OR REPLACE → INSERT ... ON CONFLICT DO UPDATE on all listed columns.
      Uses a heuristic: attempts ON CONFLICT on all columns except common
      value-only columns (amount, score, count, etc.).  Falls back to just
      stripping `OR REPLACE` if no column list is found, which will raise a
      clear PostgreSQL error rather than silently misrouting.
    """

    if _INSERT_OR_IGNORE_RE.search(sql):
        return _INSERT_OR_IGNORE_RE.sub("INSERT", sql) + " ON CONFLICT DO NOTHING"

    if _INSERT_OR_REPLACE_RE.search(sql):
        base = _INSERT_OR_REPLACE_RE.sub("INSERT", sql)
        # Extract table name and column list
        meta = re.search(
            r"INSERT\s+INTO\s+(?P<table>\S+)\s*\((?P<cols>[^)]+)\)",
            base,
            re.IGNORECASE,
        )
        if meta:
            table = meta.group("table").strip()
            cols = [c.strip() for c in meta.group("cols").split(",")]
            # Look up known PKs for common tables
            pk_cols = _KNOWN_TABLE_PKS.get(table)
            if pk_cols is None:
                # Fallback: assume first column is the PK
                pk_cols = [cols[0]]
            update_cols = [c for c in cols if c not in pk_cols]
            pk_str = ", ".join(pk_cols)
            if update_cols:
                set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
                return f"{base} ON CONFLICT ({pk_str}) DO UPDATE SET {set_clause}"
            else:
                return f"{base} ON CONFLICT ({pk_str}) DO NOTHING"
        return base

    return sql


# Known primary key columns for tables commonly used with INSERT OR REPLACE.
# Must match schema.sql PRIMARY KEY definitions exactly.
_KNOWN_TABLE_PKS: dict[str, list[str]] = {
    "analytics_donor_summary": ["source", "donor_key"],
    "analytics_donor_committee_agg": ["source", "donor_key", "committee_id"],
    "analytics_committee_monthly_totals": ["source", "committee_name", "month_key"],
    "analytics_large_contributions": ["id"],
    "analytics_materialized_meta": ["source"],
    "analytics_snapshots": ["cache_key"],
    "irs527_organizations": ["ein", "form_id_seq"],
    "irs527_directors": ["rowid_local"],
    "irs527_expenditures": ["rowid_local"],
    "irs527_reports": ["form_id", "ein"],
    "irs527_contributions": ["rowid_local"],
    "irs527_contribution_rollup": ["ein"],
    "irs527_contributor_rollup": ["contributor_name"],
    "irs527_committee_matches": ["ein", "committee_id_sbe"],
    "irs527_director_donor_matches": ["match_id"],
    "irs527_director_candidate_matches": ["match_id"],
    "irs527_director_address_matches": ["match_id"],
    "irs527_org_address_matches": ["match_id"],
    "irs527_expenditure_recipient_matches": ["match_id"],
    "fec_il_candidate_seed": ["id"],
    "fec_candidate_match": ["seed_candidate_key"],
    "fec_candidate_committees": ["candidate_id", "committee_id", "cycle"],
    "fec_candidate_cycle_totals": ["candidate_id", "cycle"],
    "fec_schedule_a_contributions": ["sub_id"],
    "fec_schedule_b_disbursements": ["sub_id"],
    "fec_schedule_e_independent_expenditures": ["sub_id"],
    "fec_local_donor_matches": ["federal_donor_entity_key", "local_donor_key"],
    "bulk_candidates_clean": ["candidate_id"],
    "bulk_committees_clean": ["committee_id_sbe"],
    "bulk_receipts_clean": ["id"],
    "lobbying_clients": ["client_id"],
    "lobbying_entities": ["entity_id"],
    "lobbying_donor_matches": ["client_id", "donor_key"],
    "lobbying_expenditure_matches": ["match_id"],
    "lobbying_527_matches": ["client_id", "ein"],
    "openbook_vendor_seed": ["seed_id"],
    "openbook_vendor_match": ["match_id"],
}


_GLOB_RE = re.compile(
    r"(?P<col>\S+)\s+GLOB\s+'(?P<pattern>[^']+)'",
    re.IGNORECASE,
)


def _glob_to_like(match: re.Match[str]) -> str:
    """Convert SQLite GLOB pattern to PostgreSQL LIKE pattern."""
    col = match.group("col")
    pattern = match.group("pattern").replace("*", "%").replace("?", "_")
    return f"{col} LIKE '{pattern}'"


_PARAM_IS_NULL_RE = re.compile(r"\?\s+IS\s+NULL", re.IGNORECASE)


def _translate_sqlite_functions(sql: str) -> str:
    """Translate SQLite-only function usage to PostgreSQL equivalents."""
    translated = re.sub(r"\bINSTR\s*\(", "STRPOS(", sql, flags=re.IGNORECASE)
    translated = _GLOB_RE.sub(_glob_to_like, translated)
    # ? IS NULL → CAST(? AS TEXT) IS NULL  (PostgreSQL needs typed params)
    translated = _PARAM_IS_NULL_RE.sub("CAST(? AS TEXT) IS NULL", translated)
    # SQLite temp.table_name → PostgreSQL pg_temp.table_name
    translated = re.sub(
        r"\btemp\.(?=\w)", "pg_temp.", translated, flags=re.IGNORECASE
    )

    def _replace_printf_date(match: re.Match[str]) -> str:
        year = match.group("year").strip()
        month = match.group("month").strip()
        day = match.group("day").strip()
        return (
            f"(LPAD(CAST({year} AS TEXT), 4, '0') || '-' || "
            f"LPAD(CAST({month} AS TEXT), 2, '0') || '-' || "
            f"LPAD(CAST({day} AS TEXT), 2, '0'))"
        )

    translated = _PRINTF_DATE_RE.sub(_replace_printf_date, translated)
    # STRFTIME('%Y', col) → EXTRACT(YEAR FROM col::date)::int
    translated = _STRFTIME_YEAR_RE.sub(
        lambda m: f"EXTRACT(YEAR FROM ({m.group('col').strip()})::date)::int",
        translated,
    )
    return translated


class PostgresCompatCursor:
    def __init__(self, conn: "PostgresCompatConnection") -> None:
        self._conn = conn
        self._cursor = conn._pg_conn.cursor()
        self._fake_rows: list[dict[str, Any]] | None = None
        self._fake_index = 0
        self.lastrowid = None

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> "PostgresCompatCursor":
        bound_params = tuple(params or ())
        stripped = sql.strip()

        m = _SQLITE_TABLE_EXISTS_RE.match(stripped)
        if m:
            if bound_params:
                table_name = str(bound_params[0])
            else:
                table_name = m.group("inline_name") or ""
            self._fake_rows = self._conn._sqlite_master_table_exists(table_name)
            self._fake_index = 0
            return self

        pragma_match = _PRAGMA_TABLE_INFO_RE.match(stripped)
        if pragma_match:
            table_name = _strip_identifier(pragma_match.group("table"))
            is_temp = bool(pragma_match.group("temp_prefix"))
            self._fake_rows = self._conn._pragma_table_info(table_name, temp=is_temp)
            self._fake_index = 0
            return self

        self._fake_rows = None
        translated = sql
        # Apply DDL translation for CREATE TABLE statements
        upper = stripped.upper()
        if upper.startswith("CREATE TABLE") or upper.startswith("CREATE TEMP TABLE"):
            translated = _adapt_ddl_for_postgres(translated)
        # Apply DML translation for INSERT OR REPLACE / INSERT OR IGNORE
        if "INSERT OR " in upper:
            translated = _adapt_dml_for_postgres(translated)
        # Translate SQLite functions BEFORE qmark conversion (which doubles %)
        translated = _qmark_to_percent_s(_translate_sqlite_functions(translated))
        is_insert = upper.startswith("INSERT")
        try:
            self._cursor.execute(translated, bound_params)
            # Capture lastrowid via lastval() for INSERT into SERIAL columns
            if is_insert:
                try:
                    self._conn._pg_conn.execute("SAVEPOINT _lastrowid_sp")
                    row = self._conn._pg_conn.execute("SELECT lastval()").fetchone()
                    self._conn._pg_conn.execute("RELEASE SAVEPOINT _lastrowid_sp")
                    if row:
                        self.lastrowid = row[0] if isinstance(row, tuple) else list(row.values())[0]
                except Exception:
                    try:
                        self._conn._pg_conn.execute("ROLLBACK TO SAVEPOINT _lastrowid_sp")
                        self._conn._pg_conn.execute("RELEASE SAVEPOINT _lastrowid_sp")
                    except Exception:
                        pass
        except Exception:
            self._conn.rollback()
            raise
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> "PostgresCompatCursor":
        self._fake_rows = None
        translated = sql
        upper = sql.strip().upper()
        if upper.startswith("CREATE TABLE") or upper.startswith("CREATE TEMP TABLE"):
            translated = _adapt_ddl_for_postgres(translated)
        if "INSERT OR " in upper:
            translated = _adapt_dml_for_postgres(translated)
        translated = _qmark_to_percent_s(_translate_sqlite_functions(translated))
        try:
            self._cursor.executemany(translated, seq_of_params)
        except Exception:
            self._conn.rollback()
            raise
        return self

    @staticmethod
    def _wrap_row(row):
        if row is None:
            return None
        if isinstance(row, dict) and not isinstance(row, _DualAccessRow):
            return _DualAccessRow(row)
        return row

    def fetchone(self):
        if self._fake_rows is not None:
            if self._fake_index >= len(self._fake_rows):
                return None
            row = self._fake_rows[self._fake_index]
            self._fake_index += 1
            return self._wrap_row(row)
        return self._wrap_row(self._cursor.fetchone())

    def fetchall(self):
        if self._fake_rows is not None:
            if self._fake_index >= len(self._fake_rows):
                return []
            rows = self._fake_rows[self._fake_index :]
            self._fake_index = len(self._fake_rows)
            return [self._wrap_row(r) for r in rows]
        return [self._wrap_row(r) for r in self._cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def __iter__(self):
        return iter(self.fetchall())

    def close(self) -> None:
        self._cursor.close()

    def __enter__(self) -> "PostgresCompatCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class PostgresCompatConnection:
    """Connection wrapper that emulates the sqlite3 API used by this project."""

    def __init__(self, dsn: str, *, schema: str | None = None):
        self._dsn = dsn
        self._schema = schema or "public"
        self._pg_conn = psycopg.connect(dsn, row_factory=dict_row)
        if schema and schema != "public":
            self._pg_conn.execute(f"SET search_path TO {schema}, public")
            self._pg_conn.commit()

    def cursor(self) -> PostgresCompatCursor:
        return PostgresCompatCursor(self)

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> PostgresCompatCursor:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> PostgresCompatCursor:
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def executescript(self, script: str) -> None:
        """Execute a multi-statement SQL script.

        Sends the entire script as a single command to PostgreSQL, which
        handles multiple statements in one round-trip and avoids cursor
        state issues that arise from executing 200+ statements one by one.
        """
        # Commit any pending transaction first so DDL runs cleanly
        try:
            self._pg_conn.commit()
        except Exception:
            pass

        # Apply DDL adaptations to the entire script at once
        adapted = _adapt_ddl_for_postgres(script)
        adapted = _translate_sqlite_functions(adapted)

        # Strip trailing whitespace/semicolons that could produce empty statements
        adapted = adapted.rstrip().rstrip(";")

        # Execute the whole script as one multi-statement command via pipeline
        with self._pg_conn.cursor() as cur:
            cur.execute(adapted)
        self._pg_conn.commit()

    def commit(self) -> None:
        self._pg_conn.commit()

    def rollback(self) -> None:
        self._pg_conn.rollback()

    def close(self) -> None:
        self._pg_conn.close()

    @property
    def in_transaction(self) -> bool:
        return self._pg_conn.info.transaction_status != 0

    def set_progress_handler(self, handler, n: int) -> None:  # noqa: ARG002
        # SQLite-only optimization hook. Intentionally a no-op on PostgreSQL.
        return None

    def _sqlite_master_table_exists(self, table_name: str) -> list[dict[str, Any]]:
        schema = self._schema
        try:
            row = self._pg_conn.execute(
                """
                SELECT 1 AS one FROM (
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    UNION ALL
                    SELECT matviewname FROM pg_matviews
                    WHERE schemaname = %s AND matviewname = %s
                ) combined LIMIT 1
                """,
                (schema, table_name, schema, table_name),
            ).fetchone()
        except Exception:
            self._pg_conn.rollback()
            row = self._pg_conn.execute(
                """
                SELECT 1 AS one FROM (
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    UNION ALL
                    SELECT matviewname FROM pg_matviews
                    WHERE schemaname = %s AND matviewname = %s
                ) combined LIMIT 1
                """,
                (schema, table_name, schema, table_name),
            ).fetchone()
        return [{"one": 1}] if row else []

    def _pragma_table_info(self, table_name: str, *, temp: bool = False) -> list[dict[str, Any]]:
        if temp:
            return self._pragma_table_info_temp(table_name)
        schema = self._schema
        try:
            rows = self._pg_conn.execute(
                """
                WITH pk_columns AS (
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                     AND tc.table_name = kcu.table_name
                    WHERE tc.table_schema = %s
                      AND tc.table_name = %s
                      AND tc.constraint_type = 'PRIMARY KEY'
                )
                SELECT
                    c.ordinal_position - 1 AS cid,
                    c.column_name AS name,
                    c.data_type AS type,
                    CASE WHEN c.is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                    c.column_default AS dflt_value,
                    CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS pk
                FROM information_schema.columns c
                LEFT JOIN pk_columns pk ON pk.column_name = c.column_name
                WHERE c.table_schema = %s
                  AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (schema, table_name, schema, table_name),
            ).fetchall()
        except Exception:
            self._pg_conn.rollback()
            rows = self._pg_conn.execute(
                """
                WITH pk_columns AS (
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                     AND tc.table_name = kcu.table_name
                    WHERE tc.table_schema = %s
                      AND tc.table_name = %s
                      AND tc.constraint_type = 'PRIMARY KEY'
                )
                SELECT
                    c.ordinal_position - 1 AS cid,
                    c.column_name AS name,
                    c.data_type AS type,
                    CASE WHEN c.is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                    c.column_default AS dflt_value,
                    CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS pk
                FROM information_schema.columns c
                LEFT JOIN pk_columns pk ON pk.column_name = c.column_name
                WHERE c.table_schema = %s
                  AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (schema, table_name, schema, table_name),
            ).fetchall()
        return [dict(row) for row in rows]

    def _pragma_table_info_temp(self, table_name: str) -> list[dict[str, Any]]:
        """Introspect temp table columns via pg_catalog (temp tables live in pg_temp)."""
        try:
            rows = self._pg_conn.execute(
                """
                SELECT
                    a.attnum - 1 AS cid,
                    a.attname AS name,
                    pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
                    CASE WHEN a.attnotnull THEN 1 ELSE 0 END AS notnull,
                    pg_get_expr(d.adbin, d.adrelid) AS dflt_value,
                    0 AS pk
                FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_class c ON a.attrelid = c.oid
                LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
                WHERE c.relname = %s
                  AND c.relpersistence = 't'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                (table_name,),
            ).fetchall()
        except Exception:
            self._pg_conn.rollback()
            rows = []
        return [dict(row) for row in rows]
