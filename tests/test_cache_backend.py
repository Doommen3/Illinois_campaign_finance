"""LRU behaviour of RouteCache's in-process fallback (plan S-S1a, item B5).

With no Redis, RouteCache keeps ONE local slot, so every new cache key evicts
the previous one (state-race detail payloads thrash). Pinned contract: a
32-entry least-recently-used store per RouteCache instance, each entry with its
own TTL. Redis is forced off in every test and only the public get / set /
invalidate calls are used, so the storage structure is the implementer's choice.
"""
from __future__ import annotations

import time

import pytest

import webapp.cache_backend as cache_backend
from webapp.cache_backend import RouteCache

CAPACITY = 32
TTL = 60  # seconds; far longer than any test runs unless the clock is advanced


@pytest.fixture(autouse=True)
def _force_in_process_fallback(monkeypatch):
    # Same technique as tests/test_privacy_redactions.py: never touch a
    # developer's real Redis from the test suite.
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)


@pytest.fixture
def advance_clock(monkeypatch):
    """Shift time.monotonic() forward on demand; the real clock keeps running."""
    real_monotonic = time.monotonic
    shift = {"seconds": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + shift["seconds"])

    def advance(seconds):
        shift["seconds"] += seconds

    return advance


def key(n):
    return ("route", n)


def payload(n):
    return {"value": n}


def fill(cache, first, last):
    """set() keys first..last inclusive, in that order, each with payload(n)."""
    for n in range(first, last + 1):
        cache.set(key(n), payload(n), TTL)


def test_capacity_is_exactly_32_and_the_oldest_entry_is_evicted():
    cache = RouteCache("lru_capacity")

    fill(cache, 1, CAPACITY + 1)  # 33 distinct keys into a 32-entry store

    # Exactly one eviction: k1 (oldest) is gone, k2..k33 (32 keys) all survive.
    assert cache.get(key(1)) is None
    for n in range(2, CAPACITY + 2):
        assert cache.get(key(n)) == payload(n), f"k{n} should still be cached"


def test_get_counts_as_use_so_the_least_recently_used_entry_is_evicted():
    cache = RouteCache("lru_recency")
    fill(cache, 1, CAPACITY)  # exactly full; k1 is the oldest write
    assert cache.get(key(1)) == payload(1), "precondition: 32 entries fit"

    cache.set(key(CAPACITY + 1), payload(CAPACITY + 1), TTL)  # forces one eviction

    # k1 was read after k2..k32 were written, so k2 is now the least recently used.
    # (A FIFO store would evict k1 here instead.)
    assert cache.get(key(2)) is None
    assert cache.get(key(1)) == payload(1)
    for n in range(3, CAPACITY + 2):
        assert cache.get(key(n)) == payload(n), f"k{n} should still be cached"


def test_resetting_an_existing_key_at_capacity_replaces_it_without_evicting():
    cache = RouteCache("lru_reset")
    fill(cache, 1, CAPACITY)
    replacement = {"value": "replaced"}

    # A middle key, not the oldest: an implementation that evicts before checking
    # whether the key already exists would silently drop k1 here.
    cache.set(key(16), replacement, TTL)

    assert cache.get(key(16)) == replacement
    for n in range(1, CAPACITY + 1):
        if n == 16:
            continue
        assert cache.get(key(n)) == payload(n), f"k{n} must survive a re-set of k16"


def test_each_entry_expires_on_its_own_ttl(advance_clock):
    cache = RouteCache("lru_ttl")
    cache.set(key(1), payload(1), 5)    # short-lived
    cache.set(key(2), payload(2), TTL)  # long-lived
    assert cache.get(key(1)) == payload(1), "precondition: both entries coexist"
    assert cache.get(key(2)) == payload(2), "precondition: both entries coexist"

    advance_clock(10)  # past k1's 5 s ttl, far inside k2's 60 s ttl

    assert cache.get(key(1)) is None
    assert cache.get(key(2)) == payload(2)


@pytest.mark.parametrize("call_style", ["no_argument", "explicit_none"])
def test_invalidate_without_a_key_clears_every_entry(call_style):
    cache = RouteCache("lru_clear_all")
    fill(cache, 1, 5)
    for n in range(1, 6):
        assert cache.get(key(n)) == payload(n), "precondition: five entries cached"

    if call_style == "no_argument":
        cache.invalidate()
    else:
        cache.invalidate(None)

    for n in range(1, 6):
        assert cache.get(key(n)) is None, f"k{n} should be gone"
    cache.set(key(99), payload(99), TTL)
    assert cache.get(key(99)) == payload(99), "cache must stay usable after a full clear"


def test_invalidate_with_a_key_removes_only_that_entry_and_frees_its_slot():
    cache = RouteCache("lru_clear_one")
    fill(cache, 1, CAPACITY)

    cache.invalidate(("route", "never-cached"))  # absent key: no error, no side effect
    for n in range(1, CAPACITY + 1):
        assert cache.get(key(n)) == payload(n), f"k{n} must survive invalidating an absent key"

    cache.invalidate(key(2))
    assert cache.get(key(2)) is None
    survivors = [1] + list(range(3, CAPACITY + 1))
    for n in survivors:
        assert cache.get(key(n)) == payload(n), f"k{n} must survive invalidating k2"

    # k2's slot was freed, so one more key fits without evicting anybody.
    cache.set(key(99), payload(99), TTL)
    for n in survivors:
        assert cache.get(key(n)) == payload(n), f"k{n} must not be evicted by k99"
    assert cache.get(key(99)) == payload(99)


def test_cache_instances_do_not_share_entries_or_capacity():
    first = RouteCache("lru_first")
    second = RouteCache("lru_second")
    for n in range(1, CAPACITY + 1):
        first.set(key(n), {"cache": "first", "n": n}, TTL)
        second.set(key(n), {"cache": "second", "n": n}, TTL)

    # 64 entries in total: each instance holds its own full 32 under the same keys.
    for n in range(1, CAPACITY + 1):
        assert first.get(key(n)) == {"cache": "first", "n": n}, f"first lost k{n}"
        assert second.get(key(n)) == {"cache": "second", "n": n}, f"second lost k{n}"

    second.invalidate()

    for n in range(1, CAPACITY + 1):
        assert second.get(key(n)) is None, f"second still holds k{n}"
        assert first.get(key(n)) == {"cache": "first", "n": n}, f"clearing second dropped first's k{n}"
