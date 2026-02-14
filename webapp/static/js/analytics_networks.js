(() => {
    /* ── Shared utilities ── */
    const SVG_NS = "http://www.w3.org/2000/svg";
    const createSvgEl = (tag, attrs = {}) => {
        const el = document.createElementNS(SVG_NS, tag);
        for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
        return el;
    };
    const clearSvg = (svg) => { while (svg.firstChild) svg.removeChild(svg.firstChild); };
    const viewBoxSize = (svg) => {
        const vb = (svg.getAttribute("viewBox") || "0 0 980 520").split(/\s+/).map(Number);
        return { width: vb[2] || 980, height: vb[3] || 520 };
    };
    const drawText = (svg, x, y, text, opts = {}) => {
        const node = createSvgEl("text", {
            x, y, fill: opts.color || "#334155",
            "font-size": opts.size || "10px",
            "text-anchor": opts.anchor || "middle",
            "font-weight": opts.weight || "400",
        });
        node.textContent = text;
        svg.appendChild(node);
        return node;
    };
    const drawEmpty = (svg, message) => {
        clearSvg(svg);
        const { width, height } = viewBoxSize(svg);
        svg.appendChild(createSvgEl("rect", { x: 24, y: 24, width: width - 48, height: height - 48, rx: 10, fill: "#f8fafc", stroke: "#cbd5e1", "stroke-width": 1 }));
        drawText(svg, width / 2, height / 2, message, { size: "12px", color: "#475569" });
    };
    const shortLabel = (text, maxLen = 28) => {
        const s = String(text || "Unknown").trim() || "Unknown";
        return s.length <= maxLen ? s : s.slice(0, maxLen - 1) + "\u2026";
    };
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const hashCode = (s) => {
        let h = 0;
        const t = String(s || "");
        for (let i = 0; i < t.length; i++) { h = ((h << 5) - h) + t.charCodeAt(i); h |= 0; }
        return Math.abs(h);
    };

    const typeColor = {
        donor: "#2563eb", committee: "#059669", candidate: "#dc2626",
        vendor: "#d97706", lobbying_client: "#7c3aed", lobbying_entity: "#a855f7",
        matched_donor: "#6366f1", matched_payee: "#0891b2",
        irs527_org: "#be185d", director: "#ea580c", recipient_target: "#ef4444",
        local_committee: "#059669", federal_committee: "#0284c7",
    };
    const regionColors = {
        "Chicago Metro": "#0ea5e9", "Collar Counties": "#22c55e",
        "Central Illinois": "#f59e0b", "Southern Illinois": "#ef4444",
        "Other Illinois": "#a855f7", "Out of State": "#64748b", Unknown: "#94a3b8",
    };
    const edgeTypeFallback = {
        donor_committee: { label: "Donor -> committee contributions", description: "Observed donor contributions into committee receipts.", unit: "usd", metric: "Contribution amount (USD)" },
        committee_candidate: { label: "Committee receipts linked to candidate", description: "Committee receipts associated with a candidate linkage (not a direct transfer edge).", unit: "usd", metric: "Linked committee receipts (USD)" },
        committee_vendor: { label: "Committee -> vendor spending", description: "Committee expenditures paid to vendor/payee records.", unit: "usd", metric: "Expenditure amount (USD)" },
        donor_local: { label: "Donor -> state committee", description: "Matched donor flow into Illinois state committees.", unit: "usd", metric: "Contribution amount (USD)" },
        donor_federal: { label: "Donor -> federal committee", description: "Matched donor flow into federal committees.", unit: "usd", metric: "Contribution amount (USD)" },
        client_entity: { label: "Client -> lobbying entity", description: "Registered client/entity relationship in lobbying filings.", unit: "count", metric: "Registration link count" },
        client_donor_match: { label: "Client -> matched donor", description: "Fuzzy name match between lobbying client and donor identity.", unit: "score", metric: "Name-match confidence score" },
        client_payee_match: { label: "Client -> matched payee", description: "Fuzzy name match between lobbying client and campaign payee.", unit: "score", metric: "Name-match confidence score" },
        entity_payee_match: { label: "Entity -> matched payee", description: "Fuzzy name match between lobbying entity and campaign payee.", unit: "score", metric: "Name-match confidence score" },
        payee_committee_match: { label: "Payee -> committee spending link", description: "Observed committee spending to matched payee.", unit: "usd", metric: "Matched committee spend (USD)" },
        donor_committee_flow: { label: "Matched donor -> committee flow", description: "Observed donor contributions from matched donor identities.", unit: "usd", metric: "Contribution amount (USD)" },
        org_committee_match: { label: "527 organization -> committee match", description: "Fuzzy name match between IRS 527 organization and committee.", unit: "score", metric: "Name-match confidence score" },
        org_recipient_match: { label: "527 organization -> matched recipient", description: "Matched 527 recipient linked to campaign-finance target.", unit: "usd", metric: "Matched 527 expenditure amount (USD)" },
        org_director: { label: "527 organization -> director", description: "Director listed on IRS 527 filing linked to organization.", unit: "score", metric: "Linkage score" },
        director_donor_match: { label: "Director -> matched donor", description: "Fuzzy name match between 527 director and donor identity.", unit: "score", metric: "Name-match confidence score" },
    };
    const nodeTypeMeaning = {
        donor: "Donor entities contributing into committees.",
        matched_donor: "Donor identities matched from cross-dataset name resolution.",
        committee: "Political committees in state campaign finance filings.",
        local_committee: "Illinois state committees from ISBE data.",
        federal_committee: "Federal committees from FEC data.",
        candidate: "Candidates linked via committee-candidate records.",
        vendor: "Payees receiving committee expenditures.",
        matched_payee: "Payees matched to lobbying or cross-dataset entities.",
        lobbying_client: "Registered lobbying clients.",
        lobbying_entity: "Registered lobbying entities/firms.",
        irs527_org: "IRS 527 political organizations.",
        director: "Directors from IRS 527 filings.",
        recipient_target: "Matched recipient target from 527 expenditure data.",
    };
    const normalizeDisplayKey = (text) => String(text || "").trim().toLowerCase().replace(/\s+/g, " ");
    const edgeMeta = (edge) => {
        const fallback = edgeTypeFallback[edge.edge_type] || {};
        const unit = edge.weight_unit || fallback.unit || "value";
        return {
            label: edge.edge_label || fallback.label || (edge.edge_type ? edge.edge_type.replace(/_/g, " ") : "relationship"),
            description: edge.edge_description || fallback.description || "Graph relationship edge.",
            metric: edge.metric_label || fallback.metric || "Graph weight",
            unit,
            caveat: edge.edge_caveat || "",
        };
    };
    const formatMetric = (value, unit) => {
        const n = Number(value || 0);
        if (unit === "usd") return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
        if (unit === "score") return n.toFixed(3);
        if (unit === "count") return `${Math.round(n).toLocaleString()}`;
        return n.toLocaleString(undefined, { maximumFractionDigits: 3 });
    };
    const unitLabel = (unit) => {
        if (unit === "usd") return "USD";
        if (unit === "score") return "score";
        if (unit === "count") return "count";
        return "value";
    };

    /* ── Load JSON data blocks ── */
    const loadJson = (id) => {
        const el = document.getElementById(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent || "{}"); } catch { return null; }
    };
    const networkData = loadJson("analytics-network-data");
    const vendorData = loadJson("vendor-network-data");
    const overlapData = loadJson("overlap-graph-data");
    const lobbyingData = loadJson("lobbying-graph-data");
    const darkMoneyData = loadJson("ecosystem-527-data");

    if (!networkData || !Array.isArray(networkData.nodes)) return;

    /* ── Build main graph index ── */
    const nodesById = new Map();
    const centralityById = new Map();
    for (const row of networkData.centrality || []) centralityById.set(row.node_id, row);
    for (const node of networkData.nodes) {
        const c = centralityById.get(node.id) || {};
        nodesById.set(node.id, {
            ...node,
            weighted_degree: Number(c.weighted_degree || 0),
            degree: Number(c.degree || 0),
            region: node.region || "Unknown",
        });
    }
    const allEdges = (networkData.edges || [])
        .filter((e) => nodesById.has(e.source) && nodesById.has(e.target))
        .map((e) => ({ ...e, weight: Number(e.weight || 0) }));

    const allRegions = Array.from(new Set(Array.from(nodesById.values()).map((n) => n.region || "Unknown"))).sort();

    /* ── Populate region selects ── */
    const regionSelect = document.getElementById("network-region");
    if (regionSelect) {
        for (const r of allRegions) {
            const opt = document.createElement("option");
            opt.value = r; opt.textContent = r;
            regionSelect.appendChild(opt);
        }
    }

    /* ── Helper: build adjacency map ── */
    const buildAdjacency = (nodes, edges) => {
        const adj = new Map();
        for (const n of nodes) adj.set(n.id, new Set([n.id]));
        for (const e of edges) {
            adj.get(e.source)?.add(e.target);
            adj.get(e.target)?.add(e.source);
        }
        return adj;
    };

    /* ── Helper: apply click-to-lock highlighting + info panel ── */
    const setupClickToLock = (svg, nodeEls, labelEls, edgeEls, adjacency, localById, infoPanelId, graphEdges) => {
        let lockedNodeId = "";
        const infoPanel = infoPanelId ? document.getElementById(infoPanelId) : null;

        // Build edge lookup: nodeId -> [{neighbor, weight, direction, metadata...}]
        const edgeLookup = new Map();
        if (graphEdges) {
            for (const e of graphEdges) {
                const w = Number(e.weight || 0);
                const meta = edgeMeta(e);
                const shared = {
                    edgeType: e.edge_type || "relationship",
                    unit: meta.unit,
                    edgeLabel: meta.label,
                    edgeDescription: meta.description,
                    metricLabel: meta.metric,
                    caveat: meta.caveat || "",
                    matchScoreSum: Number(e.match_score_sum || 0),
                    linkedCommitteeSpendAmount: Number(e.linked_committee_spend_amount || e.matched_amount || 0),
                    linkedCommitteeSpendTransactions: Number(e.linked_committee_spend_transactions || e.matched_transaction_count || 0),
                    estimatedTopDonors: Array.isArray(e.estimated_top_donors) ? e.estimated_top_donors : [],
                    committeeTotalReceipts: Number(e.committee_total_receipts || 0),
                    isDirectTransfer: e.is_direct_transfer,
                };
                if (!edgeLookup.has(e.source)) edgeLookup.set(e.source, []);
                edgeLookup.get(e.source).push({ neighbor: e.target, weight: w, direction: "outgoing", ...shared });
                if (!edgeLookup.has(e.target)) edgeLookup.set(e.target, []);
                edgeLookup.get(e.target).push({ neighbor: e.source, weight: w, direction: "incoming", ...shared });
            }
        }

        const applyHighlight = (nodeId) => {
            const neighborhood = nodeId ? (adjacency.get(nodeId) || new Set([nodeId])) : null;
            for (const c of nodeEls) {
                const keep = !neighborhood || neighborhood.has(c.dataset.nodeId || "");
                c.setAttribute("fill-opacity", keep ? "1" : "0.18");
                c.setAttribute("stroke-opacity", keep ? "1" : "0.12");
            }
            for (const l of labelEls) {
                const keep = !neighborhood || neighborhood.has(l.dataset.nodeId || "");
                l.setAttribute("fill-opacity", keep ? "1" : "0.16");
            }
            for (const e of edgeEls) {
                const keep = !neighborhood || (neighborhood.has(e.dataset.source || "") && neighborhood.has(e.dataset.target || ""));
                e.setAttribute("stroke-opacity", keep ? "0.46" : "0.08");
            }
        };

        const showInfoPanel = (nodeId) => {
            if (!infoPanel || !localById) return;
            const node = localById.get(nodeId);
            if (!node) { infoPanel.style.display = "none"; return; }
            const neighbors = adjacency.get(nodeId) || new Set();

            const connMap = new Map();
            const nodeEdges = edgeLookup.get(nodeId) || [];
            const unitTotals = new Map();
            const relationshipTotals = new Map();
            const uniqueNeighborIds = new Set();

            for (const e of nodeEdges) {
                const neighbor = localById.get(e.neighbor);
                if (!neighbor || e.neighbor === nodeId) continue;
                uniqueNeighborIds.add(e.neighbor);
                const displayKey = `${normalizeDisplayKey(neighbor.label)}|${neighbor.node_type || "unknown"}|${e.edgeType}|${e.unit}`;
                if (!connMap.has(displayKey)) {
                    connMap.set(displayKey, {
                        label: neighbor.label || e.neighbor,
                        type: neighbor.node_type || "unknown",
                        unit: e.unit || "value",
                        edgeType: e.edgeType,
                        edgeLabel: e.edgeLabel,
                        edgeDescription: e.edgeDescription,
                        metricLabel: e.metricLabel,
                        caveat: e.caveat || "",
                        totalWeight: 0,
                        incoming: 0,
                        outgoing: 0,
                        occurrences: 0,
                        linkedCommitteeSpendAmount: 0,
                        linkedCommitteeSpendTransactions: 0,
                        matchScoreSum: 0,
                        estimatedTopDonors: [],
                        committeeTotalReceipts: 0,
                        isDirectTransfer: e.isDirectTransfer,
                    });
                }
                const c = connMap.get(displayKey);
                c.totalWeight += e.weight;
                c.occurrences += 1;
                c.matchScoreSum += Number(e.matchScoreSum || 0);
                c.linkedCommitteeSpendAmount += Number(e.linkedCommitteeSpendAmount || 0);
                c.linkedCommitteeSpendTransactions += Number(e.linkedCommitteeSpendTransactions || 0);
                c.committeeTotalReceipts = Math.max(c.committeeTotalReceipts, Number(e.committeeTotalReceipts || 0));
                if (Array.isArray(e.estimatedTopDonors) && e.estimatedTopDonors.length) c.estimatedTopDonors = e.estimatedTopDonors;
                if (e.direction === "incoming") c.incoming += e.weight;
                else c.outgoing += e.weight;
                unitTotals.set(c.unit, (unitTotals.get(c.unit) || 0) + e.weight);
                relationshipTotals.set(c.edgeType, (relationshipTotals.get(c.edgeType) || 0) + e.weight);
            }

            // Include adjacency-only neighbors with no edge payload (rare fallback case)
            for (const nid of neighbors) {
                if (nid === nodeId || uniqueNeighborIds.has(nid)) continue;
                const neighbor = localById.get(nid);
                if (!neighbor) continue;
                const displayKey = `${normalizeDisplayKey(neighbor.label)}|${neighbor.node_type || "unknown"}|adjacent|value`;
                if (!connMap.has(displayKey)) {
                    connMap.set(displayKey, {
                        label: neighbor.label || nid,
                        type: neighbor.node_type || "unknown",
                        unit: "value",
                        edgeType: "adjacent",
                        edgeLabel: "Adjacent relationship",
                        edgeDescription: "Connected in graph adjacency.",
                        metricLabel: "Graph linkage",
                        caveat: "",
                        totalWeight: 0,
                        incoming: 0,
                        outgoing: 0,
                        occurrences: 1,
                        linkedCommitteeSpendAmount: 0,
                        linkedCommitteeSpendTransactions: 0,
                        matchScoreSum: 0,
                        estimatedTopDonors: [],
                        committeeTotalReceipts: 0,
                        isDirectTransfer: undefined,
                    });
                }
            }

            const connections = Array.from(connMap.values()).sort((a, b) => b.totalWeight - a.totalWeight);
            const topConns = connections.slice(0, 12);
            const connectionCount = Math.max(0, Math.max(neighbors.size - 1, uniqueNeighborIds.size));
            const nodeUnits = new Set(nodeEdges.map((edge) => edge.unit).filter(Boolean));
            const singleNodeUnit = nodeUnits.size === 1 ? Array.from(nodeUnits)[0] : null;

            let html = `<h4>${shortLabel(node.label, 58)}</h4>`;
            html += `<p><strong>Type:</strong> ${node.node_type || "unknown"}`;
            if (node.region && node.region !== "Unknown") html += ` | <strong>Region:</strong> ${node.region}`;
            html += `</p>`;
            if (singleNodeUnit) {
                html += `<p><strong>Weighted degree:</strong> ${formatMetric(node.weighted_degree || 0, singleNodeUnit)} <small>(${unitLabel(singleNodeUnit)}-weighted)</small></p>`;
            } else {
                html += `<p><strong>Weighted degree:</strong> ${Number(node.weighted_degree || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })} <small>(mixed units across edge types)</small></p>`;
            }
            html += `<p><strong>Connections:</strong> ${connectionCount} | <strong>Relationship rows:</strong> ${connections.length}</p>`;
            html += `<p class="help-text"><strong>What this node represents:</strong> ${nodeTypeMeaning[node.node_type] || "Entity in this network graph."}</p>`;
            if (relationshipTotals.size) {
                const breakdown = Array.from(relationshipTotals.entries())
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 4)
                    .map(([edgeType]) => edgeTypeFallback[edgeType]?.label || edgeType.replace(/_/g, " "));
                html += `<p class="help-text"><strong>Relationship meaning:</strong> ${breakdown.join(" | ")}</p>`;
            }
            if (unitTotals.has("score")) {
                html += `<p class="help-text">Score-based edges are fuzzy name-matching confidence, not direct dollar transactions.</p>`;
            }

            if (topConns.length) {
                html += `<table class="data-table" style="font-size:0.82rem;margin-top:0.4rem"><thead><tr><th>Connected To</th><th>Relationship</th><th>Metric</th><th>Flow</th><th>Notes</th></tr></thead><tbody>`;
                for (const c of topConns) {
                    let flowDir = "";
                    if (c.outgoing > 0 && c.incoming > 0) flowDir = "both";
                    else if (c.outgoing > 0) flowDir = "\u2192 outgoing";
                    else if (c.incoming > 0) flowDir = "\u2190 incoming";
                    else flowDir = "-";

                    const notes = [];
                    if (c.occurrences > 1) notes.push(`aggregated ${c.occurrences} links`);
                    if (c.caveat) notes.push(c.caveat);
                    if (c.unit === "score" && c.linkedCommitteeSpendAmount > 0) {
                        notes.push(`linked committee spend ${formatMetric(c.linkedCommitteeSpendAmount, "usd")} (${Math.round(c.linkedCommitteeSpendTransactions).toLocaleString()} txns)`);
                    }
                    if (c.edgeType === "committee_candidate" && c.isDirectTransfer === false) {
                        notes.push("not a direct transfer edge");
                    }
                    if (c.edgeType === "committee_candidate" && c.estimatedTopDonors.length) {
                        const topDonorText = c.estimatedTopDonors
                            .slice(0, 3)
                            .map((d) => `${shortLabel(d.donor_label || d.donor_name, 18)} ${formatMetric(d.estimated_amount_to_candidate_receipts || 0, "usd")}`)
                            .join(", ");
                        if (topDonorText) notes.push(`est. top donor provenance: ${topDonorText}`);
                    }

                    html += `<tr><td>${shortLabel(c.label, 32)}</td><td><small>${shortLabel(c.edgeLabel || c.edgeType, 34)}</small></td><td>${formatMetric(c.totalWeight, c.unit)} <small>${unitLabel(c.unit)}</small></td><td><small>${flowDir}</small></td><td><small>${shortLabel(notes.join(" | ") || "-", 88)}</small></td></tr>`;
                }
                html += `</tbody></table>`;
                if (connections.length > 12) html += `<p class="help-text">Showing top 12 of ${connections.length} relationship rows.</p>`;
            }
            infoPanel.innerHTML = html;
            infoPanel.style.display = "block";
        };

        const hideInfoPanel = () => { if (infoPanel) infoPanel.style.display = "none"; };

        for (const c of nodeEls) {
            const nid = c.dataset.nodeId || "";
            c.addEventListener("mouseenter", () => { if (!lockedNodeId) applyHighlight(nid); });
            c.addEventListener("mouseleave", () => { if (!lockedNodeId) applyHighlight(""); });
            c.addEventListener("click", (ev) => {
                ev.stopPropagation();
                if (lockedNodeId === nid) { lockedNodeId = ""; applyHighlight(""); hideInfoPanel(); return; }
                lockedNodeId = nid; applyHighlight(nid); showInfoPanel(nid);
            });
        }
        svg.addEventListener("click", () => { lockedNodeId = ""; applyHighlight(""); hideInfoPanel(); });
    };

    /* ── Helper: zoom and pan on SVG ── */
    const setupZoomPan = (svg) => {
        const vb = (svg.getAttribute("viewBox") || "0 0 980 520").split(/\s+/).map(Number);
        let vx = vb[0], vy = vb[1], vw = vb[2], vh = vb[3];
        const origW = vw, origH = vh;
        let isPanning = false, startX = 0, startY = 0, startVx = 0, startVy = 0, moved = false;
        const MOVE_THRESHOLD = 4;

        svg.addEventListener("wheel", (e) => {
            e.preventDefault();
            const rect = svg.getBoundingClientRect();
            const mx = (e.clientX - rect.left) / rect.width;
            const my = (e.clientY - rect.top) / rect.height;
            const factor = e.deltaY > 0 ? 1.12 : 0.89;
            const newW = clamp(vw * factor, origW * 0.25, origW * 4);
            const newH = clamp(vh * factor, origH * 0.25, origH * 4);
            vx += (vw - newW) * mx;
            vy += (vh - newH) * my;
            vw = newW; vh = newH;
            svg.setAttribute("viewBox", `${vx.toFixed(1)} ${vy.toFixed(1)} ${vw.toFixed(1)} ${vh.toFixed(1)}`);
        }, { passive: false });

        svg.addEventListener("mousedown", (e) => {
            if (e.button !== 0) return;
            isPanning = true; moved = false;
            startX = e.clientX; startY = e.clientY;
            startVx = vx; startVy = vy;
            svg.classList.add("is-dragging");
        });
        window.addEventListener("mousemove", (e) => {
            if (!isPanning) return;
            const dx = e.clientX - startX, dy = e.clientY - startY;
            if (Math.abs(dx) + Math.abs(dy) > MOVE_THRESHOLD) moved = true;
            const rect = svg.getBoundingClientRect();
            vx = startVx - dx * (vw / rect.width);
            vy = startVy - dy * (vh / rect.height);
            svg.setAttribute("viewBox", `${vx.toFixed(1)} ${vy.toFixed(1)} ${vw.toFixed(1)} ${vh.toFixed(1)}`);
        });
        window.addEventListener("mouseup", () => {
            isPanning = false;
            svg.classList.remove("is-dragging");
        });

        // Suppress click events that were actually pan drags
        svg.addEventListener("click", (e) => {
            if (moved) { e.stopPropagation(); moved = false; }
        }, true);
    };

    /* ── Helper: run force layout ── */
    const runForceLayout = (nodes, edges, width, height, opts = {}) => {
        // Adaptive charge: scale up for denser graphs to prevent overlap
        const baseCharge = opts.charge || (nodes.length > 80 ? 5000 : nodes.length > 40 ? 4000 : 3200);
        const charge = baseCharge;
        const spring = opts.spring || 0.012;
        const centerPull = opts.centerPull || 0.015;
        const damping = opts.damping || 0.86;
        const linkLength = opts.linkLength || (nodes.length > 80 ? 140 : nodes.length > 40 ? 120 : 100);
        const iterations = opts.iterations || Math.min(320, 100 + nodes.length * 2);
        const anchorFn = opts.anchorFn || null;
        const anchorStrength = opts.anchorStrength || 0.01;

        const byId = new Map();
        for (const n of nodes) {
            n.x = (anchorFn ? anchorFn(n, "x", width, height) : Math.random() * (width - 160) + 80);
            n.y = (anchorFn ? anchorFn(n, "y", width, height) : Math.random() * (height - 120) + 60);
            n.vx = 0; n.vy = 0;
            byId.set(n.id, n);
        }

        for (let step = 0; step < iterations; step++) {
            for (let i = 0; i < nodes.length; i++) {
                const n1 = nodes[i];
                for (let j = i + 1; j < nodes.length; j++) {
                    const n2 = nodes[j];
                    let dx = n2.x - n1.x, dy = n2.y - n1.y;
                    const distSq = Math.max(64, dx * dx + dy * dy);
                    const force = charge / distSq;
                    const dist = Math.sqrt(distSq);
                    dx /= dist; dy /= dist;
                    n1.vx -= force * dx; n1.vy -= force * dy;
                    n2.vx += force * dx; n2.vy += force * dy;
                }
            }
            for (const e of edges) {
                const s = byId.get(e.source), t = byId.get(e.target);
                if (!s || !t) continue;
                const dx = t.x - s.x, dy = t.y - s.y;
                const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
                const diff = dist - linkLength;
                const fx = (dx / dist) * spring * diff, fy = (dy / dist) * spring * diff;
                s.vx += fx; s.vy += fy; t.vx -= fx; t.vy -= fy;
            }
            for (const n of nodes) {
                if (anchorFn) {
                    n.vx += (anchorFn(n, "x", width, height) - n.x) * anchorStrength;
                    n.vy += (anchorFn(n, "y", width, height) - n.y) * anchorStrength;
                } else {
                    n.vx += (width / 2 - n.x) * centerPull;
                    n.vy += (height / 2 - n.y) * centerPull;
                }
                n.vx *= damping; n.vy *= damping;
                n.x += n.vx; n.y += n.vy;
                n.x = clamp(n.x, 16, width - 16);
                n.y = clamp(n.y, 16, height - 16);
            }
        }

        // Post-layout collision resolution: push overlapping nodes apart
        const getR = (n) => Math.max(5, Math.min(30, 5 + Math.sqrt(Math.max(n.weighted_degree || 0, 1)) / 2));
        for (let pass = 0; pass < 8; pass++) {
            for (let i = 0; i < nodes.length; i++) {
                const a = nodes[i];
                const ra = getR(a) + 6; // padding
                for (let j = i + 1; j < nodes.length; j++) {
                    const b = nodes[j];
                    const rb = getR(b) + 6;
                    const minDist = ra + rb;
                    let dx = b.x - a.x, dy = b.y - a.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < minDist && dist > 0.01) {
                        const overlap = (minDist - dist) / 2;
                        const nx = dx / dist, ny = dy / dist;
                        a.x -= nx * overlap; a.y -= ny * overlap;
                        b.x += nx * overlap; b.y += ny * overlap;
                        a.x = clamp(a.x, 16, width - 16);
                        a.y = clamp(a.y, 16, height - 16);
                        b.x = clamp(b.x, 16, width - 16);
                        b.y = clamp(b.y, 16, height - 16);
                    }
                }
            }
        }
    };

    /* ── Helper: get node radius ── */
    const getNodeRadius = (node) => Math.max(5, Math.min(30, 5 + Math.sqrt(Math.max(node.weighted_degree || 0, 1)) / 2));

    /* ── Helper: reduce clutter for dense graphs ── */
    const applyDensityFilter = (nodes, edges, mode = "balanced") => {
        const rawNodes = Array.isArray(nodes) ? nodes : [];
        const rawEdges = Array.isArray(edges) ? edges : [];
        if (!rawNodes.length || !rawEdges.length || mode === "full") {
            return {
                nodes: rawNodes.map((n) => ({ ...n })),
                edges: rawEdges.map((e) => ({ ...e })),
                filtered: false,
                rawNodeCount: rawNodes.length,
                rawEdgeCount: rawEdges.length,
            };
        }
        const shouldFilter = mode === "focus" || rawEdges.length > 320 || rawNodes.length > 180;
        if (!shouldFilter) {
            return {
                nodes: rawNodes.map((n) => ({ ...n })),
                edges: rawEdges.map((e) => ({ ...e })),
                filtered: false,
                rawNodeCount: rawNodes.length,
                rawEdgeCount: rawEdges.length,
            };
        }

        const nodeEdgeMap = new Map();
        for (const n of rawNodes) nodeEdgeMap.set(n.id, []);
        for (const e of rawEdges) {
            if (nodeEdgeMap.has(e.source)) nodeEdgeMap.get(e.source).push(e);
            if (nodeEdgeMap.has(e.target)) nodeEdgeMap.get(e.target).push(e);
        }

        const perNodeLimit = mode === "focus" ? 3 : (rawEdges.length > 1200 ? 4 : rawEdges.length > 700 ? 5 : 7);
        const edgeCap = mode === "focus" ? 280 : (rawEdges.length > 1200 ? 720 : rawEdges.length > 700 ? 620 : 520);

        const keepEdgeKeys = new Set();
        const edgeKey = (edge) => `${edge.source}|${edge.target}|${edge.edge_type || ""}`;
        for (const [nodeId, incidentEdges] of nodeEdgeMap.entries()) {
            const sorted = incidentEdges
                .slice()
                .sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0))
                .slice(0, perNodeLimit);
            for (const edge of sorted) {
                keepEdgeKeys.add(edgeKey(edge));
            }
            if (sorted.length === 0) nodeEdgeMap.delete(nodeId);
        }

        const strongestEdges = rawEdges
            .slice()
            .sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0))
            .slice(0, edgeCap);
        for (const edge of strongestEdges) keepEdgeKeys.add(edgeKey(edge));

        let filteredEdges = rawEdges.filter((edge) => keepEdgeKeys.has(edgeKey(edge)));
        filteredEdges = filteredEdges
            .sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0))
            .slice(0, edgeCap);
        const usedNodeIds = new Set();
        for (const edge of filteredEdges) {
            usedNodeIds.add(edge.source);
            usedNodeIds.add(edge.target);
        }
        const filteredNodes = rawNodes.filter((node) => usedNodeIds.has(node.id));

        return {
            nodes: filteredNodes.map((n) => ({ ...n })),
            edges: filteredEdges.map((e) => ({ ...e })),
            filtered: filteredNodes.length !== rawNodes.length || filteredEdges.length !== rawEdges.length,
            rawNodeCount: rawNodes.length,
            rawEdgeCount: rawEdges.length,
        };
    };

    /* ── Helper: render a generic force graph ── */
    const renderForceGraph = (svg, summaryEl, nodes, edges, colorFn, opts = {}) => {
        clearSvg(svg);
        const { width, height } = viewBoxSize(svg);
        if (!nodes.length || !edges.length) {
            drawEmpty(svg, opts.emptyMsg || "No data available for this visualization.");
            if (summaryEl) summaryEl.textContent = "";
            return;
        }

        const densityMode = opts.densityMode || "balanced";
        const filtered = applyDensityFilter(nodes, edges, densityMode);
        if (!filtered.nodes.length || !filtered.edges.length) {
            drawEmpty(svg, opts.emptyMsg || "No data available for this visualization.");
            if (summaryEl) summaryEl.textContent = "";
            return;
        }

        runForceLayout(filtered.nodes, filtered.edges, width, height, opts);
        const adjacency = buildAdjacency(filtered.nodes, filtered.edges);
        const localById = new Map(filtered.nodes.map((n) => [n.id, n]));

        const edgeGroup = createSvgEl("g");
        const nodeGroup = createSvgEl("g");
        const labelGroup = createSvgEl("g");
        svg.appendChild(edgeGroup);
        svg.appendChild(nodeGroup);
        svg.appendChild(labelGroup);

        const edgeEls = [];
        for (const e of filtered.edges) {
            const s = localById.get(e.source), t = localById.get(e.target);
            if (!s || !t) continue;
            const meta = edgeMeta(e);
            const line = createSvgEl("line", {
                x1: s.x.toFixed(2), y1: s.y.toFixed(2),
                x2: t.x.toFixed(2), y2: t.y.toFixed(2),
                stroke: "#94a3b8", "stroke-opacity": "0.42",
                "stroke-width": String(Math.max(0.8, Math.log10((e.weight || 1) + 1))),
            });
            line.dataset.source = e.source;
            line.dataset.target = e.target;
            const title = createSvgEl("title");
            title.textContent = `${shortLabel(s.label)} \u2192 ${shortLabel(t.label)} | ${meta.label}: ${formatMetric(e.weight || 0, meta.unit)}`;
            line.appendChild(title);
            edgeGroup.appendChild(line);
            edgeEls.push(line);
        }

        const topLabels = filtered.nodes.slice().sort((a, b) => (b.weighted_degree || 0) - (a.weighted_degree || 0)).slice(0, 30);
        const labeledSet = new Set(topLabels.map((n) => n.id));

        const nodeEls = [];
        const labelEls = [];
        for (const n of filtered.nodes) {
            const r = getNodeRadius(n);
            const circle = createSvgEl("circle", {
                cx: n.x.toFixed(2), cy: n.y.toFixed(2), r,
                fill: colorFn(n), "fill-opacity": "0.9",
                stroke: "#0f172a", "stroke-width": "0.7",
            });
            circle.dataset.nodeId = n.id;
            circle.style.cursor = "pointer";
            const title = createSvgEl("title");
            title.textContent = `${n.label} (${n.node_type}) | weighted degree ${Number(n.weighted_degree || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
            circle.appendChild(title);
            nodeGroup.appendChild(circle);
            nodeEls.push(circle);

            if (labeledSet.has(n.id)) {
                const text = createSvgEl("text", {
                    x: (n.x + r + 3).toFixed(2), y: (n.y + 4).toFixed(2),
                    "font-size": "11", fill: "#0f172a",
                });
                text.dataset.nodeId = n.id;
                text.textContent = shortLabel(n.label, 22);
                labelGroup.appendChild(text);
                labelEls.push(text);
            }
        }

        setupClickToLock(svg, nodeEls, labelEls, edgeEls, adjacency, localById, opts.infoPanelId, filtered.edges);
        setupZoomPan(svg);
        if (summaryEl) {
            const modeLabel = densityMode === "focus" ? "Strongest links" : densityMode === "full" ? "Full detail" : "Balanced";
            if (filtered.filtered) {
                summaryEl.textContent = `Showing ${filtered.nodes.length}/${filtered.rawNodeCount} nodes and ${filtered.edges.length}/${filtered.rawEdgeCount} edges (${modeLabel}). Click a node to inspect relationship meaning and metrics. Scroll to zoom, drag to pan.`;
            } else {
                summaryEl.textContent = `${filtered.nodes.length} nodes, ${filtered.edges.length} edges (${modeLabel}). Click a node to inspect relationship meaning and metrics. Scroll to zoom, drag to pan.`;
            }
        }
    };

    /* ── Helper: build Sankey column layout ── */
    const buildColumn = (rows, weights, gap, minHeight, availH, marginTop) => {
        const total = weights.reduce((s, v) => s + v, 0) || 1;
        const raw = weights.map((v) => Math.max(minHeight, (v / total) * (availH - gap * (rows.length - 1))));
        const totalRaw = raw.reduce((s, v) => s + v, 0);
        const target = Math.max(10, availH - gap * (rows.length - 1));
        const factor = totalRaw > 0 ? target / totalRaw : 1;
        const heights = raw.map((v) => v * factor);
        const positions = new Map();
        let y = marginTop;
        rows.forEach((row, idx) => { positions.set(row.id, { y, h: heights[idx] }); y += heights[idx] + gap; });
        return positions;
    };

    /* ═════════════════════════════════════════════════════════════════
       1. INTERACTIVE FORCE GRAPH
       ═════════════════════════════════════════════════════════════════ */
    const networkSvg = document.getElementById("network-svg");
    const networkSummary = document.getElementById("network-summary");
    const modeSelect = document.getElementById("network-view-mode");
    const renderBtn = document.getElementById("network-render-btn");
    const densitySelect = document.getElementById("graph-density-mode");
    const getDensityMode = () => densitySelect?.value || "balanced";

    const getGraphSubset = () => {
        const mode = modeSelect ? modeSelect.value : "power";
        const selectedRegion = regionSelect ? regionSelect.value : "All";
        if (mode === "power") {
            const top = Array.from(nodesById.values()).sort((a, b) => b.weighted_degree - a.weighted_degree).slice(0, 45);
            const keep = new Set(top.map((n) => n.id));
            for (const e of allEdges) { if (keep.has(e.source) || keep.has(e.target)) { keep.add(e.source); keep.add(e.target); } }
            return { nodes: Array.from(keep).map((id) => ({ ...nodesById.get(id) })), edges: allEdges.filter((e) => keep.has(e.source) && keep.has(e.target)), mode };
        }
        if (selectedRegion === "All") {
            return { nodes: Array.from(nodesById.values()).map((n) => ({ ...n })), edges: allEdges.slice(), mode };
        }
        const seed = new Set(Array.from(nodesById.values()).filter((n) => n.region === selectedRegion).map((n) => n.id));
        for (const e of allEdges) { if (seed.has(e.source) || seed.has(e.target)) { seed.add(e.source); seed.add(e.target); } }
        return { nodes: Array.from(seed).map((id) => ({ ...nodesById.get(id) })), edges: allEdges.filter((e) => seed.has(e.source) && seed.has(e.target)), mode };
    };

    const renderForce = () => {
        if (!networkSvg) return;
        const { nodes, edges, mode } = getGraphSubset();
        const colorFn = (n) => mode === "region" ? (regionColors[n.region] || regionColors.Unknown) : (typeColor[n.node_type] || "#64748b");
        renderForceGraph(networkSvg, networkSummary, nodes, edges, colorFn, {
            emptyMsg: "No network data. Switch to Full mode.",
            infoPanelId: "network-info-panel",
            densityMode: getDensityMode(),
        });
        // Toggle description text based on mode
        const descEl = document.getElementById("network-description");
        if (descEl) {
            descEl.textContent = mode === "region"
                ? "Regional view: nodes are colored by geographic region. Filter by a specific region to focus the graph."
                : "Power Players view: top contribution nodes and their direct connections. Node detail explains whether edge values are dollars, counts, or match scores.";
        }
    };

    if (renderBtn) renderBtn.addEventListener("click", renderForce);
    if (modeSelect) modeSelect.addEventListener("change", () => {
        if (regionSelect) regionSelect.disabled = modeSelect.value === "power";
        renderForce();
    });
    if (regionSelect) {
        regionSelect.disabled = true;
        regionSelect.addEventListener("change", () => { if (modeSelect && modeSelect.value === "region") renderForce(); });
    }

    /* ═════════════════════════════════════════════════════════════════
       2. THREE-COLUMN SANKEY FLOW
       ═════════════════════════════════════════════════════════════════ */
    const sankeySvg = document.getElementById("sankey-svg");
    const sankeySummary = document.getElementById("sankey-summary");
    const sankeyDonorLimit = document.getElementById("sankey-donor-limit");
    const sankeyCandidateLimit = document.getElementById("sankey-candidate-limit");
    const sankeyRenderBtn = document.getElementById("sankey-render-btn");

    const renderSankey = () => {
        if (!sankeySvg) return;
        clearSvg(sankeySvg);
        const { width, height } = viewBoxSize(sankeySvg);
        const maxDonors = Number(sankeyDonorLimit?.value || 25);
        const maxCandidates = Number(sankeyCandidateLimit?.value || 15);
        const marginTop = 30, marginBottom = 20;
        const nodeWidth = 14;

        // Classify nodes
        const donors = [], committees = [], candidates = [];
        for (const n of nodesById.values()) {
            if (n.node_type === "donor") donors.push(n);
            else if (n.node_type === "committee") committees.push(n);
            else if (n.node_type === "candidate") candidates.push(n);
        }

        // Accumulate flows
        const donorToCommittee = new Map(); // donor->committee weight
        const committeeToCandidate = new Map(); // committee->candidate weight
        const donorTotals = new Map(), committeeTotals = new Map(), candidateTotals = new Map();

        for (const e of allEdges) {
            const s = nodesById.get(e.source), t = nodesById.get(e.target);
            if (!s || !t) continue;
            if (s.node_type === "donor" && t.node_type === "committee") {
                const key = `${s.id}|${t.id}`;
                donorToCommittee.set(key, (donorToCommittee.get(key) || 0) + e.weight);
                donorTotals.set(s.id, (donorTotals.get(s.id) || 0) + e.weight);
                committeeTotals.set(t.id, (committeeTotals.get(t.id) || 0) + e.weight);
            } else if (s.node_type === "committee" && t.node_type === "candidate") {
                const key = `${s.id}|${t.id}`;
                committeeToCandidate.set(key, (committeeToCandidate.get(key) || 0) + e.weight);
                committeeTotals.set(s.id, (committeeTotals.get(s.id) || 0) + e.weight);
                candidateTotals.set(t.id, (candidateTotals.get(t.id) || 0) + e.weight);
            }
        }

        const topDonors = donors.filter((d) => donorTotals.has(d.id)).sort((a, b) => (donorTotals.get(b.id) || 0) - (donorTotals.get(a.id) || 0)).slice(0, maxDonors);
        const topCandidates = candidates.filter((c) => candidateTotals.has(c.id)).sort((a, b) => (candidateTotals.get(b.id) || 0) - (candidateTotals.get(a.id) || 0)).slice(0, maxCandidates);

        // Filter committees to those connected to top donors AND top candidates
        const topDonorIds = new Set(topDonors.map((d) => d.id));
        const topCandidateIds = new Set(topCandidates.map((c) => c.id));
        const connectedCommittees = new Set();
        for (const [key] of donorToCommittee) {
            const [did, cid] = key.split("|");
            if (topDonorIds.has(did)) connectedCommittees.add(cid);
        }
        const topCommittees = committees.filter((c) => connectedCommittees.has(c.id)).sort((a, b) => (committeeTotals.get(b.id) || 0) - (committeeTotals.get(a.id) || 0)).slice(0, 20);
        const topCommitteeIds = new Set(topCommittees.map((c) => c.id));

        if (!topDonors.length || !topCommittees.length) {
            drawEmpty(sankeySvg, "Not enough data for Sankey flow.");
            return;
        }

        const availH = height - marginTop - marginBottom;
        const donorX = 140, committeeX = width / 2 - nodeWidth / 2, candidateX = width - 160;

        const donorLayout = buildColumn(topDonors, topDonors.map((d) => donorTotals.get(d.id) || 1), 4, 8, availH, marginTop);
        const committeeLayout = buildColumn(topCommittees, topCommittees.map((c) => committeeTotals.get(c.id) || 1), 4, 6, availH, marginTop);
        const candidateLayout = buildColumn(topCandidates, topCandidates.map((c) => candidateTotals.get(c.id) || 1), 5, 8, availH, marginTop);

        // Background
        sankeySvg.appendChild(createSvgEl("rect", { x: 16, y: 16, width: width - 32, height: height - 32, rx: 10, fill: "#f8fafc" }));

        // Draw ribbons: donor -> committee
        const donorOffsets = new Map(), committeeOffsetsL = new Map(), committeeOffsetsR = new Map(), candidateOffsets = new Map();
        const sortedDC = Array.from(donorToCommittee.entries()).filter(([k]) => { const [d, c] = k.split("|"); return topDonorIds.has(d) && topCommitteeIds.has(c); }).sort((a, b) => b[1] - a[1]);

        for (const [key, weight] of sortedDC) {
            const [did, cid] = key.split("|");
            const dBox = donorLayout.get(did), cBox = committeeLayout.get(cid);
            if (!dBox || !cBox) continue;
            const dTotal = donorTotals.get(did) || 1, cTotal = committeeTotals.get(cid) || 1;
            const span = Math.max(1, Math.min(dBox.h * (weight / dTotal), cBox.h * (weight / cTotal)));
            const dOff = donorOffsets.get(did) || 0, cOff = committeeOffsetsL.get(cid) || 0;
            const y1 = dBox.y + dOff + span / 2, y2 = cBox.y + cOff + span / 2;
            donorOffsets.set(did, dOff + span);
            committeeOffsetsL.set(cid, cOff + span);
            const cx1 = donorX + nodeWidth + 80, cx2 = committeeX - 80;
            sankeySvg.appendChild(createSvgEl("path", {
                d: `M ${donorX + nodeWidth} ${y1} C ${cx1} ${y1}, ${cx2} ${y2}, ${committeeX} ${y2}`,
                fill: "none", stroke: "#2563eb", "stroke-width": span, "stroke-opacity": "0.3",
            }));
        }

        // Draw ribbons: committee -> candidate
        const sortedCC = Array.from(committeeToCandidate.entries()).filter(([k]) => { const [c, ca] = k.split("|"); return topCommitteeIds.has(c) && topCandidateIds.has(ca); }).sort((a, b) => b[1] - a[1]);

        for (const [key, weight] of sortedCC) {
            const [cid, caid] = key.split("|");
            const cBox = committeeLayout.get(cid), caBox = candidateLayout.get(caid);
            if (!cBox || !caBox) continue;
            const cTotal = committeeTotals.get(cid) || 1, caTotal = candidateTotals.get(caid) || 1;
            const span = Math.max(1, Math.min(cBox.h * (weight / cTotal), caBox.h * (weight / caTotal)));
            const cOff = committeeOffsetsR.get(cid) || 0, caOff = candidateOffsets.get(caid) || 0;
            const y1 = cBox.y + cOff + span / 2, y2 = caBox.y + caOff + span / 2;
            committeeOffsetsR.set(cid, cOff + span);
            candidateOffsets.set(caid, caOff + span);
            const cx1 = committeeX + nodeWidth + 80, cx2 = candidateX - 80;
            sankeySvg.appendChild(createSvgEl("path", {
                d: `M ${committeeX + nodeWidth} ${y1} C ${cx1} ${y1}, ${cx2} ${y2}, ${candidateX} ${y2}`,
                fill: "none", stroke: "#dc2626", "stroke-width": span, "stroke-opacity": "0.3",
            }));
        }

        // Draw node bars + labels
        for (const d of topDonors) {
            const box = donorLayout.get(d.id);
            if (!box) continue;
            sankeySvg.appendChild(createSvgEl("rect", { x: donorX, y: box.y, width: nodeWidth, height: box.h, rx: 3, fill: "#2563eb", stroke: "#1d4ed8", "stroke-width": 1 }));
            drawText(sankeySvg, donorX - 6, box.y + box.h / 2 + 3, shortLabel(d.label, 22), { anchor: "end", size: "9px" });
        }
        for (const c of topCommittees) {
            const box = committeeLayout.get(c.id);
            if (!box) continue;
            sankeySvg.appendChild(createSvgEl("rect", { x: committeeX, y: box.y, width: nodeWidth, height: box.h, rx: 3, fill: "#059669", stroke: "#047857", "stroke-width": 1 }));
        }
        for (const c of topCandidates) {
            const box = candidateLayout.get(c.id);
            if (!box) continue;
            sankeySvg.appendChild(createSvgEl("rect", { x: candidateX, y: box.y, width: nodeWidth, height: box.h, rx: 3, fill: "#dc2626", stroke: "#b91c1c", "stroke-width": 1 }));
            drawText(sankeySvg, candidateX + nodeWidth + 6, box.y + box.h / 2 + 3, shortLabel(c.label, 24), { anchor: "start", size: "9px" });
        }

        // Column headers
        drawText(sankeySvg, donorX + nodeWidth / 2, 18, "Donors", { size: "12px", weight: "600", color: "#0f172a" });
        drawText(sankeySvg, committeeX + nodeWidth / 2, 18, "Committees", { size: "12px", weight: "600", color: "#0f172a" });
        drawText(sankeySvg, candidateX + nodeWidth / 2, 18, "Candidates", { size: "12px", weight: "600", color: "#0f172a" });

        if (sankeySummary) sankeySummary.textContent = `Sankey: ${topDonors.length} donors \u2192 ${topCommittees.length} committees \u2192 ${topCandidates.length} candidates`;
    };

    if (sankeyRenderBtn) sankeyRenderBtn.addEventListener("click", renderSankey);
    if (sankeyDonorLimit) sankeyDonorLimit.addEventListener("change", renderSankey);
    if (sankeyCandidateLimit) sankeyCandidateLimit.addEventListener("change", renderSankey);

    /* ═════════════════════════════════════════════════════════════════
       3. DONOR x COMMITTEE HEATMAP
       ═════════════════════════════════════════════════════════════════ */
    const heatmapSvg = document.getElementById("heatmap-svg");
    const heatmapSummary = document.getElementById("heatmap-summary");
    const heatmapDonorLimit = document.getElementById("heatmap-donor-limit");
    const heatmapCommitteeLimit = document.getElementById("heatmap-committee-limit");
    const heatmapRenderBtn = document.getElementById("heatmap-render-btn");

    const renderHeatmap = () => {
        if (!heatmapSvg) return;
        clearSvg(heatmapSvg);
        const { width, height } = viewBoxSize(heatmapSvg);
        const maxDonors = Number(heatmapDonorLimit?.value || 22);
        const maxCommittees = Number(heatmapCommitteeLimit?.value || 14);

        const donorTotals = new Map(), committeeTotals = new Map(), cellValues = new Map();
        for (const e of allEdges) {
            const s = nodesById.get(e.source), t = nodesById.get(e.target);
            if (!s || !t) continue;
            let donorId = "", committeeId = "";
            if (s.node_type === "donor" && t.node_type === "committee") { donorId = s.id; committeeId = t.id; }
            else if (s.node_type === "committee" && t.node_type === "donor") { donorId = t.id; committeeId = s.id; }
            else continue;
            donorTotals.set(donorId, (donorTotals.get(donorId) || 0) + e.weight);
            committeeTotals.set(committeeId, (committeeTotals.get(committeeId) || 0) + e.weight);
            const key = `${donorId}|${committeeId}`;
            cellValues.set(key, (cellValues.get(key) || 0) + e.weight);
        }

        const donors = Array.from(nodesById.values()).filter((n) => n.node_type === "donor" && donorTotals.has(n.id)).sort((a, b) => (donorTotals.get(b.id) || 0) - (donorTotals.get(a.id) || 0)).slice(0, maxDonors);
        const committees = Array.from(nodesById.values()).filter((n) => n.node_type === "committee" && committeeTotals.has(n.id)).sort((a, b) => (committeeTotals.get(b.id) || 0) - (committeeTotals.get(a.id) || 0)).slice(0, maxCommittees);

        if (!donors.length || !committees.length) {
            drawEmpty(heatmapSvg, "Not enough data for heatmap.");
            return;
        }

        const margin = { top: 140, right: 26, bottom: 36, left: 250 };
        const gridW = Math.max(120, width - margin.left - margin.right);
        const gridH = Math.max(120, height - margin.top - margin.bottom);
        const cellW = gridW / committees.length, cellH = gridH / donors.length;
        const maxVal = Math.max(...Array.from(cellValues.values()), 1);

        heatmapSvg.appendChild(createSvgEl("rect", { x: 16, y: 16, width: width - 32, height: height - 32, rx: 10, fill: "#f8fafc", stroke: "#cbd5e1", "stroke-width": 1 }));
        drawText(heatmapSvg, 24, 34, "Rows: top donors | Columns: top committees", { anchor: "start", size: "11px", color: "#0f172a", weight: "600" });

        // Row labels
        for (let r = 0; r < donors.length; r++) {
            const y = margin.top + r * cellH;
            drawText(heatmapSvg, margin.left - 8, y + cellH / 2 + 3, shortLabel(donors[r].label, 30), { anchor: "end", size: "9px" });
        }

        // Column headers (rotated)
        for (let c = 0; c < committees.length; c++) {
            const x = margin.left + c * cellW + cellW / 2;
            const label = createSvgEl("text", {
                x, y: margin.top - 22, fill: "#334155", "font-size": "8px", "text-anchor": "start",
                transform: `rotate(-40 ${x} ${margin.top - 22})`,
            });
            label.textContent = shortLabel(committees[c].label, 24);
            heatmapSvg.appendChild(label);
        }

        // Cells
        for (let r = 0; r < donors.length; r++) {
            for (let c = 0; c < committees.length; c++) {
                const key = `${donors[r].id}|${committees[c].id}`;
                const val = Number(cellValues.get(key) || 0);
                const ratio = val > 0 ? val / maxVal : 0;
                const x = margin.left + c * cellW, y = margin.top + r * cellH;
                const fill = ratio > 0 ? "#059669" : "#e2e8f0";
                const rect = createSvgEl("rect", {
                    x, y, width: Math.max(1, cellW - 1), height: Math.max(1, cellH - 1),
                    fill, "fill-opacity": ratio > 0 ? (0.14 + ratio * 0.78).toFixed(3) : "1",
                    stroke: "#fff", "stroke-width": 0.8,
                });
                const title = createSvgEl("title");
                title.textContent = `${donors[r].label} \u2192 ${committees[c].label} | $${val.toLocaleString()}`;
                rect.appendChild(title);
                heatmapSvg.appendChild(rect);
            }
        }

        // Legend
        const lx = margin.left, ly = height - 18;
        for (let i = 0; i <= 8; i++) {
            heatmapSvg.appendChild(createSvgEl("rect", { x: lx + i * 18, y: ly, width: 18, height: 8, fill: "#059669", "fill-opacity": (0.14 + (i / 8) * 0.78).toFixed(3) }));
        }
        drawText(heatmapSvg, lx - 4, ly + 7, "Lower", { anchor: "end", size: "8px", color: "#64748b" });
        drawText(heatmapSvg, lx + 8 * 18 + 24, ly + 7, "Higher", { anchor: "start", size: "8px", color: "#64748b" });

        if (heatmapSummary) heatmapSummary.textContent = `Heatmap: ${donors.length} donors \u00d7 ${committees.length} committees`;
    };

    if (heatmapRenderBtn) heatmapRenderBtn.addEventListener("click", renderHeatmap);
    if (heatmapDonorLimit) heatmapDonorLimit.addEventListener("change", renderHeatmap);
    if (heatmapCommitteeLimit) heatmapCommitteeLimit.addEventListener("change", renderHeatmap);

    /* ═════════════════════════════════════════════════════════════════
       4. VENDOR EXPENDITURE NETWORK
       ═════════════════════════════════════════════════════════════════ */
    const vendorSvg = document.getElementById("vendor-svg");
    const vendorSummary = document.getElementById("vendor-summary");

    const renderVendor = () => {
        if (!vendorSvg || !vendorData) return;
        const nodes = (vendorData.nodes || []).map((n) => ({
            ...n, weighted_degree: 0, degree: 0,
        }));
        const edges = (vendorData.edges || []).map((e) => ({ ...e, weight: Number(e.weight || 0) }));
        // Compute weighted degree
        for (const e of edges) {
            const s = nodes.find((n) => n.id === e.source);
            const t = nodes.find((n) => n.id === e.target);
            if (s) { s.weighted_degree += e.weight; s.degree++; }
            if (t) { t.weighted_degree += e.weight; t.degree++; }
        }
        const colorFn = (n) => {
            if (n.is_lobbying_match) return "#be185d";
            return typeColor[n.node_type] || "#64748b";
        };
        renderForceGraph(vendorSvg, vendorSummary, nodes, edges, colorFn, {
            emptyMsg: "No vendor expenditure data available.",
            charge: 3000, spring: 0.012, iterations: 200,
            infoPanelId: "vendor-info-panel",
            densityMode: getDensityMode(),
        });
    };

    /* ═════════════════════════════════════════════════════════════════
       5. COMBINED MONEY FLOW (4-column Sankey)
       ═════════════════════════════════════════════════════════════════ */
    const combinedSvg = document.getElementById("combined-flow-svg");
    const combinedSummary = document.getElementById("combined-summary");
    const combinedDonorLimit = document.getElementById("combined-donor-limit");
    const combinedVendorLimit = document.getElementById("combined-vendor-limit");
    const combinedRenderBtn = document.getElementById("combined-render-btn");

    const renderCombined = () => {
        if (!combinedSvg) return;
        clearSvg(combinedSvg);
        const { width, height } = viewBoxSize(combinedSvg);
        const maxDonors = Number(combinedDonorLimit?.value || 15);
        const maxVendors = Number(combinedVendorLimit?.value || 10);
        const marginTop = 30, nodeWidth = 12;

        // Donor -> Committee flows
        const donorTotals = new Map(), committeeTotals = new Map(), candidateTotals = new Map(), vendorTotals = new Map();
        const dcFlows = new Map(), ccFlows = new Map(), cvFlows = new Map();

        for (const e of allEdges) {
            const s = nodesById.get(e.source), t = nodesById.get(e.target);
            if (!s || !t) continue;
            if (s.node_type === "donor" && t.node_type === "committee") {
                dcFlows.set(`${s.id}|${t.id}`, (dcFlows.get(`${s.id}|${t.id}`) || 0) + e.weight);
                donorTotals.set(s.id, (donorTotals.get(s.id) || 0) + e.weight);
                committeeTotals.set(t.id, (committeeTotals.get(t.id) || 0) + e.weight);
            } else if (s.node_type === "committee" && t.node_type === "candidate") {
                ccFlows.set(`${s.id}|${t.id}`, (ccFlows.get(`${s.id}|${t.id}`) || 0) + e.weight);
                candidateTotals.set(t.id, (candidateTotals.get(t.id) || 0) + e.weight);
            }
        }

        // Vendor flows from vendorData
        if (vendorData) {
            for (const e of vendorData.edges || []) {
                const w = Number(e.weight || 0);
                cvFlows.set(`${e.source}|${e.target}`, (cvFlows.get(`${e.source}|${e.target}`) || 0) + w);
                vendorTotals.set(e.target, (vendorTotals.get(e.target) || 0) + w);
            }
        }

        const topDonors = Array.from(nodesById.values()).filter((n) => n.node_type === "donor" && donorTotals.has(n.id)).sort((a, b) => (donorTotals.get(b.id) || 0) - (donorTotals.get(a.id) || 0)).slice(0, maxDonors);
        const topCandidates = Array.from(nodesById.values()).filter((n) => n.node_type === "candidate" && candidateTotals.has(n.id)).sort((a, b) => (candidateTotals.get(b.id) || 0) - (candidateTotals.get(a.id) || 0)).slice(0, 10);

        const topDonorIds = new Set(topDonors.map((d) => d.id));
        const connectedCommittees = new Set();
        for (const [k] of dcFlows) { const [d, c] = k.split("|"); if (topDonorIds.has(d)) connectedCommittees.add(c); }
        const topCommittees = Array.from(nodesById.values()).filter((n) => n.node_type === "committee" && connectedCommittees.has(n.id)).sort((a, b) => (committeeTotals.get(b.id) || 0) - (committeeTotals.get(a.id) || 0)).slice(0, 12);
        const topCommitteeIds = new Set(topCommittees.map((c) => c.id));

        const vendorNodes = (vendorData?.nodes || []).filter((n) => n.node_type === "vendor" && vendorTotals.has(n.id));
        const topVendors = vendorNodes.sort((a, b) => (vendorTotals.get(b.id) || 0) - (vendorTotals.get(a.id) || 0)).slice(0, maxVendors);

        if (!topDonors.length || !topCommittees.length) {
            drawEmpty(combinedSvg, "Not enough data for combined flow.");
            return;
        }

        const availH = height - marginTop - 20;
        const col1X = 130, col2X = width * 0.38, col3X = width * 0.62, col4X = width - 140;

        const donorLayout = buildColumn(topDonors, topDonors.map((d) => donorTotals.get(d.id) || 1), 3, 6, availH, marginTop);
        const committeeLayout = buildColumn(topCommittees, topCommittees.map((c) => committeeTotals.get(c.id) || 1), 3, 5, availH, marginTop);
        const candidateLayout = buildColumn(topCandidates, topCandidates.map((c) => candidateTotals.get(c.id) || 1), 4, 6, availH, marginTop);
        const vendorLayout = topVendors.length ? buildColumn(topVendors, topVendors.map((v) => vendorTotals.get(v.id) || 1), 4, 6, availH, marginTop) : new Map();

        combinedSvg.appendChild(createSvgEl("rect", { x: 12, y: 12, width: width - 24, height: height - 24, rx: 10, fill: "#f8fafc" }));

        // Ribbons: donor -> committee (blue)
        const dOff = new Map(), cOffL = new Map(), cOffR = new Map(), caOff = new Map(), vOff = new Map();
        for (const [key, weight] of Array.from(dcFlows.entries()).sort((a, b) => b[1] - a[1])) {
            const [did, cid] = key.split("|");
            if (!topDonorIds.has(did) || !topCommitteeIds.has(cid)) continue;
            const dBox = donorLayout.get(did), cBox = committeeLayout.get(cid);
            if (!dBox || !cBox) continue;
            const span = Math.max(1, Math.min(dBox.h * (weight / (donorTotals.get(did) || 1)), cBox.h * (weight / (committeeTotals.get(cid) || 1))));
            const y1 = dBox.y + (dOff.get(did) || 0) + span / 2, y2 = cBox.y + (cOffL.get(cid) || 0) + span / 2;
            dOff.set(did, (dOff.get(did) || 0) + span);
            cOffL.set(cid, (cOffL.get(cid) || 0) + span);
            combinedSvg.appendChild(createSvgEl("path", {
                d: `M ${col1X + nodeWidth} ${y1} C ${col1X + 60} ${y1}, ${col2X - 60} ${y2}, ${col2X} ${y2}`,
                fill: "none", stroke: "#2563eb", "stroke-width": span, "stroke-opacity": "0.25",
            }));
        }

        // Ribbons: committee -> candidate (red)
        const topCandidateIds = new Set(topCandidates.map((c) => c.id));
        for (const [key, weight] of Array.from(ccFlows.entries()).sort((a, b) => b[1] - a[1])) {
            const [cid, caid] = key.split("|");
            if (!topCommitteeIds.has(cid) || !topCandidateIds.has(caid)) continue;
            const cBox = committeeLayout.get(cid), caBox = candidateLayout.get(caid);
            if (!cBox || !caBox) continue;
            const span = Math.max(1, Math.min(cBox.h * (weight / (committeeTotals.get(cid) || 1)), caBox.h * (weight / (candidateTotals.get(caid) || 1))));
            const y1 = cBox.y + (cOffR.get(cid) || 0) + span / 2, y2 = caBox.y + (caOff.get(caid) || 0) + span / 2;
            cOffR.set(cid, (cOffR.get(cid) || 0) + span);
            caOff.set(caid, (caOff.get(caid) || 0) + span);
            combinedSvg.appendChild(createSvgEl("path", {
                d: `M ${col2X + nodeWidth} ${y1} C ${col2X + 60} ${y1}, ${col3X - 60} ${y2}, ${col3X} ${y2}`,
                fill: "none", stroke: "#dc2626", "stroke-width": span, "stroke-opacity": "0.25",
            }));
        }

        // Ribbons: committee -> vendor (amber)
        const topVendorIds = new Set(topVendors.map((v) => v.id));
        for (const [key, weight] of Array.from(cvFlows.entries()).sort((a, b) => b[1] - a[1])) {
            const [cid, vid] = key.split("|");
            if (!topCommitteeIds.has(cid) || !topVendorIds.has(vid)) continue;
            const cBox = committeeLayout.get(cid), vBox = vendorLayout.get(vid);
            if (!cBox || !vBox) continue;
            const span = Math.max(1, Math.min(cBox.h * 0.3, vBox.h * (weight / (vendorTotals.get(vid) || 1))));
            const y1 = cBox.y + cBox.h / 2, y2 = vBox.y + (vOff.get(vid) || 0) + span / 2;
            vOff.set(vid, (vOff.get(vid) || 0) + span);
            combinedSvg.appendChild(createSvgEl("path", {
                d: `M ${col2X + nodeWidth} ${y1} C ${col3X} ${y1}, ${col4X - 80} ${y2}, ${col4X} ${y2}`,
                fill: "none", stroke: "#d97706", "stroke-width": span, "stroke-opacity": "0.25",
            }));
        }

        // Node bars
        for (const d of topDonors) { const b = donorLayout.get(d.id); if (b) combinedSvg.appendChild(createSvgEl("rect", { x: col1X, y: b.y, width: nodeWidth, height: b.h, rx: 3, fill: "#2563eb" })); drawText(combinedSvg, col1X - 4, b.y + b.h / 2 + 3, shortLabel(d.label, 18), { anchor: "end", size: "8px" }); }
        for (const c of topCommittees) { const b = committeeLayout.get(c.id); if (b) combinedSvg.appendChild(createSvgEl("rect", { x: col2X, y: b.y, width: nodeWidth, height: b.h, rx: 3, fill: "#059669" })); }
        for (const c of topCandidates) { const b = candidateLayout.get(c.id); if (b) { combinedSvg.appendChild(createSvgEl("rect", { x: col3X, y: b.y, width: nodeWidth, height: b.h, rx: 3, fill: "#dc2626" })); drawText(combinedSvg, col3X + nodeWidth + 4, b.y + b.h / 2 + 3, shortLabel(c.label, 18), { anchor: "start", size: "8px" }); } }
        for (const v of topVendors) { const b = vendorLayout.get(v.id); if (b) { combinedSvg.appendChild(createSvgEl("rect", { x: col4X, y: b.y, width: nodeWidth, height: b.h, rx: 3, fill: "#d97706" })); drawText(combinedSvg, col4X + nodeWidth + 4, b.y + b.h / 2 + 3, shortLabel(v.label, 18), { anchor: "start", size: "8px" }); } }

        drawText(combinedSvg, col1X, 18, "Donors", { size: "11px", weight: "600" });
        drawText(combinedSvg, col2X, 18, "Committees", { size: "11px", weight: "600" });
        drawText(combinedSvg, col3X, 18, "Candidates", { size: "11px", weight: "600" });
        drawText(combinedSvg, col4X, 18, "Vendors", { size: "11px", weight: "600" });

        if (combinedSummary) combinedSummary.textContent = `Combined flow: ${topDonors.length} donors, ${topCommittees.length} committees, ${topCandidates.length} candidates, ${topVendors.length} vendors`;
    };

    if (combinedRenderBtn) combinedRenderBtn.addEventListener("click", renderCombined);
    if (combinedDonorLimit) combinedDonorLimit.addEventListener("change", renderCombined);
    if (combinedVendorLimit) combinedVendorLimit.addEventListener("change", renderCombined);

    /* ═════════════════════════════════════════════════════════════════
       6. STATE-FEDERAL DONOR OVERLAP
       ═════════════════════════════════════════════════════════════════ */
    const overlapSvg = document.getElementById("overlap-svg");
    const overlapSummaryEl = document.getElementById("overlap-summary");

    const renderOverlap = () => {
        if (!overlapSvg || !overlapData) return;
        const nodes = (overlapData.nodes || []).map((n) => ({ ...n, weighted_degree: 0, degree: 0 }));
        const edges = (overlapData.edges || []).map((e) => ({ ...e, weight: Number(e.weight || 0) }));
        for (const e of edges) {
            const s = nodes.find((n) => n.id === e.source);
            const t = nodes.find((n) => n.id === e.target);
            if (s) { s.weighted_degree += e.weight; s.degree++; }
            if (t) { t.weighted_degree += e.weight; t.degree++; }
        }
        const { width, height } = viewBoxSize(overlapSvg);
        const anchorFn = (n, axis, w, h) => {
            if (axis === "x") {
                if (n.node_type === "local_committee") return w * 0.15;
                if (n.node_type === "federal_committee") return w * 0.85;
                return w * 0.5;
            }
            return h * 0.5 + (hashCode(n.id) % 200 - 100);
        };
        const colorFn = (n) => typeColor[n.node_type] || "#64748b";
        renderForceGraph(overlapSvg, overlapSummaryEl, nodes, edges, colorFn, {
            emptyMsg: "No state-federal overlap data. Run cross-matching first.",
            anchorFn, anchorStrength: 0.015, charge: 3500, spring: 0.01,
            infoPanelId: "overlap-info-panel",
            densityMode: getDensityMode(),
        });
    };

    /* ═════════════════════════════════════════════════════════════════
       7. LOBBYING-CAMPAIGN FINANCE BRIDGE
       ═════════════════════════════════════════════════════════════════ */
    const lobbyingSvg = document.getElementById("lobbying-svg");
    const lobbyingSummaryEl = document.getElementById("lobbying-summary");

    const renderLobbying = () => {
        if (!lobbyingSvg || !lobbyingData) return;
        const nodes = (lobbyingData.nodes || []).map((n) => ({ ...n, weighted_degree: 0, degree: 0 }));
        const edges = (lobbyingData.edges || []).map((e) => ({ ...e, weight: Number(e.weight || 0) }));
        for (const e of edges) {
            const s = nodes.find((n) => n.id === e.source);
            const t = nodes.find((n) => n.id === e.target);
            if (s) { s.weighted_degree += e.weight; s.degree++; }
            if (t) { t.weighted_degree += e.weight; t.degree++; }
        }
        const colorFn = (n) => typeColor[n.node_type] || "#64748b";
        renderForceGraph(lobbyingSvg, lobbyingSummaryEl, nodes, edges, colorFn, {
            emptyMsg: "No lobbying data. Import lobbying data and run cross-matching first.",
            charge: 3000, spring: 0.012,
            infoPanelId: "lobbying-info-panel",
            densityMode: getDensityMode(),
        });
    };

    /* ═════════════════════════════════════════════════════════════════
       8. 527 DARK MONEY PATHWAY
       ═════════════════════════════════════════════════════════════════ */
    const darkMoneySvg = document.getElementById("dark-money-svg");
    const darkMoneySummaryEl = document.getElementById("darkmoney-summary");

    const renderDarkMoney = () => {
        if (!darkMoneySvg || !darkMoneyData) return;
        const nodes = (darkMoneyData.nodes || []).map((n) => ({ ...n, weighted_degree: 0, degree: 0 }));
        const edges = (darkMoneyData.edges || []).map((e) => ({ ...e, weight: Number(e.weight || 0) }));
        for (const e of edges) {
            const s = nodes.find((n) => n.id === e.source);
            const t = nodes.find((n) => n.id === e.target);
            if (s) { s.weighted_degree += e.weight; s.degree++; }
            if (t) { t.weighted_degree += e.weight; t.degree++; }
        }
        const colorFn = (n) => typeColor[n.node_type] || "#64748b";
        renderForceGraph(darkMoneySvg, darkMoneySummaryEl, nodes, edges, colorFn, {
            emptyMsg: "No 527 data. Import IRS 527 data and run cross-matching first.",
            charge: 3000, spring: 0.012,
            infoPanelId: "darkmoney-info-panel",
            densityMode: getDensityMode(),
        });
    };

    /* ── Tab-driven lazy rendering ── */
    const tabRadios = document.querySelectorAll('input[name="network-tabs"]');
    const rendered = new Set();
    const densityNote = document.getElementById("graph-density-note");

    const renderTab = (id) => {
        if (id === "tab-force") renderForce();
        else if (id === "tab-sankey") renderSankey();
        else if (id === "tab-heatmap") renderHeatmap();
        else if (id === "tab-vendor") renderVendor();
        else if (id === "tab-combined") renderCombined();
        else if (id === "tab-overlap") renderOverlap();
        else if (id === "tab-lobbying") renderLobbying();
        else if (id === "tab-darkmoney") renderDarkMoney();
    };

    const renderActiveTab = (force = false) => {
        for (const radio of tabRadios) {
            if (!radio.checked) continue;
            const id = radio.id;
            if (!force && rendered.has(id)) break;
            rendered.add(id);
            renderTab(id);
            break;
        }
    };

    for (const radio of tabRadios) radio.addEventListener("change", () => renderActiveTab(true));
    if (densitySelect) {
        densitySelect.addEventListener("change", () => {
            if (densityNote) {
                if (densitySelect.value === "full") densityNote.textContent = "Full detail mode shows all edges and may be dense on large graphs.";
                else if (densitySelect.value === "focus") densityNote.textContent = "Strongest links mode keeps only the highest-weight relationships for maximum readability.";
                else densityNote.textContent = "Balanced mode hides weaker links in dense views to reduce overlap. Full detail restores every edge.";
            }
            renderActiveTab(true);
        });
    }

    // Initial render of the force graph (default tab)
    renderActiveTab(true);
})();
