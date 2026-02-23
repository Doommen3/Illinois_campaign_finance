(() => {
    const graphEl = document.getElementById("triple-pipeline-graph");
    const timelineEl = document.getElementById("triple-pipeline-timeline");
    const edgeTypeSelect = document.getElementById("triple-edge-types");
    const channelCheckboxes = Array.from(document.querySelectorAll(".viz-checkboxes input[type='checkbox']"));
    const topNSlider = document.getElementById("triple-top-n");
    const topNValue = document.getElementById("triple-top-n-value");
    const minWeightSlider = document.getElementById("triple-min-weight");
    const minWeightValue = document.getElementById("triple-min-weight-value");
    const searchInput = document.getElementById("triple-search");
    const searchOptions = document.getElementById("triple-search-options");
    const summaryEl = document.getElementById("triple-graph-summary");
    const alertEl = document.getElementById("triple-pipeline-alert");
    const nodeDetailEl = document.getElementById("triple-node-detail");
    const edgeDetailEl = document.getElementById("triple-edge-detail");
    const resetBtn = document.getElementById("triple-pipeline-reset");

    if (!graphEl || !timelineEl || !edgeTypeSelect || !searchInput) {
        return;
    }

    const state = {
        raw: null,
        active: null,
        simulation: null,
        selectedNode: null,
        selectedEdge: null,
        mode: "global",
        entitySuggestionByValue: new Map(),
        entityIdsByCanonical: new Map(),
        searchValueByEntityId: new Map(),
        rawNodeByEntityId: new Map(),
    };

    const tooltip = document.createElement("div");
    tooltip.className = "viz-tooltip";
    document.body.appendChild(tooltip);

    const fetchJson = async (url) => {
        const resp = await fetch(url);
        if (!resp.ok) {
            throw new Error(`Failed to load ${url}`);
        }
        return await resp.json();
    };

    const formatNumber = (value) => {
        const num = Number(value || 0);
        if (Number.isNaN(num)) return "0";
        return num.toLocaleString();
    };

    const normalizeLookupKey = (value) => String(value || "").trim().toLowerCase();

    const stripEntitySuffix = (value) => String(value || "").replace(/\s*\[[^\]]+\]\s*$/, "").trim();

    const channelLabel = (node) => {
        const channels = [];
        if (node.channel_lobby) channels.push("Lobby");
        if (node.channel_527) channels.push("527");
        if (node.channel_campaign) channels.push("Campaign");
        return channels.length ? channels.join(" / ") : "Unknown";
    };

    const renderNodeDetail = (node) => {
        if (!node) {
            nodeDetailEl.textContent = "Click a node to load its ego network and timeline.";
            return;
        }
        nodeDetailEl.innerHTML = `<strong>${node.canonical_name || node.id}</strong><br>Type: ${node.entity_type || "-"}<br>Channels: ${channelLabel(node)}<br>Evidence counts: Lobby ${node.evidence_count_lobby || 0}, 527 ${node.evidence_count_527 || 0}, Campaign ${node.evidence_count_campaign || 0}`;
    };

    const updateAlert = () => {
        if (!state.raw) return;
        const filters = state.raw.metadata?.filters || {};
        if (filters.downsampled) {
            const reason = filters.downsample_reason === "edge_threshold" ? "Edge threshold" : "Filtered";
            alertEl.textContent = `${reason}: showing ${filters.filtered_node_count} nodes and ${filters.filtered_edge_count} edges from ${filters.raw_edge_count} total.`;
            alertEl.style.display = "block";
        } else {
            alertEl.textContent = "";
            alertEl.style.display = "none";
        }
    };

    const initControls = (graph) => {
        const edgeTypes = Array.from(new Set(graph.edges.map((e) => e.edge_type))).sort();
        edgeTypeSelect.innerHTML = "";
        edgeTypes.forEach((edgeType) => {
            const option = document.createElement("option");
            option.value = edgeType;
            option.textContent = edgeType;
            option.selected = true;
            edgeTypeSelect.appendChild(option);
        });

        const maxNodes = graph.metadata?.filters?.max_nodes || graph.nodes.length || 400;
        topNSlider.max = String(maxNodes);
        topNSlider.value = String(Math.min(250, maxNodes));
        topNValue.textContent = topNSlider.value;

        const maxWeight = graph.edges.reduce((acc, edge) => Math.max(acc, edge.weight || 0), 0);
        const sliderMax = Math.max(1, Math.ceil(maxWeight));
        minWeightSlider.max = String(sliderMax);
        minWeightSlider.step = String(Math.max(1, Math.floor(sliderMax / 100)));
        minWeightSlider.value = "0";
        minWeightValue.textContent = "0";
    };

    const nodeMatchesChannel = (node, selectedChannels) => {
        if (!selectedChannels.size) return true;
        if (selectedChannels.has("lobby") && node.channel_lobby) return true;
        if (selectedChannels.has("527") && node.channel_527) return true;
        if (selectedChannels.has("campaign") && node.channel_campaign) return true;
        return false;
    };

    const resolveEntityIdFromInput = (rawInput) => {
        const text = String(rawInput || "").trim();
        if (!text) return "";
        const normalized = normalizeLookupKey(text);

        if (state.entitySuggestionByValue.has(normalized)) {
            return state.entitySuggestionByValue.get(normalized) || "";
        }
        const bracketMatch = text.match(/\[([^\]]+)\]\s*$/);
        if (bracketMatch && bracketMatch[1]) {
            return bracketMatch[1].trim();
        }
        if (state.rawNodeByEntityId.has(text)) {
            return text;
        }

        const canonical = normalizeLookupKey(stripEntitySuffix(text));
        const candidateIds = state.entityIdsByCanonical.get(canonical) || [];
        if (candidateIds.length === 1) {
            return candidateIds[0];
        }
        return "";
    };

    const setSearchValueForEntity = (entityId) => {
        if (!entityId) return;
        const suggestionValue = state.searchValueByEntityId.get(entityId);
        if (suggestionValue) {
            searchInput.value = suggestionValue;
            return;
        }
        const node = state.rawNodeByEntityId.get(entityId);
        if (node?.canonical_name) {
            searchInput.value = node.canonical_name;
            return;
        }
        searchInput.value = entityId;
    };

    const applyFilters = () => {
        if (!state.active) return;
        const selectedTypes = new Set(Array.from(edgeTypeSelect.selectedOptions).map((o) => o.value));
        const selectedChannels = new Set(
            channelCheckboxes.filter((c) => c.checked).map((c) => c.value)
        );
        const topN = Number(topNSlider.value || 0);
        const minWeight = Number(minWeightSlider.value || 0);
        const searchRaw = (searchInput.value || "").trim();
        const searchTerm = normalizeLookupKey(stripEntitySuffix(searchRaw));
        const exactEntityId = normalizeLookupKey(resolveEntityIdFromInput(searchRaw));

        const nodes = state.active.nodes
            .filter((node) => nodeMatchesChannel(node, selectedChannels))
            .sort((a, b) => (b.node_size_score || 0) - (a.node_size_score || 0))
            .slice(0, topN || state.active.nodes.length);

        const nodeIds = new Set(nodes.map((n) => n.id));
        const edges = state.active.edges.filter((edge) => {
            if (selectedTypes.size && !selectedTypes.has(edge.edge_type)) return false;
            if (edge.weight < minWeight) return false;
            if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return false;
            return true;
        });

        const highlightIds = new Set();
        if (searchTerm || exactEntityId) {
            nodes.forEach((node) => {
                const canonicalName = normalizeLookupKey(node.canonical_name || "");
                const entityId = normalizeLookupKey(node.entity_id || node.id || "");
                const matchesText = searchTerm && (canonicalName.includes(searchTerm) || entityId.includes(searchTerm));
                const matchesEntity = exactEntityId && entityId === exactEntityId;
                if (matchesText || matchesEntity) {
                    highlightIds.add(node.id);
                }
            });
        }

        summaryEl.textContent = `${nodes.length} nodes, ${edges.length} edges (${state.mode})`;
        renderGraph(nodes, edges, highlightIds);
    };

    const renderGraph = (nodes, edges, highlightIds) => {
        d3.select(graphEl).selectAll("*").remove();
        const width = graphEl.clientWidth || 720;
        const height = 560;
        graphEl.setAttribute("width", width);
        graphEl.setAttribute("height", height);

        const svg = d3.select(graphEl);
        const link = svg
            .append("g")
            .attr("stroke", "#94a3b8")
            .attr("stroke-opacity", 0.4)
            .selectAll("line")
            .data(edges)
            .join("line")
            .attr("stroke-width", (d) => Math.max(0.5, Math.log1p(d.weight || 0)));

        const node = svg
            .append("g")
            .attr("stroke", "#0f172a")
            .attr("stroke-width", 0.6)
            .selectAll("circle")
            .data(nodes)
            .join("circle")
            .attr("r", (d) => 4 + Math.min(8, d.node_size_score || 0))
            .attr("fill", (d) => {
                if (d.node_channel_mask === 7) return "#0f766e";
                if (d.channel_campaign) return "#2563eb";
                if (d.channel_527) return "#7c3aed";
                if (d.channel_lobby) return "#b45309";
                return "#64748b";
            })
            .attr("opacity", (d) => (highlightIds.size ? (highlightIds.has(d.id) ? 1 : 0.3) : 0.9))
            .call(
                d3
                    .drag()
                    .on("start", (event, d) => {
                        if (!event.active) state.simulation.alphaTarget(0.3).restart();
                        d.fx = d.x;
                        d.fy = d.y;
                    })
                    .on("drag", (event, d) => {
                        d.fx = event.x;
                        d.fy = event.y;
                    })
                    .on("end", (event, d) => {
                        if (!event.active) state.simulation.alphaTarget(0);
                        d.fx = null;
                        d.fy = null;
                    })
            );

        node.on("mouseover", (event, d) => {
            tooltip.innerHTML = `<strong>${d.canonical_name || d.id}</strong><br>${d.entity_type || ""}<br>${channelLabel(d)}<br>Evidence: Lobby ${d.evidence_count_lobby || 0}, 527 ${d.evidence_count_527 || 0}, Campaign ${d.evidence_count_campaign || 0}`;
            tooltip.style.opacity = 1;
            tooltip.style.left = `${event.pageX + 12}px`;
            tooltip.style.top = `${event.pageY + 12}px`;
        });

        node.on("mouseout", () => {
            tooltip.style.opacity = 0;
        });

        node.on("click", (_, d) => {
            state.selectedNode = d;
            const entityId = d.entity_id || d.id;
            setSearchValueForEntity(entityId);
            renderNodeDetail(d);
            loadEgoGraph(entityId);
            loadTimeline(entityId);
        });

        link.on("mouseover", (event, d) => {
            tooltip.innerHTML = `<strong>${d.edge_type}</strong><br>Weight: ${formatNumber(d.weight)} ${d.weight_unit || ""}<br>Evidence: ${d.evidence_table || "-"} / ${d.evidence_ref || "-"}`;
            tooltip.style.opacity = 1;
            tooltip.style.left = `${event.pageX + 12}px`;
            tooltip.style.top = `${event.pageY + 12}px`;

            edgeDetailEl.innerHTML = `<strong>${d.edge_type}</strong><br>Weight: ${formatNumber(d.weight)} ${d.weight_unit || ""}<br>Evidence: ${d.evidence_table || "-"} / ${d.evidence_ref || "-"}`;
        });

        link.on("mouseout", () => {
            tooltip.style.opacity = 0;
        });

        state.simulation = d3
            .forceSimulation(nodes)
            .force("link", d3.forceLink(edges).id((d) => d.id).distance(90).strength(0.2))
            .force("charge", d3.forceManyBody().strength(-120))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide().radius((d) => 6 + Math.min(8, d.node_size_score || 0)));

        state.simulation.on("tick", () => {
            link
                .attr("x1", (d) => d.source.x)
                .attr("y1", (d) => d.source.y)
                .attr("x2", (d) => d.target.x)
                .attr("y2", (d) => d.target.y);
            node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
        });
    };

    const loadEgoGraph = async (entityId) => {
        if (!entityId) return false;
        try {
            const data = await fetchJson(`/experimental/viz-lab/data/triple-pipeline/ego?entity_id=${encodeURIComponent(entityId)}`);
            state.active = data;
            state.mode = "ego";
            applyFilters();
            return true;
        } catch (err) {
            console.warn(err);
            return false;
        }
    };

    const loadTimeline = async (entityId) => {
        if (!entityId) return false;
        try {
            const payload = await fetchJson(`/experimental/viz-lab/data/triple-pipeline/timeline?entity_id=${encodeURIComponent(entityId)}`);
            renderTimeline(payload.series || []);
            return true;
        } catch (err) {
            console.warn(err);
            return false;
        }
    };

    const focusEntity = async (entityId, {syncSearch = true} = {}) => {
        if (!entityId) return;
        if (syncSearch) {
            setSearchValueForEntity(entityId);
        }
        const node = state.rawNodeByEntityId.get(entityId);
        if (node) {
            renderNodeDetail(node);
        }
        const egoLoaded = await loadEgoGraph(entityId);
        await loadTimeline(entityId);
        if (!egoLoaded) {
            state.active = state.raw;
            state.mode = "global";
            applyFilters();
        }
    };

    const buildFallbackSuggestions = (graph) => {
        const entries = (graph?.nodes || [])
            .map((node) => ({
                entity_id: String(node.entity_id || node.id || "").trim(),
                canonical_name: String(node.canonical_name || node.entity_id || node.id || "").trim(),
                node_size_score: Number(node.node_size_score || 0),
                rank: null,
            }))
            .filter((entry) => entry.entity_id)
            .sort((a, b) => (b.node_size_score || 0) - (a.node_size_score || 0));
        const top = entries[0];
        return {
            entities: entries,
            default_entity_id: top?.entity_id || "",
        };
    };

    const initEntitySuggestions = (graph, suggestionPayload) => {
        const payload = suggestionPayload && Array.isArray(suggestionPayload.entities) && suggestionPayload.entities.length
            ? suggestionPayload
            : buildFallbackSuggestions(graph);
        const entities = payload.entities || [];

        state.entitySuggestionByValue.clear();
        state.entityIdsByCanonical.clear();
        state.searchValueByEntityId.clear();

        if (searchOptions) {
            searchOptions.innerHTML = "";
        }

        entities.forEach((entry) => {
            const entityId = String(entry.entity_id || "").trim();
            const canonicalName = String(entry.canonical_name || entityId).trim();
            if (!entityId) return;

            const canonicalKey = normalizeLookupKey(canonicalName);
            const displayValue = `${canonicalName} [${entityId}]`;

            if (searchOptions) {
                const option = document.createElement("option");
                option.value = displayValue;
                option.label = entityId;
                option.textContent = entityId;
                searchOptions.appendChild(option);
            }

            state.entitySuggestionByValue.set(normalizeLookupKey(displayValue), entityId);
            if (!state.entityIdsByCanonical.has(canonicalKey)) {
                state.entityIdsByCanonical.set(canonicalKey, []);
            }
            state.entityIdsByCanonical.get(canonicalKey).push(entityId);
            state.searchValueByEntityId.set(entityId, displayValue);
        });

        const defaultEntityId = String(payload.default_entity_id || "").trim();
        if (defaultEntityId) {
            setSearchValueForEntity(defaultEntityId);
        }
        return defaultEntityId;
    };

    const commitSearchSelection = async () => {
        const entityId = resolveEntityIdFromInput(searchInput.value);
        if (!entityId) {
            applyFilters();
            return;
        }
        await focusEntity(entityId);
    };

    const renderTimeline = (series) => {
        d3.select(timelineEl).selectAll("*").remove();
        const width = timelineEl.clientWidth || 320;
        const height = 160;
        timelineEl.setAttribute("width", width);
        timelineEl.setAttribute("height", height);

        if (!series.length) {
            return;
        }

        const svg = d3.select(timelineEl);
        const months = series.map((d) => d.month_key);
        const values = series.flatMap((d) => [
            Math.log1p(d.lobbying_reports || 0),
            Math.log1p(d.campaign_receipts || 0),
            Math.log1p(d.irs527_contributions || 0),
        ]);
        const x = d3.scalePoint().domain(months).range([32, width - 12]);
        const y = d3.scaleLinear().domain([0, d3.max(values) || 1]).range([height - 24, 12]);

        const line = (accessor, color) => {
            const path = d3.line()
                .x((d) => x(d.month_key))
                .y((d) => y(Math.log1p(accessor(d) || 0)));
            svg.append("path")
                .datum(series)
                .attr("fill", "none")
                .attr("stroke", color)
                .attr("stroke-width", 1.6)
                .attr("d", path);
        };

        line((d) => d.lobbying_reports, "#b45309");
        line((d) => d.campaign_receipts, "#2563eb");
        line((d) => d.irs527_contributions, "#7c3aed");
    };

    const resetView = () => {
        if (!state.raw) return;
        state.active = state.raw;
        state.mode = "global";
        applyFilters();
    };

    const attachEvents = () => {
        edgeTypeSelect.addEventListener("change", applyFilters);
        channelCheckboxes.forEach((checkbox) => checkbox.addEventListener("change", applyFilters));
        topNSlider.addEventListener("input", () => {
            topNValue.textContent = topNSlider.value;
            applyFilters();
        });
        minWeightSlider.addEventListener("input", () => {
            minWeightValue.textContent = minWeightSlider.value;
            applyFilters();
        });
        searchInput.addEventListener("input", applyFilters);
        searchInput.addEventListener("change", () => {
            void commitSearchSelection();
        });
        searchInput.addEventListener("keydown", (event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            void commitSearchSelection();
        });
        resetBtn.addEventListener("click", resetView);
    };

    const init = async () => {
        try {
            const [graph, suggestionPayload] = await Promise.all([
                fetchJson("/experimental/viz-lab/data/triple-pipeline/graph"),
                fetchJson("/experimental/viz-lab/data/triple-pipeline/entities").catch(() => null),
            ]);
            state.raw = graph;
            state.active = graph;
            state.rawNodeByEntityId.clear();
            graph.nodes.forEach((node) => {
                const entityId = String(node.entity_id || node.id || "").trim();
                if (entityId) {
                    state.rawNodeByEntityId.set(entityId, node);
                }
            });
            initControls(graph);
            attachEvents();
            updateAlert();
            const defaultEntityId = initEntitySuggestions(graph, suggestionPayload);
            applyFilters();
            if (defaultEntityId) {
                await focusEntity(defaultEntityId, {syncSearch: false});
            }
        } catch (err) {
            alertEl.textContent = "Unable to load triple pipeline artifacts. Run the build script to generate graph.json.";
            alertEl.style.display = "block";
            console.error(err);
        }
    };

    init();
})();
