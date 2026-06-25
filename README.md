<h1 align="center">Follow The Money IL</h1>
<p align="center">An end-to-end campaign-finance intelligence platform for Illinois state and federal political money.</p>

<p align="center">
  <a href="https://www.followthemoneyil.com"><img alt="Live demo" src="https://img.shields.io/badge/live-followthemoneyil.com-2ea44f?style=flat"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white">
  <img alt="Flask" src="https://img.shields.io/badge/Flask-3.0-000000?style=flat&logo=flask&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat&logo=postgresql&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/Playwright-2EAD33?style=flat&logo=playwright&logoColor=white">
  <img alt="OpenFEC" src="https://img.shields.io/badge/OpenFEC-API-005ea2?style=flat">
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat">
</p>

## Overview

Follow The Money IL aggregates, normalizes, and analyzes Illinois campaign-finance data from both state and federal sources, making the flow of political money searchable and explorable in one place. It ingests Illinois State Board of Elections bulk filings alongside federal Schedule A/B/E data from the OpenFEC API, then layers on entity resolution, network analysis, and donor intelligence across election cycles. The platform tracks **6.4M+ itemized receipt rows**, **530K+ D-2 filing totals**, and **1.3M+ donor-analytics rows**, and is deployed in production at [followthemoneyil.com](https://www.followthemoneyil.com) with scheduled syncs that keep federal and analytics data current.

## Features

- **Multi-source ingestion** — Illinois State Board of Elections (ISBE) committee filings, D-2 reports, and itemized receipts/expenditures; federal candidate, contribution, disbursement, and independent-expenditure data via the OpenFEC API; plus IL Secretary of State lobbying, IRS 527 political-organization filings, City of Chicago open data, and OpenBook Comptroller contracts.
- **Resilient scraping pipeline** — Async Playwright scraping with ASP.NET postback/ViewState handling, resumable scrape state, configurable rate limiting, and exponential backoff.
- **Bulk ETL** — Chunked loading of large CSV/TXT bulk exports with amendment-safe aggregation, duplicate detection by source identifiers, and quarantining of malformed rows.
- **Entity resolution & cross-matching** — Jaccard-similarity matching across state and federal records, plus a cross-matching engine that links lobbying entities, 527 directors and organizations, donors, committees, and candidates.
- **Analytics** — Network/graph analysis, anomaly detection (large-contribution and monthly-spike flags), concentration metrics (HHI, Gini, top-N donor share), time-series aggregation, geographic donor rollups, and per-flag risk explainability.
- **Interactive web app** — Flask UI with sortable/filterable tables, global period filtering, candidate/committee drilldowns, side-by-side compare mode, money-flow and network visualizations, a dark-money tracker, and unified cross-dataset person search, with CSV/JSON export throughout.
- **Production-ready API surface** — Optional API-key auth, per-key rate limiting, and Redis-backed route caching for heavy aggregate views.

## Tech stack

- **Python 3.12** — core language
- **Flask 3 + Jinja2** — web framework and templating
- **PostgreSQL 16** — primary database (local and production)
- **Playwright** — async browser automation for scraping
- **OpenFEC API** — federal campaign-finance data
- **Click** — CLI command framework
- **NetworkX** — graph/network analysis
- **Redis** — shared route cache (with in-process fallback)
- **pandas / NumPy / Matplotlib** — data processing and visualization
- **Gunicorn** — WSGI server in production
- **pytest** — test suite

## How it works

```
  Ingest                Parse / normalize           Store              Serve
  ──────                ─────────────────           ─────              ─────
  ISBE bulk filings  ┐                          ┌── PostgreSQL ──┐
  OpenFEC API        ├─► chunked ETL + async ───┤   normalized   ├─► Flask web UI
  IL SOS lobbying    │   Playwright scraping,    │   tables +     │   + JSON/CSV API
  IRS 527 filings    │   entity resolution,      │   analytics    │   (Redis-cached
  Chicago open data  │   cross-matching          │   rollups      │    heavy views)
  OpenBook contracts ┘                          └────────────────┘
```

1. **Ingest** — Bulk ISBE exports are downloaded and staged; federal data is pulled from the OpenFEC API; lobbying, 527, Chicago, and OpenBook sources are scraped or pulled via their respective APIs.
2. **Parse / normalize** — Raw records are cleaned, deduplicated (amendment-safe), and resolved into canonical donor, committee, and candidate entities.
3. **Store** — Normalized records and precomputed analytics rollups are written to PostgreSQL.
4. **Serve** — A Flask application exposes search, drilldowns, network visualizations, and a JSON/CSV API, with Redis caching for compute-heavy aggregate routes.

Scheduled jobs keep the data fresh: a recurring sync refreshes federal (FEC) contributions, rebuilds donor identities, and rebuilds analytics rollups.

## Getting started

### Prerequisites

- Python 3.12+
- PostgreSQL 16+
- An OpenFEC API key (optional, for federal data sync — free from [api.open.fec.gov](https://api.open.fec.gov/developers/))

### Install

```bash
git clone https://github.com/Doommen3/Illinois_campaign_finance.git
cd Illinois_campaign_finance

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Configure

```bash
cp .env.example .env
# Set DATABASE_URL, FEC_API_KEY, and FLASK_SECRET_KEY in .env

createdb ilcf
export DATABASE_URL=postgresql://$(whoami)@localhost/ilcf
```

`DATABASE_URL` is required in all environments. (A legacy `DATABASE_PATH` SQLite setting exists for backward compatibility but is deprecated and unused for new setups.)

### Initialize and run

```bash
# Create the database schema
python run.py init-db

# Sync federal (FEC) data for Illinois candidates
python run.py sync-fec-il-federal
python run.py rebuild-fec-donor-identities

# Build analytics rollups
python run.py refresh-analytics

# Start the development web server
python run.py runserver --port 5000
```

In production the app is served via Gunicorn against the WSGI entry point (`wsgi.py`). Run `python run.py --help` to see the full set of ingestion, scraping, and maintenance commands.

### Tests

```bash
pytest -q
```

## Author

**Devin Oommen** — [devinoommen.com](https://devinoommen.com) · Oommen & Company

## License

Released under the [MIT License](LICENSE).
