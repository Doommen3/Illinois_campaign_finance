# Manual test register

These are manual checks to run on the live site (or a local server), on top of the automated `pytest` suite.
The automated tests show that the code does what the tests say. These checks show that the site does what a
reader expects: the pages load, the numbers agree with each other, the date windows read correctly and the links
lead somewhere. Each check says what it tests, when it was written, the exact steps, and what counts as a pass or
a fail.

## How to run a session

1. Open https://www.followthemoneyil.com, or a local server (for example `run.py runserver --port 5057`, then
   http://127.0.0.1:5057).
2. Pick a set: the ⭐ core subset (about 15 minutes), every test, or only the tests whose **Area** matches what
   just changed.
3. Skip any test marked **Retired**. Check its **Precondition** first; if the precondition does not hold, record
   the test as "skipped" rather than failed.
4. Follow the steps exactly as written. Paths in backticks are relative to the site root (on the live site,
   `/privacy` means https://www.followthemoneyil.com/privacy).
5. Decide **Pass** or **Fail** from what is on the page. When something is not covered by either, call it a fail
   and write down what you saw.
6. Add one row to the run log at the end of this file: date, site, the tests you ran, the result, and the ids of
   any failures with a short note.

## Maintenance rule

- Every feature update or bug fix that lands adds at least one test here, in the same commit or in the commit
  that records the deploy: a new `### MT-NNN` section plus its row in the index.
- Ids are sequential (MT-001, MT-002, ...) and never reused.
- To change a test, edit it and add a line `**Revised:** <date> — <what changed>`. To retire one, add
  `**Retired:** <date> — <why>` and keep the section; never delete a test.
- Before committing, run `python3 scripts/audit/check_manual_tests.py` (structure: required fields, unique ids,
  index rows) and, with a local server running, `python3 scripts/audit/check_manual_tests.py --host http://127.0.0.1:5057`
  (every path quoted in this file must answer 200).
- Data-integrity checks (row counts, totals against the source files) belong in `docs/data_integrity_checks.md`
  (to-do item 10), not here.

## Conventions

- Dates are ISO (`YYYY-MM-DD`).
- Site paths are written in backticks, relative to the site root (for example `/candidates/13505`), so the checker
  can fetch them. Placeholders go in angle brackets (`/committees/<id>`); the checker skips any path containing
  `<` or `>`.
- Examples use public officials and political committees only (Michael W Frerichs = candidate 13505; Democratic
  Party of Illinois = committee 6239). Donor pages are reached by clicking through from a committee; this file
  never names a private individual or quotes an address or donor key.
- "Window" means the time scope bar under the page title: "Showing", the scope name (for example "2026 Cycle" or
  "All time"), the dates it covers, and "latest ISBE data: <date>".
- **Source commit** is the commit whose behaviour the test guards (`baseline` for the day-one set).
- ⭐ in the Core column marks the core subset (about 15 minutes).

## Index

| ID | Area | What is tested | Created | Core |
|---|---|---|---|---|
| MT-001 | Home | Home page loads with its sections, window and data freshness | 2026-10-29 | ⭐ |
| MT-002 | Candidates | Candidate profile tabs load; totals and "This cycle" subtotals agree | 2026-10-29 | ⭐ |
| MT-003 | Committees | Committee profile, donors and payees pages: totals agree, top donors link onward | 2026-10-29 | ⭐ |
| MT-004 | Time scope | Default windows (lists vs profiles), custom range, window carried by in-section links | 2026-10-29 | ⭐ |
| MT-005 | Privacy | Privacy page explains removal requests and is linked from every page | 2026-10-29 | ⭐ |

## Tests

### MT-001 — Home page loads with its sections and data freshness

- **Area:** Home
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** The home page is the front door; it guards against a blank or erroring dashboard and against stale or
  missing data going unnoticed.
- **Steps:**
  1. Open `/`.
  2. Read the heading and the window under it.
  3. Scroll through the page and note the section headings.
  4. In the "Data Status" box near the top, read "Local receipts:".
  5. In the "Data Freshness" section lower down, read "Latest local receipt date:".
- **Pass:** The page shows "Dashboard Overview"; the window reads "Showing 2026 Cycle", starting Jan 1, 2025;
  the sections "Start Here", "Key Findings", "527 Dark Money Summary", "Data Freshness", "Launch Paths" and
  "Top Donors" are all present; the stat cards (such as "Local Receipts" and "Federal Contributions") show numbers;
  the two local receipt dates in steps 4 and 5 are the same date.
- **Fail:** Error page or blank page; a missing section; a stat card that reads "None", "N/A" or is empty;
  "Not loaded" for local receipts; the two dates in steps 4 and 5 differ; the window is not "2026 Cycle".

### MT-002 — Candidate profile tabs and totals

- **Area:** Candidates
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** The candidate profile is the most visited entity page; it guards against tabs that do not load and
  against totals that disagree with each other or with the window.
- **Steps:**
  1. Open `/candidates/13505`.
  2. Read the window and the tab row under it.
  3. In the "Committees" section, read "Total raised: $X | This cycle: $Y" and the "Raised" column of the
     committee table.
  4. Click "2026 Cycle" in the window and read "Total raised" and "This cycle" again.
  5. Click the "Donors" tab. Read the window and, in the "Concentration by group" table, the "Total" of the
     "All committees" row (or of the only row, when there is one group).
  6. Click "Networks", "Risk", "Geography" and "Relationships" in turn.
- **Pass:** Step 2: the window reads "All time" and the tabs are Overview, Networks, Risk, Donors, Geography,
  Relationships. Step 3: amounts are formatted as dollars; $Y is less than or equal to $X; the "Raised" column
  adds up to "Total raised". Step 4: "Total raised" equals "This cycle". Step 5: the window still reads
  "2026 Cycle", and the Donors total is less than or equal to the Overview "Total raised" from step 4 (the
  Donors tab counts itemized contributions only, the Overview counts all receipts). Step 6: each tab loads with
  the heading "Michael W Frerichs — <tab name>" and the window still reads "2026 Cycle".
- **Fail:** Error page; a missing tab; an amount shown as "N/A", "None" or blank; "This cycle" larger than
  "Total raised"; the "Raised" column does not add up to "Total raised"; in the 2026 Cycle window the two
  figures differ; the Donors total is larger than the Overview total; a tab that resets the window to
  another scope.

### MT-003 — Committee profile, donors and payees pages

- **Area:** Committees
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** Guards the committee profile's summaries and its "See all" pages against totals that disagree and
  links that go nowhere.
- **Steps:**
  1. Open `/committees/6239`.
  2. Read "Receipts Summary" ("Total receipts" and "This cycle") and "Expenditures Summary"
     ("Total expenditures" and "This cycle").
  3. Note the first row of "Top Donors" (name and total) and the number in the "See all N donors →" link.
  4. Click "See all N donors →" (lands on `/committees/6239/donors`). Read the line under "Donors"
     ("N unique donors, $T | This cycle: $C") and the first row of the table.
  5. Click the first donor's name.
  6. Go back to `/committees/6239` and click "See all N payees →" (lands on `/committees/6239/payees`). Read the
     line under "Payees" ("N unique payees, $T | This cycle: $C").
- **Pass:** Step 2: all four amounts are dollar figures, and each "This cycle" is less than or equal to its
  total. Step 4: the donor count equals the N in the link; the first row matches the first "Top Donors" row from
  step 3 (same name, same total); $T is less than or equal to "Total receipts" (the donors page counts itemized
  contributions only). Step 5: a donor page loads showing that donor's contributions, including at least one
  to Democratic Party of Illinois. Step 6: the payee count equals the N in the link; $T equals "Total
  expenditures" and $C equals the expenditures "This cycle" from step 2.
- **Fail:** Error page; "N/A", "None" or a blank amount; a count that differs from its "See all" link; a first
  donor that differs between the profile and the donors page; a donors total above "Total receipts"; a payees
  total that differs from "Total expenditures"; a donor link that errors or shows no contribution to this
  committee.

### MT-004 — Time scope defaults, custom range and carried windows

- **Area:** Time scope
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** Every page resolves its own date window from the URL; this guards the defaults (lists = current
  cycle, profiles = all time) and the rule that in-section links keep the window while the top navigation
  resets it.
- **Steps:**
  1. Open `/committees/`. Read the window.
  2. Open `/committees/6239`. Read the window and the "Receipts Summary" line.
  3. Open `/committees/6239?date_from=2025-01-01&date_to=2025-06-30`. Read the window and the "Receipts Summary"
     "Date range".
  4. Click "See all N donors →". Read the window and the address bar.
  5. Click "Committees" in the top navigation. Read the window.
- **Pass:** Step 1: "Showing 2026 Cycle", "Jan 1, 2025 – <end>" where <end> is the earlier of Dec 31, 2026 and the
  "latest ISBE data" date; the "2026 Cycle" button is highlighted. Step 2: "Showing All time" and the summary
  shows both a total and a "This cycle" figure. Step 3: "Showing Custom range", "Jan 1, 2025 – Jun 30, 2025", a
  "Reset" link is shown, and the "Date range" falls inside Jan 1 – Jun 30, 2025. Step 4: the window still reads
  "Custom range, Jan 1, 2025 – Jun 30, 2025" and the address contains `date_from=2025-01-01` and
  `date_to=2025-06-30`. Step 5: the window is back to "2026 Cycle".
- **Fail:** No window shown on any of these pages; a list page that defaults to anything other than the 2026
  cycle; a profile that defaults to anything other than all time; a custom range that is ignored (amounts or
  date range unchanged from all time); the donors page losing the custom range; the top navigation carrying the
  custom range.

### MT-005 — Privacy and removal requests page

- **Area:** Privacy
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** The site honours home-address removal requests (Judicial Privacy Act and related laws); the public
  policy page must stay reachable and must say how to make a request.
- **Steps:**
  1. Open `/`. Scroll to the page footer.
  2. Click "Privacy & removal requests" (lands on `/privacy`).
  3. Read the section headings.
  4. Read "What a Request Needs" and "How to Contact Us".
  5. Click the "Redaction Request form" link.
- **Pass:** The footer link is present and opens the page titled "Privacy & Removal Requests"; the sections
  "What This Site Publishes and Where It Comes From", "Removal Requests We Honor", "What a Request Needs",
  "What We Redact and What We Keep", "The Durable Fix: Redaction at the Source" and "How to Contact Us" are all
  present; "What a Request Needs" lists the name, the zip code and the law; "How to Contact Us" gives a way to
  send a request (on the live site, an email link); the form link opens the State Board of Elections site
  (elections.il.gov).
- **Fail:** No footer link; error page; a missing section; no way to contact the site; a broken form link.

## Run log

| Date | Site | Tests run | Result | Notes |
|---|---|---|---|---|
