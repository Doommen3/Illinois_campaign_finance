"""The site-wide graph key (plan .claude/plans/race-sort-and-entity-networks.md, decision N18).

Devin could not tell what a dashed green line into a candidate meant: independent spending AGAINST her was drawn in the
same green as spending for her, only dashed.  One style map (``GRAPH_STYLES`` in ``webapp/static/js/graph_legend.js``)
now colours every graph and ``renderGraphKey`` prints the key under it.  Pinned here:

  * every template that mounts a graph loads graph_legend.js before the renderer and carries the key containers the
    renderer fills (the ids are tied to the JS so a rename cannot silently drop a key);
  * GRAPH_STYLES covers every edge type the entity network and the global graphs emit, and the two independent
    expenditure styles cannot be mistaken for each other (solid green for, dashed red against);
  * renderGraphKey lists only the types it is given, with real SVG line swatches, and says "against" in words;
  * each entity view (money flow, vendor network, ego graph) draws opposing lines with the shared style and fills the
    key from the real projections of ``database.entity_network``.
"""
from __future__ import annotations

import json
import re

import pytest

from database.analytics import _EDGE_TYPE_SEMANTICS
from database.entity_network import EDGE_TYPE_DEFINITIONS, ego_force, money_flow, vendor_network
from tests.graph_js_harness import JS_DIR, ROOT, run_graph_js

TEMPLATES = ROOT / "webapp" / "templates"
# template -> (renderer scripts, key container ids the renderers fill). The networks heatmap has no key: it draws a
# value grid with its own Lower/Higher scale and no edge types.
MOUNTS = {
    "entity/network.html": (["entity_views/money_flow.js", "entity_views/vendor_network.js", "entity_views/ego_force.js"],
                            ["entity-network-key"]),
    "analytics/networks.html": (["analytics_networks.js"],
                                ["network-key", "sankey-key", "vendor-key", "combined-key", "overlap-key", "lobbying-key",
                                 "darkmoney-key", "custom-sankey-key"]),
    "analytics/relationships.html": (["relationship_graphs.js"],
                                     [f"{key}-key" for key in ("donor-cogiving", "committee-similarity", "candidate-state",
                                                               "candidate-federal", "candidate-combined",
                                                               "lobbying-influence", "ecosystem-527")]),
}


@pytest.mark.parametrize("template", sorted(MOUNTS))
def test_graph_templates_load_the_legend_first_and_carry_the_key_containers(template):
    """Kills: a renderer loaded before graph_legend.js (it reads window.GraphKey at load); a graph without its key
    container; a key id the renderer does not know."""
    renderers, key_ids = MOUNTS[template]
    html = (TEMPLATES / template).read_text()
    legend = html.index("js/graph_legend.js")
    for renderer in renderers:
        assert legend < html.index(f"js/{renderer}"), f"graph_legend.js must load before {renderer}"
    source = "".join((JS_DIR / renderer).read_text() for renderer in renderers) + html  # the entity page looks its box up inline
    for key_id in key_ids:
        assert re.search(rf'class="graph-key[^"]*"\s+id="{key_id}"', html), f"{template} lacks <div class=graph-key id={key_id}>"
        if template != "analytics/relationships.html":  # relationship_graphs.js derives its ids from the spec keys
            assert re.search(rf'getElementById\("{key_id}"\)|drawKey\("{key_id}", \{{|keyId: "{key_id}"', source), f"nothing fills {key_id}"
    if template == "analytics/relationships.html":
        assert re.findall(r'^\s+key: "([a-z0-9-]+)",$', source, re.M) == [key_id[:-4] for key_id in key_ids]


def test_graph_styles_cover_every_edge_type_the_site_draws():
    """Kills: an edge type added to the entity network or a global graph without a style (it would draw in the fallback
    grey and be missing from the key)."""
    keys = set(run_graph_js(["graph_legend.js"], "result = Object.keys(sandbox.GRAPH_STYLES);"))
    wanted = set(EDGE_TYPE_DEFINITIONS) | set(_EDGE_TYPE_SEMANTICS) | {
        "donor_cogiving", "committee_similarity", "candidate_competition",
        "independent_expenditure:S", "independent_expenditure:O",
        "ie_committee_candidate", "ie_committee_candidate:S", "ie_committee_candidate:O",
    }
    assert wanted <= keys, sorted(wanted - keys)


def test_independent_expenditure_for_and_against_are_visibly_different_in_every_family():
    """Kills: the original bug -- spending against drawn in the colour of spending for.  Entity-network edge types are
    pairwise distinct in (colour, dash), and for / against differ in both."""
    styles = run_graph_js(["graph_legend.js"], "result = sandbox.GRAPH_STYLES;")
    for family in ("independent_expenditure", "ie_committee_candidate"):
        support, oppose = styles[f"{family}:S"], styles[f"{family}:O"]
        assert (support["color"], support["dash"]) == ("#38a169", None)
        assert oppose["color"] == "#e53e3e" and oppose["dash"]
        assert support["color"] != oppose["color"] and support["dash"] != oppose["dash"]
    entity = {styles[key]["color"] + "|" + str(styles[key]["dash"]) for key in (
        "donor_committee", "in_kind_donor_committee", "transfer_in", "committee_vendor", "committee_candidate",
        "independent_expenditure:S", "independent_expenditure:O")}
    assert len(entity) == 7


def test_render_graph_key_lists_only_what_it_is_given_and_says_against_in_words():
    """Kills: a key that lists every style; a swatch that is not a real line (dashed must show as dashed); an IE row
    without the for / against wording; definitions from the payload ignored for an unsigned type."""
    result = run_graph_js(["graph_legend.js"], """
        const box = dom.document.createElement("div");
        sandbox.renderGraphKey(box, {
            edgeTypes: ["independent_expenditure:O", "donor_committee", "independent_expenditure:S"],
            definitions: { donor_committee: { edge_label: "Gift", edge_description: "Money in." },
                           independent_expenditure: { edge_label: "Independent expenditure", edge_description: "ignored for signed rows" } },
            nodeTypes: ["candidate", "donor", "candidate"],
        });
        const rows = all(box).filter((n) => n.tag === "li");
        result = { text: box.textContent, lines: all(box).filter((n) => n.tag === "line").map((n) => n.attrs),
                   rows: rows.length, nodeRows: rows.filter((r) => !all(r).some((n) => n.tag === "line")).length };
    """)
    text = result["text"].lower()
    assert "spent for the candidate by an outside committee" in text
    assert "spent against the candidate by an outside committee (red, dashed)" in text
    assert "gift" in text and "money in." in text and "ignored for signed rows" not in text
    assert "in-kind" not in text and "transfer" not in text and "vendor" not in text
    assert [line["stroke"] for line in result["lines"]] == ["#2563eb", "#38a169", "#e53e3e"]  # donor, for, against (row order)
    assert [line.get("stroke-dasharray") for line in result["lines"]] == [None, None, "6 4"]
    assert (result["rows"], result["nodeRows"]) == (5, 2)  # three line rows, candidate + donor once each


def test_render_graph_key_with_nothing_to_list_empties_the_container():
    result = run_graph_js(["graph_legend.js"], """
        const box = dom.document.createElement("div");
        sandbox.renderGraphKey(box, { edgeTypes: ["donor_committee"] });
        sandbox.renderGraphKey(box, { edgeTypes: [], nodeTypes: [] });
        result = box.children.length;
    """)
    assert result == 0


def _ego():
    def edge(source, target, edge_type, weight, **extra):
        return {"source": source, "target": target, "edge_type": edge_type, "weight": weight, "count": 1,
                **EDGE_TYPE_DEFINITIONS[edge_type], **extra}

    def node(node_id, node_type, role, total, label):
        return {"id": node_id, "label": label, "node_type": node_type, "role": role, "total_amount": total}

    edges = [
        edge("donor:ann|lee", "committee:1", "donor_committee", 900.0),
        edge("committee:1", "vendor:acme", "committee_vendor", 400.0),
        edge("committee:9", "candidate:5", "independent_expenditure", 700.0, support_oppose="S", signed_weight=700.0),
        edge("committee:8", "candidate:5", "independent_expenditure", 300.0, support_oppose="O", signed_weight=-300.0),
        edge("committee:1", "candidate:5", "committee_candidate", 900.0),
    ]
    nodes = [
        node("committee:1", "committee", "entity", 2200.0, "Friends of Pat"), node("candidate:5", "candidate", "candidate", 1900.0, "Pat Roe"),
        node("donor:ann|lee", "donor", "donor", 900.0, "Ann Lee"), node("vendor:acme", "vendor", "vendor", 400.0, "Acme Print"),
        node("committee:9", "committee", "ie_spender", 700.0, "Pro Pat PAC"), node("committee:8", "committee", "ie_spender", 300.0, "Stop Pat PAC"),
    ]
    summary = {"truncated": {"donors": 0, "vendors": 0, "transfers": 0, "ie_spenders": 0}, "edge_type_definitions": EDGE_TYPE_DEFINITIONS}
    return {"nodes": nodes, "edges": edges, "summary": summary}


# view -> (projection, script, key rows its payload must produce, rows it must not)
VIEWS = {
    "money_flow": (money_flow, "entity_views/money_flow.js", ["contribution", "payment to vendor"], ["raises for candidate"]),
    "vendor_network": (vendor_network, "entity_views/vendor_network.js", ["payment to vendor"], ["contribution", "raises for candidate"]),
    "ego": (ego_force, "entity_views/ego_force.js", ["contribution", "payment to vendor", "raises for candidate"], []),
}


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_entity_views_draw_opposing_spending_with_the_shared_style_and_fill_the_key(view):
    """Kills: a view that keeps a private colour map (against drawn like for); a view that does not fill ctx.keyBox; a
    key that lists a type the view did not draw (in-kind and transfers are absent from the payload; the money flow
    leaves the committee_candidate link out and the vendor network keeps only vendor and independent-spending edges)."""
    project, script, present, absent = VIEWS[view]
    data = project(_ego())
    result = run_graph_js(["graph_legend.js", script], f"""
        const container = dom.document.createElement("div"), keyBox = dom.document.createElement("div");
        sandbox.EntityViews[{json.dumps(view)}](container, {json.dumps(data)}, {{ keyBox, definitions: {json.dumps(EDGE_TYPE_DEFINITIONS)} }});
        const drawn = all(container).filter((n) => ["path", "line"].includes(n.tag) && n.attrs.stroke);
        result = {{ strokes: drawn.map((n) => [n.attrs.stroke, n.attrs["stroke-dasharray"] || null]), key: keyBox.textContent }};
    """)
    assert ["#e53e3e", "6 4"] in result["strokes"] and ["#38a169", None] in result["strokes"]
    key = result["key"].lower()
    assert "spent against the candidate by an outside committee" in key and "spent for the candidate by an outside committee" in key
    assert "in-kind" not in key and "transfer" not in key
    for label in present:
        assert label in key, label
    for label in absent:
        assert label not in key, label
