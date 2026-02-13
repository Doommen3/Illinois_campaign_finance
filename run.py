#!/usr/bin/env python3
"""Entry point for Illinois Campaign Finance Tracker.

Usage:
    python run.py init-db                          # Initialize database
    python run.py create-user                      # Create/update manual-entry user
    python run.py import-bulk-download             # Load normalized bulk download tables + joins
    python run.py refresh-analytics                # Rebuild analytics materialized/cache tables
    python run.py rebuild-local-donor-entities     # Build confidence-scored local donor entities
    python run.py sync-fec-il-federal              # Sync Illinois federal candidates + FEC Schedule A data
    python run.py rebuild-fec-donor-identities     # Rebuild donor entity IDs for federal donor drill-down
    python run.py clean-data --apply                # Clean garbage rows and normalize donor metadata
    python run.py data-quality                     # Show data quality summary
    python run.py requeue-details --apply          # Requeue reports for detail scrape rebuild
    python run.py scrape-main --start-page 1 --end-page 40  # Scrape main list
    python run.py scrape-main --resume             # Resume interrupted scrape
    python run.py seed-committee-urls --batch-size 200       # Resolve CommitteeDetail URLs from SBE committee IDs
    python run.py scrape-committee-reports --batch-size 20  # Scrape committee pages
    python run.py scrape-committee-reports --committee-id-sbe 32451 --committee-id-sbe 40973
    python run.py scrape-d2-details --batch-size 20         # Scrape D-2 detail pages
    python run.py scrape-d2-details --committee-id-sbe 32451 --with-itemized
    python run.py scrape-d2-itemized --batch-size 50        # Scrape pending D-2 itemized links
    python run.py scrape-d2-all-pending --detail-batch-size 500 --itemized-batch-size 1000
    python run.py scrape-details --batch-size 20   # Scrape detail pages
    python run.py scrape-status                    # Check scrape status
    python run.py runserver --port 5000            # Run web server
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli.commands import cli

if __name__ == '__main__':
    cli()
