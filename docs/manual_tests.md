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
| MT-006 | Search | Site search finds a committee by name and links to its profile | 2026-10-29 | ⭐ |
| MT-007 | Candidates | Candidates hub and the state candidate list find a candidate | 2026-10-29 |  |
| MT-008 | Donors | Donors list defaults to the 2026 cycle and its top row matches the donor's page | 2026-10-29 | ⭐ |
| MT-009 | Analytics | Analytics dashboard loads with Top State Races | 2026-10-29 |  |
| MT-010 | Analytics | State races index and a race detail agree on donors, contributions and total | 2026-10-29 | ⭐ |
| MT-011 | Analytics | State geo drill-down for Illinois: totals and the sum check | 2026-10-29 |  |
| MT-012 | Analytics | Networks and Relationships pages draw their graphs | 2026-10-29 |  |
| MT-013 | Federal | Federal finance overview: window, stat cards, Top Federal Races | 2026-10-29 | ⭐ |
| MT-014 | Federal | Federal geo drill-down for Illinois: totals and the sum check | 2026-10-29 |  |
| MT-015 | Federal | Federal race outside spending (IL-01) stays inside the window | 2026-10-29 |  |
| MT-016 | Lobbying | Lobbying list snaps the window to registration years | 2026-10-29 |  |
| MT-017 | 527 | 527 list and an organization's page: summary agrees with its sections | 2026-10-29 |  |
| MT-018 | 527 | Dark money tracker loads with its funding sources | 2026-10-29 |  |
| MT-019 | OpenBook | OpenBook vendor list row agrees with the vendor's page | 2026-10-29 |  |
| MT-020 | Tools | Compare, Investigate and Person Intelligence pages render | 2026-10-29 |  |
| MT-021 | Home | About page renders its sections | 2026-10-29 |  |
| MT-022 | Home | "Find unusual patterns this month" opens the latest month of data | 2026-10-29 | ⭐ |
| MT-023 | Freshness | Data Status box: same dates on every page, agrees with the window's "latest data" | 2026-10-29 | ⭐ |
| MT-024 | Network tab | Network tab click drill-down panel (money flow and vendor network) | 2026-10-29 | ⭐ |

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
  links that go nowhere. Known failing on local data 2026-10-29: the top donor's page omits its $1.55M receipt to
  this committee; root cause under item 10 batch 1 (docs/audits/data_integrity_batch1_donors_2026-10-29.md).
- **Revised:** 2026-10-29 — known-failing note added (step 5).
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

### MT-006 — Site search finds a committee

- **Area:** Search
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** Search is how most readers start; it guards against empty results for an exact committee name and
  result links that go nowhere.
- **Steps:**
  1. Open `/search?q=Democratic%20Party%20of%20Illinois`.
  2. Read the window and the result section headings.
  3. In "Committees (N)", click "Democratic Party of Illinois".
- **Pass:** The page heading is "Search" and the window reads "2026 Cycle"; the search box still holds
  "Democratic Party of Illinois"; a "Committees (N)" section with N ≥ 1 lists "Democratic Party of Illinois"
  (type "Political Party (Democratic)", Chicago, IL); the click lands on the committee profile with the heading
  "Democratic Party of Illinois" and "SBE Committee ID: 6239".
- **Fail:** Error page; no "Committees" section or N = 0; the committee missing from the list; the link lands on
  another committee or an error.

### MT-007 — Candidates hub and state candidate list

- **Area:** Candidates
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** Guards the route from the "Candidates" menu to a candidate profile.
- **Steps:**
  1. Open `/candidates`.
  2. Read the "State Candidates (ISBE)" and "Federal Candidates (FEC)" cards.
  3. Click "Explore State Candidates" (lands on `/candidate-finance/`).
  4. Type "Frerichs" in the "Search candidate, committee, or office" box and submit.
  5. Click "Michael W Frerichs".
- **Pass:** Step 2: both cards show numbers (Candidates, Committees, Total Receipts, Total Expenditures for the
  state card; Candidates and Contributions for the federal card) and each card's "Latest ... date" is a date.
  Step 3: the page heading is "Candidate Committee Finance" and the window reads "2026 Cycle". Step 4: the list
  shows Michael W Frerichs. Step 5: the profile `/candidates/13505` opens with the heading "Michael W Frerichs" and
  the window still "2026 Cycle".
- **Fail:** Error page; a card value that is blank, "None" or "N/A"; Frerichs missing from the search results;
  the link opens a different candidate.

### MT-008 — Donors list and the top donor's page

- **Area:** Donors
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** `refresh-analytics` has run since the last ISBE import (the list reads the per-cycle summary
  table).
- **Why:** The donors list reads a pre-computed per-cycle table while the donor page reads live rows; this guards
  against the two drifting apart.
- **Steps:**
  1. Open `/donors/`. Read the window and the line "N total donors".
  2. Note the first row's "Total Contributed", "Contributions" and "Committees".
  3. Click the first row's name.
  4. On the donor page read "Total Contributed", "This cycle", "Number of Contributions" and
     "Committees Supported".
- **Pass:** Step 1: the window reads "2026 Cycle", "Jan 1, 2025 – <latest ISBE data date>", and N is a number
  above zero. Step 4: the donor page's window still reads "2026 Cycle"; its "Total Contributed" equals the list's
  "Total Contributed", "Number of Contributions" equals "Contributions" and "Committees Supported" equals
  "Committees"; "This cycle" equals "Total Contributed".
- **Fail:** Error page; an empty list; the window is not "2026 Cycle"; any of the three figures differ between
  the list and the donor page.

### MT-009 — Analytics dashboard with Top State Races

- **Area:** Analytics
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** The analytics caches are warm (the first load after a restart can take a minute or more).
- **Why:** The dashboard is the heaviest page; it guards against a timeout or error page and against a missing
  "Top State Races" table (which needs the `isbe_race_candidacies` view).
- **Steps:**
  1. Open `/analytics/`. If it errors on the first load after a restart, wait one minute and reload once.
  2. Read the heading, the window and the section headings.
  3. In "Top State Races", note the first race's "Total Amount" and click the race name.
- **Pass:** The heading is "Analytics Overview" and the window reads "2026 Cycle"; the sections "Top State Races",
  "Trend Visuals" and "Time-Series Preview" are present; "Top State Races" lists races with dollar amounts and is
  sorted by "Total Amount" (largest first); the click opens "State Race Detail" with the same "Total Amount".
- **Fail:** Error page or "Something Went Wrong" after the one reload; "Top State Races" missing or showing
  "not available"; amounts out of order; the race detail shows a different total.

### MT-010 — State races index and race detail agree

- **Area:** Analytics
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** Guards the race totals against the index and the detail page computing them differently.
- **Steps:**
  1. Open `/analytics/state-races/`. Read the window and the line "N of M races have activity".
  2. Note the "Governor - Statewide Illinois" row: "Donors", "Contributions" and "Total Amount".
  3. Click "Governor - Statewide Illinois" (lands on `/analytics/state-races/governor-statewide-illinois?period=cycle:2026`).
  4. Read the stat cards "Donors", "Contributions" and "Total Amount", and the tab row.
  5. Click the "Donors" tab.
- **Pass:** Step 1: the heading is "All State Races", the window reads "2026 Cycle" and N ≤ M. Step 4: the
  heading is "State Race Detail", the window still reads "2026 Cycle", and the three stat cards equal the row
  noted in step 2; the tabs are Overview, Networks, Risk, Donors, Geography, Relationships. Step 5: the Donors tab
  loads with the window still "2026 Cycle".
- **Fail:** Error page; any of the three figures differ between the index and the detail page; a missing tab; a
  tab that changes the window.

### MT-011 — State geo drill-down for Illinois

- **Area:** Analytics
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** The geo drill-down prints its own consistency check ("Sum check delta"); this guards against the page
  total and the row sums drifting apart.
- **Steps:**
  1. Open `/analytics/geo-drilldown?geo_type=state&geo_value=IL&period=all`.
  2. Read the heading, the window and the summary line ("Total amount", "Contributions", "Donors",
     "Detail rows").
  3. Read the second line ("Page subtotal", "Page contributions", "Sum check delta").
- **Pass:** The heading is "Geo Drilldown: IL" and the window reads "All time"; every summary figure is a number
  above zero; "Page subtotal" is no larger than "Total amount"; "Sum check delta" is under $1.00 (cents of
  rounding only); the table rows are sorted by "Total Amount", largest first.
- **Fail:** Error page; a blank or zero summary figure; "Sum check delta" of $1.00 or more; an empty table.

### MT-012 — Analytics Networks and Relationships pages

- **Area:** Analytics
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** The analytics caches are warm.
- **Why:** Guards the two site-wide graph pages against empty graphs and errors.
- **Steps:**
  1. Open `/analytics/networks`. Read the window and the "Network Graph Analytics" line ("Nodes", "Edges").
  2. Open `/analytics/relationships`. Read the "Donor Co-Giving Network" line ("Nodes", "Edges").
  3. On the Relationships page, click any node in the "Donor Co-Giving Network" graph.
- **Pass:** Step 1: the heading is "Analytics: Networks", the window reads "2026 Cycle", Nodes and Edges are
  above zero and a graph is drawn. Step 2: the heading is "Analytics: Relationships", the sections "Donor
  Co-Giving Network", "Committee Similarity Network", "Candidate Competition Networks", "Lobbying Influence
  Graph" and "527 Ecosystem Graph" are present, and Nodes and Edges are above zero. Step 3: the clicked node's
  neighbours are highlighted.
- **Fail:** Error page; Nodes or Edges of 0; an empty graph area; a missing section; clicking does nothing.

### MT-013 — Federal finance overview

- **Area:** Federal
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** A federal sync has run (`sync-fec-il-federal --refresh-cache`).
- **Why:** Guards the federal landing page: its window must end at the latest FEC data, and its stat cards and
  race table must be populated.
- **Steps:**
  1. Open `/federal-finance/`.
  2. Read the window, including "latest FEC data".
  3. Read the stat cards ("Candidates", "Races", "Network Donors", "Network Total", "Schedule B Rows",
     "Schedule E Rows").
  4. Read "Top Federal Races".
- **Pass:** The heading is "Federal Finance (FEC)"; the window reads "2026 Cycle", starts Jan 1, 2025 and ends on
  the "latest FEC data" date; every stat card is a number above zero; "Top Federal Races" lists races (such as
  "U.S. Senate - Statewide") with dollar amounts; the sections "Top Donors (Network)", "Geographic Snapshot -
  States" and "Geographic Snapshot - Cities" are present.
- **Fail:** Error page; a stat card that is 0, blank or "N/A"; an empty "Top Federal Races"; a window end date that
  differs from "latest FEC data".

### MT-014 — Federal geo drill-down for Illinois

- **Area:** Federal
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** A federal sync has run.
- **Why:** Same consistency check as MT-011 on the federal side.
- **Steps:**
  1. Open `/federal-finance/geo-drilldown?geo_type=state&geo_value=IL&cycle=2026`.
  2. Read the heading, the window and the summary line ("Total amount", "Contributions", "Donors",
     "Candidates", "Detail rows").
  3. Read "Page subtotal" and "Sum check delta".
- **Pass:** The heading is "Federal Geo Drilldown: IL" and the window reads "2026 Cycle"; every summary figure is
  a number above zero; "Page subtotal" is no larger than "Total amount"; "Sum check delta" is under $1.00; every
  row's "State" is IL.
- **Fail:** Error page; a blank or zero summary figure; "Sum check delta" of $1.00 or more; a row from another
  state.

### MT-015 — Federal race outside spending stays in the window

- **Area:** Federal
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** Schedule E has been backfilled (`backfill-fec-schedule-e`).
- **Why:** Guards the race outside-spending page against showing expenditures from outside the selected window.
  Known failing on local data 2026-10-29: the page reads "2026 Cycle" but shows "Latest Expenditure Date:
  2022-11-05" with 18 transactions against the 2022 race (reported to terminal 1, untriaged).
- **Revised:** 2026-10-29 — known-failing note added.
- **Steps:**
  1. Open `/federal-finance/races/H/01/outside-spending?cycle=2026`.
  2. Read the window and the summary ("Race", "Candidates", "Total Schedule E Amount", "Transaction Count",
     "Latest Expenditure Date").
  3. Read "Top IE Committees" and "Independent Expenditures (Schedule E)".
- **Pass:** The heading is "Race Outside Spending (E)", "Race: U.S. House - IL-01", and the window reads
  "2026 Cycle"; "Latest Expenditure Date" falls inside the window (on or after Jan 1, 2025), or the page shows no
  expenditures at all; every expenditure row's date is inside the window; the "Total Amount" values in "Top IE
  Committees" add up to "Total Schedule E Amount".
- **Fail:** Error page; a "Latest Expenditure Date" or any row dated before Jan 1, 2025 while the window reads
  "2026 Cycle"; committee totals that do not add up to the summary.

### MT-016 — Lobbying list snaps to registration years

- **Area:** Lobbying
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** A lobbying extract has been imported (`import-lobbying`).
- **Why:** Lobbying data is by registration year, so the window must widen to whole years and say so.
- **Steps:**
  1. Open `/lobbying/`. Read the window and the "Reg Year" column.
  2. Click "Last 12 months" in the window.
  3. Click the first entity name.
- **Pass:** Step 1: the heading is "IL Lobbying Entities"; the window reads "2026 Cycle", "Jan 1, 2025 – Dec 31,
  2026", "by registration year"; every "Reg Year" is 2025 or 2026. Step 2: the window reads "Last 12 months" with
  the note "shown as registration years …" naming whole years. Step 3: the entity page opens with the entity's
  name as heading and the sections "Money Destinations" and "Clients (N)".
- **Fail:** Error page; a "Reg Year" outside the window's years; no registration-year note in step 2; an
  entity link that errors.

### MT-017 — 527 list and an organization's page

- **Area:** 527
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** IRS 527 data has been imported (`import-irs527`).
- **Why:** Guards the 527 organization page's summary against its own detail sections. Known failing on local data
  2026-10-29: the organization page shows "Period: 1000 to 995" instead of two dates (an IRS 527 period-parsing
  defect; reported, untriaged). The totals do reconcile.
- **Revised:** 2026-10-29 — known-failing note added (step 3, "Period").
- **Steps:**
  1. Open `/527/`. Read the window and "N organizations with Illinois connections".
  2. Click "National Democratic Redistricting Committee" (lands on `/527/<ein>`).
  3. Read "Financial Summary" ("Total Contributions", "Total Expenditures", "Period").
  4. Read the "Top Contributors (N records, $X total)" and "Top Expenditure Recipients (N records, $X total)"
     headings.
- **Pass:** Step 1: the heading is "IRS 527 Political Organizations", the window reads "2026 Cycle" and N is above
  zero. Step 3: the window reads "All time"; "Period" shows a start date and an end date. Step 4: the
  Top Contributors $X equals "Total Contributions" and the Top Expenditure Recipients $X equals
  "Total Expenditures"; the sections "Directors / Officers", "Related Organizations" and "Filing History" are
  present.
- **Fail:** Error page; totals that differ from the section headings; a "Period" that is not two dates; a
  missing section.

### MT-018 — Dark money tracker

- **Area:** 527
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** IRS 527 data imported and cross-matching run (`run-cross-matching --only all`).
- **Why:** Guards the dark-money page's matched expenditures and funding-source summary.
- **Steps:**
  1. Open `/527/dark-money`.
  2. Read the window and the line "527 organization expenditures matched to IL committees and candidates (N
     matches)".
  3. In "527 Funding Sources", read "Total Contributed", "Unique Contributors", "Contribution Records" and the
     "Top Contributors" table.
- **Pass:** The heading is "Dark Money Tracker", the window reads "2026 Cycle", N is above zero; the three
  funding figures are numbers above zero with "Unique Contributors" ≤ "Contribution Records"; the top
  contributor's amount is no larger than "Total Contributed".
- **Fail:** Error page; N = 0; a blank funding figure; more contributors than records; a top contributor above
  the total.

### MT-019 — OpenBook vendor list and vendor page

- **Area:** OpenBook
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** OpenBook data has been imported (`import-openbook-batch`).
- **Why:** Guards the OpenBook directory's per-vendor figures against the vendor page.
- **Steps:**
  1. Open `/openbook/`. Read the stat cards ("Matched Vendors", "Contracts", "Total Award Amount").
  2. Pick a row with "Contracts" above zero; note its "Contracts" and "Total Awards".
  3. Click that vendor's name.
- **Pass:** Step 1: the heading is "OpenBook Vendor Matches" and the stat cards are numbers above zero. Step 3:
  the vendor page's heading is the vendor's name, the window reads "All time", and its "Contracts" count and
  "total awards" equal the row from step 2; the sections "Seed Provenance", "Contracts" and "Contributions" are
  present.
- **Fail:** Error page; a zero stat card; the vendor page's contract count or award total differs from the list.

### MT-020 — Tools render: Compare, Investigate, Person Intelligence

- **Area:** Tools
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** Render check for the three tool pages under the "Tools" menu.
- **Steps:**
  1. Open `/compare`.
  2. Open `/investigate`.
  3. Open `/person-intelligence`.
- **Pass:** Step 1: the heading is "Compare", the "Compare mode" choice offers "Candidate vs Candidate" and
  "Committee vs Committee", and the "Left" drop-down lists candidates. Step 2: the heading is "Start Here: Guided
  Investigation Workspace" with the three templates "Investigate a person/organization", "Trace money flow" and
  "Find unusual patterns this month". Step 3: the heading is "Person Intelligence" with a search box and a
  "Search" button.
- **Fail:** Error page on any of the three; an empty drop-down; a missing template.

### MT-021 — About page

- **Area:** Home
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** The about page explains where the data comes from and when it arrives; it must stay reachable.
- **Steps:**
  1. Open `/about`.
  2. Read the section headings.
- **Pass:** The heading is "About FollowTheMoneyIL.com"; the sections "What This Site Does", "Where the Data Comes
  From and When It Arrives", "Illinois Sunshine Attribution" and "Corrections and Questions" are present.
- **Fail:** Error page; a missing section.

### MT-022 — "Find unusual patterns this month" opens the latest month

- **Area:** Home
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** A reader who clicks "this month" expects the latest month of data. This is the known to-do item 3
  defect (the month shown lags the data); the test is meant to fail until that is fixed.
- **Steps:**
  1. Open `/`. In the "Data Status" box, note the month of the "Local receipts:" date.
  2. Under "Start Here", click "Find unusual patterns this month".
  3. Read the heading "Unusual Patterns: YYYY-MM" and the "Month" drop-down under "Advanced filters (optional)".
- **Pass:** The month in the "Unusual Patterns" heading equals the month from step 1, and the "Month" drop-down's
  first option is that month.
- **Fail:** An older month in the heading (for example 2026-06 when local receipts run to 2026-09-30); no
  heading; error page.

### MT-023 — Data Status box

- **Area:** Freshness
- **Created:** 2026-10-29
- **Source commit:** baseline
- **Precondition:** none
- **Why:** The Data Status box is the reader's only signal of how current the data is; it must read the same
  everywhere and agree with each page's window.
- **Steps:**
  1. Open `/`. Read the "Data Status" box: "Local receipts", "Federal last sync", "Federal latest report
     coverage" and the three "Federal latest ... (row-level)" dates; all but the coverage date carry
     "(N days old)".
  2. Open `/about` and `/committees/`. Read the same box.
  3. On `/committees/`, read "latest ISBE data" in the window.
- **Pass:** Every value is a date (none reads "Not loaded" or "Not available"); the values are identical on all
  three pages; each "(N days old)" equals today's date minus that date; the "latest ISBE data" date in step 3 is
  the "Local receipts" date; when any age exceeds its source's cadence, the line "At least one source appears
  stale for its expected cadence" is shown.
- **Fail:** A "Not loaded" value; the box differs between pages; a wrong day count; the window's latest ISBE date
  differs from "Local receipts"; old data without the stale notice.

### MT-024 — Network tab click drill-down

- **Area:** Network tab
- **Created:** 2026-10-29
- **Source commit:** 6674379
- **Precondition:** JavaScript enabled; the candidate has itemized donors (13505 does).
- **Why:** The drill-down panel lists the members behind a graph node (`docs/entity_networks.md`, "The drill-down
  panel"); it guards against panel sums that disagree with the node, dead member links and a panel that will not
  clear.
- **Steps:**
  1. Open `/candidates/13505/network`. The "Money flow" view is drawn by default.
  2. Hover the left-hand node "Top N individual donors" and note the amount in its tooltip.
  3. Click that node.
  4. Read the panel that appears under the graph and click one member's name, then go back.
  5. Click "See all donors" in the panel.
  6. Go back, click the same node twice, then click it once and press Escape, then click it once and click the
     empty graph background.
  7. Click "Vendor network", then click any vendor node (right-hand side).
- **Pass:** Step 4: the panel's heading is "Inside Top N individual donors"; it lists N rows with Name, Amount and
  Rows; the amounts add up to the tooltip amount from step 2; the note reads "Top N individual donors in this
  graph; M more donors not shown."; the member link opens that donor's page. Step 5: the link lands on the
  Donors tab (`/candidates/13505/donors`) with the same window as the Network tab. Step 6: each of the three
  actions (second click, Escape, background click) empties the panel and removes the highlighting. Step 7: the
  panel's heading is "Paid to <vendor>" and it lists the committee(s) that paid the vendor with amounts.
- **Fail:** No panel on click; member amounts that do not add up to the node amount; no cap note or no
  "See all donors" link while donors are capped; a dead member link; "See all donors" landing elsewhere or
  changing the window; a panel that does not clear; the vendor view's panel missing or empty.

## Run log

| Date | Site | Tests run | Result | Notes |
|---|---|---|---|---|
