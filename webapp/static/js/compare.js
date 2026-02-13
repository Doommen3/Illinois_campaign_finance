(() => {
    const svg = document.getElementById("compare-trend-svg");
    const dataNode = document.getElementById("compare-trend-data");
    const labelsNode = document.getElementById("compare-trend-labels");
    if (!svg || !dataNode) return;

    let rows = [];
    let labels = {left: "Left", right: "Right"};
    try {
        rows = JSON.parse(dataNode.textContent || "[]");
        labels = labelsNode ? JSON.parse(labelsNode.textContent || "{}") : labels;
    } catch (_err) {
        rows = [];
    }

    const SVG_NS = "http://www.w3.org/2000/svg";
    const create = (tag, attrs = {}) => {
        const el = document.createElementNS(SVG_NS, tag);
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
        return el;
    };
    const text = (x, y, value, opts = {}) => {
        const node = create("text", {
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
    const clear = () => {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
    };
    const drawEmpty = (message) => {
        clear();
        svg.appendChild(create("rect", {
            x: 16,
            y: 16,
            width: 948,
            height: 308,
            rx: 8,
            fill: "#f8fafc",
            stroke: "#cbd5e1",
        }));
        text(490, 170, message, {anchor: "middle", size: "12px"});
    };

    if (!Array.isArray(rows) || rows.length === 0) {
        drawEmpty("No monthly trend rows available for this comparison.");
        return;
    }

    clear();
    const bounds = {left: 56, right: 948, top: 26, bottom: 300};
    svg.appendChild(create("line", {x1: bounds.left, y1: bounds.bottom, x2: bounds.right, y2: bounds.bottom, stroke: "#94a3b8"}));
    svg.appendChild(create("line", {x1: bounds.left, y1: bounds.top, x2: bounds.left, y2: bounds.bottom, stroke: "#94a3b8"}));

    const maxVal = Math.max(
        ...rows.map((row) => Math.max(Number(row.left_total || 0), Number(row.right_total || 0))),
        1,
    );
    const stepX = rows.length > 1 ? (bounds.right - bounds.left) / (rows.length - 1) : 0;
    const point = (idx, value) => ({
        x: bounds.left + (idx * stepX),
        y: bounds.bottom - ((Number(value || 0) / maxVal) * (bounds.bottom - bounds.top)),
    });
    const toPath = (key) => rows.map((row, idx) => {
        const p = point(idx, row[key]);
        return `${idx === 0 ? "M" : "L"} ${p.x} ${p.y}`;
    }).join(" ");

    const leftColor = "#1d4ed8";
    const rightColor = "#ea580c";
    svg.appendChild(create("path", {d: toPath("left_total"), fill: "none", stroke: leftColor, "stroke-width": 2.5}));
    svg.appendChild(create("path", {d: toPath("right_total"), fill: "none", stroke: rightColor, "stroke-width": 2.5}));

    rows.forEach((row, idx) => {
        const left = point(idx, row.left_total);
        const right = point(idx, row.right_total);
        svg.appendChild(create("circle", {cx: left.x, cy: left.y, r: 2.7, fill: leftColor}));
        svg.appendChild(create("circle", {cx: right.x, cy: right.y, r: 2.7, fill: rightColor}));
    });

    const labelStep = Math.max(1, Math.floor(rows.length / 8));
    rows.forEach((row, idx) => {
        if (idx % labelStep !== 0 && idx !== rows.length - 1) return;
        const x = bounds.left + (idx * stepX);
        text(x, bounds.bottom + 14, row.month, {anchor: "middle", size: "9px", color: "#64748b"});
    });

    text(bounds.left, bounds.top - 8, `Peak ${new Intl.NumberFormat().format(maxVal)}`, {size: "10px", weight: "600", color: "#0f172a"});

    svg.appendChild(create("rect", {x: bounds.right - 250, y: bounds.top - 2, width: 10, height: 10, fill: leftColor, rx: 2}));
    text(bounds.right - 235, bounds.top + 7, labels.left || "Left", {size: "10px", color: "#1e293b"});
    svg.appendChild(create("rect", {x: bounds.right - 130, y: bounds.top - 2, width: 10, height: 10, fill: rightColor, rx: 2}));
    text(bounds.right - 115, bounds.top + 7, labels.right || "Right", {size: "10px", color: "#1e293b"});
})();
