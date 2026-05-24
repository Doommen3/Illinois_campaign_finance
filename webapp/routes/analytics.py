"""Analytics dashboard routes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import threading
import time

from flask import Blueprint, abort, current_app, render_template, request, url_for

from database.analytics import (
    build_dashboard_full_snapshot,
    get_anomaly_flags,
    get_candidate_competition_networks,
    get_analytics_data_sources,
    get_committee_similarity_network,
    get_donor_cogiving_network,
    get_donor_concentration,
    get_dashboard_snapshot,
    get_geo_drilldown,
    get_geo_summary,
    get_irs527_ecosystem_graph,
    get_lobbying_influence_graph,
    get_network_graph,
    get_nlp_spending_summary,
    get_reconciliation_outliers,
    get_state_race_analytics,
    get_state_race_detail,
    get_state_federal_overlap_graph,
    get_time_series,
    get_vendor_expenditure_network,
    save_dashboard_snapshot,
)
from database.connection import get_db, close_db
from webapp.utils.time_filter import get_active_period, period_to_date_window

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
_networks_cache: dict = {"payload": None, "expires_at": 0.0, "key": None}
_networks_cache_lock = threading.Lock()
_overview_cache: dict = {"payload": None, "expires_at": 0.0, "key": None}
_overview_cache_lock = threading.Lock()
_risk_cache: dict = {"payload": None, "expires_at": 0.0, "key": None}
_risk_cache_lock = threading.Lock()
_geo_drilldown_cache: dict[str, dict] = {}
_geo_drilldown_cache_lock = threading.Lock()


def _is_true_arg(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


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


def _get_cached_geo_drilldown(cache_key: str) -> dict | None:
    now = time.monotonic()
    with _geo_drilldown_cache_lock:
        entry = _geo_drilldown_cache.get(cache_key)
        if not entry:
            return None
        if float(entry.get("expires_at", 0.0)) <= now:
            _geo_drilldown_cache.pop(cache_key, None)
            return None
        return entry.get("payload")


def _store_cached_geo_drilldown(cache_key: str, payload: dict, ttl_seconds: int) -> None:
    now = time.monotonic()
    with _geo_drilldown_cache_lock:
        _geo_drilldown_cache[cache_key] = {
            "payload": payload,
            "expires_at": now + max(15, int(ttl_seconds)),
            "updated_at": now,
        }
        if len(_geo_drilldown_cache) > 96:
            stale_keys = [
                key
                for key, entry in _geo_drilldown_cache.items()
                if float(entry.get("expires_at", 0.0)) <= now
            ]
            for key in stale_keys:
                _geo_drilldown_cache.pop(key, None)
            if len(_geo_drilldown_cache) > 96:
                oldest_keys = sorted(
                    _geo_drilldown_cache.keys(),
                    key=lambda key: float(_geo_drilldown_cache[key].get("updated_at", 0.0)),
                )[: len(_geo_drilldown_cache) - 96]
                for key in oldest_keys:
                    _geo_drilldown_cache.pop(key, None)


def _parse_filters() -> dict:
    period = get_active_period()
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

    explicit_date_from = (request.args.get("date_from", "", type=str) or "").strip()
    explicit_date_to = (request.args.get("date_to", "", type=str) or "").strip()
    date_from, date_to = period_to_date_window(period, explicit_date_from, explicit_date_to)

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
        "date_from": date_from or "",
        "date_to": date_to or "",
        "explicit_date_from": explicit_date_from,
        "explicit_date_to": explicit_date_to,
        "time_period_key": period["key"],
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
        "snapshot_version": 3,
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


_networks_parallel_executor = ThreadPoolExecutor(max_workers=5)


def _compute_graph_with_own_conn(graph_key: str, func, args, kwargs, fallback: dict, db_target: str) -> tuple[str, dict]:
    """Run a graph function in a thread with its own DB connection."""
    conn = None
    try:
        conn = get_db(db_target)
        result = func(conn, *args, **kwargs)
        if isinstance(result, dict) and result.get("nodes") is not None and result.get("edges") is not None:
            return graph_key, result
    except Exception:
        logging.getLogger(__name__).exception("Parallel graph build failed: %s", graph_key)
    finally:
        if conn is not None:
            close_db(conn)

    summary = dict((fallback.get("summary") or {}))
    summary.setdefault("node_count", 0)
    summary.setdefault("edge_count", 0)
    summary["error"] = f"{graph_key}_query_failed"
    return graph_key, {"nodes": [], "edges": [], "centrality": [], "summary": summary}


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
    valid_state_race_sorts = {"total_amount", "outside_spending_total", "outside_pressure_ratio"}
    state_race_sort = (request.args.get("state_race_sort", "total_amount", type=str) or "total_amount").strip().lower()
    if state_race_sort not in valid_state_race_sorts:
        state_race_sort = "total_amount"
    state_race_dir = "asc" if (request.args.get("state_race_dir", "desc", type=str) or "").strip().lower() == "asc" else "desc"

    # --- TTL cache for overview aggregates ---
    overview_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS", 300)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    election_cycle = request.args.get("election_cycle", type=int)
    cache_key = (
        filters["time_period_key"],
        filters["date_from"],
        filters["date_to"],
        filters["min_edge_amount"],
        filters["network_limit"],
        filters["anomaly_limit"],
        filters["concentration_limit"],
        filters["months"],
        filters["nlp_limit"],
        election_cycle,
        state_race_sort,
        state_race_dir,
    )
    now = time.monotonic()

    cached_payload = None
    if cache_enabled and not refresh_requested:
        with _overview_cache_lock:
            if (
                _overview_cache.get("payload") is not None
                and _overview_cache.get("key") == cache_key
                and float(_overview_cache.get("expires_at", 0.0)) > now
            ):
                cached_payload = _overview_cache["payload"]

    if cached_payload is not None:
        network = cached_payload["network"]
        anomalies = cached_payload["anomalies"]
        concentration = cached_payload["concentration"]
        geo_summary = cached_payload["geo_summary"]
        time_series = cached_payload["time_series"]
        nlp_summary = cached_payload["nlp_summary"]
        reconciliation = cached_payload["reconciliation"]
        state_race_analytics = cached_payload["state_race_analytics"]
        state_race_table_available = cached_payload["state_race_table_available"]
        state_race_nonzero_below_visible = cached_payload.get("state_race_nonzero_below_visible", False)
        snapshot_state["heavy_sections_loaded"] = True
    else:
        if snapshot_state["heavy_sections_loaded"]:
            network = payload.get("network", _empty_network())
            anomalies = payload.get("anomalies", [])
            concentration = payload.get("concentration", [])
            geo_summary = payload.get("geo_summary", _empty_geo_summary())
            time_series = payload.get("time_series", [])
            nlp_summary = payload.get("nlp_summary", [])
            reconciliation = payload.get("reconciliation", [])
        else:
            network = _safe_optional_graph(
                "network",
                lambda: get_network_graph(
                    conn,
                    min_edge_amount=filters["min_edge_amount"],
                    limit=filters["network_limit"],
                    date_from=filters["date_from"],
                    date_to=filters["date_to"],
                ),
                _empty_network(),
            )
            anomalies = get_anomaly_flags(
                conn,
                limit=filters["anomaly_limit"],
                date_from=filters["date_from"],
                date_to=filters["date_to"],
            )
            concentration = get_donor_concentration(
                conn,
                limit=filters["concentration_limit"],
                date_from=filters["date_from"],
                date_to=filters["date_to"],
            )
            geo_summary = _empty_geo_summary()
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
            snapshot_state["heavy_sections_loaded"] = True

        state_race_table_available = all(
            _table_exists(conn, table_name)
            for table_name in (
                "bulk_candidate_committee_finance_agg",
                "bulk_receipts_clean",
                "bulk_expenditures_clean",
            )
        )
        state_race_all_rows = get_state_race_analytics(
            conn,
            limit=5000,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            election_cycle=election_cycle,
            sort_by=state_race_sort,
            sort_dir=state_race_dir,
        )
        state_race_analytics = state_race_all_rows[:12]
        visible_rows = state_race_analytics
        hidden_rows = state_race_all_rows[12:]
        visible_has_nonzero_outside = any(
            float(row.get("outside_spending_total") or 0.0) > 0.0 for row in visible_rows
        )
        hidden_has_nonzero_outside = any(
            float(row.get("outside_spending_total") or 0.0) > 0.0 for row in hidden_rows
        )
        state_race_nonzero_below_visible = bool(visible_rows) and (not visible_has_nonzero_outside) and hidden_has_nonzero_outside
        if cache_enabled:
            with _overview_cache_lock:
                _overview_cache["payload"] = {
                    "network": network,
                    "anomalies": anomalies,
                    "concentration": concentration,
                    "geo_summary": geo_summary,
                    "time_series": time_series,
                    "nlp_summary": nlp_summary,
                    "reconciliation": reconciliation,
                    "state_race_analytics": state_race_analytics,
                    "state_race_table_available": state_race_table_available,
                    "state_race_nonzero_below_visible": state_race_nonzero_below_visible,
                }
                _overview_cache["key"] = cache_key
                _overview_cache["expires_at"] = now + float(overview_cache_ttl)

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
        state_race_table_available=state_race_table_available,
        state_race_analytics=state_race_analytics,
        state_race_sort=state_race_sort,
        state_race_dir=state_race_dir,
        state_race_nonzero_below_visible=state_race_nonzero_below_visible,
        election_cycle=election_cycle,
    )


@analytics_bp.route("/state-races/<race_key>")
def state_race_detail(race_key: str):
    """Render a state race detail page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    election_cycle = request.args.get("election_cycle", type=int)
    race_detail = get_state_race_detail(
        conn,
        race_key=race_key,
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        election_cycle=election_cycle,
    )
    if not race_detail:
        abort(404)

    return render_template(
        "analytics/state_race_detail.html",
        active_page="overview",
        load_mode=filters["load_mode"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        time_period_key=filters["time_period_key"],
        explicit_date_from=filters["explicit_date_from"],
        explicit_date_to=filters["explicit_date_to"],
        election_cycle=election_cycle,
        race_detail=race_detail,
    )


@analytics_bp.route("/networks")
def networks():
    """Render network analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    empty_graph = {"nodes": [], "edges": [], "centrality": [], "summary": {"node_count": 0, "edge_count": 0}}

    # --- TTL cache for all 5 network graphs ---
    networks_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_NETWORKS_CACHE_TTL_SECONDS", 300)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    cache_key = (
        filters["time_period_key"],
        filters["date_from"],
        filters["date_to"],
        filters["min_edge_amount"],
        filters["network_limit"],
    )
    now = time.monotonic()

    cached_payload = None
    if cache_enabled and not refresh_requested:
        with _networks_cache_lock:
            if (
                _networks_cache.get("payload") is not None
                and _networks_cache.get("key") == cache_key
                and float(_networks_cache.get("expires_at", 0.0)) > now
            ):
                cached_payload = _networks_cache["payload"]

    if cached_payload is not None:
        network = cached_payload["network"]
        vendor_network = cached_payload["vendor_network"]
        overlap_graph = cached_payload["overlap_graph"]
        lobbying_graph = cached_payload["lobbying_graph"]
        ecosystem_527 = cached_payload["ecosystem_527"]
        snapshot_state["heavy_sections_loaded"] = True
    else:
        # Use snapshot data for network if available, otherwise compute it as
        # part of the parallel batch below.
        network_from_snapshot = None
        if snapshot_state["heavy_sections_loaded"]:
            network_from_snapshot = payload.get("network", _empty_network())

        # Run all 5 (or 4, if network came from snapshot) graph functions in
        # parallel, each with its own DB connection.
        db_target = current_app.config.get("DATABASE_TARGET", "")
        graph_jobs: list[tuple[str, object, tuple, dict, dict]] = []
        if network_from_snapshot is None:
            graph_jobs.append((
                "network", get_network_graph, (),
                {"min_edge_amount": filters["min_edge_amount"], "limit": filters["network_limit"],
                 "date_from": filters["date_from"], "date_to": filters["date_to"]},
                _empty_network(),
            ))
        graph_jobs.extend([
            ("vendor_network", get_vendor_expenditure_network, (),
             {"committee_limit": 60, "vendor_limit": 100, "edge_limit": 800}, empty_graph),
            ("overlap_graph", get_state_federal_overlap_graph, (),
             {"donor_limit": 100, "edge_limit": 600}, empty_graph),
            ("lobbying_graph", get_lobbying_influence_graph, (),
             {"client_limit": 80, "edge_limit": 600,
              "date_from": filters["date_from"], "date_to": filters["date_to"]}, empty_graph),
            ("ecosystem_527", get_irs527_ecosystem_graph, (),
             {"org_limit": 80, "edge_limit": 600,
              "date_from": filters["date_from"], "date_to": filters["date_to"]}, empty_graph),
        ])

        results: dict[str, dict] = {}
        futures = {
            _networks_parallel_executor.submit(
                _compute_graph_with_own_conn, key, func, args, kwargs, fallback, db_target,
            ): key
            for key, func, args, kwargs, fallback in graph_jobs
        }
        for future in as_completed(futures):
            key, result = future.result()
            results[key] = result

        network = network_from_snapshot or results.get("network", _empty_network())
        vendor_network = results.get("vendor_network", empty_graph)
        overlap_graph = results.get("overlap_graph", empty_graph)
        lobbying_graph = results.get("lobbying_graph", empty_graph)
        ecosystem_527 = results.get("ecosystem_527", empty_graph)
        snapshot_state["heavy_sections_loaded"] = True

        if cache_enabled:
            with _networks_cache_lock:
                _networks_cache["payload"] = {
                    "network": network,
                    "vendor_network": vendor_network,
                    "overlap_graph": overlap_graph,
                    "lobbying_graph": lobbying_graph,
                    "ecosystem_527": ecosystem_527,
                }
                _networks_cache["key"] = cache_key
                _networks_cache["expires_at"] = now + float(networks_cache_ttl)

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

    # --- TTL cache for risk aggregates ---
    risk_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_RISK_CACHE_TTL_SECONDS", 300)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    cache_key = (
        filters["time_period_key"],
        filters["date_from"],
        filters["date_to"],
        filters["anomaly_limit"],
        filters["recon_limit"],
        round(filters["recon_min_abs_diff"], 2),
    )
    now = time.monotonic()

    cached_payload = None
    if cache_enabled and not refresh_requested:
        with _risk_cache_lock:
            if (
                _risk_cache.get("payload") is not None
                and _risk_cache.get("key") == cache_key
                and float(_risk_cache.get("expires_at", 0.0)) > now
            ):
                cached_payload = _risk_cache["payload"]

    if cached_payload is not None:
        anomalies = cached_payload["anomalies"]
        reconciliation = cached_payload["reconciliation"]
        snapshot_state["heavy_sections_loaded"] = True
    else:
        if snapshot_state["heavy_sections_loaded"]:
            anomalies = payload.get("anomalies", [])
            reconciliation = payload.get("reconciliation", [])
        else:
            anomalies = get_anomaly_flags(
                conn,
                limit=filters["anomaly_limit"],
                date_from=filters["date_from"],
                date_to=filters["date_to"],
            )
            reconciliation = get_reconciliation_outliers(
                conn,
                limit=filters["recon_limit"],
                min_abs_diff=filters["recon_min_abs_diff"],
            )
            snapshot_state["heavy_sections_loaded"] = True
        if cache_enabled:
            with _risk_cache_lock:
                _risk_cache["payload"] = {
                    "anomalies": anomalies,
                    "reconciliation": reconciliation,
                }
                _risk_cache["key"] = cache_key
                _risk_cache["expires_at"] = now + float(risk_cache_ttl)

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

    if snapshot_state["heavy_sections_loaded"]:
        concentration = payload.get("concentration", [])
        nlp_summary = payload.get("nlp_summary", [])
    else:
        concentration = get_donor_concentration(
            conn,
            limit=filters["concentration_limit"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
        )
        nlp_summary = get_nlp_spending_summary(conn, limit=filters["nlp_limit"])
        snapshot_state["heavy_sections_loaded"] = True

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

    if snapshot_state["heavy_sections_loaded"]:
        geo_summary = payload.get("geo_summary", _empty_geo_summary())
        time_series = payload.get("time_series", [])
    else:
        geo_summary = get_geo_summary(
            conn,
            limit_states=filters.get("geo_state_limit", 15),
            limit_cities=filters.get("geo_city_limit", 25),
            date_from=filters["date_from"],
            date_to=filters["date_to"],
        )
        time_series = get_time_series(
            conn,
            months=filters["months"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
        )
        snapshot_state["heavy_sections_loaded"] = True

    return render_template(
        "analytics/geography.html",
        **_base_context("geography", filters, snapshot_state),
        geo_summary=geo_summary,
        time_series=time_series,
    )


@analytics_bp.route("/geo-drilldown")
def geo_drilldown():
    """Render state/city donor->committee drilldown for a geography aggregate cell."""
    conn = current_app.get_database()
    filters = _parse_filters()

    geo_type = (request.args.get("geo_type", "state", type=str) or "state").strip().lower()
    geo_value = (request.args.get("geo_value", "", type=str) or "").strip()
    geo_state = (request.args.get("geo_state", "", type=str) or "").strip().upper()
    source_page = (request.args.get("source_page", "geography", type=str) or "geography").strip().lower()
    range_key = (request.args.get("range", "", type=str) or "").strip() or filters["time_period_key"]

    page = max(request.args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(request.args.get("per_page", 50, type=int) or 50, 10), 250)
    sort_by = (request.args.get("sort", "total_amount", type=str) or "total_amount").strip().lower()
    sort_dir = (request.args.get("dir", "desc", type=str) or "desc").strip().lower()

    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    cache_ttl = max(15, int(current_app.config.get("ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS", 180)))
    cache_params = {
        "version": 1,
        "range": range_key,
        "period": filters["time_period_key"],
        "date_from": filters["date_from"],
        "date_to": filters["date_to"],
        "geo_type": geo_type,
        "geo_value": geo_value,
        "geo_state": geo_state,
        "page": page,
        "per_page": per_page,
        "sort": sort_by,
        "dir": sort_dir,
    }
    cache_key = json.dumps(cache_params, sort_keys=True, separators=(",", ":"))

    drilldown = _get_cached_geo_drilldown(cache_key) if cache_enabled else None
    if drilldown is None:
        drilldown = get_geo_drilldown(
            conn,
            geo_type=geo_type,
            geo_value=geo_value,
            geo_state=geo_state,
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_dir=sort_dir,
            date_from=filters["date_from"],
            date_to=filters["date_to"],
        )
        if cache_enabled:
            _store_cached_geo_drilldown(cache_key, drilldown, cache_ttl)

    if source_page == "overview":
        back_url = url_for(
            "analytics.dashboard",
            load_mode=filters["load_mode"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            months=filters["months"],
            geo_state_limit=filters["geo_state_limit"],
            geo_city_limit=filters["geo_city_limit"],
            period=filters["time_period_key"],
        )
    else:
        back_url = url_for(
            "analytics.geography",
            load_mode=filters["load_mode"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            months=filters["months"],
            geo_state_limit=filters["geo_state_limit"],
            geo_city_limit=filters["geo_city_limit"],
            period=filters["time_period_key"],
        )

    return render_template(
        "analytics/geo_drilldown.html",
        active_page="geography",
        **filters,
        range_key=range_key,
        source_page=source_page,
        drilldown=drilldown,
        back_url=back_url,
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
        filters["time_period_key"],
        filters["date_from"],
        filters["date_to"],
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
                date_from=filters["date_from"],
                date_to=filters["date_to"],
            ),
            "ecosystem_527": get_irs527_ecosystem_graph(
                conn,
                org_limit=org_limit,
                edge_limit=edge_limit,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
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


def warm_analytics_caches(
    db_target: str,
    networks_ttl: int = 1800,
    relationships_ttl: int = 1800,
    risk_ttl: int = 1800,
    overview_ttl: int = 1800,
) -> None:
    """Pre-populate heavy analytics route caches in the current worker process.

    Called from a background thread per gunicorn worker on startup. Each worker
    calls this independently so all workers end up with warm caches.

    Cache keys must match the defaults produced by _parse_filters() for a
    request with no query parameters (period="2026cycle", default limits).
    """
    from webapp.utils.time_filter import period_to_date_window as _ptdw

    logger = logging.getLogger(__name__)

    # Compute the default date window matching _parse_filters() defaults
    from datetime import date as _date
    _today = _date.today().isoformat()
    default_period_key = "2026cycle"
    default_date_from = "2025-01-01"
    default_date_to = _today

    # Default filter values matching _parse_filters() with no query params
    default_anomaly_limit = 25
    default_recon_limit = 20
    default_recon_min_abs_diff = 1000.0
    default_min_edge_amount = 1000.0
    default_network_limit = 200
    default_concentration_limit = 25
    default_months = 24
    default_nlp_limit = 20
    default_state_race_sort = "total_amount"
    default_state_race_dir = "desc"
    default_election_cycle = None  # request.args.get("election_cycle", type=int) → None by default

    # Default relationship params
    default_donor_limit = 200
    default_committee_limit = 120
    default_candidate_limit = 80
    default_client_limit = 80
    default_org_limit = 100
    default_edge_limit = 200
    default_min_shared_amount = 5000.0
    default_min_shared_targets = 2
    default_min_shared_donors = 2

    # --- Risk cache ---
    try:
        conn = get_db(db_target)
        anomalies = get_anomaly_flags(
            conn, limit=default_anomaly_limit,
            date_from=default_date_from, date_to=default_date_to,
        )
        reconciliation = get_reconciliation_outliers(
            conn, limit=default_recon_limit,
            min_abs_diff=default_recon_min_abs_diff,
        )
        now = time.monotonic()
        cache_key = (
            default_period_key, default_date_from, default_date_to,
            default_anomaly_limit, default_recon_limit, default_recon_min_abs_diff,
        )
        with _risk_cache_lock:
            _risk_cache["payload"] = {
                "anomalies": anomalies,
                "reconciliation": reconciliation,
            }
            _risk_cache["key"] = cache_key
            _risk_cache["expires_at"] = now + float(risk_ttl)
        logger.info("Analytics prewarm: risk cache populated")
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm: risk cache failed")

    # --- Networks cache ---
    try:
        empty_graph = {"nodes": [], "edges": [], "centrality": [], "summary": {"node_count": 0, "edge_count": 0}}
        graph_jobs = [
            ("network", get_network_graph, (),
             {"min_edge_amount": default_min_edge_amount, "limit": default_network_limit,
              "date_from": default_date_from, "date_to": default_date_to},
             _empty_network()),
            ("vendor_network", get_vendor_expenditure_network, (),
             {"committee_limit": 60, "vendor_limit": 100, "edge_limit": 800}, empty_graph),
            ("overlap_graph", get_state_federal_overlap_graph, (),
             {"donor_limit": 100, "edge_limit": 600}, empty_graph),
            ("lobbying_graph", get_lobbying_influence_graph, (),
             {"client_limit": 80, "edge_limit": 600,
              "date_from": default_date_from, "date_to": default_date_to}, empty_graph),
            ("ecosystem_527", get_irs527_ecosystem_graph, (),
             {"org_limit": 80, "edge_limit": 600,
              "date_from": default_date_from, "date_to": default_date_to}, empty_graph),
        ]
        results: dict[str, dict] = {}
        futures = {
            _networks_parallel_executor.submit(
                _compute_graph_with_own_conn, key, func, args, kwargs, fallback, db_target,
            ): key
            for key, func, args, kwargs, fallback in graph_jobs
        }
        for future in as_completed(futures):
            key, result = future.result()
            results[key] = result

        now = time.monotonic()
        cache_key = (
            default_period_key, default_date_from, default_date_to,
            default_min_edge_amount, default_network_limit,
        )
        with _networks_cache_lock:
            _networks_cache["payload"] = {
                "network": results.get("network", _empty_network()),
                "vendor_network": results.get("vendor_network", empty_graph),
                "overlap_graph": results.get("overlap_graph", empty_graph),
                "lobbying_graph": results.get("lobbying_graph", empty_graph),
                "ecosystem_527": results.get("ecosystem_527", empty_graph),
            }
            _networks_cache["key"] = cache_key
            _networks_cache["expires_at"] = now + float(networks_ttl)
        logger.info("Analytics prewarm: networks cache populated")
    except Exception:
        logger.exception("Analytics prewarm: networks cache failed")

    # --- Relationships cache ---
    try:
        conn = get_db(db_target)
        rel_payload = {
            "donor_cogiving": get_donor_cogiving_network(
                conn, donor_limit=default_donor_limit, edge_limit=default_edge_limit,
                min_shared_amount=default_min_shared_amount, min_shared_targets=default_min_shared_targets),
            "committee_similarity": get_committee_similarity_network(
                conn, committee_limit=default_committee_limit, edge_limit=default_edge_limit,
                min_shared_donors=default_min_shared_donors, min_shared_amount=default_min_shared_amount),
            "candidate_competition": get_candidate_competition_networks(
                conn, candidate_limit=default_candidate_limit, edge_limit=default_edge_limit,
                min_shared_donors=default_min_shared_donors, min_shared_amount=default_min_shared_amount),
            "lobbying_influence": get_lobbying_influence_graph(
                conn, client_limit=default_client_limit, edge_limit=default_edge_limit,
                date_from=default_date_from, date_to=default_date_to),
            "ecosystem_527": get_irs527_ecosystem_graph(
                conn, org_limit=default_org_limit, edge_limit=default_edge_limit,
                date_from=default_date_from, date_to=default_date_to),
        }
        now = time.monotonic()
        cache_key = (
            default_period_key, default_date_from, default_date_to,
            default_donor_limit, default_committee_limit, default_candidate_limit,
            default_client_limit, default_org_limit, default_edge_limit,
            default_min_shared_amount, default_min_shared_targets, default_min_shared_donors,
        )
        with _relationships_cache_lock:
            _relationships_cache["payload"] = rel_payload
            _relationships_cache["key"] = cache_key
            _relationships_cache["expires_at"] = now + float(relationships_ttl)
        logger.info("Analytics prewarm: relationships cache populated")
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm: relationships cache failed")

    # --- Overview cache (dashboard route, /analytics/) ---
    # Without this, the default landing-page hit pays the full live-compute
    # cost (~30-40s cold) on every worker even when the lower-level networks /
    # risk / relationships caches are already populated. The dashboard route
    # consults _overview_cache *first*; lower-level caches aren't checked on
    # the live-compute fallback path. So this is the cache that actually
    # short-circuits the public landing-page request.
    try:
        conn = get_db(db_target)
        network = get_network_graph(
            conn,
            min_edge_amount=default_min_edge_amount,
            limit=default_network_limit,
            date_from=default_date_from,
            date_to=default_date_to,
        )
        anomalies = get_anomaly_flags(
            conn, limit=default_anomaly_limit,
            date_from=default_date_from, date_to=default_date_to,
        )
        concentration = get_donor_concentration(
            conn, limit=default_concentration_limit,
            date_from=default_date_from, date_to=default_date_to,
        )
        geo_summary = _empty_geo_summary()
        time_series = get_time_series(
            conn, months=default_months,
            date_from=default_date_from, date_to=default_date_to,
        )
        nlp_summary = get_nlp_spending_summary(conn, limit=default_nlp_limit)
        reconciliation = get_reconciliation_outliers(
            conn, limit=default_recon_limit,
            min_abs_diff=default_recon_min_abs_diff,
        )

        state_race_table_available = all(
            _table_exists(conn, table_name)
            for table_name in (
                "bulk_candidate_committee_finance_agg",
                "bulk_receipts_clean",
                "bulk_expenditures_clean",
            )
        )
        state_race_all_rows = get_state_race_analytics(
            conn,
            limit=5000,
            date_from=default_date_from,
            date_to=default_date_to,
            election_cycle=default_election_cycle,
            sort_by=default_state_race_sort,
            sort_dir=default_state_race_dir,
        )
        state_race_analytics = state_race_all_rows[:12]
        visible_has_nonzero_outside = any(
            float(row.get("outside_spending_total") or 0.0) > 0.0
            for row in state_race_analytics
        )
        hidden_has_nonzero_outside = any(
            float(row.get("outside_spending_total") or 0.0) > 0.0
            for row in state_race_all_rows[12:]
        )
        state_race_nonzero_below_visible = (
            bool(state_race_analytics)
            and (not visible_has_nonzero_outside)
            and hidden_has_nonzero_outside
        )

        now = time.monotonic()
        # MUST match the cache_key tuple constructed at lines ~427-440 of this
        # module (dashboard() route). If you change one, change both.
        cache_key = (
            default_period_key,
            default_date_from,
            default_date_to,
            default_min_edge_amount,
            default_network_limit,
            default_anomaly_limit,
            default_concentration_limit,
            default_months,
            default_nlp_limit,
            default_election_cycle,
            default_state_race_sort,
            default_state_race_dir,
        )
        with _overview_cache_lock:
            _overview_cache["payload"] = {
                "network": network,
                "anomalies": anomalies,
                "concentration": concentration,
                "geo_summary": geo_summary,
                "time_series": time_series,
                "nlp_summary": nlp_summary,
                "reconciliation": reconciliation,
                "state_race_analytics": state_race_analytics,
                "state_race_table_available": state_race_table_available,
                "state_race_nonzero_below_visible": state_race_nonzero_below_visible,
            }
            _overview_cache["key"] = cache_key
            _overview_cache["expires_at"] = now + float(overview_ttl)
        logger.info("Analytics prewarm: overview cache populated")
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm: overview cache failed")
