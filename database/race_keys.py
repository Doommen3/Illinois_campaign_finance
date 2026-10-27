"""Race keys: one readable, digest-free slug per race, shared by the state and federal builders.

A key is the lowercase alphanumeric runs of every non-empty part, joined by single
hyphens, so spelling variants of one race (case, whitespace, punctuation) share a key:
``race_key("State Representative", "Representative District", "45")`` ->
``"state-representative-representative-district-45"``.

Keys minted before 2026-10 carried a ``-<10 hex>`` sha1 suffix; ``canonical_race_key``
strips it so old bookmarks can redirect to the current key.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LEGACY_DIGEST_SUFFIX_RE = re.compile(r"-[0-9a-f]{10}$")


def race_key(*parts: object) -> str:
    """Join the lowercase alphanumeric runs of every part with single hyphens; None/blank parts add nothing."""
    tokens: list[str] = []
    for part in parts:
        if part is None:
            continue
        tokens.extend(_TOKEN_RE.findall(str(part).lower()))
    return "-".join(tokens)


def canonical_race_key(key: str) -> str:
    """Strip the legacy ``-<10 hex>`` digest suffix; a current key comes back unchanged."""
    return _LEGACY_DIGEST_SUFFIX_RE.sub("", key)
