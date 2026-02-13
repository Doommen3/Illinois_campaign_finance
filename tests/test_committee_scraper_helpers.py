"""Unit tests for committee scraper helper functions."""

from scraper.committee_scraper import (
    _parse_date,
    _classify_itemized_type,
    _map_itemized_columns,
    _parse_amount,
    _entry_type_from_headers,
)


def test_parse_date_supports_iso_and_us_formats():
    assert str(_parse_date("2025-06-01")) == "2025-06-01"
    assert str(_parse_date("06/01/2025")) == "2025-06-01"
    assert str(_parse_date("02/13/2026 1:35 PM Filed electronically")) == "2026-02-13"
    assert str(_parse_date("Filed: 02/13/2026\nElectronic filing")) == "2026-02-13"
    assert _parse_date("invalid") is None


def test_classify_itemized_type_from_label_or_href():
    assert _classify_itemized_type("a. Itemized Contributions", None) == "contribution"
    assert _classify_itemized_type("a. Itemized", "https://x/ItemizedExpenditures.aspx") == "expenditure"
    assert _classify_itemized_type("Unknown", "https://x/no-match") == "other"


def test_map_itemized_columns_detects_both_layouts():
    contrib_headers = [
        "contributed by",
        "address",
        "amount",
        "description",
        "vendor name",
        "vendor address",
    ]
    contrib_map = _map_itemized_columns(contrib_headers)
    assert contrib_map["contributed_by"] == 0
    assert contrib_map["amount"] == 2
    assert contrib_map["vendor_name"] == 4

    exp_headers = [
        "received by",
        "address",
        "amount",
        "expended by",
        "purpose/beneficiary",
        "candidate name",
        "office - district",
        "supporting/opposing",
    ]
    exp_map = _map_itemized_columns(exp_headers)
    assert exp_map["received_by"] == 0
    assert exp_map["expended_by"] == 3
    assert exp_map["purpose_beneficiary"] == 4


def test_entry_type_from_headers():
    assert _entry_type_from_headers(["contributed by", "amount"]) == "contribution"
    assert _entry_type_from_headers(["received by", "expended by"]) == "expenditure"
    assert _entry_type_from_headers(["unknown header"]) is None


def test_parse_amount_parses_negative_and_plain_values():
    assert _parse_amount("$1,234.56") == 1234.56
    assert _parse_amount("($500.00)") == -500.0
    assert _parse_amount("invalid") is None
