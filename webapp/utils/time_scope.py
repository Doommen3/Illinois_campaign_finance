"""Time-scope resolver: one date window per request, shown and adjusted by the scope bar.

Pure functions over a query-string mapping (see section 3.1 of
.claude/plans/time-scope-overhaul.md). Query contract: ``period`` preset,
``date_from``/``date_to`` custom bounds, ``year``/``cycle`` shorthands.
Precedence: date_from/date_to > year > cycle > period > page default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

EN_DASH = "–"
AXIS_LABELS = {
    "received": "by receipt date",
    "filed": "by filing date",
    "transaction": "by transaction date",
    "reg_year": "by registration year",
    "cycle": "by election cycle",
    "none": "",
    "report_period": "by reporting period end",
}
_GRANULARITY = {"reg_year": "year", "cycle": "cycle"}
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_YEAR = r"[12][0-9]{3}"


@dataclass(frozen=True)
class TimeScope:
    key: str                   # "cycle:2026" | "all" | "last12m" | "custom"
    start: date | None         # None = unbounded
    end: date | None           # None = open-ended
    axis: str                  # key of AXIS_LABELS
    granularity: str           # "day" | "year" | "cycle"
    data_through: date | None  # latest loaded date for the page's source
    is_default: bool
    is_custom: bool
    label: str                 # "2026 Cycle" | "All time" | "Last 12 months" | "Custom range" | "Year 2024"
    window_label: str          # "Jan 1, 2025 – Oct 21, 2026"
    axis_label: str            # AXIS_LABELS[axis]
    snap_note: str             # "" or e.g. "shown as registration years 2025–2026"


def current_cycle(today: date | None = None) -> int:
    """Election cycle containing ``today``: an even year is its own cycle, an odd year belongs to year + 1."""
    year = (today or date.today()).year
    return year + year % 2


def cycle_window(cycle: int) -> tuple[date, date]:
    return date(cycle - 1, 1, 1), date(cycle, 12, 31)


def preset_options(today: date | None = None) -> list[tuple[str, str]]:
    cycle = current_cycle(today)
    return [
        (f"cycle:{cycle}", f"{cycle} Cycle"),
        (f"cycle:{cycle - 2}", f"{cycle - 2} Cycle"),
        ("last12m", "Last 12 months"),
        ("all", "All time"),
    ]


def _preset(key, start, end, label, custom=False) -> dict:
    return {"key": key, "start": start, "end": end, "label": label, "is_custom": custom}


def _cycle_preset(year: int) -> dict:
    cycle = year + year % 2  # an odd year snaps up to the cycle it belongs to
    start, end = cycle_window(cycle)
    return _preset(f"cycle:{cycle}", start, end, f"{cycle} Cycle")


def _year(value) -> int | None:
    text = str(value or "").strip()
    return int(text) if re.fullmatch(_YEAR, text) else None


def _iso(value) -> date | None:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text) if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text) else None
    except ValueError:
        return None


def _period(value, today: date) -> dict | None:
    """Map a ``period`` value (modern or legacy) to a scope dict; None when unknown."""
    text = str(value or "").strip()
    cycle = re.fullmatch(rf"cycle:({_YEAR})|({_YEAR})cycle", text)
    if cycle:
        return _cycle_preset(int(cycle.group(1) or cycle.group(2)))
    if text == "all":
        return _preset("all", None, None, "All time")
    if text in ("last12m", "1y"):
        return _preset("last12m", today - timedelta(days=365), today, "Last 12 months")
    if text == "2y":  # legacy: keeps old links meaning the same window
        return _preset("custom", today - timedelta(days=730), today, "Past 2 years", True)
    return None


def parse_scope_args(args, *, default: str = "cycle:current", today: date | None = None) -> dict:
    """Resolve query args to {key, start, end, label, is_custom, is_default}."""
    today = today or date.today()
    if default == "cycle:current":
        default = f"cycle:{current_cycle(today)}"
    base = _period(default, today)
    lo, hi = _iso(args.get("date_from")), _iso(args.get("date_to"))
    year, cycle = _year(args.get("year")), _year(args.get("cycle"))
    if lo or hi:
        if lo and hi and lo > hi:
            lo, hi = hi, lo
        found = _preset("custom", lo, hi, "Custom range", True)
    elif year:
        found = _preset("custom", date(year, 1, 1), date(year, 12, 31), f"Year {year}", True)
    elif cycle:
        found = _cycle_preset(cycle)
    else:
        found = _period(args.get("period"), today) or base
    found["is_default"] = not found["is_custom"] and found["key"] == base["key"]
    return found


def _snap(start, end, granularity):
    """Widen the bounded sides to whole years/cycles; returns (start, end, snap_note)."""
    if granularity == "day":
        return start, end, ""
    unit, number, back = (
        ("registration year", lambda d: d.year, 0) if granularity == "year"
        else ("election cycle", current_cycle, 1)
    )
    first = number(start) if start else None
    last = number(end) if end else None
    new_start = date(first - back, 1, 1) if start else None
    new_end = date(last, 12, 31) if end else None
    if (new_start, new_end) == (start, end):
        return start, end, ""
    if first and last:
        plural = first != last
        span = f"{first}{EN_DASH}{last}" if plural else str(first)
    else:
        plural, span = True, (f"{first} onward" if first else f"through {last}")
    return new_start, new_end, f"shown as {unit}{'s' if plural else ''} {span}"


def _fmt(day: date) -> str:
    return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"


def _window_label(start, end, data_through) -> str:
    shown_end = min(end, data_through) if end and data_through else (end or data_through)
    if start and shown_end:
        return f"{_fmt(start)} {EN_DASH} {_fmt(shown_end)}"
    if shown_end:
        return f"Through {_fmt(shown_end)}"
    if start:
        return f"Since {_fmt(start)}"
    return "All available data"


def resolve_time_scope(*, default: str = "cycle:current", axis: str = "received", today: date | None = None,
                       args=None, data_through: date | None = None) -> TimeScope:
    """Resolve the request's time scope; ``args`` defaults to ``flask.request.args``."""
    today = today or date.today()
    if args is None:
        from flask import request

        args = request.args
    parsed = parse_scope_args(args, default=default, today=today)
    granularity = _GRANULARITY.get(axis, "day")
    start, end, snap_note = _snap(parsed["start"], parsed["end"], granularity)
    return TimeScope(
        key=parsed["key"], start=start, end=end, axis=axis, granularity=granularity,
        data_through=data_through, is_default=parsed["is_default"], is_custom=parsed["is_custom"],
        label=parsed["label"], window_label=_window_label(start, end, data_through),
        axis_label=AXIS_LABELS[axis], snap_note=snap_note,
    )


def scope_params(scope: TimeScope) -> dict:
    """Query params that reproduce ``scope``: ``period`` for presets, bounds for custom ranges (never both)."""
    if not scope.is_custom:
        return {"period": scope.key}
    return {name: day.isoformat() for name, day in (("date_from", scope.start), ("date_to", scope.end)) if day}


def scope_cache_key(scope: TimeScope) -> str:
    return f"{scope.axis}:{scope.start.isoformat() if scope.start else ''}:{scope.end.isoformat() if scope.end else ''}"


def scope_where(scope: TimeScope, column: str, *, style: str = "qmark", prefix: str = "AND"):
    """SQL fragment + params restricting ``column`` to the window; ("", []) / ("", {}) when unbounded."""
    named = style == "named"
    bounds = (("ts_start", ">=", scope.start), ("ts_end", "<=", scope.end))
    present = [(name, op, day.isoformat()) for name, op, day in bounds if day]
    sql = " AND ".join(f"{column} {op} {':' + name if named else '?'}" for name, op, _ in present)
    params = {name: value for name, _, value in present} if named else [value for _, _, value in present]
    return (f" {prefix} {sql}" if sql else ""), params


def scope_cycles(scope: TimeScope) -> tuple[int, ...] | None:
    """Election cycles overlapping the window; None when it is unbounded below."""
    if scope.start is None:
        return None
    last = current_cycle(scope.end) if scope.end else current_cycle()
    return tuple(range(current_cycle(scope.start), last + 1, 2))
