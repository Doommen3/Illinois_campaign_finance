-- Illinois Campaign Finance Database Schema

-- Committees (candidates/organizations filing reports)
CREATE TABLE IF NOT EXISTS committees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    detail_url TEXT,
    source_identifier TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_committees_name ON committees(name);
CREATE INDEX IF NOT EXISTS idx_committees_source_identifier ON committees(source_identifier);

-- Reports (individual filings from main list page)
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    committee_id INTEGER NOT NULL,
    report_type TEXT,
    reporting_period TEXT,
    filed_date TEXT,
    pages INTEGER,
    clarification TEXT,
    detail_url TEXT,
    source_identifier TEXT,
    is_paper_filed BOOLEAN DEFAULT FALSE,
    scrape_status TEXT DEFAULT 'pending',  -- pending, scraped, skipped, error
    scrape_error TEXT,
    source_page INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (committee_id) REFERENCES committees(id)
);

CREATE INDEX IF NOT EXISTS idx_reports_committee_id ON reports(committee_id);
CREATE INDEX IF NOT EXISTS idx_reports_scrape_status ON reports(scrape_status);
CREATE INDEX IF NOT EXISTS idx_reports_filed_date ON reports(filed_date);
CREATE INDEX IF NOT EXISTS idx_reports_is_paper_filed ON reports(is_paper_filed);
CREATE INDEX IF NOT EXISTS idx_reports_source_identifier ON reports(source_identifier);
CREATE INDEX IF NOT EXISTS idx_reports_detail_url ON reports(detail_url);

-- D-2 quarterly reports and itemized data
CREATE TABLE IF NOT EXISTS d2_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    committee_id INTEGER NOT NULL,
    report_type TEXT,
    reporting_period TEXT,
    filed_date TEXT,
    pages INTEGER,
    clarification TEXT,
    detail_url TEXT,
    source_identifier TEXT UNIQUE,
    summary_json TEXT,
    detail_scrape_status TEXT DEFAULT 'pending', -- pending, scraped, error
    detail_scrape_error TEXT,
    itemized_scrape_status TEXT DEFAULT 'pending', -- pending, scraped, error, no_itemized
    itemized_scrape_error TEXT,
    source_page INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (committee_id) REFERENCES committees(id)
);

CREATE INDEX IF NOT EXISTS idx_d2_reports_committee_id ON d2_reports(committee_id);
CREATE INDEX IF NOT EXISTS idx_d2_reports_filed_date ON d2_reports(filed_date);
CREATE INDEX IF NOT EXISTS idx_d2_reports_detail_status ON d2_reports(detail_scrape_status);
CREATE INDEX IF NOT EXISTS idx_d2_reports_itemized_status ON d2_reports(itemized_scrape_status);

CREATE TABLE IF NOT EXISTS d2_itemized_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    d2_report_id INTEGER NOT NULL,
    label TEXT,
    itemized_type TEXT, -- contribution, expenditure, other
    url TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    status TEXT DEFAULT 'pending', -- pending, scraped, error
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(d2_report_id, source_identifier),
    FOREIGN KEY (d2_report_id) REFERENCES d2_reports(id)
);

CREATE INDEX IF NOT EXISTS idx_d2_itemized_links_report_id ON d2_itemized_links(d2_report_id);
CREATE INDEX IF NOT EXISTS idx_d2_itemized_links_status ON d2_itemized_links(status);

CREATE TABLE IF NOT EXISTS d2_itemized_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    d2_report_id INTEGER NOT NULL,
    itemized_link_id INTEGER NOT NULL,
    source_page INTEGER,
    source_row INTEGER,
    row_hash TEXT NOT NULL,
    entry_type TEXT, -- contribution, expenditure
    contributed_by TEXT,
    received_by TEXT,
    address TEXT,
    amount REAL,
    description TEXT,
    vendor_name TEXT,
    vendor_address TEXT,
    expended_by TEXT,
    purpose_beneficiary TEXT,
    candidate_name TEXT,
    office_district TEXT,
    supporting_opposing TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(itemized_link_id, row_hash),
    FOREIGN KEY (d2_report_id) REFERENCES d2_reports(id),
    FOREIGN KEY (itemized_link_id) REFERENCES d2_itemized_links(id)
);

CREATE INDEX IF NOT EXISTS idx_d2_itemized_entries_report_id ON d2_itemized_entries(d2_report_id);
CREATE INDEX IF NOT EXISTS idx_d2_itemized_entries_itemized_link_id ON d2_itemized_entries(itemized_link_id);
CREATE INDEX IF NOT EXISTS idx_d2_itemized_entries_amount ON d2_itemized_entries(amount);

-- Donors (unique by normalized name + address)
CREATE TABLE IF NOT EXISTS donors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    occupation TEXT,
    employer TEXT,
    normalized_name TEXT NOT NULL,
    normalized_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_donors_normalized ON donors(normalized_name, normalized_address);
CREATE INDEX IF NOT EXISTS idx_donors_name ON donors(name);

-- Contributions (from detail pages)
CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    donor_id INTEGER NOT NULL,
    amount REAL,
    transaction_date TEXT,
    received_by TEXT,
    description TEXT,
    vendor_name TEXT,
    vendor_address TEXT,
    raw_contributed_by TEXT,
    raw_address TEXT,
    raw_occupation TEXT,
    raw_employer TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(id),
    FOREIGN KEY (donor_id) REFERENCES donors(id)
);

CREATE INDEX IF NOT EXISTS idx_contributions_report_id ON contributions(report_id);
CREATE INDEX IF NOT EXISTS idx_contributions_donor_id ON contributions(donor_id);
CREATE INDEX IF NOT EXISTS idx_contributions_amount ON contributions(amount);
CREATE INDEX IF NOT EXISTS idx_contributions_transaction_date ON contributions(transaction_date);

-- Raw extraction staging for parser reprocessing and audits
CREATE TABLE IF NOT EXISTS raw_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,            -- main_list_row, a1_contribution_row, etc
    source_identifier TEXT NOT NULL,      -- stable identifier for dedupe
    source_url TEXT,
    parser_version TEXT,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, source_identifier)
);

CREATE INDEX IF NOT EXISTS idx_raw_extractions_source_type ON raw_extractions(source_type);

-- Scrape state (for resumability)
CREATE TABLE IF NOT EXISTS scrape_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_type TEXT NOT NULL,  -- 'main_list' or 'details'
    last_page INTEGER,
    last_report_id INTEGER,
    total_pages INTEGER,
    status TEXT DEFAULT 'pending',  -- pending, in_progress, completed, error
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scrape_state_type ON scrape_state(scrape_type);
CREATE INDEX IF NOT EXISTS idx_scrape_state_status ON scrape_state(status);

-- Manual entry queue (for paper-filed reports)
CREATE TABLE IF NOT EXISTS manual_entry_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL UNIQUE,
    priority INTEGER DEFAULT 0,
    notes TEXT,
    status TEXT DEFAULT 'pending',  -- pending, in_progress, completed
    assigned_to TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(id)
);

CREATE INDEX IF NOT EXISTS idx_manual_entry_queue_status ON manual_entry_queue(status);
CREATE INDEX IF NOT EXISTS idx_manual_entry_queue_priority ON manual_entry_queue(priority DESC);

-- App users (for protected manual entry access)
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_app_users_username ON app_users(username);

-- Materialized analytics aggregates + snapshot cache
CREATE TABLE IF NOT EXISTS analytics_donor_committee_agg (
    source TEXT NOT NULL, -- bulk_receipts, contributions
    donor_key TEXT NOT NULL,
    donor_name TEXT NOT NULL,
    donor_address TEXT,
    donor_city TEXT,
    donor_state TEXT,
    occupation TEXT,
    employer TEXT,
    committee_id TEXT NOT NULL,
    committee_name TEXT NOT NULL,
    total_amount REAL NOT NULL,
    contribution_count INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, donor_key, committee_id)
);

CREATE INDEX IF NOT EXISTS idx_analytics_donor_committee_source_amount
    ON analytics_donor_committee_agg(source, total_amount DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_donor_committee_committee
    ON analytics_donor_committee_agg(source, committee_name);

CREATE TABLE IF NOT EXISTS analytics_committee_monthly_totals (
    source TEXT NOT NULL, -- bulk_receipts, contributions
    committee_name TEXT NOT NULL,
    month_key TEXT NOT NULL, -- YYYY-MM
    month_total REAL NOT NULL,
    contribution_count INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, committee_name, month_key)
);

CREATE INDEX IF NOT EXISTS idx_analytics_monthly_source_month
    ON analytics_committee_monthly_totals(source, month_key);

CREATE TABLE IF NOT EXISTS analytics_large_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, -- bulk_receipts, contributions
    committee_name TEXT NOT NULL,
    donor_name TEXT,
    event_date TEXT,
    amount REAL NOT NULL,
    large_threshold REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analytics_large_source_amount
    ON analytics_large_contributions(source, amount DESC);

CREATE TABLE IF NOT EXISTS analytics_materialized_meta (
    source TEXT PRIMARY KEY, -- bulk_receipts, contributions
    donor_row_count INTEGER NOT NULL DEFAULT 0,
    monthly_row_count INTEGER NOT NULL DEFAULT 0,
    large_row_count INTEGER NOT NULL DEFAULT 0,
    large_threshold REAL NOT NULL DEFAULT 0,
    materialization_version INTEGER NOT NULL DEFAULT 1,
    materialization_notes TEXT,
    refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    cache_key TEXT PRIMARY KEY,
    snapshot_type TEXT NOT NULL, -- dashboard_full
    params_json TEXT NOT NULL,
    payload_json TEXT,
    status TEXT NOT NULL DEFAULT 'empty', -- empty, in_progress, completed, error
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_type_status
    ON analytics_snapshots(snapshot_type, status, completed_at);

CREATE TABLE IF NOT EXISTS analytics_donor_summary (
    source TEXT NOT NULL, -- bulk_receipts, contributions
    donor_key TEXT NOT NULL,
    local_donor_id INTEGER,
    donor_name TEXT NOT NULL,
    donor_address TEXT,
    donor_city TEXT,
    donor_state TEXT,
    occupation TEXT,
    employer TEXT,
    total_amount REAL NOT NULL,
    contribution_count INTEGER NOT NULL,
    committee_count INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, donor_key)
);

CREATE INDEX IF NOT EXISTS idx_analytics_donor_summary_source_amount
    ON analytics_donor_summary(source, total_amount DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_donor_summary_source_name
    ON analytics_donor_summary(source, donor_name);

-- Federal Elections Commission (FEC) candidate and contribution ingestion
CREATE TABLE IF NOT EXISTS fec_il_candidate_seed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key TEXT NOT NULL UNIQUE,
    as_of_date TEXT,
    cycle INTEGER,
    office TEXT,
    office_code TEXT,
    district TEXT,
    district_code TEXT,
    party TEXT,
    party_code TEXT,
    election_stage TEXT,
    candidate_name TEXT NOT NULL,
    normalized_candidate_name TEXT NOT NULL,
    write_in INTEGER,
    already_listed_general INTEGER,
    source_file TEXT,
    source_row_number INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fec_seed_cycle ON fec_il_candidate_seed(cycle);
CREATE INDEX IF NOT EXISTS idx_fec_seed_name ON fec_il_candidate_seed(normalized_candidate_name);

CREATE TABLE IF NOT EXISTS fec_candidate_match (
    seed_candidate_key TEXT PRIMARY KEY,
    candidate_name TEXT NOT NULL,
    office TEXT,
    office_code TEXT,
    district TEXT,
    district_code TEXT,
    party TEXT,
    party_code TEXT,
    election_stage TEXT,
    cycle INTEGER,
    fec_candidate_id TEXT,
    fec_name TEXT,
    fec_office TEXT,
    fec_state TEXT,
    fec_district TEXT,
    fec_party TEXT,
    match_status TEXT NOT NULL DEFAULT 'unmatched', -- matched, unmatched, ambiguous
    match_score REAL,
    match_method TEXT,
    candidate_payload_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(seed_candidate_key) REFERENCES fec_il_candidate_seed(candidate_key)
);

CREATE INDEX IF NOT EXISTS idx_fec_match_candidate_id ON fec_candidate_match(fec_candidate_id);
CREATE INDEX IF NOT EXISTS idx_fec_match_status ON fec_candidate_match(match_status);

CREATE TABLE IF NOT EXISTS fec_candidate_committees (
    candidate_id TEXT NOT NULL,
    committee_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    committee_name TEXT,
    committee_type TEXT,
    committee_designation TEXT,
    committee_designation_full TEXT,
    filing_frequency TEXT,
    committee_party TEXT,
    committee_city TEXT,
    committee_state TEXT,
    committee_zip TEXT,
    is_principal INTEGER NOT NULL DEFAULT 0,
    source_payload_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(candidate_id, committee_id, cycle)
);

CREATE INDEX IF NOT EXISTS idx_fec_committees_candidate ON fec_candidate_committees(candidate_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_committees_committee ON fec_candidate_committees(committee_id);

CREATE TABLE IF NOT EXISTS fec_schedule_a_contributions (
    sub_id TEXT PRIMARY KEY,
    cycle INTEGER NOT NULL,
    candidate_id TEXT,
    candidate_name TEXT,
    committee_id TEXT,
    committee_name TEXT,
    contributor_name TEXT,
    contributor_city TEXT,
    contributor_state TEXT,
    contributor_zip TEXT,
    contributor_employer TEXT,
    contributor_occupation TEXT,
    contributor_id TEXT,
    is_individual INTEGER,
    line_number TEXT,
    receipt_type TEXT,
    receipt_type_desc TEXT,
    memo_text TEXT,
    contribution_receipt_amount REAL,
    contribution_receipt_date TEXT,
    two_year_transaction_period INTEGER,
    donor_key TEXT,
    donor_entity_key TEXT,
    donor_entity_method TEXT,
    load_date TEXT,
    image_number TEXT,
    api_source_identifier TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fec_schedule_candidate ON fec_schedule_a_contributions(candidate_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_committee ON fec_schedule_a_contributions(committee_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_date ON fec_schedule_a_contributions(contribution_receipt_date);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_donor_key ON fec_schedule_a_contributions(donor_key);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_donor_entity_key ON fec_schedule_a_contributions(donor_entity_key);
