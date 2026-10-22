"""Step 0.1 - pure unit tests for webapp/utils/time_scope.py (the time-scope resolver).

Written tests-first against "Contract for 0.1" in .claude/plans/time-scope-overhaul.md (section 5).
Fixed clock: TODAY = 2026-10-22; cycle 2026 = 2025-01-01..2026-12-31; cycle 2024 = 2023-01-01..2024-12-31.
Only the single Flask test touches the app; everything else is pure and passes ``today=`` explicitly.
"""
import dataclasses
from datetime import date

import pytest

from database.connection import init_db
from webapp.app import create_app
from webapp.utils.time_scope import (
    current_cycle,
    cycle_window,
    parse_scope_args,
    preset_options,
    resolve_time_scope,
    scope_cache_key,
    scope_cycles,
    scope_params,
    scope_where,
)

D = date
TODAY = D(2026, 10, 22)
EN = "–"  # en dash


def _scope(args, axis="received", today=TODAY, **kw):
    return resolve_time_scope(args=args, axis=axis, today=today, **kw)


def _expect(actual, **want):
    """Assert each named field separately (dict from parse_scope_args, or a TimeScope)."""
    got = actual if isinstance(actual, dict) else dataclasses.asdict(actual)
    for field, value in want.items():
        assert got[field] == value, f"{field}: got {got[field]!r}, want {value!r}"


# --- current_cycle / cycle_window / preset_options -------------------------------------------

@pytest.mark.parametrize("day, cycle", [
    (D(2026, 1, 1), 2026), (D(2026, 12, 31), 2026),   # even year -> itself, both edges
    (D(2025, 1, 1), 2026), (D(2025, 12, 31), 2026),   # odd year -> year + 1, both edges
    (D(2027, 1, 5), 2028), (D(2028, 12, 31), 2028),   # rollover
])
def test_current_cycle(day, cycle):
    assert current_cycle(day) == cycle


def test_current_cycle_without_argument_uses_system_today():
    assert current_cycle() == current_cycle(D.today())


@pytest.mark.parametrize("cycle, start, end", [
    (2024, D(2023, 1, 1), D(2024, 12, 31)),
    (2026, D(2025, 1, 1), D(2026, 12, 31)),
    (2028, D(2027, 1, 1), D(2028, 12, 31)),
])
def test_cycle_window(cycle, start, end):
    assert cycle_window(cycle) == (start, end)


@pytest.mark.parametrize("today, current, previous", [(TODAY, 2026, 2024), (D(2027, 1, 5), 2028, 2026)])
def test_preset_options_exact_list(today, current, previous):
    assert preset_options(today) == [
        (f"cycle:{current}", f"{current} Cycle"),
        (f"cycle:{previous}", f"{previous} Cycle"),
        ("last12m", "Last 12 months"),
        ("all", "All time"),
    ]


def test_rollover_2027():
    """Jan 2027 starts the 2028 cycle: default and is_default move; legacy keys keep their old meaning."""
    jan = D(2027, 1, 5)
    _expect(_scope({}, today=jan), key="cycle:2028", start=D(2027, 1, 1), label="2028 Cycle", is_default=True)
    _expect(_scope({"period": "cycle:2026"}, today=jan), key="cycle:2026", is_default=False)
    _expect(_scope({"period": "2026cycle"}, today=jan), key="cycle:2026", start=D(2025, 1, 1), is_default=False)
    _expect(_scope({"cycle": "2027"}, today=jan), key="cycle:2028", is_default=True)


# --- parse_scope_args: mapping, precedence, shorthands, invalid input ------------------------

PARSE_CASES = [
    # legacy period keys (2y: 2024-10-22 + 730 days = 2026-10-22, no Feb 29 in between)
    ("2026cycle", {"period": "2026cycle"}, dict(key="cycle:2026", start=D(2025, 1, 1), label="2026 Cycle", is_custom=False)),
    ("2024cycle", {"period": "2024cycle"}, dict(key="cycle:2024", start=D(2023, 1, 1), end=D(2024, 12, 31), label="2024 Cycle", is_custom=False)),
    ("1y", {"period": "1y"}, dict(key="last12m", start=D(2025, 10, 22), end=TODAY, label="Last 12 months", is_custom=False)),
    ("2y", {"period": "2y"}, dict(key="custom", start=D(2024, 10, 22), end=TODAY, label="Past 2 years", is_custom=True)),
    # modern period keys; odd cycles snap UP (2025 -> 2026, 2027 -> 2028)
    ("cycle:2024", {"period": "cycle:2024"}, dict(key="cycle:2024", start=D(2023, 1, 1), end=D(2024, 12, 31), label="2024 Cycle", is_custom=False)),
    ("cycle:2025", {"period": "cycle:2025"}, dict(key="cycle:2026", start=D(2025, 1, 1), label="2026 Cycle")),
    ("cycle:2027", {"period": "cycle:2027"}, dict(key="cycle:2028", start=D(2027, 1, 1), end=D(2028, 12, 31), label="2028 Cycle")),
    ("last12m", {"period": "last12m"}, dict(key="last12m", start=D(2025, 10, 22), end=TODAY, label="Last 12 months")),
    ("all", {"period": "all"}, dict(key="all", start=None, end=None, label="All time", is_custom=False)),
    ("custom-no-dates-is-default", {"period": "custom"}, dict(key="cycle:2026", is_custom=False, is_default=True)),
    ("custom-with-date", {"period": "custom", "date_from": "2025-03-01"}, dict(key="custom", start=D(2025, 3, 1), end=None, is_custom=True)),
    # year= / cycle= shorthands
    ("year", {"year": "2024"}, dict(key="custom", start=D(2024, 1, 1), end=D(2024, 12, 31), label="Year 2024", is_custom=True, is_default=False)),
    ("cycle-even", {"cycle": "2024"}, dict(key="cycle:2024", start=D(2023, 1, 1), end=D(2024, 12, 31), label="2024 Cycle", is_custom=False)),
    ("cycle-odd-snaps-up", {"cycle": "2027"}, dict(key="cycle:2028", start=D(2027, 1, 1), end=D(2028, 12, 31), label="2028 Cycle")),
    # precedence: date_from/date_to > year > cycle > period
    ("dates-beat-all", {"date_from": "2025-03-01", "date_to": "2025-06-30", "year": "2022", "cycle": "2024", "period": "all"},
     dict(key="custom", start=D(2025, 3, 1), end=D(2025, 6, 30), label="Custom range", is_custom=True)),
    ("year-beats-cycle-and-period", {"year": "2022", "cycle": "2024", "period": "all"},
     dict(key="custom", start=D(2022, 1, 1), end=D(2022, 12, 31), label="Year 2022")),
    ("year-and-cycle-not-combined", {"year": "2024", "cycle": "2026"}, dict(start=D(2024, 1, 1), end=D(2024, 12, 31))),
    ("cycle-beats-period", {"cycle": "2024", "period": "all"}, dict(key="cycle:2024", start=D(2023, 1, 1))),
    ("date_to-does-not-inherit-period-start", {"period": "1y", "date_to": "2025-06-30"}, dict(key="custom", start=None, end=D(2025, 6, 30))),
    ("date_from-does-not-inherit-year-end", {"year": "2022", "date_from": "2023-02-01"}, dict(key="custom", start=D(2023, 2, 1), end=None)),
    # one-sided and swapped bounds
    ("only-from", {"date_from": "2025-03-01"}, dict(key="custom", start=D(2025, 3, 1), end=None, label="Custom range", is_custom=True, is_default=False)),
    ("only-to", {"date_to": "2025-06-30"}, dict(key="custom", start=None, end=D(2025, 6, 30), label="Custom range", is_custom=True)),
    ("swapped", {"date_from": "2025-06-30", "date_to": "2025-03-01"}, dict(start=D(2025, 3, 1), end=D(2025, 6, 30))),
    # invalid ISO dates are ignored (absent), not raised on and not defaulted wholesale
    ("bad-from-falls-to-period", {"date_from": "garbage", "period": "all"}, dict(key="all", is_custom=False)),
    ("impossible-dates-fall-to-year", {"date_from": "2025-02-30", "date_to": "2025-13-01", "year": "2022"}, dict(start=D(2022, 1, 1), end=D(2022, 12, 31))),
    ("empty-strings", {"date_from": "", "date_to": ""}, dict(key="cycle:2026", is_custom=False, is_default=True)),
    ("bad-from-good-to", {"date_from": "garbage", "date_to": "2025-06-30"}, dict(key="custom", start=None, end=D(2025, 6, 30), is_custom=True)),
    ("good-from-bad-to", {"date_from": "2025-03-01", "date_to": "junk"}, dict(key="custom", start=D(2025, 3, 1), end=None)),
    ("bad-year-does-not-raise", {"year": "abcd"}, dict(key="cycle:2026", is_custom=False, is_default=True)),
    # unknown / invalid period -> default
    ("garbage-period", {"period": "banana"}, dict(key="cycle:2026", is_default=True)),
    ("empty-period", {"period": ""}, dict(key="cycle:2026", is_default=True)),
    ("cycle-colon-empty", {"period": "cycle:"}, dict(key="cycle:2026", is_default=True)),
    ("cycle-colon-garbage", {"period": "cycle:20x4"}, dict(key="cycle:2026", is_default=True)),
]


@pytest.mark.parametrize("args, want", [pytest.param(a, w, id=i) for i, a, w in PARSE_CASES])
def test_parse_scope_args(args, want):
    _expect(parse_scope_args(args, today=TODAY), **want)


def test_parse_scope_args_returns_exactly_the_contract_keys():
    assert set(parse_scope_args({}, today=TODAY)) == {"key", "start", "end", "label", "is_custom", "is_default"}


def test_last12m_is_365_days_not_same_calendar_day_last_year():
    # 2028-03-01 minus 365 days = 2027-03-02 (the 366-day span crosses Feb 29, 2028); replace(year=) would say 2027-03-01
    _expect(parse_scope_args({"period": "1y"}, today=D(2028, 3, 1)), start=D(2027, 3, 2), end=D(2028, 3, 1))


@pytest.mark.parametrize("args, default, today, is_custom, is_default", [
    ({}, "cycle:current", TODAY, False, True),
    ({"period": "cycle:2026"}, "cycle:current", TODAY, False, True),
    ({"cycle": "2026"}, "cycle:current", TODAY, False, True),
    ({"period": "cycle:2025"}, "cycle:current", TODAY, False, True),     # snaps to 2026 == default key
    ({"period": "cycle:2024"}, "cycle:current", TODAY, False, False),
    ({"period": "all"}, "cycle:current", TODAY, False, False),
    ({"period": "1y"}, "cycle:current", TODAY, False, False),
    ({"date_from": "2025-03-01"}, "cycle:current", TODAY, True, False),
    ({"year": "2024"}, "cycle:current", TODAY, True, False),
    ({"period": "2y"}, "cycle:current", TODAY, True, False),
    ({"period": "cycle:2026"}, "cycle:current", D(2027, 1, 5), False, False),   # default rolled to 2028
    ({"period": "cycle:2028"}, "cycle:current", D(2027, 1, 5), False, True),
    ({}, "all", TODAY, False, True),
    ({"period": "all"}, "all", TODAY, False, True),
    ({"period": "cycle:2026"}, "all", TODAY, False, False),
    ({"year": "2024"}, "all", TODAY, True, False),
    ({"period": "banana"}, "all", TODAY, False, True),
])
def test_is_default_is_custom_truth_table(args, default, today, is_custom, is_default):
    _expect(parse_scope_args(args, default=default, today=today), is_custom=is_custom, is_default=is_default)


# --- resolve_time_scope: passthrough, granularity, snapping, frozen, Flask --------------------

def test_resolve_passes_default_axis_and_data_through_and_is_frozen():
    scope = resolve_time_scope(args={}, default="all", today=TODAY, data_through=D(2026, 10, 21))
    _expect(scope, key="all", axis="received", data_through=D(2026, 10, 21), is_default=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.key = "cycle:2026"


def test_default_cycle_runs_to_cycle_end_not_today():
    # INTERPRETATION (contract silent): cycle:YYYY uses cycle_window(), so end is Dec 31 of the cycle even for the current one
    _expect(_scope({}), key="cycle:2026", start=D(2025, 1, 1), end=D(2026, 12, 31))


@pytest.mark.parametrize("axis, granularity, axis_label", [
    ("received", "day", "by receipt date"), ("filed", "day", "by filing date"),
    ("transaction", "day", "by transaction date"), ("reg_year", "year", "by registration year"),
    ("cycle", "cycle", "by election cycle"), ("none", "day", ""),
])
def test_granularity_and_axis_label(axis, granularity, axis_label):
    _expect(_scope({}, axis=axis), axis=axis, granularity=granularity, axis_label=axis_label)


NOTE_Y = f"shown as registration years 2025{EN}2026"
NOTE_C = f"shown as election cycles 2024{EN}2026"
COLLAPSED_Y = ("shown as registration years 2025", "shown as registration year 2025")
COLLAPSED_C = ("shown as election cycles 2026", "shown as election cycle 2026")

SNAP_CASES = [
    ("reg_year", {"date_from": "2025-03-10", "date_to": "2026-08-15"}, D(2025, 1, 1), D(2026, 12, 31), NOTE_Y),
    ("reg_year", {"date_from": "2025-03-10", "date_to": "2025-08-15"}, D(2025, 1, 1), D(2025, 12, 31), COLLAPSED_Y),
    ("reg_year", {"date_from": "2025-01-01", "date_to": "2026-12-31"}, D(2025, 1, 1), D(2026, 12, 31), ""),   # already aligned
    ("reg_year", {"period": "last12m"}, D(2025, 1, 1), D(2026, 12, 31), NOTE_Y),    # 2025-10-22..2026-10-22 widens to whole years
    ("reg_year", {"period": "all"}, None, None, ""),
    ("cycle", {"date_from": "2023-05-01", "date_to": "2026-02-01"}, D(2023, 1, 1), D(2026, 12, 31), NOTE_C),   # cycles 2024..2026
    ("cycle", {"date_from": "2024-03-10", "date_to": "2025-06-01"}, D(2023, 1, 1), D(2026, 12, 31), NOTE_C),   # even-year start, odd-year end
    ("cycle", {"date_from": "2025-03-10", "date_to": "2026-08-15"}, D(2025, 1, 1), D(2026, 12, 31), COLLAPSED_C),  # odd start != year-1
    ("cycle", {"date_from": "2024-12-31", "date_to": "2024-12-31"}, D(2023, 1, 1), D(2024, 12, 31), ("shown as election cycles 2024", "shown as election cycle 2024")),
    ("cycle", {"period": "cycle:2024"}, D(2023, 1, 1), D(2024, 12, 31), ""),
    ("transaction", {"date_from": "2025-03-10", "date_to": "2026-08-15"}, D(2025, 3, 10), D(2026, 8, 15), ""),   # day axis: untouched
]


@pytest.mark.parametrize("axis, args, start, end, note", SNAP_CASES)
def test_snapping(axis, args, start, end, note):
    scope = _scope(args, axis=axis)
    _expect(scope, start=start, end=end)
    assert scope.snap_note in ((note,) if isinstance(note, str) else note)


def test_snapping_touches_only_the_bounded_side_and_keeps_preset_key():
    _expect(_scope({"date_from": "2025-03-10"}, axis="reg_year"), start=D(2025, 1, 1), end=None)
    _expect(_scope({"date_to": "2026-08-15"}, axis="reg_year"), start=None, end=D(2026, 12, 31))
    _expect(_scope({"period": "last12m"}, axis="reg_year"), key="last12m", is_custom=False)


def test_args_none_reads_flask_request_and_empty_mapping_does_not(tmp_path):
    init_db(str(tmp_path / "ts.db"))
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "ts.db"),
                      "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})
    with app.test_request_context("/?period=cycle:2024"):
        from_request = resolve_time_scope(today=TODAY)
        from_empty_mapping = resolve_time_scope(args={}, today=TODAY)
    _expect(from_request, key="cycle:2024", start=D(2023, 1, 1), end=D(2024, 12, 31))
    _expect(from_empty_mapping, key="cycle:2026")


# --- window_label ---------------------------------------------------------------------------

DT = D(2026, 10, 21)
LABEL_CASES = [
    ({"date_from": "2025-01-01", "date_to": "2025-06-30"}, None, f"Jan 1, 2025 {EN} Jun 30, 2025"),
    ({"date_from": "2025-01-01", "date_to": "2026-12-31"}, DT, f"Jan 1, 2025 {EN} Oct 21, 2026"),   # end later than data_through -> data_through
    ({"date_from": "2025-01-01", "date_to": "2025-06-30"}, DT, f"Jan 1, 2025 {EN} Jun 30, 2025"),   # end earlier -> end
    ({"date_to": "2025-06-30"}, None, "Through Jun 30, 2025"),
    ({"date_to": "2025-06-30"}, DT, "Through Jun 30, 2025"),
    ({"date_from": "2025-01-01"}, None, "Since Jan 1, 2025"),
    ({"date_from": "2025-01-01"}, DT, f"Jan 1, 2025 {EN} Oct 21, 2026"),   # open end falls back to data_through
    ({"period": "all"}, None, "All available data"),
    ({"period": "all"}, DT, "Through Oct 21, 2026"),
    ({}, DT, f"Jan 1, 2025 {EN} Oct 21, 2026"),   # current cycle clamped to loaded data (the plan's own example)
    ({"date_from": "2026-09-03", "date_to": "2026-09-30"}, None, f"Sep 3, 2026 {EN} Sep 30, 2026"),   # 'Sep' not 'Sept'/'September', no zero pad
]


@pytest.mark.parametrize("args, data_through, label", LABEL_CASES)
def test_window_label(args, data_through, label):
    assert _scope(args, data_through=data_through).window_label == label


@pytest.mark.parametrize("month, abbr", list(enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)))
def test_window_label_month_abbreviations(month, abbr):
    assert _scope({"date_to": f"2025-{month:02d}-05"}).window_label == f"Through {abbr} 5, 2025"


def test_resolved_labels():
    _expect(_scope({}), label="2026 Cycle")
    _expect(_scope({"period": "1y"}), label="Last 12 months")
    _expect(_scope({"period": "all"}), label="All time")
    _expect(_scope({"date_to": "2025-06-30"}), label="Custom range")
    _expect(_scope({"year": "2024"}), label="Year 2024")


# --- scope_params / scope_cache_key ----------------------------------------------------------

PARAM_CASES = [
    ({}, {"period": "cycle:2026"}),
    ({"period": "cycle:2024"}, {"period": "cycle:2024"}),
    ({"period": "1y"}, {"period": "last12m"}),
    ({"period": "all"}, {"period": "all"}),
    ({"date_from": "2025-03-01", "date_to": "2025-06-30"}, {"date_from": "2025-03-01", "date_to": "2025-06-30"}),
    ({"date_from": "2025-03-01"}, {"date_from": "2025-03-01"}),
    ({"date_to": "2025-06-30"}, {"date_to": "2025-06-30"}),
    ({"year": "2024"}, {"date_from": "2024-01-01", "date_to": "2024-12-31"}),
    ({"period": "2y"}, {"date_from": "2024-10-22", "date_to": "2026-10-22"}),
]


@pytest.mark.parametrize("args, params", PARAM_CASES)
def test_scope_params_exact_and_round_trips(args, params):
    scope = _scope(args)
    assert scope_params(scope) == params          # exact dict: never both forms, missing bounds omitted
    again = _scope(scope_params(scope))
    _expect(again, key=scope.key, start=scope.start, end=scope.end)


def test_scope_params_for_a_snapped_preset_is_still_the_preset():
    assert scope_params(_scope({"period": "last12m"}, axis="reg_year")) == {"period": "last12m"}


@pytest.mark.parametrize("args, axis, key", [
    ({"period": "cycle:2024"}, "received", "received:2023-01-01:2024-12-31"),
    ({"period": "cycle:2024"}, "filed", "filed:2023-01-01:2024-12-31"),
    ({"date_from": "2023-01-01", "date_to": "2024-12-31"}, "received", "received:2023-01-01:2024-12-31"),  # same window, same key
    ({"period": "all"}, "received", "received::"),
    ({"date_from": "2025-03-01"}, "transaction", "transaction:2025-03-01:"),
    ({"date_to": "2025-06-30"}, "received", "received::2025-06-30"),
    ({"period": "last12m"}, "reg_year", "reg_year:2025-01-01:2026-12-31"),   # keyed on the snapped window
])
def test_scope_cache_key(args, axis, key):
    assert scope_cache_key(_scope(args, axis=axis)) == key


def test_cache_key_tracks_the_rolling_window_but_not_data_through():
    assert scope_cache_key(_scope({"period": "last12m"})) != scope_cache_key(_scope({"period": "last12m"}, today=D(2026, 10, 23)))
    assert scope_cache_key(_scope({"period": "cycle:2024"}, data_through=DT)) == "received:2023-01-01:2024-12-31"


# --- scope_where -----------------------------------------------------------------------------

COL = "r.received_date"
QMARK_CASES = [
    ({"period": "cycle:2024"}, "AND", f" AND {COL} >= ? AND {COL} <= ?", ["2023-01-01", "2024-12-31"]),
    ({"period": "cycle:2024"}, "WHERE", f" WHERE {COL} >= ? AND {COL} <= ?", ["2023-01-01", "2024-12-31"]),
    ({"date_from": "2025-03-01"}, "AND", f" AND {COL} >= ?", ["2025-03-01"]),
    ({"date_to": "2025-06-30"}, "AND", f" AND {COL} <= ?", ["2025-06-30"]),
    ({"period": "all"}, "AND", "", []),
    ({"period": "all"}, "WHERE", "", []),
]
NAMED_CASES = [
    ({"period": "cycle:2024"}, f" AND {COL} >= :ts_start AND {COL} <= :ts_end", {"ts_start": "2023-01-01", "ts_end": "2024-12-31"}),
    ({"date_from": "2025-03-01"}, f" AND {COL} >= :ts_start", {"ts_start": "2025-03-01"}),
    ({"date_to": "2025-06-30"}, f" AND {COL} <= :ts_end", {"ts_end": "2025-06-30"}),
]


@pytest.mark.parametrize("args, prefix, sql, params", QMARK_CASES)
def test_scope_where_qmark(args, prefix, sql, params):
    got_sql, got_params = scope_where(_scope(args), COL, prefix=prefix)
    assert got_sql == sql
    assert got_params == params
    assert got_sql.count("?") == len(got_params)


@pytest.mark.parametrize("args, sql, params", NAMED_CASES)
def test_scope_where_named(args, sql, params):
    got_sql, got_params = scope_where(_scope(args), COL, style="named")
    assert got_sql == sql
    assert got_params == params


def test_scope_where_named_all_time_is_empty_and_column_is_used_verbatim():
    got_sql, got_params = scope_where(_scope({"period": "all"}), COL, style="named")
    assert got_sql == ""
    assert len(got_params) == 0
    expr = "COALESCE(a.d, b.d)"
    sql, _ = scope_where(_scope({"period": "cycle:2024"}), expr)
    assert sql == f" AND {expr} >= ? AND {expr} <= ?"


# --- scope_cycles ----------------------------------------------------------------------------

@pytest.mark.parametrize("args, cycles", [
    ({"period": "cycle:2024"}, (2024,)),
    ({"date_from": "2024-06-01", "date_to": "2025-06-01"}, (2024, 2026)),        # spans a cycle boundary
    ({"date_from": "2022-12-31", "date_to": "2026-01-01"}, (2022, 2024, 2026)),  # middle cycle must not be skipped
    ({"date_from": "2024-12-31", "date_to": "2024-12-31"}, (2024,)),             # last day of 2024 cycle
    ({"date_from": "2025-01-01", "date_to": "2025-01-01"}, (2026,)),             # first day of 2026 cycle
    ({"period": "last12m"}, (2026,)),                                            # 2025-10-22..2026-10-22
])
def test_scope_cycles_bounded(args, cycles):
    assert scope_cycles(_scope(args)) == cycles


def test_scope_cycles_all_time_is_none_not_empty():
    assert scope_cycles(_scope({"period": "all"})) is None


def test_scope_cycles_open_end_runs_to_the_real_current_cycle():
    expected = tuple(range(2022, current_cycle(D.today()) + 1, 2))   # 2021-03-01 is in cycle 2022
    assert scope_cycles(_scope({"date_from": "2021-03-01"})) == expected
