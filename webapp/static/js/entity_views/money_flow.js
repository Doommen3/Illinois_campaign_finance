/* Money flow view of an entity's network (plan step 8, Phase A): a three-column sankey drawn in plain SVG from
   network.json?view=money_flow -- sources (donor classes, in-kind, transfer sources, independent spenders) on the
   left, the entity's committees and the candidates named by independent spending in the middle, vendors on the
   right. Band width and node height are proportional to dollars on one shared scale. */
(function () {
    window.EntityViews = window.EntityViews || {};
    var NS = "http://www.w3.org/2000/svg";
    var COLORS = {
        donor_committee: "#3182ce", in_kind_donor_committee: "#63b3ed", transfer_in: "#805ad5",
        committee_vendor: "#dd6b20", independent_expenditure: "#38a169"
    };

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

    window.EntityViews.money_flow = function (container, data) {
        container.innerHTML = "";
        if (!data.links || !data.links.length) {
            container.textContent = "No money flows in this window.";
            return;
        }
        var labels = {};
        data.nodes.forEach(function (node) { labels[node.id] = node.label; });

        var width = Math.max(container.clientWidth || 900, 640), height = 540, pad = 28, nodeW = 14, gap = 10;
        var columns = [[], [], []];
        data.nodes.forEach(function (node) { columns[node.column].push(node); });
        var scale = Infinity;
        columns.forEach(function (column) {
            var total = column.reduce(function (sum, node) { return sum + node.amount; }, 0);
            if (total > 0) { scale = Math.min(scale, (height - 2 * pad - gap * (column.length - 1)) / total); }
        });
        if (!isFinite(scale)) { scale = 0; }

        var xs = [pad, width / 2 - nodeW / 2, width - pad - nodeW], pos = {};
        columns.forEach(function (column, index) {
            var y = pad;
            column.forEach(function (node) {
                var h = Math.max(4, node.amount * scale);
                pos[node.id] = { x: xs[index], y: y, h: h, out: 0, inn: 0 };
                y += h + gap;
            });
        });

        var svg = el("svg", { viewBox: "0 0 " + width + " " + height, width: "100%", height: height, role: "img",
                              "aria-label": "Money flow of the entity's committees" });
        var links = data.links.slice().sort(function (a, b) {
            return (pos[a.source].y - pos[b.source].y) || (pos[a.target].y - pos[b.target].y);
        });
        links.forEach(function (link) {
            var s = pos[link.source], t = pos[link.target];
            if (!s || !t) { return; }
            var w = Math.max(1, link.weight * scale);
            var y0 = s.y + s.out + w / 2, y1 = t.y + t.inn + w / 2;
            s.out += w; t.inn += w;
            var x0 = s.x + nodeW, x1 = t.x, mx = (x0 + x1) / 2;
            var path = el("path", {
                d: "M" + x0 + "," + y0 + " C" + mx + "," + y0 + " " + mx + "," + y1 + " " + x1 + "," + y1,
                fill: "none", stroke: COLORS[link.edge_type] || "#718096", "stroke-width": w, "stroke-opacity": 0.45,
                "stroke-dasharray": link.support_oppose === "O" ? "6 4" : null
            });
            var side = link.support_oppose ? (link.support_oppose === "S" ? ", supporting" : ", opposing") : "";
            path.appendChild(el("title", {}, labels[link.source] + " → " + labels[link.target] + ": " + money(link.weight)
                + " (" + link.edge_label + side + ", " + link.count + " rows)"));
            svg.appendChild(path);
        });
        data.nodes.forEach(function (node) {
            var p = pos[node.id], g = el("g", { "class": "money-flow-node money-flow-" + node.kind });
            g.appendChild(el("rect", { x: p.x, y: p.y, width: nodeW, height: p.h, rx: 2,
                                       fill: node.column === 1 ? "#2d3748" : "#a0aec0" }));
            var anchor = node.column === 2 ? "end" : "start";
            var tx = node.column === 2 ? p.x - 6 : p.x + nodeW + 6;
            var text = el("text", { x: tx, y: p.y + Math.min(p.h / 2, 14) + 4, "text-anchor": anchor, "font-size": 12 },
                          node.label.length > 34 ? node.label.slice(0, 33) + "…" : node.label);
            g.appendChild(text);
            g.appendChild(el("title", {}, node.label + ": " + money(node.amount)));
            svg.appendChild(g);
        });
        container.appendChild(svg);

        var legend = document.createElement("p");
        legend.className = "help-text";
        var parts = [];
        Object.keys(data.totals).forEach(function (key) {
            if (key === "support" || key === "oppose") { return; }
            parts.push(key.replace(/_/g, " ") + " " + money(data.totals[key]));
        });
        if (data.totals.support || data.totals.oppose) {
            parts.push("independent spending for " + money(data.totals.support) + " / against " + money(data.totals.oppose) + " (dashed)");
        }
        var notes = [];
        var folded = Object.keys(data.folded || {}).filter(function (key) { return key !== "internal" && data.folded[key]; })
            .map(function (key) { return data.folded[key] + " " + key.replace(/_/g, " ") + " grouped as Other"; });
        if (folded.length) { notes.push(folded.join(", ")); }
        // The ego graph keeps only the top donors, so the donor-class totals above are partial when donors were dropped;
        // the class nodes are labelled "Top N ... donors" and N is read back from those labels.
        var dropped = Number((data.truncated || {}).donors) || 0;
        if (dropped > 0) {
            var shown = 0;
            data.nodes.forEach(function (node) {
                var match = node.kind === "donor_class" ? /^Top (\d+) /.exec(node.label) : null;
                if (match) { shown += Number(match[1]); }
            });
            notes.push("Top " + shown + " donors shown; " + dropped + " more donor" + (dropped === 1 ? "" : "s") + " not shown");
        }
        var internal = (data.folded || {}).internal;
        if (internal && internal.count > 0) {
            notes.push(internal.count + " row" + (internal.count === 1 ? "" : "s") + " (" + money(internal.amount)
                + ") moving money between this entity's own committees not drawn");
        }
        legend.textContent = parts.join("; ") + (notes.length ? ". " + notes.join(". ") + "." : ".");
        container.appendChild(legend);
    };
})();
