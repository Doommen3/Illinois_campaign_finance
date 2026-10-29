/* Vendor network view of an entity's network (plan step 8, Phase A): a two-column bipartite graph in plain SVG
   from network.json?view=vendor_network -- the entity's committees and independent spenders on the left, the
   vendors they pay and the candidates they support or oppose on the right. Vendors paid by two or more of the
   entity's committees are highlighted as shared. Line and node colours come from the site-wide style map
   (js/graph_legend.js); the key under the graph (ctx.keyBox) explains them.

   Click a node to drill down (plan .claude/plans/money-flow-drilldown.md, D3; the panel is entity_views/
   focus_panel.js in ctx.focusBox): a vendor lists the committees that paid it (and says when it is shared), a
   committee or spender what it paid or spent on, a candidate the spending for and against it. Its lines get class
   "focused", the rest dim; clicking it again, the background or pressing Escape clears the panel. */
(function () {
    window.EntityViews = window.EntityViews || {};
    var NS = "http://www.w3.org/2000/svg";
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

    function rowText(count) {
        return count + (count === 1 ? " row" : " rows");
    }

    function heading(node) {
        if (node.side === "payer") { return "Spending by " + node.label; }
        return (node.node_type === "vendor" ? "Paid to " : "Independent spending on ") + node.label;
    }

    function radius(amount, max) {
        return max > 0 ? 5 + 13 * Math.sqrt(Math.max(amount, 0) / max) : 5;
    }

    window.EntityViews.vendor_network = function (container, data, ctx) {
        container.innerHTML = "";
        var keyBox = ctx && ctx.keyBox, definitions = ctx && ctx.definitions;
        var focusBox = ctx && ctx.focusBox, hrefs = (ctx && ctx.hrefs) || {};
        window.renderGraphKey(keyBox);  // no options: empties the key until the graph below has drawn
        if (focusBox) { focusBox.textContent = ""; }
        if (!data.edges || !data.edges.length) {
            container.textContent = "No vendor payments or independent expenditures in this window.";
            return;
        }
        var payers = data.nodes.filter(function (node) { return node.side === "payer"; });
        var payees = data.nodes.filter(function (node) { return node.side === "payee"; });
        var width = Math.max(container.clientWidth || 900, 640);
        var rows = Math.max(payers.length, payees.length), rowH = 34, pad = 30;
        var height = Math.max(240, pad * 2 + rows * rowH);
        var maxAmount = Math.max.apply(null, data.nodes.map(function (node) { return node.total_amount || 0; }).concat([0]));
        var maxWeight = Math.max.apply(null, data.edges.map(function (edge) { return edge.weight; }).concat([1]));
        var pos = {}, xLeft = pad + 180, xRight = width - pad - 180;
        function place(nodes, x) {
            var step = nodes.length > 1 ? (height - 2 * pad) / (nodes.length - 1) : 0;
            nodes.forEach(function (node, index) {
                pos[node.id] = { x: x, y: nodes.length > 1 ? pad + index * step : height / 2 };
            });
        }
        place(payers, xLeft);
        place(payees, xRight);

        var svg = el("svg", { viewBox: "0 0 " + width + " " + height, width: "100%", height: height, role: "img",
                              "aria-label": "Vendors and independent spending around the entity's committees" });
        var labels = {}, byId = {}, lines = [], focus = null;
        data.nodes.forEach(function (node) { labels[node.id] = node.label; byId[node.id] = node; });
        data.edges.forEach(function (edge) {
            var s = pos[edge.source], t = pos[edge.target];
            if (!s || !t) { return; }
            var ie = edge.edge_type === "independent_expenditure";
            var style = window.GraphKey.edgeStyle(edge);
            var line = el("line", {
                x1: s.x, y1: s.y, x2: t.x, y2: t.y, stroke: style.color,
                "stroke-width": Math.max(1, 10 * Math.sqrt(edge.weight / maxWeight)), "stroke-opacity": 0.5,
                "stroke-dasharray": style.dash
            });
            var side = ie ? (edge.support_oppose === "S" ? " supporting " : " opposing ") : " → ";
            line.appendChild(el("title", {}, labels[edge.source] + side + labels[edge.target] + ": " + money(edge.weight)
                + " (" + edge.count + " rows)"));
            svg.appendChild(line);
            lines.push({ edge: edge, line: line });
        });
        data.nodes.forEach(function (node) {
            var p = pos[node.id], shared = node.shared === true;
            var g = el("g", { "class": "vendor-network-node" + (shared ? " shared" : ""), "data-node-id": node.id });
            g.addEventListener("click", function (event) {
                event.stopPropagation();
                setFocus(focus === node.id ? null : node.id);
            });
            g.appendChild(el("circle", { cx: p.x, cy: p.y, r: radius(node.total_amount, maxAmount), fill: window.GraphKey.nodeStyle(node).color,
                                         stroke: shared ? "#1a202c" : "#fff", "stroke-width": shared ? 3 : 1 }));
            var left = node.side === "payer";
            var caption = node.label.length > 30 ? node.label.slice(0, 29) + "…" : node.label;
            if (shared) { caption += " (shared by " + node.committee_count + ")"; }
            g.appendChild(el("text", { x: left ? p.x - 22 : p.x + 22, y: p.y + 4, "text-anchor": left ? "end" : "start",
                                       "font-size": 12, "font-weight": shared ? "bold" : null }, caption));
            g.appendChild(el("title", {}, node.label + ": " + money(node.total_amount || 0)
                + (shared ? "; paid by " + node.committee_count + " of the entity's committees" : "")));
            svg.appendChild(g);
        });
        container.appendChild(svg);

        function setFocus(id) {
            focus = id;
            lines.forEach(function (item) {
                var touches = item.edge.source === id || item.edge.target === id;
                window.EntityViews.markFocus(item.line, id && touches, id && !touches);
            });
            if (!id) { window.EntityViews.clearFocus(focusBox); return; }
            var node = byId[id];
            var flows = lines.map(function (item) { return item.edge; })
                .filter(function (edge) { return edge.source === id || edge.target === id; })
                .map(function (edge) { return { edge: edge, other: edge.source === id ? edge.target : edge.source }; })
                .sort(function (a, b) { return (b.edge.weight - a.edge.weight) || String(labels[a.other]).localeCompare(String(labels[b.other])); });
            window.EntityViews.focusPanel(focusBox, {
                heading: heading(node), empty: "No payments in this window.",
                note: node.shared ? "Shared by " + node.committee_count + " of this entity's committees." : null,
                columns: ["Name", "Amount", "Rows", "Flow"],
                rows: flows.map(function (flow) {
                    return { label: labels[flow.other], href: hrefs[flow.other],
                             cells: [money(flow.edge.weight), rowText(flow.edge.count),
                                     window.GraphKey.entry(window.GraphKey.styleKey(flow.edge), definitions).label] };
                })
            });
        }

        svg.addEventListener("click", function () { setFocus(null); });
        if (escapeHandler) { document.removeEventListener("keydown", escapeHandler); }
        escapeHandler = function (event) { if (event.key === "Escape" && focus) { setFocus(null); } };
        document.addEventListener("keydown", escapeHandler);

        var note = document.createElement("p");
        note.className = "help-text";
        note.textContent = (data.shared_vendors.length
            ? data.shared_vendors.length + " vendor(s) paid by two or more of the entity's committees are outlined. "
            : "No vendor is shared by two of the entity's committees. ")
            + "The key below explains the lines. Click a node to list its payments or independent spending"
            + " (click it again, click the background or press Esc to clear).";
        container.appendChild(note);
        window.renderGraphKey(keyBox, { edgeTypes: window.GraphKey.styleKeys(data.edges), definitions: definitions,
                                        nodeTypes: data.nodes.map(window.GraphKey.nodeKey) });
    };
})();
