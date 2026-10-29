"""Click-to-focus on the ego graph (plan .claude/plans/race-sort-and-entity-networks.md, decision N19).

``webapp/static/js/entity_views/ego_force.js`` runs under ``node`` against the DOM stand-in with the small ego payload of
``tests.graph_js_harness``.  A click on a node keeps it, its incident edges and its neighbours at full opacity (class
``focused``), dims the rest (class ``dimmed``, opacity 0.15) and lists the neighbours by edge weight under "Connected to
<node>"; clicking the focused node again, the background or pressing Escape clears everything.  The hover tooltips
(``<title>`` children) stay.
"""
from __future__ import annotations

import json

import pytest

from database.entity_network import EDGE_TYPE_DEFINITIONS, ego_force
from tests.graph_js_harness import check_syntax, run_graph_js, small_ego_payload

HREFS = {"committee:1": "/committees/1", "committee:8": "/committees/8", "committee:9": "/committees/9",
         "candidate:5": "/candidates/5", "donor:ann|lee": "/donors/key/ann|lee", "vendor:acme": None}

HARNESS = f"""
const container = dom.document.createElement("div"), keyBox = dom.document.createElement("div"), focusBox = dom.document.createElement("div");
sandbox.EntityViews.ego(container, {json.dumps(ego_force(small_ego_payload()))},
    {{ keyBox, focusBox, hrefs: {json.dumps(HREFS)}, definitions: {json.dumps(EDGE_TYPE_DEFINITIONS)} }});
const nodes = all(container).filter((n) => n.tag === "g"), edges = all(container).filter((n) => n.tag === "line");
const svg = all(container).find((n) => n.tag === "svg");
const count = (list, name) => list.filter((n) => n.classList.contains(name)).length;
const state = () => {{
    const body = all(focusBox).find((n) => n.tag === "tbody");
    const heading = all(focusBox).find((n) => n.tag === "h3");
    return {{
        nodes: [count(nodes, "focused"), count(nodes, "dimmed"), nodes.filter((n) => n.attrs.opacity === "0.15").length],
        edges: [count(edges, "focused"), count(edges, "dimmed"), edges.filter((n) => n.attrs.opacity === "0.15").length],
        focusedIds: nodes.filter((n) => n.classList.contains("focused")).map((n) => n.attrs["data-node-id"]).sort(),
        heading: heading ? heading.textContent : null,
        rows: body ? body.children.map((tr) => {{
            const link = all(tr).find((n) => n.tag === "a");
            return [tr.children[0].textContent, link ? link.attrs.href : null, tr.children[1].textContent, tr.children[2].textContent];
        }}) : [],
    }};
}};
const click = (id) => nodes.find((n) => n.attrs["data-node-id"] === id).fire("click");
const log = {{}};
log.initial = state();
click("candidate:5");   log.candidate = state();
click("candidate:5");   log.again = state();
click("committee:1");   log.committee = state();
svg.fire("click");      log.background = state();
click("vendor:acme");   log.vendor = state();
dom.document.fire("keydown", {{ key: "Escape" }});  log.escape = state();
click("candidate:5");   click("committee:9");  log.switched = state();
log.titles = all(container).filter((n) => n.tag === "title").length;
log.note = all(container).filter((n) => n.tag === "p").map((n) => n.textContent).join(" ");
log.key = keyBox.textContent;
result = log;
"""

CLEAR = {"nodes": [0, 0, 0], "edges": [0, 0, 0], "focusedIds": [], "heading": None, "rows": []}


def test_clicking_a_node_focuses_it_dims_the_rest_and_lists_its_neighbours_by_weight():
    log = run_graph_js(["graph_legend.js", "entity_views/focus_panel.js", "entity_views/ego_force.js"], HARNESS)

    assert log["initial"] == CLEAR
    # candidate:5 touches three edges (the committee's raise, one for, one against); the donor and the vendor are dimmed
    candidate = log["candidate"]
    assert candidate["focusedIds"] == ["candidate:5", "committee:1", "committee:8", "committee:9"]
    assert candidate["nodes"] == [4, 2, 2] and candidate["edges"] == [3, 2, 2]
    assert candidate["heading"] == "Connected to Pat Roe"
    assert candidate["rows"] == [
        ["Friends of Pat", "/committees/1", "Raises for candidate", "$900.00"],
        ["Pro Pat PAC", "/committees/9", "Independent expenditure, for", "$700.00"],
        ["Stop Pat PAC", "/committees/8", "Independent expenditure, against", "$300.00"],
    ]


def test_the_list_links_donors_and_committees_and_leaves_unlinked_nodes_as_text_with_ties_by_label():
    log = run_graph_js(["graph_legend.js", "entity_views/focus_panel.js", "entity_views/ego_force.js"], HARNESS)
    committee = log["committee"]
    assert committee["heading"] == "Connected to Friends of Pat"
    assert committee["rows"] == [
        ["Ann Lee", "/donors/key/ann|lee", "Contribution", "$900.00"],   # ties at $900: label order
        ["Pat Roe", "/candidates/5", "Raises for candidate", "$900.00"],
        ["Acme Print", None, "Payment to vendor", "$400.00"],               # no page for a vendor
    ]
    assert committee["nodes"] == [4, 2, 2] and committee["edges"] == [3, 2, 2]


def test_a_second_click_the_background_and_escape_each_clear_the_focus_and_the_list():
    log = run_graph_js(["graph_legend.js", "entity_views/focus_panel.js", "entity_views/ego_force.js"], HARNESS)
    assert log["again"] == CLEAR, "clicking the focused node again"
    assert log["background"] == CLEAR, "clicking the svg background"
    assert log["vendor"]["heading"] == "Connected to Acme Print" and log["vendor"]["nodes"] == [2, 4, 4]
    assert log["escape"] == CLEAR, "Escape"


def test_a_click_on_another_node_replaces_the_focus_and_the_list_and_the_tooltips_and_key_stay():
    log = run_graph_js(["graph_legend.js", "entity_views/focus_panel.js", "entity_views/ego_force.js"], HARNESS)
    switched = log["switched"]
    assert switched["heading"] == "Connected to Pro Pat PAC"
    assert switched["focusedIds"] == ["candidate:5", "committee:9"] and switched["rows"] == [
        ["Pat Roe", "/candidates/5", "Independent expenditure, for", "$700.00"]]
    assert log["titles"] == 6 + 5, "every node and edge keeps its hover title"
    assert "press Esc to clear" in log["note"]
    # the focus list speaks the key's language
    for _, _, kind, _ in log["candidate"]["rows"]:
        assert kind in log["key"]


@pytest.mark.parametrize("script", ["graph_legend.js", "analytics_networks.js", "relationship_graphs.js", "entity_views/money_flow.js",
                                    "entity_views/vendor_network.js", "entity_views/ego_force.js"])
def test_every_graph_script_the_key_touches_parses(script):
    """``node --check``.  analytics_networks.js and relationship_graphs.js run only in a browser (no harness executes
    them), so a syntax slip in either would blank every graph on /analytics/networks or /analytics/relationships."""
    done = check_syntax(script)
    assert done.returncode == 0, done.stderr
