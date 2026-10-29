# Staleness accounting (2026-10-29)

To-do item 3: what on the site goes stale when new data is downloaded, whether every piece is refreshed on every
pull, why "Find unusual patterns this month" showed 09 2026, and what to change. The row-by-row list lives in
`docs/refresh_inventory.md` (rows RF-001…RF-051); this document is the dated review: the worked example, the flags
Devin must decide, the proposed refresh chain that item 4 (scheduled server update) consumes, and the doc edits
recommended for the two written chains. Structural only: no private individuals, addresses or donor keys.

## Scope and method

- **Code:** worktree worktree-ui-improvements at main 48e5399, which includes fix-8b F1 and F6 (merge 3398990,
  deployed 2026-10-29 06:18Z) and the 527 period fix (4529328, deployed 06:39Z).
- **Inventory method:** from each page class in `docs/page_inventory.md` (C-01…C-17, S-01…S-06) to its templates
  and routes, to the tables, views, caches and files they read, to the command or step that rebuilds each. Then
  reconciled against four written chains: CLAUDE.md "Data Refresh Schedule", `scripts/refresh_prod_chain.sh` (the
  chain actually run on prod), `scripts/sync-fec.sh` (the Sunday FEC timer) and `docs/data_update_guide.md`.
- **Local measurements:** read-only SQL on the local `ilcf`, 2026-10-29 06:50–06:55Z, with
  `PGOPTIONS='-c default_transaction_read_only=on'`. The local analytics tables were built 2026-10-26 23:51,
  **before** F1 and F6, so locally the pre-F1 behaviour is still visible. Every query is in the appendix.
- **Prod observations:** public HTTPS GETs of `https://www.followthemoneyil.com/` only, each dated. Facts about
  prod operations that this audit did not observe itself (import and refresh times) are attributed to the
  orchestrator's records or `docs/runbooks/deployment.md`. No SSH, no psql to prod.
- **Not done:** no code, no edits to the written chains or guides (plan decision E2), no local server.

## Worked example: "Find unusual patterns this month" lands on 09 2026

### The code path

1. Home button: `webapp/templates/index.html:20` renders "Find unusual patterns this month" as a link to
   `main.investigate_workspace` with `tab='patterns'` and the page's scope query. No month is passed.
2. Route: `webapp/routes/main.py:2768`,
   `month_key = month if month else (_latest_month_with_large_contributions(conn) if _table_exists(conn, "analytics_large_contributions") else datetime.utcnow().strftime("%Y-%m"))`.
   The calendar clock is only the fallback when the table does not exist.
3. `_latest_month_with_large_contributions()` (`webapp/routes/main.py:2517`):
   `SELECT SUBSTR(MAX(event_date), 1, 7) FROM analytics_large_contributions`.
4. Heading: `webapp/templates/investigate.html:187`, "Unusual Patterns: {{ month_key }}".

So "this month" means **the latest month that has any row in `analytics_large_contributions`**. That table is
rebuilt only by `refresh-analytics` (inventory RF-009).

### What fills `analytics_large_contributions`, and with which filter

`refresh-analytics` → `refresh_analytics_materialized()` → the ISBE direct path insert at `database/analytics.py:3617`
(`FROM isbe_condensed_receipts r … WHERE r.amount >= ? AND {_ISBE_DONOR_RECEIPT_FILTER_SQL}`) or the bulk path at
`:3861` (`FROM bulk_receipts_clean r … WHERE COALESCE(r.amount, 0) >= ? AND {_BULK_DONOR_RECEIPT_FILTER_SQL}`). The
threshold is max(5000, 2 × p95) over the same filtered rows (5,000 locally).

- **Before F1** both constants carried
  `EXISTS (SELECT 1 FROM isbe_d2_reports d2 WHERE d2.filed_doc_id = r.filed_doc_id AND d2.archived = FALSE)` (bulk
  path: on `bulk_d2_totals_clean`). A receipt counted only if its filing had a D-2 report. Large contributions
  filed on an A-1 (the 48-hour report for contributions of $1,000 or more) have no D-2 until the next quarterly
  report re-lists them.
- **Since F1** (`database/analytics.py:614` and `:617`):
  `_BULK_DONOR_RECEIPT_FILTER_SQL = receipts_filter_sql("r")` and
  `_ISBE_DONOR_RECEIPT_FILTER_SQL = "COALESCE(r.amount, 0) > 0 AND r.archived = FALSE AND (COALESCE(r.d2_part, '') = '' OR r.d2_part LIKE '1%')"`,
  the live contributions predicate with no D-2 requirement.

### What the label equalled, before and after F1

Under the old predicate the label equals **the last month covered by a filed quarterly D-2**, so it always lags by
up to a quarter plus the 15-day filing window.

| Where | Data | Label | Evidence |
|---|---|---|---|
| Local, pre-F1 build | ISBE receipts through 2026-09-30 (Q1a); newest filing 2026-09-30 08:02 (Q1b). The Q3 quarterlies (period ending 09-30, due 10-15) are not in this download | **2026-06** | Q2a: `SUBSTR(MAX(event_date),1,7)` = 2026-06; Q2b: newest `event_date` 2026-06-30, 90,056 rows. Q10: the same receipts (≥ 5,000, part 1, not archived) reach **2026-09 without** the D-2 EXISTS and **2026-06 with** it |
| Prod, pre-F1 (Devin's observation, 2026-10-28) | ISBE imported 2026-10-27 with the Q3 quarterlies filed by 10-15 (orchestrator's records; `docs/runbooks/deployment.md` "Prod 2026-10-27 notes") | **2026-09** ("as of 09 2026") | The Q3 quarterlies cover July–September, so September was the last D-2-covered month; October's A-1s were dropped |
| Prod, after F1 + Devin's `refresh-analytics --with-snapshot` (06:27–06:32:53Z per the orchestrator) | same import; A-1s now counted | **2026-10** | GET 2026-10-29 06:53:03Z of `/investigate` (tab=patterns, period=cycle:2026): HTTP 200, heading "Unusual Patterns: 2026-10", month list 2026-10, 2026-09, 2026-08. Same GET's Data Status strip: "Local receipts: 2026-10-27 (2 days old)" |

### Verdicts

- **H1 (the analytics filter drops A-1 rows, so the newest month lags): confirmed, the cause.** Locally the gap is
  exactly the D-2 EXISTS (Q10). On prod the observed 2026-09 is the last quarterly-covered month, and the same data
  refreshed under F1 gives 2026-10.
- **H2 (prod had not run `refresh-analytics` after the October import): secondary.** A refresh run before F1 would
  still have shown 2026-09, because the filter, not the timing, removed October. Timing still matters in general:
  the label moves only when `refresh-analytics` runs (decay "stale until the next `refresh-analytics`").

### Should October be included?

Yes, and since F1 and the 06:27Z prod refresh it is, **as a partial month**: on 2026-10-29 the October rows are
A-1 large contributions received through 2026-10-27, with no quarterly yet. The heading does not say the month is
partial, so a reader can take "2026-10" for a complete month. That is the remaining label decision (RF-041).

### Fix options (owner terminal 1)

Footprint for every option: `webapp/templates/index.html:20` (button text), `webapp/templates/investigate.html:16`
(the page's own button) and `:187` (heading), the `month_key` line in `webapp/routes/main.py` (`:2768`), and
`docs/manual_tests.md` MT-022.

| Option | What changes | Pros | Cons |
|---|---|---|---|
| (a) **Relabel (recommended)** | Button "Find recent unusual patterns"; heading "Unusual patterns: October 2026 (data through 2026-10-27)", the date being `MAX(received_date)` of the receipts (already in the Data Status snapshot, `latest_data_date("isbe")`) | Honest about what the month is and that it is partial; no query change; no empty pages | Two template edits and one route value |
| (b) Clock month | `month_key` defaults to `datetime.utcnow()`'s month; show "No flagged contributions yet this month" when empty | The label means the calendar | Empty for the first days or weeks of each month and whenever imports lag; the reader lands on nothing |
| (c) Keep the label, add a note | Keep "this month"; add a help line "latest month in the data; data through <date>" | Smallest change | The button still promises the calendar month |

**Recommendation: (a).** It implies an MT-022 revision: today MT-022 passes when the heading's month equals the
month of the Data Status "Local receipts" date (it passes on prod as of 06:53Z: 2026-10 vs 2026-10-27). Revised,
step 3 reads the new heading, and Pass becomes "the heading's month equals the Data Status month, and its 'data
through' date equals the 'Local receipts' date"; Fail adds "a 'data through' date that differs from the Data Status
box".

Related context, not a change requested here: F1 also made the committee monthly spike flags skip months after the
committee's latest quarterly (`database/analytics.py:1642`, `:1991`), a deliberate choice because those months hold
only A-1 rows. The large-contribution table has no such cutoff, which is why it now reaches October.

## Findings, in order of money and reader impact

1. **The D-2 reconciliation pages are nine months old and nothing in the chain rebuilds them (RF-006).**
   `bulk_d2_receipts_recon` and `bulk_d2_expenditures_recon` are created only by the legacy `import-bulk-download`
   (`database/bulk_download_loader.py:1085`, `:1231`). Locally their newest dates are 2025-12-31 and 2026-01-28
   (Q4a, Q4b) while receipts run to 2026-09-30. They feed `/d2-reconciliation/`, `/d2-expenditures-reconciliation/`,
   the `/analytics/risk` "D2 Reconciliation Signals", the overview's reconciliation preview and
   `/api/analytics/reconciliation`. The pages carry no date, so readers cannot tell.
2. **"This month" was a quarter behind (RF-009, RF-041).** Root cause fixed by F1; the label still overstates
   ("this month", partial month shown as complete).
3. **The chain actually used on prod has no freshness gate and runs the swap with the service up (RF-048,
   RF-049).** `scripts/refresh_prod_chain.sh` never runs `validate-freshness`, and its step 4 runs `sunshine-import`
   (which runs `scripts/swap_bulk_to_isbe.py`) while ilcf-web.service serves. The 2026-10-27 run deadlocked
   there (`docs/runbooks/deployment.md`, "Prod 2026-10-27 notes"). A failed swap only warns, leaving the
   candidate-finance aggregate and the race feed on old data.
4. **Federal Schedule B is refreshed by nothing scheduled (RF-047).** The main sync does not touch B or E; prod has
   an hourly Schedule E timer, but no B timer and no chain step. The local copy shows B at 2025-12-31.
5. **The candidate-finance aggregate stops at the last quarterly D-2 by design (RF-004).** `/candidate-finance/`,
   the race builder, `/search` candidates, the home and candidate directory cards and `/compare` read it; A-1
   receipts after the last quarterly are not in their "current cycle" figures (locally `bulk_candidate_committee_finance_agg.period_end_date` 2026-06-30
   vs receipts 2026-09-30).
6. **Match tables carry no build time (RF-016).** Every row's `created_at` is NULL in the nine cross-matching
   tables, so neither a person nor `validate-freshness` can tell how old they are.
7. **Manual sources have no schedule and no gate (RF-017, RF-018, RF-019).** Lobbying, IRS 527 and OpenBook were
   last loaded locally in February 2026; prod dates are not visible on any public page.
8. **The default window follows the clock (RF-039).** On 2027-01-01 every default window becomes cycle 2028, which
   holds almost no data until filings arrive.
9. **Smaller items:** the materialization version was not bumped by F1 (RF-010); committee families are never
   re-seeded (RF-021); the frozen legacy scraper pages say "Recent" (RF-023, RF-044); the docs call the FEC
   payload cache "SQLite" (RF-037); the operator guide is out of date (RF-050); nothing schedules the ISBE import
   (RF-051).

What is in good shape: the ISBE tables, matviews and compat views (RF-001…RF-003, RF-005), the analytics donor
tables (RF-007, RF-008), `donor_entity_local` since F6 (RF-012), the route caches (flushed by `refresh-analytics`,
`sunshine-import` and the swap; RF-024…RF-027), and the Data Status strip, whose labels name the date they measure
(RF-042).

## Flags for Devin

FIX-PROPOSED first, ranked by reader impact, then REVIEW.

| # | RF | What is stale or misleading | Who sees it | The decision | Proposed fix, footprint, owner |
|---|---|---|---|---|---|
| 1 | RF-006 | D-2 reconciliation tables built only by the legacy loader; locally 2025-12-31 / 2026-01-28 | Readers of `/d2-reconciliation/`, `/d2-expenditures-reconciliation/`, `/analytics/risk`, the analytics overview, `/api/analytics/reconciliation` | Rebuild them on every import, or label / hide the pages until they are rebuilt | Build both from `isbe_d2_reports` + `isbe_condensed_receipts` / `isbe_condensed_expenditures` at the end of `scripts/swap_bulk_to_isbe.py` (or as views), with a guard test; meanwhile an "as of <newest date>" line. Owner: the other session (swap script), terminal 1 (readers) |
| 2 | RF-049 | Prod chain runs the compat swap with the service up; a deadlock leaves RF-004 / RF-005 stale and the chain carries on | Everyone during and after a refresh (downtime or stale race and candidate-finance figures) | Accept a short planned stop around the swap | `sunshine-import --skip-compat-swap`, then stop ilcf-web, run `scripts/swap_bulk_to_isbe.py`, start; fail the chain if the swap fails. Footprint `scripts/refresh_prod_chain.sh`. Owner: the other session; item 4 |
| 3 | RF-048 | `validate-freshness` is not in the prod chain and covers only ISBE filings and FEC A / B / E | Nobody sees it directly; a no-op refresh goes unnoticed | Make the gate mandatory in the chain; decide which new specs fail versus warn | Run `validate-freshness --source isbe` after the ISBE step and `--source fec` after the FEC steps (failing); add warn-only specs for the analytics build time, `donor_entity_local`, lobbying / 527 / OpenBook loads and match tables. Footprint `database/data_freshness.py` (terminal 1), chain script (other session) |
| 4 | RF-047 | Schedule B is refreshed by no timer and no chain step (prod has an E timer only) | Readers of `/federal-finance/money-flow`, overview, candidate pages, `/live-feed`, home Schedule B card | Weekly chain step or a B timer like the E one | Add `backfill-fec-schedule-b --cycle 2026 --max-calls 1000 --refresh-cache` to the chain (or ship `docs/systemd/` units for B). Owner: the other session; Devin installs any unit |
| 5 | RF-050 | `docs/data_update_guide.md` tells operators to run the legacy ISBE loader and a FEC sync without `--refresh-cache` | Whoever refreshes by following the guide | Rewrite or retire the guide in favour of one chain | Edits listed under "Reconciliation notes". Owner: whoever owns doc sync |
| 6 | RF-041 | "This month" means the latest month with flagged rows; prod's 2026-10 is a partial month shown as complete | Home "Start Here" readers | Relabel (option a), clock month (b) or a note (c) | Option (a), footprint above, MT-022 revision. Owner terminal 1 |
| 7 | RF-004 | Candidate-committee aggregate stops at the last quarterly D-2; A-1 receipts after it are not in "current cycle" totals | `/candidate-finance/`, `/compare`, home and `/candidates` cards, `/search` candidates, state races | Accept as "sworn D-2 totals" and label it, or add post-quarterly A-1s | Label "D-2 totals through <period end>" (terminal 1, templates); a data change would be a separate design |
| 8 | RF-016 | Match tables have no build timestamp | Readers of lobbying, 527, investigate, relationships pages; the gate | Record a build time per match job | Stamp `created_at` (or a run table) in `database/cross_matching.py`, then gate on it. Owner terminal 1 |
| 9 | RF-017 | Lobbying is loaded by hand, no schedule or gate | `/lobbying/` pages, home lobbying card | Schedule it (weekly, when the daily extract publishes) or label the extract date | Add a chain slot (below) and a warn-only freshness spec. Owner: the other session; item 4 |
| 10 | RF-018 | IRS 527 is loaded by hand, no schedule or gate | `/527/` pages, home 527 cards | Monthly slot or label | Chain slot (below), warn-only spec. Owner: the other session; item 4 |
| 11 | RF-019 | OpenBook is loaded by hand, no schedule or gate | `/openbook/` pages, home OpenBook card | Cadence (quarterly?) or label | Chain slot (below), warn-only spec. Owner: the other session; item 4 |
| 12 | RF-039 | Default window follows the clock; on 2027-01-01 defaults become cycle 2028 | Every list and dashboard | Clock or data decides the default cycle; decide before January 2027 | If data: default to the cycle of the newest receipt in `webapp/utils/time_scope.py`. Owner terminal 1 |
| 13 | RF-051 | Nothing schedules the ISBE import, cross-matching or analytics; the Sunday FEC timer refreshes analytics but not matches | All derived pages between manual runs | One scheduled chain versus today's manual chain plus a FEC timer | The proposed chain below, scheduled by item 4. Owner Devin (systemd), the other session (script) |
| 14 | RF-021 | Committee families are never re-seeded; new re-registrations are never proposed | `/committees/` by family, candidate profiles, committee banners, entity tabs | Re-seed after each import (pending rows only) or keep a periodic manual seed | `scripts/committee_families.py seed` as a monthly chain slot. Owner: the other session |
| 15 | RF-010 | F1 kept `ISBE_RECEIPTS_MATERIALIZATION_VERSION = 3` / `BULK_RECEIPTS_MATERIALIZATION_VERSION = 2`; a database built under the old filter passes the version gate | Anyone running a copy that was not refreshed after F1 (local, restored dumps) | Bump the versions or accept | Bump both in `database/analytics.py`. Owner terminal 1 |
| 16 | RF-023 | Frozen legacy scraper tables behind live-looking pages | `/legacy`, `/reports/`, `/committees/legacy/<int:committee_id>`, the v1 API | Keep and label "archived scrape (2026-02)", or retire | Template label or route removal. Owner terminal 1 |
| 17 | RF-044 | "Recent Contributions" heading on a frozen legacy page | `/committees/legacy/<int:committee_id>` | Folded into #16 | Rename to "Contributions (archived scrape)". Owner terminal 1 |
| 18 | RF-037 | Docs call the FEC payload cache "legacy SQLite"; it is the `raw_extractions` table | Operators reading CLAUDE.md or the guide | Accept the wording fix | Edit listed under "Reconciliation notes". Owner: the other session |

## Proposed refresh chain

Item 4 (scheduled server update) consumes this section. TODAY = already in `scripts/refresh_prod_chain.sh`; ADD =
new, with the row it closes. Commands run on the server from /srv/illinois_campaign_finance/app with
`PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3` and the env file exported, as the script does.

**Weekly (and every few days near filing deadlines)**

| # | Step | Status |
|---|---|---|
| 1 | Row-count baseline of `Bulk_download/*.txt` into `Bulk_download/.row_counts_prev` | TODAY (step 1) |
| 2 | Download the 12 ISBE files (curl loop) | TODAY (step 2) |
| 3 | `python3 scripts/check_row_counts.py` (fails on a > 5 % drop) | TODAY (step 3) |
| 4 | `$PYTHON run.py sunshine-import --bulk-dir Bulk_download --skip-compat-swap` (load, matviews, privacy re-apply, cache flush) | TODAY without the flag; ADD `--skip-compat-swap` (RF-049) |
| 5 | `systemctl stop ilcf-web.service` → `$PYTHON scripts/swap_bulk_to_isbe.py` (fail the chain on a non-zero exit) → `systemctl start ilcf-web.service` | ADD (RF-049) |
| 6 | Rebuild the D-2 reconciliation tables (inside step 5 once the swap script does it) | ADD (RF-006), after the code change |
| 7 | `$PYTHON run.py validate-freshness --source isbe` (gate: stop the chain on failure) | ADD (RF-048); CLAUDE.md already lists it |
| 8 | `$PYTHON run.py sync-fec-il-federal --refresh-cache` (also runs `refresh-fec-local-donor-matches`) | TODAY (step 5) |
| 9 | `$PYTHON run.py backfill-fec-schedule-b --cycle 2026 --max-calls 1000 --max-pages-per-committee 25 --refresh-cache` | ADD (RF-047) |
| 10 | Schedule E: keep the hourly il-campaign-fec-schedule-e-catchup.timer (or run `backfill-fec-schedule-e --cycle 2026 --max-calls 1000 --max-pages-per-candidate 25 --refresh-cache` here) | TODAY (timer, outside the script) |
| 11 | `$PYTHON run.py validate-freshness --source fec` (gate) | ADD (RF-048) |
| 12 | `$PYTHON run.py run-cross-matching --only all` | TODAY (step 6) |
| 13 | `$PYTHON run.py refresh-analytics --with-snapshot` (aggregates under the F1 filter, then `donor_entity_local` (F6), then the cache flush) | TODAY (step 7) |
| 14 | `$PYTHON run.py validate-freshness --warn-only` with the extended specs (analytics build time not older than the import, `donor_entity_local`, match tables, manual sources) | ADD (RF-048, RF-016), warn-only until the specs exist |
| 15 | `$PYTHON scripts/warm_caches.py` | TODAY (step 7B) |
| 16 | `systemctl restart ilcf-web.service` + local smoke GET of `/` | TODAY (step 8) |
| 17 | Public check: the Data Status strip's "Local receipts" date and the `/investigate` (tab=patterns) heading month agree (MT-022) | ADD (RF-041), a read-only check |

The Sunday il-campaign-fec-sync.timer (`scripts/sync-fec.sh`) can stay as a mid-week FEC top-up; it already runs
`refresh-analytics` (so F6 and the flush), but not `run-cross-matching`, so match tables wait for the weekly chain.

**Manual-source slots (after step 3 of the next weekly run, so steps 12–16 pick them up)**

| Source | Cadence | Command | Freshness line (read-only check) |
|---|---|---|---|
| IL SOS lobbying | weekly, when a new daily extract publishes | `$PYTHON run.py import-lobbying --file Bulk_download/Lobbyist_Entity_Client_Data_Daily_<YYYYMMDD>.csv` | `SELECT MAX(created_at) FROM lobbying_entities;` within 14 days |
| IRS 527 | monthly | `$PYTHON run.py import-irs527 --file <FullDataFile.txt> --illinois-only` (rebuilds the rollups) | `SELECT MAX(created_at) FROM irs527_reports;` within 45 days |
| OpenBook | quarterly or as needed | `$PYTHON run.py import-openbook-batch --generate-seeds --max-vendors 20` | `SELECT MAX(completed_at) FROM openbook_scrape_runs;` within 120 days |
| Committee families | monthly (pending rows only) | `$PYTHON scripts/committee_families.py seed` | `SELECT MAX(created_at) FROM committee_family;` after the last import |
| Chicago (no site reader) | optional | `$PYTHON run.py import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert` | none needed until a page reads it |

## Reconciliation notes (recommended edits, not made here)

**`docs/data_update_guide.md`** (RF-050, RF-037):

1. "Manual FEC Sync": add `--refresh-cache` to the `sync-fec-il-federal` line, with one sentence on the silent
   no-op; the wrapper already passes it.
2. "Automated Weekly Sync": note that the unit files shown are installed on prod but not shipped in `docs/systemd/`
   (only the Schedule E pair is); either ship them or say where they live.
3. "ISBE (State) Updates": replace Option 1's `import-bulk-download` + `refresh-analytics` with the proposed chain
   (`sunshine-import --download`, swap with the service stopped, `validate-freshness --source isbe`,
   `run-cross-matching --only all`, `refresh-analytics --with-snapshot`, `scripts/warm_caches.py`, restart). Mark
   Option 2 (scrapers) as writing the frozen legacy tables only; drop Option 3 or mark it superseded by the native
   `--download`.
4. "Post-Update Verification": add `validate-freshness`, the Data Status strip dates, and MT-022's month check.
5. Add the Schedule B gap: no timer exists for `backfill-fec-schedule-b`.

**CLAUDE.md "Data Refresh Schedule"** (RF-037, RF-047…RF-049):

1. Weekly step 1: `sunshine-import --download --skip-compat-swap`, then the swap with ilcf-web.service stopped
   (the existing note under "Notes" becomes a step).
2. Weekly step 2: add `backfill-fec-schedule-b --refresh-cache` (Schedule E stays on its hourly timer).
3. Weekly step 3: `run-cross-matching --only all` → `refresh-analytics --with-snapshot` (now also rebuilds
   `donor_entity_local`, F6) → `scripts/warm_caches.py` → restart.
4. "FEC silent no-op": "legacy SQLite payload cache" → "the FEC payload cache (`raw_extractions` table)".
5. Add one line pointing to `docs/refresh_inventory.md` as the list of what each step refreshes.
6. Make `scripts/refresh_prod_chain.sh` and this section say the same thing (today they differ on the gate, the
   swap and the warm step).

## Appendix: read-only queries

Local `ilcf`, 2026-10-29 06:50–06:55Z, `PGOPTIONS='-c default_transaction_read_only=on' psql ilcf -X`.

- **Q1a** `SELECT MAX(received_date) FROM isbe_receipts WHERE archived = FALSE;` → 2026-09-30
- **Q1b** `SELECT MAX(received_datetime) FROM isbe_filed_docs;` → 2026-09-30 08:02:06
- **Q1c** `SELECT MAX(received_date) FROM isbe_condensed_receipts WHERE archived = FALSE;` → 2026-09-30
- **Q1d** `SELECT MAX(expended_date) FROM isbe_condensed_expenditures WHERE archived = FALSE;` → 2026-09-28
- **Q1e** `SELECT MAX(received_datetime) FROM isbe_most_recent_filings;` → 2026-09-29 15:29:18
- **Q1f** `SELECT MAX(last_receipt_date) FROM isbe_committee_money;` → 2026-09-30
- **Q2a** `SELECT SUBSTR(MAX(event_date), 1, 7) FROM analytics_large_contributions;` → 2026-06
- **Q2b** `SELECT MAX(event_date), COUNT(*), MAX(created_at) FROM analytics_large_contributions;` → 2026-06-30, 90,056, 2026-10-26 23:51:22
- **Q2c/Q2d/Q2f** `SELECT MAX(updated_at) FROM analytics_donor_committee_agg;` (and `analytics_donor_summary`, `analytics_committee_monthly_totals`) → 2026-10-26 23:51:22 each
- **Q2e** `SELECT MAX(updated_at) FROM analytics_donor_cycle_summary;` → 2026-10-27 00:24:57
- **Q2g** `SELECT MAX(updated_at) FROM donor_entity_local WHERE source = 'bulk_receipts';` → 2026-02-13 10:26:05
- **Q2h** `SELECT MAX(updated_at) FROM donor_entity_local_member;` → 2026-02-13 10:26:05
- **Q3a** `SELECT source, large_row_count, large_threshold, materialization_version, materialization_notes, refreshed_at FROM analytics_materialized_meta;` → bulk_receipts, 90,056, 5000, 3, "isbe_direct_active_non_archived_d2_part1_with_active_d2_filing", 2026-10-26 23:51:22
- **Q3b** `SELECT snapshot_type, status, COUNT(*), MAX(completed_at) FROM analytics_snapshots GROUP BY 1, 2;` → "dashboard_full" 2026-09-30 19:37; federal snapshot types 2026-10-29 01:17
- **Q3c** `SELECT relname, relkind FROM pg_class WHERE relname IN ('bulk_candidate_committee_finance_agg', 'isbe_race_candidacies', 'bulk_d2_receipts_recon', 'bulk_d2_expenditures_recon', 'bulk_receipts_clean');` → views (v) except the two recon tables (r)
- **Q4a** `SELECT MAX(last_receipt_date) FROM bulk_d2_receipts_recon;` → 2025-12-31
- **Q4b** `SELECT MAX(last_expenditure_date) FROM bulk_d2_expenditures_recon;` → 2026-01-28
- **Q4c** `SELECT MAX(period_end_date) FROM bulk_candidate_committee_finance_agg;` → 2026-06-30
- **Q5a** `SELECT MAX(contribution_receipt_date), MAX(updated_at) FROM fec_schedule_a_contributions;` → 2026-07-01, 2026-09-30 19:32
- **Q5b** `SELECT MAX(disbursement_date), MAX(updated_at) FROM fec_schedule_b_disbursements;` → 2025-12-31, 2026-02-13 15:55
- **Q5c** `SELECT MAX(expenditure_date), MAX(updated_at) FROM fec_schedule_e_independent_expenditures;` → 2026-02-10, 2026-02-13 16:22
- **Q5d** `SELECT MAX(updated_at) FROM fec_candidate_cycle_totals;` → 2026-09-30 19:23
- **Q5e** `SELECT MAX(refreshed_at) FROM fec_local_donor_matches;` → 2026-09-30 19:32
- **Q5f** `SELECT MAX(updated_at) FROM fec_candidate_match;` → 2026-09-30 19:23
- **Q6a–Q6i** `SELECT MAX(created_at) FROM <match table>;` for `lobbying_donor_matches`, `lobbying_expenditure_matches`, `lobbying_527_matches`, `irs527_committee_matches`, `irs527_expenditure_recipient_matches`, `irs527_director_donor_matches`, `irs527_director_candidate_matches`, `irs527_director_address_matches`, `irs527_org_address_matches` → NULL for all nine
- **Q7a** `SELECT MAX(reg_year), MAX(created_at) FROM lobbying_entities;` → 2026, 2026-02-15 02:39
- **Q7b** the same on `lobbying_lobbyist_registrations` → 2026, 2026-02-15 02:39
- **Q7c/Q7d** `SELECT MAX(date), MAX(created_at) FROM irs527_contributions;` / `irs527_expenditures` → `99999`, 2026-02-15 00:35 / 20251231, 2026-02-13 19:03
- **Q7e/Q7f** `SELECT MAX(updated_at) FROM irs527_contribution_rollup;` / `irs527_contributor_rollup` → 2026-02-16 07:31 / 2026-02-16 01:28
- **Q7i** `SELECT MAX(completed_at) FROM openbook_scrape_runs;` → 2026-02-16 16:36
- **Q7j** `SELECT MAX(last_seen_at) FROM openbook_contracts_raw;` → 2026-02-16 16:32
- **Q7k/Q7l** `SELECT status, COUNT(*) FROM committee_family GROUP BY status;` / `SELECT MAX(created_at) FROM committee_family;` → pending 753 / 2026-10-26 21:48
- **Q7m** `SELECT COUNT(*), MAX(last_applied_at) FROM privacy_redactions;` → 0, NULL
- **Q7n** `SELECT MAX(transaction_date) FROM contributions;` and `SELECT MAX(filed_date) FROM reports;` → NULL, NULL
- **Q7o** `SELECT MAX(updated_at) FROM raw_extractions WHERE source_type LIKE 'fec_api:%';` → 2026-09-30 19:32
- **Q8** `SELECT COUNT(*)` on the nine match tables, the Chicago tables, the legacy tables and `openbook_vendor_match` → matches 30…260,387 rows; Chicago 0; legacy committees 344, reports 650, contributions 766; OpenBook matches 2,451
- **Q9** `SELECT job_name, updated_at FROM cross_matching_job_fingerprints ORDER BY updated_at DESC;` → one row, 2026-02-15 08:01
- **Q10** (decisive for the worked example):

  ```sql
  SELECT SUBSTR(MAX(r.received_date)::text, 1, 7) AS without_d2_exists,
         SUBSTR(MAX(r.received_date) FILTER (WHERE EXISTS (SELECT 1 FROM isbe_d2_reports d2
                WHERE d2.filed_doc_id = r.filed_doc_id AND d2.archived = FALSE))::text, 1, 7) AS with_d2_exists
  FROM isbe_condensed_receipts r
  WHERE r.archived = FALSE AND r.d2_part LIKE '1%' AND r.amount >= 5000;
  ```

  → without 2026-09, with 2026-06.

Orchestrator re-ran Q1a, Q2a, Q2b, Q10 at 07:03Z: identical.

**Public GET:** 2026-10-29 06:53:03Z, https://www.followthemoneyil.com/investigate?tab=patterns&period=cycle:2026
→ HTTP 200; heading "Unusual Patterns: 2026-10"; month list 2026-10, 2026-09, 2026-08; Data Status strip "Local
receipts: 2026-10-27 (2 days old)", "Federal last sync: 2026-10-27 16:20:25", "Federal latest report coverage:
2026-10-15", "Federal latest receipt date (row-level): 2026-09-30 (29 days old)".
