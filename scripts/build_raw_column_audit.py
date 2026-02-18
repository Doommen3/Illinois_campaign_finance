#!/usr/bin/env python3
"""Build docs/raw_column_audit.md and docs/raw_column_audit.csv from census + lineage rules."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
CENSUS_PATH = REPO_ROOT / "output/raw_schema_census.json"
OUT_MD = REPO_ROOT / "docs/raw_column_audit.md"
OUT_CSV = REPO_ROOT / "docs/raw_column_audit.csv"


# ---------------------------------------------------------------------------
# Feed lineage mappings
# ---------------------------------------------------------------------------

ISBE_BULK_MAPPINGS: dict[str, dict[str, str]] = {
    "isbe_committees_tsv": {
        "ID": "committee_id_sbe",
        "TypeOfCommittee": "committee_type",
        "StateCommittee": "is_state_committee_obsolete",
        "StateID": "state_committee_id_obsolete",
        "LocalCommittee": "is_local_committee_obsolete",
        "LocalID": "local_committee_id_obsolete",
        "ReferName": "reference_name",
        "Name": "committee_name",
        "Address1": "address_line_1",
        "Address2": "address_line_2",
        "Address3": "address_line_3",
        "City": "city",
        "State": "state",
        "Zip": "postal_code",
        "Status": "committee_status_code",
        "StatusDate": "status_date",
        "CreationDate": "creation_date",
        "CreationAmount": "creation_funds_available",
        "DispFundsReturn": "residual_funds_return_to_contributors",
        "DispFundsPolComm": "residual_funds_to_political_committee",
        "DispFundsCharity": "residual_funds_to_charity",
        "DispFunds95": "residual_funds_per_ilcs_9_5",
        "DispFundsDescrip": "residual_funds_description",
        "CanSuppOpp": "candidate_support_or_oppose",
        "PolicySuppOpp": "policy_support_or_oppose",
        "PartyAffiliation": "party_affiliation",
        "Purpose": "committee_purpose",
    },
    "isbe_d2totals_tsv": {
        "ID": "d2_totals_record_id",
        "CommitteeID": "committee_id_sbe",
        "FiledDocID": "filed_doc_id",
        "BegFundsAvail": "beginning_funds_available",
        "IndivContribI": "individual_contributions_itemized",
        "IndivContribNI": "individual_contributions_non_itemized",
        "XferInI": "transfers_in_itemized",
        "XferInNI": "transfers_in_non_itemized",
        "LoanRcvI": "loans_received_itemized",
        "LoanRcvNI": "loans_received_non_itemized",
        "OtherRctI": "other_receipts_itemized",
        "OtherRctNI": "other_receipts_non_itemized",
        "TotalReceipts": "total_receipts",
        "InKindI": "in_kind_contributions_itemized",
        "InKindNI": "in_kind_contributions_non_itemized",
        "TotalInKind": "total_in_kind_contributions",
        "XferOutI": "transfers_out_itemized",
        "XferOutNI": "transfers_out_non_itemized",
        "LoanMadeI": "loans_made_itemized",
        "LoanMadeNI": "loans_made_non_itemized",
        "ExpendI": "expenditures_itemized",
        "ExpendNI": "expenditures_non_itemized",
        "IndependentExpI": "independent_expenditures_itemized",
        "IndependentExpNI": "independent_expenditures_non_itemized",
        "TotalExpend": "total_expenditures",
        "DebtsI": "debts_obligations_itemized",
        "DebtsNI": "debts_obligations_non_itemized",
        "TotalDebts": "total_debts_obligations",
        "TotalInvest": "total_investments",
        "EndFundsAvail": "ending_funds_available",
        "Archived": "is_archived",
    },
    "isbe_candidates_tsv": {
        "ID": "candidate_id",
        "LastName": "last_name",
        "FirstName": "first_name",
        "Address1": "address_line_1",
        "Address2": "address_line_2",
        "City": "city",
        "State": "state",
        "Zip": "postal_code",
        "Office": "office_sought",
        "DistrictType": "district_type",
        "District": "district",
        "ResidenceCounty": "residence_county",
        "PartyAffiliation": "party_affiliation",
        "RedactionRequested": "redaction_requested",
    },
    "isbe_cmte_candidate_links_tsv": {
        "ID": "link_record_id",
        "CommitteeID": "committee_id_sbe",
        "CandidateID": "candidate_id",
    },
    "isbe_receipts_tsv": {
        "ID": "receipt_record_id",
        "CommitteeID": "committee_id_sbe",
        "FiledDocID": "filed_doc_id",
        "ETransID": "electronic_transaction_id",
        "LastOnlyName": "last_or_business_name",
        "FirstName": "first_name",
        "RcvDate": "received_date",
        "Amount": "amount",
        "AggregateAmount": "aggregate_amount",
        "LoanAmount": "loan_amount",
        "Occupation": "occupation",
        "Employer": "employer",
        "Address1": "address_line_1",
        "Address2": "address_line_2",
        "City": "city",
        "State": "state",
        "Zip": "postal_code",
        "D2Part": "d2_part_code",
        "Description": "description",
        "VendorLastOnlyName": "vendor_last_or_business_name",
        "VendorFirstName": "vendor_first_name",
        "VendorAddress1": "vendor_address_line_1",
        "VendorAddress2": "vendor_address_line_2",
        "VendorCity": "vendor_city",
        "VendorState": "vendor_state",
        "VendorZip": "vendor_postal_code",
        "Archived": "is_archived",
        "Country": "country",
        "RedactionRequested": "redaction_requested",
    },
    "isbe_expenditures_tsv": {
        "ID": "expenditure_record_id",
        "CommitteeID": "committee_id_sbe",
        "FiledDocID": "filed_doc_id",
        "ETransID": "electronic_transaction_id",
        "LastOnlyName": "payee_last_or_business_name",
        "FirstName": "payee_first_name",
        "ExpendedDate": "expended_date",
        "Amount": "amount",
        "AggregateAmount": "aggregate_amount",
        "Address1": "address_line_1",
        "Address2": "address_line_2",
        "City": "city",
        "State": "state",
        "Zip": "postal_code",
        "D2Part": "d2_part_code",
        "Purpose": "purpose",
        "CandidateName": "candidate_name",
        "Office": "office",
        "Supporting": "is_supporting",
        "Opposing": "is_opposing",
        "Archived": "is_archived",
        "Country": "country",
        "RedactionRequested": "redaction_requested",
    },
}

ISBE_BULK_TABLE = {
    "isbe_committees_tsv": "bulk_committees_clean",
    "isbe_d2totals_tsv": "bulk_d2_totals_clean",
    "isbe_candidates_tsv": "bulk_candidates_clean",
    "isbe_cmte_candidate_links_tsv": "bulk_cmte_candidate_links_clean",
    "isbe_receipts_tsv": "bulk_receipts_clean",
    "isbe_expenditures_tsv": "bulk_expenditures_clean",
}

ISBE_NEVER_LOADED_BY_BULK = {
    "isbe_filed_docs_tsv",
    "isbe_canelections_tsv",
    "isbe_officers_tsv",
    "isbe_prev_officers_tsv",
    "isbe_cmte_officer_links_tsv",
    "isbe_investments_tsv",
}

ISBE_SUNSHINE_DROP_COLUMNS = {
    # Committees
    "isbe_committees_tsv": {
        "StateID": (
            "scripts/isbe_sunshine_etl.py:55",
            "explicitly excluded (sunshine FILE_COLUMNS omits StateID)",
        ),
        "LocalID": (
            "scripts/isbe_sunshine_etl.py:55",
            "explicitly excluded (sunshine FILE_COLUMNS omits LocalID)",
        ),
        "DispFunds95": (
            "scripts/isbe_sunshine_etl.py:1243",
            "implicitly lost (collision with DispFundsDescrip -> disp_funds_95)",
        ),
    },
    # D2 totals
    "isbe_d2totals_tsv": {
        "XferOutI": (
            "scripts/isbe_sunshine_etl.py:1304",
            "implicitly lost (mapped to expenditures_itemized then overwritten by ExpendI)",
        ),
        "XferOutNI": (
            "scripts/isbe_sunshine_etl.py:1305",
            "implicitly lost (mapped to expenditures_non_itemized then overwritten by ExpendNI)",
        ),
        "LoanMadeI": (
            "scripts/isbe_sunshine_etl.py:1306",
            "implicitly lost (mapped to expenditures_itemized then overwritten by ExpendI)",
        ),
        "LoanMadeNI": (
            "scripts/isbe_sunshine_etl.py:1307",
            "implicitly lost (mapped to expenditures_non_itemized then overwritten by ExpendNI)",
        ),
    },
}

LOBBYING_ACTIVE_MAPPING = {
    "ENT_REG_YEAR": ("lobbying_entities.reg_year", "reg_year"),
    "ENTITY_ID": ("lobbying_entities.entity_id", "entity_id"),
    "ENTITY_NAME": ("lobbying_entities.entity_name", "entity_name"),
    "CLIENT_ID": ("lobbying_clients.client_id", "client_id"),
    "CLIENT_NAME": ("lobbying_clients.client_name", "client_name"),
}

LOBBYING_DAILY_MAPPING = {
    "ENT_REG_YEAR": ("lobbying_entity_clients.reg_year", "reg_year"),
    "LOBBYIST_LNAME": ("lobbying_lobbyists.last_name", "last_name"),
    "LOBBYIST_FNAME": ("lobbying_lobbyists.first_name", "first_name"),
    "LOBBYIST_MNAME": ("lobbying_lobbyists.middle_name", "middle_name"),
    "LOBBYIST_ID": ("lobbying_lobbyists.lobbyist_id", "lobbyist_id"),
    "LOBBYIST_EMAIL": ("lobbying_lobbyists.email", "email"),
    "LOBBYIST_ADDR1": ("lobbying_lobbyists.address_1", "address_1"),
    "LOBBYIST_ADDR2": ("lobbying_lobbyists.address_2", "address_2"),
    "LOBBYIST_CITY": ("lobbying_lobbyists.city", "city"),
    "LOBBYIST_ST_ABBR": ("lobbying_lobbyists.state", "state"),
    "LOBBYIST_ZIP": ("lobbying_lobbyists.postal_code", "postal_code"),
    "LOBBYIST_STATUS": ("lobbying_lobbyists.status", "status"),
    "LOBBYIST_PHONE": ("lobbying_lobbyists.phone", "phone"),
    "ENT_ID": ("lobbying_entities.entity_id", "entity_id"),
    "ENT_NAME": ("lobbying_entities.entity_name", "entity_name"),
    "ENT_ADDR1": ("lobbying_entities.address_1", "address_1"),
    "ENT_ADDR2": ("lobbying_entities.address_2", "address_2"),
    "ENT_CITY": ("lobbying_entities.city", "city"),
    "ENT_ST_ABBR": ("lobbying_entities.state", "state"),
    "ENT_ZIP": ("lobbying_entities.postal_code", "postal_code"),
    "CLIENT_ID": ("lobbying_clients.client_id", "client_id"),
    "CLIENT_NAME": ("lobbying_clients.client_name", "client_name"),
    "CLIENT_ADDR1": ("lobbying_clients.address_1", "address_1"),
    "CLIENT_ADDR2": ("lobbying_clients.address_2", "address_2"),
    "CLIENT_CITY": ("lobbying_clients.city", "city"),
    "CLIENT_ST_ABBR": ("lobbying_clients.state", "state"),
    "CLIENT_ZIP": ("lobbying_clients.postal_code", "postal_code"),
    "CLIENT_STATUS": ("lobbying_clients.status", "status"),
}

IRS_RECORD_TABLE = {
    "irs527_full_data_record_1_org_registration": "irs527_organizations",
    "irs527_full_data_record_2_periodic_report": "irs527_reports",
    "irs527_full_data_record_D_director": "irs527_directors",
    "irs527_full_data_record_R_related_org": "irs527_related_orgs",
    "irs527_full_data_record_A_contribution": "irs527_contributions",
    "irs527_full_data_record_B_expenditure": "irs527_expenditures",
    "irs527_full_data_record_E_election_authority": "irs527_election_authority",
}

IRS_POSITION_MAP: dict[str, dict[str, str]] = {
    "irs527_full_data_record_1_org_registration": {
        "pos_1": "form_id",
        "pos_2": "form_id_seq",
        "pos_6": "ein",
        "pos_7": "org_name",
        "pos_8": "address_1",
        "pos_9": "address_2",
        "pos_10": "city",
        "pos_11": "state",
        "pos_12": "zip",
        "pos_13": "zip_ext",
        "pos_14": "email",
        "pos_15": "formation_date",
        "pos_16": "custodian_name",
        "pos_17": "custodian_address_1",
        "pos_18": "custodian_address_2",
        "pos_19": "custodian_city",
        "pos_20": "custodian_state",
        "pos_21": "custodian_zip",
        "pos_22": "custodian_zip_ext",
        "pos_23": "contact_name",
        "pos_24": "contact_address_1",
        "pos_25": "contact_address_2",
        "pos_26": "contact_city",
        "pos_27": "contact_state",
        "pos_28": "contact_zip",
        "pos_29": "contact_zip_ext",
        "pos_30": "business_address_1",
        "pos_31": "business_address_2",
        "pos_32": "business_city",
        "pos_33": "business_state",
        "pos_34": "business_zip",
        "pos_35": "business_zip_ext",
        "pos_36": "purpose",
        "pos_37": "material_change_date",
        "pos_38": "insert_datetime",
        "pos_39": "related_entity_bypass",
        "pos_40": "eain_bypass",
    },
    "irs527_full_data_record_2_periodic_report": {
        "pos_1": "ein_fallback",
        "pos_2": "form_id",
        "pos_3": "period_start",
        "pos_4": "period_end",
        "pos_9": "org_name",
        "pos_10": "org_ein",
        "pos_11": "org_address_1",
        "pos_12": "org_address_2",
        "pos_13": "org_city",
        "pos_14": "org_state",
        "pos_15": "org_zip",
        "pos_16": "org_zip_ext",
        "pos_17": "email",
        "pos_18": "formation_date",
        "pos_19": "custodian_name",
        "pos_20": "custodian_address_1",
        "pos_21": "custodian_address_2",
        "pos_22": "custodian_city",
        "pos_23": "custodian_state",
        "pos_24": "custodian_zip",
        "pos_25": "custodian_zip_ext",
        "pos_26": "contact_name",
        "pos_27": "contact_address_1",
        "pos_28": "contact_address_2",
        "pos_29": "contact_city",
        "pos_30": "contact_state",
        "pos_31": "contact_zip",
        "pos_32": "contact_zip_ext",
        "pos_33": "business_address_1",
        "pos_34": "business_address_2",
        "pos_35": "business_city",
        "pos_36": "business_state",
        "pos_37": "business_zip",
        "pos_38": "business_zip_ext",
        "pos_39": "qtr_indicator",
        "pos_40": "monthly_amount_1",
        "pos_41": "monthly_amount_2",
        "pos_42": "monthly_amount_3",
        "pos_43": "total_contributions_fallback",
        "pos_44": "total_expenditures_fallback",
        "pos_45": "total_contributions",
        "pos_47": "total_expenditures",
        "pos_48": "insert_datetime",
    },
    "irs527_full_data_record_D_director": {
        "pos_1": "form_id",
        "pos_3": "org_name",
        "pos_4": "ein",
        "pos_5": "person_name",
        "pos_6": "title",
        "pos_7": "address_1",
        "pos_8": "address_2",
        "pos_9": "city",
        "pos_10": "state",
        "pos_11": "zip",
        "pos_12": "zip_ext",
    },
    "irs527_full_data_record_R_related_org": {
        "pos_1": "form_id",
        "pos_3": "org_name",
        "pos_4": "ein",
        "pos_5": "related_org_name",
        "pos_6": "relationship_type",
        "pos_7": "address_1",
        "pos_8": "address_2",
        "pos_9": "city",
        "pos_10": "state",
        "pos_11": "zip",
        "pos_12": "zip_ext",
    },
    "irs527_full_data_record_A_contribution": {
        "pos_1": "form_id",
        "pos_3": "org_name",
        "pos_4": "ein",
        "pos_5": "contributor_name",
        "pos_6": "contributor_address",
        "pos_7": "contributor_address_2",
        "pos_8": "city",
        "pos_9": "state",
        "pos_10": "zip",
        "pos_11": "zip_ext",
        "pos_12": "contributor_employer",
        "pos_13": "amount",
        "pos_14": "contributor_occupation",
        "pos_15": "date",
    },
    "irs527_full_data_record_B_expenditure": {
        "pos_1": "form_id",
        "pos_3": "org_name",
        "pos_4": "ein",
        "pos_5": "recipient_name",
        "pos_6": "recipient_address",
        "pos_7": "recipient_address_2",
        "pos_8": "city",
        "pos_9": "state",
        "pos_10": "zip",
        "pos_11": "zip_ext",
        "pos_12": "recipient_employer",
        "pos_13": "amount",
        "pos_14": "recipient_occupation",
        "pos_15": "date",
        "pos_16": "purpose",
    },
    "irs527_full_data_record_E_election_authority": {
        "pos_1": "form_id",
        "pos_2": "election_authority_id",
        "pos_3": "state",
    },
}


TABLE_USAGE_PATHS = {
    "bulk_committees_clean": [
        "webapp/routes/committees.py",
        "webapp/routes/lobbying.py",
        "database/analytics.py",
    ],
    "bulk_d2_totals_clean": [
        "database/analytics.py",
        "scripts/build_triple_pipeline.py",
    ],
    "bulk_candidates_clean": [
        "webapp/routes/committees.py",
        "webapp/routes/lobbying.py",
        "database/cross_matching.py",
    ],
    "bulk_cmte_candidate_links_clean": [
        "webapp/routes/committees.py",
        "webapp/routes/lobbying.py",
        "database/analytics.py",
    ],
    "bulk_receipts_clean": [
        "webapp/routes/committees.py",
        "webapp/routes/lobbying.py",
        "database/analytics.py",
    ],
    "bulk_expenditures_clean": [
        "webapp/routes/committees.py",
        "database/analytics.py",
        "scraper/openbook_scraper.py",
    ],
    "lobbying_entities": [
        "webapp/routes/lobbying.py",
        "database/cross_matching.py",
        "scraper/openbook_scraper.py",
    ],
    "lobbying_clients": [
        "webapp/routes/lobbying.py",
        "database/cross_matching.py",
    ],
    "lobbying_entity_clients": [
        "webapp/routes/lobbying.py",
        "database/cross_matching.py",
    ],
    "lobbying_lobbyists": [
        "webapp/routes/lobbying.py",
        "docs/case_studies/ameren_lobbying_relationships.md",
    ],
    "lobbying_lobbyist_registrations": [
        "webapp/routes/lobbying.py",
        "docs/case_studies/ameren_lobbying_relationships.md",
    ],
    "irs527_organizations": [
        "webapp/routes/irs527.py",
        "database/cross_matching.py",
    ],
    "irs527_reports": [
        "webapp/routes/irs527.py",
        "scripts/build_triple_pipeline.py",
    ],
    "irs527_directors": [
        "webapp/routes/irs527.py",
        "database/cross_matching.py",
    ],
    "irs527_related_orgs": [
        "webapp/routes/irs527.py",
    ],
    "irs527_contributions": [
        "webapp/routes/irs527.py",
        "scripts/build_triple_pipeline.py",
    ],
    "irs527_expenditures": [
        "webapp/routes/irs527.py",
        "database/cross_matching.py",
    ],
    "irs527_election_authority": [
        "database/irs527_loader.py",
    ],
    "raw_extractions": [
        "database/federal_fec.py",
        "scraper/openbook_scraper.py",
    ],
    "fec_candidate_match": [
        "database/federal_fec.py",
        "scripts/newsroom_queries.py",
    ],
    "fec_candidate_committees": [
        "database/federal_fec.py",
        "webapp/routes/main.py",
    ],
    "fec_candidate_cycle_totals": [
        "database/federal_fec.py",
        "webapp/routes/main.py",
        "scripts/newsroom_queries.py",
    ],
    "fec_schedule_a_contributions": [
        "database/federal_fec.py",
        "webapp/routes/main.py",
        "database/analytics.py",
    ],
    "fec_schedule_b_disbursements": [
        "database/federal_fec.py",
        "webapp/routes/main.py",
        "webapp/routes/federal_finance.py",
    ],
    "fec_schedule_e_independent_expenditures": [
        "database/federal_fec.py",
        "webapp/routes/main.py",
        "webapp/routes/federal_finance.py",
        "scripts/newsroom_queries.py",
    ],
    "openbook_contracts_raw": [
        "webapp/routes/openbook.py",
        "webapp/routes/main.py",
    ],
    "openbook_contributions_raw": [
        "webapp/routes/openbook.py",
    ],
    "openbook_contract_warrants": [
        "webapp/routes/openbook.py",
    ],
    "openbook_vendor_match": [
        "webapp/routes/openbook.py",
        "webapp/routes/main.py",
    ],
}

FEC_USED_KEYS: dict[str, set[str]] = {
    "fec_api_schedule_a_json": {
        "candidate_id",
        "candidate_name",
        "committee",
        "committee_name",
        "contribution_receipt_amount",
        "contribution_receipt_date",
        "contributor",
        "contributor_city",
        "contributor_employer",
        "contributor_id",
        "contributor_occupation",
        "contributor_state",
        "contributor_zip",
        "image_number",
        "is_individual",
        "line_number",
        "load_date",
        "memo_text",
        "receipt_type",
        "receipt_type_desc",
        "receipt_type_full",
        "sub_id",
        "two_year_transaction_period",
    },
    "fec_api_schedule_b_json": {
        "candidate_id",
        "candidate_name",
        "category_code",
        "category_code_full",
        "committee",
        "committee_name",
        "disbursement_amount",
        "disbursement_date",
        "disbursement_description",
        "disbursement_type",
        "disbursement_type_description",
        "election_type",
        "election_type_full",
        "image_number",
        "line_number",
        "load_date",
        "memo_text",
        "payee_city",
        "payee_employer",
        "payee_occupation",
        "payee_state",
        "payee_zip",
        "recipient_city",
        "recipient_committee_id",
        "recipient_state",
        "recipient_zip",
        "sub_id",
        "two_year_transaction_period",
    },
    "fec_api_schedule_e_json": {
        "candidate_id",
        "candidate_name",
        "candidate_office",
        "candidate_office_district",
        "candidate_office_state",
        "category_code",
        "category_code_full",
        "committee",
        "committee_id",
        "committee_name",
        "election_type",
        "election_type_full",
        "expenditure_amount",
        "expenditure_date",
        "expenditure_description",
        "filing_date",
        "form_line_number",
        "image_number",
        "line_number",
        "load_date",
        "memo_text",
        "payee_city",
        "payee_state",
        "payee_zip",
        "report_type",
        "sub_id",
        "support_oppose_indicator",
    },
    "fec_api_candidates_search_json": {
        "candidate_id",
        "name",
        "office",
        "state",
        "district",
        "party",
        "candidate_status",
        "principal_committees",
    },
    "fec_api_candidate_committees_json": {
        "committee_id",
        "name",
        "committee_type",
        "designation",
        "designation_full",
        "filing_frequency",
        "party",
        "city",
        "state",
        "zip",
    },
    "fec_api_candidate_totals_json": {
        "receipts",
        "contributions",
        "individual_contributions",
        "disbursements",
        "coverage_start_date",
        "coverage_end_date",
        "transaction_coverage_date",
        "last_report_year",
        "last_report_type_full",
        "last_cash_on_hand_end_period",
    },
}

FEC_FEED_CONFIG: dict[str, dict[str, str]] = {
    "fec_api_schedule_a_json": {
        "projection_location": "database/federal_fec.py:1173",
        "structured_table": "fec_schedule_a_contributions",
    },
    "fec_api_schedule_b_json": {
        "projection_location": "database/federal_fec.py:1358",
        "structured_table": "fec_schedule_b_disbursements",
    },
    "fec_api_schedule_e_json": {
        "projection_location": "database/federal_fec.py:1523",
        "structured_table": "fec_schedule_e_independent_expenditures",
    },
    "fec_api_candidates_search_json": {
        "projection_location": "database/federal_fec.py:802",
        "structured_table": "fec_candidate_match",
    },
    "fec_api_candidate_committees_json": {
        "projection_location": "database/federal_fec.py:938",
        "structured_table": "fec_candidate_committees",
    },
    "fec_api_candidate_totals_json": {
        "projection_location": "database/federal_fec.py:1052",
        "structured_table": "fec_candidate_cycle_totals",
    },
}

CHICAGO_TABLE_BY_FEED = {
    "chicago_contracts_socrata_json": "chicago_contracts_raw",
    "chicago_payments_socrata_json": "chicago_payments_raw",
    "chicago_lobbyist_contributions_socrata_json": "chicago_lobbyist_contributions_raw",
    "chicago_lobbying_activity_socrata_json": "chicago_lobbying_activity_raw",
}

CHICAGO_LOADED_COLUMNS: dict[str, dict[str, str]] = {
    "chicago_contracts_socrata_json": {
        "purchase_order_contract_number": "purchase_order_contract_number",
        "revision_number": "revision_number",
        "specification_number": "specification_number",
        "contract_type": "contract_type",
        "start_date": "start_date",
        "end_date": "end_date",
        "approval_date": "approval_date",
        "department": "department",
        "vendor_name": "vendor_name",
        "vendor_id": "vendor_id",
        "city": "city",
        "state": "state",
        "zip": "zip",
        "award_amount": "award_amount",
        "procurement_type": "procurement_type",
        "purchase_order_description": "purchase_order_description",
        "contract_pdf": "contract_pdf",
    },
    "chicago_payments_socrata_json": {
        "voucher_number": "voucher_number",
        "amount": "amount",
        "check_date": "check_date_raw",
        "department_name": "department_name",
        "contract_number": "contract_number",
        "vendor_name": "vendor_name",
    },
    "chicago_lobbyist_contributions_socrata_json": {
        "contribution_id": "contribution_id",
        "period_start": "period_start",
        "period_end": "period_end",
        "contribution_date": "contribution_date",
        "recipient": "recipient",
        "amount": "amount",
        "lobbyist_id": "lobbyist_id",
        "lobbyist_first_name": "lobbyist_first_name",
        "lobbyist_last_name": "lobbyist_last_name",
    },
    "chicago_lobbying_activity_socrata_json": {
        "lobbying_activity_id": "lobbying_activity_id",
        "period_start": "period_start",
        "period_end": "period_end",
        "action": "action",
        "action_sought": "action_sought",
        "department": "department",
        "client_id": "client_id",
        "client_name": "client_name",
        "lobbyist_id": "lobbyist_id",
        "lobbyist_first_name": "lobbyist_first_name",
        "lobbyist_last_name": "lobbyist_last_name",
    },
}

CHICAGO_PROJECTION_LOCATION = {
    "chicago_contracts_socrata_json": "database/chicago_loader.py:378",
    "chicago_payments_socrata_json": "database/chicago_loader.py:398",
    "chicago_lobbyist_contributions_socrata_json": "database/chicago_loader.py:407",
    "chicago_lobbying_activity_socrata_json": "database/chicago_loader.py:419",
}

OPENBOOK_FEED_MAPPING: dict[str, dict[str, Any]] = {
    "openbook_contracts_search_json": {
        "table": "openbook_contracts_raw",
        "location": "scraper/openbook_scraper.py:1432",
        "mapped": {
            "vendor_label": "vendor_label",
            "fiscal_year": "fiscal_year",
            "agency_code": "agency_code",
            "agency_name": "agency_name",
            "contract_number": "contract_number",
            "award_amount": "award_amount",
            "detail_url": "detail_url",
            "row_hash": "row_hash",
        },
    },
    "openbook_contributions_tab_json": {
        "table": "openbook_contributions_raw",
        "location": "scraper/openbook_scraper.py:1492",
        "mapped": {
            "contributor_name": "contributor_name",
            "contributor_first_name": "contributor_first_name",
            "recipient_name": "recipient_name",
            "employer": "employer",
            "contribution_date": "contribution_date",
            "amount": "amount",
            "row_hash": "row_hash",
        },
    },
    "openbook_employees_tab_json": {
        "table": "openbook_contributions_raw",
        "location": "scraper/openbook_scraper.py:1492",
        "mapped": {
            "contributor_name": "contributor_name",
            "contributor_first_name": "contributor_first_name",
            "recipient_name": "recipient_name",
            "employer": "employer",
            "contribution_date": "contribution_date",
            "amount": "amount",
            "row_hash": "row_hash",
        },
    },
    "openbook_contract_detail_json": {
        "table": "raw_extractions",
        "location": "scraper/openbook_scraper.py:1534",
        "mapped": {
            "vendor_key": "payload_json",
            "contract_number": "payload_json",
            "fiscal_year": "payload_json",
            "detail_url": "payload_json",
            "warrant_count": "payload_json",
            "warrants": "payload_json",
        },
    },
}


def _short_examples(values: list[str], max_values: int = 3) -> str:
    if not values:
        return ""
    trimmed = values[:max_values]
    return " | ".join(trimmed)


def _boolish(value: bool) -> str:
    return "yes" if value else "no"


def _summarize_items(values: list[str], limit: int = 10) -> str:
    cleaned = [v for v in values if v]
    if len(cleaned) <= limit:
        return "; ".join(cleaned)
    shown = cleaned[:limit]
    remaining = len(cleaned) - limit
    return f"{'; '.join(shown)}; ... (+{remaining} more)"


def _build_feed_inventory() -> list[dict[str, str]]:
    return [
        {
            "feed_name": "ISBE bulk TXT (legacy bulk loader)",
            "raw_locations": "Bulk_download/*.txt (committees_, d2totals_, candidates_, cmtecandidatelinks_, receipts_, expenditures_)",
            "file_formats": "TSV",
            "ingestion_scripts": "cli/commands.py::import_bulk_download_command -> database/bulk_download_loader.py::import_bulk_download",
        },
        {
            "feed_name": "ISBE bulk TXT (sunshine ETL)",
            "raw_locations": "Bulk_download/*.txt (all 12 ISBE files)",
            "file_formats": "TSV",
            "ingestion_scripts": "cli/commands.py::sunshine_import_command -> scripts/isbe_sunshine_etl.py",
        },
        {
            "feed_name": "IL SOS lobbying CSV",
            "raw_locations": "Bulk_download/ILSOS_Lobbying_activeandclients/*.csv; Bulk_download/Lobbyist_Entity_Client_Data_Daily_*.csv",
            "file_formats": "CSV",
            "ingestion_scripts": "cli/commands.py::import_lobbying_command -> database/lobbying_loader.py::load_lobbying_csv",
        },
        {
            "feed_name": "IRS 527 FullDataFile",
            "raw_locations": "Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt",
            "file_formats": "Pipe-delimited (headerless, record-typed)",
            "ingestion_scripts": "cli/commands.py::import_irs527_command -> database/irs527_loader.py::load_irs527_full_file",
        },
        {
            "feed_name": "Chicago Socrata Phase 1",
            "raw_locations": "Runtime API: data.cityofchicago.org resource/*.json",
            "file_formats": "JSON (API)",
            "ingestion_scripts": "cli/commands.py::import_chicago_phase1_command -> database/chicago_loader.py::import_chicago_phase1",
        },
        {
            "feed_name": "FEC API",
            "raw_locations": "Runtime API: api.open.fec.gov/v1/* (cached in raw_extractions)",
            "file_formats": "JSON (API)",
            "ingestion_scripts": "cli/commands.py::sync_fec_il_federal_command/backfill_* -> database/federal_fec.py",
        },
        {
            "feed_name": "OpenBook Illinois Comptroller",
            "raw_locations": "Runtime HTML pages + parsed payload snapshots in raw_extractions",
            "file_formats": "HTML/JSON",
            "ingestion_scripts": "cli/commands.py::import_openbook_batch_command -> scraper/openbook_scraper.py",
        },
        {
            "feed_name": "ISBE web scrapers (main/detail/committee/D2)",
            "raw_locations": "Runtime HTML pages (not persisted as full raw snapshots)",
            "file_formats": "HTML",
            "ingestion_scripts": "cli/commands.py::scrape_main/scrape_details/scrape_committee_reports/scrape_d2_*",
        },
        {
            "feed_name": "Comptroller contracts scraper (module)",
            "raw_locations": "Runtime HTML pages (tests fixtures only in repo)",
            "file_formats": "HTML",
            "ingestion_scripts": "scraper/comptroller_contracts.py::ComptrollerContractsScraper",
        },
    ]


def _map_row(feed_name: str, raw_col: str) -> dict[str, Any]:
    # Defaults
    row = {
        "is_loaded_from_raw": "no",
        "is_renamed_to": "",
        "is_stored": "",
        "is_used": "no",
        "where_used": "",
        "where_dropped": "",
        "drop_reason": "unknown/bug",
        "recommendation": "Add explicit mapping or mark intentional denylist with rationale.",
    }

    if feed_name in ISBE_BULK_MAPPINGS:
        mapped = ISBE_BULK_MAPPINGS[feed_name].get(raw_col)
        table = ISBE_BULK_TABLE[feed_name]
        if mapped:
            row["is_loaded_from_raw"] = "yes"
            row["is_renamed_to"] = mapped if mapped != raw_col else ""
            row["is_stored"] = f"{table}.{mapped}"
            row["is_used"] = "yes" if table in TABLE_USAGE_PATHS else "no"
            row["where_used"] = "; ".join(TABLE_USAGE_PATHS.get(table, []))
            row["drop_reason"] = ""
            row["recommendation"] = "Keep mapped; add schema regression tests."
        else:
            row["where_dropped"] = "database/bulk_download_loader.py:1362"
            row["drop_reason"] = "explicitly excluded (raw column not referenced in bulk loader mapping)"
            row["recommendation"] = "Add to loader mapping if required."

        sunshine_drop = ISBE_SUNSHINE_DROP_COLUMNS.get(feed_name, {}).get(raw_col)
        if sunshine_drop:
            where, reason = sunshine_drop
            row["where_dropped"] = where
            row["drop_reason"] = reason
            row["recommendation"] = (
                "Preserve this column in sunshine-import (distinct target column) "
                "or commit an explicit denylist decision."
            )

        return row

    if feed_name in ISBE_NEVER_LOADED_BY_BULK:
        row["where_dropped"] = "database/bulk_download_loader.py:1362"
        row["drop_reason"] = "explicitly excluded (import-bulk-download never selects this file type)"
        row["recommendation"] = (
            "Either migrate consumers to sunshine-import outputs or extend import-bulk-download "
            "to include this file family."
        )
        return row

    if feed_name == "ilsos_lobbying_active_clients_csv":
        mapped = LOBBYING_ACTIVE_MAPPING.get(raw_col)
        if mapped:
            table_col, renamed = mapped
            table = table_col.split(".", 1)[0]
            row.update(
                {
                    "is_loaded_from_raw": "yes",
                    "is_renamed_to": renamed if renamed != raw_col else "",
                    "is_stored": table_col,
                    "is_used": "yes",
                    "where_used": "; ".join(TABLE_USAGE_PATHS.get(table, [])),
                    "where_dropped": "",
                    "drop_reason": "",
                    "recommendation": "Keep mapped; add new-column alerting.",
                }
            )
        else:
            row["where_dropped"] = "database/lobbying_loader.py:108"
            row["drop_reason"] = "never read (no alias/mapping for column)"
        return row

    if feed_name == "ilsos_lobbying_daily_csv":
        mapped = LOBBYING_DAILY_MAPPING.get(raw_col)
        if mapped:
            table_col, renamed = mapped
            table = table_col.split(".", 1)[0]
            row.update(
                {
                    "is_loaded_from_raw": "yes",
                    "is_renamed_to": renamed if renamed != raw_col else "",
                    "is_stored": table_col,
                    "is_used": "yes" if table in TABLE_USAGE_PATHS else "no",
                    "where_used": "; ".join(TABLE_USAGE_PATHS.get(table, [])),
                    "where_dropped": "",
                    "drop_reason": "",
                    "recommendation": "Keep mapped; add new-column alerting.",
                }
            )
        else:
            row["where_dropped"] = "database/lobbying_loader.py:108"
            row["drop_reason"] = "never read (no alias/mapping for column)"
        return row

    if feed_name.startswith("irs527_full_data_record_"):
        if feed_name == "irs527_full_data_record_H_header":
            if raw_col in {"pos_1", "pos_2", "pos_3"}:
                row.update(
                    {
                        "is_loaded_from_raw": "yes",
                        "is_stored": "",
                        "is_used": "no",
                        "where_dropped": "database/irs527_loader.py:562",
                        "drop_reason": "always-null/meaningless for core model (header parsed for stats only)",
                        "recommendation": "Persist header metadata to an audit table if needed for provenance.",
                    }
                )
            else:
                row["where_dropped"] = "database/irs527_loader.py:64"
                row["drop_reason"] = "never read"
            return row

        if feed_name == "irs527_full_data_record_UNKNOWN":
            row.update(
                {
                    "is_loaded_from_raw": "no",
                    "where_dropped": "database/irs527_loader.py:651",
                    "drop_reason": "unknown/bug (unrecognized record type treated as malformed)",
                    "recommendation": "Persist unknown record types to quarantine table for review.",
                }
            )
            return row

        table = IRS_RECORD_TABLE.get(feed_name)
        mapping = IRS_POSITION_MAP.get(feed_name, {})
        mapped_col = mapping.get(raw_col)
        if mapped_col:
            row.update(
                {
                    "is_loaded_from_raw": "yes",
                    "is_renamed_to": mapped_col,
                    "is_stored": f"{table}.{mapped_col}" if table else mapped_col,
                    "is_used": "yes" if table in TABLE_USAGE_PATHS else "no",
                    "where_used": "; ".join(TABLE_USAGE_PATHS.get(table, [])),
                    "drop_reason": "",
                    "recommendation": "Keep mapped; enforce positional schema checks in CI.",
                }
            )
        else:
            if "record_1_" in feed_name:
                where = "database/irs527_loader.py:74"
            elif "record_2_" in feed_name:
                where = "database/irs527_loader.py:125"
            elif "record_D_" in feed_name:
                where = "database/irs527_loader.py:190"
            elif "record_R_" in feed_name:
                where = "database/irs527_loader.py:212"
            elif "record_A_" in feed_name:
                where = "database/irs527_loader.py:234"
            elif "record_B_" in feed_name:
                where = "database/irs527_loader.py:259"
            else:
                where = "database/irs527_loader.py:285"
            row.update(
                {
                    "is_loaded_from_raw": "no",
                    "where_dropped": where,
                    "drop_reason": "explicitly excluded (parser never references this position)",
                    "recommendation": "Review IRS layout and map needed positions explicitly.",
                }
            )
        return row

    if feed_name in FEC_FEED_CONFIG:
        cfg = FEC_FEED_CONFIG[feed_name]
        used_keys = FEC_USED_KEYS.get(feed_name, set())
        structured_table = cfg["structured_table"]
        projection_location = cfg["projection_location"]
        row.update(
            {
                "is_loaded_from_raw": "yes",
                "is_renamed_to": "",
                "is_stored": (
                    f"raw_extractions.payload_json; {structured_table}.*"
                    if raw_col in used_keys
                    else "raw_extractions.payload_json"
                ),
                "is_used": "yes" if raw_col in used_keys else "no",
                "where_used": "; ".join(TABLE_USAGE_PATHS.get(structured_table, [])) if raw_col in used_keys else "",
            }
        )
        if raw_col in used_keys:
            row["drop_reason"] = ""
            row["recommendation"] = "Keep full raw payload retention and structured projection tests aligned."
        else:
            row["where_dropped"] = projection_location
            row["drop_reason"] = "explicitly excluded (not projected into structured FEC table)"
            row["recommendation"] = (
                "Add explicit projection mapping if this key is needed downstream, "
                "or document intentional exclusion."
            )
        return row

    if feed_name in CHICAGO_TABLE_BY_FEED:
        table = CHICAGO_TABLE_BY_FEED[feed_name]
        mapped = CHICAGO_LOADED_COLUMNS.get(feed_name, {}).get(raw_col)
        if mapped:
            row.update(
                {
                    "is_loaded_from_raw": "yes",
                    "is_renamed_to": mapped if mapped != raw_col else "",
                    "is_stored": f"{table}.{mapped}",
                    "is_used": "yes" if table in TABLE_USAGE_PATHS else "no",
                    "where_used": "; ".join(TABLE_USAGE_PATHS.get(table, [])),
                    "drop_reason": "",
                    "recommendation": (
                        "Keep mapped; add downstream consumers or retire feed if staging-only is intentional."
                        if table not in TABLE_USAGE_PATHS
                        else "Keep mapped; add schema regression tests."
                    ),
                }
            )
        else:
            row.update(
                {
                    "is_loaded_from_raw": "no",
                    "where_dropped": CHICAGO_PROJECTION_LOCATION[feed_name],
                    "drop_reason": "explicitly excluded (Socrata $select projection omits this raw column)",
                    "recommendation": (
                        "Add this column to chicago_loader select_fields + parse/upsert mappings if required."
                    ),
                }
            )
        return row

    if feed_name in OPENBOOK_FEED_MAPPING:
        cfg = OPENBOOK_FEED_MAPPING[feed_name]
        table = cfg["table"]
        mapped = cfg["mapped"].get(raw_col)
        if mapped:
            row.update(
                {
                    "is_loaded_from_raw": "yes",
                    "is_renamed_to": mapped if mapped != raw_col else "",
                    "is_stored": f"{table}.{mapped}",
                    "is_used": "yes" if table in TABLE_USAGE_PATHS else "no",
                    "where_used": "; ".join(TABLE_USAGE_PATHS.get(table, [])),
                    "drop_reason": "",
                    "recommendation": "Keep mapped; retain raw snapshot payloads for auditability.",
                }
            )
        else:
            row.update(
                {
                    "is_loaded_from_raw": "no",
                    "where_dropped": cfg["location"],
                    "drop_reason": "explicitly excluded (parser output key not persisted)",
                    "recommendation": "Add parser and save_* mapping if downstream usage is required.",
                }
            )
        return row

    return row


def _build_lineage_rows(census: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feed in census["feeds"]:
        if feed.get("blocked"):
            continue
        feed_name = feed["feed_name"]
        file_paths = [f["path"] for f in feed.get("files", [])]
        sampling_method = (
            feed.get("profiling", {}).get("method")
            or "; ".join(f.get("sampling_method", "") for f in feed.get("files", []))
        )
        for col in feed.get("column_stats", []):
            raw_col = col["raw_column_name"]
            mapped = _map_row(feed_name, raw_col)
            rows.append(
                {
                    "feed_name": feed_name,
                    "raw_column": raw_col,
                    "is_loaded_from_raw?": mapped["is_loaded_from_raw"],
                    "is_renamed_to?": mapped["is_renamed_to"],
                    "is_stored?": mapped["is_stored"],
                    "is_used?": mapped["is_used"],
                    "where_used": mapped["where_used"],
                    "where_dropped": mapped["where_dropped"],
                    "drop_reason": mapped["drop_reason"],
                    "recommendation": mapped["recommendation"],
                    "present_in_files_count": col["present_in_files_count"],
                    "present_in_all_files?": _boolish(bool(col["present_in_all_files"])),
                    "null_rate_estimate": col.get("null_rate_estimate"),
                    "example_values": _short_examples(col.get("example_values", []), max_values=3),
                    "evidence_raw_files": _summarize_items(file_paths, limit=12),
                    "sampling_method": sampling_method,
                }
            )
    return rows


def _write_csv(rows: list[dict[str, Any]]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "feed_name",
        "raw_column",
        "is_loaded_from_raw?",
        "is_renamed_to?",
        "is_stored?",
        "is_used?",
        "where_used",
        "where_dropped",
        "drop_reason",
        "recommendation",
        "present_in_files_count",
        "present_in_all_files?",
        "null_rate_estimate",
        "example_values",
        "evidence_raw_files",
        "sampling_method",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _lineage_stage_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["feed_name"]].append(row)

    output: list[dict[str, str]] = []
    for feed_name in sorted(grouped.keys()):
        feed_rows = grouped[feed_name]
        raw_sources = sorted(
            {
                item.strip()
                for row in feed_rows
                for item in str(row.get("evidence_raw_files") or "").split(";")
                if item.strip()
            }
        )
        stored_targets = sorted(
            {
                item.strip()
                for row in feed_rows
                for item in str(row.get("is_stored?") or "").split(";")
                if item.strip()
            }
        )
        staging_targets = [t for t in stored_targets if t.startswith("raw_extractions.")]
        core_targets = [t for t in stored_targets if not t.startswith("raw_extractions.")]
        used_paths = sorted(
            {
                item.strip()
                for row in feed_rows
                for item in str(row.get("where_used") or "").split(";")
                if item.strip()
            }
        )
        output.append(
            {
                "feed_name": feed_name,
                "raw": _summarize_items(raw_sources, limit=4),
                "staging": _summarize_items(staging_targets, limit=4),
                "core": _summarize_items(core_targets, limit=4),
                "used": _summarize_items(used_paths, limit=4),
            }
        )
    return output


def _build_markdown(census: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    all_feeds = list(census["feeds"])
    feeds = [f for f in all_feeds if not f.get("blocked")]

    unique_raw_columns = {(r["feed_name"], r["raw_column"]) for r in rows}
    dropped_rows = [r for r in rows if r["where_dropped"]]
    never_loaded_rows = [r for r in rows if r["is_loaded_from_raw?"] == "no"]
    loaded_but_unused_rows = [
        r for r in rows if r["is_loaded_from_raw?"] == "yes" and r["is_stored?"] and r["is_used?"] == "no"
    ]

    md: list[str] = []
    md.append("# Raw Column Audit")
    md.append("")
    md.append(f"_Generated: {generated_at}_")
    md.append("")
    md.append("## 1) Executive summary")
    md.append("")
    md.append(f"- total_feeds: {len(all_feeds)}")
    md.append(f"- total_unique_raw_columns: {len(unique_raw_columns)}")
    md.append(f"- count_columns_dropped: {len(dropped_rows)}")
    md.append(f"- count_columns_never_loaded: {len(never_loaded_rows)}")
    md.append(f"- count_columns_loaded_but_unused: {len(loaded_but_unused_rows)}")
    md.append("")
    md.append("## Step 0 — feed inventory")
    md.append("")
    md.append("| feed_name | raw_location(s) | file_format(s) | ingestion_script(s) |")
    md.append("|---|---|---|---|")
    for feed in _build_feed_inventory():
        md.append(
            f"| {feed['feed_name']} | `{feed['raw_locations']}` | {feed['file_formats']} | `{feed['ingestion_scripts']}` |"
        )
    md.append("")
    md.append("## Step 1 — raw-file census evidence")
    md.append("")
    md.append(
        "Method: `python3 scripts/raw_schema_census.py --output-json output/raw_schema_census.json` "
        "(all delimited files scanned for headers; row profiling sampled first N rows per file; "
        "IRS record types scanned full-file and profiled per record type; runtime JSON feeds scanned "
        "from `raw_extractions`; Chicago datasets scanned from Socrata metadata plus sampled API rows)."
    )
    md.append("")
    for feed in all_feeds:
        md.append(f"### {feed['feed_name']}")
        md.append("")
        if feed.get("blocked"):
            md.append(f"- blocked: yes ({feed.get('blocked_reason', 'no reason provided')})")
            if feed.get("notes"):
                md.append(f"- notes: {feed['notes']}")
            md.append("")
            continue
        raw_files = [f["path"] for f in feed.get("files", [])]
        raw_files_display = _summarize_items(raw_files, limit=8)
        md.append(f"- raw files: {', '.join(f'`{p}`' for p in raw_files_display.split('; '))}")
        md.append(f"- union_of_columns_count: {len(feed.get('union_of_columns', []))}")
        md.append(f"- intersection_of_columns_count: {len(feed.get('intersection_of_columns', []))}")
        if feed.get("record_type"):
            md.append(f"- record_type: `{feed['record_type']}`")
            md.append(f"- rows_observed: {feed.get('rows_observed', 0)}")
        md.append("")
        md.append(
            "| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |"
        )
        md.append("|---|---:|---|---|---:|---|")
        for col in feed.get("column_stats", []):
            examples = _short_examples(col.get("example_values", []), max_values=3).replace("|", "\\|")
            null_rate = "" if col.get("null_rate_estimate") is None else f"{col.get('null_rate_estimate'):.6f}"
            notes = col.get("notes") or ""
            md.append(
                f"| `{col['raw_column_name']}` | {col['present_in_files_count']} | "
                f"{_boolish(bool(col['present_in_all_files']))} | {examples} | {null_rate} | {notes} |"
            )
        md.append("")

    md.append("### Schema drift")
    md.append("")
    drift_findings = []
    for feed in feeds:
        for drift in feed.get("schema_drift_files", []):
            drift_findings.append((feed["feed_name"], drift["path"]))
    if not drift_findings:
        md.append("- No header-level schema drift detected across scanned feeds in this workspace snapshot.")
    else:
        max_drift_lines = 25
        for feed_name, path in drift_findings[:max_drift_lines]:
            md.append(f"- {feed_name}: `{path}`")
        if len(drift_findings) > max_drift_lines:
            md.append(f"- ... (+{len(drift_findings) - max_drift_lines} additional drift entries)")
    md.append("")

    md.append("## Column lineage map")
    md.append("")
    md.append("| feed_name | RAW | STAGING | CORE/DB | API/EXPORT/UI |")
    md.append("|---|---|---|---|---|")
    for item in _lineage_stage_rows(rows):
        md.append(
            f"| {item['feed_name']} | `{item['raw']}` | `{item['staging']}` | "
            f"`{item['core']}` | `{item['used']}` |"
        )
    md.append("")

    md.append("## 2) Per-feed Raw -> Stored -> Used tables")
    md.append("")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["feed_name"]].append(row)

    for feed_name in sorted(grouped.keys()):
        md.append(f"### {feed_name}")
        md.append("")
        md.append(
            "| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |"
        )
        md.append("|---|---|---|---|---|---|---|---|---|")
        for row in grouped[feed_name]:
            md.append(
                f"| `{row['raw_column']}` | {row['is_loaded_from_raw?']} | "
                f"`{row['is_renamed_to?']}` | `{row['is_stored?']}` | {row['is_used?']} | "
                f"`{row['where_used']}` | `{row['where_dropped']}` | {row['drop_reason']} | {row['recommendation']} |"
            )
        md.append("")

    md.append("## 3) Drop reasons taxonomy")
    md.append("")
    taxonomy_order = [
        "explicitly excluded",
        "implicitly lost",
        "filtered out due to schema mismatch",
        "always-null",
        "duplicated/redundant",
        "PII/ethics exclusion",
        "unknown/bug",
    ]
    bucketed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dropped_rows:
        reason = row["drop_reason"] or ""
        key = "unknown/bug"
        for tax in taxonomy_order:
            if tax in reason:
                key = tax
                break
        bucketed[key].append(row)

    for tax in taxonomy_order:
        entries = bucketed.get(tax, [])
        if not entries:
            continue
        md.append(f"- {tax}: {len(entries)}")
        sample = entries[:10]
        for row in sample:
            md.append(
                f"  - `{row['feed_name']}.{row['raw_column']}` -> `{row['where_dropped']}` ({row['drop_reason']})"
            )
    md.append("")

    md.append("## 4) Not-skipping hardening recommendations")
    md.append("")
    md.append(
        "1. Add `schemas/raw_schema_allowlist.yml` per feed and enforce: "
        "`raw_columns <= staging_columns OR explicit_denylist`."
    )
    md.append(
        "2. Run `scripts/raw_schema_census.py` in CI and fail on newly-seen columns unless allowlist is updated with rationale."
    )
    md.append(
        "3. Capture per-run raw schema artifacts (`output/raw_schema_census.json`) and store a dated copy for regression diffing."
    )
    md.append(
        "4. In sunshine ETL, stop key-collision mapping (`DispFunds95`/`DispFundsDescrip`, D2 transfer/loan fields) and store separate target columns."
    )
    md.append(
        "5. Extend `import-bulk-download` to ingest the six currently ignored ISBE files or deprecate that path in favor of sunshine-import."
    )
    md.append(
        "6. Add row-level reject telemetry dashboards for `bulk_expenditures_rejects` and alert if reject rates spike."
    )
    md.append(
        "7. Persist unknown IRS record types into a quarantine table instead of counting only as malformed."
    )
    md.append("")

    md.append("## Blocked / partial feeds")
    md.append("")
    blocked_feeds = [f for f in all_feeds if f.get("blocked")]
    if blocked_feeds:
        for feed in blocked_feeds:
            md.append(
                f"- `{feed['feed_name']}` blocked: {feed.get('blocked_reason', 'reason not provided')} "
                f"(raw_globs={'; '.join(feed.get('raw_globs', []))})"
            )
    else:
        md.append("- No blocked feeds in this run.")
    md.append(
        "- ISBE site scrapers and Comptroller HTML scraper remain partially blocked for full raw proof "
        "because complete per-run raw HTML snapshots are not retained."
    )
    md.append("")
    md.append(
        "Needed to unblock remaining gaps: retain full raw HTML/API snapshots per run (before parsing) "
        "and keep feed-specific allowlists with rationale."
    )
    md.append("")
    return "\n".join(md)


def main() -> int:
    if not CENSUS_PATH.exists():
        raise FileNotFoundError(
            f"Missing census file: {CENSUS_PATH}. Run scripts/raw_schema_census.py first."
        )

    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    rows = _build_lineage_rows(census)
    _write_csv(rows)
    md = _build_markdown(census, rows)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(md, encoding="utf-8")

    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_CSV}")
    print(f"Rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
