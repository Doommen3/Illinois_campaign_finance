-- Illinois Campaign Finance Database Schema

-- Committees (candidates/organizations filing reports)
CREATE TABLE IF NOT EXISTS committees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    committee_id_sbe INTEGER,
    detail_url TEXT,
    source_identifier TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_committees_name ON committees(name);
CREATE INDEX IF NOT EXISTS idx_committees_committee_id_sbe ON committees(committee_id_sbe);
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
CREATE INDEX IF NOT EXISTS idx_analytics_donor_committee_source_donor
    ON analytics_donor_committee_agg(source, donor_key);

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
CREATE INDEX IF NOT EXISTS idx_analytics_donor_summary_city_state
    ON analytics_donor_summary(donor_state, donor_city);

-- Local donor entity resolution layer (confidence-scored merges)
CREATE TABLE IF NOT EXISTS donor_entity_local (
    entity_id TEXT PRIMARY KEY,
    source TEXT NOT NULL, -- usually bulk_receipts
    canonical_name TEXT NOT NULL,
    display_name TEXT,
    member_count INTEGER NOT NULL,
    total_amount REAL NOT NULL DEFAULT 0,
    confidence_score REAL NOT NULL DEFAULT 0,
    peak_confidence_score REAL NOT NULL DEFAULT 0,
    confidence_tier TEXT NOT NULL, -- high, medium, low
    merge_action TEXT NOT NULL, -- auto_merge, review, singleton
    method_version TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_donor_entity_local_source
    ON donor_entity_local(source);
CREATE INDEX IF NOT EXISTS idx_donor_entity_local_source_action
    ON donor_entity_local(source, merge_action, confidence_tier);
CREATE INDEX IF NOT EXISTS idx_donor_entity_local_source_amount
    ON donor_entity_local(source, total_amount DESC);
CREATE INDEX IF NOT EXISTS idx_donor_entity_local_source_name
    ON donor_entity_local(source, canonical_name);

CREATE TABLE IF NOT EXISTS donor_entity_local_member (
    source TEXT NOT NULL,
    donor_key TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    donor_name TEXT,
    donor_city TEXT,
    donor_state TEXT,
    donor_zip5 TEXT,
    confidence_score REAL NOT NULL DEFAULT 0,
    confidence_tier TEXT NOT NULL, -- high, medium, low
    merge_action TEXT NOT NULL, -- auto_merge, review, singleton
    total_amount REAL NOT NULL DEFAULT 0,
    contribution_count INTEGER NOT NULL DEFAULT 0,
    committee_count INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected, not_needed
    reasons_json TEXT,
    method_version TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, donor_key),
    FOREIGN KEY(entity_id) REFERENCES donor_entity_local(entity_id)
);

CREATE INDEX IF NOT EXISTS idx_donor_entity_local_member_entity
    ON donor_entity_local_member(entity_id);
CREATE INDEX IF NOT EXISTS idx_donor_entity_local_member_source_review
    ON donor_entity_local_member(source, merge_action, review_status);
CREATE INDEX IF NOT EXISTS idx_donor_entity_local_member_source_name
    ON donor_entity_local_member(source, canonical_name);
CREATE INDEX IF NOT EXISTS idx_donor_entity_local_member_source_amount
    ON donor_entity_local_member(source, total_amount DESC);

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
    last_file_date TEXT,
    first_file_date TEXT,
    party_full TEXT,
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
    amendment_indicator TEXT,
    file_number TEXT,
    transaction_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fec_schedule_candidate ON fec_schedule_a_contributions(candidate_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_committee ON fec_schedule_a_contributions(committee_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_date ON fec_schedule_a_contributions(contribution_receipt_date);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_donor_key ON fec_schedule_a_contributions(donor_key);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_donor_entity_key ON fec_schedule_a_contributions(donor_entity_key);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_a_file_number ON fec_schedule_a_contributions(file_number);

CREATE TABLE IF NOT EXISTS fec_schedule_b_disbursements (
    sub_id TEXT PRIMARY KEY,
    cycle INTEGER NOT NULL,
    candidate_id TEXT,
    candidate_name TEXT,
    committee_id TEXT,
    committee_name TEXT,
    recipient_name TEXT,
    recipient_city TEXT,
    recipient_state TEXT,
    recipient_zip TEXT,
    recipient_committee_id TEXT,
    recipient_candidate_id TEXT,
    recipient_candidate_name TEXT,
    payee_employer TEXT,
    payee_occupation TEXT,
    line_number TEXT,
    disbursement_type TEXT,
    disbursement_type_desc TEXT,
    category_code TEXT,
    category_code_full TEXT,
    election_type TEXT,
    election_type_full TEXT,
    disbursement_description TEXT,
    memo_text TEXT,
    disbursement_amount REAL,
    disbursement_date TEXT,
    two_year_transaction_period INTEGER,
    load_date TEXT,
    image_number TEXT,
    api_source_identifier TEXT,
    amendment_indicator TEXT,
    disbursement_purpose_category TEXT,
    file_number TEXT,
    transaction_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_candidate
    ON fec_schedule_b_disbursements(candidate_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_committee
    ON fec_schedule_b_disbursements(committee_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_date
    ON fec_schedule_b_disbursements(disbursement_date);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_recipient
    ON fec_schedule_b_disbursements(recipient_name);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_file_number
    ON fec_schedule_b_disbursements(file_number);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_purpose_category
    ON fec_schedule_b_disbursements(disbursement_purpose_category);

CREATE TABLE IF NOT EXISTS fec_schedule_e_independent_expenditures (
    sub_id TEXT PRIMARY KEY,
    cycle INTEGER NOT NULL,
    candidate_id TEXT,
    candidate_name TEXT,
    candidate_office TEXT,
    candidate_office_state TEXT,
    candidate_office_district TEXT,
    support_oppose_indicator TEXT,
    committee_id TEXT,
    committee_name TEXT,
    payee_name TEXT,
    payee_city TEXT,
    payee_state TEXT,
    payee_zip TEXT,
    category_code TEXT,
    category_code_full TEXT,
    election_type TEXT,
    election_type_full TEXT,
    expenditure_description TEXT,
    memo_text TEXT,
    expenditure_amount REAL,
    expenditure_date TEXT,
    filing_date TEXT,
    report_type TEXT,
    line_number TEXT,
    image_number TEXT,
    load_date TEXT,
    api_source_identifier TEXT,
    is_notice INTEGER,
    most_recent INTEGER,
    file_number TEXT,
    previous_file_number TEXT,
    amendment_indicator TEXT,
    transaction_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_candidate
    ON fec_schedule_e_independent_expenditures(candidate_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_file_number
    ON fec_schedule_e_independent_expenditures(file_number);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_most_recent
    ON fec_schedule_e_independent_expenditures(most_recent);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_committee
    ON fec_schedule_e_independent_expenditures(committee_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_date
    ON fec_schedule_e_independent_expenditures(expenditure_date);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_support_oppose
    ON fec_schedule_e_independent_expenditures(support_oppose_indicator);

CREATE TABLE IF NOT EXISTS fec_candidate_cycle_totals (
    candidate_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    receipts REAL NOT NULL DEFAULT 0,
    contributions REAL NOT NULL DEFAULT 0,
    individual_contributions REAL NOT NULL DEFAULT 0,
    disbursements REAL NOT NULL DEFAULT 0,
    coverage_start_date TEXT,
    coverage_end_date TEXT,
    transaction_coverage_date TEXT,
    last_report_year INTEGER,
    last_report_type_full TEXT,
    last_cash_on_hand_end_period REAL,
    source_payload_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (candidate_id, cycle)
);

CREATE INDEX IF NOT EXISTS idx_fec_candidate_cycle_totals_cycle
    ON fec_candidate_cycle_totals(cycle, receipts DESC);
CREATE INDEX IF NOT EXISTS idx_fec_candidate_cycle_totals_updated
    ON fec_candidate_cycle_totals(updated_at DESC);

CREATE TABLE IF NOT EXISTS fec_schedule_a_backfill_state (
    committee_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    candidate_id TEXT,
    candidate_name TEXT,
    next_last_index TEXT,
    next_last_receipt_date TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    pages_processed_total INTEGER NOT NULL DEFAULT 0,
    contributions_upserted_total INTEGER NOT NULL DEFAULT 0,
    api_calls_total INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (committee_id, cycle)
);

CREATE INDEX IF NOT EXISTS idx_fec_schedule_backfill_cycle_completed
    ON fec_schedule_a_backfill_state(cycle, completed, updated_at);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_backfill_candidate
    ON fec_schedule_a_backfill_state(candidate_id, cycle);

CREATE TABLE IF NOT EXISTS fec_schedule_b_backfill_state (
    committee_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    candidate_id TEXT,
    candidate_name TEXT,
    next_last_index TEXT,
    next_last_disbursement_date TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    pages_processed_total INTEGER NOT NULL DEFAULT 0,
    disbursements_upserted_total INTEGER NOT NULL DEFAULT 0,
    api_calls_total INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (committee_id, cycle)
);

CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_backfill_cycle_completed
    ON fec_schedule_b_backfill_state(cycle, completed, updated_at);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_backfill_candidate
    ON fec_schedule_b_backfill_state(candidate_id, cycle);

CREATE TABLE IF NOT EXISTS fec_schedule_e_backfill_state (
    candidate_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    candidate_name TEXT,
    next_last_index TEXT,
    next_last_expenditure_date TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    pages_processed_total INTEGER NOT NULL DEFAULT 0,
    expenditures_upserted_total INTEGER NOT NULL DEFAULT 0,
    api_calls_total INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (candidate_id, cycle)
);

CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_backfill_cycle_completed
    ON fec_schedule_e_backfill_state(cycle, completed, updated_at);
CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_backfill_candidate
    ON fec_schedule_e_backfill_state(candidate_id, cycle);

CREATE TABLE IF NOT EXISTS fec_transfer_source_committees (
    committee_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    committee_name TEXT,
    transfer_count INTEGER NOT NULL DEFAULT 0,
    transfer_total_amount REAL NOT NULL DEFAULT 0,
    source_candidate_count INTEGER NOT NULL DEFAULT 0,
    recipient_candidate_count INTEGER NOT NULL DEFAULT 0,
    recipient_committee_count INTEGER NOT NULL DEFAULT 0,
    latest_transfer_date TEXT,
    receipts_synced INTEGER NOT NULL DEFAULT 0,
    receipts_row_count INTEGER NOT NULL DEFAULT 0,
    receipts_total_amount REAL NOT NULL DEFAULT 0,
    receipts_coverage_start TEXT,
    receipts_coverage_end TEXT,
    next_last_index TEXT,
    next_last_receipt_date TEXT,
    receipts_pages_processed_total INTEGER NOT NULL DEFAULT 0,
    receipts_api_calls_total INTEGER NOT NULL DEFAULT 0,
    last_receipts_sync_at TIMESTAMP,
    refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (committee_id, cycle)
);

CREATE INDEX IF NOT EXISTS idx_fec_transfer_source_cycle_synced
    ON fec_transfer_source_committees(cycle, receipts_synced, transfer_total_amount DESC);
CREATE INDEX IF NOT EXISTS idx_fec_transfer_source_amount
    ON fec_transfer_source_committees(transfer_total_amount DESC, transfer_count DESC);

CREATE TABLE IF NOT EXISTS fec_local_donor_matches (
    federal_donor_entity_key TEXT NOT NULL,
    local_donor_key TEXT NOT NULL,
    primary_local_donor_key TEXT,
    federal_donor_name TEXT,
    local_donor_name TEXT,
    federal_donor_state TEXT,
    local_donor_state TEXT,
    federal_donor_zip TEXT,
    local_donor_zip TEXT,
    federal_total_amount REAL NOT NULL DEFAULT 0,
    local_total_amount REAL NOT NULL DEFAULT 0,
    federal_contribution_count INTEGER NOT NULL DEFAULT 0,
    local_contribution_count INTEGER NOT NULL DEFAULT 0,
    local_committee_count INTEGER NOT NULL DEFAULT 0,
    match_method TEXT NOT NULL,
    confidence_score REAL NOT NULL DEFAULT 0,
    local_donor_keys_json TEXT,
    refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (federal_donor_entity_key, local_donor_key)
);

CREATE INDEX IF NOT EXISTS idx_fec_local_matches_local_key
    ON fec_local_donor_matches(local_donor_key);
CREATE INDEX IF NOT EXISTS idx_fec_local_matches_confidence
    ON fec_local_donor_matches(confidence_score DESC, match_method);

-- IL Secretary of State Lobbying Data
CREATE TABLE IF NOT EXISTS lobbying_entities (
    entity_id INTEGER PRIMARY KEY,
    entity_name TEXT NOT NULL,
    reg_year INTEGER,
    address_1 TEXT,
    address_2 TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lobbying_entities_name ON lobbying_entities(entity_name);

CREATE TABLE IF NOT EXISTS lobbying_clients (
    client_id INTEGER PRIMARY KEY,
    client_name TEXT NOT NULL,
    address_1 TEXT,
    address_2 TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lobbying_clients_name ON lobbying_clients(client_name);

CREATE TABLE IF NOT EXISTS lobbying_entity_clients (
    entity_id INTEGER NOT NULL,
    client_id INTEGER NOT NULL,
    reg_year INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, client_id, reg_year),
    FOREIGN KEY (entity_id) REFERENCES lobbying_entities(entity_id),
    FOREIGN KEY (client_id) REFERENCES lobbying_clients(client_id)
);

CREATE INDEX IF NOT EXISTS idx_lobbying_entity_clients_client
    ON lobbying_entity_clients(client_id);

CREATE TABLE IF NOT EXISTS lobbying_lobbyists (
    lobbyist_id INTEGER PRIMARY KEY,
    first_name TEXT,
    middle_name TEXT,
    last_name TEXT,
    email TEXT,
    phone TEXT,
    address_1 TEXT,
    address_2 TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lobbying_lobbyists_last_first
    ON lobbying_lobbyists(last_name, first_name);

CREATE TABLE IF NOT EXISTS lobbying_lobbyist_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lobbyist_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    client_id INTEGER,
    reg_year INTEGER,
    lobbyist_status TEXT,
    client_status TEXT,
    source_file TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lobbyist_id) REFERENCES lobbying_lobbyists(lobbyist_id),
    FOREIGN KEY (entity_id) REFERENCES lobbying_entities(entity_id),
    FOREIGN KEY (client_id) REFERENCES lobbying_clients(client_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lobbying_lobbyist_regs_unique
    ON lobbying_lobbyist_registrations(
        lobbyist_id,
        entity_id,
        COALESCE(client_id, -1),
        COALESCE(reg_year, -1)
    );

CREATE INDEX IF NOT EXISTS idx_lobbying_lobbyist_regs_entity
    ON lobbying_lobbyist_registrations(entity_id);
CREATE INDEX IF NOT EXISTS idx_lobbying_lobbyist_regs_client
    ON lobbying_lobbyist_registrations(client_id);
CREATE INDEX IF NOT EXISTS idx_lobbying_lobbyist_regs_year
    ON lobbying_lobbyist_registrations(reg_year);

-- City of Chicago Open Data (Socrata) - Phase 1
CREATE TABLE IF NOT EXISTS chicago_contracts_raw (
    socrata_row_id TEXT PRIMARY KEY,
    purchase_order_contract_number TEXT,
    revision_number TEXT,
    specification_number TEXT,
    contract_type TEXT,
    start_date TEXT,
    end_date TEXT,
    approval_date TEXT,
    department TEXT,
    vendor_name TEXT,
    vendor_id TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    award_amount REAL,
    procurement_type TEXT,
    purchase_order_description TEXT,
    contract_pdf TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chicago_contracts_contract_number
    ON chicago_contracts_raw(purchase_order_contract_number);
CREATE INDEX IF NOT EXISTS idx_chicago_contracts_vendor
    ON chicago_contracts_raw(vendor_name);
CREATE INDEX IF NOT EXISTS idx_chicago_contracts_vendor_id
    ON chicago_contracts_raw(vendor_id);
CREATE INDEX IF NOT EXISTS idx_chicago_contracts_approval_date
    ON chicago_contracts_raw(approval_date);

CREATE TABLE IF NOT EXISTS chicago_payments_raw (
    socrata_row_id TEXT PRIMARY KEY,
    voucher_number TEXT,
    amount REAL,
    check_date_raw TEXT,
    check_date TEXT,
    department_name TEXT,
    contract_number TEXT,
    vendor_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chicago_payments_contract_number
    ON chicago_payments_raw(contract_number);
CREATE INDEX IF NOT EXISTS idx_chicago_payments_vendor
    ON chicago_payments_raw(vendor_name);
CREATE INDEX IF NOT EXISTS idx_chicago_payments_check_date
    ON chicago_payments_raw(check_date);

CREATE TABLE IF NOT EXISTS chicago_lobbyist_contributions_raw (
    socrata_row_id TEXT PRIMARY KEY,
    contribution_id INTEGER,
    period_start TEXT,
    period_end TEXT,
    contribution_date TEXT,
    recipient TEXT,
    amount REAL,
    lobbyist_id INTEGER,
    lobbyist_first_name TEXT,
    lobbyist_last_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chicago_lobby_contrib_lobbyist
    ON chicago_lobbyist_contributions_raw(lobbyist_id);
CREATE INDEX IF NOT EXISTS idx_chicago_lobby_contrib_recipient
    ON chicago_lobbyist_contributions_raw(recipient);
CREATE INDEX IF NOT EXISTS idx_chicago_lobby_contrib_date
    ON chicago_lobbyist_contributions_raw(contribution_date);

CREATE TABLE IF NOT EXISTS chicago_lobbying_activity_raw (
    socrata_row_id TEXT PRIMARY KEY,
    lobbying_activity_id INTEGER,
    period_start TEXT,
    period_end TEXT,
    action TEXT,
    action_sought TEXT,
    department TEXT,
    client_id INTEGER,
    client_name TEXT,
    lobbyist_id INTEGER,
    lobbyist_first_name TEXT,
    lobbyist_last_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chicago_lobby_activity_lobbyist
    ON chicago_lobbying_activity_raw(lobbyist_id);
CREATE INDEX IF NOT EXISTS idx_chicago_lobby_activity_client
    ON chicago_lobbying_activity_raw(client_id);
CREATE INDEX IF NOT EXISTS idx_chicago_lobby_activity_client_name
    ON chicago_lobbying_activity_raw(client_name);
CREATE INDEX IF NOT EXISTS idx_chicago_lobby_activity_period_start
    ON chicago_lobbying_activity_raw(period_start);

-- IRS 527 Political Organization Filings
CREATE TABLE IF NOT EXISTS irs527_organizations (
    ein TEXT NOT NULL,
    form_id INTEGER NOT NULL,
    form_id_seq INTEGER NOT NULL DEFAULT 0,
    org_name TEXT,
    address_1 TEXT,
    address_2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    zip_ext TEXT,
    email TEXT,
    formation_date TEXT,
    custodian_name TEXT,
    custodian_address_1 TEXT,
    custodian_address_2 TEXT,
    custodian_city TEXT,
    custodian_state TEXT,
    custodian_zip TEXT,
    custodian_zip_ext TEXT,
    contact_name TEXT,
    contact_address_1 TEXT,
    contact_address_2 TEXT,
    contact_city TEXT,
    contact_state TEXT,
    contact_zip TEXT,
    contact_zip_ext TEXT,
    business_address_1 TEXT,
    business_address_2 TEXT,
    business_city TEXT,
    business_state TEXT,
    business_zip TEXT,
    business_zip_ext TEXT,
    purpose TEXT,
    material_change_date TEXT,
    insert_datetime TEXT,
    related_entity_bypass INTEGER,
    eain_bypass INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ein, form_id_seq)
);

CREATE INDEX IF NOT EXISTS idx_irs527_orgs_name ON irs527_organizations(org_name);
CREATE INDEX IF NOT EXISTS idx_irs527_orgs_state ON irs527_organizations(state);
CREATE INDEX IF NOT EXISTS idx_irs527_orgs_form_id ON irs527_organizations(form_id);
CREATE INDEX IF NOT EXISTS idx_irs527_orgs_ein ON irs527_organizations(ein);

CREATE TABLE IF NOT EXISTS irs527_reports (
    form_id INTEGER NOT NULL,
    ein TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    org_name TEXT,
    org_ein TEXT,
    org_address_1 TEXT,
    org_address_2 TEXT,
    org_city TEXT,
    org_state TEXT,
    org_zip TEXT,
    org_zip_ext TEXT,
    email TEXT,
    formation_date TEXT,
    custodian_name TEXT,
    custodian_address_1 TEXT,
    custodian_address_2 TEXT,
    custodian_city TEXT,
    custodian_state TEXT,
    custodian_zip TEXT,
    custodian_zip_ext TEXT,
    contact_name TEXT,
    contact_address_1 TEXT,
    contact_address_2 TEXT,
    contact_city TEXT,
    contact_state TEXT,
    contact_zip TEXT,
    contact_zip_ext TEXT,
    business_address_1 TEXT,
    business_address_2 TEXT,
    business_city TEXT,
    business_state TEXT,
    business_zip TEXT,
    business_zip_ext TEXT,
    qtr_indicator INTEGER,
    monthly_amount_1 REAL,
    monthly_amount_2 REAL,
    monthly_amount_3 REAL,
    total_contributions REAL DEFAULT 0,
    total_expenditures REAL DEFAULT 0,
    insert_datetime TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (form_id, ein)
);

CREATE INDEX IF NOT EXISTS idx_irs527_reports_ein ON irs527_reports(ein);
CREATE INDEX IF NOT EXISTS idx_irs527_reports_period ON irs527_reports(period_start, period_end);

CREATE TABLE IF NOT EXISTS irs527_directors (
    rowid_local INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id INTEGER NOT NULL,
    ein TEXT NOT NULL,
    org_name TEXT,
    person_name TEXT,
    title TEXT,
    address_1 TEXT,
    address_2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    zip_ext TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_directors_ein ON irs527_directors(ein);
CREATE INDEX IF NOT EXISTS idx_irs527_directors_name ON irs527_directors(person_name);
CREATE INDEX IF NOT EXISTS idx_irs527_directors_state_city
    ON irs527_directors(state, city);

CREATE TABLE IF NOT EXISTS irs527_related_orgs (
    rowid_local INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id INTEGER NOT NULL,
    ein TEXT NOT NULL,
    org_name TEXT,
    related_org_name TEXT,
    relationship_type TEXT,
    address_1 TEXT,
    address_2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    zip_ext TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_related_orgs_ein ON irs527_related_orgs(ein);

CREATE TABLE IF NOT EXISTS irs527_expenditures (
    rowid_local INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id INTEGER NOT NULL,
    ein TEXT NOT NULL,
    org_name TEXT,
    recipient_name TEXT,
    recipient_address TEXT,
    recipient_address_2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    zip_ext TEXT,
    recipient_employer TEXT,
    amount REAL,
    recipient_occupation TEXT,
    date TEXT,
    purpose TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_expenditures_ein ON irs527_expenditures(ein);
CREATE INDEX IF NOT EXISTS idx_irs527_expenditures_state ON irs527_expenditures(state);
CREATE INDEX IF NOT EXISTS idx_irs527_expenditures_recipient ON irs527_expenditures(recipient_name);
CREATE INDEX IF NOT EXISTS idx_irs527_expenditures_amount ON irs527_expenditures(amount DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_expenditures_date_amount ON irs527_expenditures(date, amount DESC);

CREATE TABLE IF NOT EXISTS irs527_election_authority (
    rowid_local INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id INTEGER NOT NULL,
    election_authority_id TEXT,
    state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_election_authority_form ON irs527_election_authority(form_id);

-- Cross-matching result tables
CREATE TABLE IF NOT EXISTS lobbying_donor_matches (
    client_id INTEGER NOT NULL,
    donor_key TEXT NOT NULL,
    client_name TEXT,
    donor_name TEXT,
    score REAL NOT NULL,
    method TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (client_id, donor_key)
);

CREATE INDEX IF NOT EXISTS idx_lobbying_donor_matches_score
    ON lobbying_donor_matches(score DESC);
CREATE INDEX IF NOT EXISTS idx_lobbying_donor_matches_donor_score
    ON lobbying_donor_matches(donor_key, score DESC);
CREATE INDEX IF NOT EXISTS idx_lobbying_donor_matches_client_name
    ON lobbying_donor_matches(client_name);
CREATE INDEX IF NOT EXISTS idx_lobbying_donor_matches_client_id
    ON lobbying_donor_matches(client_id);

CREATE TABLE IF NOT EXISTS lobbying_expenditure_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    source_name TEXT,
    payee_name TEXT,
    committee_id_sbe INTEGER,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lobbying_expenditure_matches_score
    ON lobbying_expenditure_matches(score DESC);
CREATE INDEX IF NOT EXISTS idx_lobbying_exp_matches_source
    ON lobbying_expenditure_matches(source_type, source_id);

CREATE TABLE IF NOT EXISTS irs527_committee_matches (
    ein TEXT NOT NULL,
    org_name TEXT,
    committee_id_sbe INTEGER NOT NULL,
    committee_name TEXT,
    score REAL NOT NULL,
    method TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ein, committee_id_sbe)
);

CREATE INDEX IF NOT EXISTS idx_irs527_committee_matches_score
    ON irs527_committee_matches(score DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_committee_matches_ein
    ON irs527_committee_matches(ein);

CREATE TABLE IF NOT EXISTS irs527_expenditure_recipient_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT,
    org_name TEXT,
    recipient_name TEXT,
    matched_type TEXT,
    matched_id TEXT,
    matched_name TEXT,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_exp_recipient_matches_score
    ON irs527_expenditure_recipient_matches(score DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_exp_recipient_matches_ein
    ON irs527_expenditure_recipient_matches(ein);

CREATE TABLE IF NOT EXISTS irs527_director_donor_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT,
    org_name TEXT,
    director_name TEXT,
    donor_key TEXT,
    donor_name TEXT,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_director_donor_matches_score
    ON irs527_director_donor_matches(score DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_director_donor_matches_ein
    ON irs527_director_donor_matches(ein);

CREATE TABLE IF NOT EXISTS lobbying_527_matches (
    client_id INTEGER NOT NULL,
    client_name TEXT,
    ein TEXT NOT NULL,
    org_name TEXT,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (client_id, ein)
);

CREATE INDEX IF NOT EXISTS idx_lobbying_527_matches_score
    ON lobbying_527_matches(score DESC);

-- IRS 527 Contributions (Schedule A - contributions TO 527 orgs)
CREATE TABLE IF NOT EXISTS irs527_contributions (
    rowid_local INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id INTEGER NOT NULL,
    ein TEXT NOT NULL,
    org_name TEXT,
    contributor_name TEXT,
    contributor_address TEXT,
    contributor_address_2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    zip_ext TEXT,
    contributor_employer TEXT,
    amount REAL,
    contributor_occupation TEXT,
    date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_contributions_ein ON irs527_contributions(ein);
CREATE INDEX IF NOT EXISTS idx_irs527_contributions_state ON irs527_contributions(state);
CREATE INDEX IF NOT EXISTS idx_irs527_contributions_name ON irs527_contributions(contributor_name);
CREATE INDEX IF NOT EXISTS idx_irs527_contributions_amount ON irs527_contributions(amount DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_contributions_name_amount ON irs527_contributions(contributor_name, amount DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_contributions_date_amount ON irs527_contributions(date, amount DESC);

CREATE TABLE IF NOT EXISTS irs527_contribution_rollup (
    ein TEXT PRIMARY KEY,
    total_amount REAL NOT NULL DEFAULT 0,
    contribution_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_contribution_rollup_amount
    ON irs527_contribution_rollup(total_amount DESC);

CREATE TABLE IF NOT EXISTS irs527_contributor_rollup (
    contributor_name TEXT PRIMARY KEY,
    total_amount REAL NOT NULL DEFAULT 0,
    contribution_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_contributor_rollup_amount
    ON irs527_contributor_rollup(total_amount DESC);

-- Persisted donor address index for cross-matching fast path
CREATE TABLE IF NOT EXISTS cross_matching_donor_address_index (
    donor_key TEXT PRIMARY KEY,
    donor_name TEXT NOT NULL,
    donor_city TEXT,
    donor_state TEXT NOT NULL,
    donor_zip5 TEXT,
    norm_city TEXT,
    norm_state TEXT NOT NULL,
    donor_tokens TEXT
);

CREATE INDEX IF NOT EXISTS idx_cross_matching_donor_address_index_state_zip
    ON cross_matching_donor_address_index(norm_state, donor_zip5);
CREATE INDEX IF NOT EXISTS idx_cross_matching_donor_address_index_state_city
    ON cross_matching_donor_address_index(norm_state, norm_city);

-- 527 Director -> Candidate name matches
CREATE TABLE IF NOT EXISTS irs527_director_candidate_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT,
    org_name TEXT,
    director_name TEXT,
    candidate_id TEXT,
    candidate_name TEXT,
    candidate_source TEXT NOT NULL, -- 'state' or 'federal'
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_director_candidate_score
    ON irs527_director_candidate_matches(score DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_director_candidate_ein
    ON irs527_director_candidate_matches(ein);

-- 527 Director -> Donor address matches
CREATE TABLE IF NOT EXISTS irs527_director_address_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT,
    org_name TEXT,
    director_name TEXT,
    director_city TEXT,
    director_state TEXT,
    director_zip5 TEXT,
    donor_key TEXT,
    donor_name TEXT,
    donor_city TEXT,
    donor_state TEXT,
    donor_zip5 TEXT,
    address_score REAL NOT NULL,
    name_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_director_address_score
    ON irs527_director_address_matches(address_score DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_director_address_ein
    ON irs527_director_address_matches(ein);

-- 527 Organization address matches (org/custodian/contact/business addresses)
CREATE TABLE IF NOT EXISTS irs527_org_address_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT,
    org_name TEXT,
    address_type TEXT NOT NULL, -- 'org', 'custodian', 'contact', 'business'
    org_city TEXT,
    org_state TEXT,
    org_zip5 TEXT,
    matched_entity_type TEXT NOT NULL, -- 'committee', 'donor', 'candidate'
    matched_entity_id TEXT,
    matched_entity_name TEXT,
    matched_city TEXT,
    matched_state TEXT,
    matched_zip5 TEXT,
    address_score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_irs527_org_address_score
    ON irs527_org_address_matches(address_score DESC);
CREATE INDEX IF NOT EXISTS idx_irs527_org_address_ein
    ON irs527_org_address_matches(ein);

-- ============================================================
-- OpenBook Illinois Comptroller tables
-- ============================================================

-- Seed vendors from our existing data (expenditures, lobbying, etc.)
CREATE TABLE IF NOT EXISTS openbook_vendor_seed (
    seed_id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_text TEXT NOT NULL,
    seed_source TEXT NOT NULL,  -- 'expenditures', 'lobbying', 'chicago', 'fec', 'manual'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(seed_text, seed_source)
);

-- Matched OpenBook vendor identities from autosuggest API
CREATE TABLE IF NOT EXISTS openbook_vendor_match (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_id INTEGER NOT NULL REFERENCES openbook_vendor_seed(seed_id),
    openbook_vendor_key TEXT NOT NULL,
    openbook_vendor_label TEXT NOT NULL,
    match_method TEXT NOT NULL,   -- 'exact', 'prefix', 'fuzzy', 'pick_first'
    confidence REAL NOT NULL,     -- 0.0-1.0
    search_term_used TEXT,        -- which search term produced this match
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(seed_id, openbook_vendor_key)
);

CREATE INDEX IF NOT EXISTS idx_openbook_vendor_match_vendor_key
    ON openbook_vendor_match(openbook_vendor_key);

-- Raw contract rows from OpenBook search results
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

CREATE INDEX IF NOT EXISTS idx_openbook_contracts_vendor_key
    ON openbook_contracts_raw(openbook_vendor_key);
CREATE INDEX IF NOT EXISTS idx_openbook_contracts_fiscal_year
    ON openbook_contracts_raw(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_openbook_contracts_agency
    ON openbook_contracts_raw(agency_name);

-- Contract detail popup warrant rows (Issue Date / Payment Amount)
CREATE TABLE IF NOT EXISTS openbook_contract_warrants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    openbook_vendor_key TEXT NOT NULL,
    contract_number TEXT NOT NULL,
    fiscal_year INTEGER,
    issue_date TEXT,
    payment_amount REAL,
    detail_url TEXT,
    source_url TEXT NOT NULL,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_hash TEXT NOT NULL,
    UNIQUE(row_hash)
);

CREATE INDEX IF NOT EXISTS idx_openbook_contract_warrants_vendor
    ON openbook_contract_warrants(openbook_vendor_key);
CREATE INDEX IF NOT EXISTS idx_openbook_contract_warrants_contract
    ON openbook_contract_warrants(contract_number, fiscal_year);

-- Contract detail scrape status (prevents endless retries for empty popups)
CREATE TABLE IF NOT EXISTS openbook_contract_detail_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    openbook_vendor_key TEXT NOT NULL,
    contract_number TEXT NOT NULL,
    fiscal_year INTEGER,
    detail_url TEXT,
    status TEXT NOT NULL, -- has_data, no_data, error
    attempt_count INTEGER NOT NULL DEFAULT 0,
    warrant_row_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_attempted_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(openbook_vendor_key, contract_number, fiscal_year)
);

CREATE INDEX IF NOT EXISTS idx_openbook_contract_detail_status_state
    ON openbook_contract_detail_status(status, attempt_count);

-- Raw contribution rows from OpenBook (Contributed By / Employees Of tabs)
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

CREATE INDEX IF NOT EXISTS idx_openbook_contributions_vendor_key
    ON openbook_contributions_raw(openbook_vendor_key);

-- Scrape run metadata for tracking and resumability
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

-- ============================================================
-- Comptroller State Contracts tables
-- (https://illinoiscomptroller.gov/financial-reports-data/find-a-report/state-contracts)
-- ============================================================

-- Search results from Comptroller State Contracts page
CREATE TABLE IF NOT EXISTS comptroller_state_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_term TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    agency_contract_number TEXT,
    ctd_url TEXT,
    detail_url TEXT,
    payments_amount REAL,
    source_url TEXT NOT NULL,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_comptroller_contracts_vendor
    ON comptroller_state_contracts(vendor_name);
CREATE INDEX IF NOT EXISTS idx_comptroller_contracts_number
    ON comptroller_state_contracts(agency_contract_number);
CREATE INDEX IF NOT EXISTS idx_comptroller_contracts_search_term
    ON comptroller_state_contracts(search_term);

-- Contract detail data from individual contract pages
CREATE TABLE IF NOT EXISTS comptroller_contract_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agency_contract_number TEXT NOT NULL UNIQUE,
    vendor_name TEXT,
    agency TEXT,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    current_contract_amount REAL,
    award_type TEXT,
    class_subclass TEXT,
    expenditure_authority TEXT,
    source_url TEXT NOT NULL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comptroller_details_vendor
    ON comptroller_contract_details(vendor_name);

-- Scrape run metadata for tracking and resumability
CREATE TABLE IF NOT EXISTS comptroller_scrape_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    mode TEXT NOT NULL,
    vendors_searched INTEGER DEFAULT 0,
    contracts_found INTEGER DEFAULT 0,
    details_scraped INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    notes TEXT
);
