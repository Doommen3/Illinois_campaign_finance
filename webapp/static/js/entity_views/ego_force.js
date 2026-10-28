/* Ego graph view of an entity's network (plan step 8, Phase A): the graph as is, drawn in plain SVG from
   network.json?view=ego on a fixed radial layout -- the entity's committees and candidates on an inner ring,
   every neighbour on an outer ring grouped by role. This is the Phase A stand-in: plan step 7 replaces the body
   of this renderer with the shared readable force renderer (webapp/static/js/network_graph.js) and keeps the
   container (#entity-network-graph) and the data contract. */
(function () {
    window.EntityViews = window.EntityViews || {};
    var NS = "http://www.w3.org/2000/svg";
    var FILL = { committee: "#2b6cb0", candidate: "#68d391", donor: "#63b3ed", vendor: "#f6ad55" };
    var STROKE = {
        donor_committee: "#3182ce", in_kind_donor_committee: "#63b3ed", transfer_in: "#805ad5",
        committee_vendor: "#dd6b20", independent_expenditure: "#38a169", committee_candidate: "#718096"
    };
    var ROLE_ORDER = ["donor", "transfer_source", "ie_spender", "vendor"];

    function el(name, attrs, text) {
        var node = document.createElementNS(NS, name);
        Object.keys(attrs || {}).forEach(function (key) {
            if (attrs[key] !== null && attrs[key] !== undefined) { node.setAttribute(key, attrs[key]); }
        });
        if (text !== undefined) { node.textContent = text; }
        return node;
    }

    function money(value) {
        return "$" + Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    window.EntityViews.ego = function (container, data) {
        container.innerHTML = "";
        if (!data.nodes || !data.nodes.length) {
            container.textContent = "No network in this window.";
            return;
        }
        var width = Math.max(container.clientWidth || 900, 640), height = 600;
        var cx = width / 2, cy = height / 2, inner = 90, outer = Math.min(width, height) / 2 - 70;
        var core = data.nodes.filter(function (node) { return node.role === "entity" || node.role === "candidate"; });
        var ring = data.nodes.filter(function (node) { return node.role !== "entity" && node.role !== "candidate"; })
            .sort(function (a, b) {
                return (ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role)) || (b.total_amount - a.total_amount);
            });
        var maxAmount = Math.max.apply(null, data.nodes.map(function (node) { return node.total_amount || 0; }).concat([1]));
        var maxWeight = Math.max.apply(null, data.edges.map(function (edge) { return edge.weight; }).concat([1]));
        var pos = {};
        core.forEach(function (node, index) {
            var angle = core.length > 1 ? -Math.PI / 2 + 2 * Math.PI * index / core.length : 0;
            pos[node.id] = core.length > 1 ? { x: cx + inner * Math.cos(angle), y: cy + inner * Math.sin(angle) } : { x: cx, y: cy };
        });
        ring.forEach(function (node, index) {
            var angle = -Math.PI / 2 + 2 * Math.PI * index / ring.length;
            pos[node.id] = { x: cx + outer * Math.cos(angle), y: cy + outer * Math.sin(angle), angle: angle };
        });
        var labelled = {};
        data.nodes.slice().sort(function (a, b) { return b.total_amount - a.total_amount; }).slice(0, 14)
            .forEach(function (node) { labelled[node.id] = true; });
        core.forEach(function (node) { labelled[node.id] = true; });

        var svg = el("svg", { viewBox: "0 0 " + width + " " + height, width: "100%", height: height, role: "img",
                              "aria-label": "Ego network of the entity's committees" });
        var labels = {};
        data.nodes.forEach(function (node) { labels[node.id] = node.label; });
        data.edges.forEach(function (edge) {
            var s = pos[edge.source], t = pos[edge.target];
            if (!s || !t) { return; }
            var line = el("line", {
                x1: s.x, y1: s.y, x2: t.x, y2: t.y, stroke: STROKE[edge.edge_type] || "#a0aec0",
                "stroke-width": Math.max(0.75, 8 * Math.sqrt(edge.weight / maxWeight)), "stroke-opacity": 0.45,
                "stroke-dasharray": edge.support_oppose === "O" ? "6 4" : null
            });
            var side = edge.support_oppose ? (edge.support_oppose === "S" ? ", supporting" : ", opposing") : "";
            line.appendChild(el("title", {}, labels[edge.source] + " → " + labels[edge.target] + ": " + money(edge.weight)
                + " (" + edge.edge_label + side + ")"));
            svg.appendChild(line);
        });
        data.nodes.forEach(function (node) {
            var p = pos[node.id];
            var r = 4 + 12 * Math.sqrt((node.total_amount || 0) / maxAmount);
            var g = el("g", { "class": "ego-node ego-" + node.role });
            g.appendChild(el("circle", { cx: p.x, cy: p.y, r: r, fill: FILL[node.node_type] || "#a0aec0",
                                         stroke: node.role === "entity" ? "#1a365d" : "#fff", "stroke-width": node.role === "entity" ? 2.5 : 1 }));
            if (labelled[node.id]) {
                var outside = p.angle !== undefined, right = !outside || Math.cos(p.angle) >= 0;
                var caption = node.label.length > 28 ? node.label.slice(0, 27) + "…" : node.label;
                g.appendChild(el("text", { x: p.x + (right ? r + 4 : -r - 4), y: p.y + 4,
                                           "text-anchor": right ? "start" : "end", "font-size": 11 }, caption));
            }
            g.appendChild(el("title", {}, node.label + " (" + node.role.replace(/_/g, " ") + "): " + money(node.total_amount || 0)));
            svg.appendChild(g);
        });
        container.appendChild(svg);

        var note = document.createElement("p");
        note.className = "help-text";
        note.textContent = data.nodes.length + " nodes, " + data.edges.length + " edges; the entity's committees and candidates on the inner ring,"
            + " neighbours on the outer ring (donors, transfer sources, independent spenders, vendors). Hover a node or edge for its figures.";
        container.appendChild(note);
    };
})();
