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
    const summaryEl = document.getElementById("triple-graph-summary");
    const alertEl = document.getElementById("triple-pipeline-alert");
    const nodeDetailEl = document.getElementById("triple-node-detail");
    const edgeDetailEl = document.getElementById("triple-edge-detail");
    const resetBtn = document.getElementById("triple-pipeline-reset");

    const state = {
        raw: null,
        active: null,
        simulation: null,
        selectedNode: null,
        selectedEdge: null,
        mode: "global",
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

    const channelLabel = (node) => {
        const channels = [];
        if (node.channel_lobby) channels.push("Lobby");
        if (node.channel_527) channels.push("527");
        if (node.channel_campaign) channels.push("Campaign");
        return channels.length ? channels.join(" / ") : "Unknown";
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

    const applyFilters = () => {
        if (!state.active) return;
        const selectedTypes = new Set(Array.from(edgeTypeSelect.selectedOptions).map((o) => o.value));
        const selectedChannels = new Set(
            channelCheckboxes.filter((c) => c.checked).map((c) => c.value)
        );
        const topN = Number(topNSlider.value || 0);
        const minWeight = Number(minWeightSlider.value || 0);
        const searchTerm = (searchInput.value || "").trim().toLowerCase();

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
        if (searchTerm) {
            nodes.forEach((node) => {
                if ((node.canonical_name || "").toLowerCase().includes(searchTerm)) {
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
            nodeDetailEl.innerHTML = `<strong>${d.canonical_name || d.id}</strong><br>Type: ${d.entity_type || "-"}<br>Channels: ${channelLabel(d)}<br>Evidence counts: Lobby ${d.evidence_count_lobby || 0}, 527 ${d.evidence_count_527 || 0}, Campaign ${d.evidence_count_campaign || 0}`;
            loadEgoGraph(d.entity_id || d.id);
            loadTimeline(d.entity_id || d.id);
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
        if (!entityId) return;
        try {
            const data = await fetchJson(`/experimental/viz-lab/data/triple-pipeline/ego?entity_id=${encodeURIComponent(entityId)}`);
            state.active = data;
            state.mode = "ego";
            applyFilters();
        } catch (err) {
            console.warn(err);
        }
    };

    const loadTimeline = async (entityId) => {
        if (!entityId) return;
        try {
            const payload = await fetchJson(`/experimental/viz-lab/data/triple-pipeline/timeline?entity_id=${encodeURIComponent(entityId)}`);
            renderTimeline(payload.series || []);
        } catch (err) {
            console.warn(err);
        }
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
        resetBtn.addEventListener("click", resetView);
    };

    const init = async () => {
        try {
            const graph = await fetchJson("/experimental/viz-lab/data/triple-pipeline/graph");
            state.raw = graph;
            state.active = graph;
            initControls(graph);
            attachEvents();
            updateAlert();
            applyFilters();
        } catch (err) {
            alertEl.textContent = "Unable to load triple pipeline artifacts. Run the build script to generate graph.json.";
            alertEl.style.display = "block";
            console.error(err);
        }
    };

    init();
})();
