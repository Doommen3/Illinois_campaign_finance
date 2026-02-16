"""Tests for the global time period filter."""

import pytest
from datetime import date, timedelta

from webapp.utils.time_filter import (
    TIME_PERIODS,
    DEFAULT_PERIOD,
    get_period_dates,
    period_qmark_clause,
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
        assert "5y" in keys
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
