"""OpenBook Illinois Comptroller scraper.

Scrapes state contract data and campaign contribution records from
https://openbook.illinoiscomptroller.gov/ for targeted vendor enrichment.

Uses HTTP requests (with session cookies) for the primary flow and falls
back to Playwright only when needed.  The portal is ColdFusion-based on IIS;
JSESSIONID is required for POST searches.
"""
import hashlib
import asyncio
import html as html_lib
import http.cookiejar
import json
import logging
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from playwright.async_api import async_playwright, Browser, Page

from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://openbook.illinoiscomptroller.gov"
AUTOSUGGEST_URL = f"{BASE_URL}/cfcs/autosuggest.cfc"
SEARCH_URL = f"{BASE_URL}/search.cfm"
INDEX_URL = f"{BASE_URL}/index.cfm"

# Contract detail is on a different host
DETAIL_HOST = "https://office.illinoiscomptroller.gov"


def _hash_row(parts: list) -> str:
    """Create a SHA-1 hash from a list of string parts for dedupe."""
    raw = "|".join(str(p).strip() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _parse_currency(text: str) -> Optional[float]:
    """Parse a currency string like '$1,234.56' to float."""
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", html_lib.unescape(text.strip()))
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _clean(text: str) -> str:
    """Strip and collapse whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def _normalize_vendor_key(text: str) -> str:
    """Normalize vendor keys/names for exact comparisons."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().upper())


def _clean_html_cell(text: str) -> str:
    """Unescape HTML entities, strip tags, then collapse whitespace."""
    if not text:
        return ""
    text = html_lib.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text.strip())


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists (works on both SQLite and Postgres via compat layer)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _pick_best_match(
    term: str,
    suggestions: List[Dict[str, str]],
    pick_first: bool = False,
    person_exact_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """Pick the best vendor match from autosuggest results.

    Strategies in priority order: exact > prefix > fuzzy (Jaccard >= 0.5) > pick_first.
    Returns dict with vendor_key, vendor_label, match_method, confidence or None.
    """
    if not suggestions:
        return None

    if person_exact_only:
        normalized_term = _normalize_vendor_key(term)
        for s in suggestions:
            if _normalize_vendor_key(s["id"]) == normalized_term:
                return {
                    "vendor_key": s["id"],
                    "vendor_label": s["value"],
                    "match_method": "exact",
                    "confidence": 1.0,
                }
        return None

    upper_term = term.upper().strip()

    # 1) Exact match
    for s in suggestions:
        if s["id"].strip().upper() == upper_term:
            return {
                "vendor_key": s["id"],
                "vendor_label": s["value"],
                "match_method": "exact",
                "confidence": 1.0,
            }

    # 2) Prefix match
    for s in suggestions:
        if s["id"].strip().upper().startswith(upper_term):
            return {
                "vendor_key": s["id"],
                "vendor_label": s["value"],
                "match_method": "prefix",
                "confidence": 0.9,
            }

    # 3) Fuzzy (Jaccard on tokens)
    seed_tokens = set(upper_term.split())
    best_score = 0.0
    best_match = None
    for s in suggestions:
        cand_tokens = set(s["id"].strip().upper().split())
        if not seed_tokens or not cand_tokens:
            continue
        intersection = len(seed_tokens & cand_tokens)
        union = len(seed_tokens | cand_tokens)
        score = intersection / union if union > 0 else 0.0
        if score > best_score:
            best_score = score
            best_match = s

    if best_match and best_score >= 0.5:
        return {
            "vendor_key": best_match["id"],
            "vendor_label": best_match["value"],
            "match_method": "fuzzy",
            "confidence": round(best_score, 3),
        }

    # 4) Pick-first fallback
    if pick_first:
        return {
            "vendor_key": suggestions[0]["id"],
            "vendor_label": suggestions[0]["value"],
            "match_method": "pick_first",
            "confidence": 0.5,
        }

    return None


# ---------------------------------------------------------------------------
# Smart search term generation
# ---------------------------------------------------------------------------

_STRIP_SUFFIXES = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "LTD", "LP", "LLP",
    "INCORPORATED", "LIMITED", "COMPANY", "GROUP", "HOLDINGS",
    "ENTERPRISES", "SERVICES", "PARTNERS", "PARTNERSHIP",
}

_STRIP_GEO = {"OF", "ILLINOIS", "CHICAGO", "SPRINGFIELD", "IL"}

_ROMAN_NUMERALS = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}

_CONTRIB_HEADER_TEXTS = frozenset({
    "CONTRIBUTED BY", "RECEIVED BY", "EMPLOYER", "DATE", "AMOUNT",
})
_CONTRIB_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")

_ABBREVIATION_ALIASES: Dict[str, List[str]] = {
    "COMED": ["COMMONWEALTH EDISON"],
    "BCBS": ["BLUE CROSS BLUE SHIELD"],
    "CTA": ["CHICAGO TRANSIT AUTHORITY"],
    "ATT": ["AT&T"],
    "IBM": ["INTERNATIONAL BUSINESS MACHINES"],
    "CDW": ["CDW GOVERNMENT"],
    "IDOT": ["ILLINOIS DEPARTMENT OF TRANSPORTATION"],
    "IDPH": ["ILLINOIS DEPARTMENT OF PUBLIC HEALTH"],
    "IDHS": ["ILLINOIS DEPARTMENT OF HUMAN SERVICES"],
    "RTA": ["REGIONAL TRANSPORTATION AUTHORITY"],
    "METRA": ["METROPOLITAN RAIL"],
    "PACE": ["PACE SUBURBAN BUS"],
    "NICOR": ["NORTHERN ILLINOIS GAS"],
    "AMEREN": ["AMEREN ILLINOIS"],
    "UI": ["UNIVERSITY OF ILLINOIS"],
    "SIU": ["SOUTHERN ILLINOIS UNIVERSITY"],
    "UIC": ["UNIVERSITY OF ILLINOIS CHICAGO"],
    "ISBE": ["ILLINOIS STATE BOARD OF EDUCATION"],
    "CPS": ["CHICAGO PUBLIC SCHOOLS", "CHICAGO BOARD OF EDUCATION"],
    "CMS": ["ILLINOIS DEPARTMENT OF CENTRAL MANAGEMENT SERVICES"],
    "IDNR": ["ILLINOIS DEPARTMENT OF NATURAL RESOURCES"],
    "IDOA": ["ILLINOIS DEPARTMENT OF AGRICULTURE"],
    "IDES": ["ILLINOIS DEPARTMENT OF EMPLOYMENT SECURITY"],
    "IDOC": ["ILLINOIS DEPARTMENT OF CORRECTIONS"],
    "IDFPR": ["ILLINOIS DEPARTMENT OF FINANCIAL AND PROFESSIONAL REGULATION"],
    "DCFS": ["ILLINOIS DEPARTMENT OF CHILDREN AND FAMILY SERVICES"],
    "DCEO": ["ILLINOIS DEPARTMENT OF COMMERCE AND ECONOMIC OPPORTUNITY"],
    "IEMA": ["ILLINOIS EMERGENCY MANAGEMENT AGENCY"],
    "MWRD": ["METROPOLITAN WATER RECLAMATION DISTRICT"],
    "CDB": ["ILLINOIS CAPITAL DEVELOPMENT BOARD"],
}


def _is_person_name(name: str) -> bool:
    """Detect likely person names in LAST, FIRST format."""
    if not name or "," not in name:
        return False
    parts = name.strip().upper().split(",", 1)
    after_comma = parts[1].strip().split()
    if not after_comma:
        return False
    return not all(t in _STRIP_SUFFIXES for t in after_comma)


def generate_search_terms(name: str) -> List[str]:
    """Generate smart search terms from a vendor/person name.

    Returns a deduplicated list of search terms ordered by specificity:
    [primary_identifier, *alias_expansions, original_if_different].

    Examples:
        "COMCAST OF ILLINOIS III INC" -> ["COMCAST", "COMCAST OF ILLINOIS III INC"]
        "SMITH, JOHN" -> ["SMITH, JOHN"]
        "COMED" -> ["COMED", "COMMONWEALTH EDISON"]
        "DELOITTE" -> ["DELOITTE"]
    """
    if not name:
        return []

    cleaned = re.sub(r"\s+", " ", name.strip().upper())
    if not cleaned:
        return []

    terms: List[str] = []

    # --- Person detection ---
    if _is_person_name(cleaned):
        terms.append(cleaned)
        return _dedupe_filter(terms)

    # Company with comma before suffix: strip suffix, continue
    if "," in cleaned:
        parts = cleaned.split(",", 1)
        before_comma = parts[0].strip()
        if before_comma:
            cleaned = before_comma

    # --- Suffix / geo / roman stripping ---
    tokens = cleaned.split()
    significant: List[str] = []
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
        fallback = tokens[0] if tokens else ""
        if fallback:
            terms.append(fallback)
        return _dedupe_filter(terms)

    # Remove geographic phrases from middle/end ("OF ILLINOIS" etc.)
    result: List[str] = []
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
        result = [significant[0]] if significant else []

    # --- Primary identifier ---
    if len(result) <= 2:
        primary = " ".join(result)
    elif len(result) == 4 and result[0] == result[2]:
        # Pattern like "BLUE CROSS BLUE SHIELD" — keep all 4
        primary = " ".join(result)
    else:
        # Avoid overly broad one-word searches for long names ("BLUE", "CHICAGO").
        primary = " ".join(result[:2])

    if primary:
        terms.append(primary)

    # --- Abbreviation expansion ---
    # Check both the primary and the original cleaned name
    for check_term in [primary, cleaned]:
        normalized_key = re.sub(r"[&]", "", check_term).replace(" ", "")
        if check_term in _ABBREVIATION_ALIASES:
            terms.extend(_ABBREVIATION_ALIASES[check_term])
        elif normalized_key in _ABBREVIATION_ALIASES:
            terms.extend(_ABBREVIATION_ALIASES[normalized_key])

    # --- Include original as fallback ---
    original_cleaned = re.sub(r"\s+", " ", name.strip().upper())
    if original_cleaned and original_cleaned != primary:
        terms.append(original_cleaned)

    return _dedupe_filter(terms)


def _dedupe_filter(terms: List[str]) -> List[str]:
    """Remove duplicates and terms shorter than 3 chars."""
    seen: set = set()
    result: List[str] = []
    for t in terms:
        t = t.strip()
        if len(t) < 3:
            continue
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _score_one(reference: str, candidate_key: str) -> Tuple[str, float]:
    """Score a single candidate against a reference string.

    Returns (method, confidence).
    """
    upper_ref = reference.upper().strip()
    upper_cand = candidate_key.upper().strip()

    if upper_cand == upper_ref:
        return "exact", 1.0
    if upper_cand.startswith(upper_ref):
        ref_tokens = upper_ref.split()
        if len(ref_tokens) == 1 and len(upper_ref) <= 5:
            return "prefix", 0.5   # Penalize short single-token ("BUSH", "KYLE")
        return "prefix", 0.9
    if upper_ref.startswith(upper_cand):
        return "prefix", 0.85

    ref_tokens = set(upper_ref.split())
    cand_tokens = set(upper_cand.split())
    if ref_tokens and cand_tokens:
        intersection = len(ref_tokens & cand_tokens)
        union = len(ref_tokens | cand_tokens)
        score = intersection / union if union > 0 else 0.0
    else:
        score = 0.0
    return "fuzzy", round(score, 3)


def _score_all_matches(
    seed_text: str,
    suggestions: Optional[List[Dict[str, str]]],
    search_term: str,
    min_confidence: float = 0.8,
) -> List[Dict[str, Any]]:
    """Score ALL autosuggest results against the seed text AND search term.

    Scores each suggestion against both the original seed text and the search
    term, taking the maximum confidence.  This ensures that suggestions matching
    the search term well (e.g., "COMCAST CORPORATION" for search term "COMCAST")
    are kept even when the full seed text has many noise tokens.

    Returns all results above min_confidence, sorted by confidence descending.
    Each result dict: {vendor_key, vendor_label, match_method, confidence, search_term}.
    """
    if not suggestions:
        return []

    results: List[Dict[str, Any]] = []

    for s in suggestions:
        vendor_key = s["id"].strip()
        vendor_label = s["value"]

        # Score against both seed text and search term, take max
        method_seed, conf_seed = _score_one(seed_text, vendor_key)
        method_term, conf_term = _score_one(search_term, vendor_key)

        if conf_seed >= conf_term:
            method, confidence = method_seed, conf_seed
        else:
            method, confidence = method_term, conf_term

        if confidence >= min_confidence:
            results.append({
                "vendor_key": vendor_key,
                "vendor_label": vendor_label,
                "match_method": method,
                "confidence": confidence,
                "search_term": search_term,
            })

    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results


def parse_contracts_html(html: str, vendor_key: str) -> List[Dict[str, Any]]:
    """Parse contract rows from the OpenBook contracts results HTML.

    Uses regex-based extraction to avoid adding lxml/bs4 dependencies.
    Operates on the raw HTML of the contracts tab.

    Returns a list of dicts with keys:
        vendor_label, fiscal_year, agency_code, agency_name,
        contract_number, award_amount, detail_url, row_hash
    """
    contracts = []

    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)

    # Extract detail URL from onclick
    detail_pattern = re.compile(r"window\.open\('([^']+)'\s*,")

    for match in row_pattern.finditer(html):
        cells = cell_pattern.findall(match.group(1))
        if len(cells) < 5:
            continue

        vendor_label_raw = cells[0]
        fy_raw = cells[1]
        contract_cell = cells[2]
        agency_raw = cells[3]
        amount_raw = cells[4]

        # Skip header rows
        fy_text = _clean(fy_raw)
        if not fy_text or not fy_text.isdigit():
            continue

        # Skip "No Records Found"
        if "No Records Found" in vendor_label_raw:
            continue

        vendor_label = _clean(vendor_label_raw)
        fiscal_year = int(fy_text)
        agency_name = _clean(agency_raw)
        award_amount = _parse_currency(amount_raw)

        # Parse contract number from the cell (may contain a link)
        # Format: "420 / 60021431070" or just the number
        contract_text = _clean(re.sub(r"<[^>]+>", "", contract_cell))
        contract_number = None
        agency_code = None
        if "/" in contract_text:
            parts = contract_text.split("/", 1)
            agency_code = parts[0].strip()
            contract_number = parts[1].strip()
        else:
            contract_number = contract_text

        # Extract detail URL from onclick
        detail_url = None
        detail_match = detail_pattern.search(contract_cell)
        if detail_match:
            detail_url = html_lib.unescape(detail_match.group(1))

        row_hash = _hash_row([
            vendor_key, vendor_label, str(fiscal_year),
            agency_code or "", contract_number or "",
            str(award_amount),
        ])

        contracts.append({
            "vendor_label": vendor_label,
            "fiscal_year": fiscal_year,
            "agency_code": agency_code,
            "agency_name": agency_name,
            "contract_number": contract_number,
            "award_amount": award_amount,
            "detail_url": detail_url,
            "row_hash": row_hash,
        })

    return contracts


def parse_contributions_html(html: str, vendor_key: str) -> List[Dict[str, Any]]:
    """Parse contribution rows from the OpenBook contributions/employees tab HTML.

    Columns: Contributed By, Received By, Employer, Date, Amount

    Returns a list of dicts with keys:
        contributor_name, contributor_first_name, recipient_name,
        employer, contribution_date, amount, row_hash
    """
    contributions = []

    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)

    for match in row_pattern.finditer(html):
        cells = cell_pattern.findall(match.group(1))
        if len(cells) < 5:
            continue

        contributor = _clean_html_cell(cells[0])
        recipient = _clean_html_cell(cells[1])
        employer = _clean_html_cell(cells[2])
        date_str = _clean_html_cell(cells[3])
        amount_raw = cells[4]

        # Skip header rows, empty rows, and "No Records Found"
        if not contributor or contributor.upper() in _CONTRIB_HEADER_TEXTS:
            continue
        if recipient.upper() in _CONTRIB_HEADER_TEXTS:
            continue
        if "No Records Found" in contributor:
            continue
        # Date validation (same pattern as contracts parser)
        if not _CONTRIB_DATE_RE.match(date_str):
            continue

        amount = _parse_currency(amount_raw)

        row_hash = _hash_row([
            vendor_key, contributor, recipient,
            date_str, str(amount),
        ])

        contributions.append({
            "contributor_name": contributor,
            "contributor_first_name": None,
            "recipient_name": recipient,
            "employer": employer,
            "contribution_date": date_str,
            "amount": amount,
            "row_hash": row_hash,
        })

    return contributions


def parse_contract_detail_html(
    html: str,
    vendor_key: str,
    contract_number: str,
    fiscal_year: Optional[int],
) -> List[Dict[str, Any]]:
    """Parse warrant/payment rows from a contract detail popup HTML page."""
    warrants: List[Dict[str, Any]] = []
    if not html:
        return warrants

    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
    date_pattern = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")

    for match in row_pattern.finditer(html):
        cells = cell_pattern.findall(match.group(1))
        if len(cells) < 2:
            continue

        issue_raw = _clean_html_cell(cells[0])
        amount_raw = _clean_html_cell(cells[1])

        if not issue_raw or issue_raw.lower() == "issue date":
            continue
        if not date_pattern.match(issue_raw):
            continue

        payment_amount = _parse_currency(amount_raw)
        if payment_amount is None:
            continue

        row_hash = _hash_row([
            vendor_key,
            contract_number or "",
            str(fiscal_year or ""),
            issue_raw,
            str(payment_amount),
        ])

        warrants.append({
            "issue_date": issue_raw,
            "payment_amount": payment_amount,
            "row_hash": row_hash,
        })

    if html.strip() and not warrants:
        logger.debug(
            "contract_detail_html had content but parsed 0 warrants; first 500 chars: %s",
            html[:500],
        )

    return warrants


# ------------------------------------------------------------------
# Lightweight HTTP session (stdlib only, no Playwright needed)
# ------------------------------------------------------------------


class _HttpSession:
    """Minimal HTTP session with cookie persistence for OpenBook scraping."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj),
        )
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def get(self, url: str) -> str:
        """GET request, returns response body as text."""
        req = urllib.request.Request(url, headers=self._headers)
        with self._opener.open(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def post(self, url: str, data: Dict[str, str]) -> str:
        """POST with URL-encoded form data, returns response body as text."""
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=encoded, headers=self._headers, method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self._opener.open(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


class OpenBookScraper:
    """Scrapes OpenBook vendor contracts and contributions."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        rate_limiter: Optional[RateLimiter] = None,
        headless: bool = True,
    ):
        self.conn = conn
        self.rate_limiter = rate_limiter or RateLimiter(
            requests_per_minute=30, min_delay=1.0, max_delay=2.0
        )
        self.headless = headless
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._playwright = None
        self._ensure_openbook_tables()

    def _is_postgres(self) -> bool:
        return hasattr(self.conn, "_pg_conn")

    def _ensure_openbook_tables(self) -> None:
        """Ensure OpenBook tables exist for both SQLite and Postgres backends."""
        try:
            if self._is_postgres():
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_vendor_seed (
                           seed_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                           seed_text TEXT NOT NULL,
                           seed_source TEXT NOT NULL,
                           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           UNIQUE(seed_text, seed_source)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_vendor_match (
                           match_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                           seed_id BIGINT NOT NULL REFERENCES openbook_vendor_seed(seed_id),
                           openbook_vendor_key TEXT NOT NULL,
                           openbook_vendor_label TEXT NOT NULL,
                           match_method TEXT NOT NULL,
                           confidence DOUBLE PRECISION NOT NULL,
                           search_term_used TEXT,
                           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           UNIQUE(seed_id, openbook_vendor_key)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contracts_raw (
                           id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                           openbook_vendor_key TEXT NOT NULL,
                           vendor_label TEXT NOT NULL,
                           fiscal_year INTEGER,
                           agency_code TEXT,
                           agency_name TEXT,
                           contract_number TEXT,
                           award_amount DOUBLE PRECISION,
                           detail_url TEXT,
                           source_url TEXT NOT NULL,
                           first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           row_hash TEXT NOT NULL,
                           UNIQUE(openbook_vendor_key, contract_number, fiscal_year)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contract_warrants (
                           id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                           openbook_vendor_key TEXT NOT NULL,
                           contract_number TEXT NOT NULL,
                           fiscal_year INTEGER,
                           issue_date TEXT,
                           payment_amount DOUBLE PRECISION,
                           detail_url TEXT,
                           source_url TEXT NOT NULL,
                           first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           row_hash TEXT NOT NULL,
                           UNIQUE(row_hash)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contract_detail_status (
                           id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                           openbook_vendor_key TEXT NOT NULL,
                           contract_number TEXT NOT NULL,
                           fiscal_year INTEGER,
                           detail_url TEXT,
                           status TEXT NOT NULL,
                           attempt_count INTEGER NOT NULL DEFAULT 0,
                           warrant_row_count INTEGER NOT NULL DEFAULT 0,
                           last_error TEXT,
                           last_attempted_at TIMESTAMP,
                           updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           UNIQUE(openbook_vendor_key, contract_number, fiscal_year)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contributions_raw (
                           id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                           openbook_vendor_key TEXT NOT NULL,
                           contributor_name TEXT,
                           contributor_first_name TEXT,
                           recipient_name TEXT,
                           employer TEXT,
                           contribution_date TEXT,
                           amount DOUBLE PRECISION,
                           source_url TEXT NOT NULL,
                           first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           row_hash TEXT NOT NULL,
                           UNIQUE(row_hash)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_scrape_runs (
                           run_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                           started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           completed_at TIMESTAMP,
                           mode TEXT NOT NULL,
                           seed_count INTEGER DEFAULT 0,
                           match_count INTEGER DEFAULT 0,
                           contract_rows INTEGER DEFAULT 0,
                           contribution_rows INTEGER DEFAULT 0,
                           error_count INTEGER DEFAULT 0,
                           notes TEXT
                       )"""
                )
            else:
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_vendor_seed (
                           seed_id INTEGER PRIMARY KEY AUTOINCREMENT,
                           seed_text TEXT NOT NULL,
                           seed_source TEXT NOT NULL,
                           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           UNIQUE(seed_text, seed_source)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_vendor_match (
                           match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                           seed_id INTEGER NOT NULL REFERENCES openbook_vendor_seed(seed_id),
                           openbook_vendor_key TEXT NOT NULL,
                           openbook_vendor_label TEXT NOT NULL,
                           match_method TEXT NOT NULL,
                           confidence REAL NOT NULL,
                           search_term_used TEXT,
                           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           UNIQUE(seed_id, openbook_vendor_key)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contracts_raw (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           openbook_vendor_key TEXT NOT NULL,
                           vendor_label TEXT NOT NULL,
                           fiscal_year INTEGER,
                           agency_code TEXT,
                           agency_name TEXT,
                           contract_number TEXT,
                           award_amount REAL,
                           detail_url TEXT,
                           source_url TEXT NOT NULL,
                           first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           row_hash TEXT NOT NULL,
                           UNIQUE(openbook_vendor_key, contract_number, fiscal_year)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contract_warrants (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           openbook_vendor_key TEXT NOT NULL,
                           contract_number TEXT NOT NULL,
                           fiscal_year INTEGER,
                           issue_date TEXT,
                           payment_amount REAL,
                           detail_url TEXT,
                           source_url TEXT NOT NULL,
                           first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           row_hash TEXT NOT NULL,
                           UNIQUE(row_hash)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contract_detail_status (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           openbook_vendor_key TEXT NOT NULL,
                           contract_number TEXT NOT NULL,
                           fiscal_year INTEGER,
                           detail_url TEXT,
                           status TEXT NOT NULL,
                           attempt_count INTEGER NOT NULL DEFAULT 0,
                           warrant_row_count INTEGER NOT NULL DEFAULT 0,
                           last_error TEXT,
                           last_attempted_at TIMESTAMP,
                           updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           UNIQUE(openbook_vendor_key, contract_number, fiscal_year)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_contributions_raw (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           openbook_vendor_key TEXT NOT NULL,
                           contributor_name TEXT,
                           contributor_first_name TEXT,
                           recipient_name TEXT,
                           employer TEXT,
                           contribution_date TEXT,
                           amount REAL,
                           source_url TEXT NOT NULL,
                           first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           row_hash TEXT NOT NULL,
                           UNIQUE(row_hash)
                       )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS openbook_scrape_runs (
                           run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                           started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           completed_at TIMESTAMP,
                           mode TEXT NOT NULL,
                           seed_count INTEGER DEFAULT 0,
                           match_count INTEGER DEFAULT 0,
                           contract_rows INTEGER DEFAULT 0,
                           contribution_rows INTEGER DEFAULT 0,
                           error_count INTEGER DEFAULT 0,
                           notes TEXT
                       )"""
                )
            self.conn.commit()

            # Safe migration: add search_term_used column if missing (existing DBs)
            try:
                self.conn.execute(
                    "ALTER TABLE openbook_vendor_match ADD COLUMN search_term_used TEXT"
                )
                self.conn.commit()
            except Exception:
                pass  # Column already exists

        except Exception as e:
            logger.warning("Could not ensure OpenBook tables: %s", e)

    async def _init_browser(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._page = await self._browser.new_page()

    async def _close_browser(self) -> None:
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------
    # Vendor resolution via autosuggest JSON API
    # ------------------------------------------------------------------

    async def resolve_vendor(
        self, vendor_name: str, pick_first: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Resolve a vendor name to an OpenBook vendor key via autosuggest (Playwright).

        Returns dict with keys: vendor_key, vendor_label, match_method, confidence
        or None if no match found.
        """
        term = vendor_name.strip()[:60]
        if len(term) < 3:
            logger.warning("Vendor name too short for autosuggest: %s", vendor_name)
            return None

        url = f"{AUTOSUGGEST_URL}?method=getVendors&returnformat=json&term={quote(term)}"

        self.rate_limiter.wait()
        try:
            response = await self._page.context.request.get(url, timeout=30000)
            if not response.ok:
                self.rate_limiter.record_error()
                logger.error(
                    "Autosuggest request failed (%s): %s",
                    response.status,
                    url,
                )
                return None
            resp = await response.text()
            self.rate_limiter.record_success()
        except Exception as e:
            self.rate_limiter.record_error()
            logger.error("Autosuggest fetch failed: %s", e)
            return None

        try:
            suggestions = json.loads(resp)
        except (json.JSONDecodeError, TypeError):
            logger.error("Invalid JSON from autosuggest: %s", resp[:200])
            return None

        if not suggestions:
            logger.info("No autosuggest results for '%s'", term)
            return None

        result = _pick_best_match(
            term,
            suggestions,
            pick_first=pick_first,
            person_exact_only=_is_person_name(vendor_name),
        )
        if result is None:
            logger.info("No confident match for '%s' among %d suggestions", term, len(suggestions))
        return result

    # ------------------------------------------------------------------
    # HTTP-based vendor resolution (no Playwright needed)
    # ------------------------------------------------------------------

    def resolve_vendor_http(
        self, vendor_name: str, session: _HttpSession, pick_first: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Resolve a vendor name via the autosuggest JSON API using plain HTTP."""
        term = vendor_name.strip()[:60]
        if len(term) < 3:
            logger.warning("Vendor name too short for autosuggest: %s", vendor_name)
            return None

        url = f"{AUTOSUGGEST_URL}?method=getVendors&returnformat=json&term={quote(term)}"

        self.rate_limiter.wait()
        try:
            resp = session.get(url)
            self.rate_limiter.record_success()
        except Exception as e:
            self.rate_limiter.record_error()
            logger.error("HTTP autosuggest fetch failed: %s", e)
            return None

        try:
            suggestions = json.loads(resp)
        except (json.JSONDecodeError, TypeError):
            logger.error("Invalid JSON from autosuggest: %s", resp[:200])
            return None

        if not suggestions:
            logger.info("No autosuggest results for '%s'", term)
            return None

        result = _pick_best_match(
            term,
            suggestions,
            pick_first=pick_first,
            person_exact_only=_is_person_name(vendor_name),
        )
        if result is None:
            logger.info("No confident match for '%s' among %d suggestions", term, len(suggestions))
        return result

    def resolve_all_matches_http(
        self,
        seed_text: str,
        session: _HttpSession,
        search_terms: Optional[List[str]] = None,
        min_confidence: float = 0.8,
    ) -> List[Dict[str, Any]]:
        """Resolve a seed to ALL matching vendors via smart search terms.

        For each search term, queries the autosuggest API and scores all
        results. Returns deduplicated matches sorted by confidence descending.
        """
        person_exact_only = _is_person_name(seed_text)
        if search_terms is None:
            if person_exact_only:
                search_terms = [_normalize_vendor_key(seed_text)]
            else:
                search_terms = generate_search_terms(seed_text)

        if not search_terms:
            logger.warning("No search terms generated for '%s'", seed_text)
            return []

        # Collect all suggestions across all search terms, deduped by vendor_key
        all_suggestions: Dict[str, Dict[str, str]] = {}
        term_for_suggestion: Dict[str, str] = {}

        normalized_seed = _normalize_vendor_key(seed_text) if person_exact_only else ""
        for term in search_terms:
            term_truncated = term.strip()[:60]
            if len(term_truncated) < 3:
                continue

            url = f"{AUTOSUGGEST_URL}?method=getVendors&returnformat=json&term={quote(term_truncated)}"

            self.rate_limiter.wait()
            try:
                resp = session.get(url)
                self.rate_limiter.record_success()
            except Exception as e:
                self.rate_limiter.record_error()
                logger.error("HTTP autosuggest fetch failed for term '%s': %s", term_truncated, e)
                continue

            try:
                suggestions = json.loads(resp)
            except (json.JSONDecodeError, TypeError):
                logger.error("Invalid JSON from autosuggest for term '%s'", term_truncated)
                continue

            if not suggestions:
                continue

            for s in suggestions:
                if person_exact_only and _normalize_vendor_key(s["id"]) != normalized_seed:
                    continue
                vk = s["id"].strip()
                if vk not in all_suggestions:
                    all_suggestions[vk] = s
                    term_for_suggestion[vk] = term_truncated

        if not all_suggestions:
            logger.info("No autosuggest results for '%s' (tried: %s)", seed_text, search_terms)
            return []

        if person_exact_only:
            scored: List[Dict[str, Any]] = []
            for s in all_suggestions.values():
                vk = s["id"].strip()
                scored.append({
                    "vendor_key": vk,
                    "vendor_label": s["value"],
                    "match_method": "exact",
                    "confidence": 1.0,
                    "search_term": term_for_suggestion.get(vk, search_terms[0]),
                })
            scored.sort(key=lambda r: r["confidence"], reverse=True)
            logger.info(
                "Resolved '%s' -> %d exact matches (searched: %s)",
                seed_text, len(scored), ", ".join(search_terms),
            )
            return scored

        # Score all collected suggestions against the seed text
        unique_suggestions = list(all_suggestions.values())
        scored: List[Dict[str, Any]] = []

        for s in unique_suggestions:
            vk = s["id"].strip()
            search_term_used = term_for_suggestion.get(vk, search_terms[0])
            results = _score_all_matches(seed_text, [s], search_term_used, min_confidence)
            scored.extend(results)

        scored.sort(key=lambda r: r["confidence"], reverse=True)
        logger.info(
            "Resolved '%s' -> %d matches (searched: %s)",
            seed_text, len(scored), ", ".join(search_terms),
        )
        return scored

    # ------------------------------------------------------------------
    # HTTP-based contract/contribution search (no Playwright needed)
    # ------------------------------------------------------------------

    def search_contracts_http(
        self, vendor_key: str, session: _HttpSession
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Search OpenBook for contracts via plain HTTP POST.

        Returns (html, contracts_list).
        """
        # GET index.cfm first to establish/refresh session cookie
        self.rate_limiter.wait()
        try:
            session.get(INDEX_URL)
            self.rate_limiter.record_success()
        except Exception as e:
            self.rate_limiter.record_error()
            logger.warning("Index page GET failed (may be ok): %s", e)

        # POST the search form
        self.rate_limiter.wait()
        form_data = {
            "txtVendorName": vendor_key,
            "selVendorId": vendor_key,
            "hdnActiveTab": "contracts",
            "hdnMainActiveTab": "contracts",
            "hdnSubTabClick": "0",
        }
        try:
            html = session.post(SEARCH_URL, form_data)
            self.rate_limiter.record_success()
        except Exception as e:
            self.rate_limiter.record_error()
            logger.error("HTTP contract search failed for '%s': %s", vendor_key, e)
            return "", []

        contracts = parse_contracts_html(html, vendor_key)
        logger.info("Parsed %d contracts for vendor '%s' (HTTP)", len(contracts), vendor_key)
        return html, contracts

    def search_contributions_http(
        self, vendor_key: str, session: _HttpSession, tab: str = "contributions"
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Switch to contributions or employees sub-tab via HTTP POST.

        Must be called AFTER search_contracts_http (session needs prior search state).
        tab: 'contributions' or 'employees'
        """
        self.rate_limiter.wait()
        form_data = {
            "txtVendorName": vendor_key,
            "selVendorId": vendor_key,
            "hdnActiveTab": tab,
            "hdnMainActiveTab": "contracts",
            "hdnSubTabClick": "1",
        }
        try:
            html = session.post(SEARCH_URL, form_data)
            self.rate_limiter.record_success()
        except Exception as e:
            self.rate_limiter.record_error()
            logger.error("HTTP contribution tab '%s' fetch failed: %s", tab, e)
            return "", []

        contributions = parse_contributions_html(html, vendor_key)
        logger.info(
            "Parsed %d %s rows for vendor '%s' (HTTP)",
            len(contributions), tab, vendor_key,
        )
        return html, contributions

    def fetch_contract_detail_http(
        self,
        detail_url: str,
        session: _HttpSession,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Fetch a contract detail popup page and return raw HTML."""
        if not detail_url:
            return "", []

        url = detail_url.strip()
        if url.startswith("/"):
            url = DETAIL_HOST.rstrip("/") + url
        elif url.startswith("office.illinoiscomptroller.gov"):
            url = "https://" + url

        self.rate_limiter.wait()
        try:
            html = session.get(url)
            self.rate_limiter.record_success()
            return html, []
        except Exception as e:
            self.rate_limiter.record_error()
            logger.error("HTTP contract detail fetch failed for '%s': %s", url, e)
            return "", []

    # ------------------------------------------------------------------
    # Contract search (Playwright)
    # ------------------------------------------------------------------

    async def search_contracts(
        self, vendor_key: str, max_pages: int = 5
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Search OpenBook for contracts by vendor.

        Returns (html, contracts_list).
        max_pages is retained for future pagination support but currently
        all results load in one page.
        """
        self.rate_limiter.wait()

        # Navigate to index first to establish session
        try:
            await self._page.goto(INDEX_URL, wait_until="networkidle", timeout=30000)
        except Exception as e:
            logger.warning("Index page load issue (may be ok): %s", e)

        # Fill and submit the form
        self.rate_limiter.wait()

        try:
            # Fill vendor name
            await self._page.fill("#txtVendorName", vendor_key)
            await self._page.evaluate(
                """(vk) => {
                    document.getElementById('selVendorId').value = vk;
                    document.getElementById('hdnActiveTab').value = 'contracts';
                    document.getElementById('hdnMainActiveTab').value = 'contracts';
                    document.getElementById('hdnSubTabClick').value = '0';
                }""",
                vendor_key,
            )

            # Submit
            await self._page.click(".searchBtn")
            await self._page.wait_for_load_state("networkidle", timeout=30000)
            self.rate_limiter.record_success()
        except Exception as e:
            self.rate_limiter.record_error()
            logger.error("Contract search failed for '%s': %s", vendor_key, e)
            return "", []

        html = await self._page.content()
        contracts = parse_contracts_html(html, vendor_key)
        logger.info("Parsed %d contracts for vendor '%s'", len(contracts), vendor_key)
        return html, contracts

    # ------------------------------------------------------------------
    # Contribution sub-tabs (Playwright)
    # ------------------------------------------------------------------

    async def search_contributions(
        self, vendor_key: str, tab: str = "contributions"
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Switch to contributions or employees sub-tab and extract rows.

        Must be called AFTER search_contracts (requires active session on results page).
        tab: 'contributions' or 'employees'
        """
        self.rate_limiter.wait()

        try:
            # Click the appropriate sub-tab via JS form submission
            await self._page.evaluate(
                """(tab) => {
                    document.getElementById('hdnSubTabClick').value = '1';
                    document.getElementById('hdnActiveTab').value = tab;
                    document.getElementById('resultsForm').submit();
                }""",
                tab,
            )
            await self._page.wait_for_load_state("networkidle", timeout=30000)
            self.rate_limiter.record_success()
        except Exception as e:
            self.rate_limiter.record_error()
            logger.error("Contribution tab '%s' switch failed: %s", tab, e)
            return "", []

        html = ""
        for _ in range(3):
            try:
                html = await self._page.content()
                break
            except Exception as e:
                if "page is navigating" in str(e).lower():
                    try:
                        await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    await self._page.wait_for_timeout(300)
                    continue
                logger.error("Contribution tab '%s' content read failed: %s", tab, e)
                return "", []

        if not html:
            logger.error("Contribution tab '%s' content read failed after retries", tab)
            return "", []

        contributions = parse_contributions_html(html, vendor_key)
        logger.info(
            "Parsed %d %s rows for vendor '%s'",
            len(contributions), tab, vendor_key,
        )
        return html, contributions

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def _ensure_seed(self, vendor_name: str, source: str = "manual") -> int:
        """Insert or get seed record. Returns seed_id."""
        row = self.conn.execute(
            "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ? AND seed_source = ?",
            (vendor_name, source),
        ).fetchone()
        if row:
            return row["seed_id"] if hasattr(row, "keys") else row[0]

        if self._is_postgres():
            row = self.conn.execute(
                """INSERT INTO openbook_vendor_seed (seed_text, seed_source)
                   VALUES (?, ?)
                   RETURNING seed_id""",
                (vendor_name, source),
            ).fetchone()
            self.conn.commit()
            return row["seed_id"] if hasattr(row, "keys") else row[0]

        cur = self.conn.execute(
            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
            (vendor_name, source),
        )
        self.conn.commit()
        return cur.lastrowid

    def _ensure_match(self, seed_id: int, match_info: Dict[str, Any]) -> int:
        """Insert or get vendor match record. Returns match_id.

        match_info may optionally include 'search_term' which is stored
        in the search_term_used column.
        """
        row = self.conn.execute(
            "SELECT match_id FROM openbook_vendor_match WHERE seed_id = ? AND openbook_vendor_key = ?",
            (seed_id, match_info["vendor_key"]),
        ).fetchone()
        if row:
            return row["match_id"] if hasattr(row, "keys") else row[0]

        search_term_used = match_info.get("search_term")

        if self._is_postgres():
            row = self.conn.execute(
                """INSERT INTO openbook_vendor_match
                   (seed_id, openbook_vendor_key, openbook_vendor_label, match_method, confidence, search_term_used)
                   VALUES (?, ?, ?, ?, ?, ?)
                   RETURNING match_id""",
                (
                    seed_id,
                    match_info["vendor_key"],
                    match_info["vendor_label"],
                    match_info["match_method"],
                    match_info["confidence"],
                    search_term_used,
                ),
            ).fetchone()
            self.conn.commit()
            return row["match_id"] if hasattr(row, "keys") else row[0]

        cur = self.conn.execute(
            """INSERT INTO openbook_vendor_match
               (seed_id, openbook_vendor_key, openbook_vendor_label, match_method, confidence, search_term_used)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                seed_id,
                match_info["vendor_key"],
                match_info["vendor_label"],
                match_info["match_method"],
                match_info["confidence"],
                search_term_used,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def _record_no_match(self, seed_id: int) -> None:
        """Record that a seed has no OpenBook match (sentinel row).

        Uses empty vendor key + match_method='no_match' so the seed is not
        re-resolved on subsequent runs.  The UNIQUE(seed_id, openbook_vendor_key)
        constraint handles idempotency.
        """
        # Check if already recorded
        row = self.conn.execute(
            "SELECT match_id FROM openbook_vendor_match WHERE seed_id = ? AND openbook_vendor_key = ?",
            (seed_id, ""),
        ).fetchone()
        if row:
            return

        self.conn.execute(
            """INSERT INTO openbook_vendor_match
               (seed_id, openbook_vendor_key, openbook_vendor_label, match_method, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            (seed_id, "", "", "no_match", 0.0),
        )
        self.conn.commit()

    def save_contracts(
        self, vendor_key: str, contracts: List[Dict[str, Any]], source_url: str
    ) -> Tuple[int, int]:
        """Persist contract rows with idempotent upserts.

        Returns (inserted, updated).
        """
        inserted = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()

        for c in contracts:
            existing = self.conn.execute(
                """SELECT id, row_hash FROM openbook_contracts_raw
                   WHERE openbook_vendor_key = ? AND contract_number = ? AND fiscal_year = ?""",
                (vendor_key, c["contract_number"], c["fiscal_year"]),
            ).fetchone()

            if existing:
                old_hash = existing["row_hash"] if hasattr(existing, "keys") else existing[1]
                if old_hash != c["row_hash"]:
                    eid = existing["id"] if hasattr(existing, "keys") else existing[0]
                    self.conn.execute(
                        """UPDATE openbook_contracts_raw
                           SET vendor_label=?, agency_code=?, agency_name=?,
                               award_amount=?, detail_url=?, last_seen_at=?, row_hash=?
                           WHERE id=?""",
                        (
                            c["vendor_label"], c["agency_code"], c["agency_name"],
                            c["award_amount"], c["detail_url"], now, c["row_hash"],
                            eid,
                        ),
                    )
                    updated += 1
                else:
                    # Touch last_seen_at
                    eid = existing["id"] if hasattr(existing, "keys") else existing[0]
                    self.conn.execute(
                        "UPDATE openbook_contracts_raw SET last_seen_at=? WHERE id=?",
                        (now, eid),
                    )
            else:
                self.conn.execute(
                    """INSERT INTO openbook_contracts_raw
                       (openbook_vendor_key, vendor_label, fiscal_year, agency_code,
                        agency_name, contract_number, award_amount, detail_url,
                        source_url, first_seen_at, last_seen_at, row_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        vendor_key, c["vendor_label"], c["fiscal_year"],
                        c["agency_code"], c["agency_name"], c["contract_number"],
                        c["award_amount"], c["detail_url"], source_url,
                        now, now, c["row_hash"],
                    ),
                )
                inserted += 1

        self.conn.commit()
        return inserted, updated

    def save_contributions(
        self, vendor_key: str, contributions: List[Dict[str, Any]], source_url: str
    ) -> Tuple[int, int]:
        """Persist contribution rows with idempotent upserts.

        Returns (inserted, updated).
        """
        inserted = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()

        for c in contributions:
            existing = self.conn.execute(
                "SELECT id FROM openbook_contributions_raw WHERE row_hash = ?",
                (c["row_hash"],),
            ).fetchone()

            if existing:
                eid = existing["id"] if hasattr(existing, "keys") else existing[0]
                self.conn.execute(
                    "UPDATE openbook_contributions_raw SET last_seen_at=? WHERE id=?",
                    (now, eid),
                )
                updated += 1
            else:
                self.conn.execute(
                    """INSERT INTO openbook_contributions_raw
                       (openbook_vendor_key, contributor_name, contributor_first_name,
                        recipient_name, employer, contribution_date, amount,
                        source_url, first_seen_at, last_seen_at, row_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        vendor_key, c["contributor_name"], c["contributor_first_name"],
                        c["recipient_name"], c["employer"], c["contribution_date"],
                        c["amount"], source_url, now, now, c["row_hash"],
                    ),
                )
                inserted += 1

        self.conn.commit()
        return inserted, updated

    def save_raw_extraction(
        self, source_type: str, source_identifier: str,
        payload: Any, source_url: str = "",
    ) -> None:
        """Save a raw extraction record for auditability (Postgres-safe)."""
        payload_json = json.dumps(payload, default=str) if not isinstance(payload, str) else payload
        try:
            # Check-then-insert pattern for Postgres compatibility
            # (avoids INSERT OR REPLACE which is SQLite-only)
            existing = self.conn.execute(
                "SELECT id FROM raw_extractions WHERE source_type = ? AND source_identifier = ?",
                (source_type, source_identifier),
            ).fetchone()
            if existing:
                eid = existing["id"] if hasattr(existing, "keys") else existing[0]
                self.conn.execute(
                    """UPDATE raw_extractions
                       SET source_url=?, parser_version=?, payload_json=?, updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (source_url, "openbook_v1", payload_json, eid),
                )
            else:
                self.conn.execute(
                    """INSERT INTO raw_extractions
                       (source_type, source_identifier, source_url, parser_version, payload_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source_type, source_identifier, source_url, "openbook_v1", payload_json),
                )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logger.warning(
                "Skipping raw extraction persistence for %s:%s due to DB error: %s",
                source_type,
                source_identifier,
                e,
            )

    def save_contract_warrants(
        self,
        vendor_key: str,
        contract_number: str,
        fiscal_year: Optional[int],
        detail_url: str,
        warrants: List[Dict[str, Any]],
        source_url: str,
    ) -> Tuple[int, int]:
        """Persist contract detail warrant rows with idempotent upserts."""
        inserted = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()

        for row in warrants:
            existing = self.conn.execute(
                "SELECT id FROM openbook_contract_warrants WHERE row_hash = ?",
                (row["row_hash"],),
            ).fetchone()
            if existing:
                existing_id = existing["id"] if hasattr(existing, "keys") else existing[0]
                self.conn.execute(
                    "UPDATE openbook_contract_warrants SET last_seen_at=? WHERE id=?",
                    (now, existing_id),
                )
                updated += 1
            else:
                self.conn.execute(
                    """INSERT INTO openbook_contract_warrants
                       (openbook_vendor_key, contract_number, fiscal_year,
                        issue_date, payment_amount, detail_url, source_url,
                        first_seen_at, last_seen_at, row_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        vendor_key,
                        contract_number,
                        fiscal_year,
                        row["issue_date"],
                        row["payment_amount"],
                        detail_url,
                        source_url,
                        now,
                        now,
                        row["row_hash"],
                    ),
                )
                inserted += 1

        self.conn.commit()
        return inserted, updated

    def upsert_contract_detail_status(
        self,
        vendor_key: str,
        contract_number: str,
        fiscal_year: Optional[int],
        detail_url: str,
        status: str,
        warrant_row_count: int = 0,
        error_message: Optional[str] = None,
    ) -> None:
        """Upsert detail scrape status with attempt accounting."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self.conn.execute(
            """SELECT id, attempt_count
               FROM openbook_contract_detail_status
               WHERE openbook_vendor_key = ? AND contract_number = ? AND fiscal_year = ?""",
            (vendor_key, contract_number, fiscal_year),
        ).fetchone()

        if existing:
            existing_id = existing["id"] if hasattr(existing, "keys") else existing[0]
            attempt_count = existing["attempt_count"] if hasattr(existing, "keys") else existing[1]
            self.conn.execute(
                """UPDATE openbook_contract_detail_status
                   SET detail_url=?, status=?, attempt_count=?, warrant_row_count=?,
                       last_error=?, last_attempted_at=?, updated_at=?
                   WHERE id=?""",
                (
                    detail_url,
                    status,
                    (attempt_count or 0) + 1,
                    warrant_row_count,
                    error_message,
                    now,
                    now,
                    existing_id,
                ),
            )
        else:
            self.conn.execute(
                """INSERT INTO openbook_contract_detail_status
                   (openbook_vendor_key, contract_number, fiscal_year, detail_url,
                    status, attempt_count, warrant_row_count, last_error,
                    last_attempted_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vendor_key,
                    contract_number,
                    fiscal_year,
                    detail_url,
                    status,
                    1,
                    warrant_row_count,
                    error_message,
                    now,
                    now,
                ),
            )
        self.conn.commit()

    def _detail_fetch_state(
        self,
        vendor_key: str,
        contract_number: str,
        fiscal_year: Optional[int],
        max_error_retries: int,
    ) -> str:
        """Return fetch state for a contract detail row.

        Values: fetch, skip_has_data, skip_no_data, skip_error_cap.
        """
        row = self.conn.execute(
            """SELECT status, attempt_count
               FROM openbook_contract_detail_status
               WHERE openbook_vendor_key = ? AND contract_number = ? AND fiscal_year = ?""",
            (vendor_key, contract_number, fiscal_year),
        ).fetchone()
        if not row:
            return "fetch"

        status = row["status"] if hasattr(row, "keys") else row[0]
        attempts = row["attempt_count"] if hasattr(row, "keys") else row[1]
        if status == "has_data":
            return "skip_has_data"
        if status == "no_data":
            return "skip_no_data"
        if status == "error" and (attempts or 0) >= max_error_retries:
            return "skip_error_cap"
        return "fetch"

    def enrich_contract_details_http(
        self,
        vendor_key: str,
        contracts: List[Dict[str, Any]],
        session: _HttpSession,
        run_id: int,
        max_error_retries: int = 2,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, int]:
        """Fetch and persist contract detail warrant rows for resolvable links."""
        stats = {
            "detail_attempted": 0,
            "detail_rows_inserted": 0,
            "detail_rows_updated": 0,
            "detail_no_data": 0,
            "detail_errors": 0,
            "detail_skipped_has_data": 0,
            "detail_skipped_no_data": 0,
            "detail_skipped_error_cap": 0,
        }

        for contract in contracts:
            detail_url = contract.get("detail_url")
            contract_number = contract.get("contract_number")
            fiscal_year = contract.get("fiscal_year")
            if not detail_url or not contract_number:
                continue

            state = self._detail_fetch_state(
                vendor_key,
                contract_number,
                fiscal_year,
                max_error_retries=max_error_retries,
            )
            if state != "fetch":
                stats[f"detail_{state}"] += 1
                continue

            stats["detail_attempted"] += 1
            if progress_callback:
                progress_callback(f"    detail: {contract_number}")

            html, _ = self.fetch_contract_detail_http(detail_url, session)
            if not html:
                self.upsert_contract_detail_status(
                    vendor_key,
                    contract_number,
                    fiscal_year,
                    detail_url,
                    status="error",
                    warrant_row_count=0,
                    error_message="empty_or_failed_response",
                )
                stats["detail_errors"] += 1
                continue

            warrants = parse_contract_detail_html(
                html,
                vendor_key,
                contract_number,
                fiscal_year,
            )
            if warrants:
                ins, upd = self.save_contract_warrants(
                    vendor_key,
                    contract_number,
                    fiscal_year,
                    detail_url,
                    warrants,
                    source_url=detail_url,
                )
                stats["detail_rows_inserted"] += ins
                stats["detail_rows_updated"] += upd
                self.upsert_contract_detail_status(
                    vendor_key,
                    contract_number,
                    fiscal_year,
                    detail_url,
                    status="has_data",
                    warrant_row_count=len(warrants),
                    error_message=None,
                )
            else:
                stats["detail_no_data"] += 1
                self.upsert_contract_detail_status(
                    vendor_key,
                    contract_number,
                    fiscal_year,
                    detail_url,
                    status="no_data",
                    warrant_row_count=0,
                    error_message=None,
                )

            self.save_raw_extraction(
                "openbook_contract_detail",
                f"{vendor_key}:detail:{contract_number}:{fiscal_year}:{run_id}",
                {
                    "vendor_key": vendor_key,
                    "contract_number": contract_number,
                    "fiscal_year": fiscal_year,
                    "detail_url": detail_url,
                    "warrant_count": len(warrants),
                    "warrants": warrants,
                },
                detail_url,
            )

        return stats

    async def _scrape_vendor_playwright(
        self,
        vendor_key: str,
        run_id: int,
        with_details: bool,
        max_detail_error_retries: int,
    ) -> Dict[str, Any]:
        """Scrape a resolved vendor using Playwright and persist results."""
        stats: Dict[str, Any] = {
            "contracts_inserted": 0,
            "contracts_updated": 0,
            "contributions_inserted": 0,
            "contributions_updated": 0,
            "detail_rows_inserted": 0,
            "detail_rows_updated": 0,
            "detail_no_data": 0,
            "detail_errors": 0,
            "contracts_count": 0,
            "contributions_count": 0,
            "error": None,
        }

        detail_session = _HttpSession() if with_details else None

        try:
            await self._init_browser()

            html, contracts = await self.search_contracts(vendor_key)
            stats["contracts_count"] = len(contracts)

            if contracts:
                ins, upd = self.save_contracts(vendor_key, contracts, SEARCH_URL)
                stats["contracts_inserted"] = ins
                stats["contracts_updated"] = upd

                self.save_raw_extraction(
                    "openbook_contracts_search",
                    f"{vendor_key}:contracts:{run_id}",
                    {
                        "vendor_key": vendor_key,
                        "contract_count": len(contracts),
                        "contracts": contracts,
                    },
                    SEARCH_URL,
                )

                if with_details and detail_session is not None:
                    detail_stats = self.enrich_contract_details_http(
                        vendor_key,
                        contracts,
                        detail_session,
                        run_id,
                        max_error_retries=max_detail_error_retries,
                    )
                    stats["detail_rows_inserted"] = detail_stats["detail_rows_inserted"]
                    stats["detail_rows_updated"] = detail_stats["detail_rows_updated"]
                    stats["detail_no_data"] = detail_stats["detail_no_data"]
                    stats["detail_errors"] = detail_stats["detail_errors"]

            _, _ = await self.search_contracts(vendor_key)
            _, contributions = await self.search_contributions(vendor_key, tab="contributions")
            if contributions:
                ins, upd = self.save_contributions(vendor_key, contributions, SEARCH_URL)
                stats["contributions_inserted"] += ins
                stats["contributions_updated"] += upd

                self.save_raw_extraction(
                    "openbook_contributions_tab",
                    f"{vendor_key}:contributions:{run_id}",
                    {
                        "vendor_key": vendor_key,
                        "contribution_count": len(contributions),
                        "contributions": contributions,
                    },
                    SEARCH_URL,
                )

            _, _ = await self.search_contracts(vendor_key)
            _, emp_contributions = await self.search_contributions(vendor_key, tab="employees")
            if emp_contributions:
                ins, upd = self.save_contributions(vendor_key, emp_contributions, SEARCH_URL)
                stats["contributions_inserted"] += ins
                stats["contributions_updated"] += upd

                self.save_raw_extraction(
                    "openbook_employees_tab",
                    f"{vendor_key}:employees:{run_id}",
                    {
                        "vendor_key": vendor_key,
                        "contribution_count": len(emp_contributions),
                        "contributions": emp_contributions,
                    },
                    SEARCH_URL,
                )

            stats["contributions_count"] = len(contributions) + len(emp_contributions)

        except Exception as e:
            logger.error("Playwright batch scrape failed for '%s': %s", vendor_key, e)
            stats["error"] = str(e)
        finally:
            await self._close_browser()

        return stats

    def create_run(self, mode: str = "vendor_poc") -> int:
        """Create a scrape run record. Returns run_id."""
        if self._is_postgres():
            row = self.conn.execute(
                "INSERT INTO openbook_scrape_runs (mode) VALUES (?) RETURNING run_id",
                (mode,),
            ).fetchone()
            self.conn.commit()
            return row["run_id"] if hasattr(row, "keys") else row[0]

        cur = self.conn.execute(
            "INSERT INTO openbook_scrape_runs (mode) VALUES (?)",
            (mode,),
        )
        self.conn.commit()
        return cur.lastrowid

    def complete_run(
        self, run_id: int, seed_count: int, match_count: int,
        contract_rows: int, contribution_rows: int, error_count: int,
        notes: str = "",
    ) -> None:
        """Mark a scrape run as completed."""
        self.conn.execute(
            """UPDATE openbook_scrape_runs
               SET completed_at=?, seed_count=?, match_count=?,
                   contract_rows=?, contribution_rows=?, error_count=?, notes=?
               WHERE run_id=?""",
            (
                datetime.now(timezone.utc).isoformat(), seed_count, match_count,
                contract_rows, contribution_rows, error_count, notes, run_id,
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Seed generation from existing DB tables
    # ------------------------------------------------------------------

    @staticmethod
    def generate_seeds(
        conn,
        min_amount: float = 10000.0,
        limit_per_source: int = 500,
        sources: str = "all",
    ) -> Dict[str, int]:
        """Populate openbook_vendor_seed from existing DB tables.

        Sources:
          - expenditures: d2_itemized_entries WHERE entry_type='expenditure'
          - lobbying: lobbying_entities
          - chicago: chicago_contracts_raw
          - fec: fec_schedule_b_disbursements
          - isbe: bulk_expenditures_clean (ISBE payees by total spend)

        Returns stats dict with counts per source and total_new_seeds.
        """
        stats: Dict[str, int] = {}
        run_sources = (
            ["expenditures", "lobbying", "chicago", "fec", "isbe"]
            if sources == "all"
            else [s.strip() for s in sources.split(",")]
        )

        for source in run_sources:
            count = 0

            if source == "expenditures":
                if not _table_exists(conn, "d2_itemized_entries"):
                    logger.info("Table d2_itemized_entries not found, skipping expenditures seeds")
                    stats["expenditures_seeds"] = 0
                    continue
                rows = conn.execute(
                    """SELECT UPPER(TRIM(vendor_name)) AS name, SUM(amount) AS total
                       FROM d2_itemized_entries
                       WHERE entry_type = 'expenditure'
                         AND vendor_name IS NOT NULL AND vendor_name != ''
                       GROUP BY UPPER(TRIM(vendor_name))
                       HAVING SUM(amount) >= ?
                       ORDER BY SUM(amount) DESC
                       LIMIT ?""",
                    (min_amount, limit_per_source),
                ).fetchall()
                for r in rows:
                    name = r["name"] if hasattr(r, "keys") else r[0]
                    if not name or len(name.strip()) < 3:
                        continue
                    existing = conn.execute(
                        "SELECT 1 FROM openbook_vendor_seed WHERE seed_text = ? AND seed_source = ?",
                        (name, "expenditures"),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
                            (name, "expenditures"),
                        )
                        count += 1
                conn.commit()
                stats["expenditures_seeds"] = count

            elif source == "lobbying":
                if not _table_exists(conn, "lobbying_entities"):
                    logger.info("Table lobbying_entities not found, skipping lobbying seeds")
                    stats["lobbying_seeds"] = 0
                    continue
                rows = conn.execute(
                    """SELECT DISTINCT UPPER(TRIM(entity_name)) AS name
                       FROM lobbying_entities
                       WHERE entity_name IS NOT NULL AND entity_name != ''
                       ORDER BY name
                       LIMIT ?""",
                    (limit_per_source,),
                ).fetchall()
                for r in rows:
                    name = r["name"] if hasattr(r, "keys") else r[0]
                    if not name or len(name.strip()) < 3:
                        continue
                    existing = conn.execute(
                        "SELECT 1 FROM openbook_vendor_seed WHERE seed_text = ? AND seed_source = ?",
                        (name, "lobbying"),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
                            (name, "lobbying"),
                        )
                        count += 1
                conn.commit()
                stats["lobbying_seeds"] = count

            elif source == "chicago":
                if not _table_exists(conn, "chicago_contracts_raw"):
                    logger.info("Table chicago_contracts_raw not found, skipping chicago seeds")
                    stats["chicago_seeds"] = 0
                    continue
                rows = conn.execute(
                    """SELECT UPPER(TRIM(vendor_name)) AS name, SUM(award_amount) AS total
                       FROM chicago_contracts_raw
                       WHERE vendor_name IS NOT NULL AND vendor_name != ''
                       GROUP BY UPPER(TRIM(vendor_name))
                       HAVING SUM(award_amount) >= ?
                       ORDER BY SUM(award_amount) DESC
                       LIMIT ?""",
                    (min_amount, limit_per_source),
                ).fetchall()
                for r in rows:
                    name = r["name"] if hasattr(r, "keys") else r[0]
                    if not name or len(name.strip()) < 3:
                        continue
                    existing = conn.execute(
                        "SELECT 1 FROM openbook_vendor_seed WHERE seed_text = ? AND seed_source = ?",
                        (name, "chicago"),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
                            (name, "chicago"),
                        )
                        count += 1
                conn.commit()
                stats["chicago_seeds"] = count

            elif source == "fec":
                if not _table_exists(conn, "fec_schedule_b_disbursements"):
                    logger.info("Table fec_schedule_b_disbursements not found, skipping fec seeds")
                    stats["fec_seeds"] = 0
                    continue
                rows = conn.execute(
                    """SELECT UPPER(TRIM(recipient_name)) AS name, SUM(disbursement_amount) AS total
                       FROM fec_schedule_b_disbursements
                       WHERE recipient_name IS NOT NULL AND recipient_name != ''
                       GROUP BY UPPER(TRIM(recipient_name))
                       HAVING SUM(disbursement_amount) >= ?
                       ORDER BY SUM(disbursement_amount) DESC
                       LIMIT ?""",
                    (min_amount, limit_per_source),
                ).fetchall()
                for r in rows:
                    name = r["name"] if hasattr(r, "keys") else r[0]
                    if not name or len(name.strip()) < 3:
                        continue
                    existing = conn.execute(
                        "SELECT 1 FROM openbook_vendor_seed WHERE seed_text = ? AND seed_source = ?",
                        (name, "fec"),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
                            (name, "fec"),
                        )
                        count += 1
                conn.commit()
                stats["fec_seeds"] = count

            elif source == "isbe":
                if not _table_exists(conn, "bulk_expenditures_clean"):
                    logger.info("Table bulk_expenditures_clean not found, skipping isbe seeds")
                    stats["isbe_seeds"] = 0
                    continue
                import config as _cfg
                isbe_limit = getattr(_cfg, "OPENBOOK_ISBE_SEED_LIMIT", 5000)
                rows = conn.execute(
                    """SELECT UPPER(TRIM(payee_last_or_business_name)) AS name,
                              SUM(amount) AS total
                       FROM bulk_expenditures_clean
                       WHERE payee_last_or_business_name IS NOT NULL
                         AND payee_last_or_business_name != ''
                         AND (is_amount_anomalous = 0 OR is_amount_anomalous IS NULL)
                       GROUP BY UPPER(TRIM(payee_last_or_business_name))
                       HAVING SUM(amount) >= ?
                       ORDER BY SUM(amount) DESC
                       LIMIT ?""",
                    (min_amount, isbe_limit),
                ).fetchall()
                for r in rows:
                    name = r["name"] if hasattr(r, "keys") else r[0]
                    if not name or len(name.strip()) < 3:
                        continue
                    if _is_person_name(name):
                        continue
                    existing = conn.execute(
                        "SELECT 1 FROM openbook_vendor_seed WHERE seed_text = ? AND seed_source = ?",
                        (name, "isbe"),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
                            (name, "isbe"),
                        )
                        count += 1
                conn.commit()
                stats["isbe_seeds"] = count

        stats["total_new_seeds"] = sum(
            v for k, v in stats.items() if k.endswith("_seeds")
        )
        return stats

    # ------------------------------------------------------------------
    # Batch import orchestrator (HTTP-based)
    # ------------------------------------------------------------------

    def import_batch(
        self,
        max_vendors: Optional[int] = None,
        pick_first: bool = False,
        use_playwright: bool = True,
        with_details: bool = True,
        max_detail_error_retries: int = 2,
        max_consecutive_errors: int = 10,
        use_smart_search: bool = True,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Batch-resolve and scrape all pending seeds.

        Phase 1: Resolve unmatched seeds to OpenBook vendor keys.
            When use_smart_search=True, generates smart search terms and
            stores ALL matching vendors per seed.
        Phase 2: Scrape contracts + contributions for resolved-but-unscraped vendors
        via Playwright (default) or HTTP fallback.

        Returns summary dict with counts.
        """
        run_id = self.create_run("targeted_batch")
        session = _HttpSession()

        total_resolved = 0
        total_no_match = 0
        total_contracts_ins = 0
        total_contracts_upd = 0
        total_contributions_ins = 0
        total_contributions_upd = 0
        total_detail_rows_ins = 0
        total_detail_rows_upd = 0
        total_detail_no_data = 0
        total_detail_errors = 0
        total_errors = 0
        consecutive_errors = 0
        vendors_processed = 0

        # ------------------------------------------------------------------
        # Phase 1: Resolve unmatched seeds
        # ------------------------------------------------------------------
        unresolved = self.conn.execute(
            """SELECT s.seed_id, s.seed_text, s.seed_source
               FROM openbook_vendor_seed s
               LEFT JOIN openbook_vendor_match m ON s.seed_id = m.seed_id
               WHERE m.match_id IS NULL
               ORDER BY s.seed_id""",
        ).fetchall()

        total_to_resolve = len(unresolved)
        if max_vendors is not None:
            unresolved = unresolved[:max_vendors]

        for idx, row in enumerate(unresolved, 1):
            seed_id = row["seed_id"] if hasattr(row, "keys") else row[0]
            seed_text = row["seed_text"] if hasattr(row, "keys") else row[1]

            if progress_callback:
                progress_callback(f"[{idx}/{len(unresolved)}] Resolving: {seed_text}")

            try:
                if use_smart_search:
                    # Smart search: generate terms, resolve all matches
                    matches = self.resolve_all_matches_http(seed_text, session)
                    if matches:
                        for m in matches:
                            self._ensure_match(seed_id, m)
                        total_resolved += 1
                        search_terms = list({m["search_term"] for m in matches})
                        logger.info(
                            "Resolved '%s' -> %d matches (searched: %s)",
                            seed_text, len(matches), ", ".join(search_terms),
                        )
                        if progress_callback:
                            progress_callback(
                                f"  -> {len(matches)} matches (searched: {', '.join(search_terms)})"
                            )
                    else:
                        self._record_no_match(seed_id)
                        total_no_match += 1
                        logger.info("No match for '%s'", seed_text)
                else:
                    # Legacy single-match mode
                    match_info = self.resolve_vendor_http(seed_text, session, pick_first=pick_first)
                    if match_info:
                        self._ensure_match(seed_id, match_info)
                        total_resolved += 1
                        logger.info(
                            "Resolved '%s' -> '%s' (%s, %.3f)",
                            seed_text, match_info["vendor_key"],
                            match_info["match_method"], match_info["confidence"],
                        )
                    else:
                        self._record_no_match(seed_id)
                        total_no_match += 1
                        logger.info("No match for '%s'", seed_text)
                consecutive_errors = 0
            except Exception as e:
                total_errors += 1
                consecutive_errors += 1
                logger.error("Error resolving '%s': %s", seed_text, e)
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "Stopping: %d consecutive errors reached limit",
                        consecutive_errors,
                    )
                    break

        # ------------------------------------------------------------------
        # Phase 2: Scrape resolved-but-unscraped vendor keys
        # ------------------------------------------------------------------
        unscraped = self.conn.execute(
            """SELECT
                m.openbook_vendor_key,
                m.openbook_vendor_label,
                MIN(m.seed_id) AS seed_id,
                MIN(m.match_id) AS first_match_id
               FROM openbook_vendor_match m
               LEFT JOIN openbook_contracts_raw c
               ON m.openbook_vendor_key = c.openbook_vendor_key
               WHERE m.match_method != 'no_match'
             AND m.openbook_vendor_key != ''
             AND c.id IS NULL
               GROUP BY m.openbook_vendor_key, m.openbook_vendor_label
               ORDER BY MIN(m.match_id)""",
        ).fetchall()

        if max_vendors is not None:
            remaining = max(0, max_vendors - len(unresolved))
            unscraped = unscraped[:remaining] if remaining > 0 else unscraped[:max_vendors]

        consecutive_errors = 0

        for idx, row in enumerate(unscraped, 1):
            vendor_key = row["openbook_vendor_key"] if hasattr(row, "keys") else row[0]
            vendor_label = row["openbook_vendor_label"] if hasattr(row, "keys") else row[1]

            if progress_callback:
                progress_callback(f"[{idx}/{len(unscraped)}] Scraping: {vendor_label}")

            try:
                contribution_count = 0
                if use_playwright:
                    scrape_stats = asyncio.run(
                        self._scrape_vendor_playwright(
                            vendor_key,
                            run_id,
                            with_details=with_details,
                            max_detail_error_retries=max_detail_error_retries,
                        )
                    )
                    contracts = [None] * int(scrape_stats.get("contracts_count", 0) or 0)
                    contribution_count = int(scrape_stats.get("contributions_count", 0) or 0)

                    total_contracts_ins += int(scrape_stats.get("contracts_inserted", 0) or 0)
                    total_contracts_upd += int(scrape_stats.get("contracts_updated", 0) or 0)
                    total_contributions_ins += int(scrape_stats.get("contributions_inserted", 0) or 0)
                    total_contributions_upd += int(scrape_stats.get("contributions_updated", 0) or 0)
                    total_detail_rows_ins += int(scrape_stats.get("detail_rows_inserted", 0) or 0)
                    total_detail_rows_upd += int(scrape_stats.get("detail_rows_updated", 0) or 0)
                    total_detail_no_data += int(scrape_stats.get("detail_no_data", 0) or 0)
                    total_detail_errors += int(scrape_stats.get("detail_errors", 0) or 0)

                    if scrape_stats.get("error"):
                        total_errors += 1
                        consecutive_errors += 1
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error(
                                "Stopping: %d consecutive errors reached limit",
                                consecutive_errors,
                            )
                            break
                    else:
                        consecutive_errors = 0
                else:
                    html, contracts = self.search_contracts_http(vendor_key, session)

                    if not html or len(html) < 100:
                        logger.warning("Empty response for '%s', refreshing session...", vendor_key)
                        try:
                            session.get(INDEX_URL)
                        except Exception:
                            pass
                        html, contracts = self.search_contracts_http(vendor_key, session)

                    if contracts:
                        ins, upd = self.save_contracts(vendor_key, contracts, SEARCH_URL)
                        total_contracts_ins += ins
                        total_contracts_upd += upd

                        self.save_raw_extraction(
                            "openbook_contracts_search",
                            f"{vendor_key}:contracts:{run_id}",
                            {"vendor_key": vendor_key, "contract_count": len(contracts),
                             "contracts": contracts},
                            SEARCH_URL,
                        )

                        if with_details:
                            detail_stats = self.enrich_contract_details_http(
                                vendor_key,
                                contracts,
                                session,
                                run_id,
                                max_error_retries=max_detail_error_retries,
                                progress_callback=progress_callback,
                            )
                            total_detail_rows_ins += detail_stats["detail_rows_inserted"]
                            total_detail_rows_upd += detail_stats["detail_rows_updated"]
                            total_detail_no_data += detail_stats["detail_no_data"]
                            total_detail_errors += detail_stats["detail_errors"]

                    _, contributions = self.search_contributions_http(
                        vendor_key, session, tab="contributions"
                    )
                    if contributions:
                        ins, upd = self.save_contributions(vendor_key, contributions, SEARCH_URL)
                        total_contributions_ins += ins
                        total_contributions_upd += upd

                        self.save_raw_extraction(
                            "openbook_contributions_tab",
                            f"{vendor_key}:contributions:{run_id}",
                            {"vendor_key": vendor_key, "contribution_count": len(contributions),
                             "contributions": contributions},
                            SEARCH_URL,
                        )

                    _, emp_contributions = self.search_contributions_http(
                        vendor_key, session, tab="employees"
                    )
                    if emp_contributions:
                        ins, upd = self.save_contributions(vendor_key, emp_contributions, SEARCH_URL)
                        total_contributions_ins += ins
                        total_contributions_upd += upd

                        self.save_raw_extraction(
                            "openbook_employees_tab",
                            f"{vendor_key}:employees:{run_id}",
                            {"vendor_key": vendor_key, "contribution_count": len(emp_contributions),
                             "contributions": emp_contributions},
                            SEARCH_URL,
                        )
                    contribution_count = len(contributions) + len(emp_contributions)

                vendors_processed += 1
                if not use_playwright:
                    consecutive_errors = 0

                if progress_callback:
                    progress_callback(
                        f"  -> {len(contracts)} contracts, "
                        f"{contribution_count} contributions"
                    )

            except Exception as e:
                total_errors += 1
                consecutive_errors += 1
                logger.error("Error scraping '%s': %s", vendor_key, e)
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "Stopping: %d consecutive errors reached limit",
                        consecutive_errors,
                    )
                    break

        # Complete the run
        self.complete_run(
            run_id,
            seed_count=total_to_resolve,
            match_count=total_resolved,
            contract_rows=total_contracts_ins + total_contracts_upd,
            contribution_rows=total_contributions_ins + total_contributions_upd,
            error_count=total_errors,
            notes=f"batch: {vendors_processed} vendors scraped, "
                  f"{total_resolved} resolved, {total_no_match} no-match",
        )

        return {
            "seeds_checked": total_to_resolve,
            "resolved": total_resolved,
            "no_match": total_no_match,
            "vendors_scraped": vendors_processed,
            "contracts_inserted": total_contracts_ins,
            "contracts_updated": total_contracts_upd,
            "contributions_inserted": total_contributions_ins,
            "contributions_updated": total_contributions_upd,
            "detail_rows_inserted": total_detail_rows_ins,
            "detail_rows_updated": total_detail_rows_upd,
            "detail_no_data": total_detail_no_data,
            "detail_errors": total_detail_errors,
            "errors": total_errors,
            "run_id": run_id,
        }

    # ------------------------------------------------------------------
    # End-to-end single-vendor import (Playwright, legacy POC)
    # ------------------------------------------------------------------

    async def import_vendor(
        self,
        vendor_name: str,
        max_contract_pages: int = 5,
        max_contribution_pages: int = 5,
        pick_first: bool = False,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """Full pipeline: resolve vendor, scrape contracts + contributions, persist.

        Returns a summary dict.
        """
        summary = {
            "vendor_name": vendor_name,
            "vendor_key": None,
            "vendor_label": None,
            "match_method": None,
            "confidence": None,
            "contracts_inserted": 0,
            "contracts_updated": 0,
            "contributions_inserted": 0,
            "contributions_updated": 0,
            "pages_visited": 0,
            "errors": [],
        }

        run_id = self.create_run("vendor_poc")

        try:
            await self._init_browser()

            # Step 1: Resolve vendor
            if progress_callback:
                progress_callback("Resolving vendor name...")

            match_info = await self.resolve_vendor(vendor_name, pick_first=pick_first)
            if not match_info:
                summary["errors"].append(f"No OpenBook vendor match for '{vendor_name}'")
                self.complete_run(run_id, 1, 0, 0, 0, 1, "No vendor match")
                return summary

            vendor_key = match_info["vendor_key"]
            summary["vendor_key"] = vendor_key
            summary["vendor_label"] = match_info["vendor_label"]
            summary["match_method"] = match_info["match_method"]
            summary["confidence"] = match_info["confidence"]

            # Persist seed and match
            seed_id = self._ensure_seed(vendor_name, "manual")
            self._ensure_match(seed_id, match_info)
            summary["pages_visited"] += 1

            # Step 2: Search contracts
            if progress_callback:
                progress_callback("Searching contracts...")

            html, contracts = await self.search_contracts(
                vendor_key, max_pages=max_contract_pages
            )
            summary["pages_visited"] += 1

            if contracts:
                ins, upd = self.save_contracts(vendor_key, contracts, SEARCH_URL)
                summary["contracts_inserted"] = ins
                summary["contracts_updated"] = upd

                # Save raw extraction
                self.save_raw_extraction(
                    "openbook_contracts_search",
                    f"{vendor_key}:contracts:{run_id}",
                    {"vendor_key": vendor_key, "contract_count": len(contracts),
                     "contracts": contracts},
                    SEARCH_URL,
                )

            # Step 3: Check contributions tab
            if progress_callback:
                progress_callback("Checking contributions tab...")

            # We need to go back to contracts first, then switch to contributions
            # Re-search to get back to the results page
            _, _ = await self.search_contracts(vendor_key)
            summary["pages_visited"] += 1

            contrib_html, contributions = await self.search_contributions(
                vendor_key, tab="contributions"
            )
            summary["pages_visited"] += 1

            if contributions:
                ins, upd = self.save_contributions(
                    vendor_key, contributions, SEARCH_URL
                )
                summary["contributions_inserted"] += ins
                summary["contributions_updated"] += upd

                self.save_raw_extraction(
                    "openbook_contributions_tab",
                    f"{vendor_key}:contributions:{run_id}",
                    {"vendor_key": vendor_key, "contribution_count": len(contributions),
                     "contributions": contributions},
                    SEARCH_URL,
                )

            # Step 4: Check employees tab
            if progress_callback:
                progress_callback("Checking employees tab...")

            # Re-search again to get back to results page
            _, _ = await self.search_contracts(vendor_key)
            summary["pages_visited"] += 1

            emp_html, emp_contributions = await self.search_contributions(
                vendor_key, tab="employees"
            )
            summary["pages_visited"] += 1

            if emp_contributions:
                ins, upd = self.save_contributions(
                    vendor_key, emp_contributions, SEARCH_URL
                )
                summary["contributions_inserted"] += ins
                summary["contributions_updated"] += upd

                self.save_raw_extraction(
                    "openbook_employees_tab",
                    f"{vendor_key}:employees:{run_id}",
                    {"vendor_key": vendor_key, "contribution_count": len(emp_contributions),
                     "contributions": emp_contributions},
                    SEARCH_URL,
                )

        except Exception as e:
            logger.error("Import failed for '%s': %s", vendor_name, e)
            summary["errors"].append(str(e))
        finally:
            await self._close_browser()

        # Complete run
        total_contracts = summary["contracts_inserted"] + summary["contracts_updated"]
        total_contributions = summary["contributions_inserted"] + summary["contributions_updated"]
        self.complete_run(
            run_id,
            seed_count=1,
            match_count=1 if summary["vendor_key"] else 0,
            contract_rows=total_contracts,
            contribution_rows=total_contributions,
            error_count=len(summary["errors"]),
            notes=f"POC import for '{vendor_name}'",
        )

        return summary
