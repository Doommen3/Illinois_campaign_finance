# Route Performance Caching — Reference

Detailed reference for the `RouteCache` layer used by heavy routes (`webapp/cache_backend.py`): Redis when `REDIS_URL` is set, else a 32-entry per-instance in-process LRU. The top-level invariants live in `CLAUDE.md`; this file captures the architecture details, dated bug recaps, and contract-level requirements that don't need to be in every conversation context.

## Cached routes

`RouteCache` TTL caches are applied to:

- `/` — homepage candidate stats, top donors, dashboard insights
- `/analytics/` — overview (all 7 aggregates + state race analytics)
- `/analytics/state-races/` and `/analytics/state-races/<race_key>` — the full race list (the same cached rows the overview slices its Top 12 from) and one race's detail
- `/analytics/networks`
- `/analytics/risk` — anomalies + reconciliation
- `/analytics/relationships`
- `/analytics/geo-drilldown`
- `/federal-finance/geo-drilldown`
- `/candidate-finance/` — paginated, keyed on all filter params
- `/527/dark-money` — summary stats
- `/experimental/viz-lab/data/*` — prototype gallery endpoints

All caches respect `ROUTE_PERF_CACHE_ENABLED` and `TESTING` flags; the startup prewarm is the exception — it is gated only by `DASHBOARD_PREWARM_ENABLED` (and skipped under `TESTING`) and never reads `ROUTE_PERF_CACHE_ENABLED`. Append `?refresh_cache=1` to force a miss. TTL constants live in `config.py` under names matching `*_CACHE_TTL_SECONDS` — `grep _CACHE_TTL_SECONDS config.py` for the current list.

## Performance baselines

| Route | TTL | Cold | Warm (median) | After time-scope overhaul (local M3 Pro, 2026-10-22; cold / warm median of 2) |
|---|---|---|---|---|
| `/analytics/networks` | 21600s | ~104s | ~420ms | 92.5s / 116ms |
| `/analytics/` | 21600s | ~27s | ~14ms | 32.0s / 49ms |
| `/analytics/risk` | 21600s | ~18s | ~400ms | 10.9s / 30ms |
| `/analytics/relationships` | 21600s | ~110s | ~400ms | 107.3s / 24ms |
| `/candidate-finance/` | 180s | — | — | — |

Prod re-measure after deploy of `23c6cc7` (2026-10-26, through Cloudflare): forced cold misses (`?refresh_cache=1`) took networks 125 s → Cloudflare 524, overview 53 s, risk 20 s, relationships 125 s → 524; warm passes once every worker had prewarmed: networks 0.75–1.1 s, overview 0.22–0.75 s, risk 0.27–0.61 s, relationships 0.43–1.1 s. The cold path for networks/relationships exceeds Cloudflare's 100 s origin timeout, so only prewarmed keys (the default cycle) are safe to serve cold; `?period=all` on the analytics pages is an unwarmed miss (the sweep's 30 s probe timed out on overview, risk and donors).

The first Cold/Warm columns are the pre-overhaul prod measurements; the last column was taken on the local prod-sized `ilcf` after cache keys moved to the resolved scope (`scope_cache_key`), so the default window is the whole current cycle (`2025-01-01..2026-12-31`) rather than cycle-start..today. Cold cost is the same order of magnitude; warm hits are unchanged in kind (in-process cache). Not directly comparable machine-to-machine — re-measure on prod after deploy.

`bulk_candidate_committee_finance_agg` is a **view**, not a table — it joins/aggregates 6.4M receipt rows on each access.

### 2026-10-27 release baselines (local, before → after)

Local M3 Pro, prod-sized `ilcf` (ISBE data through 2026-09-30), figures as recorded per step in `.claude/plans/profiles-races-dated-widgets.STATE.md`; "before" is the state at the start of that step. Not directly comparable to the prod rows above.

| Page / query | Before | After | What changed (plan step) |
|---|---|---|---|
| `/donors/?cycle=2026`, warm | 5.62 s | 0.05 s | `analytics_donor_cycle_summary` serves exact-cycle windows (B4, S-S2) |
| `/analytics/relationships`, cold, default window | 212.7 s | 17.4 s | window-free graphs built once, cheaper lobbying graph (B3, S-S2) |
| `/analytics/relationships`, cold, `cycle:2024` | 248 s | 6.9 s | same |
| `/analytics/relationships`, cold, all-time | 10.1 s | 2.0 s | same |
| lobbying graph, windowed | 161.6 s | 4.55 s | IN-list of the kept match keys instead of a join on the computed `donor_key` (S-S2) |
| 527 graph, windowed | 14.7 s | 4.34 s | S-S2 |
| `/analytics/networks`, cold | not recorded | 8.5 s | S-S2 |
| `/analytics/` overview, cold | 21.9 s | 9.3 s | race builder fed by `isbe_race_candidacies`, archived receipts excluded (S-RA) |
| `/analytics/` overview, all-time window | 40 s | 8.3 s | S-RA |
| Governor race detail | 22 s | 3.1 s | S-RA |
| `/analytics/state-races/` | new page | cold 7.9 s; warm 20–46 ms (1,465 races, 1,016 active, 725 KB) | S-RB |
| `/committees/1126`, all-time window | 415 ms | 262 ms after S-PA; 392 ms after S-PB's two see-all count queries; ~307 ms after `COUNT(*) OVER ()` (S-C2) | rollup helpers, `COLLATE "C"` grouping (S-PA, S-PB, S-C2) |
| `/candidates/<id>`, warm | new page | 10–34 ms (7 queries per page) | S-C1 |
| committee Top Payees (expenditures lookup) | 660–1,117 ms | 0.6–21 ms | `(committee_id, expended_date)` index on `isbe_condensed_expenditures` (B1, step 0.1) |
| committee `analytics_donor_committee_agg` lookup | 1.6–1.9 s (seq scan) | 5–55 ms (index scan, 8.8 MB) | `(source, committee_id)` index (B2, S-S1a) |
| top-12 race-detail prewarm | 16.4 s | 5.0 s | one full build instead of per-race builds (S-S2); a warm-cache re-run on 2026-10-27 gave 10.5 s → 1.8 s with identical payloads |

Prod after the 2026-10-27 deploy: not yet re-measured; add the numbers here after the post-deploy sweep (`docs/runbooks/deployment.md`, Release 2026-10-27).

## Route cache + startup prewarm

`warm_analytics_caches()` runs in a background thread per gunicorn worker on startup (gated by `DASHBOARD_PREWARM_ENABLED`). It pre-populates the risk, networks, relationships, and overview caches, plus the twelve state-race details the overview links to, with default filter values (the current election cycle resolved by `resolve_time_scope(default="cycle:current", args={})`, default limits; every prewarmed page, risk included, keys on the `received` (receipt-date) axis).

- **Why prewarm**: where `REDIS_URL` is set (production), `RouteCache` entries live in Redis and are shared by every worker. Without Redis (dev, CI) each of the 3 gunicorn `--workers` processes falls back to its own per-instance 32-entry LRU, so without prewarm only ~33% of requests hit a warm worker. Prewarm populates the default-scope keys at startup either way.
- **Cache key alignment**: prewarm keys must exactly match `_parse_filters()` defaults — otherwise prewarm results won't be hit. Both sides derive the key from the resolved scope (`scope_cache_key(scope)` = `axis:start:end`), so a cycle preset's key is stable across days; only `last12m` and custom windows roll.
- **TTL choice**: the analytics caches default to 21600s (6 h; `ANALYTICS_*_CACHE_TTL_SECONDS` in `config.py`), which ensures they survive between typical user sessions. Data only changes on import, so long TTL is safe.
- **Cost**: ~2-3 min per worker for prewarm to complete.
- **Window-free graphs**: builders that take no dates (relationships: donor co-giving, committee similarity, candidate competition; networks: state–federal overlap) are cached once under `(builder name, arguments)` in the `analytics_window_free_graphs` cache and serve every window, so `?period=all` on relationships only builds the lobbying and 527 graphs. Prewarm fills them; a failed build is not cached (B3, J-S15).
- **Invalidation**: `refresh-analytics` (after its commit) and `sunshine-import` (after the matviews and compat views) call `flush_all_route_caches()`, which deletes the Redis `ilcf:cache:*` keys. The per-instance LRUs live in each gunicorn worker and clear only on a service restart, so restart after a refresh (the weekly chain does).

## Viz Lab network advanced metrics contract

Endpoint: `/experimental/viz-lab/data/network_slice`.

- Advanced metrics run **only** when explicitly requested (`compute_advanced=1`). Default response stays degree-only.
- Params: `mode=fast|safe`, `k` (8..128), `compute_communities`, `weight_mode=weighted|unweighted`, `edge_threshold`, `edge_limit`, `node_cap`.
- Safety caps (applied before compute):
  - `fast`: `max_nodes=160`, `max_edges=700`, default `k=32`
  - `safe`: `max_nodes=260`, `max_edges=1200`, default `k=64`
- Oversized requests **must** return a refusal payload with suggested tighter settings — no heavy compute attempt.
- `graph_meta` must include: `compute_ms`, `k`, caps (max/applied/actual), `cache_hit`, `edge_type_set`, `mode`, `weight_mode`.
- Cache key must include all advanced params + date/filter identity to prevent metric cross-talk between requests.

## Global time-filter canonical semantics

- Report-driven pages/queries filter by `filed_date`.
- Contribution-driven pages/queries filter by `transaction_date` (or `received_date` where bulk receipts don't expose transaction timestamps).
- Every page resolves its own time scope from the URL (`period=`, `cycle=`, `year=`, or `date_from`/`date_to`) via `webapp/utils/time_scope.py`; list and search pages default to the current cycle, entity profiles to all time with a current-cycle subtotal. The scope bar partial shows the resolved window; links inside a section carry it, top-nav links do not (there is no global period any more).
- Geo drilldown cache keys include range + geo coords (`period/range`, `date_from/date_to`, `geo_type`, `geo_value`, optional `geo_state`) plus pagination/sort params.

## Search route guardrails

- In `type=all`, `filed_docs` runs only for doc-id-like queries.
- In `type=all`, `donor_keys` runs only for donor-key-like queries.
- Cached in-process by `period + query + type`.
- Source-aware helpers:
  - Committees: prefers `isbe_committees` (LIKE name match), falls back to legacy `committees`.
  - Donors: prefers `analytics_donor_summary` (LIKE), falls back to legacy `donors`.
  - Template links use `donor_detail_by_key` for materialized donors, `donor_detail` for legacy.

## Required indexes for cached routes

Created automatically by `isbe_sunshine_etl.py` matview/schema DDL and `database/schema.sql`:

- `isbe_condensed_receipts`: `filed_doc_id`, `committee_id`, `received_date`, `donor_name_trgm`, `donor_key_trgm`, `active_part1` (partial: `archived = FALSE AND d2_part LIKE '1%' AND amount > 0`)
- `isbe_d2_reports`: `active_filed_doc` (partial: `(filed_doc_id) WHERE archived = FALSE`)
- 527 perf: `idx_irs527_contributions_name_amount`, `idx_irs527_contributions_date_amount`, `idx_irs527_expenditures_date_amount`
- Analytics graphs: `idx_irs527_orgs_ein`, `idx_irs527_committee_matches_ein`, `idx_irs527_exp_recipient_matches_ein`, `idx_irs527_director_donor_matches_ein` (EIN lookups for 527 ecosystem graph); `idx_lobbying_donor_matches_client_id`, `idx_lobbying_exp_matches_source` (lobbying influence graph); `idx_isbe_condensed_exp_committee_id` (vendor network)
- Committee profile and see-all lists: `idx_isbe_condensed_expenditures_committee_date (committee_id, expended_date)` (matview DDL; prod needs the one-time `CREATE INDEX IF NOT EXISTS` from `deployment.md`) and `idx_analytics_donor_committee_agg_source_committee (source, committee_id)` (`database/schema.sql`); the per-cycle `/donors/` list reads `analytics_donor_cycle_summary` (`idx_analytics_donor_cycle_summary_total`)

## Snapshot build compatibility

- `get_nlp_spending_summary` falls back to `bulk_expenditures_clean` when legacy `contributions` is absent.
- `get_dashboard_snapshot` handles PostgreSQL `datetime` objects for `completed_at` in addition to string timestamps.

## Query-shape rules

- 527 contribution/expenditure queries prefer **indexable predicates** (`amount > 0`) and **dual-format date range filters** (`YYYY-MM-DD` + `YYYYMMDD`) over `DATE(column)` wrappers.
- OpenBook date filtering uses string comparison (`contribution_date >= ?`) instead of `DATE()` casts. Avoids `InvalidDatetimeFormat` errors on rows with non-date values (e.g., header rows with literal `"DATE"`).
- D2 Expenditures Reconciliation: `D2ExpendituresRecon` sort/filter expressions use safe `CAST(NULLIF(TRIM(CAST(col AS TEXT)), '') AS REAL)` for all numeric columns that may be TEXT-typed in the underlying `CREATE TABLE AS SELECT` view. Row parsing uses `_sf()`/`_si()` safe converters.

## Performance fast-paths

- Homepage donor query uses `analytics_donor_summary` (fallback: legacy donor totals query if summary table is absent).
- Homepage candidate stats consolidates aggregates into single-pass local candidate/committee counts + combined D2 and federal Schedule B/E queries.
- `/person-intelligence` candidate enrichment uses set-based batching for committees/candidacies (avoids per-candidate N+1 loops).
- Homepage legacy summary cards (`reports` / `committees` / `donors`) return zero when legacy tables are absent — they don't error the route.
- 527 contribution ingest populates `irs527_contributor_rollup` to reduce recomputation for contribution summary metrics.

## Dated bug recaps

Kept here in case the underlying patterns regress; otherwise the fixes are in code.

### Federal Custom Sankey frontend (2026-02-24)

`webapp/templates/federal_finance/networks.html`:
- Must include `static/js/autocomplete.js`. Without it, the Custom Sankey search input shows no suggestions even when `/api/federal/network/suggest` works.
- `renderFedCustomSankey()` must define/use a scoped `buildColumn()` helper before calling it. Missing helper throws a browser runtime error and UI falls back to `"Error loading data. Please try again."` even with HTTP 200 + valid JSON from `/api/federal/network/focus-sankey`.

## Benchmarking guidance

- Always measure **one cold pass + one warm pass** at minimum.
- For reliable warm numbers, run 2-3 warm passes and use median.
- In tests, route perf cache is disabled by default under `TESTING` unless explicitly enabled.

## Setup/ETL performance notes (2026-02-16)

SQL-first patterns to preserve:

- `database/analytics.py::_refresh_materialized_contributions()` — set-based `INSERT ... SELECT` for donor aggregates, monthly totals, large-contribution rows. Don't reintroduce Python row-loop aggregation.
- `database/analytics.py::_refresh_materialized_isbe()` (ISBE direct path, 2026-02-19) — queries `isbe_condensed_receipts` and `isbe_committees` directly, bypassing `bulk_*_clean` compat views. Auto-selected when ISBE tables exist + have data. Uses native column names (`last_name`, `address1`, `zipcode`, `d2_part`, `archived = FALSE`). Source tag remains `'bulk_receipts'` for downstream compat. Materialization version 3 (vs 2 for compat-view path).
- ISBE direct path avoids: compat view column renames / BOOLEAN→int conversions, correlated EXISTS through compat views, text→date casting. Uses `idx_isbe_condensed_receipts_active_part1` + `idx_isbe_d2_reports_active_filed_doc`.
- `_use_isbe_direct_path(conn)` is the detection function for refresh routing. `_donor_flow_source(conn)` still returns `"bulk_receipts"` for source-tag compat.
- `database/irs527_loader.py` — Illinois-only first pass uses lightweight field extraction (`record_type` + EIN/state indexes) instead of full parser tuple builds. Expenditure IL-state lookup uses the state slot in parsed tuples (index 7).
- `database/bulk_download_loader.py` — receipt date normalization is LRU-cached for repeated values in multi-million-row imports.
- `database/federal_fec.py` — candidate seed and committee upserts are batched via `executemany()`. Prefer this pattern for similar high-row upsert paths.

Bounded synthetic benchmarks (local):
- analytics contributions materialization: ~2.07s → ~0.79s (300k synthetic rows)
- IRS Illinois EIN first-pass extraction: ~1.34s → ~0.23s (800k synthetic lines)
- receipt date normalization loop: ~13.65s → ~0.09s (2M repeated values)

## Prod measurements — Release 2026-10-27 (`2301286`)

Measured over HTTPS from the laptop (3 passes, warm unless noted) after the restart at 14:52 UTC; "before" = the pre-release code the same morning.

| Route | Before | After |
|---|---|---|
| `/donors/?cycle=2026` | 5.7–6.9 s (live query) | 0.34–0.56 s (`analytics_donor_cycle_summary`; prod table 1,475,932 rows, cycle 2026 46,308 — built from the old matview picker, see below) |
| `/analytics/state-races/` | 404 (new) | cold 19.8 s on the server during prewarm; warm 0.42–0.72 s |
| `/analytics/state-races/governor-statewide-illinois` | — | warm 0.16–0.21 s (prewarmed detail); an unwarmed race (`alderperson-city-aurora`) 2.4 s cold |
| `/candidates/16872` | 404 (new) | 0.29–0.33 s |
| `/committees/1126` | 0.64–1.07 s | 0.93–1.52 s (reordered page; same queries plus donor-key links) |
| `/committees/1126/donors` | — | 0.62–0.74 s |
| `/committees/?by=family` | — | 0.34–0.39 s (753 pending families, none accepted yet) |
| `/analytics/` | 0.22 s (cached) | 0.25 s (cached) |
| `/` | — | 0.21–0.31 s; first hit after restart 13.3 s (prewarm threads competing) |

Server-side steps: `init-db` 7 s; expenditures index 1.9 s; `refresh-analytics --with-snapshot` 4 min 23 s (14:47:20 → 14:51:43 UTC); swap script 6 s under an 8 s service stop (see the deployment runbook, "Release 2026-10-27" step 5 note for why it must run stopped); `committee_families.py seed` 2.2 s (753 families / 1,626 members).

**Data caveat (A2, still open on prod):** prod's `isbe_most_recent_filings` picker has no `NULLS LAST` yet (the DDL fix ships in the ETL and lands at the next `sunshine-import`, or by the hand rebuild in the deployment runbook). Measured 2026-10-27: condensed non-archived receipts for the 2026 cycle = 112,469 rows / $348.1M against 230,305 / $601.1M raw non-archived (42 % of the dollars hidden); all time $5.457B against $6.702B; 60,067 A-1 filed docs have a NULL `received_datetime`. Every cycle-2026 figure on prod (donor list, committee profiles, race totals, the cycle table) is understated by about that much until the matviews are rebuilt and `refresh-analytics` runs again.
