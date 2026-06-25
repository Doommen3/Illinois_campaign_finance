# CLAUDE.md - Illinois Campaign Finance Intelligence Platform

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Project Overview

Full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from state (ISBE) and federal (FEC) sources, plus IL SOS lobbying data and IRS 527 political organization filings. ~200K lines of code, PostgreSQL database (`ilcf` in both local + prod), 5.5GB+.

## Local Development Machine

- MacBook Pro M3 Pro, 18GB RAM, Apple Silicon (ARM64)
- Prefer running CPU/memory-intensive tasks locally vs. on the 16GB Hetzner VPS
- For new numeric/scientific deps, prefer Apple Silicon-optimized builds (numpy with Accelerate, scipy with vecLib)

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://devin@localhost/ilcf
python run.py init-db
python run.py runserver --port 5000
```

## Database Environment

PostgreSQL only. Always write PostgreSQL-compatible SQL — never SQLite-specific syntax. Use `ILIKE` for case-insensitive matching, proper `INTERVAL` syntax, etc. Tests must use PostgreSQL fixtures, not SQLite in-memory.

- Local: `DATABASE_URL=postgresql://devin@localhost/ilcf`
- Prod: `DATABASE_URL` set in `/srv/illinois_campaign_finance/shared/.env`
- SQLite (`DATABASE_PATH`) is deprecated. Do not use for new development.

## Architecture

- **Backend**: Python 3.12, Flask 3.0, PostgreSQL 16
- **Frontend**: Jinja2 templates, vanilla JS, CSS-only tabbed interfaces
- **Scraping**: Playwright async with ASP.NET ViewState handling
- **CLI**: Click framework via `run.py`
- **Testing**: pytest (20+ modules)

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
| `scripts/isbe_sunshine_etl.py` | ISBE bulk data ETL (PostgreSQL-native, adapted from datamade/illinois-sunshine) |
| `scripts/build_geometry.py` | Build IL TIGER/Line district TopoJSON + validation |
| `scripts/validate_geometry_keys.py` | Validate geometry join keys for CD/SLDL/SLDU outputs |
| `scraper/openbook_scraper.py` | OpenBook Comptroller scraper + smart search |
| `scraper/comptroller_contracts.py` | Comptroller State Contracts DataTables scraper |
| `webapp/routes/` | 16 Flask route modules |
| `webapp/routes/experimental.py` | Local-only `/experimental/viz-lab` + cached prototype APIs |
| `webapp/templates/` | 50+ Jinja2 templates |
| `docs/` | Runbooks and planning docs |
| `docs/runbooks/` | Operational reference (deployment, route perf caching) |
| `tests/` | pytest suite |

### Data Sources

1. **ISBE** (state) — Committees, candidates, receipts, expenditures, D-2 reports, filed docs, candidacies, officers, investments
   - Primary ETL: `scripts/isbe_sunshine_etl.py` → `isbe_*` tables with FK constraints + materialized views (`isbe_condensed_receipts`, `isbe_condensed_expenditures`, `isbe_committee_money`, `isbe_candidate_money`)
   - CLI: `python run.py sunshine-import [--download] [--bulk-dir Bulk_download] [--skip-compat-swap]`
   - Auto-runs `swap_bulk_to_isbe.py` after import to (re)create `bulk_*` compat views for legacy routes. `--skip-compat-swap` disables; `/candidate-finance/` then uses ISBE fallback mode.
   - Legacy loader: `python run.py import-bulk-download` → `bulk_*_clean` tables (works, but `isbe_*` is preferred)
2. **FEC** (federal) — IL candidates + Schedule A/B/E
   - CLI: `python run.py sync-fec-il-federal --refresh-cache` (needs `FEC_API_KEY`; `--refresh-cache` forces a live pull — see "FEC silent no-op" under Data Refresh Schedule). Backfills: `backfill-fec-schedule-{a,b,e}`. Donor matching: `refresh-fec-local-donor-matches` (runs automatically after `sync-fec-il-federal`).
3. **IL SOS** — Lobbying entities/clients + daily lobbyist/entity/client extract
4. **IRS 527** — Political org registrations, reports, directors, expenditures
5. **City of Chicago (Socrata)** — Contracts, payments, lobbyist contributions, lobbying activity (Phase 1)
6. **OpenBook IL Comptroller** — State contract data + campaign contribution records via autosuggest API
7. **Census TIGER/Line** — IL district boundaries for map layers (`tl_2025_17_cd119/sldl/sldu`) → `data/geometry/il/*.topo.json`. Validation: `python3 scripts/validate_geometry_keys.py`. Join keys:
   - Congressional: `district_key` string (`"01"`..`"17"`) + `district` int (`1..17`)
   - State House/Senate: `district` int (`1..118` / `1..59`)
   - All: `geoid` string + raw code fields (`CD119FP`, `SLDLST`, `SLDUST`)

### ISBE bulk download (hardened native path)

`sunshine-import --download` now handles the ISBE bulk fetch directly — browser User-Agent (bypasses ISBE's 403), retry+timeout, atomic `.partial` writes, and a post-download row-count sanity check against `Bulk_download/.row_counts` (aborts if any file drops >5% vs the previous run, catching ISBE's silent-truncation failure mode). The loader also tracks FK-rejected rows per file and aborts before building matviews if any file exceeds 1% reject rate — the catastrophic-truncation signature from the 2026-05-13 incident.

```bash
$PYTHON run.py sunshine-import --download
```

To force a fresh download when stale files are still in `Bulk_download/`, move them aside first (the `--download` flag only fetches missing files):

```bash
mkdir -p Bulk_download/.archive_$(date +%Y%m%d)
mv Bulk_download/{Candidates,CanElections,Committees,Officers,PrevOfficers,D2Totals,Receipts,Expenditures,Investments,FiledDocs,CmteCandidateLinks,CmteOfficerLinks}.txt Bulk_download/.archive_$(date +%Y%m%d)/ 2>/dev/null
$PYTHON run.py sunshine-import --download
```

Fallback curl loop (preserved in case the native path regresses):

```bash
for f in Candidates.txt CanElections.txt Committees.txt Officers.txt PrevOfficers.txt \
         D2Totals.txt Receipts.txt Expenditures.txt Investments.txt FiledDocs.txt \
         CmteCandidateLinks.txt CmteOfficerLinks.txt; do
  curl -fsS --retry 5 --retry-all-errors --connect-timeout 30 --max-time 1800 \
    -A "Mozilla/5.0" -o "Bulk_download/$f" \
    "https://elections.il.gov/campaigndisclosuredatafiles/$f"
done
$PYTHON run.py sunshine-import --bulk-dir Bulk_download
```

## Cross-Matching Engine

All name matching uses Jaccard similarity with sparse inverted-index candidate generation (`database/cross_matching.py`). Address matching uses normalized zip5+city+state scoring. Current matches:

- Lobbying client → donor (Jaccard 0.80)
- Lobbying client/entity → expenditure payee (0.80)
- 527 org → IL committee (0.80)
- 527 expenditure recipient → committee/candidate (0.80)
- 527 director → donor (0.80, exhaustive — no row limit)
- 527 director → state + federal candidate (0.80)
- 527 director → donor by address (city+state+zip, 0.5)
- 527 org addresses → committee/donor (4 address types: org, custodian, contact, business)
- Lobbying client → 527 org (0.80)
- Federal donor → local donor (zip+state+name)

**Performance**: Phase 1 (name) uses Python dict-based sparse inverted indexes — faster than matrix ops for small token sets (3–8 tokens/name). Phase 2 (address) is SQL-native `INSERT...SELECT` with `pg_trgm` (parallel merge join, ~10s for 25M candidate pairs).

**numpy/scipy policy**: Not currently used. Acceptable if a concrete use case arises. For current workloads, Python sets (Phase 1) + pg_trgm (Phase 2) outperform sparse matrix alternatives.

## Performance & Caching

Heavy routes use in-process TTL caching (per-worker dict). Architecture details, dated bug recaps, and the Viz Lab network contract are in **`docs/runbooks/route_perf_caching.md`** — load on demand. Top-level invariants:

- Heavy routes: `/`, `/analytics/*`, `/federal-finance/geo-drilldown`, `/527/dark-money`, `/experimental/viz-lab/data/*`
- Append `?refresh_cache=1` to force a miss
- TTLs are in `config.py` — grep `_CACHE_TTL_SECONDS` for the current list
- Caches respect `ROUTE_PERF_CACHE_ENABLED` + `TESTING` flags
- `warm_analytics_caches()` runs per gunicorn worker on startup (gated by `DASHBOARD_PREWARM_ENABLED`) — pre-populates risk/networks/relationships caches; ~2-3 min per worker
- Cache keys must include date/filter identity + (for Viz Lab) all advanced params, to prevent metric cross-talk
- Time-filter semantics: report-driven queries → `filed_date`; contribution-driven → `transaction_date` (or `received_date` for bulk receipts)

## Testing

- Run full suite after every implementation change: `pytest -q`
- Specific module: `pytest -q tests/test_cross_matching.py`
- Fast pre-deploy subset: `pytest -q tests/test_federal_fec.py tests/test_lobbying_routes.py tests/test_uiux_improvements.py tests/test_webapp.py::TestWebApp::test_federal_finance_page_loads_with_synced_rows`
- Test fixtures must include all required NOT NULL fields (e.g., `form_id`)
- Do not adjust tests to match buggy code — fix the code

### CI / GitHub Actions

- Workflow: `.github/workflows/ci.yml` — runs on every push/PR to `main`
- Job: `test-and-lint` — `ruff` (E9/F63/F7/F82) then `pytest -q -m "not integration"`
- PostgreSQL 16 service container, `TEST_DATABASE_URL`/`DATABASE_URL` = `postgresql://test_user:test_pass@localhost/ilcf_test`
- `tests/conftest.py` creates a fresh schema per test inside `ilcf_test`
- Tests requiring PostgreSQL extensions (e.g., `pg_trgm`) must be marked `@pytest.mark.integration` — CI skips these
- **Boolean columns**: fixtures must use PostgreSQL `BOOLEAN` (`TRUE`/`FALSE`), not SQLite-style `INTEGER`. Source SQL must compare booleans with `= TRUE`/`= FALSE`, not `= 0`/`= 1`.
- `sqlite3.connect()` calls (e.g., threading tests) must respect `check_same_thread=True` — close connections in their creator thread.

## Performance-First Delivery Policy (Required)

For any significant code path (imports, migrations, cross-matching, analytics refreshes, route queries, caching changes), treat performance as a first-class requirement.

1. **Measure baseline before refactor** — runtime + throughput on realistic data volume. Routes: 1 cold + 2-3 warm passes (use median warm). CLI: wall-clock start/end + rows processed.
2. **Evaluate fast paths before deep implementation** — compare at least two viable approaches based on measured end-to-end runtime, not intuition. Typical comparisons: SQL set-based vs. Python post-processing; incremental vs. full rebuild; COPY/bulk insert vs. row-by-row; parallel vs. single-writer under contention.
3. **Optimization order**: reduce rows scanned → reuse cached/materialized outputs → optimize writes (batch/bulk) → tune parallelism last.
4. **Completion gate**: re-measure after changes, report before/after numbers, iterate or document why no gain is observed.
5. **Document**: update `README.md` + relevant runbooks with the fastest known command pattern. Call out when a "fast" mode is unsafe in production.

## Code Style

- PostgreSQL connection-per-request via Flask app context.
- Follow existing patterns: dataclass models, Jinja2 templates extending `base.html`, route blueprints.
- Use `_table_exists()` before querying cross-matching tables (may not exist in fresh DBs).
- Use `_scalar()` helper for safe single-value queries with defaults.
- Prefer `INSERT ... ON CONFLICT DO UPDATE` (upsert) for idempotent writes.
- Use chunked batch inserts (`_chunked()`) for large data loads.
- Use `psql` for ad-hoc queries, not `sqlite3`. Local: `psql ilcf`. Prod: `psql -h localhost ilcf`.
- Shell commands shown to the user must be single-line or properly escaped.

## Memory & Performance

- For 100K+ row datasets, always use SQL JOINs + server-side filtering. Never load entire tables into Python memory.
- Cross-matching: batch with LIMIT/OFFSET or cursor pagination.
- Watch for OOM on the 16GB production server.

## Token/Credit Efficiency Policy (Required)

Before starting any non-trivial process, evaluate whether the user can run it directly with lower credit/token cost than agent execution.

- If user handoff is feasible and cheaper, pause first with: exact command, estimated savings range, expected runtime, what output to return.
- If the request is high-cost in credits/tokens, warn first with: estimated cost range, main cost driver (huge logs, broad refactors, verbose full-suite output), and cheaper alternatives (scope reduction, targeted files, summarized output, staged execution).
- If the user confirms, proceed.

## Documentation Sync Policy (Required)

After changes that introduce useful project knowledge, update both `README.md` and `CLAUDE.md` in the same task. "Useful project knowledge" includes new commands/flags, workflow updates, config/env changes, performance findings, operational caveats, troubleshooting. Do not mark implementation complete until docs are updated, or explicitly state why no doc change is needed.

## Workflow Conventions

- **Scope**: when the user says "implement all of these", they mean ALL items — do not subset. If a session is running long, checkpoint and provide a resumption prompt rather than leaving work incomplete. Confirm scope before starting large implementations.

## New Dataset Integration Workflow (Required)

For any new data source (or major new columns), evaluate integration across the platform:

1. **Schema + loader + QA**: tables/indexes in `database/schema.sql`, idempotent upserts, post-import QA metrics (null/malformed counts, min/max valid dates, freshness).
2. **Cross-matching expansion (mandatory review)**: decide if the new data should be matched against donors, committees, candidates, lobbying entities/clients, vendors, 527 entities. If yes, add match jobs in `database/cross_matching.py` with thresholds + tests, persist output in dedicated tables.
3. **Analytics + visualization integration**: identify where it belongs in dashboards (`/analytics`, `/federal-finance`, `/lobbying`, `/527`, homepage). Add new visualizations where useful. Document metric semantics.
4. **Full-system validation**: targeted tests first → full suite (`pytest -q`) → validate routes render at realistic data volumes → update `README.md` + `CLAUDE.md` + runbooks → check performance impact + add caching/materialization changes as needed.

Don't treat dataset ingestion as complete until all four areas are addressed.

## Deployment

Detailed deploy runbook (auth plumbing, server inventory, endpoint sweep, DB sync): **`docs/runbooks/deployment.md`**.

Server quick reference:
- Host: `178.156.162.56` (SSH: `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56`)
- App root: `/srv/illinois_campaign_finance/app`
- Python: `/srv/illinois_campaign_finance/shared/venv/bin/python3`
- Env file: `/srv/illinois_campaign_finance/shared/.env`
- Service: `ilcf-web.service` (legacy `illinois-web.service` stays disabled)

### Operational rules

- Default to REMOTE/PRODUCTION when discussing deployment unless explicitly told local.
- Always verify actual server paths, service names, directory structures — never assume defaults.
- Use `python3` (not `python`) on the server. Systemd `ExecStart` must be single-line. Env vars must be **exported**, not just set.
- Stop temporary debug servers (`run.py runserver --port 5051`) after debug cutover tests.

### Long-running server tasks (>10 min)

Do **NOT** run autonomously via Claude. Provide the user the exact command to run themselves. Examples: `run-cross-matching --only all`, `import-irs527`, `refresh-analytics` on large datasets. Always estimate runtime before handoff. Provide fastest validated safe command first (with incremental flags), then fallback/recovery commands.

**Exception (OpenBook only)**: long-running OpenBook scrape/import commands (`import-openbook-batch` and related flows) are allowed autonomously when explicitly requested.

### Deploy steps

1. Run local tests: `pytest -q`
2. Push to main: `git push origin main`
3. SSH + pull (deploy key, no PAT):
   ```bash
   ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
     "cd /srv/illinois_campaign_finance/app && git pull --ff-only origin main"
   ```
4. Install deps: `/srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt`
5. Init DB (schema migrations): `... python3 run.py init-db`
6. Refresh analytics (if needed): `... python3 run.py refresh-analytics --with-snapshot`
7. Restart: `systemctl restart ilcf-web.service`
8. Verify: `systemctl status ilcf-web.service --no-pager && curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/`

## Data Refresh Schedule

**Weekly** (routine):
1. ISBE: `sunshine-import --download` (hardened native path — see "ISBE bulk download" above). Every few days near filing deadlines. Then gate: `validate-freshness --source isbe`.
2. FEC main sync: `sync-fec-il-federal --refresh-cache`. **The `--refresh-cache` flag is required** — without it the sync replays the legacy payload cache (`api_calls_made=0`) and refreshes **nothing**, while still reporting success (see "FEC silent no-op" below). Then gate: `validate-freshness --source fec`.
3. Post-import chain: `run-cross-matching --only all` → `refresh-analytics --with-snapshot` → `systemctl restart ilcf-web.service`.

**Monthly**: FEC backfills (`backfill-fec-schedule-{a,b,e}` — Schedule E weekly during election season), `import-irs527`, `import-chicago-phase1`, `import-openbook-batch` as needed.

**As available**: `import-lobbying` when a new daily extract publishes (weekly is reasonable).

**Notes**:
- `refresh-fec-local-donor-matches` runs automatically after `sync-fec-il-federal` (via `--refresh-local-matches` default). Standalone only after a backfill or if that flag was skipped.
- `run-cross-matching` + `refresh-analytics` are long-running — use `tmux`/`screen` on the server.

### FEC silent no-op + freshness gate

**FEC silent no-op (2026-06-24):** `sync-fec-il-federal` routes every API request
through a legacy SQLite payload cache (`database/federal_fec._request_with_cache`).
A cache hit does **not** increment `api_calls_made`, so a routine sync can report
success with `api_calls_made: 0` and re-upsert identical rows — leaving Schedule A
months stale (it was stuck at 2025-12-31). **Always pass `--refresh-cache`** to force
a live pull. Schedule B/E are only refreshed by `backfill-fec-schedule-{b,e}`, not the
main sync.

**`validate-freshness` gate** (`database/data_freshness.py`): post-refresh gate that
checks `MAX(date)` per source table and exits non-zero if data is stale/empty/missing
— the backstop that catches a no-op refresh regardless of cause. Run it as the final
step of any refresh (`--source isbe` / `--source fec` to gate one half). Signals:
ISBE uses `isbe_filed_docs.received_datetime` (transaction dates lag ~4-5 months and
would false-positive); FEC uses Schedule A/B/E transaction dates. Thresholds are
generous (catch broken refreshes, not normal filing lag) — see `DEFAULT_SPECS`.
Tests: `tests/test_data_freshness.py` (includes the no-op regression).

## Common Operations

```bash
# IL SOS lobbying (daily extract — persists lobbyist profile + entity/client addresses + client_status)
python run.py import-lobbying --file Bulk_download/Lobbyist_Entity_Client_Data_Daily_<YYYYMMDD>.csv

# IRS 527 (Illinois-only fast path)
python run.py import-irs527 --file Bulk_download/IRS_data/.../FullDataFile.txt --illinois-only

# Chicago Phase 1 Socrata (datasets: rsxa-ify5, s4vu-giwb, p9p7-vfqc, pahz-egmi)
python run.py import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert

# OpenBook batch (smart-search by default — generates short search terms from verbose names,
# stores all matches per seed; --no-smart-search reverts to verbatim single-match)
python run.py import-openbook-batch --generate-seeds --max-vendors 20
# Targeted case study:
python run.py import-openbook-batch --seed-source ameren_case_study --seed-like 'AMEREN%' --max-vendors 20

# Cross-matching + analytics
python run.py run-cross-matching --only all
python run.py refresh-analytics --with-snapshot
```

## Database Notes

- PostgreSQL 16 (`ilcf`), schema in `database/schema.sql`. Materialized views refreshed via `refresh-analytics`.
- Cross-matching results live in dedicated match tables: `irs527_director_donor_matches`, `irs527_director_candidate_matches`, `irs527_director_address_matches`, `irs527_org_address_matches`, `lobbying_donor_matches`, `lobbying_expenditure_matches`, etc.
- `irs527_contributions` — parsed type-A records (who donates TO 527 orgs).
- Lobbying tables: `lobbying_entities` / `lobbying_clients` (with address/city/state/postal + `client_status`), `lobbying_lobbyists`, `lobbying_lobbyist_registrations` (lobbyist↔entity↔client/year; client nullable for unassigned rows).
- All IRS 527 tables prefixed `irs527_`.
- OpenBook tables: `openbook_vendor_seed` (candidate names), `openbook_vendor_match` (autosuggest matches; `UNIQUE(seed_id, openbook_vendor_key)`; `search_term_used` tracks which smart-search term produced each match), `openbook_contracts_raw` / `openbook_contract_warrants` / `openbook_contract_detail_status` (scraped contract data), `openbook_contributions_raw`, `openbook_scrape_runs`.
- OpenBook routes: `/openbook/` (resolved vendor directory) and `/openbook/<vendor_key>` (vendor detail with seed provenance). Cross-matching uses `openbook_vendor_seed → openbook_vendor_match` as canonical linkage to source datasets.
