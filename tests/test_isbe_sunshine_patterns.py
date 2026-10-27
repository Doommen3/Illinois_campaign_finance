"""Guards for high-value Sunshine view/index integration."""
from pathlib import Path


def test_compat_views_use_condensed_receipts_and_expenditures():
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert "FROM isbe_condensed_receipts r;" in source
    assert "FROM isbe_condensed_expenditures e;" in source


def test_matviews_include_core_patterns():
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert "CREATE MATERIALIZED VIEW isbe_most_recent_filings AS" in source
    assert "CREATE MATERIALIZED VIEW isbe_condensed_receipts AS" in source
    assert "CREATE MATERIALIZED VIEW isbe_condensed_expenditures AS" in source
    assert "CREATE MATERIALIZED VIEW isbe_candidate_money AS" in source


def test_schema_has_join_date_doc_indexes():
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert "CREATE INDEX idx_isbe_receipts_committee_date_doc" in source
    assert "CREATE INDEX idx_isbe_expenditures_committee_date_doc" in source
    assert "CREATE INDEX idx_isbe_d2_reports_filed_doc_committee" in source
    assert "CREATE INDEX idx_isbe_cc_candidate_committee" in source


def test_condensed_expenditures_has_committee_date_index():
    # Top Payees / candidate-profile "last activity" look up the expenditures
    # matview by committee; without this index every profile seq-scans 3M rows.
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert (
        "CREATE INDEX idx_isbe_condensed_expenditures_committee_date "
        "ON isbe_condensed_expenditures (committee_id, expended_date);"
    ) in source


def test_filed_doc_pickers_order_nulls_last():
    # DISTINCT ON ... ORDER BY received_datetime DESC puts NULL-dated A-1 docs
    # first, so they win over the real quarterly and its receipts vanish from
    # the condensed matviews (open bug since 2026-06-10). NULLS LAST on every
    # filed-doc picker.
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert source.count("received_datetime DESC NULLS LAST") == 3
    assert "received_datetime DESC\n" not in source
