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


_SQLITE_TABLE_EXISTS_RE = re.compile(
    r"^\s*SELECT\s+1\s+FROM\s+sqlite_master\s+WHERE\s+type\s*(?:=\s*'table'|IN\s*\(\s*'table'\s*,\s*'view'\s*\))\s+AND\s+name\s*=\s*(?:\?|'(?P<inline_name>[^']+)')\s*$",
    re.IGNORECASE,
)
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+(?:temp\.)?table_info\((?P<table>[^)]+)\)\s*;?\s*$",
    re.IGNORECASE,
)
_PRINTF_DATE_RE = re.compile(
    r"PRINTF\(\s*'%04d-%02d-%02d'\s*,\s*(?P<year>[^,]+?)\s*,\s*(?P<month>[^,]+?)\s*,\s*(?P<day>[^)]+?)\s*\)",
    re.IGNORECASE | re.DOTALL,
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


def _translate_sqlite_functions(sql: str) -> str:
    """Translate SQLite-only function usage to PostgreSQL equivalents."""
    translated = re.sub(r"\bINSTR\s*\(", "STRPOS(", sql, flags=re.IGNORECASE)

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
            self._fake_rows = self._conn._pragma_table_info(table_name)
            self._fake_index = 0
            return self

        self._fake_rows = None
        translated = _translate_sqlite_functions(_qmark_to_percent_s(sql))
        try:
            self._cursor.execute(translated, bound_params)
        except Exception:
            self._conn.rollback()
            raise
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> "PostgresCompatCursor":
        self._fake_rows = None
        translated = _translate_sqlite_functions(_qmark_to_percent_s(sql))
        try:
            self._cursor.executemany(translated, seq_of_params)
        except Exception:
            self._conn.rollback()
            raise
        return self

    def fetchone(self):
        if self._fake_rows is not None:
            if self._fake_index >= len(self._fake_rows):
                return None
            row = self._fake_rows[self._fake_index]
            self._fake_index += 1
            return row
        return self._cursor.fetchone()

    def fetchall(self):
        if self._fake_rows is not None:
            if self._fake_index >= len(self._fake_rows):
                return []
            rows = self._fake_rows[self._fake_index :]
            self._fake_index = len(self._fake_rows)
            return rows
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()

    def __enter__(self) -> "PostgresCompatCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class PostgresCompatConnection:
    """Connection wrapper that emulates the sqlite3 API used by this project."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pg_conn = psycopg.connect(dsn, row_factory=dict_row)

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
        statements = [part.strip() for part in script.split(";") if part.strip()]
        with self.cursor() as cur:
            for statement in statements:
                cur.execute(statement)

    def commit(self) -> None:
        self._pg_conn.commit()

    def rollback(self) -> None:
        self._pg_conn.rollback()

    def close(self) -> None:
        self._pg_conn.close()

    def set_progress_handler(self, handler, n: int) -> None:  # noqa: ARG002
        # SQLite-only optimization hook. Intentionally a no-op on PostgreSQL.
        return None

    def _sqlite_master_table_exists(self, table_name: str) -> list[dict[str, Any]]:
        try:
            row = self._pg_conn.execute(
                """
                SELECT 1 AS one
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
                """,
                (table_name,),
            ).fetchone()
        except Exception:
            self._pg_conn.rollback()
            row = self._pg_conn.execute(
                """
                SELECT 1 AS one
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
                """,
                (table_name,),
            ).fetchone()
        return [{"one": 1}] if row else []

    def _pragma_table_info(self, table_name: str) -> list[dict[str, Any]]:
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
                    WHERE tc.table_schema = 'public'
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
                WHERE c.table_schema = 'public'
                  AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (table_name, table_name),
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
                    WHERE tc.table_schema = 'public'
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
                WHERE c.table_schema = 'public'
                  AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (table_name, table_name),
            ).fetchall()
        return [dict(row) for row in rows]
