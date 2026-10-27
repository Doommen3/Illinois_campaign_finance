#!/usr/bin/env python3
"""Committee families — group serial re-registrations of the same political actor.

`report` is read-only: it applies the evidence rule and prints the proposed
families (design: docs/committee_families_design.md, suggestions item C8).
`seed` stores every proposed family that is not stored yet as a pending row in
`committee_family` (idempotent on the member set: reviewed families keep their
status and notes); review happens at /admin/committee-families. The evidence
rule and its SQL live in database/committee_families.py, so the two commands
cannot drift.

Evidence rule (a pair of committees is linked when):
  shared candidate AND (same normalized name OR trigram similarity >= 0.8
                         OR shared officer name OR same non-blank street + zip)
Pairs that are >= 0.8 similar and share a zip code but NO candidate are reported
separately as "weak" (review queue only; genuinely distinct committees live here,
e.g. a PAC and its IE PAC).

Usage:
  python3 scripts/committee_families.py report [--limit 30] [--db-url URL]
  python3 scripts/committee_families.py seed [--db-url URL]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from database.committee_families import propose_families, seed_families, weak_pairs  # noqa: E402
from database.pg_compat import PostgresCompatConnection  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "postgresql://devin@localhost/ilcf")

MEMBER_SQL = """
SELECT c.id, c.name, c.active, c.creation_date, c.status_date, c.type,
       m.last_receipt_date
FROM isbe_committees c
LEFT JOIN isbe_committee_money m ON m.committee_id = c.id
WHERE c.id = ANY(?)
ORDER BY c.active DESC, m.last_receipt_date DESC NULLS LAST, c.creation_date DESC NULLS LAST
"""


def format_evidence(evidence: dict) -> str:
    tags = [key for key in ("same_name", "shared_officer", "same_address") if evidence.get(key)]
    if evidence.get("similar_name"):
        tags.append(f"similar_name:{evidence['similar_name']}")
    tags.append("shared_candidates:" + ",".join(str(c) for c in evidence["shared_candidates"]))
    return ", ".join(tags)


def report(conn, limit: int) -> None:
    pairs, families = propose_families(conn)
    weak = weak_pairs(conn)
    by_size = defaultdict(int)
    for family in families:
        by_size[len(family["members"])] += 1

    print("# Committee families — dry run (read-only)\n")
    print(f"Strong pairs (shared candidate + corroboration): {len(pairs)}")
    print(f"  same normalized name: {sum(1 for p in pairs if p['same_name'])}")
    print(f"  similar name >= 0.8 (not identical): {sum(1 for p in pairs if p['similar'] and not p['same_name'])}")
    print(f"  shared officer: {sum(1 for p in pairs if p['shared_officer'])}")
    print(f"  same address: {sum(1 for p in pairs if p['same_address'])}")
    print(f"Proposed families: {len(families)} covering {sum(len(f['members']) for f in families)} committees")
    print("  by size: " + ", ".join(f"{size}:{n}" for size, n in sorted(by_size.items())))
    print(f"Weak pairs (same zip + similar name, no shared candidate — review only): {len(weak)}\n")

    ordered = sorted(families, key=lambda f: (-len(f["members"]), f["members"]))[: max(0, limit)]
    for n, family in enumerate(ordered, 1):
        members = conn.execute(MEMBER_SQL, (family["members"],)).fetchall()
        active = sum(1 for m in members if m["active"])
        print(f"## {n}. {family['label']}  ({len(members)} committees, {active} active)")
        print("evidence: " + format_evidence(family["evidence"]))
        for m in members:
            last = m["last_receipt_date"] or "—"
            print(f"- {m['id']}: {m['name']} · {'active' if m['active'] else 'inactive'} · created {m['creation_date'] or '—'} · last receipt {last}")
        print()

    print("## Weak pairs sample (first 20)")
    for row in weak[:20]:
        members = conn.execute(MEMBER_SQL, ([row["c1"], row["c2"]],)).fetchall()
        names = " | ".join(f"{m['id']} {m['name']}" for m in members)
        print(f"- sim {row['sim']}: {names}")


def seed(conn) -> None:
    if conn.execute("SELECT to_regclass('committee_family') AS t").fetchone()["t"] is None:
        sys.exit("committee_family tables are missing; apply database/schema.sql first (python run.py init-db).")
    started = time.perf_counter()
    result = seed_families(conn)
    conn.commit()
    print(f"Strong pairs: {result['pairs']}")
    print(f"Families created (pending): {result['families_created']}")
    print(f"Families already stored: {result['families_existing']}")
    print(f"Stored families with a member missing from isbe_committees: {result['families_orphaned']}")
    print(f"Runtime: {time.perf_counter() - started:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="print proposed families (read-only)")
    rep.add_argument("--limit", type=int, default=30, help="families to print (largest first)")
    rep.add_argument("--db-url", default=DB_URL)
    sd = sub.add_parser("seed", help="store proposed families as pending rows (idempotent)")
    sd.add_argument("--db-url", default=DB_URL)
    args = parser.parse_args()
    conn = PostgresCompatConnection(args.db_url)
    try:
        if args.command == "report":
            report(conn, args.limit)
        else:
            seed(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
