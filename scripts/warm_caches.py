#!/usr/bin/env python3
"""Warm the heavy route caches once, for the four scope presets (load-time plan L8).

Fills the analytics overview, risk, networks and relationships caches (with the geography,
donors and top-12 race-detail entries warm_analytics_caches also fills) and the homepage
caches for every preset_options() window: current cycle, previous cycle, last 12 months,
all time. Keys already cached are skipped, so a run right after a restart only fills what
is missing. Returns when everything is built.

Run it after anything that flushes the route caches (`refresh-analytics`, `sunshine-import`,
the Sunday FEC timer) and after a service restart, with the web app's environment
(DATABASE_URL, REDIS_URL, the *_CACHE_TTL_SECONDS overrides). The caches live in Redis;
without REDIS_URL they would live in this process only and vanish when it exits (each web
worker's own startup prewarm is the fallback there). With REDIS_URL set but Redis unreachable it
warms nothing and exits 1.

Usage:
  python3 scripts/warm_caches.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config as app_config  # noqa: E402
from webapp import cache_backend  # noqa: E402
from webapp.routes.analytics import warm_analytics_caches  # noqa: E402
from webapp.routes.main import warm_dashboard_home_cache  # noqa: E402
from webapp.utils.time_scope import preset_options  # noqa: E402

logger = logging.getLogger("warm_caches")


def _ttl(setting: str) -> int:
    """The TTL the web app uses for a cache (config.py / environment), floored at 15 s like the routes."""
    return max(15, int(getattr(app_config, setting)))


def main() -> int:
    if not (os.environ.get("REDIS_URL") or "").strip():
        logger.warning("REDIS_URL is not set: the caches warmed here are this process's own and vanish when it exits")
    elif cache_backend._get_redis_client() is None:
        logger.error(
            "REDIS_URL is set but Redis is unreachable (or the redis package is missing): "
            "nothing warmed here would outlive this process; not warming"
        )
        return 1
    for scope_key, label in preset_options():
        started = time.perf_counter()
        warm_analytics_caches(
            app_config.DATABASE_TARGET,
            networks_ttl=_ttl("ANALYTICS_NETWORKS_CACHE_TTL_SECONDS"),
            relationships_ttl=_ttl("ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS"),
            risk_ttl=_ttl("ANALYTICS_RISK_CACHE_TTL_SECONDS"),
            overview_ttl=_ttl("ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS"),
            geography_ttl=_ttl("ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS"),
            donors_ttl=_ttl("ANALYTICS_DONORS_CACHE_TTL_SECONDS"),
            scope_key=scope_key,
        )
        warm_dashboard_home_cache(
            app_config.DATABASE_TARGET,
            insights_ttl_seconds=_ttl("DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS"),
            candidate_stats_ttl_seconds=_ttl("DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS"),
            top_donors_ttl_seconds=_ttl("DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS"),
            scope_key=scope_key,
        )
        logger.info("warmed %s (%s) in %.1f s", scope_key, label, time.perf_counter() - started)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
