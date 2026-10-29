/* Money flow view of an entity's network (plan step 8, Phase A): a three-column sankey drawn in plain SVG from
   network.json?view=money_flow -- sources (donor classes, in-kind, transfer sources, independent spenders) on the
   left, the entity's committees and the candidates named by independent spending in the middle, vendors on the
   right. Band width and node height are proportional to dollars on one shared scale. Band colour and dash come from the
   site-wide style map (js/graph_legend.js), and the key under the graph (ctx.keyBox) explains them.

   Click a node to drill down (plan .claude/plans/money-flow-drilldown.md, D1-D5; the panel is entity_views/
   focus_panel.js in ctx.focusBox): a group node (a donor class, in-kind, an "Other" node) lists who is inside it from
   data.members, saying when the ego graph's caps (data.truncated) left more out and linking a capped donor group to
   the Donors tab (ctx.donorsTabUrl); any other node lists its flows. Its bands get class "focused", the rest dim.
   Clicking it again, the background or pressing Escape clears the panel. */
(function () {
    window.EntityViews = window.EntityViews || {};
    var NS = "http://www.w3.org/2000/svg";
    var escapeHandler = null;  // the one document-level Escape listener; the next render replaces it
    // Group nodes: [singular, plural] for the cap note, and the ``truncated`` class whose dropped count it reports.
    var DONOR_GROUPS = { "class:individual": ["individual donor", "individual donors"],
                         "class:organization": ["organization donor", "organization donors"],
                         "class:in_kind": ["in-kind donor", "in-kind donors"] };
    var OTHER_GROUPS = { "other:vendors": ["vendor", "vendors", "vendors"],
                         "other:transfers": ["transfer source", "transfer sources", "transfers"],
                         "other:spenders": ["independent spender", "independent spenders", "ie_spenders"] };

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

    // The cap note of a group node: how many the graph holds and how many more the ego graph's caps left out.
    function capNote(id, members, truncated, donorsTabUrl) {
        var shown = Object.keys(members.reduce(function (seen, member) { seen[member.id] = true; return seen; }, {})).length;
        var donor = DONOR_GROUPS[id], other = OTHER_GROUPS[id];
        var dropped = Number((truncated || {})[donor ? "donors" : other ? other[2] : ""]) || 0;
        if (!dropped) { return {}; }
        var noun = (donor || other)[shown === 1 ? 0 : 1];
        if (donor) {
            return { note: "Top " + shown + " " + noun + " in this graph; " + dropped + " more donor" + (dropped === 1 ? "" : "s") + " not shown.",
                     link: donorsTabUrl ? { text: "See all donors", href: donorsTabUrl } : null };
        }
        return { note: shown + " " + noun + " grouped here; " + dropped + " more " + other[dropped === 1 ? 0 : 1] + " not in this graph." };
    }

    window.EntityViews.money_flow = function (container, data, ctx) {
        container.innerHTML = "";
        var keyBox = ctx && ctx.keyBox, definitions = ctx && ctx.definitions;
        var focusBox = ctx && ctx.focusBox, hrefs = (ctx && ctx.hrefs) || {};
        window.renderGraphKey(keyBox);  // no options: empties the key until the graph below has drawn
        if (focusBox) { focusBox.textContent = ""; }
        if (!data.links || !data.links.length) {
            container.textContent = "No money flows in this window.";
            return;
        }
        var labels = {}, byId = {}, bands = [], focus = null;
        data.nodes.forEach(function (node) { labels[node.id] = node.label; byId[node.id] = node; });

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
            var style = window.GraphKey.edgeStyle(link);
            var path = el("path", {
                d: "M" + x0 + "," + y0 + " C" + mx + "," + y0 + " " + mx + "," + y1 + " " + x1 + "," + y1,
                fill: "none", stroke: style.color, "stroke-width": w, "stroke-opacity": 0.45, "stroke-dasharray": style.dash
            });
            var side = link.support_oppose ? (link.support_oppose === "S" ? ", supporting" : ", opposing") : "";
            path.appendChild(el("title", {}, labels[link.source] + " → " + labels[link.target] + ": " + money(link.weight)
                + " (" + link.edge_label + side + ", " + link.count + " rows)"));
            svg.appendChild(path);
            bands.push({ link: link, path: path });
        });
        data.nodes.forEach(function (node) {
            var p = pos[node.id], g = el("g", { "class": "money-flow-node money-flow-" + node.kind, "data-node-id": node.id });
            g.addEventListener("click", function (event) {
                event.stopPropagation();
                setFocus(focus === node.id ? null : node.id);
            });
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

        function setFocus(id) {
            focus = id;
            bands.forEach(function (item) {
                var touches = item.link.source === id || item.link.target === id;
                window.EntityViews.markFocus(item.path, id && touches, id && !touches);
            });
            if (!id) { window.EntityViews.clearFocus(focusBox); return; }
            var members = (data.members || {})[id];
            if (members) {
                var sided = id === "other:spenders", cap = capNote(id, members, data.truncated, ctx && ctx.donorsTabUrl);
                window.EntityViews.focusPanel(focusBox, {
                    heading: "Inside " + byId[id].label, note: cap.note, link: cap.link,
                    columns: ["Name", "Amount", "Rows"].concat(sided ? ["Side"] : []),
                    rows: members.map(function (member) {
                        var side = member.support_oppose === "S" ? "for" : member.support_oppose === "O" ? "against" : "";
                        return { label: member.label, href: hrefs[member.id],
                                 cells: [money(member.amount), rowText(member.count)].concat(sided ? [side] : []) };
                    })
                });
                return;
            }
            var flows = bands.map(function (item) { return item.link; })
                .filter(function (link) { return link.source === id || link.target === id; })
                .map(function (link) { return { link: link, other: link.source === id ? link.target : link.source }; })
                .sort(function (a, b) { return (b.link.weight - a.link.weight) || String(labels[a.other]).localeCompare(String(labels[b.other])); });
            window.EntityViews.focusPanel(focusBox, {
                heading: "Flows of " + byId[id].label, empty: "No flows in this window.",
                columns: ["Name", "Amount", "Rows", "Flow"],
                rows: flows.map(function (flow) {
                    return { label: labels[flow.other], href: hrefs[flow.other],
                             cells: [money(flow.link.weight), rowText(flow.link.count),
                                     window.GraphKey.entry(window.GraphKey.styleKey(flow.link), definitions).label] };
                })
            });
        }

        svg.addEventListener("click", function () { setFocus(null); });
        if (escapeHandler) { document.removeEventListener("keydown", escapeHandler); }
        escapeHandler = function (event) { if (event.key === "Escape" && focus) { setFocus(null); } };
        document.addEventListener("keydown", escapeHandler);

        var legend = document.createElement("p");
        legend.className = "help-text";
        var parts = [];
        Object.keys(data.totals).forEach(function (key) {
            if (key === "support" || key === "oppose") { return; }
            parts.push(key.replace(/_/g, " ") + " " + money(data.totals[key]));
        });
        if (data.totals.support || data.totals.oppose) {
            parts.push("independent spending for " + money(data.totals.support) + " / against " + money(data.totals.oppose));
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
        legend.textContent = parts.join("; ") + (notes.length ? ". " + notes.join(". ") + "." : ".")
            + " Click a node to list who is inside a group or the flows of any other node"
            + " (click it again, click the background or press Esc to clear).";
        container.appendChild(legend);
        window.renderGraphKey(keyBox, { edgeTypes: window.GraphKey.styleKeys(data.links), definitions: definitions });
    };
})();
