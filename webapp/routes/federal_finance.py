"""Federal candidate finance routes (FEC data)."""
from __future__ import annotations

import csv
from io import StringIO

from flask import Blueprint, Response, current_app, render_template, request

from database.models import Donor
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
    get_federal_multilayer_network_graph,
    get_federal_cross_role_organizations,
    get_federal_local_donor_matches,
    get_federal_local_overlap_network,
    get_federal_network_graph,
    get_federal_race_analytics,
    get_federal_view_snapshot,
    list_federal_candidates,
    save_federal_view_snapshot,
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


def _federal_cache_enabled() -> bool:
    return bool(current_app.config.get('FEDERAL_VIEW_CACHE_ENABLED', True))


def _federal_cache_ttl(config_key: str, default_seconds: int) -> int:
    return max(30, int(current_app.config.get(config_key, default_seconds)))


def _federal_cache_refresh_requested() -> bool:
    return request.args.get('refresh_cache', 0, type=int) == 1


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _scalar(conn, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return default
    if not row:
        return default
    keys = row.keys() if hasattr(row, "keys") else []
    if not keys:
        return default
    value = row[keys[0]]
    return default if value is None else value


def _federal_schedule_b_e_metrics(conn, *, cycle: int, analysis_office: str, analysis_district: str) -> dict:
    """Return filtered Schedule B/E row counts and totals for federal overview cards."""
    metrics = {
        'schedule_b_count': 0,
        'schedule_b_total': 0.0,
        'schedule_e_count': 0,
        'schedule_e_total': 0.0,
    }

    if not _table_exists(conn, "fec_candidate_match"):
        return metrics

    district_value = (analysis_district or '').strip()
    if _table_exists(conn, "fec_schedule_b_disbursements"):
        row = conn.execute(
            """
            WITH candidate_scope AS (
                SELECT DISTINCT fec_candidate_id AS candidate_id, cycle
                FROM fec_candidate_match
                WHERE cycle = ?
                  AND fec_candidate_id IS NOT NULL
                  AND (? = '' OR office_code = ? OR office = ?)
                  AND (? = '' OR district_code = ? OR district = ?)
            )
            SELECT
                COUNT(*) AS disbursement_count,
                COALESCE(SUM(sb.disbursement_amount), 0.0) AS disbursement_total
            FROM fec_schedule_b_disbursements sb
            JOIN candidate_scope cs
              ON cs.candidate_id = sb.candidate_id
             AND cs.cycle = sb.cycle
            """,
            (cycle, analysis_office, analysis_office, analysis_office, district_value, district_value, district_value),
        ).fetchone()
        if row:
            metrics['schedule_b_count'] = int(row["disbursement_count"] or 0)
            metrics['schedule_b_total'] = float(row["disbursement_total"] or 0.0)

    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        row = conn.execute(
            """
            WITH candidate_scope AS (
                SELECT DISTINCT fec_candidate_id AS candidate_id, cycle
                FROM fec_candidate_match
                WHERE cycle = ?
                  AND fec_candidate_id IS NOT NULL
                  AND (? = '' OR office_code = ? OR office = ?)
                  AND (? = '' OR district_code = ? OR district = ?)
            )
            SELECT
                COUNT(*) AS expenditure_count,
                COALESCE(SUM(se.expenditure_amount), 0.0) AS expenditure_total
            FROM fec_schedule_e_independent_expenditures se
            JOIN candidate_scope cs
              ON cs.candidate_id = se.candidate_id
             AND cs.cycle = se.cycle
            """,
            (cycle, analysis_office, analysis_office, analysis_office, district_value, district_value, district_value),
        ).fetchone()
        if row:
            metrics['schedule_e_count'] = int(row["expenditure_count"] or 0)
            metrics['schedule_e_total'] = float(row["expenditure_total"] or 0.0)

    return metrics


def _csv_response(rows: list[list], headers: list[str], filename: str) -> Response:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


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
        'schedule_b_count': 0,
        'schedule_b_total': 0.0,
        'schedule_e_count': 0,
        'schedule_e_total': 0.0,
    }
    race_analytics: list[dict] = []
    geographic = {'states': [], 'cities': [], 'race_concentration': []}
    top_donors: list[dict] = []
    top_candidates: list[dict] = []

    cache_status = None
    if table_available:
        payload = None
        cache_params = {
            'cycle': cycle,
            'analysis_office': analysis_office or '',
            'analysis_district': analysis_district or '',
            'version': 2,
        }
        if _federal_cache_enabled() and not _federal_cache_refresh_requested():
            cache_status = get_federal_view_snapshot(
                conn,
                snapshot_type='federal_overview',
                params=cache_params,
                ttl_seconds=_federal_cache_ttl('FEDERAL_OVERVIEW_CACHE_TTL_SECONDS', 900),
            )
            if cache_status.get('is_fresh') and cache_status.get('payload'):
                payload = cache_status.get('payload')

        if payload is None:
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
            overview.update(
                _federal_schedule_b_e_metrics(
                    conn,
                    cycle=cycle,
                    analysis_office=analysis_office,
                    analysis_district=analysis_district,
                )
            )
            top_donors = [row for row in network_snapshot.get('centrality', []) if row.get('node_type') == 'donor'][:10]
            top_candidates = [row for row in network_snapshot.get('centrality', []) if row.get('node_type') == 'candidate'][:10]

            payload = {
                'overview': overview,
                'race_analytics': race_analytics,
                'geographic': geographic,
                'top_donors': top_donors,
                'top_candidates': top_candidates,
            }
            if _federal_cache_enabled():
                cache_status = save_federal_view_snapshot(
                    conn,
                    snapshot_type='federal_overview',
                    params=cache_params,
                    status='completed',
                    payload=payload,
                )
        else:
            overview = payload.get('overview', overview)
            race_analytics = payload.get('race_analytics', race_analytics)
            geographic = payload.get('geographic', geographic)
            top_donors = payload.get('top_donors', top_donors)
            top_candidates = payload.get('top_candidates', top_candidates)

    return render_template(
        'federal_finance/overview.html',
        **_base_context('overview', table_available, cycle, analysis_office, analysis_district),
        overview=overview,
        race_analytics=race_analytics,
        geographic=geographic,
        top_donors=top_donors,
        top_candidates=top_candidates,
        cache_status=cache_status,
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
    multilayer_network = {
        'nodes': [],
        'edges': [],
        'centrality': [],
        'summary': {
            'node_count': 0,
            'edge_count': 0,
            'donor_count': 0,
            'candidate_count': 0,
            'candidate_committee_count': 0,
            'vendor_count': 0,
            'ie_committee_count': 0,
            'total_amount': 0.0,
            'donor_candidate_total_amount': 0.0,
            'committee_vendor_total_amount': 0.0,
            'ie_committee_candidate_total_amount': 0.0,
        },
        'layer_summary': [],
    }
    cross_role_orgs = {
        'rows': [],
        'summary': {
            'matched_organization_count': 0,
            'total_donor_amount': 0.0,
            'total_out_amount': 0.0,
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

    cache_status = None
    if table_available:
        payload = None
        cache_params = {
            'cycle': cycle,
            'analysis_office': analysis_office or '',
            'analysis_district': analysis_district or '',
            'network_min_edge_amount': round(network_min_edge_amount, 2),
            'network_limit': network_limit,
            'overlap_edge_limit': overlap_edge_limit,
            'local_match_limit': local_match_limit,
            'version': 2,
        }
        if _federal_cache_enabled() and not _federal_cache_refresh_requested():
            cache_status = get_federal_view_snapshot(
                conn,
                snapshot_type='federal_networks',
                params=cache_params,
                ttl_seconds=_federal_cache_ttl('FEDERAL_NETWORKS_CACHE_TTL_SECONDS', 600),
            )
            if cache_status.get('is_fresh') and cache_status.get('payload'):
                payload = cache_status.get('payload')

        if payload is None:
            federal_network = get_federal_network_graph(
                conn,
                cycle=cycle,
                office_code=analysis_office or None,
                district_code=analysis_district or None,
                min_edge_amount=network_min_edge_amount,
                limit=network_limit,
            )
            multilayer_network = get_federal_multilayer_network_graph(
                conn,
                cycle=cycle,
                office_code=analysis_office or None,
                district_code=analysis_district or None,
                min_edge_amount=network_min_edge_amount,
                limit=network_limit,
            )
            cross_role_orgs = get_federal_cross_role_organizations(
                conn,
                cycle=cycle,
                office_code=analysis_office or None,
                district_code=analysis_district or None,
                limit=60,
                min_total_amount=network_min_edge_amount,
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
            payload = {
                'federal_network': federal_network,
                'multilayer_network': multilayer_network,
                'cross_role_orgs': cross_role_orgs,
                'overlap_network': overlap_network,
            }
            if _federal_cache_enabled():
                cache_status = save_federal_view_snapshot(
                    conn,
                    snapshot_type='federal_networks',
                    params=cache_params,
                    status='completed',
                    payload=payload,
                )
        else:
            federal_network = payload.get('federal_network', federal_network)
            multilayer_network = payload.get('multilayer_network', multilayer_network)
            cross_role_orgs = payload.get('cross_role_orgs', cross_role_orgs)
            overlap_network = payload.get('overlap_network', overlap_network)

    return render_template(
        'federal_finance/networks.html',
        **_base_context('networks', table_available, cycle, analysis_office, analysis_district),
        network_min_edge_amount=network_min_edge_amount,
        network_limit=network_limit,
        overlap_edge_limit=overlap_edge_limit,
        local_match_limit=local_match_limit,
        federal_network=federal_network,
        multilayer_network=multilayer_network,
        cross_role_orgs=cross_role_orgs,
        overlap_network=overlap_network,
        cache_status=cache_status,
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

    cache_status = None
    if table_available:
        payload = None
        cache_allowed = _federal_cache_enabled() and not follow_donor_key
        cache_params = {
            'cycle': cycle,
            'analysis_office': analysis_office or '',
            'analysis_district': analysis_district or '',
            'segmentation_method': segmentation_method,
            'segmentation_donor_limit': segmentation_donor_limit,
            'segmentation_k': segmentation_k,
            'segmentation_dbscan_eps': round(segmentation_dbscan_eps, 4),
            'segmentation_dbscan_min_samples': segmentation_dbscan_min_samples,
            'cluster_limit': cluster_limit,
            'network_min_edge_amount': round(min_edge_amount, 2),
            'version': 1,
        }
        if cache_allowed and not _federal_cache_refresh_requested():
            cache_status = get_federal_view_snapshot(
                conn,
                snapshot_type='federal_donor_intelligence',
                params=cache_params,
                ttl_seconds=_federal_cache_ttl('FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS', 600),
            )
            if cache_status.get('is_fresh') and cache_status.get('payload'):
                payload = cache_status.get('payload')

        if payload is None:
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
            if cache_allowed:
                payload = {
                    'donor_segmentation': donor_segmentation,
                    'donor_clusters': donor_clusters,
                    'influence': influence,
                }
                cache_status = save_federal_view_snapshot(
                    conn,
                    snapshot_type='federal_donor_intelligence',
                    params=cache_params,
                    status='completed',
                    payload=payload,
                )
        else:
            donor_segmentation = payload.get('donor_segmentation', donor_segmentation)
            donor_clusters = payload.get('donor_clusters', donor_clusters)
            influence = payload.get('influence', influence)

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
        cache_status=cache_status,
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

    cache_status = None
    if table_available:
        payload = None
        cache_params = {
            'cycle': cycle,
            'analysis_office': analysis_office or '',
            'analysis_district': analysis_district or '',
            'local_match_limit': local_match_limit,
            'match_limit': match_limit,
            'overlap_edge_limit': overlap_edge_limit,
            'network_min_edge_amount': round(min_edge_amount, 2),
            'version': 1,
        }
        if _federal_cache_enabled() and not _federal_cache_refresh_requested():
            cache_status = get_federal_view_snapshot(
                conn,
                snapshot_type='federal_matching',
                params=cache_params,
                ttl_seconds=_federal_cache_ttl('FEDERAL_MATCHING_CACHE_TTL_SECONDS', 600),
            )
            if cache_status.get('is_fresh') and cache_status.get('payload'):
                payload = cache_status.get('payload')

        if payload is None:
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
            if _federal_cache_enabled():
                cache_status = save_federal_view_snapshot(
                    conn,
                    snapshot_type='federal_matching',
                    params=cache_params,
                    status='completed',
                    payload={
                        'local_matches': local_matches,
                        'overlap_summary': overlap_summary,
                    },
                )
        else:
            local_matches = payload.get('local_matches', local_matches)
            overlap_summary = payload.get('overlap_summary', overlap_summary)

    return render_template(
        'federal_finance/matching.html',
        **_base_context('matching', table_available, cycle, analysis_office, analysis_district),
        local_match_limit=local_match_limit,
        match_limit=match_limit,
        overlap_edge_limit=overlap_edge_limit,
        network_min_edge_amount=min_edge_amount,
        local_matches=local_matches,
        overlap_summary=overlap_summary,
        cache_status=cache_status,
    )


@federal_finance_bp.route('/matches/<federal_donor_entity_key>/<path:local_donor_key>')
def federal_matched_donor_profile(federal_donor_entity_key: str, local_donor_key: str):
    """Show a combined profile view for one matched federal/local donor pair."""
    conn = current_app.get_database()
    cycle, analysis_office, analysis_district = _parse_shared_filters()
    table_available = federal_data_available(conn)

    local_source = request.args.get('local_source', 'bulk_receipts', type=str).strip() or 'bulk_receipts'
    match_method = request.args.get('match_method', '', type=str).strip()
    confidence_score = request.args.get('confidence', type=float)
    local_donor_keys_arg = (request.args.get('local_donor_keys', '', type=str) or '').strip()

    federal_page = max(request.args.get('federal_page', 1, type=int), 1)
    local_page = max(request.args.get('local_page', 1, type=int), 1)
    federal_per_page = 100
    local_per_page = 50
    federal_offset = (federal_page - 1) * federal_per_page
    local_offset = (local_page - 1) * local_per_page

    federal_detail = None
    local_donor = None
    local_committees: list[dict] = []
    federal_total = 0
    federal_pages = 0
    local_committee_count = 0
    local_pages = 0
    local_donor_keys: list[str] = []

    seen_local_keys: set[str] = set()
    for key in [local_donor_key] + [part.strip() for part in local_donor_keys_arg.split(',') if part.strip()]:
        normalized = key.strip()
        if not normalized or normalized in seen_local_keys:
            continue
        seen_local_keys.add(normalized)
        local_donor_keys.append(normalized)

    if table_available:
        federal_detail = get_federal_donor_detail(
            conn,
            donor_entity_key=federal_donor_entity_key,
            cycle=cycle,
            contribution_limit=federal_per_page,
            contribution_offset=federal_offset,
        )

        local_rows = []
        for key in local_donor_keys:
            row = Donor.get_summary_by_key(conn, donor_key=key, source=local_source)
            if row:
                local_rows.append(row)

        if local_rows:
            primary = max(local_rows, key=lambda row: float(row.total_amount or 0.0))
            local_donor = primary
            local_donor.total_amount = round(sum(float(row.total_amount or 0.0) for row in local_rows), 2)
            local_donor.contribution_count = sum(int(row.contribution_count or 0) for row in local_rows)
            local_donor.matched_variant_count = len(local_rows)
            local_donor.matched_local_keys = [row.donor_key for row in local_rows if row.donor_key]

            placeholders = ",".join(["?"] * len(local_donor_keys))
            committee_rows = conn.execute(
                f"""
                SELECT
                    committee_id,
                    committee_name,
                    COALESCE(SUM(total_amount), 0) AS total_amount,
                    COALESCE(SUM(contribution_count), 0) AS contribution_count
                FROM analytics_donor_committee_agg
                WHERE source = ?
                  AND donor_key IN ({placeholders})
                GROUP BY committee_id, committee_name
                ORDER BY total_amount DESC, committee_name ASC
                LIMIT ? OFFSET ?
                """,
                [local_source, *local_donor_keys, local_per_page, local_offset],
            ).fetchall()
            local_committees = [
                {
                    'committee_id': row['committee_id'],
                    'committee_name': row['committee_name'],
                    'total_amount': float(row['total_amount'] or 0.0),
                    'contribution_count': int(row['contribution_count'] or 0),
                }
                for row in committee_rows
            ]

            count_row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM (
                    SELECT committee_id
                    FROM analytics_donor_committee_agg
                    WHERE source = ?
                      AND donor_key IN ({placeholders})
                    GROUP BY committee_id
                ) x
                """,
                [local_source, *local_donor_keys],
            ).fetchone()
            local_committee_count = int(count_row['count'] or 0) if count_row else 0
            local_donor.committee_count = local_committee_count
            local_pages = (local_committee_count + local_per_page - 1) // local_per_page if local_committee_count else 0

        if federal_detail:
            federal_total = int(federal_detail.get('total_contributions') or 0)
            federal_pages = (federal_total + federal_per_page - 1) // federal_per_page if federal_total else 0

    return render_template(
        'federal_finance/match_profile.html',
        **_base_context('matching', table_available, cycle, analysis_office, analysis_district),
        federal_donor_entity_key=federal_donor_entity_key,
        local_donor_key=local_donor_key,
        local_donor_keys=local_donor_keys,
        local_source=local_source,
        match_method=match_method,
        confidence_score=confidence_score,
        federal_detail=federal_detail,
        local_donor=local_donor,
        local_committees=local_committees,
        federal_page=federal_page,
        federal_total=federal_total,
        federal_pages=federal_pages,
        local_page=local_page,
        local_committee_count=local_committee_count,
        local_pages=local_pages,
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
    output_format = request.args.get('format', 'html', type=str).strip().lower()
    export_table = request.args.get('table', '', type=str).strip().lower()
    contribution_page = max(request.args.get('contribution_page', 1, type=int), 1)
    schedule_b_page = max(request.args.get('schedule_b_page', 1, type=int), 1)
    schedule_e_page = max(request.args.get('schedule_e_page', 1, type=int), 1)
    schedule_b_sort = request.args.get('schedule_b_sort', 'date', type=str).strip().lower()
    schedule_b_dir = request.args.get('schedule_b_dir', 'desc', type=str).strip().lower()
    schedule_e_sort = request.args.get('schedule_e_sort', 'date', type=str).strip().lower()
    schedule_e_dir = request.args.get('schedule_e_dir', 'desc', type=str).strip().lower()
    if schedule_b_sort not in {'date', 'amount', 'recipient', 'committee', 'type', 'category'}:
        schedule_b_sort = 'date'
    if schedule_b_dir not in {'asc', 'desc'}:
        schedule_b_dir = 'desc'
    if schedule_e_sort not in {'date', 'amount', 'support_oppose', 'committee', 'payee', 'category'}:
        schedule_e_sort = 'date'
    if schedule_e_dir not in {'asc', 'desc'}:
        schedule_e_dir = 'desc'
    contribution_per_page = 100
    schedule_b_per_page = 100
    schedule_e_per_page = 100
    contribution_offset = (contribution_page - 1) * contribution_per_page
    schedule_b_offset = (schedule_b_page - 1) * schedule_b_per_page
    schedule_e_offset = (schedule_e_page - 1) * schedule_e_per_page

    detail = get_federal_candidate_detail(
        conn,
        candidate_id=candidate_id,
        cycle=cycle,
        top_donor_limit=25,
        contribution_limit=contribution_per_page,
        contribution_offset=contribution_offset,
        schedule_b_limit=schedule_b_per_page,
        schedule_b_offset=schedule_b_offset,
        schedule_e_limit=schedule_e_per_page,
        schedule_e_offset=schedule_e_offset,
        schedule_b_sort=schedule_b_sort,
        schedule_b_dir=schedule_b_dir,
        schedule_e_sort=schedule_e_sort,
        schedule_e_dir=schedule_e_dir,
    )

    contribution_total = detail['total_contributions'] if detail else 0
    contribution_pages = (contribution_total + contribution_per_page - 1) // contribution_per_page if detail else 0
    schedule_b_total = detail['total_schedule_b_disbursements'] if detail else 0
    schedule_b_pages = (schedule_b_total + schedule_b_per_page - 1) // schedule_b_per_page if detail else 0
    schedule_e_total = detail['total_schedule_e_expenditures'] if detail else 0
    schedule_e_pages = (schedule_e_total + schedule_e_per_page - 1) // schedule_e_per_page if detail else 0

    if output_format == 'csv':
        if not detail:
            return Response("federal candidate detail unavailable\n", mimetype='text/plain', status=404)

        if export_table == 'schedule_b':
            schedule_b_rows = get_federal_candidate_detail(
                conn,
                candidate_id=candidate_id,
                cycle=cycle,
                top_donor_limit=1,
                contribution_limit=1,
                contribution_offset=0,
                schedule_b_limit=500000,
                schedule_b_offset=0,
                schedule_e_limit=1,
                schedule_e_offset=0,
                schedule_b_sort=schedule_b_sort,
                schedule_b_dir=schedule_b_dir,
                schedule_e_sort=schedule_e_sort,
                schedule_e_dir=schedule_e_dir,
            )["schedule_b_disbursements"]
            csv_rows = [
                [
                    row.get('sub_id'),
                    cycle,
                    candidate_id,
                    row.get('committee_id'),
                    row.get('committee_name'),
                    row.get('disbursement_date'),
                    row.get('recipient_name'),
                    row.get('recipient_city'),
                    row.get('recipient_state'),
                    row.get('recipient_zip'),
                    row.get('recipient_candidate_id'),
                    row.get('recipient_candidate_name'),
                    row.get('disbursement_type'),
                    row.get('disbursement_type_desc'),
                    row.get('category_code'),
                    row.get('category_code_full'),
                    row.get('disbursement_amount'),
                    row.get('memo_text'),
                ]
                for row in schedule_b_rows
            ]
            return _csv_response(
                csv_rows,
                [
                    'sub_id',
                    'cycle',
                    'candidate_id',
                    'committee_id',
                    'committee_name',
                    'disbursement_date',
                    'recipient_name',
                    'recipient_city',
                    'recipient_state',
                    'recipient_zip',
                    'recipient_candidate_id',
                    'recipient_candidate_name',
                    'disbursement_type',
                    'disbursement_type_desc',
                    'category_code',
                    'category_code_full',
                    'disbursement_amount',
                    'memo_text',
                ],
                filename=f"federal_candidate_{candidate_id}_schedule_b.csv",
            )

        if export_table == 'schedule_e':
            schedule_e_rows = get_federal_candidate_detail(
                conn,
                candidate_id=candidate_id,
                cycle=cycle,
                top_donor_limit=1,
                contribution_limit=1,
                contribution_offset=0,
                schedule_b_limit=1,
                schedule_b_offset=0,
                schedule_e_limit=500000,
                schedule_e_offset=0,
                schedule_b_sort=schedule_b_sort,
                schedule_b_dir=schedule_b_dir,
                schedule_e_sort=schedule_e_sort,
                schedule_e_dir=schedule_e_dir,
            )["schedule_e_independent_expenditures"]
            csv_rows = [
                [
                    row.get('sub_id'),
                    cycle,
                    candidate_id,
                    row.get('expenditure_date'),
                    row.get('support_oppose_indicator'),
                    row.get('committee_id'),
                    row.get('committee_name'),
                    row.get('payee_name'),
                    row.get('payee_city'),
                    row.get('payee_state'),
                    row.get('payee_zip'),
                    row.get('category_code'),
                    row.get('category_code_full'),
                    row.get('report_type'),
                    row.get('line_number'),
                    row.get('expenditure_amount'),
                    row.get('expenditure_description'),
                    row.get('memo_text'),
                ]
                for row in schedule_e_rows
            ]
            return _csv_response(
                csv_rows,
                [
                    'sub_id',
                    'cycle',
                    'candidate_id',
                    'expenditure_date',
                    'support_oppose_indicator',
                    'committee_id',
                    'committee_name',
                    'payee_name',
                    'payee_city',
                    'payee_state',
                    'payee_zip',
                    'category_code',
                    'category_code_full',
                    'report_type',
                    'line_number',
                    'expenditure_amount',
                    'expenditure_description',
                    'memo_text',
                ],
                filename=f"federal_candidate_{candidate_id}_schedule_e.csv",
            )

        return Response("unsupported csv export table\n", mimetype='text/plain', status=400)

    return render_template(
        'federal_finance/detail.html',
        candidate_id=candidate_id,
        cycle=cycle,
        detail=detail,
        contribution_page=contribution_page,
        contribution_total=contribution_total,
        contribution_pages=contribution_pages,
        schedule_b_page=schedule_b_page,
        schedule_b_total=schedule_b_total,
        schedule_b_pages=schedule_b_pages,
        schedule_b_sort=schedule_b_sort,
        schedule_b_dir=schedule_b_dir,
        schedule_e_page=schedule_e_page,
        schedule_e_total=schedule_e_total,
        schedule_e_pages=schedule_e_pages,
        schedule_e_sort=schedule_e_sort,
        schedule_e_dir=schedule_e_dir,
    )
