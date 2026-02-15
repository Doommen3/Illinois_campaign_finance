#!/usr/bin/env python3
"""Migrate SQLite schema+data into PostgreSQL.

This is a pragmatic migration helper for this project:
- Reads SQLite table definitions and rows.
- Creates PostgreSQL tables with mapped types.
- Loads all rows in chunks.
- Attempts to recreate non-unique/unique indexes from sqlite_master SQL.

It is intentionally conservative and logs warnings for statements requiring
manual adaptation.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from typing import Iterable

import psycopg
from psycopg.rows import dict_row


SQLITE_INTERNAL_TABLE_PREFIXES = ("sqlite_",)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def map_sqlite_type_to_pg(sqlite_type: str | None) -> str:
    raw = (sqlite_type or "").strip().lower()
    if not raw:
        return "TEXT"

    if "int" in raw:
        return "BIGINT"
    if any(token in raw for token in ("real", "floa", "doub")):
        return "DOUBLE PRECISION"
    if "bool" in raw:
        return "BOOLEAN"
    if any(token in raw for token in ("blob", "bytea")):
        return "BYTEA"
    if any(token in raw for token in ("numeric", "decimal")):
        return "NUMERIC"
    if any(token in raw for token in ("date", "time")):
        return "TIMESTAMP"
    return "TEXT"


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
        """
    ).fetchall()
    tables: list[str] = []
    for row in rows:
        name = row[0]
        if any(name.startswith(prefix) for prefix in SQLITE_INTERNAL_TABLE_PREFIXES):
            continue
        tables.append(name)
    return tables


def sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> list[dict]:
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table_name)})").fetchall()
    columns: list[dict] = []
    for cid, name, col_type, notnull, default_value, pk in rows:
        columns.append(
            {
                "name": name,
                "type": col_type,
                "notnull": bool(notnull),
                "default": default_value,
                "pk": int(pk),
            }
        )
    return columns


def create_pg_table(pg_conn: psycopg.Connection, table_name: str, columns: list[dict]) -> None:
    pk_columns = [col["name"] for col in columns if col["pk"] > 0]

    column_sql_parts: list[str] = []
    for col in columns:
        col_sql = f"{quote_ident(col['name'])} {map_sqlite_type_to_pg(col['type'])}"
        if col["notnull"]:
            col_sql += " NOT NULL"
        if col["default"] is not None:
            col_sql += f" DEFAULT {col['default']}"
        column_sql_parts.append(col_sql)

    if pk_columns:
        pk_sql = ", ".join(quote_ident(col) for col in pk_columns)
        column_sql_parts.append(f"PRIMARY KEY ({pk_sql})")

    ddl = f"CREATE TABLE IF NOT EXISTS {quote_ident(table_name)} ({', '.join(column_sql_parts)})"
    with pg_conn.cursor() as cur:
        cur.execute(ddl)
    pg_conn.commit()


def truncate_pg_table(pg_conn: psycopg.Connection, table_name: str) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {quote_ident(table_name)}")
    pg_conn.commit()


def iter_sqlite_rows(
    sqlite_conn: sqlite3.Connection,
    table_name: str,
    column_names: list[str],
) -> Iterable[tuple]:
    sql = f"SELECT {', '.join(quote_ident(c) for c in column_names)} FROM {quote_ident(table_name)}"
    for row in sqlite_conn.execute(sql):
        yield tuple(row)


def insert_pg_rows(
    pg_conn: psycopg.Connection,
    table_name: str,
    column_names: list[str],
    rows: list[tuple],
) -> None:
    if not rows:
        return

    quoted_columns = ", ".join(quote_ident(c) for c in column_names)
    placeholders = ", ".join(["%s"] * len(column_names))
    sql = f"INSERT INTO {quote_ident(table_name)} ({quoted_columns}) VALUES ({placeholders})"

    with pg_conn.cursor() as cur:
        cur.executemany(sql, rows)
    pg_conn.commit()


def _to_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off", ""}:
        return False
    return bool(text)


def _coerce_rows_for_pg(columns: list[dict], rows: list[tuple]) -> list[tuple]:
    bool_indexes = [
        index for index, column in enumerate(columns) if map_sqlite_type_to_pg(column.get("type")) == "BOOLEAN"
    ]
    if not bool_indexes:
        return rows

    coerced: list[tuple] = []
    for row in rows:
        mutable = list(row)
        for index in bool_indexes:
            mutable[index] = _to_bool(mutable[index])
        coerced.append(tuple(mutable))
    return coerced


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg.Connection,
    table_name: str,
    chunk_size: int,
    truncate_first: bool,
) -> tuple[int, int]:
    columns = sqlite_table_columns(sqlite_conn, table_name)
    if not columns:
        return 0, 0

    create_pg_table(pg_conn, table_name, columns)
    if truncate_first:
        truncate_pg_table(pg_conn, table_name)

    column_names = [c["name"] for c in columns]
    total_rows = 0
    chunk: list[tuple] = []

    for row in iter_sqlite_rows(sqlite_conn, table_name, column_names):
        chunk.append(row)
        if len(chunk) >= chunk_size:
            insert_pg_rows(pg_conn, table_name, column_names, _coerce_rows_for_pg(columns, chunk))
            total_rows += len(chunk)
            chunk = []

    if chunk:
        insert_pg_rows(pg_conn, table_name, column_names, _coerce_rows_for_pg(columns, chunk))
        total_rows += len(chunk)

    return len(columns), total_rows


def sqlite_index_sql(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type='index'
            AND sql IS NOT NULL
        ORDER BY name
        """
    ).fetchall()
    return [row[0] for row in rows]


def adapt_sqlite_index_sql_to_pg(sql: str) -> str:
    # Strip SQLite-specific quoting style differences only where needed.
    adapted = sql.strip().rstrip(";")
    # Ensure IF NOT EXISTS is present for rerunnable runs.
    adapted = re.sub(r"^CREATE\s+INDEX\s+", "CREATE INDEX IF NOT EXISTS ", adapted, flags=re.IGNORECASE)
    adapted = re.sub(r"^CREATE\s+UNIQUE\s+INDEX\s+", "CREATE UNIQUE INDEX IF NOT EXISTS ", adapted, flags=re.IGNORECASE)
    return adapted


def migrate_indexes(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection) -> tuple[int, int]:
    created = 0
    skipped = 0
    for sql in sqlite_index_sql(sqlite_conn):
        stmt = adapt_sqlite_index_sql_to_pg(sql)
        try:
            with pg_conn.cursor() as cur:
                cur.execute(stmt)
            pg_conn.commit()
            created += 1
        except Exception:
            pg_conn.rollback()
            skipped += 1
    return created, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate SQLite DB to PostgreSQL")
    parser.add_argument("--sqlite-path", required=True, help="Path to SQLite database file")
    parser.add_argument("--pg-dsn", required=True, help="PostgreSQL DSN")
    parser.add_argument("--chunk-size", type=int, default=5000, help="Insert chunk size")
    parser.add_argument("--truncate-first", action="store_true", help="Truncate destination table before load")
    parser.add_argument("--skip-indexes", action="store_true", help="Skip index recreation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = psycopg.connect(args.pg_dsn, row_factory=dict_row)

    try:
        tables = sqlite_tables(sqlite_conn)
        print(f"tables_found={len(tables)}")

        total_rows = 0
        for table_name in tables:
            col_count, row_count = migrate_table(
                sqlite_conn,
                pg_conn,
                table_name,
                chunk_size=max(1, args.chunk_size),
                truncate_first=args.truncate_first,
            )
            total_rows += row_count
            print(f"table={table_name} columns={col_count} rows={row_count}")

        if not args.skip_indexes:
            created, skipped = migrate_indexes(sqlite_conn, pg_conn)
            print(f"indexes_created={created} indexes_skipped={skipped}")

        print(f"done total_rows={total_rows}")
        return 0
    finally:
        pg_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
