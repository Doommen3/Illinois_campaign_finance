# Plan — outside spending / independent expenditure drill-downs (requested 2026-10-28)

**Status:** REQUESTED, not yet planned (written at a context-ceiling checkpoint so the ask survives compaction; plan it with task-router → tests-first after the load-time plan).

## Devin's ask (verbatim, 2026-10-28)

> Whenever a candidate has outside spending or independent expenditures, I want that to show up on that candidate profile page as a drill down that we can see the spending or expenditures, and also if an outside spending row or rows appear on a state race detail page, I want to be able to click and see where the outside spending is coming from as line items

## Pointers (unverified until planning)

- Candidate profile: `webapp/routes/candidates.py` + `webapp/templates/candidates/detail.html` (`/candidates/<id>`, 7 queries/page; registry rows in `tests/scope_widgets_registry.py`, tests in `tests/test_candidate_profile.py`).
- State race detail: `/analytics/state-races/<race_key>` in `webapp/routes/analytics.py`; race builder in `database/analytics.py` + `database/race_keys.py` (A5 semantic: outside spending excludes archived rows; the all-time outside spending figure is asserted in `tests/test_analytics_features.py:~1252`).
- State-level outside spending comes from ISBE expenditure rows flagged supporting/opposing a candidate (check the `isbe_expenditures` / `isbe_condensed_expenditures` columns in `.claude/data-dictionary.md` and the race builder's outside-spending query); federal candidates use FEC Schedule E (`fec_schedule_e_independent_expenditures`, existing page `/federal-finance/races/<office>/<district>/outside-spending`).
- Windowed widgets need a `Widget` row in `tests/scope_widgets_registry.py` and a probe (CLAUDE.md "Registry rule").

## Shape to plan

1. Candidate profile: a "Outside spending" section shown only when rows exist (support/oppose split, total, count), linking to a drill-down page/section listing the line items (spender committee, date, amount, support/oppose, purpose, link to the spender's committee page).
2. State race detail: every outside-spending row (per candidate or per spender) links to the same line-item view filtered to that race/candidate.
3. Tests first (route tests on the PostgreSQL fixture; registry rows); docs (README/CLAUDE route line, runbook if a new cached builder appears).
