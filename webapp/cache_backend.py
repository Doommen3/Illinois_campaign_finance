"""Route-level cache abstraction with Redis backend + in-process fallback.

The platform's 9 route-level caches (analytics overview, networks, risk,
relationships and state_race_detail; irs527 dark_money_stats; main
dashboard_insights, candidate_stats and top_donors) all follow the same
shape: a tuple cache key, a JSON-serializable payload, and a TTL. (The
geo-drilldown and federal geo-drilldown caches are plain per-worker dicts
and the search-results cache is its own OrderedDict; none of them use this
class.) Originally each cache was an in-process dict — per-worker, which
meant a 3-worker gunicorn deployment paid 3× the cold-load cost on every
restart.

This module wraps that dict pattern behind a `RouteCache` class that uses
Redis when `REDIS_URL` is set in the environment, and falls back to an
in-process store when it isn't (dev machines, CI): a 32-entry
least-recently-used cache per RouteCache instance, each entry with its own
TTL. The fallback keys entries by the raw cache key, so keys must be
hashable. The cache key / TTL semantics are identical; only the storage
layer changes.

Cache keys are hashed (SHA1 of `repr(cache_key)`) so they fit comfortably
within Redis' key-length limits and are safe to log.

Payloads are serialized via `json.dumps(..., default=str)`. The dashboard
payloads contain dicts of lists of primitives + date/Decimal values; `default=str`
covers the non-JSON-native types we hit in practice. If a payload contains
something genuinely non-serializable (sets, custom classes), Redis writes
will fail loudly via the logged exception and the in-process fallback path
takes over for that route.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Capacity of each RouteCache instance's in-process LRU (the no-Redis fallback,
# and the local mirror of Redis writes).
_LOCAL_MAX_ENTRIES = 32

# Module-level Redis client, lazily initialized on first access. None when
# REDIS_URL is unset or the connection fails — both cases trigger fallback
# to the in-process dict.
_redis_client = None
_redis_init_lock = threading.Lock()
_redis_init_attempted = False


def _get_redis_client():
    """Return a live Redis client, or None if unavailable."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client

    with _redis_init_lock:
        if _redis_init_attempted:
            return _redis_client
        _redis_init_attempted = True

        redis_url = (os.environ.get("REDIS_URL") or "").strip()
        if not redis_url:
            return None

        try:
            import redis as redis_lib  # type: ignore
        except ImportError:
            logger.warning("RouteCache: REDIS_URL set but `redis` package not installed; using in-process fallback")
            return None

        try:
            client = redis_lib.from_url(
                redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            client.ping()
            _redis_client = client
            logger.info(f"RouteCache: connected to Redis at {redis_url}")
            return client
        except Exception as exc:
            logger.warning(f"RouteCache: Redis connection failed ({exc}); using in-process fallback")
            return None


class RouteCache:
    """Shared cache for route-level computed payloads.

    Backed by Redis when available; otherwise (and as a local mirror of Redis
    writes) by a per-instance LRU of `_LOCAL_MAX_ENTRIES` entries, each stored
    as `(payload, expires_at)`. Thread-safe.
    """

    def __init__(self, name: str):
        self.name = name
        self._lock = threading.Lock()
        # cache_key -> (payload, expires_at), least recently used first.
        self._local: OrderedDict = OrderedDict()

    def _redis_key(self, cache_key: Any) -> str:
        # Hash the cache_key (typically a tuple of strings + numbers) so the
        # Redis key has a bounded length regardless of how many filter
        # dimensions accumulate over time.
        digest = hashlib.sha1(repr(cache_key).encode("utf-8")).hexdigest()
        return f"ilcf:cache:{self.name}:{digest}"

    def get(self, cache_key: Any) -> Optional[Any]:
        """Return the cached payload if cache_key matches and hasn't expired,
        otherwise None."""
        client = _get_redis_client()
        if client is not None:
            try:
                raw = client.get(self._redis_key(cache_key))
                if raw is None:
                    return None
                return json.loads(raw)
            except Exception:
                logger.exception(f"RouteCache[{self.name}].get: Redis read failed; falling back to local")

        with self._lock:
            entry = self._local.get(cache_key)
            if entry is None:
                return None
            payload, expires_at = entry
            if expires_at <= time.monotonic():
                del self._local[cache_key]
                return None
            self._local.move_to_end(cache_key)
            return payload

    def _store_local(self, cache_key: Any, payload: Any, ttl_seconds: float) -> None:
        """Insert/refresh one entry in the local LRU, evicting the least recently used when full."""
        with self._lock:
            self._local[cache_key] = (payload, time.monotonic() + float(ttl_seconds))
            self._local.move_to_end(cache_key)
            if len(self._local) > _LOCAL_MAX_ENTRIES:
                self._local.popitem(last=False)

    def set(self, cache_key: Any, payload: Any, ttl_seconds: float) -> None:
        """Store `payload` under `cache_key` for `ttl_seconds`."""
        ttl = int(max(1, ttl_seconds))
        client = _get_redis_client()
        if client is not None:
            try:
                client.setex(
                    self._redis_key(cache_key),
                    ttl,
                    json.dumps(payload, default=str),
                )
                # Keep the local mirror too so single-worker dev/test paths
                # don't depend on a Redis round-trip per request.
                self._store_local(cache_key, payload, ttl_seconds)
                return
            except Exception:
                logger.exception(f"RouteCache[{self.name}].set: Redis write failed; using local only")

        self._store_local(cache_key, payload, ttl_seconds)

    def invalidate(self, cache_key: Optional[Any] = None) -> None:
        """Drop a specific cache_key, or the entire cache if `cache_key` is None."""
        client = _get_redis_client()
        if cache_key is None:
            # Drop both local and all Redis keys under this cache's namespace
            with self._lock:
                self._local.clear()
            if client is not None:
                try:
                    # Use SCAN to find keys to delete; KEYS would block for large sets
                    cursor = 0
                    pattern = f"ilcf:cache:{self.name}:*"
                    while True:
                        cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
                        if keys:
                            client.delete(*keys)
                        if cursor == 0:
                            break
                except Exception:
                    logger.exception(f"RouteCache[{self.name}].invalidate: Redis scan/delete failed")
            return

        with self._lock:
            self._local.pop(cache_key, None)
        if client is not None:
            try:
                client.delete(self._redis_key(cache_key))
            except Exception:
                logger.exception(f"RouteCache[{self.name}].invalidate: Redis delete failed")


def flush_all_route_caches() -> int:
    """Delete every `ilcf:cache:*` key in Redis; return the number deleted.

    Only the Redis layer is flushed. The in-process fallback dicts live in
    each gunicorn worker and are cleared by restarting the service.
    """
    client = _get_redis_client()
    if client is None:
        logger.info("flush_all_route_caches: Redis unavailable; nothing flushed")
        return 0
    deleted = 0
    try:
        # SCAN, not KEYS, so a large keyspace does not block Redis.
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match="ilcf:cache:*", count=100)
            if keys:
                deleted += client.delete(*keys)
            if cursor == 0:
                break
    except Exception as exc:
        logger.info(f"flush_all_route_caches: Redis scan/delete failed ({exc})")
        return 0
    return deleted
