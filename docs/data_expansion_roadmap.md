# Data Expansion Roadmap

## Objective
Build a repeatable pipeline that adds more campaign-finance data while keeping data quality high and enabling future network analysis.

## Phase 1 (Implemented now)
- Capture row-level transaction dates from contribution amount cells.
- Add raw extraction staging (`raw_extractions`) so parsing can be audited/reprocessed without immediate re-scrape.
- Add operational data-quality metrics command.
- Add cleanup/normalization workflows for garbage committee rows and donor occupation/employer parsing.

### Commands
- `python run.py clean-data --only committees`
- `python run.py clean-data --only donors`
- `python run.py clean-data --apply`
- `python run.py data-quality`
- `python run.py requeue-details --missing-transaction-date --apply`

## Phase 2 (Next)
- Expand committee-level metadata from Committee Detail pages:
  - committee address
  - committee status
  - treasurer/contact fields
  - office/district context
- Promote these fields into dedicated columns/tables (not free text).
- Add diff-based updates to detect amended filings and changed values.

## Phase 3 (Graph-ready model)
- Add entity resolution tables:
  - `entities_people`
  - `entities_organizations`
  - `entity_aliases`
- Add typed edge tables:
  - donor/person -> committee (`contributed_to`)
  - committee -> vendor (`spent_with`)
  - committee -> candidate/ballot (`supports_or_opposes`)
- Persist edge timestamp, amount, and source_id for each edge.

## Phase 4 (Coverage and refresh)
- Add scheduled incremental jobs:
  - daily newest filings
  - weekly backfill windows
  - monthly reconciliation scan
- Add run-history table with success/failure stats and row counts.

## Phase 5 (Analytics and delivery)
- Build export command for graph tooling (`nodes.csv`, `edges.csv`, optional `graphml`).
- Add API endpoints for filtered network slices (date range, min amount, report type).
- Add dashboards for quality trends and scrape freshness.

## Acceptance Checks
- No garbage committee names in DB.
- Re-running scrapers does not duplicate reports.
- Occupation/employer extracted where present.
- Raw extraction counts grow with scrape volume.
- Data-quality command shows declining null/error rates over time.
