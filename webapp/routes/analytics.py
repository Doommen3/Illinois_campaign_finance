"""Analytics dashboard routes."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from flask import Blueprint, current_app, render_template, request

from database.analytics import (
    build_dashboard_full_snapshot,
    get_candidate_competition_networks,
    get_analytics_data_sources,
    get_committee_similarity_network,
    get_donor_cogiving_network,
    get_dashboard_snapshot,
    get_irs527_ecosystem_graph,
    get_lobbying_influence_graph,
    get_nlp_spending_summary,
    get_reconciliation_outliers,
    get_state_federal_overlap_graph,
    get_time_series,
    get_vendor_expenditure_network,
    save_dashboard_snapshot,
)
from database.connection import get_db

analytics_bp = Blueprint("analytics", __name__)

FULL_SNAPSHOT_TTL_SECONDS = 900
_snapshot_executor = ThreadPoolExecutor(max_workers=1)
_snapshot_lock = threading.Lock()
_running_snapshot_keys: set[str] = set()
_relationships_cache = {
    "payload": None,
    "expires_at": 0.0,
    "key": None,
}
_relationships_cache_lock = threading.Lock()


def _is_true_arg(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _empty_network() -> dict:
    return {
        "nodes": [],
        "edges": [],
        "centrality": [],
        "summary": {
            "node_count": 0,
            "edge_count": 0,
            "donor_committee_edges": 0,
            "committee_candidate_edges": 0,
            "donor_committee_source": "pending",
            "region_counts": {},
        },
    }


def _empty_geo_summary() -> dict:
    return {"states": [], "cities": []}


def _parse_filters() -> dict:
    load_mode = (request.args.get("load_mode", "quick", type=str) or "quick").strip().lower()
    if load_mode not in {"quick", "full"}:
        load_mode = "quick"

    min_edge_amount = max(request.args.get("min_edge_amount", 1000.0, type=float) or 1000.0, 0.0)
    network_limit = min(max(request.args.get("network_limit", 200, type=int) or 200, 50), 5000)
    anomaly_limit = min(max(request.args.get("anomaly_limit", 25, type=int) or 25, 1), 500)
    concentration_limit = min(max(request.args.get("concentration_limit", 25, type=int) or 25, 1), 500)
    months = min(max(request.args.get("months", 24, type=int) or 24, 1), 120)
    geo_state_limit = min(max(request.args.get("geo_state_limit", 15, type=int) or 15, 1), 100)
    geo_city_limit = min(max(request.args.get("geo_city_limit", 25, type=int) or 25, 1), 500)
    nlp_limit = min(max(request.args.get("nlp_limit", 20, type=int) or 20, 1), 500)
    recon_limit = min(max(request.args.get("recon_limit", 20, type=int) or 20, 1), 500)
    recon_min_abs_diff = max(request.args.get("recon_min_abs_diff", 1000.0, type=float) or 1000.0, 0.0)

    return {
        "load_mode": load_mode,
        "full_mode_requested": load_mode == "full",
        "refresh_full": _is_true_arg(request.args.get("refresh_full")),
        "sync_full": _is_true_arg(request.args.get("sync_full")),
        "min_edge_amount": min_edge_amount,
        "network_limit": network_limit,
        "anomaly_limit": anomaly_limit,
        "concentration_limit": concentration_limit,
        "months": months,
        "geo_state_limit": geo_state_limit,
        "geo_city_limit": geo_city_limit,
        "nlp_limit": nlp_limit,
        "recon_limit": recon_limit,
        "recon_min_abs_diff": recon_min_abs_diff,
        "date_from": (request.args.get("date_from", "", type=str) or "").strip(),
        "date_to": (request.args.get("date_to", "", type=str) or "").strip(),
    }


def _build_snapshot_params(
    min_edge_amount: float,
    network_limit: int,
    anomaly_limit: int,
    concentration_limit: int,
    months: int,
    geo_state_limit: int,
    geo_city_limit: int,
    nlp_limit: int,
    recon_limit: int,
    recon_min_abs_diff: float,
    date_from: str | None,
    date_to: str | None,
) -> dict:
    return {
        "min_edge_amount": float(min_edge_amount),
        "network_limit": int(network_limit),
        "anomaly_limit": int(anomaly_limit),
        "concentration_limit": int(concentration_limit),
        "months": int(months),
        "geo_state_limit": int(geo_state_limit),
        "geo_city_limit": int(geo_city_limit),
        "nlp_limit": int(nlp_limit),
        "recon_limit": int(recon_limit),
        "recon_min_abs_diff": float(recon_min_abs_diff),
        "date_from": (date_from or "").strip() or None,
        "date_to": (date_to or "").strip() or None,
        "snapshot_version": 2,
    }


def _snapshot_params_from_filters(filters: dict) -> dict:
    return _build_snapshot_params(
        min_edge_amount=filters["min_edge_amount"],
        network_limit=filters["network_limit"],
        anomaly_limit=filters["anomaly_limit"],
        concentration_limit=filters["concentration_limit"],
        months=filters["months"],
        geo_state_limit=filters["geo_state_limit"],
        geo_city_limit=filters["geo_city_limit"],
        nlp_limit=filters["nlp_limit"],
        recon_limit=filters["recon_limit"],
        recon_min_abs_diff=filters["recon_min_abs_diff"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
    )


def _run_snapshot_job(database_path: str, params: dict, rebuild_materialized: bool) -> None:
    conn = get_db(database_path)
    try:
        save_dashboard_snapshot(conn, params=params, status="in_progress")
        payload = build_dashboard_full_snapshot(
            conn,
            params=params,
            rebuild_materialized=rebuild_materialized,
        )
        save_dashboard_snapshot(
            conn,
            params=params,
            status="completed",
            payload=payload,
            error_message=None,
        )
    except Exception as exc:
        save_dashboard_snapshot(
            conn,
            params=params,
            status="error",
            payload=None,
            error_message=str(exc),
        )
    finally:
        conn.close()


def _schedule_snapshot_refresh(
    database_path: str,
    params: dict,
    cache_key: str,
    rebuild_materialized: bool,
) -> bool:
    with _snapshot_lock:
        if cache_key in _running_snapshot_keys:
            return False
        _running_snapshot_keys.add(cache_key)

    def _job():
        try:
            _run_snapshot_job(database_path, params=params, rebuild_materialized=rebuild_materialized)
        finally:
            with _snapshot_lock:
                _running_snapshot_keys.discard(cache_key)

    _snapshot_executor.submit(_job)
    return True


def _load_snapshot_state(conn, filters: dict) -> dict:
    state = {
        "payload": None,
        "snapshot_meta": None,
        "snapshot_loading": False,
        "snapshot_refreshed": False,
        "heavy_sections_loaded": False,
        "data_sources": get_analytics_data_sources(conn),
    }
    if not filters["full_mode_requested"]:
        return state

    full_params = _snapshot_params_from_filters(filters)
    snapshot_meta = get_dashboard_snapshot(conn, params=full_params, ttl_seconds=FULL_SNAPSHOT_TTL_SECONDS)
    if filters["sync_full"]:
        save_dashboard_snapshot(conn, params=full_params, status="in_progress")
        try:
            payload = build_dashboard_full_snapshot(
                conn,
                params=full_params,
                rebuild_materialized=filters["refresh_full"],
            )
            snapshot_meta = save_dashboard_snapshot(
                conn,
                params=full_params,
                status="completed",
                payload=payload,
                error_message=None,
            )
        except Exception as exc:
            snapshot_meta = save_dashboard_snapshot(
                conn,
                params=full_params,
                status="error",
                payload=None,
                error_message=str(exc),
            )
    else:
        should_refresh = (
            filters["refresh_full"]
            or snapshot_meta["is_stale"]
            or snapshot_meta["status"] in {"empty", "error"}
        )
        if should_refresh:
            scheduled = _schedule_snapshot_refresh(
                database_path=current_app.config["DATABASE_PATH"],
                params=full_params,
                cache_key=snapshot_meta["cache_key"],
                rebuild_materialized=filters["refresh_full"] or snapshot_meta["status"] == "empty",
            )
            state["snapshot_refreshed"] = scheduled
        snapshot_meta = get_dashboard_snapshot(conn, params=full_params, ttl_seconds=FULL_SNAPSHOT_TTL_SECONDS)

    state["snapshot_meta"] = snapshot_meta
    payload = snapshot_meta.get("payload")
    if payload:
        state["payload"] = payload
        state["heavy_sections_loaded"] = True
        state["data_sources"] = payload.get("data_sources", state["data_sources"])
    else:
        state["snapshot_loading"] = snapshot_meta.get("status") in {"in_progress", "empty"}
    return state


def _refresh_params(filters: dict) -> dict:
    keys = [
        "min_edge_amount",
        "network_limit",
        "anomaly_limit",
        "concentration_limit",
        "months",
        "geo_state_limit",
        "geo_city_limit",
        "nlp_limit",
        "recon_limit",
        "recon_min_abs_diff",
        "date_from",
        "date_to",
    ]
    return {key: filters[key] for key in keys}


def _safe_optional_graph(graph_key: str, callback, fallback: dict) -> dict:
    """Return optional graph payload, logging and degrading safely on failure."""
    try:
        payload = callback()
    except Exception:
        current_app.logger.exception("Analytics optional graph build failed: %s", graph_key)
        payload = {}

    if isinstance(payload, dict) and payload.get("nodes") is not None and payload.get("edges") is not None:
        return payload

    summary = dict((fallback.get("summary") or {}))
    summary.setdefault("node_count", 0)
    summary.setdefault("edge_count", 0)
    summary["error"] = f"{graph_key}_query_failed"
    return {
        "nodes": [],
        "edges": [],
        "centrality": [],
        "summary": summary,
    }


def _base_context(active_page: str, filters: dict, snapshot_state: dict) -> dict:
    return {
        "active_page": active_page,
        "snapshot_ttl_seconds": FULL_SNAPSHOT_TTL_SECONDS,
        "refresh_params": _refresh_params(filters),
        **filters,
        **snapshot_state,
    }


@analytics_bp.route("/", endpoint="dashboard")
@analytics_bp.route("/overview", endpoint="overview")
def dashboard():
    """Render the analytics overview page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    network = payload.get("network", _empty_network()) if snapshot_state["heavy_sections_loaded"] else _empty_network()
    anomalies = payload.get("anomalies", []) if snapshot_state["heavy_sections_loaded"] else []
    concentration = payload.get("concentration", []) if snapshot_state["heavy_sections_loaded"] else []
    geo_summary = payload.get("geo_summary", _empty_geo_summary()) if snapshot_state["heavy_sections_loaded"] else _empty_geo_summary()

    if snapshot_state["heavy_sections_loaded"]:
        time_series = payload.get("time_series", [])
        nlp_summary = payload.get("nlp_summary", [])
        reconciliation = payload.get("reconciliation", [])
    else:
        time_series = get_time_series(
            conn,
            months=filters["months"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
        )
        nlp_summary = get_nlp_spending_summary(conn, limit=filters["nlp_limit"])
        reconciliation = get_reconciliation_outliers(
            conn,
            limit=filters["recon_limit"],
            min_abs_diff=filters["recon_min_abs_diff"],
        )

    latest_time_point = time_series[-1] if time_series else None

    return render_template(
        "analytics/overview.html",
        **_base_context("overview", filters, snapshot_state),
        network=network,
        anomalies=anomalies,
        concentration=concentration,
        geo_summary=geo_summary,
        time_series=time_series,
        nlp_summary=nlp_summary,
        reconciliation=reconciliation,
        latest_time_point=latest_time_point,
    )


@analytics_bp.route("/networks")
def networks():
    """Render network analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    network = payload.get("network", _empty_network()) if snapshot_state["heavy_sections_loaded"] else _empty_network()

    empty_graph = {"nodes": [], "edges": [], "centrality": [], "summary": {"node_count": 0, "edge_count": 0}}
    if snapshot_state["heavy_sections_loaded"]:
        vendor_network = _safe_optional_graph(
            "vendor_network",
            lambda: get_vendor_expenditure_network(conn, committee_limit=60, vendor_limit=100, edge_limit=800),
            empty_graph,
        )
        overlap_graph = _safe_optional_graph(
            "overlap_graph",
            lambda: get_state_federal_overlap_graph(conn, donor_limit=100, edge_limit=600),
            empty_graph,
        )
        lobbying_graph = _safe_optional_graph(
            "lobbying_graph",
            lambda: get_lobbying_influence_graph(conn, client_limit=80, edge_limit=600),
            empty_graph,
        )
        ecosystem_527 = _safe_optional_graph(
            "ecosystem_527",
            lambda: get_irs527_ecosystem_graph(conn, org_limit=80, edge_limit=600),
            empty_graph,
        )
    else:
        vendor_network = empty_graph
        overlap_graph = empty_graph
        lobbying_graph = empty_graph
        ecosystem_527 = empty_graph

    return render_template(
        "analytics/networks.html",
        **_base_context("networks", filters, snapshot_state),
        network=network,
        vendor_network=vendor_network,
        overlap_graph=overlap_graph,
        lobbying_graph=lobbying_graph,
        ecosystem_527=ecosystem_527,
    )


@analytics_bp.route("/risk")
def risk():
    """Render anomaly and reconciliation analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    anomalies = payload.get("anomalies", []) if snapshot_state["heavy_sections_loaded"] else []
    if snapshot_state["heavy_sections_loaded"]:
        reconciliation = payload.get("reconciliation", [])
    else:
        reconciliation = get_reconciliation_outliers(
            conn,
            limit=filters["recon_limit"],
            min_abs_diff=filters["recon_min_abs_diff"],
        )

    return render_template(
        "analytics/risk.html",
        **_base_context("risk", filters, snapshot_state),
        anomalies=anomalies,
        reconciliation=reconciliation,
    )


@analytics_bp.route("/donors")
def donors():
    """Render donor concentration and NLP analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    concentration = payload.get("concentration", []) if snapshot_state["heavy_sections_loaded"] else []
    if snapshot_state["heavy_sections_loaded"]:
        nlp_summary = payload.get("nlp_summary", [])
    else:
        nlp_summary = get_nlp_spending_summary(conn, limit=filters["nlp_limit"])

    return render_template(
        "analytics/donors.html",
        **_base_context("donors", filters, snapshot_state),
        concentration=concentration,
        nlp_summary=nlp_summary,
    )


@analytics_bp.route("/geography")
def geography():
    """Render geography and trend analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    geo_summary = payload.get("geo_summary", _empty_geo_summary()) if snapshot_state["heavy_sections_loaded"] else _empty_geo_summary()
    if snapshot_state["heavy_sections_loaded"]:
        time_series = payload.get("time_series", [])
    else:
        time_series = get_time_series(
            conn,
            months=filters["months"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
        )

    return render_template(
        "analytics/geography.html",
        **_base_context("geography", filters, snapshot_state),
        geo_summary=geo_summary,
        time_series=time_series,
    )


@analytics_bp.route("/relationships")
def relationships():
    """Render relationship network graphs (co-giving/similarity/competition/lobbying/527)."""
    conn = current_app.get_database()
    filters = _parse_filters()

    donor_limit = min(max(request.args.get("donor_limit", 200, type=int) or 200, 50), 5000)
    committee_limit = min(max(request.args.get("committee_limit", 120, type=int) or 120, 50), 5000)
    candidate_limit = min(max(request.args.get("candidate_limit", 80, type=int) or 80, 50), 2000)
    client_limit = min(max(request.args.get("client_limit", 80, type=int) or 80, 20), 2000)
    org_limit = min(max(request.args.get("org_limit", 100, type=int) or 100, 20), 2000)
    edge_limit = min(max(request.args.get("edge_limit", 200, type=int) or 200, 50), 10000)

    min_shared_amount = max(request.args.get("min_shared_amount", 5000.0, type=float) or 5000.0, 0.0)
    min_shared_targets = max(request.args.get("min_shared_targets", 2, type=int) or 2, 1)
    min_shared_donors = max(request.args.get("min_shared_donors", 2, type=int) or 2, 1)
    relationships_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS", 300)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    cache_key = (
        donor_limit,
        committee_limit,
        candidate_limit,
        client_limit,
        org_limit,
        edge_limit,
        round(min_shared_amount, 2),
        min_shared_targets,
        min_shared_donors,
    )
    now = time.monotonic()

    payload = None
    if cache_enabled and not refresh_requested:
        with _relationships_cache_lock:
            if (
                _relationships_cache.get("payload") is not None
                and _relationships_cache.get("key") == cache_key
                and float(_relationships_cache.get("expires_at", 0.0)) > now
            ):
                payload = _relationships_cache.get("payload")

    if payload is None:
        payload = {
            "donor_cogiving": get_donor_cogiving_network(
                conn,
                donor_limit=donor_limit,
                edge_limit=edge_limit,
                min_shared_amount=min_shared_amount,
                min_shared_targets=min_shared_targets,
            ),
            "committee_similarity": get_committee_similarity_network(
                conn,
                committee_limit=committee_limit,
                edge_limit=edge_limit,
                min_shared_donors=min_shared_donors,
                min_shared_amount=min_shared_amount,
            ),
            "candidate_competition": get_candidate_competition_networks(
                conn,
                candidate_limit=candidate_limit,
                edge_limit=edge_limit,
                min_shared_donors=min_shared_donors,
                min_shared_amount=min_shared_amount,
            ),
            "lobbying_influence": get_lobbying_influence_graph(
                conn,
                client_limit=client_limit,
                edge_limit=edge_limit,
            ),
            "ecosystem_527": get_irs527_ecosystem_graph(
                conn,
                org_limit=org_limit,
                edge_limit=edge_limit,
            ),
        }
        if cache_enabled:
            with _relationships_cache_lock:
                _relationships_cache["payload"] = payload
                _relationships_cache["key"] = cache_key
                _relationships_cache["expires_at"] = now + float(relationships_cache_ttl)

    return render_template(
        "analytics/relationships.html",
        active_page="relationships",
        load_mode=filters["load_mode"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        donor_limit=donor_limit,
        committee_limit=committee_limit,
        candidate_limit=candidate_limit,
        client_limit=client_limit,
        org_limit=org_limit,
        edge_limit=edge_limit,
        min_shared_amount=min_shared_amount,
        min_shared_targets=min_shared_targets,
        min_shared_donors=min_shared_donors,
        donor_cogiving=payload["donor_cogiving"],
        committee_similarity=payload["committee_similarity"],
        candidate_competition=payload["candidate_competition"],
        lobbying_influence=payload["lobbying_influence"],
        ecosystem_527=payload["ecosystem_527"],
    )
