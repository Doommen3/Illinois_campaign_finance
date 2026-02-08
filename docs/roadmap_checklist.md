# Illinois Campaign Finance Tracker Checklist

## Completed in this change
- [x] Add sortable columns for major website tables (reports, committees, donors, donor detail, committee detail, report detail).
- [x] Add identifier-based dedupe safeguards for repeated scrapes.
- [x] Save committee detail links from the main report table.
- [x] Add committee-page scraper command to ingest A-1 and D-2 rows.
- [x] Add filed-date cutoff support (`6/1/2025`) when walking committee report pages.
- [x] Add D-2 storage tables for report metadata, itemized links, and itemized rows.
- [x] Add separate D-2 commands for detail scraping and itemized scraping.
- [x] Add tests for dedupe logic, sort query handling, and D-2 helper parsing.
- [x] Integrate bulk `receipts_*.txt` import with size-aware chunking and normalized columns.
- [x] Add receipts join tables:
  - [x] `bulk_committee_receipts`
  - [x] `bulk_d2_receipts_recon`
  - [x] `bulk_candidate_committee_receipts_agg`
- [x] Add tests validating receipts load + committee/candidate/D2 joins.

## How to run
- [ ] `python run.py init-db`
- [ ] `python run.py scrape-main --start-page 1 --end-page 40`
- [ ] `python run.py scrape-committee-reports --batch-size 20 --filed-cutoff 2025-06-01`
- [ ] `python run.py scrape-d2-details --batch-size 20`
- [ ] `python run.py scrape-d2-itemized --batch-size 50`
- [ ] `python run.py scrape-details --batch-size 20`
- [ ] `python run.py import-bulk-download --directory Bulk_download`
- [ ] `python run.py runserver --port 5000`

## Network analysis path (next)
- [ ] Build an edge table/view: donor -> committee with amount, count, date range.
- [ ] Add committee-to-committee transfer edges from D-2 itemized expenditures where entity matching is possible.
- [ ] Export graph files (`.csv` edges/nodes and `.graphml`).
- [ ] Compute centrality and community metrics (degree, weighted degree, betweenness, Louvain).
- [ ] Add a web view with filters (date range, min amount, report type) and graph snapshots.

## Website improvements to consider
- [ ] Add saved filter presets and shareable URLs for common analyses.
- [ ] Add scraper job history UI with retries and per-job logs.
- [ ] Add data freshness badges (last scrape timestamp by dataset).
- [ ] Add report/source backlinks on every row for traceability.
- [ ] Add duplicate-review UI to inspect/merge suspect entities.
- [ ] Add CSV export for every table with current filters/sorts.
