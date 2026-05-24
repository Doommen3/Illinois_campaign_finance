"""Route-level cache abstraction with Redis backend + in-process fallback.

The platform's 9 route-level caches (analytics overview, networks, risk,
relationships, geo-drilldown, federal geo-drilldown, dashboard insights,
search results, dark-money stats) all follow the same shape: a tuple cache
key, a JSON-serializable payload, and a TTL. Originally each cache was an
in-process dict — per-worker, which meant a 3-worker gunicorn deployment
paid 3× the cold-load cost on every restart.

This module wraps that dict pattern behind a `RouteCache` class that uses
Redis when `REDIS_URL` is set in the environment, and falls back to the
original in-process dict when it isn't (dev machines, CI). The cache key /
TTL semantics are identical; only the storage layer changes.

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
from typing import Any, Optional

logger = logging.getLogger(__name__)

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

    Behaves like a dict with `payload` / `key` / `expires_at` fields, but
    backed by Redis when available. Thread-safe.
    """

    def __init__(self, name: str):
        self.name = name
        self._lock = threading.Lock()
        self._local: dict = {"payload": None, "key": None, "expires_at": 0.0}

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
            if (
                self._local["payload"] is not None
                and self._local["key"] == cache_key
                and float(self._local["expires_at"]) > time.monotonic()
            ):
                return self._local["payload"]
            return None

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
                with self._lock:
                    self._local["payload"] = payload
                    self._local["key"] = cache_key
                    self._local["expires_at"] = time.monotonic() + float(ttl_seconds)
                return
            except Exception:
                logger.exception(f"RouteCache[{self.name}].set: Redis write failed; using local only")

        with self._lock:
            self._local["payload"] = payload
            self._local["key"] = cache_key
            self._local["expires_at"] = time.monotonic() + float(ttl_seconds)

    def invalidate(self, cache_key: Optional[Any] = None) -> None:
        """Drop a specific cache_key, or the entire cache if `cache_key` is None."""
        client = _get_redis_client()
        if cache_key is None:
            # Drop both local and all Redis keys under this cache's namespace
            with self._lock:
                self._local = {"payload": None, "key": None, "expires_at": 0.0}
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
            if self._local.get("key") == cache_key:
                self._local = {"payload": None, "key": None, "expires_at": 0.0}
        if client is not None:
            try:
                client.delete(self._redis_key(cache_key))
            except Exception:
                logger.exception(f"RouteCache[{self.name}].invalidate: Redis delete failed")
