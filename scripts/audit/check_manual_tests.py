#!/usr/bin/env python3
"""Check the manual test register (docs/manual_tests.md).

Structure: every ``### MT-NNN`` section carries the required fields, ids are
unique, and every id appears in the index table. With ``--host`` every site
path quoted in backticks (`/candidates/13505`) is fetched once (GET only) and
must answer 200 after redirects; paths holding ``<``/``>`` placeholders are
skipped.

Usage:
    python3 scripts/audit/check_manual_tests.py
    python3 scripts/audit/check_manual_tests.py --host http://127.0.0.1:5057

Exit status 1 on any failure.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from urllib import error, request

REQUIRED = ("**Area:**", "**Created:**", "**Source commit:**", "**Precondition:**", "**Steps:**", "**Pass:**", "**Fail:**")
SECTION_RE = re.compile(r"^### (MT-\d{3})\b.*$", re.M)
INDEX_RE = re.compile(r"^\| (MT-\d{3}) \|", re.M)
PATH_RE = re.compile(r"`(/[^`\s]*)`")
DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "manual_tests.md")


def fetch(url: str) -> object:
    try:
        with request.urlopen(request.Request(url, method="GET"), timeout=120) as resp:
            return resp.status
    except error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # connection refused, timeout
        return exc.__class__.__name__


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--host", help="base URL of a running site; every `/path` in the document is fetched")
    args = ap.parse_args()

    text = open(args.file, encoding="utf-8").read()
    parts = SECTION_RE.split(text)
    ids, bodies = parts[1::2], parts[2::2]
    failures: list[str] = []
    if not ids:
        failures.append("no '### MT-NNN' sections found")
    for test_id, body in zip(ids, bodies):
        missing = [k for k in REQUIRED if k not in body]
        if missing:
            failures.append(f"{test_id}: missing {', '.join(missing)}")
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        failures.append(f"duplicate ids: {', '.join(dups)}")
    indexed = set(INDEX_RE.findall(text))
    for test_id in ids:
        if test_id not in indexed:
            failures.append(f"{test_id}: not in the index table")

    swept = 0
    if args.host:
        paths = sorted({p for p in PATH_RE.findall(text) if "<" not in p and ">" not in p})
        for path in paths:
            code = fetch(args.host.rstrip("/") + path)
            swept += 1
            print(f"{code} {path}")
            if code != 200:
                failures.append(f"{code} {path}")

    print(f"{len(ids)} tests, {len(set(ids))} unique ids" + (f", {swept} paths swept" if args.host else ""))
    if failures:
        print("FAIL")
        for item in failures:
            print(" -", item)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
