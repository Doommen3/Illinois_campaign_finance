/* The click drill-down panel shared by the entity Network tab's views (plan .claude/plans/money-flow-drilldown.md, D4;
   extracted from ego_force.js's N19 "Connected to" list, whose markup it keeps).

   EntityViews.focusPanel(focusBox, spec) fills ctx.focusBox: an h3 heading; a p.help-text note when spec.note or
   spec.link is given (the link, {text, href}, follows the note); then a table.data-table with a th per spec.columns
   and one body row per spec.rows entry -- the first cell is row.label, linked when row.href is set, then one cell
   per row.cells entry. With no rows it shows spec.empty instead of the table. EntityViews.clearFocus(focusBox)
   empties the panel. EntityViews.markFocus(element, focused, dimmed) sets the "focused" / "dimmed" classes and the
   0.15 opacity of a dimmed element (neither: both removed).

   Rows buttons (plan .claude/plans/inkind-rows-and-readable-ego.md, D3): a spec row with drill = {source, target,
   edge_type, cell} and spec.rowsUrl set shows cells[drill.cell] as a button; a click fetches spec.rowsUrl + source,
   target (when set) and edge_type (network/rows.json) and lists the filed rows in a nested table under the row; a
   second click collapses it. EntityViews.drill(edge, source, target, cell) is the drill of one ego edge, or null for
   an edge type the rows route does not serve. */
(function () {
    window.EntityViews = window.EntityViews || {};
    var DIM = 0.15;
    var ROW_TYPES = { donor_committee: true, in_kind_donor_committee: true, transfer_in: true, committee_vendor: true };

    function money(value) {
        return "$" + Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function element(tag, text) {
        var node = document.createElement(tag);
        if (text !== undefined) { node.textContent = text; }
        return node;
    }

    window.EntityViews.drill = function (edgeType, source, target, cell) {
        return ROW_TYPES[edgeType] ? { source: source, target: target || null, edge_type: edgeType, cell: cell } : null;
    };

    function rowsQuery(rowsUrl, drill) {
        var parts = ["source=" + encodeURIComponent(drill.source)];
        if (drill.target) { parts.push("target=" + encodeURIComponent(drill.target)); }
        parts.push("edge_type=" + encodeURIComponent(drill.edge_type));
        return rowsUrl + (rowsUrl.indexOf("?") < 0 ? "?" : "&") + parts.join("&");
    }

    function rowsTable(data) {
        var wrap = element("div");
        wrap.setAttribute("class", "edge-rows");
        if (data.unsupported) {
            wrap.appendChild(paragraph("Row detail is not available for this relationship."));
            return wrap;
        }
        if (data.truncated) {
            wrap.appendChild(paragraph("Showing " + data.rows.length + " of " + data.total_count + " rows."));
        }
        var table = element("table"), head = element("tr"), body = element("tbody"), thead = element("thead");
        table.setAttribute("class", "data-table edge-rows-table");
        ["Date", "Amount", "Type", "Description", "Filing"].forEach(function (title) { head.appendChild(element("th", title)); });
        thead.appendChild(head);
        table.appendChild(thead);
        data.rows.forEach(function (row) {
            var tr = element("tr"), filing = element("td");
            tr.appendChild(element("td", row.date || ""));
            tr.appendChild(element("td", money(row.amount)));
            tr.appendChild(element("td", row.d2_part || ""));
            tr.appendChild(element("td", row.description || row.purpose || ""));
            if (row.filing_href) {
                var link = element("a", "Filing");
                link.setAttribute("href", row.filing_href);
                filing.appendChild(link);
            }
            tr.appendChild(filing);
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        return wrap;
    }

    function toggleRows(rowsUrl, drill, tr, button, width) {
        if (tr.edgeRows) {
            tr.parentNode.removeChild(tr.edgeRows);
            tr.edgeRows = null;
            button.setAttribute("aria-expanded", "false");
            return;
        }
        var detail = element("tr"), cell = element("td");
        detail.setAttribute("class", "edge-rows-detail");
        cell.setAttribute("colspan", width);
        cell.appendChild(paragraph("Loading\u2026"));
        detail.appendChild(cell);
        tr.parentNode.insertBefore(detail, tr.nextSibling);
        tr.edgeRows = detail;
        button.setAttribute("aria-expanded", "true");
        function show(content) {
            cell.textContent = "";
            cell.appendChild(content);
        }
        window.fetch(rowsQuery(rowsUrl, drill), { credentials: "same-origin" }).then(function (response) {
            if (!response.ok) { throw new Error(String(response.status)); }
            return response.json();
        }).then(function (data) { show(rowsTable(data)); })
            .catch(function () { show(paragraph("The rows could not be loaded.")); });
    }

    function paragraph(text) {
        var p = document.createElement("p");
        p.setAttribute("class", "help-text");
        p.textContent = text;
        return p;
    }

    window.EntityViews.clearFocus = function (focusBox) {
        if (focusBox) { focusBox.textContent = ""; }
    };

    window.EntityViews.markFocus = function (element, focused, dimmed) {
        element.classList.remove("focused");
        element.classList.remove("dimmed");
        if (focused) { element.classList.add("focused"); }
        if (dimmed) { element.classList.add("dimmed"); }
        if (focused || dimmed) { element.setAttribute("opacity", dimmed ? DIM : 1); } else { element.removeAttribute("opacity"); }
    };

    window.EntityViews.focusPanel = function (focusBox, spec) {
        if (!focusBox) { return; }
        focusBox.textContent = "";
        var heading = document.createElement("h3");
        heading.textContent = spec.heading;
        focusBox.appendChild(heading);
        if (spec.note || spec.link) {
            var note = paragraph(spec.note ? spec.note + (spec.link ? " " : "") : "");
            if (spec.link) {
                var more = document.createElement("a");
                more.setAttribute("href", spec.link.href);
                more.textContent = spec.link.text;
                note.appendChild(more);
            }
            focusBox.appendChild(note);
        }
        if (!spec.rows.length) {
            if (spec.empty) { focusBox.appendChild(paragraph(spec.empty)); }
            return;
        }
        var table = document.createElement("table"), thead = document.createElement("thead");
        var head = document.createElement("tr"), body = document.createElement("tbody");
        table.setAttribute("class", "data-table");
        spec.columns.forEach(function (title) {
            var cell = document.createElement("th");
            cell.textContent = title;
            head.appendChild(cell);
        });
        thead.appendChild(head);
        table.appendChild(thead);
        spec.rows.forEach(function (row) {
            var tr = document.createElement("tr"), name = document.createElement("td");
            if (row.href) {
                var link = document.createElement("a");
                link.setAttribute("href", row.href);
                link.textContent = row.label;
                name.appendChild(link);
            } else {
                name.textContent = row.label;
            }
            tr.appendChild(name);
            row.cells.forEach(function (value, index) {
                var cell = document.createElement("td");
                if (row.drill && spec.rowsUrl && window.fetch && index === row.drill.cell) {
                    var button = element("button", value);
                    button.setAttribute("type", "button");
                    button.setAttribute("class", "edge-rows-toggle");
                    button.setAttribute("aria-expanded", "false");
                    button.addEventListener("click", function (event) {
                        event.stopPropagation();
                        toggleRows(spec.rowsUrl, row.drill, tr, button, spec.columns.length);
                    });
                    cell.appendChild(button);
                } else {
                    cell.textContent = value;
                }
                tr.appendChild(cell);
            });
            body.appendChild(tr);
        });
        table.appendChild(body);
        focusBox.appendChild(table);
    };
})();
