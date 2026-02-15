"""Loader for IRS 527 Political Organization pipe-delimited FullDataFile."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _strip_bom_prefix(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text


def _clean_text(value: str | None) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _to_float(value: str | None) -> Optional[float]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except (ValueError, OverflowError):
        return None


def _to_int(value: str | None) -> Optional[int]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return None


def _safe_get(fields: list[str], idx: int) -> Optional[str]:
    if idx < len(fields):
        return fields[idx]
    return None


def _chunked(rows, size: int = 5000):
    chunk = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _parse_header(fields: list[str]) -> dict:
    """Parse H record: H|date|time|format|"""
    return {
        "record_type": "H",
        "date": _clean_text(_safe_get(fields, 1)),
        "time": _clean_text(_safe_get(fields, 2)),
        "format": _clean_text(_safe_get(fields, 3)),
    }


def _parse_org(fields: list[str]) -> Optional[tuple]:
    """Parse type 1 (organization registration) record.

    Returns tuple for irs527_organizations table.
    """
    if len(fields) < 10:
        return None
    ein = _clean_text(_safe_get(fields, 6))
    if not ein:
        return None
    return (
        ein,                                        # ein
        _to_int(_safe_get(fields, 1)),              # form_id
        _to_int(_safe_get(fields, 2)),              # form_id_seq
        _clean_text(_safe_get(fields, 7)),          # org_name
        _clean_text(_safe_get(fields, 8)),          # address_1
        _clean_text(_safe_get(fields, 9)),          # address_2
        _clean_text(_safe_get(fields, 10)),         # city
        _clean_text(_safe_get(fields, 11)),         # state
        _clean_text(_safe_get(fields, 12)),         # zip
        _clean_text(_safe_get(fields, 13)),         # zip_ext
        _clean_text(_safe_get(fields, 14)),         # email
        _clean_text(_safe_get(fields, 16)),         # custodian_name
        _clean_text(_safe_get(fields, 17)),         # custodian_address_1
        _clean_text(_safe_get(fields, 18)),         # custodian_address_2
        _clean_text(_safe_get(fields, 19)),         # custodian_city
        _clean_text(_safe_get(fields, 20)),         # custodian_state
        _clean_text(_safe_get(fields, 21)),         # custodian_zip
        _clean_text(_safe_get(fields, 22)),         # custodian_zip_ext
        _clean_text(_safe_get(fields, 23)),         # contact_name
        _clean_text(_safe_get(fields, 24)),         # contact_address_1
        _clean_text(_safe_get(fields, 25)),         # contact_address_2
        _clean_text(_safe_get(fields, 26)),         # contact_city
        _clean_text(_safe_get(fields, 27)),         # contact_state
        _clean_text(_safe_get(fields, 28)),         # contact_zip
        _clean_text(_safe_get(fields, 29)),         # contact_zip_ext
        _clean_text(_safe_get(fields, 30)),         # business_address_1
        _clean_text(_safe_get(fields, 31)),         # business_address_2
        _clean_text(_safe_get(fields, 32)),         # business_city
        _clean_text(_safe_get(fields, 33)),         # business_state
        _clean_text(_safe_get(fields, 34)),         # business_zip
        _clean_text(_safe_get(fields, 35)),         # business_zip_ext
        _clean_text(_safe_get(fields, 36)),         # purpose (may span multiple fields)
        _clean_text(_safe_get(fields, 37)),         # material_change_date
        _clean_text(_safe_get(fields, 38)),         # insert_datetime
        _to_int(_safe_get(fields, 39)),             # related_entity_bypass
        _to_int(_safe_get(fields, 40)),             # eain_bypass
        _clean_text(_safe_get(fields, 15)),         # formation_date
    )


def _parse_report(fields: list[str]) -> Optional[tuple]:
    """Parse type 2 (periodic report) record."""
    if len(fields) < 10:
        return None
    form_id = _to_int(_safe_get(fields, 2))
    ein = _clean_text(_safe_get(fields, 10)) or _clean_text(_safe_get(fields, 1))
    if form_id is None or not ein:
        return None

    # Current IRS FullDataFile 8872 layout:
    # idx 45 = TOTAL_SCHED_A, idx 47 = TOTAL_SCHED_B, idx 48 = INSERT_DATETIME.
    # Keep a fallback for older files where totals appeared at lower indexes.
    total_contributions = _to_float(_safe_get(fields, 45))
    if total_contributions is None:
        total_contributions = _to_float(_safe_get(fields, 43))
    total_expenditures = _to_float(_safe_get(fields, 47))
    if total_expenditures is None:
        total_expenditures = _to_float(_safe_get(fields, 44))
    insert_datetime = _clean_text(_safe_get(fields, 48)) or _clean_text(_safe_get(fields, 47))

    return (
        form_id,
        ein,
        _clean_text(_safe_get(fields, 3)),          # period_start
        _clean_text(_safe_get(fields, 4)),          # period_end
        _clean_text(_safe_get(fields, 9)),          # org_name
        _clean_text(_safe_get(fields, 10)),         # org_ein
        _clean_text(_safe_get(fields, 11)),         # org_address_1
        _clean_text(_safe_get(fields, 12)),         # org_address_2
        _clean_text(_safe_get(fields, 13)),         # org_city
        _clean_text(_safe_get(fields, 14)),         # org_state
        _clean_text(_safe_get(fields, 15)),         # org_zip
        _clean_text(_safe_get(fields, 16)),         # org_zip_ext
        _clean_text(_safe_get(fields, 17)),         # email
        _clean_text(_safe_get(fields, 18)),         # formation_date
        _clean_text(_safe_get(fields, 19)),         # custodian_name
        _clean_text(_safe_get(fields, 20)),         # custodian_address_1
        _clean_text(_safe_get(fields, 21)),         # custodian_address_2
        _clean_text(_safe_get(fields, 22)),         # custodian_city
        _clean_text(_safe_get(fields, 23)),         # custodian_state
        _clean_text(_safe_get(fields, 24)),         # custodian_zip
        _clean_text(_safe_get(fields, 25)),         # custodian_zip_ext
        _clean_text(_safe_get(fields, 26)),         # contact_name
        _clean_text(_safe_get(fields, 27)),         # contact_address_1
        _clean_text(_safe_get(fields, 28)),         # contact_address_2
        _clean_text(_safe_get(fields, 29)),         # contact_city
        _clean_text(_safe_get(fields, 30)),         # contact_state
        _clean_text(_safe_get(fields, 31)),         # contact_zip
        _clean_text(_safe_get(fields, 32)),         # contact_zip_ext
        _clean_text(_safe_get(fields, 33)),         # business_address_1
        _clean_text(_safe_get(fields, 34)),         # business_address_2
        _clean_text(_safe_get(fields, 35)),         # business_city
        _clean_text(_safe_get(fields, 36)),         # business_state
        _clean_text(_safe_get(fields, 37)),         # business_zip
        _clean_text(_safe_get(fields, 38)),         # business_zip_ext
        _to_int(_safe_get(fields, 39)),             # qtr_indicator
        _to_float(_safe_get(fields, 40)),           # monthly_amount_1 (monthly report month when present)
        _to_float(_safe_get(fields, 41)),           # monthly_amount_2
        _to_float(_safe_get(fields, 42)),           # monthly_amount_3
        total_contributions,                        # total_contributions (TOTAL_SCHED_A)
        total_expenditures,                         # total_expenditures (TOTAL_SCHED_B)
        insert_datetime,                            # insert_datetime
    )


def _parse_director(fields: list[str]) -> Optional[tuple]:
    """Parse type D (director/officer) record."""
    if len(fields) < 5:
        return None
    form_id = _to_int(_safe_get(fields, 1))
    if form_id is None:
        return None
    return (
        form_id,
        _clean_text(_safe_get(fields, 4)),          # ein
        _clean_text(_safe_get(fields, 3)),          # org_name
        _clean_text(_safe_get(fields, 5)),          # person_name
        _clean_text(_safe_get(fields, 6)),          # title
        _clean_text(_safe_get(fields, 7)),          # address_1
        _clean_text(_safe_get(fields, 8)),          # address_2
        _clean_text(_safe_get(fields, 9)),          # city
        _clean_text(_safe_get(fields, 10)),         # state
        _clean_text(_safe_get(fields, 11)),         # zip
        _clean_text(_safe_get(fields, 12)),         # zip_ext
    )


def _parse_related_org(fields: list[str]) -> Optional[tuple]:
    """Parse type R (related organization) record."""
    if len(fields) < 5:
        return None
    form_id = _to_int(_safe_get(fields, 1))
    if form_id is None:
        return None
    return (
        form_id,
        _clean_text(_safe_get(fields, 4)),          # ein
        _clean_text(_safe_get(fields, 3)),          # org_name
        _clean_text(_safe_get(fields, 5)),          # related_org_name
        _clean_text(_safe_get(fields, 6)),          # relationship_type
        _clean_text(_safe_get(fields, 7)),          # address_1
        _clean_text(_safe_get(fields, 8)),          # address_2
        _clean_text(_safe_get(fields, 9)),          # city
        _clean_text(_safe_get(fields, 10)),         # state
        _clean_text(_safe_get(fields, 11)),         # zip
        _clean_text(_safe_get(fields, 12)),         # zip_ext
    )


def _parse_contribution(fields: list[str]) -> Optional[tuple]:
    """Parse type A (contribution TO 527 org) record."""
    if len(fields) < 5:
        return None
    form_id = _to_int(_safe_get(fields, 1))
    if form_id is None:
        return None
    return (
        form_id,
        _clean_text(_safe_get(fields, 4)),          # ein
        _clean_text(_safe_get(fields, 3)),          # org_name
        _clean_text(_safe_get(fields, 5)),          # contributor_name
        _clean_text(_safe_get(fields, 6)),          # contributor_address
        _clean_text(_safe_get(fields, 7)),          # contributor_address_2
        _clean_text(_safe_get(fields, 8)),          # city
        _clean_text(_safe_get(fields, 9)),          # state
        _clean_text(_safe_get(fields, 10)),         # zip
        _clean_text(_safe_get(fields, 11)),         # zip_ext
        _clean_text(_safe_get(fields, 12)),         # contributor_employer
        _to_float(_safe_get(fields, 13)),           # amount
        _clean_text(_safe_get(fields, 14)),         # contributor_occupation
        _clean_text(_safe_get(fields, 15)),         # date
    )


def _parse_expenditure(fields: list[str]) -> Optional[tuple]:
    """Parse type B (expenditure) record."""
    if len(fields) < 5:
        return None
    form_id = _to_int(_safe_get(fields, 1))
    if form_id is None:
        return None
    return (
        form_id,
        _clean_text(_safe_get(fields, 4)),          # ein
        _clean_text(_safe_get(fields, 3)),          # org_name
        _clean_text(_safe_get(fields, 5)),          # recipient_name
        _clean_text(_safe_get(fields, 6)),          # recipient_address
        _clean_text(_safe_get(fields, 7)),          # recipient_address_2
        _clean_text(_safe_get(fields, 8)),          # city
        _clean_text(_safe_get(fields, 9)),          # state
        _clean_text(_safe_get(fields, 10)),         # zip
        _clean_text(_safe_get(fields, 11)),         # zip_ext
        _clean_text(_safe_get(fields, 12)),         # recipient_employer
        _to_float(_safe_get(fields, 13)),           # amount
        _clean_text(_safe_get(fields, 14)),         # recipient_occupation
        _clean_text(_safe_get(fields, 15)),         # date
        _clean_text(_safe_get(fields, 16)),         # purpose
    )


def _parse_election_authority(fields: list[str]) -> Optional[tuple]:
    """Parse type E (election authority) record."""
    if len(fields) < 3:
        return None
    form_id = _to_int(_safe_get(fields, 1))
    if form_id is None:
        return None
    return (
        form_id,
        _clean_text(_safe_get(fields, 2)),          # election_authority_id
        _clean_text(_safe_get(fields, 3)) if len(fields) > 3 else None,  # state
    )


def _is_illinois_org(parsed: tuple) -> bool:
    """Check if org record has IL state."""
    # state is at index 7 in the org tuple
    state = parsed[7]
    return state is not None and state.upper() == "IL"


def _is_illinois_expenditure(parsed: tuple) -> bool:
    """Check if expenditure has IL state."""
    # state is at index 8 in the expenditure tuple
    state = parsed[8]
    return state is not None and state.upper() == "IL"


def load_irs527_full_file(
    conn: sqlite3.Connection,
    file_path: str | Path,
    *,
    illinois_only: bool = False,
) -> dict:
    """Parse IRS 527 FullDataFile and load into database tables.

    Args:
        conn: SQLite connection.
        file_path: Path to FullDataFile.txt.
        illinois_only: If True, only keep orgs with IL addresses or
            expenditures to IL addresses.

    Returns stats per record type.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"IRS 527 file not found: {file_path}")

    stats = {
        "headers": 0,
        "orgs": 0,
        "orgs_skipped": 0,
        "reports": 0,
        "directors": 0,
        "related_orgs": 0,
        "contributions": 0,
        "contributions_skipped": 0,
        "expenditures": 0,
        "expenditures_skipped": 0,
        "election_authorities": 0,
        "malformed_rows": 0,
        "total_lines": 0,
    }

    # If illinois_only, first pass to collect IL EINs
    il_eins: set[str] | None = None
    if illinois_only:
        il_eins = set()
        logger.info("First pass: collecting Illinois EINs...")
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n\r")
                if not line:
                    continue
                line = _strip_bom_prefix(line)
                fields = line.split("|")
                record_type = fields[0] if fields else ""

                if record_type == "1":
                    parsed = _parse_org(fields)
                    if parsed and _is_illinois_org(parsed):
                        il_eins.add(parsed[0])
                elif record_type == "B":
                    parsed = _parse_expenditure(fields)
                    if parsed and _is_illinois_expenditure(parsed):
                        ein = parsed[1]
                        if ein:
                            il_eins.add(ein)
                elif record_type == "A":
                    parsed = _parse_contribution(fields)
                    if parsed:
                        contributor_state = (parsed[7] or "").strip().upper()
                        if contributor_state == "IL":
                            ein = parsed[1]
                            if ein:
                                il_eins.add(ein)
        logger.info("Found %d Illinois-related EINs", len(il_eins))

    # Buffers for batch inserts
    org_rows: list[tuple] = []
    report_rows: list[tuple] = []
    director_rows: list[tuple] = []
    related_org_rows: list[tuple] = []
    contribution_rows: list[tuple] = []
    expenditure_rows: list[tuple] = []
    election_authority_rows: list[tuple] = []

    def _flush_all():
        _flush_orgs()
        _flush_reports()
        _flush_directors()
        _flush_related_orgs()
        _flush_contributions()
        _flush_expenditures()
        _flush_election_authorities()

    def _flush_orgs():
        nonlocal org_rows
        if not org_rows:
            return
        for chunk in _chunked(org_rows):
            conn.executemany(
                """
                INSERT OR REPLACE INTO irs527_organizations (
                    ein, form_id, form_id_seq, org_name,
                    address_1, address_2, city, state, zip, zip_ext,
                    email, custodian_name,
                    custodian_address_1, custodian_address_2,
                    custodian_city, custodian_state, custodian_zip, custodian_zip_ext,
                    contact_name, contact_address_1, contact_address_2,
                    contact_city, contact_state, contact_zip, contact_zip_ext,
                    business_address_1, business_address_2,
                    business_city, business_state, business_zip, business_zip_ext,
                    purpose, material_change_date, insert_datetime,
                    related_entity_bypass, eain_bypass, formation_date
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        org_rows = []

    def _flush_reports():
        nonlocal report_rows
        if not report_rows:
            return
        for chunk in _chunked(report_rows):
            conn.executemany(
                """
                INSERT OR REPLACE INTO irs527_reports (
                    form_id, ein, period_start, period_end,
                    org_name, org_ein, org_address_1, org_address_2,
                    org_city, org_state, org_zip, org_zip_ext,
                    email, formation_date, custodian_name,
                    custodian_address_1, custodian_address_2,
                    custodian_city, custodian_state, custodian_zip, custodian_zip_ext,
                    contact_name, contact_address_1, contact_address_2,
                    contact_city, contact_state, contact_zip, contact_zip_ext,
                    business_address_1, business_address_2,
                    business_city, business_state, business_zip, business_zip_ext,
                    qtr_indicator, monthly_amount_1, monthly_amount_2, monthly_amount_3,
                    total_contributions, total_expenditures, insert_datetime
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        report_rows = []

    def _flush_directors():
        nonlocal director_rows
        if not director_rows:
            return
        for chunk in _chunked(director_rows):
            conn.executemany(
                """
                INSERT INTO irs527_directors (
                    form_id, ein, org_name, person_name, title,
                    address_1, address_2, city, state, zip, zip_ext
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        director_rows = []

    def _flush_related_orgs():
        nonlocal related_org_rows
        if not related_org_rows:
            return
        for chunk in _chunked(related_org_rows):
            conn.executemany(
                """
                INSERT INTO irs527_related_orgs (
                    form_id, ein, org_name, related_org_name, relationship_type,
                    address_1, address_2, city, state, zip, zip_ext
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        related_org_rows = []

    def _flush_contributions():
        nonlocal contribution_rows
        if not contribution_rows:
            return
        for chunk in _chunked(contribution_rows):
            conn.executemany(
                """
                INSERT INTO irs527_contributions (
                    form_id, ein, org_name, contributor_name,
                    contributor_address, contributor_address_2, city, state, zip, zip_ext,
                    contributor_employer, amount, contributor_occupation, date
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        contribution_rows = []

    def _flush_expenditures():
        nonlocal expenditure_rows
        if not expenditure_rows:
            return
        for chunk in _chunked(expenditure_rows):
            conn.executemany(
                """
                INSERT INTO irs527_expenditures (
                    form_id, ein, org_name, recipient_name,
                    recipient_address, recipient_address_2, city, state, zip, zip_ext,
                    recipient_employer, amount, recipient_occupation, date, purpose
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        expenditure_rows = []

    def _flush_election_authorities():
        nonlocal election_authority_rows
        if not election_authority_rows:
            return
        for chunk in _chunked(election_authority_rows):
            conn.executemany(
                """
                INSERT INTO irs527_election_authority (
                    form_id, election_authority_id, state
                ) VALUES (?,?,?)
                """,
                chunk,
            )
        conn.commit()
        election_authority_rows = []

    FLUSH_THRESHOLD = 5000

    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, start=1):
            stats["total_lines"] += 1
            line = line.rstrip("\n\r")
            if not line:
                continue
            line = _strip_bom_prefix(line)
            fields = line.split("|")
            record_type = fields[0] if fields else ""

            try:
                if record_type == "H":
                    _parse_header(fields)
                    stats["headers"] += 1

                elif record_type == "1":
                    parsed = _parse_org(fields)
                    if parsed is None:
                        stats["malformed_rows"] += 1
                        continue
                    if il_eins is not None and parsed[0] not in il_eins:
                        stats["orgs_skipped"] += 1
                        continue
                    org_rows.append(parsed)
                    stats["orgs"] += 1
                    if len(org_rows) >= FLUSH_THRESHOLD:
                        _flush_orgs()

                elif record_type == "2":
                    parsed = _parse_report(fields)
                    if parsed is None:
                        stats["malformed_rows"] += 1
                        continue
                    if il_eins is not None and parsed[1] not in il_eins:
                        continue
                    report_rows.append(parsed)
                    stats["reports"] += 1
                    if len(report_rows) >= FLUSH_THRESHOLD:
                        _flush_reports()

                elif record_type == "D":
                    parsed = _parse_director(fields)
                    if parsed is None:
                        stats["malformed_rows"] += 1
                        continue
                    if il_eins is not None and parsed[1] not in il_eins:
                        continue
                    director_rows.append(parsed)
                    stats["directors"] += 1
                    if len(director_rows) >= FLUSH_THRESHOLD:
                        _flush_directors()

                elif record_type == "R":
                    parsed = _parse_related_org(fields)
                    if parsed is None:
                        stats["malformed_rows"] += 1
                        continue
                    if il_eins is not None and parsed[1] not in il_eins:
                        continue
                    related_org_rows.append(parsed)
                    stats["related_orgs"] += 1
                    if len(related_org_rows) >= FLUSH_THRESHOLD:
                        _flush_related_orgs()

                elif record_type == "A":
                    parsed = _parse_contribution(fields)
                    if parsed is None:
                        stats["malformed_rows"] += 1
                        continue
                    if il_eins is not None and parsed[1] not in il_eins:
                        stats["contributions_skipped"] += 1
                        continue
                    contribution_rows.append(parsed)
                    stats["contributions"] += 1
                    if len(contribution_rows) >= FLUSH_THRESHOLD:
                        _flush_contributions()

                elif record_type == "B":
                    parsed = _parse_expenditure(fields)
                    if parsed is None:
                        stats["malformed_rows"] += 1
                        continue
                    if il_eins is not None and parsed[1] not in il_eins:
                        stats["expenditures_skipped"] += 1
                        continue
                    expenditure_rows.append(parsed)
                    stats["expenditures"] += 1
                    if len(expenditure_rows) >= FLUSH_THRESHOLD:
                        _flush_expenditures()

                elif record_type == "E":
                    parsed = _parse_election_authority(fields)
                    if parsed is None:
                        stats["malformed_rows"] += 1
                        continue
                    election_authority_rows.append(parsed)
                    stats["election_authorities"] += 1
                    if len(election_authority_rows) >= FLUSH_THRESHOLD:
                        _flush_election_authorities()

                else:
                    stats["malformed_rows"] += 1

            except Exception:
                logger.warning("Error parsing line %d: %.100s", line_num, line, exc_info=True)
                stats["malformed_rows"] += 1

            if stats["total_lines"] % 500000 == 0:
                logger.info("Processed %d lines...", stats["total_lines"])

    # Final flush
    _flush_all()

    logger.info("IRS 527 load complete: %s", stats)
    return stats


def reload_irs527_reports(
    conn: sqlite3.Connection,
    file_path: str | Path,
    *,
    illinois_only: bool = False,
    replace_existing: bool = True,
) -> dict:
    """Rebuild irs527_reports from FullDataFile type-2 rows only."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"IRS 527 file not found: {file_path}")

    stats = {
        "reports_loaded": 0,
        "reports_skipped": 0,
        "reports_malformed": 0,
        "total_lines": 0,
        "existing_reports_deleted": 0,
    }

    il_eins: set[str] | None = None
    if illinois_only:
        il_eins = set()
        logger.info("First pass: collecting Illinois EINs for report rebuild...")
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n\r")
                if not line:
                    continue
                line = _strip_bom_prefix(line)
                fields = line.split("|")
                record_type = fields[0] if fields else ""
                if record_type == "1":
                    parsed = _parse_org(fields)
                    if parsed and _is_illinois_org(parsed):
                        il_eins.add(parsed[0])
                elif record_type == "B":
                    parsed = _parse_expenditure(fields)
                    if parsed and _is_illinois_expenditure(parsed):
                        ein = parsed[1]
                        if ein:
                            il_eins.add(ein)
        logger.info("Illinois EIN set size for report rebuild: %d", len(il_eins))

    if replace_existing:
        existing_reports = conn.execute("SELECT COUNT(*) AS count FROM irs527_reports").fetchone()
        stats["existing_reports_deleted"] = int(existing_reports["count"] or 0) if existing_reports else 0
        conn.execute("DELETE FROM irs527_reports")
        conn.commit()

    report_rows: list[tuple] = []

    def _flush_reports():
        nonlocal report_rows
        if not report_rows:
            return
        for chunk in _chunked(report_rows):
            conn.executemany(
                """
                INSERT OR REPLACE INTO irs527_reports (
                    form_id, ein, period_start, period_end,
                    org_name, org_ein, org_address_1, org_address_2,
                    org_city, org_state, org_zip, org_zip_ext,
                    email, formation_date, custodian_name,
                    custodian_address_1, custodian_address_2,
                    custodian_city, custodian_state, custodian_zip, custodian_zip_ext,
                    contact_name, contact_address_1, contact_address_2,
                    contact_city, contact_state, contact_zip, contact_zip_ext,
                    business_address_1, business_address_2,
                    business_city, business_state, business_zip, business_zip_ext,
                    qtr_indicator, monthly_amount_1, monthly_amount_2, monthly_amount_3,
                    total_contributions, total_expenditures, insert_datetime
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        report_rows = []

    flush_threshold = 5000
    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stats["total_lines"] += 1
            line = line.rstrip("\n\r")
            if not line:
                continue
            line = _strip_bom_prefix(line)
            fields = line.split("|")
            record_type = fields[0] if fields else ""
            if record_type != "2":
                continue

            parsed = _parse_report(fields)
            if parsed is None:
                stats["reports_malformed"] += 1
                continue

            report_ein = parsed[1]
            if il_eins is not None and report_ein not in il_eins:
                stats["reports_skipped"] += 1
                continue

            report_rows.append(parsed)
            stats["reports_loaded"] += 1
            if len(report_rows) >= flush_threshold:
                _flush_reports()

    _flush_reports()
    logger.info("IRS 527 report rebuild complete: %s", stats)
    return stats


def reload_irs527_contributions(
    conn: sqlite3.Connection,
    file_path: str | Path,
    *,
    illinois_only: bool = False,
    replace_existing: bool = True,
) -> dict:
    """Rebuild irs527_contributions from FullDataFile type-A rows only."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"IRS 527 file not found: {file_path}")

    stats = {
        "contributions_loaded": 0,
        "contributions_skipped": 0,
        "contributions_malformed": 0,
        "total_lines": 0,
        "existing_contributions_deleted": 0,
    }

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS irs527_contribution_rollup (
            ein TEXT PRIMARY KEY,
            total_amount REAL NOT NULL DEFAULT 0,
            contribution_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    il_eins: set[str] | None = None
    if illinois_only:
        il_eins = set()
        logger.info("First pass: collecting Illinois EINs for contribution rebuild...")
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n\r")
                if not line:
                    continue
                line = _strip_bom_prefix(line)
                fields = line.split("|")
                record_type = fields[0] if fields else ""

                if record_type == "1":
                    parsed = _parse_org(fields)
                    if parsed and _is_illinois_org(parsed):
                        il_eins.add(parsed[0])
                elif record_type == "A":
                    parsed = _parse_contribution(fields)
                    if parsed:
                        contributor_state = (parsed[7] or "").strip().upper()
                        if contributor_state == "IL":
                            ein = parsed[1]
                            if ein:
                                il_eins.add(ein)
                elif record_type == "B":
                    parsed = _parse_expenditure(fields)
                    if parsed and _is_illinois_expenditure(parsed):
                        ein = parsed[1]
                        if ein:
                            il_eins.add(ein)
        logger.info("Illinois EIN set size for contribution rebuild: %d", len(il_eins))

    if replace_existing:
        existing = conn.execute("SELECT COUNT(*) AS count FROM irs527_contributions").fetchone()
        stats["existing_contributions_deleted"] = int(existing["count"] or 0) if existing else 0
        conn.execute("DELETE FROM irs527_contributions")
        conn.execute("DELETE FROM irs527_contribution_rollup")
        conn.commit()

    rows: list[tuple] = []

    def _flush_rows() -> None:
        nonlocal rows
        if not rows:
            return
        for chunk in _chunked(rows):
            conn.executemany(
                """
                INSERT INTO irs527_contributions (
                    form_id, ein, org_name, contributor_name,
                    contributor_address, contributor_address_2, city, state, zip, zip_ext,
                    contributor_employer, amount, contributor_occupation, date
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                chunk,
            )
        conn.commit()
        rows = []

    FLUSH_THRESHOLD = 5000

    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stats["total_lines"] += 1
            line = line.rstrip("\n\r")
            if not line:
                continue
            line = _strip_bom_prefix(line)

            fields = line.split("|")
            record_type = fields[0] if fields else ""
            if record_type != "A":
                continue

            parsed = _parse_contribution(fields)
            if parsed is None:
                stats["contributions_malformed"] += 1
                continue
            if il_eins is not None and parsed[1] not in il_eins:
                stats["contributions_skipped"] += 1
                continue

            rows.append(parsed)
            stats["contributions_loaded"] += 1
            if len(rows) >= FLUSH_THRESHOLD:
                _flush_rows()

    _flush_rows()

    conn.execute("DELETE FROM irs527_contribution_rollup")
    conn.execute(
        """
        INSERT INTO irs527_contribution_rollup (ein, total_amount, contribution_count, updated_at)
        SELECT
            ein,
            COALESCE(SUM(amount), 0) AS total_amount,
            COUNT(*) AS contribution_count,
            CURRENT_TIMESTAMP AS updated_at
        FROM irs527_contributions
        GROUP BY ein
        """
    )
    conn.commit()

    logger.info("IRS 527 contribution rebuild complete: %s", stats)
    return stats
