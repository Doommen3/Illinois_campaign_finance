# Entity networks: the Network tab of a candidate or race

The Network tab (`/candidates/<id>/network`, `/analytics/state-races/<race_key>/network`) draws the money network
around one entity's committees. This page describes the data contract, the three views, the click drill-down panel
and the cache rules. Code: `database/entity_network.py` (payload and projections), `webapp/routes/entity_analytics.py`
(routes and caching), `webapp/templates/entity/network.html` and `webapp/static/js/entity_views/*.js` (rendering).
The panel's Rows buttons open the filed rows behind an edge (see "Edge rows" below).

## The ego payload

`get_entity_network(conn, entity, date_from=, date_to=, caps=)` returns `{entity, nodes, edges, centrality, summary}`.
Nodes are the entity's own committees (`role: "entity"`) and candidates (`role: "candidate"`) plus their one-hop
neighbours: donors (`donor:<donor key>`), transfer sources (`committee:<id>` when the payer name matches exactly one
committee, else `transfer:<hash>`), vendors (`vendor:<hash>`) and independent spenders. Each node has `id`, `label`,
`node_type`, `role` and `total_amount` (the sum of its edge weights), and optionally `group_label` and `redacted`.
Each edge has `source`, `target`, `edge_type`, `weight` (dollars), `count` (rows) and the labels of its type, plus
`support_oppose` (`S` / `O`) on independent expenditures. The six edge types are `donor_committee`,
`in_kind_donor_committee`, `transfer_in`, `committee_vendor`, `independent_expenditure` and `committee_candidate`; their
labels and descriptions are in `summary.edge_type_definitions`. Neighbours are capped per class by `DEFAULT_CAPS`
(donors 40, vendors 25, transfers 15, independent spenders 15; in-kind donors count against the donor cap). The
top-N by total survive, and `summary.truncated` (`donors`, `vendors`, `transfers`, `ie_spenders`) counts the nodes
each class lost. The entity's own committees and candidates are never capped.

## The three views

`network.json?view=` serves one of three views. Each view is built from the one cached ego payload; none reads the
database itself.

- **`ego`**: the ego payload as is, plus `view: "ego"`. `ego_force.js` draws it with the readable force renderer
  (see "The ego renderer" below), or on a radial layout when d3 is not loaded.
  (`ego_force(payload)` in the module returns copies of the nodes and edges.)
- **`money_flow`** (`money_flow(payload)`): a three-column sankey. Column 0 holds the sources: donor classes
  (`class:individual`, `class:organization`, read from whether the donor filed a first name), `class:in_kind`,
  transfer sources and independent spenders. Column 1 holds the entity's committees and the candidates that
  independent spending names. Column 2 holds the vendors. The top 10 transfer sources, spenders and vendors keep
  their own node; the rest fold into `other:transfers`, `other:spenders` and `other:vendors`, and `folded` counts
  them. `committee_candidate` edges are not flows and are left out. Money between the entity's own committees is
  left out too and reported in `folded.internal`. When donors were capped, the class nodes read "Top N individual
  donors" / "Top N organization donors". The view returns `nodes`, `links`, `totals`, `folded`, `truncated` (passed
  through from the payload) and `members`.
- **`vendor_network`** (`vendor_network(payload)`): a bipartite graph of payers (the entity's committees and the
  independent spenders) against payees (vendors and the candidates spent on), from the `committee_vendor` and
  `independent_expenditure` edges only. A vendor paid by two or more of the entity's committees has `shared: true`,
  with `committee_ids` and `committee_count`; `shared_vendors` lists them. Limit: the payload holds only the entity
  committees' own payees, so a vendor shared with an independent spender's payees is not visible.

### `members`: what is inside a group node

`money_flow()["members"]` maps every aggregate node id (`class:*` and `other:*`) to the list of ego nodes folded into
it. No other node has an entry, and an empty graph gives `{}`. Each member is
`{id, label, node_type, amount, count}`, with `support_oppose` on spender members. `amount` and `count` are that
member's edges into that aggregate, summed over the entity's committees and rounded to cents. Lists are sorted by
amount descending, then label, then id, then side.

- **Reconciliation.** For every aggregate node, the members' amounts sum to the node's `amount` to the cent, and their
  counts sum to the rows of the node's links (`tests/test_entity_network.py`).
- **One donor, two lists.** A donor with both cash and in-kind money is a member of its class and of
  `class:in_kind`, each with that edge type's dollars only.
- **Both-sides spender.** A spender folded into `other:spenders` that spent both for and against is two members, one
  per `support_oppose`, as the links split.
- **Capped graph.** Members are complete for the graph, not for the entity: donors beyond the ego cap are not in
  the payload, so they are not members. The panel says so (below).

## The drill-down panel

Clicking a node fills the panel under the graph (`#entity-network-focus`). The clicked node's bands or lines get
class `focused`; the rest get class `dimmed` at opacity 0.15. Names link through the page's node links
(`ctx.hrefs`): committees, candidates and non-redacted donors with itemized (1A) rows into the entity have pages;
vendors, `transfer:` payers and in-kind-only (5A) donors do not, because a donor page lists part-1 contributions
only and the Donors tab lists no in-kind rows (fix-8b F2a).
Flow names come from the graph key's labels (`js/graph_legend.js`), for example "Contribution", "Payment to vendor",
"Independent expenditure, for".

| View | Node clicked | Heading | Columns |
|---|---|---|---|
| Money flow | a group node (`class:*`, `other:*`) | Inside *label* | Name, Amount, Rows (+ Side, for / against, on Other independent spenders) |
| Money flow | any other node | Flows of *label* | Name, Amount, Rows, Flow |
| Vendor network | a vendor | Paid to *label* | Name, Amount, Rows, Flow |
| Vendor network | a committee or spender | Spending by *label* | Name, Amount, Rows, Flow |
| Vendor network | a candidate | Independent spending on *label* | Name, Amount, Rows, Flow |
| Ego graph | any node | Connected to *label* | Name, Relationship, Amount, Rows |

Rows are sorted by dollars, ties by name. Row counts read "1 row" / "3 rows". Where a panel row stands for ego
edges of a contribution, in-kind, transfer or vendor type, its row count is a button that lists those edges' filed
rows (see "Edge rows").

**Notes.** A note appears only when there is something to say:

- A donor group (`class:individual`, `class:organization`, `class:in_kind`) when `truncated.donors` > 0: "Top N
  individual donors in this graph; M more donors not shown." followed by a **See all donors** link to the entity's
  Donors tab. M is the donor cap's count across every donor class.
- An Other group when its class was capped (`truncated.vendors`, `truncated.transfers`, `truncated.ie_spenders`):
  "N independent spenders grouped here; M more independent spenders not in this graph." There is no link.
- A shared vendor in the vendor network: "Shared by N of this entity's committees."

The Donors tab URL comes from the route (`donors_tab_url` in the page's `entity-network-meta` JSON, read as
`ctx.donorsTabUrl`). It is built like the tab bar's Donors link: same entity, the page's time scope, and for races the
election cycle when the request carried one.

**Clearing.** Clicking the same node again, clicking the graph background or pressing Escape clears the panel and
the highlighting. Clicking another node replaces it. Each view script keeps one document-level Escape listener and
replaces it on every render. Switching views empties the panel.

### `focus_panel.js`: the shared helper

`webapp/static/js/entity_views/focus_panel.js` is loaded before the three view scripts. Every view's panel goes
through it, and a new view must use it too.

- `EntityViews.focusPanel(focusBox, spec)` draws an `h3` (`spec.heading`), then a `p.help-text` when `spec.note` or
  `spec.link` (`{text, href}`) is given, then a `table.data-table` with a `th` per `spec.columns` and one body row per
  `spec.rows` entry `{label, href, cells}`. The first cell is the label, linked when `href` is set, and one cell
  follows per `cells` value. With no rows it shows `spec.empty` instead of the table.
- `EntityViews.clearFocus(focusBox)` empties the panel.
- `EntityViews.markFocus(element, focused, dimmed)` sets or clears the `focused` / `dimmed` classes and the 0.15
  opacity.
- A row with `drill: {source, target, edge_type, cell}` and a `spec.rowsUrl` shows `cells[drill.cell]` as a button
  (class `edge-rows-toggle`, `aria-expanded`). `EntityViews.drill(edgeType, source, target, cell)` builds the drill,
  or returns null for the independent-expenditure and committee-candidate types.

The view scripts draw without `focus_panel.js`, but clicking needs it.

## Edge rows

`edge_rows(conn, payload, *, source, target=None, edge_type, date_from=, date_to=, limit=200)` in
`database/entity_network.py` returns the condensed rows behind the cached ego payload's edges of `edge_type` between
`source` and `target`. The edge's direction does not matter. With `target=None` it covers every edge of that type at
`source`, so a money-flow member's rows add up to its listed amount across the entity's committees.

- **Same predicates as the graph.** Contribution (1A), in-kind (5A) and transfer (2A) edges read
  `isbe_condensed_receipts`. Vendor edges read `isbe_condensed_expenditures`. Each type uses the part filter and
  neighbour key that the graph build uses: the donor key, the payer name or the payee name. Archived rows and amounts
  of zero or less never appear. So the rows' amounts sum to the edge weight, and their number is the edge `count`
  (`tests/test_entity_network.py`).
- **Result.** `{rows, total_amount, total_count, truncated}`. Rows come newest first, at most `limit`, and the totals
  cover every row. Each row has `date`, `amount`, `d2_part`, `description` (receipts) or `purpose` (expenditures),
  `counterparty` (the name as filed), `committee_id`, `committee_name`, `filed_doc_id` and `filing_href`
  (`/committees/filing/<id>`, or null). No address field is ever returned, so a redacted donor's address never shows.
- **Phase B types.** Independent-expenditure and committee-candidate edges return `{rows: [], unsupported: true}`.
- **Unknown input.** A node the payload does not hold, or no such edge between the two nodes, returns `None`. Request
  values only select payload edges. The SQL is built from the payload's own ids.

Routes: `/candidates/<id>/network/rows.json` and `/analytics/state-races/<race_key>/network/rows.json`. They take
`source`, `target` (optional) and `edge_type` plus the page's scope query. A missing `source`, an unknown `edge_type`,
an unknown entity, or `None` from `edge_rows` is a 404. The route reads the `network:ego` cache entry and caches the
result under tab `network:rows`. The key carries the caps, the 200-row limit, `edge_type`, `source` and `target`. TTL,
`?refresh_cache=1` gating and headers are the same as `network.json`. The page passes the URL, with its scope and the
race's election cycle, as `rows_url` in `entity-network-meta`, read as `ctx.rowsUrl`.

In the panel, a click on a Rows button fetches `rows_url` plus `source`, `target` and `edge_type`. It inserts a nested
table under the row with the columns Date, Amount, Type, Description and Filing. A second click collapses it. Rows
buttons appear on:

- members of the donor classes, in-kind, Other transfers and Other vendors (no target);
- money-flow flows between two ego nodes (not flows to or from a group node);
- every vendor-network panel row and every ego "Connected to" row of a supported type (both ends given).

Each of three cases shows one line instead of, or above, the table:

- a capped list: "Showing N of M rows."
- an unsupported type: "Row detail is not available for this relationship."
- a failed request: "The rows could not be loaded."
`tests/test_network_drilldown_js.py` and `tests/test_ego_focus_js.py` run the scripts under node with it loaded.

## The ego renderer

`webapp/static/js/network_graph.js` draws the ego view with d3 v7, which is vendored as one file
(`webapp/static/js/vendor/d3.v7.min.js`; version, checksum and license in `webapp/static/js/vendor/README.md`).
`NetworkGraph.render(container, {nodes, edges}, options)` returns the node and edge elements and a
`setTypeVisible(type, visible)` function. `ego_force.js` passes the colours, sizes, titles and click handlers, and keeps
the focus, the panel and Escape itself. Without `window.d3` it falls back to the radial layout, which the node tests
exercise.

- **Layout.** A force simulation (link distance by weight rank, many-body repulsion, collision at radius plus 6 px)
  starts from a radial seed by role and uses a fixed random seed. It runs 300 ticks before drawing, and a separation
  pass then leaves no two circles overlapping. The same data gives the same picture. The svg gets `data-settled="1"`
  when it is drawn.
- **Zoom and labels.** The graph opens fitted to the container. Scroll zooms and drag pans. Labels stay 12 px on screen
  at any zoom. About 24 of the largest nodes are labelled at the fitted zoom, the entity's committees and candidates
  first, and more as you zoom in. A label goes right, left, above or below its node, wherever it overlaps no other
  label and no other node and stays inside the graph. Otherwise it stays hidden. Hovering a node shows its label, and
  every node and edge keeps its tooltip.
- **Legend chips.** With an `onToggle(nodeType, visible)` option, `renderGraphKey` draws each node item as a chip
  (`button.graph-key-chip`, `aria-pressed`). A click hides or shows that node type and its edges. Other graphs pass no
  `onToggle` and keep the plain key.
- **Linked edge table.** The Network edges table (`#entity-network-edges`) carries `data-source` and `data-target` on
  each row. In the ego view, clicking a row focuses its node: the end that is not one of the entity's committees.
  Focusing a node highlights its rows (class `ng-row-focused`). Switching views clears the highlight.
- **Opening a view by URL.** `?view=ego`, `?view=vendor_network` or `?view=money_flow` on the Network tab opens that
  view first.

**Readability probe (N8).** `tests/integration/test_network_graph_readability.py` opens the ego view at 1200 x 700 on
candidate 13505 and the Evanston alderperson race. It counts label intersections, circle overlaps, elements outside
the container, labels under 11 px and unlabelled nodes whose label does not show on hover. Every count must be zero.
It needs a running server and is marked `integration`:

```bash
DASHBOARD_PREWARM_ENABLED=false python3 run.py runserver --port 5053
python3 -m pytest -q -m integration tests/integration/test_network_graph_readability.py
```

On the radial renderer (2026-10-29, local data) it measured the following.

| Page | Label intersections | Circle overlaps | Outside | Labels under 11 px | Hover failures |
|---|---|---|---|---|---|
| Candidate 13505 | 8 | 0 | 0 | 14 | 5 of 5 |
| Evanston race | 1 | 0 | 0 | 14 | 5 of 5 |

The force renderer measures zero on all five.

## Cache and static-file rules

- **Cache revision.** Every entity-tab cache key starts with `ENTITY_CACHE_REV` (`database/entity_analytics.py`).
  Bump it whenever the JSON shape of the payload or a view changes, so no entry in the old shape is served. It is
  `ea2` since the `members` field was added. The network entries are `network:ego`, `network:money_flow` and
  `network:vendor_network` plus the HTML tab's own entry. The projections and the HTML tab read the `network:ego`
  entry, so the page and its three views build the graph once.
- **Script stamps.** `entity/network.html` loads each script with a `?v=YYYYMMDD-N` stamp, and d3 with its version.
  Bump the stamp of every script you change, so browsers fetch the new file. `focus_panel.js`, `money_flow.js` and
  `vendor_network.js` are at `20261029-2`, `ego_force.js` is at `20261029-3`, and `graph_legend.js` and
  `network_graph.js` are at `20261029-1`. `css/style.css` is stamped in `base.html`.

## Not covered

- Independent-expenditure and committee-candidate edges have no rows drill-down (Phase B).
- The global `/analytics/networks` and `/analytics/relationships` pages have no drill-down panel.
