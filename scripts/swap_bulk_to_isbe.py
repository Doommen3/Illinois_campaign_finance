#!/usr/bin/env python3
"""Swap bulk_*_clean tables to use ISBE sunshine data via views.

This script:
1. Recreates compat views with correct column names
2. Renames old bulk_*_clean tables to *_legacy
3. Creates views with the old names pointing to isbe_* data

Safe to run multiple times (idempotent).
Requires isbe_* tables to be populated first (run isbe_sunshine_etl.py).

Usage:
  python scripts/swap_bulk_to_isbe.py [--dry-run]
"""
import argparse
import os
import sys

import psycopg

DB_URL = os.environ.get("DATABASE_URL", "postgresql://devin@localhost/ilcf")

# Tables to swap: (old_table_name, compat_view_name)
SWAP_PAIRS = [
    ("bulk_receipts_clean", "isbe_bulk_receipts_clean_compat"),
    ("bulk_expenditures_clean", "isbe_bulk_expenditures_clean_compat"),
    ("bulk_committees_clean", "isbe_bulk_committees_clean_compat"),
    ("bulk_candidates_clean", "isbe_bulk_candidates_clean_compat"),
    ("bulk_d2_totals_clean", "isbe_bulk_d2_totals_clean_compat"),
    ("bulk_cmte_candidate_links_clean", "isbe_bulk_cmte_candidate_links_clean_compat"),
    ("bulk_committee_candidate_links", "isbe_bulk_committee_candidate_links_compat"),
]


def main():
    parser = argparse.ArgumentParser(description="Swap bulk tables to ISBE sunshine views")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    parser.add_argument("--db-url", default=DB_URL, help="PostgreSQL connection URL")
    args = parser.parse_args()

    conn = psycopg.connect(args.db_url, autocommit=True)
    cur = conn.cursor()

    # Step 1: Verify isbe_* tables have data
    print("Verifying ISBE tables are populated...")
    for check_table in ["isbe_receipts", "isbe_expenditures", "isbe_committees", "isbe_candidates", "isbe_d2_reports", "isbe_candidate_committees"]:
        cur.execute(f"SELECT COUNT(*) FROM {check_table}")
        count = cur.fetchone()[0]
        if count == 0:
            print(f"  ERROR: {check_table} is empty. Run isbe_sunshine_etl.py first.")
            sys.exit(1)
        print(f"  {check_table}: {count:,} rows ✓")

    # Step 2: Recreate compat views from the ETL script (with fixed column names)
    print("\nRecreating compat views with correct column names...")
    # Import and run the compat view DDL
    sys.path.insert(0, os.path.dirname(__file__))
    from isbe_sunshine_etl import COMPAT_VIEWS_DDL
    if args.dry_run:
        print("  [DRY RUN] Would execute COMPAT_VIEWS_DDL")
    else:
        cur.execute(COMPAT_VIEWS_DDL)
        print("  Compat views created ✓")

    # Step 3: For each pair, rename old table to _legacy, create view with old name
    print("\nSwapping tables...")
    for old_name, compat_view in SWAP_PAIRS:
        legacy_name = f"{old_name}_legacy"

        # Check current state
        cur.execute("""
            SELECT table_type FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        """, (old_name,))
        row = cur.fetchone()

        if row is None:
            # Table doesn't exist at all — just create the view
            sql = f'CREATE VIEW {old_name} AS SELECT * FROM {compat_view};'
            print(f"  {old_name}: not found, creating view → {compat_view}")
            if not args.dry_run:
                cur.execute(sql)
        elif row[0] == "VIEW":
            # Already a view — drop and recreate
            sql = f'DROP VIEW IF EXISTS {old_name} CASCADE; CREATE VIEW {old_name} AS SELECT * FROM {compat_view};'
            print(f"  {old_name}: already a view, recreating → {compat_view}")
            if not args.dry_run:
                cur.execute(sql)
        elif row[0] == "BASE TABLE":
            # Check if legacy backup already exists
            cur.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            """, (legacy_name,))
            if cur.fetchone():
                # Legacy already exists — drop the old table, create view
                sql = f'DROP TABLE IF EXISTS {old_name} CASCADE; CREATE VIEW {old_name} AS SELECT * FROM {compat_view};'
                print(f"  {old_name}: {legacy_name} exists, replacing table with view → {compat_view}")
            else:
                sql = f'ALTER TABLE {old_name} RENAME TO {legacy_name}; CREATE VIEW {old_name} AS SELECT * FROM {compat_view};'
                print(f"  {old_name}: renamed to {legacy_name}, created view → {compat_view}")
            if not args.dry_run:
                cur.execute(sql)

    # Step 4: Verify
    print("\nVerification:")
    for old_name, _ in SWAP_PAIRS:
        cur.execute("""
            SELECT table_type FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        """, (old_name,))
        row = cur.fetchone()
        status = row[0] if row else "MISSING"
        cur.execute(f"SELECT COUNT(*) FROM {old_name}")
        count = cur.fetchone()[0]
        print(f"  {old_name}: {status} ({count:,} rows)")

    print("\nDone! ISBE sunshine data is now served through bulk_*_clean views.")
    conn.close()


if __name__ == "__main__":
    main()
