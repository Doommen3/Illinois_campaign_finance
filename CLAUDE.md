# CLAUDE.md - Illinois Campaign Finance Intelligence Platform

## Project Overview

Full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from state (ISBE) and federal (FEC) sources, plus IL SOS lobbying data and IRS 527 political organization filings. ~200K lines of code, 59-table SQLite schema, 5.5GB+ database.

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
python run.py init-db
python run.py runserver --port 5000
```

## Architecture

- **Backend**: Python 3.12, Flask 3.0, SQLite (WAL mode)
- **Frontend**: Jinja2 templates, vanilla JS, CSS-only tabbed interfaces
- **Scraping**: Playwright async with ASP.NET ViewState handling
- **CLI**: Click framework via `run.py`
- **Testing**: pytest (20+ test modules)

### Key Directories

| Path | Purpose |
|------|---------|
| `cli/commands.py` | All CLI commands |
| `database/schema.sql` | 57-table DDL schema |
| `database/models.py` | Dataclass-based ORM |
| `database/analytics.py` | Network, anomaly, concentration analytics |
| `database/cross_matching.py` | Jaccard-based cross-dataset matching engine |
| `database/irs527_loader.py` | IRS 527 FullDataFile parser |
| `database/federal_fec.py` | FEC API integration |
| `webapp/routes/` | 15 Flask route modules |
| `webapp/templates/` | 50+ Jinja2 templates |
| `tests/` | pytest suite |

### Data Sources

1. **ISBE** (state) - Committees, candidates, receipts, expenditures, D-2 reports
2. **FEC** (federal) - IL candidates, Schedule A/B/E contributions/disbursements
3. **IL SOS** - Lobbying entities/clients + daily lobbyist/entity/client extract
4. **IRS 527** - Political org registrations, reports, directors, expenditures
5. **City of Chicago (Socrata)** - Contracts, payments, lobbyist contributions, and lobbying activity (Phase 1 API ingest)

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

**Performance note**: The matching engine uses Python dict-based sparse inverted indexes, NOT numpy/matrix operations. Each match function builds an in-memory token→entity index, then iterates candidates. Address matching uses SQL-level JOINs on normalized state+city to keep memory low. For ~5K directors × ~1M donors, the inverted index approach keeps CPU reasonable (~1–2 minutes locally on M3 Pro).

**Why not numpy/scipy?** The Jaccard similarity on small token sets (3–8 tokens per name) is more efficient with Python set operations and inverted-index pruning than sparse matrix multiplication. The inverted index already reduces the 6.3B-pair search space by 100–144x. NumPy would add a dependency for marginal gains at current scale. If donor tables grow 10x+, scipy sparse matrix Jaccard could be considered.

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
   - Explicitly call out when a "fast" mode is unsafe in production (for example lock contention, high WAL pressure, or memory risk).

## Code Style

- Primary stack: Python (backend), HTML/CSS/JavaScript (frontend).
- Always use SQLite-safe threading patterns (check_same_thread=False or connection-per-request) in Flask apps.
- When writing shell commands for the user to copy, ensure they are single-line or properly escaped.
- Follow existing patterns: dataclass models, Jinja2 templates extending `base.html`, route blueprints.
- Use `_table_exists()` checks before querying cross-matching tables (they may not exist in fresh DBs).
- Use `_scalar()` helper for safe single-value queries with defaults.
- Prefer `INSERT OR REPLACE` for idempotent upserts.
- Use chunked batch inserts (`_chunked()` helper) for large data loads.

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
- For long-running server tasks, provide the fastest validated safe command first (including flags like incremental mode), then provide fallback/recovery commands.
- Runtime DB selection: if `DATABASE_URL` is set, the web app targets PostgreSQL at runtime; if unset, it falls back to `DATABASE_PATH` (SQLite).
- Safe runtime cutover policy:
   1. Require successful migration parity check (`mismatches=0`) before switching service env.
   2. Keep `DATABASE_PATH` unchanged for rollback.
   3. Add `DATABASE_URL` in service env, restart service, run endpoint sweep.
   4. On any critical regression, remove `DATABASE_URL` and restart immediately.
- Validated short-downtime cutover mode (allowed and often faster):
   1. Announce a short maintenance window.
   2. Deploy latest code (`./scripts/deploy.sh`) before switching DB target.
   3. Set `DATABASE_URL` in `/srv/illinois_campaign_finance/shared/.env`.
   4. Restart `ilcf-web.service` and run full endpoint sweep.
   5. Confirm runtime backend from app context (`PostgresCompatConnection`, `current_database() = ilcf`).
   6. If any critical route fails, rollback immediately by unsetting `DATABASE_URL` and restarting service.
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
| DB | `/srv/illinois_campaign_finance/shared/data/campaign_finance.db` |
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

Use `scripts/pull-db.sh` to pull the production SQLite DB to your local machine. The script checkpoints the WAL, backs up the existing local DB, and uses rsync for efficient incremental transfers.

```bash
# Full sync (interactive — ssh-agent handles passphrase)
./scripts/pull-db.sh

# Dry run (show what would transfer)
./scripts/pull-db.sh --dry-run
```

**Important**: Do NOT run `init-db`, `run-cross-matching`, or other DB-writing commands on the server while rsync is in progress — they write to the WAL and can cause an inconsistent local copy. Finish the pull first, then run migrations.

Local DB path: `data/campaign_finance.db` (matches `config.DATABASE_PATH` default).
Backups retained: 3 most recent in `data/backups/`.

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

### Run cross-matching
```bash
python run.py run-cross-matching --only all
```

### Refresh analytics
```bash
python run.py refresh-analytics --with-snapshot
```

## Database Notes

- 57-table schema in `database/schema.sql`
- WAL mode enabled for concurrent reads
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
