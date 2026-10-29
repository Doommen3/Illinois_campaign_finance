#!/usr/bin/env python3
"""Run-record entry points for the refresh shell scripts (database/data_refresh.py).

  start --kind nightly|manual --log PATH   open a run, print its id     (scripts/nightly_refresh.sh)
  finish RUN_ID EXIT_CODE                  close it with the after snapshot and the diff
  count DATASET                            print the row count of the dataset's main table
  record-manual DATASET FILE [--file-date YYYY-MM-DD] [--rows-before N] [--notes TEXT]
                                           record a hand load in data_manual_loads (scripts/manual_load.sh);
                                           rows_loaded = current count - N; the file date defaults to
                                           the first YYYYMMDD in the file name

Kept out of cli/commands.py on purpose (plan .claude/plans/scheduled-refresh.md D3).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from database.connection import get_db  # noqa: E402
from database.data_refresh import DATASETS, finish_run, record_manual_load, start_run  # noqa: E402

_BY_KEY = {d.key: d for d in DATASETS}


def _count(conn, dataset_key: str) -> int:
    # The table name comes from DATASETS, never from the argument itself.
    return int(conn.execute(f"SELECT COUNT(*) FROM {_BY_KEY[dataset_key].table}").fetchone()[0])


def _date_from_name(name: str) -> date | None:
    m = re.search(r"(\d{8})", Path(name).name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh run record")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start")
    p.add_argument("--kind", choices=["nightly", "manual"], default="nightly")
    p.add_argument("--log")
    p = sub.add_parser("finish")
    p.add_argument("run_id", type=int)
    p.add_argument("exit_code", type=int)
    p = sub.add_parser("count")
    p.add_argument("dataset", choices=sorted(_BY_KEY))
    p = sub.add_parser("record-manual")
    p.add_argument("dataset", choices=sorted(_BY_KEY))
    p.add_argument("file")
    p.add_argument("--file-date", type=date.fromisoformat)
    p.add_argument("--rows-before", type=int)
    p.add_argument("--notes")
    args = parser.parse_args(argv)

    conn = get_db()
    try:
        if args.command == "start":
            print(start_run(conn, kind=args.kind, log_path=args.log))
        elif args.command == "finish":
            finish_run(conn, args.run_id, args.exit_code)
        elif args.command == "count":
            print(_count(conn, args.dataset))
        else:
            rows = _count(conn, args.dataset) - args.rows_before if args.rows_before is not None else None
            load_id = record_manual_load(
                conn, args.dataset, args.file,
                file_date=args.file_date or _date_from_name(args.file), rows_loaded=rows, notes=args.notes,
            )
            print(f"recorded manual load {load_id}: {args.dataset} {args.file} rows_loaded={rows}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
