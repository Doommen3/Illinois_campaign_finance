#!/usr/bin/env python3
"""Swap bulk_*_clean tables to use ISBE sunshine data via views.

This script:
1. Recreates compat views with correct column names
2. Renames old bulk_*_clean tables to *_legacy
3. Creates views with the old names pointing to isbe_* data
4. Recreates the state race builder inputs: bulk_candidate_committee_finance_agg
   (a MATERIALIZED VIEW, rebuilt with its indexes and analyzed on every run)
   and the isbe_race_candidacies candidacy feed
5. Flushes the Redis route caches (a no-op without REDIS_URL): every cached payload was
   built from the objects this run just replaced

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

# SQL for the ISBE-derived candidate committee finance aggregate (PostgreSQL). A MATERIALIZED view
# (load-time plan L6): as a plain view every reader -- search, /candidate-finance/, the race builder --
# re-aggregated all receipts on each access. main() drops and rebuilds it on every run, so it is as
# fresh as the last import.
CANDIDATE_FINANCE_AGG_VIEW_SQL = """
CREATE MATERIALIZED VIEW bulk_candidate_committee_finance_agg AS
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

# Built on the fresh materialized view by every run (they go with it when it is dropped).
CANDIDATE_FINANCE_AGG_INDEX_SQL = [
    "CREATE INDEX idx_bulk_candidate_committee_finance_agg_candidate ON bulk_candidate_committee_finance_agg (candidate_id);",
    "CREATE INDEX idx_bulk_candidate_committee_finance_agg_committee ON bulk_candidate_committee_finance_agg (committee_id_sbe);",
    "CREATE INDEX idx_bulk_candidate_committee_finance_agg_cycle ON bulk_candidate_committee_finance_agg (election_cycle);",
    # /search matches candidate names by token (database/name_normalize.py NAME_TSVECTOR_SQL spelled out).
    "CREATE INDEX idx_bulk_cand_cmte_finance_agg_name_tsv ON bulk_candidate_committee_finance_agg USING gin (to_tsvector('simple', regexp_replace(upper(coalesce(candidate_full_name, '')), '[^A-Z0-9 ]', ' ', 'g')));",
]

# information_schema.tables does not list materialized views, so relation kinds come from pg_class:
# 'r' table, 'v' view, 'm' materialized view; no row when the relation is absent.
RELKIND_SQL = """
    SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = %s
"""
RELKIND_LABELS = {"r": "BASE TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW"}


def _relkind(cur, name):
    cur.execute(RELKIND_SQL, (name,))
    row = cur.fetchone()
    return row[0] if row else None


# The candidacy feed for the state race builder: one row per candidacy x linked
# committee (committee columns NULL when the candidate has no committee).  The
# cycle folds odd election years forward (2025 -> 2026).  Office and district come
# from the candidate row; ISBE publishes none per candidacy.
RACE_CANDIDACIES_VIEW_SQL = """
CREATE VIEW isbe_race_candidacies AS
SELECT
    cy.id AS candidacy_id,
    cy.candidate_id,
    cy.election_year,
    cy.election_year + cy.election_year % 2 AS election_cycle,
    cy.election_type,
    cy.race_type::text AS race_type,
    cy.outcome::text AS outcome,
    TRIM(COALESCE(ca.first_name, '') || ' ' || COALESCE(ca.last_name, '')) AS candidate_full_name,
    ca.office AS office_sought,
    ca.district_type,
    ca.district,
    ca.party AS candidate_party_affiliation,
    cc.committee_id AS committee_id_sbe,
    c.name AS committee_name
FROM isbe_candidacies cy
JOIN isbe_candidates ca ON ca.id = cy.candidate_id
LEFT JOIN isbe_candidate_committees cc ON cc.candidate_id = cy.candidate_id
LEFT JOIN isbe_committees c ON c.id = cc.committee_id
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

    # Step 4: Rebuild bulk_candidate_committee_finance_agg as a MATERIALIZED VIEW. DROP ... IF EXISTS
    # still raises on the wrong kind of relation, so the removal follows what is there now.
    kind = _relkind(cur, "bulk_candidate_committee_finance_agg")
    print(f"\nRebuilding bulk_candidate_committee_finance_agg as a MATERIALIZED VIEW (now: {RELKIND_LABELS.get(kind, kind or 'absent')})...")
    if kind == "r":
        if _relkind(cur, "bulk_candidate_committee_finance_agg_legacy"):
            removal = "DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg CASCADE"
        else:
            removal = "ALTER TABLE bulk_candidate_committee_finance_agg RENAME TO bulk_candidate_committee_finance_agg_legacy"
    elif kind == "v":
        removal = "DROP VIEW IF EXISTS bulk_candidate_committee_finance_agg CASCADE"
    elif kind == "m":
        removal = "DROP MATERIALIZED VIEW IF EXISTS bulk_candidate_committee_finance_agg CASCADE"
    else:
        removal = None
    statements = [removal] if removal else []
    statements += [CANDIDATE_FINANCE_AGG_VIEW_SQL, *CANDIDATE_FINANCE_AGG_INDEX_SQL, "ANALYZE bulk_candidate_committee_finance_agg"]
    for statement in statements:
        first_line = statement.strip().splitlines()[0]
        if args.dry_run:
            print(f"  [DRY RUN] Would execute: {first_line}")
        else:
            cur.execute(statement)
            print(f"  {first_line} ✓")

    # Step 5: Candidacy feed for the state race builder
    print("\nRecreating isbe_race_candidacies VIEW...")
    if args.dry_run:
        print("  [DRY RUN] Would recreate view")
    else:
        cur.execute("DROP VIEW IF EXISTS isbe_race_candidacies CASCADE")
        cur.execute(RACE_CANDIDACIES_VIEW_SQL)
        print("  Recreated isbe_race_candidacies view ✓")

    # Step 6: Verify
    print("\nVerification:")
    all_tables = [name for name, _ in SWAP_PAIRS] + ["bulk_candidate_committee_finance_agg", "isbe_race_candidacies"]
    for tbl_name in all_tables:
        kind = _relkind(cur, tbl_name)
        status = RELKIND_LABELS.get(kind, kind) if kind else "MISSING"
        cur.execute(f"SELECT COUNT(*) FROM {tbl_name}")
        count = cur.fetchone()[0]
        print(f"  {tbl_name}: {status} ({count:,} rows)")

    print("\nDone! ISBE sunshine data is now served through bulk_*_clean views.")
    conn.close()

    # Step 7: flush the route caches. Run as a script, sys.path[0] is scripts/, not the repo
    # root where webapp lives, so put the root on the path before importing it.
    print("\nFlushing the route caches...")
    if args.dry_run:
        print("  [DRY RUN] Would flush the route caches")
    else:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from webapp.cache_backend import flush_all_route_caches

        print(f"  Deleted {flush_all_route_caches()} cached route keys (Redis)")


if __name__ == "__main__":
    main()
