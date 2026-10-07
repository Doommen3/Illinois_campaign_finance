"""Privacy redactions for personal-information removal requests.

Requests live in the ``privacy_redactions`` table. ``apply_privacy_redactions``
scrubs the matching contributor addresses to ISBE's own redaction format and
never deletes a contribution row. Contract: .claude/plans/privacy-redaction.md
(Step 1) and tests/test_privacy_redactions.py. Callers commit.
"""
from __future__ import annotations

from datetime import date
import re

from database.analytics import _table_exists

REDACTED_ADDRESS = "Redaction Requested"

_ZIP5_RE = re.compile(r"[0-9]{5}")

# Receipt-shaped tables: (first-name col, last-name col, address col, zip col,
# remaining SET assignments).
_RECEIPT_TARGETS = {
    "isbe_receipts": (
        "first_name", "last_name", "address1", "zipcode",
        "address2 = NULL, city = NULL, state = NULL, zipcode = NULL, redaction_requested = TRUE",
    ),
    "bulk_receipts_clean": (
        "first_name", "last_or_business_name", "address_line_1", "postal_code",
        "address_line_2 = NULL, city = NULL, state = NULL, postal_code = NULL, redaction_requested = 1",
    ),
    "bulk_receipts_clean_legacy": (
        "first_name", "last_or_business_name", "address_line_1", "postal_code",
        "address_line_2 = NULL, city = NULL, state = NULL, postal_code = NULL, redaction_requested = 1",
    ),
}
_TARGETS = (*_RECEIPT_TARGETS, "donors", "analytics_donor_summary")


def add_privacy_redaction(
    conn,
    *,
    last_name,
    zip5,
    first_name=None,
    requested_by,
    request_date,
    statute=None,
    notes=None,
) -> int:
    last_name = (last_name or "").strip()
    if not last_name:
        raise ValueError("last_name is required")
    zip5 = (zip5 or "").strip()
    if not _ZIP5_RE.fullmatch(zip5):
        raise ValueError(f"zip5 must be exactly five digits, got {zip5!r}")
    if isinstance(request_date, str):
        request_date = date.fromisoformat(request_date.strip())
    row = conn.execute(
        "INSERT INTO privacy_redactions "
        "(last_name, first_name, zip5, requested_by, request_date, statute, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (
            last_name,
            (first_name or "").strip() or None,
            zip5,
            requested_by,
            request_date,
            statute,
            notes,
        ),
    ).fetchone()
    return int(row["id"])


def _is_base_table(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relname = ? AND c.relkind = 'r'",
        (table,),
    ).fetchone()
    return row is not None


def _scrub_receipts(conn, table: str, last: str, first: str | None, zip5: str) -> int:
    first_col, last_col, addr_col, zip_col, other_sets = _RECEIPT_TARGETS[table]
    # A row's zip is the first five digits after stripping non-digits, so
    # "61820-4455" and " 61820" match 61820 but "606182000" does not.
    sql = (
        f"UPDATE {table} SET {addr_col} = ?, {other_sets} "
        f"WHERE lower(trim({last_col})) = ? "
        rf"AND left(regexp_replace(coalesce({zip_col}, ''), '\D', '', 'g'), 5) = ? "
        f"AND {addr_col} IS DISTINCT FROM ?"
    )
    params = [REDACTED_ADDRESS, last, zip5, REDACTED_ADDRESS]
    if first is not None:
        sql += f" AND lower(trim({first_col})) = ?"
        params.append(first)
    return conn.execute(sql, params).rowcount


def apply_privacy_redactions(conn) -> dict[str, int]:
    if not _table_exists(conn, "privacy_redactions"):
        return {}
    counts = dict.fromkeys(_TARGETS, 0)
    present = {table for table in _TARGETS if _table_exists(conn, table)}
    # After the compat swap bulk_receipts_clean is a view; UPDATE on it raises.
    if "bulk_receipts_clean" in present and not _is_base_table(conn, "bulk_receipts_clean"):
        present.discard("bulk_receipts_clean")

    requests = conn.execute(
        "SELECT last_name, first_name, zip5 FROM privacy_redactions ORDER BY id"
    ).fetchall()
    for request in requests:
        last = request["last_name"].strip().lower()
        first = (request["first_name"] or "").strip().lower() or None
        zip5 = request["zip5"].strip()
        zip_pattern = f"%{zip5}%"
        if first is None:
            name_sql, name_param = "LIKE ?", f"% {last}"
        else:
            name_sql, name_param = "= ?", f"{first} {last}"

        for table in _RECEIPT_TARGETS:
            if table in present:
                counts[table] += _scrub_receipts(conn, table, last, first, zip5)
        if "donors" in present:
            counts["donors"] += conn.execute(
                "UPDATE donors SET address = ?, normalized_address = NULL "
                f"WHERE lower(name) {name_sql} AND address ILIKE ?",
                (REDACTED_ADDRESS, name_param, zip_pattern),
            ).rowcount
        if "analytics_donor_summary" in present:
            counts["analytics_donor_summary"] += conn.execute(
                "DELETE FROM analytics_donor_summary "
                f"WHERE lower(donor_name) {name_sql} AND donor_address ILIKE ?",
                (name_param, zip_pattern),
            ).rowcount

    conn.execute("UPDATE privacy_redactions SET last_applied_at = CURRENT_TIMESTAMP")
    return counts


def refresh_dependent_views(conn) -> None:
    exists = conn.execute(
        "SELECT 1 FROM pg_matviews WHERE schemaname = current_schema() AND matviewname = ?",
        ("isbe_condensed_receipts",),
    ).fetchone()
    if exists:
        conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY isbe_condensed_receipts")
