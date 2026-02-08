"""Helpers for generating stable source identifiers."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


DEFAULT_BASE_URL = "https://www.elections.il.gov/CampaignDisclosure/"


def normalize_source_url(url: str | None, base_url: str = DEFAULT_BASE_URL) -> str | None:
    """Return a canonical absolute URL for use as an identifier key."""
    if not url:
        return None

    absolute_url = url if url.startswith("http") else urljoin(base_url, url.lstrip("/"))
    parsed = urlparse(absolute_url)

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query_items.sort()

    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(query_items, doseq=True),
        fragment="",
    )
    return urlunparse(cleaned)


def make_source_identifier(url: str | None = None, *parts: str | int | None) -> str:
    """Build a stable identifier from URL or fallback parts."""
    normalized = normalize_source_url(url)
    if normalized:
        return f"url:{normalized}"

    payload = "||".join("" if part is None else str(part).strip() for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"hash:{digest}"
