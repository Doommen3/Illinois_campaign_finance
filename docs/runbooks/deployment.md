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
   - 7b. `scripts/warm_caches.py` (markers `STEP7B_START` / `STEP7B_END`; a failure logs `STEP7B_WARN` and does not fail the chain)
8. Restart `ilcf-web.service` + smoke-test HTTP 200 on `/`

Each step emits `STEP_N_START` / `STEP_N_END` markers to the log; failures emit `STEP_FAILED` + `CHAIN_FAILED`. Watch with `tail -f "$LOG"`.

Total runtime: ~60–120 min. Detach tmux during cross-matching with `Ctrl-b d`; reattach later with `tmux attach -t refresh`.

**Prod 2026-10-27 notes.** (1) `sunshine-import`'s auto-swap ran into the lock conflict described under "Release 2026-10-27" step 5 while `ilcf-web` was serving; the day's wrapper (`shared/isbe_fec_refresh_20261027.sh`) fell back to stopping the service and re-running the import, so the site was down about 20 minutes. Until the swap takes its locks up front (suggestion A12), schedule the ISBE import for a quiet window or stop the service around the swap deliberately. (2) The chain can be checked without a server login at `https://www.followthemoneyil.com` (the apex `followthemoneyil.com` answers 308 to www through Cloudflare): the homepage "Data Status" strip shows the local receipts date, the FEC candidate-totals update time and the Schedule A/B/E row-level dates, and the search page's donor rows show the `analytics_donor_summary` sync timestamp, which only `refresh-analytics` rewrites. Measured on prod 2026-10-27 (`shared/logs/isbe_fec_refresh_20261027.log`): the first `sunshine-import --download` attempt occupied 15:29–16:00Z (download, then the deadlock at the ETL's DROP block), the retry's load took 20 min with the service stopped, then FEC sync with `--refresh-cache` 6 m 49 s, `run-cross-matching --only all --workers 2` 5 m 02 s, `refresh-analytics --with-snapshot` 4 m 43 s, restart 49 s — the ISBE download and load are the long steps; everything after them takes about 17 minutes.

## Pre-deploy checks

Run locally before pushing (CLAUDE.md "Deploy steps", step 1):

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


## Deployed 2026-10-28 11:45–12:00Z — commit `295da60` (all three 2026-10-28 blocks below, plus the review fixes R1–R6)

Order actually run (Devin: "Ratify the decision, commit, push and deploy"): local full suite + scope-bar sweep → push `4acc572..295da60` (CI free) → `git pull`, `pip install` (no new deps) → pre-checks (`pg_trgm` present; gunicorn 25.0.3; Redis `maxmemory 0`, policy `noeviction`, 20 keys) → the five `CREATE INDEX CONCURRENTLY` builds (part9_date 1 s, donor_name_norm 9 s, name_trgm 10 s, geo 3 s) and the geo columns under `SET lock_timeout = '3s'` (first attempt) → `init-db` (1 s, no lock) → `systemctl stop` 11:48:47Z, `swap_bulk_to_isbe.py` 15 s (aggregate → MATERIALIZED VIEW 112,527 rows; `isbe_race_candidacies` 86,590 rows; its new post-swap flush deleted 20 route keys), `systemctl start` 11:49:03Z (16 s of downtime) → Redis lock smoke OK (`SET NX` once, refused twice, compare-and-delete 1; `ilcf:flush_gen` set) → `refresh-analytics --with-snapshot` 11:50:00–11:54:18Z (geo keys filled, 0 NULL; new fence token) → `scripts/warm_caches.py` 11:54:42–11:59:36Z (2026 Cycle 66 s, 2024 Cycle 65 s, Last 12 months 57 s, All time 104 s; 100 keys, 0 locks). Post-deploy, through Cloudflare: `/about` carries `cache-control: public, max-age=0, s-maxage=300` (`cf-cache-status: DYNAMIC` — no Cache Rule yet); anonymous `?refresh_cache=1` → 302 `/analytics/`; `/%09/example.org/login?refresh_cache=1` → 302 to `/%09/example.org/login` on this origin (open redirect closed); robots.txt 19 Disallow lines; warm pages: overview 0.26 s, `?period=all` 0.24 s, risk 0.27 s, geography 0.16 s, donors 0.14 s, state races 0.31 s, networks 0.40 s, relationships 0.37 s, home 0.20 s; geo drill-down IL all-time 2.0 s cold → 0.22 s warm, Chicago city 1.7 s cold, IL default window 2.7 s (windowed path, LD8); `/search?q=smith` 0.54 s then 0.25 s, no timed-out section; `?load_mode=full` has no snapshot form; Chicago 1B list row and detail card agree ($578,014.63 / 295.34 %); `pg_stat_activity` shows `ilcf-web` during a request; journal clean. Not done, Devin's side: the Cloudflare Cache Rule, the `ExecStartPost` warm unit, the gunicorn access-log flags, `pg_stat_statements`, and Redis `CONFIG SET maxmemory 1gb` + `maxmemory-policy volatile-lru` (+ `CONFIG REWRITE`) — the agent's permission layer refused that config change, so prod still runs `maxmemory 0` / `noeviction` (unbounded; 100 keys ≈ a few MB today).

**CI follow-up (same day).** The run for `295da60` failed 5 tests in `tests/test_request_hygiene.py` (`Vary: Cookie` on anonymous pages): CI resolves the unpinned `flask>=3.0.0` to Flask 3.1.3 / Werkzeug 3.1.9, where the session counts as "accessed" on any touch of the proxy — `base.html`'s `get_flashed_messages()` touches it on every page — while local and prod run Flask 3.0.3 (prod's `/about` had no `Vary` after the deploy). Fix, version-independent: `EdgeCacheSessionInterface` in `webapp/app.py` skips the `Vary: Cookie` header for a request that carried no session cookie and whose session stayed empty and unmodified (a cookie-bearing request, a flash or a login still varies and sets the cookie as before); three anchor tests, green under Flask 3.0.3 and 3.1.3; full suite 2270 passed. Deployed by `git pull` + `systemctl restart` (see the next records line). Pin note: a rebuilt venv will pick up Flask 3.1.x; the app no longer depends on the difference.

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
