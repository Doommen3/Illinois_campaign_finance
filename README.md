# Illinois Campaign Finance Intelligence Platform

A full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from both state and federal sources. The system ingests millions of contribution records, performs network analysis, anomaly detection, and donor intelligence across election cycles.

## Data Sources

- **Illinois State Board of Elections (ISBE)** — Committee filings, D-2 reports, itemized receipts, itemized expenditures, candidate/committee metadata via bulk TXT exports and web scraping
- **Federal Elections Commission (FEC)** — Illinois federal candidates, Schedule A contributions, Schedule B disbursements, Schedule E independent expenditures via the FEC API
- **IL Secretary of State Lobbying** — Active lobbying entities/clients plus daily lobbyist-entity-client extracts (supports both `ENTITY_ID`/`ENTITY_NAME` and `ENT_ID`/`ENT_NAME` source headers)
- **IRS 527 Political Organizations** — Organization registrations, periodic reports, directors, related organizations, expenditures, and election authority filings from IRS Form 8871/8872
- **City of Chicago Open Data (Socrata)** — Contracts, payments, lobbyist contributions, and lobbying activity ingested via SODA API pagination (Phase 1)
- **OpenBook Illinois Comptroller** — State contract data and campaign contribution records via HTTP autosuggest API + search scraping, with smart search term generation (suffix/geo stripping, person-name detection, abbreviation expansion)

## Features

### Data Pipeline
- Async Playwright-based web scraping with ASP.NET postback/ViewState handling
- Resumable scrape state management with configurable rate limiting and exponential backoff
- Bulk CSV/TXT ingestion (990MB+ receipt files and large expenditure files) with chunked loading
- Duplicate detection via source identifiers and amendment-safe aggregation
- Raw extraction staging for full audit trails

### Analytics
- **Network Analysis** — Interactive graph suite including force graph (click-to-lock), 3-column Sankey flow, donor-committee heatmap, vendor expenditure network, combined money flow (4-column), state-federal donor overlap, lobbying-campaign finance bridge, 527 dark money pathway, plus advanced relationship views (arc diagrams, local/federal overlap bubble scatter, community treemap/sunburst, and alluvial edge-type flow maps)
- **Edge Semantics + Provenance** — Edge payloads include relationship definitions and metric units (USD vs score/count), and committee-linked candidate edges include estimated donor provenance (proportional-share method) with explicit caveats
- **Anomaly Detection** — Large contribution flags, monthly spike detection, high donor concentration risk (HHI-based)
- **Risk Explainability** — Per-flag explainability payloads (rule, baseline, threshold, percentile context)
- **Concentration Metrics** — Herfindahl-Hirschman Index (HHI), Gini coefficient, top-N donor share
- **Time-Series Intelligence** — Monthly aggregation, 3-month moving averages, month-over-month change
- **Geographic Analysis** — State and city-level donor aggregation from parsed addresses
- **NLP Categorization** — Keyword-based expenditure classification
- **Entity Resolution** — Jaccard similarity candidate matching across state/federal records
- **Cross-Matching Engine** — Lobbying-to-donor, lobbying-to-expenditure, 527-to-committee, 527-expenditure-to-committee, 527-director-to-donor (exhaustive), 527-director-to-candidate (state + federal), 527-director-address-to-donor, 527-org-address-to-committee/donor, and lobbying-to-527 matching
- **527 Contribution Parsing** — IRS FullDataFile type-A records (who donates TO 527 organizations)
- **Dark Money Tracker** — 527 organization expenditures matched to IL committees and candidates
- **Person Intelligence** — Unified cross-dataset name lookup across state donors, candidates, 527 directors, lobbying entities/clients, and federal contributors

### Web Application
- Flask web UI with sortable/filterable tables, pagination, and CSV/JSON export
- Global period filter propagation across legacy and modern pages (homepage, analytics, reports, committees, donors, and search categories)
- Canonical date semantics for period filtering: report-driven views use `filed_date`; contribution-driven views use `transaction_date` (bulk-receipts fall back to `received_date` when transaction timestamps are unavailable)
- Streamlined 3-group navigation (Explore, Analytics, Admin) with consolidated admin hub
- Candidate/committee itemized inflow (receipts) and outflow (expenditures) drilldowns with CSV export
- D2-vs-itemized reconciliation pages for both receipts and expenditures
- Expanded global search across committees, donors, candidates, reports, filed-doc IDs, and donor keys
- Candidate/committee side-by-side compare mode with trend overlays and donor overlap summaries
- Row-level provenance panels on key tables (source table, sync timing, normalization notes, backlinks)
- Interactive dashboards with visual summaries for trends, geography, and risk/anomaly distributions
- Federal finance suite with dedicated pages for networks, money flow, influence scores, follow-the-money path tracing, geographic concentration, donor intelligence, and matching
- Federal transfer-source committee receipts drilldown, linked from candidate detail, money flow, and follow-the-money committee rows
- CSS-only tabbed interfaces on candidate detail (Overview/Money In/Money Out/Outside Spending/Cross-Role) and live feed (Local/Federal/Disbursements/Outside Spending)
- Contextual help-text explanations on all major pages describing data sources and methodology
- Relationship and cross-role tables now include "how to read" definitions for Donor A/B, Committee A/B, Donated (A), Received via B, and Received via E
- Lobbying pages with explanatory help-text blocks for Money Destinations, Clients, Matched Donors, and confidence scores
- Network graph zoom/pan, collision-free node layouts, graph-density modes (Balanced/Strongest/Full), node info panels with relationship semantics, metric units, and flow direction, color legends, and per-tab descriptions for all eight visualization types
- Committee->candidate link panels now explicitly explain that links represent committee receipts associated with candidate-linked committees (not direct transfers) and show estimated top donor provenance when data is available
- Adaptive force layout with post-layout collision detection for dense graph readability
- Follow-the-money donor dropdown selector (replaces raw entity key input) with top 200 donors by contribution volume, candidate-priority labels, and overlap suppression for dense graphs
- Committee SBE routing now falls back to bulk committee profiles when canonical committee records are missing, preventing lobbying-to-committee dead-end 404s
- Candidate competition networks with resolved candidate names from bulk data (not raw IDs)
- Federal-state cross-reference views and donor overlap analysis
- IL lobbying entity/client browser with cross-matched campaign finance connections
- IRS 527 organization browser with financial summaries, directors (with matched donors/candidates columns), contributions received, and IL expenditures
- Dark money tracker showing 527 expenditures flowing to IL committees and candidates
- Dashboard 527 Dark Money summary card with organization count, total expenditures, director-donor overlaps, and lobbying entity count
- Person Intelligence page for unified name search across all data sources (state donors, candidates, 527 directors, lobbying entities/clients, federal FEC contributors)
- Session authentication with consolidated admin tool hub

## Tech Stack

- **Python 3.12** — Core language
- **Flask 3.0** — Web framework (Jinja2 templates)
- **Playwright** — Async browser automation for scraping
- **PostgreSQL 16** — Database (local and production)
- **Click** — CLI command framework
- **pytest** — Test suite

## Quick Start

### Prerequisites
- Python 3.12+
- PostgreSQL 16+ (local: `brew install postgresql@16`)
- An FEC API key (optional, for federal data sync)

### Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# PostgreSQL setup (one-time)
createdb ilcf
export DATABASE_URL=postgresql://$(whoami)@localhost/ilcf

# Optional: configure environment
cp .env.example .env  # Add DATABASE_URL, FEC_API_KEY, FLASK_SECRET_KEY, etc.
```

### Initialize and Load Data
```bash
# Create database schema
python run.py init-db

# Import ISBE data via illinois-sunshine ETL (preferred — PostgreSQL-native)
# Downloads missing files automatically with --download
python run.py sunshine-import --bulk-dir Bulk_download --download

# Recreate bulk_* compatibility views after sunshine ETL
python scripts/swap_bulk_to_isbe.py

# Legacy ISBE import (still works, loads into bulk_*_clean tables)
# python run.py import-bulk-download --directory Bulk_download

# Build analytics materialized views
python run.py refresh-analytics

# Sync federal (FEC) data for Illinois candidates
python run.py sync-fec-il-federal
python run.py rebuild-fec-donor-identities

# Import IL SOS lobbying data
python run.py import-lobbying --file Bulk_download/ILSOS_Lobbying_activeandclients/Active_Lobbying_Entities_and_Their_Clients_20260213.csv

# Or import daily lobbyist/entity/client extract format
python run.py import-lobbying --file Bulk_download/Lobbyist_Entity_Client_Data_Daily_20260214.csv

# Import IRS 527 political org filings (IL-filtered by default)
python run.py import-irs527 --file Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt --illinois-only

# Import City of Chicago Phase 1 datasets from Socrata API
# Uses SOCRATA_APP_TOKEN from environment unless --app-token is provided
python run.py import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert

# Import OpenBook Comptroller vendor contracts (smart search enabled by default)
python run.py import-openbook-batch --generate-seeds --max-vendors 50
# Disable smart search to use raw vendor names verbatim
python run.py import-openbook-batch --no-smart-search --max-vendors 50
# OpenBook web routes: /openbook/ and /openbook/<vendor_key>

# Run cross-matching across all data sources
python run.py run-cross-matching --only all --parallel --workers 4

# Incremental cross-matching (skip unchanged jobs)
python run.py run-cross-matching --only all --parallel --workers 4 --incremental

# Fastest validated routine mode for repeat runs (materialized donor index + incremental fingerprints)
python run.py run-cross-matching --only all --parallel --workers 4 --incremental

# Force full rebuild
python run.py run-cross-matching --only all --no-parallel --full-rebuild

Cross-matching fast-path caveat:
- PostgreSQL cross-matching sessions use performance-oriented settings (`synchronous_commit=off`, larger temp/work memory) for staging/swap writes.
- This is safe for recomputable cross-matching output tables, but do not reuse this pattern for non-recomputable transactional writes.
```

Compute-heavy workflow recommendation:
- For commands expected to take significant CPU time (for example `import-irs527` and `run-cross-matching --only all`), consider running on your local machine first.
- Prefer uploading resulting files or derived outputs to the server, and use server-side execution for steps that must run directly against production data.

PostgreSQL:
- PostgreSQL is the primary and only supported database for both local development and production.
- `DATABASE_URL` must be set in all environments:
  - Local: `DATABASE_URL=postgresql://devin@localhost/ilcf`
  - Prod: set in `/srv/illinois_campaign_finance/shared/.env`
- See `docs/postgres_self_hosted_runbook.md` for install, backup, and maintenance checklists.
- Legacy migration scripts (from the SQLite→PostgreSQL migration) are in `scripts/postgres/` for reference only.

### Run Scrapers (Optional)
```bash
# Scrape ISBE main report list
python run.py scrape-main --start-page 1 --end-page 40

# Seed/refresh CommitteeDetail URLs from stored SBE committee IDs
python run.py seed-committee-urls --batch-size 200

# Scrape committee detail pages
python run.py scrape-committee-reports --batch-size 20 --filed-cutoff 2025-06-01
python run.py scrape-committee-reports --committee-id-sbe 32451 --committee-id-sbe 40973 --filed-cutoff 2025-06-01

# Scrape D-2 details and itemized contributions
python run.py scrape-d2-details --batch-size 20
python run.py scrape-d2-itemized --batch-size 50
python run.py scrape-d2-all-pending --detail-batch-size 500 --itemized-batch-size 1000
python run.py scrape-d2-details --committee-id-sbe 32451 --with-itemized
python run.py scrape-d2-itemized --committee-id-sbe 32451

# Scrape A-1 contribution details
python run.py scrape-details --batch-size 20
```

### Data Maintenance
```bash
# Clean garbage rows and normalize donor metadata
python run.py clean-data --apply

# View data quality summary
python run.py data-quality

# Check scraper progress
python run.py scrape-status
```

### Run the Web Server
```bash
python run.py runserver --port 5000
```

Visit `http://localhost:5000` to access the dashboard.

## Project Structure

```
├── cli/                    CLI commands (init, scrape, import, analytics)
├── scraper/                Playwright-based async scrapers + HTTP scrapers
│   ├── main_list_scraper   ISBE main report list scraper
│   ├── committee_scraper   Committee detail + D-2 filing scraper
│   ├── detail_scraper      A-1 itemized contribution scraper
│   ├── openbook_scraper    OpenBook Comptroller vendor contracts + contributions
│   ├── comptroller_contracts Comptroller State Contracts DataTables scraper
│   ├── rate_limiter        Configurable rate limiting with backoff
│   └── state_manager       Resumable scrape progress tracking
├── database/               Data layer
│   ├── schema.sql          53-table schema definition
│   ├── models.py           Dataclass-based ORM models
│   ├── analytics.py        Network, anomaly, concentration, time-series, geo, NLP
│   ├── bulk_download_loader Bulk TXT import and normalization
│   ├── federal_fec.py      FEC API integration and candidate matching
│   ├── lobbying_loader.py  IL SOS lobbying entity/client CSV loader
│   ├── irs527_loader.py    IRS 527 FullDataFile pipe-delimited loader
│   └── cross_matching.py   Cross-matching engine (lobbying, 527, campaign finance)
├── webapp/                 Flask web application
│   ├── routes/             16 route modules (dashboard, analytics, federal finance, lobbying, 527, OpenBook, etc.)
│   └── templates/          50+ Jinja2 templates (incl. tabbed detail views)
├── scripts/                Automation scripts
│   ├── sync-fec.sh         Weekly FEC data sync wrapper
│   ├── pull-db.sh          Pull production DB to local (rsync + WAL checkpoint)
│   ├── fec-schedule-b-catchup.sh  Hourly Schedule B backfill wrapper
│   └── fec-schedule-e-catchup.sh  Hourly Schedule E backfill wrapper
├── tests/                  pytest suite (20+ test modules)
├── docs/                   Data update guide, roadmaps, checklists, analysis plans
│   └── systemd/            Sample unit/timer files (including Schedule E catch-up)
├── Bulk_download/          ISBE bulk export TXT files
└── data/                   Legacy data directory (SQLite database deprecated)
```

## Planning Docs

- `docs/graphs_roadmap.md` — chart roadmap with required joins, filters, and performance notes
- `docs/network_analysis_plan.md` — network model, metrics plan, and lightweight-first compute strategy
- `docs/illinois_clickable_district_map_plan.md` — local/federal district geometry + join-key strategy
- `docs/ameren_lobbying_case_study.md` — reproducible Ameren relationship queries and normalization notes

## Testing

```bash
pytest -q
```

If you want a faster pre-deploy gate, run this subset first:

```bash
pytest -q tests/test_federal_fec.py tests/test_lobbying_routes.py tests/test_uiux_improvements.py tests/test_webapp.py::TestWebApp::test_federal_finance_page_loads_with_synced_rows
```

OpenBook smart-search validation snapshot (10 seeds):
- `--no-smart-search`: ~26.9s, 7 resolved seeds, 7 total matches (1.0 matches/resolved seed)
- smart-search default: ~195.1s, 13 resolved seeds, 997 total matches (76.7 matches/resolved seed)
- Smart search increases coverage but can broaden candidate sets; default match threshold is 0.4.

For scraper/live checks, run integration tests separately:

```bash
pytest -q -m integration
```

## Performance-First Execution Workflow (Required)

For any non-trivial pipeline, import, migration, cross-matching run, or route/query change, use this workflow before considering the task complete.

1. **Baseline First (Current Path)**
  - Measure current behavior with realistic data and record wall-clock runtime.
  - For web routes, capture at least 1 cold run and 2-3 warm runs (median warm).
  - For CLI jobs, log start/end timestamps and rows/records processed.

2. **Identify Fastest Viable Path Up Front**
  - Compare at least two implementation strategies before coding deeply (example: SQL-side aggregation/materialization vs Python loops; COPY/bulk load vs row inserts; incremental mode vs full rebuild).
  - Prefer approaches that reduce total I/O and repeated recomputation.
  - Do not assume parallelization is faster if the bottleneck is DB write contention.

3. **Choose by Measured Throughput, Not Intuition**
  - Run a bounded benchmark/prototype of candidate approaches on representative slices.
  - Pick the option with best end-to-end runtime that preserves correctness and operational safety.

4. **Default Optimization Priorities**
  - Reduce data scanned and rows touched first.
  - Reuse precomputed summaries/materialized tables where available.
  - Use chunked/bulk operations instead of per-row writes.
  - Add indexes only where query plans show benefit; avoid speculative indexing.
  - Use incremental execution modes when source fingerprints/data are unchanged.

5. **Post-Change Verification Gate**
  - Re-run the same baseline measurement after changes and report before/after numbers.
  - If speed does not improve materially, either iterate once more or clearly document why the chosen path is still preferred.
  - Update docs with command flags and the fastest known invocation for future runs.

### Large-Run Fast Path Defaults

- Cross-matching:
  - Prefer incremental mode for routine reruns:
    - `python run.py run-cross-matching --only all --parallel --workers 4 --incremental`
  - Use full rebuild only when matching logic/inputs changed significantly.

- PostgreSQL migration:
  - Prefer bulk-loading methods over row-by-row inserts when available.
  - Defer expensive index creation until after data copy for faster initial load.

- Route performance:
  - Prefer summary/materialized tables and route TTL cache fast paths before adding heavier compute in request time.

### Setup/ETL Optimization Notes (2026-02-16)

- `database/analytics.py`:
  - `_refresh_materialized_contributions()` now uses set-based SQL inserts for donor-committee aggregates, monthly totals, and large-contribution materialization (replacing Python row loops/dicts/lists).
- `database/irs527_loader.py`:
  - Illinois-only EIN discovery now uses lightweight field extraction in the first pass (instead of full row parsing) for `load_irs527_full_file()`, `reload_irs527_reports()`, and `reload_irs527_contributions()`.
  - Corrected expenditure-state index used by Illinois filtering (`_is_illinois_expenditure` now reads the state slot).
- `database/bulk_download_loader.py`:
  - `_normalize_bulk_receipt_date()` now uses LRU caching to avoid repeated date parsing work for recurring date strings in large receipt files.
- `database/federal_fec.py`:
  - `_upsert_seed_rows()` and `_upsert_candidate_committees()` now batch with `executemany()` rather than per-row upserts.

Bounded synthetic benchmarks (fast local checks, not full production runs):
- `_refresh_materialized_contributions` (300k synthetic contributions): ~2.07s -> ~0.79s (~2.6x faster).
- IRS Illinois EIN first-pass extraction (800k synthetic lines): ~1.34s -> ~0.23s (~5.9x faster).
- Receipt date normalization loop (2M repeated date values): ~13.65s -> ~0.09s (~150x faster with cache hits).

For full-volume validation, run on your data and capture elapsed + row counts:

```bash
python run.py refresh-analytics --skip-snapshot
python run.py import-irs527 --file <FullDataFile.txt> --illinois-only
python run.py import-bulk-download --directory <Bulk_download_dir>
```

## New Data Integration Checklist (Required)

Whenever a new dataset or new columns are added, treat integration as a full-system task (not just ingestion):

1. **Ingestion + Schema**
  - Add/adjust raw + cleaned tables in `database/schema.sql`.
  - Update loader/CLI paths and ensure idempotent upserts.
  - Add field-level QA checks (nulls, malformed dates/numbers, source freshness windows).

2. **Cross-Matching Review (Always Required)**
  - Evaluate whether new entities should match to existing donors, committees, candidates, lobbyists, vendors, 527 orgs/directors.
  - If matching is valuable, add/update jobs in `database/cross_matching.py` and persist results in dedicated match tables.
  - Document thresholds/methods and add tests for false-positive/false-negative edge cases.

3. **Visualization + UX Integration**
  - Decide where the dataset adds value in existing pages (`/analytics`, `/federal-finance`, `/lobbying`, `/527`, dashboard cards, detail pages).
  - Evaluate whether a new visualization is justified (network edge type, time-series, geo, concentration, flow/alluvial).
  - Add explanatory help text for any new metric/relationship semantics.

4. **Project-Wide Integration Validation**
  - Run route-level sanity checks for affected pages and verify no template/query regressions.
  - Run full tests (`pytest -q`) plus targeted module tests for new loaders/routes/matching.
  - Confirm docs are updated (`README.md`, `CLAUDE.md`, any runbooks) with commands, config, and caveats.
  - Verify performance impact (cold/warm where relevant) and add caching/materialization updates if needed.

Use this checklist in every data-expansion PR to ensure the new data is fully integrated across ingestion, matching, analytics, and UI.

## Current Data Scale

| Dataset | Records |
|---|---|
| State candidates | 32,212 |
| State committees | 33,760 |
| Itemized receipts | 6.4M+ |
| Federal candidates (IL) | 134 |
| Federal contributions | 20,261 |
| IL lobbying entities | 625 |
| IL lobbying clients | 3,748 |
| IL lobbying entity-client pairs | 14,000+ |
| IL lobbying lobbyists | 4,000+ |
| IRS 527 organizations | 17.5M lines (IL-filtered subset) |
| Database size | 5.5 GB+ |

## Configuration

Environment variables (set in `.env` or export directly):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://devin@localhost/ilcf` | PostgreSQL connection string (required) |
| `DATABASE_PATH` | `data/campaign_finance.db` | Legacy SQLite path (deprecated, unused) |
| `FEC_API_KEY` | *(empty)* | FEC API key for federal data sync |
| `APP_ENV` | `development` | Runtime environment (`development`, `staging`, `production`) |
| `FLASK_SECRET_KEY` | `dev-secret-key...` | Flask session secret |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `API_KEYS` | *(empty)* | Comma-separated API keys for `/api/*` |
| `API_REQUIRE_KEY` | `false` unless `API_KEYS` set | Require API key auth on `/api/*` |
| `API_RATE_LIMIT_PER_MINUTE` | `120` | Per-key/per-IP API request budget per minute |
| `SEARCH_MIN_QUERY_LENGTH` | `2` | Minimum search length for non-numeric queries |
| `SEARCH_MAX_QUERY_LENGTH` | `64` | Max search input length before truncation |
| `SEARCH_QUERY_TIMEOUT_MS` | `700` | Per-section query timeout guardrail on `/search` |
| `SEARCH_SLOW_QUERY_MS` | `400` | Threshold for slow-search warning logs |
| `SEARCH_RESULTS_CACHE_TTL_SECONDS` | `120` | TTL for in-process cached `/search` responses (by period + query + type) |
| `SEARCH_RESULTS_CACHE_MAX_ENTRIES` | `256` | Max retained cached `/search` keys before LRU eviction |
| `FEDERAL_VIEW_CACHE_ENABLED` | `true` | Enable snapshot caching for heavy federal views |
| `FEDERAL_OVERVIEW_CACHE_TTL_SECONDS` | `900` | Cache TTL for federal overview computations |
| `FEDERAL_NETWORKS_CACHE_TTL_SECONDS` | `600` | Cache TTL for federal network/overlap graphs |
| `FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS` | `600` | Cache TTL for donor segmentation/influence |
| `FEDERAL_MATCHING_CACHE_TTL_SECONDS` | `600` | Cache TTL for matching diagnostics |
| `ROUTE_PERF_CACHE_ENABLED` | `true` | Enable in-process cache for expensive route payloads (dashboard/relationships/527 dark-money) |
| `DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS` | `180` | Cache TTL for homepage relationship insights panel |
| `DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS` | `180` | Cache TTL for homepage candidate summary stats |
| `DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS` | `180` | Cache TTL for homepage top donor table |
| `ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS` | `180` | Cache TTL for `/analytics/relationships` network payload |
| `IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS` | `180` | Cache TTL for `/527/dark-money` summary stats payload |
| `DASHBOARD_PREWARM_ENABLED` | `true` | Prewarm homepage cache blocks in a startup background thread |
| `LOCAL_DATA_STALE_DAYS` | `45` | Freshness warning threshold for local data |
| `FEDERAL_DATA_STALE_DAYS` | `14` | Freshness warning threshold for federal data |
| `RATE_LIMIT_RPM` | `30` | Scraper requests per minute |
| `SOCRATA_APP_NAME` | `Illinois_campaignfinance` | User-Agent app name for Socrata requests |
| `SOCRATA_APP_TOKEN` | *(empty)* | Socrata app token used by `import-chicago-phase1` |
| `SOCRATA_APP_SECRET` | *(empty)* | Optional Socrata app secret (reserved for future auth flows) |
| `SOCRATA_API_BASE_URL` | `https://data.cityofchicago.org` | Base URL for Socrata API |
| `SOCRATA_API_PAGE_LIMIT` | `50000` | Maximum rows requested per Socrata page |
| `SOCRATA_API_TIMEOUT_SECONDS` | `60` | HTTP timeout for Socrata requests |
| `SOCRATA_API_MAX_RETRIES` | `5` | Retry attempts for throttled/transient Socrata failures |
| `SOCRATA_API_MIN_INTERVAL_SECONDS` | `0.25` | Minimum delay between Socrata API calls |

## Route Performance Notes (2026-02)

- Added route-level TTL caching for three heavy pages: `/`, `/analytics/relationships`, and `/527/dark-money`.
- Homepage now reuses cached insights and top-donor datasets; top donors prefer `analytics_donor_summary` for faster reads when available.
- Homepage candidate stats now consolidate multiple row-count/total queries into fewer aggregate SQL calls (notably for `bulk_candidate_committee_finance_agg`, D2 totals, and federal Schedule B/E cards).
- `/person-intelligence` now batches candidate committee + candidacy enrichment into set-based queries (removes per-candidate N+1 lookup loops).
- Homepage legacy cards (`reports` / `committees` / `donors`) now degrade safely to zero when legacy tables are absent instead of failing route render.
- Added startup prewarm (`DASHBOARD_PREWARM_ENABLED`) to reduce first-visit latency after process restart.
- Added `irs527_contributor_rollup` population in the IRS 527 loader path so dark-money and contribution-facing summaries avoid repeated full-table scans.
- Added `/search` response caching and section guards:
  - `filed_docs` search runs only for doc-like queries in `type=all`.
  - `donor_keys` search runs only for key-like queries in `type=all`.
  - explicit `type=filed_docs` / `type=donor_keys` still forces those sections.
- Added index-backed 527 query optimizations:
  - use `amount > 0` predicates (instead of `COALESCE(amount, 0) > 0`) so numeric indexes are used.
  - date filters use dual-format text ranges (`YYYY-MM-DD` and `YYYYMMDD`) to stay index-friendly without `DATE(column)` wrappers.
  - new indexes: `idx_irs527_contributions_name_amount`, `idx_irs527_contributions_date_amount`, `idx_irs527_expenditures_date_amount`.
- Added `isbe_condensed_receipts` indexes to support analytics refresh + donor search paths:
  - `idx_isbe_condensed_receipts_filed_doc_id`
  - `idx_isbe_condensed_receipts_committee_id`
  - `idx_isbe_condensed_receipts_received_date`
  - `idx_isbe_condensed_receipts_active_part1`
  - `idx_isbe_condensed_receipts_donor_name_trgm`
  - `idx_isbe_condensed_receipts_donor_key_trgm`
- `refresh-analytics --with-snapshot` now supports PostgreSQL timestamp objects in snapshot metadata parsing and can build NLP spending summary from `bulk_expenditures_clean` when legacy `contributions` is absent.
- Expected behavior: first request after cache expiry/restart can still be slower; subsequent warm requests should be significantly faster.

## Server Deployment

The production site runs on a Hetzner VPS.

### Compute-Heavy Tasks (Run Locally First)

Default policy for expensive processing:
- If a command is expected to run for a long time or heavily use CPU, consider the local-machine path first (MacBook M3 Pro, 18GB RAM).
- Run the compute-heavy step locally, validate outputs, then upload outputs/artifacts to production.
- Use server compute for these jobs only when the step must run directly against production-only data.
- **Server tasks estimated >10 minutes**: Do NOT run autonomously. Provide the exact command to the operator so they can run it themselves, watch progress, and see it through to completion. Always estimate the expected runtime before handing off.
- For tasks that will run on the server, print an estimated runtime before execution (for example: expected duration range and whether it is CPU-heavy).

Examples:
- Often local-first: very large imports, full cross-matching (`run.py run-cross-matching --only all`), one-off backfills.
- Usually server-side: lightweight deploy tasks (`git pull`, `systemctl restart ilcf-web.service`), quick targeted commands.

### SSH Access

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56
```

SSH key passphrase is stored in macOS Keychain via ssh-agent (configured in `~/.zshrc`). No passphrase prompt needed.

### Syncing Production DB to Local

```bash
# Pull the production database (WAL checkpoint + rsync)
./scripts/pull-db.sh

# Apply any new schema migrations locally
python run.py init-db
```

First sync transfers the full ~5.5GB DB. Subsequent syncs use rsync's delta algorithm to transfer only changed blocks. The script automatically backs up the existing local DB (keeps 3 most recent).

### Server File Paths

```
/srv/illinois_campaign_finance/
├── app/                        Application code (git checkout)
│   ├── run.py                  CLI entry point
│   ├── webapp/                 Flask app
│   ├── scripts/
│   │   └── sync-fec.sh         FEC sync automation
│   ├── data/
│   │   └── (legacy — database now in PostgreSQL)
│   └── ...
└── shared/                     Persistent data across deploys
    ├── venv/                   Python virtual environment (used by systemd + CLI)
    │   └── bin/python3
    ├── .env                    Environment variables (FEC_API_KEY, etc.)
    ├── logs/                   Sync and task logs
    │   └── sync-fec-*.log      Timestamped FEC sync logs
    └── downloads/              Staging area for ISBE bulk files
```

### Production Source of Truth (Important)

Use these paths/services for production operations on the Hetzner host:

- App code: `/srv/illinois_campaign_finance/app`
- Runtime env: `/srv/illinois_campaign_finance/shared/.env`
- Python: `/srv/illinois_campaign_finance/shared/venv/bin/python3`
- Web service: `ilcf-web.service`
- FEC sync service/timer: `il-campaign-fec-sync.service` + `il-campaign-fec-sync.timer`

### Running Commands From The Shared Venv (Required)

All production CLI tasks should run with the shared virtualenv binaries, not system Python.

Canonical paths:
- Python: `/srv/illinois_campaign_finance/shared/venv/bin/python3`
- Pip: `/srv/illinois_campaign_finance/shared/venv/bin/pip`
- App root: `/srv/illinois_campaign_finance/app`
- Env file: `/srv/illinois_campaign_finance/shared/.env`

Recommended pattern (without activating the venv):

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
PIP=/srv/illinois_campaign_finance/shared/venv/bin/pip

# Load/export runtime env vars (FEC_API_KEY, etc.)
set -a
source /srv/illinois_campaign_finance/shared/.env
set +a

# Install deps with venv pip
$PIP install -r /srv/illinois_campaign_finance/app/requirements.txt

# Run CLI commands with venv python3
$PYTHON run.py refresh-analytics --with-snapshot
```

Optional pattern (activate first, then use `python3`/`pip`):

```bash
cd /srv/illinois_campaign_finance/app
source /srv/illinois_campaign_finance/shared/venv/bin/activate
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a
python3 run.py refresh-analytics --with-snapshot
```

Verification commands:

```bash
/srv/illinois_campaign_finance/shared/venv/bin/python3 -V
/srv/illinois_campaign_finance/shared/venv/bin/python3 -c "import sys; print(sys.executable)"
/srv/illinois_campaign_finance/shared/venv/bin/pip -V
```

Common failures and fixes:
- `ModuleNotFoundError: No module named 'werkzeug'`:
  - `/srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt`
- `Error: missing FEC API key`:
  - `set -a; source /srv/illinois_campaign_finance/shared/.env; set +a`
- Running with wrong interpreter:
  - Use the full venv python path in every command, or activate the shared venv first.

Do not use legacy paths/services unless intentionally migrating:

- Legacy app path: `/srv/illinois/app`
- Legacy web unit: `illinois-web.service`

Quick verification:

```bash
systemctl status ilcf-web.service --no-pager
systemctl cat ilcf-web.service
systemctl status illinois-web.service --no-pager
```

If `illinois-web.service` exists, keep it disabled/inactive to avoid operator confusion:

```bash
systemctl disable --now illinois-web.service
```

### First-Time Server Setup

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56

# Create venv and install dependencies
python3 -m venv /srv/illinois_campaign_finance/shared/venv
/srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt

# Configure environment (no spaces around '=')
cat > /srv/illinois_campaign_finance/shared/.env << 'EOF'
FEC_API_KEY=your_key_here
FLASK_SECRET_KEY=change-me-in-production
EOF
```

### Deploying Code Updates

Push changes locally, then pull on the server:

```bash
# Local: push to remote
git push origin main

# SSH into server and pull
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56

cd /srv/illinois_campaign_finance/app
git config --global --add safe.directory /srv/illinois_campaign_finance/app  # first time only
git pull origin main

PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
PIP=/srv/illinois_campaign_finance/shared/venv/bin/pip

# Re-install deps (required if you hit ModuleNotFoundError such as werkzeug)
$PIP install -r /srv/illinois_campaign_finance/app/requirements.txt

# Rebuild analytics materialized views + snapshot cache
$PYTHON run.py refresh-analytics --with-snapshot

# Optional but recommended after federal transfer/committee-link changes
$PYTHON run.py refresh-fec-transfer-committees --cycle 2026
$PYTHON run.py sync-fec-transfer-committee-receipts --cycle 2026 --max-calls 1000 --max-pages-per-committee 25

# Restart the web application
systemctl restart ilcf-web.service
```

Production deployment checklist:
1. Run local tests (`pytest -q`).
2. Push to `main` and pull on server (`git pull origin main`).
3. Reinstall dependencies in shared venv (`/srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt`).
4. Refresh analytics snapshots (`/srv/illinois_campaign_finance/shared/venv/bin/python3 run.py refresh-analytics --with-snapshot`).
5. Refresh/sync transfer-source committee receipts (`/srv/illinois_campaign_finance/shared/venv/bin/python3 run.py refresh-fec-transfer-committees`, `/srv/illinois_campaign_finance/shared/venv/bin/python3 run.py sync-fec-transfer-committee-receipts`).
6. Restart web service (`systemctl restart ilcf-web.service`).
7. Verify service health (`systemctl status ilcf-web.service --no-pager`).
8. Smoke-test critical routes:
   - `/analytics/networks`
   - `/lobbying/`
   - `/committees/sbe/<known_sbe_id>`
   - `/federal-finance/follow-the-money?cycle=2026`
   - `/federal-finance/committees/<committee_id>/receipts?cycle=2026`

### Uploading New ISBE `expenditures_*.txt` and Updating Production

Use this flow when you receive a new ISBE expenditures bulk file.

```bash
# Local machine: upload file to production app bulk folder
scp -i ~/.ssh/hetzner_ed25519 \
  /Users/devin/Illinois_campaign_finance/Bulk_download/expenditures_XXXXXXXXXXXX.txt \
  root@178.156.162.56:/srv/illinois_campaign_finance/app/Bulk_download/

# Server: optional DB backup before import
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56
pg_dump -Fc ilcf > /srv/illinois_campaign_finance/shared/data/ilcf_$(date +%F_%H%M%S).dump

# Server: run bulk import (loads latest committees/d2/candidates/links/receipts/expenditures files)
cd /srv/illinois_campaign_finance/app
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a
/srv/illinois_campaign_finance/shared/venv/bin/python3 run.py import-bulk-download --directory Bulk_download

# Rebuild analytics snapshot + restart web service
/srv/illinois_campaign_finance/shared/venv/bin/python3 run.py refresh-analytics --with-snapshot
systemctl restart ilcf-web.service
```

Notes:
- The import now includes `bulk_expenditures_clean`, `bulk_expenditures_rejects`, and derived reconciliation/aggregate tables.
- Malformed expenditure rows are quarantined in `bulk_expenditures_rejects` (they do not block the whole import).
- Prefer running import directly against production DB rather than replacing DB files across environments.

### Key Web Routes

| Route | Description |
|-------|-------------|
| `/` | Dashboard overview with stats, freshness, launch paths |
| `/search` | Global search across entities (committees, donors, candidates, reports, filed docs, donor keys) |
| `/compare` | Candidate-vs-candidate or committee-vs-committee trend and overlap comparison |
| `/candidates` | Unified candidates page — state (ISBE) and federal (FEC) |
| `/candidate-finance` | State candidate finance detail (ISBE data) |
| `/candidate-finance/<candidate_id>/<committee_id>/itemized` | Candidate/committee itemized receipts (money in) |
| `/candidate-finance/<candidate_id>/<committee_id>/itemized-expenditures` | Candidate/committee itemized expenditures (money out) |
| `/d2-reconciliation` | D2-vs-itemized receipts reconciliation |
| `/d2-expenditures-reconciliation` | D2-vs-itemized expenditures reconciliation |
| `/federal-finance/` | Federal finance overview with race analytics and contribution summary |
| `/federal-finance/candidates` | Sortable federal candidate list with totals raised/spent |
| `/federal-finance/networks` | Donor-candidate network graph with flow tables and trend matrix |
| `/federal-finance/money-flow` | Multi-layer money flow (A/B/E) and cross-role organization analysis |
| `/federal-finance/donor-intelligence` | Donor segmentation (K-Means/DBSCAN), network clustering, and community treemap/sunburst |
| `/federal-finance/influence` | Influence scores for donors and candidates (PageRank, degree, weighted) |
| `/federal-finance/follow-the-money` | Multi-hop donor path tracing with interactive SVG visualization |
| `/federal-finance/geography` | Geographic concentration analysis (states, cities, HHI per race) |
| `/federal-finance/matching` | Federal/local donor matching diagnostics, overlap analysis, and local-vs-federal bubble scatter |
| `/federal-finance/<candidate_id>` | Federal candidate detail with tabbed A/B/E drilldowns |
| `/federal-finance/races/<office_code>/<district_code>/outside-spending` | Race-level Schedule E drilldown with filters, sorting, pagination, and itemized transactions |
| `/federal-finance/committees/<committee_id>/receipts` | Itemized Schedule A receipts for one committee, used to explain transfer-source provenance |
| `/admin/` | Consolidated admin hub (requires login) — links to all data tools |
| `/admin/federal-receipt-audit` | Internal mismatch flags: FEC reported totals vs synced Schedule A subtotals |
| `/admin/federal-disbursement-audit` | Internal mismatch flags: FEC reported disbursements vs synced Schedule B subtotals |
| `/analytics/` | Network, anomaly, concentration, and geographic analytics |
| `/analytics/relationships` | Relationship-specific graph lab: co-giving and committee-similarity arc views, candidate competition, and alluvial lobbying/527 pathways |
| `/analytics/risk` | Risk flags with explainability and distribution visualizations |
| `/donors` | Cross-committee donor directory |
| `/committees/sbe/<committee_id_sbe>` | Committee detail by SBE ID with canonical redirect and bulk-table fallback when canonical record is missing |
| `/lobbying/` | IL lobbying entities list with client counts |
| `/lobbying/<entity_id>` | Lobbying entity detail with clients and matched payees |
| `/lobbying/client/<client_id>` | Lobbying client detail with entities, donor matches, 527 connections |
| `/527/` | IRS 527 organization list (IL-filtered) with financial totals |
| `/527/<ein>` | 527 org detail — directors (with matched donors/candidates), contributions received, expenditures, committee matches |
| `/527/dark-money` | Dark money tracker — 527 expenditures matched to IL committees/candidates |
| `/person-intelligence` | Unified cross-dataset person search (donors, candidates, 527 directors, lobbying, FEC) |

CSV exports:
- Candidate detail Schedule B: `/federal-finance/<candidate_id>?cycle=2026&format=csv&table=schedule_b`
- Candidate detail Schedule E: `/federal-finance/<candidate_id>?cycle=2026&format=csv&table=schedule_e`
- Race-level Schedule E: `/federal-finance/races/<office_code>/<district_code>/outside-spending?cycle=2026&format=csv`
- Live feed Schedule B: `/live-feed?format=csv&table=schedule_b`
- Live feed Schedule E: `/live-feed?format=csv&table=schedule_e`
- Candidate/committee itemized receipts: `/candidate-finance/<candidate_id>/<committee_id>/itemized?format=csv`
- Candidate/committee itemized expenditures: `/candidate-finance/<candidate_id>/<committee_id>/itemized-expenditures?format=csv`
- D2 receipts reconciliation: `/d2-reconciliation/?format=csv`
- D2 expenditures reconciliation: `/d2-expenditures-reconciliation/?format=csv`

### Mobile Smoke Check

Use this to quickly validate mobile rendering across key routes and catch horizontal overflow regressions.

```bash
cd /srv/illinois_campaign_finance/app
/srv/illinois_campaign_finance/shared/venv/bin/python3 scripts/mobile_smoke_check.py --base-url http://127.0.0.1:5000
```

Outputs:
- Screenshots in `output/mobile_smoke/*.png`
- JSON report in `output/mobile_smoke/report.json`
- Markdown summary in `output/mobile_smoke/report.md`

Any non-zero exit means at least one route failed to load or exceeded horizontal overflow threshold.

## Updating Data on the Server

See [`docs/data_update_guide.md`](docs/data_update_guide.md) for full details. Quick reference below.

### FEC Data (Automated)

A systemd timer runs `scripts/sync-fec.sh` weekly (Sundays 3 AM UTC). It syncs FEC contributions, rebuilds donor identities, and refreshes analytics.

To run manually:

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56

cd /srv/illinois_campaign_finance/app
bash scripts/sync-fec.sh
```

Or run individual steps:

```bash
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
source /srv/illinois_campaign_finance/shared/.env && export FEC_API_KEY

$PYTHON run.py sync-fec-il-federal --cycle 2026 --contributor-state IL --max-calls 900
$PYTHON run.py rebuild-fec-donor-identities
$PYTHON run.py refresh-analytics --with-snapshot
```

### Hourly Schedule A Catch-Up (for receipt gaps)

Use this when FEC reported candidate totals are higher than synced Schedule A subtotal and you need to backfill missing rows under hourly API limits.

One-off run:

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
source /srv/illinois_campaign_finance/shared/.env && export FEC_API_KEY

$PYTHON run.py backfill-fec-schedule-a \
  --cycle 2026 \
  --max-calls 1000 \
  --max-pages-per-committee 25 \
  --min-abs-gap 500 \
  --refresh-cache
```

### Hourly Schedule B Catch-Up (committee disbursements)

Use this to fill missing federal committee spending rows (Schedule B) with resumable pagination.

One-off run:

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
source /srv/illinois_campaign_finance/shared/.env && export FEC_API_KEY

$PYTHON run.py backfill-fec-schedule-b \
  --cycle 2026 \
  --max-calls 1000 \
  --max-pages-per-committee 25 \
  --refresh-transfer-committees \
  --sync-transfer-receipts \
  --refresh-cache
```

Principal-only run (optional):

```bash
$PYTHON run.py backfill-fec-schedule-b --cycle 2026 --principal-only --max-calls 1000 --refresh-cache
```

Schedule B wrapper script (recommended for automation):

```bash
cd /srv/illinois_campaign_finance/app
bash scripts/fec-schedule-b-catchup.sh
```

### Transfer-Source Committee Receipts Sync (committee-to-candidate provenance)

Use this workflow when you want committee transfer rows to link to underlying committee receipts (for example, "Friends of Raja for Congress" style paths).

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
source /srv/illinois_campaign_finance/shared/.env && export FEC_API_KEY

# 1) Mark committees that transfer money to candidate or committee recipients
$PYTHON run.py refresh-fec-transfer-committees --cycle 2026

# 2) Pull Schedule A receipts for those transfer-source committees
$PYTHON run.py sync-fec-transfer-committee-receipts \
  --cycle 2026 \
  --max-calls 1000 \
  --max-pages-per-committee 25
```

### IRS 527 Totals Repair / Backfill

Use this when `/527/` shows `$0` totals because legacy 8872 parsing wrote report rows with wrong EIN/totals mapping.

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a

# Rebuild irs527_reports from type-2 (8872) rows only
$PYTHON run.py repair-irs527-reports \
  --file /srv/illinois_campaign_finance/shared/downloads/FullDataFile.txt \
  --illinois-only \
  --replace-existing
```

If your FullDataFile is stored in the repo path instead of shared downloads:

```bash
$PYTHON run.py repair-irs527-reports \
  --file /srv/illinois_campaign_finance/app/Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt \
  --illinois-only \
  --replace-existing
```

Post-repair verification:

```bash
psql ilcf <<'SQL'
SELECT COUNT(*) AS reports, COUNT(DISTINCT ein) AS distinct_eins FROM irs527_reports;
SELECT ein, COUNT(*) AS c, SUM(total_contributions), SUM(total_expenditures)
FROM irs527_reports
GROUP BY ein
ORDER BY c DESC
LIMIT 20;
SELECT COUNT(*) AS orgs_without_report_match
FROM irs527_organizations o
LEFT JOIN (SELECT DISTINCT ein FROM irs527_reports) r ON r.ein = o.ein
WHERE r.ein IS NULL;
SQL
```

Notes:
- Run SQL inside `psql` (or heredoc as above). Raw SQL pasted directly into bash will fail with `syntax error near unexpected token '('`.
- `--replace-existing` deletes current `irs527_reports` rows before rebuilding to remove bad legacy rows.

### Hourly Schedule E Catch-Up (independent expenditures)

Use this to fill missing federal independent expenditure rows (Schedule E) with resumable pagination.

One-off run:

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
source /srv/illinois_campaign_finance/shared/.env && export FEC_API_KEY

$PYTHON run.py backfill-fec-schedule-e \
  --cycle 2026 \
  --max-calls 1000 \
  --max-pages-per-candidate 25 \
  --refresh-cache
```

Schedule E wrapper script (recommended for automation):

```bash
cd /srv/illinois_campaign_finance/app
bash scripts/fec-schedule-e-catchup.sh
```

Install a dedicated Schedule E timer/service (sample unit files are in `docs/systemd/`):

```bash
cd /srv/illinois_campaign_finance/app
cp docs/systemd/il-campaign-fec-schedule-e-catchup.service /etc/systemd/system/
cp docs/systemd/il-campaign-fec-schedule-e-catchup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now il-campaign-fec-schedule-e-catchup.timer
systemctl list-timers il-campaign-fec-schedule-e-catchup.timer
journalctl -u il-campaign-fec-schedule-e-catchup.service --since today
```

Schedule A wrapper script (recommended for automation):

```bash
cd /srv/illinois_campaign_finance/app
bash scripts/fec-schedule-a-catchup.sh
```

Suggested systemd schedule while API limit is 1000/hour:

```ini
# /etc/systemd/system/il-campaign-fec-catchup.service
[Unit]
Description=Illinois Campaign Finance - FEC Schedule A catch-up
After=network-online.target

[Service]
Type=oneshot
User=app
WorkingDirectory=/srv/illinois_campaign_finance/app
ExecStart=/srv/illinois_campaign_finance/app/scripts/fec-schedule-a-catchup.sh
EnvironmentFile=/srv/illinois_campaign_finance/shared/.env
```

```ini
# /etc/systemd/system/il-campaign-fec-catchup.timer
[Unit]
Description=Run FEC Schedule A catch-up hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now il-campaign-fec-catchup.timer
systemctl list-timers il-campaign-fec-catchup.timer
journalctl -u il-campaign-fec-catchup.service --since today
```

**Note:** When sourcing `.env` manually, you must `export` variables for Python to see them. The `sync-fec.sh` script handles this automatically via `set -a`.

Check sync status:

```bash
# Timer status
systemctl list-timers il-campaign-fec-sync.timer

# Latest sync log
journalctl -u il-campaign-fec-sync.service --since today

# Or read log files directly
ls -lt /srv/illinois_campaign_finance/shared/logs/sync-fec-*.log | head -5
```

### ISBE Data (Manual)

ISBE has no public API. Download bulk files from the ISBE Campaign Disclosure website, then upload and import:

```bash
# From your local machine — upload bulk files to the server
scp -i ~/.ssh/hetzner_ed25519 *.txt root@178.156.162.56:/srv/illinois_campaign_finance/shared/downloads/

# SSH into the server
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56

# Import and rebuild analytics
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
$PYTHON run.py import-bulk-download --directory /srv/illinois_campaign_finance/shared/downloads/
$PYTHON run.py refresh-analytics --with-snapshot
```

### Setting Up the FEC Sync Timer (First Time)

Requires `FEC_API_KEY` in `/srv/illinois_campaign_finance/shared/.env`. Get a key at https://api.open.fec.gov/developers/.

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56

# Create the systemd service
cat > /etc/systemd/system/il-campaign-fec-sync.service << 'EOF'
[Unit]
Description=Illinois Campaign Finance - FEC weekly sync
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/srv/illinois_campaign_finance/app
ExecStart=/srv/illinois_campaign_finance/app/scripts/sync-fec.sh
EnvironmentFile=/srv/illinois_campaign_finance/shared/.env
EOF

# Create the timer
cat > /etc/systemd/system/il-campaign-fec-sync.timer << 'EOF'
[Unit]
Description=Run FEC sync every Sunday at 3 AM UTC

[Timer]
OnCalendar=Sun *-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Enable and start
systemctl daemon-reload
systemctl enable --now il-campaign-fec-sync.timer
```

## License

This project is for research and transparency purposes. Data sourced from the Illinois State Board of Elections and the Federal Elections Commission are public records.
