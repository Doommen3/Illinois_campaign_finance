# OpenBook Illinois Comptroller Crawl Plan

## Portal Overview

The Illinois Comptroller OpenBook portal (<https://openbook.illinoiscomptroller.gov/>) provides a searchable database of state contracts and campaign contributions. It links Comptroller accounting data with Illinois State Board of Elections (ISBE) campaign finance reports.

**Architecture**: ColdFusion server-rendered (CFML), hosted on IIS/ASP.NET. JSESSIONID cookie required. No public API; form-based search flow.

### Endpoints Discovered

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/index.cfm` | GET | Landing page (redirects from `/search.cfm`) |
| `/search.cfm` | POST | Form submission target for all search types |
| `/cfcs/autosuggest.cfc?method=getVendors&returnformat=json&term=X` | GET | Vendor autocomplete (min 3 chars) |
| `/cfcs/autosuggest.cfc?method=getContributors&returnformat=json&term=X` | GET | Contributor autocomplete (currently returning empty) |
| `/cfcs/autosuggest.cfc?method=getRecipients&returnformat=json&term=X` | GET | Recipient autocomplete (currently returning empty) |
| `office.illinoiscomptroller.gov/.../Vendor_Warrant_Listing_Dates_Amounts.cfm` | GET | Contract detail (warrant/payment list) |

### Key Findings

1. **Contracts data is fully populated** and returns all rows in a single HTML table (no server-side pagination). Example: "COMCAST" returns 173 contracts across 22 vendor name variants.
2. **Contributor/Recipient autocomplete endpoints currently return empty arrays** for all tested queries. The "Contributed By" and "Employees Of" sub-tabs consistently show 0 results. This data may be stale or no longer maintained.
3. **Contract detail pages** are on a separate host (`office.illinoiscomptroller.gov`) and show warrant/payment records per contract (Issue Date, Payment Amount).
4. **All data loads in a single page** — no pagination needed for the HTML table extraction, even for vendors with 100+ contracts.

---

## 1. Seed Strategy

### Primary Seeds: Top Vendors by Expenditure Spend

```sql
SELECT
    UPPER(TRIM(vendor_name)) AS norm_vendor,
    SUM(amount) AS total_spend,
    COUNT(*) AS entry_count
FROM d2_itemized_entries
WHERE entry_type = 'expenditure'
  AND vendor_name IS NOT NULL
  AND TRIM(vendor_name) != ''
GROUP BY UPPER(TRIM(vendor_name))
ORDER BY SUM(amount) DESC
LIMIT 500
```

### Secondary Seeds (Optional Toggles)

| Source | SQL | Toggle Flag |
|--------|-----|-------------|
| Lobbying entities | `SELECT entity_name FROM lobbying_entities` | `--include-lobbying` |
| Chicago contracts | `SELECT DISTINCT vendor_name FROM chicago_contracts_raw WHERE vendor_name IS NOT NULL` | `--include-chicago` |
| FEC disbursements | `SELECT DISTINCT recipient_name FROM fec_schedule_b_disbursements WHERE recipient_state = 'IL'` | `--include-fec` |

### Vendor Resolution

For each seed string:
1. Query getVendors autocomplete with the seed text (or first 3+ chars).
2. From the returned `{id, value}` array:
   - **Exact match**: seed text == vendor `id` (case-insensitive, ignoring trailing whitespace).
   - **Prefix match**: vendor `id` starts with seed text.
   - **Best fuzzy**: Jaccard similarity between seed tokens and vendor label tokens (reuse existing `cross_matching.py` Jaccard).
   - **Pick-first**: Use the first result (lowest-confidence fallback; enabled via `--pick-first`).
3. Record `match_method` and `confidence` (1.0 for exact, 0.9 for prefix, Jaccard score for fuzzy, 0.5 for pick-first).

### Expected Volume

**Pilot (N=50 vendors)**:
- Vendor autocomplete calls: 50
- Avg contracts per vendor: ~10-50 (based on COMCAST=173 being on the high end)
- Estimated contract rows: 500-2,500
- Contribution rows: likely 0 (based on current portal state)
- Contract detail pages: up to 2,500 (if we scrape warrants)

**Initial Production (N=500 vendors)**:
- Vendor autocomplete calls: 500
- Estimated contract rows: 5,000-25,000
- Contract detail/warrant rows: 10,000-50,000
- "Big vendors" (>100 contracts): ~5-10 vendors (telecommunications, utilities, large IT vendors)

**Measurement plan**: After pilot, compute actual averages and extrapolate. Log per-vendor counts in `openbook_scrape_runs`.

---

## 2. What to Scrape Per Vendor

### Step 1: Vendor Resolution
- Call getVendors autocomplete API with vendor name.
- Select best match and persist in `openbook_vendor_match`.

### Step 2: Contract Search
- POST to `search.cfm` with form fields:
  - `txtVendorName=<vendor_label>`
  - `selVendorId=<vendor_id>`
  - `hdnActiveTab=contracts`
  - `hdnMainActiveTab=contracts`
  - `hdnSubTabClick=0`
  - `hdnPage=index`
- Parse the contracts HTML table (`#exportPDF`). Columns:
  | Column | Selector | Notes |
  |--------|----------|-------|
  | Vendor Name | `td:nth-child(1)` | Whitespace-padded |
  | Fiscal Year | `td:nth-child(2)` | 4-digit year |
  | Contract Number | `td:nth-child(3)` | Displayed as "Agency# / ContractNum"; link contains detail URL params |
  | Agency | `td:nth-child(4)` | Whitespace-padded |
  | Contract Amount | `td:nth-child(5)` | Currency format: $1,234.56 |

- Extract detail URL parameters from the `onclick` attribute:
  ```
  window.open('https://office.illinoiscomptroller.gov/get/Enhanced_Ledger_By_vendor/
    Vendor_Warrant_Listing_Dates_Amounts.cfm?Vendor_Name=...&Agency=...&Contract_Number=...&Fiscal_Year=...')
  ```

### Step 3: Contract Details (Optional, Enabled via --with-details)
- For each contract with a detail link, fetch the warrant page from `office.illinoiscomptroller.gov`.
- Parse the warrant table: **Issue Date**, **Payment Amount**.
- Store as supplementary data linked to the contract.

### Step 4: Contribution/Employee Sub-tabs
- POST with `hdnActiveTab=contributions`, `hdnSubTabClick=1`, `hdnPage=search`.
- Parse "Contributed By" table: **Contributed By, Received By, Employer, Date, Amount**.
- POST with `hdnActiveTab=employees`, `hdnSubTabClick=1`.
- Parse "Employees Of" table: same columns.
- Note: As of 2026-02, these tabs return 0 results for all tested vendors. The scraper should gracefully handle empty results and log the count.

### Politeness & Rate Limiting

- **Concurrency**: 1 (sequential requests; single Playwright browser context).
- **Delay**: 1-2 seconds between requests (configurable via existing `RateLimiter`).
- **Backoff**: Exponential on 5xx/timeout (2x multiplier, max 60s).
- **Hard cap**: `--max-contract-pages 5` (currently moot since all contracts load in one page, but future-proofing).
- **Resumable state**: Track which vendors have been scraped in `openbook_scrape_runs` and `openbook_vendor_match`.
- **Respect**: No brute-force enumeration; only query for our known vendor seeds.

---

## 3. Schema Design

### Tables

```sql
-- Seed vendors from our existing data
CREATE TABLE IF NOT EXISTS openbook_vendor_seed (
    seed_id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_text TEXT NOT NULL,
    seed_source TEXT NOT NULL,  -- 'expenditures', 'lobbying', 'chicago', 'fec'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(seed_text, seed_source)
);

-- Matched OpenBook vendor identities
CREATE TABLE IF NOT EXISTS openbook_vendor_match (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_id INTEGER NOT NULL REFERENCES openbook_vendor_seed(seed_id),
    openbook_vendor_key TEXT NOT NULL,
    openbook_vendor_label TEXT NOT NULL,
    match_method TEXT NOT NULL,   -- 'exact', 'prefix', 'fuzzy', 'pick_first'
    confidence REAL NOT NULL,     -- 0.0-1.0
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(seed_id, openbook_vendor_key)
);

-- Raw contract rows from OpenBook
CREATE TABLE IF NOT EXISTS openbook_contracts_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    openbook_vendor_key TEXT NOT NULL,
    vendor_label TEXT NOT NULL,
    fiscal_year INTEGER,
    agency_code TEXT,
    agency_name TEXT,
    contract_number TEXT,
    award_amount REAL,
    detail_url TEXT,
    source_url TEXT NOT NULL,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_hash TEXT NOT NULL,
    UNIQUE(openbook_vendor_key, contract_number, fiscal_year)
);

-- Raw contribution rows from OpenBook (if any become available)
CREATE TABLE IF NOT EXISTS openbook_contributions_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    openbook_vendor_key TEXT NOT NULL,
    contributor_name TEXT,
    contributor_first_name TEXT,
    recipient_name TEXT,
    employer TEXT,
    contribution_date TEXT,
    amount REAL,
    source_url TEXT NOT NULL,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_hash TEXT NOT NULL,
    UNIQUE(row_hash)
);

-- Scrape run metadata
CREATE TABLE IF NOT EXISTS openbook_scrape_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    mode TEXT NOT NULL,           -- 'vendor_poc', 'targeted_batch'
    seed_count INTEGER DEFAULT 0,
    match_count INTEGER DEFAULT 0,
    contract_rows INTEGER DEFAULT 0,
    contribution_rows INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    notes TEXT
);
```

### Dedupe Rules

| Table | Dedupe Key | Method |
|-------|-----------|--------|
| `openbook_contracts_raw` | `UNIQUE(openbook_vendor_key, contract_number, fiscal_year)` | Natural key from data |
| `openbook_contributions_raw` | `UNIQUE(row_hash)` where hash = sha1(contributor + recipient + date + amount + vendor_key) | Content hash |
| `openbook_vendor_seed` | `UNIQUE(seed_text, seed_source)` | Natural key |
| `openbook_vendor_match` | `UNIQUE(seed_id, openbook_vendor_key)` | Natural key |

### Raw HTML Storage

Store in existing `raw_extractions` table:
- `source_type`: `openbook_contracts_search`, `openbook_contract_detail`, `openbook_contributions_tab`, `openbook_employees_tab`
- `source_identifier`: `{vendor_key}:{fiscal_year_filter}:{run_id}` or `{vendor_key}:detail:{contract_number}:{fiscal_year}`
- `payload_json`: Extracted table data as JSON (not raw HTML, to avoid bloat)

---

## 4. HTTP-Only Fast Path (Future Optimization)

The POC uses Playwright for reliability. Based on our curl testing, the following HTTP-only flow works:

1. GET `/index.cfm` to establish JSESSIONID cookie.
2. GET `/cfcs/autosuggest.cfc?method=getVendors&returnformat=json&term=X` for vendor resolution (no cookie needed).
3. POST `/search.cfm` with form data (requires JSESSIONID) to get contracts page.
4. POST `/search.cfm` with `hdnSubTabClick=1` to switch sub-tabs.

This can replace Playwright for 90% of operations once the POC is validated.

---

## 5. Next Steps: Targeted Batch Mode

After the single-vendor POC:

1. **Seed generation CLI**: `python run.py generate-openbook-seeds --top-n 500 --include-lobbying --include-chicago`
2. **Batch scraper**: `python run.py import-openbook-batch --seed-source expenditures --limit 50 --resume`
3. **Scheduling**: Cron or systemd timer for incremental refresh (weekly).
4. **Cross-matching**: Match `openbook_contracts_raw.vendor_label` against `d2_itemized_entries.vendor_name` and `lobbying_entities.entity_name` using Jaccard.
5. **Analytics**: Add OpenBook contract totals to vendor profile pages; create "state contracts by donor" analytical views.
6. **Warrant detail enrichment**: Optionally scrape contract detail pages for payment-level data.
