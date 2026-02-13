"""JSON API routes."""
from flask import Blueprint, jsonify, request, current_app

from database.analytics import (
    get_analytics_data_sources,
    get_anomaly_flags,
    get_candidate_competition_networks,
    get_committee_similarity_network,
    get_donor_concentration,
    get_donor_cogiving_network,
    get_geo_summary,
    get_irs527_ecosystem_graph,
    get_lobbying_influence_graph,
    get_network_graph,
    get_nlp_spending_summary,
    get_reconciliation_outliers,
    get_time_series,
)
from database.models import Committee, Report, Donor, Contribution, ScrapeState

api_bp = Blueprint('api', __name__)


@api_bp.route('/stats')
def stats():
    """Get overall statistics."""
    conn = current_app.get_database()

    return jsonify({
        'committees': Committee.count(conn),
        'reports': Report.count(conn),
        'donors': Donor.count(conn),
        'contributions': Contribution.count(conn),
        'total_amount': Contribution.total_amount(conn),
        'paper_filed_reports': Report.count(conn, paper_filed=True),
        'report_status': Report.count_by_status(conn)
    })


@api_bp.route('/committees')
def list_committees():
    """List committees as JSON."""
    conn = current_app.get_database()

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 100)  # Limit max per page
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')

    committees = Committee.get_all(
        conn,
        limit=per_page,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir
    )
    total = Committee.count(conn)

    return jsonify({
        'data': [
            {'id': c.id, 'name': c.name, 'created_at': str(c.created_at)}
            for c in committees
        ],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page
    })


@api_bp.route('/committees/<int:committee_id>')
def get_committee(committee_id):
    """Get committee details."""
    conn = current_app.get_database()

    committee = Committee.get_by_id(conn, committee_id)
    if not committee:
        return jsonify({'error': 'Committee not found'}), 404

    total_amount = Contribution.total_by_committee(conn, committee_id)
    reports = Report.get_by_committee(conn, committee_id, limit=100)

    return jsonify({
        'id': committee.id,
        'name': committee.name,
        'total_contributions': total_amount,
        'report_count': len(reports),
        'created_at': str(committee.created_at)
    })


@api_bp.route('/donors')
def list_donors():
    """List donors as JSON."""
    conn = current_app.get_database()

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 100)
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
    total = Donor.count(conn)

    return jsonify({
        'data': [
            {
                'id': d.id,
                'name': d.name,
                'address': d.address,
                'occupation': d.occupation,
                'employer': d.employer,
                'total_amount': d.total_amount,
                'contribution_count': d.contribution_count
            }
            for d in donors
        ],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page
    })


@api_bp.route('/donors/<int:donor_id>')
def get_donor(donor_id):
    """Get donor details."""
    conn = current_app.get_database()

    donor = Donor.get_by_id(conn, donor_id)
    if not donor:
        return jsonify({'error': 'Donor not found'}), 404

    total_amount = Contribution.total_by_donor(conn, donor_id)
    contributions = Contribution.get_by_donor(conn, donor_id, limit=100)

    return jsonify({
        'id': donor.id,
        'name': donor.name,
        'address': donor.address,
        'occupation': donor.occupation,
        'employer': donor.employer,
        'normalized_name': donor.normalized_name,
        'normalized_address': donor.normalized_address,
        'total_contributions': total_amount,
        'contribution_count': len(contributions)
    })


@api_bp.route('/reports')
def list_reports():
    """List reports as JSON."""
    conn = current_app.get_database()

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 100)
    offset = (page - 1) * per_page

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
        sort_by=sort_by,
        sort_dir=sort_dir
    )
    total = Report.count(conn, paper_filed=paper_filed)

    return jsonify({
        'data': [
            {
                'id': r.id,
                'committee_name': r.committee_name,
                'report_type': r.report_type,
                'reporting_period': r.reporting_period,
                'filed_date': r.filed_date,
                'pages': r.pages,
                'is_paper_filed': r.is_paper_filed,
                'scrape_status': r.scrape_status
            }
            for r in reports
        ],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page
    })


@api_bp.route('/reports/<int:report_id>')
def get_report(report_id):
    """Get report details with contributions."""
    conn = current_app.get_database()

    report = Report.get_by_id(conn, report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404

    sort_by = request.args.get('sort', 'amount')
    sort_dir = request.args.get('dir', 'desc')
    contributions = Contribution.get_by_report(conn, report_id, sort_by=sort_by, sort_dir=sort_dir)

    return jsonify({
        'id': report.id,
        'committee_name': report.committee_name,
        'report_type': report.report_type,
        'reporting_period': report.reporting_period,
        'filed_date': report.filed_date,
        'pages': report.pages,
        'is_paper_filed': report.is_paper_filed,
        'scrape_status': report.scrape_status,
        'contributions': [
            {
                'id': c.id,
                'donor_name': c.donor_name,
                'amount': c.amount,
                'received_by': c.received_by,
                'description': c.description
            }
            for c in contributions
        ],
        'total_amount': sum(c.amount or 0 for c in contributions)
    })


@api_bp.route('/scrape-status')
def scrape_status():
    """Get current scrape status."""
    conn = current_app.get_database()

    main_list_state = ScrapeState.get_latest(conn, 'main_list')
    details_state = ScrapeState.get_latest(conn, 'details')

    return jsonify({
        'main_list': {
            'status': main_list_state.status if main_list_state else None,
            'last_page': main_list_state.last_page if main_list_state else None,
            'total_pages': main_list_state.total_pages if main_list_state else None,
            'started_at': str(main_list_state.started_at) if main_list_state else None,
            'completed_at': str(main_list_state.completed_at) if main_list_state else None,
            'error': main_list_state.error_message if main_list_state else None
        } if main_list_state else None,
        'details': {
            'status': details_state.status if details_state else None,
            'last_report_id': details_state.last_report_id if details_state else None,
            'started_at': str(details_state.started_at) if details_state else None,
            'completed_at': str(details_state.completed_at) if details_state else None,
            'error': details_state.error_message if details_state else None
        } if details_state else None,
        'report_status': Report.count_by_status(conn)
    })


@api_bp.route('/analytics/network')
def analytics_network():
    """Get donor->committee->candidate network analytics."""
    conn = current_app.get_database()
    min_edge_amount = request.args.get('min_edge_amount', 1000, type=float)
    limit = min(request.args.get('limit', 200, type=int), 2000)
    return jsonify(get_network_graph(conn, min_edge_amount=min_edge_amount, limit=limit))


@api_bp.route('/analytics/anomalies')
def analytics_anomalies():
    """Get anomaly/risk flags."""
    conn = current_app.get_database()
    limit = min(request.args.get('limit', 25, type=int), 1000)
    date_from = (request.args.get('date_from', '', type=str) or '').strip() or None
    date_to = (request.args.get('date_to', '', type=str) or '').strip() or None
    return jsonify({'data': get_anomaly_flags(conn, limit=limit, date_from=date_from, date_to=date_to)})


@api_bp.route('/analytics/concentration')
def analytics_concentration():
    """Get donor concentration metrics by committee."""
    conn = current_app.get_database()
    limit = min(request.args.get('limit', 25, type=int), 1000)
    return jsonify({'data': get_donor_concentration(conn, limit=limit)})


@api_bp.route('/analytics/time-series')
def analytics_time_series():
    """Get monthly contribution time series."""
    conn = current_app.get_database()
    months = min(request.args.get('months', 24, type=int), 120)
    date_from = (request.args.get('date_from', '', type=str) or '').strip() or None
    date_to = (request.args.get('date_to', '', type=str) or '').strip() or None
    return jsonify({'data': get_time_series(conn, months=months, date_from=date_from, date_to=date_to)})


@api_bp.route('/analytics/geo')
def analytics_geo():
    """Get state/city contribution geography summary."""
    conn = current_app.get_database()
    state_limit = min(request.args.get('state_limit', 15, type=int), 100)
    city_limit = min(request.args.get('city_limit', 25, type=int), 500)
    return jsonify(get_geo_summary(conn, limit_states=state_limit, limit_cities=city_limit))


@api_bp.route('/analytics/nlp')
def analytics_nlp():
    """Get NLP category summaries from spending descriptions."""
    conn = current_app.get_database()
    limit = min(request.args.get('limit', 20, type=int), 200)
    return jsonify({'data': get_nlp_spending_summary(conn, limit=limit)})


@api_bp.route('/analytics/reconciliation')
def analytics_reconciliation():
    """Get D2-vs-receipts reconciliation outliers."""
    conn = current_app.get_database()
    limit = min(request.args.get('limit', 20, type=int), 500)
    min_abs_diff = request.args.get('min_abs_diff', 1000, type=float)
    return jsonify(
        {
            'data': get_reconciliation_outliers(conn, limit=limit, min_abs_diff=min_abs_diff),
            'sources': get_analytics_data_sources(conn),
        }
    )


@api_bp.route('/analytics/donor-cogiving')
def analytics_donor_cogiving():
    """Get donor co-giving network."""
    conn = current_app.get_database()
    donor_limit = min(max(request.args.get('donor_limit', 200, type=int), 50), 5000)
    edge_limit = min(max(request.args.get('edge_limit', 200, type=int), 50), 10000)
    min_shared_amount = max(request.args.get('min_shared_amount', 5000.0, type=float), 0.0)
    min_shared_targets = max(request.args.get('min_shared_targets', 2, type=int), 1)
    return jsonify(
        get_donor_cogiving_network(
            conn,
            donor_limit=donor_limit,
            edge_limit=edge_limit,
            min_shared_amount=min_shared_amount,
            min_shared_targets=min_shared_targets,
        )
    )


@api_bp.route('/analytics/committee-similarity')
def analytics_committee_similarity():
    """Get committee similarity network."""
    conn = current_app.get_database()
    committee_limit = min(max(request.args.get('committee_limit', 120, type=int), 50), 5000)
    edge_limit = min(max(request.args.get('edge_limit', 200, type=int), 50), 10000)
    min_shared_donors = max(request.args.get('min_shared_donors', 3, type=int), 1)
    min_shared_amount = max(request.args.get('min_shared_amount', 10000.0, type=float), 0.0)
    return jsonify(
        get_committee_similarity_network(
            conn,
            committee_limit=committee_limit,
            edge_limit=edge_limit,
            min_shared_donors=min_shared_donors,
            min_shared_amount=min_shared_amount,
        )
    )


@api_bp.route('/analytics/candidate-competition')
def analytics_candidate_competition():
    """Get state/federal/combined candidate competition networks."""
    conn = current_app.get_database()
    candidate_limit = min(max(request.args.get('candidate_limit', 80, type=int), 50), 2000)
    edge_limit = min(max(request.args.get('edge_limit', 200, type=int), 50), 10000)
    min_shared_donors = max(request.args.get('min_shared_donors', 2, type=int), 1)
    min_shared_amount = max(request.args.get('min_shared_amount', 2500.0, type=float), 0.0)
    return jsonify(
        get_candidate_competition_networks(
            conn,
            candidate_limit=candidate_limit,
            edge_limit=edge_limit,
            min_shared_donors=min_shared_donors,
            min_shared_amount=min_shared_amount,
        )
    )


@api_bp.route('/analytics/lobbying-influence')
def analytics_lobbying_influence():
    """Get lobbying influence graph."""
    conn = current_app.get_database()
    client_limit = min(max(request.args.get('client_limit', 80, type=int), 20), 2000)
    edge_limit = min(max(request.args.get('edge_limit', 200, type=int), 50), 10000)
    return jsonify(
        get_lobbying_influence_graph(
            conn,
            client_limit=client_limit,
            edge_limit=edge_limit,
        )
    )


@api_bp.route('/analytics/irs527-ecosystem')
def analytics_irs527_ecosystem():
    """Get IRS 527 ecosystem graph."""
    conn = current_app.get_database()
    org_limit = min(max(request.args.get('org_limit', 100, type=int), 20), 2000)
    edge_limit = min(max(request.args.get('edge_limit', 200, type=int), 50), 10000)
    return jsonify(
        get_irs527_ecosystem_graph(
            conn,
            org_limit=org_limit,
            edge_limit=edge_limit,
        )
    )
