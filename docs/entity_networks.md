# Entity networks: the Network tab of a candidate or race

The Network tab (`/candidates/<id>/network`, `/analytics/state-races/<race_key>/network`) draws the money network
around one entity's committees. This page describes the data contract, the three views, the click drill-down panel
and the cache rules. Code: `database/entity_network.py` (payload and projections), `webapp/routes/entity_analytics.py`
(routes and caching), `webapp/templates/entity/network.html` and `webapp/static/js/entity_views/*.js` (rendering).

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

- **`ego`**: the ego payload as is, plus `view: "ego"`. `ego_force.js` draws it on a radial layout.
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
| Ego graph | any node | Connected to *label* | Name, Relationship, Amount |

Rows are sorted by dollars, ties by name. Row counts read "1 row" / "3 rows".

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

The view scripts draw without `focus_panel.js`, but clicking needs it.
`tests/test_network_drilldown_js.py` and `tests/test_ego_focus_js.py` run the scripts under node with it loaded.

## Cache and static-file rules

- **Cache revision.** Every entity-tab cache key starts with `ENTITY_CACHE_REV` (`database/entity_analytics.py`).
  Bump it whenever the JSON shape of the payload or a view changes, so no entry in the old shape is served. It is
  `ea2` since the `members` field was added. The network entries are `network:ego`, `network:money_flow` and
  `network:vendor_network` plus the HTML tab's own entry. The projections and the HTML tab read the `network:ego`
  entry, so the page and its three views build the graph once.
- **Script stamps.** `entity/network.html` loads each script with a `?v=YYYYMMDD-N` stamp. Bump the stamp of every
  script you change, so browsers fetch the new file. The four `entity_views` scripts are at `20261029-1`, and
  `graph_legend.js` is at `20261028-2`.

## Not covered

- Clicking a vendor lists the committees that paid it and the totals. It does not list the individual payment line
  items; the committee's `/committees/<id>/payees` page lists a committee's payees.
- The global `/analytics/networks` and `/analytics/relationships` pages have no drill-down panel.
