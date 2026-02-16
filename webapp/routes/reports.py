"""Report routes."""
from flask import Blueprint, render_template, request, current_app, abort

from database.models import Report, Contribution
from webapp.utils.time_filter import get_active_period, period_to_date_window

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/')
def list_reports():
    """List all reports."""
    conn = current_app.get_database()
    period = get_active_period()
    date_from, date_to = period_to_date_window(period)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    # Filter options
    paper_filed = request.args.get('paper_filed')
    if paper_filed == 'true':
        paper_filed = True
    elif paper_filed == 'false':
        paper_filed = False
    else:
        paper_filed = None

    sort_by = request.args.get('sort', 'filed_date')
    sort_dir = request.args.get('dir', 'desc')

    reports = Report.get_all(
        conn,
        limit=per_page,
        offset=offset,
        paper_filed=paper_filed,
        filed_date_from=date_from,
        filed_date_to=date_to,
        sort_by=sort_by,
        sort_dir=sort_dir
    )
    total = Report.count(
        conn,
        paper_filed=paper_filed,
        filed_date_from=date_from,
        filed_date_to=date_to,
    )
    total_pages = (total + per_page - 1) // per_page

    # Get status counts
    status_counts = Report.count_by_status(
        conn,
        filed_date_from=date_from,
        filed_date_to=date_to,
    )

    return render_template('reports/list.html',
                           reports=reports,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           paper_filed_filter=request.args.get('paper_filed'),
                           status_counts=status_counts,
                           sort_by=sort_by,
                           sort_dir=sort_dir)


@reports_bp.route('/<int:report_id>')
def report_detail(report_id):
    """Report detail page."""
    conn = current_app.get_database()

    report = Report.get_by_id(conn, report_id)
    if not report:
        abort(404)

    sort_by = request.args.get('sort', 'amount')
    sort_dir = request.args.get('dir', 'desc')

    # Get contributions for this report
    contributions = Contribution.get_by_report(conn, report_id, sort_by=sort_by, sort_dir=sort_dir)

    # Calculate total
    total_amount = sum(c.amount or 0 for c in contributions)

    return render_template('reports/detail.html',
                           report=report,
                           contributions=contributions,
                           total_amount=total_amount,
                           sort_by=sort_by,
                           sort_dir=sort_dir)
