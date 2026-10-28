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
    # /committees/filing/<id> filters both condensed views by filed_doc_id; without
    # this index every filing page ran two parallel seq scans over 3.6M expenditure
    # rows (prod load 9-10 on 8 cores, 2026-10-28; 1.2 s -> 0.06 ms locally).
    assert (
        "CREATE INDEX idx_isbe_condensed_expenditures_filed_doc_id "
        "ON isbe_condensed_expenditures (filed_doc_id);"
    ) in source


def test_condensed_receipts_has_md5_donor_key_index_and_models_use_it():
    # /donors/key/<key> matches receipts on the computed donor-key expression
    # (database/models.py Donor._bulk_donor_key_sql); without an index that is a
    # parallel seq scan over 4M condensed receipts, three times per donor page
    # (2.2 s each locally, prod 3.1 s/page, 1.1M crawlable pages, 2026-10-28).
    # A plain expression index cannot be built (4 rows exceed the btree row
    # limit), so the index is on md5(expr) and the lookups compare md5() on
    # both sides; the planner matches it through the bulk_receipts_clean view.
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert (
        "CREATE INDEX idx_isbe_condensed_receipts_donor_key_md5 ON isbe_condensed_receipts "
        "(md5(LOWER(TRIM(COALESCE(first_name,'') || '|' || COALESCE(last_name,'') || '|' || "
        "COALESCE(address1,'') || '|' || COALESCE(address2,'') || '|' || COALESCE(city,'') || '|' || "
        "COALESCE(state,'') || '|' || COALESCE(zipcode,'')))));"
    ) in source
    models = Path("database/models.py").read_text(encoding="utf-8")
    assert models.count('f"md5({donor_key_expr}) = md5(?)"') == 3
    assert 'f"{donor_key_expr} = ?"' not in models


def test_filed_doc_pickers_order_nulls_last():
    # DISTINCT ON ... ORDER BY received_datetime DESC puts NULL-dated A-1 docs
    # first, so they win over the real quarterly and its receipts vanish from
    # the condensed matviews (open bug since 2026-06-10). NULLS LAST on every
    # filed-doc picker.
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert source.count("received_datetime DESC NULLS LAST") == 3
    assert "received_datetime DESC\n" not in source
