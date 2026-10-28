"""Route-level cache abstraction with Redis backend + in-process fallback.

Every route-level cache (the analytics pages, state races and geo
drill-downs; the federal geo drill-down; irs527 dark_money_stats; main
dashboard_insights, candidate_stats, top_donors and search_results) uses
this class, and they all follow the same shape: a tuple cache key, a
JSON-serializable payload, and a TTL. Originally each cache was an
in-process dict — per-worker, which meant a 3-worker gunicorn deployment
paid 3× the cold-load cost on every restart.

This module wraps that dict pattern behind a `RouteCache` class that uses
Redis when `REDIS_URL` is set in the environment, and falls back to an
in-process store when it isn't (dev machines, CI): a 32-entry
least-recently-used cache per RouteCache instance, each entry with its own
TTL. The fallback keys entries by the raw cache key, so keys must be
hashable. The cache key / TTL semantics are identical; only the storage
layer changes.

`get_or_compute` also owns the build: concurrent misses on one key run the
builder once (single-flight; across workers through a Redis lock), and an
optional soft TTL serves the stale value while one background refresh runs
(stale-while-revalidate).

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
import uuid
from collections import OrderedDict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Capacity of each RouteCache instance's in-process LRU (the no-Redis fallback,
# and the local mirror of Redis writes).
_LOCAL_MAX_ENTRIES = 32

# What get_or_compute stores when given a soft TTL: the payload plus the wall-clock
# time (shared by every worker) after which it is served stale. get() unwraps it.
_ENVELOPE_MARKER = "__route_cache_envelope__"
# How often a caller waiting on another worker's build re-reads Redis.
_LOCK_POLL_SECONDS = 0.05
# Delete the build lock only while it still holds this caller's token: an expired
# lock may already belong to another worker.
_UNLOCK_SCRIPT = 'if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) end return 0'
# The flush generation token: flush_all_route_caches() writes a fresh value here before it deletes, and a build compares the
# value it saw before builder() with the one after it. A build that started before a flush and would store after it must not
# keep pre-refresh data alive for a TTL. Outside the flushed ilcf:cache:* namespace, so a flush never scans or deletes it.
_FLUSH_GEN_KEY = "ilcf:flush_gen"

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


def _unwrap(stored: Any) -> tuple[Any, Optional[float]]:
    """(payload, soft_expires_at) of a stored value; a plain `set()` payload has no soft expiry."""
    if isinstance(stored, dict) and stored.get(_ENVELOPE_MARKER) is True:
        return stored.get("payload"), stored.get("soft_expires_at")
    return stored, None


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
        # cache_key -> Event set when this process's build of that key ends
        # (get_or_compute's lock when there is no Redis).
        self._building: dict = {}

    def _redis_key(self, cache_key: Any) -> str:
        # Hash the cache_key (typically a tuple of strings + numbers) so the
        # Redis key has a bounded length regardless of how many filter
        # dimensions accumulate over time.
        digest = hashlib.sha1(repr(cache_key).encode("utf-8")).hexdigest()
        return f"ilcf:cache:{self.name}:{digest}"

    def _lock_key(self, cache_key: Any) -> str:
        """Redis key of get_or_compute's build lock: outside `ilcf:cache:*`, so flushes leave it alone."""
        digest = hashlib.sha1(repr(cache_key).encode("utf-8")).hexdigest()
        return f"ilcf:lock:{self.name}:{digest}"

    def get(self, cache_key: Any) -> Optional[Any]:
        """Return the cached payload if cache_key matches and hasn't expired,
        otherwise None. A payload get_or_compute serves stale is still returned."""
        stored = self._read(cache_key)
        return None if stored is None else _unwrap(stored)[0]

    def _read(self, cache_key: Any) -> Optional[Any]:
        """The stored value (a payload, or get_or_compute's envelope) if present and unexpired, else None."""
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

    def get_or_compute(
        self,
        cache_key: Any,
        builder: Callable[[], Any],
        ttl_seconds: float,
        *,
        soft_ttl_seconds: Optional[float] = None,
        lock_timeout: float = 120,
    ) -> Any:
        """Return the cached payload for `cache_key`, building it with `builder()` on a miss.

        Single-flight: concurrent misses on one key run `builder` once (the key's build
        lock: Redis `SET NX PX`, shared by every worker, or a per-process claim without
        Redis) and the other callers return that build's value. A caller that has waited
        `lock_timeout` seconds builds for itself. A builder exception reaches its caller,
        caches nothing and frees the key at once. A None result is returned, not cached.
        A build during which `flush_all_route_caches()` ran is returned to its caller but not
        cached, so the callers queued behind it rebuild from post-flush data.

        Without `soft_ttl_seconds` the value lives `ttl_seconds`, like `set()`. With it,
        the value is fresh for `soft_ttl_seconds`, then served stale (until 2 x
        `ttl_seconds`) while ONE background refresh under the same lock rebuilds it; a
        failed refresh keeps the stale value. That refresh runs on its own thread, so a
        builder used with a soft TTL must not need a request context or a request's DB
        connection.
        """
        stored = self._read(cache_key)
        if stored is not None:
            payload, soft_expires_at = _unwrap(stored)
            if soft_expires_at is not None and soft_expires_at <= time.time():
                self._refresh_in_background(cache_key, builder, ttl_seconds, soft_ttl_seconds, lock_timeout)
            return payload

        deadline = time.perf_counter() + max(0.0, float(lock_timeout))
        while True:
            release = self._try_lock(cache_key, lock_timeout)
            if release is not None:
                try:
                    stored = self._read(cache_key)  # built while this caller waited for the lock
                    if stored is not None:
                        return _unwrap(stored)[0]
                    return self._build_and_store(cache_key, builder, ttl_seconds, soft_ttl_seconds)
                finally:
                    release()
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                logger.warning(f"RouteCache[{self.name}]: waited {lock_timeout}s for another build; building here")
                return self._build_and_store(cache_key, builder, ttl_seconds, soft_ttl_seconds)
            self._wait_for_build(cache_key, remaining)
            stored = self._read(cache_key)
            if stored is not None:
                return _unwrap(stored)[0]
            # Still nothing stored. Either the build in flight ended without a value (it failed, returned None, or a flush
            # fenced its store), or it is still running: on Redis a wait is one poll interval, and a build that outlived
            # its lock no longer blocks the key. Loop: take the key if it is free, else wait again.

    def _build_and_store(self, cache_key: Any, builder: Callable[[], Any], ttl_seconds: float,
                         soft_ttl_seconds: Optional[float]) -> Any:
        generation = self._flush_generation()
        payload = builder()
        if payload is None:
            return None
        if self._flush_generation() != generation:
            return payload  # a flush ran during the build: serve this caller, but do not keep pre-refresh data for a TTL
        if soft_ttl_seconds is None:
            self.set(cache_key, payload, ttl_seconds)
        else:
            envelope = {
                _ENVELOPE_MARKER: True,
                "payload": payload,
                "soft_expires_at": time.time() + float(soft_ttl_seconds),
            }
            self.set(cache_key, envelope, 2 * float(ttl_seconds))
        return payload

    def _flush_generation(self) -> Optional[Any]:
        """The current flush token (see `_FLUSH_GEN_KEY`), or None without Redis, before any flush, or when the read fails."""
        client = _get_redis_client()
        if client is None:
            return None
        try:
            return client.get(_FLUSH_GEN_KEY)
        except Exception:
            logger.exception(f"RouteCache[{self.name}]: flush token read failed; this build is not fenced against a flush")
            return None

    def _try_lock(self, cache_key: Any, lock_timeout: float) -> Optional[Callable[[], None]]:
        """Take the key's build lock without waiting: its release function, or None while another build holds it.

        The Redis lock expires after `lock_timeout`, so a worker that dies mid-build blocks the key no
        longer than its waiters would wait anyway. If a Redis command fails the lock is per-process.
        """
        client = _get_redis_client()
        if client is not None:
            lock_key, token = self._lock_key(cache_key), uuid.uuid4().hex
            try:
                if not client.set(lock_key, token, nx=True, px=max(1, int(float(lock_timeout) * 1000))):
                    return None
                return lambda: self._redis_unlock(client, lock_key, token)
            except Exception:
                logger.exception(f"RouteCache[{self.name}]: Redis lock failed; locking in this process only")

        with self._lock:
            if cache_key in self._building:
                return None
            done = self._building[cache_key] = threading.Event()

        def release() -> None:
            with self._lock:
                self._building.pop(cache_key, None)
            done.set()

        return release

    def _redis_unlock(self, client: Any, lock_key: str, token: str) -> None:
        try:
            client.eval(_UNLOCK_SCRIPT, 1, lock_key, token)
        except Exception:
            logger.exception(f"RouteCache[{self.name}]: Redis unlock failed; the lock expires on its own")

    def _wait_for_build(self, cache_key: Any, timeout: float) -> None:
        """Wait at most `timeout` s: until this process's build of the key ends, or one poll interval
        while the build holding the Redis lock runs in another worker (or thread)."""
        with self._lock:
            done = self._building.get(cache_key)
        if done is not None:
            done.wait(timeout)
        else:
            time.sleep(min(timeout, _LOCK_POLL_SECONDS))

    def _refresh_in_background(self, cache_key: Any, builder: Callable[[], Any], ttl_seconds: float,
                               soft_ttl_seconds: Optional[float], lock_timeout: float) -> None:
        """Rebuild a soft-expired entry on a daemon thread, unless a build of the key is already running."""
        release = self._try_lock(cache_key, lock_timeout)
        if release is None:
            return

        def refresh() -> None:
            try:
                stored = self._read(cache_key)
                soft_expires_at = None if stored is None else _unwrap(stored)[1]
                if stored is not None and (soft_expires_at is None or soft_expires_at > time.time()):
                    return  # refreshed by another worker between the stale read and the lock
                self._build_and_store(cache_key, builder, ttl_seconds, soft_ttl_seconds)
            except Exception:
                logger.exception(f"RouteCache[{self.name}]: background refresh failed; the stale value stays")
            finally:
                release()

        threading.Thread(target=refresh, name=f"RouteCache-refresh-{self.name}", daemon=True).start()

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

    First writes a fresh flush token (`_FLUSH_GEN_KEY`, outside the deleted namespace), so a build
    that started before this flush does not store its pre-flush payload after it.

    Only the Redis layer is flushed. The in-process fallback dicts live in
    each gunicorn worker and are cleared by restarting the service.
    """
    client = _get_redis_client()
    if client is None:
        logger.info("flush_all_route_caches: Redis unavailable; nothing flushed")
        return 0
    # Its own try block: a Redis that refuses SET (maxmemory with noeviction) still accepts DEL, and deleting is the flush's job.
    try:
        client.set(_FLUSH_GEN_KEY, uuid.uuid4().hex)
    except Exception as exc:
        logger.warning(f"flush_all_route_caches: could not write the flush token ({exc}); builds in flight are not fenced")
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
