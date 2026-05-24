# Phase 1 Handoff — Production Website Audit & Redesign

**Read this first if you are picking up the redesign work.**

> **Status note (2026-05-24, session 2):** S1–S5 shipped this session. New sweep
> at `docs/audits/route_sweep_2026-05-24.{md,json}`. Phase 2 plan drafted at
> `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md` — blocked on the
> P2-1 federal-vs-state filter product decision. S6 (commit + deploy) still
> pending user review. See "What session 2 (2026-05-24) accomplished" below.

This is a multi-session forensic audit + redesign of the Illinois campaign
finance production site at https://followthemoneyil.com. The user kicked it off
because the site grew sprint-by-sprint without unified product design — filters
contradict each other, drill-downs inherit invisible state, some routes are
unreachable from the nav, and several visualizations don't tell a coherent story.

The work is structured in **three sequential phases**, broken-data first:

1. **Phase 1** — Broken-data forensic sweep + filter coherence fixes *(in progress, session 1 complete)*
2. **Phase 2** — Filter & navigation coherence rethink *(not started — needs its own plan doc)*
3. **Phase 3** — Visualization & story redesign *(not started — needs its own plan doc)*

The driving plan is at `/Users/devin/.claude/plans/the-production-website-needs-valiant-quill.md`.
Detailed findings live at `docs/audits/phase1_findings.md`. The first route sweep
sits at `docs/audits/route_sweep_2026-05-23.md` / `.json`.

---

## What session 1 (2026-05-23) accomplished

### Empirical audit
- Built `scripts/audit/route_sweep.py` — a Flask `url_map` enumerator that fires
  GET requests at a target host with `?period=2026cycle` and `?period=all`, then
  categorizes results (5xx / 4xx / 200-empty / 200-ok / skipped) into a markdown
  catalog. Also includes a "period-filter no-op detector" that flags routes
  returning identical payload sizes across periods.
- Ran it against production. Result: 174 probes succeeded; 42 routes skipped
  (no local seed IDs).
- Confirmed **`/analytics/state-races/governor` is broken** (404 + 30s timeout).
- Confirmed **`/live-feed` ignores the period filter** entirely.
- Confirmed all `/api/*` 401s are intentional (post-`c94ad2f` auth hardening).

### Filter contradictions fixed (C1, C2, C4, C5)
- New shared partial `webapp/templates/_partials/active_filter_chip.html`:
  - Green chip showing the active period when no override is active.
  - Amber chip listing the explicit overrides + a "Reset to period" link when
    the user has overridden via URL or page-level inputs.
- Wired into:
  - `/candidate-finance/` list page (C1) — `start_date`/`year`/`cycle` overrides
    surface as pills.
  - `/candidate-finance/<cand>/<comm>/itemized` and `/itemized-expenditures` (C4)
    — chip + added previously missing `date_from` / `date_to` date inputs in the
    filter form so users can actually set/clear the override.
  - All 8 analytics pages (C2) — chip injected into `analytics/_subnav.html`,
    inherited by overview/networks/risk/donors/geography/geo-drilldown/
    relationships/state_race_detail.
- C5: in `database/analytics.py`, fixed `COALESCE(transaction_date, filed_date)`
  in 5 sites. Transaction date is now authoritative; rows without it are
  excluded from date-bounded queries rather than misbucketed into report-filing
  months.
- Helper API added in `webapp/utils/time_filter.py`:
  - `period_window_with_override(period, date_from, date_to) → {start, end, is_override}`
  - `build_filter_overrides(**kwargs) → [(human_label, value), ...]`

### Tests
- 8 new tests in `tests/test_time_filter.py` (33 total in file, all passing).
- 2 new regression tests in `tests/test_analytics_date_semantics.py` pinning the
  C5 fix on a known synthetic committee.
- Fast pre-deploy subset: 42 passed.
- Candidate-finance subset: 10 passed.
- Analytics features: 50 passed.
- Full non-integration suite: 852 passed; 2 pre-existing `RouteCache`-related
  failures in `test_app_performance.py` flagged as known-bad (verified via
  `git stash` round-trip — not caused by this session).

### Files changed this session
**Modified:**
- `database/analytics.py` — C5 fixes
- `tests/test_time_filter.py` — new test classes
- `webapp/routes/analytics.py` — pass `active_filter_overrides` through `_parse_filters`
- `webapp/routes/candidate_finance.py` — pass `active_filter_overrides` + explicit date params
- `webapp/static/css/style.css` — chip styles
- `webapp/templates/analytics/_subnav.html` — includes chip
- `webapp/templates/base.html` — bumped CSS cache-buster version
- `webapp/templates/candidate_finance/itemized.html` — chip + date inputs
- `webapp/templates/candidate_finance/itemized_expenditures.html` — chip + date inputs
- `webapp/templates/candidate_finance/list.html` — chip
- `webapp/utils/time_filter.py` — `period_window_with_override`, `build_filter_overrides`

**New:**
- `docs/audits/phase1_findings.md`
- `docs/audits/phase1_handoff.md` (this file)
- `docs/audits/route_sweep_2026-05-23.md` / `.json`
- `scripts/audit/route_sweep.py`
- `tests/test_analytics_date_semantics.py`
- `webapp/templates/_partials/active_filter_chip.html`

**Nothing is committed yet** — the user is reviewing.

---

## What session 2 (2026-05-24) accomplished

### Fix-soon items shipped (S1–S5)

| ID | Action | Files |
|---|---|---|
| **S1** | `/analytics/state-races/<race_key>` was not actually broken — the prior sweep used bogus seed values (`governor`/`senate`) when the route expects hashed slugs like `governor-statewide-illinois-f72f709631`. Added a route-level `_state_race_detail_cache` (RouteCache) keyed by `(race_key, date_from, date_to, election_cycle)` to mitigate the slow `?period=all` cold path (26 s on prod). Cache TTL reuses `ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS`. Also propagated `active_filter_overrides` to the template context. | `webapp/routes/analytics.py` |
| **S2** | `/live-feed` now honors the global period via `period_window_with_override`. Threaded `date_from`/`date_to` through `_recent_local_candidate_donations`, `_recent_federal_candidate_donations`, `_recent_federal_schedule_b_disbursements`, `_recent_federal_schedule_e_expenditures` (HTML + CSV export paths). Added the active-filter chip to `live_feed.html`. | `webapp/routes/main.py`, `webapp/templates/live_feed.html` |
| **S3** | `test_app_performance.py` tests for dashboard insights + relationships cache used pre-Redis dict assignment (`cache["payload"] = None`). Replaced with `cache.invalidate()` calls. | `tests/test_app_performance.py` |
| **S4** | `route_sweep.py` seed queries pointed at tables that no longer exist (`donors_canonical`, `federal_candidates`, etc.) or used wrong column names (`lobbying_entities.id` instead of `entity_id`). Rewrote `fetch_seed_ids()` to match the real ISBE-migrated schema, including a live `_state_race_slug` build for the state-race route. Skipped routes dropped from 42 to 14 (7 unique × 2 periods); the remaining 7 hit empty post-migration tables (`donors`, `reports`, `donor_entity_local`). | `scripts/audit/route_sweep.py` |
| **S5** | Added an `auth-gated` category bucketing the `/api/*` 401s separately from real 4xx, with a header note explaining commit `c94ad2f`. | `scripts/audit/route_sweep.py` |

### Sweep re-run
- Output: `docs/audits/route_sweep_2026-05-24.{md,json}`
- 174 → 202 probes; 42 → 14 skipped
- Confirmed S1 fix: `/analytics/state-races/governor-statewide-illinois-f72f709631?period=2026cycle` → 200 in ~6 s (warm path; `?period=all` still times out on prod because the cache code isn't deployed yet)
- Confirmed S2 wiring locally; on prod the `/live-feed` 200 KB payload is still identical across periods (deploy needed)

### Tests passing this session
- `tests/test_time_filter.py` + `tests/test_analytics_date_semantics.py` + `tests/test_app_performance.py`: 38 passed (was 36, +2 from S3 restoration)
- Fast pre-deploy subset: 42 passed in 13.8 s
- `tests/test_analytics_features.py` + `tests/test_redesign.py`: 93 passed in 29.6 s

### New surface uncovered (not Phase 1 scope; logged in Phase 2 plan)
- `/federal-finance/committees/C00305920/receipts` → 404 on prod for both periods (committee_id exists in local DB; investigate prod data or route lookup)
- `/lobbying/flows/data?period=...` → 30 s read timeout on prod (perf regression suspect)
- `/analytics/state-races/.../?period=all` → still 30 s read timeout (session-2 cache fix mitigates once warm, but cold path needs a deeper SQL-level prefilter in `_build_state_race_analytics_rows`)

### Files changed this session
**Modified:**
- `database/analytics.py` *(no change this session; carried forward from session 1)*
- `scripts/audit/route_sweep.py` — S4, S5
- `tests/test_app_performance.py` — S3
- `webapp/routes/analytics.py` — S1
- `webapp/routes/main.py` — S2
- `webapp/templates/live_feed.html` — S2

**New:**
- `docs/audits/route_sweep_2026-05-24.md` / `.json`
- `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md`

Still uncommitted. S6 deploy pending user review.

---

## What still needs to be done

### Fix-soon (finish Phase 1 in the next 1–2 sessions)

| ID | Item | Notes |
|---|---|---|
| **S1** | `/analytics/state-races/governor` returns 404, `?period=all` times out at 30 s | First fast win for the next session. Read `webapp/routes/analytics.py:state_race_detail` (line 572-ish), identify the expected `race_key` shape, and either ship a fix or hide the route. |
| **S2** | `/live-feed` ignores the global period (verified empirically) | Decide: (a) actually apply the filter, or (b) hide the period dropdown on that page and explain why. (a) is preferred. |
| **S3** | `test_app_performance.py` `RouteCache` cache tests fail on `main` | Pre-existing breakage from the Redis-backed cache migration (commits `d213f1a`, `17f37c7`, `466d104`). Update tests to use `RouteCache.invalidate()` / `set()` instead of dict assignment. |
| **S4** | `route_sweep.py` skips 42 parameterized routes due to mismatched seed table names | Update seed queries in `fetch_seed_ids()` to match the real PostgreSQL schema (`donors_canonical`, `federal_candidates`, `lobbying_entities`, etc.). |
| **S5** | Sweep doc lumps the auth-gated `/api/*` 401s into the 4xx section | Add a "by-design 401" filter to the route-sweep output so the 4xx list shows real problems. |
| **S6** | Commit + deploy the session 1 changes | Once the user approves, commit, push, deploy to prod, restart `ilcf-web.service`, run the endpoint-sweep skill to confirm. |

### Phase 2 — Filter & navigation coherence (separate plan doc, future session)

The biggest open product question:

- **P2-1.** Federal cycle vs state date filter (C3 from the plan). `/federal-finance/*`
  translates the global period to an FEC cycle and drops the date dimension.
  Three options:
  1. Extend federal queries to accept date windows.
  2. Make the global filter cycle-aware (relabel periods as cycles).
  3. Split the global control into two visible filters (one date, one cycle).

- **P2-2.** Lobbying filter outlier: list pages ignore date filtering; detail
  pages have their own date pickers. Resolve.

- **P2-3.** Sitemap & navigation. Decide each not-in-nav route:
  - `/d2-reconciliation/`, `/d2-expenditures-reconciliation/` — add to nav under admin? hide?
  - `/legacy` — keep, label, or remove?
  - `/compare` — promote to nav?
  - Several `federal-finance` race / match detail pages.
  - Remove dead templates: `federal_finance/list.html`,
    `experimental/viz_lab_index.html`, `experimental/viz_lab_triple_pipeline.html`.

- **P2-4.** Subnav state propagation. Right now `_subnav.html` only carries
  `date_from`/`date_to`; navigating between analytics tabs resets the other
  dozen filters. Decide whether to preserve all filters or scope them per-tab.

- **P2-5.** Cross-section "this person" links — connect donors ↔ federal donors
  ↔ 527 directors ↔ lobbying clients ↔ OpenBook vendors explicitly so users
  don't have to know they're separate datasets.

### Phase 3 — Visualization & story redesign (separate plan doc, future session)

- **P3-1.** Unify visualization style. Each page styles charts independently
  today. Define a shared theme + chart wrapper.
- **P3-2.** Audit every chart on prod against `docs/graphs_roadmap.md`'s 12
  ideas. Keep / replace / kill each.
- **P3-3.** Slot in the stalled plan docs:
  - `docs/illinois_clickable_district_map_plan.md` — design done; pick a host page.
  - `docs/network_analysis_plan.md` — Phase-1 prototype ready; decide if it
    graduates from `/experimental/`.
  - `docs/column_integration_plan.md` — 22 columns ready for schema work;
    sequence after Phase 2 finalizes table names.
- **P3-4.** Story-first homepage. `/` is currently a card grid. Phase 3 should
  turn it into a narrative entry point.

---

## Concrete plan for the next agent session

### Goal of session 2
Finish the "fix-soon" items (S1–S6) and start drafting the Phase 2 plan doc.
Do **not** start Phase 2 implementation work — only the plan doc.

### Step-by-step

1. **Pick up context** (5 min)
   - Read this file end-to-end.
   - Read `docs/audits/phase1_findings.md` for the categorized inventory.
   - Skim `docs/audits/route_sweep_2026-05-23.md` (or re-run the sweep — see step 6).
   - Check `git status` — if session 1's work is still uncommitted, ask the user
     before doing anything else.

2. **Tackle S1 — fix the state-race route**
   - Read `webapp/routes/analytics.py` around the `state_race_detail` endpoint
     (search for `state-races/<race_key>`).
   - Hit `https://followthemoneyil.com/analytics/state-races/governor?period=2026cycle`
     to capture the actual error response.
   - Decide: is the route looking up `race_key` against a table that no longer
     exists / has different values? Is it expecting a specific format?
   - Fix it (or, if it's been superseded by something else, hide the route and
     remove the template).
   - Add a route-level regression test if appropriate.

3. **Tackle S2 — `/live-feed` period filter**
   - Read `webapp/routes/main.py:live_feed`.
   - Decision: apply the period filter (preferred) or visibly disable the
     period dropdown for this page. Make the call and ship it.

4. **Tackle S3 — fix `test_app_performance.py` cache tests**
   - The two failing tests assume `_relationships_cache` and `_dashboard_insights_cache`
     are dicts. They are now `RouteCache` instances (post Redis migration).
   - Update each test to use `cache.invalidate()` or `cache.set(payload=...)`
     rather than `_cache["payload"] = None`. Check `database/route_cache.py`
     (or wherever `RouteCache` is defined) for the actual API.

5. **Tackle S4 — improve `route_sweep.py` seed coverage**
   - In `scripts/audit/route_sweep.py`, `fetch_seed_ids()` queries tables that
     don't all exist locally. Update queries to match the real PostgreSQL
     schema. Verify with `\dt` on the local `ilcf` database.
   - Also handle the `Bash` "no seed available" skipped routes — most of them
     can be filled in.

6. **Re-run the production sweep**
   - `DATABASE_URL=postgresql://devin@localhost/ilcf .venv/bin/python3 scripts/audit/route_sweep.py --host https://followthemoneyil.com --output docs/audits/route_sweep_$(date +%Y-%m-%d).md --json docs/audits/route_sweep_$(date +%Y-%m-%d).json`
   - Compare against the 2026-05-23 baseline. Confirm S1 and S2 are now green.

7. **Run the test suites**
   - `TEST_DATABASE_URL=postgresql://devin@localhost/ilcf_test .venv/bin/python3 -m pytest -q tests/test_time_filter.py tests/test_analytics_date_semantics.py tests/test_app_performance.py`
   - Then the fast pre-deploy subset from CLAUDE.md.
   - Then full non-integration if time allows: `pytest -q -m "not integration" --ignore=tests/test_triple_pipeline_viz.py --ignore=tests/test_network_advanced_metrics.py`

8. **S6 — commit + deploy (only if the user explicitly says go)**
   - Per CLAUDE.md, deployment is human-driven. Don't deploy autonomously.
   - Suggest a commit message grouping: one commit for filter chip + C1/C2/C4,
     one for C5 (analytics date semantics), one for the audit tooling, one for
     S1/S2/S3 if shipped.

9. **Draft the Phase 2 plan doc**
   - File: `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md`
   - Use the P2-* items in `phase1_findings.md` / this file as the spine.
   - For P2-1 (federal cycle vs state dates), ask the user the product
     question first before writing the design — that decision drives
     everything else.

10. **Update the multi-session plan**
    - Tick off completed Phase 1 items in
      `/Users/devin/.claude/plans/the-production-website-needs-valiant-quill.md`.
    - Add a link to the Phase 2 plan doc.

### Verification checklist for session 2

- [ ] `/analytics/state-races/...` returns 200 with data on prod (or is gone)
- [ ] `/live-feed?period=2026cycle` and `?period=all` return different payloads (or the dropdown is suppressed for this page)
- [ ] `test_app_performance.py` cache tests pass
- [ ] `route_sweep.py` skips ≤ 10 routes (down from 42) on the next run
- [ ] Fresh sweep doc written; baseline diff documented in the doc itself
- [ ] Phase 2 plan doc exists
- [ ] User has been asked for the P2-1 product decision before Phase 2 work proceeds

---

## Seed prompt for the next session

Copy/paste this when launching the next session:

> We're continuing a multi-session forensic audit + redesign of the production
> website at https://followthemoneyil.com. Session 1 finished a production
> sweep and fixed four filter-coherence bugs.
>
> Pick up where session 1 left off. Read `docs/audits/phase1_handoff.md` end to
> end and follow the "Concrete plan for the next agent session" steps in order.
>
> Goals for this session: ship S1–S5 from the handoff doc (fix the broken
> state-race route, fix the live-feed period no-op, fix the stale RouteCache
> tests, improve the route sweep seed coverage, and clean up the sweep doc).
> Then draft the Phase 2 plan doc at
> `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md`, but ask me the
> P2-1 federal-vs-state filter question before designing.
>
> Do not deploy autonomously. Show me the diff and ask before committing.

---

## Quick reference for the next agent

- **Plan file:** `/Users/devin/.claude/plans/the-production-website-needs-valiant-quill.md`
- **Findings doc:** `docs/audits/phase1_findings.md`
- **This handoff:** `docs/audits/phase1_handoff.md`
- **Route sweep tool:** `scripts/audit/route_sweep.py`
- **Latest sweep results:** `docs/audits/route_sweep_2026-05-23.{md,json}`
- **Active-filter chip:** `webapp/templates/_partials/active_filter_chip.html`
- **Time-filter helpers:** `webapp/utils/time_filter.py` (`period_window_with_override`, `build_filter_overrides`)
- **Production host:** https://followthemoneyil.com (Hetzner 178.156.162.56)
- **Deployment runbook:** `docs/runbooks/deployment.md`
