"""Load and normalize Illinois bulk-download TXT files into renamed SQL tables."""
from __future__ import annotations

import csv
from pathlib import Path
import sqlite3
import sys
from typing import Iterable, Optional


COMMITTEES_PREFIX = "committees_"
D2_TOTALS_PREFIX = "d2totals_"
CANDIDATES_PREFIX = "candidates_"
CMTE_CANDIDATE_LINKS_PREFIX = "cmtecandidatelinks_"
RECEIPTS_PREFIX = "receipts_"


def _set_csv_field_size_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = limit // 10
            if limit <= 0:
                return


_set_csv_field_size_limit()


def _clean_text(value: str | None) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _to_int(value: str | None) -> Optional[int]:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _to_float(value: str | None) -> Optional[float]:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_bool(value: str | None) -> Optional[int]:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    lowered = cleaned.lower()
    if lowered in {"true", "t", "1", "yes", "y"}:
        return 1
    if lowered in {"false", "f", "0", "no", "n"}:
        return 0
    return None


def find_latest_file(directory: Path, prefix: str, required: bool = True) -> Optional[Path]:
    matches = sorted(directory.glob(f"{prefix}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        if required:
            raise FileNotFoundError(f"No file found for prefix '{prefix}' in {directory}")
        return None
    return matches[0]


def init_bulk_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS bulk_committees_clean;
        DROP TABLE IF EXISTS bulk_d2_totals_clean;
        DROP TABLE IF EXISTS bulk_candidates_clean;
        DROP TABLE IF EXISTS bulk_cmte_candidate_links_clean;
        DROP TABLE IF EXISTS bulk_receipts_clean;
        DROP TABLE IF EXISTS bulk_committee_d2_totals;
        DROP TABLE IF EXISTS bulk_committee_candidate_links;
        DROP TABLE IF EXISTS bulk_candidate_committee_d2_totals;
        DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg;
        DROP TABLE IF EXISTS bulk_committee_receipts;
        DROP TABLE IF EXISTS bulk_d2_receipts_recon;
        DROP TABLE IF EXISTS bulk_candidate_committee_receipts_agg;

        CREATE TABLE bulk_committees_clean (
            committee_id_sbe INTEGER PRIMARY KEY,
            committee_type TEXT,
            is_state_committee_obsolete INTEGER,
            state_committee_id_obsolete INTEGER,
            is_local_committee_obsolete INTEGER,
            local_committee_id_obsolete INTEGER,
            reference_name TEXT,
            committee_name TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            address_line_3 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            committee_status_code TEXT,
            status_date TEXT,
            creation_date TEXT,
            creation_funds_available REAL,
            residual_funds_return_to_contributors INTEGER,
            residual_funds_to_political_committee INTEGER,
            residual_funds_to_charity INTEGER,
            residual_funds_per_ilcs_9_5 INTEGER,
            residual_funds_description TEXT,
            candidate_support_or_oppose TEXT,
            policy_support_or_oppose TEXT,
            party_affiliation TEXT,
            committee_purpose TEXT,
            source_file TEXT,
            source_row_number INTEGER
        );

        CREATE INDEX idx_bulk_committees_name ON bulk_committees_clean(committee_name);
        CREATE INDEX idx_bulk_committees_city_state ON bulk_committees_clean(city, state);

        CREATE TABLE bulk_d2_totals_clean (
            d2_totals_record_id INTEGER PRIMARY KEY,
            committee_id_sbe INTEGER,
            filed_doc_id INTEGER,
            beginning_funds_available REAL,
            individual_contributions_itemized REAL,
            individual_contributions_non_itemized REAL,
            transfers_in_itemized REAL,
            transfers_in_non_itemized REAL,
            loans_received_itemized REAL,
            loans_received_non_itemized REAL,
            other_receipts_itemized REAL,
            other_receipts_non_itemized REAL,
            total_receipts REAL,
            in_kind_contributions_itemized REAL,
            in_kind_contributions_non_itemized REAL,
            total_in_kind_contributions REAL,
            transfers_out_itemized REAL,
            transfers_out_non_itemized REAL,
            loans_made_itemized REAL,
            loans_made_non_itemized REAL,
            expenditures_itemized REAL,
            expenditures_non_itemized REAL,
            independent_expenditures_itemized REAL,
            independent_expenditures_non_itemized REAL,
            total_expenditures REAL,
            debts_obligations_itemized REAL,
            debts_obligations_non_itemized REAL,
            total_debts_obligations REAL,
            total_investments REAL,
            ending_funds_available REAL,
            is_archived INTEGER,
            source_file TEXT,
            source_row_number INTEGER
        );

        CREATE INDEX idx_bulk_d2_committee_id ON bulk_d2_totals_clean(committee_id_sbe);
        CREATE INDEX idx_bulk_d2_filed_doc_id ON bulk_d2_totals_clean(filed_doc_id);

        CREATE TABLE bulk_candidates_clean (
            candidate_id INTEGER PRIMARY KEY,
            last_name TEXT,
            first_name TEXT,
            candidate_full_name TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            residence_county TEXT,
            party_affiliation TEXT,
            redaction_requested INTEGER,
            source_file TEXT,
            source_row_number INTEGER
        );

        CREATE INDEX idx_bulk_candidates_name ON bulk_candidates_clean(candidate_full_name);
        CREATE INDEX idx_bulk_candidates_office ON bulk_candidates_clean(office_sought);

        CREATE TABLE bulk_cmte_candidate_links_clean (
            link_record_id INTEGER PRIMARY KEY,
            committee_id_sbe INTEGER,
            candidate_id INTEGER,
            source_file TEXT,
            source_row_number INTEGER
        );

        CREATE INDEX idx_bulk_links_committee_id ON bulk_cmte_candidate_links_clean(committee_id_sbe);
        CREATE INDEX idx_bulk_links_candidate_id ON bulk_cmte_candidate_links_clean(candidate_id);

        CREATE TABLE bulk_receipts_clean (
            bulk_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_record_id INTEGER,
            committee_id_sbe INTEGER,
            filed_doc_id INTEGER,
            electronic_transaction_id TEXT,
            last_or_business_name TEXT,
            first_name TEXT,
            received_date TEXT,
            amount REAL,
            aggregate_amount REAL,
            loan_amount REAL,
            occupation TEXT,
            employer TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            d2_part_code TEXT,
            description TEXT,
            vendor_last_or_business_name TEXT,
            vendor_first_name TEXT,
            vendor_address_line_1 TEXT,
            vendor_address_line_2 TEXT,
            vendor_city TEXT,
            vendor_state TEXT,
            vendor_postal_code TEXT,
            is_archived INTEGER,
            country TEXT,
            redaction_requested INTEGER,
            source_file TEXT,
            source_row_number INTEGER
        );

        CREATE INDEX idx_bulk_receipts_record_id ON bulk_receipts_clean(receipt_record_id);
        CREATE INDEX idx_bulk_receipts_committee_id ON bulk_receipts_clean(committee_id_sbe);
        CREATE INDEX idx_bulk_receipts_filed_doc_id ON bulk_receipts_clean(filed_doc_id);
        CREATE INDEX idx_bulk_receipts_received_date ON bulk_receipts_clean(received_date);
        CREATE INDEX idx_bulk_receipts_d2_part_code ON bulk_receipts_clean(d2_part_code);
        CREATE INDEX idx_bulk_receipts_name ON bulk_receipts_clean(last_or_business_name, first_name);
        """
    )
    conn.commit()


def _chunked(rows: Iterable[tuple], size: int = 5000):
    chunk = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def load_committees_file(conn: sqlite3.Connection, file_path: Path) -> int:
    insert_sql = """
        INSERT INTO bulk_committees_clean (
            committee_id_sbe, committee_type, is_state_committee_obsolete, state_committee_id_obsolete,
            is_local_committee_obsolete, local_committee_id_obsolete, reference_name, committee_name,
            address_line_1, address_line_2, address_line_3, city, state, postal_code,
            committee_status_code, status_date, creation_date, creation_funds_available,
            residual_funds_return_to_contributors, residual_funds_to_political_committee,
            residual_funds_to_charity, residual_funds_per_ilcs_9_5, residual_funds_description,
            candidate_support_or_oppose, policy_support_or_oppose, party_affiliation, committee_purpose,
            source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def rows():
        with file_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row_num, row in enumerate(reader, start=2):
                yield (
                    _to_int(row.get("ID")),
                    _clean_text(row.get("TypeOfCommittee")),
                    _to_bool(row.get("StateCommittee")),
                    _to_int(row.get("StateID")),
                    _to_bool(row.get("LocalCommittee")),
                    _to_int(row.get("LocalID")),
                    _clean_text(row.get("ReferName")),
                    _clean_text(row.get("Name")),
                    _clean_text(row.get("Address1")),
                    _clean_text(row.get("Address2")),
                    _clean_text(row.get("Address3")),
                    _clean_text(row.get("City")),
                    _clean_text(row.get("State")),
                    _clean_text(row.get("Zip")),
                    _clean_text(row.get("Status")),
                    _clean_text(row.get("StatusDate")),
                    _clean_text(row.get("CreationDate")),
                    _to_float(row.get("CreationAmount")),
                    _to_bool(row.get("DispFundsReturn")),
                    _to_bool(row.get("DispFundsPolComm")),
                    _to_bool(row.get("DispFundsCharity")),
                    _to_bool(row.get("DispFunds95")),
                    _clean_text(row.get("DispFundsDescrip")),
                    _clean_text(row.get("CanSuppOpp")),
                    _clean_text(row.get("PolicySuppOpp")),
                    _clean_text(row.get("PartyAffiliation")),
                    _clean_text(row.get("Purpose")),
                    file_path.name,
                    row_num,
                )

    total = 0
    for chunk in _chunked(rows()):
        conn.executemany(insert_sql, chunk)
        total += len(chunk)
    conn.commit()
    return total


def load_d2_totals_file(conn: sqlite3.Connection, file_path: Path) -> int:
    insert_sql = """
        INSERT INTO bulk_d2_totals_clean (
            d2_totals_record_id, committee_id_sbe, filed_doc_id,
            beginning_funds_available, individual_contributions_itemized, individual_contributions_non_itemized,
            transfers_in_itemized, transfers_in_non_itemized, loans_received_itemized, loans_received_non_itemized,
            other_receipts_itemized, other_receipts_non_itemized, total_receipts,
            in_kind_contributions_itemized, in_kind_contributions_non_itemized, total_in_kind_contributions,
            transfers_out_itemized, transfers_out_non_itemized, loans_made_itemized, loans_made_non_itemized,
            expenditures_itemized, expenditures_non_itemized,
            independent_expenditures_itemized, independent_expenditures_non_itemized,
            total_expenditures, debts_obligations_itemized, debts_obligations_non_itemized,
            total_debts_obligations, total_investments, ending_funds_available,
            is_archived, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def rows():
        with file_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row_num, row in enumerate(reader, start=2):
                yield (
                    _to_int(row.get("ID")),
                    _to_int(row.get("CommitteeID")),
                    _to_int(row.get("FiledDocID")),
                    _to_float(row.get("BegFundsAvail")),
                    _to_float(row.get("IndivContribI")),
                    _to_float(row.get("IndivContribNI")),
                    _to_float(row.get("XferInI")),
                    _to_float(row.get("XferInNI")),
                    _to_float(row.get("LoanRcvI")),
                    _to_float(row.get("LoanRcvNI")),
                    _to_float(row.get("OtherRctI")),
                    _to_float(row.get("OtherRctNI")),
                    _to_float(row.get("TotalReceipts")),
                    _to_float(row.get("InKindI")),
                    _to_float(row.get("InKindNI")),
                    _to_float(row.get("TotalInKind")),
                    _to_float(row.get("XferOutI")),
                    _to_float(row.get("XferOutNI")),
                    _to_float(row.get("LoanMadeI")),
                    _to_float(row.get("LoanMadeNI")),
                    _to_float(row.get("ExpendI")),
                    _to_float(row.get("ExpendNI")),
                    _to_float(row.get("IndependentExpI")),
                    _to_float(row.get("IndependentExpNI")),
                    _to_float(row.get("TotalExpend")),
                    _to_float(row.get("DebtsI")),
                    _to_float(row.get("DebtsNI")),
                    _to_float(row.get("TotalDebts")),
                    _to_float(row.get("TotalInvest")),
                    _to_float(row.get("EndFundsAvail")),
                    _to_bool(row.get("Archived")),
                    file_path.name,
                    row_num,
                )

    total = 0
    for chunk in _chunked(rows()):
        conn.executemany(insert_sql, chunk)
        total += len(chunk)
    conn.commit()
    return total


def load_candidates_file(conn: sqlite3.Connection, file_path: Path) -> int:
    insert_sql = """
        INSERT INTO bulk_candidates_clean (
            candidate_id, last_name, first_name, candidate_full_name,
            address_line_1, address_line_2, city, state, postal_code,
            office_sought, district_type, district, residence_county,
            party_affiliation, redaction_requested, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def rows():
        with file_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row_num, row in enumerate(reader, start=2):
                last_name = _clean_text(row.get("LastName"))
                first_name = _clean_text(row.get("FirstName"))
                full_name = " ".join([part for part in [first_name, last_name] if part]).strip() or None
                yield (
                    _to_int(row.get("ID")),
                    last_name,
                    first_name,
                    full_name,
                    _clean_text(row.get("Address1")),
                    _clean_text(row.get("Address2")),
                    _clean_text(row.get("City")),
                    _clean_text(row.get("State")),
                    _clean_text(row.get("Zip")),
                    _clean_text(row.get("Office")),
                    _clean_text(row.get("DistrictType")),
                    _clean_text(row.get("District")),
                    _clean_text(row.get("ResidenceCounty")),
                    _clean_text(row.get("PartyAffiliation")),
                    _to_bool(row.get("RedactionRequested")),
                    file_path.name,
                    row_num,
                )

    total = 0
    for chunk in _chunked(rows()):
        conn.executemany(insert_sql, chunk)
        total += len(chunk)
    conn.commit()
    return total


def load_cmte_candidate_links_file(conn: sqlite3.Connection, file_path: Path) -> int:
    insert_sql = """
        INSERT INTO bulk_cmte_candidate_links_clean (
            link_record_id, committee_id_sbe, candidate_id, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?)
    """

    def rows():
        with file_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row_num, row in enumerate(reader, start=2):
                yield (
                    _to_int(row.get("ID")),
                    _to_int(row.get("CommitteeID")),
                    _to_int(row.get("CandidateID")),
                    file_path.name,
                    row_num,
                )

    total = 0
    for chunk in _chunked(rows()):
        conn.executemany(insert_sql, chunk)
        total += len(chunk)
    conn.commit()
    return total


def load_receipts_file(conn: sqlite3.Connection, file_path: Path) -> int:
    insert_sql = """
        INSERT INTO bulk_receipts_clean (
            receipt_record_id, committee_id_sbe, filed_doc_id, electronic_transaction_id,
            last_or_business_name, first_name, received_date,
            amount, aggregate_amount, loan_amount,
            occupation, employer, address_line_1, address_line_2, city, state, postal_code,
            d2_part_code, description,
            vendor_last_or_business_name, vendor_first_name,
            vendor_address_line_1, vendor_address_line_2, vendor_city, vendor_state, vendor_postal_code,
            is_archived, country, redaction_requested,
            source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def rows():
        with file_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row_num, row in enumerate(reader, start=2):
                yield (
                    _to_int(row.get("ID")),
                    _to_int(row.get("CommitteeID")),
                    _to_int(row.get("FiledDocID")),
                    _clean_text(row.get("ETransID")),
                    _clean_text(row.get("LastOnlyName")),
                    _clean_text(row.get("FirstName")),
                    _clean_text(row.get("RcvDate")),
                    _to_float(row.get("Amount")),
                    _to_float(row.get("AggregateAmount")),
                    _to_float(row.get("LoanAmount")),
                    _clean_text(row.get("Occupation")),
                    _clean_text(row.get("Employer")),
                    _clean_text(row.get("Address1")),
                    _clean_text(row.get("Address2")),
                    _clean_text(row.get("City")),
                    _clean_text(row.get("State")),
                    _clean_text(row.get("Zip")),
                    _clean_text(row.get("D2Part")),
                    _clean_text(row.get("Description")),
                    _clean_text(row.get("VendorLastOnlyName")),
                    _clean_text(row.get("VendorFirstName")),
                    _clean_text(row.get("VendorAddress1")),
                    _clean_text(row.get("VendorAddress2")),
                    _clean_text(row.get("VendorCity")),
                    _clean_text(row.get("VendorState")),
                    _clean_text(row.get("VendorZip")),
                    _to_bool(row.get("Archived")),
                    _clean_text(row.get("Country")),
                    _to_bool(row.get("RedactionRequested")),
                    file_path.name,
                    row_num,
                )

    total = 0
    for chunk in _chunked(rows(), size=10000):
        conn.executemany(insert_sql, chunk)
        total += len(chunk)
    conn.commit()
    return total


def build_committee_d2_join(conn: sqlite3.Connection) -> int:
    conn.executescript(
        """
        CREATE TABLE bulk_committee_d2_totals AS
        SELECT
            d2.d2_totals_record_id,
            d2.committee_id_sbe,
            c.committee_name,
            c.reference_name,
            c.committee_type,
            c.party_affiliation,
            c.committee_status_code,
            c.city AS committee_city,
            c.state AS committee_state,
            c.postal_code AS committee_postal_code,
            c.committee_purpose,
            d2.filed_doc_id,
            d2.beginning_funds_available,
            d2.individual_contributions_itemized,
            d2.individual_contributions_non_itemized,
            d2.transfers_in_itemized,
            d2.transfers_in_non_itemized,
            d2.loans_received_itemized,
            d2.loans_received_non_itemized,
            d2.other_receipts_itemized,
            d2.other_receipts_non_itemized,
            d2.total_receipts,
            d2.in_kind_contributions_itemized,
            d2.in_kind_contributions_non_itemized,
            d2.total_in_kind_contributions,
            d2.transfers_out_itemized,
            d2.transfers_out_non_itemized,
            d2.loans_made_itemized,
            d2.loans_made_non_itemized,
            d2.expenditures_itemized,
            d2.expenditures_non_itemized,
            d2.independent_expenditures_itemized,
            d2.independent_expenditures_non_itemized,
            d2.total_expenditures,
            d2.debts_obligations_itemized,
            d2.debts_obligations_non_itemized,
            d2.total_debts_obligations,
            d2.total_investments,
            d2.ending_funds_available,
            d2.is_archived,
            d2.source_file AS d2_source_file,
            c.source_file AS committee_source_file
        FROM bulk_d2_totals_clean d2
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = d2.committee_id_sbe;

        CREATE INDEX idx_bulk_join_committee_id ON bulk_committee_d2_totals(committee_id_sbe);
        CREATE INDEX idx_bulk_join_filed_doc_id ON bulk_committee_d2_totals(filed_doc_id);
        """
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM bulk_committee_d2_totals").fetchone()[0]


def build_candidate_committee_joins(conn: sqlite3.Connection) -> dict:
    conn.executescript(
        """
        CREATE TABLE bulk_committee_candidate_links AS
        SELECT
            l.link_record_id,
            l.committee_id_sbe,
            l.candidate_id,
            c.committee_name,
            c.reference_name,
            c.committee_type,
            c.party_affiliation AS committee_party_affiliation,
            c.committee_status_code,
            c.city AS committee_city,
            c.state AS committee_state,
            c.postal_code AS committee_postal_code,
            c.committee_purpose,
            cand.last_name,
            cand.first_name,
            cand.candidate_full_name,
            cand.office_sought,
            cand.district_type,
            cand.district,
            cand.residence_county,
            cand.party_affiliation AS candidate_party_affiliation,
            cand.city AS candidate_city,
            cand.state AS candidate_state,
            cand.postal_code AS candidate_postal_code,
            l.source_file AS link_source_file,
            cand.source_file AS candidate_source_file,
            c.source_file AS committee_source_file
        FROM bulk_cmte_candidate_links_clean l
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = l.committee_id_sbe
        LEFT JOIN bulk_candidates_clean cand
          ON cand.candidate_id = l.candidate_id;

        CREATE INDEX idx_bulk_cc_links_committee_id ON bulk_committee_candidate_links(committee_id_sbe);
        CREATE INDEX idx_bulk_cc_links_candidate_id ON bulk_committee_candidate_links(candidate_id);

        CREATE TABLE bulk_candidate_committee_d2_totals AS
        SELECT
            cc.link_record_id,
            cc.committee_id_sbe,
            cc.candidate_id,
            cc.candidate_full_name,
            cc.office_sought,
            cc.district_type,
            cc.district,
            cc.candidate_party_affiliation,
            cc.committee_name,
            cc.committee_type,
            cc.committee_party_affiliation,
            d2.filed_doc_id,
            d2.total_receipts,
            d2.total_expenditures,
            d2.ending_funds_available,
            d2.is_archived
        FROM bulk_committee_candidate_links cc
        LEFT JOIN bulk_d2_totals_clean d2
          ON d2.committee_id_sbe = cc.committee_id_sbe;

        CREATE INDEX idx_bulk_cand_d2_candidate_id ON bulk_candidate_committee_d2_totals(candidate_id);
        CREATE INDEX idx_bulk_cand_d2_committee_id ON bulk_candidate_committee_d2_totals(committee_id_sbe);

        CREATE TABLE bulk_candidate_committee_finance_agg AS
        SELECT
            cc.candidate_id,
            cc.candidate_full_name,
            cc.office_sought,
            cc.district_type,
            cc.district,
            cc.candidate_party_affiliation,
            cc.committee_id_sbe,
            cc.committee_name,
            cc.committee_type,
            cc.committee_party_affiliation,
            COUNT(DISTINCT d2.filed_doc_id) AS filing_count,
            COALESCE(SUM(d2.total_receipts), 0) AS sum_total_receipts,
            COALESCE(SUM(d2.total_expenditures), 0) AS sum_total_expenditures,
            COALESCE(MAX(d2.ending_funds_available), 0) AS max_ending_funds_available,
            COALESCE(SUM(CASE WHEN d2.is_archived = 1 THEN 1 ELSE 0 END), 0) AS archived_filing_count
        FROM bulk_committee_candidate_links cc
        LEFT JOIN bulk_d2_totals_clean d2
          ON d2.committee_id_sbe = cc.committee_id_sbe
        GROUP BY
            cc.candidate_id,
            cc.candidate_full_name,
            cc.office_sought,
            cc.district_type,
            cc.district,
            cc.candidate_party_affiliation,
            cc.committee_id_sbe,
            cc.committee_name,
            cc.committee_type,
            cc.committee_party_affiliation;

        CREATE INDEX idx_bulk_cand_agg_candidate_id ON bulk_candidate_committee_finance_agg(candidate_id);
        CREATE INDEX idx_bulk_cand_agg_committee_id ON bulk_candidate_committee_finance_agg(committee_id_sbe);
        """
    )
    conn.commit()

    return {
        "committee_candidate_links_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_committee_candidate_links"
        ).fetchone()[0],
        "candidate_committee_d2_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_candidate_committee_d2_totals"
        ).fetchone()[0],
        "candidate_committee_agg_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_candidate_committee_finance_agg"
        ).fetchone()[0],
    }


def build_receipts_joins(conn: sqlite3.Connection) -> dict:
    conn.executescript(
        """
        CREATE TABLE bulk_committee_receipts AS
        SELECT
            r.receipt_record_id,
            r.committee_id_sbe,
            c.committee_name,
            c.reference_name,
            c.committee_type,
            c.party_affiliation AS committee_party_affiliation,
            c.committee_status_code,
            c.city AS committee_city,
            c.state AS committee_state,
            c.postal_code AS committee_postal_code,
            r.filed_doc_id,
            r.electronic_transaction_id,
            r.last_or_business_name,
            r.first_name,
            r.received_date,
            r.amount,
            r.aggregate_amount,
            r.loan_amount,
            r.occupation,
            r.employer,
            r.address_line_1,
            r.address_line_2,
            r.city,
            r.state,
            r.postal_code,
            r.d2_part_code,
            r.description,
            r.vendor_last_or_business_name,
            r.vendor_first_name,
            r.vendor_address_line_1,
            r.vendor_address_line_2,
            r.vendor_city,
            r.vendor_state,
            r.vendor_postal_code,
            r.is_archived,
            r.country,
            r.redaction_requested,
            r.source_file AS receipt_source_file,
            c.source_file AS committee_source_file
        FROM bulk_receipts_clean r
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = r.committee_id_sbe;

        CREATE INDEX idx_bulk_committee_receipts_committee_id ON bulk_committee_receipts(committee_id_sbe);
        CREATE INDEX idx_bulk_committee_receipts_filed_doc_id ON bulk_committee_receipts(filed_doc_id);
        CREATE INDEX idx_bulk_committee_receipts_date ON bulk_committee_receipts(received_date);
        CREATE INDEX idx_bulk_committee_receipts_d2_part ON bulk_committee_receipts(d2_part_code);

        CREATE TABLE bulk_d2_receipts_recon AS
        WITH receipts_by_filing AS (
            SELECT
                committee_id_sbe,
                filed_doc_id,
                COUNT(*) AS receipt_row_count,
                COALESCE(SUM(amount), 0) AS receipts_amount_sum,
                MIN(received_date) AS first_receipt_date,
                MAX(received_date) AS last_receipt_date
            FROM bulk_receipts_clean
            GROUP BY committee_id_sbe, filed_doc_id
        )
        SELECT
            d2.d2_totals_record_id,
            d2.committee_id_sbe,
            c.committee_name,
            d2.filed_doc_id,
            d2.total_receipts AS d2_total_receipts,
            d2.total_expenditures AS d2_total_expenditures,
            d2.ending_funds_available,
            d2.is_archived,
            COALESCE(rf.receipt_row_count, 0) AS receipt_row_count,
            COALESCE(rf.receipts_amount_sum, 0) AS receipts_amount_sum,
            rf.first_receipt_date,
            rf.last_receipt_date,
            COALESCE(rf.receipts_amount_sum, 0) - COALESCE(d2.total_receipts, 0) AS receipts_minus_d2_total
        FROM bulk_d2_totals_clean d2
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = d2.committee_id_sbe
        LEFT JOIN receipts_by_filing rf
          ON rf.committee_id_sbe = d2.committee_id_sbe
         AND rf.filed_doc_id = d2.filed_doc_id;

        CREATE INDEX idx_bulk_d2_receipts_recon_committee_id ON bulk_d2_receipts_recon(committee_id_sbe);
        CREATE INDEX idx_bulk_d2_receipts_recon_filed_doc_id ON bulk_d2_receipts_recon(filed_doc_id);

        CREATE TABLE bulk_candidate_committee_receipts_agg AS
        SELECT
            cc.candidate_id,
            cc.candidate_full_name,
            cc.office_sought,
            cc.district_type,
            cc.district,
            cc.candidate_party_affiliation,
            cc.committee_id_sbe,
            cc.committee_name,
            cc.committee_type,
            cc.committee_party_affiliation,
            COUNT(r.receipt_record_id) AS receipt_count,
            COUNT(DISTINCT r.filed_doc_id) AS filing_count_with_receipts,
            COALESCE(SUM(r.amount), 0) AS sum_receipt_amount,
            COALESCE(SUM(r.aggregate_amount), 0) AS sum_aggregate_amount,
            COALESCE(SUM(r.loan_amount), 0) AS sum_loan_amount,
            COALESCE(SUM(CASE WHEN r.d2_part_code LIKE '1%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_1_contributions,
            COALESCE(SUM(CASE WHEN r.d2_part_code LIKE '2%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_2_transfers_in,
            COALESCE(SUM(CASE WHEN r.d2_part_code LIKE '3%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_3_loans_received,
            COALESCE(SUM(CASE WHEN r.d2_part_code LIKE '4%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_4_other_receipts,
            COALESCE(SUM(CASE WHEN r.d2_part_code LIKE '5%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_5_expenditures,
            COALESCE(SUM(CASE WHEN r.d2_part_code LIKE '8%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_8_debts,
            MIN(r.received_date) AS first_receipt_date,
            MAX(r.received_date) AS last_receipt_date
        FROM bulk_committee_candidate_links cc
        LEFT JOIN bulk_receipts_clean r
          ON r.committee_id_sbe = cc.committee_id_sbe
        GROUP BY
            cc.candidate_id,
            cc.candidate_full_name,
            cc.office_sought,
            cc.district_type,
            cc.district,
            cc.candidate_party_affiliation,
            cc.committee_id_sbe,
            cc.committee_name,
            cc.committee_type,
            cc.committee_party_affiliation;

        CREATE INDEX idx_bulk_candidate_receipts_agg_candidate_id ON bulk_candidate_committee_receipts_agg(candidate_id);
        CREATE INDEX idx_bulk_candidate_receipts_agg_committee_id ON bulk_candidate_committee_receipts_agg(committee_id_sbe);
        """
    )
    conn.commit()

    return {
        "committee_receipts_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_committee_receipts"
        ).fetchone()[0],
        "d2_receipts_recon_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_d2_receipts_recon"
        ).fetchone()[0],
        "candidate_committee_receipts_agg_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_candidate_committee_receipts_agg"
        ).fetchone()[0],
    }


def import_bulk_download(conn: sqlite3.Connection, directory: Path) -> dict:
    directory = Path(directory)
    committees_file = find_latest_file(directory, COMMITTEES_PREFIX, required=True)
    d2_file = find_latest_file(directory, D2_TOTALS_PREFIX, required=True)
    candidates_file = find_latest_file(directory, CANDIDATES_PREFIX, required=False)
    links_file = find_latest_file(directory, CMTE_CANDIDATE_LINKS_PREFIX, required=False)
    receipts_file = find_latest_file(directory, RECEIPTS_PREFIX, required=False)

    init_bulk_tables(conn)

    committees_loaded = load_committees_file(conn, committees_file)
    d2_loaded = load_d2_totals_file(conn, d2_file)

    candidates_loaded = 0
    if candidates_file:
        candidates_loaded = load_candidates_file(conn, candidates_file)

    links_loaded = 0
    if links_file:
        links_loaded = load_cmte_candidate_links_file(conn, links_file)

    receipts_loaded = 0
    if receipts_file:
        receipts_loaded = load_receipts_file(conn, receipts_file)

    joined_rows = build_committee_d2_join(conn)
    candidate_join_stats = build_candidate_committee_joins(conn)
    receipts_join_stats = build_receipts_joins(conn)

    unmatched_d2_committee_ids = conn.execute(
        """
        SELECT COUNT(*)
        FROM bulk_d2_totals_clean d2
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = d2.committee_id_sbe
        WHERE c.committee_id_sbe IS NULL
        """
    ).fetchone()[0]

    unmatched_links_candidate_id = conn.execute(
        """
        SELECT COUNT(*)
        FROM bulk_cmte_candidate_links_clean l
        LEFT JOIN bulk_candidates_clean cand
          ON cand.candidate_id = l.candidate_id
        WHERE cand.candidate_id IS NULL
        """
    ).fetchone()[0]

    links_id_matches_candidate_id = conn.execute(
        """
        SELECT COUNT(*)
        FROM bulk_cmte_candidate_links_clean l
        JOIN bulk_candidates_clean cand
          ON cand.candidate_id = l.link_record_id
        """
    ).fetchone()[0]

    unmatched_receipts_committee_ids = conn.execute(
        """
        SELECT COUNT(*)
        FROM bulk_receipts_clean r
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = r.committee_id_sbe
        WHERE c.committee_id_sbe IS NULL
        """
    ).fetchone()[0]

    unmatched_receipts_d2_filed_docs = conn.execute(
        """
        SELECT COUNT(*)
        FROM bulk_receipts_clean r
        LEFT JOIN bulk_d2_totals_clean d2
          ON d2.committee_id_sbe = r.committee_id_sbe
         AND d2.filed_doc_id = r.filed_doc_id
        WHERE d2.d2_totals_record_id IS NULL
        """
    ).fetchone()[0]

    return {
        "committees_file": committees_file.name,
        "d2_file": d2_file.name,
        "candidates_file": candidates_file.name if candidates_file else None,
        "cmte_candidate_links_file": links_file.name if links_file else None,
        "receipts_file": receipts_file.name if receipts_file else None,
        "committees_loaded": committees_loaded,
        "d2_totals_loaded": d2_loaded,
        "candidates_loaded": candidates_loaded,
        "cmte_candidate_links_loaded": links_loaded,
        "receipts_loaded": receipts_loaded,
        "committee_d2_join_rows": joined_rows,
        "unmatched_d2_committee_ids": unmatched_d2_committee_ids,
        "unmatched_links_candidate_ids": unmatched_links_candidate_id,
        "unmatched_receipts_committee_ids": unmatched_receipts_committee_ids,
        "unmatched_receipts_d2_filed_docs": unmatched_receipts_d2_filed_docs,
        "links_id_matches_candidates_id_count": links_id_matches_candidate_id,
        **candidate_join_stats,
        **receipts_join_stats,
    }
