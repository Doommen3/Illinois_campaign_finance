"""Shared search query normalization for consistent matching across pages.

All search inputs (main query and autocomplete suggestions) pass through
``normalize_search_query`` so that "AMEREN", "ameren", "Ameren Inc.",
and "Ameren  Inc" all match the same records.
"""

from __future__ import annotations

import re

# Trailing abbreviation suffixes that should be stripped of their period
# (e.g. "Inc." → "Inc", "Ltd." → "Ltd").  Only stripped when they appear
# at the END of the query or followed by whitespace.
_ABBREV_DOT_RE = re.compile(
    r"\b(Inc|Ltd|Corp|Co|Assn|Assoc|Dept|Gov|Org|Comm|Natl|Intl|Jr|Sr|Dr|Mr|Mrs|Ms|St|Ave|Blvd)\.",
    re.IGNORECASE,
)

# Collapse multiple whitespace characters into a single space.
_MULTI_WS_RE = re.compile(r"\s+")


def normalize_search_query(raw: str | None) -> str:
    """Normalize a search query for consistent matching.

    Steps:
    1. Strip leading/trailing whitespace.
    2. Lowercase.
    3. Remove trailing periods from common abbreviations.
    4. Collapse multiple whitespace into single space.
    5. Strip SQL wildcard characters (``%`` and ``_``) to prevent injection
       into LIKE/ILIKE patterns.

    Returns the normalized string (possibly empty).
    """
    if not raw:
        return ""
    text = raw.strip().lower()
    # Remove periods from common abbreviations
    text = _ABBREV_DOT_RE.sub(lambda m: m.group(1).lower(), text)
    # Collapse whitespace
    text = _MULTI_WS_RE.sub(" ", text).strip()
    # Strip SQL wildcard chars
    text = text.replace("%", "").replace("_", " ")
    text = _MULTI_WS_RE.sub(" ", text).strip()
    return text
