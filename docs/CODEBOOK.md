# Illinois Campaign Finance Intelligence Platform - Codebook

> Comprehensive technical reference for the Illinois Campaign Finance Intelligence Platform.
> Last updated: 2026-04-25

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Data Sources & Pipeline](#3-data-sources--pipeline)
4. [Database Schema Reference](#4-database-schema-reference)
5. [CLI Command Reference](#5-cli-command-reference)
6. [Web Application Routes](#6-web-application-routes)
7. [Cross-Matching Engine](#7-cross-matching-engine)
8. [Analytics Engine](#8-analytics-engine)
9. [Configuration Reference](#9-configuration-reference)
10. [Testing](#10-testing)
11. [Deployment & Operations](#11-deployment--operations)
12. [Data Refresh Workflows](#12-data-refresh-workflows)
13. [Glossary](#13-glossary)

---

## 1. Project Overview

A full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from multiple authoritative sources:

- **Illinois State Board of Elections (ISBE)** - State-level committees, candidates, receipts, expenditures
- **Federal Election Commission (FEC)** - Federal candidates, Schedule A/B/E filings
- **Illinois Secretary of State** - Lobbying entities, clients, registrations
- **IRS Form 990/527** - Political organization filings, directors, dark money flows
- **City of Chicago (Socrata)** - Contracts, payments, lobbyist contributions
- **OpenBook Illinois Comptroller** - State vendor contracts and employee campaign contributions

The platform provides network analysis, anomaly detection, geographic concentration metrics, cross-dataset entity matching, and interactive visualizations to surface money-in-politics patterns.

**Scale**: ~200K lines of code, 53+ database tables, 5.5GB+ PostgreSQL database, 6.4M+ receipt records, 32K+ state candidates.

---

## 2. Architecture

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask 3.0 |
| Database | PostgreSQL 16 |
| Frontend | Jinja2 templates, vanilla JS, CSS |
| Scraping | Playwright (async), HTTP/requests |
| CLI | Click framework |
| Graph Analysis | NetworkX |
| Testing | pytest (45+ test modules) |

### Directory Structure

```
Illinois_campaign_finance/
├── cli/
│   └── commands.py              # 36+ Click CLI commands
├── database/
│   ├── schema.sql               # DDL (1530 lines, 53+ tables)
│   ├── models.py                # Dataclass ORM models
│   ├── connection.py            # PostgreSQL connection management
│   ├── analytics.py             # Materialized views & analytics (6867 lines)
│   ├── cross_matching.py        # Jaccard entity matching engine (2505 lines)
│   ├── federal_fec.py           # FEC API integration
│   ├── bulk_download_loader.py  # ISBE bulk TXT importer
│   ├── irs527_loader.py         # IRS 527 FullDataFile parser
│   ├── lobbying_loader.py       # IL SOS lobbying CSV loader
│   ├── chicago_loader.py        # Socrata API loader
│   ├── local_donor_entities.py  # Donor entity resolution
│   ├── donor_prospecting.py     # Prospect discovery queries
│   ├── maintenance.py           # Data cleanup utilities
│   ├── identifiers.py           # Stable ID generation
│   └── pg_compat.py             # PostgreSQL compatibility layer
├── webapp/
│   ├── app.py                   # Flask app factory
│   ├── routes/                  # 16 route blueprint modules
│   │   ├── main.py              # Dashboard, search, person intelligence
│   │   ├── admin.py             # Admin hub, audit, donor merges
│   │   ├── analytics.py         # Network, risk, geography, relationships
│   │   ├── api.py               # 20+ JSON API endpoints
│   │   ├── candidate_finance.py # State candidate finance drilldowns
│   │   ├── committees.py        # Committee directory & detail
│   │   ├── donors.py            # Donor directory & detail
│   │   ├── federal_finance.py   # 16 federal finance routes
│   │   ├── irs527.py            # 527 orgs, dark money tracker
│   │   ├── lobbying.py          # Lobbying entities, clients, flows
│   │   ├── openbook.py          # Comptroller vendor data
│   │   ├── experimental.py      # Viz lab prototypes
│   │   └── auth.py              # Login/logout
│   ├── templates/               # 50+ Jinja2 templates
│   └── static/                  # JS, CSS, images
├── scraper/
│   ├── openbook_scraper.py      # OpenBook Comptroller scraper
│   └── comptroller_contracts.py # State contracts DataTables scraper
├── scripts/
│   ├── isbe_sunshine_etl.py     # ISBE bulk ETL (PostgreSQL-native)
│   ├── build_geometry.py        # TIGER/Line district TopoJSON builder
│   ├── validate_geometry_keys.py # Geometry join key validator
│   ├── swap_bulk_to_isbe.py     # Legacy compat view builder
│   └── triple_pipeline_viz.py   # Triple pipeline visualization
├── tests/                       # 45+ pytest modules
├── docs/                        # Runbooks, plans, case studies
├── data/                        # Geometry files, legacy data
├── config.py                    # Centralized configuration
├── run.py                       # CLI entry point
├── wsgi.py                      # WSGI entry point
└── requirements.txt             # Python dependencies
```

### Request Flow

```
Browser → Flask (wsgi.py)
       → Route Blueprint (webapp/routes/*.py)
       → Database Query (database/*.py, PostgreSQL)
       → Jinja2 Template (webapp/templates/*.html)
       → Response
```

### Data Pipeline Flow

```
Source (ISBE/FEC/IRS/SOS/Socrata/OpenBook)
  → CLI Command (cli/commands.py)
  → Loader Module (database/*_loader.py)
  → PostgreSQL Tables (database/schema.sql)
  → Cross-Matching (database/cross_matching.py)
  → Analytics Materialization (database/analytics.py)
  → Web Visualization (webapp/)
```

---

## 3. Data Sources & Pipeline

### 3.1 ISBE (Illinois State Board of Elections)

**What it contains**: State-level committees, candidates, receipts, expenditures, D-2 quarterly reports, filed documents, candidacy records, officers, investments.

**Import method**: Bulk TXT files downloaded from ISBE, loaded via `isbe_sunshine_etl.py`.

**Tables populated**: `isbe_committees`, `isbe_candidates`, `isbe_candidacies`, `isbe_receipts`, `isbe_expenditures`, `isbe_d2_reports`, `isbe_filed_docs`, `isbe_officers`, `isbe_prev_officers`, `isbe_investments`, `isbe_cmte_candidate_links`, `isbe_cmte_officer_links`

**Materialized views**: `isbe_condensed_receipts`, `isbe_condensed_expenditures` (deduplicate amended filings), `isbe_committee_money`, `isbe_candidate_money`

**CLI**:
```bash
python run.py sunshine-import --bulk-dir Bulk_download
```

**Update frequency**: Weekly during filing season, otherwise biweekly.

### 3.2 FEC (Federal Election Commission)

**What it contains**: Illinois federal candidates, Schedule A (individual contributions), Schedule B (disbursements), Schedule E (independent expenditures), committee financials.

**Import method**: FEC API with paginated backfill and resumable state.

**Tables populated**: `fec_il_candidate_seed`, `fec_candidate_match`, `fec_candidate_committees`, `fec_schedule_a_contributions`, `fec_schedule_b_disbursements`, `fec_schedule_e_independent_expenditures`, `fec_candidate_cycle_totals`, `fec_local_donor_matches`

**CLI**:
```bash
python run.py sync-fec-il-federal          # Main sync
python run.py backfill-fec-schedule-a      # Schedule A gaps
python run.py backfill-fec-schedule-b      # Schedule B gaps
python run.py backfill-fec-schedule-e      # Schedule E gaps
```

**Requires**: `FEC_API_KEY` environment variable.

**Update frequency**: Weekly (main sync), monthly (backfills).

### 3.3 IL Secretary of State - Lobbying

**What it contains**: Lobbying entities (firms), clients, individual lobbyists, registration relationships.

**Import method**: CSV daily extract from IL SOS.

**Tables populated**: `lobbying_entities`, `lobbying_clients`, `lobbying_lobbyists`, `lobbying_lobbyist_registrations`, `lobbying_entity_clients`

**CLI**:
```bash
python run.py import-lobbying --file Bulk_download/Lobbyist_Entity_Client_Data_Daily_YYYYMMDD.csv
```

**Update frequency**: Weekly (when new daily extract is published).

### 3.4 IRS 527 Political Organizations

**What it contains**: 527 organization registrations (Form 8871), periodic reports (Form 8872), directors/officers, related organizations, contributions (who donates TO 527s), expenditures.

**Import method**: IRS FullDataFile (pipe-delimited), filtered to Illinois.

**Tables populated**: `irs527_organizations`, `irs527_reports`, `irs527_directors`, `irs527_related_orgs`, `irs527_contributions`, `irs527_expenditures`, `irs527_election_authority`, `irs527_contribution_rollup`, `irs527_contributor_rollup`

**CLI**:
```bash
python run.py import-irs527 --file Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt --illinois-only
```

**Update frequency**: Monthly (IRS updates infrequently).

### 3.5 City of Chicago (Socrata Open Data)

**What it contains**: City contracts, payments, lobbyist contributions to officials, lobbying activity logs.

**Import method**: Socrata API with upsert support.

**Tables populated**: `chicago_contracts_raw`, `chicago_payments_raw`, `chicago_lobbyist_contributions_raw`, `chicago_lobbying_activity_raw`

**CLI**:
```bash
python run.py import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert
```

**Update frequency**: Monthly.

### 3.6 OpenBook Illinois Comptroller

**What it contains**: State vendor contracts, contract warrants (payments), vendor employee campaign contributions.

**Import method**: HTTP autosuggest API + search result scraping with smart search term generation.

**Tables populated**: `openbook_vendor_seed`, `openbook_vendor_match`, `openbook_contracts_raw`, `openbook_contract_warrants`, `openbook_contract_detail_status`, `openbook_contributions_raw`, `openbook_scrape_runs`

**CLI**:
```bash
python run.py import-openbook-batch --generate-seeds --max-vendors 20
```

**Update frequency**: As needed.

### 3.7 Census TIGER/Line Geometry

**What it contains**: Illinois congressional district, state house, and state senate boundary geometries as TopoJSON.

**Build**:
```bash
python3 scripts/build_geometry.py
python3 scripts/validate_geometry_keys.py
```

**Output**: `data/geometry/il/*.topo.json`

---

## 4. Database Schema Reference

PostgreSQL 16 database (`ilcf`). Schema defined in `database/schema.sql`.

### 4.1 ISBE State Tables

| Table | Description | Key Columns |
|-------|-------------|-------------|
| `isbe_committees` | Campaign committees | `id`, `name`, `type_desc`, `status`, `address`, `city`, `state`, `zip` |
| `isbe_candidates` | State candidates | `id`, `first_name`, `last_name`, `district_type`, `district`, `office` |
| `isbe_candidacies` | Candidate-election links | `id`, `candidate_id`, `election_type`, `election_year`, `race` |
| `isbe_receipts` | Contribution receipts | `id`, `committee_id`, `filed_doc_id`, `amount`, `received_date`, `last_name`, `first_name`, `d2_part` |
| `isbe_expenditures` | Committee expenditures | `id`, `committee_id`, `filed_doc_id`, `amount`, `expended_date`, `last_name`, `purpose` |
| `isbe_d2_reports` | D-2 quarterly reports | `id`, `committee_id`, `filed_doc_id`, `reporting_period_from/to`, `archived` |
| `isbe_filed_docs` | Filed documents | `id`, `committee_id`, `doc_name`, `filed_date` |
| `isbe_officers` | Current committee officers | `id`, `committee_id`, `first_name`, `last_name`, `title` |
| `isbe_prev_officers` | Previous officers | `id`, `committee_id`, `first_name`, `last_name`, `title` |
| `isbe_investments` | Committee investments | `id`, `committee_id`, `description`, `amount` |
| `isbe_cmte_candidate_links` | Committee-candidate relationships | `committee_id`, `candidate_id` |
| `isbe_cmte_officer_links` | Committee-officer relationships | `committee_id`, `officer_id` |

**Materialized Views**:
- `isbe_condensed_receipts` - Deduplicated receipts (filters amended filings)
- `isbe_condensed_expenditures` - Deduplicated expenditures
- `isbe_committee_money` - Committee financial summaries
- `isbe_candidate_money` - Candidate financial summaries

### 4.2 FEC Federal Tables

| Table | Description | Key Columns |
|-------|-------------|-------------|
| `fec_il_candidate_seed` | Illinois federal candidate seeds | `candidate_id`, `name`, `office`, `district`, `party`, `election_year` |
| `fec_candidate_match` | Seed-to-FEC ID matches | `seed_id`, `fec_candidate_id`, `match_score` |
| `fec_candidate_committees` | Committees by candidate/cycle | `candidate_id`, `committee_id`, `designation`, `cycle` |
| `fec_schedule_a_contributions` | Individual contributions | `sub_id`, `committee_id`, `contributor_name`, `contributor_city/state/zip`, `contribution_receipt_amount`, `contribution_receipt_date` |
| `fec_schedule_b_disbursements` | Committee disbursements | `sub_id`, `committee_id`, `recipient_name`, `disbursement_amount`, `disbursement_date` |
| `fec_schedule_e_independent_expenditures` | IE spending | `sub_id`, `committee_id`, `candidate_id`, `payee_name`, `expenditure_amount`, `expenditure_date`, `support_oppose_indicator` |
| `fec_candidate_cycle_totals` | Reported totals | `candidate_id`, `cycle`, `total_receipts`, `total_disbursements`, `cash_on_hand` |
| `fec_local_donor_matches` | Federal-local donor overlaps | `federal_donor_key`, `local_donor_key`, `match_score` |
| `fec_schedule_a/b/e_backfill_state` | Pagination resume state | `committee_id`, `cycle`, `last_index`, `page_count` |

### 4.3 Lobbying Tables

| Table | Description | Key Columns |
|-------|-------------|-------------|
| `lobbying_entities` | Lobbying firms/individuals | `id`, `name`, `address`, `city`, `state`, `postal` |
| `lobbying_clients` | Lobbying clients | `id`, `name`, `address`, `city`, `state`, `postal`, `client_status` |
| `lobbying_lobbyists` | Individual lobbyists | `id`, `first_name`, `last_name`, `status` |
| `lobbying_lobbyist_registrations` | Lobbyist-entity-client registrations | `id`, `lobbyist_id`, `entity_id`, `client_id`, `registration_year` |
| `lobbying_entity_clients` | Entity-client associations | `entity_id`, `client_id` |

### 4.4 IRS 527 Tables

| Table | Description | Key Columns |
|-------|-------------|-------------|
| `irs527_organizations` | 527 org registrations (Form 8871) | `ein`, `org_name`, `state`, `purpose`, `material_change_date` |
| `irs527_reports` | Periodic reports (Form 8872) | `id`, `ein`, `period_begin/end`, `total_contributions`, `total_expenditures` |
| `irs527_directors` | Org directors/officers | `id`, `ein`, `name`, `title`, `address`, `city`, `state`, `zip` |
| `irs527_related_orgs` | Related organization links | `id`, `ein`, `related_org_name`, `relationship` |
| `irs527_contributions` | Contributions TO 527 orgs | `id`, `ein`, `contributor_name`, `amount`, `date`, `employer`, `occupation` |
| `irs527_expenditures` | 527 expenditures | `id`, `ein`, `recipient_name`, `amount`, `date`, `purpose` |
| `irs527_election_authority` | Election authority registrations | `id`, `ein`, `state`, `authority_name` |
| `irs527_contribution_rollup` | Pre-aggregated org totals | `ein`, `total_contributions`, `contributor_count` |
| `irs527_contributor_rollup` | Pre-aggregated contributor totals | `contributor_name`, `total_amount`, `org_count` |

### 4.5 Chicago / Socrata Tables

| Table | Description | Key Columns |
|-------|-------------|-------------|
| `chicago_contracts_raw` | City contracts | `id`, `vendor_name`, `amount`, `start_date`, `end_date`, `department` |
| `chicago_payments_raw` | Contract payments | `id`, `contract_id`, `amount`, `payment_date` |
| `chicago_lobbyist_contributions_raw` | Lobbyist contributions | `id`, `lobbyist_name`, `recipient_name`, `amount`, `date` |
| `chicago_lobbying_activity_raw` | Lobbying activity | `id`, `lobbyist_name`, `client_name`, `action` |

### 4.6 OpenBook Comptroller Tables

| Table | Description | Key Columns |
|-------|-------------|-------------|
| `openbook_vendor_seed` | Seed vendor names from other datasets | `id`, `vendor_name`, `source`, `source_id`, `seed_amount` |
| `openbook_vendor_match` | Autosuggest API matches | `id`, `seed_id`, `openbook_vendor_key`, `vendor_name`, `confidence`, `search_term_used` |
| `openbook_contracts_raw` | Contract search results | `id`, `vendor_key`, `agency`, `amount`, `start_date`, `end_date` |
| `openbook_contract_warrants` | Payment warrants | `id`, `contract_id`, `amount`, `warrant_date` |
| `openbook_contract_detail_status` | Scrape completion tracking | `contract_id`, `status`, `error_count` |
| `openbook_contributions_raw` | Employee campaign contributions | `id`, `vendor_key`, `contributor_name`, `recipient_name`, `amount`, `date` |
| `openbook_scrape_runs` | Batch run metadata | `id`, `started_at`, `completed_at`, `vendors_processed`, `status` |

### 4.7 Analytics Tables

| Table | Description |
|-------|-------------|
| `analytics_donor_committee_agg` | Pre-aggregated donor-committee contribution totals |
| `analytics_committee_monthly_totals` | Monthly committee financial totals |
| `analytics_large_contributions` | Flagged large contribution rows |
| `analytics_donor_summary` | Cross-source donor summary (state + federal) |
| `analytics_materialized_meta` | Materialization metadata and versioning |
| `analytics_snapshots` | Dashboard cache snapshots (JSON) |
| `donor_entity_local` | Local donor entity resolution groups |
| `donor_entity_local_member` | Entity membership with confidence tiers |

### 4.8 Cross-Matching Result Tables

| Table | Match Type | Threshold |
|-------|-----------|-----------|
| `lobbying_donor_matches` | Lobbying client → donor | Jaccard 0.80 |
| `lobbying_expenditure_matches` | Lobbying client/entity → expenditure payee | Jaccard 0.80 |
| `irs527_committee_matches` | 527 org → IL committee | Jaccard 0.80 |
| `irs527_expenditure_recipient_matches` | 527 expenditure → committee/candidate | Jaccard 0.80 |
| `irs527_director_donor_matches` | 527 director → donor (exhaustive) | Jaccard 0.80 |
| `irs527_director_candidate_matches` | 527 director → state/federal candidate | Jaccard 0.80 |
| `irs527_director_address_matches` | 527 director → donor (address proximity) | 0.50 |
| `irs527_org_address_matches` | 527 org address → committee/donor | Address scoring |
| `lobbying_527_matches` | Lobbying client → 527 org | Jaccard 0.80 |
| `cross_matching_donor_address_index` | Donor address index for fast-path matching | N/A |

---

## 5. CLI Command Reference

Entry point: `python run.py <command>`

### Database Management

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `init-db` | Initialize database schema | |
| `create-user` | Create manual-entry web user | `--username`, `--password` |
| `clean-data` | Clean garbage committees, normalize donors | |

### Data Import - State (ISBE)

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `sunshine-import` | PostgreSQL-native ISBE bulk import | `--download`, `--bulk-dir`, `--skip-compat-swap` |
| `import-bulk-download` | Legacy ISBE bulk import | `--file` |

### Data Import - Federal (FEC)

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `sync-fec-il-federal` | Sync IL federal candidates & contributions | `--api-key`, `--refresh-local-matches` |
| `backfill-fec-schedule-a` | Backfill Schedule A (receipts) | `--cycle`, `--limit` |
| `backfill-fec-schedule-b` | Backfill Schedule B (disbursements) | `--cycle`, `--limit` |
| `backfill-fec-schedule-e` | Backfill Schedule E (IEs) | `--cycle` |
| `refresh-fec-transfer-committees` | Identify transfer-source committees | |
| `sync-fec-transfer-committee-receipts` | Pull transfer committee receipts | |
| `refresh-fec-local-donor-matches` | Match federal donors to state donors | |
| `rebuild-fec-donor-identities` | Rebuild FEC donor entity keys | |

### Data Import - Other Sources

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `import-lobbying` | Import IL SOS lobbying CSV | `--file` |
| `import-irs527` | Import IRS 527 FullDataFile | `--file`, `--illinois-only` |
| `import-chicago-phase1` | Import Chicago Socrata data | `--app-token`, `--upsert` |
| `import-openbook-batch` | Batch OpenBook vendor resolution | `--generate-seeds`, `--max-vendors`, `--seed-source`, `--seed-like` |

### Analytics & Matching

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `refresh-analytics` | Build/refresh materialized views | `--with-snapshot` |
| `run-cross-matching` | Execute cross-matching jobs | `--only all` |
| `rebuild-local-donor-entities` | Rebuild donor entity resolution | |

### Scraping

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `scrape-main` | Scrape ISBE main reports list | `--start-page`, `--end-page` |
| `scrape-details` | Scrape A-1 contribution details | `--batch-size` |
| `scrape-committee-reports` | Scrape committee detail pages | |
| `scrape-d2-details` | Scrape D-2 summaries | |
| `scrape-d2-itemized` | Scrape D-2 itemized rows | |
| `scrape-d2-all-pending` | Scrape all pending D-2 data | |
| `scrape-status` | Check scraper progress | |

### Repair & Maintenance

| Command | Description |
|---------|-------------|
| `repair-irs527-reports` | Rebuild 527 report totals from 8872 rows |
| `repair-irs527-contributions` | Rebuild 527 contribution records |

### Development

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `runserver` | Launch Flask dev server | `--port` (default: 5000) |

---

## 6. Web Application Routes

Base URL: `http://localhost:5000` (dev) or production server.

### 6.1 Main Pages

| Route | Description |
|-------|-------------|
| `GET /` | Dashboard with stats, freshness indicators, quick navigation |
| `GET /about` | About page |
| `GET /candidates` | Unified candidate list (state + federal) |
| `GET /search?q=<query>&type=<type>&period=<period>` | Global search (committees, donors, candidates, reports, filed docs) |
| `GET /person-intelligence?q=<name>` | Cross-dataset person lookup |
| `GET /live-feed` | Real-time contribution/disbursement feed |
| `GET /compare` | Candidate/committee comparison tool |

### 6.2 Analytics

| Route | Description |
|-------|-------------|
| `GET /analytics/` | Analytics hub |
| `GET /analytics/overview` | Summary metrics |
| `GET /analytics/networks` | Interactive network graphs (force layout) |
| `GET /analytics/risk` | Risk flags, anomaly detection |
| `GET /analytics/donors` | Donor directory with anomaly highlighting |
| `GET /analytics/geography` | Geographic concentration analysis |
| `GET /analytics/geo-drilldown` | Paginated geo detail rows |
| `GET /analytics/relationships` | Co-giving, committee similarity, lobbying/527 flows |

### 6.3 Candidate Finance

| Route | Description |
|-------|-------------|
| `GET /candidate-finance/` | State candidate finance overview |
| `GET /candidate-finance/<cid>/<cmte>/itemized` | Itemized receipts (CSV export) |
| `GET /candidate-finance/<cid>/<cmte>/itemized-expenditures` | Itemized expenditures (CSV export) |

### 6.4 Committees

| Route | Description |
|-------|-------------|
| `GET /committees/` | Committee directory |
| `GET /committees/suggest` | Autocomplete |
| `GET /committees/<id>` | Committee detail (internal ID) |
| `GET /committees/sbe/<sbe_id>` | Committee detail (SBE ID) |
| `GET /committees/filing/<doc_id>` | Filing detail |

### 6.5 Donors

| Route | Description |
|-------|-------------|
| `GET /donors/` | Donor directory |
| `GET /donors/<id>` | Donor detail (internal ID) |
| `GET /donors/key/<donor_key>` | Donor detail (composite key) |
| `GET /donors/entity/<entity_id>` | Merged entity detail |

### 6.6 Federal Finance

| Route | Description |
|-------|-------------|
| `GET /federal-finance/` | Federal finance hub |
| `GET /federal-finance/candidates` | Federal candidate list with totals |
| `GET /federal-finance/networks` | Donor-candidate network graph |
| `GET /federal-finance/money-flow` | Multi-layer money flow (A/B/E) |
| `GET /federal-finance/donor-intelligence` | Donor segmentation and clustering |
| `GET /federal-finance/influence` | Influence scores (PageRank, degree) |
| `GET /federal-finance/follow-the-money` | Multi-hop donor path tracing |
| `GET /federal-finance/geography` | Geographic concentration |
| `GET /federal-finance/geo-drilldown` | Paginated geo detail |
| `GET /federal-finance/matching` | Federal-local donor overlap |
| `GET /federal-finance/races/<office>/<district>/outside-spending` | Race-level IE drilldown |
| `GET /federal-finance/<candidate_id>` | Federal candidate detail |

### 6.7 IRS 527 / Dark Money

| Route | Description |
|-------|-------------|
| `GET /527/` | 527 organization list (IL-filtered) |
| `GET /527/suggest` | Autocomplete |
| `GET /527/<ein>` | 527 org detail (directors, contributions, expenditures, matches) |
| `GET /527/dark-money` | Dark money tracker |

### 6.8 Lobbying

| Route | Description |
|-------|-------------|
| `GET /lobbying/` | Lobbying entity list |
| `GET /lobbying/suggest` | Autocomplete |
| `GET /lobbying/<entity_id>` | Entity detail with clients and matches |
| `GET /lobbying/client/<client_id>` | Client detail with donor/527 connections |
| `GET /lobbying/flows` | Lobbying-to-money flow visualization |

### 6.9 OpenBook Comptroller

| Route | Description |
|-------|-------------|
| `GET /openbook/` | Vendor directory with contract/contribution aggregates |
| `GET /openbook/<vendor_key>` | Vendor detail with provenance |

### 6.10 API Endpoints (JSON)

All under `/api/`. Key endpoints:

| Endpoint | Description |
|----------|-------------|
| `/api/stats` | Summary statistics |
| `/api/committees`, `/api/committees/<id>` | Committee data |
| `/api/donors`, `/api/donors/<id>` | Donor data |
| `/api/reports`, `/api/reports/<id>` | Report data |
| `/api/scrape-status` | Scraper progress |
| `/api/analytics/network` | Network graph data |
| `/api/analytics/anomalies` | Anomaly flags |
| `/api/analytics/concentration` | Concentration metrics (HHI, Gini) |
| `/api/analytics/time-series` | Time-series data |
| `/api/analytics/geo` | Geographic aggregates |
| `/api/analytics/nlp` | NLP expenditure categories |
| `/api/analytics/reconciliation` | D2 reconciliation |
| `/api/analytics/donor-cogiving` | Co-giving patterns |
| `/api/analytics/committee-similarity` | Committee similarity |
| `/api/analytics/candidate-competition` | Candidate competition |
| `/api/analytics/lobbying-influence` | Lobbying-money flows |
| `/api/analytics/irs527-ecosystem` | 527 ecosystem |

### 6.11 Admin & Auth

| Route | Description |
|-------|-------------|
| `GET /admin/` | Admin hub (requires login) |
| `GET /admin/federal-receipt-audit` | FEC receipt audit |
| `GET /admin/donor-merges` | Donor merge review |
| `GET/POST /login` | Authentication |
| `GET /logout` | Session clear |

---

## 7. Cross-Matching Engine

Module: `database/cross_matching.py`

The cross-matching engine links entities across datasets using **Jaccard similarity** on tokenized names with sparse inverted-index candidate generation.

### Algorithm

1. **Tokenization**: Names are lowercased, split on whitespace/punctuation, stop words removed.
2. **Inverted Index**: A token → entity mapping is built in-memory for the target dataset.
3. **Candidate Generation**: For each source entity, tokens are looked up in the inverted index to find candidate matches (entities sharing at least one token).
4. **Jaccard Scoring**: `|intersection(tokens_a, tokens_b)| / |union(tokens_a, tokens_b)|`
5. **Threshold Filtering**: Only matches above the configured threshold (typically 0.80) are persisted.

### Match Jobs

| Job | Source | Target | Threshold |
|-----|--------|--------|-----------|
| Lobbying client → Donor | `lobbying_clients` | `analytics_donor_summary` | 0.80 |
| Lobbying client → Expenditure payee | `lobbying_clients/entities` | Expenditure records | 0.80 |
| 527 org → IL committee | `irs527_organizations` | `isbe_committees` | 0.80 |
| 527 expenditure → Committee/candidate | `irs527_expenditures` | Committees + candidates | 0.80 |
| 527 director → Donor | `irs527_directors` | All donors (exhaustive) | 0.80 |
| 527 director → Candidate | `irs527_directors` | State + federal candidates | 0.80 |
| 527 director → Donor (address) | `irs527_directors` | Donors (city+state+zip) | 0.50 |
| 527 org address → Committee/donor | `irs527_organizations` (4 address types) | Committees + donors | Address scoring |
| Lobbying client → 527 org | `lobbying_clients` | `irs527_organizations` | 0.80 |
| Federal donor → Local donor | `fec_schedule_a` | Local donors (zip+state+name) | Composite |

### Performance Notes

- Phase 1 (name matching): Python dict-based sparse inverted indexes. Faster than matrix operations for small token sets (3-8 tokens per name).
- Phase 2 (address matching): SQL-native `INSERT...SELECT` with `pg_trgm` similarity. ~10s for 25M candidate pairs.
- All jobs run via `python run.py run-cross-matching --only all` with optional `ThreadPoolExecutor` parallelism.

---

## 8. Analytics Engine

Module: `database/analytics.py` (6867 lines)

### Materialized Aggregates

Built by `python run.py refresh-analytics --with-snapshot`:

| Aggregate | Description |
|-----------|-------------|
| `analytics_donor_committee_agg` | Donor-committee total contributions, count, date range |
| `analytics_committee_monthly_totals` | Monthly receipts/expenditures per committee |
| `analytics_large_contributions` | Contributions above configurable thresholds |
| `analytics_donor_summary` | Cross-source donor profiles (state + federal) |

### Analysis Capabilities

1. **Network Analysis**: Donor-committee flow graphs, committee-candidate links, vendor expenditure networks. Uses NetworkX for graph metrics (degree, betweenness, PageRank).

2. **Concentration Metrics**: Herfindahl-Hirschman Index (HHI), Gini coefficient, top-N donor share per committee/candidate.

3. **Anomaly Detection**: Large contribution flags, monthly spending spikes, unusually high donor concentration, velocity anomalies.

4. **Time-Series Intelligence**: Monthly aggregation, moving averages, month-over-month change rates.

5. **Geographic Analysis**: State/city-level donor aggregation, geographic concentration scoring.

6. **NLP Expenditure Categorization**: Keyword-based expenditure purpose classification.

7. **Relationship Analysis**: Co-giving patterns (donors who give to the same committees), committee similarity scoring, candidate competition networks.

### Caching Architecture

- **In-process TTL caching** on heavy routes (configurable per-route, default 1800s for analytics).
- **Startup prewarm**: Background thread per gunicorn worker populates caches on boot.
- **Per-worker isolation**: Each gunicorn worker has its own cache dict. Prewarm ensures all workers start warm.
- **Cache bust**: Append `?refresh_cache=1` to any cached route.

---

## 9. Configuration Reference

All settings in `config.py`, overridable via environment variables.

### Core

| Setting | Default | Description |
|---------|---------|-------------|
| `DATABASE_URL` | `postgresql://devin@localhost/ilcf` | PostgreSQL connection string |
| `APP_ENV` | `development` | Runtime environment |
| `FLASK_SECRET_KEY` | (generated) | Session secret |
| `FEC_API_KEY` | (required) | FEC API key for federal sync |

### Caching

| Setting | Default | Description |
|---------|---------|-------------|
| `ROUTE_PERF_CACHE_ENABLED` | `true` | Enable route-level caching |
| `DASHBOARD_PREWARM_ENABLED` | `true` | Prewarm caches on startup |
| `ANALYTICS_NETWORKS_CACHE_TTL_SECONDS` | `1800` | Networks page cache TTL |
| `ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS` | `1800` | Relationships cache TTL |
| `ANALYTICS_RISK_CACHE_TTL_SECONDS` | `1800` | Risk page cache TTL |
| `ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS` | `1800` | Overview cache TTL |
| `ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS` | `300` | Geo drilldown cache TTL |
| `IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS` | `300` | Dark money stats cache TTL |
| `SEARCH_RESULTS_CACHE_TTL_SECONDS` | `120` | Search result cache TTL |
| `SEARCH_RESULTS_CACHE_MAX_ENTRIES` | `256` | Max cached search results |

### Search Guardrails

| Setting | Default | Description |
|---------|---------|-------------|
| `SEARCH_MIN_QUERY_LENGTH` | `2` | Minimum query length |
| `SEARCH_MAX_QUERY_LENGTH` | `64` | Maximum query length |
| `SEARCH_QUERY_TIMEOUT_MS` | `700` | Query timeout (ms) |
| `SEARCH_SLOW_QUERY_MS` | `400` | Slow query threshold (ms) |

### API

| Setting | Default | Description |
|---------|---------|-------------|
| `API_KEYS` | (empty) | Comma-separated API keys |
| `API_REQUIRE_KEY` | `false` | Enforce API key auth |
| `API_RATE_LIMIT_PER_MINUTE` | `120` | Per-key/IP rate limit |

### Scraping

| Setting | Default | Description |
|---------|---------|-------------|
| `RATE_LIMIT_RPM` | `30` | Requests per minute |
| `DEFAULT_BATCH_SIZE` | `20` | Scraper batch size |
| `OPENBOOK_BATCH_RPM` | (configured) | OpenBook rate limit |

### Socrata (Chicago)

| Setting | Default | Description |
|---------|---------|-------------|
| `SOCRATA_APP_TOKEN` | (required) | Socrata API token |
| `SOCRATA_API_BASE_URL` | `data.cityofchicago.org` | API base URL |
| `SOCRATA_API_PAGE_LIMIT` | `1000` | Records per page |
| `SOCRATA_API_TIMEOUT_SECONDS` | `30` | Request timeout |

### Feature Flags

| Setting | Default | Description |
|---------|---------|-------------|
| `EXPERIMENTAL_VIZ_LAB_ENABLED` | `true` (non-prod) | Enable viz lab |

---

## 10. Testing

### Running Tests

```bash
# Full suite
pytest -q

# Specific module
pytest -q tests/test_webapp.py

# Fast pre-deploy subset
pytest -q tests/test_federal_fec.py tests/test_lobbying_routes.py tests/test_uiux_improvements.py tests/test_webapp.py::TestWebApp::test_federal_finance_page_loads_with_synced_rows

# Skip integration tests (CI default)
pytest -q -m "not integration"
```

### Test Modules (45+)

| Module | Coverage Area |
|--------|--------------|
| `test_webapp.py` | Core route loading, template rendering |
| `test_analytics_features.py` | Analytics materialization, metrics |
| `test_federal_fec.py` | FEC sync, schedule loading |
| `test_cross_matching.py` | Jaccard matching, threshold logic |
| `test_cross_matching_postgres.py` | PostgreSQL-specific matching (integration) |
| `test_irs527_loader.py` | 527 FullDataFile parsing |
| `test_irs_527_parse.py` | 527 record type parsing |
| `test_irs_527_parse_edge_cases.py` | 527 edge cases, threading |
| `test_527_integration.py` | 527 route integration |
| `test_irs527_routes.py` | 527 web routes |
| `test_lobbying_loader.py` | Lobbying CSV import |
| `test_lobbying_routes.py` | Lobbying web routes |
| `test_bulk_download_loader.py` | ISBE bulk loader |
| `test_chicago_phase1_loader.py` | Socrata loader |
| `test_openbook.py` | OpenBook scraper |
| `test_donor_normalizer.py` | Donor name normalization |
| `test_entity_resolution.py` | Entity merge logic |
| `test_local_donor_entities.py` | Donor entity groups |
| `test_network_advanced_metrics.py` | Network graph metrics |
| `test_experimental_viz_lab.py` | Viz lab feature flag |
| `test_viz_lab_routes.py` | Viz lab routes |
| `test_app_performance.py` | Performance benchmarks |
| `test_perf_schema_patterns.py` | Schema/index patterns |
| `test_time_filter.py` | Global period filtering |
| `test_search_normalize.py` | Search normalization |
| `test_amount_parsing.py` | Amount string parsing |
| `test_text_parsing.py` | Text extraction |
| `test_redesign.py` | UI redesign tests |
| `test_uiux_improvements.py` | UX improvements |
| `test_isbe_rewire.py` | ISBE data path switching |
| `test_isbe_sunshine_patterns.py` | Sunshine ETL patterns |
| `test_models_dedupe.py` | Model deduplication |
| `test_maintenance.py` | Data cleanup |
| `test_insights.py` | Dashboard insights |
| `test_guided_workspace.py` | Investigation workspace |
| `test_triple_pipeline_viz.py` | Triple pipeline viz |
| `test_postgres_join_casts.py` | PostgreSQL type casting |
| `test_connection_postgres_init.py` | DB connection init |
| `test_cli_db_target.py` | CLI DB targeting |
| `test_column_integration.py` | Column integration |
| `test_comptroller_contracts.py` | Comptroller scraper |
| `test_committee_scraper_helpers.py` | Committee scraper |
| `test_scraper_integration.py` | Scraper integration |

### Test Infrastructure

- **Isolation**: `tests/conftest.py` creates a fresh PostgreSQL schema per test, drops on teardown.
- **Fixtures**: `tests/fixtures/` contains sample data files.
- **CI**: GitHub Actions runs `ruff` lint + `pytest -q -m "not integration"` against PostgreSQL 16 service container.
- **Integration marker**: Tests requiring `pg_trgm` or live services use `@pytest.mark.integration`.

### Key Testing Rules

- Always use PostgreSQL-compatible SQL (not SQLite).
- Test fixtures must use `BOOLEAN` types (`TRUE`/`FALSE`), not integers (`0`/`1`).
- Run full suite after every change before considering work complete.

---

## 11. Deployment & Operations

### Infrastructure

| Component | Details |
|-----------|---------|
| Server | Hetzner VPS, 16GB RAM |
| Host | `178.156.162.56` |
| SSH | `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56` |
| App root | `/srv/illinois_campaign_finance/app` |
| Python | `/srv/illinois_campaign_finance/shared/venv/bin/python3` |
| Env file | `/srv/illinois_campaign_finance/shared/.env` |
| Web service | `ilcf-web.service` (systemd) |
| Database | PostgreSQL `ilcf` |
| Workers | gunicorn with 3 workers |

### Deploy Steps

```bash
# 1. Run local tests
pytest -q

# 2. Push to main
git push origin main

# 3. Pull on server
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "cd /srv/illinois_campaign_finance/app && git pull --ff-only origin main"

# 4. Install deps
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "/srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt"

# 5. Init DB (if schema changed)
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "cd /srv/illinois_campaign_finance/app && set -a && source /srv/illinois_campaign_finance/shared/.env && set +a && /srv/illinois_campaign_finance/shared/venv/bin/python3 run.py init-db"

# 6. Restart service
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "systemctl restart ilcf-web.service"

# 7. Verify
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "systemctl status ilcf-web.service --no-pager && curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/"
```

### Database Sync (Prod → Local)

```bash
# Dump from production
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "pg_dump -Fc ilcf" > data/ilcf_prod.dump

# Restore locally
pg_restore --clean --if-exists -d ilcf data/ilcf_prod.dump
```

---

## 12. Data Refresh Workflows

### Weekly Production Refresh

```bash
# 1. ISBE state data (download manually first due to 403 workaround)
for f in Candidates.txt CanElections.txt Committees.txt Officers.txt PrevOfficers.txt \
         D2Totals.txt Receipts.txt Expenditures.txt Investments.txt FiledDocs.txt \
         CmteCandidateLinks.txt CmteOfficerLinks.txt; do
  curl -A "Mozilla/5.0" -o "Bulk_download/$f" "https://elections.il.gov/campaigndisclosuredatafiles/$f"
done
python run.py sunshine-import --bulk-dir Bulk_download

# 2. FEC federal data
python run.py sync-fec-il-federal

# 3. Cross-matching (long-running, use tmux)
python run.py run-cross-matching --only all

# 4. Analytics refresh
python run.py refresh-analytics --with-snapshot

# 5. Restart web service
systemctl restart ilcf-web.service
```

### Monthly

```bash
# FEC backfills
python run.py backfill-fec-schedule-a
python run.py backfill-fec-schedule-b
python run.py backfill-fec-schedule-e

# IRS 527
python run.py import-irs527 --file <path> --illinois-only

# Chicago Socrata
python run.py import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert

# OpenBook (as needed)
python run.py import-openbook-batch --generate-seeds --max-vendors 20
```

### Post-Import Sequence

Always run in this order after any data import:
1. `run-cross-matching --only all`
2. `refresh-analytics --with-snapshot`
3. `systemctl restart ilcf-web.service`

---

## 13. Glossary

| Term | Definition |
|------|-----------|
| **ISBE** | Illinois State Board of Elections - state campaign finance authority |
| **FEC** | Federal Election Commission - federal campaign finance authority |
| **SBE ID** | State Board of Elections committee identifier |
| **EIN** | Employer Identification Number (IRS 527 org identifier) |
| **D-2 Report** | Quarterly campaign finance disclosure filed with ISBE |
| **Schedule A** | FEC individual contribution receipts |
| **Schedule B** | FEC committee disbursement records |
| **Schedule E** | FEC independent expenditure filings |
| **527 Organization** | Tax-exempt political org under IRC Section 527 (IRS-regulated) |
| **Form 8871** | IRS 527 organization registration |
| **Form 8872** | IRS 527 periodic financial report |
| **Dark Money** | Political spending where the funding source is not disclosed |
| **Jaccard Similarity** | Set-based similarity: \|A∩B\| / \|A∪B\| (used for name matching) |
| **HHI** | Herfindahl-Hirschman Index - market concentration measure (0-10000) |
| **Gini Coefficient** | Inequality measure (0=equal, 1=concentrated) |
| **PageRank** | Google's algorithm adapted for influence scoring in donor networks |
| **Materialized View** | Pre-computed query result stored as a table for fast access |
| **TTL Cache** | Time-to-live in-process cache (Python dict with expiry) |
| **Prewarm** | Populating caches at startup before user requests arrive |
| **Cross-Matching** | Linking entities across different datasets by name/address similarity |
| **Entity Resolution** | Merging duplicate records representing the same real-world entity |
| **OpenBook** | Illinois Comptroller's public contract/payment transparency portal |
| **Socrata** | Open data API platform used by City of Chicago |
| **TIGER/Line** | Census Bureau geographic boundary shapefiles |
| **TopoJSON** | Compact topology-encoded geographic data format |
| **Donor Key** | Composite key identifying a unique donor across records |
| **Compat Views** | SQL views that map `isbe_*` tables to legacy `bulk_*` column names |
| **pg_trgm** | PostgreSQL extension for trigram-based text similarity |
| **Condensed Receipts** | Materialized view deduplicating amended ISBE receipt filings |
