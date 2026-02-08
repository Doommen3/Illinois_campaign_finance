"""Federal candidate finance routes (FEC data)."""
from __future__ import annotations

from flask import Blueprint, current_app, render_template, request

from database.federal_fec import (
    count_federal_candidates,
    federal_data_available,
    get_federal_candidate_detail,
    get_federal_donor_detail,
    get_federal_donor_network_clusters,
    get_federal_donor_segmentation,
    get_federal_follow_the_money,
    get_federal_geographic_concentration,
    get_federal_influence_scores,
    get_federal_local_donor_matches,
    get_federal_local_overlap_network,
    get_federal_network_graph,
    get_federal_race_analytics,
    list_federal_candidates,
)

federal_finance_bp = Blueprint('federal_finance', __name__)


def _parse_shared_filters() -> tuple[int, str, str]:
    cycle = request.args.get('cycle', 2026, type=int)
    if cycle < 1970 or cycle > 2100:
        cycle = 2026

    analysis_office = request.args.get('analysis_office', '', type=str).strip().upper()
    if analysis_office not in {'', 'H', 'S', 'P'}:
        analysis_office = ''
    analysis_district = request.args.get('analysis_district', '', type=str).strip()
    return cycle, analysis_office, analysis_district


def _base_context(active_page: str, table_available: bool, cycle: int, analysis_office: str, analysis_district: str) -> dict:
    return {
        'active_page': active_page,
        'table_available': table_available,
        'cycle': cycle,
        'analysis_office': analysis_office,
        'analysis_district': analysis_district,
    }


@federal_finance_bp.route('/', endpoint='list_federal_finance')
@federal_finance_bp.route('/overview', endpoint='federal_overview')
def federal_overview():
    """Overview page with high-level federal race, donor, and geography summaries."""
    conn = current_app.get_database()
    cycle, analysis_office, analysis_district = _parse_shared_filters()
    table_available = federal_data_available(conn)

    overview = {
        'candidate_count': 0,
        'race_count': 0,
        'network_total_amount': 0.0,
        'network_donor_count': 0,
        'network_candidate_count': 0,
    }
    race_analytics: list[dict] = []
    geographic = {'states': [], 'cities': [], 'race_concentration': []}
    top_donors: list[dict] = []
    top_candidates: list[dict] = []

    if table_available:
        overview['candidate_count'] = count_federal_candidates(conn, cycle=cycle)
        race_analytics = get_federal_race_analytics(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            limit=12,
        )
        overview['race_count'] = len(race_analytics)

        geographic = get_federal_geographic_concentration(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            limit_states=10,
            limit_cities=12,
            limit_races=10,
        )

        network_snapshot = get_federal_network_graph(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            min_edge_amount=100.0,
            limit=500,
        )
        overview['network_total_amount'] = network_snapshot['summary'].get('total_amount', 0.0)
        overview['network_donor_count'] = network_snapshot['summary'].get('donor_count', 0)
        overview['network_candidate_count'] = network_snapshot['summary'].get('candidate_count', 0)

        top_donors = [row for row in network_snapshot.get('centrality', []) if row.get('node_type') == 'donor'][:10]
        top_candidates = [row for row in network_snapshot.get('centrality', []) if row.get('node_type') == 'candidate'][:10]

    return render_template(
        'federal_finance/overview.html',
        **_base_context('overview', table_available, cycle, analysis_office, analysis_district),
        overview=overview,
        race_analytics=race_analytics,
        geographic=geographic,
        top_donors=top_donors,
        top_candidates=top_candidates,
    )


@federal_finance_bp.route('/candidates')
def federal_candidates():
    """Candidate table and drill-down entry points."""
    conn = current_app.get_database()
    cycle, analysis_office, analysis_district = _parse_shared_filters()
    table_available = federal_data_available(conn)

    page = max(request.args.get('page', 1, type=int), 1)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'total_amount', type=str).strip()
    sort_dir = request.args.get('dir', 'desc', type=str).strip().lower()
    query = request.args.get('q', '', type=str).strip()
    candidate_office = request.args.get('candidate_office', '', type=str).strip()
    if not candidate_office:
        candidate_office = request.args.get('office', '', type=str).strip()
    candidate_party = request.args.get('candidate_party', '', type=str).strip()
    if not candidate_party:
        candidate_party = request.args.get('party', '', type=str).strip()
    match_status = request.args.get('match_status', '', type=str).strip()

    rows: list[dict] = []
    total = 0
    total_pages = 0

    if table_available:
        rows = list_federal_candidates(
            conn,
            limit=per_page,
            offset=offset,
            cycle=cycle,
            search=query,
            office=candidate_office,
            party=candidate_party,
            match_status=match_status,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        total = count_federal_candidates(
            conn,
            cycle=cycle,
            search=query,
            office=candidate_office,
            party=candidate_party,
            match_status=match_status,
        )
        total_pages = (total + per_page - 1) // per_page

    return render_template(
        'federal_finance/candidates.html',
        **_base_context('candidates', table_available, cycle, analysis_office, analysis_district),
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        query=query,
        candidate_office=candidate_office,
        candidate_party=candidate_party,
        match_status=match_status,
    )


@federal_finance_bp.route('/networks')
def federal_networks():
    """Visual network analysis page (federal and local/federal overlap)."""
    conn = current_app.get_database()
    cycle, analysis_office, analysis_district = _parse_shared_filters()
    table_available = federal_data_available(conn)

    network_min_edge_amount = max(request.args.get('network_min_edge_amount', 250.0, type=float), 0.0)
    network_limit = min(max(request.args.get('network_limit', 1500, type=int), 100), 5000)
    overlap_edge_limit = min(max(request.args.get('overlap_edge_limit', 900, type=int), 100), 5000)
    local_match_limit = min(max(request.args.get('local_match_limit', 100000, type=int), 1000), 500000)

    federal_network = {
        'nodes': [],
        'edges': [],
        'centrality': [],
        'summary': {
            'node_count': 0,
            'edge_count': 0,
            'donor_count': 0,
            'candidate_count': 0,
            'total_amount': 0.0,
        },
    }
    overlap_network = {
        'nodes': [],
        'edges': [],
        'centrality': [],
        'summary': {
            'node_count': 0,
            'edge_count': 0,
            'matched_donors': 0,
            'federal_candidate_count': 0,
            'local_committee_count': 0,
        },
        'match_summary': {
            'federal_donors_considered': 0,
            'local_donors_considered': 0,
            'federal_donors_matched': 0,
            'local_donors_matched': 0,
            'match_rate': 0.0,
            'tier_counts': {},
            'matches': [],
        },
    }

    if table_available:
        federal_network = get_federal_network_graph(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            min_edge_amount=network_min_edge_amount,
            limit=network_limit,
        )
        overlap_network = get_federal_local_overlap_network(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            min_edge_amount=network_min_edge_amount,
            edge_limit=overlap_edge_limit,
            federal_donor_limit=max(2000, network_limit),
            local_donor_limit=local_match_limit,
        )

    return render_template(
        'federal_finance/networks.html',
        **_base_context('networks', table_available, cycle, analysis_office, analysis_district),
        network_min_edge_amount=network_min_edge_amount,
        network_limit=network_limit,
        overlap_edge_limit=overlap_edge_limit,
        local_match_limit=local_match_limit,
        federal_network=federal_network,
        overlap_network=overlap_network,
    )


@federal_finance_bp.route('/donor-intelligence')
def federal_donor_intelligence():
    """Donor segmentation, clustering, influence, and multi-hop tracing."""
    conn = current_app.get_database()
    cycle, analysis_office, analysis_district = _parse_shared_filters()
    table_available = federal_data_available(conn)

    segmentation_method = request.args.get('segmentation_method', 'kmeans', type=str).strip().lower()
    if segmentation_method not in {'kmeans', 'dbscan'}:
        segmentation_method = 'kmeans'

    segmentation_donor_limit = min(max(request.args.get('segmentation_donor_limit', 3000, type=int), 100), 10000)
    segmentation_k = min(max(request.args.get('segmentation_k', 5, type=int), 2), 12)
    segmentation_dbscan_eps = min(max(request.args.get('segmentation_dbscan_eps', 1.0, type=float), 0.05), 5.0)
    segmentation_dbscan_min_samples = min(max(request.args.get('segmentation_dbscan_min_samples', 8, type=int), 2), 100)
    cluster_limit = min(max(request.args.get('cluster_limit', 1500, type=int), 100), 5000)
    min_edge_amount = max(request.args.get('network_min_edge_amount', 100.0, type=float), 0.0)

    follow_donor_key = request.args.get('follow_donor_key', '', type=str).strip()
    follow_max_hops = min(max(request.args.get('follow_max_hops', 3, type=int), 1), 8)
    follow_min_edge_amount = max(request.args.get('follow_min_edge_amount', 0.0, type=float), 0.0)

    donor_segmentation = {
        'method': segmentation_method,
        'donor_count': 0,
        'cluster_count': 0,
        'clusters': [],
        'sample_donors': [],
    }
    donor_clusters = {'cluster_count': 0, 'clusters': [], 'graph_summary': {}}
    influence = {'donors': [], 'candidates': []}
    follow_money = {
        'requested_donor_entity_key': follow_donor_key,
        'start_node_found': False,
        'nodes': [],
        'edges': [],
        'summary': {'node_count': 0, 'edge_count': 0, 'max_hops': follow_max_hops},
        'reachable_candidates': [],
        'reachable_committees': [],
        'connected_donors': [],
    }

    if table_available:
        donor_segmentation = get_federal_donor_segmentation(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            method=segmentation_method,
            donor_limit=segmentation_donor_limit,
            kmeans_k=segmentation_k,
            dbscan_eps=segmentation_dbscan_eps,
            dbscan_min_samples=segmentation_dbscan_min_samples,
        )
        donor_clusters = get_federal_donor_network_clusters(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            min_edge_amount=min_edge_amount,
            limit=cluster_limit,
        )
        influence = get_federal_influence_scores(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            min_edge_amount=min_edge_amount,
            limit=cluster_limit,
        )
        if follow_donor_key:
            follow_money = get_federal_follow_the_money(
                conn,
                donor_entity_key=follow_donor_key,
                cycle=cycle,
                office_code=analysis_office or None,
                district_code=analysis_district or None,
                max_hops=follow_max_hops,
                min_edge_amount=follow_min_edge_amount,
            )

    return render_template(
        'federal_finance/donor_intelligence.html',
        **_base_context('donor_intelligence', table_available, cycle, analysis_office, analysis_district),
        segmentation_method=segmentation_method,
        segmentation_donor_limit=segmentation_donor_limit,
        segmentation_k=segmentation_k,
        segmentation_dbscan_eps=segmentation_dbscan_eps,
        segmentation_dbscan_min_samples=segmentation_dbscan_min_samples,
        cluster_limit=cluster_limit,
        network_min_edge_amount=min_edge_amount,
        follow_donor_key=follow_donor_key,
        follow_max_hops=follow_max_hops,
        follow_min_edge_amount=follow_min_edge_amount,
        donor_segmentation=donor_segmentation,
        donor_clusters=donor_clusters,
        influence=influence,
        follow_money=follow_money,
    )


@federal_finance_bp.route('/matching')
def federal_matching():
    """Federal/local donor matching diagnostics and confidence review."""
    conn = current_app.get_database()
    cycle, analysis_office, analysis_district = _parse_shared_filters()
    table_available = federal_data_available(conn)

    local_match_limit = min(max(request.args.get('local_match_limit', 100000, type=int), 1000), 500000)
    match_limit = min(max(request.args.get('match_limit', 5000, type=int), 100), 20000)
    overlap_edge_limit = min(max(request.args.get('overlap_edge_limit', 600, type=int), 100), 5000)
    min_edge_amount = max(request.args.get('network_min_edge_amount', 100.0, type=float), 0.0)

    local_matches = {
        'federal_donors_considered': 0,
        'local_donors_considered': 0,
        'federal_donors_matched': 0,
        'local_donors_matched': 0,
        'match_rate': 0.0,
        'tier_counts': {},
        'matches': [],
    }
    overlap_summary = {
        'node_count': 0,
        'edge_count': 0,
        'matched_donors': 0,
        'federal_candidate_count': 0,
        'local_committee_count': 0,
    }

    if table_available:
        local_matches = get_federal_local_donor_matches(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            federal_donor_limit=5000,
            local_donor_limit=local_match_limit,
            match_limit=match_limit,
        )
        overlap_network = get_federal_local_overlap_network(
            conn,
            cycle=cycle,
            office_code=analysis_office or None,
            district_code=analysis_district or None,
            min_edge_amount=min_edge_amount,
            edge_limit=overlap_edge_limit,
            federal_donor_limit=5000,
            local_donor_limit=local_match_limit,
        )
        overlap_summary = overlap_network.get('summary', overlap_summary)

    return render_template(
        'federal_finance/matching.html',
        **_base_context('matching', table_available, cycle, analysis_office, analysis_district),
        local_match_limit=local_match_limit,
        match_limit=match_limit,
        overlap_edge_limit=overlap_edge_limit,
        network_min_edge_amount=min_edge_amount,
        local_matches=local_matches,
        overlap_summary=overlap_summary,
    )


@federal_finance_bp.route('/donors/<donor_entity_key>')
def federal_donor_detail(donor_entity_key: str):
    """Show one donor's contributions across federal candidates."""
    conn = current_app.get_database()

    cycle = request.args.get('cycle', 2026, type=int)
    contribution_page = max(request.args.get('contribution_page', 1, type=int), 1)
    contribution_per_page = 100
    contribution_offset = (contribution_page - 1) * contribution_per_page

    detail = get_federal_donor_detail(
        conn,
        donor_entity_key=donor_entity_key,
        cycle=cycle,
        contribution_limit=contribution_per_page,
        contribution_offset=contribution_offset,
    )
    contribution_total = detail['total_contributions'] if detail else 0
    contribution_pages = (contribution_total + contribution_per_page - 1) // contribution_per_page if detail else 0

    return render_template(
        'federal_finance/donor_detail.html',
        donor_entity_key=donor_entity_key,
        cycle=cycle,
        detail=detail,
        contribution_page=contribution_page,
        contribution_total=contribution_total,
        contribution_pages=contribution_pages,
    )


@federal_finance_bp.route('/<candidate_id>')
def federal_candidate_detail(candidate_id: str):
    """Show top donors and recent FEC Schedule A contributions for one candidate."""
    conn = current_app.get_database()

    cycle = request.args.get('cycle', 2026, type=int)
    contribution_page = max(request.args.get('contribution_page', 1, type=int), 1)
    contribution_per_page = 100
    contribution_offset = (contribution_page - 1) * contribution_per_page

    detail = get_federal_candidate_detail(
        conn,
        candidate_id=candidate_id,
        cycle=cycle,
        top_donor_limit=25,
        contribution_limit=contribution_per_page,
        contribution_offset=contribution_offset,
    )

    contribution_total = detail['total_contributions'] if detail else 0
    contribution_pages = (contribution_total + contribution_per_page - 1) // contribution_per_page if detail else 0

    return render_template(
        'federal_finance/detail.html',
        candidate_id=candidate_id,
        cycle=cycle,
        detail=detail,
        contribution_page=contribution_page,
        contribution_total=contribution_total,
        contribution_pages=contribution_pages,
    )
