# Plan: privacy redactions (site-side fixes for personal-information removal requests)

**Written:** 2026-09-30
**Why:** `docs/audits/judicial_privacy_takedown_2026-07-16.md` (legal assessment) and `docs/runbooks/privacy_takedown_response.md` (operational steps). The site has no mechanism to honor a Judicial Privacy Act removal request, the ISBE ETL drops and reloads `isbe_receipts` on every import (so any manual fix is undone weekly), and donor keys embed the street address in the URL path.
**Routing:** interactive. Steps 1, 2, 4 are delegable to `implementer` one at a time (small, well-bounded, shared files touched serially). Step 3 (ETL hook) and step 5 (docs) stay with the orchestrator. Step 6 is run by Devin on prod.
**Status:** Steps 1–5 landed 2026-09-30, all uncommitted. Step 6 (prod rollout) is Devin's.

## Design decisions (already made; revisit only if a step contradicts them)

1. **Redact at the data layer, not in templates.** One UPDATE on the base tables, mirroring ISBE's own format (`address1 = 'Redaction Requested'`, `address2/city/state/zipcode = NULL`, `redaction_requested = TRUE`). Every exit (11 templates, JSON, CSV, Viz Lab, donor keys, cross-matching) inherits it. Template-level hiding was rejected: it misses exits and leaves the address in the URL.
2. **Match on last name + zip5, first name optional.** Exact normalized match (`lower(trim(...))`); zip compared on the first five digits after stripping non-digits so `60022` matches `60022-1317`. Broader fuzzy matching was rejected: over-redaction falsifies disclosure records for unrelated people.
3. **Store requests in a `privacy_redactions` table, never in the repo.** Seed rows (names, zips, requester) are inserted on prod via CLI. `schema.sql` holds only the DDL.
4. **Re-apply automatically after every ISBE import**, before matviews are built, so the redaction survives the drop-and-reload.
5. **Never delete the contribution row.** Only address fields change.
6. **Out of scope (deferred, note if asked):** `isbe_expenditures` vendor/payee addresses, `isbe_officers` addresses, FEC data (no street column exists), `noindex` on donor pages.

## Shared verification tooling

- Test file created in step 1 and extended in steps 2 and 4: `tests/test_privacy_redactions.py`. Every later step's verification runs it.
- Full-suite gate before step 6: `pytest -q`.
- Lint gate (matches CI): `ruff check --select E9,F63,F7,F82 .`

---

## Step 1 — Schema, core module, tests (tests first)

**Target files**
- `database/schema.sql` — add table (place after `analytics_donor_summary`):
  ```sql
  CREATE TABLE IF NOT EXISTS privacy_redactions (
      id SERIAL PRIMARY KEY,
      last_name TEXT NOT NULL,
      first_name TEXT,                 -- NULL matches any first name
      zip5 TEXT NOT NULL,
      requested_by TEXT NOT NULL,
      request_date DATE NOT NULL,
      statute TEXT,
      notes TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      last_applied_at TIMESTAMP
  );
  ```
  (Check `_adapt_schema_for_postgres` in `database/connection.py` handles `SERIAL`/`DATE` the same way neighbouring tables do; match whatever idiom the file already uses for autoincrement.)
- `database/privacy_redactions.py` — new module:
  - `REDACTED_ADDRESS = "Redaction Requested"`
  - `add_privacy_redaction(conn, *, last_name, zip5, first_name=None, requested_by, request_date, statute=None, notes=None) -> int` — inserts, returns id. Validates zip5 is exactly five digits.
  - `apply_privacy_redactions(conn) -> dict[str, int]` — for every row in `privacy_redactions`, scrubs each target that exists and returns `{table: rows_changed}`. Targets, each guarded by `_table_exists` (reuse from `database/analytics.py` or `database/models.py`) and, for `bulk_receipts_clean`, by a `pg_class.relkind = 'r'` check so a view is skipped:
    - `isbe_receipts`: columns `first_name, last_name, address1, address2, city, state, zipcode, redaction_requested` (boolean). Only touch rows whose `address1` is not already `REDACTED_ADDRESS`.
    - `bulk_receipts_clean` (only if a real table) and `bulk_receipts_clean_legacy`: legacy columns `first_name, last_or_business_name, address_line_1, address_line_2, city, state, postal_code, redaction_requested` (INTEGER 0/1 — check `database/bulk_download_loader.py:271-360`).
    - `donors`: `name, address, normalized_address` — match `lower(name) = lower(first || ' ' || last)` (or `lower(name) LIKE '% ' || lower(last)` when first is NULL) and `address ILIKE '%' || zip5 || '%'`; set `address = REDACTED_ADDRESS`, `normalized_address = NULL`.
    - `analytics_donor_summary`: DELETE rows where `lower(donor_name)` matches and `donor_address ILIKE '%' || zip5 || '%'`. (Rows are rebuilt with the redacted address on the next `refresh-analytics`; deleting gives immediate effect on `/search`.)
    - Sets `last_applied_at = now()` on each request row.
  - `refresh_dependent_views(conn) -> None` — `REFRESH MATERIALIZED VIEW CONCURRENTLY isbe_condensed_receipts` if the matview exists (it has a unique index on `id`, see `scripts/isbe_sunshine_etl.py:507`). Runs inside the caller's transaction (PostgreSQL permits CONCURRENTLY refresh in a transaction block; `PostgresCompatConnection` has no autocommit attribute). Amended 2026-09-30 after the tester probed it.
- `tests/test_privacy_redactions.py` — new. `isbe_receipts` is ETL-created and absent in the CI test schema, so the test creates a minimal one itself (`CREATE TABLE isbe_receipts (id BIGINT PRIMARY KEY, committee_id INT, first_name TEXT, last_name TEXT, address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT, received_date DATE, amount DOUBLE PRECISION, archived BOOLEAN DEFAULT FALSE, redaction_requested BOOLEAN DEFAULT FALSE)`). Use the `pg_test_schema` autouse fixture and `get_db`/`init_db` as in `tests/test_data_freshness.py`. Cases:
  1. Matched row (name + `60022-1317`) is scrubbed to the ISBE format; `redaction_requested` becomes TRUE; other columns untouched.
  2. Same name, different zip → untouched. Same zip, different last name → untouched. First name differs when `first_name` is set on the request → untouched; matches when request `first_name` is NULL.
  3. Idempotent: second `apply` returns 0 for every table.
  4. `analytics_donor_summary`: matching row deleted, non-matching kept.
  5. `donors` legacy row scrubbed.
  6. Missing tables tolerated: drop `isbe_receipts`, apply → no exception, count absent or 0.
  7. `add_privacy_redaction` rejects a non-5-digit zip.
  8. `refresh_dependent_views` is a no-op when the matview is absent.

**Verification**
```bash
pytest -q tests/test_privacy_redactions.py
ruff check --select E9,F63,F7,F82 database/privacy_redactions.py tests/test_privacy_redactions.py
```
Orchestrator observes red before implementation (tests written first, module stub only), then green.

## Step 2 — CLI commands and cache flush

**Target files**
- `webapp/cache_backend.py` — add `flush_all_route_caches() -> int`: SCAN/DELETE `ilcf:cache:*` via `_get_redis_client()`; returns keys deleted; returns 0 and logs when Redis is unavailable. (Mirror the loop in `RouteCache.invalidate`.)
- `cli/commands.py` — two commands next to `validate-freshness` (same `get_db(_db_target())` / try-finally pattern):
  - `add-privacy-redaction --last TEXT --zip TEXT --requested-by TEXT [--first TEXT] [--request-date YYYY-MM-DD, default today] [--statute TEXT, default "705 ILCS 90"] [--notes TEXT] [--no-apply]` — inserts, then (unless `--no-apply`) runs apply + `refresh_dependent_views` + `flush_all_route_caches`, prints per-table counts, and prints the reminder `Restart ilcf-web.service to clear the in-process search cache.`
  - `apply-privacy-redactions [--skip-matview-refresh] [--skip-cache-flush]` — same post-steps, for re-applying after a manual DB change or `sync-prod-db`.
- `tests/test_privacy_redactions.py` — extend: `click.testing.CliRunner` invoking both commands against the test schema (conftest sets `DATABASE_URL`); assert exit code 0, the inserted row exists, and the scrubbed `isbe_receipts` row. Use `--skip-cache-flush` / `--skip-matview-refresh` in tests. Reject `--zip 6002`.

**Verification**
```bash
pytest -q tests/test_privacy_redactions.py
python run.py apply-privacy-redactions --skip-cache-flush     # local DB, table empty → all counts 0, exit 0
time psql ilcf -c "REFRESH MATERIALIZED VIEW CONCURRENTLY isbe_condensed_receipts;"   # record the wall-clock in this step's result
```
Performance gate (CLAUDE.md policy): record the local matview refresh time on 3.6M rows. If it exceeds ~2 minutes, keep the CONCURRENTLY refresh but note in the runbook that prod runs it inside tmux.

## Step 3 — ETL hook (orchestrator)

**Target file**
- `scripts/isbe_sunshine_etl.py` `main()` — immediately before the `if not args.skip_views:` block (currently lines ~1932-1937), after `infer_missing_candidate_links`:
  ```python
  from database.privacy_redactions import apply_privacy_redactions
  counts = apply_privacy_redactions(conn)
  conn.commit()
  if any(counts.values()):
      print(f"  Privacy redactions applied: {counts}")
  ```
  `apply_privacy_redactions` must return `{}` without error when the `privacy_redactions` table does not exist (fresh DB before `init-db`), so the import never fails on it. The hook runs before `create_materialized_views`, so the matview is built from scrubbed rows and no separate refresh is needed on the import path.

**Verification**
```bash
ruff check --select E9,F63,F7,F82 scripts/isbe_sunshine_etl.py
pytest -q tests/test_isbe_sunshine_patterns.py tests/test_isbe_rewire.py tests/test_privacy_redactions.py
grep -n "apply_privacy_redactions\|create_materialized_views(conn)" scripts/isbe_sunshine_etl.py   # hook line number must precede the create_materialized_views call
```
Orchestrator reviews the diff by hand; a full local `sunshine-import` is not required for this step (drop-and-reload behaviour is already established at `scripts/isbe_sunshine_etl.py:152-169`).

## Step 4 — Privacy and removal-request page, footer link

**Target files**
- `webapp/routes/main.py` — `@main_bp.route('/privacy')` next to `/about` (line ~2696), `render_template('privacy.html')`.
- `webapp/templates/privacy.html` — extends `base.html`, same structure as `about.html`. Content, in plain language: what the site publishes and where it comes from (ISBE bulk files, FEC, IL SOS, IRS); that contributor addresses are part of the public disclosure record; that the site honors removal requests under the Illinois Judicial Privacy Act (705 ILCS 90), the Public Official Safety and Privacy Act (5 ILCS 347), and the federal Daniel Anderl Judicial Security and Privacy Act; what a request needs (name as it appears, zip, the statute, and for a representative the officer's written consent per 705 ILCS 90/2-10(c)); the 72-hour commitment; exactly what is redacted (ISBE format) and what is kept; a link to ISBE's Redaction Request form as the source-level fix; contact via `public_contact_email` when set. No legal advice, no case names.
- `webapp/templates/base.html` footer (lines ~140-146) — add a link `Privacy & removal requests` to `url_for('main.privacy')`; when `public_contact_email` is empty, the fallback sentence links to the same page.
- `tests/test_privacy_page.py` (new; amended from `test_privacy_redactions.py` so steps 2 and 4 could run in parallel) — two route tests using the `app` fixture pattern from `tests/test_webapp.py:17-30`: `GET /privacy` → 200 and body contains `Redaction Requested` and `72 hours`; `GET /about` body contains `href="/privacy"`.

**Verification**
```bash
pytest -q tests/test_privacy_page.py tests/test_webapp.py
```

## Step 5 — Documentation sync (orchestrator)

**Target files**
- `README.md` — Data Maintenance section (~line 212): the two commands, the ETL hook, the matview refresh timing from step 2.
- `CLAUDE.md` — Common Operations block: the two commands; a short "Privacy redactions" note under Database Notes naming the table and the ETL hook; add `docs/runbooks/privacy_takedown_response.md` to the runbooks list.
- `docs/runbooks/privacy_takedown_response.md` — replace section 5's manual SQL with the commands; flip the status banner; update the checklist rows that the tooling now covers.

**Verification**
```bash
grep -n "add-privacy-redaction\|apply-privacy-redactions" README.md CLAUDE.md docs/runbooks/privacy_takedown_response.md
pytest -q            # full-suite gate before rollout
```

## In-flight state (2026-09-30 evening)

- Local data refresh DONE 2026-09-30 19:37 (53 min): 13.2M ISBE rows, FK rejects ≤0.06%, ISBE filings fresh to 2026-09-30, FEC Schedule A to 2026-07-01 (429 API calls); Schedule B still stale (needs `backfill-fec-schedule-b`, overdue monthly task). ETL privacy hook ran; apply-privacy-redactions all zero as expected. Log: `logs/refresh_local_20260930.log`.
- Prod data refresh DONE 2026-10-01 00:54 UTC (65 min; same row counts as local: 13,230,110 rows, isbe_receipts 6,526,500; sanity check OK; FEC 429 calls). Service still inactive. Was running in tmux `refresh0930`, launched by Devin: `/srv/illinois_campaign_finance/shared/logs/refresh_20260930_234937.log` (sentinel `CHAIN_END`/`CHAIN_FAILED`), PID 457154, `SKIP_RESTART=1` so the service stays down. Script: `scripts/refresh_prod_chain.sh` (fixed: `--refresh-cache`, `SKIP_RESTART`), copied to `shared/`.
- Auto-mode classifier blocks launching prod jobs from this session ("Production Deploy"); reads of the server are allowed. Devin runs prod commands.
- Deploy prerequisites: local `main` diverged from `origin/main` (origin has 8523a12, 27e0530 cherry-pick, b89184f; local has 735d030 duplicate) → rebase before commit/push. Repo is PUBLIC so CI on push is free. Nothing committed or pushed yet.

## Step 6 — Rollout on prod (Devin runs; nothing here is delegated)

Deploy per CLAUDE.md "Deploy steps" (push, pull, pip, `init-db`), then:

```bash
cd /srv/illinois_campaign_finance/app
/srv/illinois_campaign_finance/shared/venv/bin/python3 run.py add-privacy-redaction \
  --first Howard --last Chrisman --zip 60022 \
  --requested-by "Ironwall by Incogni on behalf of Judge Amy J. St. Eve" \
  --request-date 2026-07-16 --statute "705 ILCS 90" \
  --notes "Request email 2026-07-16; signed consent per 2-10(c) requested"
systemctl restart ilcf-web.service
```

Expected output: `isbe_receipts` count equals the number of Chrisman/60022 rows (locally 13 incl. archived versions), `analytics_donor_summary` 3, matview refreshed, cache keys flushed.

Then, in tmux (long-running): `run.py refresh-analytics --with-snapshot` so `analytics_donor_summary` is rebuilt with the redacted row.

Bring the site out of maintenance mode, then verify from outside:
```bash
curl -s "https://www.followthemoneyil.com/search?period=2026cycle&q=Chrisman" | grep -ci "beach"          # expect 0
curl -s -o /dev/null -w "%{http_code}\n" "https://www.followthemoneyil.com/donors/key/howard%7Cchrisman%7C251%20beach%20rd%7C%7Cglencoe%7Cil%7C60022-1317?source=bulk_receipts"   # expect 404
```
Finish the runbook checklist (search-engine removals, reply to Ironwall, log row).

## Step table

| # | Step | Executor | Verification | Status |
|---|---|---|---|---|
| 1 | Schema + `database/privacy_redactions.py` + tests | implementer (after orchestrator sees red) | `pytest -q tests/test_privacy_redactions.py` | ✅ 2026-09-30 — 22 passed (19 tester cases + 3 orchestrator-added empty-last-name guards); ruff clean; `init_db` suites green |
| 2 | CLI commands + Redis flush helper | implementer | same + local `apply-privacy-redactions` run + matview timing | ✅ 2026-09-30 — 3 CliRunner tests (incl. commit check); local run all-zero counts; `REFRESH MATERIALIZED VIEW CONCURRENTLY isbe_condensed_receipts` = 28.9 s on 3,613,834 rows (no tmux needed); orchestrator re-ran |
| 3 | ETL hook | orchestrator | ruff + ETL pattern tests + grep ordering | ✅ 2026-09-30 — `apply_privacy_redactions_post_load()` at scripts/isbe_sunshine_etl.py:1833, called before `create_materialized_views`; opens a PostgresCompatConnection because the ETL's own conn is raw psycopg; ruff clean, 74 ETL pattern tests pass, live no-op run returned {} (local DB predates init-db) |
| 4 | `/privacy` page + footer link | implementer | `pytest -q tests/test_privacy_page.py tests/test_webapp.py` | ✅ 2026-09-30 — route tests moved to their own file `tests/test_privacy_page.py` (steps 2 and 4 ran in parallel and could not share a test file); 87 passed; orchestrator re-ran and reviewed the page copy |
| 5 | README / CLAUDE.md / runbook sync | orchestrator | grep + `pytest -q` | ✅ 2026-09-30 — full suite 920 passed, 13 failed: 12 are `integration`-marked (live ISBE site / pg_trgm, skipped in CI) and 1 (`test_bulk_download_loader.py::test_import_bulk_download_with_receipts_creates_receipts_tables`) fails on a pre-existing uncommitted archived-row filter in `build_receipts_joins`, unrelated to this plan |
| 6 | Prod rollout for the Chrisman request | Devin | external curl checks | ☐ |
