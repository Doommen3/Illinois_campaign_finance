# Chart date order: inventory for review (to-do item 6)

**Date:** 2026-11-08. **Branch:** `item6-chart-order` off `main` `fa3158e`. **Plan:** `.claude/plans/chart-date-order.md`.

Devin's request: "for the time series line chart and the MoM change distribution, the date order should be most
recent first, and the same for any other graphs we have like this on the site. if you find any others, put them into
a document for my review before making the changes on them."

This document lists every time-ordered graph (section A) and every table whose rows are time periods (section B) on
the site, with the current order and a recommendation. Only the two charts Devin named change on this branch (M2);
every other row waits for Devin's call. "Newest first" for a graph means the newest month sits at the left edge.

## Sweep method

Run from the repo root. Every hit was opened and classified by hand.

1. Graph containers in templates:
   `grep -rn -i "<svg\|<canvas\|mini-timeline" webapp/templates` (icon SVGs excluded).
2. CSS-drawn bars: `grep -rn "style=\"height:\s*{{\|style=\"width:\s*{{\|bar-fill\|sparkline" webapp/templates`.
3. Chart.js use: `grep -rn "createChart\|new Chart(" webapp/static/js webapp/templates`.
4. Time axes in scripts: `grep -rn -i "\.month\b\|month_rows\|monthly\|month_key\|\.year\b\|cycle" webapp/static/js/*.js webapp/static/js/entity_views/*.js`.
5. Time-period tables: `grep -rln -i "monthly\|by month\|month_key\|row\.month\|\.month\b\|timeline\|<th>Month\|<th>Date\|<th>Year\|<th>Cycle" webapp/templates`,
   then `grep -rn -i "<th[^>]*>\s*\(Month\|Date\|Year\|Cycle\|Period\|Filed\|Election Year\)" webapp/templates`.
6. For each time-ordered hit, the data function was traced to its `ORDER BY` / `sorted(...)` / slice.

Swept: `webapp/templates/**/*.html`, `webapp/static/js/*.js`, `webapp/static/js/entity_views/*.js`
(`webapp/static/js/vendor/` is third-party and was skipped).

## A. Graphs with a time axis

| # | Page URL | File:line | What it shows | Data function | Current order | Recommendation |
|---|---|---|---|---|---|---|
| A1 | `/analytics/` (also `/analytics/overview`) | `webapp/templates/analytics/overview.html:146`; `webapp/static/js/analytics_visuals.js:85` `renderOverviewTimeSeries` | **Time-Series Line Chart**: monthly total amount, one point per month, month labels under the axis | `get_time_series` (`database/analytics.py:2079`, months sorted at `:2186`), called from `dashboard()` (`webapp/routes/analytics.py:679`) | Oldest at left, newest at right | **Changing in M2**: flip, newest at left (authorized; direction is decision J1) |
| A2 | `/analytics/` | `webapp/templates/analytics/overview.html:152`; `webapp/static/js/analytics_visuals.js:134` `renderOverviewMom` | **MoM Change Distribution**: one bar per month with a month-over-month %, green up / red down, no labels | same as A1 (months whose MoM % is empty are skipped) | Oldest at left, newest at right | **Changing in M2**: flip, newest at left (authorized; J1) |
| A3 | `/compare` | `webapp/templates/compare.html:205`; `webapp/static/js/compare.js:65-91` | Trend Comparison: two monthly total lines (left and right selection), month labels | `_build_overlap` (`webapp/routes/main.py:2188`, `sorted(trend_by_month)` at `:2231`); each side's trend comes from the compare profile loaders (`ORDER BY month_key` at `main.py:1895` and `:2044`, `sorted` at `:2161`) | Oldest at left, newest at right | Flip, same shape as A1; reverse in `compare.js` only |
| A4 | `/lobbying/<entity_id>` | `webapp/templates/lobbying/entity_detail.html:31-40` | CSS mini-timeline: monthly donations by the entity's matched donors, last 18 months, bar labels show the two-digit month only | `_get_receipt_activity` (`webapp/routes/lobbying.py:437`; `sorted(monthly_totals.items())` at `:578`, `[-18:]` at `:591`) via `_build_money_destinations` (`:597`) | Oldest at left, newest at right | Flip; reverse in the template (`|reverse`) so the Python `[-18:]` still keeps the newest 18. The two-digit labels hide the year boundary either way |
| A5 | `/lobbying/client/<client_id>` | `webapp/templates/lobbying/client_detail.html:31-40` | Same mini-timeline for a lobbying client | same as A4 | Oldest at left, newest at right | Flip together with A4 (same markup) |
| A6 | `/experimental/viz-lab` (local only) | `webapp/templates/experimental/viz_lab.html:139` canvas; `webapp/static/js/experimental_viz_lab.js:413` `renderCumulativeInflowChart` | Cumulative inflow by month per series (Chart.js line) | `_build_cumulative_inflow` (`webapp/routes/experimental.py:284`); month labels sorted ascending in JS at `experimental_viz_lab.js:417` | Oldest at left, newest at right | Leave: a running total reads chronologically by construction, and the page is local-only |

**No time axis (out of scope):** every other graph on the site is a network, sankey, heatmap, bubble scatter,
treemap/sunburst, histogram, severity bar, state grid, city bar or district map. That covers `analytics/networks.html`,
`analytics/relationships.html`, `analytics/risk.html:47,53`, `analytics/geography.html:47,53`,
`federal_finance/{matching,donor_intelligence,follow_the_money,networks}.html`, `lobbying/flows.html`,
`experimental/viz_lab_state_maps.html`, `webapp/static/js/entity_views/*.js` and the Viz Lab bubble and bar charts
(`experimental_viz_lab.js` race pressure, race concentration, payee dominance, entity resolution, network slice).

## B. Tables with time rows

### B.1 Rows are time periods

| # | Page URL | File:line | What it shows | Data function | Current order | Recommendation |
|---|---|---|---|---|---|---|
| B1 | `/analytics/` | `webapp/templates/analytics/overview.html:161-183` | Time-Series Preview: the latest 8 months (`time_series[-8:]` at `:173`) with total, count, 3-month average, MoM % | `get_time_series` (as A1) | Oldest at top | Flip if A1 is accepted (`time_series[-8:]|reverse`, slice first) so the table reads like the chart above it |
| B2 | `/analytics/geography` | `webapp/templates/analytics/geography.html:63-85` | Time-Series Intelligence: every month in the window, same columns as B1 | `get_time_series` via `geography()` (`webapp/routes/analytics.py:1080`) | Oldest at top | Flip if A1 is accepted |
| B3 | `/candidates/<id>/geography`, `/analytics/state-races/<race_key>/geography` | `webapp/templates/entity/geography.html:66-80` | Monthly contributions: every month from first to last contribution, gaps kept | `_monthly_series` (`database/entity_analytics.py:459`, `range(first, last + 1)` at `:481`) | Oldest at top | Flip if A1 is accepted |
| B4 | `/investigate` | `webapp/templates/investigate.html:41-43` | Month picker (dropdown) | `investigate_workspace` (`webapp/routes/main.py:2534`, `ORDER BY month_key DESC` at `:2762`) | Newest first | Leave: already newest first |
| B5 | `/compare` | `webapp/templates/compare.html:103-115` and the right-hand copy below it | Election History per side | `_load_candidate_compare_profile` (`webapp/routes/main.py:1836`, `ORDER BY election_year DESC` at `:1962`) | Newest first | Leave: already newest first |
| B6 | `/candidates/<id>` | `webapp/templates/candidates/detail.html:106-115` | Candidacies by election year | `webapp/routes/candidates.py:182-188` (`ORDER BY election_year DESC NULLS LAST`) | Newest first | Leave: already newest first |
| B7 | `/527/<ein>` | `webapp/templates/irs527/detail.html:252-270` | Filing History (period start, period end, filed) | `webapp/routes/irs527.py:618-627` (`ORDER BY period_end DESC, period_start DESC`) | Newest first | Leave: already newest first |

### B.2 Date column present, rows not ordered by time

| # | Page URL | File:line | What it shows | Data function | Current order | Recommendation |
|---|---|---|---|---|---|---|
| B8 | `/analytics/risk` | `webapp/templates/analytics/risk.html:69` "Date/Month" | Anomaly flags | `get_anomaly_flags` (`database/analytics.py:1632`, sorted by severity at `:2075`) | By severity, highest first | N/A: not a time series |
| B9 | `/candidates/<id>/risk`, `/analytics/state-races/<race_key>/risk` | `webapp/templates/entity/risk.html:29,38` "Date" | Per-entity risk flags | `get_entity_risk` (`database/entity_analytics.py:721`; flag lists sorted by value at `:648`, `:674`) | By value, highest first | N/A: not a time series |
| B10 | `/lobbying/<entity_id>`, `/lobbying/client/<client_id>` | `webapp/routes/lobbying.py:654-667` (no template table of its own) | Committee destinations of the matched donors' money (top committees) | `committee_series` (`webapp/routes/lobbying.py:580-584`) | By amount, highest first | N/A: not a time series |

**Record lists with a Date or Filed column (rows are records, not periods; order not checked; leave):**
`live_feed.html:59,105,148,193`, `investigate.html:195`, `search.html:207`, `openbook/detail.html:140`,
`manual_entry/queue.html:30`, `committees/_filing_table.html:6`, `committees/filing_detail.html:57,78`,
`federal_finance/match_profile.html:100`, `federal_finance/donor_detail.html:107`, `federal_finance/detail.html:254`,
`irs527/detail.html:199,231`. Date-range summary cells (`federal_finance/{money_flow,match_profile,donor_detail,networks,detail}.html`)
and single-record forms (`manual_entry/form.html`, `reports/detail.html`) carry no ordering.

## C. JSON endpoints and embedded data

| # | Endpoint / tag | File:line | Data function | Current order | Recommendation |
|---|---|---|---|---|---|
| C1 | `/api/analytics/time-series` | `webapp/routes/api.py:338` | `get_time_series` | Oldest first | Leave: an API contract; chronological order is what callers expect |
| C2 | `#analytics-overview-time-series` script tag | `webapp/templates/analytics/overview.html:156` | `get_time_series` | Oldest first | Leave: the M2 flip happens inside the two render functions, so the 3-month average and MoM % stay computed in time order |
| C3 | `#compare-trend-data` script tag | `webapp/templates/compare.html:207` | `_build_overlap` | Oldest first | Leave; if A3 is flipped, flip in `compare.js` |
| C4 | `/experimental/viz-lab/data/triple-pipeline/timeline` (local only) | `webapp/routes/viz_lab.py:183` | precomputed `timeline_index.json` artifact | As stored in the artifact (not checked) | Leave |

## D. Manual-test entry (for terminal 1 to append to `docs/manual_tests.md` at deploy)

Index row:

```
| MT-034 | Analytics | The overview trend charts put the newest month on the left | 2026-11-08 |  |
```

Entry:

```
### MT-034 — The overview trend charts put the newest month on the left

- **Area:** Analytics
- **Created:** 2026-11-08
- **Source commit:** pending
- **Precondition:** the analytics overview has at least three months of data in its default window.
- **Why:** Devin asked (to-do item 6) for the Time-Series Line Chart and the MoM Change Distribution to show the
  most recent month first; this guards against the charts drifting back to oldest-first and against the chart and
  its preview table disagreeing about which month is newest.
- **Steps:**
  1. Open `/analytics/` and scroll to "Trend Visuals".
  2. In "Time-Series Line Chart", read the month labels under the axis from left to right.
  3. Scroll to "Time-Series Preview" and find the newest month (the last row) and its "MoM Change %".
  4. In "MoM Change Distribution", look at the leftmost bar.
  5. Reload with `?refresh_cache=1` and repeat steps 2 and 4.
- **Pass:** The leftmost month label is the newest month in the preview table and the labels run backwards in time
  to the right; the leftmost MoM bar is green when the newest month's MoM % is positive and red when it is negative;
  the "Peak" and "Range" captions still show; the result is the same after the reload.
- **Fail:** The leftmost label is the oldest month; labels out of order; the leftmost bar's colour disagrees with
  the newest month's MoM %; either chart is blank or shows "No ... available" while the preview table has rows.
```

## E. How to find charts like this

- **SVG charts** are empty `<svg id="...">` tags in a template, drawn by a script in `webapp/static/js/`. Grep the
  templates for `<svg` and the scripts for the `id`; a time axis shows up as `row.month` or a `month` label in the
  render function.
- **Chart.js charts** exist only in the local Viz Lab: `<canvas>` in `experimental/viz_lab.html`, drawn by
  `createChart(...)` in `experimental_viz_lab.js`. Look at each config's `labels`.
- **CSS charts** are `<div>` bars whose `style="height: {{ ... }}%"` is computed in Jinja; today only the lobbying
  `mini-timeline`.
- **Order lives in the data function**, almost always ascending: SQL `ORDER BY month_key` or Python
  `sorted(...)` on `YYYY-MM` keys. A newest-first change belongs in the render step (JS reverse or Jinja `|reverse`),
  after any `[-N:]` slice that keeps the latest N, and never in the function itself, because the moving average and
  MoM % are computed in time order and the JSON API shares the function.
- **New time charts:** decide newest-left vs oldest-left when adding one and note it in this document.
