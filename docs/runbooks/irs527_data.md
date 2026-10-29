# IRS 527 data — status, refresh procedure, cadence and integration

To-do item 5 (2026-10-29). Answers: is the 527 data updated automatically; how to download it, where to put it
and how to import it (locally and on prod); how often; and whether / how it should be joined to the state and
federal analytics. Evidence for every claim is in the Appendix (probes and queries, dated, UTC).

Written 2026-10-29: sections 1–6 (M1, 06:49–06:56Z), sections 7–8 (M2, 07:00Z on). Read-only research; nothing
in §8 is implemented.

## 1. Status today — is it automated?

**No.** The 527 tables change only when someone runs `import-irs527` (and the two `repair-irs527-*` commands) by
hand on a FullDataFile they downloaded by hand. Three pieces of evidence:

1. **No chain step.** The prod refresh chain `scripts/refresh_prod_chain.sh` runs snapshot → ISBE download →
   sanity check → `sunshine-import` → `sync-fec-il-federal` → `run-cross-matching --only all` →
   `refresh-analytics` → cache warm → restart (STEP1–STEP8); the string `527` does not occur in it (0 hits).
   CLAUDE.md "Data Refresh Schedule" lists `import-irs527` under **Monthly … as needed** (CLAUDE.md:324), i.e. manual.
   (`run-cross-matching --only all` in the chain does re-run the 527 *match* jobs, but over whatever 527 rows are
   already loaded.)
2. **No scheduler.** The prod timer inventory (`docs/runbooks/deployment.md` §"Scheduled jobs on prod", l.176–183)
   lists only `il-campaign-fec-sync.timer` and `il-campaign-fec-schedule-e-catchup.timer`; nothing IRS. No repo
   shell script (`scripts/*.sh`) invokes an `irs527` command (the only `527` hits are two smoke-test URLs in
   `scripts/deploy.sh:130-131`).
3. **No freshness spec.** `validate-freshness` (`database/data_freshness.py:50-55`, `DEFAULT_SPECS`) checks
   `isbe_filed_docs` and FEC Schedules A/B/E only; a stale 527 load passes every gate. No `webapp/utils` module or
   `_partials` template references an `irs527` table, so the Data Status box does not show 527 freshness either.

**What is loaded locally (2026-10-29 06:51Z, read-only):**

| Table | Rows | Load timestamp (`created_at` / `updated_at`) | Newest data |
|---|---|---|---|
| `irs527_organizations` | 2,343 (1,896 distinct EINs) | 2026-02-13 19:03:09 | `material_change_date` unusable — holds state codes (see §6, D2) |
| `irs527_reports` | 1,739 | 2026-02-14 09:03:37 | `MAX(period_end)` = `20251231`; newest `insert_datetime` 2026-02-01 20:01:28 |
| `irs527_contributions` | 8,974,461 (656 EINs) | 2026-02-15 00:32:41–00:35:55 | no usable date (A11, §6) |
| `irs527_expenditures` | 35,914 | 2026-02-13 19:02:30–19:03:09 | `MAX(date)` = `20251231` |
| `irs527_directors` / `irs527_related_orgs` / `irs527_election_authority` | 5,613 / 796 / 17,305 | (have `created_at`; not queried) | — |
| `irs527_contribution_rollup` / `irs527_contributor_rollup` | 656 / 1,052,915 | 2026-02-16 07:31 / 2026-02-16 01:28 | — |

**When and from what it was last loaded.** The file on disk is the main checkout's
`Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` — 2,769,000,376 bytes, mtime
2026-02-08 03:38 (local time; `unzip` preserves the IRS build time), extracted into `IRS_data/` on 2026-02-13. The
timestamps read as: `import-irs527` on 2026-02-13 (orgs, expenditures), `repair-irs527-reports` on 2026-02-14,
`repair-irs527-contributions` on 2026-02-15, rollups rebuilt 2026-02-16. That ordering is an inference from
`created_at` values; no import-run or meta table records 527 loads (the schema has run tables only for OpenBook and
the Comptroller scraper), and git history was not consulted in this task, so the exact commands and flags used
cannot be established. The data therefore stops at **2025-12-31** (latest report period) — about ten months stale
on 2026-10-29. Prod is almost certainly the same vintage: the public `/527/813554739` page (National Democratic
Redistricting Committee) shows the identical figures to local — "Reports Filed: 42", "Total Contributions:
$60,789,551.00", "Period: 2016-08-15 to 2025-12-31" (public GET 2026-10-29 06:52:54Z); prod tables were not queried.

## 2. Where the data comes from

- **Landing page (IRS):** https://www.irs.gov/charities-non-profits/political-organizations/political-organization-filing-and-disclosure
  links to the POFD download page. (The URL in the task brief,
  `…/political-organizations/political-organization-disclosures-data-download`, returns **404** — it does not exist.)
- **Download page:** https://forms.irs.gov/app/pod/dataDownload/dataDownload (200, text/html). Its text, verbatim
  (2026-10-29 06:50:55Z): "you may download the entire database of electronically submitted Forms 8871 and Forms
  8872 as zipped text files. Please note that the downloadable text files only include information concerning
  electronic submissions." … "Select this file for all electronically filed form data. **This file is updated every
  Saturday at 3:00AM.** It is a very large file …". The same page offers the data-layout file and four alphabetical
  split files (A–G, H–M, N–R, S–Z), "also updated every Saturday at 3:00AM".
- **File URL:** https://forms.irs.gov/app/pod/dataDownload/fullData → `200`, `content-type: application/zip`,
  `content-disposition: attachment; filename=PolOrgsFullData.zip`, **no `content-length`** (streamed), no
  `last-modified`, no redirect on GET. Split files: `/app/pod/dataDownload/dataAG`, `dataHM`, `dataNR`, `dataSZ`.
  Layout: `/app/pod/dataDownload/dataLayout` (a copy from 2015 sits on disk as `Bulk_download/IRS_data/PolOrgsFileLayout.doc`).
- **Zip contents (current file, read from its local-file header 2026-10-29):** one entry,
  `var/IRS/data/scripts/pofd/download/FullDataFile.txt` — the same internal path the repo documents
  (`docs/CODEBOOK.md` §3.4, CLAUDE.md "Common Operations"), deflate, 365,832,912 bytes compressed,
  **2,986,796,580 bytes uncompressed**, entry timestamp **2026-10-24 08:14** (a Saturday, consistent with the weekly
  rebuild). The repo's path is the zip's internal layout, not a convention of ours.
- **Format:** pipe-delimited, one record type per line: `H` header, `1` Form 8871 (registration), `D` directors,
  `R` related entities, `E` election-authority IDs, `2` Form 8872 (periodic report), `A` Schedule A
  (contributions received), `B` Schedule B (expenditures), `F` footer (layout doc).
- **Coverage caveat (IRS):** electronic filings only. Paper-filed 8871/8872s are not in the file.

## 3. Download, placement, import, prod upload

The ISBE files live in `Bulk_download/` of the **main checkout** (`/Users/devin/Illinois_campaign_finance`);
`Bulk_download/` is not in git, so worktrees have none. Run everything below from the main checkout.

**Placement convention.** On disk today: the zip was extracted in place, giving
`Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` (no date folder, so a new download
would overwrite the old). **Proposed** (not yet on disk): one dated folder per pull, keeping the zip —
`Bulk_download/IRS_data/<YYYYMMDD>/PolOrgsFullData.zip` and its extracted
`Bulk_download/IRS_data/<YYYYMMDD>/var/IRS/data/scripts/pofd/download/FullDataFile.txt`. Delete old extractions
once a load is verified (each is ~3 GB; the zip is ~0.37 GB).

**Step 1 — download (≈20 s for the 366 MB zip on the 2026-10-29 connection).**

```bash
cd /Users/devin/Illinois_campaign_finance && D=Bulk_download/IRS_data/$(date +%Y%m%d) && mkdir -p "$D" && curl -fL --retry 5 --retry-all-errors --connect-timeout 30 --max-time 1800 -o "$D/PolOrgsFullData.zip.partial" https://forms.irs.gov/app/pod/dataDownload/fullData && mv "$D/PolOrgsFullData.zip.partial" "$D/PolOrgsFullData.zip" && unzip -o "$D/PolOrgsFullData.zip" -d "$D"
```

Check: `unzip -l "$D/PolOrgsFullData.zip"` lists one entry `var/IRS/data/scripts/pofd/download/FullDataFile.txt`
of ~3.0 GB. Do not use `curl -I` to test the URL — HEAD is answered with a redirect to the IRS 404 page (§5).

**Step 2 — import. Read the re-load hazard first.** Flags below are exactly those printed by `--help` (Appendix B).

- `import-irs527 --file <FullDataFile.txt>` with `--illinois-only / --all-states` (default `illinois-only`: "Only
  load orgs with IL addresses or IL expenditures"). It loads orgs and reports with `INSERT OR REPLACE` (translated to
  `ON CONFLICT DO UPDATE` by `database/pg_compat.py:139-145`) but **plain `INSERT`s** directors, related orgs,
  contributions, expenditures and election authorities (`database/irs527_loader.py:470-540`), and
  `load_irs527_full_file` (l.349-667) contains no `DELETE`/`TRUNCATE`. **Re-running it over a loaded database
  appends a second copy of those five tables.** It also does not rebuild the two rollup tables.
- `repair-irs527-reports --file <…>` with `--illinois-only / --all-states` and `--replace-existing / --append`
  (default `replace-existing`: "Delete current irs527_reports rows before rebuild"). Rebuilds `irs527_reports`
  "totals/ein mapping from FullDataFile type-2 records".
- `repair-irs527-contributions --file <…>` with the same two flag pairs (default `replace-existing`: "Delete current
  irs527_contributions rows before rebuild"). Deletes and reloads `irs527_contributions` from type-A records and
  rebuilds `irs527_contribution_rollup` and `irs527_contributor_rollup` (`irs527_loader.py:811-814`, 867-894).

Safe sequence for a **refresh** with today's code (no code change; `F=$D/var/IRS/data/scripts/pofd/download/FullDataFile.txt`):

1. Empty the five append-only tables first, or they double. There is no CLI for this; it is a manual SQL step
   (Devin's decision — destructive, not run or tested in this task):
   `psql ilcf -c "TRUNCATE irs527_directors, irs527_related_orgs, irs527_expenditures, irs527_election_authority, irs527_contributions;"`
2. `python3 run.py import-irs527 --file "$F" --illinois-only`
3. `python3 run.py repair-irs527-reports --file "$F" --illinois-only --replace-existing`
4. `python3 run.py repair-irs527-contributions --file "$F" --illinois-only --replace-existing` — also rebuilds the
   rollups. (Step 2 already loads contributions; step 4 replaces them, which is redundant but is the only path that
   rebuilds the rollups.)
5. `python3 run.py run-cross-matching --only all` (or the 527 jobs individually: `--only` accepts `527-committees`,
   `527-expenditures`, `527-directors`, `527-director-candidates`, `527-director-addresses`, `527-org-addresses`,
   `lobbying-527`), then `python3 run.py refresh-analytics --with-snapshot`, then restart the web service (prod).

Runtime was not measured in this task (the Feb 2026 load spread over three days of timestamps); treat it as a
long-running job — on prod, CLAUDE.md "Long-running server tasks" applies (Devin runs it in `tmux`).

**Step 3 — post-import checks** (read-only):

```bash
psql ilcf -c "SELECT (SELECT COUNT(*) FROM irs527_organizations) orgs, (SELECT COUNT(*) FROM irs527_reports) reports, (SELECT COUNT(*) FROM irs527_contributions) contribs, (SELECT COUNT(*) FROM irs527_expenditures) exps, (SELECT MAX(period_end) FROM irs527_reports) max_period_end, (SELECT MAX(date) FROM irs527_expenditures WHERE date ~ '^[0-9]{8}$') max_exp_date;"
```

Expect `max_period_end` to move past `20251231` and counts to grow modestly, not double (doubling means step 1 was
skipped). `validate-freshness` will not catch a stale or failed 527 load (§1, evidence 3).

**Step 4 — getting it to prod (written for Devin, not run).** Upload the zip, not the 3 GB text file:

```bash
scp -i ~/.ssh/hetzner_ed25519 "$D/PolOrgsFullData.zip" root@178.156.162.56:/srv/illinois_campaign_finance/app/Bulk_download/IRS_data/
```

then on the server, in `tmux`: unzip into a dated folder under `/srv/illinois_campaign_finance/app/Bulk_download/IRS_data/`
(if `unzip` is missing: `python3 -m zipfile -e PolOrgsFullData.zip <dir>`), and run steps 1–5 above with
`/srv/illinois_campaign_finance/shared/venv/bin/python3` and `sudo -u postgres psql -d ilcf` for the TRUNCATE. The
server path is the one CLAUDE.md uses for `Bulk_download/`; whether an `IRS_data/` folder already exists on prod was
not checked (no prod access in this task). Alternatively the server can download directly with the Step 1 `curl`
line (§5 shows it needs no browser), which avoids the upload entirely.

## 4. Cadence

**IRS side.** The bulk file is rebuilt weekly (Saturday 3:00 AM per the download page; current build 2026-10-24).
Pulling more than once a week gains nothing.

**Filer side (Form 8872 schedule).** The following is the statutory schedule for Form 8872 (IRC §527(j)) as
summarised from the IRS Form 8872 instructions — **not re-fetched in this task; verify before relying on the dates**:
a 527 organization files either **monthly** (due the 20th of the following month; in election years the November
report is replaced by pre-general and post-general reports, and December by a year-end report due January 31) or on
the **quarterly / semi-annual** schedule — in election years quarterly reports due April 15, July 15 and October 15,
a pre-election report 12 days before an election, a post-general report 30 days after the general and a year-end
report due January 31; in non-election years a mid-year report due July 31 and a year-end report due January 31.

**Recommendation.**

- **Baseline: monthly**, in the week after the 20th (after monthly filers' deadline and the following Saturday rebuild).
- **Extra pulls** in the first week after each major deadline cluster: after **January 31** (year-end reports, the
  biggest single batch), after **April 15, July 15 / July 31, October 15**, and — in even years — after the
  **pre-general** (≈12 days before the November election) and **post-general** (30 days after; early December)
  reports.
- For the 2026 general (2026-11-03): pulls on or after Saturday 2026-10-24 have the pre-general reports; the next
  high-value pull is after the post-general deadline (early December 2026), then early February 2027 for year-end.
- Immediate need: the loaded data ends at the 2025 year-end period; a pull now would add every 2026 report.

## 5. Automation feasibility

**Probe results (2026-10-29; details in Appendix A):**

| URL | Method | Result |
|---|---|---|
| `…/political-organization-disclosures-data-download` (brief's URL) | HEAD | 404, text/html |
| `forms.irs.gov/app/pod/dataDownload/dataDownload` | GET | 200, text/html, no redirect |
| `forms.irs.gov/app/pod/dataDownload/fullData` | HEAD | 302 → `www.irs.gov/static_assets/error/404errorPage.html` → 404 |
| `forms.irs.gov/app/pod/dataDownload/fullData` | GET (curl default UA and `Mozilla/5.0`) | 200, `application/zip`, `content-disposition: attachment; filename=PolOrgsFullData.zip`, no `content-length`, no `last-modified`, Range header ignored (full body sent) |
| `forms.irs.gov/app/pod/dataDownload/dataLayout` | HEAD | 302 → 404 (same HEAD behaviour) |

**What item 4 can script.** The download is a plain unauthenticated GET — no form, no POST, no cookie, no
CAPTCHA, and curl's default User-Agent works. So `curl` (Step 1 line) plus `unzip` can run unattended, on the laptop
or on the server. Points a script must handle:

- **No HEAD and no `content-length` / `last-modified`.** A change check cannot use `curl -I`. It can read the first
  ~64 bytes of the GET (`curl -s URL | head -c 64`) and decode the zip local-file header's DOS date/time (bytes
  10–13) — that is the IRS build time (2026-10-24 08:14 today) — and skip the download if it equals the last
  loaded build. Integrity: check `unzip -t` and that the uncompressed size is within a few percent of the previous
  pull (the analogue of ISBE's `.row_counts` gate).
- **Bot-management cookies.** The response sets Akamai cookies (`_abck`, `bm_sz`, `akaalb_PAWS_ALB_PROD`). Today they
  are not enforced on this URL, but an unattended job should fail loudly (non-zip body, `file` ≠ Zip) rather than
  import an HTML challenge page.
- **The import is the hard part, not the download.** Re-running `import-irs527` duplicates five tables (§3), so an
  unattended refresh needs either the manual TRUNCATE baked into a wrapper or a loader change that replaces those
  tables itself (an M2 proposal). The contribution parser defect (A11, §6) also means an automated reload today
  re-imports bad dates.
- **What stays manual:** the decision to TRUNCATE on prod, the long import itself on prod (CLAUDE.md long-running
  rule), and the post-import review until a `validate-freshness` 527 spec exists.

## 6. Data-quality flags

Field numbers in this document are **0-based indexes** into the pipe-split record, the numbering the loader's
`_safe_get(fields, N)` uses; the IRS layout document's 1-based column N is index N−1 (and `cut -f<N>` is 1-based).

| ID | Defect | Local measurement (2026-10-29) | Display impact | Which command fixes stored values |
|---|---|---|---|---|
| **A11** | `irs527_contributions.date` holds the **aggregate-year-to-date amount**, not the contribution date. Root cause: `_parse_contribution` (`database/irs527_loader.py:255`) reads field 15, which the IRS layout names `AGG CONTRIBUTION YTD`; `CONTRIBUTION DATE` is field 16 (layout doc, Schedule A record). | 8,974,461 rows: **0** hold a valid `YYYYMMDD` or ISO date; 8,973,691 unsigned integer text (`MIN`/`MAX` text `-1000`…`99999` across all non-blank), 61 signed or decimal, 709 blank; 934,638 equal the row's own `amount` (first gift of the year, where YTD = amount). | Display-side fix deployed: `_iso_text_date_sql` (`webapp/routes/irs527.py:247`) turns non-date text into NULL, so the bad values no longer show as dates (deploy `4529328`, prod 06:39Z 2026-10-29; `docs/runbooks/deployment.md` l.227). The data is still wrong: every date-windowed 527 contribution figure (cycle subtotals, `/527/dark-money` windows, Top Contributors date ranges) has no dates to filter on. | **None today.** `repair-irs527-contributions` calls the same `_parse_contribution` (`irs527_loader.py:852`), so it reproduces the defect. After a one-line parser fix (field 16 for the date; field 15 could be kept as a new YTD column), `repair-irs527-contributions --file <…> --replace-existing` is the right command to correct the stored values without a full re-import. |
| **D2** (new) | `irs527_organizations.purpose` / `material_change_date` / `insert_datetime` are shifted by three fields. `_parse_org` reads fields 36/37/38 (`irs527_loader.py:116-118`); in the IRS 8871 layout those are `EXEMPT 8872 INDICATOR`, `EXEMPT STATE`, `EXEMPT 990 INDICATOR`; purpose, material change date and insert datetime are fields 39/40/41 (bypass flags 42/43, read as 39/40). | `material_change_date`: 1,339 rows hold two-letter state codes (`AL`…`WY`), 1,004 blank, 0 dates. `MAX(insert_datetime)` = `1`. `purpose`: 2,066 rows hold `0`/`1`, 277 blank, 0 real purpose text. Related: `form_id` is the constant `8871` (the Form Type field) and the real Form ID sits in `form_id_seq`. | **Visible today:** `/527/<ein>` prints "Purpose: 0" (`irs527/detail.html:80`; public `/527/813554739`). `material_change_date` / `insert_datetime` are not displayed but are tie-breakers in the "latest registration" pickers (`webapp/routes/irs527.py:155-156`, `350-351`); `form_id_seq` sorts first, so the pick is still the newest form. See §7. | None: no repair command touches `irs527_organizations`. Needs a parser fix, then a reload of the org table. |
| **D3** (new) | `import-irs527` is not idempotent (§3): five tables are appended on every run. | Not measured whether the local tables already hold duplicates. | Doubling of directors, expenditures, contributions, election authorities on a careless reload. | Procedure (§3 TRUNCATE) or a loader change. |
| **MT-017** | Org page "Period: 1000 to 995" (lexicographic MIN/MAX over malformed text). | Local reports' `period_start`/`period_end`: all 1,739 are `YYYYMMDD` (`20000701`…`20251101` / `20001127`…`20251231`). Local `/527/813554739` on `:5061` (06:53:02Z): "Period: 2016-08-15 to 2025-12-31". | **Fixed (display).** The period is now the union of the reports' span and the valid transaction dates, both through `_iso_text_date_sql`; deploy `4529328` (prod 06:39Z); public page 06:52:54Z shows "Period: 2016-08-15 to 2025-12-31". The malformed values came from the contribution `date` column (A11) on prod; prod `irs527_reports` was not queried. | n/a — the stored A11 values remain (row above). |
| — | `irs527_expenditures.date` is clean. | 35,235 valid `YYYYMMDD` (`20021007`…`20251231`), 679 blank, 0 other. | — | — |

Counts here use a stricter test than `.claude/data-dictionary.md`'s A11 note (2026-10-26/27: 8,972,064 amount-like,
934,577 equal to `amount`); the totals reconcile (8,973,691 + 61 + 709 = 8,974,461). The differences come from the
equality test (text equal to either `amount::bigint::text` or `amount::text` here) and the earlier note's bucket
definitions; neither changes the conclusion — no row has a usable date.

## 7. Integration today

Found by grepping `webapp/` and `database/analytics.py` for `irs527` / `lobbying_527` (worktree at main `48e5399`).
Lines are those of 2026-10-29. "A11" marks a figure that filters `irs527_contributions` by its `date` column, so its
windowed value has no real dates behind it (§6). The local `/527/dark-money` contribution window total fell from
$4.51M to $224K once malformed values stopped counting (`docs/runbooks/deployment.md:174`).

| 527 data | Page / widget | Route:line or template | Via (table / match table) | Window / scope | Notes |
|---|---|---|---|---|---|
| Nav entry | "527s" in the top nav | `webapp/templates/base.html:45` | — | — | Every page. |
| Org list | `/527/` organization list, totals, "committee match" column | `irs527.py:258` `list_orgs`; `irs527/list.html` (match column l.46) | `irs527_organizations` (latest-registration picker l.336-356), `irs527_expenditures` (l.289), `irs527_contributions` (l.306), `irs527_contribution_rollup` (l.400), `irs527_committee_matches` (l.403) | Transaction window, default current cycle (`_resolved_transaction_window`, l.43) | Contribution totals: A11. Picker tie-breaks on D2 columns (§6). |
| Autocomplete | `/527/suggest` (JSON) | `irs527.py:416` | `irs527_organizations` | — | Org names and EINs. |
| Org profile | `/527/<ein>`: registration block, financial summary and period, filing history, expenditures, contributions, related orgs, directors | `irs527.py:435` `org_detail`; `irs527/detail.html` | `irs527_organizations` (`_latest_org` l.143), `irs527_reports` (l.449-473), `irs527_expenditures` (l.475-510), `irs527_contributions` (l.512-530, 637-690), `irs527_directors` (l.540), `irs527_related_orgs` (l.548; detail.html:155-167) | All time by default; financial summary follows the window | **D2 visible:** "Purpose: 0" (`detail.html:80`). Contribution window and "This cycle" subtotal: A11. Period line fixed (MT-017, §6). |
| Committee matches | `/527/<ein>` "matched committees" table | `irs527.py:573-585`; `detail.html:86-97` | `irs527_committee_matches` | All time | Also feeds the list column and the ecosystem graph. |
| Director → donor / candidate | `/527/<ein>` directors table, "Matched Donor" / "Matched Candidate" columns | `irs527.py:586-613`; `detail.html:110-141` | `irs527_director_donor_matches`, `irs527_director_candidate_matches` | All time | Up to 3 matches per director. |
| Contributors ledger | `/527/<ein>/contributors` | `irs527.py:740` (`_LEDGERS` l.68-78); `irs527/contributors.html` | `irs527_contributions` | Transaction window | Windowed view: A11. |
| Recipients ledger | `/527/<ein>/recipients` | `irs527.py:746`; `irs527/recipients.html` | `irs527_expenditures` | Transaction window | Dates clean. |
| Dark money tracker | `/527/dark-money`: matched expenditure list + funding-source stats | `irs527.py:789` `dark_money`; stats `_get_dark_money_contribution_stats` l.164-245; `irs527/dark_money.html` | `irs527_expenditure_recipient_matches` + `irs527_expenditures` (l.795-900); `irs527_contributions`, `irs527_contribution_rollup`, `irs527_contributor_rollup` (l.186-240) | Transaction window, default current cycle | Funding-source stats: A11. |
| Home "527 Dark Money Summary" | Org count, total 527 expenditures, total 527 contributions received, director-donor match count | `main.py:2905` `index` (l.2937-3001); `index.html:181-208` | `irs527_organizations`, `irs527_expenditures` (fallback `irs527_reports`), `irs527_contributions` (fallback `irs527_contribution_rollup`), `irs527_director_donor_matches` (count only) | Org count all time; money tiles windowed | Contributions tile: A11. |
| Home insights | "527 Directors Who Are Also Candidates"; dark-money matched total | `main.py:254` `_build_dashboard_insights` (l.305-330, 332-352); `index.html:61-80`, `117-118` | `irs527_director_candidate_matches`; `irs527_expenditures` ⋈ `irs527_expenditure_recipient_matches` (score ≥ 0.80) | Directors all time; dark-money total windowed on `DATE(e.date)` | Expenditure dates are clean. |
| Investigate | `/investigate` cross-dataset hits | `main.py:2532` (527 orgs l.2642-2650, 8 hits); `investigate.html:29` | `irs527_organizations` | — | Name match on org name. |
| Person Intelligence | `/person-intelligence` "527 Organization Directors" | `main.py:3275` (l.3549-3560); `person_intelligence.html:118-136` | `irs527_directors` | — | Person lookup; links to the org page. |
| Network graphs | `/analytics/networks` "527 Dark Money" tab | `analytics.py:922` → `_networks_graphs` l.395 → `database/analytics.py:6534` `get_irs527_ecosystem_graph`; `analytics/networks.html:96`, `233-296` | `irs527_organizations`, `irs527_committee_matches`, `irs527_expenditure_recipient_matches`, `irs527_director_donor_matches`, `irs527_expenditures`; `irs527_contributions` picks "active" EINs in a window (`database/analytics.py:6591-6600`) | Windowed | Active-EIN filter on contributions: A11. |
| Relationship graphs | `/analytics/relationships` "527 Ecosystem Graph" + edge table | `analytics.py:1224` → `_relationships_graphs` l.449; `analytics/relationships.html:454-525` | Same as above | Windowed | Same A11 caveat. |
| Ecosystem API | `/api/analytics/irs527-ecosystem` (JSON) | `api.py:462` | Same as above | Query params | — |
| Lobbying client | `/lobbying/client/<id>` "527 Organization Connections" | `lobbying.py:882` `client_detail` (l.958-970); `lobbying/client_detail.html:199-213` | `lobbying_527_matches` | All time | 30 match rows locally. |

**Match tables (local row counts, 2026-10-29):** `irs527_committee_matches` 1,144 · `irs527_expenditure_recipient_matches`
11,189 · `irs527_director_donor_matches` 13,186 · `irs527_director_candidate_matches` 1,206 · `lobbying_527_matches` 30
are displayed as above. **Never displayed:** `irs527_director_address_matches` (44,376 rows) and
`irs527_org_address_matches` (260,387 rows). There are 0 references in `webapp/` or `database/analytics.py`. They are
computed by `run-cross-matching` and read by nothing. `irs527_election_authority` (17,305 rows) is also not displayed.

**Where 527 does not appear:**
- Search and the Data Status box: `/search` (`main.py:3050`) and the Data Status box / freshness strip.
- Profile pages: committee (`committees.py`), candidate (`candidates.py`, `candidate_finance.py`), donor (`donors.py`)
  and lobbying-entity pages.
- Analytics: `/analytics/` overview tiles, state races, `/analytics/risk`.
- `/federal-finance`.

A committee matched to a 527 org, or a donor matched to a 527 director, shows the link only on the 527 side.

**D2 display impact:**
- "Purpose: 0" / "Purpose: 1" on `/527/<ein>` (`detail.html:80`) for 2,066 of 2,343 registration rows. It is
  visible on prod: the public `/527/813554739` reads "Purpose: 0", fetched 06:52:54Z.
- `material_change_date` and `insert_datetime` are not displayed. They act only as tie-breakers in the
  latest-registration ordering (`irs527.py:155-156`, `350-351`).

## 8. Gaps and proposals for Devin

Owners:
- **T1** is terminal 1 (routes, analytics, templates).
- **Other session** is the session that owns `cli/commands.py` and the loaders.
- **Devin** owns prod runs and destructive SQL.

Footprints are estimates from this read and have not been built. Ranked by value ÷ effort, with correctness first.

| P-N | Proposal | Pages affected | Footprint (files) | Owner | Value (one line) | Devin decision |
|---|---|---|---|---|---|---|
| P-1 | **Fix A11**: `_parse_contribution` reads the date from field 16 and keeps field 15 as a new `agg_contribution_ytd` column. Then reload with `repair-irs527-contributions --file <…> --replace-existing` (locally, then prod). | Every A11 row in §7: `/527/` totals, `/527/<ein>` contribution window and cycle subtotal, contributors ledger, dark-money funding stats, home contributions tile, both ecosystem graphs | `database/irs527_loader.py` (parser + insert column list), `database/schema.sql` (new column), `tests/test_irs527_loader.py` | Other session (code); Devin (prod reload, long-running) | Restores dates to 8.97M contribution rows, so every windowed 527 contribution figure means something. | Approve fix + reload |
| P-2 | **Fix D2**: `_parse_org` reads purpose / material_change_date / insert_datetime / bypass flags from fields 39–43, and form_id from field 2. Reload `irs527_organizations` (needs a replace path, see P-3). | `/527/<ein>` "Purpose" line; latest-registration pickers | `database/irs527_loader.py`, `tests/test_irs527_loader.py` | Other session; Devin (prod reload) | Real purpose text instead of "Purpose: 0"; correct registration metadata. | Approve |
| P-3 | **Make `import-irs527` re-runnable**: a `--replace-existing` mode that deletes the five append-only tables (and the org table for P-2) before loading, and rebuilds both rollups at the end, so one command does a full refresh. | None directly; it enables P-1, P-2 and P-6 | `database/irs527_loader.py`, `cli/commands.py`, tests | Other session | Removes the manual TRUNCATE (§3) and the double-load hazard (D3); prerequisite for unattended runs. | Approve; until then, decide whether the §3 TRUNCATE is acceptable on prod |
| P-4 | **Pull the October file now** (after P-1 to P-3, or with the §3 manual procedure if Devin accepts loading the A11/D2 defects again) and load it locally and on prod. | All 527 pages (data ends 2025-12-31) | None (runbook §3) | Devin | Adds every 2026 Form 8872 report, about 10 months of data, ahead of the Nov 3 general. | Timing: now with known defects, or after the fixes |
| P-5 | **`validate-freshness` 527 spec**: `FreshnessSpec("IRS 527 reports", "irs527", "irs527_reports", "period_end", 120)`. `period_end` is clean `YYYYMMDD` text, which `_coerce_date` does not parse yet; it needs a compact-date branch, or use `insert_datetime` (filing timestamp; parses today). After P-1, a second spec on `irs527_contributions.date` is possible. Threshold: about 120 days covers the quarterly cycle. | CLI gate only | `database/data_freshness.py`, `cli/commands.py` (`--source` choice is `all\|isbe\|fec` today, l.1002), `tests/test_data_freshness.py`, CLAUDE.md "Data Refresh Schedule" | Other session | A stale or failed 527 load fails the gate instead of passing silently (§1, evidence 3). | Approve; choose column and threshold |
| P-6 | **Item-4 wrapper** `scripts/irs527-refresh.sh`, run weekly on Sundays after the Saturday 03:00 rebuild. Steps: `curl … \| head -c 64` and compare the zip header's DOS date/time with the last loaded build (stored beside the data); exit if unchanged. Otherwise download to `.partial`, check that `file` reports a Zip and fail loudly on HTML, `unzip -t`, check the uncompressed size is within ±10 % of the previous build, unzip to a dated folder (P-7), run `import-irs527 --replace-existing` (P-3), run `run-cross-matching` 527 jobs, then `validate-freshness --source irs527` (P-5). Optionally a systemd timer like the FEC one. | None directly | New `scripts/irs527-refresh.sh` (+ optional `.timer`/`.service` on prod), `docs/runbooks/deployment.md` timer table | Item 4 owner (script); Devin (timer install on prod) | Turns the 527 refresh from "forgotten since February" into a weekly no-op-if-unchanged job. | Approve once P-3 and P-5 land |
| P-7 | **Dated-folder placement** `Bulk_download/IRS_data/<YYYYMMDD>/` keeping the zip (§3), with old extractions deleted after a verified load. | None | Runbook only (this file, CODEBOOK §3.4, CLAUDE.md "Common Operations" paths) | T1 (docs) | Old builds are no longer overwritten in place, rollback is possible, and the wrapper knows which build is loaded. | Approve convention |
| P-8 | **Freshness line for 527** in the Data Status box / freshness strip, e.g. "IRS 527: reports through <MAX(period_end)>, file built <date>". | Every page with the Data Status box; `/527/*` | Data Status partial + its context provider (path to locate), test | T1 | Readers see that 527 data stops at 2025-12-31 instead of assuming it is current. | Approve |
| P-9 | **527 links on committee / candidate / donor pages** from the existing match tables. Committee page: "matched 527 organization" (`irs527_committee_matches`) and "527 spending received" (`irs527_expenditure_recipient_matches` ⋈ `irs527_expenditures`). Candidate page: same via `irs527_expenditure_recipient_matches` (candidate targets) and "director of 527 org" (`irs527_director_candidate_matches`). Donor page: "director of 527 org" (`irs527_director_donor_matches`). | `/committees/<id>`, `/candidates/<id>`, donor profile | `webapp/routes/committees.py`, `candidates.py`, `donors.py` + templates + `tests/scope_widgets_registry.py` rows for windowed widgets | T1 | Makes the existing 527 joins visible where readers already are; today they are visible only from the 527 side. | Approve; choose which pages |
| P-10 | **Dark-money totals in state-race analytics**: per race, the sum of matched 527 expenditures to the race's candidates and committees (`irs527_expenditure_recipient_matches`, expenditure dates clean), next to the ISBE totals. | `/analytics/state-races/`, `/analytics/state-races/<race_key>` | `webapp/routes/analytics.py`, race templates, tests, cache keys | T1 | Shows outside 527 money in a race beside the candidates' own receipts. Needs a match-quality review first (Jaccard 0.80 name matches). | Approve + review match precision first |
| P-11 | **527 orgs in `/search`** (absent today; `/investigate` already lists them): add an org-name hit group linking to `/527/<ein>`. | `/search` | `webapp/routes/main.py` (search), search template, tests | T1 | A reader searching an org name finds its 527 filing. | Approve |
| P-12 | **Address match tables**: decide to show `irs527_org_address_matches` / `irs527_director_address_matches` (for example "shares an address with" on the 527 org page), or stop computing them. | `/527/<ein>`, or none | Show: `irs527.py` + template; stop: `database/cross_matching.py` job list | T1 or Other session | 304,763 rows are computed on every `run-cross-matching --only all` and shown nowhere. The address-proximity matches involve individuals' addresses, so a display needs a privacy review. | Show, drop, or leave |

**Decisions Devin must make**

1. Approve the parser fixes P-1 (A11) and P-2 (D2), and the re-runnable import P-3. They are the prerequisite for any
   refresh worth doing.
2. Choose the timing of the next pull (P-4): now with the known defects, using the §3 manual TRUNCATE on prod, or
   after P-1 to P-3 land.
3. Choose whether the 527 refresh becomes a weekly prod job (P-6 + timer) or stays a monthly manual run with the
   §4 deadline pulls.
4. Choose the freshness column and threshold for P-5, and whether readers see 527 freshness (P-8).
5. Choose which integration ideas to pursue (P-9 to P-11) and in which order. P-10 needs a match-precision review
   first.
6. Decide what happens to the unused address-match tables (P-12), including the privacy question.

## Appendix — probes and queries (2026-10-29, UTC)

### A. IRS site probes

All from the laptop with `curl`; `-A "Mozilla/5.0"` unless noted.

1. 06:50:11Z `curl -sIL --max-time 30 https://www.irs.gov/charities-non-profits/political-organizations/political-organization-disclosures-data-download`
   → `HTTP/2 404`, `content-type: text/html; charset=UTF-8`, `content-length: 86364`, date `Thu, 29 Oct 2026 06:50:11 GMT`.
2. 06:50:12Z `curl -sIL --max-time 30 https://forms.irs.gov/app/pod/dataDownload/fullData`
   → `HTTP/2 302`, `content-length: 0`, `location: https://www.irs.gov/static_assets/error/404errorPage.html` → `HTTP/2 404`, `text/html`, `content-length: 86242`.
3. 06:50:20Z `curl -sL --max-time 30 -r 0-1023 -D - -o <scratch> https://forms.irs.gov/app/pod/dataDownload/fullData`
   → `HTTP/2 200`, `content-type: application/zip`, `content-disposition: attachment; filename=PolOrgsFullData.zip`,
   date `06:50:21 GMT`; no `content-range`, no `content-length`. **The server ignored the Range header and sent the
   whole archive** (365,833,164 bytes, received within the 30 s limit). The local-file header was read
   (entry `var/IRS/data/scripts/pofd/download/FullDataFile.txt`, method 8, DOS time 2026-10-24 08:14,
   compressed 365,832,912, uncompressed 2,986,796,580) and the file was deleted at 06:50:41Z without extraction.
4. 06:50:20Z GET `https://www.irs.gov/charities-non-profits/political-organizations` → 200, 105,600 bytes; link to
   `…/political-organization-filing-and-disclosure`.
5. 06:50:46Z GET `…/political-organization-filing-and-disclosure` → 200, 108,328 bytes; links to
   `https://forms.irs.gov/app/pod/dataDownload/dataDownload` and `…/basicSearch/search`.
6. 06:50:55Z GET `https://forms.irs.gov/app/pod/dataDownload/dataDownload` → 200, `text/html;charset=UTF-8`,
   6,469 bytes, 0 redirects; links `dataLayout`, `fullData`, `dataAG`, `dataHM`, `dataNR`, `dataSZ`; text quoted in §2.
7. 06:51:06Z `curl -sL --max-time 30 -D <hdr> https://forms.irs.gov/app/pod/dataDownload/fullData | head -c 64`
   (curl default User-Agent) → `HTTP/2 200`, `application/zip`, same `content-disposition`, `cache-control: max-age=0,
   no-cache, no-store`, sets cookies `istio_session`, `akaalb_PAWS_ALB_PROD`, `_abck`, `bm_sz`; first bytes `PK\x03\x04`,
   DOS date 2026-10-24.
8. 06:51:06Z `curl -sIL https://forms.irs.gov/app/pod/dataDownload/dataLayout` → 302 → 404 (as probe 2).

### B. CLI `--help` (worktree, `/opt/anaconda3/bin/python3 run.py <cmd> --help`, 06:49Z)

- `import-irs527`: `--file TEXT  Path to IRS 527 FullDataFile.txt  [required]`;
  `--illinois-only / --all-states  Only load orgs with IL addresses or IL expenditures  [default: illinois-only]`.
- `repair-irs527-reports`: "Repair irs527_reports totals/ein mapping from FullDataFile type-2 records." `--file`
  [required]; `--illinois-only / --all-states  Limit repair load to Illinois-related EINs [default: illinois-only]`;
  `--replace-existing / --append  Delete current irs527_reports rows before rebuild [default: replace-existing]`.
- `repair-irs527-contributions`: "Repair irs527_contributions from FullDataFile type-A records." `--file`
  [required]; `--illinois-only / --all-states  Limit contribution reload to Illinois-related EINs [default:
  illinois-only]`; `--replace-existing / --append  Delete current irs527_contributions rows before rebuild [default:
  replace-existing]`.
- `run-cross-matching`: `--only [all|lobbying-donors|lobbying-expenditures|527-committees|527-expenditures|527-directors|527-director-candidates|527-director-addresses|527-org-addresses|lobbying-527]`.

### C. Disk (read-only listings, 06:49Z)

- Worktree: no `Bulk_download/` directory.
- Main checkout `ls -lT Bulk_download/IRS_data` tree: `IRS_data/` (dir, 2026-02-13 09:27), `PolOrgsFileLayout.doc`
  (306,176 bytes, 2015-01-21), `var/IRS/data/scripts/pofd/download/FullDataFile.txt` (2,769,000,376 bytes,
  2026-02-08 03:38:21 local). No zip, no other IRS file in `Bulk_download/`.
- Layout doc read with `textutil -convert txt -stdout` into the session scratchpad (Schedule A and 8871 field order).
- Raw-record check (Fable, about 07:00Z, read-only on the main checkout's `FullDataFile.txt`). Three `A` records give
  index 15 (`cut -f16`) = `5000` / `92298` / `77215` (amounts: AGG CONTRIBUTION YTD) and index 16 (`cut -f17`) =
  `20030214` / `20030630` / `20030531` (dates: CONTRIBUTION DATE), so the loader's index 15 is the YTD amount
  (A11). In `1` records, Python index 39 holds purpose text and index 41 the insert timestamp (D2). Together with the
  layout doc, this is the evidence for A11 and D2.

### D. Local DB queries (`PGOPTIONS='-c default_transaction_read_only=on'`, `SHOW default_transaction_read_only` = `on`)

1. 06:51:14Z row counts: `SELECT '<t>', COUNT(*) FROM <t>` for the nine `irs527_*` base and rollup tables (§1 table).
2. Load timestamps / newest values:
   `SELECT MIN(created_at), MAX(created_at), MAX(material_change_date), MAX(insert_datetime) FROM irs527_organizations`;
   same for `irs527_reports` with `MAX(period_end)`, `irs527_contributions`, `irs527_expenditures` with `MAX(date)`;
   `SELECT MIN(updated_at), MAX(updated_at)` on both rollups.
3. Pattern buckets (`yyyymmdd` / ISO / blank / numeric / other) with `COUNT`, `MIN`, `MAX` for
   `irs527_reports.period_start`, `period_end`, `irs527_organizations.material_change_date`.
4. 06:51:39Z and 06:53:28Z A11:
   `SELECT COUNT(*) FILTER (WHERE date ~ '^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$'),
   COUNT(*) FILTER (WHERE date ~ '^\d+$'), COUNT(*) FILTER (WHERE date ~ '^-?\d+(\.\d+)?$' AND date !~ '^\d+$'),
   COUNT(*) FILTER (WHERE date IS NULL OR date=''), COUNT(*) FILTER (WHERE date = amount::bigint::text OR date = amount::text)
   FROM irs527_contributions` → `0 | 8973691 | 61 | 709 | 934638`.
5. `irs527_expenditures.date` buckets → 35,235 `yyyymmdd` (`20021007`…`20251231`), 679 blank.
6. `SELECT COUNT(DISTINCT ein) FROM irs527_contributions` → 656; `irs527_organizations`: 2,343 rows, 1,896 distinct
   EINs, 2,283 with `state = 'IL'`.
7. About 07:02Z (M2; not timestamped separately) `irs527_organizations.purpose` buckets → 2,066 `0`/`1`, 277 blank. `COUNT(DISTINCT form_id)` = 1
   (`8871`), `COUNT(DISTINCT form_id_seq)` = 2,343.
8. About 07:02Z (M2; same query batch) match-table counts: `irs527_director_address_matches` 44,376 · `irs527_org_address_matches` 260,387 ·
   `irs527_committee_matches` 1,144 · `irs527_expenditure_recipient_matches` 11,189 · `irs527_director_donor_matches`
   13,186 · `irs527_director_candidate_matches` 1,206 · `lobbying_527_matches` 30.

### F. Code inventory for §7 (M2, 07:00–07:03Z, read-only grep)

- `grep -rn "irs527\|lobbying_527" webapp database/analytics.py` (outside `routes/irs527.py`): hits only in
  `routes/main.py`, `routes/analytics.py`, `routes/api.py`, `routes/lobbying.py`, `app.py`, `cache_backend.py`, three
  graph JS files and `database/analytics.py` (`get_irs527_ecosystem_graph` only). The enclosing route and function
  for each hit were found by the nearest preceding `@…route` / `def`.
- `grep -rln "irs527\|527" webapp/templates`: `base.html`, `index.html`, `investigate.html`,
  `person_intelligence.html`, `analytics/networks.html`, `analytics/relationships.html`,
  `lobbying/client_detail.html`, the five `irs527/*.html` and `privacy.html` (text only).
- `irs527_director_address_matches`, `irs527_org_address_matches`: 0 hits in `webapp/` and `database/analytics.py`.
  `irs527_election_authority`: 0 hits.
- `/527` routes by decorator: `/` l.258, `/suggest` l.416, `/<path:ein>` l.435, `/<path:ein>/contributors` l.740,
  `/<path:ein>/recipients` l.746, `/dark-money` l.789. These match `docs/page_inventory.md` C-12, plus `/suggest`,
  which the inventory lists among the non-page (JSON) endpoints rather than under C-12.
- D2 display: the saved public and local copies of `/527/813554739` (Appendix E) both contain
  `Purpose:</strong> 0`.

### E. Pages

- 06:52:54Z public GET `https://www.followthemoneyil.com/527/813554739` → 200, 0.61 s; "Reports Filed: 42 · Total
  Contributions: $60,789,551.00 · Total Expenditures: $59,271,597.00 · Period: 2016-08-15 to 2025-12-31".
- 06:53:02Z local `runserver --port 5061` (read-only DB session), GET `/527/813554739` → 200, 1.10 s; same four
  figures. Server stopped 06:53Z (port 5061 free).
