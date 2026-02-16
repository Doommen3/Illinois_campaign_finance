(() => {
    const fmtCurrency = new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
    });
    const fmtNumber = new Intl.NumberFormat(undefined, {maximumFractionDigits: 2});

    const prototypesNode = document.getElementById("viz-lab-prototypes");
    const stateNode = document.getElementById("viz-lab-state");
    if (!prototypesNode || !stateNode) return;

    let prototypes = [];
    let baseState = {};
    try {
        prototypes = JSON.parse(prototypesNode.textContent || "[]");
        baseState = JSON.parse(stateNode.textContent || "{}");
    } catch (_err) {
        return;
    }

    const chartRegistry = new Map();
    const pageState = new Map();
    prototypes.forEach((prototype) => pageState.set(prototype.key, 1));

    const activeDateFrom = (baseState.date_from || "").trim();
    const activeDateTo = (baseState.date_to || "").trim();
    const activePeriod = (baseState.period_key || "").trim();
    const networkAdvancedState = {enabled: false};

    const buildQueryForPrototype = (key, page) => {
        const params = new URLSearchParams();
        if (activePeriod) params.set("period", activePeriod);
        if (activeDateFrom) params.set("date_from", activeDateFrom);
        if (activeDateTo) params.set("date_to", activeDateTo);
        params.set("page", String(page));
        params.set("per_page", "10");

        if (key === "network_slice") {
            const controls = document.querySelector('[data-network-controls="network_slice"]');
            if (controls) {
                const edgeThreshold = controls.querySelector('input[name="edge_threshold"]');
                const edgeLimit = controls.querySelector('input[name="edge_limit"]');
                const nodeCap = controls.querySelector('input[name="node_cap"]');
                const nodeType = controls.querySelector('select[name="node_type"]');
                const mode = controls.querySelector('select[name="mode"]');
                const k = controls.querySelector('select[name="k"]');
                const weightMode = controls.querySelector('select[name="weight_mode"]');
                const computeCommunities = controls.querySelector('input[name="compute_communities"]');
                const search = controls.querySelector('input[name="search"]');
                if (edgeThreshold && edgeThreshold.value) {
                    params.set("edge_threshold", edgeThreshold.value);
                    params.set("min_edge_amount", edgeThreshold.value);
                }
                if (edgeLimit && edgeLimit.value) params.set("edge_limit", edgeLimit.value);
                if (nodeCap && nodeCap.value) params.set("node_cap", nodeCap.value);
                if (nodeType && nodeType.value) params.set("node_type", nodeType.value);
                if (mode && mode.value) params.set("mode", mode.value);
                if (k && k.value) params.set("k", k.value);
                if (weightMode && weightMode.value) params.set("weight_mode", weightMode.value);
                if (computeCommunities) params.set("compute_communities", computeCommunities.checked ? "1" : "0");
                if (search && search.value.trim()) params.set("search", search.value.trim());
                params.set("compute_advanced", networkAdvancedState.enabled ? "1" : "0");
            }
        }

        return params;
    };

    const destroyChart = (key) => {
        const chart = chartRegistry.get(key);
        if (chart) {
            chart.destroy();
            chartRegistry.delete(key);
        }
    };

    const createChart = (key, config) => {
        const canvas = document.getElementById(`viz-chart-${key}`);
        if (!canvas || !window.Chart) return;
        destroyChart(key);
        const chart = new window.Chart(canvas, config);
        chartRegistry.set(key, chart);
    };

    const runtimeNode = (key) => document.getElementById(`viz-runtime-${key}`);
    const notesNode = (key) => document.getElementById(`viz-notes-${key}`);
    const sanityNode = (key) => document.getElementById(`viz-sanity-${key}`);
    const pageNode = (key) => document.getElementById(`viz-page-${key}`);
    const networkMetaNode = () => document.getElementById("viz-network-meta");

    const setRuntime = (key, payload) => {
        const node = runtimeNode(key);
        if (!node) return;
        if (!payload) {
            node.textContent = "Failed to load data.";
            return;
        }
        const queryMs = fmtNumber.format(Number(payload.query_ms || 0));
        const cacheTag = payload.cache_hit ? "cache hit" : "fresh query";
        const speedTag = Number(payload.query_ms || 0) <= 2000 ? "within <2s target" : "over <2s target";
        node.textContent = `Query runtime: ${queryMs} ms (${cacheTag}; ${speedTag}).`;
        if (key === "network_slice") {
            const metaNode = networkMetaNode();
            if (metaNode) {
                const graphMeta = payload?.graph_meta || {};
                const mode = graphMeta.mode || "fast";
                const advanced = graphMeta.compute_advanced ? "advanced" : "degree-only";
                const k = graphMeta.k ? `k=${graphMeta.k}` : "k=n/a";
                const warnings = (graphMeta.warnings || []).join(" | ");
                metaNode.textContent = warnings
                    ? `Mode ${mode}, ${advanced}, ${k}. ${warnings}`
                    : `Mode ${mode}, ${advanced}, ${k}.`;
            }
        }
    };

    const setNotes = (key, payload) => {
        const node = notesNode(key);
        if (!node) return;
        const notes = payload?.notes || {};
        const lines = [];
        if (notes.sql_source) lines.push(`SQL/data source: ${notes.sql_source}`);
        (notes.known_limitations || []).forEach((line) => lines.push(`Known limitation: ${line}`));
        (notes.validate || []).forEach((line) => lines.push(`Validate: ${line}`));
        if (!lines.length) lines.push("No notes available for this prototype.");
        node.innerHTML = lines.map((line) => `<li>${line}</li>`).join("");
    };

    const summaryLine = (payload) => {
        const summary = payload?.summary || {};
        if (payload?.prototype_key === "race_money_pressure") {
            return `Total receipts: ${fmtCurrency.format(summary.total_receipts || 0)} | Outside spend: ${fmtCurrency.format(summary.total_outside_spending || 0)}`;
        }
        if (payload?.prototype_key === "race_concentration") {
            return `Average top-candidate share: ${fmtNumber.format(summary.avg_top_candidate_share_pct || 0)}%`;
        }
        if (payload?.prototype_key === "cumulative_inflow") {
            return `Race lines: ${summary.race_count || 0} | Month limit: ${summary.month_limit || 0}`;
        }
        if (payload?.prototype_key === "payee_dominance") {
            return `Race/payee rows: ${summary.payee_rows || 0} | Distinct races: ${summary.race_count || 0}`;
        }
        if (payload?.prototype_key === "entity_resolution") {
            return `At-risk total: ${fmtCurrency.format(summary.amount_at_risk_total || 0)} | Snapshot source: ${summary.source || "unknown"}`;
        }
        if (payload?.prototype_key === "network_slice") {
            const mode = payload?.graph_meta?.mode || summary.mode || "fast";
            const advanced = payload?.graph_meta?.compute_advanced ? "yes" : "no";
            return `Nodes: ${summary.node_count || 0} | Edges: ${summary.edge_count || 0} | Min edge amount: ${fmtCurrency.format(summary.min_edge_amount || 0)} | Mode: ${mode} | Advanced: ${advanced}`;
        }
        return "";
    };

    const setSanity = (key, payload) => {
        const node = sanityNode(key);
        if (!node) return;
        const items = [];
        if (payload?.supports_date_window) {
            items.push(`Date window support: yes (${payload?.window?.date_from || "earliest"} to ${payload?.window?.date_to || "latest"}).`);
        } else {
            items.push("Date window support: not yet (snapshot-level prototype).");
        }
        const summary = summaryLine(payload);
        if (summary) items.push(`Aggregate cross-check: ${summary}`);
        const queryMs = Number(payload?.query_ms || 0);
        items.push(`Render speed check: ${fmtNumber.format(queryMs)} ms ${queryMs <= 2000 ? "(pass target)" : "(review needed)"}.`);
        if (key === "network_slice") {
            const caps = payload?.graph_meta?.caps || {};
            items.push(
                `Caps: nodes ${caps.actual_nodes || 0}/${caps.max_nodes || 0}, edges ${caps.actual_edges || 0}/${caps.max_edges || 0}.`
            );
        }
        node.innerHTML = items.map((line) => `<li>${line}</li>`).join("");
    };

    const renderTable = (key, payload) => {
        const table = document.getElementById(`viz-table-${key}`);
        if (!table) return;
        const thead = table.querySelector("thead");
        const tbody = table.querySelector("tbody");
        if (!thead || !tbody) return;

        const rows = payload?.table_rows || [];
        const renderRows = () => {
            if (!rows.length) {
                tbody.innerHTML = '<tr><td colspan="8">No rows available.</td></tr>';
                return;
            }
            if (key === "race_money_pressure") {
                tbody.innerHTML = rows.map((row) => (
                    `<tr>
                        <td>${row.race_label}</td>
                        <td>${fmtCurrency.format(row.total_amount || 0)}</td>
                        <td>${fmtCurrency.format(row.outside_spending_total || 0)}</td>
                        <td>${fmtNumber.format(row.donor_count || 0)}</td>
                        <td>${fmtNumber.format(row.candidate_count || 0)}</td>
                        <td>${fmtNumber.format((row.outside_pressure_ratio || 0) * 100)}%</td>
                    </tr>`
                )).join("");
                return;
            }
            if (key === "race_concentration") {
                tbody.innerHTML = rows.map((row) => (
                    `<tr>
                        <td>${row.race_label}</td>
                        <td>${fmtNumber.format(row.donor_count || 0)}</td>
                        <td>${fmtNumber.format(row.top_candidate_share_pct || 0)}%</td>
                        <td>${fmtCurrency.format(row.total_amount || 0)}</td>
                        <td>${fmtNumber.format(row.outside_pressure_ratio_pct || 0)}%</td>
                    </tr>`
                )).join("");
                return;
            }
            if (key === "cumulative_inflow") {
                tbody.innerHTML = rows.map((row) => (
                    `<tr>
                        <td>${row.race_label}</td>
                        <td>${row.latest_month}</td>
                        <td>${fmtCurrency.format(row.latest_cumulative_amount || 0)}</td>
                        <td>${fmtCurrency.format(row.latest_month_amount || 0)}</td>
                        <td>${fmtNumber.format(row.month_points || 0)}</td>
                    </tr>`
                )).join("");
                return;
            }
            if (key === "payee_dominance") {
                tbody.innerHTML = rows.map((row) => (
                    `<tr>
                        <td>${row.race_label}</td>
                        <td>${row.payee_name}</td>
                        <td>${fmtCurrency.format(row.total_amount || 0)}</td>
                        <td>${fmtNumber.format(row.txn_count || 0)}</td>
                    </tr>`
                )).join("");
                return;
            }
            if (key === "entity_resolution") {
                tbody.innerHTML = rows.map((row) => (
                    `<tr>
                        <td>${row.canonical_name}</td>
                        <td>${fmtNumber.format(row.variant_count || 0)}</td>
                        <td>${fmtCurrency.format(row.amount_at_risk || 0)}</td>
                        <td>${fmtCurrency.format(row.top_variant_amount || 0)}</td>
                        <td>${(row.variant_examples || []).join(", ")}</td>
                    </tr>`
                )).join("");
                return;
            }
            if (key === "network_slice") {
                tbody.innerHTML = rows.map((row) => (
                    `<tr>
                        <td>${row.label}</td>
                        <td>${row.node_type}</td>
                        <td>${row.system || "-"}</td>
                        <td>${fmtNumber.format(row.degree || 0)}</td>
                        <td>${fmtCurrency.format(row.weighted_degree || 0)}</td>
                        <td>${row.betweenness_approx === null || row.betweenness_approx === undefined ? "-" : fmtNumber.format(row.betweenness_approx)}</td>
                        <td>${row.community_id === null || row.community_id === undefined ? "-" : row.community_id}</td>
                        <td>${row.bridge_ratio === null || row.bridge_ratio === undefined ? "-" : `${fmtNumber.format((row.bridge_ratio || 0) * 100)}%`}</td>
                    </tr>`
                )).join("");
            }
        };

        if (key === "race_money_pressure") {
            thead.innerHTML = "<tr><th>Race</th><th>Total Receipts</th><th>Outside Spending</th><th>Donors</th><th>Candidates</th><th>Outside Ratio</th></tr>";
        } else if (key === "race_concentration") {
            thead.innerHTML = "<tr><th>Race</th><th>Donors</th><th>Top Candidate Share</th><th>Total Receipts</th><th>Outside Pressure</th></tr>";
        } else if (key === "cumulative_inflow") {
            thead.innerHTML = "<tr><th>Race</th><th>Latest Month</th><th>Latest Cumulative</th><th>Latest Month Inflow</th><th>Months</th></tr>";
        } else if (key === "payee_dominance") {
            thead.innerHTML = "<tr><th>Race</th><th>Payee</th><th>Total Spend</th><th>Txn Count</th></tr>";
        } else if (key === "entity_resolution") {
            thead.innerHTML = "<tr><th>Canonical Name</th><th>Variant Count</th><th>Amount at Risk</th><th>Largest Variant</th><th>Top Variants</th></tr>";
        } else if (key === "network_slice") {
            thead.innerHTML = "<tr><th>Node</th><th>Type</th><th>System</th><th>Degree</th><th>Weighted Degree</th><th>Betweenness</th><th>Community</th><th>Bridge Ratio</th></tr>";
        }
        renderRows();
    };

    const renderNetworkCommunityTable = (payload) => {
        const table = document.getElementById("viz-community-table-network_slice");
        if (!table) return;
        const thead = table.querySelector("thead");
        const tbody = table.querySelector("tbody");
        if (!thead || !tbody) return;
        thead.innerHTML = "<tr><th>Community</th><th>Size</th><th>Top Nodes (Betweenness)</th></tr>";

        const rows = payload?.community_summary || [];
        if (!rows.length) {
            tbody.innerHTML = "<tr><td colspan=\"3\">No community results (compute advanced metrics with communities enabled).</td></tr>";
            return;
        }
        tbody.innerHTML = rows.map((row) => {
            const topNodes = (row.top_nodes || []).map((node) => {
                const score = node.betweenness_approx === null || node.betweenness_approx === undefined
                    ? "-"
                    : fmtNumber.format(node.betweenness_approx);
                return `${node.label} (${score})`;
            }).join(", ");
            return `<tr>
                <td>${row.community_id}</td>
                <td>${fmtNumber.format(row.size || 0)}</td>
                <td>${topNodes || "-"}</td>
            </tr>`;
        }).join("");
    };

    const renderNetworkEdgeTable = (payload) => {
        const table = document.getElementById("viz-edge-table-network_slice");
        if (!table) return;
        const thead = table.querySelector("thead");
        const tbody = table.querySelector("tbody");
        if (!thead || !tbody) return;
        thead.innerHTML = "<tr><th>Source</th><th>Target</th><th>Edge Type</th><th>Weight</th></tr>";
        const rows = payload?.edge_rows || [];
        if (!rows.length) {
            tbody.innerHTML = "<tr><td colspan=\"4\">No edge preview rows.</td></tr>";
            return;
        }
        tbody.innerHTML = rows.map((row) => (
            `<tr>
                <td>${row.source_label}</td>
                <td>${row.target_label}</td>
                <td>${row.edge_type}</td>
                <td>${fmtCurrency.format(row.weight || 0)}</td>
            </tr>`
        )).join("");
    };

    const toBubblePoint = (row, xField, yField, rField, labelField) => {
        const radiusRaw = Number(row[rField] || 0);
        const radius = Math.max(4, Math.min(20, Math.sqrt(Math.max(1, radiusRaw))));
        return {
            x: Number(row[xField] || 0),
            y: Number(row[yField] || 0),
            r: radius,
            label: row[labelField] || "",
        };
    };

    const renderRaceMoneyPressureChart = (key, payload) => {
        const rows = payload?.chart_rows || [];
        createChart(key, {
            type: "bubble",
            data: {
                datasets: [{
                    label: "Race",
                    data: rows.map((row) => toBubblePoint(row, "total_amount", "outside_spending_total", "donor_count", "race_label")),
                    backgroundColor: "rgba(29, 78, 216, 0.55)",
                    borderColor: "rgba(29, 78, 216, 1)",
                    borderWidth: 1,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {display: false},
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const point = context.raw || {};
                                return `${point.label}: receipts ${fmtCurrency.format(point.x || 0)}, outside ${fmtCurrency.format(point.y || 0)}`;
                            },
                        },
                    },
                },
                scales: {
                    x: {title: {display: true, text: "Total Receipts"}},
                    y: {title: {display: true, text: "Outside Spending"}},
                },
            },
        });
    };

    const renderRaceConcentrationChart = (key, payload) => {
        const rows = payload?.chart_rows || [];
        createChart(key, {
            type: "bubble",
            data: {
                datasets: [{
                    label: "Race",
                    data: rows.map((row) => toBubblePoint(row, "donor_count", "top_candidate_share_pct", "total_amount", "race_label")),
                    backgroundColor: "rgba(15, 118, 110, 0.55)",
                    borderColor: "rgba(15, 118, 110, 1)",
                    borderWidth: 1,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {display: false},
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const point = context.raw || {};
                                return `${point.label}: donors ${fmtNumber.format(point.x || 0)}, top-candidate share ${fmtNumber.format(point.y || 0)}%`;
                            },
                        },
                    },
                },
                scales: {
                    x: {title: {display: true, text: "Donor Count"}},
                    y: {title: {display: true, text: "Top Candidate Share (%)"}},
                },
            },
        });
    };

    const renderCumulativeInflowChart = (key, payload) => {
        const series = payload?.chart_rows || [];
        const allMonths = new Set();
        series.forEach((row) => (row.points || []).forEach((point) => allMonths.add(point.month)));
        const labels = Array.from(allMonths).sort();
        const colorPool = ["#1d4ed8", "#0f766e", "#dc2626", "#7c3aed", "#ea580c", "#0369a1", "#9333ea", "#15803d"];
        const datasets = series.slice(0, 10).map((row, idx) => {
            const map = new Map((row.points || []).map((point) => [point.month, point.cumulative_amount]));
            return {
                label: row.race_label,
                data: labels.map((label) => map.get(label) || null),
                borderColor: colorPool[idx % colorPool.length],
                backgroundColor: colorPool[idx % colorPool.length],
                tension: 0.25,
                spanGaps: true,
            };
        });
        createChart(key, {
            type: "line",
            data: {labels, datasets},
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {mode: "nearest", intersect: false},
                plugins: {legend: {position: "bottom"}},
                scales: {
                    x: {title: {display: true, text: "Month"}},
                    y: {title: {display: true, text: "Cumulative Receipts"}},
                },
            },
        });
    };

    const renderPayeeDominanceChart = (key, payload) => {
        const rows = payload?.chart_rows || [];
        createChart(key, {
            type: "bar",
            data: {
                labels: rows.map((row) => row.payee_name),
                datasets: [{
                    label: "Total Spend",
                    data: rows.map((row) => Number(row.total_amount || 0)),
                    backgroundColor: "rgba(2, 132, 199, 0.65)",
                    borderColor: "rgba(2, 132, 199, 1)",
                    borderWidth: 1,
                }],
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: {legend: {display: false}},
                scales: {
                    x: {title: {display: true, text: "Total Spend"}},
                    y: {ticks: {autoSkip: false}},
                },
            },
        });
    };

    const renderEntityResolutionChart = (key, payload) => {
        const rows = payload?.chart_rows || [];
        createChart(key, {
            type: "bar",
            data: {
                labels: rows.map((row) => row.canonical_name),
                datasets: [{
                    label: "Amount at Risk",
                    data: rows.map((row) => Number(row.amount_at_risk || 0)),
                    backgroundColor: "rgba(180, 83, 9, 0.65)",
                    borderColor: "rgba(180, 83, 9, 1)",
                    borderWidth: 1,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {legend: {display: false}},
                scales: {
                    x: {ticks: {autoSkip: false, maxRotation: 70, minRotation: 35}},
                    y: {title: {display: true, text: "Amount at Risk"}},
                },
            },
        });
    };

    const renderNetworkSliceChart = (key, payload) => {
        const rows = payload?.chart_rows || [];
        const useBetweenness = Boolean(payload?.graph_meta?.compute_advanced);
        const metricLabel = useBetweenness ? "Betweenness (Approx)" : "Weighted Degree";
        createChart(key, {
            type: "bar",
            data: {
                labels: rows.map((row) => row.label),
                datasets: [{
                    label: metricLabel,
                    data: rows.map((row) => Number(useBetweenness ? (row.betweenness_approx || 0) : (row.weighted_degree || 0))),
                    backgroundColor: "rgba(20, 83, 45, 0.65)",
                    borderColor: "rgba(20, 83, 45, 1)",
                    borderWidth: 1,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {legend: {display: false}},
                scales: {
                    x: {ticks: {autoSkip: false, maxRotation: 75, minRotation: 30}},
                    y: {title: {display: true, text: metricLabel}},
                },
            },
        });
    };

    const renderChart = (key, payload) => {
        if (!payload?.available) {
            destroyChart(key);
            const canvas = document.getElementById(`viz-chart-${key}`);
            if (!canvas) return;
            const ctx = canvas.getContext("2d");
            if (!ctx) return;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.font = "14px sans-serif";
            ctx.fillStyle = "#64748b";
            ctx.fillText(payload?.reason || "No data available.", 12, 26);
            return;
        }
        if (key === "race_money_pressure") renderRaceMoneyPressureChart(key, payload);
        if (key === "race_concentration") renderRaceConcentrationChart(key, payload);
        if (key === "cumulative_inflow") renderCumulativeInflowChart(key, payload);
        if (key === "payee_dominance") renderPayeeDominanceChart(key, payload);
        if (key === "entity_resolution") renderEntityResolutionChart(key, payload);
        if (key === "network_slice") renderNetworkSliceChart(key, payload);
    };

    const updatePager = (key, payload) => {
        const pageInfo = payload?.pagination || {};
        const page = pageNode(key);
        if (page) {
            page.textContent = `Page ${pageInfo.page || 1} of ${pageInfo.total_pages || 1} (${pageInfo.total_rows || 0} rows)`;
        }
        const pager = document.querySelector(`[data-prototype-pager="${key}"]`);
        if (!pager) return;
        const prev = pager.querySelector('[data-pager-action="prev"]');
        const next = pager.querySelector('[data-pager-action="next"]');
        if (prev) prev.disabled = !pageInfo.has_prev;
        if (next) next.disabled = !pageInfo.has_next;
    };

    const loadPrototype = async (key) => {
        const prototype = prototypes.find((item) => item.key === key);
        if (!prototype) return;
        const card = document.querySelector(`[data-prototype-card="${key}"]`);
        if (!card || card.classList.contains("viz-lab-card-hidden")) return;

        const page = pageState.get(key) || 1;
        const query = buildQueryForPrototype(key, page);
        const endpoint = `${prototype.endpoint}?${query.toString()}`;

        const runtime = runtimeNode(key);
        if (runtime) {
            if (key === "network_slice" && networkAdvancedState.enabled) {
                runtime.textContent = "Computing advanced metrics...";
            } else {
                runtime.textContent = "Loading...";
            }
        }

        let payload = null;
        try {
            const response = await fetch(endpoint);
            if (!response.ok) {
                throw new Error(`Request failed (${response.status})`);
            }
            payload = await response.json();
        } catch (_err) {
            if (key === "network_slice" && runtime) {
                runtime.textContent = "Request failed. Try fast mode, lower node cap/edge limit, or reduce k.";
            } else {
                setRuntime(key, null);
            }
            return;
        }

        renderChart(key, payload);
        renderTable(key, payload);
        if (key === "network_slice") {
            renderNetworkCommunityTable(payload);
            renderNetworkEdgeTable(payload);
        }
        setNotes(key, payload);
        setSanity(key, payload);
        setRuntime(key, payload);
        updatePager(key, payload);
    };

    const loadVisiblePrototypes = async () => {
        for (const prototype of prototypes) {
            const card = document.querySelector(`[data-prototype-card="${prototype.key}"]`);
            if (!card || card.classList.contains("viz-lab-card-hidden")) continue;
            // Keep sequence deterministic for readable runtime notes.
            // eslint-disable-next-line no-await-in-loop
            await loadPrototype(prototype.key);
        }
    };

    const setupToggles = () => {
        const toggles = document.querySelectorAll(".viz-lab-visibility-toggle");
        toggles.forEach((toggle) => {
            toggle.addEventListener("change", () => {
                const target = toggle.getAttribute("data-target");
                const card = document.querySelector(`[data-prototype-card="${target}"]`);
                if (!card) return;
                if (toggle.checked) {
                    card.classList.remove("viz-lab-card-hidden");
                    loadPrototype(target);
                } else {
                    card.classList.add("viz-lab-card-hidden");
                    destroyChart(target);
                }
            });
        });
    };

    const setupPager = () => {
        const buttons = document.querySelectorAll("[data-pager-action]");
        buttons.forEach((button) => {
            button.addEventListener("click", () => {
                const key = button.getAttribute("data-prototype");
                if (!key) return;
                const currentPage = pageState.get(key) || 1;
                const action = button.getAttribute("data-pager-action");
                const nextPage = action === "next" ? currentPage + 1 : Math.max(1, currentPage - 1);
                pageState.set(key, nextPage);
                loadPrototype(key);
            });
        });
    };

    const setupNetworkApply = () => {
        const button = document.querySelector('[data-network-apply="network_slice"]');
        if (!button) return;
        button.addEventListener("click", () => {
            networkAdvancedState.enabled = false;
            pageState.set("network_slice", 1);
            loadPrototype("network_slice");
        });
    };

    const setupNetworkAdvanced = () => {
        const button = document.querySelector('[data-network-advanced="network_slice"]');
        if (!button) return;
        button.addEventListener("click", () => {
            networkAdvancedState.enabled = true;
            pageState.set("network_slice", 1);
            loadPrototype("network_slice");
        });
    };

    const setupMapShell = () => {
        const detail = document.getElementById("viz-map-detail");
        const buttons = document.querySelectorAll(".viz-map-mock-district");
        if (!detail || !buttons.length) return;
        const mock = {
            "IL-H-01": {total: 2850000, donors: 4200, candidates: 4},
            "IL-H-07": {total: 3120000, donors: 5100, candidates: 5},
            "IL-H-13": {total: 2410000, donors: 3900, candidates: 3},
            "IL-S-26": {total: 1780000, donors: 2450, candidates: 2},
            "IL-R-042": {total: 960000, donors: 1320, candidates: 2},
        };
        buttons.forEach((button) => {
            button.addEventListener("click", () => {
                const district = button.getAttribute("data-district") || "Unknown";
                const row = mock[district] || {total: 0, donors: 0, candidates: 0};
                detail.innerHTML = `
                    <p class="help-text"><strong>Selected district:</strong> ${district}</p>
                    <p class="help-text">Mock total amount: <strong>${fmtCurrency.format(row.total)}</strong></p>
                    <p class="help-text">Mock donor count: <strong>${fmtNumber.format(row.donors)}</strong></p>
                    <p class="help-text">Mock candidate count: <strong>${fmtNumber.format(row.candidates)}</strong></p>
                `;
            });
        });
    };

    setupToggles();
    setupPager();
    setupNetworkApply();
    setupNetworkAdvanced();
    setupMapShell();
    loadVisiblePrototypes();
})();
