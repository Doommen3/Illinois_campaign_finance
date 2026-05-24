# Phase 2 Handoff — Filter & Navigation Coherence

**Read this first if you are picking up the redesign work.**

Multi-session forensic audit + redesign of `https://followthemoneyil.com`.
Phase 1 (broken-data sweep + filter-coherence fixes) is **complete and
deployed**. Phase 2 (filter & navigation coherence) is **in progress** —
P2-1 (Option A date-window rollout) is now complete; the remaining
P2-2…P2-5 sub-tasks are the next session's work.

The work is structured in **three sequential phases:**

1. **Phase 1** — Broken-data forensic sweep + filter coherence fixes *(complete, deployed)*
2. **Phase 2** — Filter & navigation coherence rethink *(this handoff; P2-1 foundation shipped, P2-1.4 + P2-2…P2-5 pending)*
3. **Phase 3** — Visualization & story redesign *(not started; depends on Phase 2)*

**Driving documents:**
- Master plan: `/Users/devin/.claude/plans/the-production-website-needs-valiant-quill.md`
- Phase 2 plan: `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md`
- Phase 1 handoff: `docs/audits/phase1_handoff.md` (background; mostly historical)
- Phase 1 findings: `docs/audits/phase1_findings.md`
- Route sweeps: `docs/audits/route_sweep_2026-05-23.{md,json}`, `docs/audits/route_sweep_2026-05-24.{md,json}`

---

## What's done

### Phase 1 — complete + deployed
Filter contradictions C1, C2, C4, C5 fixed. Live-feed period filter wired.
State-race detail cache shipped. Production sweep tool + categorized
catalog. Stale RouteCache tests fixed. All deployed to
`178.156.162.56` and verified.

Full detail in `docs/audits/phase1_handoff.md`. Tagged commits:
- `45fd23b` — phase1: filter coherence chip + C5 date semantics + state-race cache + live-feed period filter
- `151ed44` — phase1: production route sweep tool + audit findings + handoff docs
- `07de78a` — test: fix stale RouteCache assertions in test_app_performance.py

### Phase 2 — P2-1 product decision: **Option A** (extend FEC queries to accept date windows)

Three options were presented for the federal cycle vs. state date filter
contradiction (C3 from Phase 1). User chose Option A on 2026-05-24:
federal pages will honor the global period as a date window like every
other section, with cycle as a secondary filter.

Trade-offs documented in `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md`:
- ~19 FEC functions in `database/federal_fec.py` need optional
  `date_from`/`date_to` kwargs.
- 12 routes in `webapp/routes/federal_finance.py` need to plumb
  date_from/date_to alongside cycle.
- Federal route-level caches need `(date_from, date_to)` added to keys.
- `fec_candidate_cycle_totals` is the exception — pre-aggregated, no
  per-row date; stays cycle-only with a docstring note.

### Phase 2 session 3 — P2-1 foundation (shipped + deployed)

Tagged commit: `948c128` — phase2 P2-1: foundation for date-windowed FEC queries (Option A)

**P2-1.1 — Route-level filter parsing**
- New `_parse_federal_window()` in `webapp/routes/federal_finance.py`
  alongside the existing `_parse_shared_filters()`. Returns dict:
  `{date_from, date_to, is_override, time_period_key, explicit_date_from,
  explicit_date_to, period_cycles, active_filter_overrides}`.
- `_base_context()` now accepts an optional `window=` kwarg and threads
  the date_from/date_to + chip context into the template namespace when
  provided.
- **Additive design** — existing tuple-returning `_parse_shared_filters()`
  is unchanged. Routes adopt the new helper one at a time.

**P2-1.2 — Federal active-filter chip**
- `webapp/templates/federal_finance/_subnav.html` includes
  `_partials/active_filter_chip.html`. Every federal page that uses the
  subnav now shows the active period (and override pills when the user
  passes `?date_from=` / `?date_to=`).

**P2-1.3 — One FEC function patterned end-to-end**
- `database/federal_fec.py:get_top_donor_entities()` gains optional
  `date_from`/`date_to` kwargs filtering Schedule A by
  `contribution_receipt_date`. Cycle-only callsites work unchanged.
- `webapp/routes/federal_finance.py:federal_follow_the_money` is the
  first route wired: calls `_parse_federal_window()`, forwards
  date_from/date_to to `get_top_donor_entities`, and passes the window
  to `_base_context()`.
- `tests/test_federal_fec.py:test_get_top_donor_entities_respects_date_window`
  pins the new semantics: pre-/in-/post-window rows behave correctly;
  date_from-only and date_to-only paths work.

**Production verification (post-deploy):**
- `/federal-finance/follow-the-money` returns 200 in ~1.9s with the chip rendering.
- `?date_from=2026-01-01&date_to=2026-06-30` is honored end-to-end.

### Phase 2 session 4 — P2-1.4 full rollout (shipped + deployed)

Tagged commits (this session):
- FEC functions: date_from/date_to kwargs on all Schedule A/B/E consumers
- Routes: `_parse_federal_window()` wired into all 15 federal routes
- Cache keys: `(date_from, date_to)` appended to every FEDERAL_* cache_params, with version bumps to invalidate stale entries
- Tests: 15 new regression tests in `tests/test_federal_fec.py` named
  `test_get_federal_*_respects_date_window`; one shared
  `_seed_date_window_fixture` builds pre/in/post-window rows across A/B/E

**P2-1.4 — FEC functions wired (Schedule A)**
Helpers `_federal_edge_rows`, `_federal_donor_committee_edges`,
`_federal_committee_candidate_edges`, `_filtered_edge_rows` accept
`date_from` / `date_to` and filter Schedule A on
`contribution_receipt_date`. This propagates automatically through every
consumer that calls them. Consumers explicitly updated to forward the
kwargs (or filter inline SQL):
`get_federal_network_graph`, `get_federal_cross_role_organizations`,
`get_federal_multilayer_network_graph` (also B/E), `get_federal_donor_segmentation`,
`get_federal_donor_network_clusters`, `get_federal_influence_scores`,
`get_federal_follow_the_money`, `get_federal_geographic_concentration`,
`get_federal_geo_drilldown`, `get_federal_local_donor_matches`,
`get_federal_local_overlap_network`, `get_federal_donor_detail`.

**P2-1.4 — FEC functions wired (Schedule B / E)**
- `get_federal_committee_receipts` — `contribution_receipt_date`
  (Schedule A receipts for one committee)
- `get_federal_candidate_detail` — A: `contribution_receipt_date`,
  B: `disbursement_date`, E: `expenditure_date`. Cycle-totals summary
  block (joined to `fec_candidate_cycle_totals`) stays cycle-only
  because that table is pre-aggregated
- `get_federal_race_outside_spending` — `expenditure_date`
- `get_federal_race_analytics` — A: `contribution_receipt_date`,
  E: `expenditure_date`

**P2-1.4 — explicitly skipped per handoff guidance**
- `count_federal_candidates` / `list_federal_candidates` — backed by
  `fec_candidate_cycle_totals` (no per-row date). Route still passes
  `window` so the chip renders; counts reflect the whole cycle. Docstring
  on the route notes this.

**P2-1.4 — routes**
All 15 routes now call `_parse_federal_window()` and pass `window=` to
`_base_context()` (or thread the chip vars directly when the template
extends `base.html` rather than `federal_finance/base.html`):
`federal_overview`, `federal_candidates`, `federal_networks`,
`federal_donor_intelligence`, `federal_money_flow`, `federal_influence`,
`federal_follow_the_money` (session 3), `federal_geography`,
`federal_geo_drilldown`, `federal_matching`,
`federal_matched_donor_profile`, `federal_donor_detail`,
`federal_committee_receipts`, `federal_race_outside_spending`,
`federal_candidate_detail`.

**P2-1.4 — cache keys**
Every `FEDERAL_*_CACHE_TTL` cache_params dict now includes
`date_from` + `date_to` with a `version` bump so old entries don't
collide:
- `federal_overview` (v2 → v3)
- `federal_networks` (v3 → v4)
- `federal_donor_intelligence` (v2 → v3)
- `federal_matching` (v1 → v2)
- `federal_geo_drilldown` in-process cache (v1 → v2)

**P2-1.4 — tests**
29 federal_fec tests pass (14 pre-existing + 15 new date-window
regression tests). Fast pre-deploy subset (58 tests) green.

---

## What's next (session 5)

**Goal:** start P2-2 / P2-3 / P2-4 / P2-5. P2-1 (Option A — federal
date-window rollout) is now done and deployed. Next session should
re-run the sweep and confirm federal routes drop off the period-filter
no-op detector list, then begin the next sub-task.

### Suggested next sequence

1. **Re-run the sweep on production**:
   `python3 scripts/audit/route_sweep.py --host https://www.followthemoneyil.com`
   and confirm `/federal-finance/networks` and `/federal-finance/matching`
   no longer appear in the period-filter no-op detector. `/analytics/*`
   ones remain and are part of P2-4 follow-up scope, not P2-1.

2. **P2-3 (sitemap) and/or P2-4 (subnav propagation)** — parallel-safe
   relative to P2-2/P2-5. See sections below.

### P2-1 verification checklist (now done)

- [x] Every FEC function in the Schedule A/B/E clusters accepts `date_from`/`date_to`
- [x] Every federal route passes `window` to `_base_context()` (or threads
      chip context directly to templates that extend base.html)
- [x] Active-filter chip shows override pills on every federal page when
      `?date_from=...&date_to=...` is set
- [x] All federal tests pass (`pytest -q tests/test_federal_fec.py` — 29 tests)
- [x] Fast pre-deploy subset passes (58 tests)
- [x] Cache keys include `(date_from, date_to)` with version bumps to
      invalidate pre-window-aware cached payloads
- [ ] Re-run sweep against prod and confirm federal routes drop off the
      period-filter no-op detector (next session's first action)

---

## What's planned (sessions 5–7)

### Session 5 — P2-3 (sitemap) + P2-4 (subnav propagation), parallel-safe

**P2-3 — sitemap & nav rationalization** (no P2-1 dependency)
- Promote `/compare` to main nav — it's a user-facing comparison tool
- Group `/admin/donor-merges`, `/admin/federal-receipt-audit`,
  `/admin/federal-disbursement-audit` under the existing `/admin/` index
- Add footer link for `/legacy`, label as "Legacy archive"
- Add `/d2-reconciliation/`, `/d2-expenditures-reconciliation/` under an
  Admin / QA submenu
- Confirm reachability of:
  - `/federal-finance/races/<office>/<district>/outside-spending`
    (does a `/federal-finance/races` index exist? if no, build one)
  - `/federal-finance/matches/<fed_donor>/<local_donor>`
  - `/federal-finance/donors/<entity_key>`
- Remove confirmed dead templates:
  - `webapp/templates/federal_finance/list.html`
  - `webapp/templates/experimental/viz_lab_index.html`
  - `webapp/templates/experimental/viz_lab_triple_pipeline.html`
- Verify `analytics/_subnav.html` is **not** removed — it hosts the
  active-filter chip and is included by all 8 analytics templates

**P2-4 — subnav filter-state propagation**
- `webapp/templates/analytics/_subnav.html` currently carries only
  `date_from`/`date_to` when building inter-tab links. Each analytics page
  accepts a dozen more filters (`min_edge_amount`, `network_limit`,
  `anomaly_limit`, `concentration_limit`, etc.) that silently reset on
  tab change
- Hand-build an allow-list of which filters propagate (build from
  observing which appear on multiple tabs). Same idea for
  `federal_finance/_subnav.html` once P2-1 is done
- Add tests that assert allow-listed filters survive a tab change and
  non-allow-listed ones reset

### Session 6 — P2-2 (lobbying) + P2-5 (cross-section links)

**P2-2 — lobbying-page filter outlier**
- `/lobbying/` list pages ignore the global filter entirely; detail
  pages have their own date pickers
- **Verify before designing:** does the lobbying registration data have
  a true transaction-date axis (per-payment), or only annual
  registration windows (`reg_year`)?
  - If annual: chip should label the granularity ("By registration
    year") rather than pretending day-resolution
  - If per-payment: full date_from/date_to filtering
- Consolidate: list pages honor the global period; detail-page date
  pickers either become override pills (matching candidate-finance) or
  are removed

**P2-5 — cross-section "this person" links**
- On each entity detail page (donor, federal donor, 527 director,
  lobbying client, OpenBook vendor), render an "Also appears in"
  sidebar driven by existing `cross_matching` tables:
  - `fec_local_donor_matches`
  - `lobbying_donor_matches`
  - `irs527_director_donor_matches`
  - `lobbying_527_matches`
  - `irs527_expenditure_recipient_matches`
  - `openbook_vendor_match`
- Build as a shared partial: `_partials/cross_section_links.html`
- Add one test per detail page asserting the sidebar renders when
  matches exist and gracefully degrades when they don't

### Session 7 — Phase 2 verification + Phase 3 handoff

- Full route sweep + endpoint-sweep skill against prod
- Click-path walks (the three journeys from the master plan): donor →
  recipient; lobbying → contribution; 527 → state candidate
- Open `docs/audits/phase3_handoff.md` mirroring this doc
- Hand off to Phase 3 (visualization & story redesign) with Phase 2
  outputs as fixed inputs

---

## Open follow-ups uncovered but not Phase-2-scoped

These came out of the 2026-05-24 sweep but aren't filter / nav work.
Log here so they don't get lost:

| Issue | Where | Notes |
|---|---|---|
| `/federal-finance/committees/C00305920/receipts` returns 404 on prod | both periods | committee_id is valid in local DB; either prod data is missing it or the route lookup is broken. Triage as standalone fix. |
| `/lobbying/flows/data?period=...` times out at 30 s on prod | both periods | perf regression candidate; profile before fixing |
| `/analytics/state-races/.../?period=all` cold path still 38 s | warm hits 1.9 s after session 2 cache | route-level cache mitigates warm hits but cold path needs a SQL-level prefilter in `_build_state_race_analytics_rows` — currently builds every race in the cycle then iterates Python-side to find the matching slug |

---

## Critical files for session 4

**Read first:**
- `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md` — full Phase 2 plan
- `webapp/routes/federal_finance.py:_parse_federal_window` (line ~70-95) — the helper to consume
- `webapp/routes/federal_finance.py:federal_follow_the_money` (line ~755-800) — the pattern to copy
- `database/federal_fec.py:get_top_donor_entities` (line ~7853-7905) — the FEC function pattern to copy
- `tests/test_federal_fec.py:test_get_top_donor_entities_respects_date_window` (end of file) — the test pattern to copy

**To modify:**
- `database/federal_fec.py` — add `date_from`/`date_to` to the functions listed in the Schedule A/B/E clusters
- `webapp/routes/federal_finance.py` — wire `_parse_federal_window()` + `window=` into the remaining 14 routes
- `tests/test_federal_fec.py` — add per-function date-window regression tests
- `webapp/templates/federal_finance/_subnav.html` — already done in session 3
- Federal route cache key tuples — add `(date_from, date_to)` to every key

**Don't touch:**
- `_parse_shared_filters` — keep it as a tuple-return for backward compat
- `fec_candidate_cycle_totals` query path — no per-row date; cycle-only is correct here

---

## Seed prompt for the next session

Copy/paste this when launching the next session:

> We're continuing a multi-session forensic audit + redesign of the
> production website at https://www.followthemoneyil.com. Phase 1 is
> deployed; Phase 2 P2-1 (Option A federal date-window rollout) is now
> deployed end-to-end.
>
> Pick up where session 4 left off. Read `docs/audits/phase2_handoff.md`
> end to end. Start with the "What's next (session 5)" sequence: first
> re-run the production sweep and confirm the federal routes have
> dropped off the period-filter no-op detector list, then begin one of
> the remaining P2 sub-tasks (P2-2 lobbying, P2-3 sitemap, P2-4 subnav
> propagation, or P2-5 cross-section links).
>
> Run the fast pre-deploy subset before each commit. Deploy + verify on
> prod when done.

---

## Quick reference for the next agent

| Resource | Path |
|---|---|
| **Master plan** | `/Users/devin/.claude/plans/the-production-website-needs-valiant-quill.md` |
| **Phase 2 plan** | `/Users/devin/.claude/plans/phase2-filter-nav-coherence.md` |
| **Phase 1 handoff (background)** | `docs/audits/phase1_handoff.md` |
| **Phase 1 findings (background)** | `docs/audits/phase1_findings.md` |
| **Phase 2 handoff (this file)** | `docs/audits/phase2_handoff.md` |
| **Latest route sweep** | `docs/audits/route_sweep_2026-05-24.{md,json}` |
| **Route sweep tool** | `scripts/audit/route_sweep.py` |
| **Active-filter chip** | `webapp/templates/_partials/active_filter_chip.html` |
| **Time-filter helpers** | `webapp/utils/time_filter.py` |
| **Federal filter helper** | `webapp/routes/federal_finance.py:_parse_federal_window` |
| **Production host** | https://www.followthemoneyil.com (Hetzner 178.156.162.56) |
| **Deployment runbook** | `docs/runbooks/deployment.md` |
