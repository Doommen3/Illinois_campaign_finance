"""Link helpers for the time-scope bar (time-scope overhaul, step 0.2).

Registered as Jinja globals in ``webapp/app.py``. They rebuild the *current*
request URL with the time parameters replaced, so a preset click or a custom
range keeps every other filter on the page (q, office, sort, ...) and resets
pagination.
"""

from __future__ import annotations

from urllib.parse import urlencode

from flask import request

# Query parameters that express the active time scope. Replaced wholesale by
# the bar's links/form; dropped from the "keep everything else" set.
TIME_PARAMS: tuple[str, ...] = (
    "period",
    "date_from",
    "date_to",
    "cycle",
    "year",
    "start_date",
    "end_date",
)

_DROP_WITH_TIME: tuple[str, ...] = TIME_PARAMS + ("page",)


def _carried_args() -> list[tuple[str, str]]:
    """Current query args minus time params and pagination (multi-valued safe)."""
    return [
        (key, value)
        for key, value in request.args.items(multi=True)
        if key not in _DROP_WITH_TIME
    ]


def scope_url(**time_params: str | None) -> str:
    """Current path with the time scope replaced by ``time_params``.

    ``scope_url(period="cycle:2024")`` or ``scope_url(date_from="2025-01-01",
    date_to="2025-06-30")``. ``None``/empty values are omitted.
    """
    items = _carried_args()
    items.extend((k, v) for k, v in time_params.items() if v)
    query = urlencode(items, doseq=False)
    return f"{request.path}?{query}" if query else request.path


def scope_form_hidden() -> list[tuple[str, str]]:
    """(name, value) pairs for hidden inputs so a custom-range form keeps the page's other filters."""
    return _carried_args()


def scope_presets() -> list[tuple[str, str]]:
    """Preset (key, label) pairs for the bar; lazy import keeps app start independent of the resolver."""
    from webapp.utils.time_scope import preset_options

    return preset_options()
