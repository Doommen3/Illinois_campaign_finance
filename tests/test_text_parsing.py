"""Tests for text parsing helpers used by scrapers and cleanup logic."""

from scraper.text_parsing import (
    is_garbage_committee_name,
    parse_contributor_metadata,
    parse_amount_and_date,
)


def test_detects_garbage_committee_names():
    assert is_garbage_committee_name('. 11 12 13 14 15 16 17 18 19 20 ...')
    assert is_garbage_committee_name('…')
    assert is_garbage_committee_name('1')


def test_allows_normal_committee_names():
    assert not is_garbage_committee_name('Citizens for Jane Doe')
    assert not is_garbage_committee_name('Friends of Ward 1')


def test_parse_contributor_metadata_full_string():
    name, occupation, employer = parse_contributor_metadata(
        'Bishop, Elizabeth Occupation: Politics Employer: City of LaSalle'
    )
    assert name == 'Bishop, Elizabeth'
    assert occupation == 'Politics'
    assert employer == 'City of LaSalle'


def test_parse_contributor_metadata_partial_fields():
    name, occupation, employer = parse_contributor_metadata('Jane Doe Employer: Acme Inc')
    assert name == 'Jane Doe'
    assert occupation is None
    assert employer == 'Acme Inc'

    name2, occupation2, employer2 = parse_contributor_metadata('John Smith Occupation: Attorney')
    assert name2 == 'John Smith'
    assert occupation2 == 'Attorney'
    assert employer2 is None


def test_parse_amount_and_date_extracts_both_values():
    amount, tx_date = parse_amount_and_date('$32,500.00 2/6/2026')
    assert amount == 32500.0
    assert tx_date == '2026-02-06'


def test_parse_amount_and_date_negative_and_missing_date():
    amount, tx_date = parse_amount_and_date('($500.00)')
    assert amount == -500.0
    assert tx_date is None
