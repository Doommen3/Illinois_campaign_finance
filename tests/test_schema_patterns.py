"""Guards for schema.sql index statements (plan S-S1a, item B2)."""
import re
from pathlib import Path

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "database" / "schema.sql"

AGG_COMMITTEE_ID_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_analytics_donor_committee_agg_source_committee "
    "ON analytics_donor_committee_agg (source, committee_id);"
)


def _executable_sql() -> str:
    """schema.sql with /* block */ and -- line comments removed."""
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", text)


def test_donor_committee_agg_is_indexed_on_source_and_committee_id():
    # _bulk_committee_profile reads this 1.18M-row table by (source, committee_id).
    # The existing idx_analytics_donor_committee_committee is on (source,
    # committee_name), so it does not serve that lookup. A commented-out
    # statement must not satisfy this guard.
    assert AGG_COMMITTEE_ID_INDEX in _executable_sql()
