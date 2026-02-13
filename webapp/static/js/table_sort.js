(() => {
    const TABLE_SELECTOR = "table.data-table, table.info-table";
    const collator = new Intl.Collator(undefined, {
        numeric: true,
        sensitivity: "base",
    });

    const normalizeText = (value) => {
        return (value || "").replace(/\s+/g, " ").trim();
    };

    const parseNumber = (value) => {
        if (!value) return null;
        let cleaned = value
            .replace(/\u2212/g, "-")
            .replace(/\$/g, "")
            .replace(/,/g, "")
            .replace(/%/g, "")
            .replace(/\s+/g, "");

        if (/^\(.*\)$/.test(cleaned)) {
            cleaned = `-${cleaned.slice(1, -1)}`;
        }

        if (!/^-?\d*\.?\d+$/.test(cleaned)) {
            return null;
        }

        const parsed = Number(cleaned);
        return Number.isFinite(parsed) ? parsed : null;
    };

    const parseDate = (value) => {
        if (!value) return null;
        const looksLikeDate =
            /[A-Za-z]{3,}/.test(value) ||
            /\d{1,4}[/-]\d{1,2}[/-]\d{1,4}/.test(value);
        if (!looksLikeDate) return null;
        const parsed = Date.parse(value);
        return Number.isNaN(parsed) ? null : parsed;
    };

    const getCellValue = (row, index) => {
        const cell = row.cells[index];
        if (!cell) return "";
        if (cell.dataset.sortValue) return normalizeText(cell.dataset.sortValue);
        return normalizeText(cell.textContent || "");
    };

    const inferColumnType = (rows, index) => {
        let nonEmpty = 0;
        let numberCount = 0;
        let dateCount = 0;

        for (const row of rows.slice(0, 40)) {
            const value = getCellValue(row, index);
            if (!value) continue;
            nonEmpty += 1;
            if (parseNumber(value) !== null) {
                numberCount += 1;
                continue;
            }
            if (parseDate(value) !== null) {
                dateCount += 1;
            }
        }

        if (nonEmpty === 0) return "text";
        if (numberCount / nonEmpty >= 0.6) return "number";
        if (dateCount / nonEmpty >= 0.6) return "date";
        return "text";
    };

    const compareValues = (a, b, columnType) => {
        if (columnType === "number") {
            const left = parseNumber(a);
            const right = parseNumber(b);
            if (left !== null && right !== null) return left - right;
            if (left !== null) return 1;
            if (right !== null) return -1;
        }

        if (columnType === "date") {
            const left = parseDate(a);
            const right = parseDate(b);
            if (left !== null && right !== null) return left - right;
            if (left !== null) return 1;
            if (right !== null) return -1;
        }

        return collator.compare(a, b);
    };

    const getHeaderRow = (table) => {
        if (table.tHead && table.tHead.rows.length > 0) return table.tHead.rows[0];
        return Array.from(table.rows).find((row) =>
            Array.from(row.cells).some((cell) => cell.tagName === "TH")
        ) || null;
    };

    const getSortableRows = (table, headerRow) => {
        if (table.tBodies && table.tBodies.length > 0) {
            return Array.from(table.tBodies[0].rows);
        }

        const allRows = Array.from(table.rows);
        const headerIndex = allRows.indexOf(headerRow);
        return allRows.filter((row, index) => index > headerIndex);
    };

    const updateHeaderState = (headers, activeIndex, direction) => {
        headers.forEach((header, index) => {
            const isActive = index === activeIndex;
            header.dataset.sortDir = isActive ? direction : "";
            header.setAttribute(
                "aria-sort",
                isActive ? (direction === "asc" ? "ascending" : "descending") : "none"
            );
        });
    };

    const sortTable = (table, headerRow, headers, columnIndex, direction) => {
        const rows = getSortableRows(table, headerRow);
        if (rows.length === 0) return;

        const columnType = inferColumnType(rows, columnIndex);
        const factor = direction === "asc" ? 1 : -1;
        const decorated = rows.map((row, originalIndex) => ({
            row,
            originalIndex,
            value: getCellValue(row, columnIndex),
        }));

        decorated.sort((left, right) => {
            const cmp = compareValues(left.value, right.value, columnType);
            if (cmp !== 0) return cmp * factor;
            return left.originalIndex - right.originalIndex;
        });

        const fragment = document.createDocumentFragment();
        for (const item of decorated) {
            fragment.appendChild(item.row);
        }

        if (table.tBodies && table.tBodies.length > 0) {
            table.tBodies[0].appendChild(fragment);
        } else {
            table.appendChild(fragment);
        }

        updateHeaderState(headers, columnIndex, direction);
    };

    const shouldUseDefaultClick = (event) => {
        return event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey;
    };

    const initTable = (table) => {
        if (table.dataset.sortInitialized === "true") return;
        const headerRow = getHeaderRow(table);
        if (!headerRow) return;

        const headers = Array.from(headerRow.cells);
        if (headers.length === 0) return;

        headers.forEach((header, headerIndex) => {
            header.classList.add("js-sortable-header");
            header.setAttribute("role", "button");
            header.setAttribute("tabindex", "0");
            header.setAttribute("aria-sort", "none");

            const onActivate = (event) => {
                if (shouldUseDefaultClick(event)) return;
                const link = event.target.closest("a");
                if (link) event.preventDefault();
                const nextDirection = header.dataset.sortDir === "asc" ? "desc" : "asc";
                sortTable(table, headerRow, headers, headerIndex, nextDirection);
            };

            header.addEventListener("click", onActivate);
            header.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                const nextDirection = header.dataset.sortDir === "asc" ? "desc" : "asc";
                sortTable(table, headerRow, headers, headerIndex, nextDirection);
            });
        });

        table.dataset.sortInitialized = "true";
    };

    const init = () => {
        document.querySelectorAll(TABLE_SELECTOR).forEach(initTable);
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, {once: true});
    } else {
        init();
    }
})();
