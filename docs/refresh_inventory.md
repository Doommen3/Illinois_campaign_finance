# Refresh inventory: what goes stale when new data arrives

The living list of every site piece whose content is **derived**: it changes only when a command rebuilds it, a
cache flush or TTL expires it, or a person re-runs an import. For each piece: what refreshes it, whether the
written weekly chain does, how it decays, and what the local database showed when measured. To-do item 3; item 4
(scheduled server update) consumes it. The dated review with the worked example ("this month" = 09 2026), the
flags for Devin and the proposed chain is `docs/audits/staleness_accounting_2026-10-29.md`.

**As of:** 2026-10-29, branch worktree-ui-improvements at main 48e5399 (includes fix-8b F1 and F6, merge
3398990, deployed 2026-10-29 06:18Z, and the 527 period fix 4529328, deployed 06:39Z).
**Local measurements:** 2026-10-29 06:50–06:55Z, read-only (`PGOPTIONS='-c default_transaction_read_only=on'`), on
the local `ilcf`. The local analytics tables were built 2026-10-26 23:51, **before** F1 and F6, so they still show
the pre-F1 behaviour. Query ids (Q1a…Q10) are listed under "Local measurements" at the end.
**Prod observations:** public HTTPS GETs only, each dated in the row. No SSH, no psql.

## Legend

- **Row id** `RF-NNN`: sequential across categories, never reused.
- **Coverage:** page classes from `docs/page_inventory.md` (C-01…C-17, S-01…S-06); the "Page classes covered"
  section maps every class to its rows.
- **In the weekly chain?** `yes` = a step of the written chain refreshes it (CLAUDE.md "Data Refresh Schedule" or
  `scripts/refresh_prod_chain.sh`; differences are spelled out under "Reconciliation"); `no` = no chain step does;
  `manual` = a documented command a person runs as needed; `external` = outside the repo (Cloudflare, Redis,
  systemd on the server; owner Devin); `TTL` = expires by time, no step needed.
- **Decay mode:** stale-until-rebuild (correct until new data lands, then wrong until the step runs);
  TTL <s> (wrong for at most that long after a flush-less change); never-rebuilt (no chain step ever rebuilds
  it); clock-derived (moves with the calendar, not the data); data-derived-label (a label whose meaning moves
  with the data); external.
- **Flag:** **OK** = refreshed by a chain step today and the label is honest; **REVIEW** = refreshed, but the
  behaviour or label needs Devin's judgement; **FIX-PROPOSED** = not refreshed by any chain step, or refreshed by a
  step that drops data, with a concrete fix, footprint and owner. Owners: terminal 1 for `database/analytics.py`,
  `database/data_freshness.py`, the route modules under webapp/routes/, templates; the other session for `cli/commands.py`, chain
  scripts, CLAUDE.md, README; Devin for external (Cloudflare, Redis, systemd install).
- **Notation:** backticks hold database objects, CLI commands, files and code symbols. Route-cache names (the
  `RouteCache("...")` argument, not database objects) are written in double quotes.

## A. Database artifacts rebuilt by commands

| Id | Piece | Where shown | Derived from | Refreshed by | In the weekly chain? | Decay mode | Observed locally (as-of) | Flag |
|---|---|---|---|---|---|---|---|---|
| RF-001 | ISBE base tables (`isbe_receipts`, `isbe_expenditures`, `isbe_filed_docs`, `isbe_d2_reports`, `isbe_committees`, `isbe_candidates`, `isbe_candidacies`, `isbe_candidate_committees`, officers, investments) | Every ISBE page: C-01…C-08, C-14, S-01…S-04 | ISBE bulk files in `Bulk_download/` (dropped and reloaded each run) | `sunshine-import --download` (`scripts/isbe_sunshine_etl.py`) | yes | stale-until-rebuild | receipts through 2026-09-30 (Q1a); newest filing 2026-09-30 08:02 (Q1b) | OK |
| RF-002 | ISBE matviews `isbe_condensed_receipts`, `isbe_condensed_expenditures`, `isbe_most_recent_filings`, `isbe_committee_money`, `isbe_candidate_money` | C-02, C-03, C-04, C-05, S-02; the Data Status strip reads `isbe_condensed_receipts` | RF-001 | `sunshine-import` (built after the load, before the flush at `scripts/isbe_sunshine_etl.py:1992`) | yes | stale-until-rebuild | condensed receipts 2026-09-30 (Q1c); expenditures 2026-09-28 (Q1d); most-recent filings 2026-09-29 (Q1e); committee money 2026-09-30 (Q1f) | OK |
| RF-003 | Compat views `bulk_receipts_clean`, `bulk_expenditures_clean`, `bulk_committees_clean`, `bulk_candidates_clean`, `bulk_d2_totals_clean`, `bulk_cmte_candidate_links_clean`, `bulk_committee_candidate_links` | C-01, C-04, C-06, C-07, C-08, C-11, C-14, C-16, S-01, S-02, S-04 | RF-002 (plain views, read live) | `sunshine-import` (runs `scripts/swap_bulk_to_isbe.py`) | yes | none while the view exists (live read) | all plain views (Q3c) | OK |
| RF-004 | Candidate-committee aggregate `bulk_candidate_committee_finance_agg` (MATERIALIZED VIEW on prod since load-time plan P6; still a plain view locally) | C-06 `/candidate-finance/`, C-07 race builder, C-14 `/search` candidates, S-01 and S-03 candidate cards, S-04 `/compare` | `isbe_d2_reports` periods + links + `isbe_receipts` | `sunshine-import` (swap script drops and rebuilds it) | yes | stale-until-rebuild; by design its periods end at the newest **D-2** report, so receipts after the last quarterly (A-1s) are outside it | local is a plain view (Q3c); newest `bulk_candidate_committee_finance_agg.period_end_date` 2026-06-30 while receipts run to 2026-09-30 (Q4c vs Q1a) | REVIEW |
| RF-005 | Race feed `isbe_race_candidacies` (view) | C-03 race tabs, C-07 `/analytics/state-races/`, C-08 "Top State Races" | `isbe_candidacies` × candidates × linked committees | `sunshine-import` (swap script) | yes | none (view) | view present (Q3c) | OK |
| RF-006 | D-2 reconciliation tables `bulk_d2_receipts_recon`, `bulk_d2_expenditures_recon` | C-05 `/d2-reconciliation/`, `/d2-expenditures-reconciliation/`; C-08 `/analytics/risk` "D2 Reconciliation Signals" and the overview's "Reconciliation Preview"; C-16 `/api/analytics/reconciliation`; the "dashboard_full" snapshot (RF-011) | Legacy bulk_*_clean_legacy load (CREATE TABLE AS in `database/bulk_download_loader.py:1085` and `:1231`) | only `import-bulk-download` (the legacy loader); `sunshine-import` and the swap script do not touch them | no | never-rebuilt | newest receipt in the receipts recon 2025-12-31, expenditures recon 2026-01-28 (Q4a, Q4b), nine months behind RF-001 | FIX-PROPOSED: rebuild both tables from `isbe_d2_reports` + `isbe_condensed_receipts` / `isbe_condensed_expenditures` at the end of the swap script (or as views), so every `sunshine-import` refreshes them; footprint `scripts/swap_bulk_to_isbe.py` + its guard tests; owner the other session (chain script) with terminal 1 for the analytics readers. Until then label the pages "as of <date>" |
| RF-007 | Analytics donor aggregates `analytics_donor_committee_agg`, `analytics_donor_summary`, `analytics_committee_monthly_totals` | C-01 key pages, C-04 "Top Donors", C-08 dashboards, C-10 match profile (local side), C-11 money destinations, C-14 `/search` donors, C-16, S-01 "Top Donors" | `isbe_condensed_receipts` through `_ISBE_DONOR_RECEIPT_FILTER_SQL` | `refresh-analytics` (`refresh_analytics_materialized()` in `database/analytics.py`) | yes | stale-until-rebuild | built 2026-10-26 23:51 (Q2c, Q2d, Q2f), pre-F1 filter | OK (F1 aligned the filter with the live predicate, deployed 06:18Z) |
| RF-008 | Per-cycle donor table `analytics_donor_cycle_summary` | C-01 `/donors/` when the window is exactly one cycle | same filter as RF-007 | `refresh-analytics` (`_refresh_donor_cycle_summary()`) | yes | stale-until-rebuild | built 2026-10-27 00:24 (Q2e) | OK |
| RF-009 | Large-contribution flags `analytics_large_contributions` | C-14 `/investigate` "Unusual Patterns" tab and its month list; C-08 `/analytics/risk` anomalies; C-16 `/api/analytics/anomalies` | `isbe_condensed_receipts` rows with `amount >= large_threshold` (threshold = max(5000, 2 × p95 of the same filtered rows)) | `refresh-analytics`: ISBE direct path insert `database/analytics.py:3617` (`FROM isbe_condensed_receipts r … WHERE r.amount >= ? AND {_ISBE_DONOR_RECEIPT_FILTER_SQL}`), bulk path insert `:3861` (`FROM bulk_receipts_clean r … WHERE COALESCE(r.amount, 0) >= ? AND {_BULK_DONOR_RECEIPT_FILTER_SQL}`). Current constants (`:614`, `:617`): `_BULK_DONOR_RECEIPT_FILTER_SQL = receipts_filter_sql("r")` and `_ISBE_DONOR_RECEIPT_FILTER_SQL = "COALESCE(r.amount, 0) > 0 AND r.archived = FALSE AND (COALESCE(r.d2_part, '') = '' OR r.d2_part LIKE '1%')"`. Before F1 both added `EXISTS (SELECT 1 FROM isbe_d2_reports d2 WHERE d2.filed_doc_id = r.filed_doc_id AND d2.archived = FALSE)` (bulk: on `bulk_d2_totals_clean`) | yes | stale-until-rebuild | local (pre-F1 build 2026-10-26 23:51): newest `analytics_large_contributions.event_date` 2026-06-30, 90,056 rows, threshold 5,000 (Q2b, Q3a); the same receipts without the D-2 EXISTS reach 2026-09 (Q10). Prod GET 2026-10-29 06:53Z: `/investigate` (tab=patterns, period=cycle:2026) heads "Unusual Patterns: 2026-10" | OK (root cause fixed by F1; the label is RF-041) |
| RF-010 | Materialization marker `analytics_materialized_meta` | none directly; `_rows_for_source_exist()` / `_materialized_source_is_current()` (`database/analytics.py:755`) decide whether readers trust RF-007/RF-009 | written by the same refresh | `refresh-analytics` | yes | stale-until-rebuild | `refreshed_at` 2026-10-26 23:51, `materialization_version` 3, notes still the pre-F1 "isbe_direct_active_non_archived_d2_part1_with_active_d2_filing" (Q3a) | REVIEW: F1 changed the filter but kept `ISBE_RECEIPTS_MATERIALIZATION_VERSION = 3` / `BULK_RECEIPTS_MATERIALIZATION_VERSION = 2`, so a database built under the old filter still passes the version gate; only the notes string tells them apart. Decision: bump the versions (forces a live fallback until the next refresh) or accept (prod is refreshed). Owner terminal 1 |
| RF-011 | Dashboard snapshot rows in `analytics_snapshots` (snapshot type "dashboard_full") | C-08 `/analytics/` with ?load_mode=full ("Last computed" in `webapp/templates/analytics/_status.html`) | `build_dashboard_full_snapshot()` over RF-006/RF-007/RF-009 | `refresh-analytics --with-snapshot` (default on) | yes | stale-until-rebuild (served stale, never rebuilt from a request) | newest completed 2026-09-30 19:37 (Q3b): older than the 2026-10-26 aggregates, so the last local refresh skipped the snapshot | OK (the label shows the computed time) |
| RF-012 | Merged donor entities `donor_entity_local`, `donor_entity_local_member` | C-01 `/donors/` all-time directory and `/donors/entity/<path:entity_id>`; C-07 race "Donors" (`donor_entity_local_member`); C-15 `/api/donors` | `analytics_donor_summary` | `refresh-analytics` (F6: `refresh_analytics_materialized()` ends with `_rebuild_local_donor_entities_step()`, `database/analytics.py:4196`, the same arguments as `rebuild-local-donor-entities`; merge 3398990, deployed 2026-10-29 06:18Z). `sunshine-import` does not call it | yes (via the chain's `refresh-analytics` step) | stale-until-rebuild (until the next `refresh-analytics`) | built 2026-02-13 10:26 (Q2g, Q2h); the local DB predates F6 | OK |
| RF-013 | Federal↔local donor matches `fec_local_donor_matches` | C-09 `/federal-finance/matching`, C-10 match profile, S-01 "Matched Donor Pairs" | FEC Schedule A × local donor keys | `refresh-fec-local-donor-matches` (runs automatically at the end of `sync-fec-il-federal`) | yes | stale-until-rebuild; after an ISBE-only import it still matches against the previous local donors until the next FEC sync (the chain runs the sync after `sunshine-import`, so the weekly order is right) | refreshed 2026-09-30 19:32 (Q5e) | OK |
| RF-014 | FEC donor identities (`fec_schedule_a_contributions.donor_entity_key`) | C-10 `/federal-finance/donors/<donor_entity_key>`, C-09 donor intelligence | Schedule A names | assigned at upsert by `sync-fec-il-federal` (`database/federal_fec.py:1242`); `rebuild-fec-donor-identities` back-fills blank keys | yes (sync); the rebuild is in `scripts/sync-fec.sh` and the guide only | stale-until-rebuild | Schedule A rows updated 2026-09-30 19:32 (Q5a) | OK |
| RF-015 | FEC base tables: Schedule A `fec_schedule_a_contributions`, `fec_candidate_cycle_totals`, `fec_candidate_match`, `fec_candidate_committees` | C-09, C-10, C-14 `/live-feed` and `/person-intelligence`, S-01 federal cards, S-03 federal section, Data Status strip | OpenFEC API | `sync-fec-il-federal --refresh-cache` | yes | stale-until-rebuild | newest Schedule A receipt 2026-07-01, last sync 2026-09-30 19:32 (Q5a, Q5d, Q5f) | OK (Schedule B / E: RF-047) |
| RF-016 | Cross-matching tables `lobbying_donor_matches`, `lobbying_expenditure_matches`, `lobbying_527_matches`, `irs527_committee_matches`, `irs527_expenditure_recipient_matches`, `irs527_director_donor_matches`, `irs527_director_candidate_matches`, `irs527_director_address_matches`, `irs527_org_address_matches` | C-11 entity / client pages, C-12 org pages, C-08 relationships and networks graphs, C-14 `/investigate` and `/person-intelligence`, S-01 "Director-Donor Overlaps" | lobbying, 527 and ISBE donor tables | `run-cross-matching --only all` | yes | stale-until-rebuild | 30 to 260,387 rows per table, but `created_at` is NULL on every row (Q6a–Q6i, Q8); the only timestamp is one fingerprint row, 2026-02-15 (Q9). As-of not measurable | REVIEW: nothing records when a match table was built, so its staleness is invisible to people and to `validate-freshness`. Decision: have each match job stamp `created_at` (or a run table) and gate on it. Owner terminal 1 (`database/cross_matching.py`) |
| RF-017 | Lobbying tables `lobbying_entities`, `lobbying_clients`, `lobbying_entity_clients`, `lobbying_lobbyists`, `lobbying_lobbyist_registrations` | C-11 `/lobbying/`, `/lobbying/<int:entity_id>`, `/lobbying/client/<int:client_id>`, `/lobbying/flows`; S-01 "Lobbying Entities" | IL SOS daily extract CSV | `import-lobbying --file …` | manual ("as available", weekly suggested) | stale-until-rebuild | loaded 2026-02-15, registration years up to 2026 (Q7a, Q7b) | REVIEW: no schedule, no download step, no freshness gate; the matches (RF-016) must re-run after it. Decision: add to the scheduled chain (item 4) or label the pages with the extract date |
| RF-018 | IRS 527 tables `irs527_organizations`, `irs527_reports`, `irs527_contributions`, `irs527_expenditures`, `irs527_directors`, rollups `irs527_contribution_rollup`, `irs527_contributor_rollup` | C-12 `/527/`, `/527/<path:ein>` and its contributors / recipients pages, `/527/dark-money`; S-01 527 cards | IRS FullDataFile | `import-irs527` (rebuilds the rollups, `database/irs527_loader.py:813`) | manual (monthly) | stale-until-rebuild | loaded 2026-02-13…16; rollups 2026-02-16 (Q7c–Q7f); text dates include non-dates (MAX of the text `date` column is `99999`) | REVIEW: no schedule or gate. The org "Period" text-date bug (MT-017) is fixed by deploy 4529328 (`_iso_text_date_sql()`, `webapp/routes/irs527.py:247`) |
| RF-019 | OpenBook tables `openbook_vendor_seed`, `openbook_vendor_match`, `openbook_vendor_label`, `openbook_contracts_raw`, `openbook_contract_warrants`, `openbook_contributions_raw` | C-13 `/openbook/`, `/openbook/<path:vendor_key>`; S-01 "OpenBook Vendors Matched" | IL Comptroller scrape | `import-openbook-batch`, `import-openbook-vendor` | manual | stale-until-rebuild | last scrape run 2026-02-16 (Q7i, Q7j) | REVIEW: no schedule or gate |
| RF-020 | Chicago tables `chicago_contracts_raw`, `chicago_payments_raw`, `chicago_lobbyist_contributions_raw`, `chicago_lobbying_activity_raw` | nothing: no route or analytics builder reads them (grep of `webapp/` and `database/analytics.py`) | Socrata | `import-chicago-phase1` | manual | stale-until-rebuild (not shown) | 0 rows locally (Q8) | OK (nothing derived on the site) |
| RF-021 | Committee families `committee_family`, `committee_family_member` | C-02 candidate profile grouping, C-03 entity tabs, C-04 committee banner, S-02 `/committees/` with ?by=family (accepted rows only) | ISBE committee names and links | `scripts/committee_families.py seed` (a script, not a CLI command) plus human review at `/admin/committee-families` | manual | never-rebuilt (new re-registrations after the seed are never proposed) | 753 pending, none accepted; seeded 2026-10-26 21:48 (Q7k, Q7l) | REVIEW: decide whether the seed runs after each `sunshine-import` (idempotent pending rows) or stays a periodic manual task |
| RF-022 | Privacy redactions applied to receipts and donor tables | C-01 and every donor surface | `privacy_redactions` | `sunshine-import` (ETL `apply_privacy_redactions_post_load()`, `scripts/isbe_sunshine_etl.py:1855`), `apply-privacy-redactions`; `refresh-analytics` rebuilds the analytics donor tables from the scrubbed receipts | yes | stale-until-rebuild (after a prod-DB copy it needs `apply-privacy-redactions` by hand, CLAUDE.md) | 0 requests locally (Q7m) | OK |
| RF-023 | Legacy scraper tables `committees`, `reports`, `contributions`, `donors`, `scrape_status` | C-15 `/legacy`, `/reports/`, `/committees/legacy/<int:committee_id>`, `/api/stats` and the other v1 API rules | Playwright scrapers | `scrape-*` commands, `seed-committee-urls` (not run) | no (frozen by design, `docs/page_inventory.md` C-15) | never-rebuilt | 344 committees, 650 reports, 766 contributions, no transaction dates (Q7n, Q8) | REVIEW: frozen pages with live-sounding labels (see RF-044). Decision: keep and label "archived scrape", or retire |

## B. Route caches and revisions

| Id | Piece | Where shown | Derived from | Refreshed by | In the weekly chain? | Decay mode | Observed locally (as-of) | Flag |
|---|---|---|---|---|---|---|---|---|
| RF-024 | Analytics route caches "analytics_overview", "analytics_risk", "analytics_networks", "analytics_relationships", "analytics_geography", "analytics_donors", "analytics_geo_drilldown", "analytics_state_races", "analytics_state_race_detail", "analytics_window_free_graphs" (revision `_STATE_RACE_CACHE_REV` = "os3") | C-07, C-08 (C-16 is not route-cached) | RF-003…RF-011, RF-016 | flushed by `flush_all_route_caches()` at the end of `refresh-analytics` (after the F6 rebuild), of `sunshine-import` (`scripts/isbe_sunshine_etl.py:1992`, `scripts/swap_bulk_to_isbe.py:287`) and of `apply-privacy-redactions` (`cli/commands.py:1078`); refilled by `scripts/warm_caches.py` (chain step 7B) and each worker's startup prewarm | yes | TTL 21600 (`ANALYTICS_*_CACHE_TTL_SECONDS`, `config.py:96`–`102`); the flush makes a refresh visible at once | not inspected (no Redis read) | OK |
| RF-025 | Homepage caches "dashboard_insights", "candidate_stats", "top_donors" | S-01, S-03 | RF-003, RF-004, RF-007, RF-015 | same flush; prewarm `warm_dashboard_home_cache()` | yes | TTL 21600 (`DASHBOARD_*_CACHE_TTL_SECONDS`, `config.py:93`–`95`) | not inspected | OK |
| RF-026 | Search cache "search_results", revision `SEARCH_CACHE_REVISION` = "sn2" (`webapp/routes/main.py:211`) | C-14 `/search` | RF-004, RF-007, ISBE and FEC tables | same flush | yes | TTL 21600 (`SEARCH_RESULTS_CACHE_TTL_SECONDS`) | not inspected | OK |
| RF-027 | Entity tab caches "entity_network", "entity_risk", "entity_donors", "entity_geography", "entity_relationships", "entity_risk_threshold"; revision `ENTITY_CACHE_REV` = "ea2" (`database/entity_analytics.py:35`) | C-03 | RF-002, RF-005, RF-021 | same flush | yes | TTL 21600 (`ENTITY_ANALYTICS_CACHE_TTL_SECONDS`) | not inspected | OK |
| RF-028 | Cache revisions (`ENTITY_CACHE_REV`, `SEARCH_CACHE_REVISION`, `_STATE_RACE_CACHE_REV`) | C-03, C-07, C-14 | code | bumped by a code change when a payload shape changes; data refreshes never need a bump | n/a (code) | none for data | values above | OK |
| RF-029 | Short-TTL caches: "federal_geo_drilldown" (Redis), "irs527_dark_money_stats" (Redis), the `/candidate-finance/` list (per-process dict, `webapp/routes/candidate_finance.py:18`), the Viz Lab prototype cache (per-process) | C-09 geo drill-down, C-12 `/527/dark-money`, C-06 `/candidate-finance/`, C-17 | RF-004, RF-015, RF-018 | expiry; the two Redis ones are also flushed; the per-process ones are not | TTL | TTL 180 (`FEDERAL_GEO_DRILLDOWN_CACHE_TTL_SECONDS`, `IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS`, `cache_ttl = 180`, `EXPERIMENTAL_VIZ_CACHE_TTL_SECONDS`) | not inspected | OK |
| RF-030 | Federal view snapshots in `analytics_snapshots` (snapshot types "federal_overview", "federal_networks", "federal_donor_intelligence", "federal_matching") | C-09 `/federal-finance/overview`, networks, donor intelligence, matching | RF-013, RF-015 | written on a request miss; read while younger than the TTL. `flush_all_route_caches()` does not touch them (Redis only) | TTL | TTL 900 / 600 (`FEDERAL_*_CACHE_TTL_SECONDS`, `config.py:68`–`71`): up to 15 min of pre-sync figures after a sync | rows written up to 2026-10-29 01:17 (Q3b) | OK |
| RF-031 | In-process fallback LRU (each worker, when `REDIS_URL` is unset or Redis is down) | every RouteCache page | as RF-024…RF-027 | not flushed by `flush_all_route_caches()` (`webapp/cache_backend.py:391`: "Only the Redis layer is flushed"); cleared by a service restart. `scripts/refresh_prod_chain.sh` restarts (step 8); `scripts/sync-fec.sh` does not | yes on the main chain; no after the Sunday FEC timer | TTL 21600 if Redis is down at refresh time | not applicable locally | OK (prod runs Redis; risk only if Redis is down during a refresh) |
| RF-032 | Global Data Status snapshot (per-process dict, `get_global_data_status()`, `webapp/app.py`) | the Data Status strip on every page (`webapp/templates/base.html`), the time-scope bar "latest … data", S-01 "Data freshness" | RF-002, RF-015 maxima | expiry only; not flushed | TTL | TTL 900 (`GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS`) | not inspected | OK |
| RF-033 | Cloudflare edge cache for anonymous HTML (`Cache-Control: public, max-age=0, s-maxage=<HTML_EDGE_CACHE_SECONDS>`, `webapp/app.py:454`) | every public page | origin HTML | expiry; no purge step after a refresh | external (Devin: Cloudflare Cache Rule) | TTL 300 (`HTML_EDGE_CACHE_SECONDS`) | n/a (prod only) | OK |

## C. Static or derived files

| Id | Piece | Where shown | Derived from | Refreshed by | In the weekly chain? | Decay mode | Observed locally (as-of) | Flag |
|---|---|---|---|---|---|---|---|---|
| RF-034 | District geometry `data/geometry/il/*.topo.json` | C-17 `/experimental/viz-lab/state-maps`, `/experimental/viz-lab/data/geometry/il/<layer_key>` (local only) | Census TIGER/Line 2025 shapefiles | `scripts/build_geometry.py`, checked by `scripts/validate_geometry_keys.py` | manual (once per TIGER vintage) | external (annual vintage) | this worktree holds only `data/geometry/il/README.md`; the TopoJSON files are not present | OK (C-17 is local-only and not deployed) |
| RF-035 | Triple-pipeline JSON artifacts (directory artifacts/triple_pipeline/, not in the repo) | C-17 `/experimental/viz-lab/data/triple-pipeline/graph` and siblings | the database | `scripts/build_triple_pipeline.py` | manual | never-rebuilt by any chain | absent in this worktree; the nav link hides itself when `graph.json` is missing | OK |
| RF-036 | Row-count baseline `Bulk_download/.row_counts` | not shown; the truncation guard of the ISBE download | previous download | `sunshine-import --download` (`verify_row_counts()`, `scripts/isbe_sunshine_etl.py:675`); `scripts/refresh_prod_chain.sh` writes its own `Bulk_download/.row_counts_prev` and runs `scripts/check_row_counts.py` | yes | stale-until-rebuild | not measured (the file lives in the main checkout, outside this worktree) | OK |
| RF-037 | FEC payload cache (`raw_extractions`, read by `_request_with_cache()`, `database/federal_fec.py:643`) | not shown; feeds RF-013…RF-015 | OpenFEC responses | bypassed only with `--refresh-cache` on `sync-fec-il-federal` and the backfills | yes (every written chain passes the flag; see RF-050 for the guide) | never-rebuilt without the flag (the 2026 silent no-op) | newest FEC payload 2026-09-30 19:32 (Q7o) | REVIEW: CLAUDE.md and `docs/data_update_guide.md` call it a "legacy SQLite payload cache"; it is the `raw_extractions` table in `ilcf`. Doc wording only |
| RF-038 | CSV exports (`/candidate-finance/` list and itemized, D-2 reconciliation lists, investigate, live feed, federal race / candidate exports) | C-05, C-06, C-09, C-10, C-14 | the same queries as the page | built per request; nothing stored | n/a | as the page's source rows | n/a | OK (no CSV is read by the site; grep of `webapp/routes/` finds only response exports) |

## D. Clock-vs-data defaults

| Id | Piece | Where shown | Derived from | Refreshed by | In the weekly chain? | Decay mode | Observed locally (as-of) | Flag |
|---|---|---|---|---|---|---|---|---|
| RF-039 | Default window "current cycle" (`current_cycle()` = `date.today().year` rounded up to even, `webapp/utils/time_scope.py:46`), and "This cycle" subtotals | every windowed list and dashboard: C-01 directory, C-06…C-09, C-11, C-12, C-14, S-01…S-04; "This cycle" on C-01, C-02, C-04 | the clock | nothing (clock) | n/a | clock-derived: on 2027-01-01 every default window becomes `cycle:2028` (2027-01-01..2028-12-31), which holds almost nothing until filings for 2027 arrive and are imported | today `cycle:2026` | REVIEW: decide whether the default cycle should follow the data (the cycle of the newest receipt) or the clock, before January 2027 |
| RF-040 | "Last 12 months" preset (today − 365 days, `webapp/utils/time_scope.py:98`) and the lobbying registration-year snap (`webapp/routes/lobbying.py:693`) | the scope bar presets on windowed pages; C-11 lists | the clock | nothing (clock) | n/a | clock-derived: the newest weeks of the window are empty while the data lags (local ISBE ends 2026-09-30) | n/a | OK (the time-scope bar shows "latest … data: <date>", RF-042) |
| RF-041 | "This month" on `/investigate` (tab=patterns): `month_key = month if month else (_latest_month_with_large_contributions(conn) if _table_exists(conn, "analytics_large_contributions") else datetime.utcnow().strftime("%Y-%m"))` (`webapp/routes/main.py:2768`); `_latest_month_with_large_contributions()` = `SELECT SUBSTR(MAX(event_date), 1, 7) FROM analytics_large_contributions` (`webapp/routes/main.py:2517`) | C-14 `/investigate` (home button "Find unusual patterns this month", `webapp/templates/index.html:20`; heading "Unusual Patterns: <month>", `webapp/templates/investigate.html:187`) | RF-009 | `refresh-analytics` | yes | data-derived-label; stale-until-rebuild (moves only when `refresh-analytics` runs, and only as far as the filter admits rows) | local 2026-06 (Q2a) vs receipts to 2026-09-30 (Q1a): the pre-F1 filter dropped A-1 rows (Q10: 2026-09 without the D-2 EXISTS). Prod GET 2026-10-29 06:53Z: "Unusual Patterns: 2026-10" (prod refreshed after F1, 06:27–06:32:53Z per Fable) | REVIEW: "this month" means "the latest month that has flagged rows", not the calendar month; the clock fallback runs only when the table is missing. Prod's 2026-10 is a partial month (data through 2026-10-27, A-1s only) shown as if complete. Decision: relabel ("latest month in the data, through <date>") or keep. Owner terminal 1 (templates / route). Worked example in the review document |
| RF-042 | Data Status strip (`webapp/templates/base.html`) and home "Data freshness" block (`webapp/templates/index.html:215`–219), time-scope bar "latest … data" (`webapp/templates/_partials/time_scope_bar.html`) | every page; S-01 | RF-032: `MAX(received_date)` of non-archived `isbe_condensed_receipts`; FEC Schedule A / B / E maxima, `fec_candidate_cycle_totals`, FEC payload sync time (`_build_global_data_status()`, `webapp/app.py:174`); stale after `LOCAL_DATA_STALE_DAYS` 45 / `FEDERAL_DATA_STALE_DAYS` 14 | live MAX through RF-032 | yes (follows RF-002, RF-015) | data-derived-label, TTL 900 | local receipts 2026-09-30 (Q1c). Prod GET 2026-10-29 06:53Z: "Local receipts: 2026-10-27 (2 days old)", "Federal last sync: 2026-10-27 16:20", "Federal latest report coverage: 2026-10-15", "Federal latest receipt date (row-level): 2026-09-30 (29 days old)" | OK (the label names the date it measures; the age grows if imports stop) |

## E. Labels that claim currency

| Id | Piece | Where shown | Derived from | Refreshed by | In the weekly chain? | Decay mode | Observed locally (as-of) | Flag |
|---|---|---|---|---|---|---|---|---|
| RF-043 | "Local feed latest date", "Federal feed latest date", "Federal Schedule B / E latest date", "Federal latest report coverage" (`webapp/templates/live_feed.html:13`–17); "Latest receipt date" and Schedule A / B / E dates on `/candidates` (`webapp/templates/candidates.html:39`–83); "Latest FEC Report Coverage" (`webapp/templates/federal_finance/detail.html:64`) | C-14 `/live-feed`, S-03, C-10 | live MAX over RF-002 / RF-015 in the window | live | yes | data-derived-label | — | OK (each names its source and date) |
| RF-044 | "Recent Contributions" heading on the legacy committee page (`webapp/templates/committees/detail.html:84`) | C-15 `/committees/legacy/<int:committee_id>` | RF-023 (frozen) | nothing | no | never-rebuilt | no dated rows (Q7n) | REVIEW: "Recent" on a frozen scrape. Decision folded into RF-023 |
| RF-045 | "Last computed" / "Age" / "TTL" of the analytics snapshot (`webapp/templates/analytics/_status.html`) | C-08 `/analytics/` | RF-011 | `refresh-analytics --with-snapshot` | yes | stale-until-rebuild | snapshot 2026-09-30 19:37 (Q3b) | OK (honest: shows the computed time) |
| RF-046 | Static pages: "About" freshness text (`webapp/templates/about.html:40`, describes the Data Status strip) and "Privacy" | S-05 `/about`, S-06 `/privacy` | nothing derived | — | n/a | none | — | OK (nothing derived) |

## F. External refresh gaps

| Id | Piece | Where shown | Derived from | Refreshed by | In the weekly chain? | Decay mode | Observed locally (as-of) | Flag |
|---|---|---|---|---|---|---|---|---|
| RF-047 | FEC Schedule B `fec_schedule_b_disbursements` and Schedule E `fec_schedule_e_independent_expenditures` | C-09 money flow / overview, C-10 race outside spending and candidate exports, C-14 `/live-feed`, S-01 Schedule B / E cards | OpenFEC | only `backfill-fec-schedule-b` / `backfill-fec-schedule-e` (the main sync does not refresh them, CLAUDE.md); the repo ships a unit only for E (`docs/systemd/il-campaign-fec-schedule-e-catchup.service` / `docs/systemd/il-campaign-fec-schedule-e-catchup.timer`, hourly); B and the Schedule A catch-up have scripts (`scripts/fec-schedule-b-catchup.sh`, `scripts/fec-schedule-a-catchup.sh`) and guide text but no unit files | E: yes, outside the chain (the hourly Schedule E timer is installed on prod per `docs/runbooks/deployment.md` "Scheduled jobs on prod", inventory 2026-10-27); B: no (no timer, no chain step) | stale-until-rebuild | newest Schedule B 2025-12-31 (updated 2026-02-13), Schedule E 2026-02-10 (updated 2026-02-13) (Q5b, Q5c) | FIX-PROPOSED: add `backfill-fec-schedule-b --refresh-cache` to the scheduled chain (or ship and install a Schedule B timer like the E one), and keep E on its timer; the local B / E dates above only show that the local copy never ran them; footprint chain script + `docs/systemd/`; owner the other session (script), Devin (install), item 4 |
| RF-048 | `validate-freshness` coverage (`DEFAULT_SPECS`, `database/data_freshness.py:50`) | none; the post-refresh gate | ISBE `isbe_filed_docs.received_datetime` (14 d), FEC Schedule A / B / E (120 / 180 / 240 d) | `validate-freshness` | CLAUDE.md lists it after the ISBE and FEC steps; `scripts/refresh_prod_chain.sh` and `scripts/sync-fec.sh` never run it | n/a | — | FIX-PROPOSED: run it in the chain script after step 5; extend the specs to the analytics build time (`analytics_materialized_meta.refreshed_at` not older than the newest import), `donor_entity_local.updated_at`, lobbying / 527 / OpenBook load dates (warn-only), and match tables once they carry a timestamp (RF-016). Footprint `database/data_freshness.py` (terminal 1), chain script (other session) |
| RF-049 | Compat swap with the service running: `scripts/refresh_prod_chain.sh` step 4 runs `sunshine-import`, which runs `scripts/swap_bulk_to_isbe.py`, while ilcf-web.service is up; CLAUDE.md says the swap must run with the service stopped (deadlock / timeout on 2026-10-27) | all ISBE pages (RF-003…RF-005) | — | — | yes, in the unsafe order | a failed swap leaves RF-004 / RF-005 stale; `sunshine-import` only warns (`cli/commands.py`, "compat swap failed") | — | FIX-PROPOSED: in the chain script, stop the service around `sunshine-import` (or run the ETL with `--skip-compat-swap`, then stop, swap, start); fail the chain when the swap fails. Owner the other session; item 4 |
| RF-050 | `docs/data_update_guide.md` disagrees with the code: ISBE "Option 1" runs `import-bulk-download` (the legacy loader) rather than `sunshine-import`; "Option 2" scrapers write the frozen legacy tables (RF-023); the "Manual FEC Sync" block omits `--refresh-cache`; the guide's weekly-sync and Schedule A catch-up units do not exist in `docs/systemd/` | none (operator doc) | — | — | n/a | a person following the guide refreshes nothing (FEC) or the wrong tables (ISBE) | — | FIX-PROPOSED: rewrite the ISBE section around `sunshine-import --download` and the manual FEC block with `--refresh-cache`; point to the proposed chain. Doc edit; owner whoever owns doc sync (not edited here, plan decision E2) |
| RF-051 | Scheduling: nothing in the repo schedules the ISBE import, cross-matching, `refresh-analytics` or the manual sources; the only shipped unit is the Schedule E catch-up; `scripts/sync-fec.sh` (described as the Sunday 03:00 timer) runs `refresh-analytics` without `run-cross-matching` or a restart | all derived rows | — | — | n/a | stale-until-rebuild for everything until a person runs the chain | — | REVIEW: input to item 4. Decision: one scheduled chain (the proposed chain in the review document) versus today's mix of a manual chain and a FEC timer |

## Page classes covered

| Class | Rows |
|---|---|
| C-01 Donors | RF-001, RF-003, RF-007, RF-008, RF-012, RF-022, RF-039 |
| C-02 Candidate profiles | RF-001, RF-002, RF-021, RF-039 |
| C-03 Entity analytics tabs | RF-002, RF-005, RF-021, RF-027, RF-028 |
| C-04 Committee profiles and payees | RF-002, RF-003, RF-007, RF-021, RF-039 |
| C-05 Filings and D-2 reconciliation | RF-001, RF-002, RF-006, RF-038 |
| C-06 Candidate-committee finance | RF-004, RF-029, RF-038, RF-039 |
| C-07 State races | RF-004, RF-005, RF-012, RF-024 |
| C-08 Analytics dashboards | RF-006, RF-007, RF-009, RF-011, RF-016, RF-024, RF-045 |
| C-09 Federal dashboards | RF-013, RF-014, RF-015, RF-029, RF-030, RF-047 |
| C-10 Federal profiles and race outside spending | RF-013, RF-014, RF-015, RF-043, RF-047 |
| C-11 Lobbying | RF-016, RF-017, RF-040 |
| C-12 IRS 527 | RF-016, RF-018, RF-029 |
| C-13 OpenBook | RF-019 |
| C-14 Cross-source tools | RF-009, RF-016, RF-026, RF-041, RF-043 |
| C-15 Legacy pages and v1 API | RF-012, RF-023, RF-044 |
| C-16 Analytics JSON API | RF-006, RF-007, RF-009 |
| C-17 Viz Lab (local only) | RF-029, RF-034, RF-035 |
| S-01 Home | RF-004, RF-007, RF-013, RF-015, RF-025, RF-041, RF-042 |
| S-02 Committee directory | RF-002, RF-003, RF-021 |
| S-03 Candidate directory | RF-004, RF-015, RF-025, RF-043 |
| S-04 Compare | RF-003, RF-004 |
| S-05 About | RF-046 (nothing derived) |
| S-06 Privacy | RF-046 (nothing derived) |

## Reconciliation with the written chains

Four written chains exist. What each runs, in order:

| Chain | Steps |
|---|---|
| CLAUDE.md "Data Refresh Schedule" (weekly) | `sunshine-import --download` → `validate-freshness --source isbe` → `sync-fec-il-federal --refresh-cache` → `validate-freshness --source fec` → `run-cross-matching --only all` → `refresh-analytics --with-snapshot` → restart ilcf-web.service. Monthly: `backfill-fec-schedule-a/b/e`, `import-irs527`, `import-chicago-phase1`, `import-openbook-batch`; `import-lobbying` as available |
| `scripts/refresh_prod_chain.sh` (what is actually run on prod by hand) | row-count baseline → curl download of the 12 ISBE files → `scripts/check_row_counts.py` → `sunshine-import --bulk-dir Bulk_download` (service up) → `sync-fec-il-federal --refresh-cache` → `run-cross-matching --only all` → `refresh-analytics --with-snapshot` → `scripts/warm_caches.py` → restart + smoke GET |
| `scripts/sync-fec.sh` (the Sunday FEC timer in the guide) | `sync-fec-il-federal --cycle 2026 --contributor-state IL --max-calls 900 --refresh-cache` → `rebuild-fec-donor-identities` → `refresh-analytics --with-snapshot` → `scripts/warm_caches.py` (no restart, no cross-matching) |
| `docs/data_update_guide.md` | FEC: the `scripts/sync-fec.sh` steps (manual block without `--refresh-cache`), hourly Schedule A catch-up, Schedule B / E catch-ups; ISBE: Option 1 `import-bulk-download` + `refresh-analytics`, Option 2 scrapers; verification by eye |

Which rows each chain covers or misses:

- **Covered by every chain that runs `sunshine-import` + `refresh-analytics`:** RF-001…RF-005, RF-007…RF-012 (RF-012 only since F6), RF-022, RF-024…RF-027.
- **Covered by the FEC steps:** RF-013…RF-015, RF-030 (TTL). `scripts/sync-fec.sh` also runs `refresh-analytics`, which since F6 rebuilds RF-012 and flushes the caches, but it skips `run-cross-matching`, so RF-016 is not refreshed by the timer.
- **Missed by all four:** RF-006 (only the legacy `import-bulk-download`, which the guide still recommends for a different reason), RF-047 (Schedule B / E: monthly backfills in CLAUDE.md, a unit only for E), RF-021 (family seed), RF-016's timestamps (built, but undatable).
- **Manual by design, no gate:** RF-017, RF-018, RF-019, RF-020, RF-034, RF-035.
- **Gate:** only CLAUDE.md runs `validate-freshness`; the script that is actually used does not (RF-048), and the gate does not cover analytics, entities, lobbying, 527, OpenBook or matches.
- **Order hazards:** `scripts/refresh_prod_chain.sh` runs the swap with the service up (RF-049); CLAUDE.md omits `scripts/warm_caches.py` (pages build cold on first request after the flush, a speed issue, not staleness).
- **Docs only:** RF-037 (cache described as SQLite), RF-050 (guide out of date).

## Local measurements (2026-10-29 06:50–06:55Z, read-only)

Run with `PGOPTIONS='-c default_transaction_read_only=on' psql ilcf -X`. Results are in the rows above; structural
values only.

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
- **Q3b** `SELECT snapshot_type, status, COUNT(*), MAX(completed_at) FROM analytics_snapshots GROUP BY 1, 2;` → dashboard_full 2026-09-30 19:37; federal_* 2026-10-29 01:17
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
- **Q6a–Q6i** `SELECT MAX(created_at) FROM <match table>;` for the nine RF-016 tables → NULL for all nine
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
- **Q10** the RF-009 decisive query:

  ```sql
  SELECT SUBSTR(MAX(r.received_date)::text, 1, 7) AS without_d2_exists,
         SUBSTR(MAX(r.received_date) FILTER (WHERE EXISTS (SELECT 1 FROM isbe_d2_reports d2
                WHERE d2.filed_doc_id = r.filed_doc_id AND d2.archived = FALSE))::text, 1, 7) AS with_d2_exists
  FROM isbe_condensed_receipts r
  WHERE r.archived = FALSE AND r.d2_part LIKE '1%' AND r.amount >= 5000;
  ```

  → without 2026-09, with 2026-06.

**Prod GET:** 2026-10-29 06:53:03Z, `https://www.followthemoneyil.com/investigate?tab=patterns&period=cycle:2026`
→ HTTP 200, heading "Unusual Patterns: 2026-10", month list starting 2026-10, 2026-09, 2026-08; Data Status strip
as quoted in RF-042.
