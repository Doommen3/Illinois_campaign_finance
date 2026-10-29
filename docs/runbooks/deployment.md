# Deployment Runbook

Detailed deployment + auth reference. The short version lives in `CLAUDE.md` under "Deployment". This file covers the auth plumbing and recovery paths.

## Server inventory

| Item | Value |
|------|-------|
| Host | 178.156.162.56 |
| SSH | `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56` |
| App root | `/srv/illinois_campaign_finance/app` |
| Python | `/srv/illinois_campaign_finance/shared/venv/bin/python3` |
| Pip | `/srv/illinois_campaign_finance/shared/venv/bin/pip` |
| Env file | `/srv/illinois_campaign_finance/shared/.env` |
| DB | PostgreSQL `ilcf` (prod via `DATABASE_URL` in `.env`) |
| Web service | `ilcf-web.service` |
| Legacy service (keep disabled) | `illinois-web.service` |

## SSH authentication

The SSH key `~/.ssh/hetzner_ed25519` passphrase is stored in **macOS Keychain via ssh-agent**, configured in `~/.zshrc`:

```bash
ssh-add --apple-use-keychain ~/.ssh/hetzner_ed25519 2>/dev/null
```

SSH, rsync, scp, and Claude Code autonomous SSH all work without passphrase prompts. If the agent loses the key (e.g., after OS update), re-run `ssh-add --apple-use-keychain ~/.ssh/hetzner_ed25519` and enter the passphrase once.

## Server-side GitHub auth (deploy key)

The server uses a **GitHub deploy key** for git pull access. No PAT needed.

- Deploy key (read-only): `/root/.ssh/github_deploy` (private), `/root/.ssh/github_deploy.pub`
- SSH config (`/root/.ssh/config`):
  ```
  Host github.com
    User git
    IdentityFile /root/.ssh/github_deploy
    IdentitiesOnly yes
  ```
- Remote URL: `git@github.com:Doommen3/Illinois_campaign_finance.git` (SSH, not HTTPS)
- `git pull --ff-only origin main` works directly on the server with no credentials prompt.

**Do NOT** switch the remote URL to HTTPS or embed a PAT. If the deploy key stops working: check that `/root/.ssh/github_deploy` exists (permissions `0600`) and that the public key is still registered under **Settings > Deploy keys** in the GitHub repo.

## Local GitHub auth

- Username: Doommen3
- Remote: HTTPS, auth via classic PAT in macOS Keychain (`credential.helper = osxkeychain` in `.gitconfig`)
- `gh` CLI is installed and authenticated (fine-grained PAT) for API operations (`gh api`, `gh pr`, etc.)
- Git push uses the classic PAT stored in osxkeychain (the fine-grained PAT in `gh` doesn't have git push scope)
- If push returns 403, re-store the classic PAT:
  ```bash
  printf 'protocol=https\nhost=github.com\nusername=Doommen3\npassword=<PAT>\n' | git credential-osxkeychain store
  ```

## Server command pattern

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
PIP=/srv/illinois_campaign_finance/shared/venv/bin/pip
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a
$PYTHON run.py <command>
```

## Syncing production DB to local

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "pg_dump -Fc ilcf" > data/ilcf_prod.dump

pg_restore --clean --if-exists -d ilcf data/ilcf_prod.dump
```

**Do NOT** run `init-db`, `run-cross-matching`, or other DB-writing commands on the server while a dump is in progress.

Local DB: `postgresql://devin@localhost/ilcf` (matches `config.DATABASE_URL` default).

## Weekly data refresh (canonical chain)

The full prod refresh is orchestrated by `scripts/refresh_prod_chain.sh` (in this repo). Companion sanity-check: `scripts/check_row_counts.py`. After `git pull`, both are available at `/srv/illinois_campaign_finance/app/scripts/`.

Run inside `tmux` so the chain survives SSH disconnects:

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56
tmux new -s refresh
cd /srv/illinois_campaign_finance/app
LOG=/srv/illinois_campaign_finance/shared/refresh_$(date +%Y%m%d_%H%M%S).log
nohup bash scripts/refresh_prod_chain.sh "$LOG" > /dev/null 2>&1 &
echo "PID=$!  LOG=$LOG"
```

The chain runs:

1. Snapshot prior row counts → `Bulk_download/.row_counts_prev`
2. Download 12 ISBE bulk files via hardened curl (retry/timeout/User-Agent)
3. Sanity-check row counts vs. baseline (aborts if any file dropped >5% — the 2026-05-13 silent-truncation signature)
4. `sunshine-import --bulk-dir Bulk_download` (with the loader's own >1% FK-reject integrity gate)
5. `sync-fec-il-federal`
6. `run-cross-matching --only all` (longest step, ~6–60 min depending on data volume)
7. `refresh-analytics --with-snapshot`
   - 7a. Its last step (fix-8b F6) rebuilds the merged donor entities behind the all-time `/donors/` directory
     (`donor_entity_local` / `donor_entity_local_member`) from the new `analytics_donor_summary`, with the
     `rebuild-local-donor-entities` defaults (source `bulk_receipts`, thresholds 0.70 / 0.82 / 0.90, max group 400).
     It runs after the aggregates are committed and before the route-cache flush; the output line
     `local_donor_entities:` reports rows written and elapsed seconds. A dry run took 43.7 s locally (1,120,844 summary
     rows; 245,399 entities and 496,604 members would be written). If it fails, the error is logged and the command
     exits non-zero, but the aggregates stay committed; rerun `rebuild-local-donor-entities` on its own.
   - 7b. `scripts/warm_caches.py` (markers `STEP7B_START` / `STEP7B_END`; a failure logs `STEP7B_WARN` and does not fail the chain)
8. Restart `ilcf-web.service` + smoke-test HTTP 200 on `/`

Each step emits `STEP_N_START` / `STEP_N_END` markers to the log; failures emit `STEP_FAILED` + `CHAIN_FAILED`. Watch with `tail -f "$LOG"`.

Total runtime: ~60–120 min. Detach tmux during cross-matching with `Ctrl-b d`; reattach later with `tmux attach -t refresh`.

**Prod 2026-10-27 notes.** (1) `sunshine-import`'s auto-swap ran into the lock conflict described under "Release 2026-10-27" step 5 while `ilcf-web` was serving; the day's wrapper (`shared/isbe_fec_refresh_20261027.sh`) fell back to stopping the service and re-running the import, so the site was down about 20 minutes. Until the swap takes its locks up front (suggestion A12), schedule the ISBE import for a quiet window or stop the service around the swap deliberately. (2) The chain can be checked without a server login at `https://www.followthemoneyil.com` (the apex `followthemoneyil.com` answers 308 to www through Cloudflare): the homepage "Data Status" strip shows the local receipts date, the FEC candidate-totals update time and the Schedule A/B/E row-level dates, and the search page's donor rows show the `analytics_donor_summary` sync timestamp, which only `refresh-analytics` rewrites. Measured on prod 2026-10-27 (`shared/logs/isbe_fec_refresh_20261027.log`): the first `sunshine-import --download` attempt occupied 15:29–16:00Z (download, then the deadlock at the ETL's DROP block), the retry's load took 20 min with the service stopped, then FEC sync with `--refresh-cache` 6 m 49 s, `run-cross-matching --only all --workers 2` 5 m 02 s, `refresh-analytics --with-snapshot` 4 m 43 s, restart 49 s — the ISBE download and load are the long steps; everything after them takes about 17 minutes.

## Pre-deploy checks

Run locally before pushing (CLAUDE.md "Deploy steps", step 1):

**GitHub CI is retired (Devin, 2026-10-28: "No more github CI, we're doing tests locally").** The `CI` workflow is disabled in the repository settings and its triggers are manual-only; nothing runs on push or PR. The local full suite below IS the merge and deploy gate: run it on the exact commit you are about to merge, and record the counts in the release block. Never re-enable or dispatch the workflow without Devin's explicit ask.

**Running the tests.** `pytest-xdist` is in `requirements.txt`, and the suite is safe to run in parallel: `pytest -q -n auto --dist loadfile -m "not integration"` takes about 3 to 6 minutes on the M3 Pro against about 10 in one process (runs on 2026-10-28 ranged from 2:52 to 6:48; the spread is other test runs sharing the PostgreSQL server), with the same pass / skip / xfail counts (2878 / 6 / 63; the `-m` filter drops the 22 integration-marked tests that plain `pytest -q` below also runs). `--dist loadfile` keeps each test file on one worker; the entity suites rely on it, because they build one schema per file instead of one per test (`tests/pg_worlds.py`). Creating and dropping a test schema holds 800 to 1,050 PostgreSQL locks each and a default server has 6,400, so `tests/conftest.py` lets at most four processes do either at once (`ddl_slot`); without that, a dozen workers fail with "out of shared memory". `-n 4` is slower (6½ minutes). To debug one failure, run it alone in one process: `pytest -q path/to/test.py::test_name -n 0`. The roughly 2,300 schemas a run creates and drops bloat the test database's catalogs (on 2026-10-28 `pg_attribute` held 9 GB with 44 million dead rows; creating a schema took 2 to 3 times and dropping one about 6 times as long as in a new database); when the suite slows down, recreate the database while no run is active: `dropdb ilcf_test && createdb ilcf_test && psql ilcf_test -c "CREATE EXTENSION pg_trgm SCHEMA public"`.

```bash
pytest -q
pytest -q tests/test_scope_widgets.py
python3 scripts/audit/route_sweep.py --check-scope-bar --host http://127.0.0.1:5000
```

- `tests/test_scope_widgets.py` is the windowed-widget registry completeness test plus the behaviour harness. The full run includes it; run it alone first for a quick signal after template or `time_scope` changes.
- The sweep needs a local server and the local DB: start `python run.py runserver --port 5000` in another terminal and export `DATABASE_URL=postgresql://devin@localhost/ilcf` (the sweep seeds parameterized routes from it), then stop the server afterwards. The script's default `--host` is production, so always pass `--host` for the local form.
- Read the report's "Scope bar coverage" section: "Pages with more than 1 bar" must be empty. "Pages with 0 bars" lists the pages outside the scope model (admin, login, `/about`, `/privacy`, `/compare`, `/investigate`, `/person-intelligence`, ...); look into any other page that appears there.

## Endpoint sweep after deploy

```
/                                                          # homepage
/search?q=Chicago
/candidates
/federal-finance/
/analytics/
/lobbying/
/527/
/federal-finance/geo-drilldown?geo_type=state&geo_value=IL&cycle=2026
/analytics/geo-drilldown?geo_type=state&geo_value=IL&period=all
/federal-finance/races/H/01/outside-spending?cycle=2026
/527/dark-money
/527/<ein>
```

Full sweep script: see `codex.md`.

## Release 2026-10-27 (profiles / races / dated widgets / suggestions)

Ships the candidate profile, full committee and 527 donor/payee lists, `/analytics/state-races/`, committee families (admin review + public banner/grouping), the windowed-widget registry, the per-cycle donor table behind `/donors/`, and the cache/ETL fixes. Plan `.claude/plans/profiles-races-dated-widgets.md`; per-item log `docs/architecture_suggestions_2026-10-26.md` §G. The new pages read DB objects that do not exist on prod yet, so the order matters: the swap script must run **before** the restart, or Top State Races shows "not available". Use the "Server command pattern" above (`$PYTHON`, `$PIP`, env exported — `swap_bulk_to_isbe.py` and `committee_families.py` read `DATABASE_URL` from the environment). Step 6 is a long-running task: run it in `tmux`.

1. Pull: `cd /srv/illinois_campaign_finance/app && git pull --ff-only origin main`
2. Dependencies: `$PIP install -r requirements.txt`
3. Schema: `$PYTHON run.py init-db` — creates `committee_family`, `committee_family_member`, `analytics_donor_cycle_summary` and the `analytics_donor_committee_agg (source, committee_id)` index (`idx_analytics_donor_committee_agg_source_committee`).
4. Expenditures index (the ETL DDL carries it for future imports; prod needs it once): `sudo -u postgres psql -d ilcf -c "CREATE INDEX IF NOT EXISTS idx_isbe_condensed_expenditures_committee_date ON isbe_condensed_expenditures (committee_id, expended_date);"`
5. Race feed + compat views: `$PYTHON scripts/swap_bulk_to_isbe.py` — creates the `isbe_race_candidacies` view (and recreates the `bulk_*` compat views and `bulk_candidate_committee_finance_agg`). Without it Top State Races and `/analytics/state-races/` show "not available".
   **Prod 2026-10-27 — run this step with the service stopped.** With `ilcf-web` serving, the script deadlocked (`DeadlockDetected`: the compat `DROP VIEW … CASCADE` locks `isbe_bulk_receipts_clean_compat` and then the `bulk_receipts_clean` wrapper, while a web request holds the wrapper and expands into the compat view) and four retries with `lock_timeout=4s` all timed out — bot traffic keeps share locks on the compat views almost continuously. The transaction rolls back cleanly (views intact), so a failed run is safe. Working path: run step 6 first (it only reads the views; it must have finished, since its share locks would block the swap too), then `systemctl stop ilcf-web.service` → `pg_terminate_backend` any leftover `ilcf_app` backends → swap → step 7 → `systemctl start ilcf-web.service`. Prewarm runs in daemon threads, so the outage is the stop/swap/start itself (about 30 s). Script: `/srv/illinois_campaign_finance/shared/release_2026-10-27_swap.sh` (log beside it). `sunshine-import`'s auto-swap has the same exposure on prod.
6. Analytics: `$PYTHON run.py refresh-analytics --with-snapshot` — fills `analytics_donor_cycle_summary` (1,625,516 rows and 57.6 s for that step locally) and flushes the Redis route caches. Until it has run, `/donors/` keeps using the live query (the cycle table is read only when the cycle has rows).
7. Optional: `$PYTHON scripts/committee_families.py seed` — stores pending committee families only (753 families / 1,624 committees locally); nothing is public until an admin accepts a family at `/admin/committee-families`.
8. Restart: `systemctl restart ilcf-web.service` — clears the per-worker fallback caches and starts the per-worker prewarm (overview, risk, networks, relationships, top-12 state-race details).
9. Verify: the status/curl check from CLAUDE.md "Deploy steps" step 8, then the endpoint sweep above plus `/analytics/state-races/`, `/committees/1126/donors` and `/candidates/<id>`. Re-measure `/committees/1126` and `/analytics/state-races/` and record the prod numbers in `docs/runbooks/route_perf_caching.md`.

**Matview rebuild (the `NULLS LAST` fix).** The corrected filed-doc pickers are in the matview DDL of `scripts/isbe_sunshine_etl.py`, so prod gets them at the next `sunshine-import`; until then its condensed views keep the old pickers (locally 3,510 committees had a condensed receipts total more than 1 % off their raw non-archived total; after the rebuild, 741). To get them at deploy instead, rebuild by hand in a quiet window (82 s locally): drop the five matviews (`DROP MATERIALIZED VIEW IF EXISTS … CASCADE` for `isbe_committee_money`, `isbe_candidate_money`, `isbe_condensed_expenditures`, `isbe_condensed_receipts`, `isbe_most_recent_filings`; the `CASCADE` also drops the dependent compat views until the swap script recreates them), call `create_materialized_views(conn)` and `create_compat_views(conn)` from `scripts/isbe_sunshine_etl.py`, then run `scripts/swap_bulk_to_isbe.py`. Do this between steps 4 and 5 (it ends with the swap script) and before step 6, so the cycle table is built from the corrected views.

**Expected differences after the release** (local numbers; prod will differ in size): race totals drop because archived receipts are excluded, while the 2026 race list grows from 599 to 1,465 races (443 to 1,012 with receipts in the window) because candidacies now define the cycle; after a matview rebuild the condensed non-archived receipts total rises from $5.40B to $6.46B; windowed 527 totals shrink (local `/527/dark-money` window total $4.51M to $224K) because `irs527_contributions.date` holds amounts rather than dates (A11; prod unchecked).

## Scheduled jobs on prod (systemd timers, inventory 2026-10-27)

| Timer | Schedule | Runs | Notes |
|---|---|---|---|
| `il-campaign-fec-sync.timer` | Sundays 03:00 UTC | `/bin/bash scripts/sync-fec.sh` (`sync-fec-il-federal --cycle 2026 --contributor-state IL --max-calls 900`, then `rebuild-fec-donor-identities`, then `refresh-analytics --with-snapshot`) | Logs `shared/logs/sync-fec-<stamp>.log`; lock `shared/sync-fec.lock`. Until 2026-10-27 the script did not pass `--refresh-cache`, so every weekly run was the silent no-op (`api_calls_made: 0`, Schedule A stuck at 2026-07-01); fixed in `scripts/sync-fec.sh` — the timer picks it up at the next `git pull` on the server. |
| `il-campaign-fec-schedule-e-catchup.timer` | hourly | `scripts/fec-schedule-e-catchup.sh` (`backfill-fec-schedule-e` with refresh) | Logs `shared/logs/fec-schedule-e-catchup-<stamp>.log`. |

There is no ISBE timer: `sunshine-import` runs by hand (wrappers `shared/isbe_refresh_<date>.sh`, `shared/isbe_fec_refresh_20261027.sh`). `systemctl list-timers --all | grep il-campaign` shows the next runs.

## Live load diagnosis (added 2026-10-28)

When the site hangs, read before changing anything. Both scripts are read-only and run from the laptop (prod SSH is denied to the agent in auto mode, so Devin runs them with the `!` prefix):

- `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 bash -s < scripts/audit/prod_snapshot.sh` — load, memory, gunicorn workers, perf env keys, Redis, active queries, planner settings, analyze state of the big relations, recent journal lines.
- `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 bash -s < scripts/audit/prod_activity_sample.sh` — six samples 10 s apart of active backends, leaders separated from parallel workers, so the number of real requests behind the load is visible.
- From outside: `https://www.followthemoneyil.com` (the apex answers 308 to www through Cloudflare); the homepage "Data Status" strip and sequential `curl -w '%{time_total}'` timings of the heavy routes.

2026-10-28 case: load 9–10 on 8 cores came from `/committees/filing/<id>` (two parallel seq scans per page over `isbe_condensed_expenditures`, no `filed_doc_id` index; 956K crawlable pages) and `/donors/key/<key>` (three computed-key scans per page over the condensed receipts; 1.1M pages). Fixes: the `filed_doc_id` index and the `md5(donor key)` expression index, both in the ETL DDL now and built once by hand on prod with `CREATE INDEX CONCURRENTLY` (2.2 s and 15 s); details in `docs/architecture_suggestions_2026-10-26.md` §G A15/A16.


## Release 2026-10-28 — outside spending drill-downs (plan `.claude/plans/outside-spending-drilldown.md`)

Before `systemctl restart ilcf-web.service`, build the new partial index on prod (tiny, seconds; the ETL recreates it on the next `sunshine-import`):

```
sudo -u postgres psql -d ilcf -X -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_isbe_condensed_expenditures_part9_date ON isbe_condensed_expenditures (expended_date) WHERE d2_part LIKE '9%' AND archived = FALSE;"
```

Also from the load-time plan (`.claude/plans/load-times.md` P4): the lobbying entity/client pages' receipt lookup now has an expression index — build it the same way (55 MB, ~15 s locally; concurrent build on prod takes longer but does not block readers):

```
sudo -u postgres psql -d ilcf -X -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_isbe_condensed_receipts_donor_name_norm ON isbe_condensed_receipts (LOWER(TRIM(COALESCE(NULLIF(TRIM(first_name), ''), '') || CASE WHEN COALESCE(NULLIF(TRIM(first_name), ''), '') != '' AND COALESCE(NULLIF(TRIM(last_name), ''), '') != '' THEN ' ' ELSE '' END || COALESCE(NULLIF(TRIM(last_name), ''), ''))));"
```

Cached state-race payloads: the per-race DETAIL route recomputes a payload of the old shape itself (one without `outside_spending_window` counts as a miss); the race LIST and the overview's Top State Races keep the old row shape with new numbers, so their keys now start with `_STATE_RACE_CACHE_REV` (`webapp/routes/analytics.py`, "os2") and every entry built by the old code is simply never read again (it expires by its own TTL). No manual flush is needed for that reason — but note that a service restart never clears Redis (only the per-worker fallback LRUs); `refresh-analytics` / `sunshine-import` do. (`outside_spending_rows` items now carry `candidate_id`, `support_total`, `oppose_total`, `row_count`; the payload carries `outside_spending_window`.) Verify after the restart: `/analytics/state-races/member-school-chicago-1b?period=cycle:2026` shows Supporting / Opposing columns and both candidates; `/candidates/47517/outside-spending?period=cycle:2026` lists the line items. Semantics: `docs/outside_spending.md`.

## Observability for the next hang (load-time plan L13, 2026-10-28 — prod configuration, Devin's side)

Nothing on the box records which route or client was slow: no Caddy access log, no gunicorn access log, no `pg_stat_statements`. The 2026-10-28 diagnosis relied on sampling `pg_stat_activity` (`scripts/audit/prod_activity_sample.sh`). Before the next incident:

1. **gunicorn access log with request time** — in `ilcf-web.service` `ExecStart` add `--access-logfile - --access-logformat '%({x-forwarded-for}i)s %(t)s "%(r)s" %(s)s %(b)s %(D)sus pid=%(p)s "%(a)s"'` (journald keeps it; `%(D)s` is the request time in microseconds, `%(p)s` the worker pid, `%(a)s` the user agent). `%(h)s` would log the socket peer, always `127.0.0.1` behind Caddy, and `--forwarded-allow-ips` does not change that (it only governs scheme/forwarder headers, and `127.0.0.1` is its default); use `%({cf-connecting-ip}i)s` instead if the Caddy config does not trust Cloudflare's `X-Forwarded-For` (check it on the box — the Caddyfile is not in the repo).
2. **Caddy access log** — a `log { output file /var/log/caddy/access.log { roll_size 50mb roll_keep 5 } }` block in the site definition (the Caddyfile is not in the repo; capture it into this runbook when edited).
3. **pg_stat_statements** — `shared_preload_libraries = 'pg_stat_statements'` in `postgresql.conf` (needs one PostgreSQL restart; do it with the web service stopped), then `CREATE EXTENSION pg_stat_statements;` in `ilcf`. Afterwards `SELECT calls, mean_exec_time, query FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;` answers "what is slow" in one statement.
4. **Record the unit** — `systemctl cat ilcf-web.service` and `gunicorn --version` belong in the server inventory above; gunicorn is not pinned in `requirements.txt` (pin it when the next `pip install -r` is planned).
5. **Web connections are now labelled** (`application_name = ilcf-web`, load-time plan L2) so `pg_stat_activity` separates web load from CLI chains.


## Deployed 2026-10-29 06:18Z — merge `3398990` (search/page follow-ups `search-followups` merged as `cfd8292`; 8b donor fixes `fix-8b-donor-aggregates` merged as `3398990`)

Order actually run (Devin: "merge and deploy"): release clone `main` `13d514b` → `git merge --no-ff search-followups` (`c761e82`: one `substring_pattern` for every LIKE branch and `/search` revision sn3; folded alias twins keep their filings via `alias_keys`; all five federal Schedule E readers bounded by the race cycle's dates because `fec_schedule_e_independent_expenditures.cycle` is only the backfill's fetch cycle; the 527 organization period over ISO/compact text dates only) = `cfd8292` → `git merge --no-ff fix-8b-donor-aggregates` (`81406be`: the analytics donor filter constants drop their redundant D-2 EXISTS and use the live contributions predicate — specialist-reconciled to the sworn quarterly totals to the cent; in-kind-only donor nodes carry no donor-page link; donor page note "Itemized monetary contributions (D-2 part 1)…"; `refresh-analytics` ends by rebuilding `donor_entity_local`; monthly spike flags skip months after the latest quarterly) = `3398990`, 31 files vs `13d514b`, no conflicts → gate on the exact head: full suite `pytest -q -p no:cacheprovider -n auto --dist loadfile -m "not integration"` → **3,047 passed, 6 skipped, 63 xfailed in 4:21**, ruff E9/F63/F7/F82 clean, `scripts/audit/check_manual_tests.py` OK → smoke on a temporary `runserver --port 5053` (prewarm off): `/investigate?q=O'Neil` with no plain Neil, `/search?q=xamplet`, `/federal-finance/races/H/01/outside-spending?cycle=2026` with no 2022 dates, `/527/813554739`, `/candidates/13505/network` and its money-flow JSON, the Frankfort 2018 race page, `/candidate-finance/?q=%` empty — all 200 → scope-bar sweep: 250 probes in 135 s, 186 ok / 24 ok-empty / 14 4xx (the known focus-sankey 400s and viz-lab 404s) / 10 timeouts (heavy analytics cold builds) / 0 5xx, 34 known zero-bar pages, no page with more than one bar; server stopped → `git push origin main` `13d514b..3398990` 06:18:24Z (CI retired) → shared tree `git pull --ff-only` (none of the other session's dirty files touched) → prod `git pull --ff-only` `13d514b` → `3398990`, `systemctl restart ilcf-web.service` 06:18:29–06:18:34Z, local `/` 200 first try. Skipped deliberately: `pip install`, `init-db`, index builds (no dependency, schema or index change), cache flush (`/search` keys carry sn3; entity payloads carry no hrefs). Public checks 06:18:40Z all 200: `/investigate?q=O'Neil` no plain Neil (0.47 s); `/search?q=xamplet` 0.47 s; `/federal-finance/races/H/01/outside-spending?cycle=2026` no 2022 dates (0.21 s); `/candidates/13505/network` 0.46 s; the Frankfort race 0.25 s; `/candidate-finance/?q=%` 0.39 s; service active, 0 errors in the journal. **Residual found at the public check:** `/527/813554739` still "Period: 1000 to 995" — a second code path overrides the reports' period with MIN/MAX of the TEXT transaction `date` columns (A11) → fixed in the follow-up deploy below. **Prod data step owed:** `refresh-analytics --with-snapshot` (Devin, detached on the server) rebuilds the donor aggregates under the new filter and, as its new last step, `donor_entity_local`; until it runs the donor pages show the old figures.

**Follow-up deploy 06:39Z — merge `4529328`** (FB-M4b/M4c on `search-followups`, `cf480ab`): the 527 organization period is the union of the reports' span and the valid transaction dates; both go through `_iso_text_date_sql` (ISO or 8-digit text → date, anything else NULL), so malformed text can neither replace nor widen the span. Gate on the exact head: first full suite 3,047 passed + 1 failed (`tests/test_irs_527_parse_edge_cases.py::TestSQLiteThreadingSafety::test_concurrent_reads`, untouched by the merge — `git diff --name-only 3398990 HEAD` = `webapp/routes/irs527.py`, `tests/test_irs527_routes.py`, `.claude/data-dictionary.md`); the test alone passed 3/3 and the full suite re-run **3,048 passed, 6 skipped, 63 xfailed in 3:10, exit 0**. `git push origin main` `3398990..4529328` 06:38Z → shared tree `git pull --ff-only` → prod `git pull --ff-only` + `systemctl restart ilcf-web.service`, active at 06:39:00Z, local `/` 200. Public check 06:39Z: `/527/813554739` **"Period: 2016-08-15 to 2025-12-31"** (200, 0.55 s) — MT-017 passes; its known-failing note is withdrawn in `docs/manual_tests.md`.

## Deployed 2026-10-29 04:37Z — merge `9fed404` (to-do item 9 manual test register `633cfb8` fast-forwarded; search Phase 2 M1–M7 merged as `9fed404`)

Order actually run (Devin: "merge and deploy"): release clone `main` `2df4ae8` → `git fetch` terminal 2's `worktree-ui-improvements` from the shared tree and `git merge --ff-only 633cfb8` (to-do item 9: `docs/manual_tests.md` with 24 tests, `scripts/audit/check_manual_tests.py`, its plan — documents only) → `git merge --no-ff search-phase2` (`d1e9e3d`, M1–M7: the Phase 2 helpers in `database/name_normalize.py` and every name-search caller rerouted (`committees`, `lobbying`, `irs527`, `api`, `candidate_finance`, `main`, `experimental`, `database/models.py`; `webapp/utils/search_normalize.py` deleted); the Part-9 matcher's hyphen/nickname rules and `name_keys_match` alias-twin fold in the race builder and the entity network; the P2-19 donor-section query plan; the apostrophe rule with `SEARCH_CACHE_REVISION` sn2; three GIN indexes in `database/schema.sql`; `scripts/audit/part9_coverage.py`; docs) = `9fed404`, 28 files vs `2df4ae8` → gate on the exact head: full suite `pytest -q -p no:cacheprovider -n auto --dist loadfile -m "not integration"` → **3,017 passed, 6 skipped, 63 xfailed in 3:23**, ruff E9/F63/F7/F82 clean → scope-bar route sweep against a temporary `runserver --port 5053` from the clone (prewarm off): 250 probes in 136 s, 182 ok / 24 ok-empty / 14 4xx (the four `focus-sankey` 400s and ten viz-lab local-data 404s, as before) / 13 timeouts (the heavy analytics cold builds at the sweep's 30 s cap, the same set as the 03:19Z sweep) / 1 500 (`/analytics/donors?period=all` at 30.1 s — the server-side statement timeout on the same cold build that was a client timeout in the previous sweep; `period=cycle:2026` 200 in 2.8 s; the `database/analytics.py` change in this merge is the race-builder fold, not that page), "Pages with more than 1 bar" empty, 34 zero-bar pages = the known out-of-scope set; direct smoke on the clone: `/search?q=O'Neil` Committees 32 / Donors 50 with no plain Neil, `/search?q=Women's` Committees 50, the Frankfort 2018 race page 8,233.30, every Phase 2 list page 200; temporary server stopped → `git push origin main` `2df4ae8..9fed404` at 04:36:56Z (CI retired — nothing fires) → local shared tree `git pull --ff-only` (the merge touches none of the other session's dirty files) → prod BEFORE the restart: `CREATE INDEX CONCURRENTLY IF NOT EXISTS` for `idx_lobbying_entities_name_tsv`, `idx_lobbying_clients_name_tsv`, `idx_irs527_organizations_name_tsv` with the DDL text from `database/schema.sql`, then `ANALYZE` of the three tables — 04:37:37–04:37:38Z, all `indisvalid`, 176 / 264 / 200 kB (tables of 1.8k / 3.3k / 2.3k rows) → prod `git pull --ff-only` `6674379` → `9fed404`, `systemctl restart ilcf-web.service` 04:37:39–04:37:44Z, local `/` 200 on the first try. Deliberately skipped: `pip install` (no dependency change) and `init-db` (the only schema change is the three indexes, already built by hand); no cache flush (the `/search` keys carry revision sn2; the other Phase 2 routes are uncached or TTL-bound). Public checks at 04:38:39Z, all 200: `/search?q=O'Neil` Committees 31 / Donors 50 / Candidates 25 with **no plain Neil** (0.31 s cold, 0.25 s warm; prod listed every Neil before); `/search?q=Women's` Committees 50 / Donors 50 (the possessive keeps its plain reading); `/search?q=Mike+Frerichs` 3 / 7 / 5 exactly as after Phase 1; `/committees/?q=Mike+Frerichs` and `?q=Michael+Frerichs` 200; `/lobbying/?q=Women's` 0.14 s; `/527/?q=Illinois` 0.18 s; `/analytics/state-races/supervisor-township-frankfort?cycle=2018` shows **$8,233.30**, not the doubled 16,466.60 (0.27 s cold / 0.14 s warm); `/investigate?q=O'Neil` 200 with six plain-Neil rows from the transactional substring branch (decision P2-2a joins the normalized tokens with `%`, so "O NEIL" becomes `%O%NEIL%` — follow-up P2-32; not a regression, the page listed every Neil before). Prod `pg_stat_user_indexes` right after those requests: all three new indexes scanned (entities 2, clients 1, 527 organizations 3). Service active, 0 errors or tracebacks in the journal since the restart. Register: MT-025 (search Phase 2) appended with this deploy, checker 25 / 25 OK. What the change does: `docs/runbooks/route_perf_caching.md` ("Name search outside /search" and the apostrophe bullet), `docs/outside_spending.md` (matcher rules).

## Deployed 2026-10-29 03:19Z — merge `6674379` (to-do item 1: click drill-down panels on the entity Network tab; plan `.claude/plans/money-flow-drilldown.md`)

Order actually run (Devin: "merge and deploy item 1"): release clone off `main` `1d48d62` → `git fetch` the branch from the drill-down scratch clone → `git merge --no-ff money-flow-drilldown` (`6ed586c`: `80e8719` tests, `fc6e82a` implementation, `4f8cf82` `docs/entity_networks.md`) → one runbook bullet in `docs/runbooks/route_perf_caching.md` (entity-tab cache keys, `ENTITY_CACHE_REV` ea2) = `6674379`, 14 files vs `main` → gate on the exact head: ruff clean, full suite `pytest -q -p no:cacheprovider -n auto --dist loadfile -m "not integration"` → **2,966 passed, 6 skipped, 63 xfailed in 4 min 42 s** (+19 vs `ddd5867` = the new drill-down cases) → scope-bar route sweep against a temporary `runserver --port 5053` from the clone (port 5000 is held by macOS Control Center): 250 probes in 160 s, 181 ok / 24 ok-empty / 14 4xx (the four `focus-sankey` 400s without params, ten viz-lab `triple-pipeline` + `geometry` 404s — local-only data, as before) / 15 timeouts (the heavy analytics cold builds at the sweep's 30 s cap with prewarm off while the full suite ran — not touched by this change), "Pages with more than 1 bar" empty, 34 zero-bar pages = the known out-of-scope set; direct smoke on the clone: candidate + race network pages 200, both JSON views 200, `members` reconcile to every aggregate node → `git push origin main` (no workflow fires — CI retired) → prod `git pull --ff-only` + `systemctl restart ilcf-web.service` 03:19:11–03:19:18Z (no `pip install`, no `init-db`: no dependency or schema change; no cache flush needed — `ENTITY_CACHE_REV` ea2 makes the old entries unreachable, and the four changed `entity_views` scripts carry `?v=20261029-1`) → public checks: `/`, `/candidates/28183/network`, a state-rep race network page, `/static/js/entity_views/focus_panel.js?v=20261029-1` all 200; `/candidates/28183/network.json?view=money_flow` carries `members` for 5 aggregates reconciling to the cent on prod data (truncated donors 1,644), 614 ms cold → 228 ms warm. Temporary servers stopped. What the change does: `docs/entity_networks.md`.

## Deployed 2026-10-29 02:12Z — merge `ddd5867` (test-speed T1/T2 merged as `bc99643`; search Phase 1 S1–S5 merged as `ddd5867`)

Order actually run (Devin: "merge test-speed and deploy search Phase 1"): release clone off `main` `82aa989` → `git merge --no-ff origin/test-speed` (`bc99643`, 8 files) → `git merge --no-ff origin/search-names` (`ddd5867`, 10 files; no conflicts, the two branches share no file) → local gate on the exact merge commit with the new runner `pytest -q -p no:cacheprovider -n auto --dist loadfile -m "not integration"`: **2,947 passed, 6 skipped, 63 xfailed in 3:08**, ruff E9/F63/F7/F82 clean → prod BEFORE the restart: the five name GIN indexes built with `CREATE INDEX CONCURRENTLY IF NOT EXISTS …` using the DDL text taken verbatim from the merged `scripts/isbe_sunshine_etl.py`, `scripts/swap_bulk_to_isbe.py` and `database/schema.sql` (02:08:35–02:08:47Z, all `indisvalid`; `idx_analytics_donor_summary_name_tsv` 17 MB, the other four 16 kB–1.8 MB) then `ANALYZE` of the five tables → `main` pushed `82aa989..ddd5867` at 02:12:09Z (CI retired — nothing fires) → local shared tree `git pull --ff-only` (the merge touched none of the other session's dirty files) → prod `git pull --ff-only` `bbbe23e` → `ddd5867`, `systemctl restart ilcf-web.service` 02:12:13–02:12:16Z. Deliberately skipped: `pip install` (`requirements.txt` only gained `pytest-xdist`, test tooling) and `init-db` (the only schema change is the two `schema.sql` indexes, already built by hand); the local route sweep with a server (the change is confined to `/search` and test infrastructure; the public checks below cover it). Public checks: `/search?q=Mike+Frerichs` went from Committees 1 / Donors 4 / Candidates 1 to **Committees 3 / Donors 7 / Candidates 5** with two "via candidate" rows (Michael Frerichs, Michael W Frerichs); `Frerichs Mike` and `Michael Frerichs` return the same 3 / 7 / 5; `Frerichs` alone 4 / 35 / 6; each 0.19–0.28 s. Endpoint sweep subset all 200: `/` 0.34 s, `/search?q=Chicago` 0.44, `/candidates` 0.25, `/analytics/` 0.29, `/lobbying/` 0.17, `/527/` 0.19, `/federal-finance/` 1.47, `/527/dark-money` 0.28. Prod `pg_stat_user_indexes` after those requests: four of the five new indexes already scanned (the donor predicate plans as a Bitmap Index Scan on `idx_analytics_donor_summary_name_tsv`, 0.8 ms for the Frerichs tsquery); `idx_fec_candidate_match_name_tsv` 0 scans — the federal section ORs an unindexed `fec_name` expression and the table has 134 rows, so the planner's seq scan is right. The search cache key carries revision `sn1`, so no Redis flush was needed. Journal clean.

## Deployed 2026-10-29 01:21Z — `bbbe23e` (site-wide graph key N18, ego-graph click focus N19, candidate nodes indigo N20)

Order actually run (Devin: "ok push and deploy the key"): branch `graph-legend-focus` pushed (no workflow fires — CI retired) → local gate = full suite on the exact head in the scratch clone: 2,894 passed, 6 skipped, 63 xfailed, 22 deselected, 1 failed (`tests/test_irs_527_parse_edge_cases.py::TestSQLiteThreadingSafety::test_concurrent_reads`, a SQLite threading test unrelated to the change; green alone on re-run — a timing flake under three concurrent test runs on the machine) → `main` fast-forwarded to `bbbe23e` and pushed → prod `git pull --ff-only` `9e3a83c` → `bbbe23e` (no deps / schema change) → `systemctl restart` 01:21:30–01:21:37Z (657 Redis keys survived) → public checks: the entity Network tab, `/analytics/networks` (8 key containers) and `/analytics/relationships` (7) all load `graph_legend.js` and render `.graph-key`; the script is served with `GRAPH_STYLES`. Observation (pre-existing, not this change): `/analytics/relationships` took 8.4 s on the first request after the restart — its window-free graphs are cached in-process, not in Redis, so every restart serves them cold until the first visitor pays; candidate for the warm script / Redis migration. Journal clean.

## Deployed 2026-10-28 23:25Z — merge `9e3a83c` (PR #1: entity analytics tabs Phase A, review fixes, filings note, CI retired)

Order actually run (Devin: "merge and deploy and do the prod analyze one liner"): merge gate = the LOCAL full suite on the exact PR head `3b26945` (GitHub CI retired the same evening): ruff clean, `pytest -q -p no:cacheprovider -m "not integration"` → 2,874 passed, 6 skipped, 63 xfailed, 22 deselected in 13 min 18 s → prod `ANALYZE isbe_condensed_receipts` (751 ms; `pg_stats` rows for `idx_isbe_condensed_receipts_donor_key_md5` 0 → 1 — the hand-built A16 index had no planner statistics on prod, so the co-giving and `/donors/key` lookups were seq-scanning) → `gh pr merge 1 --merge` → `9e3a83c` on `main` → prod `git pull --ff-only` `e196649` → `9e3a83c` (`requirements.txt` / `schema.sql` untouched: no `pip install`, `init-db`, swap or refresh) → `systemctl restart ilcf-web.service` 23:24:47–23:24:48Z (1 s; 516 Redis keys survived, no warm needed — the new tabs are never warmed, revision unchanged) → public checks through Cloudflare: race Overview `/analytics/state-races/member-school-chicago-1b` 0.77 s with the entity tab bar only (N17; no global analytics subnav), `/risk` 1.72 s cold (first-ever site-wide threshold build, then cached in Redis), `/network` 0.35 s, `/candidates/39266/donors` 0.29 s, `/relationships` 0.45 s; every page one scope bar and the filings/deadlines footer note (`/about` carries the note twice: footer + its new section); journal clean. Local main tree fast-forwarded afterwards (the superseded pre-fix copies of the new modules and suites moved to the session scratchpad). Not done: the Phase A browser click-through is Devin's, now on the live site; step 7 (readable renderer + N8 probe), Geography SVGs, legend labels and docs (N9) follow; `README.md` / `CLAUDE.md` sync (CI section, deploy step 2) is owed to the session holding those files.

## Deployed 2026-10-28 15:00Z — commit `e196649` (Top State Races whole-list sort; plan `.claude/plans/race-sort-and-entity-networks.md` steps 1–2)

Order actually run (Devin: "commit push and deploy"): local full suite the same day (CI selection, 2283 passed) + ruff + a focused scope-bar check of the two changed templates on a temporary local server (`/analytics/`, two header variants, `/analytics/state-races/?sort=candidate_count&dir=asc`: one `time-scope-bar` each, 8 header links, a sort variant served in 0.03 s from the one cached build; server stopped) → commit `e196649`, push `9eb07a7..e196649` (CI free) → prod `git pull --ff-only` (10 files; `requirements.txt` and `schema.sql` untouched, so no `pip install`, `init-db`, swap or `refresh-analytics`) → `systemctl restart ilcf-web.service` 14:59:57–15:00:00Z (3 s; the 151 Redis keys survived) → `scripts/warm_caches.py` 15:00:24–15:02:00Z (96 s for the four presets; Last 12 months 24 s, All time 38 s; 152 → 160 keys: the "os3" overview and race-list entries — the "os2" ones are unreachable and expire with their TTL). Public checks through Cloudflare: overview warm 0.23–0.28 s; every header variant a cache hit (Donors ↓ 0.25 s, Race ↑ 0.38 s, Outside Spending ↓ with `period=all` 0.29 s, Candidates ↑ 0.27 s); `/analytics/state-races/?sort=candidate_count&dir=asc` 0.40 s, `?sort=top_candidate_share&dir=desc` 0.98 s; each page one scope bar, 8 `state_race_sort` links, `data-server-sort`, no sort `<select>`; the preview order changes with the column (default: Governor, Representative 7, Secretary of State; Donors ↓: Governor, Comptroller, Cook County Board President; Race ↑ and Candidates ↑: small municipal races first). `/about` keeps `public, max-age=0, s-maxage=300` (`cf-cache-status: DYNAMIC`); journal clean after the restart. CI for `e196649`: green (run 40711410947).

## Deployed 2026-10-28 11:45–12:00Z — commit `295da60` (all three 2026-10-28 blocks below, plus the review fixes R1–R6)

Order actually run (Devin: "Ratify the decision, commit, push and deploy"): local full suite + scope-bar sweep → push `4acc572..295da60` (CI free) → `git pull`, `pip install` (no new deps) → pre-checks (`pg_trgm` present; gunicorn 25.0.3; Redis `maxmemory 0`, policy `noeviction`, 20 keys) → the five `CREATE INDEX CONCURRENTLY` builds (part9_date 1 s, donor_name_norm 9 s, name_trgm 10 s, geo 3 s) and the geo columns under `SET lock_timeout = '3s'` (first attempt) → `init-db` (1 s, no lock) → `systemctl stop` 11:48:47Z, `swap_bulk_to_isbe.py` 15 s (aggregate → MATERIALIZED VIEW 112,527 rows; `isbe_race_candidacies` 86,590 rows; its new post-swap flush deleted 20 route keys), `systemctl start` 11:49:03Z (16 s of downtime) → Redis lock smoke OK (`SET NX` once, refused twice, compare-and-delete 1; `ilcf:flush_gen` set) → `refresh-analytics --with-snapshot` 11:50:00–11:54:18Z (geo keys filled, 0 NULL; new fence token) → `scripts/warm_caches.py` 11:54:42–11:59:36Z (2026 Cycle 66 s, 2024 Cycle 65 s, Last 12 months 57 s, All time 104 s; 100 keys, 0 locks). Post-deploy, through Cloudflare: `/about` carries `cache-control: public, max-age=0, s-maxage=300` (`cf-cache-status: DYNAMIC` — no Cache Rule yet); anonymous `?refresh_cache=1` → 302 `/analytics/`; `/%09/example.org/login?refresh_cache=1` → 302 to `/%09/example.org/login` on this origin (open redirect closed); robots.txt 19 Disallow lines; warm pages: overview 0.26 s, `?period=all` 0.24 s, risk 0.27 s, geography 0.16 s, donors 0.14 s, state races 0.31 s, networks 0.40 s, relationships 0.37 s, home 0.20 s; geo drill-down IL all-time 2.0 s cold → 0.22 s warm, Chicago city 1.7 s cold, IL default window 2.7 s (windowed path, LD8); `/search?q=smith` 0.54 s then 0.25 s, no timed-out section; `?load_mode=full` has no snapshot form; Chicago 1B list row and detail card agree ($578,014.63 / 295.34 %); `pg_stat_activity` shows `ilcf-web` during a request; journal clean. Not done, Devin's side: the Cloudflare Cache Rule, the `ExecStartPost` warm unit, the gunicorn access-log flags, `pg_stat_statements`, and Redis `CONFIG SET maxmemory 1gb` + `maxmemory-policy volatile-lru` (+ `CONFIG REWRITE`) — the agent's permission layer refused that config change, so prod still runs `maxmemory 0` / `noeviction` (unbounded; 100 keys ≈ a few MB today).

**CI follow-up (same day).** The run for `295da60` failed 5 tests in `tests/test_request_hygiene.py` (`Vary: Cookie` on anonymous pages): CI resolves the unpinned `flask>=3.0.0` to Flask 3.1.3 / Werkzeug 3.1.9, where the session counts as "accessed" on any touch of the proxy — `base.html`'s `get_flashed_messages()` touches it on every page — while local and prod run Flask 3.0.3 (prod's `/about` had no `Vary` after the deploy). Fix, version-independent: `EdgeCacheSessionInterface` in `webapp/app.py` skips the `Vary: Cookie` header for a request that carried no session cookie and whose session stayed empty and unmodified (a cookie-bearing request, a flash or a login still varies and sets the cookie as before); three anchor tests, green under Flask 3.0.3 and 3.1.3; full suite 2270 passed. Deployed as `26dceab`: `git pull` + `systemctl restart` 12:17:03–12:17:12Z (9 s; the 119 warm Redis keys survived the restart; `/about` keeps the public edge header with no `Vary`; overview warm 0.31 s); CI for `26dceab` green (run 40686491747; the `295da60` run is the red one). Pin note: a rebuilt venv will pick up Flask 3.1.x; the app no longer depends on the difference.

## Release 2026-10-28 — load-time plan Phase 1 (`.claude/plans/load-times.md` P1/P2/P4/L12)

Behaviour changes to know before the restart:

- **Cache busting needs a sign-in.** `?refresh_cache=1`, `refresh_full`, `sync_full` from an anonymous request now 302 to the same URL without them (LD4). Sign in at `/auth/login` first, or set `REFRESH_PARAMS_REQUIRE_LOGIN=0` in `shared/.env` to restore the old behaviour.
- **Edge caching headers.** Anonymous 200/404 HTML carries `Cache-Control: public, max-age=0, s-maxage=300` (`HTML_EDGE_CACHE_SECONDS`, 0 disables) and no `Vary: Cookie`. Cloudflare only honours it with a Cache Rule (Devin: "Cache eligible" for `www.followthemoneyil.com` when the request has no `session` cookie; bypass `/admin`, `/manual-entry`, `/auth`, `/api`, `/experimental`).
- **robots.txt** is served by the app (LD1 rules). Cloudflare's managed robots.txt may prepend its block; check `curl -s https://www.followthemoneyil.com/robots.txt` shows the `Disallow` lines.
- **Web connections** carry `statement_timeout=30s`, `max_parallel_workers_per_gather=2`, `application_name=ilcf-web` (`WEB_STATEMENT_TIMEOUT_MS`, `WEB_MAX_PARALLEL_WORKERS_PER_GATHER`). CLI and prewarm connections are unchanged — and so are the `/analytics/networks` graph threads, which open plain connections of their own (no timeout, no `ilcf-web` label; plan LD14), so in `pg_stat_activity` a cold networks build looks like CLI load.
- **Search timeout is real now** (700 ms per section, `SEARCH_QUERY_TIMEOUT_MS`): until P6 materializes `bulk_candidate_committee_finance_agg` and adds the donor-name trigram index, common-name searches may report the candidates/donors sections as timed out instead of taking 5–8 s.
- **Banner freshness** reads the matview (0.9 ms instead of a 1.2 s scan) and `GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS` (already 900 in prod `.env`) is finally honoured.
- `/candidates/` (trailing slash) serves the landing page.

Post-deploy checks: `curl -sI https://www.followthemoneyil.com/about | grep -iE 'cache-control|vary'`; `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' 'https://www.followthemoneyil.com/analytics/?refresh_cache=1'` → `302 …/analytics/`; `sudo -u postgres psql -d ilcf -X -At -c "SELECT application_name, count(*) FROM pg_stat_activity GROUP BY 1"` shows `ilcf-web`.

## Cache warm step (load-time plan P7 / L8, 2026-10-28)

- **`scripts/warm_caches.py`** fills Redis with the analytics overview, risk, networks and relationships payloads (plus geography, donors and the overview's top-12 race details) and the homepage caches, for the four scope presets (current cycle, previous cycle, last 12 months, all time). Keys already in Redis are skipped; it returns when done and exits 0 (local prod copy, cold: 360 s for the four presets — 2026 Cycle 67 s, 2024 Cycle 70 s, Last 12 months 85 s, All time 138 s; prod is slower). Run it with the environment of "Server command pattern": `$PYTHON scripts/warm_caches.py` (it warns when `REDIS_URL` is unset: the caches would then die with the script; when `REDIS_URL` is set but Redis is unreachable it exits 1 without warming, so the chain's `STEP7B_WARN` fires).
- **Run it after every flush**: after `refresh-analytics` and after `sunshine-import` (both flush the route caches). `scripts/refresh_prod_chain.sh` (step 7B) and `scripts/sync-fec.sh` (step 4) call it right after their `refresh-analytics` step, so the timers flush then warm instead of leaving the site cold; a warm failure is logged and does not fail the chain.
- **After a restart** (unit edit, Devin): `ExecStartPost=` the same command. ExecStartPost counts against `TimeoutStartSec` (90 s by default) and a cold warm takes minutes, so start it without blocking (`ExecStartPost=/bin/systemctl --no-block start ilcf-cache-warm.service`, a `Type=oneshot` unit with the web unit's `User`, `WorkingDirectory` and `EnvironmentFile`) or raise the timeout.
- The per-worker startup prewarm is now the fallback (no Redis): it checks the cache first and builds only missing keys. The warm (except the top-12 race-detail batch, which only checks its keys first) and the six heavy analytics pages (overview, risk, networks, relationships, state-race list and detail) build through `RouteCache.get_or_compute`: concurrent misses on one key, across workers, build it once (Redis `SET NX PX` lock `ilcf:lock:<cache>:<hash>`, 120 s; the others wait for its value).
- **`?load_mode=full` never builds a snapshot** (nor refreshes the materialized tables) from a request any more: `refresh_full` / `sync_full` are ignored (logged), the stored `analytics_snapshots` row is served stale or not, and without one the page computes live under its own cache key (the overview key carries the mode, the recon parameters and, in full mode, the geo limits — review C15; the first full-mode reader of a window pays one cold build). The "Refresh Full Snapshot" form was removed from the page. `refresh-analytics --with-snapshot` stores its snapshot under other parameters (`snapshot_version` 1, no window) than the pages look up (`snapshot_version` 3 plus the window), so it does not feed them.

## Release 2026-10-28 — load-time plan Phase 2 (`.claude/plans/load-times.md` P3/P5/P6/P7, L12 refresh ANALYZE)

Order on prod (the service stops once, for the swap script):

1. `git pull --ff-only origin main` and `pip install -r requirements.txt` (no new dependencies; `redis` was already required).
2. Pre-checks: `sudo -u postgres psql -d ilcf -X -At -c "SELECT extname FROM pg_extension WHERE extname='pg_trgm'"` prints `pg_trgm` (cross-matching already uses it, so `init-db`'s `CREATE EXTENSION IF NOT EXISTS` is a no-op). `grep -n CACHE_TTL /srv/illinois_campaign_finance/shared/.env` — a pinned `ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS=180` would override the new 21600 default.
3. Build the new indexes while the old code still serves (readers unaffected): `sudo -u postgres psql -d ilcf -X -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_analytics_donor_summary_name_trgm ON analytics_donor_summary USING gin (donor_name gin_trgm_ops)"`, then `sudo -u postgres psql -d ilcf -X -c "SET lock_timeout = '3s'; ALTER TABLE analytics_donor_committee_agg ADD COLUMN IF NOT EXISTS donor_state_key TEXT, ADD COLUMN IF NOT EXISTS donor_city_key TEXT"` (metadata-only, but it takes an ACCESS EXCLUSIVE lock and queues every new reader of the table behind the longest in-flight request — the old code has no statement timeout and a request holds its locks until teardown, so with `lock_timeout` it fails fast instead; retry until it succeeds, or run it in a quiet minute) and `sudo -u postgres psql -d ilcf -X -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_analytics_donor_committee_agg_geo ON analytics_donor_committee_agg (source, donor_state_key, donor_city_key, total_amount DESC)"` (73 MB, ~3 s locally on 1.3M rows). `ensure_schema`'s `_ensure_column` calls then issue no ALTER and `init-db` finds the index present.
4. `python3 run.py init-db` (schema migrations: the `pg_trgm` extension line; the two geo columns are added through `_ensure_column` only when missing — `schema.sql` carries no `ALTER TABLE`, so once step 3 has run this takes no table lock — and the indexes if step 3 was skipped, built non-concurrently inside `init-db`'s transaction, which is why step 3 exists). The outside-spending block above has two more `CREATE INDEX CONCURRENTLY` statements (`idx_isbe_condensed_expenditures_part9_date`, `idx_isbe_condensed_receipts_donor_name_norm`) that `init-db` never creates (they live in the ETL DDL): run them in the same sitting.
5. `systemctl stop ilcf-web.service`, run `scripts/swap_bulk_to_isbe.py` exactly as the "Release 2026-10-27" block does (live readers hold share locks on the compat views): it turns `bulk_candidate_committee_finance_agg` into a MATERIALIZED VIEW with 3 indexes + ANALYZE and is re-runnable from any prior state. Then `systemctl start ilcf-web.service` (new code live).
6. Redis lock smoke (the single-flight path was exercised only through a fake in tests): with `REDIS_URL` from `.env`, `redis-cli -u "$REDIS_URL" SET ilcf:lock:smoke 1 NX PX 5000` → `OK`, the same command again → `(nil)`, `redis-cli -u "$REDIS_URL" EVAL "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end" 1 ilcf:lock:smoke 1` → `(integer) 1`. After the warm (step 7) `redis-cli -u "$REDIS_URL" --scan --pattern 'ilcf:lock:*'` prints nothing (locks are released; a leftover one expires after 120 s — a worker killed mid-build leaves its key blocked that long, plan LD11; an optional `ExecStartPre=` that runs `redis-cli -u "$REDIS_URL" --scan --pattern 'ilcf:lock:*' | xargs -r redis-cli -u "$REDIS_URL" DEL` clears them at a restart, when no worker is alive). `redis-cli -u "$REDIS_URL" GET ilcf:flush_gen` prints a token after any flush (the fence that keeps a build straddling the flush from storing pre-refresh data). Also record `redis-cli -u "$REDIS_URL" CONFIG GET maxmemory` and `CONFIG GET maxmemory-policy`: the geo drill-down and search caches store one entry per distinct URL for 6 h (both paths are robots-disallowed), so a `maxmemory` with `volatile-lru` is the safety net against a crawler piling up keys (every `ilcf:cache:*` and `ilcf:lock:*` key has a TTL; the flush token `ilcf:flush_gen` has none on purpose and must not be evicted, which rules out `allkeys-lru`).
7. `refresh-analytics --with-snapshot` (long-running: tmux, or wait for the Sunday timer — both chain scripts now warm afterwards). It fills `donor_state_key` / `donor_city_key`, runs the new `ANALYZE` over the rebuilt aggregates (+~4 s) and flushes the route caches; until it has run, the all-time geo drill-down keeps its old 8–26 s path (now cached in Redis for 6 h). Then `python3 scripts/warm_caches.py` (minutes; fills the 4 scope presets).
8. Post-deploy checks: `curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' 'https://www.followthemoneyil.com/analytics/geo-drilldown?geo_type=state&geo_value=IL&period=all'` twice (second one from Redis, well under a second once step 7 ran); the same for `/analytics/geography` and `/analytics/donors`; `curl -s 'https://www.followthemoneyil.com/search?q=smith' | grep -c 'timed out'` → `0`; `curl -s 'https://www.followthemoneyil.com/analytics/?load_mode=full' | grep -c 'Refresh Full Snapshot'` → `0`; `journalctl -u ilcf-web.service --since '15 min ago' | grep -E 'for another build|Redis lock failed|Redis unlock failed|background refresh failed|Redis (read|write) failed|Traceback' | head` (those are the strings `webapp/cache_backend.py` logs; the lock-wait warning only appears after a build longer than 120 s with waiters).
9. Unit edit (Devin): the `ExecStartPost` warm after a restart, per "Cache warm step" above.

Behaviour changes: `?load_mode=full` never builds or refreshes a snapshot from a request (the form is gone; without a stored snapshot the geo section computes live, under a full-mode cache key); a `/search` page with a timed-out or errored section answers `Cache-Control: no-store` (the edge never keeps a degraded page); the geography, donors, geo drill-down and search payloads are Redis-shared with 6 h TTLs (`ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS`, `ANALYTICS_DONORS_CACHE_TTL_SECONDS`, `ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS`, `SEARCH_RESULTS_CACHE_TTL_SECONDS`, all flushed by `refresh-analytics` / `sunshine-import`); concurrent cold requests for one heavy page build it once across workers (waiters block up to the winner's build, 120 s at most); TTL-expiry cold builds remain (one per key) until the `.env` TTLs are raised above the refresh cadence (plan LD7).
