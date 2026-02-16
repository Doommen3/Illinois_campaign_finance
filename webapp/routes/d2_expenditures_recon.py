"""D2-vs-itemized expenditures reconciliation routes."""
import csv
from io import StringIO

from flask import Blueprint, Response, current_app, render_template, request

from database.models import D2ExpendituresRecon
from webapp.utils.time_filter import get_active_period

d2_expenditures_recon_bp = Blueprint("d2_expenditures_recon", __name__)


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


@d2_expenditures_recon_bp.route("/")
def list_d2_expenditures_recon():
    """List D2 rows with itemized expenditure reconciliation metrics."""
    conn = current_app.get_database()
    period = get_active_period()
    period_start = period.get("start_date")
    period_end = period.get("end_date")

    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get("sort", "abs_diff")
    sort_dir = request.args.get("dir", "desc")
    query = request.args.get("q", "").strip()
    min_abs_diff_raw = request.args.get("min_abs_diff", "").strip()
    min_expenditure_rows_raw = request.args.get("min_expenditure_rows", "").strip()
    anomalies_only = request.args.get("anomalies_only", "no").strip().lower()
    output_format = request.args.get("format", "html").strip().lower()

    min_abs_diff = _parse_float(min_abs_diff_raw)
    min_expenditure_rows = _parse_int(min_expenditure_rows_raw)
    table_available = D2ExpendituresRecon.is_available(conn)

    rows = []
    total = 0
    total_pages = 0
    if table_available:
        rows = D2ExpendituresRecon.get_all(
            conn,
            limit=per_page,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=query,
            min_abs_diff=min_abs_diff,
            min_expenditure_rows=min_expenditure_rows,
            anomalies_only=anomalies_only,
            period_start=period_start,
            period_end=period_end,
        )
        total = D2ExpendituresRecon.count(
            conn,
            search=query,
            min_abs_diff=min_abs_diff,
            min_expenditure_rows=min_expenditure_rows,
            anomalies_only=anomalies_only,
            period_start=period_start,
            period_end=period_end,
        )
        total_pages = (total + per_page - 1) // per_page

    if output_format == "csv":
        if not table_available:
            return Response("d2 expenditures reconciliation table unavailable\n", mimetype="text/plain", status=404)

        csv_rows = D2ExpendituresRecon.get_all(
            conn,
            limit=500000,
            offset=0,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=query,
            min_abs_diff=min_abs_diff,
            min_expenditure_rows=min_expenditure_rows,
            anomalies_only=anomalies_only,
            period_start=period_start,
            period_end=period_end,
        )

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "d2_totals_record_id",
                "committee_id_sbe",
                "committee_name",
                "filed_doc_id",
                "d2_itemized_expenditures_total",
                "expenditures_amount_sum",
                "expenditures_minus_d2_itemized_total",
                "sum_part_6_transfers_out",
                "sum_part_7_loans_made",
                "sum_part_8_expenditures",
                "sum_part_9_independent_expenditures",
                "d2_transfers_out_itemized",
                "d2_loans_made_itemized",
                "d2_expenditures_itemized",
                "d2_independent_expenditures_itemized",
                "part_6_minus_d2_transfers_out_itemized",
                "part_7_minus_d2_loans_made_itemized",
                "part_8_minus_d2_expenditures_itemized",
                "part_9_minus_d2_independent_expenditures_itemized",
                "expenditure_row_count",
                "anomaly_row_count",
                "first_expenditure_date",
                "last_expenditure_date",
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
                    row.d2_itemized_expenditures_total,
                    row.expenditures_amount_sum,
                    row.expenditures_minus_d2_itemized_total,
                    row.sum_part_6_transfers_out,
                    row.sum_part_7_loans_made,
                    row.sum_part_8_expenditures,
                    row.sum_part_9_independent_expenditures,
                    row.d2_transfers_out_itemized,
                    row.d2_loans_made_itemized,
                    row.d2_expenditures_itemized,
                    row.d2_independent_expenditures_itemized,
                    row.part_6_minus_d2_transfers_out_itemized,
                    row.part_7_minus_d2_loans_made_itemized,
                    row.part_8_minus_d2_expenditures_itemized,
                    row.part_9_minus_d2_independent_expenditures_itemized,
                    row.expenditure_row_count,
                    row.anomaly_row_count,
                    row.first_expenditure_date,
                    row.last_expenditure_date,
                    row.d2_total_expenditures,
                    row.ending_funds_available,
                    row.is_archived,
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=d2_expenditures_reconciliation.csv"
        return response

    return render_template(
        "d2_expenditures_recon/list.html",
        rows=rows,
        page=page,
        total=total,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        query=query,
        min_abs_diff=min_abs_diff_raw,
        min_expenditure_rows=min_expenditure_rows_raw,
        anomalies_only=anomalies_only,
        table_available=table_available,
    )
