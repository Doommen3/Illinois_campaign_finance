/* Site-wide graph key (plan race-sort-and-entity-networks, decision N18).
   One style map for every edge type a graph on the site draws (GRAPH_STYLES: colour, dash, label, one-line meaning),
   one node palette (GRAPH_NODE_STYLES) and renderGraphKey(), which prints a compact key under a graph listing ONLY
   what that graph draws: each edge type with a swatch drawn as a real SVG line (so dashed shows as dashed), its label
   and meaning, then a row of node types. Independent expenditures are two styles -- spent FOR a candidate (green,
   solid) and AGAINST (red, dashed) -- because one colour for both made an opposing line read as support.
   Loaded before the renderers on every page that draws a graph: the entity Network tab, /analytics/networks and
   /analytics/relationships. Helpers live under window.GraphKey. */
(function () {
    var SVG_NS = "http://www.w3.org/2000/svg";

    // Edge styles keyed by edge type; a signed type adds ":S" (for) or ":O" (against). Order is the key's row order.
    var GRAPH_STYLES = {
        donor_committee: { color: "#2563eb", dash: null, label: "Contribution", meaning: "Itemized money a donor gave to a committee." },
        in_kind_donor_committee: { color: "#63b3ed", dash: "4 3", label: "In-kind contribution", meaning: "Goods or services a donor gave to a committee, valued as filed." },
        transfer_in: { color: "#805ad5", dash: null, label: "Transfer in", meaning: "Money moved to a committee from another committee." },
        committee_vendor: { color: "#d97706", dash: null, label: "Payment to vendor", meaning: "Money a committee paid to a vendor or other payee." },
        committee_candidate: { color: "#718096", dash: null, label: "Committee linked to candidate", meaning: "A committee and the candidate it raises money for (receipts linked to the candidate, not a payment to them)." },
        independent_expenditure: { color: "#718096", dash: null, label: "Independent expenditure", meaning: "Money an outside committee spent on a candidate's race (support or oppose not filed)." },
        "independent_expenditure:S": { color: "#38a169", dash: null, side: "S", label: "Independent expenditure, for", meaning: "Independent expenditure spent FOR the candidate by an outside committee (green, solid)." },
        "independent_expenditure:O": { color: "#e53e3e", dash: "6 4", side: "O", label: "Independent expenditure, against", meaning: "Independent expenditure spent AGAINST the candidate by an outside committee (red, dashed)." },
        ie_committee_candidate: { color: "#718096", dash: null, label: "Independent expenditure (Schedule E)", meaning: "Money an outside committee spent on a federal candidate's race (support and oppose mixed or not filed)." },
        "ie_committee_candidate:S": { color: "#38a169", dash: null, side: "S", label: "Independent expenditure, for (Schedule E)", meaning: "Independent expenditure spent FOR the candidate by an outside committee (green, solid)." },
        "ie_committee_candidate:O": { color: "#e53e3e", dash: "6 4", side: "O", label: "Independent expenditure, against (Schedule E)", meaning: "Independent expenditure spent AGAINST the candidate by an outside committee (red, dashed)." },
        donor_local: { color: "#059669", dash: null, label: "Donor to state committee", meaning: "A matched donor's giving to an Illinois state committee." },
        donor_federal: { color: "#0284c7", dash: null, label: "Donor to federal committee", meaning: "A matched donor's giving to a federal committee (FEC)." },
        donor_cogiving: { color: "#64748b", dash: null, label: "Donors giving to the same targets", meaning: "Two donors who gave to overlapping committees or candidates." },
        committee_similarity: { color: "#0f766e", dash: null, label: "Similar donor base", meaning: "Two committees whose donors overlap." },
        candidate_competition: { color: "#1d4ed8", dash: null, label: "Shared donors between candidates", meaning: "Two candidates whose donors overlap (they draw on the same money)." },
        community_bridge: { color: "#475569", dash: null, label: "Community bridge", meaning: "Combined links between two collapsed communities." },
        client_entity: { color: "#7c3aed", dash: null, label: "Client to lobbying entity", meaning: "A registered lobbying client and the entity it is registered with." },
        client_donor_match: { color: "#2563eb", dash: null, label: "Client matched to donor", meaning: "Fuzzy name match between a lobbying client and a donor." },
        client_payee_match: { color: "#f59e0b", dash: null, label: "Client matched to payee", meaning: "Fuzzy name match between a lobbying client and a campaign payee." },
        entity_payee_match: { color: "#d97706", dash: null, label: "Entity matched to payee", meaning: "Fuzzy name match between a lobbying entity and a campaign payee." },
        donor_committee_flow: { color: "#059669", dash: null, label: "Matched donor to committee", meaning: "Contributions from a donor identity matched across datasets." },
        payee_committee_match: { color: "#475569", dash: null, label: "Payee to committee spending", meaning: "Committee spending paid to a payee matched to lobbying data." },
        org_committee_match: { color: "#0ea5e9", dash: null, label: "527 organization matched to committee", meaning: "Fuzzy name match between an IRS 527 organization and a committee." },
        org_recipient_match: { color: "#0284c7", dash: null, label: "527 organization to recipient", meaning: "527 spending matched to a campaign-finance recipient." },
        org_director: { color: "#a16207", dash: null, label: "527 organization to director", meaning: "A director named on the organization's IRS 527 filing." },
        director_donor_match: { color: "#4f46e5", dash: null, label: "Director matched to donor", meaning: "Fuzzy name match between a 527 director and a donor." },
        system_state: { color: "#0369a1", dash: null, label: "State link", meaning: "Link between candidates in Illinois state (ISBE) data." },
        system_federal: { color: "#b91c1c", dash: null, label: "Federal link", meaning: "Link between candidates in federal (FEC) data." },
        system_cross_system: { color: "#7c3aed", dash: null, label: "Cross-system link", meaning: "Link that joins state and federal data." }
    };

    // One node palette for the whole site. ie_spender is a committee that spends independently (node role), shown
    // apart from the entity's own committees on the entity networks.
    var GRAPH_NODE_STYLES = {
        donor: { color: "#2563eb", shape: "circle", label: "Donor" },
        matched_donor: { color: "#6366f1", shape: "circle", label: "Matched donor" },
        committee: { color: "#059669", shape: "circle", label: "Committee" },
        local_committee: { color: "#059669", shape: "circle", label: "State committee" },
        federal_committee: { color: "#0284c7", shape: "circle", label: "Federal committee" },
        ie_spender: { color: "#9f7aea", shape: "circle", label: "Outside spender" },
        candidate: { color: "#dc2626", shape: "circle", label: "Candidate" },
        vendor: { color: "#d97706", shape: "circle", label: "Vendor" },
        matched_payee: { color: "#0891b2", shape: "circle", label: "Matched payee" },
        lobbying_client: { color: "#7c3aed", shape: "circle", label: "Lobbying client" },
        lobbying_entity: { color: "#a855f7", shape: "circle", label: "Lobbying entity" },
        irs527_org: { color: "#be185d", shape: "circle", label: "527 organization" },
        director: { color: "#92400e", shape: "circle", label: "527 director" },
        recipient_target: { color: "#475569", shape: "circle", label: "Matched recipient" },
        community: { color: "#334155", shape: "circle", label: "Community" }
    };
    var FALLBACK_COLOR = "#94a3b8";
    var STYLE_ORDER = Object.keys(GRAPH_STYLES);
    var NODE_ORDER = Object.keys(GRAPH_NODE_STYLES);

    function humanize(type) {
        var text = String(type || "").replace(/[_:]+/g, " ").trim();
        return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Unknown";
    }

    // The GRAPH_STYLES key of an edge: its type, plus ":S" / ":O" when it is signed. The entity network files
    // support_oppose; FEC Schedule E rows file support_oppose_values ("S", "O" or "S,O" -- mixed stays unsigned).
    function styleKey(edge) {
        var type = String((edge && edge.edge_type) || "").toLowerCase();
        var side = edge && edge.support_oppose;
        if (!side && edge && edge.support_oppose_values) {
            var sides = String(edge.support_oppose_values).toUpperCase().replace(/[^SO]/g, "");
            side = sides === "S" || sides === "O" ? sides : "";
        }
        return side && GRAPH_STYLES[type + ":" + side] ? type + ":" + side : type;
    }

    function rank(order, key) {
        var index = order.indexOf(key);
        return index < 0 ? order.length : index;
    }

    // Distinct, non-empty style keys in the key's row order (GRAPH_STYLES order; unknown keys last, first seen first).
    function orderedKeys(keys) {
        var seen = {}, unique = [];
        (keys || []).forEach(function (key) {
            if (key && !seen[key]) { seen[key] = true; unique.push(key); }
        });
        return unique.map(function (key, index) { return { key: key, index: index }; })
            .sort(function (a, b) { return (rank(STYLE_ORDER, a.key) - rank(STYLE_ORDER, b.key)) || (a.index - b.index); })
            .map(function (item) { return item.key; });
    }

    // The style keys of a list of edges (or links), in the key's row order.
    function styleKeys(edges) {
        return orderedKeys((edges || []).map(styleKey));
    }

    function edgeStyle(edge) {
        return GRAPH_STYLES[styleKey(edge)] || { color: FALLBACK_COLOR, dash: null };
    }

    // What the key prints for one style key. A signed style keeps its own label and meaning (the payload has one
    // definition for both sides); any other type takes the payload's definition (edge_type_definitions) when given.
    function entry(key, definitions) {
        var style = GRAPH_STYLES[key] || { color: FALLBACK_COLOR, dash: null };
        var definition = (!style.side && definitions && definitions[key]) || {};
        return {
            key: key,
            color: style.color,
            dash: style.dash || null,
            label: definition.edge_label || style.label || humanize(key),
            meaning: definition.edge_description || style.meaning || ""
        };
    }

    // The node palette key of a node: its type, or ie_spender for a committee that spends independently.
    function nodeKey(node) {
        return node && node.role === "ie_spender" ? "ie_spender" : String((node && node.node_type) || "");
    }

    function nodeStyle(node) {
        return GRAPH_NODE_STYLES[nodeKey(node)] || { color: FALLBACK_COLOR, shape: "circle" };
    }

    function svgEl(name, attrs) {
        var node = document.createElementNS(SVG_NS, name);
        Object.keys(attrs).forEach(function (attr) {
            if (attrs[attr] !== null && attrs[attr] !== undefined) { node.setAttribute(attr, attrs[attr]); }
        });
        return node;
    }

    function htmlEl(name, className, text) {
        var node = document.createElement(name);
        if (className) { node.setAttribute("class", className); }
        if (text !== undefined) { node.textContent = text; }
        return node;
    }

    function edgeSwatch(item) {
        var svg = svgEl("svg", { "class": "graph-key-swatch", width: 36, height: 10, viewBox: "0 0 36 10", "aria-hidden": "true" });
        svg.appendChild(svgEl("line", { x1: 1, y1: 5, x2: 35, y2: 5, stroke: item.color, "stroke-width": 3,
                                        "stroke-dasharray": item.dash }));
        return svg;
    }

    function nodeSwatch(item) {
        var svg = svgEl("svg", { "class": "graph-key-swatch", width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": "true" });
        var c = 7, r = 5.5, points;
        if (item.shape === "square") {
            svg.appendChild(svgEl("rect", { x: c - r, y: c - r, width: 2 * r, height: 2 * r, rx: 2, fill: item.color }));
            return svg;
        }
        if (item.shape === "diamond") {
            points = [[c, c - r], [c - r, c], [c, c + r], [c + r, c]];
        } else if (item.shape === "triangle") {
            points = [[c, c - r], [c - r, c + r], [c + r, c + r]];
        } else if (item.shape === "hex") {
            points = [0, 1, 2, 3, 4, 5].map(function (i) {
                var angle = Math.PI / 3 * i + Math.PI / 6;
                return [c + r * Math.cos(angle), c + r * Math.sin(angle)];
            });
        }
        svg.appendChild(points
            ? svgEl("polygon", { points: points.map(function (p) { return p[0].toFixed(2) + "," + p[1].toFixed(2); }).join(" "), fill: item.color })
            : svgEl("circle", { cx: c, cy: c, r: r, fill: item.color }));
        return svg;
    }

    // Node specs are palette keys ("candidate") or {type, label, color, shape} overrides, for renderers that colour by
    // something else (region, system) or draw other shapes. Equal label + colour + shape collapse into one item.
    function nodeItems(specs) {
        var seen = {}, items = [];
        (specs || []).forEach(function (spec, index) {
            if (typeof spec === "string") { spec = { type: spec }; }
            var base = GRAPH_NODE_STYLES[spec.type] || {};
            var item = { label: spec.label || base.label || humanize(spec.type), color: spec.color || base.color || FALLBACK_COLOR,
                         shape: spec.shape || base.shape || "circle", order: rank(NODE_ORDER, spec.type) * 1000 + index };
            var id = [item.label, item.color, item.shape].join("|");
            if (!seen[id]) { seen[id] = true; items.push(item); }
        });
        return items.sort(function (a, b) { return a.order - b.order; });
    }

    // renderGraphKey(container, {edgeTypes, definitions, nodeTypes}): edgeTypes are GRAPH_STYLES keys present in the
    // graph (GraphKey.styleKeys(edges)), definitions the payload's edge_type_definitions, nodeTypes node specs.
    // An empty graph empties the container.
    function renderGraphKey(container, options) {
        if (!container) { return; }
        var opts = options || {};
        while (container.firstChild) { container.removeChild(container.firstChild); }
        var edges = orderedKeys(opts.edgeTypes).map(function (key) { return entry(key, opts.definitions); });
        var nodes = nodeItems(opts.nodeTypes);
        if (!edges.length && !nodes.length) { return; }
        container.setAttribute("role", "group");
        container.setAttribute("aria-label", "Graph key");
        if (edges.length) {
            container.appendChild(htmlEl("p", "graph-key-heading", "Lines"));
            var lines = htmlEl("ul", "graph-key-lines");
            edges.forEach(function (item) {
                var row = htmlEl("li", "graph-key-item");
                row.appendChild(edgeSwatch(item));
                row.appendChild(htmlEl("span", "graph-key-label", item.label));
                if (item.meaning) { row.appendChild(htmlEl("span", "graph-key-meaning", item.meaning)); }
                lines.appendChild(row);
            });
            container.appendChild(lines);
        }
        if (nodes.length) {
            container.appendChild(htmlEl("p", "graph-key-heading", "Nodes"));
            var list = htmlEl("ul", "graph-key-nodes");
            nodes.forEach(function (item) {
                var row = htmlEl("li", "graph-key-item");
                row.appendChild(nodeSwatch(item));
                row.appendChild(htmlEl("span", "graph-key-label", item.label));
                list.appendChild(row);
            });
            container.appendChild(list);
        }
    }

    window.GRAPH_STYLES = GRAPH_STYLES;
    window.GRAPH_NODE_STYLES = GRAPH_NODE_STYLES;
    window.renderGraphKey = renderGraphKey;
    window.GraphKey = { styleKey: styleKey, styleKeys: styleKeys, edgeStyle: edgeStyle, entry: entry, nodeKey: nodeKey, nodeStyle: nodeStyle };
})();
