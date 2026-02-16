#!/usr/bin/env python3
"""Quick local route + SQL performance audit for followthemoneyil.com."""

from __future__ import annotations

import argparse
import sqlite3
import time
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.app import create_app


DEFAULT_ENDPOINTS = [
    "/",
    "/search?q=illinois",
    "/search?q=michael+madigan",
    "/person-intelligence?q=michael+madigan",
    "/analytics/",
    "/527/dark-money",
]


def _run_endpoint_benchmark(db_path: str, runs: int) -> None:
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": "",
            "DATABASE_PATH": db_path,
            "DATABASE_TARGET": db_path,
            "DASHBOARD_PREWARM_ENABLED": False,
            "ROUTE_PERF_CACHE_ENABLED": True,
            "API_REQUIRE_KEY": False,
        }
    )
    client = app.test_client()

    print("ENDPOINT_TIMINGS")
    print("endpoint,status,cold_ms,warm_median_ms")
    for endpoint in DEFAULT_ENDPOINTS:
        samples: list[float] = []
        status = 0
        for _ in range(max(1, runs)):
            started = time.perf_counter()
            response = client.get(endpoint)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            status = response.status_code
            samples.append(elapsed_ms)
        cold_ms = samples[0]
        warm_samples = samples[1:] if len(samples) > 1 else [samples[0]]
        print(f"{endpoint},{status},{cold_ms:.2f},{median(warm_samples):.2f}")


def _print_table_counts(conn: sqlite3.Connection) -> None:
    tables = [
        "bulk_receipts_clean",
        "bulk_d2_receipts_recon",
        "analytics_donor_summary",
        "irs527_contributions",
        "irs527_expenditures",
        "irs527_expenditure_recipient_matches",
    ]
    print("\nTABLE_COUNTS")
    print("table_name,row_count")
    for table in tables:
        try:
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table},{row_count}")
        except Exception as exc:
            print(f"{table},ERR:{exc}")


def _print_sql_timings(conn: sqlite3.Connection) -> None:
    benchmarks = [
        (
            "donor_keys_source_prefix",
            """
            SELECT source, donor_key, donor_name, total_amount
            FROM analytics_donor_summary
            WHERE source = 'bulk_receipts'
              AND (donor_key LIKE ? OR donor_name LIKE ?)
            ORDER BY total_amount DESC, donor_name ASC
            LIMIT ?
            """,
            ("illinois%", "illinois%", 30),
        ),
        (
            "filed_docs_numeric_eq",
            """
            SELECT r.filed_doc_id, r.committee_id_sbe, COUNT(*) AS receipt_row_count
            FROM bulk_receipts_clean r
            WHERE r.filed_doc_id = ?
            GROUP BY r.filed_doc_id, r.committee_id_sbe
            ORDER BY receipt_row_count DESC
            LIMIT ?
            """,
            (123456, 30),
        ),
        (
            "irs527_top_contributors",
            """
            SELECT contributor_name, SUM(amount) AS total_amount, COUNT(*) AS cnt
            FROM irs527_contributions
            WHERE contributor_name IS NOT NULL
              AND contributor_name != ''
              AND amount > 0
            GROUP BY contributor_name
            ORDER BY total_amount DESC
            LIMIT 10
            """,
            (),
        ),
        (
            "irs527_period_sum",
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM irs527_contributions
            WHERE amount > 0
              AND ((date >= ? AND date <= ?) OR (date >= ? AND date <= ?))
            """,
            ("2025-01-01", "2026-12-31", "20250101", "20261231"),
        ),
    ]

    print("\nSQL_TIMINGS")
    print("name,elapsed_ms,row_count")
    for name, sql, params in benchmarks:
        started = time.perf_counter()
        rows = conn.execute(sql, params).fetchall()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        print(f"{name},{elapsed_ms:.2f},{len(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local route and SQL performance audit")
    parser.add_argument("--db", required=True, help="SQLite database path")
    parser.add_argument("--runs", type=int, default=3, help="Runs per endpoint (first is cold)")
    args = parser.parse_args()

    _run_endpoint_benchmark(args.db, args.runs)

    conn = sqlite3.connect(args.db)
    try:
        _print_table_counts(conn)
        _print_sql_timings(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
