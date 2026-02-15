#!/usr/bin/env python3
"""Compare per-table row counts between SQLite and PostgreSQL."""

from __future__ import annotations

import argparse
import sqlite3

import psycopg
from psycopg.rows import dict_row


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def sqlite_count(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(table_name)}").fetchone()
    return int(row[0])


def pg_count(conn: psycopg.Connection, table_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM {quote_ident(table_name)}")
        row = cur.fetchone()
    return int(row["c"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify SQLite vs PostgreSQL table row counts")
    parser.add_argument("--sqlite-path", required=True, help="Path to SQLite database file")
    parser.add_argument("--pg-dsn", required=True, help="PostgreSQL DSN")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    pg_conn = psycopg.connect(args.pg_dsn, row_factory=dict_row)

    mismatches = 0
    try:
        tables = sqlite_tables(sqlite_conn)
        print(f"tables={len(tables)}")

        for table_name in tables:
            left = sqlite_count(sqlite_conn, table_name)
            right = pg_count(pg_conn, table_name)
            status = "OK" if left == right else "MISMATCH"
            print(f"{status} table={table_name} sqlite={left} postgres={right}")
            if left != right:
                mismatches += 1

        print(f"mismatches={mismatches}")
        return 1 if mismatches else 0
    finally:
        pg_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
