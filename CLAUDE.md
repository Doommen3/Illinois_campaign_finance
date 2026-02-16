# CLAUDE.md - Illinois Campaign Finance Intelligence Platform

## Project Overview

Full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from state (ISBE) and federal (FEC) sources, plus IL SOS lobbying data and IRS 527 political organization filings. ~200K lines of code, PostgreSQL database (local: `ilcf`, prod: `ilcf`), 5.5GB+.

## Local Development Machine

- **Hardware**: MacBook Pro M3 Pro, 18GB RAM, Apple Silicon (ARM64)
- All local compute-heavy operations should be optimized for Apple Silicon when possible (e.g., use Metal/Accelerate-backed libraries, ARM-native builds).
- Prefer running CPU/memory-intensive tasks locally rather than on the 16GB Hetzner VPS.
- When adding numeric/scientific dependencies in the future, prefer Apple Silicon-optimized builds (numpy with Accelerate, scipy with vecLib, etc.).

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://devin@localhost/ilcf
python run.py init-db
python run.py runserver --port 5000
```

## Architecture

- **Backend**: Python 3.12, Flask 3.0, PostgreSQL 16
- **Frontend**: Jinja2 templates, vanilla JS, CSS-only tabbed interfaces
- **Scraping**: Playwright async with ASP.NET ViewState handling
- **CLI**: Click framework via `run.py`
- **Testing**: pytest (20+ test modules)

### Key Directories

| Path | Purpose |
|------|---------|
| `cli/commands.py` | All CLI commands |
| `database/schema.sql` | DDL schema |
| `database/models.py` | Dataclass-based ORM |
| `database/analytics.py` | Network, anomaly, concentration analytics |
| `database/cross_matching.py` | Jaccard-based cross-dataset matching engine |
| `database/irs527_loader.py` | IRS 527 FullDataFile parser |
| `database/federal_fec.py` | FEC API integration |
| `scripts/isbe_sunshine_etl.py` | ISBE bulk data ETL (PostgreSQL-native, adapted from illinois-sunshine) |
| `scraper/openbook_scraper.py` | OpenBook Comptroller scraper + smart search |
| `scraper/comptroller_contracts.py` | Comptroller State Contracts DataTables scraper |
| `webapp/routes/` | 15 Flask route modules |
| `webapp/templates/` | 50+ Jinja2 templates |
| `tests/` | pytest suite |

### Data Sources

1. **ISBE** (state) - Committees, candidates, receipts, expenditures, D-2 reports, filed docs, candidacies, officers, investments
   - **Primary ETL**: `scripts/isbe_sunshine_etl.py` (adapted from datamade/illinois-sunshine)
   - Loads 12 ISBE bulk files into `isbe_*` tables with FK constraints
   - Creates materialized views: `isbe_condensed_receipts`, `isbe_condensed_expenditures` (dedup amended filings), `isbe_committee_money`, `isbe_candidate_money`
   - CLI: `python run.py sunshine-import [--download] [--bulk-dir Bulk_download]`
   - Legacy loader: `python run.py import-bulk-download` → `bulk_*_clean` tables (still works, but `isbe_*` tables are preferred)
2. **FEC** (federal) - IL candidates, Schedule A/B/E contributions/disbursements
3. **IL SOS** - Lobbying entities/clients + daily lobbyist/entity/client extract
4. **IRS 527** - Political org registrations, reports, directors, expenditures
5. **City of Chicago (Socrata)** - Contracts, payments, lobbyist contributions, and lobbying activity (Phase 1 API ingest)
6. **OpenBook Illinois Comptroller** - State contract data and campaign contribution records via HTTP autosuggest API + search scraping (`scraper/openbook_scraper.py`)

### Cross-Matching Engine

All name matching uses Jaccard similarity with sparse inverted-index candidate generation (`database/cross_matching.py`). Address matching uses normalized zip5+city+state scoring. Current matches:
- Lobbying client -> donor (Jaccard 0.80 threshold)
- Lobbying client/entity -> expenditure payee (Jaccard 0.80)
- 527 org -> IL committee (Jaccard 0.80)
- 527 expenditure recipient -> committee/candidate (Jaccard 0.80)
- 527 director -> donor (Jaccard 0.80, exhaustive — no row limit)
- 527 director -> state + federal candidate (Jaccard 0.80)
- 527 director -> donor by address (city+state+zip scoring, 0.5 threshold)
- 527 org addresses -> committee/donor (4 address types: org, custodian, contact, business)
- Lobbying client -> 527 org (Jaccard 0.80)
- Federal donor -> local donor (zip+state+name)

**Performance note**: Phase 1 (name matching) uses Python dict-based sparse inverted indexes — each match function builds an in-memory token→entity index, then iterates candidates. This is faster than matrix operations for small token sets (3–8 tokens per name). Phase 2 (address matching) runs as SQL-native `INSERT...SELECT` with `pg_trgm` similarity on PostgreSQL (parallel merge join, ~10s for 25M candidate pairs).

**numpy/scipy policy**: Not currently used. Acceptable to add if a concrete use case arises (e.g., embedding-based similarity, large dense matrix operations). For current workloads, Python set operations (Phase 1) and PostgreSQL pg_trgm (Phase 2) are faster than sparse matrix alternatives. When adding, prefer Apple Silicon-optimized builds (numpy with Accelerate, scipy with vecLib).

### Route Performance Caching (2026-02)

- Heavy routes with in-process TTL caching:
   - `/` (homepage candidate stats, top donors, and dashboard insights)
   - `/analytics/relationships`
   - `/527/dark-money` (summary stats)
- Config flags in `config.py`:
   - `ROUTE_PERF_CACHE_ENABLED`
   - `DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS`
   - `DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS`
   - `DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS`
   - `ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS`
   - `IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS`
   - `DASHBOARD_PREWARM_ENABLED`
   - `SEARCH_RESULTS_CACHE_TTL_SECONDS`
   - `SEARCH_RESULTS_CACHE_MAX_ENTRIES`
   - `SOCRATA_APP_NAME`
   - `SOCRATA_APP_TOKEN`
   - `SOCRATA_APP_SECRET`
   - `SOCRATA_API_BASE_URL`
   - `SOCRATA_API_PAGE_LIMIT`
   - `SOCRATA_API_TIMEOUT_SECONDS`
   - `SOCRATA_API_MAX_RETRIES`
   - `SOCRATA_API_MIN_INTERVAL_SECONDS`
- Homepage donor query fast path uses `analytics_donor_summary` (fallback remains legacy donor totals query if the summary table is absent).
- 527 contribution ingest now populates `irs527_contributor_rollup` to reduce expensive recomputation for contribution summary metrics.
- Search route performance guardrails:
  - In `type=all`, `filed_docs` runs only for doc-id-like queries.
  - In `type=all`, `donor_keys` runs only for donor-key-like queries.
  - Search responses are cached in-process by `period + query + type`.
- 527 contribution/expenditure route queries should prefer indexable predicates (`amount > 0`) and dual-format date range filters (`YYYY-MM-DD` + `YYYYMMDD`) over `DATE(column)` wrappers.
- Required 527 indexes for perf-sensitive routes:
  - `idx_irs527_contributions_name_amount`
  - `idx_irs527_contributions_date_amount`
  - `idx_irs527_expenditures_date_amount`
- Global time-filter canonical semantics:
   - Report-driven pages/queries filter by `filed_date`.
   - Contribution-driven pages/queries filter by `transaction_date` (or `received_date` where bulk receipts do not expose transaction timestamps).
   - Legacy routes (`/reports`, `/donors`, `/committees`, and search report/filed-doc/donor-key sections) are expected to propagate the active global period window end-to-end.
- Benchmarking guidance:
   - Always measure at least one **cold** pass and one **warm** pass.
   - For reliable warm numbers, run 2-3 warm passes and use median.
   - In tests, route perf cache is disabled by default under `TESTING` unless explicitly enabled in a test config.

## Testing

- Run all tests: `pytest -q`
- Run specific module: `pytest -q tests/test_cross_matching.py`
- Run fast pre-deploy subset: `pytest -q tests/test_federal_fec.py tests/test_lobbying_routes.py tests/test_uiux_improvements.py tests/test_webapp.py::TestWebApp::test_federal_finance_page_loads_with_synced_rows`
- After generating test data, always verify NOT NULL constraints and required fields match the actual schema.
- Run tests after every implementation change before presenting work as complete.

## Performance-First Delivery Policy (Required)

For any significant code path (imports, migrations, cross-matching, analytics refreshes, route queries, or caching changes), treat performance as a first-class requirement.

1. **Measure Baseline Before Refactor**
   - Capture current runtime and throughput using realistic data volume.
   - For routes, capture 1 cold and 2-3 warm passes (use median warm).
   - For long CLI jobs, capture wall-clock start/end and rows processed.

2. **Evaluate Fast Paths Before Deep Implementation**
   - Compare at least two viable approaches and choose based on measured end-to-end runtime, not intuition.
   - Typical comparisons:
     - SQL aggregation/materialized summaries vs Python post-processing
     - Incremental vs full rebuild execution
     - Bulk load/COPY-style insertion vs row/chunk executemany
     - Parallel workers vs single-writer mode when DB contention exists

3. **Optimization Order of Operations**
   - First reduce rows scanned/recomputed.
   - Then reuse cached/materialized outputs.
   - Then optimize write paths (batch/bulk operations).
   - Only then tune parallelism/worker counts.

4. **Completion Gate (Do Not Skip)**
   - Re-run the same measurements after changes.
   - Report before/after numbers in the handoff.
   - If no meaningful gain is observed, document why and either iterate once more or keep the simpler safer path.

5. **Documentation Requirement**
   - Update `README.md` (and relevant runbooks) with the fastest known command pattern and caveats.
   - Explicitly call out when a "fast" mode is unsafe in production (for example lock contention or memory risk).

## Code Style

- Primary stack: Python (backend), HTML/CSS/JavaScript (frontend).
- Use PostgreSQL connection pooling patterns in Flask apps (connection-per-request via app context).
- When writing shell commands for the user to copy, ensure they are single-line or properly escaped.
- Follow existing patterns: dataclass models, Jinja2 templates extending `base.html`, route blueprints.
- Use `_table_exists()` checks before querying cross-matching tables (they may not exist in fresh DBs).
- Use `_scalar()` helper for safe single-value queries with defaults.
- Prefer `INSERT ... ON CONFLICT DO UPDATE` (upsert) for idempotent writes.
- Use chunked batch inserts (`_chunked()` helper) for large data loads.
- Use `psql` for ad-hoc queries, not `sqlite3`. Local DB: `psql ilcf`. Prod: `psql -h localhost ilcf`.

## Token/Credit Efficiency Policy (Required)

- Before starting any non-trivial process, evaluate whether the user can run it directly with lower credit/token cost than agent execution.
- If user handoff is feasible and cheaper, pause and prompt the user first with:
  - the exact command/process they can run,
  - an estimated token/credit savings range if they run it themselves instead of the agent,
  - expected runtime and what output to return.
- If a request/prompt is estimated to be high-cost in credits/tokens, warn the user before proceeding and include:
  - estimated token/credit usage range,
  - the main cost driver (for example: very large logs, broad multi-file refactors, or verbose full-suite outputs),
  - one or more cheaper alternatives (scope reduction, targeted files, summarized output, staged execution).
- If the user confirms they still want the high-cost path, proceed.

## Documentation Sync Policy (Required)

- After making changes that introduce useful project knowledge, update both `README.md` and `CLAUDE.md` in the same task.
- "Useful project knowledge" includes new commands/flags, workflow updates, config/env changes, performance findings, operational caveats, and troubleshooting notes.
- Do not mark implementation complete until documentation is updated, or explicitly state why no documentation change is needed.

## New Dataset Integration Workflow (Required)

Whenever adding a new data source (or major new columns), always evaluate integration across the entire platform:

1. **Schema + Loader + QA**
   - Add/adjust schema tables and indexes in `database/schema.sql`.
   - Implement loader parsing + idempotent upserts.
   - Add post-import QA metrics (null/malformed counts, min/max valid dates, freshness windows).

2. **Cross-Matching Expansion (Mandatory Review)**
   - Explicitly review whether the new data should be matched against existing donors, committees, candidates, lobbying entities/clients, vendors, and 527 entities.
   - If yes, add/update match jobs in `database/cross_matching.py`, with thresholds and tests.
   - Persist match output in dedicated tables and expose summary counts where useful.

3. **Analytics + Visualization Integration**
   - Check where the new data belongs in existing dashboards/routes (`/analytics`, `/federal-finance`, `/lobbying`, `/527`, homepage cards).
   - Evaluate whether to add new visualizations (network edges, Sankey/alluvial, time-series, geo, concentration, anomaly panels).
   - Add help-text/methodology notes for any new metric semantics.

4. **Full-System Validation**
   - Run targeted tests first, then full suite (`pytest -q`).
   - Validate impacted routes render and load with realistic data volumes.
   - Update docs (`README.md`, `CLAUDE.md`, and runbooks) with commands/config/caveats.
   - Check performance impact and add caching/materialization changes when needed.

Do not treat dataset ingestion as complete until all four workflow areas are addressed.

## Deployment

- When asked about deployment, always assume REMOTE/PRODUCTION server unless explicitly told otherwise.
- Always verify actual server paths, service names, and directory structures before generating deployment commands — never assume defaults.
- Use `python3` (not `python`) in all server scripts and systemd files.
- **Long-running server tasks (>10 minutes)**: Do NOT run these autonomously via Claude. Instead, provide the user with the exact command to run so they can execute it themselves, watch the output, and see it through to completion. Examples: `run-cross-matching --only all`, `import-irs527`, `refresh-analytics` on large datasets. Always estimate the runtime before handing off.
   - **Exception (OpenBook only):** Long-running OpenBook scrape/import commands (for example `import-openbook-batch` and related OpenBook scraping flows) are allowed to run autonomously via Claude when explicitly requested.
- For long-running server tasks, provide the fastest validated safe command first (including flags like incremental mode), then provide fallback/recovery commands.
- Runtime DB: PostgreSQL is the primary and only supported database. `DATABASE_URL` must be set in all environments.
  - Local: `DATABASE_URL=postgresql://devin@localhost/ilcf`
  - Prod: `DATABASE_URL` set in `/srv/illinois_campaign_finance/shared/.env`
- SQLite (`DATABASE_PATH`) is deprecated and will be removed. Do not use it for new development.
- After any debug cutover test, always stop temporary debug servers (`run.py runserver --port 5051`) to avoid stale processes.

### Server Details

| Item | Value |
|------|-------|
| Host | 178.156.162.56 |
| SSH | `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56` |
| App root | `/srv/illinois_campaign_finance/app` |
| Python | `/srv/illinois_campaign_finance/shared/venv/bin/python3` |
| Pip | `/srv/illinois_campaign_finance/shared/venv/bin/pip` |
| Env file | `/srv/illinois_campaign_finance/shared/.env` |
| DB | PostgreSQL `ilcf` (local: `postgresql://devin@localhost/ilcf`, prod: via `DATABASE_URL` in `.env`) |
| Web service | `ilcf-web.service` |
| Legacy service (keep disabled) | `illinois-web.service` |

### Deploy Steps

1. Run local tests: `pytest -q`
2. Push to main: `git push origin main`
3. SSH to server and pull (see SSH note below):
   ```bash
   cd /srv/illinois_campaign_finance/app && git pull --ff-only origin main
   ```
4. Install deps:
   ```bash
   /srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt
   ```
5. Init DB (schema migrations):
   ```bash
   /srv/illinois_campaign_finance/shared/venv/bin/python3 run.py init-db
   ```
6. Refresh analytics (if needed):
   ```bash
   /srv/illinois_campaign_finance/shared/venv/bin/python3 run.py refresh-analytics --with-snapshot
   ```
7. Restart service:
   ```bash
   systemctl restart ilcf-web.service
   ```
8. Verify:
   ```bash
   systemctl status ilcf-web.service --no-pager
   curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/
   ```

### SSH Authentication Notes

The SSH key `~/.ssh/hetzner_ed25519` passphrase is stored in **macOS Keychain via ssh-agent**. This was configured in `~/.zshrc`:
```bash
ssh-add --apple-use-keychain ~/.ssh/hetzner_ed25519 2>/dev/null
```
SSH, rsync, scp, and Claude Code autonomous SSH all work without passphrase prompts. If the agent ever loses the key (e.g., after OS update), re-run `ssh-add --apple-use-keychain ~/.ssh/hetzner_ed25519` and enter the passphrase once.

The server does NOT have GitHub credentials configured. To pull on the server, either:
1. Use a deploy key, or
2. Temporarily embed a PAT in the remote URL (remove after pull):
   ```bash
   git remote set-url origin https://<PAT>@github.com/Doommen3/Illinois_campaign_finance.git
   git pull --ff-only origin main
   git remote set-url origin https://github.com/Doommen3/Illinois_campaign_finance.git
   ```

### Server Command Pattern

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
PIP=/srv/illinois_campaign_finance/shared/venv/bin/pip
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a
$PYTHON run.py <command>
```

### GitHub

- Username: Doommen3
- Remote: HTTPS
- Local auth: Classic PAT via macOS Keychain (`credential.helper = osxkeychain` in `.gitconfig`)
- `gh` CLI is installed and authenticated (fine-grained PAT) for API operations (`gh api`, `gh pr`, etc.)
- Git push uses the classic PAT stored in osxkeychain (the fine-grained PAT in `gh` does not have git push scope)
- If push ever returns 403, re-store the classic PAT: `printf 'protocol=https\nhost=github.com\nusername=Doommen3\npassword=<PAT>\n' | git credential-osxkeychain store`

### Syncing Production DB to Local

Use `pg_dump`/`pg_restore` or `scripts/pull-db.sh` (if adapted for PostgreSQL) to sync production data. The production database is PostgreSQL on the Hetzner VPS.

```bash
# Dump from prod (via SSH tunnel or direct)
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "pg_dump -Fc ilcf" > data/ilcf_prod.dump

# Restore locally
pg_restore --clean --if-exists -d ilcf data/ilcf_prod.dump
```

**Important**: Do NOT run `init-db`, `run-cross-matching`, or other DB-writing commands on the server while a dump is in progress.

Local DB: `postgresql://devin@localhost/ilcf` (matches `config.DATABASE_URL` default).

### Endpoint Sweep

After deploy, sweep key routes (see `codex.md` for full endpoint sweep script). Critical routes:
- `/`, `/search?q=Chicago`, `/candidates`
- `/federal-finance/`, `/analytics/`, `/lobbying/`, `/527/`
- `/federal-finance/races/H/01/outside-spending?cycle=2026`
- `/527/dark-money`, `/527/<ein>`

## Common Operations

### Import IL SOS lobbying data
```bash
python run.py import-lobbying --file Bulk_download/Lobbyist_Entity_Client_Data_Daily_20260214.csv
```

### Import IRS 527 data
```bash
python run.py import-irs527 --file Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt --illinois-only
```

### Import Chicago Phase 1 Socrata data
```bash
python run.py import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert
```

Phase 1 datasets imported:
- `rsxa-ify5` (Contracts)
- `s4vu-giwb` (Payments)
- `p9p7-vfqc` (Lobbyist Contributions)
- `pahz-egmi` (Lobbying Activity)

### OpenBook batch import (smart search, default)
```bash
python run.py import-openbook-batch --generate-seeds --max-vendors 20
```

Smart search (default) generates shorter search terms from verbose vendor names
(e.g., "COMCAST OF ILLINOIS III INC" → searches "COMCAST") and stores ALL
matching vendors per seed. Disable with `--no-smart-search` to fall back to
verbatim single-match resolution.

Key functions in `scraper/openbook_scraper.py`:
- `generate_search_terms(name)` — strips suffixes/geo/roman numerals, detects
  person names ("SMITH, JOHN" → "SMITH"), expands abbreviations via
  `_ABBREVIATION_ALIASES` (COMED → COMMONWEALTH EDISON, etc.)
- `_score_all_matches()` — scores suggestions against both seed text and search
  term (max confidence), returns all above threshold (default 0.4)
- `resolve_all_matches_http()` — queries autosuggest for each generated term,
  deduplicates by vendor_key, returns scored matches
- `openbook_vendor_match.search_term_used` column tracks which term produced
  each match
- Web routes:
  - `/openbook/` — resolved vendor directory with contract/contribution aggregates
  - `/openbook/<vendor_key>` — vendor detail with seed provenance + contracts/warrants/contributions
- Cross-matching note: OpenBook currently uses `openbook_vendor_seed -> openbook_vendor_match`
  as the canonical linkage to source datasets (expenditures/lobbying/FEC), and
  vendor detail surfaces that provenance directly.

### Run cross-matching
```bash
python run.py run-cross-matching --only all
```

### Refresh analytics
```bash
python run.py refresh-analytics --with-snapshot
```

## Database Notes

- PostgreSQL 16 database (`ilcf`) with schema defined in `database/schema.sql`
- Materialized views refreshed via `refresh-analytics` CLI command
- Cross-matching results stored in dedicated match tables:
  - `irs527_director_donor_matches` — name-based director↔donor
  - `irs527_director_candidate_matches` — director↔state/federal candidate
  - `irs527_director_address_matches` — address-based director↔donor
  - `irs527_org_address_matches` — org address↔committee/donor
  - `lobbying_donor_matches`, `lobbying_expenditure_matches`, etc.
- `irs527_contributions` — parsed type-A records (who donates TO 527 orgs)
- Lobbying raw dimensions and facts include:
   - `lobbying_lobbyists` — lobbyist profile/contact/status rows
   - `lobbying_lobbyist_registrations` — lobbyist↔entity↔client/year registrations (nullable client for unassigned rows)
- All IRS 527 tables prefixed with `irs527_`
- OpenBook tables:
   - `openbook_vendor_seed` — candidate vendor names from expenditures/lobbying/FEC
   - `openbook_vendor_match` — autosuggest matches (supports multiple matches per seed via `UNIQUE(seed_id, openbook_vendor_key)`; `search_term_used` tracks which smart-search term produced each match)
   - `openbook_contracts_raw`, `openbook_contract_warrants`, `openbook_contract_detail_status` — scraped contract data
   - `openbook_contributions_raw` — scraped campaign contribution data
   - `openbook_scrape_runs` — batch run metadata
