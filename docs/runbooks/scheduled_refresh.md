# Runbook: scheduled data refresh (nightly run, 05:00 check, data status)

**What it is:** a systemd timer runs the full prod refresh chain every night at 03:00 Chicago time, a second timer
checks the run at 05:00, and the public page `/data-status` shows how old every dataset is, the hand-loaded ones
included. Plan: `.claude/plans/scheduled-refresh.md`. Code: `database/data_refresh.py` (dataset list, run record,
snapshots, status), `scripts/nightly_refresh.sh`, `scripts/refresh_prod_chain.sh`, `scripts/nightly_check.py`,
`scripts/data_refresh_cli.py`, `scripts/manual_load.sh`, units in `docs/systemd/ilcf-nightly-*`.

> Status (2026-10-29): merged and deployed 13:30Z (`cbdf4c5`); first prod run started by hand 13:32–14:46Z, ok (73.5 min,
> exit 0, check OK, run row on `/data-status`; see the deployment runbook's 13:30Z block). Timers not installed on prod:
> section 1 is Devin's install.

---

## 1. Install on prod (Devin)

Prerequisites: the branch is merged and deployed (`git pull`, `pip install -r requirements.txt`, then `init-db`, which
creates the two new tables `data_refresh_runs` and `data_manual_loads`). `shared/.env` holds `DATABASE_URL` and
`FEC_API_KEY`; add `SOCRATA_APP_TOKEN` to enable the nightly City of Chicago pull (without it the step logs
`STEP5B_SKIPPED`).

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56
cd /srv/illinois_campaign_finance/app && set -a && . ../shared/.env && set +a
../shared/venv/bin/python3 run.py init-db
mkdir -p ../shared/refresh_runs ../shared/manual/lobbying ../shared/manual/irs527 ../shared/manual/openbook
cp docs/systemd/ilcf-nightly-refresh.service docs/systemd/ilcf-nightly-refresh.timer docs/systemd/ilcf-nightly-check.service docs/systemd/ilcf-nightly-check.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/ilcf-nightly-refresh.service /etc/systemd/system/ilcf-nightly-check.service
systemctl daemon-reload && systemctl enable --now ilcf-nightly-refresh.timer ilcf-nightly-check.timer
systemctl list-timers --all | grep -E 'ilcf-nightly|il-campaign'
```

- `systemd-analyze verify` should report no errors for these two units. The timers use `OnCalendar=… America/Chicago`, which needs systemd
  235 or newer (`systemctl --version`).
- Both units run as root (no `User=`): the chain stops and starts `ilcf-web.service`. The `il-campaign-*` units keep
  `User=app`.
- Safe smoke test (writes only a report file, changes no data): `../shared/venv/bin/python3 scripts/nightly_check.py
  --report-dir /tmp/ilcf-check-smoke` prints `MISSING` and exits 1 until the first nightly run exists.
- The first real run is the next 03:00. To run one by hand instead (60–120 min, the site is down ≈20 min during the
  ISBE step), start it in `tmux`: `REFRESH_KIND=manual bash scripts/nightly_refresh.sh`.

Timers on the box after the install:

| Timer | Schedule | Runs |
|---|---|---|
| `ilcf-nightly-refresh.timer` | daily 03:00 America/Chicago (+ up to 5 min random delay; `Persistent=true` catches up after downtime) | `scripts/nightly_refresh.sh` → `scripts/refresh_prod_chain.sh` |
| `ilcf-nightly-check.timer` | daily 05:00 America/Chicago | `scripts/nightly_check.py` |
| `il-campaign-fec-sync.timer` (existing) | Sundays 03:00 UTC | `scripts/sync-fec.sh` |
| `il-campaign-fec-schedule-e-catchup.timer` (existing) | hourly | `scripts/fec-schedule-e-catchup.sh` |

## 2. What runs at night

`nightly_refresh.sh` takes an `flock` on `shared/refresh.lock` (a second refresh or a `manual_load.sh` exits instead
of overlapping), waits up to 60 min (`FEC_LOCK_WAIT_MINUTES`) while the Sunday FEC timer's `shared/sync-fec.lock`
exists, opens a run in `data_refresh_runs` (with a row-count + latest-date snapshot of every source table), runs the
chain with one log `shared/logs/nightly-<YYYYMMDD-HHMMSS>.log`, closes the run (after snapshot + diff, `ok` on exit 0
else `failed`), then runs the check itself so a run that ended after 05:00 still gets its final report.

The chain (`refresh_prod_chain.sh`, each step logs `STEP<N>_START` / `STEP<N>_END`):

| Step | What | On failure |
|---|---|---|
| 1 | row-count baseline of `Bulk_download/*.txt` | — |
| 2 | download the 12 ISBE files | exit 2 |
| 3 | `scripts/check_row_counts.py` (aborts if a file shrank > 5 %) | exit 3 |
| 4 | **`ilcf-web.service` stopped**, `sunshine-import --bulk-dir Bulk_download --skip-compat-swap` | service started, exit 4 |
| 4B | `scripts/swap_bulk_to_isbe.py`, then **`ilcf-web.service` started** | service started, exit 10 |
| 5 | `sync-fec-il-federal --refresh-cache` | exit 5 |
| 5B | `import-chicago-phase1 --upsert` (only with `SOCRATA_APP_TOKEN`) | warning, continues |
| 5C | Wednesdays only: `scripts/fec-schedule-b-catchup.sh` | warning, continues |
| 6 | `run-cross-matching --only all` | exit 6 |
| 7 / 7B | `refresh-analytics --with-snapshot`, then `scripts/warm_caches.py` | exit 7 / warning |
| 8 | `systemctl restart ilcf-web.service` + HTTP 200 smoke test (skipped with `SKIP_RESTART=1`) | exit 8 / 9 |
| 9 | `validate-freshness` (every source; the FEC silent no-op shows here) | exit 11 |

The site is down from `STEP4_START` to `STEP4B_END` (≈20 min): the ETL's DROP block and the compat swap both deadlock
against live readers (deployment.md, 2026-10-27). Wrapper exit codes: 1 = another refresh holds the lock,
20 = `sync-fec.lock` still present after the wait.

## 3. Reading the 05:00 report

Where: `shared/refresh_runs/<YYYY-MM-DD>.md` (Chicago date), the run row in `data_refresh_runs`, the public page
`/data-status` (last 14 runs + every dataset), and `systemctl --failed` (the check unit fails when the check is not OK).

`**Check: …**` at the top:

- **OK** — the run finished with exit 0 and ISBE filings and FEC Schedule A pass their `data_freshness` thresholds.
- **FAILED** — the run failed (exit code given; the Steps table names the failed step), or it exited 0 but ISBE
  filings / FEC Schedule A are stale or missing.
- **RUNNING** — the run was still going at 05:00 (the line names the step it was in). The wrapper re-runs the check
  when the run ends and overwrites the report and `check_status`.
- **MISSING** — no nightly run started that Chicago day: the timer did not fire, the lock was held, or the database
  was unreachable when the wrapper started (the log then says `NIGHTLY_WARN could not open a run record`).

Sections: **Run** (id, status, exit code, start / finish in Chicago time, minutes, log path); **Steps** (minutes per
chain step from the log; `skipped` / `warn` steps are expected when the token is absent or it is not Wednesday);
**What changed** (per source table: rows before / after / added, latest date before / after, `(new)` when it
advanced — ISBE tables are dropped and reloaded, so their "added" is the net change and can be negative; counts are
raw table rows, archived ISBE rows included, never money); **Freshness** (the `validate-freshness` output);
**Datasets** (every dataset with its latest data date, last load, age). **Notes** lists older runs still marked
`running` (a wrapper that died): mark them by hand (section 5).

## 4. Datasets that are loaded by hand

| Dataset | Cadence | Why not automated | How |
|---|---|---|---|
| IL SOS lobbying | weekly (when a new daily extract publishes) | the extract is a download from the SOS site | `manual_load.sh lobbying <extract.csv>` |
| IRS 527 | monthly (the IRS rebuilds `PolOrgsFullData.zip` every Saturday) | the download is scriptable (plain GET), but `import-irs527` plain-INSERTs five tables and a re-run doubles them (`docs/runbooks/irs527_data.md` P-3); it also stores the wrong field as the contribution date (P-1). Loaded data ends 2025-12-31 (loaded Feb 2026). | follow `docs/runbooks/irs527_data.md` for the reload, then record it with `manual_load.sh irs527 <FullDataFile.txt>` only when that runbook says a plain import is safe |
| OpenBook | as needed | a long scrape of the Comptroller site, not a file | `manual_load.sh openbook -` (in `tmux`) |
| FEC Schedule B | automated weekly (step 5C); by hand any day | — | `manual_load.sh fec_b -` |

Once P-3 lands, the IRS 527 download + import becomes a weekly chain step like 5C and the dataset flips to automated
in `DATASETS`.

The flow for a file:

1. On the laptop, download into `data/manual/<dataset>/` (ignored by git; keep the source's file name, which carries
   its date, e.g. `Lobbyist_Entity_Client_Data_Daily_20261028.csv`).
2. Copy it to the server: `scp -i ~/.ssh/hetzner_ed25519 data/manual/lobbying/<file> root@178.156.162.56:/srv/illinois_campaign_finance/shared/manual/lobbying/`
3. Load and record it (on the server, from the app directory):
   `bash scripts/manual_load.sh lobbying /srv/illinois_campaign_finance/shared/manual/lobbying/<file>`

`manual_load.sh` takes the same `refresh.lock` (it refuses to run during the nightly), runs the loader, then writes a
`data_manual_loads` row: the file, its data date (third argument `YYYY-MM-DD`, else the first `YYYYMMDD` in the file
name), and the rows the load added to the dataset's main table. `/data-status` shows it as the dataset's last load.
After a hand load, the next nightly's cross-matching and analytics pick the new rows up; run `run-cross-matching
--only all` and `refresh-analytics --with-snapshot` by hand only if they are needed sooner.

Dates on `/data-status`: lobbying has no per-row date, so its "latest data" is when the newest registration first
loaded; OpenBook's is the last completed scrape run; IRS 527 uses the latest well-formed report period end.

## 5. Recovery

- **A step failed** (report FAILED, step named): read the log around `STEP_FAILED`. Fix the cause, then re-run the
  whole chain by hand in `tmux`: `REFRESH_KIND=manual bash scripts/nightly_refresh.sh` (a manual-kind run does not
  replace the nightly's report). Re-running the whole chain is the same recovery the by-hand weekly refresh always used.
- **The site is down after a refresh**: the chain stops `ilcf-web.service` only for steps 4–4B and starts it again on
  both of their failure paths, but a wrapper killed between the stop and the start (OOM, reboot) leaves it stopped,
  and a step 8 failure (exit 8 / 9) means the restart itself failed: `systemctl start ilcf-web.service`, then
  `journalctl -u ilcf-web.service -n 50`.
- **Freshness gate failed** (step 9 or the check): the named source did not advance. For FEC Schedule A that is
  usually the payload-cache no-op (CLAUDE.md "FEC silent no-op"); for ISBE, a truncated or shadowed download
  (step 3, or stale suffixed files in `Bulk_download/`).
- **An abandoned run** (Notes: "never finished: abandoned"): close it by hand:
  `sudo -u postgres psql -d ilcf -c "UPDATE data_refresh_runs SET status = 'failed', notes = 'abandoned' WHERE id = <N>"`
- **MISSING with a lock message** in the log: another refresh or a `manual_load.sh` held `shared/refresh.lock` (flock
  releases it when the holder exits), or `shared/sync-fec.lock` outlived its run — remove that PID file only after
  `ps aux | grep sync-fec` shows nothing.
- **RUNNING at 05:00 every day**: the chain outgrew 03:00–05:00; move the check timer later
  (`OnCalendar=*-*-* 06:00:00 America/Chicago`).
- **Turn it off**: `systemctl disable --now ilcf-nightly-refresh.timer ilcf-nightly-check.timer`; the by-hand chain
  (`bash scripts/refresh_prod_chain.sh <log>`) still works without a run record.
