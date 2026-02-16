"""Comptroller State Contracts scraper.

Scrapes state contract data from
https://illinoiscomptroller.gov/financial-reports-data/find-a-report/state-contracts

Uses Playwright for browser automation (the site uses Select2 dropdowns and
DataTables with AJAX-loaded results that require JavaScript execution).
"""
import hashlib
import html as html_lib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://illinoiscomptroller.gov"
    "/financial-reports-data/find-a-report/state-contracts"
)

# Common corporate/legal suffixes to strip for primary identifier
_STRIP_SUFFIXES = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "LTD", "LP", "LLP",
    "INCORPORATED", "LIMITED", "COMPANY", "GROUP", "HOLDINGS",
    "ENTERPRISES", "SERVICES", "PARTNERS", "PARTNERSHIP",
}

# Geographic modifiers to strip
_STRIP_GEO = {"OF", "ILLINOIS", "CHICAGO", "SPRINGFIELD", "IL"}

# Roman numerals to strip (only when they appear as standalone tokens)
_ROMAN_NUMERALS = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}


def _hash_row(parts: list) -> str:
    """Create a SHA-1 hash from a list of string parts for dedupe."""
    raw = "|".join(str(p).strip() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _parse_currency(text: str) -> Optional[float]:
    """Parse a currency string like '$1,234.56' to float."""
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _clean(text: str) -> str:
    """Strip and collapse whitespace, decode HTML entities."""
    if not text:
        return ""
    decoded = html_lib.unescape(text)
    return re.sub(r"\s+", " ", decoded.strip())


def extract_primary_identifier(name: Optional[str]) -> str:
    """Extract a short, primary search term from a full vendor name.

    Examples:
        "COMCAST OF ILLINOIS III INC" -> "COMCAST"
        "AT&T SERVICES INC" -> "AT&T"
        "BLUE CROSS BLUE SHIELD OF ILLINOIS" -> "BLUE CROSS BLUE SHIELD"
        "DELOITTE" -> "DELOITTE"
        "3M COMPANY INC" -> "3M"
    """
    if not name:
        return ""

    # Normalize whitespace
    cleaned = re.sub(r"\s+", " ", name.strip().upper())
    if not cleaned:
        return ""

    tokens = cleaned.split()

    # Remove trailing suffixes, geo modifiers, and roman numerals
    # Work backwards to strip from the end
    significant: list[str] = []
    hit_significant = False

    for token in reversed(tokens):
        if not hit_significant:
            if token in _STRIP_SUFFIXES or token in _ROMAN_NUMERALS:
                continue
            if token in _STRIP_GEO and not significant:
                continue
            hit_significant = True
        significant.append(token)

    significant.reverse()

    if not significant:
        # Everything was stripped — fall back to first token of original
        return tokens[0] if tokens else ""

    # Now strip geographic phrases from what remains
    # Remove "OF ILLINOIS", "OF CHICAGO" etc. from the middle/end
    result: list[str] = []
    skip_next = False
    for i, token in enumerate(significant):
        if skip_next:
            skip_next = False
            continue
        if token == "OF" and i + 1 < len(significant) and significant[i + 1] in _STRIP_GEO:
            skip_next = True
            continue
        result.append(token)

    if not result:
        return significant[0] if significant else ""

    # For short names (1-2 words), keep everything
    if len(result) <= 2:
        return " ".join(result)

    # For longer names, check if it looks like a repeated brand pattern
    # (e.g., "BLUE CROSS BLUE SHIELD" where first two words repeat).
    # In that case keep the full name. Otherwise take just the first word.
    # This maximizes search results on the Comptroller site (e.g., searching
    # "Comcast" returns more results than "Comcast Business Communications").
    if len(result) == 4 and result[0] == result[2]:
        # Pattern like "BLUE CROSS BLUE SHIELD" — keep all 4
        return " ".join(result)

    return result[0]


def parse_search_results_html(html: Optional[str]) -> List[Dict[str, Any]]:
    """Parse contract rows from the Comptroller State Contracts DataTables HTML.

    Expects a rendered DataTables table with columns:
        VENDOR, AGENCY CONTRACT NUMBER, CONTRACT TRANSPARENCY DOCUMENT, DETAILS, PAYMENTS

    Returns a list of dicts with keys:
        vendor_name, agency_contract_number, ctd_url, detail_url,
        payments_amount, row_hash
    """
    if not html:
        return []

    contracts: List[Dict[str, Any]] = []

    # Match rows within <tbody id="content"> or any tbody
    tbody_match = re.search(
        r"<tbody[^>]*id=[\"']content[\"'][^>]*>(.*?)</tbody>",
        html, re.DOTALL | re.IGNORECASE,
    )
    if not tbody_match:
        # Try any tbody
        tbody_match = re.search(
            r"<tbody[^>]*>(.*?)</tbody>",
            html, re.DOTALL | re.IGNORECASE,
        )
    if not tbody_match:
        return []

    tbody_html = tbody_match.group(1)

    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
    link_pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)

    for row_match in row_pattern.finditer(tbody_html):
        row_html = row_match.group(1)
        cells = cell_pattern.findall(row_html)

        if len(cells) < 5:
            continue

        vendor_raw = cells[0]
        contract_num_raw = cells[1]
        ctd_raw = cells[2]
        details_raw = cells[3]
        payments_raw = cells[4]

        # Clean vendor name (decode HTML entities)
        vendor_name = _clean(re.sub(r"<[^>]+>", "", vendor_raw))

        # Skip empty/no-data rows
        if not vendor_name or "No matching records" in vendor_name:
            continue

        # Clean contract number
        agency_contract_number = _clean(re.sub(r"<[^>]+>", "", contract_num_raw))

        # Extract CTD URL
        ctd_url = None
        ctd_link = link_pattern.search(ctd_raw)
        if ctd_link:
            ctd_url = html_lib.unescape(ctd_link.group(1))

        # Extract detail URL
        detail_url = None
        detail_link = link_pattern.search(details_raw)
        if detail_link:
            detail_url = html_lib.unescape(detail_link.group(1))

        # Parse payments amount
        payments_text = _clean(re.sub(r"<[^>]+>", "", payments_raw))
        payments_amount = _parse_currency(payments_text)

        row_hash = _hash_row([vendor_name, agency_contract_number, str(payments_amount)])

        contracts.append({
            "vendor_name": vendor_name,
            "agency_contract_number": agency_contract_number,
            "ctd_url": ctd_url,
            "detail_url": detail_url,
            "payments_amount": payments_amount,
            "row_hash": row_hash,
        })

    return contracts


def parse_contract_detail_html(html: Optional[str]) -> Dict[str, Any]:
    """Parse key-value pairs from a contract detail page.

    Expects a table with <th>Label</th><td>Value</td> rows.

    Returns a dict with normalized keys:
        vendor_name, agency, contract_number, description,
        start_date, end_date, current_contract_amount, award_type,
        class_subclass, expenditure_authority
    """
    if not html:
        return {}

    # Field name -> dict key mapping
    field_map = {
        "vendor name": "vendor_name",
        "agency": "agency",
        "contract number": "contract_number",
        "description": "description",
        "start date": "start_date",
        "end date": "end_date",
        "current contract amount": "current_contract_amount",
        "award type": "award_type",
        "class/subclass": "class_subclass",
        "expenditure authority": "expenditure_authority",
    }

    # Currency fields
    currency_fields = {"current_contract_amount"}

    result: Dict[str, Any] = {}

    # Match <th>...<td>... pairs
    pair_pattern = re.compile(
        r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
        re.DOTALL | re.IGNORECASE,
    )

    for match in pair_pattern.finditer(html):
        label = _clean(re.sub(r"<[^>]+>", "", match.group(1))).lower()
        value = _clean(re.sub(r"<[^>]+>", "", match.group(2)))

        if not label or not value:
            continue

        dict_key = field_map.get(label)
        if dict_key:
            if dict_key in currency_fields:
                result[dict_key] = _parse_currency(value)
            else:
                result[dict_key] = value

    return result


class ComptrollerContractsScraper:
    """Scraper for IL Comptroller State Contracts website."""

    SEARCH_URL = SEARCH_URL

    def __init__(self, conn, headless: bool = True):
        self.conn = conn
        self.headless = headless

    # ------------------------------------------------------------------
    # DB persistence — contracts from search results
    # ------------------------------------------------------------------

    def save_contracts(
        self,
        search_term: str,
        contracts: List[Dict[str, Any]],
        source_url: str,
    ) -> Tuple[int, int]:
        """Persist parsed contract rows. Returns (inserted, updated)."""
        inserted = 0
        updated = 0

        for c in contracts:
            row_hash = c["row_hash"]
            existing = self.conn.execute(
                "SELECT id, row_hash FROM comptroller_state_contracts "
                "WHERE vendor_name = ? AND agency_contract_number = ?",
                (c["vendor_name"], c["agency_contract_number"]),
            ).fetchone()

            if existing:
                if existing["row_hash"] != row_hash:
                    self.conn.execute(
                        """UPDATE comptroller_state_contracts
                           SET ctd_url = ?, detail_url = ?, payments_amount = ?,
                               source_url = ?, last_seen_at = CURRENT_TIMESTAMP,
                               row_hash = ?
                           WHERE id = ?""",
                        (c.get("ctd_url"), c.get("detail_url"),
                         c.get("payments_amount"), source_url, row_hash,
                         existing["id"]),
                    )
                    updated += 1
            else:
                self.conn.execute(
                    """INSERT INTO comptroller_state_contracts
                       (search_term, vendor_name, agency_contract_number,
                        ctd_url, detail_url, payments_amount,
                        source_url, row_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (search_term, c["vendor_name"], c["agency_contract_number"],
                     c.get("ctd_url"), c.get("detail_url"),
                     c.get("payments_amount"), source_url, row_hash),
                )
                inserted += 1

        self.conn.commit()
        return inserted, updated

    # ------------------------------------------------------------------
    # DB persistence — contract detail
    # ------------------------------------------------------------------

    def save_contract_detail(
        self,
        agency_contract_number: str,
        detail: Dict[str, Any],
        source_url: str,
    ) -> None:
        """Persist contract detail data. Upsert on agency_contract_number."""
        row_hash = _hash_row([
            detail.get("vendor_name", ""),
            agency_contract_number,
            str(detail.get("current_contract_amount", "")),
            detail.get("description", ""),
        ])

        existing = self.conn.execute(
            "SELECT id, row_hash FROM comptroller_contract_details "
            "WHERE agency_contract_number = ?",
            (agency_contract_number,),
        ).fetchone()

        if existing:
            if existing["row_hash"] != row_hash:
                self.conn.execute(
                    """UPDATE comptroller_contract_details
                       SET vendor_name = ?, agency = ?, description = ?,
                           start_date = ?, end_date = ?,
                           current_contract_amount = ?, award_type = ?,
                           class_subclass = ?, expenditure_authority = ?,
                           source_url = ?, updated_at = CURRENT_TIMESTAMP,
                           row_hash = ?
                       WHERE id = ?""",
                    (detail.get("vendor_name"), detail.get("agency"),
                     detail.get("description"), detail.get("start_date"),
                     detail.get("end_date"), detail.get("current_contract_amount"),
                     detail.get("award_type"), detail.get("class_subclass"),
                     detail.get("expenditure_authority"), source_url,
                     row_hash, existing["id"]),
                )
        else:
            self.conn.execute(
                """INSERT INTO comptroller_contract_details
                   (agency_contract_number, vendor_name, agency, description,
                    start_date, end_date, current_contract_amount, award_type,
                    class_subclass, expenditure_authority, source_url, row_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (agency_contract_number, detail.get("vendor_name"),
                 detail.get("agency"), detail.get("description"),
                 detail.get("start_date"), detail.get("end_date"),
                 detail.get("current_contract_amount"), detail.get("award_type"),
                 detail.get("class_subclass"), detail.get("expenditure_authority"),
                 source_url, row_hash),
            )

        self.conn.commit()

    # ------------------------------------------------------------------
    # Scrape run tracking
    # ------------------------------------------------------------------

    def create_run(self, mode: str) -> int:
        """Create a new scrape run record."""
        cur = self.conn.execute(
            "INSERT INTO comptroller_scrape_runs (mode) VALUES (?)",
            (mode,),
        )
        self.conn.commit()
        return cur.lastrowid

    def complete_run(
        self,
        run_id: int,
        vendors_searched: int = 0,
        contracts_found: int = 0,
        details_scraped: int = 0,
        error_count: int = 0,
        notes: str = "",
    ) -> None:
        """Mark a scrape run as complete with stats."""
        self.conn.execute(
            """UPDATE comptroller_scrape_runs
               SET completed_at = CURRENT_TIMESTAMP,
                   vendors_searched = ?, contracts_found = ?,
                   details_scraped = ?, error_count = ?, notes = ?
               WHERE run_id = ?""",
            (vendors_searched, contracts_found, details_scraped,
             error_count, notes, run_id),
        )
        self.conn.commit()
