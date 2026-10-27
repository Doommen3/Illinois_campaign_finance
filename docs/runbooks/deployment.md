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
8. Restart `ilcf-web.service` + smoke-test HTTP 200 on `/`

Each step emits `STEP_N_START` / `STEP_N_END` markers to the log; failures emit `STEP_FAILED` + `CHAIN_FAILED`. Watch with `tail -f "$LOG"`.

Total runtime: ~60–120 min. Detach tmux during cross-matching with `Ctrl-b d`; reattach later with `tmux attach -t refresh`.

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
6. Analytics: `$PYTHON run.py refresh-analytics --with-snapshot` — fills `analytics_donor_cycle_summary` (1,625,516 rows and 57.6 s for that step locally) and flushes the Redis route caches. Until it has run, `/donors/` keeps using the live query (the cycle table is read only when the cycle has rows).
7. Optional: `$PYTHON scripts/committee_families.py seed` — stores pending committee families only (753 families / 1,624 committees locally); nothing is public until an admin accepts a family at `/admin/committee-families`.
8. Restart: `systemctl restart ilcf-web.service` — clears the per-worker fallback caches and starts the per-worker prewarm (overview, risk, networks, relationships, top-12 state-race details).
9. Verify: the status/curl check from CLAUDE.md "Deploy steps" step 8, then the endpoint sweep above plus `/analytics/state-races/`, `/committees/1126/donors` and `/candidates/<id>`. Re-measure `/committees/1126` and `/analytics/state-races/` and record the prod numbers in `docs/runbooks/route_perf_caching.md`.

**Matview rebuild (the `NULLS LAST` fix).** The corrected filed-doc pickers are in the matview DDL of `scripts/isbe_sunshine_etl.py`, so prod gets them at the next `sunshine-import`; until then its condensed views keep the old pickers (locally 3,510 committees had a condensed receipts total more than 1 % off their raw non-archived total; after the rebuild, 741). To get them at deploy instead, rebuild by hand in a quiet window (82 s locally): drop the five matviews (`DROP MATERIALIZED VIEW IF EXISTS … CASCADE` for `isbe_committee_money`, `isbe_candidate_money`, `isbe_condensed_expenditures`, `isbe_condensed_receipts`, `isbe_most_recent_filings`; the `CASCADE` also drops the dependent compat views until the swap script recreates them), call `create_materialized_views(conn)` and `create_compat_views(conn)` from `scripts/isbe_sunshine_etl.py`, then run `scripts/swap_bulk_to_isbe.py`. Do this between steps 4 and 5 (it ends with the swap script) and before step 6, so the cycle table is built from the corrected views.

**Expected differences after the release** (local numbers; prod will differ in size): race totals drop because archived receipts are excluded, while the 2026 race list grows from 599 to 1,465 races (443 to 1,012 with receipts in the window) because candidacies now define the cycle; after a matview rebuild the condensed non-archived receipts total rises from $5.40B to $6.46B; windowed 527 totals shrink (local `/527/dark-money` window total $4.51M to $224K) because `irs527_contributions.date` holds amounts rather than dates (A11; prod unchecked).
