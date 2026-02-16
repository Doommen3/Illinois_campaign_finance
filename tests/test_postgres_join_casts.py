"""Guards for Postgres-safe committee ID joins across text/int tables."""
from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_analytics_joins_cast_committee_id_sbe_to_text():
    source = Path("database/analytics.py").read_text(encoding="utf-8")
    assert "ON CAST(l.committee_id_sbe AS TEXT) = a.committee_id" in source


def test_lobbying_route_casts_committee_join_key():
    source = Path("webapp/routes/lobbying.py").read_text(encoding="utf-8")
    assert "ON CAST(l.committee_id_sbe AS TEXT) = a.committee_id" in source
