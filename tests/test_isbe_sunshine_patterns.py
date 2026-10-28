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


def test_condensed_expenditures_has_part9_partial_index():
    # Candidate outside-spending pages (plan outside-spending-drilldown D6) read the
    # Part-9 rows of the matview directly: 19K rows of 3.5M. Without the partial index
    # every candidate page is a 170-185 ms parallel seq scan (crawler-exposed, cf. A15/A16).
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert (
        "CREATE INDEX idx_isbe_condensed_expenditures_part9_date ON isbe_condensed_expenditures "
        "(expended_date) WHERE d2_part LIKE '9%' AND archived = FALSE;"
    ) in source
    # database/pg_compat.py rewrites upper-case LIKE to ILIKE (SQLite semantics); an
    # ILIKE predicate cannot use the partial index, so the module spells it lower-case.
    module = Path("database/outside_spending.py").read_text(encoding="utf-8")
    assert "e.d2_part like '9%' AND e.archived = FALSE" in module
    assert "e.d2_part LIKE '9%'" not in module
    # The state race builder reads the same Part-9 rows through the same index (database/analytics.py): its
    # predicate repeats the index's, and the name prefilter is lower-case for the same reason. The upper-case
    # LIKEs of the compat-view fallback just below it are deliberate (that view has no such index), so the
    # absence of upper-case LIKE is NOT asserted here.
    analytics = Path("database/analytics.py").read_text(encoding="utf-8")
    assert "e.d2_part like '9%' AND e.archived = FALSE" in analytics
    assert "{STRIPPED_NAME_SQL} like ANY(?)" in analytics


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


def test_condensed_receipts_has_normalized_donor_name_index():
    # /lobbying/<id> and /lobbying/client/<id> (_get_receipt_activity) look up receipts by the
    # normalized donor name through the compat view; without this expression index every
    # page is a 1.3 s parallel seq scan over 4M rows (load-time plan L3 / P4, 2026-10-28).
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert (
        "CREATE INDEX idx_isbe_condensed_receipts_donor_name_norm ON isbe_condensed_receipts "
        "(LOWER(TRIM(COALESCE(NULLIF(TRIM(first_name), ''), '') || CASE WHEN COALESCE(NULLIF(TRIM(first_name), ''), '') != '' "
        "AND COALESCE(NULLIF(TRIM(last_name), ''), '') != '' THEN ' ' ELSE '' END || COALESCE(NULLIF(TRIM(last_name), ''), ''))));"
    ) in source


def test_etl_analyzes_rebuilt_relations_and_indexes_filed_doc_received_datetime():
    # Load-time plan L12: nothing in the refresh chain ran ANALYZE after the drop-and-reload,
    # so the first requests after each weekly import planned on empty statistics
    # (prod incident 2026-10-22). The freshness gate's MAX(received_datetime) probe on
    # isbe_filed_docs was a 957K-row seq scan for want of an index.
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert "ANALYZE isbe_condensed_receipts, isbe_condensed_expenditures, isbe_most_recent_filings" in source
    assert (
        "CREATE INDEX idx_isbe_filed_docs_received_datetime ON isbe_filed_docs (received_datetime);"
    ) in source


def test_filed_doc_pickers_order_nulls_last():
    # DISTINCT ON ... ORDER BY received_datetime DESC puts NULL-dated A-1 docs
    # first, so they win over the real quarterly and its receipts vanish from
    # the condensed matviews (open bug since 2026-06-10). NULLS LAST on every
    # filed-doc picker.
    source = Path("scripts/isbe_sunshine_etl.py").read_text(encoding="utf-8")
    assert source.count("received_datetime DESC NULLS LAST") == 3
    assert "received_datetime DESC\n" not in source
