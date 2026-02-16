"""Global time period filter for site-wide date range control."""

from datetime import date, timedelta
from flask import request


# Ordered list of available time periods.
# Each tuple: (key, label, description)
TIME_PERIODS = [
    ("2026cycle", "2026 Cycle", "Jan 2025 – present"),
    ("2024cycle", "2024 Cycle", "Jan 2023 – Dec 2024"),
    ("1y", "Past Year", "Last 12 months"),
    ("2y", "Past 2 Years", "Last 24 months"),
    ("5y", "Past 5 Years", "Last 60 months"),
    ("all", "All Time", "Earliest available data"),
]

DEFAULT_PERIOD = "2026cycle"


def get_period_dates(period_key: str) -> tuple[str | None, str | None]:
    """Return (start_date, end_date) as ISO strings for a given period key.

    Returns (None, None) for 'all' meaning no date filter.
    end_date is always today for non-cycle periods.
    """
    today = date.today()

    if period_key == "2026cycle":
        return ("2025-01-01", today.isoformat())
    elif period_key == "2024cycle":
        return ("2023-01-01", "2024-12-31")
    elif period_key == "1y":
        start = today - timedelta(days=365)
        return (start.isoformat(), today.isoformat())
    elif period_key == "2y":
        start = today - timedelta(days=730)
        return (start.isoformat(), today.isoformat())
    elif period_key == "5y":
        start = today - timedelta(days=1826)
        return (start.isoformat(), today.isoformat())
    else:  # "all" or unknown
        return (None, None)


def get_active_period() -> dict:
    """Read the active time period from the request query string.

    Returns a dict with keys: key, label, description, start_date, end_date.
    """
    key = request.args.get("period", DEFAULT_PERIOD)
    # Validate
    valid_keys = {p[0] for p in TIME_PERIODS}
    if key not in valid_keys:
        key = DEFAULT_PERIOD

    label = ""
    description = ""
    for k, l, d in TIME_PERIODS:
        if k == key:
            label = l
            description = d
            break

    start_date, end_date = get_period_dates(key)
    return {
        "key": key,
        "label": label,
        "description": description,
        "start_date": start_date,
        "end_date": end_date,
    }


def period_where_clause(date_column: str, period: dict, prefix: str = "AND") -> str:
    """Build a SQL WHERE fragment for the given period.

    Returns empty string for 'all' (no filtering).
    Uses parameter placeholders (:tf_start, :tf_end) for safe binding.
    """
    if period["key"] == "all" or not period["start_date"]:
        return ""
    parts = []
    if period["start_date"]:
        parts.append(f"{date_column} >= :tf_start")
    if period["end_date"]:
        parts.append(f"{date_column} <= :tf_end")
    if not parts:
        return ""
    return f" {prefix} " + " AND ".join(parts)


def period_params(period: dict) -> dict:
    """Return parameter dict for binding :tf_start / :tf_end."""
    params = {}
    if period.get("start_date"):
        params["tf_start"] = period["start_date"]
    if period.get("end_date"):
        params["tf_end"] = period["end_date"]
    return params


def period_qmark_clause(date_column: str, period: dict, prefix: str = "AND") -> tuple[str, list]:
    """Build a SQL WHERE fragment using ? placeholders (for sqlite3/psycopg).

    Returns (clause_string, param_list). Empty string and [] for 'all'.
    """
    if period["key"] == "all" or not period["start_date"]:
        return ("", [])
    parts = []
    params = []
    if period["start_date"]:
        parts.append(f"{date_column} >= ?")
        params.append(period["start_date"])
    if period["end_date"]:
        parts.append(f"{date_column} <= ?")
        params.append(period["end_date"])
    if not parts:
        return ("", [])
    return (f" {prefix} " + " AND ".join(parts), params)
