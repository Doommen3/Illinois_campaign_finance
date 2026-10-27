"""Date windows over TEXT columns holding ISO or compact dates (the IRS 527 tables).

One definition for every route that windows those columns (/527 pages and the home page's 527
cards), so the format handling cannot drift between them.
"""
from __future__ import annotations


def text_date_window_clause(
    column: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    prefix: str = "AND",
) -> tuple[str, list[object]]:
    """Window clause for a TEXT date column holding ISO (YYYY-MM-DD) or compact (YYYYMMDD) dates.

    Each branch only compares values of its own format (5th character '-' or not): compared
    lexically with ISO bounds, a compact value of the window's first year passes ('20250131' >
    '2025-02-01'), and an ISO value late in its last year passes compact bounds. Bounds are
    inclusive; NULL / blank values fall outside any bound. The two ranges keep the (date, amount)
    btree indexes usable (normalising the column in the expression forces a full scan).
    """
    start_iso = (date_from or "").strip()[:10]
    end_iso = (date_to or "").strip()[:10]
    if not start_iso and not end_iso:
        return "", []

    iso_parts: list[str] = [f"SUBSTR({column}, 5, 1) = '-'"]
    iso_params: list[object] = []
    compact_parts: list[str] = [f"SUBSTR({column}, 5, 1) <> '-'"]
    compact_params: list[object] = []

    if start_iso:
        iso_parts.append(f"{column} >= ?")
        iso_params.append(start_iso)
        compact_parts.append(f"{column} >= ?")
        compact_params.append(start_iso.replace("-", ""))
    if end_iso:
        iso_parts.append(f"{column} <= ?")
        iso_params.append(end_iso)
        compact_parts.append(f"{column} <= ?")
        compact_params.append(end_iso.replace("-", ""))

    if not iso_parts:
        return "", []

    clause = f" {prefix} (({' AND '.join(iso_parts)}) OR ({' AND '.join(compact_parts)}))"
    return clause, iso_params + compact_params
