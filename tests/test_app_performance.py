"""Performance-focused tests for app request-path caching behavior."""
from pathlib import Path

from database.connection import init_db
from webapp.app import create_app
import webapp.app as app_module


def test_global_data_status_cached_across_requests(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "perf_cache.db")
    init_db(db_path)

    call_count = {"value": 0}

    def fake_status(conn, *, local_stale_days, federal_stale_days):
        call_count["value"] += 1
        return {
            "local_receipt_date": None,
            "federal_receipt_date": None,
            "federal_disbursement_date": None,
            "federal_independent_expenditure_date": None,
            "federal_sync_updated_at": None,
            "federal_latest_coverage_date": None,
            "local_age_days": None,
            "federal_age_days": None,
            "federal_receipt_age_days": None,
            "federal_disbursement_age_days": None,
            "federal_independent_expenditure_age_days": None,
            "local_is_stale": False,
            "federal_is_stale": False,
            "is_any_stale": False,
            "has_any_data": True,
        }

    monkeypatch.setattr(app_module, "_build_global_data_status", fake_status)

    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": 60,
        }
    )

    client = app.test_client()
    first = client.get("/")
    second = client.get("/search?q=test")

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count["value"] == 1
