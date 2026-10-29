"""Click drill-down on the Network tab's money flow and vendor network views (.claude/plans/money-flow-drilldown.md,
decisions D2-D5).

``webapp/static/js/entity_views/money_flow.js`` and ``vendor_network.js`` run under ``node`` against the DOM stand-in
(``tests/graph_js_harness.py``) with projections of the harness's small ego payload, after the shared panel helper
``entity_views/focus_panel.js``.  A click on a node fills ``ctx.focusBox`` with an ``h3`` heading, an optional note
(``p``) and a table whose body rows are [name (linked through ``ctx.hrefs``), cells...]; the clicked node's bands or
lines get class ``focused``, the others ``dimmed`` at opacity 0.15 (the ego graph's N19 contract).  Clicking an
aggregate money-flow node ("Individual donors", "Other independent spenders", ...) lists its members from
``money_flow()["members"]``; a capped donor class says how many donors the graph left out and links to the Donors tab
(``ctx.donorsTabUrl``).  Clicking the same node again, the background or pressing Escape clears the panel.
"""
from __future__ import annotations

import copy
import json

from database.entity_network import EDGE_TYPE_DEFINITIONS, ego_force, money_flow, vendor_network
from tests.graph_js_harness import check_syntax, run_graph_js, small_ego_payload

MONEY_SCRIPTS = ["graph_legend.js", "entity_views/focus_panel.js", "entity_views/money_flow.js"]
VENDOR_SCRIPTS = ["graph_legend.js", "entity_views/focus_panel.js", "entity_views/vendor_network.js"]

HREFS = {"committee:1": "/committees/1", "committee:2": "/committees/2", "committee:8": "/committees/8",
         "committee:9": "/committees/9", "candidate:5": "/candidates/5", "donor:ann|lee": "/donors/key/ann|lee",
         "vendor:acme": None}
DONORS_TAB_URL = "/candidates/5/donors?period=all"
ROWS_URL = "/candidates/5/network/rows.json?period=all"
EGO_SCRIPTS = ["graph_legend.js", "entity_views/focus_panel.js", "entity_views/ego_force.js"]

PRELUDE = """
const container = dom.document.createElement("div"), keyBox = dom.document.createElement("div"), focusBox = dom.document.createElement("div");
const ctx = { keyBox, focusBox, hrefs: %(hrefs)s, definitions: %(definitions)s, donorsTabUrl: %(donors)s, rowsUrl: %(rows)s };
const render = (data) => sandbox.EntityViews[%(view)s](container, data, ctx);
const nodes = () => all(container).filter((n) => n.tag === "g");
const bands = () => all(container).filter((n) => n.tag === %(band)s);
const svg = () => all(container).find((n) => n.tag === "svg");
const count = (list, name) => list.filter((n) => n.classList.contains(name)).length;
const state = () => {
    const body = all(focusBox).find((n) => n.tag === "tbody");
    const heading = all(focusBox).find((n) => n.tag === "h3");
    const note = all(focusBox).find((n) => n.tag === "p");
    const drawn = bands();
    return {
        heading: heading ? heading.textContent : null,
        note: note ? note.textContent : null,
        noteLinks: note ? all(note).filter((n) => n.tag === "a").map((a) => [a.textContent, a.attrs.href]) : [],
        columns: all(focusBox).filter((n) => n.tag === "th").map((n) => n.textContent),
        rows: body ? body.children.map((tr) => {
            const link = all(tr.children[0]).find((n) => n.tag === "a");
            return [tr.children[0].textContent, link ? link.attrs.href : null].concat(tr.children.slice(1).map((td) => td.textContent));
        }) : [],
        bands: [count(drawn, "focused"), count(drawn, "dimmed"), drawn.filter((n) => n.attrs.opacity === "0.15").length],
    };
};
const click = (id) => {
    const g = nodes().find((n) => n.attrs["data-node-id"] === id);
    if (!g) { throw new Error("no clickable node with data-node-id=" + id); }
    g.fire("click");
};
const escape = () => dom.document.fire("keydown", { key: "Escape" });
const log = {};
"""

CLEAR = {"heading": None, "note": None, "noteLinks": [], "columns": [], "rows": [], "bands": [0, 0, 0]}


def _run(view: str, data: dict, steps: str, **more: dict):
    """Render ``data`` with ``EntityViews[view]``, then run ``steps`` (JavaScript; ``DATA`` and each ``more`` name are
    bound to their JSON) and return ``log``."""
    scripts, band = {"money_flow": (MONEY_SCRIPTS, "path"), "vendor_network": (VENDOR_SCRIPTS, "line"),
                     "ego": (EGO_SCRIPTS, "line")}[view]
    body = PRELUDE % {"hrefs": json.dumps(HREFS), "definitions": json.dumps(EDGE_TYPE_DEFINITIONS),
                      "donors": json.dumps(DONORS_TAB_URL), "view": json.dumps(view), "band": json.dumps(band),
                      "rows": json.dumps(ROWS_URL)}
    for name, value in {"DATA": data, **more}.items():
        body += f"const {name} = {json.dumps(value)};\n"
    return run_graph_js(scripts, body + "render(DATA);\n" + steps + "\nresult = log;\n")


def _edge(source, target, edge_type, weight, count=1, **extra):
    return {"source": source, "target": target, "edge_type": edge_type, "weight": weight, "count": count,
            **EDGE_TYPE_DEFINITIONS[edge_type], **extra}


def _two_donor_payload(**truncated):
    """The harness payload plus a second individual donor, Kai Moss (250 in 3 rows to the committee, no page)."""
    payload = small_ego_payload()
    payload["nodes"].append({"id": "donor:kai|moss", "label": "Kai Moss", "node_type": "donor", "role": "donor",
                             "total_amount": 250.0})
    payload["edges"].append(_edge("donor:kai|moss", "committee:1", "donor_committee", 250.0, 3))
    payload["summary"]["truncated"].update(truncated)
    return payload


# ---------------------------------------------------------------------------
# Money flow
# ---------------------------------------------------------------------------

def test_clicking_a_donor_class_lists_its_donors_by_dollars_with_their_pages_and_highlights_its_flow():
    """D1/D4: the "Individual donors" node opens "Inside Individual donors" -- one row per donor, dollars and rows into
    the entity, biggest first, linked where the donor has a page.  Its one band is focused, the other three dim.
    Kills: a panel reading the class's links instead of its members; a row per committee; a link for a node without
    a page; a cap note when nothing was capped."""
    log = _run("money_flow", money_flow(_two_donor_payload()), """
log.initial = state();
click("class:individual"); log.clicked = state();
""")
    assert log["initial"] == CLEAR
    clicked = log["clicked"]
    assert clicked["heading"] == "Inside Individual donors"
    assert clicked["columns"] == ["Name", "Amount", "Rows"]
    assert clicked["rows"] == [["Ann Lee", "/donors/key/ann|lee", "$900.00", "1 row"],
                               ["Kai Moss", None, "$250.00", "3 rows"]]
    assert clicked["note"] is None, "nothing was capped, so no cap note"
    assert clicked["bands"] == [1, 3, 3]


def test_a_capped_donor_class_says_how_many_donors_the_graph_left_out_and_links_to_the_donors_tab():
    """D5: the ego graph keeps only the top donors; with 12 dropped the class reads "Top 2 individual donors" and the
    panel must say the list is not every donor, then link onward.  Kills: a capped list presented as complete; a
    "more" count recomputed from the members; a link that is not the entity's Donors tab."""
    log = _run("money_flow", money_flow(_two_donor_payload(donors=12)), """
click("class:individual"); log.clicked = state();
""")
    clicked = log["clicked"]
    assert clicked["heading"] == "Inside Top 2 individual donors"
    assert clicked["note"] == "Top 2 individual donors in this graph; 12 more donors not shown. See all donors"
    assert clicked["noteLinks"] == [["See all donors", DONORS_TAB_URL]]
    assert [row[0] for row in clicked["rows"]] == ["Ann Lee", "Kai Moss"]


def test_the_other_spenders_node_lists_each_folded_spender_with_its_side_and_says_when_spenders_were_capped():
    """With no spender kept, both fold into "Other independent spenders": the panel lists each with for / against.
    When the ego payload also dropped spenders (``truncated.ie_spenders``) the note says how many; no Donors link
    there.  Kills: sides lost in the fold; the donor note or link on a non-donor group."""
    full = money_flow(small_ego_payload(), max_spenders=0)
    capped = copy.deepcopy(full)
    capped["truncated"]["ie_spenders"] = 3
    log = _run("money_flow", full, """
click("other:spenders"); log.full = state();
render(CAPPED); click("other:spenders"); log.capped = state();
""", CAPPED=capped)
    assert log["full"]["heading"] == "Inside Other independent spenders"
    assert log["full"]["columns"] == ["Name", "Amount", "Rows", "Side"]
    assert log["full"]["rows"] == [["Pro Pat PAC", "/committees/9", "$700.00", "1 row", "for"],
                                   ["Stop Pat PAC", "/committees/8", "$300.00", "1 row", "against"]]
    assert log["full"]["note"] is None
    assert log["full"]["bands"] == [2, 2, 2]
    assert log["capped"]["note"] == "2 independent spenders grouped here; 3 more independent spenders not in this graph."
    assert log["capped"]["noteLinks"] == []


def test_clicking_a_committee_or_a_candidate_lists_its_flows_in_the_keys_language():
    """D2: a plain node lists its links -- the other end (linked where it has a page), dollars, rows and the flow as
    the key names it.  Kills: flows of the wrong end; IE sides dropped; flow names the key below does not use."""
    log = _run("money_flow", money_flow(small_ego_payload()), """
click("committee:1"); log.committee = state();
click("candidate:5"); log.candidate = state();
log.key = keyBox.textContent;
""")
    committee, candidate = log["committee"], log["candidate"]
    assert committee["heading"] == "Flows of Friends of Pat"
    assert committee["columns"] == ["Name", "Amount", "Rows", "Flow"]
    assert committee["rows"] == [["Individual donors", None, "$900.00", "1 row", "Contribution"],
                                 ["Acme Print", None, "$400.00", "1 row", "Payment to vendor"]]
    assert committee["bands"] == [2, 2, 2]
    assert candidate["heading"] == "Flows of Pat Roe"
    assert candidate["rows"] == [["Pro Pat PAC", "/committees/9", "$700.00", "1 row", "Independent expenditure, for"],
                                 ["Stop Pat PAC", "/committees/8", "$300.00", "1 row", "Independent expenditure, against"]]
    assert candidate["bands"] == [2, 2, 2]
    for row in committee["rows"] + candidate["rows"]:
        assert row[-1] in log["key"]


def test_a_second_click_the_background_and_escape_clear_the_money_flow_panel_and_a_rerender_keeps_one_escape_listener():
    """N19 interaction on the sankey: the same node again, the svg background and Escape each clear; another node
    replaces the panel; re-rendering replaces (not adds) the document's Escape listener; hover titles stay."""
    log = _run("money_flow", money_flow(small_ego_payload()), """
click("committee:1"); click("committee:1"); log.again = state();
click("committee:1"); svg().fire("click"); log.background = state();
click("class:individual"); escape(); log.escape = state();
click("committee:1"); click("vendor:acme"); log.switched = state();
render(DATA); render(DATA);
log.listeners = (dom.document.listeners.keydown || []).length;
click("committee:1"); escape(); log.rerendered = state();
log.titles = all(container).filter((n) => n.tag === "title").length;
""")
    assert log["again"] == CLEAR, "clicking the focused node again"
    assert log["background"] == CLEAR, "clicking the svg background"
    assert log["escape"] == CLEAR, "Escape"
    assert log["switched"]["heading"] == "Flows of Acme Print"
    assert log["switched"]["rows"] == [["Friends of Pat", "/committees/1", "$400.00", "1 row", "Payment to vendor"]]
    assert log["switched"]["bands"] == [1, 3, 3]
    assert log["listeners"] == 1
    assert log["rerendered"] == CLEAR
    assert log["titles"] == 6 + 4, "every node and band keeps its hover title"


# ---------------------------------------------------------------------------
# Vendor network
# ---------------------------------------------------------------------------

def _shared_vendor_view():
    """The harness payload plus a second entity committee, Roe Victory Fund, paying Acme Print 150 in 2 rows."""
    payload = small_ego_payload()
    payload["nodes"].append({"id": "committee:2", "label": "Roe Victory Fund", "node_type": "committee", "role": "entity",
                             "total_amount": 150.0})
    payload["edges"].append(_edge("committee:2", "vendor:acme", "committee_vendor", 150.0, 2))
    return vendor_network(payload)


def test_clicking_a_vendor_lists_the_committees_that_paid_it_and_says_when_it_is_shared():
    """D3: "Paid to Acme Print" lists each paying committee with dollars and rows; a vendor paid by two of the entity's
    committees says so.  An unshared vendor has no note.  Kills: a vendor panel listing the vendor's own total only; a
    shared note on every vendor."""
    log = _run("vendor_network", _shared_vendor_view(), """
click("vendor:acme"); log.shared = state();
render(SINGLE); click("vendor:acme"); log.single = state();
""", SINGLE=vendor_network(small_ego_payload()))
    shared = log["shared"]
    assert shared["heading"] == "Paid to Acme Print"
    assert shared["columns"] == ["Name", "Amount", "Rows", "Flow"]
    assert shared["rows"] == [["Friends of Pat", "/committees/1", "$400.00", "1 row", "Payment to vendor"],
                              ["Roe Victory Fund", "/committees/2", "$150.00", "2 rows", "Payment to vendor"]]
    assert shared["note"] == "Shared by 2 of this entity's committees."
    assert shared["bands"] == [2, 2, 2]
    assert log["single"]["rows"] == [["Friends of Pat", "/committees/1", "$400.00", "1 row", "Payment to vendor"]]
    assert log["single"]["note"] is None


def test_clicking_a_committee_a_spender_or_a_candidate_lists_its_side_of_the_vendor_network():
    """D3: a committee lists its vendors, a spender the candidates it spent on, a candidate the spenders for and
    against it -- never "paid to" a candidate.  Kills: a candidate panel worded as payments to the candidate; sides
    lost; rows from edges the node does not touch."""
    log = _run("vendor_network", _shared_vendor_view(), """
click("committee:1"); log.committee = state();
click("committee:9"); log.spender = state();
click("candidate:5"); log.candidate = state();
""")
    assert log["committee"]["heading"] == "Spending by Friends of Pat"
    assert log["committee"]["rows"] == [["Acme Print", None, "$400.00", "1 row", "Payment to vendor"]]
    assert log["committee"]["bands"] == [1, 3, 3]
    assert log["spender"]["heading"] == "Spending by Pro Pat PAC"
    assert log["spender"]["rows"] == [["Pat Roe", "/candidates/5", "$700.00", "1 row", "Independent expenditure, for"]]
    assert log["candidate"]["heading"] == "Independent spending on Pat Roe"
    assert log["candidate"]["rows"] == [
        ["Pro Pat PAC", "/committees/9", "$700.00", "1 row", "Independent expenditure, for"],
        ["Stop Pat PAC", "/committees/8", "$300.00", "1 row", "Independent expenditure, against"]]
    assert log["candidate"]["bands"] == [2, 2, 2]


def test_a_second_click_the_background_and_escape_clear_the_vendor_panel_and_a_rerender_keeps_one_escape_listener():
    log = _run("vendor_network", _shared_vendor_view(), """
click("vendor:acme"); click("vendor:acme"); log.again = state();
click("vendor:acme"); svg().fire("click"); log.background = state();
click("candidate:5"); escape(); log.escape = state();
render(DATA); render(DATA);
log.listeners = (dom.document.listeners.keydown || []).length;
click("vendor:acme"); escape(); log.rerendered = state();
""")
    assert log["again"] == CLEAR, "clicking the focused node again"
    assert log["background"] == CLEAR, "clicking the svg background"
    assert log["escape"] == CLEAR, "Escape"
    assert log["listeners"] == 1
    assert log["rerendered"] == CLEAR


def test_the_shared_focus_panel_script_parses():
    """``node --check`` of the new helper; the three view scripts are checked by tests/test_ego_focus_js.py."""
    done = check_syntax("entity_views/focus_panel.js")
    assert done.returncode == 0, done.stderr


# ---------------------------------------------------------------------------
# Rows buttons (.claude/plans/inkind-rows-and-readable-ego.md, D3): a panel row that stands for ego edges of a
# supported type opens the filed rows behind it (network/rows.json) in a nested table under it.
# ---------------------------------------------------------------------------

# A fetch stand-in whose promise settles synchronously, so the harness's single pass sees the rendered rows.
FETCH = """
const fetched = [];
const settled = (value) => ({ then(ok) { try { const next = ok(value); return next && next.then ? next : settled(next); }
                                         catch (error) { return failed(error); } },
                              catch() { return this; } });
const failed = (error) => ({ then() { return this; }, catch(handle) { handle(error); return settled(undefined); } });
let answer = { ok: true, body: null };
sandbox.fetch = (url) => { fetched.push(url); return settled({ ok: answer.ok, status: answer.ok ? 200 : 500, json: () => answer.body }); };
const bodyRows = () => { const body = all(focusBox).find((n) => n.tag === "tbody"); return body ? body.children : []; };
const buttonOf = (label) => { const tr = bodyRows().find((row) => row.children[0].textContent === label);
                              return tr ? all(tr).find((n) => n.tag === "button") || null : null; };
const detail = () => bodyRows().filter((tr) => tr.classList.contains("edge-rows-detail")).map((tr) => ({
    after: bodyRows()[bodyRows().indexOf(tr) - 1].children[0].textContent,
    columns: all(tr).filter((n) => n.tag === "th").map((n) => n.textContent),
    rows: all(tr).filter((n) => n.tag === "tbody").flatMap((b) => b.children).map((row) => row.children.map((td) => {
        const link = all(td).find((n) => n.tag === "a"); return link ? [td.textContent, link.attrs.href] : td.textContent; })),
    notes: all(tr).filter((n) => n.tag === "p").map((n) => n.textContent),
}));
"""

ROWS = {"rows": [
    {"date": "2026-03-05", "amount": 2000.0, "d2_part": "5A", "description": "Mailing", "counterparty": "Ann Lee",
     "committee_id": 1, "committee_name": "Friends of Pat", "filed_doc_id": 77, "filing_href": "/committees/filing/77"},
    {"date": "2026-02-10", "amount": 30.5, "d2_part": "5A", "description": None, "counterparty": "Ann Lee",
     "committee_id": 1, "committee_name": "Friends of Pat", "filed_doc_id": None, "filing_href": None},
], "total_amount": 2030.5, "total_count": 2, "truncated": False}


def _in_kind_payload():
    payload = _two_donor_payload()
    payload["edges"].append(_edge("donor:ann|lee", "committee:1", "in_kind_donor_committee", 2030.5, 2))
    return payload


def test_a_members_rows_cell_is_a_button_that_lists_its_filed_rows_and_a_second_click_collapses_them():
    """Item 8a: "Inside In-kind contributions" -> Ann Lee, "2 rows" -> the two filed rows (date, amount, part,
    description, a link to the filing when there is one) under her row. The URL is the page's rows_url plus the member,
    no target (her rows into every committee) and the aggregate's edge type. Kills: a Rows cell that is plain text; a
    fetch without the page's scope; a target on a member request; a nested table in the wrong place; a toggle that only
    opens."""
    log = _run("money_flow", money_flow(_in_kind_payload()), FETCH + """
click("class:in_kind");
answer.body = ROWS_BODY;
log.button = buttonOf("Ann Lee") && [buttonOf("Ann Lee").textContent, buttonOf("Ann Lee").attrs["aria-expanded"]];
buttonOf("Ann Lee").fire("click");
log.open = { fetched: fetched.slice(), detail: detail(), expanded: buttonOf("Ann Lee").attrs["aria-expanded"],
             heading: all(focusBox).find((n) => n.tag === "h3").textContent };
buttonOf("Ann Lee").fire("click");
log.closed = { fetched: fetched.length, detail: detail(), expanded: buttonOf("Ann Lee").attrs["aria-expanded"] };
""", ROWS_BODY=ROWS)
    assert log["button"] == ["2 rows", "false"]
    assert log["open"]["fetched"] == [ROWS_URL + "&source=donor%3Aann%7Clee&edge_type=in_kind_donor_committee"]
    assert log["open"]["heading"] == "Inside In-kind contributions", "a click inside the panel must not clear the focus"
    assert log["open"]["expanded"] == "true"
    assert log["open"]["detail"] == [{
        "after": "Ann Lee",
        "columns": ["Date", "Amount", "Type", "Description", "Filing"],
        "rows": [["2026-03-05", "$2,000.00", "5A", "Mailing", ["Filing", "/committees/filing/77"]],
                 ["2026-02-10", "$30.50", "5A", "", ""]],
        "notes": [],
    }]
    assert log["closed"] == {"fetched": 1, "detail": [], "expanded": "false"}


def test_truncated_unsupported_and_failed_row_requests_say_so_in_one_line():
    """Kills: a capped list shown as complete; a silent failure; a broken panel after an error."""
    log = _run("money_flow", money_flow(_in_kind_payload()), FETCH + """
click("class:in_kind");
answer.body = Object.assign({}, ROWS_BODY, { rows: ROWS_BODY.rows.slice(0, 1), total_count: 250, truncated: true });
buttonOf("Ann Lee").fire("click"); log.truncated = detail()[0].notes; buttonOf("Ann Lee").fire("click");
answer.body = { rows: [], unsupported: true };
buttonOf("Ann Lee").fire("click"); log.unsupported = detail()[0].notes; buttonOf("Ann Lee").fire("click");
answer.ok = false;
buttonOf("Ann Lee").fire("click"); log.failed = detail()[0].notes;
""", ROWS_BODY=ROWS)
    assert log["truncated"] == ["Showing 1 of 250 rows."]
    assert log["unsupported"] == ["Row detail is not available for this relationship."]
    assert log["failed"] == ["The rows could not be loaded."]


def test_rows_buttons_appear_only_where_a_panel_row_is_ego_edges_of_a_supported_type():
    """Members of donor classes, in-kind, Other transfers and Other vendors, and every vendor-network or ego-list row of a
    contribution, in-kind, transfer or vendor edge, get a button; independent expenditures and "raises for candidate"
    (Phase B) and flows to or from an aggregate node do not. Vendor-network and ego rows name both ends."""
    flow = _run("money_flow", money_flow(_in_kind_payload()), FETCH + """
click("class:individual"); log.individual = ["Ann Lee", "Kai Moss"].map((name) => !!buttonOf(name));
click("committee:1"); log.committee = bodyRows().map((tr) => [tr.children[0].textContent, !!all(tr).find((n) => n.tag === "button")]);
""")
    assert flow["individual"] == [True, True]
    assert dict(flow["committee"]) == {"Individual donors": False, "In-kind contributions": False, "Acme Print": True}, \
        "a flow to a class or Other node is not one edge; a kept vendor's flow is"
    vendor = _run("vendor_network", vendor_network(small_ego_payload()), FETCH + """
answer.body = { rows: [], total_amount: 0, total_count: 0, truncated: false };
click("vendor:acme"); buttonOf("Friends of Pat").fire("click"); log.fetched = fetched.slice();
click("candidate:5"); log.spenders = ["Pro Pat PAC", "Stop Pat PAC"].map((name) => !!buttonOf(name));
""")
    assert vendor["fetched"] == [ROWS_URL + "&source=vendor%3Aacme&target=committee%3A1&edge_type=committee_vendor"]
    assert vendor["spenders"] == [False, False]
    ego = _run("ego", ego_force(small_ego_payload()), FETCH + """
answer.body = { rows: [], total_amount: 0, total_count: 0, truncated: false };
click("committee:1");
log.columns = all(focusBox).filter((n) => n.tag === "th").map((n) => n.textContent);
log.buttons = bodyRows().map((tr) => [tr.children[0].textContent, !!all(tr).find((n) => n.tag === "button")]);
buttonOf("Ann Lee").fire("click"); log.fetched = fetched.slice();
""")
    assert ego["columns"] == ["Name", "Relationship", "Amount", "Rows"]
    assert ego["buttons"] == [["Ann Lee", True], ["Pat Roe", False], ["Acme Print", True]]
    assert ego["fetched"] == [ROWS_URL + "&source=committee%3A1&target=donor%3Aann%7Clee&edge_type=donor_committee"]
