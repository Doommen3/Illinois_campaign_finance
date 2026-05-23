# Route Performance Caching — Reference

Detailed reference for the in-process TTL cache layer used by heavy routes. The top-level invariants live in `CLAUDE.md`; this file captures the architecture details, dated bug recaps, and contract-level requirements that don't need to be in every conversation context.

## Cached routes

In-process TTL caches are applied to:

- `/` — homepage candidate stats, top donors, dashboard insights
- `/analytics/` — overview (all 7 aggregates + state race analytics)
- `/analytics/networks`
- `/analytics/risk` — anomalies + reconciliation
- `/analytics/relationships`
- `/analytics/geo-drilldown`
- `/federal-finance/geo-drilldown`
- `/candidate-finance/` — paginated, keyed on all filter params
- `/527/dark-money` — summary stats
- `/experimental/viz-lab/data/*` — prototype gallery endpoints

All caches respect `ROUTE_PERF_CACHE_ENABLED` and `TESTING` flags. Append `?refresh_cache=1` to force a miss. TTL constants live in `config.py` under names matching `*_CACHE_TTL_SECONDS` — `grep _CACHE_TTL_SECONDS config.py` for the current list.

## Performance baselines

| Route | TTL | Cold | Warm (median) |
|---|---|---|---|
| `/analytics/networks` | 1800s | ~104s | ~420ms |
| `/analytics/` | 1800s | ~27s | ~14ms |
| `/analytics/risk` | 1800s | ~18s | ~400ms |
| `/analytics/relationships` | 1800s | ~110s | ~400ms |
| `/candidate-finance/` | 180s | — | — |

`bulk_candidate_committee_finance_agg` is a **view**, not a table — it joins/aggregates 6.4M receipt rows on each access.

## Per-worker cache + startup prewarm

`warm_analytics_caches()` runs in a background thread per gunicorn worker on startup (gated by `DASHBOARD_PREWARM_ENABLED`). It pre-populates risk, networks, and relationships caches with default filter values (`period=2026cycle`, default limits).

- **Why prewarm**: gunicorn `--workers 3` means 3 separate Python processes, each with its own in-process dict cache. Without prewarm, ~33% of requests hit a warm worker. Prewarm warms all workers on startup.
- **Cache key alignment**: prewarm keys must exactly match `_parse_filters()` defaults — otherwise prewarm results won't be hit.
- **TTL choice**: 1800s (30 min) ensures caches survive between typical user sessions. Data only changes on import, so long TTL is safe.
- **Cost**: ~2-3 min per worker for prewarm to complete.

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
- Legacy routes (`/reports`, `/donors`, `/committees`, search report/filed-doc/donor-key sections) propagate the active global period window end-to-end.
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
