"""D2-vs-itemized receipts reconciliation routes."""
import csv
from io import StringIO

from flask import Blueprint, Response, current_app, render_template, request

from database.models import D2ReceiptsRecon

d2_receipts_recon_bp = Blueprint("d2_receipts_recon", __name__)


def _parse_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


@d2_receipts_recon_bp.route("/")
def list_d2_receipts_recon():
    """List D2 rows with itemized receipt reconciliation metrics."""
    conn = current_app.get_database()

    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get("sort", "abs_diff")
    sort_dir = request.args.get("dir", "desc")
    query = request.args.get("q", "").strip()
    min_abs_diff_raw = request.args.get("min_abs_diff", "").strip()
    min_receipt_rows_raw = request.args.get("min_receipt_rows", "").strip()
    output_format = request.args.get("format", "html").strip().lower()

    min_abs_diff = _parse_float(min_abs_diff_raw)
    min_receipt_rows = _parse_int(min_receipt_rows_raw)
    table_available = D2ReceiptsRecon.is_available(conn)

    rows = []
    total = 0
    total_pages = 0
    if table_available:
        rows = D2ReceiptsRecon.get_all(
            conn,
            limit=per_page,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=query,
            min_abs_diff=min_abs_diff,
            min_receipt_rows=min_receipt_rows,
        )
        total = D2ReceiptsRecon.count(
            conn,
            search=query,
            min_abs_diff=min_abs_diff,
            min_receipt_rows=min_receipt_rows,
        )
        total_pages = (total + per_page - 1) // per_page

    if output_format == "csv":
        if not table_available:
            return Response("d2 receipts reconciliation table unavailable\n", mimetype="text/plain", status=404)

        csv_rows = D2ReceiptsRecon.get_all(
            conn,
            limit=500000,
            offset=0,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=query,
            min_abs_diff=min_abs_diff,
            min_receipt_rows=min_receipt_rows,
        )

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "d2_totals_record_id",
                "committee_id_sbe",
                "committee_name",
                "filed_doc_id",
                "d2_total_receipts",
                "receipts_amount_sum",
                "receipts_minus_d2_total",
                "receipt_row_count",
                "first_receipt_date",
                "last_receipt_date",
                "d2_total_expenditures",
                "ending_funds_available",
                "is_archived",
            ]
        )
        for row in csv_rows:
            writer.writerow(
                [
                    row.d2_totals_record_id,
                    row.committee_id_sbe,
                    row.committee_name,
                    row.filed_doc_id,
                    row.d2_total_receipts,
                    row.receipts_amount_sum,
                    row.receipts_minus_d2_total,
                    row.receipt_row_count,
                    row.first_receipt_date,
                    row.last_receipt_date,
                    row.d2_total_expenditures,
                    row.ending_funds_available,
                    row.is_archived,
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=d2_receipts_reconciliation.csv"
        return response

    return render_template(
        "d2_receipts_recon/list.html",
        rows=rows,
        page=page,
        total=total,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        query=query,
        min_abs_diff=min_abs_diff_raw,
        min_receipt_rows=min_receipt_rows_raw,
        table_available=table_available,
    )
