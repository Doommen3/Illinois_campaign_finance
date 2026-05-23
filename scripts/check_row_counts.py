"""Post-download row-count sanity check for the ISBE bulk refresh.

Reads `Bulk_download/.row_counts_prev` (written by the orchestrator before
the curl loop runs) and compares each of the 12 canonical ISBE files'
current row count against the prior count. Exits non-zero if any file
dropped more than 5% — the silent-truncation signature from the 2026-05-13
incident where ISBE returned a partial FiledDocs.txt with no HTTP error.

Called by `scripts/refresh_prod_chain.sh` at step 3. Also safe to run
standalone after a manual curl loop.

Exit codes:
  0 — all files within 5% of prior count (or no prior baseline)
  1 — at least one file dropped more than 5%, or a required file is missing
"""
from pathlib import Path
import sys
prev = {}
prev_path = Path("Bulk_download/.row_counts_prev")
if prev_path.exists():
    for line in prev_path.read_text().splitlines():
        if "\t" in line:
            name, n = line.split("\t", 1)
            try:
                prev[name] = int(n)
            except Exception:
                pass
fail = False
files = ["Candidates.txt","CanElections.txt","Committees.txt","Officers.txt",
        "PrevOfficers.txt","D2Totals.txt","Receipts.txt","Expenditures.txt",
        "Investments.txt","FiledDocs.txt","CmteCandidateLinks.txt","CmteOfficerLinks.txt"]
for f in files:
    p = Path("Bulk_download")/f
    if not p.exists():
        print(f"MISSING: {f}"); fail = True; continue
    cur = sum(1 for _ in p.open("rb"))
    p0 = prev.get(f, 0)
    if p0 > 0 and cur/p0 < 0.95:
        drop = (1 - cur/p0)*100
        print(f"FAIL  {f}: {cur:,} now vs {p0:,} prev (-{drop:.1f}%)"); fail = True
    else:
        delta = f"+{cur-p0:,}" if p0 else "(baseline)"
        print(f"OK    {f}: {cur:,} {delta}")
sys.exit(1 if fail else 0)
