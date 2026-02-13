(() => {
    const SVG_NS = "http://www.w3.org/2000/svg";

    const graphSpecs = [
        {dataId: "donor-cogiving-data", svgId: "donor-cogiving-svg", summaryId: "donor-cogiving-summary"},
        {dataId: "committee-similarity-data", svgId: "committee-similarity-svg", summaryId: "committee-similarity-summary"},
        {dataId: "candidate-state-data", svgId: "candidate-state-svg", summaryId: "candidate-state-summary"},
        {dataId: "candidate-federal-data", svgId: "candidate-federal-svg", summaryId: "candidate-federal-summary"},
        {dataId: "candidate-combined-data", svgId: "candidate-combined-svg", summaryId: "candidate-combined-summary"},
        {dataId: "lobbying-influence-data", svgId: "lobbying-influence-svg", summaryId: "lobbying-influence-summary"},
        {dataId: "ecosystem-527-data", svgId: "ecosystem-527-svg", summaryId: "ecosystem-527-summary"},
    ];

    const nodeColors = {
        donor: "#2563eb",
        matched_donor: "#2563eb",
        committee: "#059669",
        candidate: "#dc2626",
        lobbying_client: "#7c3aed",
        lobbying_entity: "#8b5cf6",
        matched_payee: "#f59e0b",
        irs527_org: "#0284c7",
        director: "#7c2d12",
        recipient_target: "#475569",
    };

    const parseGraph = (id) => {
        const node = document.getElementById(id);
        if (!node) return null;
        try {
            return JSON.parse(node.textContent || "{}");
        } catch (_err) {
            return null;
        }
    };

    const viewBoxSize = (svg) => {
        const vb = (svg.getAttribute("viewBox") || "0 0 1000 480").split(/\s+/).map((v) => Number(v));
        return {width: vb[2] || 1000, height: vb[3] || 480};
    };

    const clearSvg = (svg) => {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
    };

    const createSvg = (tag, attrs = {}) => {
        const el = document.createElementNS(SVG_NS, tag);
        for (const [k, v] of Object.entries(attrs)) {
            el.setAttribute(k, String(v));
        }
        return el;
    };

    const drawEmpty = (svg, message) => {
        clearSvg(svg);
        const {width, height} = viewBoxSize(svg);
        svg.appendChild(
            createSvg("rect", {
                x: 24,
                y: 24,
                width: width - 48,
                height: height - 48,
                rx: 10,
                fill: "#f8fafc",
                stroke: "#cbd5e1",
            })
        );
        const text = createSvg("text", {
            x: width / 2,
            y: height / 2,
            "text-anchor": "middle",
            fill: "#475569",
            "font-size": "12px",
        });
        text.textContent = message;
        svg.appendChild(text);
    };

    const nodeRadius = (node) => {
        const size = Number(node.total_amount || node.weighted_degree || 0);
        const r = 5 + Math.sqrt(Math.max(0, size)) / 10;
        return Math.max(4, Math.min(16, r));
    };

    const nodeColor = (node) => {
        return nodeColors[node.node_type] || "#334155";
    };

    const buildSubset = (graph, maxNodes = 80, maxEdges = 180) => {
        const edges = (graph.edges || [])
            .slice()
            .sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0))
            .slice(0, maxEdges);
        const usedNodeIds = new Set();
        for (const edge of edges) {
            usedNodeIds.add(edge.source);
            usedNodeIds.add(edge.target);
        }
        const rankedNodes = (graph.centrality || [])
            .slice()
            .sort((a, b) => Number(b.weighted_degree || 0) - Number(a.weighted_degree || 0))
            .map((row) => row.node_id);
        const keepNodeIds = new Set();
        for (const nodeId of rankedNodes) {
            if (keepNodeIds.size >= maxNodes) break;
            if (usedNodeIds.has(nodeId)) keepNodeIds.add(nodeId);
        }
        const nodes = (graph.nodes || []).filter((node) => keepNodeIds.has(node.id));
        const finalEdges = edges.filter((edge) => keepNodeIds.has(edge.source) && keepNodeIds.has(edge.target));
        return {nodes, edges: finalEdges};
    };

    const runForceLayout = (nodes, edges, width, height) => {
        const byId = new Map();
        for (const node of nodes) {
            node.x = Math.random() * (width - 140) + 70;
            node.y = Math.random() * (height - 120) + 60;
            node.vx = 0;
            node.vy = 0;
            byId.set(node.id, node);
        }

        const iterations = 220;
        const charge = 1800;
        const linkLength = 86;
        const spring = 0.02;
        const damping = 0.88;
        const centerPull = 0.02;

        for (let step = 0; step < iterations; step += 1) {
            for (let i = 0; i < nodes.length; i += 1) {
                const a = nodes[i];
                for (let j = i + 1; j < nodes.length; j += 1) {
                    const b = nodes[j];
                    let dx = b.x - a.x;
                    let dy = b.y - a.y;
                    const distSq = Math.max(90, (dx * dx) + (dy * dy));
                    const dist = Math.sqrt(distSq);
                    dx /= dist;
                    dy /= dist;
                    const force = charge / distSq;
                    a.vx -= force * dx;
                    a.vy -= force * dy;
                    b.vx += force * dx;
                    b.vy += force * dy;
                }
            }

            for (const edge of edges) {
                const source = byId.get(edge.source);
                const target = byId.get(edge.target);
                if (!source || !target) continue;
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const dist = Math.max(1, Math.sqrt((dx * dx) + (dy * dy)));
                const force = spring * (dist - linkLength);
                const fx = (dx / dist) * force;
                const fy = (dy / dist) * force;
                source.vx += fx;
                source.vy += fy;
                target.vx -= fx;
                target.vy -= fy;
            }

            for (const node of nodes) {
                node.vx += ((width / 2) - node.x) * centerPull;
                node.vy += ((height / 2) - node.y) * centerPull;
                node.vx *= damping;
                node.vy *= damping;
                node.x += node.vx;
                node.y += node.vy;
                node.x = Math.max(20, Math.min(width - 20, node.x));
                node.y = Math.max(20, Math.min(height - 20, node.y));
            }
        }
    };

    const shortLabel = (value, max = 24) => {
        const text = String(value || "").trim();
        if (text.length <= max) return text;
        return `${text.slice(0, max - 1)}…`;
    };

    const render = (spec) => {
        const graph = parseGraph(spec.dataId);
        const svg = document.getElementById(spec.svgId);
        const summary = document.getElementById(spec.summaryId);
        if (!graph || !svg) return;

        const subset = buildSubset(graph);
        if (!subset.nodes.length || !subset.edges.length) {
            drawEmpty(svg, "No graph edges for the current filters.");
            if (summary) {
                summary.textContent = `Nodes ${graph.summary?.node_count || 0}, edges ${graph.summary?.edge_count || 0}.`;
            }
            return;
        }

        clearSvg(svg);
        const {width, height} = viewBoxSize(svg);
        runForceLayout(subset.nodes, subset.edges, width, height);

        const edgeGroup = createSvg("g");
        const nodeGroup = createSvg("g");
        const labelGroup = createSvg("g");
        svg.appendChild(edgeGroup);
        svg.appendChild(nodeGroup);
        svg.appendChild(labelGroup);

        const maxWeight = Math.max(...subset.edges.map((edge) => Number(edge.weight || 0)), 1);
        for (const edge of subset.edges) {
            const source = subset.nodes.find((node) => node.id === edge.source);
            const target = subset.nodes.find((node) => node.id === edge.target);
            if (!source || !target) continue;
            const strokeWidth = 0.7 + ((Number(edge.weight || 0) / maxWeight) * 2.6);
            edgeGroup.appendChild(
                createSvg("line", {
                    x1: source.x,
                    y1: source.y,
                    x2: target.x,
                    y2: target.y,
                    stroke: "#94a3b8",
                    "stroke-opacity": "0.65",
                    "stroke-width": strokeWidth.toFixed(2),
                })
            );
        }

        const rankedForLabels = subset.nodes
            .slice()
            .sort((a, b) => Number(b.total_amount || b.weighted_degree || 0) - Number(a.total_amount || a.weighted_degree || 0))
            .slice(0, 26)
            .map((node) => node.id);
        const labelSet = new Set(rankedForLabels);

        for (const node of subset.nodes) {
            nodeGroup.appendChild(
                createSvg("circle", {
                    cx: node.x,
                    cy: node.y,
                    r: nodeRadius(node).toFixed(2),
                    fill: nodeColor(node),
                    "fill-opacity": "0.92",
                    stroke: "#ffffff",
                    "stroke-width": 1,
                })
            );
            if (!labelSet.has(node.id)) continue;
            const label = createSvg("text", {
                x: node.x,
                y: node.y - 9,
                "text-anchor": "middle",
                "font-size": "9px",
                fill: "#1e293b",
            });
            label.textContent = shortLabel(node.label, 26);
            labelGroup.appendChild(label);
        }

        if (summary) {
            summary.textContent = `Rendering top ${subset.nodes.length} nodes and ${subset.edges.length} edges (total: ${graph.summary?.node_count || 0} / ${graph.summary?.edge_count || 0}).`;
        }
    };

    graphSpecs.forEach(render);
})();
