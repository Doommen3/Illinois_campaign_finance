/* The click drill-down panel shared by the entity Network tab's views (plan .claude/plans/money-flow-drilldown.md, D4;
   extracted from ego_force.js's N19 "Connected to" list, whose markup it keeps).

   EntityViews.focusPanel(focusBox, spec) fills ctx.focusBox: an h3 heading; a p.help-text note when spec.note or
   spec.link is given (the link, {text, href}, follows the note); then a table.data-table with a th per spec.columns
   and one body row per spec.rows entry -- the first cell is row.label, linked when row.href is set, then one cell
   per row.cells entry. With no rows it shows spec.empty instead of the table. EntityViews.clearFocus(focusBox)
   empties the panel. EntityViews.markFocus(element, focused, dimmed) sets the "focused" / "dimmed" classes and the
   0.15 opacity of a dimmed element (neither: both removed). */
(function () {
    window.EntityViews = window.EntityViews || {};
    var DIM = 0.15;

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
            row.cells.forEach(function (value) {
                var cell = document.createElement("td");
                cell.textContent = value;
                tr.appendChild(cell);
            });
            body.appendChild(tr);
        });
        table.appendChild(body);
        focusBox.appendChild(table);
    };
})();
