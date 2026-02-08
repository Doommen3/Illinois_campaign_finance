"""Main routes for dashboard and search."""
from flask import Blueprint, render_template, request, current_app

from database.models import Committee, Report, Donor, Contribution

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """Dashboard with stats."""
    conn = current_app.get_database()

    # Get statistics
    stats = {
        'total_committees': Committee.count(conn),
        'total_reports': Report.count(conn),
        'total_donors': Donor.count(conn),
        'total_contributions': Contribution.count(conn),
        'total_amount': Contribution.total_amount(conn),
        'paper_filed_count': Report.count(conn, paper_filed=True),
        'report_status': Report.count_by_status(conn),
    }

    # Get recent reports
    recent_reports = Report.get_all(conn, limit=10)

    # Get top donors
    top_donors = Donor.get_all_with_totals(conn, limit=10, sort_by='total_amount')

    return render_template('index.html',
                           stats=stats,
                           recent_reports=recent_reports,
                           top_donors=top_donors)


@main_bp.route('/search')
def search():
    """Global search."""
    conn = current_app.get_database()
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'all')

    results = {
        'committees': [],
        'donors': [],
        'query': query,
        'type': search_type
    }

    if query:
        if search_type in ('all', 'committees'):
            results['committees'] = Committee.search(conn, query, limit=50)

        if search_type in ('all', 'donors'):
            results['donors'] = Donor.search(conn, query, limit=50)

    return render_template('search.html', **results)
