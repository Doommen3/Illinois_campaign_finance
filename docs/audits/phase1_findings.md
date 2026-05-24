# Phase 1 Findings — Production Audit & Filter Coherence

**Session date:** 2026-05-23
**Plan:** `/Users/devin/.claude/plans/the-production-website-needs-valiant-quill.md`
**Companion audit data:** `docs/audits/route_sweep_2026-05-23.md`, `docs/audits/route_sweep_2026-05-23.json`

This document consolidates what Phase 1 of the end-to-end website redesign uncovered,
categorized by disposition: **fixed this session**, **fix-soon (next session)**,
**Phase 2 redesign**, **Phase 3 redesign**, **wontfix / by design**.

---

## Fixed this session

### F1. Global-period vs page-filter contradictions — active-filter chip
A single `_partials/active_filter_chip.html` now surfaces the active date window on
every page that consumes the global period. When the user overrides via URL or a
page-level date picker, the chip switches to amber, lists the overridden parameters
explicitly, and offers a "Reset to period" link that strips the overrides.

**Wired into:**
- `/candidate-finance/` (C1) — chip at top of the list; existing `start_date`/`year`/`cycle`
  inputs now surface as override pills
- `/candidate-finance/<cand>/<comm>/itemized` (C4) — chip + a previously missing
  `date_from` / `date_to` date input in the filter form
- `/candidate-finance/<cand>/<comm>/itemized-expenditures` (C4) — same pattern
- `/analytics/*` (C2) — chip injected into `analytics/_subnav.html`, so all eight
  analytics templates (overview/networks/risk/donors/geography/geo-drilldown/
  relationships/state_race_detail) now show the chip automatically

**Helper API added** in `webapp/utils/time_filter.py`:
- `period_window_with_override(period, date_from, date_to)` → dict
  `{start, end, is_override}`
- `build_filter_overrides(**kwargs)` → list of `(human_label, value)` tuples

**Tests:** `tests/test_time_filter.py` (8 new tests, 33 total in file).

### F2. COALESCE(transaction_date, filed_date) misattribution (C5)
In `database/analytics.py`, five sites used `COALESCE(transaction_date, filed_date)` to
date legacy `contributions` rows. Reports filed months after a transaction were
silently re-attributed to the report-filing month, polluting time series and
anomaly buckets.

**Sites fixed:**
- `_get_donor_committee_rows` SQL CTE (lines ~995, 998)
- `_get_anomaly_flags` large-contribution flag loop (line ~1892)
- `_get_anomaly_flags` monthly-totals loop (line ~1918)
- `get_time_series` legacy-contributions fallback loop (line ~2107)
- `analytics_large_contributions` INSERT (line ~3592)

In all five, `transaction_date` is now used alone; rows lacking it are excluded
from date-bounded queries rather than being misbucketed.

**Tests:** `tests/test_analytics_date_semantics.py` (2 tests). Construct a synthetic
`contributions` row whose `filed_date` would have placed it inside a Jan-Dec 2024
window but whose true `transaction_date` is null or in 2023 — verify it is now
excluded rather than leaking via `filed_date`.

### F3. Production endpoint sweep tool
- `scripts/audit/route_sweep.py` enumerates the Flask `url_map`, sweeps a target
  host with `?period=2026cycle` and `?period=all`, and produces a markdown
  categorization plus raw JSON.
- First sweep run lives at `docs/audits/route_sweep_2026-05-23.md`. 174 probes
  succeeded, 42 routes skipped (no local seed IDs for parameterized URLs).

---

## Fix-soon (next session, before Phase 2)

### S1. Broken state-race detail route
`/analytics/state-races/governor?period=2026cycle` returns **404** and
`?period=all` **times out at 30 s**. Either the seed race keys `governor`/`senate`
are no longer valid (the route expects something else), or the route is broken.
Confirmed live, not a sweeper artifact.

**Action:** read `webapp/routes/analytics.py:state_race_detail`, identify the
expected `race_key` shape, and either ship a fix or hide the route.

### S2. `/live-feed` ignores the global period
Sweep confirms identical 200 KB payload for `?period=2026cycle` and `?period=all`.
The CLAUDE-md-noted behavior ("Live feed shows all-time recent transactions, no
period filter") is intentional in the code, but the URL still carries the period
and gives the user no signal that it's a no-op.

**Action:** either (a) actually apply the period filter — preferred — or (b)
suppress the period dropdown on this page and explain why. Defer the design
decision to Phase 2.

### S3. Stale `test_app_performance.py` cache tests
`test_dashboard_insights_cached_across_requests` and
`test_relationships_network_cached_across_requests` fail on `main` (verified
via `git stash` round-trip) with
`'RouteCache' object does not support item assignment`. These pre-date this
session — they were not updated when the Redis-backed `RouteCache` migration
landed (commits `d213f1a`, `17f37c7`, `466d104`).

**Action:** update the tests to mutate via `RouteCache.invalidate()` / `set()`
rather than direct dict assignment. Out of scope for this session.

### S4. Seed-table coverage in `route_sweep.py`
42 parameterized routes were skipped because the seed SQL targets table names
that don't exist in the local PostgreSQL DB (e.g. `donors_canonical`,
`federal_candidates`, `lobbying_entities`). Update the seed queries to match the
real schema so the next sweep covers donor/committee/candidate/527/lobbying
detail pages.

### S5. Document the API auth gate in the sweep template
53 `/api/*` routes returned 401 — expected post-`c94ad2f`, but a stranger reading
the sweep doc would assume widespread breakage. Add a note + filter the API
routes into a separate section.

---

## Phase 2 — Filter & navigation coherence (separate plan doc, future session)

### P2-1. Federal cycle vs state date filter (C3)
`/federal-finance/*` translates the global period to an FEC cycle and drops the
date dimension. A user choosing "Past Year" sees the entire 2026 cycle on
federal pages. Resolution requires a product call:

1. **Extend federal to accept date windows** — add `date_from`/`date_to` to FEC
   queries; period-driven federal pages would inherit ranges, not cycles.
2. **Cycle-ify the global period** — make period selection cycle-aware (label
   periods as cycles, e.g. "2026 Cycle", "2024 Cycle", drop "Past Year").
3. **Split the global control into two visible filters** — one date-window, one
   cycle; each page honors what it can.

Option 2 is closest to the current data model; Option 1 is closest to user mental
model. Decide in Phase 2.

### P2-2. Lobbying-filter outlier
`/lobbying/` list pages ignore date filtering entirely; detail pages have their
own date pickers. Resolve as part of Phase 2's filter rethink.

### P2-3. Sitemap and navigation redesign
- **Not in nav today**: `/d2-reconciliation/`, `/d2-expenditures-reconciliation/`,
  `/legacy`, `/compare`, several `federal-finance` race / match detail pages.
  Decide which are user-facing (add to nav), which are admin-internal (move to
  `/admin/`), which are dead (remove).
- **Dead templates to remove**: `federal_finance/list.html`,
  `experimental/viz_lab_index.html`, `experimental/viz_lab_triple_pipeline.html`.
- **Orphan partials** (mentioned in inventory but, on inspection, still used):
  `analytics/_status.html` (overview.html includes it),
  `analytics/_subnav.html` (now hosts the active-filter chip — 8 templates),
  `federal_finance/_subnav.html` (verify before removal).
- **Cross-section "this person" links** — connect donors ↔ federal donors ↔ 527
  directors ↔ lobbying clients ↔ OpenBook vendors explicitly so users don't have
  to know they're separate datasets.

### P2-4. Drill-down state propagation
The `_subnav.html` links only carry `date_from`/`date_to`, but each analytics
page accepts a dozen more filters (`min_edge_amount`, `network_limit`, etc.).
Navigating between analytics tabs silently resets those. Worth deciding whether
to preserve all filters across the subnav, or to scope filter state per-tab.

---

## Phase 3 — Visualization & story redesign (separate plan doc, future session)

### P3-1. Visualization unification
Each page styles charts independently. Color palettes, axis formatting, and
tooltips are inconsistent across `/analytics`, `/federal-finance`, `/527`,
`/lobbying`. Define a shared chart vocabulary (probably ECharts theme + a
small wrapper module).

### P3-2. Audit existing charts against `docs/graphs_roadmap.md`'s 12 ideas
Some of the 12 are already shipped (state-race outside-spending, donor
concentration); others overlap with `network_analysis_plan.md` and the
unbuilt district map. Walk every visualization on prod and decide: keep,
replace, kill.

### P3-3. Slot in stalled plans
- District map (`illinois_clickable_district_map_plan.md`) — design done; pick
  a host page in Phase 3.
- Network analysis lab (`network_analysis_plan.md`) — Phase-1 prototype ready;
  decide whether it lives under `/experimental/` permanently or graduates.
- Column integration (`column_integration_plan.md`) — 22 cols still need
  schema work; sequence after Phase 2 finalizes table names.

### P3-4. Story-first homepage redesign
The current `/` is a card grid of summary numbers. Phase 3 should turn it into
a narrative entry point ("here is the IL money story; click to drill in"),
not a navigator.

---

## Wontfix / by design (documented for the record)

| Finding | Reason |
|---|---|
| `/api/*` returns 401 | Post `c94ad2f` API auth hardening — intentional. |
| `/527/suggest`, `/committees/suggest`, etc. return `{}` | Autocomplete endpoints expect a `q=` term; empty when omitted. |
| `/analytics/geo-drilldown` shows "no matching" with `?period=all` and no city/state | Landing page that prompts the user to drill in; not empty data. |
| `/experimental/viz-lab/data/*` 404 on prod | Local-only per CLAUDE.md (`EXPERIMENTAL_VIZ_LAB_ENABLED=false` in prod). |
| `/auth/logout`, `/admin/*`, `/manual-entry/` return identical payload across periods | Auth-gated; users see the login redirect, not the page. |

---

## Verification checklist (Phase 1)

- [x] `scripts/audit/route_sweep.py` produces a status/payload catalog; output committed to `docs/audits/`
- [x] Filter conflicts **C1, C2, C4** have shipped fixes with tests
- [x] **C5** has a shipped fix with a regression test that pins the new semantics
- [x] **C3** documented as Phase 2 work with three explicit options
- [x] `pytest -q tests/test_time_filter.py tests/test_analytics_date_semantics.py` — 33 passed
- [x] Fast pre-deploy subset — 42 passed
- [x] Candidate-finance subset — 10 passed
- [x] Analytics features — 50 passed
- [x] Full non-integration suite — 852 passed; 2 pre-existing RouteCache test failures recorded as S3
- [x] Smoke test of chip rendering verified locally — override and non-override paths both render

---

## Notes for the next session

1. **Resume point**: tackle S1 (broken state-race route) first — it's a fast win and
   was the only real 5xx-ish finding from the sweep. Then S2 (live-feed period
   no-op), then S4 (improve sweep coverage).
2. **Phase 2 plan starts here**: Use the P2-* items above as the table of contents
   when you write `/Users/devin/.claude/plans/<phase2>.md`.
3. **Don't re-explore the inventory** — the route map and template inventory from
   this session are still accurate. Just re-run the route sweep at the start of
   the next session for fresh empirical state.
