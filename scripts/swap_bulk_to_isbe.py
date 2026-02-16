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

# SQL for the ISBE-derived candidate committee finance agg view (PostgreSQL)
CANDIDATE_FINANCE_AGG_VIEW_SQL = """
CREATE VIEW bulk_candidate_committee_finance_agg AS
WITH filing_periods AS (
    SELECT
        committee_id AS committee_id_sbe,
        filed_doc_id,
        EXTRACT(YEAR FROM MAX(received_date))::INTEGER AS period_year,
        MIN(received_date) AS period_start_date,
        MAX(received_date) AS period_end_date
    FROM isbe_receipts
    WHERE received_date IS NOT NULL
    GROUP BY committee_id, filed_doc_id
)
SELECT
    cc.candidate_id,
    TRIM(COALESCE(ca.first_name, '') || ' ' || COALESCE(ca.last_name, '')) AS candidate_full_name,
    ca.office AS office_sought,
    ca.district_type,
    ca.district,
    ca.party AS candidate_party_affiliation,
    cc.committee_id AS committee_id_sbe,
    c.name AS committee_name,
    c.type AS committee_type,
    c.party AS committee_party_affiliation,
    fp.period_year,
    CASE
        WHEN fp.period_year IS NULL THEN NULL
        WHEN fp.period_year % 2 = 0 THEN fp.period_year
        ELSE fp.period_year + 1
    END AS election_cycle,
    COUNT(DISTINCT CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN d2.filed_doc_id END) AS filing_count,
    COALESCE(
        SUM(CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN COALESCE(d2.total_receipts, 0) ELSE 0 END),
        0
    )::REAL AS sum_total_receipts,
    COALESCE(
        SUM(CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN COALESCE(d2.total_expenditures, 0) ELSE 0 END),
        0
    )::REAL AS sum_total_expenditures,
    COALESCE(
        MAX(CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN d2.end_funds_available END),
        0
    )::REAL AS max_ending_funds_available,
    COALESCE(SUM(CASE WHEN d2.archived = TRUE THEN 1 ELSE 0 END), 0)::INTEGER AS archived_filing_count,
    MIN(fp.period_start_date)::TEXT AS period_start_date,
    MAX(fp.period_end_date)::TEXT AS period_end_date
FROM isbe_candidate_committees cc
JOIN isbe_candidates ca ON ca.id = cc.candidate_id
JOIN isbe_committees c ON c.id = cc.committee_id
LEFT JOIN isbe_d2_reports d2
    ON d2.committee_id = cc.committee_id
LEFT JOIN filing_periods fp
    ON fp.committee_id_sbe = d2.committee_id
    AND fp.filed_doc_id = d2.filed_doc_id
GROUP BY
    cc.candidate_id, ca.first_name, ca.last_name,
    ca.office, ca.district_type, ca.district, ca.party,
    cc.committee_id, c.name, c.type, c.party,
    fp.period_year,
    CASE
        WHEN fp.period_year IS NULL THEN NULL
        WHEN fp.period_year % 2 = 0 THEN fp.period_year
        ELSE fp.period_year + 1
    END
"""


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

    # Step 4: Swap bulk_candidate_committee_finance_agg to ISBE-derived VIEW
    print("\nSwapping bulk_candidate_committee_finance_agg to ISBE-derived VIEW...")
    cur.execute("""
        SELECT table_type FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'bulk_candidate_committee_finance_agg'
    """)
    row = cur.fetchone()
    if row and row[0] == "BASE TABLE":
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'bulk_candidate_committee_finance_agg_legacy'
        """)
        if cur.fetchone():
            if args.dry_run:
                print("  [DRY RUN] Would drop table and create view")
            else:
                cur.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg CASCADE")
                cur.execute(CANDIDATE_FINANCE_AGG_VIEW_SQL)
                print("  Dropped old table (legacy exists), created ISBE view ✓")
        else:
            if args.dry_run:
                print("  [DRY RUN] Would rename to _legacy and create view")
            else:
                cur.execute("ALTER TABLE bulk_candidate_committee_finance_agg RENAME TO bulk_candidate_committee_finance_agg_legacy")
                cur.execute(CANDIDATE_FINANCE_AGG_VIEW_SQL)
                print("  Renamed to _legacy, created ISBE view ✓")
    elif row and row[0] == "VIEW":
        if args.dry_run:
            print("  [DRY RUN] Would recreate view")
        else:
            cur.execute("DROP VIEW IF EXISTS bulk_candidate_committee_finance_agg CASCADE")
            cur.execute(CANDIDATE_FINANCE_AGG_VIEW_SQL)
            print("  Recreated ISBE view ✓")
    else:
        if args.dry_run:
            print("  [DRY RUN] Would create view")
        else:
            cur.execute(CANDIDATE_FINANCE_AGG_VIEW_SQL)
            print("  Created ISBE view ✓")

    # Step 5: Verify
    print("\nVerification:")
    all_tables = [name for name, _ in SWAP_PAIRS] + ["bulk_candidate_committee_finance_agg"]
    for tbl_name in all_tables:
        cur.execute("""
            SELECT table_type FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        """, (tbl_name,))
        row = cur.fetchone()
        status = row[0] if row else "MISSING"
        cur.execute(f"SELECT COUNT(*) FROM {tbl_name}")
        count = cur.fetchone()[0]
        print(f"  {tbl_name}: {status} ({count:,} rows)")

    print("\nDone! ISBE sunshine data is now served through bulk_*_clean views.")
    conn.close()


if __name__ == "__main__":
    main()
