#!/usr/bin/env python3
"""Newsroom story leads pipeline: run queries, export CSVs, generate markdown memo.

Usage:
    python scripts/newsroom_story_leads.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from newsroom_queries import run_all_queries, export_csvs, export_json, DB_PATH, CSV_DIR
from newsroom_renderer import build_story_leads, build_verification_queue, render_to_file


def main():
    print('=' * 60)
    print('Illinois 2026 Cycle — Newsroom Story Leads Pipeline')
    print('=' * 60)

    # Step 1: Run queries
    print('\n[1/3] Running SQL queries against campaign_finance.db...')
    results = run_all_queries()
    print(f'  → {len(results)} query result sets loaded.')

    # Step 2: Export CSVs + JSON
    print('\n[2/3] Exporting CSVs and JSON...')
    csv_paths = export_csvs(results)
    json_path = export_json(results)
    print(f'  → {len(csv_paths)} CSVs written to {CSV_DIR}/')
    print(f'  → JSON: {json_path}')

    # Step 3: Generate markdown
    print('\n[3/3] Generating story leads markdown...')
    leads = build_story_leads(results)
    verification = build_verification_queue(results)
    md_path = render_to_file(leads, verification, results)
    print(f'  → {len(leads)} story leads + {len(verification)} verification items')
    print(f'  → Markdown: {md_path}')

    # Summary
    print('\n' + '=' * 60)
    print('DONE. Outputs:')
    print(f'  Markdown memo:  {md_path}')
    print(f'  CSVs:           {CSV_DIR}/')
    print(f'  JSON data:      {json_path}')
    print('=' * 60)


if __name__ == '__main__':
    main()
