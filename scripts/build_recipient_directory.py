#!/usr/bin/env python3
"""Build (or rebuild) the /search recipient directory: isbe_recipient_summary and isbe_recipient_committees.

The ISBE ETL rebuilds it on every sunshine-import; run this once after deploying the feature (or any time the directory
is missing) instead of waiting for the next import. Reads isbe_condensed_expenditures; additive, a few seconds.

Usage:
  python3 scripts/build_recipient_directory.py
"""
import os
import sys
import time
from pathlib import Path

import psycopg

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from database.recipient_directory import build_recipient_directory  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "postgresql://devin@localhost/ilcf")


def main() -> None:
    started = time.time()
    with psycopg.connect(DB_URL) as conn:
        counts = build_recipient_directory(conn)
        conn.commit()
    print(f"Recipient directory built in {time.time() - started:.1f}s: {counts['recipients']:,} recipients, "
          f"{counts['recipient_committees']:,} recipient x committee rows.")


if __name__ == "__main__":
    main()
