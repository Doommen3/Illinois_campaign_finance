"""Committee routes."""
from flask import Blueprint, render_template, request, current_app, abort

from database.models import Committee, Report, Contribution

committees_bp = Blueprint('committees', __name__)


@committees_bp.route('/')
def list_committees():
    """List all committees."""
    conn = current_app.get_database()

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')

    committees = Committee.get_all(conn, limit=per_page, offset=offset, sort_by=sort_by, sort_dir=sort_dir)
    total = Committee.count(conn)
    total_pages = (total + per_page - 1) // per_page

    return render_template('committees/list.html',
                           committees=committees,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           sort_by=sort_by,
                           sort_dir=sort_dir)


@committees_bp.route('/<int:committee_id>')
def committee_detail(committee_id):
    """Committee detail page."""
    conn = current_app.get_database()

    committee = Committee.get_by_id(conn, committee_id)
    if not committee:
        abort(404)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    report_sort = request.args.get('report_sort', 'filed_date')
    report_dir = request.args.get('report_dir', 'desc')
    contrib_sort = request.args.get('contrib_sort', 'filed_date')
    contrib_dir = request.args.get('contrib_dir', 'desc')

    # Get reports for this committee
    reports = Report.get_by_committee(
        conn,
        committee_id,
        limit=per_page,
        offset=offset,
        sort_by=report_sort,
        sort_dir=report_dir
    )

    # Get contributions for this committee
    contributions = Contribution.get_by_committee(
        conn,
        committee_id,
        limit=100,
        sort_by=contrib_sort,
        sort_dir=contrib_dir
    )

    # Get total
    total_amount = Contribution.total_by_committee(conn, committee_id)

    # Count reports
    report_count = Report.count_by_committee(conn, committee_id)
    total_pages = (report_count + per_page - 1) // per_page

    return render_template('committees/detail.html',
                           committee=committee,
                           reports=reports,
                           contributions=contributions,
                           total_amount=total_amount,
                           page=page,
                           total_pages=total_pages,
                           report_sort=report_sort,
                           report_dir=report_dir,
                           contrib_sort=contrib_sort,
                           contrib_dir=contrib_dir)
