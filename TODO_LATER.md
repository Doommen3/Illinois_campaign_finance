# To-Do Later — Refresh / Loader Follow-Ups

Created: 2026-05-13. Surfaced during the 2026-05-13 prod + local data refresh (see commit/branch context for that day).

## 1. Scrub DB password from prod refresh log

**Where:** `178.156.162.56:/srv/illinois_campaign_finance/shared/refresh_20260513_171705.log`

**Issue:** The line emitted by `cli/commands.py` when invoking the sunshine ETL prints the full DB URL including the `ilcf_app` Postgres password in plaintext:

```
Running: /srv/illinois_campaign_finance/shared/venv/bin/python3 scripts/isbe_sunshine_etl.py --bulk-dir Bulk_download --db-url postgresql://ilcf_app:<password>@127.0.0.1:5432/ilcf
```

Log is root-owned (0640) so blast radius is small, but the password should be redacted.

**Suggested fix:**
- Patch the CLI command (or wherever this `Running:` line is generated) to mask the password before printing: `re.sub(r'(://[^:]+:)[^@]+(@)', r'\1***\2', url)`.
- Redact the existing file in place:
  ```bash
  ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
    "sed -i 's|postgresql://ilcf_app:[^@]*@|postgresql://ilcf_app:***@|g' /srv/illinois_campaign_finance/shared/refresh_*.log"
  ```

## 2. Harden ISBE bulk download (silent curl truncation) — RESOLVED 2026-05-23

Fixed in `scripts/isbe_sunshine_etl.py`:
- `download_file()` sends `User-Agent: Mozilla/5.0` (defeats ISBE 403), retries with backoff, validates Content-Length when present, writes via `.partial` + atomic rename.
- New `verify_row_counts()` runs after download — compares each file's row count against `Bulk_download/.row_counts` (baseline seeded 2026-05-23 from DB counts post-restore) and aborts with `sys.exit(2)` if any file dropped >5% vs prior run.
- Validated end-to-end via local refresh on 2026-05-23 (files came down within ±0.01% of baseline; check passed).

Original (now historical) description:

**Why this matters:** During the 2026-05-13 local refresh, three large ISBE files were silently truncated mid-stream because ISBE's server omits `Content-Length` and `curl -fsS` returns exit 0 on partial transfers.

| File | Local (truncated) | Expected | Drop |
|---|---|---|---|
| FiledDocs.txt | 83,584 rows | 944,798 | −91% |
| Receipts.txt | 5,421,844 | 6,479,711 | −16% |
| Expenditures.txt | 4,424,389 | 4,818,448 | −8% |

FiledDocs's truncation cascaded into 247-of-5.4M receipts inserted (FK rejects), then 54 donor rows in `analytics_donor_summary`. Required a full `pg_dump`/`pg_restore` from prod to recover.

**Where:** The curl loop is documented in `CLAUDE.md` under "ISBE 403 workaround" and is likely the literal pattern used by any production refresh script.

**Suggested fix:** Replace the curl command with retry + timeout + checked-resume:

```bash
curl -fsS --retry 5 --retry-all-errors --connect-timeout 30 --max-time 1800 \
  -A "Mozilla/5.0" -o "Bulk_download/$f" \
  "https://elections.il.gov/campaigndisclosuredatafiles/$f"
```

Add a post-download sanity check: compare each new file's row count against the previous run (e.g., a stored `Bulk_download/.row_counts`) and abort the chain if any file dropped more than ~5% unexpectedly.

## 3. Loader reports false success when FK-rejected batches drop rows — RESOLVED 2026-05-23

Fixed in `scripts/isbe_sunshine_etl.py`:
- `_copy_batch()` and `load_file()` now return `(inserted, rejected)` tuples. Per-file summary line reads `N inserted, M FK-rejected, K row errors`.
- Main flow buckets rejects by rate: <1% per file logs as "stale ISBE cross-reference" noise (normal between filings); ≥1% per file is catastrophic and triggers `sys.exit(3)` before matview creation.
- Validated on 2026-05-23 refresh: caught noise (D2Totals 0.000%, link tables 0.03–0.06%) correctly without false abort; the 2026-05-13 truncation signature (99.99% reject rate) would have tripped the gate.

Original (now historical) description:

**Where:** `scripts/isbe_sunshine_etl.py` (the loader path for `isbe_receipts` and `isbe_expenditures`)

**Issue:** When FK-violating batches fall through to the row-by-row fallback path, the loader logged:

```
5,420,718 rows loaded in 1294.2s (4,188 rows/s) | 0 errors
```

— but only 247 rows actually committed. The "loaded" counter appears to be rows-attempted; batch-fallback rejects are not counted as errors, and `0 errors` masks catastrophic data loss.

**Suggested fix:**
- Track inserted-rows and rejected-rows separately in the batch loop. Emit `N inserted, M rejected by FK, K hard errors`.
- Treat any non-zero `M` as a hard failure (exit non-zero) so chained refreshes fail loudly rather than continuing silently to subsequent steps.

---

## Reminder schedule

`CLAUDE.md` has a `## Pending Reminders` section dated **2026-05-20**. On or after that date, surface this file at the start of the conversation.
