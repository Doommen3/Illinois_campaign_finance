"""Text parsing helpers for scraper data quality."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Tuple


def is_garbage_committee_name(name: str | None) -> bool:
    """Detect committee names that are really pager fragments/noise."""
    if not name:
        return True

    cleaned = re.sub(r"\s+", " ", name).strip()
    if not cleaned:
        return True

    if cleaned in {".", "...", "…"}:
        return True

    # Names made only of digits, dots, spaces, and ellipsis characters are pager noise.
    if re.fullmatch(r"[\d.\s…]+", cleaned):
        return True

    return False


def parse_contributor_metadata(raw_name: str | None) -> Tuple[str, Optional[str], Optional[str]]:
    """Split contributor name text into name, occupation, and employer.

    Input can be a single string like:
    "Bishop, Elizabeth Occupation: Politics Employer: City of LaSalle"
    """
    if not raw_name:
        return "", None, None

    text = re.sub(r"\s+", " ", raw_name).strip()
    if not text:
        return "", None, None

    occ_match = re.search(r"occupation\s*:\s*", text, flags=re.IGNORECASE)
    emp_match = re.search(r"employer\s*:\s*", text, flags=re.IGNORECASE)

    if not occ_match and not emp_match:
        return text, None, None

    first_match_start = min(
        [m.start() for m in (occ_match, emp_match) if m],
        default=len(text),
    )
    name_part = text[:first_match_start].strip(" -;,")

    occupation = None
    employer = None

    if occ_match:
        occ_start = occ_match.end()
        occ_end = emp_match.start() if emp_match and emp_match.start() > occ_start else len(text)
        occupation = text[occ_start:occ_end].strip(" -;,") or None

    if emp_match:
        emp_start = emp_match.end()
        emp_end = occ_match.start() if occ_match and occ_match.start() > emp_start else len(text)
        employer = text[emp_start:emp_end].strip(" -;,") or None

    return name_part or text, occupation, employer


def parse_amount_and_date(amount_text: str | None) -> Tuple[Optional[float], Optional[str]]:
    """Parse amount cell text into numeric amount and transaction date.

    Examples:
    - "$32,500.00 2/6/2026"
    - "$32,500.00\\n2/6/2026"
    - "($500.00) 01/10/2025"
    """
    if not amount_text:
        return None, None

    text = amount_text.strip()

    # Amount parsing
    is_negative = bool(re.search(r"\(\s*\$?[\d,]+\.?\d*\s*\)", text))
    amount = None
    amount_match = re.search(r"\$?\(?([\d,]+\.?\d*)\)?", text)
    if amount_match:
        amount_clean = amount_match.group(1).replace(",", "")
        try:
            amount = float(amount_clean)
            if is_negative:
                amount = -amount
        except ValueError:
            amount = None

    # Date parsing (support m/d/yyyy and mm/dd/yyyy)
    transaction_date = None
    date_match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", text)
    if date_match:
        date_str = date_match.group(1)
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                parsed = datetime.strptime(date_str, fmt).date()
                transaction_date = parsed.isoformat()
                break
            except ValueError:
                continue

    return amount, transaction_date
