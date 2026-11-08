"""The analytics overview's Time-Series Line Chart and MoM Change Distribution draw the newest month first (to-do
item 6, .claude/plans/chart-date-order.md).

``webapp/static/js/analytics_visuals.js`` runs under ``node`` against the DOM stand-in (``tests/graph_js_harness.py``)
with the two overview SVGs and the JSON script tag it reads; the script renders on load.  Every plotted point
(``circle``) and every MoM bar (``rect``) carries ``data-month``; the test orders them by x position and expects the
newest month at the left edge, for the points, the month labels and the bars alike.
"""
from __future__ import annotations

import json

from tests.graph_js_harness import JS_DIR, check_syntax, run_graph_js

MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
ROWS = [
    {
        "month": month,
        "total_amount": 1000.0 * (idx + 1),
        "contribution_count": idx + 1,
        "moving_avg_3": 1000.0 * (idx + 1),
        "mom_change_pct": None if idx == 0 else 10.0 * idx,
    }
    for idx, month in enumerate(MONTHS)
]

BODY = """
const nodes = {};
const make = (tag, id, text) => {
    const n = dom.document.createElementNS("http://www.w3.org/2000/svg", tag);
    n.setAttribute("id", id);
    n.setAttribute("viewBox", "0 0 980 340");
    if (text !== undefined) n.textContent = text;
    nodes[id] = n;
    return n;
};
make("svg", "overview-time-series-svg");
make("svg", "overview-mom-svg");
make("script", "analytics-overview-time-series", %(rows)s);
dom.document.getElementById = (id) => nodes[id] || null;
vm.runInContext(fs.readFileSync(%(script)s, "utf8"), sandbox, { filename: "analytics_visuals.js" });
const byX = (list) => list.sort((a, b) => a[1] - b[1]).map((pair) => pair[0]);
const series = all(nodes["overview-time-series-svg"]);
result = {
    points: byX(series.filter((n) => n.tag === "circle").map((n) => [n.attrs["data-month"] || null, Number(n.attrs.cx)])),
    labels: byX(series.filter((n) => n.tag === "text" && /^\\d{4}-\\d{2}$/.test(n.textContent)).map((n) => [n.textContent, Number(n.attrs.x)])),
    bars: byX(all(nodes["overview-mom-svg"]).filter((n) => n.tag === "rect" && n.attrs["data-month"]).map((n) => [n.attrs["data-month"], Number(n.attrs.x)])),
};
"""


def _render():
    return run_graph_js([], BODY % {
        "rows": json.dumps(json.dumps(ROWS)),
        "script": json.dumps(str(JS_DIR / "analytics_visuals.js")),
    })


def test_script_parses():
    done = check_syntax("analytics_visuals.js")
    assert done.returncode == 0, done.stderr


def test_time_series_points_and_labels_run_newest_first():
    out = _render()
    assert out["points"] == list(reversed(MONTHS)), out
    assert out["labels"][0] == MONTHS[-1], out
    assert out["labels"] == sorted(out["labels"], reverse=True), out


def test_mom_bars_run_newest_first():
    out = _render()
    # The first month has no previous month, so it has no MoM value and no bar.
    assert out["bars"] == list(reversed(MONTHS[1:])), out
