"""latest_data_date() reads the cached global data status and tolerates every value shape."""
from datetime import date, datetime
from pathlib import Path

import pytest

from database.connection import init_db
from webapp.app import create_app
from webapp.utils.data_through import latest_data_date


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "dt.db")
    init_db(db_path)
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


@pytest.mark.parametrize("value, expected", [
    ("2026-10-21", date(2026, 10, 21)),
    ("2026-10-21 03:00:02.4", date(2026, 10, 21)),
    (date(2026, 7, 1), date(2026, 7, 1)),
    (datetime(2026, 7, 1, 12, 30), date(2026, 7, 1)),
    ("not a date", None),
    (None, None),
])
def test_latest_data_date_parses_every_shape(app, value, expected):
    app.get_global_data_status = lambda: {"local_receipt_date": value, "federal_receipt_date": "2026-07-01"}
    with app.app_context():
        assert latest_data_date("isbe") == expected
        assert latest_data_date("fec") == date(2026, 7, 1)
        assert latest_data_date("unknown-source") is None


def test_latest_data_date_without_status(app):
    app.get_global_data_status = lambda: None
    with app.app_context():
        assert latest_data_date("isbe") is None


def test_real_status_on_empty_db_returns_none_not_error(app):
    with app.app_context():
        assert latest_data_date("isbe") is None


@pytest.mark.parametrize("value, expected", [
    (datetime(2026, 10, 21, 3, 0, 2), date(2026, 10, 21)),
    (date(2026, 10, 21), date(2026, 10, 21)),
    ("2026-10-21", date(2026, 10, 21)),
    ("2026-10-21 03:00:02", date(2026, 10, 21)),
    ("", None),
    (None, None),
])
def test_app_parse_date_accepts_db_native_values(value, expected):
    """Regression: psycopg hands back datetime objects; _parse_date used to call .strip() on them,
    raising inside the data-status builder and blanking the snapshot."""
    from webapp.app import _age_days, _parse_date
    assert _parse_date(value) == expected
    assert (_age_days(value) is None) == (expected is None)
