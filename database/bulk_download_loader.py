"""Load and normalize Illinois bulk-download TXT files into renamed SQL tables."""
from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
import sqlite3
import sys
from datetime import datetime
from typing import Iterable, Optional


COMMITTEES_PREFIX = "committees_"
D2_TOTALS_PREFIX = "d2totals_"
CANDIDATES_PREFIX = "candidates_"
CMTE_CANDIDATE_LINKS_PREFIX = "cmtecandidatelinks_"
RECEIPTS_PREFIX = "receipts_"
EXPENDITURES_PREFIX = "expenditures_"

EXTREME_EXPENDITURE_AMOUNT_THRESHOLD = 10_000_000.0
EXTREME_RECEIPT_AMOUNT_THRESHOLD = 10_000_000.0


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


@lru_cache(maxsize=131072)
def _normalize_bulk_receipt_date(value: str | None) -> tuple[Optional[str], Optional[str]]:
    """Normalize receipt date to YYYY-MM-DD and preserve raw datetime text when present."""
    cleaned = _clean_text(value)
    if cleaned is None:
        return None, None

    raw_datetime = cleaned if len(cleaned) > 10 else None

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%m/%d/%y",
    ):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.date().isoformat(), raw_datetime
        except ValueError:
            continue

    iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", cleaned)
    if iso_match:
        return iso_match.group(0), raw_datetime

    us_match = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", cleaned)
    if us_match:
        candidate = us_match.group(0)
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                parsed = datetime.strptime(candidate, fmt)
                return parsed.date().isoformat(), raw_datetime
            except ValueError:
                continue

    return cleaned, raw_datetime


def _normalize_bool(value: str | None) -> tuple[Optional[int], bool]:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None, True
    parsed = _to_bool(cleaned)
    if parsed is None:
        return None, False
    return parsed, True


def _normalize_expenditure_d2_part(value: str | None) -> Optional[str]:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    normalized = cleaned.upper()
    if normalized[:1] not in {"6", "7", "8", "9"}:
        return None
    return normalized


def _serialize_row_for_reject(row: dict) -> str:
    normalized: dict[str, object] = {}
    for key, value in row.items():
        if key is None:
            normalized["_extra_columns"] = value
        else:
            normalized[str(key)] = value
    return json.dumps(normalized, ensure_ascii=True, sort_keys=True)


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
        DROP TABLE IF EXISTS bulk_expenditures_clean;
        DROP TABLE IF EXISTS bulk_expenditures_rejects;
        DROP TABLE IF EXISTS bulk_committee_d2_totals;
        DROP TABLE IF EXISTS bulk_committee_candidate_links;
        DROP TABLE IF EXISTS bulk_candidate_committee_d2_totals;
        DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg;
        DROP TABLE IF EXISTS bulk_committee_receipts;
        DROP TABLE IF EXISTS bulk_committee_expenditures;
        DROP TABLE IF EXISTS bulk_d2_receipts_recon;
        DROP TABLE IF EXISTS bulk_d2_expenditures_recon;
        DROP TABLE IF EXISTS bulk_candidate_committee_receipts_agg;
        DROP TABLE IF EXISTS bulk_candidate_committee_expenditures_agg;

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
            received_datetime_raw TEXT,
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
            is_amount_anomalous INTEGER,
            anomaly_reason TEXT,
            source_file TEXT,
            source_row_number INTEGER
        );

        CREATE INDEX idx_bulk_receipts_record_id ON bulk_receipts_clean(receipt_record_id);
        CREATE INDEX idx_bulk_receipts_committee_id ON bulk_receipts_clean(committee_id_sbe);
        CREATE INDEX idx_bulk_receipts_filed_doc_id ON bulk_receipts_clean(filed_doc_id);
        CREATE INDEX idx_bulk_receipts_received_date ON bulk_receipts_clean(received_date);
        CREATE INDEX idx_bulk_receipts_d2_part_code ON bulk_receipts_clean(d2_part_code);
        CREATE INDEX idx_bulk_receipts_name ON bulk_receipts_clean(last_or_business_name, first_name);
        CREATE INDEX idx_bulk_receipts_amount_anomaly ON bulk_receipts_clean(is_amount_anomalous);

        CREATE TABLE bulk_expenditures_clean (
            bulk_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            expenditure_record_id INTEGER,
            committee_id_sbe INTEGER,
            filed_doc_id INTEGER,
            electronic_transaction_id TEXT,
            payee_last_or_business_name TEXT,
            payee_first_name TEXT,
            expended_date TEXT,
            amount REAL,
            aggregate_amount REAL,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            d2_part_code TEXT,
            purpose TEXT,
            candidate_name TEXT,
            office TEXT,
            is_supporting INTEGER,
            is_opposing INTEGER,
            is_archived INTEGER,
            country TEXT,
            redaction_requested INTEGER,
            is_amount_anomalous INTEGER,
            anomaly_reason TEXT,
            source_file TEXT,
            source_row_number INTEGER
        );

        CREATE INDEX idx_bulk_expenditures_record_id ON bulk_expenditures_clean(expenditure_record_id);
        CREATE INDEX idx_bulk_expenditures_committee_id ON bulk_expenditures_clean(committee_id_sbe);
        CREATE INDEX idx_bulk_expenditures_filed_doc_id ON bulk_expenditures_clean(filed_doc_id);
        CREATE INDEX idx_bulk_expenditures_expended_date ON bulk_expenditures_clean(expended_date);
        CREATE INDEX idx_bulk_expenditures_d2_part_code ON bulk_expenditures_clean(d2_part_code);
        CREATE INDEX idx_bulk_expenditures_payee_name ON bulk_expenditures_clean(payee_last_or_business_name, payee_first_name);
        CREATE INDEX idx_bulk_expenditures_amount_anomaly ON bulk_expenditures_clean(is_amount_anomalous);

        CREATE TABLE bulk_expenditures_rejects (
            reject_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT,
            source_row_number INTEGER,
            reject_reason TEXT,
            raw_row_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX idx_bulk_expenditure_rejects_source_row ON bulk_expenditures_rejects(source_file, source_row_number);
        CREATE INDEX idx_bulk_expenditure_rejects_reason ON bulk_expenditures_rejects(reject_reason);
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


def load_receipts_file(conn: sqlite3.Connection, file_path: Path) -> dict:
    insert_sql = """
        INSERT INTO bulk_receipts_clean (
            receipt_record_id, committee_id_sbe, filed_doc_id, electronic_transaction_id,
            last_or_business_name, first_name, received_date, received_datetime_raw,
            amount, aggregate_amount, loan_amount,
            occupation, employer, address_line_1, address_line_2, city, state, postal_code,
            d2_part_code, description,
            vendor_last_or_business_name, vendor_first_name,
            vendor_address_line_1, vendor_address_line_2, vendor_city, vendor_state, vendor_postal_code,
            is_archived, country, redaction_requested,
            is_amount_anomalous, anomaly_reason,
            source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    loaded = 0
    anomaly_rows = 0
    batch: list[tuple] = []

    def flush_batch() -> None:
        nonlocal loaded
        if batch:
            conn.executemany(insert_sql, batch)
            loaded += len(batch)
            batch.clear()

    with file_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row_num, row in enumerate(reader, start=2):
            received_date, received_datetime_raw = _normalize_bulk_receipt_date(row.get("RcvDate"))
            amount = _to_float(row.get("Amount"))

            is_amount_anomalous = 0
            anomaly_reason = None
            if amount is not None and abs(amount) >= EXTREME_RECEIPT_AMOUNT_THRESHOLD:
                is_amount_anomalous = 1
                anomaly_reason = f"abs(amount)>={EXTREME_RECEIPT_AMOUNT_THRESHOLD:.0f}"
                anomaly_rows += 1

            batch.append((
                _to_int(row.get("ID")),
                _to_int(row.get("CommitteeID")),
                _to_int(row.get("FiledDocID")),
                _clean_text(row.get("ETransID")),
                _clean_text(row.get("LastOnlyName")),
                _clean_text(row.get("FirstName")),
                received_date,
                received_datetime_raw,
                amount,
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
                is_amount_anomalous,
                anomaly_reason,
                file_path.name,
                row_num,
            ))

            if len(batch) >= 10000:
                flush_batch()

    flush_batch()
    conn.commit()
    return {
        "receipts_loaded": loaded,
        "receipt_amount_anomaly_rows": anomaly_rows,
    }


def load_expenditures_file(conn: sqlite3.Connection, file_path: Path) -> dict:
    insert_sql = """
        INSERT INTO bulk_expenditures_clean (
            expenditure_record_id, committee_id_sbe, filed_doc_id, electronic_transaction_id,
            payee_last_or_business_name, payee_first_name, expended_date,
            amount, aggregate_amount,
            address_line_1, address_line_2, city, state, postal_code,
            d2_part_code, purpose, candidate_name, office,
            is_supporting, is_opposing, is_archived,
            country, redaction_requested,
            is_amount_anomalous, anomaly_reason,
            source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    reject_insert_sql = """
        INSERT INTO bulk_expenditures_rejects (
            source_file, source_row_number, reject_reason, raw_row_json
        ) VALUES (?, ?, ?, ?)
    """

    loaded = 0
    rejected = 0
    anomaly_rows = 0
    clean_batch: list[tuple] = []
    reject_batch: list[tuple] = []

    def flush_batches() -> None:
        nonlocal loaded, rejected
        if clean_batch:
            conn.executemany(insert_sql, clean_batch)
            loaded += len(clean_batch)
            clean_batch.clear()
        if reject_batch:
            conn.executemany(reject_insert_sql, reject_batch)
            rejected += len(reject_batch)
            reject_batch.clear()

    with file_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row_num, row in enumerate(reader, start=2):
            reject_reason = None

            if None in row and row.get(None):
                reject_reason = "malformed_extra_columns"

            expenditure_record_id = _to_int(row.get("ID"))
            committee_id_sbe = _to_int(row.get("CommitteeID"))
            filed_doc_id = _to_int(row.get("FiledDocID"))
            amount = _to_float(row.get("Amount"))
            d2_part_code = _normalize_expenditure_d2_part(row.get("D2Part"))

            is_archived, archived_valid = _normalize_bool(row.get("Archived"))
            is_supporting, supporting_valid = _normalize_bool(row.get("Supporting"))
            is_opposing, opposing_valid = _normalize_bool(row.get("Opposing"))
            redaction_requested, redaction_valid = _normalize_bool(row.get("RedactionRequested"))

            if reject_reason is None and expenditure_record_id is None:
                reject_reason = "missing_expenditure_id"
            if reject_reason is None and committee_id_sbe is None:
                reject_reason = "missing_committee_id"
            if reject_reason is None and filed_doc_id is None:
                reject_reason = "missing_filed_doc_id"
            if reject_reason is None and amount is None:
                reject_reason = "invalid_amount"
            if reject_reason is None and d2_part_code is None:
                reject_reason = "invalid_d2_part_allowlist_6_7_8_9"
            if reject_reason is None and not archived_valid:
                reject_reason = "invalid_archived_boolean"
            if reject_reason is None and not supporting_valid:
                reject_reason = "invalid_supporting_boolean"
            if reject_reason is None and not opposing_valid:
                reject_reason = "invalid_opposing_boolean"
            if reject_reason is None and not redaction_valid:
                reject_reason = "invalid_redaction_boolean"
            if reject_reason is None and is_supporting == 1 and is_opposing == 1:
                reject_reason = "supporting_and_opposing_both_true"

            if reject_reason:
                reject_batch.append((file_path.name, row_num, reject_reason, _serialize_row_for_reject(row)))
            else:
                is_amount_anomalous = 1 if abs(float(amount)) >= EXTREME_EXPENDITURE_AMOUNT_THRESHOLD else 0
                anomaly_reason = (
                    f"abs(amount)>={EXTREME_EXPENDITURE_AMOUNT_THRESHOLD:.0f}" if is_amount_anomalous else None
                )
                if is_amount_anomalous:
                    anomaly_rows += 1

                clean_batch.append(
                    (
                        expenditure_record_id,
                        committee_id_sbe,
                        filed_doc_id,
                        _clean_text(row.get("ETransID")),
                        _clean_text(row.get("LastOnlyName")),
                        _clean_text(row.get("FirstName")),
                        _clean_text(row.get("ExpendedDate")),
                        amount,
                        _to_float(row.get("AggregateAmount")),
                        _clean_text(row.get("Address1")),
                        _clean_text(row.get("Address2")),
                        _clean_text(row.get("City")),
                        _clean_text(row.get("State")),
                        _clean_text(row.get("Zip")),
                        d2_part_code,
                        _clean_text(row.get("Purpose")),
                        _clean_text(row.get("CandidateName")),
                        _clean_text(row.get("Office")),
                        is_supporting,
                        is_opposing,
                        is_archived,
                        _clean_text(row.get("Country")),
                        redaction_requested,
                        is_amount_anomalous,
                        anomaly_reason,
                        file_path.name,
                        row_num,
                    )
                )

            if len(clean_batch) >= 10000 or len(reject_batch) >= 2000:
                flush_batches()

    flush_batches()
    conn.commit()
    return {
        "expenditures_loaded": loaded,
        "expenditures_rejected": rejected,
        "expenditure_amount_anomaly_rows": anomaly_rows,
    }


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
        WITH filing_periods AS (
            SELECT
                committee_id_sbe,
                filed_doc_id,
                CAST(STRFTIME('%Y', MAX(received_date)) AS INTEGER) AS period_year,
                MIN(received_date) AS period_start_date,
                MAX(received_date) AS period_end_date
            FROM bulk_receipts_clean
            WHERE received_date IS NOT NULL
              AND LENGTH(received_date) >= 4
            GROUP BY committee_id_sbe, filed_doc_id
        )
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
            fp.period_year,
            CASE
                WHEN fp.period_year IS NULL THEN NULL
                WHEN fp.period_year % 2 = 0 THEN fp.period_year
                ELSE fp.period_year + 1
            END AS election_cycle,
            COUNT(DISTINCT CASE WHEN COALESCE(d2.is_archived, 0) = 0 THEN d2.filed_doc_id END) AS filing_count,
            COALESCE(
                SUM(CASE WHEN COALESCE(d2.is_archived, 0) = 0 THEN COALESCE(d2.total_receipts, 0) ELSE 0 END),
                0
            ) AS sum_total_receipts,
            COALESCE(
                SUM(CASE WHEN COALESCE(d2.is_archived, 0) = 0 THEN COALESCE(d2.total_expenditures, 0) ELSE 0 END),
                0
            ) AS sum_total_expenditures,
            COALESCE(
                MAX(CASE WHEN COALESCE(d2.is_archived, 0) = 0 THEN d2.ending_funds_available END),
                0
            ) AS max_ending_funds_available,
            COALESCE(SUM(CASE WHEN COALESCE(d2.is_archived, 0) = 1 THEN 1 ELSE 0 END), 0) AS archived_filing_count,
            MIN(fp.period_start_date) AS period_start_date,
            MAX(fp.period_end_date) AS period_end_date
        FROM bulk_committee_candidate_links cc
        LEFT JOIN bulk_d2_totals_clean d2
          ON d2.committee_id_sbe = cc.committee_id_sbe
        LEFT JOIN filing_periods fp
          ON fp.committee_id_sbe = d2.committee_id_sbe
         AND fp.filed_doc_id = d2.filed_doc_id
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
            cc.committee_party_affiliation,
            fp.period_year,
            CASE
                WHEN fp.period_year IS NULL THEN NULL
                WHEN fp.period_year % 2 = 0 THEN fp.period_year
                ELSE fp.period_year + 1
            END;

        CREATE INDEX idx_bulk_cand_agg_candidate_id ON bulk_candidate_committee_finance_agg(candidate_id);
        CREATE INDEX idx_bulk_cand_agg_committee_id ON bulk_candidate_committee_finance_agg(committee_id_sbe);
        CREATE INDEX idx_bulk_cand_agg_period_year ON bulk_candidate_committee_finance_agg(period_year);
        CREATE INDEX idx_bulk_cand_agg_election_cycle ON bulk_candidate_committee_finance_agg(election_cycle);
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
            WHERE COALESCE(is_archived, 0) = 0
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
            COUNT(CASE WHEN COALESCE(r.is_archived, 0) = 0 THEN r.receipt_record_id END) AS receipt_count,
            COUNT(DISTINCT CASE WHEN COALESCE(r.is_archived, 0) = 0 THEN r.filed_doc_id END) AS filing_count_with_receipts,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 THEN COALESCE(r.amount, 0) ELSE 0 END), 0) AS sum_receipt_amount,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 THEN COALESCE(r.aggregate_amount, 0) ELSE 0 END), 0) AS sum_aggregate_amount,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 THEN COALESCE(r.loan_amount, 0) ELSE 0 END), 0) AS sum_loan_amount,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 AND r.d2_part_code LIKE '1%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_1_contributions,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 AND r.d2_part_code LIKE '2%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_2_transfers_in,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 AND r.d2_part_code LIKE '3%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_3_loans_received,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 AND r.d2_part_code LIKE '4%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_4_other_receipts,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 AND r.d2_part_code LIKE '5%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_5_expenditures,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 0 AND r.d2_part_code LIKE '8%' THEN r.amount ELSE 0 END), 0) AS sum_amount_part_8_debts,
            COALESCE(SUM(CASE WHEN COALESCE(r.is_archived, 0) = 1 THEN 1 ELSE 0 END), 0) AS archived_receipt_count,
            MIN(CASE WHEN COALESCE(r.is_archived, 0) = 0 THEN r.received_date END) AS first_receipt_date,
            MAX(CASE WHEN COALESCE(r.is_archived, 0) = 0 THEN r.received_date END) AS last_receipt_date
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


def build_expenditures_joins(conn: sqlite3.Connection) -> dict:
    conn.executescript(
        """
        CREATE TABLE bulk_committee_expenditures AS
        SELECT
            e.expenditure_record_id,
            e.committee_id_sbe,
            c.committee_name,
            c.reference_name,
            c.committee_type,
            c.party_affiliation AS committee_party_affiliation,
            c.committee_status_code,
            c.city AS committee_city,
            c.state AS committee_state,
            c.postal_code AS committee_postal_code,
            e.filed_doc_id,
            e.electronic_transaction_id,
            e.payee_last_or_business_name,
            e.payee_first_name,
            e.expended_date,
            e.amount,
            e.aggregate_amount,
            e.address_line_1,
            e.address_line_2,
            e.city,
            e.state,
            e.postal_code,
            e.d2_part_code,
            e.purpose,
            e.candidate_name,
            e.office,
            e.is_supporting,
            e.is_opposing,
            e.is_archived,
            e.country,
            e.redaction_requested,
            e.is_amount_anomalous,
            e.anomaly_reason,
            e.source_file AS expenditure_source_file,
            c.source_file AS committee_source_file
        FROM bulk_expenditures_clean e
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = e.committee_id_sbe;

        CREATE INDEX idx_bulk_committee_expenditures_committee_id ON bulk_committee_expenditures(committee_id_sbe);
        CREATE INDEX idx_bulk_committee_expenditures_filed_doc_id ON bulk_committee_expenditures(filed_doc_id);
        CREATE INDEX idx_bulk_committee_expenditures_date ON bulk_committee_expenditures(expended_date);
        CREATE INDEX idx_bulk_committee_expenditures_d2_part ON bulk_committee_expenditures(d2_part_code);

        CREATE TABLE bulk_d2_expenditures_recon AS
        WITH expenditures_by_filing AS (
            SELECT
                committee_id_sbe,
                filed_doc_id,
                COUNT(*) AS expenditure_row_count,
                COALESCE(SUM(amount), 0) AS expenditures_amount_sum,
                COALESCE(SUM(CASE WHEN d2_part_code LIKE '6%' THEN amount ELSE 0 END), 0) AS sum_part_6_transfers_out,
                COALESCE(SUM(CASE WHEN d2_part_code LIKE '7%' THEN amount ELSE 0 END), 0) AS sum_part_7_loans_made,
                COALESCE(SUM(CASE WHEN d2_part_code LIKE '8%' THEN amount ELSE 0 END), 0) AS sum_part_8_expenditures,
                COALESCE(SUM(CASE WHEN d2_part_code LIKE '9%' THEN amount ELSE 0 END), 0) AS sum_part_9_independent_expenditures,
                COALESCE(SUM(CASE WHEN COALESCE(is_amount_anomalous, 0) = 1 THEN 1 ELSE 0 END), 0) AS anomaly_row_count,
                MIN(expended_date) AS first_expenditure_date,
                MAX(expended_date) AS last_expenditure_date
            FROM bulk_expenditures_clean
            WHERE COALESCE(is_archived, 0) = 0
            GROUP BY committee_id_sbe, filed_doc_id
        )
        SELECT
            d2.d2_totals_record_id,
            d2.committee_id_sbe,
            c.committee_name,
            d2.filed_doc_id,
            d2.transfers_out_itemized AS d2_transfers_out_itemized,
            d2.loans_made_itemized AS d2_loans_made_itemized,
            d2.expenditures_itemized AS d2_expenditures_itemized,
            d2.independent_expenditures_itemized AS d2_independent_expenditures_itemized,
            (
                COALESCE(d2.transfers_out_itemized, 0)
                + COALESCE(d2.loans_made_itemized, 0)
                + COALESCE(d2.expenditures_itemized, 0)
                + COALESCE(d2.independent_expenditures_itemized, 0)
            ) AS d2_itemized_expenditures_total,
            d2.total_expenditures AS d2_total_expenditures,
            d2.ending_funds_available,
            d2.is_archived,
            COALESCE(ef.expenditure_row_count, 0) AS expenditure_row_count,
            COALESCE(ef.expenditures_amount_sum, 0) AS expenditures_amount_sum,
            COALESCE(ef.sum_part_6_transfers_out, 0) AS sum_part_6_transfers_out,
            COALESCE(ef.sum_part_7_loans_made, 0) AS sum_part_7_loans_made,
            COALESCE(ef.sum_part_8_expenditures, 0) AS sum_part_8_expenditures,
            COALESCE(ef.sum_part_9_independent_expenditures, 0) AS sum_part_9_independent_expenditures,
            COALESCE(ef.anomaly_row_count, 0) AS anomaly_row_count,
            ef.first_expenditure_date,
            ef.last_expenditure_date,
            COALESCE(ef.expenditures_amount_sum, 0) - (
                COALESCE(d2.transfers_out_itemized, 0)
                + COALESCE(d2.loans_made_itemized, 0)
                + COALESCE(d2.expenditures_itemized, 0)
                + COALESCE(d2.independent_expenditures_itemized, 0)
            ) AS expenditures_minus_d2_itemized_total,
            COALESCE(ef.sum_part_6_transfers_out, 0) - COALESCE(d2.transfers_out_itemized, 0) AS part_6_minus_d2_transfers_out_itemized,
            COALESCE(ef.sum_part_7_loans_made, 0) - COALESCE(d2.loans_made_itemized, 0) AS part_7_minus_d2_loans_made_itemized,
            COALESCE(ef.sum_part_8_expenditures, 0) - COALESCE(d2.expenditures_itemized, 0) AS part_8_minus_d2_expenditures_itemized,
            COALESCE(ef.sum_part_9_independent_expenditures, 0) - COALESCE(d2.independent_expenditures_itemized, 0) AS part_9_minus_d2_independent_expenditures_itemized
        FROM bulk_d2_totals_clean d2
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = d2.committee_id_sbe
        LEFT JOIN expenditures_by_filing ef
          ON ef.committee_id_sbe = d2.committee_id_sbe
         AND ef.filed_doc_id = d2.filed_doc_id;

        CREATE INDEX idx_bulk_d2_expenditures_recon_committee_id ON bulk_d2_expenditures_recon(committee_id_sbe);
        CREATE INDEX idx_bulk_d2_expenditures_recon_filed_doc_id ON bulk_d2_expenditures_recon(filed_doc_id);

        CREATE TABLE bulk_candidate_committee_expenditures_agg AS
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
            COUNT(CASE WHEN COALESCE(e.is_archived, 0) = 0 THEN e.expenditure_record_id END) AS expenditure_count,
            COUNT(DISTINCT CASE WHEN COALESCE(e.is_archived, 0) = 0 THEN e.filed_doc_id END) AS filing_count_with_expenditures,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_archived, 0) = 0 THEN COALESCE(e.amount, 0) ELSE 0 END), 0) AS sum_expenditure_amount,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_archived, 0) = 0 THEN COALESCE(e.aggregate_amount, 0) ELSE 0 END), 0) AS sum_aggregate_amount,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_archived, 0) = 0 AND e.d2_part_code LIKE '6%' THEN COALESCE(e.amount, 0) ELSE 0 END), 0) AS sum_amount_part_6_transfers_out,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_archived, 0) = 0 AND e.d2_part_code LIKE '7%' THEN COALESCE(e.amount, 0) ELSE 0 END), 0) AS sum_amount_part_7_loans_made,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_archived, 0) = 0 AND e.d2_part_code LIKE '8%' THEN COALESCE(e.amount, 0) ELSE 0 END), 0) AS sum_amount_part_8_expenditures,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_archived, 0) = 0 AND e.d2_part_code LIKE '9%' THEN COALESCE(e.amount, 0) ELSE 0 END), 0) AS sum_amount_part_9_independent_expenditures,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_archived, 0) = 1 THEN 1 ELSE 0 END), 0) AS archived_expenditure_count,
            COALESCE(SUM(CASE WHEN COALESCE(e.is_amount_anomalous, 0) = 1 THEN 1 ELSE 0 END), 0) AS anomaly_expenditure_count,
            MIN(CASE WHEN COALESCE(e.is_archived, 0) = 0 THEN e.expended_date END) AS first_expended_date,
            MAX(CASE WHEN COALESCE(e.is_archived, 0) = 0 THEN e.expended_date END) AS last_expended_date
        FROM bulk_committee_candidate_links cc
        LEFT JOIN bulk_expenditures_clean e
          ON e.committee_id_sbe = cc.committee_id_sbe
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

        CREATE INDEX idx_bulk_candidate_expenditures_agg_candidate_id ON bulk_candidate_committee_expenditures_agg(candidate_id);
        CREATE INDEX idx_bulk_candidate_expenditures_agg_committee_id ON bulk_candidate_committee_expenditures_agg(committee_id_sbe);
        """
    )
    conn.commit()

    return {
        "committee_expenditures_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_committee_expenditures"
        ).fetchone()[0],
        "d2_expenditures_recon_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_d2_expenditures_recon"
        ).fetchone()[0],
        "candidate_committee_expenditures_agg_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_candidate_committee_expenditures_agg"
        ).fetchone()[0],
        "expenditures_rejects_rows": conn.execute(
            "SELECT COUNT(*) FROM bulk_expenditures_rejects"
        ).fetchone()[0],
        "expenditure_amount_anomaly_rows_total": conn.execute(
            "SELECT COUNT(*) FROM bulk_expenditures_clean WHERE COALESCE(is_amount_anomalous, 0) = 1"
        ).fetchone()[0],
    }


def import_bulk_download(conn: sqlite3.Connection, directory: Path) -> dict:
    directory = Path(directory)
    committees_file = find_latest_file(directory, COMMITTEES_PREFIX, required=True)
    d2_file = find_latest_file(directory, D2_TOTALS_PREFIX, required=True)
    candidates_file = find_latest_file(directory, CANDIDATES_PREFIX, required=False)
    links_file = find_latest_file(directory, CMTE_CANDIDATE_LINKS_PREFIX, required=False)
    receipts_file = find_latest_file(directory, RECEIPTS_PREFIX, required=False)
    expenditures_file = find_latest_file(directory, EXPENDITURES_PREFIX, required=False)

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
    receipt_amount_anomaly_rows = 0
    if receipts_file:
        receipts_stats = load_receipts_file(conn, receipts_file)
        receipts_loaded = receipts_stats["receipts_loaded"]
        receipt_amount_anomaly_rows = receipts_stats["receipt_amount_anomaly_rows"]

    expenditures_loaded = 0
    expenditures_rejected = 0
    expenditure_amount_anomaly_rows = 0
    if expenditures_file:
        expenditures_stats = load_expenditures_file(conn, expenditures_file)
        expenditures_loaded = expenditures_stats["expenditures_loaded"]
        expenditures_rejected = expenditures_stats["expenditures_rejected"]
        expenditure_amount_anomaly_rows = expenditures_stats["expenditure_amount_anomaly_rows"]

    joined_rows = build_committee_d2_join(conn)
    candidate_join_stats = build_candidate_committee_joins(conn)
    receipts_join_stats = build_receipts_joins(conn)
    expenditures_join_stats = build_expenditures_joins(conn)

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

    unmatched_expenditures_committee_ids = conn.execute(
        """
        SELECT COUNT(*)
        FROM bulk_expenditures_clean e
        LEFT JOIN bulk_committees_clean c
          ON c.committee_id_sbe = e.committee_id_sbe
        WHERE c.committee_id_sbe IS NULL
        """
    ).fetchone()[0]

    unmatched_expenditures_d2_filed_docs = conn.execute(
        """
        SELECT COUNT(*)
        FROM bulk_expenditures_clean e
        LEFT JOIN bulk_d2_totals_clean d2
          ON d2.committee_id_sbe = e.committee_id_sbe
         AND d2.filed_doc_id = e.filed_doc_id
        WHERE d2.d2_totals_record_id IS NULL
        """
    ).fetchone()[0]

    return {
        "committees_file": committees_file.name,
        "d2_file": d2_file.name,
        "candidates_file": candidates_file.name if candidates_file else None,
        "cmte_candidate_links_file": links_file.name if links_file else None,
        "receipts_file": receipts_file.name if receipts_file else None,
        "expenditures_file": expenditures_file.name if expenditures_file else None,
        "committees_loaded": committees_loaded,
        "d2_totals_loaded": d2_loaded,
        "candidates_loaded": candidates_loaded,
        "cmte_candidate_links_loaded": links_loaded,
        "receipts_loaded": receipts_loaded,
        "expenditures_loaded": expenditures_loaded,
        "expenditures_rejected": expenditures_rejected,
        "expenditure_amount_anomaly_rows": expenditure_amount_anomaly_rows,
        "committee_d2_join_rows": joined_rows,
        "unmatched_d2_committee_ids": unmatched_d2_committee_ids,
        "unmatched_links_candidate_ids": unmatched_links_candidate_id,
        "unmatched_receipts_committee_ids": unmatched_receipts_committee_ids,
        "unmatched_receipts_d2_filed_docs": unmatched_receipts_d2_filed_docs,
        "unmatched_expenditures_committee_ids": unmatched_expenditures_committee_ids,
        "unmatched_expenditures_d2_filed_docs": unmatched_expenditures_d2_filed_docs,
        "links_id_matches_candidates_id_count": links_id_matches_candidate_id,
        **candidate_join_stats,
        **receipts_join_stats,
        **expenditures_join_stats,
    }
