"""Chicago Open Data (Socrata) Phase 1 ingestion utilities."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Iterable

import config

logger = logging.getLogger(__name__)

_DATASET_CONTRACTS = "rsxa-ify5"
_DATASET_PAYMENTS = "s4vu-giwb"
_DATASET_CONTRIBUTIONS = "p9p7-vfqc"
_DATASET_ACTIVITY = "pahz-egmi"

PHASE1_DATASET_IDS = (
    _DATASET_CONTRACTS,
    _DATASET_PAYMENTS,
    _DATASET_CONTRIBUTIONS,
    _DATASET_ACTIVITY,
)


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: object | None) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError, OverflowError):
        return None


def _to_float(value: object | None) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError, OverflowError):
        return None


def _to_iso_date(value: object | None) -> str | None:
    text = _clean_text(value)
    if not text:
        return None

    if "T" in text:
        return text.split("T", 1)[0]

    if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
        return text

    if len(text) == 4 and text.isdigit():
        return f"{text}-01-01"

    try:
        return datetime.strptime(text, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _decode_json_payload(payload: bytes) -> object:
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Socrata JSON payload") from exc


def _chunked(rows: Iterable[tuple], size: int = 5000):
    chunk: list[tuple] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _fallback_row_id(row: dict) -> str:
    payload = json.dumps(row, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _request_json(
    url: str,
    headers: dict[str, str],
    timeout_seconds: int,
    max_retries: int,
    min_interval_seconds: float,
    last_request_ts: list[float],
) -> object:
    attempt = 0
    while True:
        now = time.monotonic()
        elapsed = now - last_request_ts[0]
        if elapsed < min_interval_seconds:
            time.sleep(min_interval_seconds - elapsed)

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read()
            last_request_ts[0] = time.monotonic()
            return _decode_json_payload(payload)
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            should_retry = status in {429, 500, 502, 503, 504} and attempt < max_retries
            if not should_retry:
                raise

            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and str(retry_after).isdigit():
                delay_seconds = max(1, int(retry_after))
            else:
                delay_seconds = min(30, 2 ** (attempt + 1))
            logger.warning("Socrata request retry %s status=%s delay=%ss", attempt + 1, status, delay_seconds)
            time.sleep(delay_seconds)
            attempt += 1


def _fetch_dataset_rows(
    dataset_id: str,
    row_limit: int | None,
    page_limit: int,
    *,
    app_token: str,
    base_url: str,
    timeout_seconds: int,
    max_retries: int,
    min_interval_seconds: float,
    select_fields: tuple[str, ...],
    last_request_ts: list[float],
) -> list[dict]:
    offset = 0
    remaining = row_limit
    rows: list[dict] = []

    headers = {
        "Accept": "application/json",
        "User-Agent": f"{config.SOCRATA_APP_NAME}/1.0",
    }
    if app_token:
        headers["X-App-Token"] = app_token

    while True:
        limit = page_limit
        if remaining is not None:
            if remaining <= 0:
                break
            limit = min(limit, remaining)

        params = {
            "$select": ",".join(select_fields),
            "$limit": str(limit),
            "$offset": str(offset),
            "$order": ":id",
        }
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        url = f"{base_url}/resource/{dataset_id}.json?{query}"
        payload = _request_json(
            url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            min_interval_seconds=min_interval_seconds,
            last_request_ts=last_request_ts,
        )

        if not isinstance(payload, list):
            raise ValueError(f"Unexpected Socrata response shape for dataset {dataset_id}")

        if not payload:
            break

        batch = [row for row in payload if isinstance(row, dict)]
        rows.extend(batch)
        count = len(batch)
        if count == 0:
            break

        offset += count
        if remaining is not None:
            remaining -= count

        if count < limit:
            break

    return rows


def _parse_contract_row(row: dict) -> tuple:
    row_id = _clean_text(row.get("socrata_row_id")) or _fallback_row_id(row)
    return (
        row_id,
        _clean_text(row.get("purchase_order_contract_number")),
        _clean_text(row.get("revision_number")),
        _clean_text(row.get("specification_number")),
        _clean_text(row.get("contract_type")),
        _to_iso_date(row.get("start_date")),
        _to_iso_date(row.get("end_date")),
        _to_iso_date(row.get("approval_date")),
        _clean_text(row.get("department")),
        _clean_text(row.get("vendor_name")),
        _clean_text(row.get("vendor_id")),
        _clean_text(row.get("city")),
        _clean_text(row.get("state")),
        _clean_text(row.get("zip")),
        _to_float(row.get("award_amount")),
        _clean_text(row.get("procurement_type")),
        _clean_text(row.get("purchase_order_description")),
        _clean_text(row.get("contract_pdf")),
    )


def _parse_payments_row(row: dict) -> tuple:
    row_id = _clean_text(row.get("socrata_row_id")) or _fallback_row_id(row)
    check_date_raw = _clean_text(row.get("check_date"))
    return (
        row_id,
        _clean_text(row.get("voucher_number")),
        _to_float(row.get("amount")),
        check_date_raw,
        _to_iso_date(check_date_raw),
        _clean_text(row.get("department_name")),
        _clean_text(row.get("contract_number")),
        _clean_text(row.get("vendor_name")),
    )


def _parse_contribution_row(row: dict) -> tuple:
    row_id = _clean_text(row.get("socrata_row_id")) or _fallback_row_id(row)
    return (
        row_id,
        _to_int(row.get("contribution_id")),
        _to_iso_date(row.get("period_start")),
        _to_iso_date(row.get("period_end")),
        _to_iso_date(row.get("contribution_date")),
        _clean_text(row.get("recipient")),
        _to_float(row.get("amount")),
        _to_int(row.get("lobbyist_id")),
        _clean_text(row.get("lobbyist_first_name")),
        _clean_text(row.get("lobbyist_last_name")),
    )


def _parse_activity_row(row: dict) -> tuple:
    row_id = _clean_text(row.get("socrata_row_id")) or _fallback_row_id(row)
    return (
        row_id,
        _to_int(row.get("lobbying_activity_id")),
        _to_iso_date(row.get("period_start")),
        _to_iso_date(row.get("period_end")),
        _clean_text(row.get("action")),
        _clean_text(row.get("action_sought")),
        _clean_text(row.get("department")),
        _to_int(row.get("client_id")),
        _clean_text(row.get("client_name")),
        _to_int(row.get("lobbyist_id")),
        _clean_text(row.get("lobbyist_first_name")),
        _clean_text(row.get("lobbyist_last_name")),
    )


def _upsert_rows(conn: sqlite3.Connection, query: str, rows: list[tuple]) -> int:
    inserted = 0
    for chunk in _chunked(rows):
        conn.executemany(query, chunk)
        inserted += len(chunk)
    conn.commit()
    return inserted


def _date_quality_summary(
    conn: sqlite3.Connection,
    table_name: str,
    field_name: str,
    *,
    min_valid_date: str = "1900-01-01",
) -> dict[str, int | str | None]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN {field_name} IS NULL OR TRIM({field_name}) = '' THEN 1 ELSE 0 END) AS missing_rows,
            SUM(
                CASE
                    WHEN {field_name} IS NULL OR TRIM({field_name}) = '' THEN 0
                    WHEN NOT ({field_name} GLOB '????-??-??') OR {field_name} < ? THEN 1
                    ELSE 0
                END
            ) AS malformed_rows,
            MIN(
                CASE
                    WHEN {field_name} IS NOT NULL
                        AND TRIM({field_name}) != ''
                        AND {field_name} GLOB '????-??-??'
                        AND {field_name} >= ?
                    THEN {field_name}
                END
            ) AS min_valid_date,
            MAX(
                CASE
                    WHEN {field_name} IS NOT NULL
                        AND TRIM({field_name}) != ''
                        AND {field_name} GLOB '????-??-??'
                        AND {field_name} >= ?
                    THEN {field_name}
                END
            ) AS max_valid_date
        FROM {table_name}
        """,
        (min_valid_date, min_valid_date, min_valid_date),
    ).fetchone()

    return {
        "total_rows": int(row["total_rows"] or 0),
        "missing_rows": int(row["missing_rows"] or 0),
        "malformed_rows": int(row["malformed_rows"] or 0),
        "min_valid_date": row["min_valid_date"],
        "max_valid_date": row["max_valid_date"],
    }


def import_chicago_phase1(
    conn: sqlite3.Connection,
    *,
    app_token: str | None = None,
    full_refresh: bool = True,
    row_limit: int | None = None,
    page_limit: int | None = None,
    fetcher: Callable[[str, int | None, int], list[dict]] | None = None,
) -> dict:
    """Import Chicago Phase 1 datasets into raw staging tables."""
    if fetcher is None and not (app_token or config.SOCRATA_APP_TOKEN):
        raise ValueError("Socrata app token is required for live API ingestion")

    app_token = app_token or config.SOCRATA_APP_TOKEN
    page_limit = int(page_limit or config.SOCRATA_API_PAGE_LIMIT)
    page_limit = max(100, min(page_limit, 50000))

    if row_limit is not None:
        row_limit = max(1, int(row_limit))

    if full_refresh:
        conn.execute("DELETE FROM chicago_contracts_raw")
        conn.execute("DELETE FROM chicago_payments_raw")
        conn.execute("DELETE FROM chicago_lobbyist_contributions_raw")
        conn.execute("DELETE FROM chicago_lobbying_activity_raw")
        conn.commit()

    if fetcher is None:
        base_url = config.SOCRATA_API_BASE_URL.rstrip("/")
        timeout_seconds = int(config.SOCRATA_API_TIMEOUT_SECONDS)
        max_retries = int(config.SOCRATA_API_MAX_RETRIES)
        min_interval_seconds = float(config.SOCRATA_API_MIN_INTERVAL_SECONDS)
        last_request_ts = [0.0]

        def fetcher(dataset_id: str, dataset_row_limit: int | None, dataset_page_limit: int) -> list[dict]:
            select_fields = {
                _DATASET_CONTRACTS: (
                    ":id as socrata_row_id",
                    "purchase_order_contract_number",
                    "revision_number",
                    "specification_number",
                    "contract_type",
                    "start_date",
                    "end_date",
                    "approval_date",
                    "department",
                    "vendor_name",
                    "vendor_id",
                    "city",
                    "state",
                    "zip",
                    "award_amount",
                    "procurement_type",
                    "purchase_order_description",
                    "contract_pdf",
                ),
                _DATASET_PAYMENTS: (
                    ":id as socrata_row_id",
                    "voucher_number",
                    "amount",
                    "check_date",
                    "department_name",
                    "contract_number",
                    "vendor_name",
                ),
                _DATASET_CONTRIBUTIONS: (
                    ":id as socrata_row_id",
                    "contribution_id",
                    "period_start",
                    "period_end",
                    "contribution_date",
                    "recipient",
                    "amount",
                    "lobbyist_id",
                    "lobbyist_first_name",
                    "lobbyist_last_name",
                ),
                _DATASET_ACTIVITY: (
                    ":id as socrata_row_id",
                    "lobbying_activity_id",
                    "period_start",
                    "period_end",
                    "action",
                    "action_sought",
                    "department",
                    "client_id",
                    "client_name",
                    "lobbyist_id",
                    "lobbyist_first_name",
                    "lobbyist_last_name",
                ),
            }[dataset_id]

            return _fetch_dataset_rows(
                dataset_id,
                dataset_row_limit,
                dataset_page_limit,
                app_token=app_token or "",
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                min_interval_seconds=min_interval_seconds,
                select_fields=select_fields,
                last_request_ts=last_request_ts,
            )

    contracts_rows = [_parse_contract_row(row) for row in fetcher(_DATASET_CONTRACTS, row_limit, page_limit)]
    payments_rows = [_parse_payments_row(row) for row in fetcher(_DATASET_PAYMENTS, row_limit, page_limit)]
    contributions_rows = [_parse_contribution_row(row) for row in fetcher(_DATASET_CONTRIBUTIONS, row_limit, page_limit)]
    activity_rows = [_parse_activity_row(row) for row in fetcher(_DATASET_ACTIVITY, row_limit, page_limit)]

    contracts_upserted = _upsert_rows(
        conn,
        """
        INSERT INTO chicago_contracts_raw (
            socrata_row_id, purchase_order_contract_number, revision_number,
            specification_number, contract_type, start_date, end_date, approval_date,
            department, vendor_name, vendor_id, city, state, zip, award_amount,
            procurement_type, purchase_order_description, contract_pdf
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(socrata_row_id) DO UPDATE SET
            purchase_order_contract_number = excluded.purchase_order_contract_number,
            revision_number = excluded.revision_number,
            specification_number = excluded.specification_number,
            contract_type = excluded.contract_type,
            start_date = excluded.start_date,
            end_date = excluded.end_date,
            approval_date = excluded.approval_date,
            department = excluded.department,
            vendor_name = excluded.vendor_name,
            vendor_id = excluded.vendor_id,
            city = excluded.city,
            state = excluded.state,
            zip = excluded.zip,
            award_amount = excluded.award_amount,
            procurement_type = excluded.procurement_type,
            purchase_order_description = excluded.purchase_order_description,
            contract_pdf = excluded.contract_pdf,
            updated_at = CURRENT_TIMESTAMP
        """,
        contracts_rows,
    )

    payments_upserted = _upsert_rows(
        conn,
        """
        INSERT INTO chicago_payments_raw (
            socrata_row_id, voucher_number, amount, check_date_raw, check_date,
            department_name, contract_number, vendor_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(socrata_row_id) DO UPDATE SET
            voucher_number = excluded.voucher_number,
            amount = excluded.amount,
            check_date_raw = excluded.check_date_raw,
            check_date = excluded.check_date,
            department_name = excluded.department_name,
            contract_number = excluded.contract_number,
            vendor_name = excluded.vendor_name,
            updated_at = CURRENT_TIMESTAMP
        """,
        payments_rows,
    )

    contributions_upserted = _upsert_rows(
        conn,
        """
        INSERT INTO chicago_lobbyist_contributions_raw (
            socrata_row_id, contribution_id, period_start, period_end,
            contribution_date, recipient, amount, lobbyist_id,
            lobbyist_first_name, lobbyist_last_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(socrata_row_id) DO UPDATE SET
            contribution_id = excluded.contribution_id,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            contribution_date = excluded.contribution_date,
            recipient = excluded.recipient,
            amount = excluded.amount,
            lobbyist_id = excluded.lobbyist_id,
            lobbyist_first_name = excluded.lobbyist_first_name,
            lobbyist_last_name = excluded.lobbyist_last_name,
            updated_at = CURRENT_TIMESTAMP
        """,
        contributions_rows,
    )

    activity_upserted = _upsert_rows(
        conn,
        """
        INSERT INTO chicago_lobbying_activity_raw (
            socrata_row_id, lobbying_activity_id, period_start, period_end,
            action, action_sought, department, client_id,
            client_name, lobbyist_id, lobbyist_first_name, lobbyist_last_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(socrata_row_id) DO UPDATE SET
            lobbying_activity_id = excluded.lobbying_activity_id,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            action = excluded.action,
            action_sought = excluded.action_sought,
            department = excluded.department,
            client_id = excluded.client_id,
            client_name = excluded.client_name,
            lobbyist_id = excluded.lobbyist_id,
            lobbyist_first_name = excluded.lobbyist_first_name,
            lobbyist_last_name = excluded.lobbyist_last_name,
            updated_at = CURRENT_TIMESTAMP
        """,
        activity_rows,
    )

    stats = {
        "mode": "full_refresh" if full_refresh else "upsert",
        "row_limit": row_limit,
        "page_limit": page_limit,
        "contracts_rows": contracts_upserted,
        "payments_rows": payments_upserted,
        "lobbyist_contributions_rows": contributions_upserted,
        "lobbying_activity_rows": activity_upserted,
    }

    qa = {
        "chicago_contracts_raw.approval_date": _date_quality_summary(
            conn,
            "chicago_contracts_raw",
            "approval_date",
        ),
        "chicago_payments_raw.check_date": _date_quality_summary(
            conn,
            "chicago_payments_raw",
            "check_date",
        ),
        "chicago_lobbyist_contributions_raw.contribution_date": _date_quality_summary(
            conn,
            "chicago_lobbyist_contributions_raw",
            "contribution_date",
        ),
        "chicago_lobbying_activity_raw.period_end": _date_quality_summary(
            conn,
            "chicago_lobbying_activity_raw",
            "period_end",
        ),
    }
    stats["qa"] = qa

    malformed_total = sum(entry["malformed_rows"] for entry in qa.values())
    if malformed_total:
        logger.warning("Chicago phase1 date QA detected malformed rows: %d", malformed_total)

    logger.info("Chicago phase1 import complete: %s", stats)
    return stats


__all__ = [
    "PHASE1_DATASET_IDS",
    "_decode_json_payload",
    "import_chicago_phase1",
]
