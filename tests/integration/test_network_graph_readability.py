"""N8 probe: the ego graph on the Network tab is readable at load (plan .claude/plans/race-sort-and-entity-networks.md N7/N8,
.claude/plans/inkind-rows-and-readable-ego.md D5).

Runs a real browser (Playwright, chromium, 1200 x 700) against a running server (``PROBE_BASE_URL``, default
``http://127.0.0.1:5053``; start one with ``DASHBOARD_PREWARM_ENABLED=false python3 run.py runserver --port 5053``) on one
candidate and one state race, opens the Ego graph view, waits for the renderer's ``svg[data-settled="1"]`` and counts, in
the page:

  (i)   label_intersections   pairs of visible labels whose boxes overlap;
  (ii)  circle_overlaps       pairs of node circles closer than the sum of their radii;
  (iii) outside_container     circles and visible labels not wholly inside the graph container;
  (iv)  small_labels          visible labels drawn under 11 px on screen;
  (v)   hover_failures        sampled unlabelled nodes whose label does not show while hovered.

Every count must be zero. A node's label is a ``text`` inside its ``g[data-node-id]``.
"""
from __future__ import annotations

import json
import os
import urllib.request

import pytest

BASE_URL = os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:5053").rstrip("/")
PAGES = [
    pytest.param("/candidates/13505/network?view=ego", id="candidate-13505"),
    pytest.param("/analytics/state-races/alderperson-city-evanston/network?view=ego", id="race-evanston"),
]
VIEWPORT = {"width": 1200, "height": 700}
HOVER_SAMPLES = 5

# Visible: rendered, not hidden, not transparent, with a non-empty box.
_COUNTS_JS = """
() => {
    const box = document.getElementById("entity-network-graph");
    const svg = box.querySelector("svg");
    const frame = box.getBoundingClientRect();
    const shown = (el) => {
        for (let node = el; node && node !== box; node = node.parentNode) {
            const style = getComputedStyle(node);
            if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) { return false; }
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };
    const labels = Array.from(svg.querySelectorAll("g[data-node-id] text")).filter(shown);
    const circles = Array.from(svg.querySelectorAll("g[data-node-id] circle")).filter(shown);
    const rects = labels.map((t) => t.getBoundingClientRect());
    let labelHits = 0;
    for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) {
            const a = rects[i], b = rects[j];
            if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) { labelHits++; }
        }
    }
    const discs = circles.map((c) => { const r = c.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, r: r.width / 2 }; });
    let circleHits = 0;
    for (let i = 0; i < discs.length; i++) {
        for (let j = i + 1; j < discs.length; j++) {
            const a = discs[i], b = discs[j];
            if (Math.hypot(a.x - b.x, a.y - b.y) < a.r + b.r - 0.5) { circleHits++; }
        }
    }
    const outside = labels.concat(circles).filter((el) => {
        const r = el.getBoundingClientRect();
        return r.left < frame.left - 1 || r.right > frame.right + 1 || r.top < frame.top - 1 || r.bottom > frame.bottom + 1;
    }).length;
    const small = labels.filter((t) => parseFloat(getComputedStyle(t).fontSize) * Math.abs(t.getScreenCTM().a) < 11 - 0.01).length;
    const unlabelled = Array.from(svg.querySelectorAll("g[data-node-id]"))
        .filter((g) => !Array.from(g.querySelectorAll("text")).some(shown)).map((g) => g.getAttribute("data-node-id"));
    return { label_intersections: labelHits, circle_overlaps: circleHits, outside_container: outside, small_labels: small,
             visible_labels: labels.length, nodes: circles.length, unlabelled: unlabelled };
}
"""

_LABEL_SHOWN_JS = """
(id) => {
    const g = Array.from(document.querySelectorAll("#entity-network-graph g[data-node-id]")).find((n) => n.getAttribute("data-node-id") === id);
    return Array.from(g.querySelectorAll("text")).some((t) => {
        const style = getComputedStyle(t), rect = t.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0 && rect.width > 0;
    });
}
"""


@pytest.fixture(scope="module")
def browser():
    try:
        urllib.request.urlopen(BASE_URL + "/", timeout=10)
    except OSError as error:
        pytest.fail(f"no server at {BASE_URL} ({error}); start one on that port first")
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        launched = driver.chromium.launch()
        yield launched
        launched.close()


def measure(browser, path: str) -> dict:
    page = browser.new_page(viewport=VIEWPORT)
    try:
        page.goto(BASE_URL + path, wait_until="domcontentloaded")
        page.click('.view-switch-button[data-view="ego"]')
        page.wait_for_selector("#entity-network-graph svg g[data-node-id]", timeout=30000)
        try:
            page.wait_for_selector('#entity-network-graph svg[data-settled="1"]', timeout=10000)
            settled = True
        except Exception:  # noqa: BLE001  (Playwright's TimeoutError; the count below records it)
            settled = False
        counts = page.evaluate(_COUNTS_JS)
        failures = 0
        sampled = counts.pop("unlabelled")[:HOVER_SAMPLES]
        for node_id in sampled:
            circle = page.locator(f'#entity-network-graph g[data-node-id="{node_id}"] circle').first
            circle.hover(force=True)
            page.wait_for_timeout(100)
            if not page.evaluate(_LABEL_SHOWN_JS, node_id):
                failures += 1
            page.mouse.move(1, 1)
        counts.update(hover_failures=failures, hover_sampled=len(sampled), settled=settled)
        return counts
    finally:
        page.close()


@pytest.mark.integration
@pytest.mark.parametrize("path", PAGES)
def test_the_ego_graph_is_readable_at_load(browser, path):
    counts = measure(browser, path)
    print(f"\nN8 {path}: {json.dumps(counts, sort_keys=True)}")
    problems = {name: counts[name] for name in
                ("label_intersections", "circle_overlaps", "outside_container", "small_labels", "hover_failures")
                if counts[name]}
    assert counts["settled"], f"the renderer never set svg[data-settled=1]: {counts}"
    assert not problems, f"unreadable graph on {path}: {problems} (all counts: {counts})"
