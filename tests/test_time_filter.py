"""Tests for the global time period filter."""

import pytest
from datetime import date, timedelta

from webapp.utils.time_filter import (
    TIME_PERIODS,
    DEFAULT_PERIOD,
    build_filter_overrides,
    get_period_dates,
    period_cycle,
    period_cycles,
    period_qmark_date_clause,
    period_qmark_clause,
    period_to_date_window,
    period_window_with_override,
)


class TestTimePeriodDefinitions:

    def test_default_period_is_2026cycle(self):
        assert DEFAULT_PERIOD == "2026cycle"

    def test_time_periods_has_expected_options(self):
        keys = [p[0] for p in TIME_PERIODS]
        assert "2026cycle" in keys
        assert "2024cycle" in keys
        assert "1y" in keys
        assert "2y" in keys
        assert "all" in keys

    def test_all_periods_have_three_tuple_elements(self):
        for p in TIME_PERIODS:
            assert len(p) == 3, f"Period {p} should be (key, label, description)"


class TestGetPeriodDates:

    def test_2026cycle_starts_jan_2025(self):
        start, end = get_period_dates("2026cycle")
        assert start == "2025-01-01"
        assert end is not None

    def test_2024cycle_range(self):
        start, end = get_period_dates("2024cycle")
        assert start == "2023-01-01"
        assert end == "2024-12-31"

    def test_all_returns_none(self):
        start, end = get_period_dates("all")
        assert start is None
        assert end is None

    def test_1y_is_approximately_365_days_ago(self):
        start, end = get_period_dates("1y")
        expected_start = (date.today() - timedelta(days=365)).isoformat()
        assert start == expected_start
        assert end == date.today().isoformat()

    def test_unknown_key_returns_none(self):
        start, end = get_period_dates("bogus")
        assert start is None
        assert end is None


class TestPeriodQmarkClause:

    def test_all_returns_empty(self):
        period = {"key": "all", "start_date": None, "end_date": None}
        clause, params = period_qmark_clause("received_date", period)
        assert clause == ""
        assert params == []

    def test_2026cycle_returns_and_clause(self):
        period = {"key": "2026cycle", "start_date": "2025-01-01", "end_date": "2026-02-16"}
        clause, params = period_qmark_clause("received_date", period)
        assert "received_date >= ?" in clause
        assert "received_date <= ?" in clause
        assert params == ["2025-01-01", "2026-02-16"]

    def test_clause_uses_and_prefix_by_default(self):
        period = {"key": "1y", "start_date": "2025-02-16", "end_date": "2026-02-16"}
        clause, params = period_qmark_clause("expended_date", period)
        assert clause.strip().startswith("AND")

    def test_clause_uses_custom_prefix(self):
        period = {"key": "1y", "start_date": "2025-02-16", "end_date": "2026-02-16"}
        clause, params = period_qmark_clause("amount_date", period, prefix="WHERE")
        assert clause.strip().startswith("WHERE")

    def test_no_start_date_returns_empty(self):
        period = {"key": "2026cycle", "start_date": None, "end_date": None}
        clause, params = period_qmark_clause("received_date", period)
        assert clause == ""
        assert params == []

    def test_date_clause_wraps_with_date_function(self):
        period = {"key": "1y", "start_date": "2025-02-16", "end_date": "2026-02-16"}
        clause, params = period_qmark_date_clause("expended_date", period)
        assert "DATE(expended_date) >= ?" in clause
        assert "DATE(expended_date) <= ?" in clause
        assert params == ["2025-02-16", "2026-02-16"]


class TestPeriodHelpers:

    def test_period_to_date_window_defaults_to_period_bounds(self):
        period = {"key": "2026cycle", "start_date": "2025-01-01", "end_date": "2026-02-16"}
        date_from, date_to = period_to_date_window(period)
        assert date_from == "2025-01-01"
        assert date_to == "2026-02-16"

    def test_period_to_date_window_keeps_explicit_args(self):
        period = {"key": "2026cycle", "start_date": "2025-01-01", "end_date": "2026-02-16"}
        date_from, date_to = period_to_date_window(period, date_from="2025-03-01", date_to="2025-07-31")
        assert date_from == "2025-03-01"
        assert date_to == "2025-07-31"

    def test_period_cycle_selection(self):
        assert period_cycle({"key": "2026cycle"}) == 2026
        assert period_cycle({"key": "2024cycle"}) == 2024
        assert period_cycle({"key": "all"}) is None

    def test_period_cycles_for_range_period(self):
        cycles = period_cycles(
            {"key": "2y", "start_date": "2024-02-16", "end_date": "2026-02-16"}
        )
        assert cycles == (2024, 2026)

    def test_1y_window_is_subset_of_2y_window(self):
        one_year_start, one_year_end = get_period_dates("1y")
        two_year_start, two_year_end = get_period_dates("2y")
        assert one_year_start is not None and one_year_end is not None
        assert two_year_start is not None and two_year_end is not None
        assert two_year_start <= one_year_start
        assert one_year_end <= two_year_end


class TestPeriodWindowWithOverride:

    def test_no_override_uses_period_bounds(self):
        period = {"key": "2026cycle", "start_date": "2025-01-01", "end_date": "2026-02-16"}
        result = period_window_with_override(period)
        assert result == {"start": "2025-01-01", "end": "2026-02-16", "is_override": False}

    def test_empty_string_override_is_not_an_override(self):
        period = {"key": "2026cycle", "start_date": "2025-01-01", "end_date": "2026-02-16"}
        result = period_window_with_override(period, date_from="  ", date_to="")
        assert result["is_override"] is False
        assert result["start"] == "2025-01-01"
        assert result["end"] == "2026-02-16"

    def test_date_from_override_flags_is_override(self):
        period = {"key": "2026cycle", "start_date": "2025-01-01", "end_date": "2026-02-16"}
        result = period_window_with_override(period, date_from="2025-06-01")
        assert result["is_override"] is True
        assert result["start"] == "2025-06-01"
        assert result["end"] == "2026-02-16"

    def test_date_to_override_flags_is_override(self):
        period = {"key": "all", "start_date": None, "end_date": None}
        result = period_window_with_override(period, date_to="2024-12-31")
        assert result["is_override"] is True
        assert result["start"] is None
        assert result["end"] == "2024-12-31"


class TestBuildFilterOverrides:

    def test_drops_none_and_empty(self):
        items = build_filter_overrides(start_date="", end_date=None, year="  ")
        assert items == []

    def test_humanizes_keys(self):
        items = build_filter_overrides(start_date="2025-06-01", min_receipts="1000")
        labels = [label for label, _ in items]
        assert "Start Date" in labels
        assert "Min Receipts" in labels

    def test_preserves_order(self):
        items = build_filter_overrides(date_from="2025-06-01", date_to="2025-12-31")
        assert items == [("Date From", "2025-06-01"), ("Date To", "2025-12-31")]

    def test_strips_whitespace(self):
        items = build_filter_overrides(year=" 2026 ")
        assert items == [("Year", "2026")]


class TestTimeFilterInApp:
    """Integration tests that the time filter is injected into templates."""

    @pytest.fixture
    def app(self, tmp_path):
        import sqlite3
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE committees (id INTEGER PRIMARY KEY, name TEXT)")
        conn.close()

        from webapp.app import create_app
        app = create_app()
        app.config['TESTING'] = True
        app.config['DATABASE_PATH'] = db_path
        return app

    def test_time_period_in_context(self, app):
        with app.test_request_context('/'):
            from webapp.utils.time_filter import get_active_period
            period = get_active_period()
            assert period["key"] == "2026cycle"

    def test_time_period_from_query_param(self, app):
        with app.test_request_context('/?period=all'):
            from webapp.utils.time_filter import get_active_period
            period = get_active_period()
            assert period["key"] == "all"

    def test_invalid_period_falls_back_to_default(self, app):
        with app.test_request_context('/?period=bogus'):
            from webapp.utils.time_filter import get_active_period
            period = get_active_period()
            assert period["key"] == "2026cycle"

    def test_nav_dropdown_rendered(self, app):
        """The time period dropdown should be present in nav bar."""
        with app.test_request_context('/'):
            from flask import render_template_string
            with app.app_context():
                html = render_template_string(
                    '{% for key, label, desc in time_periods %}'
                    '<option value="{{ key }}">{{ label }}</option>'
                    '{% endfor %}'
                )
                assert '2026 Cycle' in html
                assert 'All Time' in html
