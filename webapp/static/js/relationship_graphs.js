(() => {
    const SVG_NS = "http://www.w3.org/2000/svg";

    const graphSpecs = [
        {
            key: "donor-cogiving",
            dataId: "donor-cogiving-data",
            svgId: "donor-cogiving-svg",
            summaryId: "donor-cogiving-summary",
            panelId: "donor-cogiving-panel",
            tableId: "donor-cogiving-table",
            layout: "force",
        },
        {
            key: "committee-similarity",
            dataId: "committee-similarity-data",
            svgId: "committee-similarity-svg",
            summaryId: "committee-similarity-summary",
            panelId: "committee-similarity-panel",
            tableId: "committee-similarity-table",
            layout: "clustered",
            supportsCommunityCollapse: true,
        },
        {
            key: "candidate-state",
            dataId: "candidate-state-data",
            svgId: "candidate-state-svg",
            summaryId: "candidate-state-summary",
            panelId: "candidate-state-panel",
            tableId: "candidate-state-table",
            matrixId: "candidate-state-matrix",
            rankedId: "candidate-state-ranked",
            layout: "force",
            isCandidateGraph: true,
            supportsMatrix: true,
        },
        {
            key: "candidate-federal",
            dataId: "candidate-federal-data",
            svgId: "candidate-federal-svg",
            summaryId: "candidate-federal-summary",
            panelId: "candidate-federal-panel",
            tableId: "candidate-federal-table",
            matrixId: "candidate-federal-matrix",
            rankedId: "candidate-federal-ranked",
            layout: "force",
            isCandidateGraph: true,
            supportsMatrix: true,
        },
        {
            key: "candidate-combined",
            dataId: "candidate-combined-data",
            svgId: "candidate-combined-svg",
            summaryId: "candidate-combined-summary",
            panelId: "candidate-combined-panel",
            tableId: "candidate-combined-table",
            matrixId: "candidate-combined-matrix",
            rankedId: "candidate-combined-ranked",
            layout: "force",
            isCandidateGraph: true,
            supportsMatrix: true,
            supportsCrossOnly: true,
        },
        {
            key: "lobbying-influence",
            dataId: "lobbying-influence-data",
            svgId: "lobbying-influence-svg",
            summaryId: "lobbying-influence-summary",
            panelId: "lobbying-influence-panel",
            tableId: "lobbying-influence-table",
            layout: "layered",
        },
        {
            key: "ecosystem-527",
            dataId: "ecosystem-527-data",
            svgId: "ecosystem-527-svg",
            summaryId: "ecosystem-527-summary",
            panelId: "ecosystem-527-panel",
            tableId: "ecosystem-527-table",
            layout: "layered",
        },
    ];

    const edgeTypeColors = {
        donor_cogiving: "#64748b",
        committee_similarity: "#0f766e",
        candidate_competition: "#1d4ed8",
        client_entity: "#7c3aed",
        client_donor_match: "#2563eb",
        client_payee_match: "#f59e0b",
        entity_payee_match: "#d97706",
        donor_committee_flow: "#059669",
        payee_committee_match: "#475569",
        org_committee_match: "#0ea5e9",
        org_recipient_match: "#0284c7",
        org_director: "#a16207",
        director_donor_match: "#4f46e5",
    };

    const nodeTypeColors = {
        donor: "#2563eb",
        matched_donor: "#2563eb",
        committee: "#059669",
        candidate: "#dc2626",
        lobbying_client: "#7c3aed",
        lobbying_entity: "#8b5cf6",
        matched_payee: "#f59e0b",
        irs527_org: "#0284c7",
        director: "#92400e",
        recipient_target: "#475569",
        community: "#334155",
    };

    const systemColors = {
        state: "#0369a1",
        federal: "#b91c1c",
        cross_system: "#7c3aed",
    };

    const runtime = new Map();

    const parseGraph = (id) => {
        const node = document.getElementById(id);
        if (!node) return null;
        try {
            return JSON.parse(node.textContent || "{}");
        } catch (_err) {
            return null;
        }
    };

    const byId = (id) => document.getElementById(id);

    const viewBoxSize = (svg) => {
        const vb = (svg.getAttribute("viewBox") || "0 0 1000 480").split(/\s+/).map((v) => Number(v));
        return {width: vb[2] || 1000, height: vb[3] || 480};
    };

    const createSvg = (tag, attrs = {}) => {
        const el = document.createElementNS(SVG_NS, tag);
        for (const [k, v] of Object.entries(attrs)) {
            el.setAttribute(k, String(v));
        }
        return el;
    };

    const clearElement = (node) => {
        if (!node) return;
        while (node.firstChild) node.removeChild(node.firstChild);
    };

    const shortLabel = (value, max = 26) => {
        const text = String(value || "").trim();
        if (text.length <= max) return text;
        return `${text.slice(0, max - 1)}...`;
    };

    const escapeHtml = (value) => String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");

    const edgeId = (edge) => `${edge.source}|${edge.target}|${edge.edge_type || "edge"}`;

    const weightValue = (edge) => Number(edge.weight || 0);

    const sharedValue = (edge) => Number(edge.shared_donor_count || edge.shared_targets || edge.shared_count || 1);

    const nodeRadius = (node) => {
        const size = Number(node.total_amount || node.weighted_degree || node.member_count || 0);
        const r = 5 + Math.sqrt(Math.max(0, size + 1)) / 12;
        return Math.max(4, Math.min(18, r));
    };

    const nodeColor = (node) => {
        const system = String(node.system || "").toLowerCase();
        if (system && systemColors[system]) return systemColors[system];
        const nodeType = String(node.node_type || "").toLowerCase();
        return nodeTypeColors[nodeType] || "#334155";
    };

    const edgeColor = (edge) => {
        const systemMix = String(edge.system_mix || "").toLowerCase();
        if (systemMix && systemColors[systemMix]) return systemColors[systemMix];
        const edgeType = String(edge.edge_type || "").toLowerCase();
        return edgeTypeColors[edgeType] || "#64748b";
    };

    const edgeWidth = (edge, maxWeight) => {
        const weight = Math.max(0, weightValue(edge));
        const denominator = Math.log10((maxWeight || 1) + 1);
        const normalized = denominator > 0 ? Math.log10(weight + 1) / denominator : 0;
        return 0.8 + (normalized * 3.2);
    };

    const nodeShape = (node) => {
        const nodeType = String(node.node_type || "").toLowerCase();
        if (nodeType === "candidate") return "diamond";
        if (nodeType === "committee") return "square";
        if (nodeType === "lobbying_client" || nodeType === "irs527_org") return "hex";
        if (nodeType === "lobbying_entity" || nodeType === "recipient_target") return "triangle";
        if (nodeType === "director") return "diamond";
        return "circle";
    };

    const drawNodeShape = (node, attrs) => {
        const shape = nodeShape(node);
        const x = Number(attrs.cx);
        const y = Number(attrs.cy);
        const r = Number(attrs.r);
        if (shape === "square") {
            return createSvg("rect", {
                x: (x - r).toFixed(2),
                y: (y - r).toFixed(2),
                width: (r * 2).toFixed(2),
                height: (r * 2).toFixed(2),
                rx: 2,
                fill: attrs.fill,
                stroke: attrs.stroke,
                "stroke-width": attrs["stroke-width"],
                "fill-opacity": attrs["fill-opacity"],
            });
        }
        if (shape === "triangle") {
            const points = [
                `${x},${y - r}`,
                `${x - r},${y + r}`,
                `${x + r},${y + r}`,
            ].join(" ");
            return createSvg("polygon", {
                points,
                fill: attrs.fill,
                stroke: attrs.stroke,
                "stroke-width": attrs["stroke-width"],
                "fill-opacity": attrs["fill-opacity"],
            });
        }
        if (shape === "diamond") {
            const points = [
                `${x},${y - r}`,
                `${x - r},${y}`,
                `${x},${y + r}`,
                `${x + r},${y}`,
            ].join(" ");
            return createSvg("polygon", {
                points,
                fill: attrs.fill,
                stroke: attrs.stroke,
                "stroke-width": attrs["stroke-width"],
                "fill-opacity": attrs["fill-opacity"],
            });
        }
        if (shape === "hex") {
            const points = [];
            for (let i = 0; i < 6; i += 1) {
                const angle = (Math.PI / 3) * i + (Math.PI / 6);
                points.push(`${x + (r * Math.cos(angle))},${y + (r * Math.sin(angle))}`);
            }
            return createSvg("polygon", {
                points: points.join(" "),
                fill: attrs.fill,
                stroke: attrs.stroke,
                "stroke-width": attrs["stroke-width"],
                "fill-opacity": attrs["fill-opacity"],
            });
        }
        return createSvg("circle", attrs);
    };

    const buildAdjacency = (edges) => {
        const adjacency = new Map();
        for (const edge of edges) {
            if (!adjacency.has(edge.source)) adjacency.set(edge.source, new Set());
            if (!adjacency.has(edge.target)) adjacency.set(edge.target, new Set());
            adjacency.get(edge.source).add(edge.target);
            adjacency.get(edge.target).add(edge.source);
        }
        return adjacency;
    };

    const connectedComponents = (nodes, edges) => {
        const nodeIds = nodes.map((node) => node.id);
        const adjacency = buildAdjacency(edges);
        const visited = new Set();
        const groups = [];
        for (const nodeId of nodeIds) {
            if (visited.has(nodeId)) continue;
            const stack = [nodeId];
            const component = [];
            visited.add(nodeId);
            while (stack.length) {
                const current = stack.pop();
                component.push(current);
                const neighbors = adjacency.get(current) || new Set();
                for (const neighbor of neighbors) {
                    if (visited.has(neighbor)) continue;
                    visited.add(neighbor);
                    stack.push(neighbor);
                }
            }
            groups.push(component);
        }
        groups.sort((a, b) => b.length - a.length);
        return groups;
    };

    const collapseCommunities = (subset) => {
        const groups = connectedComponents(subset.nodes, subset.edges);
        if (groups.length <= 1) return subset;
        const communityByNode = new Map();
        groups.forEach((group, idx) => {
            for (const nodeId of group) communityByNode.set(nodeId, idx);
        });

        const communityNodes = groups.map((group, idx) => {
            let totalAmount = 0;
            for (const nodeId of group) {
                const node = subset.nodes.find((item) => item.id === nodeId);
                totalAmount += Number(node?.total_amount || node?.weighted_degree || 0);
            }
            return {
                id: `community:${idx + 1}`,
                label: `Community ${idx + 1} (${group.length})`,
                node_type: "community",
                member_count: group.length,
                total_amount: totalAmount,
                members: group,
            };
        });

        const edgeMap = new Map();
        for (const edge of subset.edges) {
            const leftCommunity = communityByNode.get(edge.source);
            const rightCommunity = communityByNode.get(edge.target);
            if (leftCommunity === undefined || rightCommunity === undefined || leftCommunity === rightCommunity) continue;
            const source = `community:${Math.min(leftCommunity, rightCommunity) + 1}`;
            const target = `community:${Math.max(leftCommunity, rightCommunity) + 1}`;
            const key = `${source}|${target}|community_bridge`;
            const bucket = edgeMap.get(key) || {
                source,
                target,
                edge_type: "community_bridge",
                weight: 0,
                row_count: 0,
                source_label: source,
                target_label: target,
            };
            bucket.weight += weightValue(edge);
            bucket.row_count += 1;
            edgeMap.set(key, bucket);
        }

        const communityEdges = Array.from(edgeMap.values()).sort((a, b) => b.weight - a.weight);
        return {
            nodes: communityNodes,
            edges: communityEdges,
            wasCollapsed: true,
        };
    };

    const layoutForce = (nodes, edges, width, height, options = {}) => {
        const byNode = new Map();
        for (const node of nodes) {
            node.x = Math.random() * (width - 140) + 70;
            node.y = Math.random() * (height - 120) + 60;
            node.vx = 0;
            node.vy = 0;
            byNode.set(node.id, node);
        }

        const iterations = options.iterations || 260;
        const charge = options.charge || 2500;
        const linkLength = options.linkLength || 88;
        const spring = options.spring || 0.025;
        const damping = options.damping || 0.87;
        const centerPull = options.centerPull || 0.016;
        const collisionPadding = options.collisionPadding || 2.6;

        for (let step = 0; step < iterations; step += 1) {
            for (let i = 0; i < nodes.length; i += 1) {
                const left = nodes[i];
                for (let j = i + 1; j < nodes.length; j += 1) {
                    const right = nodes[j];
                    let dx = right.x - left.x;
                    let dy = right.y - left.y;
                    const distSq = Math.max(120, (dx * dx) + (dy * dy));
                    const dist = Math.sqrt(distSq);
                    dx /= dist;
                    dy /= dist;
                    const force = charge / distSq;
                    left.vx -= force * dx;
                    left.vy -= force * dy;
                    right.vx += force * dx;
                    right.vy += force * dy;

                    const minDist = nodeRadius(left) + nodeRadius(right) + collisionPadding;
                    if (dist < minDist) {
                        const overlap = (minDist - dist) * 0.45;
                        left.vx -= overlap * dx;
                        left.vy -= overlap * dy;
                        right.vx += overlap * dx;
                        right.vy += overlap * dy;
                    }
                }
            }

            for (const edge of edges) {
                const source = byNode.get(edge.source);
                const target = byNode.get(edge.target);
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

    const layoutLayered = (nodes, edges, width, height) => {
        const layerMap = {
            lobbying_client: 0,
            irs527_org: 0,
            lobbying_entity: 1,
            director: 1,
            donor: 2,
            matched_donor: 2,
            matched_payee: 2,
            recipient_target: 2,
            committee: 3,
            candidate: 4,
        };

        const degree = new Map();
        for (const edge of edges) {
            degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
            degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
        }

        const layers = new Map();
        for (const node of nodes) {
            const layerIndex = layerMap[String(node.node_type || "").toLowerCase()] ?? 2;
            if (!layers.has(layerIndex)) layers.set(layerIndex, []);
            layers.get(layerIndex).push(node);
        }

        const sortedLayers = Array.from(layers.keys()).sort((a, b) => a - b);
        const maxLayer = sortedLayers.length > 1 ? sortedLayers.length - 1 : 1;

        for (let idx = 0; idx < sortedLayers.length; idx += 1) {
            const layerNodes = layers.get(sortedLayers[idx]) || [];
            layerNodes.sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0));
            const x = 40 + ((width - 80) * (idx / maxLayer));
            const yGap = (height - 60) / (layerNodes.length + 1);
            layerNodes.forEach((node, nodeIndex) => {
                node.x = x;
                node.y = 30 + (nodeIndex + 1) * yGap;
                node.vx = 0;
                node.vy = 0;
            });
        }
    };

    const layoutClustered = (nodes, edges, width, height) => {
        const groups = connectedComponents(nodes, edges);
        if (!groups.length) return;

        const centers = [];
        const radius = Math.min(width, height) * 0.28;
        groups.forEach((_group, idx) => {
            const angle = (Math.PI * 2 * idx) / groups.length;
            centers.push({
                x: (width / 2) + (radius * Math.cos(angle)),
                y: (height / 2) + (radius * Math.sin(angle)),
            });
        });

        groups.forEach((group, groupIndex) => {
            const center = centers[groupIndex];
            const localNodes = group
                .map((id) => nodes.find((node) => node.id === id))
                .filter(Boolean);
            if (!localNodes.length) return;
            const localRadius = Math.max(35, 20 + (localNodes.length * 2.5));
            localNodes.forEach((node, idx) => {
                const angle = (Math.PI * 2 * idx) / localNodes.length;
                node.x = center.x + (localRadius * Math.cos(angle));
                node.y = center.y + (localRadius * Math.sin(angle));
                node.vx = 0;
                node.vy = 0;
            });
        });
        layoutForce(nodes, edges, width, height, {iterations: 120, charge: 1800, linkLength: 70, centerPull: 0.004});
    };

    const drawEmpty = (svg, message) => {
        clearElement(svg);
        const {width, height} = viewBoxSize(svg);
        const rect = createSvg("rect", {
            x: 24,
            y: 24,
            width: width - 48,
            height: height - 48,
            rx: 10,
            fill: "#f8fafc",
            stroke: "#cbd5e1",
        });
        const text = createSvg("text", {
            x: width / 2,
            y: height / 2,
            "text-anchor": "middle",
            fill: "#475569",
            "font-size": "12px",
        });
        text.textContent = message;
        svg.appendChild(rect);
        svg.appendChild(text);
    };

    const buildSubset = (graph, spec, state) => {
        const allNodes = (graph.nodes || []).map((node) => ({...node}));
        const allEdges = (graph.edges || []).map((edge) => ({...edge}));
        const byNodeId = new Map(allNodes.map((node) => [node.id, node]));

        let filteredEdges = allEdges
            .filter((edge) => byNodeId.has(edge.source) && byNodeId.has(edge.target))
            .filter((edge) => weightValue(edge) >= state.minWeight)
            .filter((edge) => sharedValue(edge) >= state.minShared);

        if (spec.supportsCrossOnly && state.crossOnly) {
            filteredEdges = filteredEdges.filter((edge) => {
                const mix = String(edge.system_mix || "").toLowerCase();
                return mix === "cross_system" || Number(edge.shared_cross_amount || 0) > 0;
            });
        }

        filteredEdges.sort((a, b) => weightValue(b) - weightValue(a));
        filteredEdges = filteredEdges.slice(0, state.maxEdges);

        const usedNodeIds = new Set();
        filteredEdges.forEach((edge) => {
            usedNodeIds.add(edge.source);
            usedNodeIds.add(edge.target);
        });

        const rankedNodeIds = (graph.centrality || [])
            .slice()
            .sort((a, b) => Number(b.weighted_degree || 0) - Number(a.weighted_degree || 0))
            .map((row) => row.node_id);

        const keepNodeIds = new Set();
        for (const nodeId of rankedNodeIds) {
            if (!usedNodeIds.has(nodeId)) continue;
            keepNodeIds.add(nodeId);
            if (keepNodeIds.size >= state.maxNodes) break;
        }
        if (!keepNodeIds.size) {
            for (const nodeId of usedNodeIds) {
                keepNodeIds.add(nodeId);
                if (keepNodeIds.size >= state.maxNodes) break;
            }
        }

        const nodes = allNodes.filter((node) => keepNodeIds.has(node.id));
        const nodeSet = new Set(nodes.map((node) => node.id));
        let edges = filteredEdges.filter((edge) => nodeSet.has(edge.source) && nodeSet.has(edge.target));

        let subset = {nodes, edges};
        if (spec.supportsCommunityCollapse && state.collapseCommunities) {
            subset = collapseCommunities(subset);
        }
        return subset;
    };

    const updatePanel = (spec, subset, state, context) => {
        const panel = byId(spec.panelId);
        if (!panel) return;

        const lines = [];
        if (state.selectedEdgeId) {
            const edge = context.edgeById.get(state.selectedEdgeId);
            if (edge) {
                lines.push(`<h4>${escapeHtml(shortLabel(edge.source_label || edge.source))} -> ${escapeHtml(shortLabel(edge.target_label || edge.target))}</h4>`);
                lines.push(`<p><strong>Weight:</strong> ${weightValue(edge).toLocaleString()}</p>`);
                if (edge.shared_donor_count !== undefined) lines.push(`<p><strong>Shared donors:</strong> ${edge.shared_donor_count}</p>`);
                if (edge.shared_targets !== undefined) lines.push(`<p><strong>Shared targets:</strong> ${edge.shared_targets}</p>`);
                if (edge.shared_committees !== undefined) lines.push(`<p><strong>Shared committees:</strong> ${edge.shared_committees}</p>`);
                if (edge.shared_candidates !== undefined) lines.push(`<p><strong>Shared candidates:</strong> ${edge.shared_candidates}</p>`);
                if (edge.system_mix) lines.push(`<p><strong>System mix:</strong> ${edge.system_mix}</p>`);
                if (edge.row_count !== undefined) lines.push(`<p><strong>Rows:</strong> ${edge.row_count}</p>`);
                lines.push("<p class='help-text'>Click background to clear selection.</p>");
            }
        } else if (state.selectedNodes.length === 2) {
            const left = context.nodeById.get(state.selectedNodes[0]);
            const right = context.nodeById.get(state.selectedNodes[1]);
            lines.push(`<h4>${escapeHtml(shortLabel(left?.label || state.selectedNodes[0]))} + ${escapeHtml(shortLabel(right?.label || state.selectedNodes[1]))}</h4>`);
            lines.push(`<p><strong>Shared neighbors:</strong> ${context.sharedNeighbors.size}</p>`);
            if (context.sharedNeighbors.size) {
                const top = Array.from(context.sharedNeighbors)
                    .map((nodeId) => context.nodeById.get(nodeId))
                    .filter(Boolean)
                    .slice(0, 10);
                lines.push("<ul>");
                top.forEach((node) => lines.push(`<li>${escapeHtml(shortLabel(node.label, 40))}</li>`));
                lines.push("</ul>");
            } else {
                lines.push("<p>No shared neighbors under current filters.</p>");
            }
        } else if (state.selectedNodes.length === 1) {
            const selected = context.nodeById.get(state.selectedNodes[0]);
            lines.push(`<h4>${escapeHtml(shortLabel(selected?.label || state.selectedNodes[0], 48))}</h4>`);
            lines.push(`<p><strong>Connections:</strong> ${context.neighbors.size}</p>`);
            if (selected) {
                const total = Number(selected.total_amount || selected.weighted_degree || 0);
                if (total > 0) lines.push(`<p><strong>Total amount:</strong> ${total.toLocaleString()}</p>`);
            }
            if (context.neighbors.size) {
                const neighborRows = Array.from(context.neighbors)
                    .map((nodeId) => {
                        const node = context.nodeById.get(nodeId);
                        if (!node) return null;
                        const neighborWeight = context.edgeWeights.get(nodeId) || 0;
                        return {node, neighborWeight};
                    })
                    .filter(Boolean)
                    .sort((a, b) => b.neighborWeight - a.neighborWeight)
                    .slice(0, 10);
                lines.push("<ul>");
                neighborRows.forEach((row) => {
                    lines.push(`<li>${escapeHtml(shortLabel(row.node.label, 40))} (${row.neighborWeight.toLocaleString()})</li>`);
                });
                lines.push("</ul>");
            }
        } else {
            const centralNodes = (subset.nodes || [])
                .slice()
                .sort((a, b) => Number(b.total_amount || b.weighted_degree || 0) - Number(a.total_amount || a.weighted_degree || 0))
                .slice(0, 7);
            lines.push("<h4>Selection Panel</h4>");
            lines.push("<p>Click a node or edge to highlight relationships.</p>");
            if (centralNodes.length) {
                lines.push("<p><strong>Top nodes in view:</strong></p><ul>");
                centralNodes.forEach((node) => lines.push(`<li>${escapeHtml(shortLabel(node.label, 40))}</li>`));
                lines.push("</ul>");
            }
        }
        panel.innerHTML = lines.join("");
    };

    const updateTable = (spec, state, context) => {
        const table = byId(spec.tableId);
        if (!table) return;
        const rows = Array.from(table.querySelectorAll("tbody tr[data-edge-id]"));
        if (!rows.length) return;

        let visibleCount = 0;
        for (const row of rows) {
            const source = row.getAttribute("data-source-id");
            const target = row.getAttribute("data-target-id");
            const rowEdgeId = row.getAttribute("data-edge-id");

            let show = true;
            let highlight = false;

            if (state.selectedEdgeId) {
                show = rowEdgeId === state.selectedEdgeId;
                highlight = show;
            } else if (state.selectedNodes.length === 1) {
                const selected = state.selectedNodes[0];
                show = source === selected || target === selected;
                highlight = show;
            } else if (state.selectedNodes.length === 2) {
                const a = state.selectedNodes[0];
                const b = state.selectedNodes[1];
                show = (source === a || source === b || target === a || target === b)
                    && context.highlightNodes.has(source)
                    && context.highlightNodes.has(target);
                highlight = show;
            }

            row.style.display = show ? "" : "none";
            row.classList.toggle("graph-row-highlight", highlight);
            if (show) visibleCount += 1;
        }

        if (!visibleCount) {
            rows.forEach((row) => {
                row.style.display = "";
                row.classList.remove("graph-row-highlight");
            });
        }

        if (state.selectedEdgeId) {
            const safeEdgeId = (typeof CSS !== "undefined" && CSS.escape)
                ? CSS.escape(state.selectedEdgeId)
                : String(state.selectedEdgeId).replaceAll('"', '\\"');
            const selectedRow = table.querySelector(`tbody tr[data-edge-id="${safeEdgeId}"]`);
            if (selectedRow) selectedRow.scrollIntoView({behavior: "smooth", block: "center"});
        }
    };

    const computeSelectionContext = (subset, state) => {
        const nodeById = new Map((subset.nodes || []).map((node) => [node.id, node]));
        const edgeById = new Map((subset.edges || []).map((edge) => [edgeId(edge), edge]));
        const adjacency = buildAdjacency(subset.edges || []);
        const highlightNodes = new Set();
        const highlightEdges = new Set();
        const sharedNeighbors = new Set();
        const neighbors = new Set();
        const edgeWeights = new Map();

        if (state.selectedEdgeId && edgeById.has(state.selectedEdgeId)) {
            const edge = edgeById.get(state.selectedEdgeId);
            highlightEdges.add(state.selectedEdgeId);
            highlightNodes.add(edge.source);
            highlightNodes.add(edge.target);
        } else if (state.selectedNodes.length === 1) {
            const selected = state.selectedNodes[0];
            highlightNodes.add(selected);
            const adjacent = adjacency.get(selected) || new Set();
            adjacent.forEach((nodeId) => {
                highlightNodes.add(nodeId);
                neighbors.add(nodeId);
            });
            for (const edge of subset.edges || []) {
                if (edge.source === selected || edge.target === selected) {
                    highlightEdges.add(edgeId(edge));
                    const other = edge.source === selected ? edge.target : edge.source;
                    edgeWeights.set(other, (edgeWeights.get(other) || 0) + weightValue(edge));
                }
            }
        } else if (state.selectedNodes.length === 2) {
            const left = state.selectedNodes[0];
            const right = state.selectedNodes[1];
            highlightNodes.add(left);
            highlightNodes.add(right);
            const leftNeighbors = adjacency.get(left) || new Set();
            const rightNeighbors = adjacency.get(right) || new Set();
            for (const nodeId of leftNeighbors) {
                if (rightNeighbors.has(nodeId)) sharedNeighbors.add(nodeId);
            }
            sharedNeighbors.forEach((nodeId) => highlightNodes.add(nodeId));

            for (const edge of subset.edges || []) {
                const id = edgeId(edge);
                const isLeftLink = (edge.source === left && sharedNeighbors.has(edge.target))
                    || (edge.target === left && sharedNeighbors.has(edge.source));
                const isRightLink = (edge.source === right && sharedNeighbors.has(edge.target))
                    || (edge.target === right && sharedNeighbors.has(edge.source));
                const directPair = (edge.source === left && edge.target === right)
                    || (edge.source === right && edge.target === left);
                if (isLeftLink || isRightLink || directPair) highlightEdges.add(id);
            }
        } else {
            (subset.nodes || []).forEach((node) => highlightNodes.add(node.id));
            (subset.edges || []).forEach((edge) => highlightEdges.add(edgeId(edge)));
        }

        if (!state.selectedNodes.length && !state.selectedEdgeId) {
            (subset.nodes || []).forEach((node) => highlightNodes.add(node.id));
            (subset.edges || []).forEach((edge) => highlightEdges.add(edgeId(edge)));
        }

        return {
            nodeById,
            edgeById,
            adjacency,
            highlightNodes,
            highlightEdges,
            neighbors,
            sharedNeighbors,
            edgeWeights,
        };
    };

    const renderMatrix = (spec, subset, state) => {
        const matrixEl = spec.matrixId ? byId(spec.matrixId) : null;
        if (!matrixEl) return;
        clearElement(matrixEl);

        const edges = subset.edges || [];
        const nodes = (subset.nodes || [])
            .slice()
            .sort((a, b) => Number(b.total_amount || b.weighted_degree || 0) - Number(a.total_amount || a.weighted_degree || 0))
            .slice(0, 18);
        if (!nodes.length || !edges.length) {
            matrixEl.textContent = "No matrix rows for current filters.";
            return;
        }

        const nodeIds = nodes.map((node) => node.id);
        const nodeSet = new Set(nodeIds);
        const weightMap = new Map();
        let maxWeight = 0;
        for (const edge of edges) {
            if (!nodeSet.has(edge.source) || !nodeSet.has(edge.target)) continue;
            const key = `${edge.source}|${edge.target}`;
            const reverse = `${edge.target}|${edge.source}`;
            const weight = weightValue(edge);
            weightMap.set(key, weight);
            weightMap.set(reverse, weight);
            maxWeight = Math.max(maxWeight, weight);
        }

        const table = document.createElement("table");
        table.className = "graph-matrix-table";

        const header = document.createElement("tr");
        const corner = document.createElement("th");
        corner.textContent = "";
        header.appendChild(corner);
        nodes.forEach((node) => {
            const th = document.createElement("th");
            th.textContent = shortLabel(node.label, 12);
            header.appendChild(th);
        });
        table.appendChild(header);

        nodes.forEach((rowNode) => {
            const row = document.createElement("tr");
            const label = document.createElement("th");
            label.textContent = shortLabel(rowNode.label, 20);
            row.appendChild(label);

            nodes.forEach((colNode) => {
                const td = document.createElement("td");
                const weight = weightMap.get(`${rowNode.id}|${colNode.id}`) || 0;
                const alpha = maxWeight > 0 ? weight / maxWeight : 0;
                td.className = "graph-matrix-cell";
                td.style.background = alpha > 0 ? `rgba(29, 78, 216, ${Math.min(0.85, 0.12 + (alpha * 0.73))})` : "rgba(148, 163, 184, 0.06)";
                td.textContent = weight > 0 ? Math.round(weight).toLocaleString() : "";
                if (weight > 0 && rowNode.id !== colNode.id) {
                    td.classList.add("is-clickable");
                    td.addEventListener("click", () => {
                        const forward = `${rowNode.id}|${colNode.id}|candidate_competition`;
                        const reverse = `${colNode.id}|${rowNode.id}|candidate_competition`;
                        if (subset.edges.find((edge) => edgeId(edge) === forward)) {
                            state.selectedEdgeId = forward;
                        } else {
                            state.selectedEdgeId = reverse;
                        }
                        state.selectedNodes = [];
                        renderSpec(spec.key);
                    });
                }
                row.appendChild(td);
            });
            table.appendChild(row);
        });

        matrixEl.appendChild(table);
    };

    const renderRankedCompetitors = (spec, subset) => {
        if (!spec.rankedId) return;
        const rankedEl = byId(spec.rankedId);
        if (!rankedEl) return;
        clearElement(rankedEl);

        const edges = (subset.edges || [])
            .slice()
            .sort((a, b) => weightValue(b) - weightValue(a))
            .slice(0, 12);
        if (!edges.length) return;

        const title = document.createElement("h4");
        title.textContent = "Top Competitors";
        rankedEl.appendChild(title);

        const list = document.createElement("div");
        list.className = "graph-ranked-list";

        edges.forEach((edge) => {
            const row = document.createElement("div");
            row.className = "graph-ranked-row";

            const label = document.createElement("div");
            label.className = "graph-ranked-label";
            label.textContent = `${shortLabel(edge.source_label || edge.source, 24)} vs ${shortLabel(edge.target_label || edge.target, 24)}`;

            const spark = document.createElement("div");
            spark.className = "graph-ranked-spark";
            const stateAmount = Number(edge.shared_state_amount || 0);
            const federalAmount = Number(edge.shared_federal_amount || 0);
            const crossAmount = Number(edge.shared_cross_amount || 0);
            const total = Math.max(weightValue(edge), stateAmount + federalAmount + crossAmount, 1);
            const segments = [
                {className: "state", value: stateAmount},
                {className: "federal", value: federalAmount},
                {className: "cross", value: crossAmount || Math.max(0, total - stateAmount - federalAmount)},
            ];
            segments.forEach((segment) => {
                const span = document.createElement("span");
                span.className = `seg ${segment.className}`;
                span.style.width = `${Math.max(4, (segment.value / total) * 100)}%`;
                spark.appendChild(span);
            });

            const value = document.createElement("div");
            value.className = "graph-ranked-value";
            value.textContent = Math.round(weightValue(edge)).toLocaleString();

            row.appendChild(label);
            row.appendChild(spark);
            row.appendChild(value);
            list.appendChild(row);
        });
        rankedEl.appendChild(list);
    };

    const applyModeVisibility = (spec, state) => {
        if (!spec.supportsMatrix) return;
        const section = document.querySelector(`[data-graph-section="${spec.key}"]`) || document.querySelector(`[data-graph-controls="${spec.key}"]`)?.closest(".candidate-network-block");
        if (!section) return;
        const networkWrap = section.querySelector(".graph-mode-network");
        const matrixWrap = spec.matrixId ? byId(spec.matrixId) : null;
        if (networkWrap) networkWrap.style.display = state.viewMode === "matrix" ? "none" : "";
        if (matrixWrap) matrixWrap.style.display = state.viewMode === "matrix" ? "block" : "none";
    };

    const bindPanZoom = (svg, viewport, state, size) => {
        if (svg.__panZoomHandlers) {
            const handlers = svg.__panZoomHandlers;
            svg.removeEventListener("wheel", handlers.onWheel);
            svg.removeEventListener("mousedown", handlers.onMouseDown);
            svg.removeEventListener("mousemove", handlers.onMouseMove);
            svg.removeEventListener("mouseup", handlers.onMouseUp);
            svg.removeEventListener("mouseleave", handlers.onMouseLeave);
        }

        let dragging = false;
        let lastX = 0;
        let lastY = 0;

        const applyTransform = () => {
            viewport.setAttribute(
                "transform",
                `translate(${state.panX.toFixed(2)} ${state.panY.toFixed(2)}) scale(${state.zoom.toFixed(3)})`
            );
        };

        applyTransform();

        const onWheel = (event) => {
            event.preventDefault();
            const rect = svg.getBoundingClientRect();
            const mouseX = ((event.clientX - rect.left) / rect.width) * size.width;
            const mouseY = ((event.clientY - rect.top) / rect.height) * size.height;
            const prevZoom = state.zoom;
            const nextZoom = Math.max(0.7, Math.min(3.2, prevZoom * (event.deltaY < 0 ? 1.1 : 0.9)));
            if (nextZoom === prevZoom) return;
            state.zoom = nextZoom;
            state.panX = mouseX - (((mouseX - state.panX) * (nextZoom / prevZoom)));
            state.panY = mouseY - (((mouseY - state.panY) * (nextZoom / prevZoom)));
            applyTransform();
        };
        svg.addEventListener("wheel", onWheel, {passive: false});

        const onMouseDown = (event) => {
            if (event.button !== 0) return;
            dragging = true;
            lastX = event.clientX;
            lastY = event.clientY;
            svg.classList.add("is-dragging");
        };
        svg.addEventListener("mousedown", onMouseDown);

        const onMouseMove = (event) => {
            if (!dragging) return;
            const dx = event.clientX - lastX;
            const dy = event.clientY - lastY;
            lastX = event.clientX;
            lastY = event.clientY;
            const scaleX = size.width / (svg.clientWidth || size.width);
            const scaleY = size.height / (svg.clientHeight || size.height);
            state.panX += dx * scaleX;
            state.panY += dy * scaleY;
            applyTransform();
        };
        svg.addEventListener("mousemove", onMouseMove);

        const onMouseUp = () => {
            if (!dragging) return;
            dragging = false;
            svg.classList.remove("is-dragging");
        };
        svg.addEventListener("mouseup", onMouseUp);

        const onMouseLeave = () => {
            dragging = false;
            svg.classList.remove("is-dragging");
        };
        svg.addEventListener("mouseleave", onMouseLeave);

        svg.__panZoomHandlers = {onWheel, onMouseDown, onMouseMove, onMouseUp, onMouseLeave};
    };

    const centerOnNode = (state, node, size) => {
        if (!node) return;
        state.panX = (size.width / 2) - (node.x * state.zoom);
        state.panY = (size.height / 2) - (node.y * state.zoom);
    };

    const drawGraph = (spec, subset, state) => {
        const svg = byId(spec.svgId);
        if (!svg) return null;
        clearElement(svg);
        const size = viewBoxSize(svg);

        if (!subset.nodes.length) {
            drawEmpty(svg, "No graph nodes for current filters.");
            return {context: null, size};
        }

        const nodes = subset.nodes.map((node) => ({...node}));
        const edges = subset.edges.map((edge) => ({...edge}));

        if (spec.layout === "layered") {
            layoutLayered(nodes, edges, size.width, size.height);
        } else if (spec.layout === "clustered") {
            layoutClustered(nodes, edges, size.width, size.height);
        } else {
            layoutForce(nodes, edges, size.width, size.height, spec.isCandidateGraph ? {charge: 3200, iterations: 280} : {});
        }

        const nodeById = new Map(nodes.map((node) => [node.id, node]));
        const edgeRows = edges
            .map((edge) => ({
                ...edge,
                _id: edgeId(edge),
                _source: nodeById.get(edge.source),
                _target: nodeById.get(edge.target),
            }))
            .filter((edge) => edge._source && edge._target);
        const maxWeight = Math.max(...edgeRows.map((edge) => weightValue(edge)), 1);

        const context = computeSelectionContext({nodes, edges: edgeRows}, state);
        const hasActiveSelection = state.selectedNodes.length > 0 || Boolean(state.selectedEdgeId);

        const viewport = createSvg("g", {class: "graph-viewport"});
        const edgeGroup = createSvg("g", {class: "graph-edges"});
        const nodeGroup = createSvg("g", {class: "graph-nodes"});
        const labelGroup = createSvg("g", {class: "graph-labels"});
        viewport.appendChild(edgeGroup);
        viewport.appendChild(nodeGroup);
        viewport.appendChild(labelGroup);
        svg.appendChild(viewport);

        const topLabelSet = new Set(
            nodes
                .slice()
                .sort((a, b) => Number(b.total_amount || b.weighted_degree || 0) - Number(a.total_amount || a.weighted_degree || 0))
                .slice(0, 8)
                .map((node) => node.id)
        );

        for (const edge of edgeRows) {
            const highlighted = hasActiveSelection && context.highlightEdges.has(edge._id);
            const hiddenByEgo = state.egoMode && (state.selectedNodes.length || state.selectedEdgeId) && !highlighted;
            const line = createSvg("line", {
                x1: edge._source.x.toFixed(2),
                y1: edge._source.y.toFixed(2),
                x2: edge._target.x.toFixed(2),
                y2: edge._target.y.toFixed(2),
                stroke: edgeColor(edge),
                "stroke-opacity": hiddenByEgo ? "0.04" : highlighted ? "0.92" : "0.14",
                "stroke-width": edgeWidth(edge, maxWeight).toFixed(2),
                "data-edge-id": edge._id,
                class: highlighted ? "is-highlighted" : "is-faded",
            });
            line.addEventListener("click", (event) => {
                event.stopPropagation();
                state.selectedEdgeId = edge._id;
                state.selectedNodes = [];
                renderSpec(spec.key);
            });
            edgeGroup.appendChild(line);
        }

        for (const node of nodes) {
            const highlighted = hasActiveSelection && context.highlightNodes.has(node.id);
            const hiddenByEgo = state.egoMode && (state.selectedNodes.length || state.selectedEdgeId) && !highlighted;
            const r = nodeRadius(node);
            const baseAttrs = {
                cx: node.x.toFixed(2),
                cy: node.y.toFixed(2),
                r: r.toFixed(2),
                fill: nodeColor(node),
                "fill-opacity": hiddenByEgo ? "0.12" : highlighted ? "0.95" : "0.22",
                stroke: highlighted ? "#0f172a" : "#ffffff",
                "stroke-width": highlighted ? "1.5" : "1",
                "data-node-id": node.id,
            };
            const shape = drawNodeShape(node, baseAttrs);
            shape.classList.add("graph-node-shape");
            shape.addEventListener("click", (event) => {
                event.stopPropagation();
                state.selectedEdgeId = null;
                if (event.shiftKey && state.selectedNodes.length === 1 && state.selectedNodes[0] !== node.id) {
                    state.selectedNodes = [state.selectedNodes[0], node.id];
                } else if (!event.shiftKey && state.selectedNodes.length === 1 && state.selectedNodes[0] === node.id) {
                    state.selectedNodes = [];
                } else {
                    state.selectedNodes = [node.id];
                }
                renderSpec(spec.key);
            });
            nodeGroup.appendChild(shape);

            const label = createSvg("text", {
                x: node.x.toFixed(2),
                y: (node.y - (r + 6)).toFixed(2),
                "text-anchor": "middle",
                "font-size": "9px",
                fill: "#0f172a",
                "data-label-for": node.id,
            });
            label.textContent = shortLabel(node.label, 24);

            const selected = state.selectedNodes.includes(node.id);
            const showBySelection = hasActiveSelection && (selected || context.highlightNodes.has(node.id));
            const showByZoom = state.zoom >= 1.4;
            const showByDefault = state.showLabels && (topLabelSet.has(node.id) || highlighted);
            const showLabel = showBySelection || showByZoom || showByDefault;

            label.style.opacity = showLabel ? "0.95" : "0";
            labelGroup.appendChild(label);
        }

        if (svg.__bgClickHandler) {
            svg.removeEventListener("click", svg.__bgClickHandler);
        }
        const onBackgroundClick = (event) => {
            if (event.target !== svg) return;
            state.selectedNodes = [];
            state.selectedEdgeId = null;
            renderSpec(spec.key);
        };
        svg.addEventListener("click", onBackgroundClick);
        svg.__bgClickHandler = onBackgroundClick;

        if (state.pendingCenterNode) {
            const targetNode = nodeById.get(state.pendingCenterNode);
            centerOnNode(state, targetNode, size);
            state.pendingCenterNode = null;
        }

        bindPanZoom(svg, viewport, state, size);
        return {context, size, nodes, edges: edgeRows};
    };

    const updateSummary = (spec, graph, subset, state, context) => {
        const summary = byId(spec.summaryId);
        if (!summary) return;
        const selectedNodeText = state.selectedNodes.length ? `Selected nodes: ${state.selectedNodes.length}` : "No node selected";
        const selectedEdgeText = state.selectedEdgeId ? "edge selected" : "no edge selected";
        summary.textContent = `Rendering ${subset.nodes.length} nodes / ${subset.edges.length} edges (total ${graph.summary?.node_count || 0} / ${graph.summary?.edge_count || 0}). ${selectedNodeText}, ${selectedEdgeText}.`;
    };

    const applySearch = (spec) => {
        const rt = runtime.get(spec.key);
        if (!rt) return;
        const term = String(rt.controls.searchInput?.value || "").trim().toLowerCase();
        if (!term) return;
        const nodes = rt.graph.nodes || [];
        const found = nodes.find((node) => String(node.label || "").toLowerCase().includes(term));
        if (!found) {
            rt.controls.searchInput.value = "";
            return;
        }
        rt.state.selectedNodes = [found.id];
        rt.state.selectedEdgeId = null;
        rt.state.pendingCenterNode = found.id;
        renderSpec(spec.key);
    };

    const renderSpec = (key) => {
        const rt = runtime.get(key);
        if (!rt) return;
        const {spec, graph, state} = rt;
        applyModeVisibility(spec, state);

        const subset = buildSubset(graph, spec, state);
        if (spec.supportsMatrix && state.viewMode === "matrix") {
            renderMatrix(spec, subset, state);
            renderRankedCompetitors(spec, subset);
            updateSummary(spec, graph, subset, state, null);
            updatePanel(spec, subset, state, computeSelectionContext(subset, state));
            updateTable(spec, state, computeSelectionContext(subset, state));
            return;
        }

        renderMatrix(spec, subset, state);
        const drawResult = drawGraph(spec, subset, state);
        const context = drawResult?.context || computeSelectionContext(subset, state);
        renderRankedCompetitors(spec, subset);
        updateSummary(spec, graph, subset, state, context);
        updatePanel(spec, subset, state, context);
        updateTable(spec, state, context);
    };

    const bindControls = (spec, graph, state) => {
        const controlsRoot = document.querySelector(`[data-graph-controls="${spec.key}"]`);
        const controls = {
            root: controlsRoot,
            maxEdges: controlsRoot?.querySelector(".graph-max-edges"),
            maxNodes: controlsRoot?.querySelector(".graph-max-nodes"),
            minWeight: controlsRoot?.querySelector(".graph-min-weight"),
            minShared: controlsRoot?.querySelector(".graph-min-shared"),
            searchInput: controlsRoot?.querySelector(".graph-search-input"),
            searchBtn: controlsRoot?.querySelector(".graph-search-btn"),
            egoMode: controlsRoot?.querySelector(".graph-ego-mode"),
            showLabels: controlsRoot?.querySelector(".graph-show-labels"),
            crossOnly: controlsRoot?.querySelector(".graph-cross-only"),
            collapseCommunities: controlsRoot?.querySelector(".graph-collapse-communities"),
            viewMode: controlsRoot?.querySelector(".graph-view-mode"),
        };

        state.maxEdges = Math.max(20, Math.min(Number(controls.maxEdges?.value || 180), 2000));
        state.maxNodes = Math.max(20, Math.min(Number(controls.maxNodes?.value || 90), 500));
        state.minWeight = Math.max(0, Number(controls.minWeight?.value || 0));
        state.minShared = Math.max(1, Number(controls.minShared?.value || 1));
        state.egoMode = Boolean(controls.egoMode?.checked);
        state.showLabels = Boolean(controls.showLabels?.checked);
        state.crossOnly = Boolean(controls.crossOnly?.checked);
        state.collapseCommunities = Boolean(controls.collapseCommunities?.checked);
        state.viewMode = String(controls.viewMode?.value || "network");

        const triggerRender = () => renderSpec(spec.key);
        controls.maxEdges?.addEventListener("change", () => {
            state.maxEdges = Math.max(20, Math.min(Number(controls.maxEdges.value || 180), 2000));
            triggerRender();
        });
        controls.maxNodes?.addEventListener("change", () => {
            state.maxNodes = Math.max(20, Math.min(Number(controls.maxNodes.value || 90), 500));
            triggerRender();
        });
        controls.minWeight?.addEventListener("change", () => {
            state.minWeight = Math.max(0, Number(controls.minWeight.value || 0));
            triggerRender();
        });
        controls.minShared?.addEventListener("change", () => {
            state.minShared = Math.max(1, Number(controls.minShared.value || 1));
            triggerRender();
        });
        controls.egoMode?.addEventListener("change", () => {
            state.egoMode = Boolean(controls.egoMode.checked);
            triggerRender();
        });
        controls.showLabels?.addEventListener("change", () => {
            state.showLabels = Boolean(controls.showLabels.checked);
            triggerRender();
        });
        controls.crossOnly?.addEventListener("change", () => {
            state.crossOnly = Boolean(controls.crossOnly.checked);
            triggerRender();
        });
        controls.collapseCommunities?.addEventListener("change", () => {
            state.collapseCommunities = Boolean(controls.collapseCommunities.checked);
            triggerRender();
        });
        controls.viewMode?.addEventListener("change", () => {
            state.viewMode = String(controls.viewMode.value || "network");
            triggerRender();
        });
        controls.searchBtn?.addEventListener("click", () => applySearch(spec));
        controls.searchInput?.addEventListener("keydown", (event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            applySearch(spec);
        });
        return controls;
    };

    const init = () => {
        graphSpecs.forEach((spec) => {
            const graph = parseGraph(spec.dataId);
            const svg = byId(spec.svgId);
            if (!graph || !svg) return;
            const state = {
                maxEdges: 180,
                maxNodes: 90,
                minWeight: 0,
                minShared: 1,
                egoMode: false,
                showLabels: false,
                crossOnly: false,
                collapseCommunities: false,
                selectedNodes: [],
                selectedEdgeId: null,
                pendingCenterNode: null,
                panX: 0,
                panY: 0,
                zoom: 1,
                viewMode: "network",
            };
            const controls = bindControls(spec, graph, state);
            runtime.set(spec.key, {spec, graph, state, controls});
            renderSpec(spec.key);
        });
    };

    init();
})();
