"""Entity ego network (plan .claude/plans/race-sort-and-entity-networks.md, decision N4).

``get_entity_network(conn, entity, ...)`` builds the money network around one entity (a race or a
candidate, as ``database/entity_analytics.resolve_entity`` describes it): the entity's committees and the
candidates they raise money for, plus their 1-hop neighbours, all read from the ISBE condensed matviews
over a date window (inclusive, either bound optional; archived rows and amounts <= 0 never count):

* donors -> committee: ``donor_committee`` (D-2 part 1A) and ``in_kind_donor_committee`` (5A);
* payers -> committee: ``transfer_in`` (2A); a payer whose lower-cased, trimmed name equals exactly one
  ``isbe_committees.name`` (same normalisation) is that committee, any other payer a ``transfer:`` node;
* committee -> payee: ``committee_vendor`` (every expenditure part but 9);
* spender -> candidate: ``independent_expenditure``, the Part-9 rows naming one of the entity's candidates,
  attributed with the repo's matcher (``database/outside_spending.py``: name key, suffix and "Last, First"
  rules, multi-name split with amount / n, the candidate's own candidacy cycles as the gate), one edge per
  (spender, candidate, support/oppose); a Part-9 row is never a vendor edge;
* committee -> candidate: ``committee_candidate``, weight = the committee's 1A total in the window.

Neighbour nodes are capped per class (``DEFAULT_CAPS`` merged with the caller's dict): the top-N by total
weight (ties: label, then id) survive, the rest and their edges are dropped, and ``summary.truncated``
reports how many nodes each class lost. The entity's own committees (and its candidates) are never capped.
A node's ``total_amount`` is the sum of its incident edge weights, i.e. the weighted degree centrality
reports. Nodes carry no address data. Pure data module: no caching (the route layer caches), no Flask.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date

from database.analytics import _compute_graph_centrality
from database.outside_spending import STRIPPED_NAME_SQL, attribute_part9_rows, candidate_name_key, name_keys_match

DEFAULT_CAPS = {"donors": 40, "vendors": 25, "transfers": 15, "ie_spenders": 15}

DONOR_COMMITTEE = "donor_committee"
IN_KIND_DONOR_COMMITTEE = "in_kind_donor_committee"
TRANSFER_IN = "transfer_in"
COMMITTEE_VENDOR = "committee_vendor"
INDEPENDENT_EXPENDITURE = "independent_expenditure"
COMMITTEE_CANDIDATE = "committee_candidate"

# Edge semantics for the renderer (not the global _EDGE_TYPE_SEMANTICS): copied onto every edge and into
# summary.edge_type_definitions.
EDGE_TYPE_DEFINITIONS = {
    DONOR_COMMITTEE: {
        "edge_label": "Contribution",
        "edge_description": "Itemized monetary contributions (D-2 part 1A) from a donor to a committee of this entity.",
        "weight_unit": "usd",
        "metric_label": "Contributed (USD)",
    },
    IN_KIND_DONOR_COMMITTEE: {
        "edge_label": "In-kind contribution",
        "edge_description": "In-kind contributions (D-2 part 5A) from a donor to a committee of this entity, valued as filed.",
        "weight_unit": "usd",
        "metric_label": "In-kind value (USD)",
    },
    TRANSFER_IN: {
        "edge_label": "Transfer in",
        "edge_description": "Transfers received from another committee (D-2 part 2A), identified by the payer name as filed.",
        "weight_unit": "usd",
        "metric_label": "Transferred (USD)",
    },
    COMMITTEE_VENDOR: {
        "edge_label": "Payment to vendor",
        "edge_description": "Expenditures of a committee of this entity paid to a payee (every D-2 part but 9), grouped by payee name.",
        "weight_unit": "usd",
        "metric_label": "Paid (USD)",
    },
    INDEPENDENT_EXPENDITURE: {
        "edge_label": "Independent expenditure",
        "edge_description": (
            "Independent expenditures (D-2 part 9) by a committee supporting (S) or opposing (O) a candidate, matched "
            "on the candidate name as filed; a row naming several candidates is split equally."
        ),
        "weight_unit": "usd",
        "metric_label": "Spent (USD)",
    },
    COMMITTEE_CANDIDATE: {
        "edge_label": "Raises for candidate",
        "edge_description": "A committee of this entity and the candidate it raises money for; the weight is the committee's itemized contributions (1A) in the window.",
        "weight_unit": "usd",
        "metric_label": "Raised (USD)",
    },
}
_SOURCE_TABLE = {
    DONOR_COMMITTEE: "isbe_condensed_receipts",
    IN_KIND_DONOR_COMMITTEE: "isbe_condensed_receipts",
    TRANSFER_IN: "isbe_condensed_receipts",
    COMMITTEE_CANDIDATE: "isbe_condensed_receipts",
    COMMITTEE_VENDOR: "isbe_condensed_expenditures",
    INDEPENDENT_EXPENDITURE: "isbe_condensed_expenditures",
}

# Cap classes in the order that decides the role of a committee that is a neighbour in two classes.
_CAP_CLASSES = ("donors", "vendors", "transfers", "ie_spenders")
_CLASS_ROLE = {"donors": "donor", "vendors": "vendor", "transfers": "transfer_source", "ie_spenders": "ie_spender"}
_NODE_TYPE_BY_PREFIX = {"donor": "donor", "vendor": "vendor", "candidate": "candidate", "committee": "committee",
                        "transfer": "committee"}
_CENTRALITY_LIMIT = 100

# The /donors/key/<donor_key> identity (database/analytics.py), the payee / payer name and the redaction rule,
# over alias ``t``. `like` is lower-case on purpose: database/pg_compat.py rewrites an upper-case LIKE to ILIKE,
# which the partial indexes cannot serve.
_DONOR_KEY_SQL = (
    "LOWER(TRIM(COALESCE(t.first_name, '') || '|' || COALESCE(t.last_name, '') || '|' || COALESCE(t.address1, '')"
    " || '|' || COALESCE(t.address2, '') || '|' || COALESCE(t.city, '') || '|' || COALESCE(t.state, '')"
    " || '|' || COALESCE(t.zipcode, '')))"
)
_NAME_SQL = "TRIM(COALESCE(t.first_name, '') || ' ' || COALESCE(t.last_name, ''))"
_REDACTED_SQL = (
    "BOOL_OR(COALESCE(t.redaction_requested, FALSE) OR COALESCE(t.address1 = 'Redaction Requested', FALSE))"
)
_PAYER_OR_DONOR_KEY_SQL = f"CASE WHEN t.d2_part = '5A' THEN {_DONOR_KEY_SQL} ELSE LOWER({_NAME_SQL}) END"
_PART9_ROWS_SQL = f"""
    SELECT e.id, e.committee_id, e.candidate_name, e.amount, e.supporting, e.opposing, e.expended_date
    FROM isbe_condensed_expenditures e
    WHERE e.d2_part like '9%' AND e.archived = FALSE AND e.amount > 0
      AND e.expended_date >= ? AND e.expended_date <= ?
      AND {STRIPPED_NAME_SQL} like ANY(?)
"""


def get_entity_network(conn, entity, *, date_from=None, date_to=None, caps=None):
    """The ego network of ``entity`` (see the module docstring) as a JSON-ready dict:
    ``entity`` (echoed), ``nodes``, ``edges``, ``centrality``, ``summary``."""
    caps = {**DEFAULT_CAPS, **(caps or {})}
    window = (date_from, date_to)
    committee_ids = sorted({int(committee_id) for committee_id in entity["committee_ids"]})
    if not committee_ids:
        return _payload(entity, [], [], [], dict.fromkeys(_CAP_CLASSES, 0), window)

    entity_committees = {f"committee:{committee_id}" for committee_id in committee_ids}
    group_label: dict[int, str] = {}
    for group in entity["groups"]:
        for committee_id in group["committee_ids"]:
            group_label.setdefault(int(committee_id), group["label"])

    labels: dict[str, str] = {}
    redacted: set[str] = set()
    # (type, source, target, support_oppose) -> (cap class, the neighbour that class ranks, the edge)
    edges: dict[tuple, tuple] = {}

    def add(edge_type, source, target, weight, count, cap_class=None, neighbour=None, support_oppose=None):
        key = (edge_type, source, target, support_oppose)
        if key not in edges:
            edge = {"source": source, "target": target, "edge_type": edge_type, "weight": 0.0, "count": 0,
                    **EDGE_TYPE_DEFINITIONS[edge_type], "source_table": _SOURCE_TABLE[edge_type]}
            if support_oppose:
                edge["support_oppose"] = support_oppose
            edges[key] = (cap_class if neighbour not in entity_committees else None, neighbour, edge)
        edge = edges[key][2]
        edge["weight"] += float(weight)
        edge["count"] += int(count)

    # --- receipts: 1A donors (partial index idx_isbe_condensed_receipts_active_part1), then 5A / 2A
    raised: dict[int, list] = defaultdict(lambda: [0.0, 0])   # committee -> [1A total, 1A rows]
    for row in _grouped(conn, "isbe_condensed_receipts", "received_date",
                        "t.d2_part like '1%' AND t.d2_part = '1A'", _DONOR_KEY_SQL, committee_ids, window):
        donor = "donor:" + row["node_key"]
        labels.setdefault(donor, row["label"])
        if row["redacted"]:
            redacted.add(donor)
        add(DONOR_COMMITTEE, donor, f"committee:{row['committee_id']}", row["amount"], row["n"], "donors", donor)
        raised[row["committee_id"]][0] += float(row["amount"])
        raised[row["committee_id"]][1] += int(row["n"])

    transfers = []
    for row in _grouped(conn, "isbe_condensed_receipts", "received_date", "t.d2_part IN ('5A', '2A')",
                        _PAYER_OR_DONOR_KEY_SQL, committee_ids, window):
        if row["d2_part"] == "2A":
            transfers.append(row)
            continue
        donor = "donor:" + row["node_key"]
        labels.setdefault(donor, row["label"])
        if row["redacted"]:
            redacted.add(donor)
        add(IN_KIND_DONOR_COMMITTEE, donor, f"committee:{row['committee_id']}", row["amount"], row["n"],
            "donors", donor)

    resolved: dict[str, tuple[int, str]] = {}   # normalised payer name -> (committee id, name) when unique
    payer_names = sorted({row["node_key"] for row in transfers})
    if payer_names:
        for row in conn.execute(
            "SELECT LOWER(TRIM(name)) AS norm, MIN(id) AS id, MIN(name) AS name, COUNT(*) AS n "
            "FROM isbe_committees WHERE LOWER(TRIM(name)) = ANY(?) GROUP BY 1",
            (payer_names,),
        ).fetchall():
            if row["n"] == 1:
                resolved[row["norm"]] = (row["id"], row["name"])
    for row in transfers:
        if row["node_key"] in resolved:
            payer_id, payer_name = resolved[row["node_key"]]
            payer = f"committee:{payer_id}"
            labels.setdefault(payer, payer_name)
        else:
            payer = "transfer:" + _hash16(row["node_key"])
            labels.setdefault(payer, row["label"])
        add(TRANSFER_IN, payer, f"committee:{row['committee_id']}", row["amount"], row["n"], "transfers", payer)

    # --- expenditures: everything but Part 9 is a payee; Part 9 is attributed to candidates below
    for row in _grouped(conn, "isbe_condensed_expenditures", "expended_date",
                        "(t.d2_part IS NULL OR NOT (t.d2_part like '9%'))", f"LOWER({_NAME_SQL})",
                        committee_ids, window):
        vendor = "vendor:" + _hash16(row["node_key"])
        labels.setdefault(vendor, row["label"])
        add(COMMITTEE_VENDOR, f"committee:{row['committee_id']}", vendor, row["amount"], row["n"], "vendors", vendor)

    # --- candidates the entity's committees raise money for, and the Part-9 rows naming them
    candidate_ids = []
    for candidate_id in [*entity["candidate_ids"], *(group.get("candidate_id") for group in entity["groups"])]:
        if candidate_id is not None and int(candidate_id) not in candidate_ids:
            candidate_ids.append(int(candidate_id))
    for group in entity["groups"]:
        for committee_id in group["committee_ids"]:
            committee_total, committee_rows = raised.get(int(committee_id), (0.0, 0))
            if group.get("candidate_id") is not None and committee_total > 0:
                add(COMMITTEE_CANDIDATE, f"committee:{int(committee_id)}", f"candidate:{int(group['candidate_id'])}",
                    committee_total, committee_rows)
    fallback_labels = {int(group["candidate_id"]): group["label"] for group in entity["groups"]
                       if group.get("candidate_id") is not None}
    attributable = _attributable_candidates(conn, candidate_ids, fallback_labels, labels)
    spenders: set[int] = set()
    for item in _part9_attributions(conn, attributable, window):
        support_oppose = "S" if item["supporting"] else "O" if item["opposing"] else None
        if support_oppose is None:
            continue
        spender = f"committee:{item['committee_id']}"
        spenders.add(item["committee_id"])
        add(INDEPENDENT_EXPENDITURE, spender, f"candidate:{item['candidate']['candidate_id']}", item["share"], 1,
            "ie_spenders", spender, support_oppose)

    committee_names = _committee_names(conn, {*committee_ids, *spenders})
    for committee_id in {*committee_ids, *spenders}:
        labels[f"committee:{committee_id}"] = committee_names.get(committee_id) or f"Committee {committee_id}"

    # --- money to cents, then the per-class caps
    records = list(edges.values())
    for _, _, edge in records:
        edge["weight"] = round(edge["weight"], 2)
        if "support_oppose" in edge:
            edge["signed_weight"] = edge["weight"] if edge["support_oppose"] == "S" else -edge["weight"]
    records, truncated = _apply_caps(records, caps, labels)

    edge_list = sorted(
        (edge for _, _, edge in records),
        key=lambda edge: (-edge["weight"], edge["edge_type"], edge["source"], edge["target"],
                          edge.get("support_oppose") or ""),
    )

    # --- nodes: every endpoint, the entity's committees and its candidates
    total: dict[str, float] = defaultdict(float)
    for edge in edge_list:
        total[edge["source"]] += edge["weight"]
        total[edge["target"]] += edge["weight"]
    neighbour_classes: dict[str, set] = defaultdict(set)
    for cap_class, neighbour, _ in records:
        if cap_class:
            neighbour_classes[neighbour].add(cap_class)
    node_ids = set(total) | entity_committees | {f"candidate:{candidate_id}" for candidate_id in candidate_ids}
    nodes = []
    for node_id in node_ids:
        prefix = node_id.split(":", 1)[0]
        node = {"id": node_id, "label": labels.get(node_id) or node_id, "node_type": _NODE_TYPE_BY_PREFIX[prefix],
                "total_amount": round(total.get(node_id, 0.0), 2)}
        if node_id in entity_committees:
            node["role"] = "entity"
            if int(node_id.split(":", 1)[1]) in group_label:
                node["group_label"] = group_label[int(node_id.split(":", 1)[1])]
        elif prefix == "candidate":
            node["role"] = "candidate"
        else:
            node["role"] = next(_CLASS_ROLE[cls] for cls in _CAP_CLASSES if cls in neighbour_classes[node_id])
        if prefix == "donor":
            node["redacted"] = node_id in redacted
        nodes.append(node)
    nodes.sort(key=lambda node: (-node["total_amount"], node["label"], node["id"]))
    return _payload(entity, nodes, edge_list, _compute_graph_centrality(nodes, edge_list, _CENTRALITY_LIMIT),
                    truncated, window)


def _hash16(normalised_name: str) -> str:
    return hashlib.sha1(normalised_name.encode("utf-8")).hexdigest()[:16]


def _window(column, window):
    """`` AND col >= ? AND col <= ?`` for the bounds that are set, and their parameters."""
    sql, params = "", []
    for operator, bound in ((">=", window[0]), ("<=", window[1])):
        if bound is not None:
            sql += f" AND {column} {operator} ?"
            params.append(bound)
    return sql, params


def _grouped(conn, table, date_column, filter_sql, key_sql, committee_ids, window):
    """One row per (committee, D-2 part, key): amount sum, row count, a display label, redaction flag."""
    window_sql, params = _window(f"t.{date_column}", window)
    return conn.execute(
        f"""
        SELECT t.committee_id, t.d2_part, {key_sql} AS node_key, MIN({_NAME_SQL}) AS label,
               SUM(t.amount) AS amount, COUNT(*) AS n, {_REDACTED_SQL} AS redacted
        FROM {table} t
        WHERE t.committee_id = ANY(?) AND t.archived = FALSE AND t.amount > 0 AND {filter_sql}{window_sql}
        GROUP BY 1, 2, 3
        """,
        [committee_ids, *params],
    ).fetchall()


def _committee_names(conn, committee_ids) -> dict[int, str]:
    rows = conn.execute("SELECT id, name FROM isbe_committees WHERE id = ANY(?)", (sorted(committee_ids),)).fetchall()
    return {row["id"]: row["name"] for row in rows}


def _attributable_candidates(conn, candidate_ids, fallback_labels, labels) -> list[dict]:
    """Candidates a Part-9 row can attach to, in entity order, one per person: a record whose name key matches an
    earlier one's (name_keys_match: same last name, first names equal or direct nickname aliases) is the same person
    registered twice, and the first record carries the rows, as on the race page; sets the candidate nodes' labels."""
    if not candidate_ids:
        return []
    names = {
        row["id"]: " ".join(part.strip() for part in (row["first_name"], row["last_name"]) if part and part.strip())
        for row in conn.execute(
            "SELECT id, first_name, last_name FROM isbe_candidates WHERE id = ANY(?)", (candidate_ids,)
        ).fetchall()
    }
    cycles: dict[int, set] = defaultdict(set)
    for row in conn.execute(
        "SELECT candidate_id, election_year FROM isbe_candidacies WHERE candidate_id = ANY(?) "
        "AND election_year IS NOT NULL",
        (candidate_ids,),
    ).fetchall():
        cycles[row["candidate_id"]].add(row["election_year"] + row["election_year"] % 2)
    attributable, keys = [], set()
    for candidate_id in candidate_ids:
        name = names.get(candidate_id)
        labels[f"candidate:{candidate_id}"] = name or fallback_labels.get(candidate_id) or f"Candidate {candidate_id}"
        key = candidate_name_key(name)
        if key is None or not cycles[candidate_id] or any(name_keys_match(key, seen) for seen in keys):
            continue
        keys.add(key)
        attributable.append({"key": key, "cycles": cycles[candidate_id], "candidate_id": candidate_id})
    return attributable


def _part9_attributions(conn, attributable, window) -> list[dict]:
    """Part-9 rows of any spender attributed to ``attributable`` candidates inside the window. The date scan is
    bounded by the candidates' cycle span (a superset of the cycle gate) and a last-name prefilter."""
    if not attributable:
        return []
    cycles = set().union(*(candidate["cycles"] for candidate in attributable))
    start, end = date(min(cycles) - 1, 1, 1), date(max(cycles), 12, 31)
    if window[0] is not None:
        start = max(start, window[0])
    if window[1] is not None:
        end = min(end, window[1])
    if start > end:
        return []
    patterns = sorted({f"%{candidate['key'][1]}%" for candidate in attributable})
    rows = conn.execute(_PART9_ROWS_SQL, (start, end, patterns)).fetchall()
    return attribute_part9_rows(rows, attributable)


def _apply_caps(records, caps, labels):
    """Keep the top-N neighbours per class by total edge weight (ties: label, id); drop the rest and their
    edges. Returns (surviving records, {class: dropped node count})."""
    totals: dict[tuple, float] = defaultdict(float)
    for cap_class, neighbour, edge in records:
        if cap_class:
            totals[(cap_class, neighbour)] += edge["weight"]
    keep, truncated = set(), {}
    for cap_class in _CAP_CLASSES:
        ranked = sorted(
            (neighbour for cls, neighbour in totals if cls == cap_class),
            key=lambda neighbour: (-round(totals[(cap_class, neighbour)], 2), labels.get(neighbour, ""), neighbour),
        )
        limit = max(int(caps[cap_class]), 0)
        keep.update((cap_class, neighbour) for neighbour in ranked[:limit])
        truncated[cap_class] = max(len(ranked) - limit, 0)
    return [record for record in records if not record[0] or (record[0], record[1]) in keep], truncated


def _payload(entity, nodes, edges, centrality, truncated, window):
    return {
        "entity": entity,
        "nodes": nodes,
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "edges_by_type": dict(Counter(edge["edge_type"] for edge in edges)),
            "truncated": truncated,
            "edge_type_definitions": {key: dict(value) for key, value in EDGE_TYPE_DEFINITIONS.items()},
            "window": {"date_from": window[0].isoformat() if window[0] else None,
                       "date_to": window[1].isoformat() if window[1] else None},
        },
    }


# ---------------------------------------------------------------------------
# Phase A views (plan step 8): pure projections of the ego payload. Nothing here reads the database; every view
# is a function of ``get_entity_network``'s dict and leaves it untouched.
# ---------------------------------------------------------------------------

MONEY_FLOW_VIEW = "money_flow"
VENDOR_NETWORK_VIEW = "vendor_network"
EGO_VIEW = "ego"
CLASS_LABELS = {"individual": "Individual donors", "organization": "Organization donors", "in_kind": "In-kind contributions"}
OTHER_LABELS = {"vendors": "Other vendors", "transfers": "Other transfers", "spenders": "Other independent spenders"}
# What the donor-class nodes are called when the donor cap dropped donors: they then hold only the kept donors' dollars.
TOP_CLASS_LABELS = {"individual": "individual donors", "organization": "organization donors"}


def donor_class(node_id: str) -> str:
    """``individual`` when the donor key's first segment (the filed first name) is non-empty, else ``organization``
    (ISBE files a business name in last_name only) -- the Donors tab's rule, applied to the node id."""
    key = node_id.partition(":")[2]
    return "individual" if key.split("|", 1)[0].strip() else "organization"


def _fold(ranked_totals: dict[str, float], limit: int, other_id: str):
    """Top ``limit`` ids by total (ties: id) keep themselves; the rest map to ``other_id``. Returns (mapping, folded)."""
    ranked = sorted(ranked_totals, key=lambda node_id: (-round(ranked_totals[node_id], 2), node_id))
    keep = set(ranked[: max(int(limit), 0)])
    return {node_id: (node_id if node_id in keep else other_id) for node_id in ranked}, max(len(ranked) - len(keep), 0)


def money_flow(payload, *, max_vendors=10, max_transfers=10, max_spenders=10):
    """Sankey projection: column 0 = donor classes, in-kind, transfer sources and independent spenders; column 1 =
    the entity's committees and the candidates independent spending names; column 2 = vendors. The top
    ``max_*`` transfer sources, spenders and vendors keep their own node, the rest fold into one "Other" node per
    class (``folded`` counts them). ``committee_candidate`` edges are links of money raised, not flows, and are
    left out. So is a ``transfer_in`` or ``independent_expenditure`` edge whose source is one of the entity's own
    committees: it would join two column-1 nodes and is money moving between the entity's own accounts, so it is
    counted in ``folded["internal"]`` (rows, dollars) instead. Every other ego edge lands in exactly one link, so
    ``totals`` per edge type equal the ego's. The ego graph holds only the top donors (``summary.truncated``, passed
    through as ``truncated``); when donors were dropped the donor-class nodes are labelled "Top N ... donors"."""
    nodes_by_id = {node["id"]: node for node in payload["nodes"]}
    entity_committees = [node["id"] for node in payload["nodes"] if node.get("role") == "entity"]
    own = set(entity_committees)
    edges, internal_rows, internal_amount = [], 0, 0.0
    for edge in payload["edges"]:
        if edge["edge_type"] == COMMITTEE_CANDIDATE:
            continue
        if edge["edge_type"] in (TRANSFER_IN, INDEPENDENT_EXPENDITURE) and edge["source"] in own:
            internal_rows += int(edge["count"])
            internal_amount += float(edge["weight"])
            continue
        edges.append(edge)
    truncated = dict(payload["summary"]["truncated"])
    donors_by_class: dict[str, set] = defaultdict(set)

    totals: dict[str, dict[str, float]] = {"vendors": defaultdict(float), "transfers": defaultdict(float),
                                           "spenders": defaultdict(float)}
    for edge in edges:
        if edge["edge_type"] == COMMITTEE_VENDOR:
            totals["vendors"][edge["target"]] += edge["weight"]
        elif edge["edge_type"] == TRANSFER_IN:
            totals["transfers"][edge["source"]] += edge["weight"]
        elif edge["edge_type"] == INDEPENDENT_EXPENDITURE:
            totals["spenders"][edge["source"]] += edge["weight"]
    mapping, folded = {}, {}
    for cls, limit in (("vendors", max_vendors), ("transfers", max_transfers), ("spenders", max_spenders)):
        cls_mapping, folded[cls] = _fold(totals[cls], limit, f"other:{cls}")
        mapping.update(cls_mapping)

    links: dict[tuple, dict] = {}
    kinds: dict[str, str] = {}
    for edge in edges:
        edge_type = edge["edge_type"]
        if edge_type == DONOR_COMMITTEE:
            source, target = f"class:{donor_class(edge['source'])}", edge["target"]
            kinds[source] = "donor_class"
            donors_by_class[donor_class(edge["source"])].add(edge["source"])
        elif edge_type == IN_KIND_DONOR_COMMITTEE:
            source, target = "class:in_kind", edge["target"]
            kinds[source] = "in_kind"
        elif edge_type == TRANSFER_IN:
            source, target = mapping[edge["source"]], edge["target"]
            kinds[source] = "transfer_source"
        elif edge_type == INDEPENDENT_EXPENDITURE:
            source, target = mapping[edge["source"]], edge["target"]
            kinds[source] = "ie_spender"
            kinds[target] = "candidate"
        else:  # committee_vendor
            source, target = edge["source"], mapping[edge["target"]]
            kinds[target] = "vendor"
        key = (source, target, edge_type, edge.get("support_oppose"))
        link = links.get(key)
        if link is None:
            link = {"source": source, "target": target, "edge_type": edge_type, "edge_label": edge["edge_label"],
                    "weight": 0.0, "count": 0}
            if edge.get("support_oppose"):
                link["support_oppose"] = edge["support_oppose"]
            links[key] = link
        link["weight"] += float(edge["weight"])
        link["count"] += int(edge["count"])
    for committee_id in entity_committees:
        kinds[committee_id] = "committee"

    amount: dict[str, float] = defaultdict(float)
    for link in links.values():
        link["weight"] = round(link["weight"], 2)
        amount[link["source"]] += link["weight"]
        amount[link["target"]] += link["weight"]
    column = {"donor_class": 0, "in_kind": 0, "transfer_source": 0, "ie_spender": 0, "committee": 1, "candidate": 1,
              "vendor": 2}
    nodes = []
    for node_id, kind in kinds.items():
        if node_id.startswith("class:"):
            donor_cls = node_id.partition(":")[2]
            label = CLASS_LABELS[donor_cls]
            if truncated.get("donors") and donor_cls in TOP_CLASS_LABELS:
                label = f"Top {len(donors_by_class[donor_cls])} {TOP_CLASS_LABELS[donor_cls]}"
        elif node_id.startswith("other:"):
            label = OTHER_LABELS[node_id.partition(":")[2]]
        else:
            label = nodes_by_id.get(node_id, {}).get("label") or node_id
        node = {"id": node_id, "label": label, "kind": kind, "column": column[kind], "amount": round(amount[node_id], 2)}
        if kind == "committee" and nodes_by_id.get(node_id, {}).get("group_label"):
            node["group_label"] = nodes_by_id[node_id]["group_label"]
        nodes.append(node)
    nodes.sort(key=lambda node: (node["column"], -node["amount"], node["label"], node["id"]))
    link_list = sorted(links.values(), key=lambda link: (-link["weight"], link["edge_type"], link["source"], link["target"],
                                                         link.get("support_oppose") or ""))
    by_type: dict[str, float] = defaultdict(float)
    support = oppose = 0.0
    for link in link_list:
        by_type[link["edge_type"]] += link["weight"]
        if link.get("support_oppose") == "S":
            support += link["weight"]
        elif link.get("support_oppose") == "O":
            oppose += link["weight"]
    return {
        "view": MONEY_FLOW_VIEW,
        "nodes": nodes,
        "links": link_list,
        "totals": {**{edge_type: round(value, 2) for edge_type, value in sorted(by_type.items())},
                   "support": round(support, 2), "oppose": round(oppose, 2)},
        "folded": {**folded, "internal": {"count": internal_rows, "amount": round(internal_amount, 2)}},
        "truncated": truncated,
    }


def vendor_network(payload):
    """Bipartite projection: the entity's committees and the independent spenders (``side: "payer"``) against the
    vendors they pay and the candidates the spenders support or oppose (``side: "payee"``); only ``committee_vendor``
    and ``independent_expenditure`` edges. A vendor paid by two or more committees is ``shared`` (``committee_ids``
    lists them). Phase A limit: the ego payload holds the entity committees' own payees only, so a vendor shared
    with an independent spender's payees cannot be seen here (plan N6 Phase B)."""
    keep = {COMMITTEE_VENDOR, INDEPENDENT_EXPENDITURE}
    edges = [dict(edge) for edge in payload["edges"] if edge["edge_type"] in keep]
    payers: dict[str, list] = defaultdict(list)
    for edge in edges:
        if edge["edge_type"] == COMMITTEE_VENDOR:
            payers[edge["target"]].append(edge["source"])
    wanted = {node["id"] for node in payload["nodes"] if node.get("role") == "entity"}
    wanted |= {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    nodes = []
    for node in payload["nodes"]:
        if node["id"] not in wanted:
            continue
        projected = dict(node, side="payee" if node["node_type"] in ("vendor", "candidate") else "payer")
        if node["node_type"] == "vendor":
            committee_ids = sorted(set(payers[node["id"]]))
            projected.update(committee_ids=committee_ids, committee_count=len(committee_ids), shared=len(committee_ids) >= 2)
        nodes.append(projected)
    nodes.sort(key=lambda node: (node["side"], -node["total_amount"], node["label"], node["id"]))
    shared = [node["id"] for node in nodes if node.get("shared")]
    return {"view": VENDOR_NETWORK_VIEW, "nodes": nodes, "edges": edges, "shared_vendors": shared}


def ego_force(payload):
    """The ego graph as is: nodes and edges only (copies), for the force renderer (plan N7, step 7)."""
    return {"view": EGO_VIEW, "nodes": [dict(node) for node in payload["nodes"]],
            "edges": [dict(edge) for edge in payload["edges"]]}
