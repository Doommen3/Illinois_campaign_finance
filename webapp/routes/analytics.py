"""Analytics dashboard routes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import json
import logging
import time

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from database.analytics import (
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
    sort_state_race_rows,
    get_state_race_detail,
    get_state_race_details,
    get_state_federal_overlap_graph,
    get_time_series,
    get_vendor_expenditure_network,
)
import database.analytics as analytics_db
from database.connection import get_db, close_db
from database.race_keys import canonical_race_key
from webapp.cache_backend import RouteCache
from webapp.utils.data_through import latest_data_date
from webapp.utils.time_scope import (
    current_cycle, cycle_window, resolve_time_scope, scope_cache_key, scope_cycles, scope_params,
)

analytics_bp = Blueprint("analytics", __name__)

FULL_SNAPSHOT_TTL_SECONDS = 900
# TTL when app.config has no analytics cache key (config.py ships 21600 = 6 h).
ANALYTICS_CACHE_TTL_FALLBACK_SECONDS = 21600

# Route-level caches. Backed by Redis when REDIS_URL is set, with in-process
# dict fallback for dev / tests / when Redis is unreachable. See
# webapp/cache_backend.py for the abstraction.
_relationships_cache = RouteCache("analytics_relationships")
_networks_cache = RouteCache("analytics_networks")
_overview_cache = RouteCache("analytics_overview")
_risk_cache = RouteCache("analytics_risk")
_geography_cache = RouteCache("analytics_geography")
_donors_cache = RouteCache("analytics_donors")
_state_race_detail_cache = RouteCache("analytics_state_race_detail")
# Every race of one cycle: the overview shows its first 12 rows, /analytics/state-races/ all.
_state_races_cache = RouteCache("analytics_state_races")
# First element of the race-list and overview keys: bump when the race builder's numbers change under an unchanged row shape, so entries built by older code are never read.
_STATE_RACE_CACHE_REV = "os3"  # os3 (2026-10-28): overview payload carries state_race_previews; race-list key has no sort
# Graphs whose builders take no dates (plan B3 / J-S15): the relationships co-giving, similarity
# and competition graphs and the networks overlap graph. One build serves every window: keyed
# by the builder's name and its own arguments, never the window, so ?period=all reuses the
# build a default request (or the prewarm) made.
_window_free_graphs_cache = RouteCache("analytics_window_free_graphs")
# Fixed arguments of the networks overlap graph (the route and the prewarm build one key).
_OVERLAP_GRAPH_KWARGS = {"donor_limit": 100, "edge_limit": 600}
_geo_drilldown_cache = RouteCache("analytics_geo_drilldown")


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


def _iso_or_empty(day: date | None) -> str:
    return day.isoformat() if day else ""


def _months_for_scope(scope) -> int:
    """Calendar months the scope window spans (inclusive), 120 when it has no start.

    Used when ``?months=`` is absent so "All time" is not silently cut to 24 months.
    """
    if scope.start is None:
        return 120
    end = scope.end or date.today()
    span = (end.year - scope.start.year) * 12 + end.month - scope.start.month + 1
    return min(max(span, 1), 120)


def _parse_filters(axis: str = "received") -> dict:
    scope = resolve_time_scope(default="cycle:current", axis=axis, data_through=latest_data_date("isbe"))
    load_mode = (request.args.get("load_mode", "quick", type=str) or "quick").strip().lower()
    if load_mode not in {"quick", "full"}:
        load_mode = "quick"

    min_edge_amount = max(request.args.get("min_edge_amount", 1000.0, type=float) or 1000.0, 0.0)
    network_limit = min(max(request.args.get("network_limit", 200, type=int) or 200, 50), 5000)
    anomaly_limit = min(max(request.args.get("anomaly_limit", 25, type=int) or 25, 1), 500)
    concentration_limit = min(max(request.args.get("concentration_limit", 25, type=int) or 25, 1), 500)
    # An explicit ?months= wins; otherwise the number derives from the scope. Only the
    # explicit value (months_param) is carried into links, so a derived count is never
    # frozen into a link and then caps a later "All time" click.
    months_arg = request.args.get("months", type=int)
    months_param = min(max(months_arg, 1), 120) if months_arg else None
    months = months_param or _months_for_scope(scope)
    geo_state_limit = min(max(request.args.get("geo_state_limit", 15, type=int) or 15, 1), 100)
    geo_city_limit = min(max(request.args.get("geo_city_limit", 25, type=int) or 25, 1), 500)
    nlp_limit = min(max(request.args.get("nlp_limit", 20, type=int) or 20, 1), 500)
    recon_limit = min(max(request.args.get("recon_limit", 20, type=int) or 20, 1), 500)
    recon_min_abs_diff = max(request.args.get("recon_min_abs_diff", 1000.0, type=float) or 1000.0, 0.0)

    date_from, date_to = _iso_or_empty(scope.start), _iso_or_empty(scope.end)

    # Allow-list of analytics filters that should survive a subnav tab change.
    # The dict is expanded into every url_for(...) in analytics/_subnav.html
    # so users don't lose their tuning when they hop between Overview /
    # Networks / Risk / Donors / Geography / Relationships.
    #
    # Excluded by design: refresh_cache / refresh_full / sync_full (one-shot
    # actions); state_race_sort / state_race_dir / election_cycle / q (page-
    # specific UI controls); geo_type / geo_value / geo_state /
    # source_page / page / per_page / sort / dir (drilldown-specific).
    subnav_persist_params = {
        "load_mode": load_mode,
        # The scope travels as its key (period=cycle:2024); resolved dates only
        # travel for a user-typed custom range (time-scope overhaul, step Q1).
        **scope_params(scope),
        "min_edge_amount": min_edge_amount,
        "network_limit": network_limit,
        "anomaly_limit": anomaly_limit,
        "concentration_limit": concentration_limit,
        "months": months_param,
        "geo_state_limit": geo_state_limit,
        "geo_city_limit": geo_city_limit,
        "nlp_limit": nlp_limit,
    }

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
        "months_param": months_param,
        "geo_state_limit": geo_state_limit,
        "geo_city_limit": geo_city_limit,
        "nlp_limit": nlp_limit,
        "recon_limit": recon_limit,
        "recon_min_abs_diff": recon_min_abs_diff,
        "date_from": date_from,
        "date_to": date_to,
        "time_scope": scope,
        "scope_default_key": f"cycle:{current_cycle()}",
        "time_scope_source_label": "ISBE",
        "scope_query": scope_params(scope),
        "time_period_key": scope_cache_key(scope),
        "subnav_persist_params": subnav_persist_params,
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


def _load_snapshot_state(conn, filters: dict) -> dict:
    """The stored full snapshot for ``load_mode=full``, read only.

    A GET never builds, queues or refreshes a snapshot (nor the materialized tables a build
    may refresh), whatever ``refresh_full`` / ``sync_full`` say: it serves the stored one, stale
    or not, and the pages compute their sections live when there is none (plan L8).
    """
    state = {
        "payload": None,
        "snapshot_meta": None,
        "heavy_sections_loaded": False,
        "data_sources": get_analytics_data_sources(conn),
    }
    if not filters["full_mode_requested"]:
        return state

    snapshot_meta = get_dashboard_snapshot(
        conn, params=_snapshot_params_from_filters(filters), ttl_seconds=FULL_SNAPSHOT_TTL_SECONDS,
    )
    if filters["refresh_full"] or filters["sync_full"]:
        logging.getLogger(__name__).info(
            "load_mode=full: refresh_full/sync_full ignored, requests no longer rebuild snapshots; "
            "serving the stored snapshot (status %s, stale %s)",
            snapshot_meta["status"], snapshot_meta["is_stale"],
        )

    state["snapshot_meta"] = snapshot_meta
    payload = snapshot_meta.get("payload")
    if payload:
        state["payload"] = payload
        state["heavy_sections_loaded"] = True
        state["data_sources"] = payload.get("data_sources", state["data_sources"])
    return state


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


def _cached_build(cache: RouteCache, cache_key, builder, ttl_seconds: int, *, cache_enabled: bool, refresh: bool):
    """``builder()`` through ``cache``: one build per key across threads and workers (get_or_compute).

    ``refresh`` (``?refresh_cache=1``) rebuilds and stores; with route caching off it only builds.
    """
    if not cache_enabled:
        return builder()
    if refresh:
        payload = builder()
        if payload is not None:
            cache.set(cache_key, payload, ttl_seconds)
        return payload
    return cache.get_or_compute(cache_key, builder, ttl_seconds)


def _window_free_key(func, kwargs: dict) -> tuple:
    """Cache key of a graph builder that takes no dates: its name and its arguments, no window."""
    return (func.__name__, tuple(sorted(kwargs.items())))


def _graph_failed(graph: dict) -> bool:
    """True for the empty, error-flagged graph _compute_graph_with_own_conn returns for a failed build."""
    return bool((graph.get("summary") or {}).get("error"))


def _window_free_graph(conn, func, kwargs: dict, *, cache_enabled: bool, refresh: bool, ttl_seconds: int) -> dict:
    """``func(conn, **kwargs)`` for a builder that takes no dates, built once for every window."""
    return _cached_build(
        _window_free_graphs_cache, _window_free_key(func, kwargs), lambda: func(conn, **kwargs), ttl_seconds,
        cache_enabled=cache_enabled, refresh=refresh,
    )


def _networks_graphs(*, db_target: str, time_period_key: str, date_from: str, date_to: str,
                     min_edge_amount: float, network_limit: int, network_from_snapshot: dict | None,
                     cache_enabled: bool, refresh: bool, ttl_seconds: int) -> dict:
    """The five /analytics/networks graphs; the route and warm_analytics_caches share these keys.

    The analytics, vendor, lobbying and 527 graphs take the window and are cached per window;
    the state-federal overlap graph takes no dates and is cached once for every window, except
    a failed build. Each of the two entries builds once per key (get_or_compute); the overlap
    graph builds on the executor alongside the windowed graphs, which build in parallel, each
    with its own DB connection.
    """
    empty_graph = {"nodes": [], "edges": [], "centrality": [], "summary": {"node_count": 0, "edge_count": 0}}
    cache_key = (time_period_key, date_from, date_to, min_edge_amount, network_limit)
    overlap_key = _window_free_key(get_state_federal_overlap_graph, _OVERLAP_GRAPH_KWARGS)

    def build_overlap() -> dict:
        _key, graph = _compute_graph_with_own_conn(
            "overlap_graph", get_state_federal_overlap_graph, (), dict(_OVERLAP_GRAPH_KWARGS), empty_graph, db_target,
        )
        return graph

    def build_windowed() -> dict:
        graph_jobs: list[tuple[str, object, tuple, dict, dict]] = []
        if network_from_snapshot is None:
            graph_jobs.append((
                "network", get_network_graph, (),
                {"min_edge_amount": min_edge_amount, "limit": network_limit,
                 "date_from": date_from, "date_to": date_to},
                _empty_network(),
            ))
        graph_jobs.extend([
            ("vendor_network", get_vendor_expenditure_network, (),
             {"committee_limit": 60, "vendor_limit": 100, "edge_limit": 800,
              "date_from": date_from, "date_to": date_to}, empty_graph),
            ("lobbying_graph", get_lobbying_influence_graph, (),
             {"client_limit": 80, "edge_limit": 600,
              "date_from": date_from, "date_to": date_to}, empty_graph),
            ("ecosystem_527", get_irs527_ecosystem_graph, (),
             {"org_limit": 80, "edge_limit": 600,
              "date_from": date_from, "date_to": date_to}, empty_graph),
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
        return {
            "network": network_from_snapshot or results.get("network", _empty_network()),
            "vendor_network": results.get("vendor_network", empty_graph),
            "lobbying_graph": results.get("lobbying_graph", empty_graph),
            "ecosystem_527": results.get("ecosystem_527", empty_graph),
        }

    overlap_future = _networks_parallel_executor.submit(
        _cached_build, _window_free_graphs_cache, overlap_key, build_overlap, ttl_seconds,
        cache_enabled=cache_enabled, refresh=refresh,
    )
    windowed = _cached_build(
        _networks_cache, cache_key, build_windowed, ttl_seconds, cache_enabled=cache_enabled, refresh=refresh,
    )
    overlap_graph = overlap_future.result()
    if cache_enabled and _graph_failed(overlap_graph):
        # Shared by every window, so a failed (empty) build is not kept for the TTL.
        _window_free_graphs_cache.invalidate(overlap_key)
    return {**windowed, "overlap_graph": overlap_graph}


def _relationships_graphs(conn, *, time_period_key: str, date_from: str, date_to: str, donor_limit: int,
                          committee_limit: int, candidate_limit: int, client_limit: int, org_limit: int,
                          edge_limit: int, min_shared_amount: float, min_shared_targets: int,
                          min_shared_donors: int, cache_enabled: bool, refresh: bool, ttl_seconds: int) -> dict:
    """The five /analytics/relationships graphs; the route and warm_analytics_caches share these keys.

    The lobbying and 527 graphs take the window and are cached per window; the co-giving,
    similarity and competition graphs take no dates and are cached once for every window.
    """
    def build_windowed() -> dict:
        return {
            "lobbying_influence": get_lobbying_influence_graph(
                conn,
                client_limit=client_limit,
                edge_limit=edge_limit,
                date_from=date_from,
                date_to=date_to,
            ),
            "ecosystem_527": get_irs527_ecosystem_graph(
                conn,
                org_limit=org_limit,
                edge_limit=edge_limit,
                date_from=date_from,
                date_to=date_to,
            ),
        }

    windowed = _cached_build(
        _relationships_cache, (time_period_key, date_from, date_to, client_limit, org_limit, edge_limit),
        build_windowed, ttl_seconds, cache_enabled=cache_enabled, refresh=refresh,
    )

    window_free = {"cache_enabled": cache_enabled, "refresh": refresh, "ttl_seconds": ttl_seconds}
    return {
        "donor_cogiving": _window_free_graph(conn, get_donor_cogiving_network, {
            "donor_limit": donor_limit,
            "edge_limit": edge_limit,
            "min_shared_amount": min_shared_amount,
            "min_shared_targets": min_shared_targets,
        }, **window_free),
        "committee_similarity": _window_free_graph(conn, get_committee_similarity_network, {
            "committee_limit": committee_limit,
            "edge_limit": edge_limit,
            "min_shared_donors": min_shared_donors,
            "min_shared_amount": min_shared_amount,
        }, **window_free),
        "candidate_competition": _window_free_graph(conn, get_candidate_competition_networks, {
            "candidate_limit": candidate_limit,
            "edge_limit": edge_limit,
            "min_shared_donors": min_shared_donors,
            "min_shared_amount": min_shared_amount,
        }, **window_free),
        **windowed,
    }


def _base_context(active_page: str, filters: dict, snapshot_state: dict) -> dict:
    return {
        "active_page": active_page,
        "snapshot_ttl_seconds": FULL_SNAPSHOT_TTL_SECONDS,
        **filters,
        **snapshot_state,
    }


_STATE_RACE_SORTS = (  # every Top State Races column; the cached race list is sorted after the read (plan N2/N3)
    "race", "candidate_count", "donor_count", "contribution_count",
    "total_amount", "outside_spending_total", "outside_pressure_ratio", "top_candidate_share",
)
# The relations the race builder reads; it returns no rows when any of them is missing.
_STATE_RACE_RELATIONS = (
    "isbe_race_candidacies",
    "bulk_candidate_committee_finance_agg",
    "bulk_receipts_clean",
    "bulk_expenditures_clean",
)


def _window_cycle(scope) -> int | None:
    """The one election cycle the window lies in; None for all time or a window spanning cycles."""
    cycles = scope_cycles(scope)
    return cycles[0] if cycles and len(cycles) == 1 else None


def _state_race_cycle(scope, election_cycle: int | None, today: date | None = None) -> int:
    """Candidate cycle of the state-race pages (plan J-R4): an explicit ``?election_cycle=``
    wins, then the window's own cycle, else the current cycle by date."""
    if election_cycle is not None:
        return int(election_cycle)
    return _window_cycle(scope) or analytics_db.current_election_cycle(today)


def _state_races_cache_key(scope, election_cycle: int | None, today: date | None = None) -> tuple:
    """Key of the shared full race list: the revision, the window and the RESOLVED cycle.

    No sort: one unsorted list per window and cycle serves every sort order, applied after the
    cache read (plan N3). The cycle is resolved, never None: ``period=all`` has the same window
    all year, so only the cycle keeps a list built before a cycle rollover from being served
    after it. The revision (_STATE_RACE_CACHE_REV, read when the key is built) comes first.
    """
    return (_STATE_RACE_CACHE_REV, scope_cache_key(scope), _state_race_cycle(scope, election_cycle, today))


def _state_race_sort_args(sort_param: str, dir_param: str) -> tuple[str, str]:
    """(sort field, direction) from the query string; unknown values fall back to total_amount, desc."""
    sort_by = (request.args.get(sort_param, "total_amount", type=str) or "total_amount").strip().lower()
    if sort_by not in _STATE_RACE_SORTS:
        sort_by = "total_amount"
    sort_dir = "asc" if (request.args.get(dir_param, "desc", type=str) or "").strip().lower() == "asc" else "desc"
    return sort_by, sort_dir


def _state_race_tables_available(conn) -> bool:
    return all(_table_exists(conn, table_name) for table_name in _STATE_RACE_RELATIONS)


def _get_state_race_rows(conn, scope, election_cycle, *, cache_enabled, refresh, ttl_seconds) -> list[dict]:
    """Every race of the resolved cycle in the builder's default order: the one cached build
    behind the overview's Top State Races and /analytics/state-races/. Callers order a COPY with
    sort_state_race_rows; the cached list is shared and never reordered."""
    cycle = _state_race_cycle(scope, election_cycle)

    def build() -> list[dict]:
        return get_state_race_analytics(
            conn,
            limit=100_000,  # no cap: the largest cycle has 1,761 races (local ilcf, 2026-10-26)
            date_from=_iso_or_empty(scope.start),
            date_to=_iso_or_empty(scope.end),
            election_cycle=cycle,
        )

    return _cached_build(
        _state_races_cache, _state_races_cache_key(scope, cycle), build, ttl_seconds,
        cache_enabled=cache_enabled, refresh=refresh,
    )


_PREVIEW_ROWS = 12


def _state_race_previews(rows: list[dict]) -> dict[str, dict]:
    """``{"<field>:<dir>": {"rows": first 12, "nonzero_below_visible": bool}}`` for every sort
    order of Top State Races, computed once per overview build (plan N3): a header click is then
    a cache hit that picks its variant."""
    previews: dict[str, dict] = {}
    for field in _STATE_RACE_SORTS:
        for direction in ("desc", "asc"):
            ordered = sort_state_race_rows(rows, field, direction)
            visible, hidden = ordered[:_PREVIEW_ROWS], ordered[_PREVIEW_ROWS:]
            visible_nonzero = any(float(row.get("outside_spending_total") or 0.0) > 0.0 for row in visible)
            hidden_nonzero = any(float(row.get("outside_spending_total") or 0.0) > 0.0 for row in hidden)
            previews[f"{field}:{direction}"] = {
                "rows": visible,
                "nonzero_below_visible": bool(visible) and not visible_nonzero and hidden_nonzero,
            }
    return previews


def _state_race_sort_links(sort_by: str, sort_dir: str) -> dict[str, str]:
    """Header-link targets of the preview: the current request with ``state_race_sort`` /
    ``state_race_dir`` replaced (the active column flips its direction; numeric columns descend
    first, the race name ascends first), anchored at the table. One-shot actions never carry."""
    dropped = {"state_race_sort", "state_race_dir", "refresh_cache", "refresh_full", "sync_full"}
    base = {
        key: value for key, value in request.args.to_dict().items()
        if key not in dropped and not key.startswith("_")
    }
    links: dict[str, str] = {}
    for field in _STATE_RACE_SORTS:
        if field == sort_by:
            direction = "asc" if sort_dir == "desc" else "desc"
        else:
            direction = "asc" if field == "race" else "desc"
        links[field] = url_for(
            "analytics.dashboard", state_race_sort=field, state_race_dir=direction, _anchor="top-state-races", **base
        )
    return links


def _state_race_detail_cache_key(race_key: str, scope, election_cycle: int | None) -> tuple:
    """Key of one race's detail payload: (race key, window, resolved cycle)."""
    return (race_key, scope_cache_key(scope), _state_race_cycle(scope, election_cycle))


def _get_state_race_detail_cached(conn, race_key, scope, election_cycle, *, cache_enabled, refresh,
                                  ttl_seconds) -> dict | None:
    """One race's detail payload, cached under _state_race_detail_cache_key.

    get_state_race_detail is a targeted lookup, but still seconds for the largest races.
    warm_analytics_caches fills the overview's twelve races under the same key, so a
    prewarmed entry is exactly the one a request reads.
    """
    cycle = _state_race_cycle(scope, election_cycle)
    cache_key = _state_race_detail_cache_key(race_key, scope, cycle)

    def build() -> dict | None:
        detail = get_state_race_detail(
            conn,
            race_key=race_key,
            date_from=_iso_or_empty(scope.start),
            date_to=_iso_or_empty(scope.end),
            election_cycle=cycle,
        )
        return detail or None  # an unknown race (404) is not cached

    detail = _cached_build(
        _state_race_detail_cache, cache_key, build, ttl_seconds, cache_enabled=cache_enabled, refresh=refresh,
    )
    # A payload without outside_spending_window was cached by an older release (its outside
    # rows have the old keys too): recomputed and stored again.
    if detail is not None and "outside_spending_window" not in detail:
        detail = _cached_build(
            _state_race_detail_cache, cache_key, build, ttl_seconds, cache_enabled=cache_enabled, refresh=True,
        )
    return detail


def _overview_cache_key(filters: dict, state_race_cycle: int) -> tuple:
    """Key of the /analytics/ payload: the revision plus every input build_overview reads.

    Not the race sort: the payload holds the top twelve of every sort order (plan N3).

    Quick and full mode get separate entries, and the geo limits count only in full mode (in
    quick mode the geo section is the empty placeholder whatever they are).
    warm_analytics_caches builds a bare request's key with it.
    """
    return (
        _STATE_RACE_CACHE_REV,
        filters["time_period_key"],
        filters["date_from"],
        filters["date_to"],
        filters["min_edge_amount"],
        filters["network_limit"],
        filters["anomaly_limit"],
        filters["concentration_limit"],
        filters["months"],
        filters["nlp_limit"],
        state_race_cycle,
        filters["recon_limit"],
        round(filters["recon_min_abs_diff"], 2),
        filters["full_mode_requested"],
        filters["geo_state_limit"] if filters["full_mode_requested"] else None,
        filters["geo_city_limit"] if filters["full_mode_requested"] else None,
    )


@analytics_bp.route("/", endpoint="dashboard")
@analytics_bp.route("/overview", endpoint="overview")
def dashboard():
    """Render the analytics overview page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}
    state_race_sort, state_race_dir = _state_race_sort_args("state_race_sort", "state_race_dir")

    # --- TTL cache for overview aggregates ---
    overview_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    election_cycle = request.args.get("election_cycle", type=int)
    state_race_cycle = _state_race_cycle(filters["time_scope"], election_cycle)
    cache_key = _overview_cache_key(filters, state_race_cycle)
    now = time.monotonic()

    def build_overview() -> dict:
        if snapshot_state["heavy_sections_loaded"]:
            sections = {
                "network": payload.get("network", _empty_network()),
                "anomalies": payload.get("anomalies", []),
                "concentration": payload.get("concentration", []),
                "geo_summary": payload.get("geo_summary", _empty_geo_summary()),
                "time_series": payload.get("time_series", []),
                "nlp_summary": payload.get("nlp_summary", []),
                "reconciliation": payload.get("reconciliation", []),
            }
        else:
            sections = {
                "network": _safe_optional_graph(
                    "network",
                    lambda: get_network_graph(
                        conn,
                        min_edge_amount=filters["min_edge_amount"],
                        limit=filters["network_limit"],
                        date_from=filters["date_from"],
                        date_to=filters["date_to"],
                    ),
                    _empty_network(),
                ),
                "anomalies": get_anomaly_flags(
                    conn,
                    limit=filters["anomaly_limit"],
                    date_from=filters["date_from"],
                    date_to=filters["date_to"],
                ),
                "concentration": get_donor_concentration(
                    conn,
                    limit=filters["concentration_limit"],
                    date_from=filters["date_from"],
                    date_to=filters["date_to"],
                ),
                # Full mode without a stored snapshot: the preview's geo rows are computed here
                # (as the snapshot build does), since a GET no longer builds the snapshot.
                "geo_summary": get_geo_summary(
                    conn,
                    limit_states=filters["geo_state_limit"],
                    limit_cities=filters["geo_city_limit"],
                    date_from=filters["date_from"],
                    date_to=filters["date_to"],
                ) if filters["full_mode_requested"] else _empty_geo_summary(),
                "time_series": get_time_series(
                    conn,
                    months=filters["months"],
                    date_from=filters["date_from"],
                    date_to=filters["date_to"],
                ),
                "nlp_summary": get_nlp_spending_summary(conn, limit=filters["nlp_limit"]),
                "reconciliation": get_reconciliation_outliers(
                    conn,
                    limit=filters["recon_limit"],
                    min_abs_diff=filters["recon_min_abs_diff"],
                ),
            }

        # One cached build of every race, shared with /analytics/state-races/; the
        # overview keeps the first 12 rows of EVERY sort order (plan N3).
        state_race_all_rows = _get_state_race_rows(
            conn,
            filters["time_scope"],
            state_race_cycle,
            cache_enabled=cache_enabled,
            refresh=refresh_requested,
            ttl_seconds=overview_cache_ttl,
        )
        return {
            **sections,
            "state_race_previews": _state_race_previews(state_race_all_rows),
            "state_race_total": len(state_race_all_rows),
            "state_race_table_available": bool(state_race_all_rows) or _state_race_tables_available(conn),
        }

    overview = _cached_build(
        _overview_cache, cache_key, build_overview, overview_cache_ttl,
        cache_enabled=cache_enabled, refresh=refresh_requested,
    )
    snapshot_state["heavy_sections_loaded"] = True
    network = overview["network"]
    anomalies = overview["anomalies"]
    concentration = overview["concentration"]
    geo_summary = overview["geo_summary"]
    time_series = overview["time_series"]
    nlp_summary = overview["nlp_summary"]
    reconciliation = overview["reconciliation"]
    # The sort picks a pre-sorted twelve out of the cached payload: no rebuild, no mutation.
    preview = overview["state_race_previews"].get(f"{state_race_sort}:{state_race_dir}") or {
        "rows": [], "nonzero_below_visible": False,
    }
    state_race_analytics = preview["rows"]
    state_race_total = overview["state_race_total"]
    state_race_table_available = overview["state_race_table_available"]
    state_race_nonzero_below_visible = preview["nonzero_below_visible"]

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
        state_race_total=state_race_total,
        state_race_cycle=state_race_cycle,
        show_state_race_cycle=state_race_cycle != _window_cycle(filters["time_scope"]),
        state_race_sort=state_race_sort,
        state_race_dir=state_race_dir,
        state_race_sort_links=_state_race_sort_links(state_race_sort, state_race_dir),
        state_race_nonzero_below_visible=state_race_nonzero_below_visible,
        election_cycle=election_cycle,
    )


@analytics_bp.route("/state-races/", endpoint="state_races")
def state_races():
    """Render every state race of the resolved cycle; races with activity in the window link to their detail."""
    conn = current_app.get_database()
    filters = _parse_filters()
    scope = filters["time_scope"]
    sort_by, sort_dir = _state_race_sort_args("sort", "dir")
    election_cycle = request.args.get("election_cycle", type=int)
    state_race_cycle = _state_race_cycle(scope, election_cycle)
    rows = sort_state_race_rows(_get_state_race_rows(
        conn,
        scope,
        state_race_cycle,
        cache_enabled=bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False))),
        refresh=request.args.get("refresh_cache", 0, type=int) == 1,
        ttl_seconds=max(15, int(current_app.config.get("ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS))),
    ), sort_by, sort_dir)
    # Activity (plan J-R2) = receipts or Part-9 outside spending in the window. D-2 totals
    # are not windowed, so a race with only a D-2 total stays unlinked.
    race_rows = [
        (row, float(row["total_amount"] or 0.0) > 0.0 or float(row["outside_spending_total"] or 0.0) > 0.0)
        for row in rows
    ]
    return render_template(
        "analytics/state_races.html",
        active_page="overview",
        time_scope=scope,
        scope_default_key=filters["scope_default_key"],
        time_scope_source_label=filters["time_scope_source_label"],
        scope_query=filters["scope_query"],
        subnav_persist_params=filters["subnav_persist_params"],
        election_cycle=election_cycle,
        state_race_cycle=state_race_cycle,
        show_state_race_cycle=state_race_cycle != _window_cycle(scope),
        state_race_table_available=bool(rows) or _state_race_tables_available(conn),
        race_rows=race_rows,
        active_count=sum(1 for _row, active in race_rows if active),
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@analytics_bp.route("/state-races/<race_key>")
def state_race_detail(race_key: str):
    """Render a state race detail page."""
    # Keys minted before 2026-10 ended in a sha1 digest; send bookmarks to the
    # current key with the query string untouched.
    canonical_key = canonical_race_key(race_key)
    if canonical_key != race_key:
        target = url_for("analytics.state_race_detail", race_key=canonical_key)
        query = request.query_string.decode()
        return redirect(f"{target}?{query}" if query else target, code=301)

    conn = current_app.get_database()
    filters = _parse_filters()
    election_cycle = request.args.get("election_cycle", type=int)
    state_race_cycle = _state_race_cycle(filters["time_scope"], election_cycle)

    # TTL piggybacks on the overview cache TTL: same data domain, same refresh cadence.
    state_race_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    race_detail = _get_state_race_detail_cached(
        conn,
        race_key,
        filters["time_scope"],
        state_race_cycle,
        cache_enabled=cache_enabled,
        refresh=refresh_requested,
        ttl_seconds=state_race_cache_ttl,
    )
    if not race_detail:
        abort(404)

    # Outside Spending rows link to each candidate's line items over the window the row was
    # cut to (the cycle window narrowed by the page window), not over the page scope.
    outside_window = race_detail["outside_spending_window"]
    cycle_start, cycle_end = cycle_window(state_race_cycle)
    if (outside_window["date_from"], outside_window["date_to"]) == (cycle_start.isoformat(), cycle_end.isoformat()):
        outside_link_query = {"period": f"cycle:{state_race_cycle}"}
    else:
        outside_link_query = {"date_from": outside_window["date_from"], "date_to": outside_window["date_to"]}

    return render_template(
        "analytics/state_race_detail.html",
        active_page="overview",
        load_mode=filters["load_mode"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        time_scope=filters["time_scope"],
        scope_default_key=filters["scope_default_key"],
        time_scope_source_label=filters["time_scope_source_label"],
        scope_query=filters["scope_query"],
        subnav_persist_params=filters["subnav_persist_params"],
        election_cycle=election_cycle,
        state_race_cycle=state_race_cycle,
        show_state_race_cycle=state_race_cycle != _window_cycle(filters["time_scope"]),
        race_detail=race_detail,
        outside_link_query=outside_link_query,
    )


@analytics_bp.route("/networks")
def networks():
    """Render network analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    # --- TTL caches: windowed graphs per window, the overlap graph once (_networks_graphs) ---
    networks_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_NETWORKS_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1

    # Use snapshot data for network if available, otherwise compute it with the other graphs.
    network_from_snapshot = None
    if snapshot_state["heavy_sections_loaded"]:
        network_from_snapshot = payload.get("network", _empty_network())

    graphs = _networks_graphs(
        db_target=current_app.config.get("DATABASE_TARGET", ""),
        time_period_key=filters["time_period_key"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        min_edge_amount=filters["min_edge_amount"],
        network_limit=filters["network_limit"],
        network_from_snapshot=network_from_snapshot,
        cache_enabled=cache_enabled,
        refresh=refresh_requested,
        ttl_seconds=networks_cache_ttl,
    )
    snapshot_state["heavy_sections_loaded"] = True

    return render_template(
        "analytics/networks.html",
        **_base_context("networks", filters, snapshot_state),
        network=graphs["network"],
        vendor_network=graphs["vendor_network"],
        overlap_graph=graphs["overlap_graph"],
        lobbying_graph=graphs["lobbying_graph"],
        ecosystem_527=graphs["ecosystem_527"],
    )


@analytics_bp.route("/risk")
def risk():
    """Render anomaly and reconciliation analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()  # get_anomaly_flags windows the contribution receipt date
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    # --- TTL cache for risk aggregates ---
    risk_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_RISK_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
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

    def build_risk() -> dict:
        if snapshot_state["heavy_sections_loaded"]:
            return {
                "anomalies": payload.get("anomalies", []),
                "reconciliation": payload.get("reconciliation", []),
            }
        return {
            "anomalies": get_anomaly_flags(
                conn,
                limit=filters["anomaly_limit"],
                date_from=filters["date_from"],
                date_to=filters["date_to"],
            ),
            "reconciliation": get_reconciliation_outliers(
                conn,
                limit=filters["recon_limit"],
                min_abs_diff=filters["recon_min_abs_diff"],
            ),
        }

    risk_payload = _cached_build(
        _risk_cache, cache_key, build_risk, risk_cache_ttl, cache_enabled=cache_enabled, refresh=refresh_requested,
    )
    snapshot_state["heavy_sections_loaded"] = True

    return render_template(
        "analytics/risk.html",
        **_base_context("risk", filters, snapshot_state),
        anomalies=risk_payload["anomalies"],
        reconciliation=risk_payload["reconciliation"],
    )


@analytics_bp.route("/donors")
def donors():
    """Render donor concentration and NLP analytics page."""
    conn = current_app.get_database()
    filters = _parse_filters()
    snapshot_state = _load_snapshot_state(conn, filters)
    payload = snapshot_state["payload"] or {}

    # --- TTL cache for donor aggregates ---
    donors_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_DONORS_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    cache_key = (
        filters["time_period_key"],
        filters["date_from"],
        filters["date_to"],
        filters["concentration_limit"],
        filters["nlp_limit"],
    )

    cached_payload = None
    if cache_enabled and not refresh_requested:
        cached_payload = _donors_cache.get(cache_key)

    if cached_payload is not None:
        concentration = cached_payload["concentration"]
        nlp_summary = cached_payload["nlp_summary"]
        snapshot_state["heavy_sections_loaded"] = True
    else:
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
        if cache_enabled:
            _donors_cache.set(
                cache_key,
                {
                    "concentration": concentration,
                    "nlp_summary": nlp_summary,
                },
                donors_cache_ttl,
            )

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

    # --- TTL cache for geography aggregates ---
    geography_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    cache_key = (
        filters["time_period_key"],
        filters["date_from"],
        filters["date_to"],
        filters["geo_state_limit"],
        filters["geo_city_limit"],
        filters["months"],
    )

    cached_payload = None
    if cache_enabled and not refresh_requested:
        cached_payload = _geography_cache.get(cache_key)

    if cached_payload is not None:
        geo_summary = cached_payload["geo_summary"]
        time_series = cached_payload["time_series"]
        snapshot_state["heavy_sections_loaded"] = True
    else:
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
        if cache_enabled:
            _geography_cache.set(
                cache_key,
                {
                    "geo_summary": geo_summary,
                    "time_series": time_series,
                },
                geography_cache_ttl,
            )

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

    page = max(request.args.get("page", 1, type=int) or 1, 1)
    per_page = min(max(request.args.get("per_page", 50, type=int) or 50, 10), 250)
    sort_by = (request.args.get("sort", "total_amount", type=str) or "total_amount").strip().lower()
    sort_dir = (request.args.get("dir", "desc", type=str) or "desc").strip().lower()

    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    cache_ttl = max(15, int(current_app.config.get("ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    cache_params = {
        "version": 1,
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

    drilldown = _geo_drilldown_cache.get(cache_key) if cache_enabled else None
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
            _geo_drilldown_cache.set(cache_key, drilldown, cache_ttl)

    if source_page == "overview":
        back_url = url_for(
            "analytics.dashboard",
            load_mode=filters["load_mode"],
            months=filters["months_param"],
            geo_state_limit=filters["geo_state_limit"],
            geo_city_limit=filters["geo_city_limit"],
            **filters["scope_query"],
        )
    else:
        back_url = url_for(
            "analytics.geography",
            load_mode=filters["load_mode"],
            months=filters["months_param"],
            geo_state_limit=filters["geo_state_limit"],
            geo_city_limit=filters["geo_city_limit"],
            **filters["scope_query"],
        )

    return render_template(
        "analytics/geo_drilldown.html",
        active_page="geography",
        **filters,
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
    relationships_cache_ttl = max(15, int(current_app.config.get("ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh_requested = request.args.get("refresh_cache", 0, type=int) == 1
    payload = _relationships_graphs(
        conn,
        time_period_key=filters["time_period_key"],
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
        cache_enabled=cache_enabled,
        refresh=refresh_requested,
        ttl_seconds=relationships_cache_ttl,
    )

    return render_template(
        "analytics/relationships.html",
        active_page="relationships",
        load_mode=filters["load_mode"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        time_scope=filters["time_scope"],
        scope_default_key=filters["scope_default_key"],
        time_scope_source_label=filters["time_scope_source_label"],
        scope_query=filters["scope_query"],
        subnav_persist_params=filters["subnav_persist_params"],
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
    networks_ttl: int = ANALYTICS_CACHE_TTL_FALLBACK_SECONDS,
    relationships_ttl: int = ANALYTICS_CACHE_TTL_FALLBACK_SECONDS,
    risk_ttl: int = ANALYTICS_CACHE_TTL_FALLBACK_SECONDS,
    overview_ttl: int = ANALYTICS_CACHE_TTL_FALLBACK_SECONDS,
    geography_ttl: int = ANALYTICS_CACHE_TTL_FALLBACK_SECONDS,
    donors_ttl: int = ANALYTICS_CACHE_TTL_FALLBACK_SECONDS,
    *,
    scope_key: str = "cycle:current",
) -> None:
    """Pre-populate the heavy analytics route caches for one preset window.

    ``scope_key`` is a preset_options() key ("cycle:2026", "last12m", "all"...; the default is
    the current cycle). Cache keys must match the ones _parse_filters() produces for a request
    with ``?period=<scope_key>`` and no other query parameter (default limits).

    Every block except the top-12 race-detail batch builds through get_or_compute, so a key
    that is already cached, in Redis by any worker or by scripts/warm_caches.py, is not
    rebuilt, and workers warming at the same time build each of those keys once. The race-detail
    batch only checks its keys first and then builds without the lock, so workers warming at
    the same time each build it (2-3 s). scripts/warm_caches.py runs this for the four presets
    after a refresh; create_app's per-worker prewarm runs it for the default window as the
    fallback.
    """
    logger = logging.getLogger(__name__)

    # Resolve the window exactly as _parse_filters() does for ?period=<scope_key> (args given,
    # so no request context is needed). Every prewarmed page, risk included, keys on the
    # "received" axis.
    scope = resolve_time_scope(default="cycle:current", axis="received", args={"period": scope_key})
    period_key = scope_cache_key(scope)
    date_from, date_to = _iso_or_empty(scope.start), _iso_or_empty(scope.end)
    months = _months_for_scope(scope)

    # Default filter values matching _parse_filters() with no other query params
    default_anomaly_limit = 25
    default_recon_limit = 20
    default_recon_min_abs_diff = 1000.0
    default_min_edge_amount = 1000.0
    default_network_limit = 200
    default_concentration_limit = 25
    default_geo_state_limit = 15
    default_geo_city_limit = 25
    default_nlp_limit = 20
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
        _risk_cache.get_or_compute(
            (
                period_key, date_from, date_to,
                default_anomaly_limit, default_recon_limit, default_recon_min_abs_diff,
            ),
            lambda: {
                "anomalies": get_anomaly_flags(
                    conn, limit=default_anomaly_limit,
                    date_from=date_from, date_to=date_to,
                ),
                "reconciliation": get_reconciliation_outliers(
                    conn, limit=default_recon_limit,
                    min_abs_diff=default_recon_min_abs_diff,
                ),
            },
            risk_ttl,
        )
        logger.info("Analytics prewarm %s: risk cache warm", scope_key)
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm %s: risk cache failed", scope_key)

    # --- Networks caches: the window's graphs + the window-free overlap graph ---
    try:
        _networks_graphs(
            db_target=db_target,
            time_period_key=period_key,
            date_from=date_from,
            date_to=date_to,
            min_edge_amount=default_min_edge_amount,
            network_limit=default_network_limit,
            network_from_snapshot=None,
            cache_enabled=True,
            refresh=False,
            ttl_seconds=networks_ttl,
        )
        logger.info("Analytics prewarm %s: networks cache warm", scope_key)
    except Exception:
        logger.exception("Analytics prewarm %s: networks cache failed", scope_key)

    # --- Relationships caches: the window's graphs + the three window-free graphs ---
    # Window-free graphs serve every window, so a second window only builds lobbying and 527.
    try:
        conn = get_db(db_target)
        _relationships_graphs(
            conn,
            time_period_key=period_key,
            date_from=date_from,
            date_to=date_to,
            donor_limit=default_donor_limit,
            committee_limit=default_committee_limit,
            candidate_limit=default_candidate_limit,
            client_limit=default_client_limit,
            org_limit=default_org_limit,
            edge_limit=default_edge_limit,
            min_shared_amount=default_min_shared_amount,
            min_shared_targets=default_min_shared_targets,
            min_shared_donors=default_min_shared_donors,
            cache_enabled=True,
            refresh=False,
            ttl_seconds=relationships_ttl,
        )
        logger.info("Analytics prewarm %s: relationships cache warm", scope_key)
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm %s: relationships cache failed", scope_key)

    # --- Overview cache (dashboard route, /analytics/) ---
    # Without this, the landing-page hit pays the full live-compute cost (~30-40s cold)
    # even when the lower-level networks / risk / relationships caches are populated:
    # the dashboard route reads _overview_cache only, never the lower-level caches.
    try:
        conn = get_db(db_target)
        # Same key as GET /analytics/?period=<scope_key>: dashboard() builds it with _overview_cache_key too.
        window_filters = {
            "time_period_key": period_key,
            "date_from": date_from,
            "date_to": date_to,
            "min_edge_amount": default_min_edge_amount,
            "network_limit": default_network_limit,
            "anomaly_limit": default_anomaly_limit,
            "concentration_limit": default_concentration_limit,
            "months": months,
            "nlp_limit": default_nlp_limit,
            "recon_limit": default_recon_limit,
            "recon_min_abs_diff": default_recon_min_abs_diff,
            "full_mode_requested": False,
            "geo_state_limit": default_geo_state_limit,
            "geo_city_limit": default_geo_city_limit,
        }
        cache_key = _overview_cache_key(window_filters, _state_race_cycle(scope, default_election_cycle))

        def build_overview() -> dict:
            network = get_network_graph(
                conn,
                min_edge_amount=default_min_edge_amount,
                limit=default_network_limit,
                date_from=date_from,
                date_to=date_to,
            )
            anomalies = get_anomaly_flags(
                conn, limit=default_anomaly_limit,
                date_from=date_from, date_to=date_to,
            )
            concentration = get_donor_concentration(
                conn, limit=default_concentration_limit,
                date_from=date_from, date_to=date_to,
            )
            time_series = get_time_series(
                conn, months=months,
                date_from=date_from, date_to=date_to,
            )
            nlp_summary = get_nlp_spending_summary(conn, limit=default_nlp_limit)
            reconciliation = get_reconciliation_outliers(
                conn, limit=default_recon_limit,
                min_abs_diff=default_recon_min_abs_diff,
            )

            # Builds (and caches) the shared race list that /analytics/state-races/ reads too.
            state_race_all_rows = _get_state_race_rows(
                conn,
                scope,
                default_election_cycle,
                cache_enabled=True,
                refresh=False,
                ttl_seconds=overview_ttl,
            )
            return {
                "network": network,
                "anomalies": anomalies,
                "concentration": concentration,
                "geo_summary": _empty_geo_summary(),
                "time_series": time_series,
                "nlp_summary": nlp_summary,
                "reconciliation": reconciliation,
                "state_race_previews": _state_race_previews(state_race_all_rows),
                "state_race_total": len(state_race_all_rows),
                "state_race_table_available": bool(state_race_all_rows) or _state_race_tables_available(conn),
            }

        _overview_cache.get_or_compute(cache_key, build_overview, overview_ttl)
        logger.info("Analytics prewarm %s: overview cache warm", scope_key)
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm %s: overview cache failed", scope_key)

    # --- State race details: the overview's twelve races ---
    # Each overview row links to a detail page whose cold build takes seconds for the
    # largest races. One full build with supporting rows fills all twelve (cheaper than
    # twelve targeted builds), skipped when every one of them is already cached. Runs
    # after the overview so the landing page is warm first; the race list is a cache hit
    # here unless the overview block failed before building it.
    try:
        conn = get_db(db_target)
        state_race_rows = _get_state_race_rows(
            conn,
            scope,
            default_election_cycle,
            cache_enabled=True,
            refresh=False,
            ttl_seconds=overview_ttl,
        )
        detail_keys = {
            row["race_key"]: _state_race_detail_cache_key(row["race_key"], scope, default_election_cycle)
            for row in state_race_rows[:12]
        }
        # A payload without outside_spending_window is an older release's (see _get_state_race_detail_cached).
        missing = [
            race_key for race_key, detail_key in detail_keys.items()
            if "outside_spending_window" not in (_state_race_detail_cache.get(detail_key) or {})
        ]
        if missing:
            details = get_state_race_details(
                conn,
                date_from=date_from,
                date_to=date_to,
                election_cycle=_state_race_cycle(scope, default_election_cycle),
            )
            for race_key in missing:
                if details.get(race_key):
                    _state_race_detail_cache.set(detail_keys[race_key], details[race_key], overview_ttl)
        logger.info("Analytics prewarm %s: top-12 state race details warm", scope_key)
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm %s: state race details failed", scope_key)

    # --- Geography cache (/analytics/geography) ---
    try:
        conn = get_db(db_target)
        _geography_cache.get_or_compute(
            (
                period_key, date_from, date_to,
                default_geo_state_limit, default_geo_city_limit, months,
            ),
            lambda: {
                "geo_summary": get_geo_summary(
                    conn,
                    limit_states=default_geo_state_limit,
                    limit_cities=default_geo_city_limit,
                    date_from=date_from,
                    date_to=date_to,
                ),
                "time_series": get_time_series(
                    conn, months=months,
                    date_from=date_from, date_to=date_to,
                ),
            },
            geography_ttl,
        )
        logger.info("Analytics prewarm %s: geography cache warm", scope_key)
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm %s: geography cache failed", scope_key)

    # --- Donors cache (/analytics/donors) ---
    try:
        conn = get_db(db_target)
        _donors_cache.get_or_compute(
            (
                period_key, date_from, date_to,
                default_concentration_limit, default_nlp_limit,
            ),
            lambda: {
                "concentration": get_donor_concentration(
                    conn, limit=default_concentration_limit,
                    date_from=date_from, date_to=date_to,
                ),
                "nlp_summary": get_nlp_spending_summary(conn, limit=default_nlp_limit),
            },
            donors_ttl,
        )
        logger.info("Analytics prewarm %s: donors cache warm", scope_key)
        close_db(conn)
    except Exception:
        logger.exception("Analytics prewarm %s: donors cache failed", scope_key)
