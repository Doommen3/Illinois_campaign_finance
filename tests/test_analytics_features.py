"""Tests for analytics features (network, anomalies, concentration, trends, geo, NLP)."""
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

import pytest

from database.analytics import (
    build_dashboard_full_snapshot,
    get_candidate_competition_networks,
    get_analytics_data_sources,
    get_anomaly_flags,
    get_committee_similarity_network,
    get_dashboard_snapshot,
    get_donor_cogiving_network,
    get_donor_concentration,
    get_geo_drilldown,
    get_geo_summary,
    get_irs527_ecosystem_graph,
    get_lobbying_influence_graph,
    get_network_graph,
    get_nlp_spending_summary,
    get_reconciliation_outliers,
    get_time_series,
    get_vendor_expenditure_network,
    refresh_analytics_materialized,
    save_dashboard_snapshot,
)
from database.connection import get_db, init_db
from database.models import Committee, Contribution, Donor, Report
from webapp.app import create_app


def _seed_analytics_dataset(conn):
    committee_one = Committee.get_or_create(conn, "Committee One")
    committee_two = Committee.get_or_create(conn, "Committee Two")

    donor_a = Donor.get_or_create(
        conn,
        "Alice Donor",
        "101 Main St, Chicago, IL 60601",
        "alice donor",
        "101 main st, chicago, il 60601",
    )
    donor_b = Donor.get_or_create(
        conn,
        "Bob Donor",
        "202 Oak St, Springfield, IL 62701",
        "bob donor",
        "202 oak st, springfield, il 62701",
    )
    donor_c = Donor.get_or_create(
        conn,
        "Carol Donor",
        "303 Pine St, Naperville, IL 60540",
        "carol donor",
        "303 pine st, naperville, il 60540",
    )
    donor_d = Donor.get_or_create(
        conn,
        "Dave Donor",
        "404 Cedar St, St. Louis, MO 63101",
        "dave donor",
        "404 cedar st, st. louis, mo 63101",
    )
    donor_e = Donor.get_or_create(
        conn,
        "Eve Donor",
        "505 Lake St, CHICAGO, IL 60602",
        "eve donor",
        "505 lake st, chicago, il 60602",
    )

    def create_report(committee_id: int, filed_date: str, period: str):
        return Report(
            committee_id=committee_id,
            report_type="A-1 ($1000+ Year Round)",
            reporting_period=period,
            filed_date=filed_date,
            pages=1,
            detail_url=f"https://example.com/report/{committee_id}/{filed_date}",
            scrape_status="scraped",
        ).save(conn)

    jan_one = create_report(committee_one.id, "01/15/2026", "Jan 2026")
    feb_one = create_report(committee_one.id, "02/15/2026", "Feb 2026")
    mar_one = create_report(committee_one.id, "03/15/2026", "Mar 2026")
    apr_one = create_report(committee_one.id, "04/15/2026", "Apr 2026")
    jan_two = create_report(committee_two.id, "01/20/2026", "Jan 2026")
    feb_two = create_report(committee_two.id, "02/20/2026", "Feb 2026")

    contribution_rows = [
        (jan_one.id, donor_a.id, 1000.0, "01/10/2026", "Digital advertising on social media"),
        (jan_one.id, donor_b.id, 500.0, "01/11/2026", "Consulting strategy services"),
        (jan_one.id, donor_e.id, 250.0, "01/12/2026", "Community outreach"),
        (feb_one.id, donor_a.id, 800.0, "02/10/2026", "Legal compliance review"),
        (feb_one.id, donor_c.id, 400.0, "02/12/2026", "Printing and postage"),
        (mar_one.id, donor_a.id, 900.0, "03/10/2026", "Payroll for campaign staff"),
        (mar_one.id, donor_b.id, 300.0, "03/11/2026", "Office supplies"),
        (apr_one.id, donor_a.id, 10000.0, "04/09/2026", "Television media blitz"),
        (jan_two.id, donor_d.id, 2000.0, "01/18/2026", "Travel and event venue"),
        (feb_two.id, donor_d.id, 1800.0, "02/18/2026", "Fundraising platform fees"),
    ]

    for report_id, donor_id, amount, transaction_date, description in contribution_rows:
        Contribution(
            report_id=report_id,
            donor_id=donor_id,
            amount=amount,
            transaction_date=transaction_date,
            description=description,
            raw_contributed_by="raw",
            raw_address="raw address",
        ).save(conn)

    conn.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg")
    conn.execute(
        """
        CREATE TABLE bulk_candidate_committee_finance_agg (
            candidate_id INTEGER,
            candidate_full_name TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            election_cycle INTEGER,
            candidate_party_affiliation TEXT,
            committee_id_sbe INTEGER,
            committee_name TEXT,
            committee_type TEXT,
            committee_party_affiliation TEXT,
            filing_count INTEGER,
            sum_total_receipts REAL,
            sum_total_expenditures REAL,
            max_ending_funds_available REAL,
            archived_filing_count INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO bulk_candidate_committee_finance_agg (
            candidate_id, candidate_full_name, office_sought, district_type, district, election_cycle,
            candidate_party_affiliation, committee_id_sbe, committee_name, committee_type,
            committee_party_affiliation, filing_count, sum_total_receipts,
            sum_total_expenditures, max_ending_funds_available, archived_filing_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            901,
            "Candidate Alpha",
            "Governor",
            "Statewide",
            "At-Large",
            2026,
            "Independent",
            1001,
            "Committee One",
            "Political Action",
            "Independent",
            4,
            12500.0,
            4500.0,
            8000.0,
            0,
        ),
    )
    conn.execute(
        """
        INSERT INTO bulk_candidate_committee_finance_agg (
            candidate_id, candidate_full_name, office_sought, district_type, district, election_cycle,
            candidate_party_affiliation, committee_id_sbe, committee_name, committee_type,
            committee_party_affiliation, filing_count, sum_total_receipts,
            sum_total_expenditures, max_ending_funds_available, archived_filing_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            902,
            "Candidate Beta",
            "Governor",
            "Statewide",
            "At-Large",
            2026,
            "Independent",
            1002,
            "Committee Two",
            "Political Action",
            "Independent",
            3,
            7500.0,
            2000.0,
            5500.0,
            0,
        ),
    )

    conn.execute("DROP TABLE IF EXISTS bulk_receipts_clean")
    conn.execute(
        """
        CREATE TABLE bulk_receipts_clean (
            committee_id_sbe INTEGER,
            contributed_by TEXT,
            d2_part_code TEXT,
            filed_doc_id INTEGER,
            is_archived INTEGER,
            amount REAL,
            received_date TEXT,
            received_datetime_raw TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO bulk_receipts_clean (
            committee_id_sbe, contributed_by, d2_part_code, filed_doc_id, is_archived, amount, received_date, received_datetime_raw
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1001, "Donor One", "1A", 50001, 0, 5000.0, "2026-01-05", "2026-01-05 12:00:00"),
            (1001, "Donor Two", "1A", 50002, 0, 2000.0, "2026-01-06", "2026-01-06 12:00:00"),
            (1002, "Donor One", "1A", 50003, 0, 3000.0, "2026-01-07", "2026-01-07 12:00:00"),
            (1001, "Legacy Donor", "1A", 50004, 0, 4000.0, "2024-06-10", "2024-06-10 12:00:00"),
        ],
    )

    conn.execute("DROP TABLE IF EXISTS bulk_d2_totals_clean")
    conn.execute(
        """
        CREATE TABLE bulk_d2_totals_clean (
            filed_doc_id INTEGER,
            is_archived INTEGER
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO bulk_d2_totals_clean (filed_doc_id, is_archived)
        VALUES (?, ?)
        """,
        [
            (50001, 0),
            (50002, 0),
            (50003, 0),
            (50004, 0),
        ],
    )

    conn.execute("DROP TABLE IF EXISTS bulk_expenditures_clean")
    conn.execute(
        """
        CREATE TABLE bulk_expenditures_clean (
            committee_id_sbe INTEGER,
            payee_last_or_business_name TEXT,
            candidate_name TEXT,
            d2_part_code TEXT,
            is_archived INTEGER,
            amount REAL,
            expended_date TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO bulk_expenditures_clean (
            committee_id_sbe, payee_last_or_business_name, candidate_name, d2_part_code, is_archived, amount, expended_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1001, "Vendor One", "Candidate Alpha", "9A", 0, 1500.0, "2026-01-08"),
            (1001, "Vendor One", "Candidate Alpha", "1A", 0, 999.0, "2026-01-08"),
            (1001, "Vendor One", "Candidate Alpha", "9A", 1, 888.0, "2026-01-08"),
            (1001, "Legacy Vendor", "Candidate Alpha", "9A", 0, 600.0, "2024-07-15"),
        ],
    )

    d2_report_id = conn.execute(
        """
        INSERT INTO d2_reports (
            committee_id, report_type, reporting_period, filed_date, detail_url, source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            committee_one.id,
            "D-2 Quarterly Report",
            "Q1 2026",
            "04/30/2026",
            "https://example.com/d2/1",
            "d2:test:1",
        ),
    ).lastrowid
    link_id = conn.execute(
        """
        INSERT INTO d2_itemized_links (
            d2_report_id, label, itemized_type, url, source_identifier
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            d2_report_id,
            "a. Itemized",
            "expenditure",
            "https://example.com/d2/1/itemized",
            "d2:itemized:test:1",
        ),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO d2_itemized_entries (
            d2_report_id, itemized_link_id, row_hash, entry_type, amount,
            description, purpose_beneficiary
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            d2_report_id,
            link_id,
            "row-hash-1",
            "expenditure",
            750.0,
            "Google digital ad buy",
            "Media advertising",
        ),
    )
    conn.commit()


def _extract_top_state_race_href(response_data: bytes, race_label: str) -> str:
    html = response_data.decode("utf-8")
    match = re.search(
        rf'href="([^"]*?/analytics/state-races/[^"]+)"[^>]*>\s*{re.escape(race_label)}\s*</a>',
        html,
    )
    assert match is not None, f"Missing state race link for label: {race_label}"
    return match.group(1)


@pytest.fixture
def analytics_conn(tmp_path: Path):
    db_path = str(tmp_path / "analytics.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_analytics_dataset(conn)
    yield conn
    conn.close()


@pytest.fixture
def analytics_app(tmp_path: Path):
    db_path = str(tmp_path / "analytics_web.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_analytics_dataset(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path})


@pytest.fixture
def analytics_client(analytics_app):
    return analytics_app.test_client()


def test_analytics_service_outputs(analytics_conn):
    network = get_network_graph(analytics_conn, min_edge_amount=0, limit=200)
    assert network["summary"]["edge_count"] > 0
    assert network["summary"]["donor_committee_source"] in {"contributions", "bulk_receipts"}
    assert any(edge["edge_type"] == "committee_candidate" for edge in network["edges"])
    assert any(node["node_type"] == "candidate" for node in network["nodes"])
    assert "edge_type_definitions" in network["summary"]
    assert "committee_candidate" in network["summary"]["edge_type_definitions"]
    donor_edge = next(edge for edge in network["edges"] if edge["edge_type"] == "donor_committee")
    assert donor_edge["weight_unit"] == "usd"
    committee_edge = next(edge for edge in network["edges"] if edge["edge_type"] == "committee_candidate")
    assert committee_edge["is_direct_transfer"] is False
    assert committee_edge["weight_unit"] == "usd"
    assert "edge_caveat" in committee_edge

    anomalies = get_anomaly_flags(analytics_conn, limit=50)
    anomaly_types = {row["flag_type"] for row in anomalies}
    assert "large_single_contribution" in anomaly_types
    assert "monthly_spike" in anomaly_types
    assert all("explainability" in row for row in anomalies)
    assert any(row.get("percentile") is not None for row in anomalies if row["flag_type"] == "large_single_contribution")

    concentration = get_donor_concentration(analytics_conn, limit=20)
    committee_one = next(row for row in concentration if row["committee_name"] == "Committee One")
    assert committee_one["top1_share"] > 0.8
    assert committee_one["hhi"] > 4500

    time_series = get_time_series(analytics_conn, months=12)
    assert time_series[-1]["month"] == "2026-04"
    assert any(row["moving_avg_3"] > 0 for row in time_series)

    geo = get_geo_summary(analytics_conn, limit_states=10, limit_cities=10)
    states = {row["state"] for row in geo["states"]}
    assert "IL" in states
    assert "MO" in states
    chicago_rows = [row for row in geo["cities"] if row["state"] == "IL" and row["city"] == "Chicago"]
    assert len(chicago_rows) == 1
    assert not any(row["city"] == "CHICAGO" for row in geo["cities"])
    geo_drilldown = get_geo_drilldown(
        analytics_conn,
        geo_type="state",
        geo_value="IL",
        page=1,
        per_page=25,
        sort_by="total_amount",
        sort_dir="desc",
    )
    assert geo_drilldown["summary"]["sum_check_delta"] == 0.0
    assert geo_drilldown["summary"]["total_amount"] >= geo_drilldown["summary"]["page_total_amount"]

    nlp = get_nlp_spending_summary(analytics_conn, limit=20)
    categories = {row["category"] for row in nlp}
    assert "media_advertising" in categories
    assert "legal_compliance" in categories

    analytics_conn.execute("DROP TABLE IF EXISTS bulk_d2_receipts_recon")
    analytics_conn.execute(
        """
        CREATE TABLE bulk_d2_receipts_recon (
            committee_id_sbe INTEGER,
            committee_name TEXT,
            filed_doc_id INTEGER,
            d2_total_receipts REAL,
            receipts_amount_sum REAL,
            receipts_minus_d2_total REAL,
            receipt_row_count INTEGER,
            first_receipt_date TEXT,
            last_receipt_date TEXT,
            is_archived INTEGER
        )
        """
    )
    analytics_conn.execute(
        """
        INSERT INTO bulk_d2_receipts_recon (
            committee_id_sbe, committee_name, filed_doc_id, d2_total_receipts,
            receipts_amount_sum, receipts_minus_d2_total, receipt_row_count,
            first_receipt_date, last_receipt_date, is_archived
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1001, "Committee One", 7001, 1000.0, 600.0, -400.0, 2, "2026-04-01", "2026-04-30", 0),
    )
    analytics_conn.commit()

    recon = get_reconciliation_outliers(analytics_conn, limit=10, min_abs_diff=100)
    assert len(recon) == 1
    assert recon[0]["committee_name"] == "Committee One"
    assert recon[0]["abs_diff"] == 400.0

    sources = get_analytics_data_sources(analytics_conn)
    assert "donor_flow_source" in sources
    assert sources["reconciliation_available"] is True
    assert sources["reconciliation_rows"] >= 1


def test_relationship_graph_services(analytics_conn):
    donor_cogiving = get_donor_cogiving_network(
        analytics_conn,
        donor_limit=50,
        edge_limit=100,
        min_shared_amount=10.0,
        min_shared_targets=1,
    )
    assert "nodes" in donor_cogiving
    assert "edges" in donor_cogiving
    assert donor_cogiving["summary"]["source"] in {"bulk_receipts", "contributions"}

    committee_similarity = get_committee_similarity_network(
        analytics_conn,
        committee_limit=50,
        edge_limit=100,
        min_shared_donors=1,
        min_shared_amount=1.0,
    )
    assert "nodes" in committee_similarity
    assert "edges" in committee_similarity
    assert committee_similarity["summary"]["source"] in {"bulk_receipts", "contributions"}

    analytics_conn.execute("DELETE FROM analytics_donor_committee_agg")
    analytics_conn.execute("DELETE FROM fec_schedule_a_contributions")
    analytics_conn.execute("DELETE FROM fec_local_donor_matches")
    analytics_conn.execute("DELETE FROM lobbying_clients")
    analytics_conn.execute("DELETE FROM lobbying_entities")
    analytics_conn.execute("DELETE FROM lobbying_entity_clients")
    analytics_conn.execute("DELETE FROM lobbying_donor_matches")
    analytics_conn.execute("DELETE FROM lobbying_expenditure_matches")
    analytics_conn.execute("DELETE FROM irs527_organizations")
    analytics_conn.execute("DELETE FROM irs527_committee_matches")
    analytics_conn.execute("DELETE FROM irs527_expenditure_recipient_matches")
    analytics_conn.execute("DELETE FROM irs527_director_donor_matches")

    analytics_conn.execute("DROP TABLE IF EXISTS bulk_cmte_candidate_links_clean")
    analytics_conn.execute(
        """
        CREATE TABLE bulk_cmte_candidate_links_clean (
            committee_id_sbe INTEGER,
            candidate_id INTEGER,
            candidate_full_name TEXT
        )
        """
    )

    analytics_conn.executemany(
        """
        INSERT INTO analytics_donor_committee_agg (
            source, donor_key, donor_name, donor_address, donor_city, donor_state,
            occupation, employer, committee_id, committee_name, total_amount, contribution_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("bulk_receipts", "d1", "Donor One", "A", "Chicago", "IL", None, None, 10, "Committee Ten", 1000.0, 2),
            ("bulk_receipts", "d1", "Donor One", "A", "Chicago", "IL", None, None, 11, "Committee Eleven", 500.0, 1),
            ("bulk_receipts", "d2", "Donor Two", "B", "Chicago", "IL", None, None, 10, "Committee Ten", 700.0, 1),
            ("bulk_receipts", "d2", "Donor Two", "B", "Chicago", "IL", None, None, 11, "Committee Eleven", 900.0, 2),
        ],
    )
    analytics_conn.executemany(
        """
        INSERT INTO bulk_cmte_candidate_links_clean (committee_id_sbe, candidate_id, candidate_full_name)
        VALUES (?, ?, ?)
        """,
        [
            (10, 1001, "State Candidate A"),
            (11, 1002, "State Candidate B"),
        ],
    )

    analytics_conn.executemany(
        """
        INSERT INTO fec_schedule_a_contributions (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            contributor_name, contribution_receipt_amount, donor_key, donor_entity_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("fa1", 2026, "F1001", "Federal Candidate A", "C1", "Federal Committee 1", "Fed Donor One", 1000.0, "fd1", "fd1"),
            ("fa2", 2026, "F1002", "Federal Candidate B", "C2", "Federal Committee 2", "Fed Donor One", 600.0, "fd1", "fd1"),
            ("fa3", 2026, "F1001", "Federal Candidate A", "C1", "Federal Committee 1", "Fed Donor Two", 700.0, "fd2", "fd2"),
            ("fa4", 2026, "F1002", "Federal Candidate B", "C2", "Federal Committee 2", "Fed Donor Two", 500.0, "fd2", "fd2"),
        ],
    )
    analytics_conn.executemany(
        """
        INSERT INTO fec_local_donor_matches (
            federal_donor_entity_key, local_donor_key, match_method, confidence_score
        ) VALUES (?, ?, ?, ?)
        """,
        [
            ("fd1", "d1", "name_state_zip", 0.95),
            ("fd2", "d2", "name_state_zip", 0.95),
        ],
    )

    analytics_conn.executemany(
        """
        INSERT INTO lobbying_clients (client_id, client_name)
        VALUES (?, ?)
        """,
        [(1, "Client One")],
    )
    analytics_conn.executemany(
        """
        INSERT INTO lobbying_entities (entity_id, entity_name, reg_year)
        VALUES (?, ?, ?)
        """,
        [(101, "Entity One", 2026)],
    )
    analytics_conn.executemany(
        """
        INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year)
        VALUES (?, ?, ?)
        """,
        [(101, 1, 2026)],
    )
    analytics_conn.executemany(
        """
        INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(1, "d1", "Client One", "Donor One", 0.91, "name_state_zip")],
    )
    analytics_conn.executemany(
        """
        INSERT INTO lobbying_expenditure_matches (
            source_type, source_id, source_name, payee_name, committee_id_sbe, score
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [("client", 1, "Client One", "Vendor One", 10, 0.88)],
    )

    analytics_conn.executemany(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, state)
        VALUES (?, ?, ?, ?, ?)
        """,
        [("EIN1", 1, 1, "Org One", "IL")],
    )
    analytics_conn.executemany(
        """
        INSERT INTO irs527_committee_matches (
            ein, org_name, committee_id_sbe, committee_name, score, method
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [("EIN1", "Org One", 10, "Committee Ten", 0.9, "name_state_zip")],
    )
    analytics_conn.executemany(
        """
        INSERT INTO irs527_expenditure_recipient_matches (
            ein, org_name, recipient_name, matched_type, matched_id, matched_name, score
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [("EIN1", "Org One", "Vendor One", "committee", "10", "Committee Ten", 0.8)],
    )
    analytics_conn.executemany(
        """
        INSERT INTO irs527_director_donor_matches (
            ein, org_name, director_name, donor_key, donor_name, score
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [("EIN1", "Org One", "Director One", "d1", "Donor One", 0.87)],
    )
    analytics_conn.commit()

    candidate_networks = get_candidate_competition_networks(
        analytics_conn,
        candidate_limit=200,
        edge_limit=200,
        min_shared_donors=1,
        min_shared_amount=1.0,
    )
    assert candidate_networks["state"]["summary"]["edge_count"] > 0
    assert candidate_networks["federal"]["summary"]["edge_count"] > 0
    assert candidate_networks["combined"]["summary"]["edge_count"] > 0

    lobbying_graph = get_lobbying_influence_graph(
        analytics_conn,
        client_limit=50,
        edge_limit=200,
    )
    assert lobbying_graph["summary"]["edge_count"] > 0
    assert any(edge["edge_type"] == "client_donor_match" for edge in lobbying_graph["edges"])

    ecosystem_graph = get_irs527_ecosystem_graph(
        analytics_conn,
        org_limit=50,
        edge_limit=200,
    )
    assert ecosystem_graph["summary"]["edge_count"] > 0
    assert any(edge["edge_type"] == "org_committee_match" for edge in ecosystem_graph["edges"])


def test_analytics_date_range_filters(analytics_conn):
    filtered_series = get_time_series(
        analytics_conn,
        months=12,
        date_from="2026-02-01",
        date_to="2026-03-31",
    )
    assert [row["month"] for row in filtered_series] == ["2026-02", "2026-03"]

    filtered_anomalies = get_anomaly_flags(
        analytics_conn,
        limit=50,
        date_from="2026-04-01",
        date_to="2026-04-30",
    )
    assert len(filtered_anomalies) > 0
    assert all(row["event_date"] for row in filtered_anomalies)
    assert all(str(row["event_date"]).startswith("2026-04") for row in filtered_anomalies)


def test_lobbying_influence_graph_date_window_filters_edges(analytics_conn):
    analytics_conn.execute("DELETE FROM lobbying_clients")
    analytics_conn.execute("DELETE FROM lobbying_entities")
    analytics_conn.execute("DELETE FROM lobbying_entity_clients")
    analytics_conn.execute("DELETE FROM lobbying_donor_matches")
    analytics_conn.execute("DELETE FROM lobbying_expenditure_matches")
    analytics_conn.execute(
        """
        INSERT INTO lobbying_clients (client_id, client_name)
        VALUES (?, ?)
        """,
        (1, "Client One"),
    )
    analytics_conn.execute(
        """
        INSERT INTO lobbying_entities (entity_id, entity_name, reg_year)
        VALUES (?, ?, ?)
        """,
        (101, "Entity One", 2026),
    )
    analytics_conn.execute(
        """
        INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year)
        VALUES (?, ?, ?)
        """,
        (101, 1, 2026),
    )
    analytics_conn.execute(
        """
        INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (1, "d1", "Client One", "Donor One", 0.91, "name_state_zip"),
    )
    analytics_conn.execute(
        """
        INSERT INTO lobbying_expenditure_matches (
            source_type, source_id, source_name, payee_name, committee_id_sbe, score
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("client", 1, "Client One", "Vendor One", 1001, 0.88),
    )
    analytics_conn.commit()

    all_time = get_lobbying_influence_graph(
        analytics_conn,
        client_limit=50,
        edge_limit=200,
    )
    filtered = get_lobbying_influence_graph(
        analytics_conn,
        client_limit=50,
        edge_limit=200,
        date_from="2027-01-01",
        date_to="2027-12-31",
    )
    assert all_time["summary"]["edge_count"] > 0
    assert filtered["summary"]["window_applied"] is True
    assert filtered["summary"]["edge_count"] == 0


def test_irs527_ecosystem_graph_date_window_filters_edges(analytics_conn):
    analytics_conn.execute("DELETE FROM irs527_organizations")
    analytics_conn.execute("DELETE FROM irs527_committee_matches")
    analytics_conn.execute("DELETE FROM irs527_expenditure_recipient_matches")
    analytics_conn.execute("DELETE FROM irs527_director_donor_matches")
    analytics_conn.execute(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, state)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("EIN1", 1, 1, "Org One", "IL"),
    )
    analytics_conn.execute(
        """
        INSERT INTO irs527_committee_matches (
            ein, org_name, committee_id_sbe, committee_name, score, method
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("EIN1", "Org One", 10, "Committee Ten", 0.9, "name_state_zip"),
    )
    analytics_conn.execute(
        """
        INSERT INTO irs527_expenditure_recipient_matches (
            ein, org_name, recipient_name, matched_type, matched_id, matched_name, score
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("EIN1", "Org One", "Vendor One", "committee", "10", "Committee Ten", 0.8),
    )
    analytics_conn.execute(
        """
        INSERT INTO irs527_director_donor_matches (
            ein, org_name, director_name, donor_key, donor_name, score
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("EIN1", "Org One", "Director One", "d1", "Donor One", 0.87),
    )
    analytics_conn.commit()

    all_time = get_irs527_ecosystem_graph(
        analytics_conn,
        org_limit=50,
        edge_limit=200,
    )
    filtered = get_irs527_ecosystem_graph(
        analytics_conn,
        org_limit=50,
        edge_limit=200,
        date_from="2027-01-01",
        date_to="2027-12-31",
    )
    assert all_time["summary"]["edge_count"] > 0
    assert filtered["summary"]["window_applied"] is True
    assert filtered["summary"]["edge_count"] == 0


def test_materialized_refresh_and_snapshot_cache(analytics_conn):
    stats = refresh_analytics_materialized(analytics_conn)
    assert stats["donor_rows"] > 0
    assert stats["monthly_rows"] > 0

    params = {
        "min_edge_amount": 0.0,
        "network_limit": 200,
        "anomaly_limit": 25,
        "concentration_limit": 25,
        "months": 12,
        "geo_state_limit": 10,
        "geo_city_limit": 10,
        "nlp_limit": 10,
        "recon_limit": 10,
        "recon_min_abs_diff": 100.0,
        "snapshot_version": 1,
    }
    payload = build_dashboard_full_snapshot(analytics_conn, params=params, rebuild_materialized=False)
    assert "network" in payload
    assert "time_series" in payload
    assert "data_sources" in payload

    saved = save_dashboard_snapshot(
        analytics_conn,
        params=params,
        status="completed",
        payload=payload,
        error_message=None,
    )
    assert saved["status"] == "completed"
    cached = get_dashboard_snapshot(analytics_conn, params=params, ttl_seconds=900)
    assert cached["is_fresh"] is True
    assert cached["payload"] is not None
    assert "network" in cached["payload"]


def test_vendor_network_supports_bulk_committees_clean_sbe_schema(analytics_conn):
    analytics_conn.execute("DROP TABLE IF EXISTS bulk_expenditures_clean")
    analytics_conn.execute(
        """
        CREATE TABLE bulk_expenditures_clean (
            committee_id_sbe INTEGER,
            payee_last_or_business_name TEXT,
            amount REAL
        )
        """
    )
    analytics_conn.execute("DROP TABLE IF EXISTS bulk_committees_clean")
    analytics_conn.execute(
        """
        CREATE TABLE bulk_committees_clean (
            committee_id_sbe INTEGER PRIMARY KEY,
            committee_name TEXT
        )
        """
    )
    analytics_conn.executemany(
        """
        INSERT INTO bulk_committees_clean (committee_id_sbe, committee_name)
        VALUES (?, ?)
        """,
        [
            (10, "Committee Ten"),
            (11, "Committee Eleven"),
        ],
    )
    analytics_conn.executemany(
        """
        INSERT INTO bulk_expenditures_clean (committee_id_sbe, payee_last_or_business_name, amount)
        VALUES (?, ?, ?)
        """,
        [
            (10, "Vendor One", 1200.0),
            (10, "Vendor One", 900.0),
            (11, "Vendor Two", 1500.0),
        ],
    )
    analytics_conn.commit()

    graph = get_vendor_expenditure_network(
        analytics_conn,
        committee_limit=20,
        vendor_limit=20,
        edge_limit=100,
        min_amount=1000.0,
    )

    assert graph["summary"]["edge_count"] > 0
    committee_labels = {node["label"] for node in graph["nodes"] if node["node_type"] == "committee"}
    assert "Committee Ten" in committee_labels
    assert "edge_type_definitions" in graph["summary"]
    vendor_edge = graph["edges"][0]
    assert vendor_edge["edge_type"] == "committee_vendor"
    assert vendor_edge["weight_unit"] == "usd"


def test_bulk_materialization_uses_active_d2_part1_rows_only(tmp_path: Path):
    db_path = str(tmp_path / "analytics_bulk_filter.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS bulk_committees_clean")
        conn.execute("DROP TABLE IF EXISTS bulk_d2_totals_clean")
        conn.execute("DROP TABLE IF EXISTS bulk_receipts_clean")

        conn.execute(
            """
            CREATE TABLE bulk_committees_clean (
                committee_id_sbe INTEGER PRIMARY KEY,
                committee_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_d2_totals_clean (
                d2_totals_record_id INTEGER PRIMARY KEY,
                committee_id_sbe INTEGER,
                filed_doc_id INTEGER,
                is_archived INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_receipts_clean (
                receipt_record_id INTEGER,
                committee_id_sbe INTEGER,
                filed_doc_id INTEGER,
                last_or_business_name TEXT,
                first_name TEXT,
                received_date TEXT,
                amount REAL,
                occupation TEXT,
                employer TEXT,
                address_line_1 TEXT,
                address_line_2 TEXT,
                city TEXT,
                state TEXT,
                postal_code TEXT,
                d2_part_code TEXT,
                is_archived INTEGER
            )
            """
        )

        conn.execute(
            """
            INSERT INTO bulk_committees_clean (committee_id_sbe, committee_name)
            VALUES (10, 'Committee Ten')
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_d2_totals_clean (d2_totals_record_id, committee_id_sbe, filed_doc_id, is_archived)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, 10, 100, 0),
                (2, 10, 101, 1),
            ],
        )
        conn.executemany(
            """
            INSERT INTO bulk_receipts_clean (
                receipt_record_id, committee_id_sbe, filed_doc_id, last_or_business_name, first_name,
                received_date, amount, occupation, employer, address_line_1, address_line_2,
                city, state, postal_code, d2_part_code, is_archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 10, 100, "Included", "Alice", "2026-01-10 00:00:00", 100.0, None, None, "1 Main", "", "Chicago", "IL", "60601", "1A", 0),
                (2, 10, 100, "Archived", "Amy", "2026-01-10 00:00:00", 900.0, None, None, "2 Main", "", "Chicago", "IL", "60601", "1A", 1),
                (3, 10, 100, "PartFive", "Pat", "2026-01-10 00:00:00", 800.0, None, None, "3 Main", "", "Chicago", "IL", "60601", "5A", 0),
                (4, 10, 999, "MissingD2", "Mia", "2026-01-10 00:00:00", 700.0, None, None, "4 Main", "", "Chicago", "IL", "60601", "1A", 0),
                (5, 10, 101, "ArchivedD2", "Dina", "2026-01-10 00:00:00", 600.0, None, None, "5 Main", "", "Chicago", "IL", "60601", "1A", 0),
            ],
        )
        conn.commit()

        stats = refresh_analytics_materialized(conn)
        assert stats["source"] == "bulk_receipts"
        assert stats["materialization_version"] >= 2
        assert stats["donor_summary_rows"] == 1

        donor_rows = conn.execute(
            """
            SELECT donor_name, total_amount, contribution_count
            FROM analytics_donor_summary
            WHERE source = 'bulk_receipts'
            ORDER BY donor_name
            """
        ).fetchall()
        assert len(donor_rows) == 1
        assert donor_rows[0]["donor_name"] == "Alice Included"
        assert donor_rows[0]["total_amount"] == 100.0
        assert donor_rows[0]["contribution_count"] == 1

        meta_row = conn.execute(
            """
            SELECT materialization_version, materialization_notes
            FROM analytics_materialized_meta
            WHERE source = 'bulk_receipts'
            """
        ).fetchone()
        assert meta_row is not None
        assert int(meta_row["materialization_version"]) >= 2
        assert "active_non_archived_d2_part1" in (meta_row["materialization_notes"] or "")
    finally:
        conn.close()


def test_analytics_dashboard_route_loads(analytics_client):
    response = analytics_client.get("/analytics/")
    assert response.status_code == 200
    assert b"Analytics Overview" in response.data
    assert b"Analytics section navigation" in response.data
    assert b"Quick mode is active" in response.data
    assert b"Heavy sections are available in Full mode after the snapshot completes." in response.data
    assert b"Donor-flow source" in response.data
    assert b'name="date_from"' in response.data
    assert b'name="date_to"' in response.data
    assert b"Top State Races" in response.data
    assert b"Outside Spending (E)" in response.data
    assert b"Outside Pressure Ratio" in response.data
    assert b"Top Candidate Share" in response.data
    assert b"Governor - Statewide At-Large" in response.data
    assert b"$10,000.00" in response.data
    assert b"$1,500.00" in response.data
    assert b"15.0%" in response.data
    assert b"62.5%" in response.data

    pages = [
        (
            "/analytics/networks",
            b"Analytics: Networks",
            b"Skipped in quick mode. Switch to Full mode to compute the network.",
        ),
        (
            "/analytics/risk",
            b"Analytics: Risk",
            b"Skipped in quick mode. Switch to Full mode to compute anomaly flags.",
        ),
        (
            "/analytics/donors",
            b"Analytics: Donors",
            b"Skipped in quick mode. Switch to Full mode to compute concentration metrics.",
        ),
        (
            "/analytics/geography",
            b"Analytics: Geography",
            b"Skipped in quick mode. Switch to Full mode to compute state-level geo summaries.",
        ),
        (
            "/analytics/relationships",
            b"Analytics: Relationships",
            b"Donor Co-Giving Network",
        ),
    ]
    for path, title, marker in pages:
        page_response = analytics_client.get(path)
        assert page_response.status_code == 200
        assert title in page_response.data
        assert b"Analytics section navigation" in page_response.data
        assert marker in page_response.data


def test_top_state_races_rows_include_clickable_hrefs(analytics_client):
    response = analytics_client.get("/analytics/?period=2026cycle")
    assert response.status_code == 200

    href = _extract_top_state_race_href(response.data, "Governor - Statewide At-Large")
    parsed = urlparse(href)
    params = parse_qs(parsed.query)

    assert parsed.path.startswith("/analytics/state-races/")
    assert params.get("period") == ["2026cycle"]
    assert "date_from" not in params
    assert "date_to" not in params


def test_state_race_detail_route_returns_200_for_valid_race(analytics_client):
    overview = analytics_client.get("/analytics/?period=2026cycle")
    assert overview.status_code == 200
    href = _extract_top_state_race_href(overview.data, "Governor - Statewide At-Large")

    detail = analytics_client.get(href)
    assert detail.status_code == 200
    assert b"State Race Detail" in detail.data
    assert b"Governor - Statewide At-Large" in detail.data
    assert b"Candidate Committee Rows" in detail.data
    assert b"Committee Contribution Rows" in detail.data
    assert b"Outside Spending Rows" in detail.data


def test_state_race_detail_route_returns_404_for_invalid_race_key(analytics_client):
    response = analytics_client.get("/analytics/state-races/not-a-real-race-key?period=2026cycle")
    assert response.status_code == 404


def test_state_race_detail_period_propagation_changes_output(analytics_client):
    cycle_overview = analytics_client.get("/analytics/?period=2026cycle")
    assert cycle_overview.status_code == 200
    cycle_href = _extract_top_state_race_href(cycle_overview.data, "Governor - Statewide At-Large")
    cycle_detail = analytics_client.get(cycle_href)
    assert cycle_detail.status_code == 200
    assert b"$10,000.00" in cycle_detail.data
    assert b"$1,500.00" in cycle_detail.data

    all_overview = analytics_client.get("/analytics/?period=all")
    assert all_overview.status_code == 200
    all_href = _extract_top_state_race_href(all_overview.data, "Governor - Statewide At-Large")
    all_parsed = urlparse(all_href)
    all_params = parse_qs(all_parsed.query)
    assert all_params.get("period") == ["all"]

    all_detail = analytics_client.get(all_href)
    assert all_detail.status_code == 200
    assert b"$14,000.00" in all_detail.data
    assert b"$2,100.00" in all_detail.data


def test_analytics_dashboard_full_mode_loads_heavy_sections(analytics_client):
    overview = analytics_client.get("/analytics/?load_mode=full&sync_full=1&period=all")
    assert overview.status_code == 200
    assert b"Quick mode is active" not in overview.data
    assert b"Full snapshot status" in overview.data

    networks = analytics_client.get("/analytics/networks?load_mode=full&period=all")
    assert networks.status_code == 200
    assert b"Analytics: Networks" in networks.data
    assert b'id="network-svg"' in networks.data
    assert b'id="network-view-mode"' in networks.data
    assert b"Sankey Flow" in networks.data
    assert b"Skipped in quick mode" not in networks.data
    assert b"Committee One" in networks.data

    risk = analytics_client.get("/analytics/risk?load_mode=full&period=all")
    assert risk.status_code == 200
    assert b"Analytics: Risk" in risk.data
    assert b"Risk and Anomaly Flags" in risk.data
    assert b"Explainability" in risk.data
    assert b"Skipped in quick mode" not in risk.data
    assert b"Committee One" in risk.data

    donors = analytics_client.get("/analytics/donors?load_mode=full&period=all")
    assert donors.status_code == 200
    assert b"Analytics: Donors" in donors.data
    assert b"Donor Concentration Metrics" in donors.data
    assert b"NLP Spending Categories" in donors.data
    assert b"Skipped in quick mode" not in donors.data
    assert b"Committee One" in donors.data

    geography = analytics_client.get("/analytics/geography?load_mode=full&period=all")
    assert geography.status_code == 200
    assert b"Analytics: Geography" in geography.data
    assert b"Time-Series Intelligence" in geography.data
    assert b"Geospatial Summary - States" in geography.data
    assert b"Skipped in quick mode" not in geography.data


def test_analytics_geo_drilldown_route(analytics_client):
    state_resp = analytics_client.get(
        "/analytics/geo-drilldown?geo_type=state&geo_value=IL&load_mode=full&period=all"
    )
    assert state_resp.status_code == 200
    assert b"Geo Drilldown: IL" in state_resp.data
    assert b"Sum check delta" in state_resp.data
    assert b"Committee One" in state_resp.data

    city_resp = analytics_client.get(
        "/analytics/geo-drilldown?geo_type=city&geo_value=Chicago&geo_state=IL&load_mode=full&period=all"
    )
    assert city_resp.status_code == 200
    assert b"Geo Drilldown: Chicago, IL" in city_resp.data


def test_analytics_networks_full_mode_survives_optional_graph_failures(analytics_client, monkeypatch):
    overview = analytics_client.get("/analytics/?load_mode=full&sync_full=1")
    assert overview.status_code == 200

    def _explode(*_args, **_kwargs):
        raise RuntimeError("simulated vendor graph failure")

    import webapp.routes.analytics as analytics_routes

    monkeypatch.setattr(analytics_routes, "get_vendor_expenditure_network", _explode)

    response = analytics_client.get("/analytics/networks?load_mode=full")
    assert response.status_code == 200
    assert b"Analytics: Networks" in response.data
    assert b'id="vendor-network-data"' in response.data
    assert b"vendor_network_query_failed" in response.data


def test_analytics_api_endpoints(analytics_client):
    network = analytics_client.get("/api/analytics/network?min_edge_amount=0&limit=100&period=all")
    assert network.status_code == 200
    network_data = network.get_json()
    assert "nodes" in network_data
    assert "edges" in network_data
    assert "summary" in network_data
    assert "region_counts" in network_data["summary"]
    assert len(network_data["summary"]["region_counts"]) > 0
    assert all("region" in node for node in network_data["nodes"])
    assert "edge_type_definitions" in network_data["summary"]

    anomalies = analytics_client.get("/api/analytics/anomalies?limit=10&period=all")
    assert anomalies.status_code == 200
    anomalies_data = anomalies.get_json()
    assert "data" in anomalies_data
    assert len(anomalies_data["data"]) > 0
    assert "explainability" in anomalies_data["data"][0]
    assert "threshold" in anomalies_data["data"][0]

    concentration = analytics_client.get("/api/analytics/concentration?limit=10&period=all")
    assert concentration.status_code == 200
    concentration_data = concentration.get_json()
    assert "data" in concentration_data
    assert len(concentration_data["data"]) > 0

    timeseries = analytics_client.get("/api/analytics/time-series?months=12&period=all")
    assert timeseries.status_code == 200
    timeseries_data = timeseries.get_json()
    assert "data" in timeseries_data
    assert len(timeseries_data["data"]) > 0

    timeseries_filtered = analytics_client.get(
        "/api/analytics/time-series?months=12&date_from=2026-02-01&date_to=2026-03-31"
    )
    assert timeseries_filtered.status_code == 200
    filtered_series_data = timeseries_filtered.get_json()
    assert [row["month"] for row in filtered_series_data["data"]] == ["2026-02", "2026-03"]

    geo = analytics_client.get("/api/analytics/geo?state_limit=5&city_limit=5&period=all")
    assert geo.status_code == 200
    geo_data = geo.get_json()
    assert "states" in geo_data
    assert "cities" in geo_data

    anomalies_filtered = analytics_client.get(
        "/api/analytics/anomalies?limit=20&date_from=2026-04-01&date_to=2026-04-30"
    )
    assert anomalies_filtered.status_code == 200
    anomalies_filtered_data = anomalies_filtered.get_json()
    assert len(anomalies_filtered_data["data"]) > 0
    assert all(str(row["event_date"]).startswith("2026-04") for row in anomalies_filtered_data["data"])

    nlp = analytics_client.get("/api/analytics/nlp?limit=10")
    assert nlp.status_code == 200
    nlp_data = nlp.get_json()
    assert "data" in nlp_data
    assert len(nlp_data["data"]) > 0

    reconciliation = analytics_client.get("/api/analytics/reconciliation?limit=10&min_abs_diff=100")
    assert reconciliation.status_code == 200
    reconciliation_data = reconciliation.get_json()
    assert "data" in reconciliation_data
    assert "sources" in reconciliation_data
    assert "donor_flow_source" in reconciliation_data["sources"]

    donor_cogiving = analytics_client.get("/api/analytics/donor-cogiving?edge_limit=100")
    assert donor_cogiving.status_code == 200
    donor_cogiving_data = donor_cogiving.get_json()
    assert "nodes" in donor_cogiving_data
    assert "edges" in donor_cogiving_data
    assert "summary" in donor_cogiving_data

    committee_similarity = analytics_client.get("/api/analytics/committee-similarity?edge_limit=100")
    assert committee_similarity.status_code == 200
    committee_similarity_data = committee_similarity.get_json()
    assert "nodes" in committee_similarity_data
    assert "edges" in committee_similarity_data
    assert "summary" in committee_similarity_data

    candidate_competition = analytics_client.get("/api/analytics/candidate-competition?edge_limit=100")
    assert candidate_competition.status_code == 200
    candidate_competition_data = candidate_competition.get_json()
    assert "state" in candidate_competition_data
    assert "federal" in candidate_competition_data
    assert "combined" in candidate_competition_data

    lobbying_influence = analytics_client.get("/api/analytics/lobbying-influence?edge_limit=100")
    assert lobbying_influence.status_code == 200
    lobbying_influence_data = lobbying_influence.get_json()
    assert "nodes" in lobbying_influence_data
    assert "edges" in lobbying_influence_data
    assert "summary" in lobbying_influence_data

    ecosystem_527 = analytics_client.get("/api/analytics/irs527-ecosystem?edge_limit=100")
    assert ecosystem_527.status_code == 200
    ecosystem_527_data = ecosystem_527.get_json()
    assert "nodes" in ecosystem_527_data
    assert "edges" in ecosystem_527_data
    assert "summary" in ecosystem_527_data
