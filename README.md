# Illinois Campaign Finance Intelligence Platform

A full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from both state and federal sources. The system ingests millions of contribution records, performs network analysis, anomaly detection, and donor intelligence across election cycles.

## Data Sources

- **Illinois State Board of Elections (ISBE)** — Committee filings, D-2 reports, itemized receipts, candidate/committee metadata via bulk TXT exports and web scraping
- **Federal Elections Commission (FEC)** — Illinois federal candidates, Schedule A contributions, and committee linkages via the FEC API

## Features

### Data Pipeline
- Async Playwright-based web scraping with ASP.NET postback/ViewState handling
- Resumable scrape state management with configurable rate limiting and exponential backoff
- Bulk CSV/TXT ingestion (990MB+ receipt files) with chunked loading
- Duplicate detection via source identifiers and amendment-safe aggregation
- Raw extraction staging for full audit trails

### Analytics
- **Network Analysis** — Weighted donor-committee-candidate graphs with degree centrality and Louvain community detection
- **Anomaly Detection** — Large contribution flags, monthly spike detection, high donor concentration risk (HHI-based)
- **Risk Explainability** — Per-flag explainability payloads (rule, baseline, threshold, percentile context)
- **Concentration Metrics** — Herfindahl-Hirschman Index (HHI), Gini coefficient, top-N donor share
- **Time-Series Intelligence** — Monthly aggregation, 3-month moving averages, month-over-month change
- **Geographic Analysis** — State and city-level donor aggregation from parsed addresses
- **NLP Categorization** — Keyword-based expenditure classification
- **Entity Resolution** — Jaccard similarity candidate matching across state/federal records

### Web Application
- Flask web UI with sortable/filterable tables, pagination, and CSV/JSON export
- Expanded global search across committees, donors, candidates, reports, filed-doc IDs, and donor keys
- Candidate/committee side-by-side compare mode with trend overlays and donor overlap summaries
- Row-level provenance panels on key tables (source table, sync timing, normalization notes, backlinks)
- Interactive dashboards with visual summaries for trends, geography, and risk/anomaly distributions
- Federal-state cross-reference views and donor overlap analysis
- Session authentication for manual data entry

## Tech Stack

- **Python 3.12** — Core language
- **Flask 3.0** — Web framework (Jinja2 templates)
- **Playwright** — Async browser automation for scraping
- **SQLite** — Database (WAL mode, 38-table schema, optimized pragmas)
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
```

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
│   ├── schema.sql          38-table schema definition
│   ├── models.py           Dataclass-based ORM models
│   ├── analytics.py        Network, anomaly, concentration, time-series, geo, NLP
│   ├── bulk_download_loader Bulk TXT import and normalization
│   └── federal_fec.py      FEC API integration and candidate matching
├── webapp/                 Flask web application
│   ├── routes/             12 route modules (dashboard, analytics, API, etc.)
│   └── templates/          36 Jinja2 templates
├── scripts/                Automation scripts
│   └── sync-fec.sh         Weekly FEC data sync wrapper
├── tests/                  pytest suite (11 test modules)
├── docs/                   Data update guide, roadmaps, checklists
├── Bulk_download/          ISBE bulk export TXT files
└── data/                   SQLite database
```

## Testing

```bash
pytest
```

## Current Data Scale

| Dataset | Records |
|---|---|
| State candidates | 32,212 |
| State committees | 33,760 |
| Itemized receipts | 6.4M+ |
| Federal candidates (IL) | 134 |
| Federal contributions | 20,261 |
| Database size | 5.5 GB |

## Configuration

Environment variables (set in `.env` or export directly):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_PATH` | `data/campaign_finance.db` | SQLite database path |
| `FEC_API_KEY` | *(empty)* | FEC API key for federal data sync |
| `FLASK_SECRET_KEY` | `dev-secret-key...` | Flask session secret |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `RATE_LIMIT_RPM` | `30` | Scraper requests per minute |

## Server Deployment

The production site runs on a Hetzner VPS.

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
├── venv/                       Python virtual environment
│   └── bin/python              Used by sync-fec.sh and systemd
└── shared/                     Persistent data across deploys
    ├── .env                    Environment variables (FEC_API_KEY, etc.)
    ├── logs/                   Sync and task logs
    │   └── sync-fec-*.log      Timestamped FEC sync logs
    └── downloads/              Staging area for ISBE bulk files
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

# Re-install deps if requirements.txt changed
/srv/illinois_campaign_finance/shared/venv/bin/pip install -r requirements.txt

# Restart the web application
systemctl restart ilcf-web

# Rebuild analytics materialized views + snapshot cache (recommended after UI/analytics changes)
/srv/illinois_campaign_finance/shared/venv/bin/python run.py refresh-analytics --with-snapshot
```

### Key Web Routes

| Route | Description |
|-------|-------------|
| `/` | Dashboard overview with stats, freshness, launch paths |
| `/search` | Global search across entities (committees, donors, candidates, reports, filed docs, donor keys) |
| `/compare` | Candidate-vs-candidate or committee-vs-committee trend and overlap comparison |
| `/candidates` | Unified candidates page — state (ISBE) and federal (FEC) |
| `/candidate-finance` | State candidate finance detail (ISBE data) |
| `/federal-finance` | Federal candidate finance detail (FEC data) |
| `/analytics` | Network, anomaly, concentration, and geographic analytics |
| `/analytics/risk` | Risk flags with explainability and distribution visualizations |
| `/donors` | Cross-committee donor directory |

### Mobile Smoke Check

Use this to quickly validate mobile rendering across key routes and catch horizontal overflow regressions.

```bash
cd /srv/illinois_campaign_finance/app
/srv/illinois_campaign_finance/shared/venv/bin/python scripts/mobile_smoke_check.py --base-url http://127.0.0.1:5000
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
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python
source /srv/illinois_campaign_finance/shared/.env && export FEC_API_KEY

$PYTHON run.py sync-fec-il-federal --cycle 2026 --contributor-state IL --max-calls 900
$PYTHON run.py rebuild-fec-donor-identities
$PYTHON run.py refresh-analytics --with-snapshot
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
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python
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
