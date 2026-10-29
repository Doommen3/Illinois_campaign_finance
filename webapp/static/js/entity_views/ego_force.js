/* Ego graph view of an entity's network (plan step 7/8): the graph as is, from network.json?view=ego. When d3 and
   js/network_graph.js are loaded it is drawn by the shared readable force renderer (NetworkGraph.render: seeded force
   layout, zoom-to-fit, labels that never overlap, legend chips that hide a node type); otherwise on the fixed radial
   layout below -- the entity's committees and candidates on an inner ring, every neighbour on an outer ring grouped by
   role. Both keep the container (#entity-network-graph) and the data contract. Line and node colours come from the
   site-wide style map (js/graph_legend.js); the key under the graph (ctx.keyBox) explains them.

   Click a node to focus it (plan N19): the node, its edges and its neighbours stay at full opacity with a "focused"
   class, everything else dims, and a "Connected to <node>" list (ctx.focusBox) names the neighbours by edge weight,
   linking them through ctx.hrefs (node id -> page, as the Network tab's edge table links them). Clicking the focused
   node again, the background or pressing Escape clears the focus. The list is drawn by entity_views/focus_panel.js
   (loaded first), which the money flow and vendor network views share. The page's edge table
   (#entity-network-edges, rows with data-source / data-target) is linked: clicking a row focuses its neighbour node,
   and focusing a node highlights its rows (class ng-row-focused). */
(function () {
    window.EntityViews = window.EntityViews || {};
    var NS = "http://www.w3.org/2000/svg";
    var ROLE_ORDER = ["donor", "transfer_source", "ie_spender", "vendor"];
    var escapeHandler = null;  // the one document-level Escape listener; the next render replaces it
    var rowHandler = null;     // the one edge-table click listener; the next render replaces it

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

    function edgeTitle(edge, labels) {
        var side = edge.support_oppose ? (edge.support_oppose === "S" ? ", supporting" : ", opposing") : "";
        return labels[edge.source] + " → " + labels[edge.target] + ": " + money(edge.weight) + " (" + edge.edge_label + side + ")";
    }

    function nodeTitle(node) {
        return node.label + " (" + node.role.replace(/_/g, " ") + "): " + money(node.total_amount || 0);
    }

    function nodeStroke(node) {
        return node.role === "entity" ? { stroke: "#1a365d", width: 2.5 } : { stroke: "#fff", width: 1 };
    }

    // The fixed radial layout (the fallback when d3 is not loaded): returns {svg, nodes, edges} like NetworkGraph.render.
    function drawRadial(container, data, labels, radius, edgeWidth, onNodeClick) {
        var width = Math.max(container.clientWidth || 900, 640), height = 600;
        var cx = width / 2, cy = height / 2, inner = 90, outer = Math.min(width, height) / 2 - 70;
        var core = data.nodes.filter(function (node) { return node.role === "entity" || node.role === "candidate"; });
        var ring = data.nodes.filter(function (node) { return node.role !== "entity" && node.role !== "candidate"; })
            .sort(function (a, b) {
                return (ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role)) || (b.total_amount - a.total_amount);
            });
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
        var edgeEls = [], nodeEls = {};
        data.edges.forEach(function (edge) {
            var s = pos[edge.source], t = pos[edge.target];
            if (!s || !t) { return; }
            var style = window.GraphKey.edgeStyle(edge);
            var line = el("line", {
                x1: s.x, y1: s.y, x2: t.x, y2: t.y, stroke: style.color,
                "stroke-width": edgeWidth(edge), "stroke-opacity": 0.45, "stroke-dasharray": style.dash
            });
            line.appendChild(el("title", {}, edgeTitle(edge, labels)));
            svg.appendChild(line);
            edgeEls.push({ edge: edge, line: line });
        });
        data.nodes.forEach(function (node) {
            var p = pos[node.id], outline = nodeStroke(node);
            var g = el("g", { "class": "ego-node ego-" + node.role, "data-node-id": node.id });
            var circle = el("circle", { cx: p.x, cy: p.y, r: radius(node), fill: window.GraphKey.nodeStyle(node).color,
                                        stroke: outline.stroke, "stroke-width": outline.width });
            g.appendChild(circle);
            nodeEls[node.id] = { g: g, circle: circle, stroke: outline.stroke, strokeWidth: outline.width };
            g.addEventListener("click", function (event) {
                event.stopPropagation();
                onNodeClick(node.id);
            });
            if (labelled[node.id]) {
                var outside = p.angle !== undefined, right = !outside || Math.cos(p.angle) >= 0;
                var r = radius(node);
                var caption = node.label.length > 28 ? node.label.slice(0, 27) + "…" : node.label;
                g.appendChild(el("text", { x: p.x + (right ? r + 4 : -r - 4), y: p.y + 4,
                                           "text-anchor": right ? "start" : "end", "font-size": 11 }, caption));
            }
            g.appendChild(el("title", {}, nodeTitle(node)));
            svg.appendChild(g);
        });
        container.appendChild(svg);
        return { svg: svg, nodes: nodeEls, edges: edgeEls, setTypeVisible: null };
    }

    window.EntityViews.ego = function (container, data, ctx) {
        container.innerHTML = "";
        var keyBox = ctx && ctx.keyBox, focusBox = ctx && ctx.focusBox, hrefs = (ctx && ctx.hrefs) || {};
        window.renderGraphKey(keyBox);  // no options: empties the key until the graph below has drawn
        if (focusBox) { focusBox.textContent = ""; }
        var table = document.getElementById ? document.getElementById("entity-network-edges") : null;
        if (rowHandler && table) { table.removeEventListener("click", rowHandler); }
        rowHandler = null;
        if (!data.nodes || !data.nodes.length) {
            container.textContent = "No network in this window.";
            return;
        }
        var maxAmount = Math.max.apply(null, data.nodes.map(function (node) { return node.total_amount || 0; }).concat([1]));
        var maxWeight = Math.max.apply(null, data.edges.map(function (edge) { return edge.weight; }).concat([1]));
        var radius = function (node) { return 4 + 12 * Math.sqrt((node.total_amount || 0) / maxAmount); };
        var edgeWidth = function (edge) { return Math.max(0.75, 8 * Math.sqrt(edge.weight / maxWeight)); };
        var labels = {}, byId = {}, incident = {}, focus = null;
        data.nodes.forEach(function (node) { labels[node.id] = node.label; byId[node.id] = node; incident[node.id] = []; });
        var toggle = function (id) { setFocus(focus === id ? null : id); };

        var forced = !!(window.d3 && window.NetworkGraph);
        var drawn = forced
            ? window.NetworkGraph.render(container, data, {
                radius: radius,
                color: function (node) { return window.GraphKey.nodeStyle(node).color; },
                nodeType: window.GraphKey.nodeKey,
                nodeStroke: nodeStroke,
                edgeStyle: function (edge) {
                    var style = window.GraphKey.edgeStyle(edge);
                    return { color: style.color, dash: style.dash, width: edgeWidth(edge) };
                },
                nodeTitle: nodeTitle,
                edgeTitle: function (edge) { return edgeTitle(edge, labels); },
                onNodeClick: toggle,
                onBackgroundClick: function () { setFocus(null); },
                ariaLabel: "Ego network of the entity's committees"
            })
            : drawRadial(container, data, labels, radius, edgeWidth, toggle);
        var nodeEls = drawn.nodes, edgeEls = drawn.edges;
        edgeEls.forEach(function (item) {
            incident[item.edge.source].push(item.edge);
            incident[item.edge.target].push(item.edge);
        });

        // Focus: the node, its incident edges and its neighbours keep full opacity (class "focused"); the rest dims.
        var mark = window.EntityViews.markFocus;
        var rows = table && table.querySelectorAll ? Array.prototype.slice.call(table.querySelectorAll("tr[data-source]")) : [];

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
            rows.forEach(function (tr) {
                var on = !!id && (tr.getAttribute("data-source") === id || tr.getAttribute("data-target") === id);
                if (on) { tr.classList.add("ng-row-focused"); } else { tr.classList.remove("ng-row-focused"); }
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

        if (!forced) { drawn.svg.addEventListener("click", function () { setFocus(null); }); }
        if (escapeHandler) { document.removeEventListener("keydown", escapeHandler); }
        escapeHandler = function (event) { if (event.key === "Escape" && focus) { setFocus(null); } };
        document.addEventListener("keydown", escapeHandler);
        if (rows.length) {
            // A row stands for one edge; it focuses the end that is not one of the entity's committees.
            rowHandler = function (event) {
                var tr = event.target.closest ? event.target.closest("tr[data-source]") : null;
                if (!tr || (event.target.closest && event.target.closest("a"))) { return; }
                var source = tr.getAttribute("data-source"), target = tr.getAttribute("data-target");
                var id = byId[source] && byId[source].role !== "entity" ? source : byId[target] ? target : source;
                if (byId[id]) { toggle(id); }
            };
            table.addEventListener("click", rowHandler);
        }

        var note = document.createElement("p");
        note.className = "help-text";
        note.textContent = data.nodes.length + " nodes, " + data.edges.length + " edges; " + (forced
            ? "the entity's committees and candidates sit in the middle, their neighbours around them. Scroll to zoom and"
              + " drag to pan; the largest nodes are labelled, and more labels appear as you zoom in. Hover a node or edge"
              + " for its figures; click a key chip to hide a node type; click a node or a row of the edge table to"
              + " highlight its connections and list them below"
            : "the entity's committees and candidates on the inner ring, neighbours on the outer ring (donors, transfer"
              + " sources, independent spenders, vendors). Hover a node or edge for its figures; click a node to highlight"
              + " its connections and list them below")
            + " (click it again, click the background or press Esc to clear).";
        container.appendChild(note);
        window.renderGraphKey(keyBox, { edgeTypes: window.GraphKey.styleKeys(data.edges), nodeTypes: data.nodes.map(window.GraphKey.nodeKey),
                                        definitions: ctx && ctx.definitions, onToggle: drawn.setTypeVisible || undefined });
    };
})();
