"""Per-entity analytics tabs for candidate and race detail pages (blueprint ``entity_analytics``).

COMPILE-ONLY STUB (plan step 5, tests first): the names below are the contract in
`.claude/plans/race-sort-and-entity-networks.md` ("Route contract -- blueprint
`entity_analytics`"). The blueprint has no routes and is NOT registered in
``webapp/app.py`` (step 6); ``_entity_cache_key`` raises until the step-6 implementation.
"""
from __future__ import annotations

from flask import Blueprint

from database.entity_analytics import (  # noqa: F401  (named imports: the route tests monkeypatch them here)
    ENTITY_CACHE_REV,
    get_entity_donors,
    get_entity_geography,
    get_entity_relationships,
    get_entity_risk,
    resolve_entity,
)
from database.entity_network import get_entity_network  # noqa: F401
from webapp.utils.time_scope import scope_cache_key  # noqa: F401

bp = Blueprint("entity_analytics", __name__)

ENTITY_TABS = ("network", "risk", "donors", "geography", "relationships")


def _entity_cache_key(kind, key, tab, scope, election_cycle, params) -> tuple:
    raise NotImplementedError("_entity_cache_key is not implemented (plan step 6)")
