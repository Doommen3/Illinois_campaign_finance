"""Tests-first suite for plan step P7 (ranked fix L8): the cache lifecycle.

Plan: .claude/plans/load-times.md (ranked fix L8; "P7 (L8) cache lifecycle" under Unit designs).

Why: after a restart or a Redis flush every worker rebuilds the same 13-34 s payloads at the same moment, each of the three
gunicorn workers repeats the whole prewarm, the warm covers one window only, and a `load_mode=full` GET can start a snapshot
rebuild that refreshes the materialized tables.  Pinned contract (each test names the wrong implementation it kills):

  * RouteCache.get_or_compute(cache_key, builder, ttl_seconds, *, soft_ttl_seconds=None, lock_timeout=120)
      - concurrent misses on one key run the builder once and every caller gets that value (8 threads, a 0.5 s builder);
      - distinct keys, and one key in two caches, never wait on each other;
      - a builder exception propagates to its caller, caches nothing and frees the lock at once: the next call, and a caller that
        was queued behind the failing build, proceed without waiting out lock_timeout;
      - after the soft TTL the stale value is served while exactly ONE refresh runs; once it finishes the next call sees the new
        value; a refresh that fails keeps the stale value; nothing stale is served past the hard TTL (the plan says 2 x ttl; the
        test allows up to 3 x);
      - lock_timeout is honoured: a caller stuck behind a slow build gives up waiting and builds itself;
      - it interoperates with the rest of the class: a value stored by set() is a hit, a value built here reads back through get()
        as the plain payload (never an envelope), invalidate() forces a rebuild, and over Redis so does flush_all_route_caches().
    Everything above runs on the in-process fallback AND over Redis (an in-memory FakeRedis below); two gunicorn "workers" sharing
    Redis are two copies of the module sharing one FakeRedis.
  * The six heavy analytics routes - overview (`dashboard`), `risk`, `networks`, `relationships`, `state_races`, `state_race_detail` -
    reach get_or_compute (source check over the route plus the module helpers it calls) AND build once under a burst of 6
    concurrent cold requests (behavioural check: a source reference alone proves nothing).
  * scripts/warm_caches.py: exists, imports without side effects, `main()` warms the four `preset_options()` windows for overview,
    risk, networks, relationships and the dashboard home, returns only when done, exits 0.  Observed end to end: after `main()`
    a GET of each page under each preset window (and the bare URL) calls no builder.
  * create_app's prewarm (only runs with TESTING false and DASHBOARD_PREWARM_ENABLED true) skips keys that are already present
    and builds only the missing ones.
  * A GET with `load_mode=full` never reaches refresh_analytics_materialized, build_dashboard_full_snapshot, a snapshot write or the
    snapshot executor, whatever refresh_full / sync_full say and whether the stored snapshot is absent or stale; it serves the stored
    snapshot and does not tell the reader a build is running.

How things are observed
  * Builders: call-through spies on the NAMES THE ROUTE MODULES USE (`webapp.routes.analytics` / `webapp.routes.main`), never on
    `database.analytics`, so a builder's own internal calls are not counted and "one build" is one count.  Each spy binds the
    call to the real signature and records the arguments.
  * Time: the `clock` fixture shifts time.time AND time.monotonic together (house technique of tests/test_cache_backend.py), so the
    implementation may keep its soft expiry on either clock.  Elapsed-time checks use time.perf_counter, which is left alone.
  * Redis: FakeRedis models the slice an implementation can reasonably use (get / set with nx, px, ex / setex / psetex / setnx /
    delete / exists / expire / pexpire / ttl / pttl / scan / mget / pipeline / compare-and-delete and compare-and-pexpire Lua),
    returns bytes like redis-py without decode_responses, expires on time.time(), and records anything else in `.unsupported`,
    which fails the test that used it (extend the fake, never the test).
  * Clock for the analytics tests: frozen at 2026-10-26 as in tests/test_analytics_features.py, so the four presets are
    cycle:2026 (2025-01-01..2026-12-31), cycle:2024 (2023-01-01..2024-12-31), last12m (2025-10-26..2026-10-26: today minus 365 days;
    no 29 February lies in between) and all (unbounded).

Interface this suite assumes beyond the brief (proposed in the report, ratify before the implementer starts):
  * scripts/warm_caches.py: `main()` callable with no arguments (sys.argv is patched to the script name), returning 0 or None, or
    raising SystemExit(0); the file puts the repo root on sys.path itself (the other scripts do) and has an `if __name__ == "__main__"`
    guard.  It calls webapp.routes.analytics.warm_analytics_caches and webapp.routes.main.warm_dashboard_home_cache once per
    preset window and carries the window as ONE argument that is a preset key ("cycle:2026", "last12m"...) or a TimeScope; the
    parameter name is free (proposal: keyword-only `scope_key: str = "cycle:current"` on both functions).
  * create_app's prewarm stays gated by `not TESTING` (the tests build the app with TESTING false) and keeps calling the same two
    warm functions for the default window.

Beyond the brief (each is its own test, delete freely): the interop pins (set() is a hit, get() returns the plain payload, invalidate()
and a Redis flush force a rebuild); a failed refresh keeps the stale value; a caller queued behind a failing build builds its own
value instead of waiting out lock_timeout; the hard TTL; the plain-script load; the Redis-shared prewarm skip; the second recorder day;
the page-text honesty check of section 6.

Deliberately NOT pinned (decide in review, then add a test): the exit code when a warm step fails; what get_or_compute does with a
soft_ttl_seconds of None between ttl and 2 x ttl; the Redis command set, lock key names and TTL multiplier beyond "a soft-expired
entry survives in Redis until 2 x ttl at the latest"; what happens to a lock whose winner dies (lock expiry); wiring of the warm script
into scripts/refresh_prod_chain.sh / sync-fec.sh and the systemd unit; the geo drill-down, geography, donors and search call sites
(other lanes); race-detail warming for the non-default windows.  The `redis` package is not installed in this environment, so the Redis
path is pinned through FakeRedis only.

No product stub is needed: every symbol that does not exist yet is looked up with getattr/importlib inside the test, so a missing
symbol is a red assertion naming it, never a collection error.

Review round R5 (2026-10-28; findings C2 and C18 of the load-times plan's "Review fixes"): sections 2b and 4b.
  * 2b: flush_all_route_caches() writes a fresh token to the key `ilcf:flush_gen` (outside `ilcf:cache:*`) before it scans; a build whose
    token differs after builder() returns hands its payload to its caller without storing it, so the caller queued behind it rebuilds
    from post-flush data.  A Redis error while reading the token (build path) or writing it (flush) is swallowed, and a failed write does
    not stop the deletion.  Pinned by behaviour, with the flush run in a second module copy (the refresh-analytics process).
  * 4b: main() returns 1 without warming when REDIS_URL is set but cache_backend._get_redis_client() is None; REDIS_URL unset or empty
    keeps the warn-and-warm fallback; a reachable Redis warms as before.
  Not pinned: whether a payload is stored after a Redis error on the token read; the log text of the warm script's refusal; a fence on
  RouteCache.invalidate() (its one production caller drops a single key); the soft-TTL refresh path beyond its sharing _build_and_store.
"""
from __future__ import annotations

import ast
import gc
import importlib
import importlib.util
import inspect
import re
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace

import pytest

import database.analytics as analytics_db
import webapp.cache_backend as cache_backend
import webapp.routes.analytics as analytics_routes
import webapp.routes.main as main_routes
from database.connection import get_db, init_db
from tests.test_analytics_features import analytics_app  # noqa: F401  (fixture)
from webapp.app import create_app
from webapp.cache_backend import RouteCache
from webapp.utils.time_scope import preset_options

REPO = Path(__file__).resolve().parents[1]
WARM_SCRIPT = REPO / "scripts" / "warm_caches.py"
KEY = ("lifecycle", "key")  # cache keys are tuples, as in the routes


def fresh_key():
    """An equal key that is a different object each call, as every request builds its own key tuple."""
    return tuple(["lifecycle", "key"])


# ---------------------------------------------------------------------------
# Redis stand-in
# ---------------------------------------------------------------------------

class FakePipeline:
    """Queues commands and runs them on execute(), like redis-py's pipeline()."""

    def __init__(self, redis):
        self._redis = redis
        self._queue = []

    def __getattr__(self, command):
        def queue(*args, **kwargs):
            self._queue.append((command, args, kwargs))
            return self
        return queue

    def execute(self):
        results = [getattr(self._redis, command)(*args, **kwargs) for command, args, kwargs in self._queue]
        self._queue = []
        return results

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeRedis:
    """In-memory Redis for RouteCache's value + lock protocol (module docstring: How things are observed)."""

    def __init__(self):
        self._mutex = threading.RLock()
        self._data = {}  # bytes key -> [bytes value, absolute expiry on time.time() or None]
        self.unsupported = []

    @staticmethod
    def _bytes(value):
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        return str(value).encode("utf-8")

    def _note(self, description):
        self.unsupported.append(description)

    def _entry(self, name):
        key = self._bytes(name)
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at = entry[1]
        if expires_at is not None and expires_at <= time.time():
            del self._data[key]
            return None
        return entry

    def __getattr__(self, command):
        # Only reached for commands the fake does not define.
        if command.startswith("__"):
            raise AttributeError(command)
        self._note(f"{command}()")
        raise AttributeError(f"FakeRedis does not model {command}()")

    def ping(self):
        return True

    def get(self, name):
        with self._mutex:
            entry = self._entry(name)
            return None if entry is None else entry[0]

    def mget(self, keys, *args):
        names = list(keys) if isinstance(keys, (list, tuple)) else [keys]
        return [self.get(name) for name in names + list(args)]

    def set(self, name, value, ex=None, px=None, nx=False, xx=False, keepttl=False, get=False, exat=None, pxat=None):
        if keepttl or get or exat is not None or pxat is not None:
            self._note("set(keepttl/get/exat/pxat)")
            raise NotImplementedError("FakeRedis.set: keepttl/get/exat/pxat")
        with self._mutex:
            exists = self._entry(name) is not None
            if nx and exists:
                return None
            if xx and not exists:
                return None
            seconds = None
            if ex is not None:
                seconds = float(ex)
            if px is not None:
                seconds = float(px) / 1000.0
            expires_at = None if seconds is None else time.time() + seconds
            self._data[self._bytes(name)] = [self._bytes(value), expires_at]
            return True

    def setex(self, name, seconds, value):
        return self.set(name, value, ex=seconds)

    def psetex(self, name, milliseconds, value):
        return self.set(name, value, px=milliseconds)

    def setnx(self, name, value):
        return bool(self.set(name, value, nx=True))

    def delete(self, *names):
        removed = 0
        with self._mutex:
            for name in names:
                if self._entry(name) is not None:
                    del self._data[self._bytes(name)]
                    removed += 1
        return removed

    def exists(self, *names):
        with self._mutex:
            return sum(1 for name in names if self._entry(name) is not None)

    def expire(self, name, seconds):
        with self._mutex:
            entry = self._entry(name)
            if entry is None:
                return False
            entry[1] = time.time() + float(seconds)
            return True

    def pexpire(self, name, milliseconds):
        return self.expire(name, float(milliseconds) / 1000.0)

    def ttl(self, name):
        with self._mutex:
            entry = self._entry(name)
            if entry is None:
                return -2
            if entry[1] is None:
                return -1
            return max(0, int(round(entry[1] - time.time())))

    def pttl(self, name):
        with self._mutex:
            entry = self._entry(name)
            if entry is None:
                return -2
            if entry[1] is None:
                return -1
            return max(0, int(round((entry[1] - time.time()) * 1000)))

    def scan(self, cursor=0, match=None, count=None, _type=None):
        pattern = match or "*"
        with self._mutex:
            live = [key for key in list(self._data) if self._entry(key) is not None]
        found = [key for key in live if fnmatchcase(key.decode("utf-8"), pattern)]
        return 0, found

    def pipeline(self, transaction=True, shard_hint=None):
        return FakePipeline(self)

    def eval(self, script, numkeys, *keys_and_args):
        keys, args = keys_and_args[:numkeys], keys_and_args[numkeys:]
        text = script.lower()
        reads_first = 'call("get"' in text or "call('get'" in text
        if reads_first and ('call("del"' in text or "call('del'" in text):  # compare-and-delete (safe unlock)
            with self._mutex:
                if self.get(keys[0]) == self._bytes(args[0]):
                    self.delete(keys[0])
                    return 1
                return 0
        if reads_first and "pexpire" in text:  # compare-and-extend
            with self._mutex:
                if self.get(keys[0]) == self._bytes(args[0]):
                    self.pexpire(keys[0], int(args[1]))
                    return 1
                return 0
        self._note(f"eval({script[:60]!r})")
        raise NotImplementedError("FakeRedis.eval: unrecognised script")

    def register_script(self, script):
        return lambda keys=(), args=(), client=None: self.eval(script, len(keys), *keys, *args)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _empty_route_caches() -> None:
    for obj in gc.get_objects():
        if isinstance(obj, RouteCache):
            obj.invalidate()


@pytest.fixture(autouse=True)
def route_caches(monkeypatch):
    """Redis off (never touch a developer's real one) and every in-process RouteCache empty around each test."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    monkeypatch.delenv("REDIS_URL", raising=False)  # scripts/warm_caches.py exits 1 when it is set and the client is None
    _empty_route_caches()
    yield
    _empty_route_caches()


@pytest.fixture
def clock(monkeypatch):
    """Move time.time and time.monotonic forward together; the real clocks keep running underneath."""
    real_time, real_monotonic = time.time, time.monotonic
    shift = {"seconds": 0.0}
    monkeypatch.setattr(time, "time", lambda: real_time() + shift["seconds"])
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + shift["seconds"])

    def advance(seconds):
        shift["seconds"] += seconds

    return SimpleNamespace(advance=advance)


@pytest.fixture(params=["local", "redis"])
def backend(request, monkeypatch):
    """The two storage layers behind RouteCache: the in-process fallback and (FakeRedis) Redis."""
    fake = None
    if request.param == "redis":
        fake = FakeRedis()
        monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: fake)
    yield SimpleNamespace(name=request.param, fake=fake)
    if fake is not None:
        assert fake.unsupported == [], (
            f"the implementation used Redis commands the test fake does not model: {fake.unsupported}; "
            "extend FakeRedis in tests/test_cache_lifecycle.py (orchestrator), not the implementation"
        )


@pytest.fixture
def redis_only(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: fake)
    yield SimpleNamespace(name="redis", fake=fake)
    assert fake.unsupported == [], (
        f"the implementation used Redis commands the test fake does not model: {fake.unsupported}; "
        "extend FakeRedis in tests/test_cache_lifecycle.py (orchestrator), not the implementation"
    )


def freeze_clock(monkeypatch, today):
    """Freeze the time-scope resolver's clock (and the race builder's current cycle) at `today`, as analytics_app does."""
    cycle = today.year + today.year % 2  # an even year is its own cycle, an odd year belongs to the next one

    monkeypatch.setattr("database.analytics.current_election_cycle", lambda today=None: cycle)  # same signature as the original

    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr("webapp.utils.time_scope.date", _FrozenDate)


@pytest.fixture
def frozen(monkeypatch):
    """Clock frozen at 2026-10-26, as tests/test_analytics_features.py::analytics_app does."""
    freeze_clock(monkeypatch, date(2026, 10, 26))


@pytest.fixture
def empty_app(tmp_path, frozen):
    """An app on an empty schema with route caches on: enough for every page the warm step covers."""
    db_path = str(tmp_path / "lifecycle.db")
    init_db(db_path)
    return create_app({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "DASHBOARD_PREWARM_ENABLED": False,
        "ROUTE_PERF_CACHE_ENABLED": True,
        "REFRESH_PARAMS_REQUIRE_LOGIN": False,  # anonymous ?refresh_cache=1 / refresh_full / sync_full (LD4)
    })


# ---------------------------------------------------------------------------
# Threading helpers and builders
# ---------------------------------------------------------------------------

class Background:
    """fn() on a daemon thread; the result or the exception it raised is kept."""

    def __init__(self, fn):
        self.value = None
        self.error = None
        self.finished = threading.Event()
        self._fn = fn
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.value = self._fn()
        except BaseException as exc:  # recorded; the test asserts on it
            self.error = exc
        finally:
            self.finished.set()

    def wait(self, timeout):
        return self.finished.wait(timeout)


def run_concurrently(count, task, timeout=30.0):
    """task(i) on `count` threads released together by a barrier; returns one Background-like outcome per thread."""
    barrier = threading.Barrier(count)
    outcomes = [SimpleNamespace(done=False, value=None, error=None) for _ in range(count)]

    def runner(index):
        try:
            barrier.wait(timeout=timeout)
            outcomes[index].value = task(index)
        except BaseException as exc:  # recorded; the test asserts on it
            outcomes[index].error = exc
        outcomes[index].done = True

    threads = [threading.Thread(target=runner, args=(i,), daemon=True) for i in range(count)]
    for thread in threads:
        thread.start()
    deadline = time.perf_counter() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.perf_counter()))
    return outcomes


def wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class BuilderFailed(Exception):
    """A deliberately custom exception type, so wrapping or swallowing it is visible."""


class Builder:
    """A zero-argument builder: counts its calls (thread safe), may sleep, may fail; call n returns payload(n)."""

    def __init__(self, payload=None, delay=0.0, error=None):
        self._lock = threading.Lock()
        self.calls = 0
        self.delay = delay
        self.error = error
        self._payload = payload or (lambda n: {"gen": n})

    def __call__(self):
        with self._lock:
            self.calls += 1
            n = self.calls
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self._payload(n)


class GatedBuilder(Builder):
    """Signals `started`, then blocks until `release` (bounded, so a broken implementation fails the test instead of hanging it)."""

    def __init__(self, payload=None, error=None, hold=6.0):
        super().__init__(payload=payload, error=error)
        self.started = threading.Event()
        self.release = threading.Event()
        self.hold = hold

    def __call__(self):
        with self._lock:
            self.calls += 1
            n = self.calls
        self.started.set()
        self.release.wait(self.hold)
        if self.error is not None:
            raise self.error
        return self._payload(n)


def make_cache(prefix="lifecycle"):
    return RouteCache(f"{prefix}_{uuid.uuid4().hex[:8]}")


def require_get_or_compute():
    """A red assertion naming the missing method, for tests that would otherwise wait on a background call that cannot happen."""
    assert callable(getattr(RouteCache, "get_or_compute", None)), "RouteCache.get_or_compute does not exist yet"


def goc(cache, key, builder, ttl, **kwargs):
    """cache.get_or_compute(key, builder, ttl, **kwargs), with a red assertion (not an AttributeError) while it is missing."""
    method = getattr(cache, "get_or_compute", None)
    assert callable(method), "RouteCache.get_or_compute does not exist yet"
    return method(key, builder, ttl, **kwargs)


# ---------------------------------------------------------------------------
# 1. RouteCache.get_or_compute
# ---------------------------------------------------------------------------

# Payload shapes the routes cache: a dict, a list of dicts (state races), and the falsy ones a truthiness check would miss.
HIT_PAYLOADS = [
    pytest.param({"nodes": [{"id": "n1"}], "edges": []}, id="dict"),
    pytest.param([{"race": "r1", "total": 10.5}, {"race": "r2", "total": 0}], id="list_of_dicts"),
    pytest.param([], id="empty_list"),
    pytest.param({}, id="empty_dict"),
    pytest.param(0, id="zero"),
]


def test_get_or_compute_has_the_pinned_signature():
    """RouteCache.get_or_compute(cache_key, builder, ttl_seconds, *, soft_ttl_seconds=None, lock_timeout=120).

    The call sites in the routes pass the first three positionally and the others by keyword.
    Kills: another parameter order or name, soft_ttl_seconds / lock_timeout positional, a different default lock_timeout."""
    method = getattr(RouteCache, "get_or_compute", None)
    assert callable(method), "RouteCache.get_or_compute does not exist yet"
    parameters = inspect.signature(method).parameters
    assert list(parameters) == ["self", "cache_key", "builder", "ttl_seconds", "soft_ttl_seconds", "lock_timeout"]
    for name in ("cache_key", "builder", "ttl_seconds"):
        assert parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, name
    for name in ("soft_ttl_seconds", "lock_timeout"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert parameters["soft_ttl_seconds"].default is None
    assert parameters["lock_timeout"].default == 120


@pytest.mark.parametrize("payload", HIT_PAYLOADS)
def test_a_built_value_is_served_again_without_rebuilding(backend, payload):
    """First call builds, second is a hit: one builder call, the same payload twice (falsy payloads included).

    Kills: no caching (2 builder calls); `if cached:` instead of `is not None` (an empty list / dict / 0 would rebuild on
    every request); a Redis hit path that cannot read a list payload back (state-race rows are a list)."""
    cache = make_cache()
    builder = Builder(payload=lambda n: payload)

    first = goc(cache, KEY, builder, 60)
    second = goc(cache, KEY, builder, 60)

    assert first == payload
    assert second == payload
    assert builder.calls == 1


def test_a_value_is_still_a_hit_well_inside_the_ttl(backend, clock):
    """ttl 100 s, no soft TTL: at t=50 s the value is served with no build.

    Kills: a stored lifetime far shorter than ttl_seconds (a hard-coded second, a seconds/milliseconds mix-up)."""
    cache = make_cache()
    assert goc(cache, KEY, Builder(), 100) == {"gen": 1}

    clock.advance(50)
    later = Builder(payload=lambda n: {"gen": 2})

    assert goc(cache, KEY, later, 100) == {"gen": 1}
    assert later.calls == 0


def test_concurrent_misses_on_one_key_run_the_builder_once(backend):
    """The brief's pin: 8 threads miss the same key together, the builder sleeps 0.5 s and counts its calls.

    Hand-computed: 1 builder call; every caller returns the winner's value {"gen": 1}; a 9th call afterwards is a hit.
    Each caller passes its own equal-but-distinct key object.  Kills: no lock (8 calls); a lock without the re-check after
    acquiring (8 calls, serially, 4 s); a lock table keyed by the key object's identity instead of its value; losers that get
    None or an error instead of the winner's value; losers that stop waiting early and build for themselves; a winner whose
    value is never stored (the 9th call would rebuild)."""
    cache = make_cache()
    builder = Builder(delay=0.5)

    outcomes = run_concurrently(8, lambda index: goc(cache, fresh_key(), builder, 60))

    for index, outcome in enumerate(outcomes):
        assert outcome.done, f"caller {index} never returned"
        assert outcome.error is None, f"caller {index} raised {outcome.error!r}"
        assert outcome.value == {"gen": 1}, f"caller {index} got {outcome.value!r}"
    assert builder.calls == 1
    assert goc(cache, KEY, builder, 60) == {"gen": 1}
    assert builder.calls == 1


def test_distinct_keys_do_not_wait_for_each_other(backend):
    """Builder A refuses to finish until builder B has finished, and B refuses to start until A has started: they only both
    complete if the two keys build at the same time.

    Kills: one lock for the whole cache or the whole process (A waits out its 4 s and reports it never saw B finish)."""
    cache = make_cache()
    a_started, b_finished = threading.Event(), threading.Event()
    seen = {}

    def build_a():
        a_started.set()
        seen["a_saw_b_finish"] = b_finished.wait(timeout=4.0)
        return {"key": "a"}

    def build_b():
        seen["b_saw_a_start"] = a_started.wait(timeout=4.0)
        b_finished.set()
        return {"key": "b"}

    def task(index):
        if index == 0:
            return goc(cache, ("lifecycle", "a"), build_a, 60)
        return goc(cache, ("lifecycle", "b"), build_b, 60)

    outcomes = run_concurrently(2, task)

    assert [outcome.error for outcome in outcomes] == [None, None]
    assert seen == {"a_saw_b_finish": True, "b_saw_a_start": True}
    assert outcomes[0].value == {"key": "a"}
    assert outcomes[1].value == {"key": "b"}


def test_the_same_key_in_two_caches_is_two_independent_entries(backend):
    """Two RouteCache instances with the same key build concurrently, once each, and keep their own value.

    Kills: a lock table or value store keyed by the cache key alone (module-level), where the second cache waits on, or reads,
    the first cache's build; a Redis key that leaves the cache name out."""
    first, second = make_cache("lifecycle_one"), make_cache("lifecycle_two")
    build_first = Builder(payload=lambda n: {"cache": "one"}, delay=0.3)
    build_second = Builder(payload=lambda n: {"cache": "two"}, delay=0.3)

    def task(index):
        if index == 0:
            return goc(first, KEY, build_first, 60)
        return goc(second, KEY, build_second, 60)

    outcomes = run_concurrently(2, task)

    assert [outcome.error for outcome in outcomes] == [None, None]
    assert outcomes[0].value == {"cache": "one"}
    assert outcomes[1].value == {"cache": "two"}
    assert build_first.calls == 1
    assert build_second.calls == 1


def test_a_failing_builder_propagates_caches_nothing_and_frees_the_lock(backend):
    """The failing call raises the builder's own exception; no value is cached; the next call builds at once.

    The follow-up runs on a watchdog thread: a lock that was never released would hold it for lock_timeout (120 s).
    Kills: no try/finally around the build (the lock stays held); swallowing the error and returning None; wrapping it in
    another exception type; caching the failure or a placeholder (get() would not be None, the next build would not run)."""
    cache = make_cache()
    failure = BuilderFailed("boom")
    failing = Builder(error=failure)

    with pytest.raises(BuilderFailed) as caught:
        goc(cache, KEY, failing, 60)

    assert caught.value is failure
    assert failing.calls == 1
    assert cache.get(KEY) is None

    good = Builder(payload=lambda n: {"recovered": True})
    follow_up = Background(lambda: goc(cache, KEY, good, 60))
    assert follow_up.wait(5.0), "the lock was not released: the next call is still waiting"
    assert follow_up.error is None
    assert follow_up.value == {"recovered": True}
    assert good.calls == 1
    assert cache.get(KEY) == {"recovered": True}


def test_a_caller_queued_behind_a_failing_build_does_not_inherit_the_failure(backend):
    """Winner W holds the key, a waiter queues behind it, W then fails.  The waiter must build its own value, promptly.

    Kills: handing the winner's exception to every waiter; waiters that keep waiting for a value that never comes until
    lock_timeout (120 s) because only a stored value, not a released lock, wakes them."""
    require_get_or_compute()
    cache = make_cache()
    failure = BuilderFailed("winner failed")
    winner_builder = GatedBuilder(error=failure)
    winner = Background(lambda: goc(cache, KEY, winner_builder, 60))
    assert winner_builder.started.wait(3.0), "the winning build never started"

    waiter_builder = Builder(payload=lambda n: {"built_by": "waiter"})
    waiter = Background(lambda: goc(cache, KEY, waiter_builder, 60))
    time.sleep(0.3)
    assert not waiter.finished.is_set(), "the second caller did not wait for the build in flight"
    assert waiter_builder.calls == 0

    winner_builder.release.set()

    assert winner.wait(5.0), "the winning build never finished"
    assert winner.error is failure
    assert waiter.wait(8.0), "the waiter is still blocked after the winning build failed"
    assert waiter.error is None
    assert waiter.value == {"built_by": "waiter"}
    assert waiter_builder.calls == 1


def test_after_the_soft_ttl_the_stale_value_is_served_while_exactly_one_refresh_runs(backend, clock):
    """Timeline (ttl 100 s, soft 10 s; hard expiry is at most 2 x 100 = 200 s):

        t=0   build            -> {"gen": 1}
        t=5   inside the soft TTL: a plain hit; no builder runs, no refresh starts
        t=11  soft-expired (10 < 11 < 100): the call returns {"gen": 1} at once and ONE refresh starts in the background
        t=11  while that refresh is blocked, another call also returns {"gen": 1} and starts nothing
        then  the refresh finishes -> the next call returns {"gen": 2} and builds nothing
        t=22  the refreshed entry is soft-expired in its turn: stale {"gen": 2} again, and a second refresh produces {"gen": 5}

    Kills: treating every hit as stale (the t=5 call refreshes); a soft TTL that is ignored (never stale); a synchronous refresh
    (the t=11 call waits for the refresh and returns {"gen": 2}); serving stale but never refreshing; one refresh per stale caller;
    a refresh whose result is not stored; a lock left held by the first build or by a finished refresh, which would keep the
    next refresh from ever starting."""
    cache = make_cache()
    assert goc(cache, KEY, Builder(), 100, soft_ttl_seconds=10) == {"gen": 1}

    clock.advance(5)
    idle = Builder(payload=lambda n: {"gen": 99})
    assert goc(cache, KEY, idle, 100, soft_ttl_seconds=10) == {"gen": 1}
    time.sleep(0.2)
    assert idle.calls == 0

    clock.advance(6)
    refresh = GatedBuilder(payload=lambda n: {"gen": 2})
    served = goc(cache, KEY, refresh, 100, soft_ttl_seconds=10)
    assert served == {"gen": 1}, "a soft-expired call must return the stale value without waiting for the refresh"
    assert refresh.started.wait(3.0), "no background refresh was started for the soft-expired entry"

    other = Builder(payload=lambda n: {"gen": 3})
    assert goc(cache, KEY, other, 100, soft_ttl_seconds=10) == {"gen": 1}
    time.sleep(0.2)
    assert other.calls == 0, "a second refresh was started while one was already running"

    refresh.release.set()
    assert wait_until(lambda: cache.get(KEY) == {"gen": 2}, 5.0), "the refreshed value never became visible"
    final = Builder(payload=lambda n: {"gen": 4})
    assert goc(cache, KEY, final, 100, soft_ttl_seconds=10) == {"gen": 2}
    assert final.calls == 0
    assert refresh.calls == 1
    assert other.calls == 0

    clock.advance(11)  # t=22: the refreshed entry went soft-expired in its turn
    again = Builder(payload=lambda n: {"gen": 5})
    assert goc(cache, KEY, again, 100, soft_ttl_seconds=10) == {"gen": 2}
    assert wait_until(lambda: cache.get(KEY) == {"gen": 5}, 5.0), "the second soft expiry never produced a refresh (lock left held?)"
    assert again.calls == 1


def test_a_failed_refresh_keeps_the_stale_value_and_the_cache_recovers(backend, clock):
    """The refresh of a soft-expired entry raises: nothing reaches the caller, the stale value is still served, and once any
    back-off (allowed up to 5 minutes) has passed a later refresh succeeds - the refresh lock was released.  With the 30 s
    statement timeout of plan L2 a failing refresh is to be expected.

    Kills: an exception from the background refresh reaching the request; the stale entry dropped or replaced by a placeholder
    when the refresh fails (every reader would then pay the cold build); a refresh lock that stays held after a failure."""
    cache = make_cache()
    assert goc(cache, KEY, Builder(), 10_000, soft_ttl_seconds=10) == {"gen": 1}

    clock.advance(11)
    failure = BuilderFailed("refresh failed")
    failed_attempt = threading.Event()

    def failing_refresh():
        failed_attempt.set()
        raise failure

    served = goc(cache, KEY, failing_refresh, 10_000, soft_ttl_seconds=10)
    assert served == {"gen": 1}
    assert failed_attempt.wait(3.0), "no refresh was attempted"
    time.sleep(0.2)

    retry = Builder(payload=lambda n: {"gen": 3})
    assert goc(cache, KEY, retry, 10_000, soft_ttl_seconds=10) == {"gen": 1}, "the stale value was lost after a failed refresh"

    clock.advance(300)  # past any plausible back-off after a failure, far inside the hard TTL
    assert goc(cache, KEY, retry, 10_000, soft_ttl_seconds=10) in ({"gen": 1}, {"gen": 3})
    assert wait_until(lambda: cache.get(KEY) == {"gen": 3}, 5.0), "no later refresh ever succeeded"


@pytest.mark.parametrize("soft_ttl", [None, 2], ids=["no_soft_ttl", "soft_ttl_2s"])
def test_nothing_stale_is_served_past_the_hard_ttl(backend, clock, soft_ttl):
    """ttl 5 s (the plan's hard TTL is 2 x ttl = 10 s): at t=16 s (3 x ttl + 1) the entry is gone, so the call builds
    synchronously and returns the NEW value.

    Kills: a stale entry that never expires (no hard TTL on the envelope or on the Redis key); serving stale forever."""
    cache = make_cache()
    assert goc(cache, KEY, Builder(), 5, soft_ttl_seconds=soft_ttl) == {"gen": 1}

    clock.advance(16)
    second = Builder(payload=lambda n: {"gen": 2})

    assert goc(cache, KEY, second, 5, soft_ttl_seconds=soft_ttl) == {"gen": 2}
    assert second.calls == 1


def test_lock_timeout_is_honoured(backend):
    """A build blocks for up to 6 s; a second caller passes lock_timeout=0.5.  It must give up waiting and build its own
    value within a few seconds, not wait for the other build.

    Kills: a hard-coded or ignored lock_timeout (the caller waits for the 6 s build and returns ITS value); waiting forever."""
    require_get_or_compute()
    cache = make_cache()
    slow = GatedBuilder(payload=lambda n: {"built_by": "slow"})
    holder = Background(lambda: goc(cache, KEY, slow, 60))
    assert slow.started.wait(3.0), "the slow build never started"

    impatient = Builder(payload=lambda n: {"built_by": "impatient"})
    caller = Background(lambda: goc(cache, KEY, impatient, 60, lock_timeout=0.5))
    gave_up = caller.wait(4.0)
    slow.release.set()

    assert gave_up, "lock_timeout=0.5 was not honoured: the caller is still waiting after 4 s"
    assert caller.error is None
    assert caller.value == {"built_by": "impatient"}
    assert impatient.calls == 1
    assert holder.wait(5.0), "the slow build never finished after its release"


@pytest.mark.parametrize("payload", HIT_PAYLOADS)
def test_a_value_stored_by_set_is_a_hit(backend, payload):
    """The warm step and tests write with set(); the request path reads with get_or_compute().

    Kills: an envelope-only read path that treats a plain stored payload as a miss (every warmed key rebuilt) or crashes on a
    list payload (state-race rows)."""
    cache = make_cache()
    cache.set(KEY, payload, 60)
    builder = Builder()

    assert goc(cache, KEY, builder, 60) == payload
    assert builder.calls == 0


@pytest.mark.parametrize("payload", HIT_PAYLOADS)
def test_a_value_built_here_reads_back_through_get_as_the_plain_payload(backend, payload):
    """get() must unwrap whatever get_or_compute stored: tests and routes still read the caches with get().

    Kills: leaking the {"payload", "soft_expires_at"} envelope through get()."""
    cache = make_cache()
    goc(cache, KEY, Builder(payload=lambda n: payload), 60, soft_ttl_seconds=30)

    assert cache.get(KEY) == payload


def test_invalidate_forces_a_rebuild(backend):
    """invalidate(key) and invalidate() clear what get_or_compute stored.

    Kills: values kept in a side store invalidate() does not know (refresh_analytics_materialized flushes through it)."""
    cache = make_cache()
    assert goc(cache, KEY, Builder(), 60) == {"gen": 1}

    cache.invalidate(KEY)
    after_key = Builder(payload=lambda n: {"gen": 2})
    assert goc(cache, KEY, after_key, 60) == {"gen": 2}
    assert after_key.calls == 1

    cache.invalidate()
    after_all = Builder(payload=lambda n: {"gen": 3})
    assert goc(cache, KEY, after_all, 60) == {"gen": 3}
    assert after_all.calls == 1


def test_a_redis_flush_forces_a_rebuild(redis_only):
    """flush_all_route_caches() (what refresh-analytics and sunshine-import run) must empty what get_or_compute stored, even
    though this worker's in-process mirror still holds the old value: Redis is authoritative.

    Kills: a hit path that consults the local mirror before Redis (every worker keeps serving pre-refresh numbers after the
    flush); a value stored outside the `ilcf:cache:` namespace (the flush deletes 0 keys)."""
    cache = make_cache()
    assert goc(cache, KEY, Builder(), 60) == {"gen": 1}

    deleted = cache_backend.flush_all_route_caches()

    assert deleted >= 1
    rebuilt = Builder(payload=lambda n: {"gen": 2})
    assert goc(cache, KEY, rebuilt, 60) == {"gen": 2}
    assert rebuilt.calls == 1


# ---------------------------------------------------------------------------
# 2. Two gunicorn workers sharing one Redis
# ---------------------------------------------------------------------------

def load_second_worker(monkeypatch, fake):
    """A second copy of webapp.cache_backend with its own module state (locks, mirrors) on the same FakeRedis: a second process."""
    spec = importlib.util.spec_from_file_location("ilcf_cache_backend_second_worker", cache_backend.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_get_redis_client", lambda: fake)
    return module


def test_two_workers_sharing_redis_build_a_cold_key_once(redis_only, monkeypatch):
    """6 callers split over two workers (two module copies, one Redis) miss one key together: one builder call in total.

    Kills: single-flight that only exists inside one process (in-process locks without the Redis lock): it passes every
    single-process test above and still builds once PER WORKER - the 3x-per-restart cost this step exists to remove."""
    second_worker = load_second_worker(monkeypatch, redis_only.fake)
    name = f"lifecycle_shared_{uuid.uuid4().hex[:8]}"
    worker_a, worker_b = RouteCache(name), second_worker.RouteCache(name)
    builder = Builder(delay=0.5)

    def task(index):
        cache = worker_a if index % 2 == 0 else worker_b
        return goc(cache, fresh_key(), builder, 60)

    outcomes = run_concurrently(6, task)

    for index, outcome in enumerate(outcomes):
        assert outcome.done, f"caller {index} never returned"
        assert outcome.error is None, f"caller {index} raised {outcome.error!r}"
        assert outcome.value == {"gen": 1}, f"caller {index} got {outcome.value!r}"
    assert builder.calls == 1


def test_two_workers_share_one_refresh_of_a_stale_key(redis_only, monkeypatch, clock):
    """Both workers see the soft-expired entry; both return the stale value at once; only ONE refresh runs across the two.

    Kills: a refresh lock that is per process (each worker refreshes: two builds of a 30 s payload)."""
    second_worker = load_second_worker(monkeypatch, redis_only.fake)
    name = f"lifecycle_shared_{uuid.uuid4().hex[:8]}"
    worker_a, worker_b = RouteCache(name), second_worker.RouteCache(name)
    assert goc(worker_a, KEY, Builder(), 100, soft_ttl_seconds=10) == {"gen": 1}

    clock.advance(11)
    refresh_a = GatedBuilder(payload=lambda n: {"gen": 2})
    refresh_b = GatedBuilder(payload=lambda n: {"gen": 2})
    assert goc(worker_a, KEY, refresh_a, 100, soft_ttl_seconds=10) == {"gen": 1}
    assert refresh_a.started.wait(3.0), "worker A did not start the refresh"
    assert goc(worker_b, KEY, refresh_b, 100, soft_ttl_seconds=10) == {"gen": 1}
    time.sleep(0.3)
    refresh_a.release.set()
    refresh_b.release.set()

    assert wait_until(lambda: worker_b.get(KEY) == {"gen": 2}, 5.0), "the refreshed value never reached the other worker"
    assert refresh_a.calls + refresh_b.calls == 1


# ---------------------------------------------------------------------------
# 2b. A flush fences the builds in flight (review C2, unit R5)
# ---------------------------------------------------------------------------

# Review C2: a cold build that starts before `refresh-analytics` commits and stores after its flush keeps the pre-refresh payload for a
# full TTL (the warm step skips keys that are present).  Pinned contract: flush_all_route_caches() writes a fresh token to a Redis key
# outside the flushed `ilcf:cache:*` namespace BEFORE it scans; RouteCache reads that token before and after builder() and, when it
# changed, returns the payload to its caller WITHOUT storing it (the lock is freed as usual, so a caller queued behind that build
# rebuilds from post-flush data).  The key is `_FLUSH_GEN_KEY = "ilcf:flush_gen"` (named in the plan and in the route_perf_caching
# runbook); the other tests pin what the token has to do.  The flush runs in a second module copy (load_second_worker):
# refresh-analytics is another process than the web workers, so a counter kept in module memory cannot fence.

def test_the_flush_leaves_a_fresh_token_under_the_documented_key_outside_the_flushed_namespace(redis_only):
    """The plan and docs/runbooks/route_perf_caching.md name the key `ilcf:flush_gen`: it must exist as the module constant
    `_FLUSH_GEN_KEY`, lie outside the flushed `ilcf:cache:*` namespace, be empty before any flush, hold a value after the first flush
    and a DIFFERENT value after the second.

    Kills: no token; a token under another name (the runbook would describe code that is not there); a token inside the flushed
    namespace (the flush deletes it as it writes it); a constant, or a whole-second, token (two flushes in a row read back equal)."""
    key = getattr(cache_backend, "_FLUSH_GEN_KEY", None)
    assert key == "ilcf:flush_gen", "webapp.cache_backend._FLUSH_GEN_KEY does not exist yet (expected the value 'ilcf:flush_gen')"
    assert not fnmatchcase(key, "ilcf:cache:*"), "the token key must lie outside the namespace the flush deletes"
    fake = redis_only.fake
    assert fake.get(key) is None, "control failed: a token exists before any flush"

    cache_backend.flush_all_route_caches()
    first = fake.get(key)
    cache_backend.flush_all_route_caches()
    second = fake.get(key)

    assert first not in (None, b""), "the first flush wrote no token (or the flush deleted it)"
    assert second not in (None, b""), "the second flush left no token"
    assert second != first, "two flushes left the same token: a build that straddles the second one would not notice it"


def test_a_build_that_straddles_a_flush_is_not_stored_and_the_caller_queued_behind_it_rebuilds(redis_only, monkeypatch):
    """Timeline (the flushes run in the second module copy = the refresh-analytics process; every build runs in this one = a web worker):

        t0  an earlier refresh flushed once, so a flush token already exists (nothing was cached, so that flush deleted nothing)
        t1  caller A misses the key and starts building; its builder blocks (it read pre-refresh data) and will return "old"
        t2  the next refresh commits and flushes: nothing is cached yet, so it deletes nothing
        t3  caller B asks for the same key and queues behind A's build lock; its builder returns "new" (post-refresh data)
        t4  A's builder finishes

    Hand-computed: A returns "old" (its own request is served; one builder call) and "old" is never written to the cache namespace; the
    lock is freed, so B builds once, returns "new", and "new" is the only value ever written there and what get() returns afterwards.

    Kills: no fence (B adopts A's "old" and builds nothing - today's behaviour); a fence kept in module memory (the flush ran in another
    process); a constant or coarse token (the t0 and t2 flushes leave it unchanged, so A stores "old"); a flush that writes its token only
    when it deleted something (it deleted nothing at t2); a token inside the flushed namespace (the flush deletes it at once); storing
    first and deleting afterwards (the count of writes below); a fenced A that returns None, raises or rebuilds for itself; a fence that
    lets B build alongside A instead of queueing behind it."""
    fake = redis_only.fake
    written = []  # every value stored under ilcf:cache:* (setex goes through set)
    real_set = fake.set

    def recording_set(name, value, *args, **kwargs):
        if FakeRedis._bytes(name).startswith(b"ilcf:cache:"):
            written.append(FakeRedis._bytes(value).decode("utf-8"))
        return real_set(name, value, *args, **kwargs)

    monkeypatch.setattr(fake, "set", recording_set)
    refresher = load_second_worker(monkeypatch, fake)
    refresher.flush_all_route_caches()  # t0
    cache = make_cache()
    builder_a = GatedBuilder(payload=lambda n: "old")
    builder_b = Builder(payload=lambda n: "new")

    first = Background(lambda: goc(cache, KEY, builder_a, 60))  # t1
    assert builder_a.started.wait(3.0), "the first build never started"
    refresher.flush_all_route_caches()  # t2
    second = Background(lambda: goc(cache, KEY, builder_b, 60))  # t3
    time.sleep(0.3)
    assert not second.finished.is_set(), "the second caller did not wait for the build in flight"
    assert builder_b.calls == 0
    builder_a.release.set()  # t4

    assert first.wait(5.0), "the straddling build never finished"
    assert second.wait(8.0), "the queued caller is still blocked after the build it waited on finished"
    assert first.error is None
    assert second.error is None
    assert first.value == "old"
    assert builder_a.calls == 1
    stored_old = [value for value in written if "old" in value]
    assert stored_old == [], (
        "the payload built across the flush was stored (review C2): flush_all_route_caches() must write a fresh token to a key "
        "outside ilcf:cache:* before it scans, and _build_and_store must skip the store when the token changed across builder()"
    )
    assert builder_b.calls == 1, "the caller queued behind the fenced build did not rebuild; it adopted the pre-flush payload"
    assert second.value == "new"
    assert len(written) == 1, f"exactly one value (the queued caller's) may reach the cache namespace, got {written}"
    assert "new" in written[0]
    assert cache.get(KEY) == "new"


def test_a_build_that_finishes_while_the_flush_is_scanning_is_not_stored(redis_only, monkeypatch):
    """The token has to be written BEFORE the flush scans, not after.  A is building when the flush starts; the scan has settled which
    keys to delete (none: A has stored nothing) and A's build finishes inside that very call; the flush then ends.  The next caller must
    build for itself and be served its own value.

    Kills: a token written after the scan loop (A sees no change, stores "old" after the scan listed its keys, and nothing deletes it
    afterwards, so the next caller is served the pre-refresh payload for a full TTL)."""
    fake = redis_only.fake
    cache = make_cache()
    builder_a = GatedBuilder(payload=lambda n: "old")
    first = Background(lambda: goc(cache, KEY, builder_a, 60))
    assert builder_a.started.wait(3.0), "the first build never started"
    real_scan = fake.scan
    finished_inside_the_scan = []

    def scan_then_let_the_build_finish(*args, **kwargs):
        found = real_scan(*args, **kwargs)  # which keys this flush deletes is settled here; A has stored nothing yet
        builder_a.release.set()
        finished_inside_the_scan.append(first.wait(5.0))  # recorded, not asserted: the flush would swallow an exception raised here
        return found

    monkeypatch.setattr(fake, "scan", scan_then_let_the_build_finish)

    cache_backend.flush_all_route_caches()

    assert finished_inside_the_scan != [], "control failed: the flush never scanned"
    assert all(finished_inside_the_scan), "control failed: the build did not finish while the flush was scanning"
    assert first.error is None
    assert first.value == "old"
    follow_up = Builder(payload=lambda n: "new")
    result = goc(cache, KEY, follow_up, 60)
    assert follow_up.calls == 1, (
        "the payload of a build that finished during the flush was stored: the flush token must be written before the scan, not after"
    )
    assert result == "new"
    assert cache.get(KEY) == "new"


# (pin) passes today; kills a fence that suppresses too much.
def test_a_flush_that_finished_before_a_build_started_does_not_suppress_its_store(redis_only, monkeypatch):
    """(pin) Two other entries are cached, a flush in the second module copy deletes exactly those two, and only then does a cold build
    start and finish.  That build began after the flush, so its value is stored and the second call is a hit.

    Kills: a store skipped whenever a flush token exists (the cache would be dead after the first refresh); a store skipped on the first
    build a process makes after a flush it did not see (a 'last token seen' kept in memory and compared with Redis); a token or lock key
    swept up by the flush or counted in its result (the flush must return exactly the 2 cache entries it deleted)."""
    other = make_cache("lifecycle_other")
    other.set(("lifecycle", "x"), {"x": 1}, 60)
    other.set(("lifecycle", "y"), {"y": 2}, 60)
    refresher = load_second_worker(monkeypatch, redis_only.fake)
    cache = make_cache()
    builder = Builder(payload=lambda n: {"gen": n})

    deleted = refresher.flush_all_route_caches()
    first = goc(cache, KEY, builder, 60)
    second = goc(cache, KEY, builder, 60)

    assert deleted == 2
    assert first == {"gen": 1}
    assert second == {"gen": 1}
    assert builder.calls == 1
    assert cache.get(KEY) == {"gen": 1}


# (pin) passes today; kills a fence that assumes Redis.
def test_without_redis_a_build_is_still_stored():
    """(pin) With Redis off (the autouse fixture) flush_all_route_caches() returns 0 without raising, and a build still ends up cached:
    the first call builds, the second is a hit.

    Kills: fence code that dereferences the missing client (every build would raise); a build that is skipped because 'no token' reads as
    'a flush happened'."""
    cache = make_cache()
    builder = Builder(payload=lambda n: {"gen": n})

    flushed = cache_backend.flush_all_route_caches()
    first = goc(cache, KEY, builder, 60)
    second = goc(cache, KEY, builder, 60)

    assert flushed == 0
    assert first == {"gen": 1}
    assert second == {"gen": 1}
    assert builder.calls == 1
    assert cache.get(KEY) == {"gen": 1}


def test_a_redis_error_while_reading_the_flush_token_does_not_fail_the_build(redis_only, monkeypatch):
    """Every other Redis call RouteCache makes swallows its error and degrades (get and set to the local store, the lock to a per-process
    claim), so a Redis blip never turns a finished 30 s build into a 500.  The fence token is the one key the build path reads outside
    the cache and lock namespaces; here every read of such a key raises.  The build must still return its payload, from one builder call.
    Whether the payload is then stored is deliberately not pinned.

    Kills: an unguarded token read in _build_and_store (the exception escapes after the work is done); a test that passes because
    nothing reads the token at all (the control below fails today)."""
    fake = redis_only.fake
    real_get = fake.get
    failed_reads = []

    def get(name):
        if FakeRedis._bytes(name).startswith((b"ilcf:cache:", b"ilcf:lock:")):
            return real_get(name)
        failed_reads.append(FakeRedis._bytes(name))
        raise ConnectionError("Redis went away")

    monkeypatch.setattr(fake, "get", get)
    cache = make_cache()
    builder = Builder(payload=lambda n: {"gen": n})

    result = goc(cache, KEY, builder, 60)

    assert result == {"gen": 1}
    assert builder.calls == 1
    assert failed_reads != [], (
        "control failed: the build path never read a key outside ilcf:cache:* and ilcf:lock:* (the flush token), so there is no fence yet"
    )


def test_a_redis_error_while_writing_the_flush_token_neither_raises_nor_stops_the_deletion(redis_only, monkeypatch):
    """flush_all_route_caches() is the last step of long jobs (refresh-analytics after its commit, sunshine-import after its matviews),
    and deleting the stale keys is its job; the token is only the fence.  A Redis error while writing the token must therefore be logged
    and swallowed, and must not stop the keys from being deleted: Redis at its maxmemory limit with a noeviction policy refuses SET yet
    still accepts DEL, and a flush that aborts then would fail exactly when stale entries are filling the memory.  (The suite's other
    fake Redises, e.g. tests/test_search_perf.py's, have no set() at all: the same rule keeps those green.)  Here every write of a key
    outside the cache and lock namespaces raises, and two cache entries exist.  The flush must return 2 without raising and both entries
    must be gone.

    Kills: a token write outside the function's try block (a traceback would end a successful import before its summary); a token write
    inside the same try as the scan (its failure skips the deletion: nothing is flushed, the flush returns 0); a test that passes because
    the flush writes no token (the control below fails today)."""
    fake = redis_only.fake
    real_set = fake.set
    failed_writes = []
    cache = make_cache()
    cache.set(("lifecycle", "x"), {"x": 1}, 60)
    cache.set(("lifecycle", "y"), {"y": 2}, 60)

    def failing_set(name, value, *args, **kwargs):
        if FakeRedis._bytes(name).startswith((b"ilcf:cache:", b"ilcf:lock:")):
            return real_set(name, value, *args, **kwargs)
        failed_writes.append(FakeRedis._bytes(name))
        raise ConnectionError("Redis went away")

    monkeypatch.setattr(fake, "set", failing_set)

    flushed = cache_backend.flush_all_route_caches()

    assert failed_writes != [], (
        "control failed: the flush wrote no key outside ilcf:cache:* and ilcf:lock:* (the token), so there is no fence yet"
    )
    assert flushed == 2, "a failing token write must not stop the flush from deleting the cache entries"
    assert cache.get(("lifecycle", "x")) is None
    assert cache.get(("lifecycle", "y")) is None


# ---------------------------------------------------------------------------
# Builder spies (the routes' names, call-through)
# ---------------------------------------------------------------------------

# Builders each page calls directly (webapp/routes/analytics.py); one cold request calls each of them exactly once
# (measured: a sequential cold request of the unmodified code, 2026-10-28).
OVERVIEW_BUILDERS = (
    "get_network_graph", "get_anomaly_flags", "get_donor_concentration", "get_time_series",
    "get_nlp_spending_summary", "get_reconciliation_outliers", "get_state_race_analytics",
)
RISK_BUILDERS = ("get_anomaly_flags", "get_reconciliation_outliers")
NETWORKS_BUILDERS = (
    "get_network_graph", "get_vendor_expenditure_network", "get_lobbying_influence_graph",
    "get_irs527_ecosystem_graph", "get_state_federal_overlap_graph",
)
RELATIONSHIPS_BUILDERS = (
    "get_donor_cogiving_network", "get_committee_similarity_network", "get_candidate_competition_networks",
    "get_lobbying_influence_graph", "get_irs527_ecosystem_graph",
)
ANALYTICS_BUILDERS = tuple(dict.fromkeys(
    OVERVIEW_BUILDERS + RISK_BUILDERS + NETWORKS_BUILDERS + RELATIONSHIPS_BUILDERS
))
HOME_BUILDERS = ("_get_candidate_stats", "_query_top_donors", "_call_build_dashboard_insights")


class Spies:
    """Call-through wrappers on module attributes: record the bound arguments, optionally sleep, then call the real function."""

    def __init__(self, monkeypatch, *targets, delay=0.0):
        self._lock = threading.Lock()
        self.delay = delay
        self.calls = {}
        for module, names in targets:
            for name in names:
                self.calls[name] = []
                monkeypatch.setattr(module, name, self._wrap(name, getattr(module, name)))

    def _wrap(self, name, real):
        signature = inspect.signature(real)
        first = next(iter(signature.parameters))

        def wrapper(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            arguments = {key: value for key, value in bound.arguments.items() if key != first}
            with self._lock:
                self.calls[name].append(arguments)
            if self.delay:
                time.sleep(self.delay)
            return real(*args, **kwargs)

        wrapper.__name__ = name
        return wrapper

    def snapshot(self):
        with self._lock:
            return {name: len(calls) for name, calls in self.calls.items()}

    def count(self, name):
        return self.snapshot()[name]

    def windows(self, name):
        """The (date_from, date_to) windows the builder was called with; '' = unbounded."""
        found = set()
        with self._lock:
            recorded = list(self.calls[name])
        for arguments in recorded:
            scope = arguments.get("scope")
            if scope is not None:
                found.add((scope.start.isoformat() if scope.start else "", scope.end.isoformat() if scope.end else ""))
            else:
                found.add((arguments.get("date_from") or "", arguments.get("date_to") or ""))
        return found


def built_since(spies, before):
    """{builder: calls since `before`} for the builders that ran."""
    after = spies.snapshot()
    return {name: after[name] - before[name] for name in after if after[name] != before[name]}


def analytics_and_home_spies(monkeypatch, delay=0.0):
    return Spies(
        monkeypatch,
        (analytics_routes, ANALYTICS_BUILDERS),
        (main_routes, HOME_BUILDERS),
        delay=delay,
    )


# ---------------------------------------------------------------------------
# 3. The heavy analytics routes use get_or_compute and build once under a burst
# ---------------------------------------------------------------------------

def reaches_get_or_compute(function):
    """The chain of functions from `function` to a get_or_compute reference, or None.

    Reads each function's source and follows the names it mentions that resolve (through its module globals) to functions of the
    same module or of another webapp module, so a route that delegates to a helper (`_networks_graphs`, `_get_state_race_rows`...)
    is judged by the helper.  Method calls on other objects are not followed."""
    pending = [(function, [function.__name__])]
    seen = set()
    while pending:
        current, path = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        tree = ast.parse(textwrap.dedent(inspect.getsource(current)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "get_or_compute":
                return path
            if isinstance(node, ast.Name):
                if node.id == "get_or_compute":
                    return path
                target = current.__globals__.get(node.id)
                if inspect.isfunction(target) and (
                    target.__module__ == current.__module__ or target.__module__.startswith("webapp.")
                ):
                    pending.append((target, path + [target.__name__]))
    return None


def test_the_source_check_follows_helpers_and_can_fail(tmp_path):
    """Guard for the check below: it finds a reference one helper away and says no when there is none.

    Kills: a vacuous source check (always passes / always fails)."""
    probe = tmp_path / "lifecycle_probe_module.py"
    probe.write_text(
        "def route():\n    return helper()\n\n"
        "def helper():\n    return cache.get_or_compute(1, lambda: 2, 3)\n\n"
        "def other():\n    return cache.get(1)\n"
    )
    spec = importlib.util.spec_from_file_location("lifecycle_probe_module", probe)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert reaches_get_or_compute(module.route) == ["route", "helper"]
    assert reaches_get_or_compute(module.other) is None


HEAVY_ROUTE_FUNCTIONS = [
    pytest.param("dashboard", id="overview"),
    pytest.param("risk", id="risk"),
    pytest.param("networks", id="networks"),
    pytest.param("relationships", id="relationships"),
    pytest.param("state_races", id="state_race_list"),
    pytest.param("state_race_detail", id="state_race_detail"),
]


@pytest.mark.parametrize("function_name", HEAVY_ROUTE_FUNCTIONS)
def test_each_heavy_route_reaches_get_or_compute(function_name):
    """The brief's source assertion: the route function (or a helper it calls) references get_or_compute.

    Kills: leaving a heavy route on the get/set pattern, which has no lock spanning the compute."""
    function = getattr(analytics_routes, function_name, None)
    assert inspect.isfunction(function), f"webapp/routes/analytics.py has no {function_name}()"
    path = reaches_get_or_compute(function)
    assert path is not None, f"{function_name}() never reaches get_or_compute (directly or through the functions it calls)"


def fire_burst(app, url, count=6, timeout=90.0):
    """`count` anonymous GETs of `url`, released together; returns each status code (or the exception it raised)."""
    barrier = threading.Barrier(count)
    results = [None] * count

    def one(index):
        client = app.test_client()
        barrier.wait(timeout=30)
        try:
            results[index] = client.get(url).status_code
        except BaseException as exc:  # recorded; the test asserts on it
            results[index] = exc

    threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(count)]
    for thread in threads:
        thread.start()
    deadline = time.perf_counter() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.perf_counter()))
    return results


def first_state_race_href(app):
    """The first state-race link of the overview (path and query), read before any cache or spy is on."""
    response = app.test_client().get("/analytics/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    match = re.search(r'href="([^"]*?/analytics/state-races/[^"]+)"', html)
    assert match is not None, "the overview has no state-race link (seed changed?)"
    return match.group(1).replace("&amp;", "&")


BURST_CASES = [
    pytest.param("/analytics/", OVERVIEW_BUILDERS, id="overview"),
    pytest.param("/analytics/risk", RISK_BUILDERS, id="risk"),
    pytest.param("/analytics/networks", NETWORKS_BUILDERS, id="networks"),
    pytest.param("/analytics/relationships", RELATIONSHIPS_BUILDERS, id="relationships"),
    pytest.param("/analytics/state-races/", ("get_state_race_analytics",), id="state_race_list"),
    pytest.param("DETAIL", ("get_state_race_detail",), id="state_race_detail"),
]


@pytest.mark.parametrize("url, builders", BURST_CASES)
def test_a_burst_of_cold_requests_builds_each_heavy_payload_once(analytics_app, monkeypatch, url, builders):
    """6 anonymous GETs hit a cold cache together; every builder is slowed to 0.25 s so they all overlap.

    Hand-computed: one cold request calls each listed builder exactly once (measured on the unmodified code), so the burst must
    call each exactly once - the unmodified code calls each 6 times.  A 7th request afterwards changes nothing.
    Kills: a get_or_compute reference that does not actually span the build (wrong key, lock_timeout=0, builder run outside
    it); single-flight on only one of the two layers of a page (the overview's race list, the networks overlap graph); a lock that
    serialises the builds instead of sharing one (still 6 calls); caching that is off for the page."""
    if url == "DETAIL":
        url = first_state_race_href(analytics_app)
    analytics_app.config["ROUTE_PERF_CACHE_ENABLED"] = True
    _empty_route_caches()
    spies = Spies(monkeypatch, (analytics_routes, builders), delay=0.25)

    statuses = fire_burst(analytics_app, url)

    assert statuses == [200] * 6, statuses
    assert spies.snapshot() == {name: 1 for name in builders}

    before = spies.snapshot()
    assert analytics_app.test_client().get(url).status_code == 200
    assert built_since(spies, before) == {}


# ---------------------------------------------------------------------------
# 4. scripts/warm_caches.py
# ---------------------------------------------------------------------------

# The four windows of preset_options() under the frozen clock, as the pages hand them to the builders ('' = unbounded).
# Hand-computed: today 2026-10-26, current cycle 2026 = Jan 1 2025 .. Dec 31 2026 (a cycle is its even year and the odd year before),
# previous cycle 2024 = Jan 1 2023 .. Dec 31 2024, last 12 months = today - 365 days .. today (no 29 February between), all = unbounded.
PRESET_WINDOWS = {
    "cycle:2026": ("2025-01-01", "2026-12-31"),
    "cycle:2024": ("2023-01-01", "2024-12-31"),
    "last12m": ("2025-10-26", "2026-10-26"),
    "all": ("", ""),
}
PRESET_KEYS = list(PRESET_WINDOWS)
# Builders that take the window (date_from/date_to, or a TimeScope for the home page): each must see all four windows.
WINDOWED_BUILDERS = (
    "get_anomaly_flags", "get_network_graph", "get_donor_concentration", "get_time_series",
    "get_lobbying_influence_graph", "get_irs527_ecosystem_graph", "get_state_race_analytics",
) + HOME_BUILDERS


def import_warm_script(monkeypatch):
    assert WARM_SCRIPT.is_file(), "scripts/warm_caches.py does not exist yet"
    monkeypatch.delitem(sys.modules, "scripts.warm_caches", raising=False)  # a fresh import: import-time work must show
    return importlib.import_module("scripts.warm_caches")


def run_main(module, monkeypatch):
    """module.main() with a clean argv; returns the exit code (None counts as 0), whether it returned or raised SystemExit."""
    main = getattr(module, "main", None)
    assert callable(main), "scripts/warm_caches.py has no main()"
    monkeypatch.setattr(sys, "argv", ["warm_caches.py"])
    try:
        result = main()
    except SystemExit as stop:
        result = stop.code
    return 0 if result is None else result


def lingering_threads(before):
    """Threads started since `before` that are still running (an executor's idle pool workers do not count)."""
    return sorted(
        thread.name for thread in threading.enumerate()
        if thread not in before and thread.is_alive() and not thread.name.startswith("ThreadPoolExecutor")
    )


def run_the_warm_script(monkeypatch):
    """Spies on the builders, then the script's main(); returns (spies, exit code, threads still running afterwards)."""
    spies = analytics_and_home_spies(monkeypatch)
    module = import_warm_script(monkeypatch)
    threads_before = set(threading.enumerate())
    code = run_main(module, monkeypatch)
    return spies, code, lingering_threads(threads_before)


def replace_everywhere(monkeypatch, module, source_module, name, replacement):
    """Swap source_module.<name>, and every alias of the same function bound inside `module` (however the script imported it)."""
    original = getattr(source_module, name)
    monkeypatch.setattr(source_module, name, replacement)
    for attribute, value in list(vars(module).items()):
        if value is original:
            monkeypatch.setattr(module, attribute, replacement)


def window_of(call, preset_keys):
    """The preset key a recorded warm call is for: the one distinct preset key among its arguments (a str, or a TimeScope's key)."""
    args, kwargs = call
    found = set()
    for value in list(args) + list(kwargs.values()):
        key = value if isinstance(value, str) else getattr(value, "key", None)
        if isinstance(key, str) and key in preset_keys:
            found.add(key)
    assert len(found) == 1, (
        f"cannot tell which preset window this warm call is for (it must carry exactly one preset key): args={args!r} kwargs={kwargs!r}"
    )
    return next(iter(found))


def test_warm_script_exists_imports_without_side_effects_and_exposes_main(frozen, monkeypatch):
    """The file exists, importing it builds nothing, it exposes main(), and `python scripts/warm_caches.py` runs main().

    Kills: warming at import time (every importer, including this test, would pay a full warm); a script without a main() or
    without a __main__ guard (the refresh chain calls it as a script)."""
    assert WARM_SCRIPT.is_file(), "scripts/warm_caches.py does not exist yet"
    spies = analytics_and_home_spies(monkeypatch)

    module = import_warm_script(monkeypatch)

    assert callable(getattr(module, "main", None))
    assert spies.snapshot() == {name: 0 for name in spies.snapshot()}
    source = WARM_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"""__name__\s*==\s*['"]__main__['"]""", source), "no `if __name__ == '__main__'` guard"


def test_warm_script_can_be_loaded_as_a_plain_script_from_any_directory(tmp_path):
    """The refresh chain runs `python3 scripts/warm_caches.py` from the app directory: Python then puts scripts/ - not the repo
    root - on sys.path.  So the script must add the repo root itself, as the other scripts do.  Checked in an isolated interpreter
    (no PYTHONPATH, no current directory on sys.path) started elsewhere, which loads the file by path and stops before main().

    Kills: a script that imports webapp / database / config and works under pytest and from the repo root only."""
    assert WARM_SCRIPT.is_file(), "scripts/warm_caches.py does not exist yet"
    probe = (
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(WARM_SCRIPT.parent)!r})\n"
        f"namespace = runpy.run_path({str(WARM_SCRIPT)!r}, run_name='warm_caches_probe')\n"
        "assert callable(namespace.get('main')), 'the script has no main()'\n"
        "print('LOADED')\n"
    )

    result = subprocess.run([sys.executable, "-I", "-c", probe], cwd=tmp_path, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr[-2000:]
    assert "LOADED" in result.stdout


# Hand-computed: 2026-10-26 -> current cycle 2026, previous 2024.  2027-03-15 is an odd year, which belongs to cycle 2028 -> current
# cycle 2028, previous 2026.  last12m and all do not move.
PRESET_KEYS_BY_DAY = [
    pytest.param(date(2026, 10, 26), ["cycle:2026", "cycle:2024", "last12m", "all"], id="2026-10-26"),
    pytest.param(date(2027, 3, 15), ["cycle:2028", "cycle:2026", "last12m", "all"], id="2027-03-15"),
]


@pytest.mark.parametrize("today, expected_keys", PRESET_KEYS_BY_DAY)
def test_warm_script_calls_each_warm_function_once_per_preset_window(monkeypatch, today, expected_keys):
    """The brief's pin, with both warm functions replaced by recorders: main() calls warm_analytics_caches and
    warm_dashboard_home_cache once for each of the four preset_options() windows, and exits 0.  Run on two different days, so
    the windows must come from preset_options() and not from a list that happens to match one date.

    The window must be identifiable in the call (a preset key string or a TimeScope); the parameter name is not pinned.
    Kills: warming only the default window (4 calls for the current cycle, or 1 call); skipping `all` or `last12m`; warming a
    window twice; a hand-rolled window list (right on 2026-10-26, wrong on 2027-03-15); a non-zero exit."""
    freeze_clock(monkeypatch, today)
    module = import_warm_script(monkeypatch)
    analytics_calls, home_calls = [], []
    replace_everywhere(monkeypatch, module, analytics_routes, "warm_analytics_caches",
                       lambda *args, **kwargs: analytics_calls.append((args, kwargs)))
    replace_everywhere(monkeypatch, module, main_routes, "warm_dashboard_home_cache",
                       lambda *args, **kwargs: home_calls.append((args, kwargs)))

    code = run_main(module, monkeypatch)

    assert code == 0
    assert [key for key, _label in preset_options()] == expected_keys, "fixture drift: the four presets changed"
    assert sorted(window_of(call, expected_keys) for call in analytics_calls) == sorted(expected_keys)
    assert sorted(window_of(call, expected_keys) for call in home_calls) == sorted(expected_keys)


def test_warm_script_returns_only_when_the_warm_is_done_and_exits_zero(empty_app, monkeypatch):
    """Run for real on the empty schema: exit code 0, and no thread it started is still running when main() returns.

    Kills: handing the work to a background thread and returning (the next step of the refresh chain restarts the service on a
    half-warm cache, and the cache-hit tests below would race); a non-zero exit on a clean run."""
    spies, code, lingering = run_the_warm_script(monkeypatch)

    assert code == 0
    assert lingering == []
    assert spies.count("get_anomaly_flags") >= 1, "control failed: the warm built nothing"


def test_warm_script_warms_the_four_windows_for_every_page(empty_app, monkeypatch):
    """After main() every windowed builder, and the home builders, have run for exactly the four preset windows.

    Kills: the default window only; a missing window (the set is compared exactly); an extra window; a window passed to the
    builders in the wrong format ('' vs None is normalised, a date string of another shape is not)."""
    spies, code, _lingering = run_the_warm_script(monkeypatch)

    assert code == 0
    expected = set(PRESET_WINDOWS.values())
    wrong = {name: sorted(spies.windows(name)) for name in WINDOWED_BUILDERS if spies.windows(name) != expected}
    assert wrong == {}, f"each builder must see exactly {sorted(expected)}"


PAGE_PATHS = [
    pytest.param("/analytics/", id="overview"),
    pytest.param("/analytics/risk", id="risk"),
    pytest.param("/analytics/networks", id="networks"),
    pytest.param("/analytics/relationships", id="relationships"),
    pytest.param("/", id="home"),
]


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_after_the_warm_script_a_request_for_any_preset_window_is_a_cache_hit(empty_app, monkeypatch, path):
    """The point of the script: main(), then GET <page>?period=<preset> for the four presets and the bare URL (the default
    window): no builder runs for any of them.  A custom window afterwards DOES run builders (control: the spies see requests).

    Kills: warming keys the requests do not read (a different months/limit/window tuple for the non-default windows, e.g. the
    24-month default used for `all`); warming a window under the wrong axis; warm functions that only learned the default
    window (the other three presets miss); a spy that is not on the request path (the custom-window control fails)."""
    spies, code, _lingering = run_the_warm_script(monkeypatch)
    assert code == 0
    client = empty_app.test_client()

    misses = []
    for key in PRESET_KEYS:
        before = spies.snapshot()
        response = client.get(f"{path}?period={key}")
        assert response.status_code == 200, f"{path}?period={key} -> {response.status_code}"
        built = built_since(spies, before)
        if built:
            misses.append(f"{path}?period={key} rebuilt {built}")
    before = spies.snapshot()
    assert client.get(path).status_code == 200
    built = built_since(spies, before)
    if built:
        misses.append(f"{path} (default window, no query) rebuilt {built}")
    assert misses == []

    before = spies.snapshot()
    assert client.get(f"{path}?date_from=2025-02-03&date_to=2025-03-04").status_code == 200
    assert built_since(spies, before) != {}, "control failed: a window nobody warmed built nothing"


# ---------------------------------------------------------------------------
# 4b. The warm script refuses to warm per-process caches when Redis was meant to be used (review C18, unit R5)
# ---------------------------------------------------------------------------

# With REDIS_URL set but no usable client (Redis down, a wrong URL, the `redis` package missing) the caches the script fills are this
# process's own and vanish when it exits, yet it used to warm for minutes and exit 0, so the refresh chain went on to restart the service
# on a cold cache.  Pinned contract: REDIS_URL set and cache_backend._get_redis_client() None -> main() returns 1 and warms nothing.
# REDIS_URL unset or empty -> the documented fallback stays (warn, warm, exit 0).  REDIS_URL set and Redis reachable -> warm, exit 0.
# The log text of the refusal is not pinned.

def record_warm_calls(monkeypatch, module):
    """Replace both warm functions the script calls with recorders; returns (analytics_calls, home_calls)."""
    analytics_calls, home_calls = [], []
    replace_everywhere(monkeypatch, module, analytics_routes, "warm_analytics_caches",
                       lambda *args, **kwargs: analytics_calls.append((args, kwargs)))
    replace_everywhere(monkeypatch, module, main_routes, "warm_dashboard_home_cache",
                       lambda *args, **kwargs: home_calls.append((args, kwargs)))
    return analytics_calls, home_calls


def test_warm_script_exits_1_without_warming_when_redis_url_is_set_but_redis_is_unreachable(monkeypatch):
    """REDIS_URL is set to a name that cannot resolve and _get_redis_client returns None (so no Redis is contacted): main() must return 1
    and call neither warm function, for any of the four preset windows.

    Kills: exiting 0 (today's behaviour: both recorders see 4 calls); warming first and failing at the end; skipping only one of the two
    warm steps; raising instead of returning an exit code; any code other than 1."""
    monkeypatch.setenv("REDIS_URL", "redis://redis.invalid:6379/0")
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    module = import_warm_script(monkeypatch)
    analytics_calls, home_calls = record_warm_calls(monkeypatch, module)

    code = run_main(module, monkeypatch)

    assert code == 1, "main() must exit 1 when REDIS_URL is set but cache_backend._get_redis_client() is None"
    assert analytics_calls == [], "warm_analytics_caches ran although there is no Redis to warm"
    assert home_calls == [], "warm_dashboard_home_cache ran although there is no Redis to warm"


# (pin) passes today; kills a refusal that fires whenever REDIS_URL is set.
def test_warm_script_still_warms_and_exits_0_when_redis_url_is_set_and_redis_is_reachable(redis_only, monkeypatch):
    """(pin) REDIS_URL is set and _get_redis_client returns a live client (FakeRedis): the script warms every preset window and exits 0.

    Kills: a check on REDIS_URL alone (the warm would refuse in production, where Redis is up); a check that reads the module's cached
    client instead of calling _get_redis_client(), which is None until something has connected, so it would refuse on a fresh process;
    a recorder that is not on the call path (the control: the counts)."""
    monkeypatch.setenv("REDIS_URL", "redis://redis.invalid:6379/0")
    module = import_warm_script(monkeypatch)
    analytics_calls, home_calls = record_warm_calls(monkeypatch, module)

    code = run_main(module, monkeypatch)

    assert code == 0
    assert len(analytics_calls) == len(preset_options())
    assert len(home_calls) == len(preset_options())


# (pin) passes today; kills a refusal that ignores whether REDIS_URL is set.
@pytest.mark.parametrize("redis_url", [None, ""], ids=["unset", "empty"])
def test_warm_script_without_a_redis_url_still_warns_warms_and_exits_0(monkeypatch, redis_url):
    """(pin) With REDIS_URL unset, or set to the empty string an env file with a bare `REDIS_URL=` produces, and no client, the script keeps
    its documented fallback: it warms every preset window (this process's own caches) and exits 0.  Set explicitly so the result does not
    depend on the shell the suite runs in.

    Kills: refusing whenever the client is None (every dev machine and CI run would exit 1); testing `'REDIS_URL' in os.environ` instead
    of its value (the empty string counts as set)."""
    if redis_url is None:
        monkeypatch.delenv("REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    module = import_warm_script(monkeypatch)
    analytics_calls, home_calls = record_warm_calls(monkeypatch, module)

    code = run_main(module, monkeypatch)

    assert code == 0
    assert len(analytics_calls) == len(preset_options())
    assert len(home_calls) == len(preset_options())


# ---------------------------------------------------------------------------
# 5. create_app's prewarm skips what is already cached
# ---------------------------------------------------------------------------

def prewarm_with_create_app(db_path, join_timeout=60.0):
    """create_app with the prewarm on (it is skipped under TESTING), then wait for the threads it started."""
    before = set(threading.enumerate())
    app = create_app({
        "TESTING": False,
        "APP_ENV": "development",
        "DATABASE_PATH": db_path,
        "DATABASE_URL": "",
        "DASHBOARD_PREWARM_ENABLED": True,
        "ROUTE_PERF_CACHE_ENABLED": True,
        "REFRESH_PARAMS_REQUIRE_LOGIN": False,
    })
    started = [thread for thread in threading.enumerate() if thread not in before]
    for thread in started:
        thread.join(join_timeout)
    stuck = [thread.name for thread in started if thread.is_alive()]
    assert stuck == [], f"prewarm threads still running after {join_timeout} s: {stuck}"
    return app


# Builders whose payload only ONE page group reads (so a rebuild shows which group was rebuilt).
NETWORKS_RELATIONSHIPS_ONLY = (
    "get_vendor_expenditure_network", "get_state_federal_overlap_graph", "get_donor_cogiving_network",
    "get_committee_similarity_network", "get_candidate_competition_networks",
    "get_lobbying_influence_graph", "get_irs527_ecosystem_graph",
)
OVERVIEW_ONLY = ("get_donor_concentration", "get_time_series", "get_nlp_spending_summary", "get_state_race_analytics")
SHARED_WITH_OVERVIEW = ("get_anomaly_flags", "get_reconciliation_outliers", "get_network_graph")


def test_create_app_prewarm_skips_every_key_that_is_already_present(empty_app, monkeypatch):
    """Two prewarms in a row: the first builds every group (control), the second builds nothing.

    Kills: a prewarm that rebuilds regardless (today it rebuilds the home trio, risk and overview on every worker start); a
    no-op prewarm (the control fails); a skip decided by a process flag instead of the cache; truthiness checks that rebuild an
    empty-list payload (the empty schema caches [] for the race list)."""
    db_path = empty_app.config["DATABASE_PATH"]
    spies = analytics_and_home_spies(monkeypatch)

    prewarm_with_create_app(db_path)
    cold = spies.snapshot()
    for name in NETWORKS_RELATIONSHIPS_ONLY + OVERVIEW_ONLY + HOME_BUILDERS + ("get_anomaly_flags",):
        assert cold[name] >= 1, f"control failed: the first prewarm never ran {name}"

    prewarm_with_create_app(db_path)

    assert built_since(spies, cold) == {}


def test_create_app_prewarm_builds_only_the_missing_keys(empty_app, monkeypatch):
    """Risk, networks, relationships and the home page are cached by ordinary requests; the overview and the race list are not.
    A prewarm must then build the overview group and nothing else.

    Hand-computed: networks/relationships-only and home builders +0; overview-only builders +1 or more; the builders the overview
    shares with the risk and networks groups run at most once (for the overview), not again for the groups already present.
    Kills: all-or-nothing skipping (a single present key skips everything, so the overview stays cold, or none is skipped);
    rebuilding the present groups; skipping the missing one."""
    db_path = empty_app.config["DATABASE_PATH"]
    spies = analytics_and_home_spies(monkeypatch)
    client = empty_app.test_client()
    for path in ("/analytics/risk", "/analytics/networks", "/analytics/relationships", "/"):
        assert client.get(path).status_code == 200
    populated = spies.snapshot()
    for name in ("get_state_federal_overlap_graph", "get_donor_cogiving_network", "_get_candidate_stats"):
        assert populated[name] == 1, f"control failed: the request for {name} did not build it"

    prewarm_with_create_app(db_path)
    built = built_since(spies, populated)

    rebuilt_present = {name: built[name] for name in NETWORKS_RELATIONSHIPS_ONLY + HOME_BUILDERS if name in built}
    assert rebuilt_present == {}, "the prewarm rebuilt keys that were already present"
    for name in OVERVIEW_ONLY:
        assert built.get(name, 0) >= 1, f"the prewarm skipped {name}, whose key was missing"
    for name in SHARED_WITH_OVERVIEW:
        assert built.get(name, 0) <= 1, f"{name} ran {built.get(name)} times: the risk or networks group was rebuilt as well"


def forget_in_process_copies():
    """What a freshly started worker has: no in-process entries at all, while Redis keeps everything."""
    for obj in gc.get_objects():
        if isinstance(obj, RouteCache):
            local = getattr(obj, "_local", None)
            if hasattr(local, "clear"):
                local.clear()


def test_create_app_prewarm_skips_keys_another_worker_already_stored_in_redis(empty_app, redis_only, monkeypatch):
    """The deployment case: worker 1 (or the warm script) filled Redis; worker 2 starts with an empty process.  Its prewarm must
    read Redis, find every key, and build nothing.

    Kills: a presence check that only looks at this process's own entries (passes every in-process test above, then rebuilds all
    of it on each of the other workers' start); a skip decided by a per-process flag."""
    db_path = empty_app.config["DATABASE_PATH"]
    spies = analytics_and_home_spies(monkeypatch)

    prewarm_with_create_app(db_path)
    cold = spies.snapshot()
    for name in NETWORKS_RELATIONSHIPS_ONLY + OVERVIEW_ONLY + HOME_BUILDERS + ("get_anomaly_flags",):
        assert cold[name] >= 1, f"control failed: the first prewarm never ran {name}"

    forget_in_process_copies()
    prewarm_with_create_app(db_path)

    assert built_since(spies, cold) == {}


def test_create_app_prewarm_does_not_rebuild_the_state_race_details(analytics_app, monkeypatch):
    """The top-12 race details are the long tail of the warm (one full detail build); a second prewarm must not repeat it.

    Kills: the details block missing the check-before-build (the other blocks skip, this one rebuilds)."""
    spies = Spies(monkeypatch, (analytics_routes, ("get_state_race_details", "get_state_race_analytics")))
    db_path = analytics_app.config["DATABASE_PATH"]

    prewarm_with_create_app(db_path)
    assert spies.count("get_state_race_details") == 1, "control failed: the seeded races' details were not built"
    cold = spies.snapshot()

    prewarm_with_create_app(db_path)

    assert built_since(spies, cold) == {}


# ---------------------------------------------------------------------------
# 6. A GET with load_mode=full never rebuilds anything
# ---------------------------------------------------------------------------

# Every analytics page that reads the stored snapshot (_load_snapshot_state); relationships and the race pages do not.
FULL_MODE_PAGES = ("/analytics/", "/analytics/networks", "/analytics/risk", "/analytics/donors", "/analytics/geography")
FULL_MODE_QUERIES = [
    pytest.param("load_mode=full", id="plain"),
    pytest.param("load_mode=full&refresh_full=1", id="refresh_full"),
    pytest.param("load_mode=full&sync_full=1", id="sync_full"),
    pytest.param("load_mode=full&refresh_full=1&sync_full=1", id="refresh_and_sync"),
]
STALE_SNAPSHOT_MARKER = "STALE-SNAPSHOT-MARKER-COMMITTEE"


def stale_snapshot():
    """What get_dashboard_snapshot returns for a stored snapshot older than its TTL: complete, with a payload, is_stale true."""
    return {
        "cache_key": "dashboard_full:stale-fixture",
        "status": "completed",
        "payload": {
            "concentration": [{
                "committee_name": STALE_SNAPSHOT_MARKER, "total_amount": 1234.0, "donor_count": 3,
                "top1_share": 0.5, "top5_share": 1.0, "top10_share": 1.0, "hhi": 3500.0, "gini": 0.25,
            }],
            "nlp_summary": [],
        },
        "error_message": None,
        "completed_at": "2026-01-02 03:04:05",
        "updated_at": "2026-01-02 03:04:05",
        "age_seconds": 86400.0,
        "is_stale": True,
        "is_fresh": False,
    }


def install_stale_snapshot(monkeypatch):
    def fake_get_dashboard_snapshot(conn, params, ttl_seconds=900):
        return stale_snapshot()

    monkeypatch.setattr(analytics_db, "get_dashboard_snapshot", fake_get_dashboard_snapshot)
    monkeypatch.setattr(analytics_routes, "get_dashboard_snapshot", fake_get_dashboard_snapshot)


class Forbidden:
    """Stands in for a function a GET must never reach: records the call, then raises."""

    def __init__(self, label):
        self.label = label
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError(f"a GET request reached {self.label}")


@pytest.fixture
def no_rebuilds(monkeypatch):
    """Everything that rebuilds or queues a snapshot or the materialized tables, on every module that names it."""
    forbidden = {}

    def forbid(label, *targets):
        spy = Forbidden(label)
        forbidden[label] = spy
        for owner, name in targets:
            monkeypatch.setattr(owner, name, spy, raising=False)

    forbid("refresh_analytics_materialized",
           (analytics_db, "refresh_analytics_materialized"), (analytics_routes, "refresh_analytics_materialized"))
    forbid("build_dashboard_full_snapshot",
           (analytics_db, "build_dashboard_full_snapshot"), (analytics_routes, "build_dashboard_full_snapshot"))
    forbid("save_dashboard_snapshot",
           (analytics_db, "save_dashboard_snapshot"), (analytics_routes, "save_dashboard_snapshot"))
    forbid("_schedule_snapshot_refresh", (analytics_routes, "_schedule_snapshot_refresh"))
    forbid("_run_snapshot_job", (analytics_routes, "_run_snapshot_job"))
    executor = getattr(analytics_routes, "_snapshot_executor", None)
    if executor is not None:
        forbid("_snapshot_executor.submit", (executor, "submit"))
    yield forbidden
    running = getattr(analytics_routes, "_running_snapshot_keys", None)
    if isinstance(running, set):
        running.clear()  # a raising spy can leave a key behind that would turn off later tests' scheduling


def snapshot_rows(app):
    conn = get_db(app.config["DATABASE_PATH"])
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM analytics_snapshots").fetchone()["n"]
    finally:
        conn.close()


@pytest.mark.parametrize("snapshot", ["absent", "stale"])
@pytest.mark.parametrize("query", FULL_MODE_QUERIES)
def test_a_full_mode_get_never_rebuilds_or_queues_a_snapshot(analytics_app, no_rebuilds, monkeypatch, query, snapshot):
    """The brief's pin: a GET of any snapshot-reading page with load_mode=full (and refresh_full / sync_full, which an anonymous
    visitor reaches because the fixture turns the LD4 gate off) reaches none of the rebuild paths, whether the stored snapshot is
    absent (empty table) or stale (older than its TTL), and writes no snapshot row.

    Every spy records AND raises, and the counters are asserted too, so an implementation that wraps the call in try/except
    still fails.  Kills: removing only the queued rebuild but leaving the synchronous sync_full build (which calls
    build_dashboard_full_snapshot and, with no aggregate rows, refresh_analytics_materialized); removing the queue for a stale
    snapshot but not for an absent one (or the reverse); refresh_full still honoured; a snapshot row written as 'in_progress'
    before the build; a rebuild moved to another thread or helper (the executor and the job are spied too)."""
    if snapshot == "stale":
        install_stale_snapshot(monkeypatch)
    rows_before = snapshot_rows(analytics_app)
    client = analytics_app.test_client()

    problems = []
    for path in FULL_MODE_PAGES:
        try:
            response = client.get(f"{path}?{query}")
        except AssertionError as exc:
            problems.append(f"{path}?{query}: {exc}")
            continue
        if response.status_code != 200:
            problems.append(f"{path}?{query}: status {response.status_code}")

    assert problems == []
    reached = {label: len(spy.calls) for label, spy in no_rebuilds.items() if spy.calls}
    assert reached == {}
    assert snapshot_rows(analytics_app) == rows_before


def test_a_full_mode_get_serves_the_stored_snapshot_instead_of_rebuilding_it(analytics_app, no_rebuilds, monkeypatch):
    """"Log and serve what exists" (plan P7): with a stale stored snapshot, /analytics/donors?load_mode=full renders the stored
    payload (its committee row) and rebuilds nothing.

    Kills: dropping the stored snapshot altogether (the marker row would be missing and the page recomputed live); refreshing
    the stale snapshot on the way."""
    install_stale_snapshot(monkeypatch)

    response = analytics_app.test_client().get("/analytics/donors?load_mode=full")

    assert response.status_code == 200
    assert STALE_SNAPSHOT_MARKER in response.get_data(as_text=True)
    reached = {label: len(spy.calls) for label, spy in no_rebuilds.items() if spy.calls}
    assert reached == {}


def test_a_full_mode_page_does_not_say_a_build_is_running_when_none_can_start(analytics_app, monkeypatch):
    """BEYOND THE BRIEF (delete if the wording is decided differently): with no stored snapshot, _status.html says "Full snapshot
    is building in the background. Reload this page in a few seconds." for status 'empty' and "Snapshot refresh was queued." after a
    queued refresh.  Once GETs no longer build snapshots both are false.  Any attempt to queue is neutralised here so nothing
    real starts; this test reads the page text only.

    Kills: removing the build but leaving the reader waiting for it."""
    monkeypatch.setattr(analytics_routes, "_schedule_snapshot_refresh", lambda *args, **kwargs: False, raising=False)
    client = analytics_app.test_client()

    claims = []
    for path in FULL_MODE_PAGES:
        html = client.get(f"{path}?load_mode=full").get_data(as_text=True)
        for claim in ("building in the background", "refresh was queued"):
            if claim in html:
                claims.append(f"{path}: {claim!r}")

    assert claims == []
