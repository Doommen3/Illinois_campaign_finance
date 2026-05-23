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
from webapp.utils.search_normalize import normalize_search_query
from webapp.utils.time_filter import get_active_period, period_to_date_window

api_bp = Blueprint('api', __name__)


def _resolved_date_window() -> tuple[str | None, str | None]:
    period = get_active_period()
    explicit_from = (request.args.get('date_from', '', type=str) or '').strip()
    explicit_to = (request.args.get('date_to', '', type=str) or '').strip()
    return period_to_date_window(period, explicit_from, explicit_to)


def _public_error_marker(error_message: str | None) -> str | None:
    """Return a non-sensitive error marker for public API responses."""
    return "internal_error" if (error_message or "").strip() else None


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
            'error': _public_error_marker(main_list_state.error_message) if main_list_state else None,
            'has_error': bool((main_list_state.error_message or "").strip()) if main_list_state else False,
        } if main_list_state else None,
        'details': {
            'status': details_state.status if details_state else None,
            'last_report_id': details_state.last_report_id if details_state else None,
            'started_at': str(details_state.started_at) if details_state else None,
            'completed_at': str(details_state.completed_at) if details_state else None,
            'error': _public_error_marker(details_state.error_message) if details_state else None,
            'has_error': bool((details_state.error_message or "").strip()) if details_state else False,
        } if details_state else None,
        'report_status': Report.count_by_status(conn)
    })


@api_bp.route('/analytics/network')
def analytics_network():
    """Get donor->committee->candidate network analytics."""
    conn = current_app.get_database()
    min_edge_amount = request.args.get('min_edge_amount', 1000, type=float)
    limit = min(request.args.get('limit', 200, type=int), 2000)
    date_from, date_to = _resolved_date_window()
    return jsonify(
        get_network_graph(
            conn,
            min_edge_amount=min_edge_amount,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
        )
    )


@api_bp.route('/analytics/anomalies')
def analytics_anomalies():
    """Get anomaly/risk flags."""
    conn = current_app.get_database()
    limit = min(request.args.get('limit', 25, type=int), 1000)
    date_from, date_to = _resolved_date_window()
    return jsonify({'data': get_anomaly_flags(conn, limit=limit, date_from=date_from, date_to=date_to)})


@api_bp.route('/analytics/concentration')
def analytics_concentration():
    """Get donor concentration metrics by committee."""
    conn = current_app.get_database()
    limit = min(request.args.get('limit', 25, type=int), 1000)
    date_from, date_to = _resolved_date_window()
    return jsonify(
        {
            'data': get_donor_concentration(
                conn,
                limit=limit,
                date_from=date_from,
                date_to=date_to,
            )
        }
    )


@api_bp.route('/analytics/time-series')
def analytics_time_series():
    """Get monthly contribution time series."""
    conn = current_app.get_database()
    months = min(request.args.get('months', 24, type=int), 120)
    date_from, date_to = _resolved_date_window()
    return jsonify({'data': get_time_series(conn, months=months, date_from=date_from, date_to=date_to)})


@api_bp.route('/analytics/geo')
def analytics_geo():
    """Get state/city contribution geography summary."""
    conn = current_app.get_database()
    state_limit = min(request.args.get('state_limit', 15, type=int), 100)
    city_limit = min(request.args.get('city_limit', 25, type=int), 500)
    date_from, date_to = _resolved_date_window()
    return jsonify(
        get_geo_summary(
            conn,
            limit_states=state_limit,
            limit_cities=city_limit,
            date_from=date_from,
            date_to=date_to,
        )
    )


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
    date_from, date_to = _resolved_date_window()
    return jsonify(
        get_lobbying_influence_graph(
            conn,
            client_limit=client_limit,
            edge_limit=edge_limit,
            date_from=date_from,
            date_to=date_to,
        )
    )


@api_bp.route('/analytics/irs527-ecosystem')
def analytics_irs527_ecosystem():
    """Get IRS 527 ecosystem graph."""
    conn = current_app.get_database()
    org_limit = min(max(request.args.get('org_limit', 100, type=int), 20), 2000)
    edge_limit = min(max(request.args.get('edge_limit', 200, type=int), 50), 10000)
    date_from, date_to = _resolved_date_window()
    return jsonify(
        get_irs527_ecosystem_graph(
            conn,
            org_limit=org_limit,
            edge_limit=edge_limit,
            date_from=date_from,
            date_to=date_to,
        )
    )


# ---------------------------------------------------------------------------
# Custom Sankey: suggest + focus-sankey
# ---------------------------------------------------------------------------

def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _stable_entity_key(name: str) -> str:
    """Deterministic short key from an entity name (matches analytics.py convention)."""
    import hashlib
    return hashlib.sha1((name or "").strip().lower().encode()).hexdigest()[:16]


@api_bp.route('/analytics/network/suggest')
def analytics_network_suggest():
    """Autocomplete suggestions across donors, committees, candidates, vendors."""
    conn = current_app.get_database()
    raw = request.args.get('q', '').strip()
    q = normalize_search_query(raw)
    limit = min(request.args.get('limit', 10, type=int), 50)
    entity_type = request.args.get('entity_type', 'all').strip().lower()

    if len(q) < 2:
        return jsonify([])

    results = []
    pattern = f"%{q}%"

    # --- donors ---
    if entity_type in ('all', 'donor'):
        donor_found = False
        if _table_exists(conn, 'analytics_donor_summary'):
            rows = conn.execute(
                "SELECT donor_key, donor_name FROM analytics_donor_summary "
                "WHERE LOWER(donor_name) LIKE LOWER(?) ORDER BY total_amount DESC LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                donor_found = True
                results.append({
                    "label": r["donor_name"],
                    "value": f"donor:{r['donor_key']}",
                    "node_type": "donor",
                })
        if not donor_found and _table_exists(conn, 'donors'):
            rows = conn.execute(
                "SELECT id, name FROM donors WHERE LOWER(name) LIKE LOWER(?) ORDER BY name LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                results.append({
                    "label": r["name"],
                    "value": f"donor:{r['id']}",
                    "node_type": "donor",
                })

    # --- committees ---
    if entity_type in ('all', 'committee'):
        cmte_found = False
        if _table_exists(conn, 'isbe_committees'):
            rows = conn.execute(
                "SELECT id, name FROM isbe_committees WHERE LOWER(name) LIKE LOWER(?) ORDER BY name LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                cmte_found = True
                results.append({
                    "label": r["name"],
                    "value": f"committee:{r['id']}",
                    "node_type": "committee",
                })
        if not cmte_found and _table_exists(conn, 'bulk_committees_clean'):
            rows = conn.execute(
                "SELECT committee_id_sbe AS id, committee_name AS name FROM bulk_committees_clean "
                "WHERE LOWER(committee_name) LIKE LOWER(?) ORDER BY committee_name LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                cmte_found = True
                results.append({
                    "label": r["name"],
                    "value": f"committee:{r['id']}",
                    "node_type": "committee",
                })
        if not cmte_found and _table_exists(conn, 'committees'):
            rows = conn.execute(
                "SELECT id, name FROM committees WHERE LOWER(name) LIKE LOWER(?) ORDER BY name LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                results.append({
                    "label": r["name"],
                    "value": f"committee:{r['id']}",
                    "node_type": "committee",
                })

    # --- candidates ---
    if entity_type in ('all', 'candidate'):
        cand_found = False
        if _table_exists(conn, 'bulk_candidates_clean'):
            rows = conn.execute(
                "SELECT DISTINCT candidate_id, candidate_full_name FROM bulk_candidates_clean "
                "WHERE LOWER(candidate_full_name) LIKE LOWER(?) ORDER BY candidate_full_name LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                cand_found = True
                results.append({
                    "label": r["candidate_full_name"],
                    "value": f"candidate:{r['candidate_id']}",
                    "node_type": "candidate",
                })
        if not cand_found and _table_exists(conn, 'bulk_candidate_committee_finance_agg'):
            rows = conn.execute(
                "SELECT DISTINCT candidate_id, candidate_full_name FROM bulk_candidate_committee_finance_agg "
                "WHERE LOWER(candidate_full_name) LIKE LOWER(?) ORDER BY candidate_full_name LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                results.append({
                    "label": r["candidate_full_name"],
                    "value": f"candidate:{r['candidate_id']}",
                    "node_type": "candidate",
                })

    # --- vendors ---
    if entity_type in ('all', 'vendor'):
        if _table_exists(conn, 'bulk_expenditures_clean'):
            rows = conn.execute(
                "SELECT payee_last_or_business_name AS name, SUM(amount) AS total "
                "FROM bulk_expenditures_clean "
                "WHERE LOWER(payee_last_or_business_name) LIKE LOWER(?) AND amount > 0 "
                "GROUP BY payee_last_or_business_name "
                "ORDER BY total DESC LIMIT ?",
                (pattern, limit),
            ).fetchall()
            for r in rows:
                vname = r["name"]
                results.append({
                    "label": vname,
                    "value": f"vendor:{_stable_entity_key(vname)}",
                    "node_type": "vendor",
                })

    # Deduplicate by value, keep first occurrence, truncate to limit
    seen = set()
    deduped = []
    for item in results:
        if item["value"] not in seen:
            seen.add(item["value"])
            deduped.append(item)
        if len(deduped) >= limit:
            break

    return jsonify(deduped)


@api_bp.route('/analytics/network/focus-sankey')
def analytics_network_focus_sankey():
    """Build a focused subgraph around a single entity for Custom Sankey rendering."""
    conn = current_app.get_database()

    focus_node_id = (request.args.get('focus_node_id', '') or '').strip()
    focus_label = (request.args.get('focus_label', '') or '').strip()
    focus_type = (request.args.get('focus_type', 'auto') or 'auto').strip().lower()
    date_from = (request.args.get('date_from', '') or '').strip() or None
    date_to = (request.args.get('date_to', '') or '').strip() or None
    min_edge_amount = max(request.args.get('min_edge_amount', 0, type=float), 0)
    committee_limit = min(request.args.get('committee_limit', 20, type=int), 100)
    candidate_limit = min(request.args.get('candidate_limit', 15, type=int), 100)
    vendor_limit = min(request.args.get('vendor_limit', 15, type=int), 100)
    donor_limit = min(request.args.get('donor_limit', 20, type=int), 100)

    # Resolve focus type from node_id prefix when auto
    if focus_type == 'auto' and ':' in focus_node_id:
        prefix = focus_node_id.split(':', 1)[0]
        if prefix in ('donor', 'committee', 'candidate', 'vendor'):
            focus_type = prefix

    if not focus_node_id and not focus_label:
        return jsonify({"error": "focus_node_id or focus_label required"}), 400

    # Auto-detect: when we only have a label, probe tables to pick the best type
    if focus_type == 'auto' and focus_label:
        for probe_type, probe_table, probe_col in [
            ('committee', 'isbe_committees', 'name'),
            ('committee', 'bulk_committees_clean', 'committee_name'),
            ('candidate', 'bulk_candidates_clean', 'candidate_full_name'),
            ('candidate', 'bulk_candidate_committee_finance_agg', 'candidate_full_name'),
            ('donor', 'analytics_donor_summary', 'donor_name'),
            ('donor', 'bulk_receipts_clean', None),  # handled below
            ('vendor', 'bulk_expenditures_clean', 'payee_last_or_business_name'),
        ]:
            if not _table_exists(conn, probe_table):
                continue
            if probe_col is None:
                # receipts: detect column dynamically
                probe_col = 'last_or_business_name' if _table_exists(conn, 'isbe_condensed_receipts') else 'contributed_by'
                if not _table_exists(conn, probe_table):
                    continue
            row = conn.execute(
                f"SELECT 1 FROM {probe_table} WHERE LOWER({probe_col}) = LOWER(?) LIMIT 1",
                (focus_label,),
            ).fetchone()
            if row:
                focus_type = probe_type
                break
        # If still auto after probes, default to donor (most common search intent)
        if focus_type == 'auto':
            focus_type = 'donor'

    nodes_map = {}   # id -> {id, label, node_type}
    edges_list = []  # [{source, target, weight, edge_type, count}]
    summary_flags = {
        "has_donor_data": False,
        "has_committee_data": False,
        "has_candidate_data": False,
        "has_vendor_data": False,
    }

    focus_key = focus_node_id.split(':', 1)[1] if ':' in focus_node_id else ''
    resolved_focus_id = focus_node_id

    def _add_node(nid, label, ntype):
        if nid not in nodes_map:
            nodes_map[nid] = {"id": nid, "label": label, "node_type": ntype}

    def _add_edge(src, tgt, weight, etype, count=1):
        if weight >= min_edge_amount:
            edges_list.append({
                "source": src, "target": tgt,
                "weight": float(weight), "edge_type": etype, "count": count,
            })

    # Date filter clause builder
    def _date_clause(col, prefix="AND"):
        parts = []
        params = []
        if date_from:
            parts.append(f"{col} >= ?")
            params.append(date_from)
        if date_to:
            parts.append(f"{col} <= ?")
            params.append(date_to)
        if parts:
            return f" {prefix} " + " AND ".join(parts), params
        return "", []

    def _bulk_receipts_columns() -> set[str]:
        if not _table_exists(conn, 'bulk_receipts_clean'):
            return set()
        try:
            row = conn.execute("SELECT * FROM bulk_receipts_clean LIMIT 0").description
            return {d[0] for d in row} if row else set()
        except Exception:
            return set()

    # Detect receipts donor-name column: compat view uses last_or_business_name,
    # legacy/test fixtures may use contributed_by
    bulk_receipts_cols = _bulk_receipts_columns()
    if 'last_or_business_name' in bulk_receipts_cols:
        receipts_donor_col = 'last_or_business_name'
    elif 'contributed_by' in bulk_receipts_cols:
        receipts_donor_col = 'contributed_by'
    else:
        receipts_donor_col = None

    has_bulk_receipts_donor_key_cols = all(
        c in bulk_receipts_cols
        for c in (
            'first_name',
            'last_or_business_name',
            'address_line_1',
            'address_line_2',
            'city',
            'state',
            'postal_code',
        )
    )

    def _bulk_receipts_donor_key_sql(alias: str = '') -> str:
        prefix = f"{alias}." if alias else ""
        return (
            f"LOWER(TRIM("
            f"COALESCE({prefix}first_name, '') || '|' || COALESCE({prefix}last_or_business_name, '') || '|' || "
            f"COALESCE({prefix}address_line_1, '') || '|' || COALESCE({prefix}address_line_2, '') || '|' || "
            f"COALESCE({prefix}city, '') || '|' || COALESCE({prefix}state, '') || '|' || COALESCE({prefix}postal_code, '')"
            f"))"
        )

    # ===== DONOR FOCUS =====
    if focus_type == 'donor':
        # Find donor->committee edges
        focus_id = focus_node_id or f"donor:{focus_key or _stable_entity_key(focus_label)}"
        focus_lbl = focus_label

        if receipts_donor_col:
            donor_edges = []

            # Resolve label from data if needed
            if not focus_lbl and focus_key and _table_exists(conn, 'analytics_donor_summary'):
                r = conn.execute(
                    "SELECT donor_name FROM analytics_donor_summary WHERE donor_key = ? LIMIT 1",
                    (focus_key,),
                ).fetchone()
                if r:
                    focus_lbl = r["donor_name"]

            # 1) Prefer key-based materialized edges when available (works for hashed donor keys)
            if focus_key and not date_from and not date_to and _table_exists(conn, 'analytics_donor_committee_agg'):
                agg_rows = conn.execute(
                    "SELECT committee_id, committee_name, SUM(total_amount) AS total, "
                    "SUM(contribution_count) AS cnt, MAX(donor_name) AS donor_name "
                    "FROM analytics_donor_committee_agg "
                    "WHERE donor_key = ? "
                    "GROUP BY committee_id, committee_name "
                    "ORDER BY total DESC LIMIT ?",
                    (focus_key, committee_limit),
                ).fetchall()
                if agg_rows:
                    if not focus_lbl:
                        focus_lbl = agg_rows[0]["donor_name"]
                    donor_edges = [
                        {
                            "committee_id_sbe": row["committee_id"],
                            "committee_name": row["committee_name"],
                            "total": row["total"],
                            "cnt": row["cnt"],
                        }
                        for row in agg_rows
                    ]

            # 2) Fallback key-based lookup from raw receipts rows
            if not donor_edges and focus_key and has_bulk_receipts_donor_key_cols:
                date_sql, date_params = _date_clause("received_date")
                key_rows = conn.execute(
                    f"SELECT committee_id_sbe, SUM(amount) AS total, COUNT(*) AS cnt "
                    f"FROM bulk_receipts_clean "
                    f"WHERE {_bulk_receipts_donor_key_sql()} = LOWER(?) AND amount > 0 AND is_archived = 0{date_sql} "
                    f"GROUP BY committee_id_sbe ORDER BY total DESC LIMIT ?",
                    [focus_key] + date_params + [committee_limit],
                ).fetchall()
                if key_rows:
                    if not focus_lbl:
                        name_row = conn.execute(
                            f"SELECT first_name, last_or_business_name FROM bulk_receipts_clean "
                            f"WHERE {_bulk_receipts_donor_key_sql()} = LOWER(?) LIMIT 1",
                            (focus_key,),
                        ).fetchone()
                        if name_row:
                            first = (name_row["first_name"] or "").strip()
                            last = (name_row["last_or_business_name"] or "").strip()
                            focus_lbl = f"{first} {last}".strip() or last or focus_key
                    donor_edges = [
                        {
                            "committee_id_sbe": row["committee_id_sbe"],
                            "committee_name": None,
                            "total": row["total"],
                            "cnt": row["cnt"],
                        }
                        for row in key_rows
                    ]

            # 3) Final fallback: label-based lookup
            if not donor_edges and focus_lbl:
                date_sql, date_params = _date_clause("received_date")
                if receipts_donor_col == 'last_or_business_name' and 'first_name' in bulk_receipts_cols:
                    label_where = (
                        "LOWER(TRIM(COALESCE(first_name, '') || "
                        "CASE WHEN COALESCE(first_name, '') <> '' AND COALESCE(last_or_business_name, '') <> '' "
                        "THEN ' ' ELSE '' END || COALESCE(last_or_business_name, ''))) = LOWER(?) "
                        "OR LOWER(last_or_business_name) = LOWER(?)"
                    )
                    label_params = [focus_lbl, focus_lbl]
                else:
                    label_where = f"LOWER({receipts_donor_col}) = LOWER(?)"
                    label_params = [focus_lbl]
                label_rows = conn.execute(
                    f"SELECT committee_id_sbe, SUM(amount) AS total, COUNT(*) AS cnt "
                    f"FROM bulk_receipts_clean "
                    f"WHERE ({label_where}) AND amount > 0 AND is_archived = 0{date_sql} "
                    f"GROUP BY committee_id_sbe ORDER BY total DESC LIMIT ?",
                    label_params + date_params + [committee_limit],
                ).fetchall()
                donor_edges = [
                    {
                        "committee_id_sbe": row["committee_id_sbe"],
                        "committee_name": None,
                        "total": row["total"],
                        "cnt": row["cnt"],
                    }
                    for row in label_rows
                ]

            if donor_edges:
                summary_flags["has_donor_data"] = True
                _add_node(focus_id, focus_lbl or focus_label or focus_key, "donor")
                committee_ids = []
                for r in donor_edges:
                    committee_key = str(r["committee_id_sbe"])
                    cid = f"committee:{committee_key}"
                    _add_node(cid, r["committee_name"] or f"Committee {committee_key}", "committee")
                    _add_edge(focus_id, cid, r["total"], "donor_committee", r["cnt"])
                    committee_ids.append(committee_key)

                # Resolve committee names
                if committee_ids and _table_exists(conn, 'bulk_committees_clean'):
                    placeholders = ','.join(['?'] * len(committee_ids))
                    name_rows = conn.execute(
                        f"SELECT committee_id_sbe, committee_name FROM bulk_committees_clean "
                        f"WHERE committee_id_sbe IN ({placeholders})",
                        committee_ids,
                    ).fetchall()
                    for nr in name_rows:
                        nid = f"committee:{nr['committee_id_sbe']}"
                        if nid in nodes_map:
                            nodes_map[nid]["label"] = nr["committee_name"]

                # Committee->candidate edges
                if committee_ids and _table_exists(conn, 'bulk_candidate_committee_finance_agg'):
                    placeholders = ','.join(['?'] * len(committee_ids))
                    cand_rows = conn.execute(
                        f"SELECT committee_id_sbe, candidate_id, candidate_full_name, "
                        f"sum_total_receipts FROM bulk_candidate_committee_finance_agg "
                        f"WHERE committee_id_sbe IN ({placeholders}) "
                        f"ORDER BY sum_total_receipts DESC LIMIT ?",
                        committee_ids + [candidate_limit],
                    ).fetchall()
                    if cand_rows:
                        summary_flags["has_candidate_data"] = True
                        for cr in cand_rows:
                            cand_id = f"candidate:{cr['candidate_id']}"
                            cmte_id = f"committee:{cr['committee_id_sbe']}"
                            _add_node(cand_id, cr["candidate_full_name"], "candidate")
                            _add_edge(cmte_id, cand_id, cr["sum_total_receipts"], "committee_candidate")

                # Committee->vendor edges
                if committee_ids and _table_exists(conn, 'bulk_expenditures_clean'):
                    summary_flags["has_vendor_data"] = True
                    placeholders = ','.join(['?'] * len(committee_ids))
                    date_sql_v, date_params_v = _date_clause("expended_date")
                    vend_rows = conn.execute(
                        f"SELECT committee_id_sbe, payee_last_or_business_name AS payee, "
                        f"SUM(amount) AS total, COUNT(*) AS cnt "
                        f"FROM bulk_expenditures_clean "
                        f"WHERE committee_id_sbe IN ({placeholders}) AND amount > 0 "
                        f"AND payee_last_or_business_name IS NOT NULL{date_sql_v} "
                        f"GROUP BY committee_id_sbe, payee_last_or_business_name "
                        f"ORDER BY total DESC LIMIT ?",
                        committee_ids + date_params_v + [vendor_limit],
                    ).fetchall()
                    for vr in vend_rows:
                        vname = vr["payee"]
                        vid = f"vendor:{_stable_entity_key(vname)}"
                        cmte_id = f"committee:{vr['committee_id_sbe']}"
                        _add_node(vid, vname, "vendor")
                        _add_edge(cmte_id, vid, vr["total"], "committee_vendor", vr["cnt"])
        resolved_focus_id = focus_id

    # ===== COMMITTEE FOCUS =====
    elif focus_type == 'committee':
        focus_lbl = focus_label
        cmte_key = focus_key

        # Resolve committee name
        if not focus_lbl and cmte_key:
            for tbl, id_col, name_col in [
                ('isbe_committees', 'id', 'name'),
                ('bulk_committees_clean', 'committee_id_sbe', 'committee_name'),
                ('committees', 'id', 'name'),
            ]:
                if _table_exists(conn, tbl):
                    r = conn.execute(
                        f"SELECT {name_col} FROM {tbl} WHERE {id_col} = ? LIMIT 1",
                        (cmte_key,),
                    ).fetchone()
                    if r:
                        focus_lbl = r[name_col]
                        break
        # Resolve committee id from label when only label is provided
        if not cmte_key and focus_lbl:
            for tbl, id_col, name_col in [
                ('isbe_committees', 'id', 'name'),
                ('bulk_committees_clean', 'committee_id_sbe', 'committee_name'),
                ('committees', 'id', 'name'),
            ]:
                if _table_exists(conn, tbl):
                    r = conn.execute(
                        f"SELECT {id_col} FROM {tbl} WHERE LOWER({name_col}) = LOWER(?) LIMIT 1",
                        (focus_lbl,),
                    ).fetchone()
                    if r and r[id_col] is not None:
                        cmte_key = str(r[id_col])
                        break

        focus_id = focus_node_id or f"committee:{cmte_key or _stable_entity_key(focus_lbl or focus_label)}"

        _add_node(focus_id, focus_lbl or f"Committee {cmte_key}", "committee")
        summary_flags["has_committee_data"] = True

        # Inbound donors
        if cmte_key and receipts_donor_col:
            date_sql, date_params = _date_clause("received_date")
            rows = conn.execute(
                f"SELECT {receipts_donor_col} AS donor_name, SUM(amount) AS total, COUNT(*) AS cnt "
                f"FROM bulk_receipts_clean "
                f"WHERE committee_id_sbe = ? AND amount > 0 AND is_archived = 0{date_sql} "
                f"GROUP BY {receipts_donor_col} ORDER BY total DESC LIMIT ?",
                [cmte_key] + date_params + [donor_limit],
            ).fetchall()
            if rows:
                summary_flags["has_donor_data"] = True
                for r in rows:
                    dname = r["donor_name"]
                    did = f"donor:{_stable_entity_key(dname)}"
                    _add_node(did, dname, "donor")
                    _add_edge(did, focus_id, r["total"], "donor_committee", r["cnt"])

        # Outbound candidates
        if cmte_key and _table_exists(conn, 'bulk_candidate_committee_finance_agg'):
            cand_rows = conn.execute(
                "SELECT candidate_id, candidate_full_name, sum_total_receipts "
                "FROM bulk_candidate_committee_finance_agg "
                "WHERE committee_id_sbe = ? ORDER BY sum_total_receipts DESC LIMIT ?",
                (cmte_key, candidate_limit),
            ).fetchall()
            if cand_rows:
                summary_flags["has_candidate_data"] = True
                for cr in cand_rows:
                    cand_id = f"candidate:{cr['candidate_id']}"
                    _add_node(cand_id, cr["candidate_full_name"], "candidate")
                    _add_edge(focus_id, cand_id, cr["sum_total_receipts"], "committee_candidate")

        # Outbound vendors
        if cmte_key and _table_exists(conn, 'bulk_expenditures_clean'):
            date_sql_v, date_params_v = _date_clause("expended_date")
            vend_rows = conn.execute(
                f"SELECT payee_last_or_business_name AS payee, SUM(amount) AS total, COUNT(*) AS cnt "
                f"FROM bulk_expenditures_clean "
                f"WHERE committee_id_sbe = ? AND amount > 0 "
                f"AND payee_last_or_business_name IS NOT NULL{date_sql_v} "
                f"GROUP BY payee_last_or_business_name ORDER BY total DESC LIMIT ?",
                [cmte_key] + date_params_v + [vendor_limit],
            ).fetchall()
            if vend_rows:
                summary_flags["has_vendor_data"] = True
                for vr in vend_rows:
                    vname = vr["payee"]
                    vid = f"vendor:{_stable_entity_key(vname)}"
                    _add_node(vid, vname, "vendor")
                    _add_edge(focus_id, vid, vr["total"], "committee_vendor", vr["cnt"])
        resolved_focus_id = focus_id

    # ===== CANDIDATE FOCUS =====
    elif focus_type == 'candidate':
        focus_lbl = focus_label
        cand_key = focus_key

        # Resolve candidate key/name from label-only focus
        if _table_exists(conn, 'bulk_candidate_committee_finance_agg'):
            if not cand_key and focus_lbl:
                r = conn.execute(
                    "SELECT candidate_id FROM bulk_candidate_committee_finance_agg "
                    "WHERE LOWER(candidate_full_name) = LOWER(?) LIMIT 1",
                    (focus_lbl,),
                ).fetchone()
                if r and r["candidate_id"] is not None:
                    cand_key = str(r["candidate_id"])
            if not focus_lbl and cand_key:
                r = conn.execute(
                    "SELECT candidate_full_name FROM bulk_candidate_committee_finance_agg "
                    "WHERE candidate_id = ? LIMIT 1",
                    (cand_key,),
                ).fetchone()
                if r:
                    focus_lbl = r["candidate_full_name"]

        focus_id = focus_node_id or f"candidate:{cand_key or _stable_entity_key(focus_lbl or focus_label)}"

        if _table_exists(conn, 'bulk_candidate_committee_finance_agg'):
            where_col = "candidate_id" if cand_key else "candidate_full_name"
            where_cond = f"{where_col} = ?" if cand_key else f"LOWER({where_col}) = LOWER(?)"
            where_val = cand_key or focus_lbl
            cand_rows = conn.execute(
                "SELECT committee_id_sbe, committee_name, candidate_full_name, sum_total_receipts "
                "FROM bulk_candidate_committee_finance_agg "
                f"WHERE {where_cond} ORDER BY sum_total_receipts DESC LIMIT ?",
                (where_val, committee_limit),
            ).fetchall()
            if cand_rows:
                if not focus_lbl:
                    focus_lbl = cand_rows[0]["candidate_full_name"]
                _add_node(focus_id, focus_lbl, "candidate")
                summary_flags["has_candidate_data"] = True
                committee_ids = []
                for cr in cand_rows:
                    cmte_id = f"committee:{cr['committee_id_sbe']}"
                    _add_node(cmte_id, cr["committee_name"], "committee")
                    _add_edge(cmte_id, focus_id, cr["sum_total_receipts"], "committee_candidate")
                    committee_ids.append(cr["committee_id_sbe"])
                    summary_flags["has_committee_data"] = True

                # Donors into those committees
                if committee_ids and receipts_donor_col:
                    placeholders = ','.join(['?'] * len(committee_ids))
                    date_sql, date_params = _date_clause("received_date")
                    donor_rows = conn.execute(
                        f"SELECT committee_id_sbe, {receipts_donor_col} AS donor_name, SUM(amount) AS total, COUNT(*) AS cnt "
                        f"FROM bulk_receipts_clean "
                        f"WHERE committee_id_sbe IN ({placeholders}) AND amount > 0 AND is_archived = 0{date_sql} "
                        f"GROUP BY committee_id_sbe, {receipts_donor_col} ORDER BY total DESC LIMIT ?",
                        committee_ids + date_params + [donor_limit],
                    ).fetchall()
                    if donor_rows:
                        summary_flags["has_donor_data"] = True
                        for dr in donor_rows:
                            dname = dr["donor_name"]
                            did = f"donor:{_stable_entity_key(dname)}"
                            cmte_id = f"committee:{dr['committee_id_sbe']}"
                            _add_node(did, dname, "donor")
                            _add_edge(did, cmte_id, dr["total"], "donor_committee", dr["cnt"])
        resolved_focus_id = focus_id

    # ===== VENDOR FOCUS =====
    elif focus_type == 'vendor':
        focus_id = focus_node_id or f"vendor:{focus_key or _stable_entity_key(focus_label)}"
        focus_lbl = focus_label

        if _table_exists(conn, 'bulk_expenditures_clean') and focus_lbl:
            date_sql_v, date_params_v = _date_clause("expended_date")
            rows = conn.execute(
                f"SELECT committee_id_sbe, SUM(amount) AS total, COUNT(*) AS cnt "
                f"FROM bulk_expenditures_clean "
                f"WHERE LOWER(payee_last_or_business_name) = LOWER(?) AND amount > 0{date_sql_v} "
                f"GROUP BY committee_id_sbe ORDER BY total DESC LIMIT ?",
                [focus_lbl] + date_params_v + [committee_limit],
            ).fetchall()
            if rows:
                _add_node(focus_id, focus_lbl, "vendor")
                summary_flags["has_vendor_data"] = True
                committee_ids = []
                for r in rows:
                    cmte_id = f"committee:{r['committee_id_sbe']}"
                    _add_node(cmte_id, f"Committee {r['committee_id_sbe']}", "committee")
                    _add_edge(cmte_id, focus_id, r["total"], "committee_vendor", r["cnt"])
                    committee_ids.append(r["committee_id_sbe"])
                    summary_flags["has_committee_data"] = True

                # Resolve committee names
                if committee_ids and _table_exists(conn, 'bulk_committees_clean'):
                    placeholders = ','.join(['?'] * len(committee_ids))
                    name_rows = conn.execute(
                        f"SELECT committee_id_sbe, committee_name FROM bulk_committees_clean "
                        f"WHERE committee_id_sbe IN ({placeholders})",
                        committee_ids,
                    ).fetchall()
                    for nr in name_rows:
                        nid = f"committee:{nr['committee_id_sbe']}"
                        if nid in nodes_map:
                            nodes_map[nid]["label"] = nr["committee_name"]

                # Donors into those committees
                if committee_ids and receipts_donor_col:
                    placeholders = ','.join(['?'] * len(committee_ids))
                    date_sql_d, date_params_d = _date_clause("received_date")
                    donor_rows = conn.execute(
                        f"SELECT committee_id_sbe, {receipts_donor_col} AS donor_name, SUM(amount) AS total, COUNT(*) AS cnt "
                        f"FROM bulk_receipts_clean "
                        f"WHERE committee_id_sbe IN ({placeholders}) AND amount > 0 AND is_archived = 0{date_sql_d} "
                        f"GROUP BY committee_id_sbe, {receipts_donor_col} ORDER BY total DESC LIMIT ?",
                        committee_ids + date_params_d + [donor_limit],
                    ).fetchall()
                    if donor_rows:
                        summary_flags["has_donor_data"] = True
                        for dr in donor_rows:
                            dname = dr["donor_name"]
                            did = f"donor:{_stable_entity_key(dname)}"
                            cmte_id = f"committee:{dr['committee_id_sbe']}"
                            _add_node(did, dname, "donor")
                            _add_edge(did, cmte_id, dr["total"], "donor_committee", dr["cnt"])
        resolved_focus_id = focus_id
    else:
        return jsonify({"error": f"unsupported focus_type: {focus_type}"}), 400

    # Build focus info
    effective_focus_id = focus_node_id or resolved_focus_id
    focus_info = nodes_map.get(effective_focus_id, {
        "node_id": effective_focus_id,
        "label": focus_label or effective_focus_id,
        "node_type": focus_type,
    })

    return jsonify({
        "focus": {
            "node_id": effective_focus_id,
            "label": focus_info.get("label", focus_label),
            "node_type": focus_info.get("node_type", focus_type),
        },
        "nodes": list(nodes_map.values()),
        "edges": edges_list,
        "summary": {
            "node_count": len(nodes_map),
            "edge_count": len(edges_list),
            **summary_flags,
        },
    })


# ---------------------------------------------------------------------------
# Federal Custom Sankey: suggest + focus-sankey
# ---------------------------------------------------------------------------

@api_bp.route('/federal/network/suggest')
def federal_network_suggest():
    """Autocomplete suggestions across FEC donors, candidates, committees, vendors."""
    conn = current_app.get_database()
    raw = request.args.get('q', '').strip()
    q = normalize_search_query(raw)
    limit = min(request.args.get('limit', 10, type=int), 50)
    entity_type = request.args.get('entity_type', 'all').strip().lower()

    if len(q) < 2:
        return jsonify([])

    results = []
    pattern = f"%{q}%"

    # --- donors (Schedule A contributors) ---
    if entity_type in ('all', 'donor') and _table_exists(conn, 'fec_schedule_a_contributions'):
        rows = conn.execute(
            "SELECT donor_entity_key, contributor_name, SUM(contribution_receipt_amount) AS total "
            "FROM fec_schedule_a_contributions "
            "WHERE contributor_name ILIKE ? AND contribution_receipt_amount > 0 "
            "GROUP BY donor_entity_key, contributor_name "
            "ORDER BY total DESC LIMIT ?",
            (pattern, limit),
        ).fetchall()
        for r in rows:
            results.append({
                "label": r["contributor_name"],
                "value": f"fed_donor:{r['donor_entity_key']}",
                "node_type": "fed_donor",
            })

    # --- candidates (matched FEC candidates) ---
    if entity_type in ('all', 'candidate') and _table_exists(conn, 'fec_candidate_match'):
        rows = conn.execute(
            "SELECT fec_candidate_id, candidate_name FROM fec_candidate_match "
            "WHERE match_status = 'matched' AND candidate_name ILIKE ? "
            "ORDER BY candidate_name LIMIT ?",
            (pattern, limit),
        ).fetchall()
        for r in rows:
            results.append({
                "label": r["candidate_name"],
                "value": f"fed_candidate:{r['fec_candidate_id']}",
                "node_type": "fed_candidate",
            })

    # --- committees (FEC candidate committees) ---
    if entity_type in ('all', 'committee') and _table_exists(conn, 'fec_candidate_committees'):
        rows = conn.execute(
            "SELECT DISTINCT committee_id, committee_name FROM fec_candidate_committees "
            "WHERE committee_name ILIKE ? ORDER BY committee_name LIMIT ?",
            (pattern, limit),
        ).fetchall()
        for r in rows:
            results.append({
                "label": r["committee_name"],
                "value": f"fed_committee:{r['committee_id']}",
                "node_type": "fed_committee",
            })

    # --- vendors (Schedule B payees) ---
    if entity_type in ('all', 'vendor') and _table_exists(conn, 'fec_schedule_b_disbursements'):
        rows = conn.execute(
            "SELECT recipient_name, SUM(disbursement_amount) AS total "
            "FROM fec_schedule_b_disbursements "
            "WHERE recipient_name ILIKE ? AND disbursement_amount > 0 "
            "GROUP BY recipient_name "
            "ORDER BY total DESC LIMIT ?",
            (pattern, limit),
        ).fetchall()
        for r in rows:
            vname = r["recipient_name"]
            results.append({
                "label": vname,
                "value": f"fed_vendor:{_stable_entity_key(vname)}",
                "node_type": "fed_vendor",
            })

    # Deduplicate by value, truncate to limit
    seen = set()
    deduped = []
    for item in results:
        if item["value"] not in seen:
            seen.add(item["value"])
            deduped.append(item)
        if len(deduped) >= limit:
            break

    return jsonify(deduped)


@api_bp.route('/federal/network/focus-sankey')
def federal_network_focus_sankey():
    """Build a focused subgraph around a single FEC entity for Custom Sankey rendering."""
    conn = current_app.get_database()

    focus_node_id = (request.args.get('focus_node_id', '') or '').strip()
    focus_label = (request.args.get('focus_label', '') or '').strip()
    focus_type = (request.args.get('focus_type', 'auto') or 'auto').strip().lower()
    cycle = request.args.get('cycle', None, type=int)
    min_edge_amount = max(request.args.get('min_edge_amount', 0, type=float), 0)
    committee_limit = min(request.args.get('committee_limit', 20, type=int), 100)
    candidate_limit = min(request.args.get('candidate_limit', 15, type=int), 100)
    vendor_limit = min(request.args.get('vendor_limit', 15, type=int), 100)
    donor_limit = min(request.args.get('donor_limit', 20, type=int), 100)

    # Resolve focus type from node_id prefix when auto
    if focus_type == 'auto' and ':' in focus_node_id:
        prefix = focus_node_id.split(':', 1)[0]
        type_map = {
            'fed_donor': 'donor', 'fed_candidate': 'candidate',
            'fed_committee': 'committee', 'fed_vendor': 'vendor',
        }
        if prefix in type_map:
            focus_type = type_map[prefix]

    if not focus_node_id and not focus_label:
        return jsonify({"error": "focus_node_id or focus_label required"}), 400

    # Auto-detect: probe FEC tables to pick the best type
    if focus_type == 'auto' and focus_label:
        for probe_type, probe_table, probe_col in [
            ('committee', 'fec_candidate_committees', 'committee_name'),
            ('candidate', 'fec_candidate_match', 'candidate_name'),
            ('donor', 'fec_schedule_a_contributions', 'contributor_name'),
            ('vendor', 'fec_schedule_b_disbursements', 'recipient_name'),
        ]:
            if not _table_exists(conn, probe_table):
                continue
            extra = " AND match_status = 'matched'" if probe_table == 'fec_candidate_match' else ''
            row = conn.execute(
                f"SELECT 1 FROM {probe_table} WHERE {probe_col} ILIKE ?{extra} LIMIT 1",
                (focus_label,),
            ).fetchone()
            if row:
                focus_type = probe_type
                break
        if focus_type == 'auto':
            focus_type = 'donor'

    nodes_map = {}
    edges_list = []
    summary_flags = {
        "has_donor_data": False,
        "has_committee_data": False,
        "has_candidate_data": False,
        "has_vendor_data": False,
    }

    focus_key = focus_node_id.split(':', 1)[1] if ':' in focus_node_id else ''

    def _add_node(nid, label, ntype):
        if nid not in nodes_map:
            nodes_map[nid] = {"id": nid, "label": label, "node_type": ntype}

    def _add_edge(src, tgt, weight, etype, count=1):
        if weight >= min_edge_amount:
            edges_list.append({
                "source": src, "target": tgt,
                "weight": float(weight), "edge_type": etype, "count": count,
            })

    def _cycle_clause(col="cycle", prefix="AND"):
        if cycle:
            return f" {prefix} {col} = ?", [cycle]
        return "", []

    has_sched_a = _table_exists(conn, 'fec_schedule_a_contributions')
    has_sched_b = _table_exists(conn, 'fec_schedule_b_disbursements')
    has_committees = _table_exists(conn, 'fec_candidate_committees')
    has_candidates = _table_exists(conn, 'fec_candidate_match')

    # ===== DONOR FOCUS =====
    if focus_type == 'donor':
        focus_id = focus_node_id or f"fed_donor:{focus_key or _stable_entity_key(focus_label)}"
        focus_lbl = focus_label

        if has_sched_a:
            # Resolve label from data if only key provided
            if not focus_lbl and focus_key:
                r = conn.execute(
                    "SELECT contributor_name FROM fec_schedule_a_contributions "
                    "WHERE donor_entity_key = ? LIMIT 1", (focus_key,),
                ).fetchone()
                if r:
                    focus_lbl = r["contributor_name"]

            if focus_lbl:
                cycle_sql, cycle_params = _cycle_clause()
                rows = conn.execute(
                    f"SELECT candidate_id, candidate_name, committee_id, committee_name, "
                    f"SUM(contribution_receipt_amount) AS total, COUNT(*) AS cnt "
                    f"FROM fec_schedule_a_contributions "
                    f"WHERE contributor_name ILIKE ? AND contribution_receipt_amount > 0{cycle_sql} "
                    f"GROUP BY candidate_id, candidate_name, committee_id, committee_name "
                    f"ORDER BY total DESC LIMIT ?",
                    [focus_lbl] + cycle_params + [candidate_limit],
                ).fetchall()
                if rows:
                    summary_flags["has_donor_data"] = True
                    _add_node(focus_id, focus_lbl, "fed_donor")
                    candidate_ids = set()
                    committee_ids = set()
                    for r in rows:
                        cand_id = f"fed_candidate:{r['candidate_id']}"
                        _add_node(cand_id, r["candidate_name"], "fed_candidate")
                        _add_edge(focus_id, cand_id, r["total"], "donor_candidate", r["cnt"])
                        summary_flags["has_candidate_data"] = True
                        candidate_ids.add(r["candidate_id"])
                        if r["committee_id"]:
                            cmte_id = f"fed_committee:{r['committee_id']}"
                            _add_node(cmte_id, r["committee_name"] or r["committee_id"], "fed_committee")
                            _add_edge(cand_id, cmte_id, r["total"], "candidate_committee")
                            summary_flags["has_committee_data"] = True
                            committee_ids.add(r["committee_id"])

                    # Committee -> vendor edges from Schedule B
                    if committee_ids and has_sched_b:
                        placeholders = ','.join(['?'] * len(committee_ids))
                        cycle_sql_v, cycle_params_v = _cycle_clause()
                        vend_rows = conn.execute(
                            f"SELECT committee_id, recipient_name, "
                            f"SUM(disbursement_amount) AS total, COUNT(*) AS cnt "
                            f"FROM fec_schedule_b_disbursements "
                            f"WHERE committee_id IN ({placeholders}) AND disbursement_amount > 0 "
                            f"AND recipient_name IS NOT NULL{cycle_sql_v} "
                            f"GROUP BY committee_id, recipient_name "
                            f"ORDER BY total DESC LIMIT ?",
                            list(committee_ids) + cycle_params_v + [vendor_limit],
                        ).fetchall()
                        for vr in vend_rows:
                            vname = vr["recipient_name"]
                            vid = f"fed_vendor:{_stable_entity_key(vname)}"
                            cmte_id = f"fed_committee:{vr['committee_id']}"
                            _add_node(vid, vname, "fed_vendor")
                            _add_edge(cmte_id, vid, vr["total"], "committee_vendor", vr["cnt"])
                            summary_flags["has_vendor_data"] = True

    # ===== CANDIDATE FOCUS =====
    elif focus_type == 'candidate':
        focus_id = focus_node_id or f"fed_candidate:{focus_key}"
        focus_lbl = focus_label
        cand_key = focus_key

        # Resolve candidate name
        if not focus_lbl and cand_key and has_candidates:
            r = conn.execute(
                "SELECT candidate_name FROM fec_candidate_match "
                "WHERE fec_candidate_id = ? AND match_status = 'matched' LIMIT 1",
                (cand_key,),
            ).fetchone()
            if r:
                focus_lbl = r["candidate_name"]

        if cand_key or focus_lbl:
            _add_node(focus_id, focus_lbl or f"Candidate {cand_key}", "fed_candidate")
            summary_flags["has_candidate_data"] = True

            # Inbound donors from Schedule A
            if has_sched_a:
                where_col = "candidate_id" if cand_key else "candidate_name"
                where_op = "=" if cand_key else "ILIKE"
                where_val = cand_key or focus_lbl
                cycle_sql, cycle_params = _cycle_clause()
                donor_rows = conn.execute(
                    f"SELECT contributor_name, donor_entity_key, "
                    f"SUM(contribution_receipt_amount) AS total, COUNT(*) AS cnt "
                    f"FROM fec_schedule_a_contributions "
                    f"WHERE {where_col} {where_op} ? AND contribution_receipt_amount > 0{cycle_sql} "
                    f"GROUP BY contributor_name, donor_entity_key "
                    f"ORDER BY total DESC LIMIT ?",
                    [where_val] + cycle_params + [donor_limit],
                ).fetchall()
                for dr in donor_rows:
                    did = f"fed_donor:{dr['donor_entity_key'] or _stable_entity_key(dr['contributor_name'])}"
                    _add_node(did, dr["contributor_name"], "fed_donor")
                    _add_edge(did, focus_id, dr["total"], "donor_candidate", dr["cnt"])
                    summary_flags["has_donor_data"] = True

            # Linked committees
            if has_committees and cand_key:
                cycle_sql_c, cycle_params_c = _cycle_clause()
                cmte_rows = conn.execute(
                    f"SELECT committee_id, committee_name FROM fec_candidate_committees "
                    f"WHERE candidate_id = ?{cycle_sql_c} LIMIT ?",
                    [cand_key] + cycle_params_c + [committee_limit],
                ).fetchall()
                for cr in cmte_rows:
                    cmte_id = f"fed_committee:{cr['committee_id']}"
                    _add_node(cmte_id, cr["committee_name"] or cr["committee_id"], "fed_committee")
                    _add_edge(focus_id, cmte_id, 0, "candidate_committee")
                    summary_flags["has_committee_data"] = True

                    # Committee -> vendor from Schedule B
                    if has_sched_b:
                        cycle_sql_v, cycle_params_v = _cycle_clause()
                        vend_rows = conn.execute(
                            f"SELECT recipient_name, SUM(disbursement_amount) AS total, COUNT(*) AS cnt "
                            f"FROM fec_schedule_b_disbursements "
                            f"WHERE committee_id = ? AND disbursement_amount > 0 "
                            f"AND recipient_name IS NOT NULL{cycle_sql_v} "
                            f"GROUP BY recipient_name ORDER BY total DESC LIMIT ?",
                            [cr["committee_id"]] + cycle_params_v + [vendor_limit],
                        ).fetchall()
                        for vr in vend_rows:
                            vname = vr["recipient_name"]
                            vid = f"fed_vendor:{_stable_entity_key(vname)}"
                            _add_node(vid, vname, "fed_vendor")
                            _add_edge(cmte_id, vid, vr["total"], "committee_vendor", vr["cnt"])
                            summary_flags["has_vendor_data"] = True

    # ===== COMMITTEE FOCUS =====
    elif focus_type == 'committee':
        focus_id = focus_node_id or f"fed_committee:{focus_key}"
        focus_lbl = focus_label
        cmte_key = focus_key

        # Resolve committee name
        if not focus_lbl and cmte_key and has_committees:
            r = conn.execute(
                "SELECT committee_name FROM fec_candidate_committees "
                "WHERE committee_id = ? LIMIT 1", (cmte_key,),
            ).fetchone()
            if r:
                focus_lbl = r["committee_name"]

        _add_node(focus_id, focus_lbl or f"Committee {cmte_key}", "fed_committee")
        summary_flags["has_committee_data"] = True

        # Inbound donors from Schedule A
        if cmte_key and has_sched_a:
            cycle_sql, cycle_params = _cycle_clause()
            donor_rows = conn.execute(
                f"SELECT contributor_name, donor_entity_key, "
                f"SUM(contribution_receipt_amount) AS total, COUNT(*) AS cnt "
                f"FROM fec_schedule_a_contributions "
                f"WHERE committee_id = ? AND contribution_receipt_amount > 0{cycle_sql} "
                f"GROUP BY contributor_name, donor_entity_key "
                f"ORDER BY total DESC LIMIT ?",
                [cmte_key] + cycle_params + [donor_limit],
            ).fetchall()
            for dr in donor_rows:
                did = f"fed_donor:{dr['donor_entity_key'] or _stable_entity_key(dr['contributor_name'])}"
                _add_node(did, dr["contributor_name"], "fed_donor")
                _add_edge(did, focus_id, dr["total"], "donor_committee", dr["cnt"])
                summary_flags["has_donor_data"] = True

        # Linked candidates
        if cmte_key and has_committees:
            cycle_sql_c, cycle_params_c = _cycle_clause()
            cand_rows = conn.execute(
                f"SELECT candidate_id FROM fec_candidate_committees "
                f"WHERE committee_id = ?{cycle_sql_c}",
                [cmte_key] + cycle_params_c,
            ).fetchall()
            for cr in cand_rows:
                cand_id = f"fed_candidate:{cr['candidate_id']}"
                # Resolve candidate name
                cand_lbl = cr["candidate_id"]
                if has_candidates:
                    nr = conn.execute(
                        "SELECT candidate_name FROM fec_candidate_match "
                        "WHERE fec_candidate_id = ? LIMIT 1", (cr["candidate_id"],),
                    ).fetchone()
                    if nr:
                        cand_lbl = nr["candidate_name"]
                _add_node(cand_id, cand_lbl, "fed_candidate")
                _add_edge(focus_id, cand_id, 0, "committee_candidate")
                summary_flags["has_candidate_data"] = True

        # Outbound vendors from Schedule B
        if cmte_key and has_sched_b:
            cycle_sql_v, cycle_params_v = _cycle_clause()
            vend_rows = conn.execute(
                f"SELECT recipient_name, SUM(disbursement_amount) AS total, COUNT(*) AS cnt "
                f"FROM fec_schedule_b_disbursements "
                f"WHERE committee_id = ? AND disbursement_amount > 0 "
                f"AND recipient_name IS NOT NULL{cycle_sql_v} "
                f"GROUP BY recipient_name ORDER BY total DESC LIMIT ?",
                [cmte_key] + cycle_params_v + [vendor_limit],
            ).fetchall()
            for vr in vend_rows:
                vname = vr["recipient_name"]
                vid = f"fed_vendor:{_stable_entity_key(vname)}"
                _add_node(vid, vname, "fed_vendor")
                _add_edge(focus_id, vid, vr["total"], "committee_vendor", vr["cnt"])
                summary_flags["has_vendor_data"] = True

    # ===== VENDOR FOCUS =====
    elif focus_type == 'vendor':
        focus_id = focus_node_id or f"fed_vendor:{_stable_entity_key(focus_label)}"
        focus_lbl = focus_label

        if has_sched_b and focus_lbl:
            cycle_sql, cycle_params = _cycle_clause()
            rows = conn.execute(
                f"SELECT committee_id, committee_name, "
                f"SUM(disbursement_amount) AS total, COUNT(*) AS cnt "
                f"FROM fec_schedule_b_disbursements "
                f"WHERE recipient_name ILIKE ? AND disbursement_amount > 0{cycle_sql} "
                f"GROUP BY committee_id, committee_name "
                f"ORDER BY total DESC LIMIT ?",
                [focus_lbl] + cycle_params + [committee_limit],
            ).fetchall()
            if rows:
                _add_node(focus_id, focus_lbl, "fed_vendor")
                summary_flags["has_vendor_data"] = True
                committee_ids = set()
                for r in rows:
                    cmte_id = f"fed_committee:{r['committee_id']}"
                    _add_node(cmte_id, r["committee_name"] or r["committee_id"], "fed_committee")
                    _add_edge(cmte_id, focus_id, r["total"], "committee_vendor", r["cnt"])
                    summary_flags["has_committee_data"] = True
                    committee_ids.add(r["committee_id"])

                # Resolve linked candidates
                if committee_ids and has_committees:
                    placeholders = ','.join(['?'] * len(committee_ids))
                    cand_rows = conn.execute(
                        f"SELECT DISTINCT candidate_id, committee_id FROM fec_candidate_committees "
                        f"WHERE committee_id IN ({placeholders})",
                        list(committee_ids),
                    ).fetchall()
                    for cr in cand_rows:
                        cand_id = f"fed_candidate:{cr['candidate_id']}"
                        cmte_id = f"fed_committee:{cr['committee_id']}"
                        cand_lbl = cr["candidate_id"]
                        if has_candidates:
                            nr = conn.execute(
                                "SELECT candidate_name FROM fec_candidate_match "
                                "WHERE fec_candidate_id = ? LIMIT 1", (cr["candidate_id"],),
                            ).fetchone()
                            if nr:
                                cand_lbl = nr["candidate_name"]
                        _add_node(cand_id, cand_lbl, "fed_candidate")
                        _add_edge(cand_id, cmte_id, 0, "candidate_committee")
                        summary_flags["has_candidate_data"] = True

                # Inbound donors
                if committee_ids and has_sched_a:
                    placeholders = ','.join(['?'] * len(committee_ids))
                    cycle_sql_d, cycle_params_d = _cycle_clause()
                    donor_rows = conn.execute(
                        f"SELECT contributor_name, donor_entity_key, committee_id, "
                        f"SUM(contribution_receipt_amount) AS total, COUNT(*) AS cnt "
                        f"FROM fec_schedule_a_contributions "
                        f"WHERE committee_id IN ({placeholders}) AND contribution_receipt_amount > 0{cycle_sql_d} "
                        f"GROUP BY contributor_name, donor_entity_key, committee_id "
                        f"ORDER BY total DESC LIMIT ?",
                        list(committee_ids) + cycle_params_d + [donor_limit],
                    ).fetchall()
                    for dr in donor_rows:
                        did = f"fed_donor:{dr['donor_entity_key'] or _stable_entity_key(dr['contributor_name'])}"
                        cmte_id = f"fed_committee:{dr['committee_id']}"
                        _add_node(did, dr["contributor_name"], "fed_donor")
                        _add_edge(did, cmte_id, dr["total"], "donor_committee", dr["cnt"])
                        summary_flags["has_donor_data"] = True
    else:
        return jsonify({"error": f"unsupported focus_type: {focus_type}"}), 400

    # Build focus info
    focus_info = nodes_map.get(focus_node_id or focus_id, {
        "node_id": focus_node_id,
        "label": focus_label or focus_node_id,
        "node_type": focus_type,
    })

    return jsonify({
        "focus": {
            "node_id": focus_node_id or focus_id,
            "label": focus_info.get("label", focus_label),
            "node_type": focus_info.get("node_type", focus_type),
        },
        "nodes": list(nodes_map.values()),
        "edges": edges_list,
        "summary": {
            "node_count": len(nodes_map),
            "edge_count": len(edges_list),
            **summary_flags,
        },
    })
