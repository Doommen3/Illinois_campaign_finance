"""Donor routes."""
from flask import Blueprint, render_template, request, current_app, abort

from database.models import Donor, Contribution
from webapp.utils.data_through import latest_data_date
from webapp.utils.time_scope import current_cycle, cycle_window, resolve_time_scope, scope_params

donors_bp = Blueprint('donors', __name__)


@donors_bp.route('/')
def list_donors():
    """List all donors."""
    conn = current_app.get_database()
    scope = resolve_time_scope(default="cycle:current", axis="received", data_through=latest_data_date("isbe"))
    date_from = scope.start.isoformat() if scope.start else None
    date_to = scope.end.isoformat() if scope.end else None

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
        sort_dir=sort_dir,
        transaction_date_from=date_from,
        transaction_date_to=date_to,
    )
    donor_source = Donor.get_directory_source(conn) or "legacy"
    total = Donor.count(
        conn,
        transaction_date_from=date_from,
        transaction_date_to=date_to,
    )
    total_pages = (total + per_page - 1) // per_page

    return render_template('donors/list.html',
                           donors=donors,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           donor_source=donor_source,
                           sort_by=sort_by,
                           sort_dir=sort_dir,
                           time_scope=scope,
                           scope_default_key=f"cycle:{current_cycle()}",
                           time_scope_source_label="ISBE",
                           scope_query=scope_params(scope))


@donors_bp.route('/<int:donor_id>')
def donor_detail(donor_id):
    """Donor detail page."""
    conn = current_app.get_database()
    scope = resolve_time_scope(default="all", axis="transaction", data_through=latest_data_date("isbe"))
    date_from = scope.start.isoformat() if scope.start else None
    date_to = scope.end.isoformat() if scope.end else None

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
        sort_dir=sort_dir,
        transaction_date_from=date_from,
        transaction_date_to=date_to,
    )

    # Get total
    total_amount = Contribution.total_by_donor(
        conn,
        donor_id,
        transaction_date_from=date_from,
        transaction_date_to=date_to,
    )

    # Current-cycle subtotal, shown beside the (possibly all-time) total
    cycle_start, cycle_end = cycle_window(current_cycle())
    cycle_total = Contribution.total_by_donor(
        conn,
        donor_id,
        transaction_date_from=cycle_start.isoformat(),
        transaction_date_to=cycle_end.isoformat(),
    )

    # Count contributions
    contribution_count = Contribution.count_by_donor(
        conn,
        donor_id,
        transaction_date_from=date_from,
        transaction_date_to=date_to,
    )
    total_pages = (contribution_count + per_page - 1) // per_page

    return render_template('donors/detail.html',
                           donor=donor,
                           contributions=contributions,
                           total_amount=total_amount,
                           cycle_total=cycle_total,
                           contribution_count=contribution_count,
                           page=page,
                           total_pages=total_pages,
                           sort_by=sort_by,
                           sort_dir=sort_dir,
                           time_scope=scope,
                           scope_default_key="all",
                           time_scope_source_label="ISBE",
                           scope_query=scope_params(scope))


@donors_bp.route('/key/<path:donor_key>')
def donor_detail_by_key(donor_key):
    """Donor detail page for materialized/bulk donor keys."""
    conn = current_app.get_database()
    scope = resolve_time_scope(default="all", axis="received", data_through=latest_data_date("isbe"))
    date_from = scope.start.isoformat() if scope.start else None
    date_to = scope.end.isoformat() if scope.end else None

    source = (request.args.get('source') or '').strip() or None
    donor = Donor.get_summary_by_key(
        conn,
        donor_key,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )
    if not donor and (date_from or date_to):
        # No receipts inside the chosen scope: keep the page (and its Reset link)
        # with zeroed totals rather than 404.
        donor = Donor.get_summary_by_key(conn, donor_key, source=source)
        if donor:
            donor.total_amount = 0.0
            donor.contribution_count = 0
            donor.committee_count = 0
    if not donor:
        abort(404)

    resolved_source = donor.source or source or Donor.get_directory_source(conn)
    if not resolved_source:
        abort(404)

    # Current-cycle subtotal, shown beside the (possibly all-time) total
    cycle_start, cycle_end = cycle_window(current_cycle())
    cycle_donor = Donor.get_summary_by_key(
        conn,
        donor.donor_key or donor_key,
        source=resolved_source,
        date_from=cycle_start.isoformat(),
        date_to=cycle_end.isoformat(),
    )
    cycle_total = cycle_donor.total_amount if cycle_donor else 0.0

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'amount')
    sort_dir = request.args.get('dir', 'desc')

    committee_rows = Donor.get_committee_breakdown_by_key(
        conn,
        donor.donor_key or donor_key,
        resolved_source,
        date_from=date_from,
        date_to=date_to,
        limit=per_page,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    committee_count = donor.committee_count
    if committee_count is None:
        committee_count = Donor.count_committee_breakdown_by_key(
            conn,
            donor.donor_key or donor_key,
            resolved_source,
            date_from=date_from,
            date_to=date_to,
        )
    total_pages = (committee_count + per_page - 1) // per_page if committee_count else 0

    return render_template(
        'donors/detail_by_key.html',
        donor=donor,
        committee_rows=committee_rows,
        total_amount=donor.total_amount or 0,
        cycle_total=cycle_total,
        contribution_count=donor.contribution_count or 0,
        committee_count=committee_count or 0,
        source=resolved_source,
        donor_key=donor.donor_key or donor_key,
        entity_id=None,
        detail_mode='key',
        page=page,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        time_scope=scope,
        scope_default_key="all",
        time_scope_source_label="ISBE",
        scope_query=scope_params(scope),
    )


@donors_bp.route('/entity/<path:entity_id>')
def donor_detail_by_entity(entity_id):
    """Donor detail page for merged local donor entities."""
    conn = current_app.get_database()
    scope = resolve_time_scope(default="all", axis="received", data_through=latest_data_date("isbe"))
    date_from = scope.start.isoformat() if scope.start else None
    date_to = scope.end.isoformat() if scope.end else None

    source = (request.args.get('source') or '').strip() or None
    donor = Donor.get_summary_by_entity(
        conn,
        entity_id,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )
    if not donor:
        abort(404)

    resolved_source = donor.source or source or Donor.get_directory_source(conn)
    if not resolved_source:
        abort(404)

    # Current-cycle subtotal, shown beside the (possibly all-time) total
    cycle_start, cycle_end = cycle_window(current_cycle())
    cycle_donor = Donor.get_summary_by_entity(
        conn,
        entity_id,
        source=resolved_source,
        date_from=cycle_start.isoformat(),
        date_to=cycle_end.isoformat(),
    )
    cycle_total = cycle_donor.total_amount if cycle_donor else 0.0

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'amount')
    sort_dir = request.args.get('dir', 'desc')

    committee_rows = Donor.get_committee_breakdown_by_entity(
        conn,
        donor.entity_id or entity_id,
        resolved_source,
        date_from=date_from,
        date_to=date_to,
        limit=per_page,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    committee_count = donor.committee_count
    if committee_count is None:
        committee_count = Donor.count_committee_breakdown_by_entity(
            conn,
            donor.entity_id or entity_id,
            resolved_source,
            date_from=date_from,
            date_to=date_to,
        )
    total_pages = (committee_count + per_page - 1) // per_page if committee_count else 0

    return render_template(
        'donors/detail_by_key.html',
        donor=donor,
        committee_rows=committee_rows,
        total_amount=donor.total_amount or 0,
        cycle_total=cycle_total,
        contribution_count=donor.contribution_count or 0,
        committee_count=committee_count or 0,
        source=resolved_source,
        donor_key=donor.donor_key,
        entity_id=donor.entity_id or entity_id,
        detail_mode='entity',
        page=page,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        time_scope=scope,
        scope_default_key="all",
        time_scope_source_label="ISBE",
        scope_query=scope_params(scope),
    )
