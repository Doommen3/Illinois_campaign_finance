(() => {
    const SVG_NS = "http://www.w3.org/2000/svg";

    const parseJson = (id) => {
        const node = document.getElementById(id);
        if (!node) return null;
        try {
            return JSON.parse(node.textContent || "null");
        } catch (_err) {
            return null;
        }
    };

    const createSvg = (tag, attrs = {}) => {
        const el = document.createElementNS(SVG_NS, tag);
        for (const [k, v] of Object.entries(attrs)) {
            el.setAttribute(k, String(v));
        }
        return el;
    };

    const clearSvg = (svg) => {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
    };

    const viewBox = (svg) => {
        const parts = (svg.getAttribute("viewBox") || "0 0 980 340").split(/\s+/).map((v) => Number(v));
        return {width: parts[2] || 980, height: parts[3] || 340};
    };

    const fmtCurrency = (value) => {
        return `$${Number(value || 0).toLocaleString(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
        })}`;
    };

    const text = (svg, x, y, value, opts = {}) => {
        const node = createSvg("text", {
            x,
            y,
            fill: opts.color || "#334155",
            "font-size": opts.size || "10px",
            "text-anchor": opts.anchor || "start",
            "font-weight": opts.weight || "400",
        });
        node.textContent = value;
        svg.appendChild(node);
    };

    const drawEmpty = (svg, message) => {
        clearSvg(svg);
        const {width, height} = viewBox(svg);
        svg.appendChild(createSvg("rect", {
            x: 16,
            y: 16,
            width: width - 32,
            height: height - 32,
            rx: 8,
            fill: "#f8fafc",
            stroke: "#cbd5e1",
        }));
        text(svg, width / 2, height / 2, message, {anchor: "middle", size: "12px", color: "#475569"});
    };

    const drawAxes = (svg, bounds) => {
        svg.appendChild(createSvg("line", {
            x1: bounds.left,
            y1: bounds.bottom,
            x2: bounds.right,
            y2: bounds.bottom,
            stroke: "#94a3b8",
            "stroke-width": 1,
        }));
        svg.appendChild(createSvg("line", {
            x1: bounds.left,
            y1: bounds.top,
            x2: bounds.left,
            y2: bounds.bottom,
            stroke: "#94a3b8",
            "stroke-width": 1,
        }));
    };

    const renderOverviewTimeSeries = () => {
        const svg = document.getElementById("overview-time-series-svg");
        const rows = parseJson("analytics-overview-time-series");
        if (!svg) return;
        if (!Array.isArray(rows) || rows.length === 0) {
            drawEmpty(svg, "No time-series rows available.");
            return;
        }

        clearSvg(svg);
        const {width, height} = viewBox(svg);
        const bounds = {left: 50, right: width - 20, top: 24, bottom: height - 40};
        drawAxes(svg, bounds);

        const maxVal = Math.max(...rows.map((row) => Number(row.total_amount || 0)), 1);
        const pointCount = rows.length;
        const stepX = pointCount > 1 ? (bounds.right - bounds.left) / (pointCount - 1) : 0;

        const points = rows.map((row, idx) => {
            const x = bounds.left + (idx * stepX);
            const y = bounds.bottom - ((Number(row.total_amount || 0) / maxVal) * (bounds.bottom - bounds.top));
            return {x, y, row};
        });

        const linePath = points.map((p, idx) => `${idx === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
        svg.appendChild(createSvg("path", {
            d: linePath,
            fill: "none",
            stroke: "#1d4ed8",
            "stroke-width": 2.5,
        }));

        for (const point of points) {
            svg.appendChild(createSvg("circle", {
                cx: point.x,
                cy: point.y,
                r: 3.2,
                fill: "#1d4ed8",
            }));
        }

        const labelStep = Math.max(1, Math.floor(pointCount / 8));
        points.forEach((point, idx) => {
            if (idx % labelStep !== 0 && idx !== pointCount - 1) return;
            text(svg, point.x, bounds.bottom + 14, point.row.month, {anchor: "middle", size: "9px", color: "#475569"});
        });
        text(svg, bounds.left, bounds.top - 6, `Peak: ${fmtCurrency(maxVal)}`, {size: "10px", color: "#0f172a", weight: "600"});
    };

    const renderOverviewMom = () => {
        const svg = document.getElementById("overview-mom-svg");
        const rows = parseJson("analytics-overview-time-series");
        if (!svg) return;
        const momRows = Array.isArray(rows)
            ? rows.filter((row) => row.mom_change_pct !== null && row.mom_change_pct !== undefined)
            : [];
        if (momRows.length === 0) {
            drawEmpty(svg, "No MoM change values available.");
            return;
        }

        clearSvg(svg);
        const {width, height} = viewBox(svg);
        const bounds = {left: 40, right: width - 20, top: 24, bottom: height - 34};
        drawAxes(svg, bounds);

        const maxAbs = Math.max(...momRows.map((row) => Math.abs(Number(row.mom_change_pct || 0))), 1);
        const zeroY = bounds.top + ((maxAbs / (maxAbs * 2)) * (bounds.bottom - bounds.top));
        svg.appendChild(createSvg("line", {
            x1: bounds.left,
            y1: zeroY,
            x2: bounds.right,
            y2: zeroY,
            stroke: "#64748b",
            "stroke-dasharray": "4 3",
        }));

        const barWidth = Math.max(6, Math.floor((bounds.right - bounds.left) / momRows.length) - 3);
        momRows.forEach((row, idx) => {
            const value = Number(row.mom_change_pct || 0);
            const x = bounds.left + idx * (barWidth + 3);
            const yScale = (Math.abs(value) / (maxAbs * 2)) * (bounds.bottom - bounds.top);
            const y = value >= 0 ? zeroY - yScale : zeroY;
            svg.appendChild(createSvg("rect", {
                x,
                y,
                width: barWidth,
                height: Math.max(1, yScale),
                fill: value >= 0 ? "#16a34a" : "#dc2626",
                rx: 2,
            }));
        });
        text(svg, bounds.left, bounds.top - 6, `Range: -${maxAbs.toFixed(1)}% to +${maxAbs.toFixed(1)}%`, {size: "10px", weight: "600"});
    };

    const renderGeoStateGrid = () => {
        const svg = document.getElementById("geo-state-grid-svg");
        const summary = parseJson("analytics-geo-summary");
        if (!svg) return;
        const states = Array.isArray(summary?.states) ? summary.states : [];
        if (states.length === 0) {
            drawEmpty(svg, "No state-level rows available.");
            return;
        }

        clearSvg(svg);
        const {width} = viewBox(svg);
        const cols = 6;
        const cellW = Math.floor((width - 40) / cols);
        const cellH = 58;
        const maxAmount = Math.max(...states.map((row) => Number(row.total_amount || 0)), 1);
        const colorFor = (amount) => {
            const ratio = Math.max(0, Math.min(1, Number(amount || 0) / maxAmount));
            const blue = Math.floor(245 - (ratio * 130));
            const green = Math.floor(250 - (ratio * 120));
            return `rgb(30, ${green}, ${blue})`;
        };

        states.forEach((row, idx) => {
            const col = idx % cols;
            const rowIdx = Math.floor(idx / cols);
            const x = 20 + col * cellW;
            const y = 24 + rowIdx * cellH;
            svg.appendChild(createSvg("rect", {
                x,
                y,
                width: cellW - 10,
                height: cellH - 10,
                rx: 6,
                fill: colorFor(row.total_amount),
                stroke: "#1e3a8a",
                "stroke-opacity": "0.18",
            }));
            text(svg, x + 10, y + 18, row.state, {weight: "700", size: "11px", color: "#0f172a"});
            text(svg, x + 10, y + 34, fmtCurrency(row.total_amount), {size: "10px", color: "#1e293b"});
            text(svg, x + 10, y + 47, `${row.donor_count} donors`, {size: "9px", color: "#334155"});
        });
    };

    const renderGeoCityBars = () => {
        const svg = document.getElementById("geo-city-bars-svg");
        const summary = parseJson("analytics-geo-summary");
        if (!svg) return;
        const rows = Array.isArray(summary?.cities) ? summary.cities.slice(0, 12) : [];
        if (rows.length === 0) {
            drawEmpty(svg, "No city-level rows available.");
            return;
        }

        clearSvg(svg);
        const {width} = viewBox(svg);
        const left = 190;
        const right = width - 24;
        const top = 28;
        const rowH = 28;
        const maxVal = Math.max(...rows.map((row) => Number(row.total_amount || 0)), 1);

        rows.forEach((row, idx) => {
            const y = top + idx * rowH;
            const amount = Number(row.total_amount || 0);
            const barW = ((amount / maxVal) * (right - left));
            text(svg, 16, y + 13, `${row.city}, ${row.state}`, {size: "10px", color: "#334155"});
            svg.appendChild(createSvg("rect", {
                x: left,
                y: y + 2,
                width: Math.max(2, barW),
                height: 14,
                rx: 3,
                fill: "#0f766e",
            }));
            text(svg, left + barW + 6, y + 13, fmtCurrency(amount), {size: "10px", color: "#0f172a"});
        });
    };

    const renderRiskHistogram = () => {
        const svg = document.getElementById("anomaly-histogram-svg");
        const rows = parseJson("analytics-risk-anomalies");
        if (!svg) return;
        const values = Array.isArray(rows)
            ? rows.map((row) => Number(row.value || 0)).filter((value) => value > 0)
            : [];
        if (values.length === 0) {
            drawEmpty(svg, "No anomaly values to render.");
            return;
        }

        clearSvg(svg);
        const {width, height} = viewBox(svg);
        const bounds = {left: 40, right: width - 20, top: 20, bottom: height - 30};
        drawAxes(svg, bounds);

        const binCount = Math.min(10, Math.max(5, Math.floor(Math.sqrt(values.length))));
        const maxVal = Math.max(...values);
        const minVal = Math.min(...values);
        const span = Math.max(1, maxVal - minVal);
        const bins = Array.from({length: binCount}, () => 0);
        values.forEach((value) => {
            const idx = Math.min(binCount - 1, Math.floor(((value - minVal) / span) * binCount));
            bins[idx] += 1;
        });
        const maxCount = Math.max(...bins, 1);
        const barW = (bounds.right - bounds.left) / binCount;

        bins.forEach((count, idx) => {
            const x = bounds.left + idx * barW + 2;
            const h = (count / maxCount) * (bounds.bottom - bounds.top);
            const y = bounds.bottom - h;
            svg.appendChild(createSvg("rect", {
                x,
                y,
                width: Math.max(4, barW - 4),
                height: Math.max(1, h),
                fill: "#1d4ed8",
                rx: 2,
            }));
            text(svg, x + ((barW - 4) / 2), bounds.bottom + 12, `${idx + 1}`, {anchor: "middle", size: "9px", color: "#475569"});
        });
        text(svg, bounds.left, bounds.top - 5, `Value range ${fmtCurrency(minVal)} - ${fmtCurrency(maxVal)}`, {size: "10px", weight: "600"});
    };

    const renderRiskSeverity = () => {
        const svg = document.getElementById("anomaly-severity-svg");
        const rows = parseJson("analytics-risk-anomalies");
        if (!svg) return;
        if (!Array.isArray(rows) || rows.length === 0) {
            drawEmpty(svg, "No anomaly severities to render.");
            return;
        }

        const grouped = new Map();
        rows.forEach((row) => {
            const key = String(row.flag_type || "unknown");
            const current = grouped.get(key) || {sum: 0, count: 0};
            current.sum += Number(row.severity || 0);
            current.count += 1;
            grouped.set(key, current);
        });
        const metrics = Array.from(grouped.entries()).map(([flagType, stats]) => ({
            flagType,
            avgSeverity: stats.count ? (stats.sum / stats.count) : 0,
            count: stats.count,
        }));
        metrics.sort((a, b) => b.avgSeverity - a.avgSeverity);
        if (metrics.length === 0) {
            drawEmpty(svg, "No anomaly severities to render.");
            return;
        }

        clearSvg(svg);
        const {width} = viewBox(svg);
        const left = 220;
        const right = width - 24;
        const top = 22;
        const rowH = 30;
        const maxVal = Math.max(...metrics.map((row) => row.avgSeverity), 1);

        metrics.forEach((row, idx) => {
            const y = top + idx * rowH;
            const barW = (row.avgSeverity / maxVal) * (right - left);
            text(svg, 12, y + 14, row.flagType, {size: "10px", color: "#334155"});
            svg.appendChild(createSvg("rect", {
                x: left,
                y: y + 2,
                width: Math.max(2, barW),
                height: 14,
                rx: 3,
                fill: "#b91c1c",
            }));
            text(svg, left + barW + 6, y + 14, `${row.avgSeverity.toFixed(2)} avg (${row.count})`, {size: "10px", color: "#0f172a"});
        });
    };

    renderOverviewTimeSeries();
    renderOverviewMom();
    renderGeoStateGrid();
    renderGeoCityBars();
    renderRiskHistogram();
    renderRiskSeverity();
})();
