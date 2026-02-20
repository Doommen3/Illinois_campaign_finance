"""Maintenance utilities for data cleanup and normalization."""
from __future__ import annotations

from datetime import datetime
import re
import sqlite3
from typing import Iterable, List

from scraper.donor_normalizer import DonorNormalizer
from scraper.text_parsing import is_garbage_committee_name, parse_contributor_metadata


def _placeholders(items: Iterable[object]) -> str:
    values = list(items)
    return ",".join(["?"] * len(values))


def _normalize_bulk_receipt_date_value(value: str | None) -> tuple[str | None, str | None, bool]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None, None, True

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
            return parsed.date().isoformat(), raw_datetime, True
        except ValueError:
            continue

    iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", cleaned)
    if iso_match:
        return iso_match.group(0), raw_datetime, True

    us_match = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", cleaned)
    if us_match:
        candidate = us_match.group(0)
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                parsed = datetime.strptime(candidate, fmt)
                return parsed.date().isoformat(), raw_datetime, True
            except ValueError:
                continue

    return cleaned, raw_datetime, False


def find_garbage_committees(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    rows = conn.execute("SELECT id, name FROM committees ORDER BY id ASC").fetchall()
    return [row for row in rows if is_garbage_committee_name(row["name"])]


def delete_committees_and_related(conn: sqlite3.Connection, committee_ids: List[int]) -> dict:
    """Delete committee rows and all dependent rows for those committee IDs."""
    if not committee_ids:
        return {
            "committees_deleted": 0,
            "reports_deleted": 0,
            "contributions_deleted": 0,
            "manual_queue_deleted": 0,
            "d2_reports_deleted": 0,
            "d2_links_deleted": 0,
            "d2_entries_deleted": 0,
        }

    report_ids = [
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM reports WHERE committee_id IN ({_placeholders(committee_ids)})",
            committee_ids,
        ).fetchall()
    ]

    d2_report_ids = [
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM d2_reports WHERE committee_id IN ({_placeholders(committee_ids)})",
            committee_ids,
        ).fetchall()
    ]

    manual_queue_deleted = 0
    contributions_deleted = 0
    reports_deleted = 0
    d2_entries_deleted = 0
    d2_links_deleted = 0
    d2_reports_deleted = 0

    if report_ids:
        manual_queue_deleted = conn.execute(
            f"DELETE FROM manual_entry_queue WHERE report_id IN ({_placeholders(report_ids)})",
            report_ids,
        ).rowcount

        contributions_deleted = conn.execute(
            f"DELETE FROM contributions WHERE report_id IN ({_placeholders(report_ids)})",
            report_ids,
        ).rowcount

        reports_deleted = conn.execute(
            f"DELETE FROM reports WHERE id IN ({_placeholders(report_ids)})",
            report_ids,
        ).rowcount

    if d2_report_ids:
        d2_entries_deleted = conn.execute(
            f"DELETE FROM d2_itemized_entries WHERE d2_report_id IN ({_placeholders(d2_report_ids)})",
            d2_report_ids,
        ).rowcount

        d2_links_deleted = conn.execute(
            f"DELETE FROM d2_itemized_links WHERE d2_report_id IN ({_placeholders(d2_report_ids)})",
            d2_report_ids,
        ).rowcount

        d2_reports_deleted = conn.execute(
            f"DELETE FROM d2_reports WHERE id IN ({_placeholders(d2_report_ids)})",
            d2_report_ids,
        ).rowcount

    committees_deleted = conn.execute(
        f"DELETE FROM committees WHERE id IN ({_placeholders(committee_ids)})",
        committee_ids,
    ).rowcount

    conn.commit()

    return {
        "committees_deleted": committees_deleted,
        "reports_deleted": reports_deleted,
        "contributions_deleted": contributions_deleted,
        "manual_queue_deleted": manual_queue_deleted,
        "d2_reports_deleted": d2_reports_deleted,
        "d2_links_deleted": d2_links_deleted,
        "d2_entries_deleted": d2_entries_deleted,
    }


def normalize_donor_metadata(conn: sqlite3.Connection) -> dict:
    """Parse Occupation/Employer embedded in donor names and normalize rows."""
    normalizer = DonorNormalizer()
    scanned = 0
    updated = 0
    merged = 0

    rows = conn.execute(
        """
        SELECT id, name, address, occupation, employer, normalized_name, normalized_address
        FROM donors
        ORDER BY id ASC
        """
    ).fetchall()

    for row in rows:
        donor_id = row["id"]
        name = row["name"] or ""
        address = row["address"] or ""
        scanned += 1

        parsed_name, parsed_occupation, parsed_employer = parse_contributor_metadata(name)
        parsed_occupation = parsed_occupation or row["occupation"]
        parsed_employer = parsed_employer or row["employer"]

        if (
            parsed_name == name
            and parsed_occupation == row["occupation"]
            and parsed_employer == row["employer"]
        ):
            continue

        norm_name, norm_address = normalizer.normalize(parsed_name, address)

        duplicate = conn.execute(
            """
            SELECT id, name, occupation, employer
            FROM donors
            WHERE id != ? AND normalized_name = ? AND normalized_address = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (donor_id, norm_name, norm_address),
        ).fetchone()

        if duplicate:
            conn.execute(
                "UPDATE contributions SET donor_id = ? WHERE donor_id = ?",
                (duplicate["id"], donor_id),
            )

            merged_occupation = duplicate["occupation"] or parsed_occupation
            merged_employer = duplicate["employer"] or parsed_employer
            merged_name = duplicate["name"]
            if "Occupation:" in merged_name or "Employer:" in merged_name:
                merged_name = parsed_name

            conn.execute(
                """
                UPDATE donors
                SET name = ?, occupation = ?, employer = ?
                WHERE id = ?
                """,
                (merged_name, merged_occupation, merged_employer, duplicate["id"]),
            )

            conn.execute("DELETE FROM donors WHERE id = ?", (donor_id,))
            merged += 1
            continue

        conn.execute(
            """
            UPDATE donors
            SET name = ?, occupation = ?, employer = ?, normalized_name = ?, normalized_address = ?
            WHERE id = ?
            """,
            (parsed_name, parsed_occupation, parsed_employer, norm_name, norm_address, donor_id),
        )
        updated += 1

    conn.commit()
    return {
        "donors_scanned": scanned,
        "donors_updated": updated,
        "donors_merged": merged,
    }


def data_quality_summary(conn: sqlite3.Connection) -> dict:
    """Return high-signal quality metrics for monitoring pipeline health."""
    total_committees = conn.execute("SELECT COUNT(*) FROM committees").fetchone()[0]
    total_reports = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    total_donors = conn.execute("SELECT COUNT(*) FROM donors").fetchone()[0]
    total_contributions = conn.execute("SELECT COUNT(*) FROM contributions").fetchone()[0]

    garbage_committees = len(find_garbage_committees(conn))

    missing_detail_url = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE detail_url IS NULL OR detail_url = ''"
    ).fetchone()[0]

    contribution_missing_amount = conn.execute(
        "SELECT COUNT(*) FROM contributions WHERE amount IS NULL"
    ).fetchone()[0]

    contribution_missing_transaction_date = conn.execute(
        "SELECT COUNT(*) FROM contributions WHERE transaction_date IS NULL OR transaction_date = ''"
    ).fetchone()[0]

    donor_missing_occ_emp = conn.execute(
        """
        SELECT COUNT(*)
        FROM donors
        WHERE (occupation IS NULL OR occupation = '')
          AND (employer IS NULL OR employer = '')
        """
    ).fetchone()[0]

    raw_extraction_total = conn.execute(
        "SELECT COUNT(*) FROM raw_extractions"
    ).fetchone()[0]

    raw_by_source = {
        row["source_type"]: row["count"]
        for row in conn.execute(
            """
            SELECT source_type, COUNT(*) AS count
            FROM raw_extractions
            GROUP BY source_type
            ORDER BY source_type ASC
            """
        ).fetchall()
    }

    return {
        "totals": {
            "committees": total_committees,
            "reports": total_reports,
            "donors": total_donors,
            "contributions": total_contributions,
            "raw_extractions": raw_extraction_total,
        },
        "quality_flags": {
            "garbage_committees": garbage_committees,
            "reports_missing_detail_url": missing_detail_url,
            "contributions_missing_amount": contribution_missing_amount,
            "contributions_missing_transaction_date": contribution_missing_transaction_date,
            "donors_missing_occupation_and_employer": donor_missing_occ_emp,
        },
        "raw_extractions_by_source": raw_by_source,
    }


def find_reports_for_detail_rescrape(
    conn: sqlite3.Connection,
    missing_transaction_date_only: bool = False,
    limit: int | None = None,
) -> List[int]:
    """Return report IDs that should be re-queued for detail scraping."""
    params: List[object] = []
    query = """
        SELECT DISTINCT r.id
        FROM reports r
        WHERE r.is_paper_filed = FALSE
          AND r.detail_url IS NOT NULL
          AND r.detail_url != ''
    """

    if missing_transaction_date_only:
        query += """
          AND EXISTS (
              SELECT 1
              FROM contributions c
              WHERE c.report_id = r.id
                AND (c.transaction_date IS NULL OR c.transaction_date = '')
          )
        """

    query += " ORDER BY r.id ASC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [row["id"] for row in rows]


def requeue_reports_for_detail_scrape(conn: sqlite3.Connection, report_ids: List[int]) -> dict:
    """Delete existing contributions for reports and set report status to pending."""
    if not report_ids:
        return {"reports_requeued": 0, "contributions_deleted": 0}

    contributions_deleted = conn.execute(
        f"DELETE FROM contributions WHERE report_id IN ({_placeholders(report_ids)})",
        report_ids,
    ).rowcount

    reports_requeued = conn.execute(
        f"""
        UPDATE reports
        SET scrape_status = 'pending', scrape_error = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id IN ({_placeholders(report_ids)})
        """,
        report_ids,
    ).rowcount

    conn.commit()
    return {
        "reports_requeued": reports_requeued,
        "contributions_deleted": contributions_deleted,
    }


def normalize_bulk_receipt_dates(conn: sqlite3.Connection, apply: bool = False) -> dict:
    """Normalize bulk receipt dates to YYYY-MM-DD and preserve raw datetime values."""
    table_row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name='bulk_receipts_clean'"
    ).fetchone()
    if not table_row:
        return {
            "table_exists": 0,
            "rows_scanned": 0,
            "rows_changed": 0,
            "rows_with_preserved_datetime": 0,
            "rows_unparsed": 0,
            "applied": int(apply),
        }

    columns = [row["name"] for row in conn.execute("PRAGMA table_info(bulk_receipts_clean)").fetchall()]
    if "received_datetime_raw" not in columns:
        conn.execute("ALTER TABLE bulk_receipts_clean ADD COLUMN received_datetime_raw TEXT")

    rows_scanned = 0
    rows_changed = 0
    rows_unparsed = 0
    rows_with_preserved_datetime = 0
    updates: list[tuple[object, ...]] = []

    cursor = conn.execute(
        """
        SELECT bulk_row_id, received_date, received_datetime_raw
        FROM bulk_receipts_clean
        WHERE received_date IS NOT NULL
          AND TRIM(received_date) != ''
        """
    )

    for row in cursor.fetchall():
        rows_scanned += 1
        normalized_date, raw_datetime, parsed_ok = _normalize_bulk_receipt_date_value(row["received_date"])
        if not parsed_ok:
            rows_unparsed += 1

        existing_raw = row["received_datetime_raw"]
        new_raw = existing_raw or raw_datetime
        if new_raw:
            rows_with_preserved_datetime += 1

        if normalized_date != row["received_date"] or new_raw != existing_raw:
            rows_changed += 1
            if apply:
                updates.append((normalized_date, new_raw, row["bulk_row_id"]))

    if apply and updates:
        conn.executemany(
            """
            UPDATE bulk_receipts_clean
            SET received_date = ?, received_datetime_raw = ?
            WHERE bulk_row_id = ?
            """,
            updates,
        )
        conn.commit()

    return {
        "table_exists": 1,
        "rows_scanned": rows_scanned,
        "rows_changed": rows_changed,
        "rows_with_preserved_datetime": rows_with_preserved_datetime,
        "rows_unparsed": rows_unparsed,
        "applied": int(apply),
    }
