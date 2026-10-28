/* Vendor network view of an entity's network (plan step 8, Phase A): a two-column bipartite graph in plain SVG
   from network.json?view=vendor_network -- the entity's committees and independent spenders on the left, the
   vendors they pay and the candidates they support or oppose on the right. Vendors paid by two or more of the
   entity's committees are highlighted as shared. */
(function () {
    window.EntityViews = window.EntityViews || {};
    var NS = "http://www.w3.org/2000/svg";

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

    function radius(amount, max) {
        return max > 0 ? 5 + 13 * Math.sqrt(Math.max(amount, 0) / max) : 5;
    }

    window.EntityViews.vendor_network = function (container, data) {
        container.innerHTML = "";
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
        var labels = {};
        data.nodes.forEach(function (node) { labels[node.id] = node.label; });
        data.edges.forEach(function (edge) {
            var s = pos[edge.source], t = pos[edge.target];
            if (!s || !t) { return; }
            var ie = edge.edge_type === "independent_expenditure";
            var line = el("line", {
                x1: s.x, y1: s.y, x2: t.x, y2: t.y,
                stroke: ie ? (edge.support_oppose === "S" ? "#38a169" : "#e53e3e") : "#dd6b20",
                "stroke-width": Math.max(1, 10 * Math.sqrt(edge.weight / maxWeight)), "stroke-opacity": 0.5,
                "stroke-dasharray": ie ? "6 4" : null
            });
            var side = ie ? (edge.support_oppose === "S" ? " supporting " : " opposing ") : " → ";
            line.appendChild(el("title", {}, labels[edge.source] + side + labels[edge.target] + ": " + money(edge.weight)
                + " (" + edge.count + " rows)"));
            svg.appendChild(line);
        });
        data.nodes.forEach(function (node) {
            var p = pos[node.id], shared = node.shared === true;
            var fill = node.node_type === "vendor" ? (shared ? "#f6ad55" : "#fbd38d")
                : node.node_type === "candidate" ? "#68d391" : node.role === "entity" ? "#2b6cb0" : "#9f7aea";
            var g = el("g", { "class": "vendor-network-node" + (shared ? " shared" : "") });
            g.appendChild(el("circle", { cx: p.x, cy: p.y, r: radius(node.total_amount, maxAmount), fill: fill,
                                         stroke: shared ? "#c05621" : "#fff", "stroke-width": shared ? 3 : 1 }));
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

        var note = document.createElement("p");
        note.className = "help-text";
        note.textContent = (data.shared_vendors.length
            ? data.shared_vendors.length + " vendor(s) paid by two or more of the entity's committees are outlined. "
            : "No vendor is shared by two of the entity's committees. ")
            + "Solid: payments to vendors; dashed: independent expenditures supporting (green) or opposing (red) a candidate.";
        container.appendChild(note);
    };
})();
