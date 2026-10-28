"""refresh-analytics leaves planner statistics behind for the rebuilt aggregates (load-time plan L12)."""
import webapp.cache_backend as cache_backend
from database import analytics

REBUILT = {
    "analytics_donor_committee_agg",
    "analytics_donor_summary",
    "analytics_committee_monthly_totals",
    "analytics_large_contributions",
}


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    def execute(self, sql, params=None):
        self.calls.append(("execute", " ".join(sql.split())))

    def commit(self):
        self.calls.append(("commit", None))


def test_refresh_analytics_materialized_analyzes_the_rebuilt_tables_before_the_flush(monkeypatch):
    calls = []
    monkeypatch.setattr(analytics, "_donor_flow_source", lambda conn: "contributions")
    monkeypatch.setattr(analytics, "_refresh_materialized_contributions", lambda conn: {"rows": 1})
    monkeypatch.setattr(cache_backend, "flush_all_route_caches", lambda: calls.append(("flush", None)))

    assert analytics.refresh_analytics_materialized(_Conn(calls)) == {"rows": 1}

    analyzes = [sql for kind, sql in calls if kind == "execute" and sql.startswith("ANALYZE")]
    assert len(analyzes) == 1, calls
    assert all(name in analyzes[0] for name in REBUILT), analyzes[0]
    kinds = [kind for kind, _ in calls]
    analyze_at = kinds.index("execute")
    assert kinds[analyze_at + 1] == "commit", kinds
    assert analyze_at < kinds.index("flush"), kinds
