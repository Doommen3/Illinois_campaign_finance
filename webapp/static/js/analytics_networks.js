(() => {
    const dataEl = document.getElementById("analytics-network-data");
    const svg = document.getElementById("network-svg");
    const modeSelect = document.getElementById("network-view-mode");
    const regionSelect = document.getElementById("network-region");
    const renderBtn = document.getElementById("network-render-btn");
    const summaryEl = document.getElementById("network-summary");
    const chordSvg = document.getElementById("chord-svg");
    const chordModeSelect = document.getElementById("chord-view-mode");
    const chordTopNSelect = document.getElementById("chord-top-n");
    const chordRegionSelect = document.getElementById("chord-region-filter");
    const chordRenderBtn = document.getElementById("chord-render-btn");
    const chordSummaryEl = document.getElementById("chord-summary");

    if (
        !dataEl ||
        !svg ||
        !modeSelect ||
        !regionSelect ||
        !renderBtn ||
        !summaryEl ||
        !chordSvg ||
        !chordModeSelect ||
        !chordTopNSelect ||
        !chordRegionSelect ||
        !chordRenderBtn ||
        !chordSummaryEl
    ) {
        return;
    }

    let graphData;
    try {
        graphData = JSON.parse(dataEl.textContent || "{}");
    } catch (error) {
        return;
    }
    if (!graphData || !Array.isArray(graphData.nodes) || !Array.isArray(graphData.edges)) {
        return;
    }

    const WIDTH = 980;
    const HEIGHT = 520;
    const CHORD_WIDTH = 980;
    const CHORD_HEIGHT = 560;

    const typeColor = {
        donor: "#2563eb",
        committee: "#059669",
        candidate: "#dc2626",
    };
    const regionColors = {
        "Chicago Metro": "#0ea5e9",
        "Collar Counties": "#22c55e",
        "Central Illinois": "#f59e0b",
        "Southern Illinois": "#ef4444",
        "Other Illinois": "#a855f7",
        "Out of State": "#64748b",
        Unknown: "#94a3b8",
    };

    const nodesById = new Map();
    const centralityById = new Map();
    for (const row of graphData.centrality || []) {
        centralityById.set(row.node_id, row);
    }
    for (const node of graphData.nodes) {
        const c = centralityById.get(node.id) || {};
        nodesById.set(node.id, {
            ...node,
            weighted_degree: Number(c.weighted_degree || 0),
            degree: Number(c.degree || 0),
            region: node.region || "Unknown",
        });
    }
    const allEdges = (graphData.edges || [])
        .filter((edge) => nodesById.has(edge.source) && nodesById.has(edge.target))
        .map((edge) => ({ ...edge, weight: Number(edge.weight || 0) }));

    const allRegions = Array.from(new Set(Array.from(nodesById.values()).map((node) => node.region || "Unknown"))).sort();
    for (const region of allRegions) {
        const networkOpt = document.createElement("option");
        networkOpt.value = region;
        networkOpt.textContent = region;
        regionSelect.appendChild(networkOpt);

        const chordOpt = document.createElement("option");
        chordOpt.value = region;
        chordOpt.textContent = region;
        chordRegionSelect.appendChild(chordOpt);
    }

    const clearSvg = () => {
        while (svg.firstChild) {
            svg.removeChild(svg.firstChild);
        }
    };

    const getNodeRadius = (node) => {
        const size = 4 + Math.sqrt(Math.max(node.weighted_degree, 1)) / 3;
        return Math.max(4, Math.min(20, size));
    };

    const getGraphSubset = () => {
        const mode = modeSelect.value;
        const selectedRegion = regionSelect.value;

        if (mode === "power") {
            const top = Array.from(nodesById.values())
                .sort((a, b) => b.weighted_degree - a.weighted_degree)
                .slice(0, 45);
            const keep = new Set(top.map((node) => node.id));
            for (const edge of allEdges) {
                if (keep.has(edge.source) || keep.has(edge.target)) {
                    keep.add(edge.source);
                    keep.add(edge.target);
                }
            }
            const nodes = Array.from(keep).map((id) => ({ ...nodesById.get(id) }));
            const edges = allEdges.filter((edge) => keep.has(edge.source) && keep.has(edge.target));
            return { nodes, edges, mode, selectedRegion: "All" };
        }

        if (selectedRegion === "All") {
            return {
                nodes: Array.from(nodesById.values()).map((node) => ({ ...node })),
                edges: allEdges.slice(),
                mode,
                selectedRegion,
            };
        }

        const seed = new Set(
            Array.from(nodesById.values())
                .filter((node) => node.region === selectedRegion)
                .map((node) => node.id)
        );
        for (const edge of allEdges) {
            if (seed.has(edge.source) || seed.has(edge.target)) {
                seed.add(edge.source);
                seed.add(edge.target);
            }
        }
        const nodes = Array.from(seed).map((id) => ({ ...nodesById.get(id) }));
        const edges = allEdges.filter((edge) => seed.has(edge.source) && seed.has(edge.target));
        return { nodes, edges, mode, selectedRegion };
    };

    const runForceLayout = (nodes, edges) => {
        const byId = new Map();
        for (const node of nodes) {
            node.x = Math.random() * (WIDTH - 160) + 80;
            node.y = Math.random() * (HEIGHT - 120) + 60;
            node.vx = 0;
            node.vy = 0;
            byId.set(node.id, node);
        }

        const charge = 2500;
        const spring = 0.015;
        const centerPull = 0.02;
        const damping = 0.88;
        const linkLength = 95;
        const iterations = 250;

        for (let step = 0; step < iterations; step += 1) {
            for (let i = 0; i < nodes.length; i += 1) {
                const n1 = nodes[i];
                for (let j = i + 1; j < nodes.length; j += 1) {
                    const n2 = nodes[j];
                    let dx = n2.x - n1.x;
                    let dy = n2.y - n1.y;
                    const distSq = Math.max(64, dx * dx + dy * dy);
                    const force = charge / distSq;
                    const dist = Math.sqrt(distSq);
                    dx /= dist;
                    dy /= dist;
                    n1.vx -= force * dx;
                    n1.vy -= force * dy;
                    n2.vx += force * dx;
                    n2.vy += force * dy;
                }
            }

            for (const edge of edges) {
                const s = byId.get(edge.source);
                const t = byId.get(edge.target);
                if (!s || !t) {
                    continue;
                }
                const dx = t.x - s.x;
                const dy = t.y - s.y;
                const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
                const diff = dist - linkLength;
                const force = spring * diff;
                const fx = (dx / dist) * force;
                const fy = (dy / dist) * force;
                s.vx += fx;
                s.vy += fy;
                t.vx -= fx;
                t.vy -= fy;
            }

            for (const node of nodes) {
                node.vx += (WIDTH / 2 - node.x) * centerPull;
                node.vy += (HEIGHT / 2 - node.y) * centerPull;
                node.vx *= damping;
                node.vy *= damping;
                node.x += node.vx;
                node.y += node.vy;
                node.x = Math.min(WIDTH - 16, Math.max(16, node.x));
                node.y = Math.min(HEIGHT - 16, Math.max(16, node.y));
            }
        }
    };

    const clearChordSvg = () => {
        while (chordSvg.firstChild) {
            chordSvg.removeChild(chordSvg.firstChild);
        }
    };

    const polarPoint = (cx, cy, radius, angle) => ({
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
    });

    const describeArc = (cx, cy, radius, startAngle, endAngle) => {
        const start = polarPoint(cx, cy, radius, startAngle);
        const end = polarPoint(cx, cy, radius, endAngle);
        const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
        return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
    };

    const getEntityChordData = (topN, regionFilter) => {
        const candidates = Array.from(nodesById.values())
            .filter((node) => regionFilter === "All" || node.region === regionFilter)
            .sort((a, b) => b.weighted_degree - a.weighted_degree)
            .slice(0, Math.max(2, topN));

        const keep = new Set(candidates.map((node) => node.id));
        const edgeMap = new Map();
        for (const edge of allEdges) {
            if (!keep.has(edge.source) || !keep.has(edge.target)) {
                continue;
            }
            if (edge.source === edge.target) {
                continue;
            }
            const sourceId = edge.source < edge.target ? edge.source : edge.target;
            const targetId = edge.source < edge.target ? edge.target : edge.source;
            const key = `${sourceId}|${targetId}`;
            const current = edgeMap.get(key) || { source: sourceId, target: targetId, weight: 0 };
            current.weight += Number(edge.weight || 0);
            edgeMap.set(key, current);
        }

        return {
            nodes: candidates.map((node) => ({ ...node })),
            edges: Array.from(edgeMap.values()),
        };
    };

    const getRegionChordData = () => {
        const regionNodes = new Map();
        for (const region of allRegions) {
            regionNodes.set(`region:${region}`, {
                id: `region:${region}`,
                label: region,
                node_type: "region",
                region,
                weighted_degree: 0,
            });
        }

        const edgeMap = new Map();
        for (const edge of allEdges) {
            const sourceNode = nodesById.get(edge.source);
            const targetNode = nodesById.get(edge.target);
            if (!sourceNode || !targetNode) {
                continue;
            }
            const sourceRegion = sourceNode.region || "Unknown";
            const targetRegion = targetNode.region || "Unknown";
            const sourceId = `region:${sourceRegion}`;
            const targetId = `region:${targetRegion}`;
            const weight = Number(edge.weight || 0);

            if (regionNodes.has(sourceId)) {
                regionNodes.get(sourceId).weighted_degree += weight;
            }
            if (regionNodes.has(targetId)) {
                regionNodes.get(targetId).weighted_degree += weight;
            }

            if (sourceId === targetId) {
                continue;
            }
            const a = sourceId < targetId ? sourceId : targetId;
            const b = sourceId < targetId ? targetId : sourceId;
            const key = `${a}|${b}`;
            const current = edgeMap.get(key) || { source: a, target: b, weight: 0 };
            current.weight += weight;
            edgeMap.set(key, current);
        }

        const nodes = Array.from(regionNodes.values()).filter((node) => node.weighted_degree > 0);
        return {
            nodes,
            edges: Array.from(edgeMap.values()),
        };
    };

    const computeChordLayout = (rawNodes, rawEdges) => {
        const nodes = rawNodes.map((node) => ({ ...node, value: 0 }));
        const nodeIndex = new Map(nodes.map((node) => [node.id, node]));
        const edges = rawEdges
            .filter((edge) => nodeIndex.has(edge.source) && nodeIndex.has(edge.target))
            .map((edge) => ({ ...edge, weight: Number(edge.weight || 0) }));

        for (const edge of edges) {
            nodeIndex.get(edge.source).value += edge.weight;
            nodeIndex.get(edge.target).value += edge.weight;
        }

        const filteredNodes = nodes.filter((node) => node.value > 0);
        const filteredNodeIndex = new Map(filteredNodes.map((node) => [node.id, node]));
        const filteredEdges = edges.filter(
            (edge) => filteredNodeIndex.has(edge.source) && filteredNodeIndex.has(edge.target)
        );
        const total = filteredNodes.reduce((sum, node) => sum + node.value, 0) || filteredNodes.length;
        const gap = filteredNodes.length > 1 ? 0.025 : 0;
        const availableAngle = Math.PI * 2 - gap * filteredNodes.length;

        let cursor = -Math.PI / 2;
        for (const node of filteredNodes) {
            const share = total > 0 ? node.value / total : 1 / filteredNodes.length;
            const span = Math.max(0.08, availableAngle * share);
            node.startAngle = cursor;
            node.endAngle = cursor + span;
            cursor = node.endAngle + gap;
        }

        return { nodes: filteredNodes, edges: filteredEdges };
    };

    const renderChord = () => {
        clearChordSvg();
        const mode = chordModeSelect.value;
        const topN = Number(chordTopNSelect.value || 12);
        const selectedRegion = chordRegionSelect.value || "All";

        const source = mode === "regions" ? getRegionChordData() : getEntityChordData(topN, selectedRegion);
        const { nodes, edges } = computeChordLayout(source.nodes, source.edges);

        if (nodes.length < 2 || edges.length === 0) {
            chordSummaryEl.textContent = "Not enough connected data to render chord diagram for this filter.";
            return;
        }

        const cx = CHORD_WIDTH / 2;
        const cy = CHORD_HEIGHT / 2;
        const outerRadius = Math.min(CHORD_WIDTH, CHORD_HEIGHT) * 0.36;
        const innerRadius = outerRadius - 22;
        const nodeMap = new Map(nodes.map((node) => [node.id, node]));
        const maxEdgeWeight = Math.max(...edges.map((edge) => edge.weight), 1);

        for (const node of nodes) {
            const arc = document.createElementNS("http://www.w3.org/2000/svg", "path");
            arc.setAttribute("d", describeArc(cx, cy, outerRadius, node.startAngle, node.endAngle));
            const color = mode === "regions" ? regionColors[node.region] || regionColors.Unknown : typeColor[node.node_type] || "#64748b";
            arc.setAttribute("stroke", color);
            arc.setAttribute("stroke-width", "18");
            arc.setAttribute("stroke-linecap", "round");
            arc.setAttribute("fill", "none");
            const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
            title.textContent = `${node.label} | Total connected weight: ${node.value.toFixed(2)}`;
            arc.appendChild(title);
            chordSvg.appendChild(arc);

            const labelAngle = (node.startAngle + node.endAngle) / 2;
            const labelPoint = polarPoint(cx, cy, outerRadius + 18, labelAngle);
            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            label.setAttribute("x", labelPoint.x.toFixed(2));
            label.setAttribute("y", labelPoint.y.toFixed(2));
            label.setAttribute("font-size", "10");
            label.setAttribute("fill", "#0f172a");
            label.setAttribute("text-anchor", Math.cos(labelAngle) >= 0 ? "start" : "end");
            const textValue = node.label.length > 22 ? `${node.label.slice(0, 22)}...` : node.label;
            label.textContent = textValue;
            chordSvg.appendChild(label);
        }

        for (const edge of edges.sort((a, b) => b.weight - a.weight)) {
            const sourceNode = nodeMap.get(edge.source);
            const targetNode = nodeMap.get(edge.target);
            if (!sourceNode || !targetNode) {
                continue;
            }
            const sourceAngle = (sourceNode.startAngle + sourceNode.endAngle) / 2;
            const targetAngle = (targetNode.startAngle + targetNode.endAngle) / 2;
            const start = polarPoint(cx, cy, innerRadius, sourceAngle);
            const end = polarPoint(cx, cy, innerRadius, targetAngle);

            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute(
                "d",
                `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} Q ${cx.toFixed(2)} ${cy.toFixed(2)} ${end.x.toFixed(2)} ${end.y.toFixed(2)}`
            );
            const color = mode === "regions" ? regionColors[sourceNode.region] || regionColors.Unknown : typeColor[sourceNode.node_type] || "#64748b";
            path.setAttribute("stroke", color);
            path.setAttribute("fill", "none");
            path.setAttribute("stroke-opacity", "0.42");
            path.setAttribute("stroke-width", String(1 + (edge.weight / maxEdgeWeight) * 10));

            const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
            title.textContent = `${sourceNode.label} -> ${targetNode.label} | Weight: ${edge.weight.toFixed(2)}`;
            path.appendChild(title);
            chordSvg.appendChild(path);
        }

        chordSummaryEl.textContent = `Chord rendered with ${nodes.length} nodes and ${edges.length} aggregated connections | Mode: ${mode}`;
    };

    const render = () => {
        const { nodes, edges, mode, selectedRegion } = getGraphSubset();
        clearSvg();
        runForceLayout(nodes, edges);

        const localNodesById = new Map(nodes.map((node) => [node.id, node]));

        for (const edge of edges) {
            const source = localNodesById.get(edge.source);
            const target = localNodesById.get(edge.target);
            if (!source || !target) {
                continue;
            }
            const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            line.setAttribute("x1", source.x.toFixed(2));
            line.setAttribute("y1", source.y.toFixed(2));
            line.setAttribute("x2", target.x.toFixed(2));
            line.setAttribute("y2", target.y.toFixed(2));
            line.setAttribute("stroke", "#94a3b8");
            line.setAttribute("stroke-opacity", "0.35");
            line.setAttribute("stroke-width", String(Math.max(0.6, Math.min(3, (edge.weight || 1) / 2500))));
            svg.appendChild(line);
        }

        const labelNodes = nodes
            .slice()
            .sort((a, b) => b.weighted_degree - a.weighted_degree)
            .slice(0, 18)
            .map((node) => node.id);
        const labeledSet = new Set(labelNodes);

        for (const node of nodes) {
            const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            circle.setAttribute("cx", node.x.toFixed(2));
            circle.setAttribute("cy", node.y.toFixed(2));
            circle.setAttribute("r", String(getNodeRadius(node)));
            const color = mode === "region" ? regionColors[node.region] || regionColors.Unknown : typeColor[node.node_type] || "#64748b";
            circle.setAttribute("fill", color);
            circle.setAttribute("fill-opacity", "0.9");
            circle.setAttribute("stroke", "#0f172a");
            circle.setAttribute("stroke-width", "0.7");

            const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
            title.textContent = `${node.label} (${node.node_type}) | Region: ${node.region} | Weighted degree: ${node.weighted_degree}`;
            circle.appendChild(title);
            svg.appendChild(circle);

            if (labeledSet.has(node.id)) {
                const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
                text.setAttribute("x", (node.x + getNodeRadius(node) + 3).toFixed(2));
                text.setAttribute("y", (node.y + 4).toFixed(2));
                text.setAttribute("font-size", "10");
                text.setAttribute("fill", "#0f172a");
                text.textContent = node.label.length > 22 ? `${node.label.slice(0, 22)}...` : node.label;
                svg.appendChild(text);
            }
        }

        summaryEl.textContent = `Rendered ${nodes.length} nodes and ${edges.length} edges | Mode: ${mode} | Region filter: ${selectedRegion}`;
    };

    renderBtn.addEventListener("click", render);
    modeSelect.addEventListener("change", () => {
        if (modeSelect.value === "power") {
            regionSelect.disabled = true;
        } else {
            regionSelect.disabled = false;
        }
        render();
    });
    regionSelect.addEventListener("change", () => {
        if (modeSelect.value === "region") {
            render();
        }
    });

    chordRenderBtn.addEventListener("click", renderChord);
    chordModeSelect.addEventListener("change", () => {
        const mode = chordModeSelect.value;
        if (mode === "regions") {
            chordTopNSelect.disabled = true;
            chordRegionSelect.disabled = true;
        } else {
            chordTopNSelect.disabled = false;
            chordRegionSelect.disabled = false;
        }
        renderChord();
    });
    chordTopNSelect.addEventListener("change", () => {
        if (chordModeSelect.value === "power") {
            renderChord();
        }
    });
    chordRegionSelect.addEventListener("change", () => {
        if (chordModeSelect.value === "power") {
            renderChord();
        }
    });

    regionSelect.disabled = true;
    chordTopNSelect.disabled = false;
    chordRegionSelect.disabled = false;
    render();
    renderChord();
})();
