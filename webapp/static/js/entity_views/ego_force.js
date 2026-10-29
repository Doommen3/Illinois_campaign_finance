/* Ego graph view of an entity's network (plan step 8, Phase A): the graph as is, drawn in plain SVG from
   network.json?view=ego on a fixed radial layout -- the entity's committees and candidates on an inner ring,
   every neighbour on an outer ring grouped by role. This is the Phase A stand-in: plan step 7 replaces the body
   of this renderer with the shared readable force renderer (webapp/static/js/network_graph.js) and keeps the
   container (#entity-network-graph) and the data contract. Line and node colours come from the site-wide style map
   (js/graph_legend.js); the key under the graph (ctx.keyBox) explains them.

   Click a node to focus it (plan N19): the node, its edges and its neighbours stay at full opacity with a "focused"
   class, everything else dims, and a "Connected to <node>" list (ctx.focusBox) names the neighbours by edge weight,
   linking them through ctx.hrefs (node id -> page, as the Network tab's edge table links them). Clicking the focused
   node again, the background or pressing Escape clears the focus. The list is drawn by entity_views/focus_panel.js
   (loaded first), which the money flow and vendor network views share. */
(function () {
    window.EntityViews = window.EntityViews || {};
    var NS = "http://www.w3.org/2000/svg";
    var ROLE_ORDER = ["donor", "transfer_source", "ie_spender", "vendor"];
    var escapeHandler = null;  // the one document-level Escape listener; the next render replaces it

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

    window.EntityViews.ego = function (container, data, ctx) {
        container.innerHTML = "";
        var keyBox = ctx && ctx.keyBox, focusBox = ctx && ctx.focusBox, hrefs = (ctx && ctx.hrefs) || {};
        window.renderGraphKey(keyBox);  // no options: empties the key until the graph below has drawn
        if (focusBox) { focusBox.textContent = ""; }
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
        var labels = {}, byId = {}, incident = {}, edgeEls = [], nodeEls = {}, focus = null;
        data.nodes.forEach(function (node) { labels[node.id] = node.label; byId[node.id] = node; incident[node.id] = []; });
        data.edges.forEach(function (edge) {
            var s = pos[edge.source], t = pos[edge.target];
            if (!s || !t) { return; }
            var style = window.GraphKey.edgeStyle(edge);
            var line = el("line", {
                x1: s.x, y1: s.y, x2: t.x, y2: t.y, stroke: style.color,
                "stroke-width": Math.max(0.75, 8 * Math.sqrt(edge.weight / maxWeight)), "stroke-opacity": 0.45,
                "stroke-dasharray": style.dash
            });
            var side = edge.support_oppose ? (edge.support_oppose === "S" ? ", supporting" : ", opposing") : "";
            line.appendChild(el("title", {}, labels[edge.source] + " → " + labels[edge.target] + ": " + money(edge.weight)
                + " (" + edge.edge_label + side + ")"));
            svg.appendChild(line);
            edgeEls.push({ edge: edge, line: line });
            incident[edge.source].push(edge);
            incident[edge.target].push(edge);
        });
        data.nodes.forEach(function (node) {
            var p = pos[node.id];
            var r = 4 + 12 * Math.sqrt((node.total_amount || 0) / maxAmount);
            var g = el("g", { "class": "ego-node ego-" + node.role, "data-node-id": node.id });
            var stroke = node.role === "entity" ? "#1a365d" : "#fff", strokeWidth = node.role === "entity" ? 2.5 : 1;
            var circle = el("circle", { cx: p.x, cy: p.y, r: r, fill: window.GraphKey.nodeStyle(node).color,
                                        stroke: stroke, "stroke-width": strokeWidth });
            g.appendChild(circle);
            nodeEls[node.id] = { g: g, circle: circle, stroke: stroke, strokeWidth: strokeWidth };
            g.addEventListener("click", function (event) {
                event.stopPropagation();
                setFocus(focus === node.id ? null : node.id);
            });
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

        // Focus: the node, its incident edges and its neighbours keep full opacity (class "focused"); the rest dims.
        var mark = window.EntityViews.markFocus;

        function setFocus(id) {
            focus = id;
            var near = {};
            if (id) {
                near[id] = true;
                incident[id].forEach(function (edge) { near[edge.source] = true; near[edge.target] = true; });
            }
            data.nodes.forEach(function (node) {
                var item = nodeEls[node.id];
                mark(item.g, id && near[node.id], id && !near[node.id]);
                item.circle.setAttribute("stroke", id === node.id ? "#1a202c" : item.stroke);
                item.circle.setAttribute("stroke-width", id === node.id ? 3 : item.strokeWidth);
            });
            edgeEls.forEach(function (item) {
                var touches = item.edge.source === id || item.edge.target === id;
                mark(item.line, id && touches, id && !touches);
            });
            showList(id);
        }

        function showList(id) {
            if (!id) { window.EntityViews.clearFocus(focusBox); return; }
            var rows = incident[id].map(function (edge) { return { edge: edge, other: byId[edge.source === id ? edge.target : edge.source] }; })
                .sort(function (a, b) { return (b.edge.weight - a.edge.weight) || String(a.other.label).localeCompare(String(b.other.label)); });
            window.EntityViews.focusPanel(focusBox, {
                heading: "Connected to " + byId[id].label,
                empty: "No connections in this window.",
                columns: ["Name", "Relationship", "Amount", "Rows"],
                rows: rows.map(function (row) {
                    var count = row.edge.count;
                    return { label: row.other.label, href: hrefs[row.other.id],
                             cells: [window.GraphKey.entry(window.GraphKey.styleKey(row.edge), ctx && ctx.definitions).label,
                                     money(row.edge.weight), count + (count === 1 ? " row" : " rows")],
                             drill: window.EntityViews.drill(row.edge.edge_type, id, row.other.id, 2) };
                }),
                rowsUrl: ctx && ctx.rowsUrl
            });
        }

        svg.addEventListener("click", function () { setFocus(null); });
        if (escapeHandler) { document.removeEventListener("keydown", escapeHandler); }
        escapeHandler = function (event) { if (event.key === "Escape" && focus) { setFocus(null); } };
        document.addEventListener("keydown", escapeHandler);

        var note = document.createElement("p");
        note.className = "help-text";
        note.textContent = data.nodes.length + " nodes, " + data.edges.length + " edges; the entity's committees and candidates on the inner ring,"
            + " neighbours on the outer ring (donors, transfer sources, independent spenders, vendors). Hover a node or edge for its figures;"
            + " click a node to highlight its connections and list them below (click it again, click the background or press Esc to clear).";
        container.appendChild(note);
        window.renderGraphKey(keyBox, { edgeTypes: window.GraphKey.styleKeys(data.edges), nodeTypes: data.nodes.map(window.GraphKey.nodeKey),
                                        definitions: ctx && ctx.definitions });
    };
})();
