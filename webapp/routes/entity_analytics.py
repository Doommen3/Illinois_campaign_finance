"""Per-entity analytics tabs for candidate and race detail pages (blueprint ``entity_analytics``).

Plan: .claude/plans/race-sort-and-entity-networks.md -- "Route contract -- blueprint `entity_analytics`",
decisions N10 (tabs mirror the global analytics subnav), N14 (one RouteCache per tab keyed by the resolved
window and cycle, single-flight, never warmed), N15 (the site-wide risk threshold computed once per process)
and N16 (race tabs derive the candidate cycle the way the race detail page does). Routes, no prefix:

    GET /candidates/<int:candidate_id>/<tab>                 candidate tabs (default window: all time)
    GET /candidates/<int:candidate_id>/network.json?view=    the Network tab's lazily fetched JSON (N5)
    GET /analytics/state-races/<race_key>/<tab>              race tabs (default window: the race's cycle)
    GET /analytics/state-races/<race_key>/network.json?view=

``tab`` is one of ENTITY_TABS; anything else, an unknown entity or an unknown ``view`` is a 404 before any
builder runs. The builders are imported by name so the route tests can replace them on this module.
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, render_template, request, url_for

from database.entity_analytics import (
    ENTITY_CACHE_REV,
    _site_large_threshold,
    get_entity_donors,
    get_entity_geography,
    get_entity_relationships,
    get_entity_risk,
    resolve_entity,
)
from database.entity_network import DEFAULT_CAPS, get_entity_network, money_flow, vendor_network
from webapp.cache_backend import RouteCache
from webapp.routes.analytics import ANALYTICS_CACHE_TTL_FALLBACK_SECONDS, _cached_build, _state_race_cycle
from webapp.utils.data_through import latest_data_date
from webapp.utils.time_scope import current_cycle, resolve_time_scope, scope_cache_key, scope_params

bp = Blueprint("entity_analytics", __name__)

ENTITY_TABS = ("network", "risk", "donors", "geography", "relationships")
TAB_LABELS = {
    "network": "Networks",
    "risk": "Risk",
    "donors": "Donors",
    "geography": "Geography",
    "relationships": "Relationships",
}
# The Network tab's JSON views (plan N6 Phase A): the ego payload itself, and two pure projections of it that are
# cached under their own tab name but read the ego entry instead of rebuilding the graph.
NETWORK_VIEWS = ("ego", "money_flow", "vendor_network")
PROJECTIONS = {"money_flow": money_flow, "vendor_network": vendor_network}

# The limits every tab is built with; they close its cache key (plan N14: two cap sets never share an entry).
RISK_LIMIT = 50
DONORS_TOP_N = 25
GEOGRAPHY_LIMITS = (20, 30)           # states, cities
RELATIONSHIPS_LIMITS = (25, 2, 2000)  # committees, min shared donors, donor pool
NETWORK_CAP_CLASSES = ("donors", "vendors", "transfers", "ie_spenders")
NETWORK_CAPS = tuple(DEFAULT_CAPS[cls] for cls in NETWORK_CAP_CLASSES)

_caches = {tab: RouteCache(f"entity_{tab}") for tab in ENTITY_TABS}
_threshold_cache = RouteCache("entity_risk_threshold")


def _entity_cache_key(kind, key, tab, scope, election_cycle, params) -> tuple:
    """(revision, kind, key, tab, window, resolved cycle, limits); the revision is read when the key is built."""
    return (ENTITY_CACHE_REV, kind, str(key), tab, scope_cache_key(scope), election_cycle, params)


def _request_settings() -> tuple[int, bool, bool]:
    """(ttl, route caching on, ``?refresh_cache=1``) -- the refresh key only reaches a view for a signed-in user
    (the app-level gate redirects everyone else before this runs)."""
    ttl = max(15, int(current_app.config.get("ENTITY_ANALYTICS_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS)))
    enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    refresh = request.args.get("refresh_cache", 0, type=int) == 1
    return ttl, enabled, refresh


def _resolve(kind: str, key) -> dict:
    """The entity, its window and its cycle for this request; 404 when ``resolve_entity`` knows no such entity.

    Candidate tabs scope like /candidates/<id> (default all time); race tabs like /analytics/state-races/<key>
    (default the cycle of ``?election_cycle=`` or the current one) and derive the candidate cycle with the race
    page's own rule (plan N16), so every tab lists the candidates Overview lists for the same URL.
    """
    conn = current_app.get_database()
    data_through = latest_data_date("isbe")
    if kind == "candidate":
        scope_default = "all"
        scope = resolve_time_scope(default=scope_default, axis="received", data_through=data_through)
        cycle_arg, cycle = None, None
        entity = resolve_entity(conn, "candidate", key)
    else:
        cycle_arg = request.args.get("election_cycle", type=int)
        scope_default = f"cycle:{cycle_arg or current_cycle()}"
        scope = resolve_time_scope(default=scope_default, axis="received", data_through=data_through)
        cycle = _state_race_cycle(scope, cycle_arg)
        entity = resolve_entity(conn, "race", key, election_cycle=cycle)
    if entity is None:
        abort(404)
    return {"conn": conn, "entity": entity, "scope": scope, "scope_default_key": scope_default,
            "cycle": cycle, "cycle_arg": cycle_arg}


def _site_threshold(conn, *, ttl: int, enabled: bool, refresh: bool):
    """Plan N15: max($5,000, 2 x p95 of every itemized contribution, all time) once per process under its own
    cache; None when the receipts matview is absent (the builder then falls back to its own statement)."""
    present = conn.execute("SELECT to_regclass(?) AS rel", ("isbe_condensed_receipts",)).fetchone()
    if not present or present["rel"] is None:
        return None
    return _cached_build(_threshold_cache, (ENTITY_CACHE_REV,), lambda: _site_large_threshold(conn), ttl,
                         cache_enabled=enabled, refresh=refresh)


def _build(ctx: dict, kind: str, key, tab: str, view: str | None = None):
    """The tab's payload through its RouteCache (``entity_<tab>``; the JSON views under tab ``network:<view>``)."""
    conn, entity, scope = ctx["conn"], ctx["entity"], ctx["scope"]
    window = {"date_from": scope.start, "date_to": scope.end}
    ttl, enabled, refresh = _request_settings()

    if tab == "network":
        params = NETWORK_CAPS

        def ego_builder():
            return get_entity_network(conn, entity, **window, caps=dict(zip(NETWORK_CAP_CLASSES, NETWORK_CAPS)))

        if view in PROJECTIONS:
            ego_key = _entity_cache_key(kind, key, "network:ego", scope, ctx["cycle"], params)

            def builder():
                ego = _cached_build(_caches["network"], ego_key, ego_builder, ttl, cache_enabled=enabled, refresh=refresh)
                return PROJECTIONS[view](ego)
        else:
            builder = ego_builder
    elif tab == "risk":
        params = (RISK_LIMIT,)

        def builder():
            threshold = _site_threshold(conn, ttl=ttl, enabled=enabled, refresh=refresh)
            return get_entity_risk(conn, entity, **window, limit=RISK_LIMIT, site_threshold=threshold)
    elif tab == "donors":
        params = (DONORS_TOP_N,)

        def builder():
            return get_entity_donors(conn, entity, **window, top_n=DONORS_TOP_N)
    elif tab == "geography":
        params = GEOGRAPHY_LIMITS

        def builder():
            return get_entity_geography(conn, entity, **window, limit_states=GEOGRAPHY_LIMITS[0],
                                        limit_cities=GEOGRAPHY_LIMITS[1])
    else:
        params = RELATIONSHIPS_LIMITS

        def builder():
            return get_entity_relationships(conn, entity, **window, limit=RELATIONSHIPS_LIMITS[0],
                                            min_shared_donors=RELATIONSHIPS_LIMITS[1],
                                            donor_pool=RELATIONSHIPS_LIMITS[2])

    cache_tab = f"{tab}:{view}" if view else tab
    cache_key = _entity_cache_key(kind, key, cache_tab, scope, ctx["cycle"], params)
    return _cached_build(_caches[tab], cache_key, builder, ttl, cache_enabled=enabled, refresh=refresh)


def _node_href(node: dict) -> str | None:
    """The page a network node links to: committees, candidates and non-redacted donors have one."""
    prefix, _, rest = node["id"].partition(":")
    if prefix == "committee":
        return url_for("committees.committee_detail", committee_id=int(rest))
    if prefix == "candidate":
        return url_for("candidates.candidate_detail", candidate_id=int(rest))
    if prefix == "donor" and not node.get("redacted"):
        return url_for("donors.donor_detail_by_key", donor_key=rest)
    return None


def _json_url(kind: str, key, ctx: dict, view: str) -> str:
    endpoint = "entity_analytics.candidate_network_json" if kind == "candidate" else "entity_analytics.race_network_json"
    key_arg = {"candidate_id": key} if kind == "candidate" else {"race_key": key}
    return url_for(endpoint, view=view, election_cycle=ctx["cycle_arg"], **key_arg, **scope_params(ctx["scope"]))


def _tab_page(kind: str, key, tab: str):
    if tab not in ENTITY_TABS:
        abort(404)
    ctx = _resolve(kind, key)
    payload = _build(ctx, kind, key, tab)
    context = {
        "entity": ctx["entity"],
        "tab": tab,
        "tab_label": TAB_LABELS[tab],
        "payload": payload,
        "subnav": {"kind": kind, "key": key, "active": tab, "election_cycle": ctx["cycle_arg"]},
        "time_scope": ctx["scope"],
        "scope_default_key": ctx["scope_default_key"],
        "time_scope_source_label": "ISBE",
        "scope_query": scope_params(ctx["scope"]),
        "state_race_cycle": ctx["cycle"],
    }
    if tab == "network":
        nodes = {node["id"]: node for node in payload["nodes"]}
        context["nodes_by_id"] = nodes
        context["node_href"] = {node_id: _node_href(node) for node_id, node in nodes.items()}
        context["network_sources"] = {view: _json_url(kind, key, ctx, view) for view in NETWORK_VIEWS}
    return render_template(f"entity/{tab}.html", **context)


def _network_json(kind: str, key):
    view = request.args.get("view", "ego")
    if view not in NETWORK_VIEWS:
        abort(404)
    ctx = _resolve(kind, key)
    payload = _build(ctx, kind, key, "network", view=view)
    return jsonify(dict(payload, view=view))


@bp.route("/candidates/<int:candidate_id>/<tab>")
def candidate_tab(candidate_id: int, tab: str):
    return _tab_page("candidate", candidate_id, tab)


@bp.route("/candidates/<int:candidate_id>/network.json")
def candidate_network_json(candidate_id: int):
    return _network_json("candidate", candidate_id)


@bp.route("/analytics/state-races/<race_key>/<tab>")
def race_tab(race_key: str, tab: str):
    return _tab_page("race", race_key, tab)


@bp.route("/analytics/state-races/<race_key>/network.json")
def race_network_json(race_key: str):
    return _network_json("race", race_key)
