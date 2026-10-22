"""`data_through` lookup for the time-scope bar (time-scope overhaul, step 1.2).

Reads the app's cached global data-status snapshot (see
`webapp/app.py:get_global_data_status`) instead of running a per-page
MAX(date) query. Returns None when the snapshot is unavailable.
"""

from __future__ import annotations

from datetime import date, datetime

from flask import current_app

# source → field in the global data-status dict
SOURCE_FIELDS = {
    "isbe": "local_receipt_date",
    "fec": "federal_receipt_date",
    "fec_b": "federal_disbursement_date",
    "fec_e": "federal_independent_expenditure_date",
}


def latest_data_date(source: str) -> date | None:
    getter = getattr(current_app, "get_global_data_status", None)
    status = getter() if callable(getter) else None
    if not status:
        return None
    value = status.get(SOURCE_FIELDS.get(source, ""))
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
