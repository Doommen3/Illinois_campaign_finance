"""Donor routes."""
from flask import Blueprint, render_template, request, current_app, abort

from database.models import Donor, Contribution

donors_bp = Blueprint('donors', __name__)


@donors_bp.route('/')
def list_donors():
    """List all donors."""
    conn = current_app.get_database()

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    sort_by = request.args.get('sort', 'total_amount')
    sort_dir = request.args.get('dir', 'desc')

    donors = Donor.get_all_with_totals(
        conn,
        limit=per_page,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir
    )
    donor_source = Donor.get_directory_source(conn) or "legacy"
    total = Donor.count(conn)
    total_pages = (total + per_page - 1) // per_page

    return render_template('donors/list.html',
                           donors=donors,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           donor_source=donor_source,
                           sort_by=sort_by,
                           sort_dir=sort_dir)


@donors_bp.route('/<int:donor_id>')
def donor_detail(donor_id):
    """Donor detail page."""
    conn = current_app.get_database()

    donor = Donor.get_by_id(conn, donor_id)
    if not donor:
        abort(404)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'filed_date')
    sort_dir = request.args.get('dir', 'desc')

    # Get contributions from this donor
    contributions = Contribution.get_by_donor(
        conn,
        donor_id,
        limit=per_page,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir
    )

    # Get total
    total_amount = Contribution.total_by_donor(conn, donor_id)

    # Count contributions
    contribution_count = conn.execute(
        "SELECT COUNT(*) FROM contributions WHERE donor_id = ?",
        (donor_id,)
    ).fetchone()[0]
    total_pages = (contribution_count + per_page - 1) // per_page

    return render_template('donors/detail.html',
                           donor=donor,
                           contributions=contributions,
                           total_amount=total_amount,
                           contribution_count=contribution_count,
                           page=page,
                           total_pages=total_pages,
                           sort_by=sort_by,
                           sort_dir=sort_dir)


@donors_bp.route('/key/<path:donor_key>')
def donor_detail_by_key(donor_key):
    """Donor detail page for materialized/bulk donor keys."""
    conn = current_app.get_database()

    source = (request.args.get('source') or '').strip() or None
    donor = Donor.get_summary_by_key(conn, donor_key, source=source)
    if not donor:
        abort(404)

    resolved_source = donor.source or source or Donor.get_directory_source(conn)
    if not resolved_source:
        abort(404)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'amount')
    sort_dir = request.args.get('dir', 'desc')

    committee_rows = Donor.get_committee_breakdown_by_key(
        conn,
        donor.donor_key or donor_key,
        resolved_source,
        limit=per_page,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    committee_count = donor.committee_count
    if committee_count is None:
        committee_count = Donor.count_committee_breakdown_by_key(
            conn, donor.donor_key or donor_key, resolved_source
        )
    total_pages = (committee_count + per_page - 1) // per_page if committee_count else 0

    return render_template(
        'donors/detail_by_key.html',
        donor=donor,
        committee_rows=committee_rows,
        total_amount=donor.total_amount or 0,
        contribution_count=donor.contribution_count or 0,
        committee_count=committee_count or 0,
        source=resolved_source,
        donor_key=donor.donor_key or donor_key,
        page=page,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
