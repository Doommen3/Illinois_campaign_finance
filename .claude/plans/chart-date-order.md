# Chart date order — newest month first (to-do item 6)

**Status:** DONE 2026-11-08T06:23:21Z on branch `item6-chart-order` (3 commits), MERGE PENDING terminal 1 + Devin's word. Was: IN PROGRESS 2026-11-08 06:12Z — terminal 2 (session `illinois-campaign-finance-0c`, Fable directing; builder
`opus-builder-6` = opus-engineer, Opus, high; controller `project-controller` = Haiku, low). Worktree
`/Users/devin/Illinois_campaign_finance/.claude/worktrees/item6-chart-order`, branch `item6-chart-order` off `main`
`fa3158e`. Status line: `to_do/to_do_10_28_26.status.md` §6 (exact-string edits only, main tree). Merge / deploy =
terminal 1 on Devin's word.

## Brief (fable-architect §1)

- **Interpretation.** Devin (to-do item 6): "for the time series line chart and the MoM change distribution, the date
  order should be most recent first, and the same for any other graphs we have like this on the site. if you find
  any others, put them into a document for my review before making the changes on them." The two named charts are the
  `<h3>` titles on `/analytics/` (`webapp/templates/analytics/overview.html` "Trend Visuals"): **Time-Series Line
  Chart** (`#overview-time-series-svg`, `renderOverviewTimeSeries`) and **MoM Change Distribution**
  (`#overview-mom-svg`, `renderOverviewMom`) in `webapp/static/js/analytics_visuals.js`. Both read the JSON script tag
  `#analytics-overview-time-series` = `get_time_series()` rows in ascending month order and plot them left to right,
  oldest first. Wanted: newest month first = the newest month at the **left edge** of both charts (decision J1 below
  confirms the direction with Devin). Every other time-ordered graph (and, as a secondary list, every time-ordered
  table) is inventoried in a review document and left unchanged.
- **Scope.** Narrow: one script, the cache stamp on the three templates that load it, one test, one document.
- **Approach.** Reverse the row order inside the two render functions only (`const rows = [...parsed].reverse()` or
  equivalent); the JSON API (`/api/analytics/time-series`), `get_time_series()` (moving average, MoM % computed in
  chronological order) and the tables keep their order. Each plotted `circle` and each MoM `rect` gets a `data-month`
  attribute (testability; no other behaviour change). The "Peak" / "Range" captions are order-independent. Bump the
  script's cache-busting stamp `v='20260213-1'` → `v='20261108-1'` in `analytics/overview.html`, `analytics/risk.html`,
  `analytics/geography.html` (same file, all three must agree). No Python changes.
- **Success criteria.** (1) `tests/test_analytics_visuals_js.py` green: by x position the points, the month labels and
  the MoM bars run newest → oldest, newest at the left. (2) `node --check webapp/static/js/analytics_visuals.js` exit 0.
  (3) `docs/audits/chart_date_order_2026-11-08.md`: method (grep patterns + templates/scripts swept), section A every
  time-ordered **graph** on the site (page URL, template / script `file:line`, what it shows, data function, current
  order, recommendation), section B every time-ordered **table** (same columns), section C JSON endpoints (leave),
  section D the manual-test entry text (next free id after `docs/manual_tests.md`'s last, format of MT-032) for terminal
  1 to append at deploy, section E "how to find charts like this". (4) `git status --short` in the worktree shows only
  the script, the three templates, the test and the document. (5) Public-repo rule: no data values, no donor names.
- **Verification (Fable).** Re-run the test and `node --check`; read the diff of the two render functions; open the
  document and spot-check two inventory rows against the code; grep the document for address / zip / donor-key
  patterns. No full suite here (merge gate is terminal 1's, in a verify clone).
- **Time budget.** 40 min builder time (range 25–55; thresholds 20 / 40 / 60). **M1** = the review document (≤ 20
  min), stop and report → **M2** = the chart flip + `data-month` + stamp bump, test green (≤ 15 min), stop.
- **Risks.** A reversed time axis is unconventional for a line chart (J1 confirms with Devin before M2). The same
  script serves three pages — the stamp must change on all three. `docs/manual_tests.md` is dirty in the main tree
  (the other session's MT-033), so the MT entry is delivered as text in the document, not appended.

## Decision journal

| id | step | decision | alternative not taken | reversible? | status |
|---|---|---|---|---|---|
| J1 | M2 | Newest month at the left edge of both charts (Devin's literal request); the tables and the JSON stay chronological | flip only the MoM bars and the tables, keep the line chart left-to-right chronological | yes | DECIDED by Devin 06:17Z ("Yes, newest month at the left") |
| J2 | M1 | The inventory covers graphs (section A) and, separately, tables with time rows (section B); only graphs are candidates for the follow-up change | graphs only | yes | OPEN |
| J3 | M2 | `data-month` attributes on the points and bars for testability | test by label text only | yes | OPEN |
| J4 | M2 | Manual-test entry delivered as text in the document (main tree's `docs/manual_tests.md` is dirty) | append MT-034 on this branch | yes | OPEN |

## Scaffold

**Seeds for the inventory (Fable's sweep, 06:00Z — the builder verifies and completes):**
- Graphs: `webapp/static/js/compare.js` `/compare` "Trend Comparison" (`#compare-trend-svg`, `overlap.month_rows`,
  `row.month` labels — same shape as the line chart); `webapp/templates/lobbying/entity_detail.html:30-42` and
  `lobbying/client_detail.html:30-42` CSS "mini-timeline" bars (`money_destinations.monthly_series`, built in
  `webapp/routes/lobbying.py:576-591` as `sorted(monthly_totals.items())[-18:]`); Viz Lab `renderCumulativeInflowChart`
  (`webapp/static/js/experimental_viz_lab.js:413`, local-only, cumulative = chronological by construction → N/A).
  Non-time graphs (networks, sankeys, heatmap, bubble scatter, histograms) are out of scope — say so in one line.
- Tables: `analytics/overview.html:161-183` "Time-Series Preview" (`time_series[-8:]`, oldest first);
  `analytics/geography.html:63-85` "Time-Series Intelligence" (all rows, oldest first); `entity/geography.html:66-85`
  "Monthly contributions" (`database/entity_analytics.py:_monthly_series`, oldest first); `analytics/risk.html:69`
  "Date/Month" column (check the sort); `investigate.html:41-43` month dropdown (check the order); lobbying
  `committee_series` if time-ordered.
- JSON: `/api/analytics/time-series` (`webapp/routes/api.py:339`) ascending — leave.
- Sweep method to record: `grep -rn -i "month\|\.year\b\|date" webapp/static/js/*.js webapp/static/js/entity_views/*.js`
  for axis labels; `grep -rln -i "monthly\|by month\|month_key\|row\.month\|timeline" webapp/templates`; every
  `<svg`/`<canvas` in `webapp/templates` classified time-ordered or not.

**Files.** Edit: `webapp/static/js/analytics_visuals.js` (lines 85–178), `webapp/templates/analytics/overview.html:157`,
`webapp/templates/analytics/risk.html:58`, `webapp/templates/analytics/geography.html:59` (stamp only). New:
`docs/audits/chart_date_order_2026-11-08.md`. Test (written by Fable, red observed 06:1xZ): `tests/test_analytics_visuals_js.py`.

**Commands.** `cd /Users/devin/Illinois_campaign_finance/.claude/worktrees/item6-chart-order`;
`../../../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_analytics_visuals_js.py`;
`node --check webapp/static/js/analytics_visuals.js`; `date -u +%FT%TZ`.

## Environment rules for the builder

Work only in the worktree above; commit there at each milestone (`git add <files>` by name, never `-A`), never push;
never touch `/Users/devin/Illinois_campaign_finance` (the main tree — another session's dirty files live there) or any
other worktree; edit only the files named under **Files**; never edit `CLAUDE.md`, `README.md`, `to_do/*`,
`docs/manual_tests.md`, `.claude/plans/*`, any Python; no database access needed; start no server; run only the named
commands (no full suite); `date -u +%FT%TZ` at start and in every report.

## Log
- 2026-11-08 06:11:45Z worktree created off `main` `fa3158e`; plan + red test written by Fable.
- 2026-11-08 06:16:42Z RED observed by Fable: `pytest -q tests/test_analytics_visuals_js.py` → 2 failed (points `[null×6]`, labels oldest-first, bars `[]`), 1 passed (`node --check`). Builder `opus-builder-6` dispatched for M1 (document only) 06:16Z; controller baselined.
- 2026-11-08 06:17Z Devin: J1 = newest month at the left (both charts); the test stands as written. M2 dispatch waits for the M1 report.
- 2026-11-08 06:20:11Z **M1 report** (3.4 min of 20): commit `31c9a1c`, `docs/audits/chart_date_order_2026-11-08.md` 131 lines — method (6 grep steps), A 6 graphs (A1/A2 changing in M2; A3 compare trend, A4/A5 lobbying mini-timelines → recommend flip; A6 Viz Lab cumulative → leave; non-time graphs listed out of scope), B.1 7 period tables (B1 overview preview, B2 geography, B3 entity monthly → flip if A1 accepted; B4–B7 already newest-first), B.2 3 N/A + record lists, C 4 JSON rows leave, D MT-034 text, E 5 bullets. **Fable verification → M1 ACCEPTED 06:23Z:** headings present; spot-checked A3 (`main.py:2231` `sorted(trend_by_month.keys())`), B4 (`main.py:2762` `ORDER BY month_key DESC`), A4 (`lobbying.py:576-579` `sorted(monthly_totals.items())`) — all match; privacy grep clean (one false positive on table pipes). M2 dispatched 06:23Z with J1; controller milestone check dispatched.
- 2026-11-08 06:21:42Z **M2 report** (builder clock 21 s; wall ≈ 1 min from the 06:21Z resume): commit `bf10065` — `analytics_visuals.js` +6/−2 (`[...rows].reverse()` in `renderOverviewTimeSeries`, `.reverse()` after the MoM filter, `data-month` on every circle and bar), the stamp `20260213-1` → `20261108-1` on the three templates; test 2 failed → 3 passed; `node --check` 0. **Fable verification → M2 ACCEPTED 2026-11-08T06:23:21Z:** re-ran the test (3 passed in 0.42 s) and `node --check` (exit 0); read the full diff (only the intended lines); `grep` shows 3 × `v='20261108-1'` and no old stamp; review document untouched (nothing in M2 changed a row). ITEM 6 COMPLETE on branch `item6-chart-order`: `31c9a1c` (inventory) → `bf10065` (charts) → test + plan commit below. Builder time ≈ 5 min of 40. Merge = terminal 1 (full suite in a verify clone, then ff), MT-034 appended to `docs/manual_tests.md` at deploy from section D of the review document.
- 2026-11-08T06:23:21Z **Status:** DONE on the branch, MERGE PENDING (terminal 1 informed by message).
