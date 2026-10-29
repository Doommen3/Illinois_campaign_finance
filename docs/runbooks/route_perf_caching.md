# Route Performance Caching — Reference

Detailed reference for the `RouteCache` layer used by heavy routes (`webapp/cache_backend.py`): Redis when `REDIS_URL` is set, else a 32-entry per-instance in-process LRU. The top-level invariants live in `CLAUDE.md`; this file captures the architecture details, dated bug recaps, and contract-level requirements that don't need to be in every conversation context.

## Cached routes

`RouteCache` TTL caches are applied to:

- `/` — homepage candidate stats, top donors, dashboard insights
- `/analytics/` — overview (all 7 aggregates + state race analytics)
- `/analytics/state-races/` and `/analytics/state-races/<race_key>` — the full race list and one race's detail. The list is ONE cached, unsorted build per window and cycle (`_state_races_cache_key(scope, election_cycle)`, revision `_STATE_RACE_CACHE_REV` = "os3" since 2026-10-28); every sort order (`sort`/`dir`, the eight Top State Races columns in `_STATE_RACE_SORTS`) is applied after the cache read with `sort_state_race_rows`. The overview's Top State Races stores the first 12 rows of all 16 orders in its payload (`state_race_previews`, ≈68 KB, ≈8 ms per build) and picks the requested one per request, so `state_race_sort`/`state_race_dir` are header links (server sort over the whole list, `data-server-sort`) and never part of the overview key
- `/candidates/<id>/<tab>` and `/analytics/state-races/<race_key>/<tab>` (Network, Risk, Donors, Geography, Relationships) — one `RouteCache` entry per tab, keyed `ENTITY_CACHE_REV` (`database/entity_analytics.py`; "ea2" since 2026-10-29, when the network JSON gained `members`) + entity + resolved scope + the tab's limits; the Network page and its three JSON views (`ego`, `money_flow`, `vendor_network`) share one `network:ego` build. Bump the revision whenever a payload or view shape changes so no entry in the old shape is served (the entity_views scripts carry a `?v=` stamp for the same reason). Contract and the click drill-down panel: `docs/entity_networks.md`.
- `/analytics/networks`
- `/analytics/risk` — anomalies + reconciliation
- `/analytics/relationships`
- `/analytics/geography` and `/analytics/donors` — `analytics_geography` / `analytics_donors` (load-time plan L5; keyed on the window plus every limit/months argument; warmed for the default scope)
- `/analytics/geo-drilldown` — `analytics_geo_drilldown` (Redis-shared since L4; was a per-worker dict with a 180 s TTL)
- `/federal-finance/geo-drilldown` — `federal_geo_drilldown` (Redis-shared since L4; TTL still 180 s)
- `/search` — `search_results` (L6; period + query + type; `SEARCH_RESULTS_CACHE_TTL_SECONDS` = 21600, flushed after imports)
- `/candidate-finance/` — paginated, keyed on all filter params
- `/527/dark-money` — summary stats
- `/experimental/viz-lab/data/*` — prototype gallery endpoints

All caches respect `ROUTE_PERF_CACHE_ENABLED` and `TESTING` flags; the startup prewarm is the exception — it is gated only by `DASHBOARD_PREWARM_ENABLED` (and skipped under `TESTING`) and never reads `ROUTE_PERF_CACHE_ENABLED`. Append `?refresh_cache=1` to force a miss — from a signed-in session: anonymous requests carrying `refresh_cache`, `refresh_full` or `sync_full` are redirected to the same URL without them (`REFRESH_PARAMS_REQUIRE_LOGIN`, L7/LD4). TTL constants live in `config.py` under names matching `*_CACHE_TTL_SECONDS` — `grep _CACHE_TTL_SECONDS config.py` for the current list.

## Performance baselines

| Route | TTL | Cold | Warm (median) | After time-scope overhaul (local M3 Pro, 2026-10-22; cold / warm median of 2) |
|---|---|---|---|---|
| `/analytics/networks` | 21600s | ~104s | ~420ms | 92.5s / 116ms |
| `/analytics/` | 21600s | ~27s | ~14ms | 32.0s / 49ms |
| `/analytics/risk` | 21600s | ~18s | ~400ms | 10.9s / 30ms |
| `/analytics/relationships` | 21600s | ~110s | ~400ms | 107.3s / 24ms |
| `/candidate-finance/` | 180s | — | — | — |

Prod re-measure after deploy of `23c6cc7` (2026-10-26, through Cloudflare): forced cold misses (`?refresh_cache=1`) took networks 125 s → Cloudflare 524, overview 53 s, risk 20 s, relationships 125 s → 524; warm passes once every worker had prewarmed: networks 0.75–1.1 s, overview 0.22–0.75 s, risk 0.27–0.61 s, relationships 0.43–1.1 s. The cold path for networks/relationships exceeds Cloudflare's 100 s origin timeout, so only prewarmed keys (the default cycle) are safe to serve cold; `?period=all` on the analytics pages is an unwarmed miss (the sweep's 30 s probe timed out on overview, risk and donors).

Prod re-measure after deploy of `295da60` (2026-10-28, through Cloudflare, right after the warm script): overview 0.26 s, overview `?period=all` 0.24 s, risk 0.27 s, geography 0.16 s, donors 0.14 s, state-race list 0.31 s, networks 0.40 s, relationships 0.37 s, homepage 0.20 s, search `smith` 0.54 s cold / 0.25 s warm, geo drill-down IL all-time 2.0 s cold / 0.22 s warm (the windowed default-scope cell still takes 2.7 s, plan LD8). The warm script took 4 min 54 s for the four presets on prod (faster than the 6 min local run).

The first Cold/Warm columns are the pre-overhaul prod measurements; the last column was taken on the local prod-sized `ilcf` after cache keys moved to the resolved scope (`scope_cache_key`), so the default window is the whole current cycle (`2025-01-01..2026-12-31`) rather than cycle-start..today. Cold cost is the same order of magnitude; warm hits are unchanged in kind (in-process cache). Not directly comparable machine-to-machine — re-measure on prod after deploy.

`bulk_candidate_committee_finance_agg` was a plain **view** (joining/aggregating 6.4M receipt rows on each access) until the load-time plan's P6 swap (2026-10-28); it is now a MATERIALIZED VIEW rebuilt by `scripts/swap_bulk_to_isbe.py` (see "Search route guardrails").

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
- **Cost**: ~2-3 min per worker for prewarm to complete — but since L8 the prewarm checks each key first and builds only the missing ones, so with Redis already warm a restart builds nothing.
- **Single-flight (L8)**: the overview, risk, networks, relationships and state-race list/detail builds go through `RouteCache.get_or_compute`: concurrent misses on one key, across workers, run the builder once (Redis `SET NX PX` lock `ilcf:lock:<cache>:<sha1>`, 120 s, compare-and-delete release; in-process `Event` fallback without Redis) and the others wait for its value; a builder exception caches nothing and releases the lock, after which the waiters retry the build one at a time (plan LD12: right for a transient failure; a persistent one costs k × build time until the 120 s wait deadline lets the rest build in parallel). A build that straddles a flush is not stored: `flush_all_route_caches()` writes a new token to `ilcf:flush_gen` and `_build_and_store` compares the token before and after the builder (review C2). The Redis lock is not renewed, so a worker killed mid-build blocks its key for up to 120 s (plan LD11). Stale-while-revalidate (`soft_ttl_seconds`) exists in the class but is not wired on routes (their builders need the request connection) — plan LD7.
- **Warm after a flush (L8)**: `scripts/warm_caches.py` fills the four scope presets (current cycle, previous cycle, last 12 months, all time) for the analytics pages and the homepage; both refresh chains (`scripts/refresh_prod_chain.sh` step 7B, `scripts/sync-fec.sh` step 4) run it right after `refresh-analytics`. Without `REDIS_URL` it only warns (the caches would die with its process); with `REDIS_URL` set but Redis unreachable it logs an error, warms nothing and exits 1 (the chain scripts log `STEP7B_WARN` and carry on).
- **Window-free graphs**: builders that take no dates (relationships: donor co-giving, committee similarity, candidate competition; networks: state–federal overlap) are cached once under `(builder name, arguments)` in the `analytics_window_free_graphs` cache and serve every window, so `?period=all` on relationships only builds the lobbying and 527 graphs. Prewarm fills them; a failed build is not cached (B3, J-S15).
- **Invalidation**: `refresh-analytics` (after its commit), `sunshine-import` (after the matviews and compat views) and `scripts/swap_bulk_to_isbe.py` (when it finishes recreating the `bulk_*` wrappers, the candidate aggregate and `isbe_race_candidacies` — the swap runs after the import's flush; skipped under `--dry-run`) call `flush_all_route_caches()`, which deletes the Redis `ilcf:cache:*` keys. The per-process catalog memo (`database/pg_compat.py`) is separate: a "table absent" answer is kept 5 s, a "present" one 300 s, and an `UndefinedTable` / `UndefinedColumn` error drops the whole memo, so a live import's DROP/CREATE shows up within seconds (review C7/C19). The per-instance LRUs live in each gunicorn worker and clear only on a service restart, so without Redis restart after a refresh (the weekly chain does); with Redis the flush is enough and the warm script refills the presets.

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
- Cached in `RouteCache("search_results")` (Redis-shared, TTL `SEARCH_RESULTS_CACHE_TTL_SECONDS` = 21600, flushed after imports) under `sn3:<resolved scope>:<type>:<normalized query>` (`SEARCH_CACHE_REVISION` in `webapp/routes/main.py`: bump it whenever the matching rules change so the 6 h cache cannot serve old results; spellings that normalize alike share one entry, a key-like query -- a `|` or `:`, or 3+ digits -- keeps its raw text); each section runs under a real `SET LOCAL statement_timeout` (`SEARCH_QUERY_TIMEOUT_MS`, 700 ms) and reports `timed_out` instead of running on; a response with a timed-out or errored section is not cached (`webapp/routes/main.py`, the `timed_out_sections` check), so the next request retries it.
- Candidates section reads the MATERIALIZED `bulk_candidate_committee_finance_agg` (swap script, L6; was a plain view: 620–2,758 ms → 50–55 ms).
- Apostrophe names (search Phase 2, P2-23 amended at P2-M7; revision `sn1` → `sn2`): on the QUERY side only, a word-internal apostrophe (`'` or `’`, a letter before it and two or more after it) keeps its token whole and asks for the joined word or its halves adjacent and in order, a one-letter half exact — "O'Neil" → `(oneil:* | (o <-> neil:*))`, matching a stored "O'Neil" or "ONeil" but not every Neil and not "Law Office of Neil Adams"; "D'Angelo" no longer matches "DBA Angelo's". A possessive or contraction (one letter after: "Women's", "McDonald's", "I'm") is no compound and keeps the sn1 reading (`women:*`, `mcdonald:*`). "O' Neil", "'Neil" and "Neil" are unchanged (`normalize_query` / `search_tsquery`). The stored side and every GIN index expression are untouched, and the cache key reads `normalize_query`, so "O'Neil", "O Neil" and "ONeil" are three entries.
- Substring pattern (follow-up FB-M1, P2-26 / P2-30 / P2-32; revision `sn2` → `sn3`): every `LIKE` substring branch binds `substring_pattern(q)` (`database/name_normalize.py`) -- the query's `name_tokens` joined by `%` and wrapped `%…%`, a compound's apostrophe as `_` (LIKE's one-character wildcard): "O'Neil Smythe" → `%O_NEIL%SMYTHE%`, which reaches a stored "O'Neil", "O Neil" or "O-Neil" but not "KHOT, NEIL" (the old `%O%NEIL%`) and is reachable where the old whole normalized string `%O NEIL SMYTHE%` never matched a stored apostrophe. A typed `%` or `_` never reaches a pattern; no token gives no pattern. Users: the `/search` fallback, `substring_fallback_sql`, `normalized_substring_sql`, the `/investigate` `donor_key LIKE` fallback and the candidate-finance `office_sought` substring (where a query with no letter or digit lists nothing). The `/search` id paths and the other raw `%{query}%` sites in `webapp/routes/main.py` still bind the raw text.
- Name matching (committees, donors, local and federal candidates) is by token (`database/name_normalize.py`): every query token must be a prefix of a name token, in any order, with nicknames expanded (`Mike` finds `Michael`); suffixes (`Jr`) and single-letter initials in the query are dropped. The predicate is `NAME_TSVECTOR_SQL(col) @@ to_tsquery('simple', ?)` and must repeat the index expression exactly, or the planner ignores the GIN index. One substring fallback (`LIKE` on `substring_pattern(q)`, for a normalized query of 4+ characters) runs per section only when its token query found nothing; the donors one is served by the trigram index. The numeric id path is a separate statement, never OR-ed into the token predicate.
- Source-aware helpers:
  - Committees: prefers `isbe_committees` (token match on the name), falls back to legacy `committees` (LIKE), then lists the committees linked to the candidates the query names (`isbe_candidate_committees` plus the members of their accepted `committee_family` groups), marked `via_candidate` ("via candidate First Last"), after the name matches.
  - Donors: prefers `analytics_donor_summary` (token match), falls back to legacy `donors` (LIKE).
  - Local candidates: the finance aggregate rows (with totals), then the `isbe_candidates` rows that have no aggregate row (totals 0).
  - Template links use `donor_detail_by_key` for materialized donors, `donor_detail` for legacy.

## Name search outside /search (search Phase 2, 2026-10-29)

Every other name filter and suggest matches the way `/search` does, through `database/name_normalize.py`:

- **Entity tables** (committees, candidates, donors, lobbying entities / clients / donor matches, 527 organizations / directors, FEC candidate match / committees, the legacy `donors` / `committees` / `bulk_committees_clean`): run `name_match_sql(col, q)` -- `NAME_TSVECTOR_SQL(col) @@ to_tsquery('simple', ?)`, the GIN index expression verbatim, `search_tsquery(q)` bound -- and only when that returns **zero rows** the one `substring_fallback_sql(col, q)` -- `col LIKE ?` (no `UPPER()`: the compat layer runs ILIKE, and the trigram index on `donor_name` serves only the bare column) with `substring_pattern(q)`, for 4+ normalized characters. Paged lists decide on the token COUNT (committees, lobbying entities); the 527 list and `/lobbying/flows/data` decide with a `SELECT 1 ... LIMIT 1` probe because the filter sits inside a CTE / join. `name_predicates(columns, q)` returns those variants in order, ORed over several columns (`lobbying_donor_matches` client OR donor name); callers loop `for where, params in name_predicates(...): rows = run(...); if rows: break`. A query with no letter or digit lists nothing (`WHERE FALSE`).
- **Transactional tables** (FEC Schedule A `contributor_name`, Schedule B `recipient_name`, `irs527_contributions.contributor_name`, the expenditure payee in the network suggest): `normalized_substring_sql(col, q)` only -- `UPPER(col) LIKE ?` with `substring_pattern(q)`, the query tokens joined by `%` ("Farrow, Michael" → `%FARROW%MICHAEL%`, so FEC's stored "FARROW, MICHAEL" still matches); no token statement, no new index.
- **In memory** (the Viz Lab label search): `name_matches(q, label)`, then `name_matches(q, label, substring=True)` only when no label matched.
- **Candidate-finance** (`/candidate-finance/`): the route counts first; a zero count re-runs the filter with `substring=True` (`CandidateCommitteeFinanceAgg._build_filter_sql`: candidate and committee names by token, `office_sought` stays a substring, on `substring_pattern`), and the decision is cached with the 3-minute in-process payload so the CSV export uses the same rule.
- **Plan trap -- `source =` plus `ORDER BY total_amount`.** On `analytics_donor_summary` the planner estimates ~22k rows for any one-token tsquery and, given `WHERE source = 'bulk_receipts' ... ORDER BY total_amount DESC LIMIT n`, walks `idx_analytics_donor_summary_source_amount` and filters row by row (5.3 s locally for one surname). The `/investigate` and `/person-intelligence` donor sections therefore wrap the match in a `MATERIALIZED` CTE (GIN first, then sort). The `/investigate` donor→committee section matches donors that way and probes `analytics_donor_committee_agg` per donor (`CROSS JOIN LATERAL (... WHERE x.source = 'bulk_receipts' AND x.donor_key = m.donor_key ORDER BY x.total_amount DESC LIMIT 15)`, served by `idx_analytics_donor_committee_source_donor`); a plain join was planned as an ordered walk or a hash join over all 1.3M rows (0.5-20 s). Its `donor_key LIKE` path runs only when no name matched. `/search` and the network suggest have no `source =` filter and are not affected.
- **Indexes** (`database/schema.sql`, created by `init-db`): `idx_lobbying_entities_name_tsv`, `idx_lobbying_clients_name_tsv`, `idx_irs527_organizations_name_tsv` (local: all three plus ANALYZE in 0.26 s). Prod, before the restart:
  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lobbying_entities_name_tsv ON lobbying_entities USING gin (to_tsvector('simple', regexp_replace(upper(coalesce(entity_name, '')), '[^A-Z0-9 ]', ' ', 'g')));
  CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lobbying_clients_name_tsv ON lobbying_clients USING gin (to_tsvector('simple', regexp_replace(upper(coalesce(client_name, '')), '[^A-Z0-9 ]', ' ', 'g')));
  CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_irs527_organizations_name_tsv ON irs527_organizations USING gin (to_tsvector('simple', regexp_replace(upper(coalesce(org_name, '')), '[^A-Z0-9 ]', ' ', 'g')));
  ANALYZE lobbying_entities; ANALYZE lobbying_clients; ANALYZE irs527_organizations;
  ```
- **Measured** (local `ilcf`, read-only, name-predicate statement time, median of 3 warm runs after 1 cold, range over the queries "Mike Frerichs" / "Frerichs" / "Illinois" / "rerich"; ms, before → after):

  | section | before | after |
  |---|---|---|
  | committee list filter | 94-151 | 43-75 |
  | committees `?by=family` | 138-161 | 163-207 |
  | committees suggest | 17-18 | 1-14 |
  | network suggest: donors / committees / candidates | 622-1,261 / 26-142 / 33-79 | 2-5 / 1-14 / 37-49 |
  | network suggest: vendors (transactional) | 1,406-2,335 | 489-1,091 |
  | federal suggest: Schedule A / the rest | 43-54 / ≤ 8 | 43-47 / ≤ 7 |
  | candidate-finance filter (locally a plain view; prod reads the matview) | 1,110-2,073 | 808-1,310 |
  | `/investigate` donor section / donor→committee aggregate | 3-11 / 1-2,039 (cold 12,560) | 1-14 / 1-153 (cold ≤ 160) |
  | `/person-intelligence` donor section / candidates / 527 directors | 2-12 / 16 / 2 | 1-8 / 38-49 / 9-10 |
  | lobbying list, suggests; 527 list, suggest | ≤ 10 | ≤ 11 |
  | `lobbying_donor_matches` sections (3,100 rows, no name index) | 1-6 | 3-34 |
  | 527 contributor search (org with 91,719 rows) | 12 | 14-16 |

  Still over the 700 ms section budget: the vendor substring (a scan of `bulk_expenditures_clean`, 4.85M rows; out of Phase 2 scope) and candidate-finance on the local view.

## Required indexes for cached routes

Created automatically by `isbe_sunshine_etl.py` matview/schema DDL and `database/schema.sql`:

- `isbe_condensed_receipts`: `filed_doc_id`, `committee_id`, `received_date`, `donor_name_trgm`, `donor_key_trgm`, `active_part1` (partial: `archived = FALSE AND d2_part LIKE '1%' AND amount > 0`)
- `isbe_d2_reports`: `active_filed_doc` (partial: `(filed_doc_id) WHERE archived = FALSE`)
- 527 perf: `idx_irs527_contributions_name_amount`, `idx_irs527_contributions_date_amount`, `idx_irs527_expenditures_date_amount`
- Analytics graphs: `idx_irs527_orgs_ein`, `idx_irs527_committee_matches_ein`, `idx_irs527_exp_recipient_matches_ein`, `idx_irs527_director_donor_matches_ein` (EIN lookups for 527 ecosystem graph); `idx_lobbying_donor_matches_client_id`, `idx_lobbying_exp_matches_source` (lobbying influence graph); `idx_isbe_condensed_exp_committee_id` (vendor network)
- Load-time plan (2026-10-28): `idx_isbe_condensed_receipts_donor_name_norm` (expression index on the normalized donor name; `/lobbying/<id>` receipt activity, L3), `idx_isbe_condensed_expenditures_part9_date` (partial: `d2_part LIKE '9%' AND archived = FALSE`; the race pages' Part-9 block and the candidate outside-spending pages), `idx_isbe_filed_docs_received_datetime` (freshness gate) — all matview/ETL DDL, prod gets them with `CREATE INDEX CONCURRENTLY` at deploy; `idx_analytics_donor_summary_name_trgm` (GIN `gin_trgm_ops`; the search donors substring fallback, L6) and `idx_analytics_donor_committee_agg_geo (source, donor_state_key, donor_city_key, total_amount DESC)` (all-time geo drill-down, L4) in `database/schema.sql`; the three btree indexes of the materialized `bulk_candidate_committee_finance_agg` (swap script; its name GIN is the next bullet).
- Search Phase 2 name matching: `idx_lobbying_entities_name_tsv`, `idx_lobbying_clients_name_tsv`, `idx_irs527_organizations_name_tsv` (`database/schema.sql`; prod statements in "Name search outside /search" above).
- Search name matching: GIN expression indexes on `to_tsvector('simple', regexp_replace(upper(coalesce(<name>, '')), '[^A-Z0-9 ]', ' ', 'g'))` -- `idx_isbe_committees_name_tsv` and `idx_isbe_candidates_name_tsv` (ETL DDL), `idx_analytics_donor_summary_name_tsv` and `idx_fec_candidate_match_name_tsv` (`database/schema.sql`), `idx_bulk_cand_cmte_finance_agg_name_tsv` (swap script; the aggregate now has four indexes). Prod builds them with `CREATE INDEX CONCURRENTLY` BEFORE the restart: without the donors one the token statement is a 1.2 s sequential scan (local) and the donors section times out. Local builds: committees 0.3 s / 1.5 MB, candidates 0.1 s / 1.3 MB, donors 4.4 s / 17 MB, FEC match < 0.1 s / 16 kB, aggregate 0.2 s / 1.7 MB.
- Committee profile and see-all lists: `idx_isbe_condensed_expenditures_committee_date (committee_id, expended_date)` (matview DDL; prod needs the one-time `CREATE INDEX IF NOT EXISTS` from `deployment.md`) and `idx_analytics_donor_committee_agg_source_committee (source, committee_id)` (`database/schema.sql`); the per-cycle `/donors/` list reads `analytics_donor_cycle_summary` (`idx_analytics_donor_cycle_summary_total`)

## Snapshot build compatibility

- `get_nlp_spending_summary` falls back to `bulk_expenditures_clean` when legacy `contributions` is absent.
- `get_dashboard_snapshot` handles PostgreSQL `datetime` objects for `completed_at` in addition to string timestamps.
- `?load_mode=full` never builds or refreshes a snapshot from a request (L8): the stored `analytics_snapshots` row is served stale or not, and without one the overview computes its geo summary live; `refresh_full` / `sync_full` are ignored and logged. `refresh-analytics --with-snapshot` stores its snapshot under `snapshot_version` 1 without a window, which the pages (version 3 + window) do not read — a pre-existing mismatch in `cli/commands.py`.

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

**Data caveat (A2) — resolved on prod 2026-10-27 15:25 UTC by the hand rebuild (cycle-2026 condensed non-archived receipts now $543.0M against $601.1M raw; all time $6.507B against $6.702B). Measured before the rebuild:** prod's `isbe_most_recent_filings` picker has no `NULLS LAST` yet (the DDL fix ships in the ETL and lands at the next `sunshine-import`, or by the hand rebuild in the deployment runbook). Measured 2026-10-27: condensed non-archived receipts for the 2026 cycle = 112,469 rows / $348.1M against 230,305 / $601.1M raw non-archived (42 % of the dollars hidden); all time $5.457B against $6.702B; 60,067 A-1 filed docs have a NULL `received_datetime`. Every cycle-2026 figure on prod (donor list, committee profiles, race totals, the cycle table) is understated by about that much until the matviews are rebuilt and `refresh-analytics` runs again.
