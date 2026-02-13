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
- **Concentration Metrics** — Herfindahl-Hirschman Index (HHI), Gini coefficient, top-N donor share
- **Time-Series Intelligence** — Monthly aggregation, 3-month moving averages, month-over-month change
- **Geographic Analysis** — State and city-level donor aggregation from parsed addresses
- **NLP Categorization** — Keyword-based expenditure classification
- **Entity Resolution** — Jaccard similarity candidate matching across state/federal records

### Web Application
- Flask web UI with sortable/filterable tables, pagination, and CSV/JSON export
- Interactive dashboards for donor networks, anomaly flags, geographic distribution, and candidate finance rollups
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

# Scrape committee detail pages
python run.py scrape-committee-reports --batch-size 20 --filed-cutoff 2025-06-01

# Scrape D-2 details and itemized contributions
python run.py scrape-d2-details --batch-size 20
python run.py scrape-d2-itemized --batch-size 50

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
│   ├── routes/             11 route modules (dashboard, analytics, API, etc.)
│   └── templates/          34 Jinja2 templates
├── tests/                  pytest suite (11 test modules)
├── docs/                   Internal roadmaps and checklists
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

## License

This project is for research and transparency purposes. Data sourced from the Illinois State Board of Elections and the Federal Elections Commission are public records.
