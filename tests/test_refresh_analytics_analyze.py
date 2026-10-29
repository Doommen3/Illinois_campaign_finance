"""refresh-analytics leaves planner statistics behind for the rebuilt aggregates (load-time plan L12), and ends by
rebuilding the merged local donor entities behind the all-time /donors/ directory (fix-8b F6)."""
import logging

import pytest

import webapp.cache_backend as cache_backend
from database import analytics
from database.connection import get_db
from tests.test_donor_cycle_summary import NORA, SOURCE, Who, _build, receipt

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

    def rollback(self):
        self.calls.append(("rollback", None))


def test_refresh_analytics_materialized_analyzes_the_rebuilt_tables_before_the_flush(monkeypatch):
    calls = []
    monkeypatch.setattr(analytics, "_donor_flow_source", lambda conn: "contributions")
    monkeypatch.setattr(analytics, "_refresh_materialized_contributions", lambda conn: {"rows": 1})
    monkeypatch.setattr(cache_backend, "flush_all_route_caches", lambda: calls.append(("flush", None)))
    monkeypatch.setattr(analytics, "_table_exists", lambda conn, name: False)  # the F6 rebuild step is skipped here

    assert analytics.refresh_analytics_materialized(_Conn(calls))["rows"] == 1

    analyzes = [sql for kind, sql in calls if kind == "execute" and sql.startswith("ANALYZE")]
    assert len(analyzes) == 1, calls
    assert all(name in analyzes[0] for name in REBUILT), analyzes[0]
    kinds = [kind for kind, _ in calls]
    analyze_at = kinds.index("execute")
    assert kinds[analyze_at + 1] == "commit", kinds
    assert analyze_at < kinds.index("flush"), kinds


# --- fix-8b F6: donor_entity_local is rebuilt by every refresh -------------------------------------------------------

# The arguments `run.py rebuild-local-donor-entities` passes by default (cli/commands.py, its click option defaults).
CLI_DEFAULTS = {"source": "bulk_receipts", "medium_threshold": 0.70, "high_threshold": 0.82, "auto_threshold": 0.90,
                "max_group_size": 400, "dry_run": False, "preview_limit": 25}


def _orchestrator(monkeypatch, calls, rebuild, tables=True):
    monkeypatch.setattr(analytics, "_donor_flow_source", lambda conn: "contributions")
    monkeypatch.setattr(analytics, "_refresh_materialized_contributions",
                        lambda conn: calls.append(("aggregates", None)) or {"rows": 1})
    monkeypatch.setattr(cache_backend, "flush_all_route_caches", lambda: calls.append(("flush", None)))
    monkeypatch.setattr(analytics, "_table_exists", lambda conn, name: tables)
    monkeypatch.setattr(analytics, "rebuild_local_donor_entities", rebuild)


def test_refresh_ends_by_rebuilding_local_donor_entities_with_the_cli_defaults(monkeypatch):
    """The rebuild runs once, with the CLI's default arguments, after the aggregates and their ANALYZE are committed
    and before the route-cache flush (the all-time /donors/ directory reads donor_entity_local, so a flush before the
    rebuild would let requests re-cache the old directory).  Rows written and elapsed seconds reach the stats the CLI
    prints.  Kills: no rebuild (the 2026-02-13 directory of finding E); other thresholds or source than the CLI's; the
    rebuild before the aggregate commit; the flush before the rebuild; the counts not reported."""
    calls = []

    def rebuild(conn, **kwargs):
        calls.append(("rebuild", kwargs))
        return {"entity_rows_written": 3, "member_rows_written": 5, "top_review_entities": [{"canonical_name": "x"}]}

    _orchestrator(monkeypatch, calls, rebuild)
    stats = analytics.refresh_analytics_materialized(_Conn(calls))

    rebuilds = [args for kind, args in calls if kind == "rebuild"]
    assert rebuilds == [CLI_DEFAULTS]
    kinds = [kind for kind, _ in calls]
    last_commit = max(i for i, kind in enumerate(kinds) if kind == "commit")
    assert kinds.index("aggregates") < last_commit < kinds.index("rebuild") < kinds.index("flush"), kinds
    step = stats["local_donor_entities"]
    assert (step["entity_rows_written"], step["member_rows_written"]) == (3, 5)
    assert isinstance(step["elapsed_seconds"], float) and step["elapsed_seconds"] >= 0
    assert stats["rows"] == 1


def test_a_failing_rebuild_is_logged_re_raised_and_leaves_the_committed_aggregates(monkeypatch, caplog):
    """The aggregates are committed before the rebuild starts and the orchestrator does not roll back; the route caches
    are still flushed (they describe the old aggregates); the error is logged and re-raised.  Kills: the rebuild inside
    the aggregate transaction; a swallowed error (refresh-analytics would report success); a rollback; no flush."""
    calls = []

    def rebuild(conn, **kwargs):
        calls.append(("rebuild", kwargs))
        raise RuntimeError("entity rebuild failed")

    _orchestrator(monkeypatch, calls, rebuild)
    with caplog.at_level(logging.ERROR, logger="database.analytics"), pytest.raises(RuntimeError, match="entity rebuild"):
        analytics.refresh_analytics_materialized(_Conn(calls))

    kinds = [kind for kind, _ in calls]
    assert "commit" in kinds[:kinds.index("rebuild")], kinds
    assert "rollback" not in kinds and "flush" in kinds, kinds
    assert any("local donor entit" in record.getMessage().lower() for record in caplog.records), caplog.records


def test_the_rebuild_is_skipped_when_its_tables_are_missing(monkeypatch):
    """Kills: a refresh that crashes on a database without the donor entity tables."""
    calls = []
    _orchestrator(monkeypatch, calls, lambda conn, **kwargs: calls.append(("rebuild", kwargs)), tables=False)

    stats = analytics.refresh_analytics_materialized(_Conn(calls))

    assert "rebuild" not in [kind for kind, _ in calls]
    assert stats["local_donor_entities"]["status"] == "skipped"


GAIL_HOME = Who("Gail", "Twin", "1 First St", "Chicago", "IL", "60601", "Nurse", "Rush")
GAIL_WORK = Who("Gail", "Twin", "2 Second St", "Chicago", "IL", "60601", "Nurse", "Rush")


def test_a_real_refresh_fills_donor_entity_local_from_the_new_summary(tmp_path):
    """On a real schema (the donor cycle summary world, ISBE-direct branch) plus one person filed under two addresses,
    the refresh writes member rows for both of her keys (the rebuild groups a name held by two or more keys), every
    member is an analytics_donor_summary donor, and the stats report the rows written.  Kills: the step wired to a
    stub, to another source, or run before the summary is rebuilt."""
    env = _build(tmp_path, "isbe")
    env.add([receipt(30301, GAIL_HOME, 100, "2025-05-01", 100.0), receipt(30302, GAIL_WORK, 200, "2025-05-02", 200.0)])
    stats = env.refresh()

    conn = env.conn()
    summary = {r["donor_key"] for r in conn.execute(
        "SELECT donor_key FROM analytics_donor_summary WHERE source = ?", (SOURCE,)).fetchall()}
    members = {r["donor_key"] for r in conn.execute(
        "SELECT donor_key FROM donor_entity_local_member WHERE source = ?", (SOURCE,)).fetchall()}
    assert {GAIL_HOME.key, GAIL_WORK.key} <= members <= summary
    assert stats["local_donor_entities"]["member_rows_written"] == len(members)


def test_a_real_failing_rebuild_does_not_undo_the_aggregates(tmp_path, monkeypatch):
    """The same world with a rebuild that raises: a fresh connection still sees the rebuilt aggregates."""
    def rebuild(conn, **kwargs):
        raise RuntimeError("entity rebuild failed")

    monkeypatch.setattr(analytics, "rebuild_local_donor_entities", rebuild)
    with pytest.raises(RuntimeError, match="entity rebuild"):
        _build(tmp_path, "isbe")

    fresh = get_db(str(tmp_path / "donor_cycle.db"))
    keys = {r["donor_key"] for r in fresh.execute(
        "SELECT donor_key FROM analytics_donor_summary WHERE source = ?", (SOURCE,)).fetchall()}
    assert NORA.key in keys
