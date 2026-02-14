# Illinois Campaign Finance Intelligence Platform

A full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from both state and federal sources. The system ingests millions of contribution records, performs network analysis, anomaly detection, and donor intelligence across election cycles.

## Data Sources

- **Illinois State Board of Elections (ISBE)** — Committee filings, D-2 reports, itemized receipts, itemized expenditures, candidate/committee metadata via bulk TXT exports and web scraping
- **Federal Elections Commission (FEC)** — Illinois federal candidates, Schedule A contributions, Schedule B disbursements, Schedule E independent expenditures via the FEC API
- **IL Secretary of State Lobbying** — Active lobbying entities and their clients (625 entities, 3,748 clients, 14K entity-client pairs)
- **IRS 527 Political Organizations** — Organization registrations, periodic reports, directors, related organizations, expenditures, and election authority filings from IRS Form 8871/8872

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
- **SQLite** — Database (WAL mode, 57-table schema, optimized pragmas)
- **Click** — CLI command framework
- **pytest** — Test suite

## Quick Start

### Prerequisites
- Python 3.12+
- An FEC API key (optional, for federal data sync)

### Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Optional: configure environment
cp .env.example .env  # Add FEC_API_KEY, FLASK_SECRET_KEY, etc.
```

### Initialize and Load Data
```bash
# Create database schema
python run.py init-db

# Import ISBE bulk download files
python run.py import-bulk-download --directory Bulk_download

# Build analytics materialized views
python run.py refresh-analytics

# Sync federal (FEC) data for Illinois candidates
python run.py sync-fec-il-federal
python run.py rebuild-fec-donor-identities

# Import IL SOS lobbying data
python run.py import-lobbying --file Bulk_download/ILSOS_Lobbying_activeandclients/Active_Lobbying_Entities_and_Their_Clients_20260213.csv

# Import IRS 527 political org filings (IL-filtered by default)
python run.py import-irs527 --file Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt --illinois-only

# Run cross-matching across all data sources
python run.py run-cross-matching --only all
```

Compute-heavy workflow recommendation:
- For commands expected to take significant CPU time (for example `import-irs527` and `run-cross-matching --only all`), consider running on your local machine first.
- Prefer uploading resulting files or derived outputs to the server, and use server-side execution for steps that must run directly against production data.

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
├── scraper/                Playwright-based async scrapers
│   ├── main_list_scraper   ISBE main report list scraper
│   ├── committee_scraper   Committee detail + D-2 filing scraper
│   ├── detail_scraper      A-1 itemized contribution scraper
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
│   ├── routes/             15 route modules (dashboard, analytics, federal finance, lobbying, 527, etc.)
│   └── templates/          50+ Jinja2 templates (incl. tabbed detail views)
├── scripts/                Automation scripts
│   ├── sync-fec.sh         Weekly FEC data sync wrapper
│   ├── fec-schedule-b-catchup.sh  Hourly Schedule B backfill wrapper
│   └── fec-schedule-e-catchup.sh  Hourly Schedule E backfill wrapper
├── tests/                  pytest suite (20+ test modules)
├── docs/                   Data update guide, roadmaps, checklists
│   └── systemd/            Sample unit/timer files (including Schedule E catch-up)
├── Bulk_download/          ISBE bulk export TXT files
└── data/                   SQLite database
```

## Testing

```bash
pytest -q
```

If you want a faster pre-deploy gate, run this subset first:

```bash
pytest -q tests/test_federal_fec.py tests/test_lobbying_routes.py tests/test_uiux_improvements.py tests/test_webapp.py::TestWebApp::test_federal_finance_page_loads_with_synced_rows
```

For scraper/live checks, run integration tests separately:

```bash
pytest -q -m integration
```

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
| IRS 527 organizations | 17.5M lines (IL-filtered subset) |
| Database size | 5.5 GB+ |

## Configuration

Environment variables (set in `.env` or export directly):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_PATH` | `data/campaign_finance.db` | SQLite database path |
| `FEC_API_KEY` | *(empty)* | FEC API key for federal data sync |
| `APP_ENV` | `development` | Runtime environment (`development`, `staging`, `production`) |
| `FLASK_SECRET_KEY` | `dev-secret-key...` | Flask session secret |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `API_KEYS` | *(empty)* | Comma-separated API keys for `/api/*` |
| `API_REQUIRE_KEY` | `false` unless `API_KEYS` set | Require API key auth on `/api/*` |
| `API_RATE_LIMIT_PER_MINUTE` | `120` | Per-key/per-IP API request budget per minute |
| `SEARCH_MIN_QUERY_LENGTH` | `2` | Minimum search length for non-numeric queries |
| `SEARCH_MAX_QUERY_LENGTH` | `64` | Max search input length before truncation |
| `SEARCH_QUERY_TIMEOUT_MS` | `700` | Per-section SQLite timeout guardrail on `/search` |
| `SEARCH_SLOW_QUERY_MS` | `400` | Threshold for slow-search warning logs |
| `FEDERAL_VIEW_CACHE_ENABLED` | `true` | Enable snapshot caching for heavy federal views |
| `FEDERAL_OVERVIEW_CACHE_TTL_SECONDS` | `900` | Cache TTL for federal overview computations |
| `FEDERAL_NETWORKS_CACHE_TTL_SECONDS` | `600` | Cache TTL for federal network/overlap graphs |
| `FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS` | `600` | Cache TTL for donor segmentation/influence |
| `FEDERAL_MATCHING_CACHE_TTL_SECONDS` | `600` | Cache TTL for matching diagnostics |
| `LOCAL_DATA_STALE_DAYS` | `45` | Freshness warning threshold for local data |
| `FEDERAL_DATA_STALE_DAYS` | `14` | Freshness warning threshold for federal data |
| `RATE_LIMIT_RPM` | `30` | Scraper requests per minute |

## Server Deployment

The production site runs on a Hetzner VPS.

### Compute-Heavy Tasks (Run Locally First)

Default policy for expensive processing:
- If a command is expected to run for a long time or heavily use CPU, consider the local-machine path first.
- Run the compute-heavy step locally, validate outputs, then upload outputs/artifacts to production.
- Use server compute for these jobs only when the step must run directly against production-only data.
- For tasks that will run on the server, print an estimated runtime before execution (for example: expected duration range and whether it is CPU-heavy).

Examples:
- Often local-first: very large imports, full cross-matching (`run.py run-cross-matching --only all`), one-off backfills.
- Usually server-side: lightweight deploy tasks (`git pull`, `systemctl restart ilcf-web.service`), quick targeted commands.

### SSH Access

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56
```

### Server File Paths

```
/srv/illinois_campaign_finance/
├── app/                        Application code (git checkout)
│   ├── run.py                  CLI entry point
│   ├── webapp/                 Flask app
│   ├── scripts/
│   │   └── sync-fec.sh         FEC sync automation
│   ├── data/
│   │   └── campaign_finance.db SQLite database
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
sqlite3 /srv/illinois_campaign_finance/shared/data/campaign_finance.db \
  \".backup '/srv/illinois_campaign_finance/shared/data/campaign_finance_$(date +%F_%H%M%S).bak'\"

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
sqlite3 /srv/illinois_campaign_finance/shared/data/campaign_finance.db <<'SQL'
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
- Run SQL inside `sqlite3` (or heredoc as above). Raw SQL pasted directly into bash will fail with `syntax error near unexpected token '('`.
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
