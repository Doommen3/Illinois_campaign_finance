"""Regression checks for perf-critical schema indexes."""
from pathlib import Path


def test_schema_has_irs527_perf_indexes():
    source = Path("database/schema.sql").read_text(encoding="utf-8")
    assert "CREATE INDEX IF NOT EXISTS idx_irs527_contributions_name_amount" in source
    assert "CREATE INDEX IF NOT EXISTS idx_irs527_contributions_date_amount" in source
    assert "CREATE INDEX IF NOT EXISTS idx_irs527_expenditures_date_amount" in source
