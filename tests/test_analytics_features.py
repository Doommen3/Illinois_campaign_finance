"""Tests for analytics features (network, anomalies, concentration, trends, geo, NLP)."""
from pathlib import Path

import pytest

from database.analytics import (
    build_dashboard_full_snapshot,
    get_analytics_data_sources,
    get_anomaly_flags,
    get_dashboard_snapshot,
    get_donor_concentration,
    get_geo_summary,
    get_network_graph,
    get_nlp_spending_summary,
    get_reconciliation_outliers,
    get_time_series,
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
            candidate_id, candidate_full_name, office_sought, district_type, district,
            candidate_party_affiliation, committee_id_sbe, committee_name, committee_type,
            committee_party_affiliation, filing_count, sum_total_receipts,
            sum_total_expenditures, max_ending_funds_available, archived_filing_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            901,
            "Candidate Alpha",
            "Governor",
            "Statewide",
            "At-Large",
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

    anomalies = get_anomaly_flags(analytics_conn, limit=50)
    anomaly_types = {row["flag_type"] for row in anomalies}
    assert "large_single_contribution" in anomaly_types
    assert "monthly_spike" in anomaly_types

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
    ]
    for path, title, marker in pages:
        page_response = analytics_client.get(path)
        assert page_response.status_code == 200
        assert title in page_response.data
        assert b"Analytics section navigation" in page_response.data
        assert marker in page_response.data


def test_analytics_dashboard_full_mode_loads_heavy_sections(analytics_client):
    overview = analytics_client.get("/analytics/?load_mode=full&sync_full=1")
    assert overview.status_code == 200
    assert b"Quick mode is active" not in overview.data
    assert b"Full snapshot status" in overview.data

    networks = analytics_client.get("/analytics/networks?load_mode=full")
    assert networks.status_code == 200
    assert b"Analytics: Networks" in networks.data
    assert b'id="network-svg"' in networks.data
    assert b'id="network-view-mode"' in networks.data
    assert b"Chord Diagram" in networks.data
    assert b"Skipped in quick mode" not in networks.data
    assert b"Committee One" in networks.data

    risk = analytics_client.get("/analytics/risk?load_mode=full")
    assert risk.status_code == 200
    assert b"Analytics: Risk" in risk.data
    assert b"Risk and Anomaly Flags" in risk.data
    assert b"Skipped in quick mode" not in risk.data
    assert b"Committee One" in risk.data

    donors = analytics_client.get("/analytics/donors?load_mode=full")
    assert donors.status_code == 200
    assert b"Analytics: Donors" in donors.data
    assert b"Donor Concentration Metrics" in donors.data
    assert b"NLP Spending Categories" in donors.data
    assert b"Skipped in quick mode" not in donors.data
    assert b"Committee One" in donors.data

    geography = analytics_client.get("/analytics/geography?load_mode=full")
    assert geography.status_code == 200
    assert b"Analytics: Geography" in geography.data
    assert b"Time-Series Intelligence" in geography.data
    assert b"Geospatial Summary - States" in geography.data
    assert b"Skipped in quick mode" not in geography.data


def test_analytics_api_endpoints(analytics_client):
    network = analytics_client.get("/api/analytics/network?min_edge_amount=0&limit=100")
    assert network.status_code == 200
    network_data = network.get_json()
    assert "nodes" in network_data
    assert "edges" in network_data
    assert "summary" in network_data
    assert "region_counts" in network_data["summary"]
    assert len(network_data["summary"]["region_counts"]) > 0
    assert all("region" in node for node in network_data["nodes"])

    anomalies = analytics_client.get("/api/analytics/anomalies?limit=10")
    assert anomalies.status_code == 200
    anomalies_data = anomalies.get_json()
    assert "data" in anomalies_data
    assert len(anomalies_data["data"]) > 0

    concentration = analytics_client.get("/api/analytics/concentration?limit=10")
    assert concentration.status_code == 200
    concentration_data = concentration.get_json()
    assert "data" in concentration_data
    assert len(concentration_data["data"]) > 0

    timeseries = analytics_client.get("/api/analytics/time-series?months=12")
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

    geo = analytics_client.get("/api/analytics/geo?state_limit=5&city_limit=5")
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
