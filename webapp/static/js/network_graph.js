/* Readable force-directed network renderer (plan .claude/plans/race-sort-and-entity-networks.md N7; the N8 probe in
   tests/integration/test_network_graph_readability.py enforces it). Needs d3 v7 (js/vendor/d3.v7.min.js).

   NetworkGraph.render(container, {nodes, edges}, options) draws into container and returns
   {svg, nodes: {id: {g, circle, stroke, strokeWidth}}, edges: [{edge, line}], setTypeVisible(type, visible)}.

   - Layout: d3.forceSimulation seeded on a radial layout by role (the entity's committees in the middle, candidates
     around them, neighbours outside), link distance by weight rank, forceManyBody, forceCollide(r + LABEL_PAD) and a
     fixed seeded random source; TICKS ticks run synchronously, then a separation pass leaves no two circles closer than
     their radii plus LABEL_PAD. The same data always gives the same picture. svg[data-settled="1"] marks the end.
   - Zoom: d3.zoom; the initial transform fits every node inside the container. Labels stay FONT px on screen at any
     zoom (semantic zoom): the top nodes by total_amount get a label, more as you zoom in, each placed right, left,
     above or below its node where it overlaps no other label, no other circle and stays inside the svg; the rest stay
     hidden. Every node has a <title>, and hovering a node shows its label.
   - setTypeVisible(type, visible) hides or shows the nodes of one type (options.nodeType(node)) and their edges.

   options: radius(node), color(node), nodeType(node), nodeStroke(node) -> {stroke, width}, edgeStyle(edge) ->
   {color, dash, width}, nodeTitle(node), edgeTitle(edge), onNodeClick(id), onBackgroundClick(), height, ariaLabel. */
(function () {
    var NS = "http://www.w3.org/2000/svg";
    var FONT = 12, LABEL_PAD = 6, GAP = 3, MARGIN = 24, LABELS_AT_FIT = 24, TICKS = 300, HEIGHT = 600, MAX_FIT = 2.5;
    var ROLE_RING = { entity: 0, candidate: 1 };

    function el(name, attrs, text) {
        var node = document.createElementNS(NS, name);
        Object.keys(attrs || {}).forEach(function (key) {
            if (attrs[key] !== null && attrs[key] !== undefined) { node.setAttribute(key, attrs[key]); }
        });
        if (text !== undefined) { node.textContent = text; }
        return node;
    }

    function seeded(seed) {
        var state = seed >>> 0;
        return function () {
            state = (Math.imul(1664525, state) + 1013904223) >>> 0;
            return state / 4294967296;
        };
    }

    function seedPositions(nodes) {
        var rings = [[], [], []];
        nodes.forEach(function (node) {
            var ring = ROLE_RING[node.data.role];
            rings[ring === undefined ? 2 : ring].push(node);
        });
        rings[2].sort(function (a, b) {
            return String(a.data.role).localeCompare(String(b.data.role)) || (b.data.total_amount || 0) - (a.data.total_amount || 0);
        });
        [rings[0].length > 1 ? 30 : 0, 70, 260].forEach(function (radius, ring) {
            rings[ring].forEach(function (node, index) {
                var angle = -Math.PI / 2 + 2 * Math.PI * index / Math.max(rings[ring].length, 1);
                node.x = radius * Math.cos(angle);
                node.y = radius * Math.sin(angle);
            });
        });
    }

    // Push apart any two circles closer than r1 + r2 + LABEL_PAD (forceCollide is soft); deterministic.
    function separate(nodes) {
        for (var pass = 0; pass < 60; pass++) {
            var moved = false;
            for (var i = 0; i < nodes.length; i++) {
                for (var j = i + 1; j < nodes.length; j++) {
                    var a = nodes[i], b = nodes[j];
                    var dx = b.x - a.x, dy = b.y - a.y, dist = Math.sqrt(dx * dx + dy * dy), need = a.r + b.r + LABEL_PAD;
                    if (dist >= need) { continue; }
                    if (dist < 1e-6) { dx = 1; dy = 0; dist = 1; }
                    var push = (need - dist) / 2 + 0.01;
                    a.x -= dx / dist * push; a.y -= dy / dist * push;
                    b.x += dx / dist * push; b.y += dy / dist * push;
                    moved = true;
                }
            }
            if (!moved) { return; }
        }
    }

    function overlaps(a, b) {
        return a.left < b.right + GAP && b.left < a.right + GAP && a.top < b.bottom + GAP && b.top < a.bottom + GAP;
    }

    function hitsDisc(box, disc) {
        var x = Math.max(box.left, Math.min(disc.x, box.right)), y = Math.max(box.top, Math.min(disc.y, box.bottom));
        return (x - disc.x) * (x - disc.x) + (y - disc.y) * (y - disc.y) < disc.r * disc.r;
    }

    function render(container, data, options) {
        var d3 = window.d3, opts = options || {};
        var style = window.getComputedStyle ? window.getComputedStyle(container) : null;
        var padding = style ? (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0) : 0;
        var width = Math.max((container.clientWidth || 900) - padding, 320), height = opts.height || HEIGHT;
        var call = function (name, fallback) { return opts[name] || fallback; };
        var radius = call("radius", function () { return 6; }), color = call("color", function () { return "#718096"; });
        var nodeType = call("nodeType", function (node) { return node.node_type; });
        var nodeStroke = call("nodeStroke", function () { return { stroke: "#fff", width: 1 }; });
        var edgeStyle = call("edgeStyle", function () { return { color: "#a0aec0", width: 1 }; });

        var nodes = data.nodes.map(function (node) { return { id: node.id, data: node, r: radius(node), type: nodeType(node) }; });
        var byId = {};
        nodes.forEach(function (node) { byId[node.id] = node; });
        var links = data.edges.filter(function (edge) { return byId[edge.source] && byId[edge.target]; })
            .map(function (edge) { return { source: edge.source, target: edge.target, edge: edge }; });
        var ranked = links.slice().sort(function (a, b) { return b.edge.weight - a.edge.weight; });
        ranked.forEach(function (link, index) { link.rank = ranked.length > 1 ? index / (ranked.length - 1) : 0; });

        seedPositions(nodes);
        var simulation = d3.forceSimulation(nodes).randomSource(seeded(20261029))
            .force("link", d3.forceLink(links).id(function (node) { return node.id; })
                .distance(function (link) { return 40 + 90 * link.rank; }))
            .force("charge", d3.forceManyBody().strength(-160))
            .force("collide", d3.forceCollide(function (node) { return node.r + LABEL_PAD; }).strength(1).iterations(3))
            .force("x", d3.forceX(0).strength(0.03))
            .force("y", d3.forceY(0).strength(0.08))
            .stop();
        for (var tick = 0; tick < TICKS; tick++) { simulation.tick(); }
        separate(nodes);

        container.textContent = "";
        var svg = el("svg", { "class": "ng-svg", width: width, height: height, role: "img",
                              "aria-label": opts.ariaLabel || "Network graph" });
        var viewport = el("g", { "class": "ng-viewport" }), edgeLayer = el("g", { "class": "ng-edges" });
        var nodeLayer = el("g", { "class": "ng-nodes" });
        viewport.appendChild(edgeLayer);
        viewport.appendChild(nodeLayer);
        svg.appendChild(viewport);
        container.appendChild(svg);

        var edgeEls = links.map(function (link) {
            var s = byId[link.source.id || link.source], t = byId[link.target.id || link.target], look = edgeStyle(link.edge);
            var line = el("line", { x1: s.x, y1: s.y, x2: t.x, y2: t.y, stroke: look.color, "stroke-width": look.width || 1,
                                    "stroke-opacity": 0.45, "stroke-dasharray": look.dash, "vector-effect": "non-scaling-stroke",
                                    "class": "ng-edge" });
            if (opts.edgeTitle) { line.appendChild(el("title", {}, opts.edgeTitle(link.edge))); }
            edgeLayer.appendChild(line);
            return { edge: link.edge, line: line, s: s, t: t };
        });
        var nodeEls = {};
        nodes.forEach(function (node) {
            var outline = nodeStroke(node.data);
            var g = el("g", { "class": "ng-node", "data-node-id": node.id, "data-node-type": node.type });
            var circle = el("circle", { cx: node.x, cy: node.y, r: node.r, fill: color(node.data), stroke: outline.stroke,
                                        "stroke-width": outline.width, "vector-effect": "non-scaling-stroke" });
            var caption = node.data.label.length > 32 ? node.data.label.slice(0, 31) + "…" : node.data.label;
            var text = el("text", { "class": "ng-label", x: 0, y: 0, "font-size": FONT, visibility: "hidden" }, caption);
            g.appendChild(circle);
            g.appendChild(text);
            g.appendChild(el("title", {}, opts.nodeTitle ? opts.nodeTitle(node.data) : node.data.label));
            g.addEventListener("click", function (event) {
                event.stopPropagation();
                if (opts.onNodeClick) { opts.onNodeClick(node.id); }
            });
            g.addEventListener("mouseenter", function () { text.style.visibility = "visible"; });
            g.addEventListener("mouseleave", function () { text.style.visibility = ""; });
            nodeLayer.appendChild(g);
            node.g = g;
            node.text = text;
            var box = text.getBBox();   // at FONT, baseline 0: the label's screen size at every zoom
            node.labelW = box.width;
            node.labelTop = box.y;
            node.labelH = box.height;
            nodeEls[node.id] = { g: g, circle: circle, stroke: outline.stroke, strokeWidth: outline.width };
        });
        svg.addEventListener("click", function () { if (opts.onBackgroundClick) { opts.onBackgroundClick(); } });

        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        nodes.forEach(function (node) {
            minX = Math.min(minX, node.x - node.r); maxX = Math.max(maxX, node.x + node.r);
            minY = Math.min(minY, node.y - node.r); maxY = Math.max(maxY, node.y + node.r);
        });
        var fitK = Math.min((width - 2 * MARGIN) / Math.max(maxX - minX, 1), (height - 2 * MARGIN) / Math.max(maxY - minY, 1), MAX_FIT);
        var fit = d3.zoomIdentity.translate(width / 2 - fitK * (minX + maxX) / 2, height / 2 - fitK * (minY + maxY) / 2).scale(fitK);
        var hidden = {}, transform = fit;
        var priority = nodes.slice().sort(function (a, b) {
            var ra = ROLE_RING[a.data.role] === undefined ? 2 : ROLE_RING[a.data.role];
            var rb = ROLE_RING[b.data.role] === undefined ? 2 : ROLE_RING[b.data.role];
            return Math.min(ra, 2) - Math.min(rb, 2) || (b.data.total_amount || 0) - (a.data.total_amount || 0)
                || String(a.data.label).localeCompare(String(b.data.label));
        });

        // Place labels in screen space: right, left, above, below the node, else hidden.
        function placeLabels() {
            var k = transform.k, shown = priority.filter(function (node) { return !hidden[node.type]; });
            var budget = Math.round(LABELS_AT_FIT * Math.pow(k / fitK, 2));
            var discs = shown.map(function (node) {
                return { id: node.id, x: transform.x + k * node.x, y: transform.y + k * node.y, r: k * node.r + 1 };
            });
            var placed = [];
            shown.forEach(function (node, index) {
                var sx = transform.x + k * node.x, sy = transform.y + k * node.y, sr = k * node.r;
                var w = node.labelW, h = node.labelH, top = node.labelTop;
                var spots = [
                    { anchor: "start", x: sx + sr + GAP, y: sy - top - h / 2, left: sx + sr + GAP },
                    { anchor: "end", x: sx - sr - GAP, y: sy - top - h / 2, left: sx - sr - GAP - w },
                    { anchor: "middle", x: sx, y: sy - sr - GAP - (top + h), left: sx - w / 2 },
                    { anchor: "middle", x: sx, y: sy + sr + GAP - top, left: sx - w / 2 }
                ];
                var chosen = null;
                for (var i = 0; index < budget && !chosen && i < spots.length; i++) {
                    var spot = spots[i];
                    var box = { left: spot.left, right: spot.left + w, top: spot.y + top, bottom: spot.y + top + h };
                    if (box.left < 2 || box.top < 2 || box.right > width - 2 || box.bottom > height - 2) { continue; }
                    if (placed.some(function (other) { return overlaps(box, other); })) { continue; }
                    if (discs.some(function (disc) { return disc.id !== node.id && hitsDisc(box, disc); })) { continue; }
                    chosen = spot;
                    placed.push(box);
                }
                var at = chosen || spots[0];
                node.text.setAttribute("x", (at.x - transform.x) / k);
                node.text.setAttribute("y", (at.y - transform.y) / k);
                node.text.setAttribute("text-anchor", at.anchor);
                node.text.setAttribute("font-size", FONT / k);
                node.text.setAttribute("stroke-width", 3 / k);
                node.text.setAttribute("visibility", chosen ? "visible" : "hidden");
            });
        }

        var zoom = d3.zoom().scaleExtent([fitK / 2, fitK * 8]).on("zoom", function (event) {
            transform = event.transform;
            viewport.setAttribute("transform", transform.toString());
            placeLabels();
        });
        d3.select(svg).call(zoom).on("dblclick.zoom", null);
        d3.select(svg).call(zoom.transform, fit);
        svg.setAttribute("data-settled", "1");

        function setTypeVisible(type, visible) {
            hidden[type] = !visible;
            nodes.forEach(function (node) { node.g.style.display = hidden[node.type] ? "none" : ""; });
            edgeEls.forEach(function (item) {
                item.line.style.display = hidden[item.s.type] || hidden[item.t.type] ? "none" : "";
            });
            placeLabels();
        }

        return {
            svg: svg, nodes: nodeEls, setTypeVisible: setTypeVisible,
            edges: edgeEls.map(function (item) { return { edge: item.edge, line: item.line }; })
        };
    }

    window.NetworkGraph = { render: render };
})();
