"""Part-9 attribution coverage: how much of the D-2 Part 9 money the outside-spending matcher attributes (read-only).

Search Phase 2, decision P2-3 (.claude/plans/outside-spending-drilldown.md). Every non-archived Part-9 row of
isbe_condensed_expenditures is matched against every isbe_candidates record that has a candidacy, with the repo's own
matcher (database/outside_spending.py: candidate_name_key + attribute_part9_rows, cycle-gated as the candidate pages are).
A row counts as attributed when at least one candidate takes it; its dollars are the full row amount ("dollars") and,
separately, the shares of the names that matched ("share dollars"). --by-nickname lists the attributions whose filed first
token differs from the record's (the nickname rule, M2), per (filed, record) pair.

    DATABASE_URL=postgresql://devin@localhost/ilcf python3 scripts/audit/part9_coverage.py [--by-nickname]

Opens the session READ ONLY; writes nothing. Run it from the checkout whose matcher is to be measured.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.connection import get_db  # noqa: E402
from database.outside_spending import attribute_part9_rows, candidate_name_key  # noqa: E402

ROWS_SQL = """
    SELECT id, candidate_name, amount, expended_date
    FROM isbe_condensed_expenditures
    WHERE d2_part like '9%' AND archived = FALSE
"""
CANDIDATES_SQL = """
    SELECT c.id, c.first_name, c.last_name, ARRAY_AGG(DISTINCT y.election_year) AS years
    FROM isbe_candidates c
    JOIN isbe_candidacies y ON y.candidate_id = c.id
    WHERE y.election_year IS NOT NULL
    GROUP BY c.id, c.first_name, c.last_name
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--by-nickname", action="store_true", help="list the attributions made through a nickname")
    args = parser.parse_args()
    if not (os.environ.get("DATABASE_URL") or "").startswith("postgresql"):
        # get_db() would otherwise fall back to the deprecated SQLite path and create data/campaign_finance.db
        sys.exit("Set DATABASE_URL to the PostgreSQL database to measure, e.g. postgresql://devin@localhost/ilcf")

    conn = get_db()
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    conn.commit()  # the setting governs the transactions that start after this one
    rows =[dict(row) for row in conn.execute(ROWS_SQL).fetchall()]
    candidates = []
    for record in conn.execute(CANDIDATES_SQL).fetchall():
        key = candidate_name_key(f"{record['first_name'] or ''} {record['last_name'] or ''}")
        if key is not None:
            cycles = {year + year % 2 for year in record["years"]}
            candidates.append({"key": key, "cycles": cycles, "candidate_id": record["id"]})
    conn.close()

    attributions = attribute_part9_rows(rows, candidates)
    attributed_ids = {item["id"] for item in attributions}
    shares = {}
    for item in attributions:  # one share per (row, matched name), however many twin records took it
        shares[(item["id"], item["name_as_filed"])] = item["share"]

    total = sum(float(row["amount"] or 0) for row in rows)
    attributed = sum(float(row["amount"] or 0) for row in rows if row["id"] in attributed_ids)
    names = {row["candidate_name"] for row in rows}
    attributed_names = {row["candidate_name"] for row in rows if row["id"] in attributed_ids}
    print(f"Part-9 rows            {len(attributed_ids):>8,} of {len(rows):,} attributed")
    print(f"filed name strings     {len(attributed_names):>8,} of {len(names):,}")
    print(f"dollars (row amount)   {attributed:>14,.2f} of {total:,.2f} = {100 * attributed / total:.1f} %")
    print(f"share dollars          {sum(shares.values()):>14,.2f} = {100 * sum(shares.values()) / total:.1f} %")

    if args.by_nickname:
        pairs: dict[tuple[str, str], dict] = defaultdict(lambda: {"rows": set(), "dollars": 0.0})
        for item in attributions:
            filed = candidate_name_key(item["name_as_filed"])
            if filed is not None and filed[0] != item["candidate"]["key"][0]:
                bucket = pairs[(filed[0], item["candidate"]["key"][0])]
                bucket["rows"].add(item["id"])
                bucket["dollars"] += item["share"]
        print("\nnickname attributions (filed first token -> record first token): rows, share dollars")
        for (filed, record), bucket in sorted(pairs.items(), key=lambda pair: -pair[1]["dollars"]):
            print(f"  {filed:>12} -> {record:<12} {len(bucket['rows']):>6,} {bucket['dollars']:>14,.2f}")
        if not pairs:
            print("  (none)")


if __name__ == "__main__":
    main()
