"""Tests for FEC federal ingestion and query helpers."""
from pathlib import Path

from database.connection import get_db, init_db
from database.federal_fec import (
    count_federal_candidates,
    federal_data_available,
    get_federal_candidate_detail,
    get_federal_donor_detail,
    get_federal_donor_network_clusters,
    get_federal_donor_segmentation,
    get_federal_follow_the_money,
    get_federal_geographic_concentration,
    get_federal_influence_scores,
    get_federal_local_donor_matches,
    get_federal_local_overlap_network,
    get_federal_network_graph,
    get_federal_race_analytics,
    list_federal_candidates,
    refresh_fec_local_donor_matches,
    sync_il_federal_fec,
)


class FakeFecClient:
    """Deterministic fake client for FEC endpoint tests."""

    base_url = "https://api.open.fec.gov/v1"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def request(self, endpoint: str, params: dict):
        self.calls.append((endpoint, dict(params)))

        if endpoint == "/candidates/search/":
            q = (params.get("q") or "").strip().lower()
            if q == "unknown person":
                return {
                    "pagination": {"count": 0, "pages": 1, "page": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            # Universe search for IL/cycle.
            return {
                "pagination": {
                    "count": 2,
                    "pages": 1,
                    "page": 1,
                    "per_page": params.get("per_page", 100),
                },
                "results": [
                    {
                        "candidate_id": "H2IL01349",
                        "name": "JACKSON, JONATHAN",
                        "office": "H",
                        "state": "IL",
                        "district": "01",
                        "party": "DEM",
                        "candidate_status": "C",
                        "principal_committees": [
                            {
                                "committee_id": "C00011111",
                                "name": "JONATHAN JACKSON FOR CONGRESS",
                                "committee_type": "H",
                                "designation": "P",
                                "designation_full": "Principal campaign committee",
                                "filing_frequency": "Q",
                                "party": "DEM",
                                "city": "WASHINGTON",
                                "state": "DC",
                                "zip": "20005",
                            }
                        ],
                    },
                    {
                        "candidate_id": "H2IL09999",
                        "name": "SMITH, JONATHAN",
                        "office": "H",
                        "state": "IL",
                        "district": "09",
                        "party": "REP",
                        "candidate_status": "C",
                        "principal_committees": [],
                    },
                ],
            }

        if endpoint == "/schedules/schedule_a/":
            committee_id = params.get("committee_id")
            if committee_id != "C00011111":
                return {
                    "pagination": {"count": 0, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            if params.get("last_index"):
                return {
                    "pagination": {"count": 1, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            return {
                "pagination": {
                    "count": 1,
                    "pages": 1,
                    "per_page": params.get("per_page", 100),
                },
                "results": [
                    {
                        "sub_id": "4101020261299999999",
                        "committee_id": "C00011111",
                        "committee_name": "JONATHAN JACKSON FOR CONGRESS",
                        "contribution_receipt_amount": 250.0,
                        "contribution_receipt_date": "2026-01-15",
                        "contributor_name": "Jane Donor",
                        "contributor_city": "Chicago",
                        "contributor_state": "IL",
                        "contributor_zip": "60601",
                        "contributor_employer": "ACME",
                        "contributor_occupation": "Engineer",
                        "contributor_id": None,
                        "is_individual": True,
                        "line_number": "11AI",
                        "receipt_type": "IND",
                        "receipt_type_desc": "Individual contribution",
                        "memo_text": "",
                        "two_year_transaction_period": 2026,
                        "load_date": "2026-01-20T01:02:03",
                        "image_number": "202601209000000001",
                    }
                ],
            }

        if endpoint.startswith("/candidate/") and endpoint.endswith("/totals/"):
            candidate_id = endpoint.split("/")[2]
            if candidate_id != "H2IL01349":
                return {
                    "pagination": {"count": 0, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }
            return {
                "pagination": {"count": 1, "pages": 1, "per_page": params.get("per_page", 100)},
                "results": [
                    {
                        "candidate_id": candidate_id,
                        "cycle": int(params.get("cycle") or 2026),
                        "receipts": 1250.0,
                        "contributions": 1250.0,
                        "individual_contributions": 1250.0,
                        "coverage_start_date": "2025-01-01",
                        "coverage_end_date": "2026-03-31",
                        "transaction_coverage_date": "2026-03-31",
                        "last_report_year": 2026,
                        "last_report_type_full": "Quarterly Report",
                        "last_cash_on_hand_end_period": 1000.0,
                    }
                ],
            }

        raise AssertionError(f"Unexpected endpoint call: {endpoint} params={params}")


def test_sync_il_federal_fec_and_queries(tmp_path: Path):
    db_path = str(tmp_path / "fec_sync.db")
    init_db(db_path)
    conn = get_db(db_path)

    csv_path = tmp_path / "il_candidates.csv"
    csv_path.write_text(
        "as_of_date,office,district,party,election_stage,candidate_name,write_in,already_listed_general\n"
        "2026-02-07,U.S. House,IL-01,Democratic,Primary,Jonathan Jackson,False,False\n"
        "2026-02-07,U.S. House,IL-02,Republican,Primary,Unknown Person,False,False\n",
        encoding="utf-8",
    )

    client = FakeFecClient()
    stats = sync_il_federal_fec(
        conn,
        candidates_csv=csv_path,
        api_key="fake-key",
        cycle=2026,
        contributor_state="IL",
        per_page=100,
        max_calls=100,
        max_pages_per_committee=10,
        include_all_committees=False,
        refresh_cache=False,
        skip_donations=False,
        client=client,
    )

    assert stats["seed_rows_loaded"] == 2
    assert stats["candidate_universe_rows"] == 2
    assert stats["matched_rows"] == 1
    assert stats["unmatched_rows"] == 1
    assert stats["committees_upserted"] == 1
    assert stats["contributions_upserted"] == 1
    assert stats["api_calls_made"] >= 2

    assert federal_data_available(conn) is True

    total_candidates = count_federal_candidates(conn, cycle=2026)
    assert total_candidates == 2

    rows = list_federal_candidates(conn, cycle=2026, limit=10)
    assert len(rows) == 2
    jackson = next(row for row in rows if row["candidate_name"] == "Jonathan Jackson")
    assert jackson["fec_candidate_id"] == "H2IL01349"
    assert jackson["contribution_count"] == 1
    assert jackson["total_amount"] == 1250.0

    detail = get_federal_candidate_detail(conn, candidate_id="H2IL01349", cycle=2026)
    assert detail is not None
    assert detail["summary"]["committee_count"] == 1
    assert detail["summary"]["contribution_count"] == 1
    assert detail["summary"]["total_amount"] == 1250.0
    assert detail["summary"]["reported_total_receipts"] == 1250.0
    assert detail["summary"]["schedule_total_amount"] == 250.0
    assert detail["summary"]["uses_reported_total_receipts"] is True
    assert detail["top_donors"][0]["donor_name"] == "Jane Donor"
    assert detail["top_donors"][0]["donor_entity_key"]
    assert detail["top_donors"][0]["donor_entity_method"] == "name_state_zip"
    assert detail["contributions"][0]["contribution_receipt_amount"] == 250.0

    donor_detail = get_federal_donor_detail(
        conn,
        donor_entity_key=detail["top_donors"][0]["donor_entity_key"],
        cycle=2026,
    )
    assert donor_detail is not None
    assert donor_detail["summary"]["donor_name"] == "Jane Donor"
    assert donor_detail["summary"]["candidate_count"] == 1
    assert donor_detail["summary"]["total_amount"] == 250.0
    assert donor_detail["by_candidate"][0]["candidate_id"] == "H2IL01349"

    raw_count = conn.execute(
        "SELECT COUNT(*) AS count FROM raw_extractions WHERE source_type LIKE 'fec_api:%'"
    ).fetchone()["count"]
    assert raw_count >= 2

    conn.close()


def test_federal_race_analytics_and_network(tmp_path: Path):
    db_path = str(tmp_path / "fec_network.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.executemany(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "seed-1",
                "2026-02-07",
                2026,
                "U.S. House",
                "H",
                "IL-01",
                "01",
                "Democratic",
                "DEM",
                "Primary",
                "Candidate One",
                "CANDIDATE ONE",
                0,
                0,
                "seed.csv",
                2,
            ),
            (
                "seed-2",
                "2026-02-07",
                2026,
                "U.S. House",
                "H",
                "IL-09",
                "09",
                "Republican",
                "REP",
                "Primary",
                "Candidate Two",
                "CANDIDATE TWO",
                0,
                0,
                "seed.csv",
                3,
            ),
        ],
    )

    conn.executemany(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle,
            fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "seed-1",
                "Candidate One",
                "U.S. House",
                "H",
                "IL-01",
                "01",
                "Democratic",
                "DEM",
                "Primary",
                2026,
                "H2IL00001",
                "ONE, CANDIDATE",
                "H",
                "IL",
                "01",
                "DEM",
                "matched",
                97.0,
                "test",
            ),
            (
                "seed-2",
                "Candidate Two",
                "U.S. House",
                "H",
                "IL-09",
                "09",
                "Republican",
                "REP",
                "Primary",
                2026,
                "H2IL00009",
                "TWO, CANDIDATE",
                "H",
                "IL",
                "09",
                "REP",
                "matched",
                96.0,
                "test",
            ),
        ],
    )

    conn.executemany(
        """
        INSERT INTO fec_schedule_a_contributions (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            contributor_name, contributor_city, contributor_state, contributor_zip,
            contributor_employer, contributor_occupation, contributor_id, is_individual,
            line_number, receipt_type, receipt_type_desc, memo_text,
            contribution_receipt_amount, contribution_receipt_date, two_year_transaction_period,
            donor_key, load_date, image_number, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "sub-1",
                2026,
                "H2IL00001",
                "ONE, CANDIDATE",
                "C00000001",
                "COMMITTEE ONE",
                "Jane Donor",
                "Chicago",
                "IL",
                "60601",
                "ACME",
                "Engineer",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                1000.0,
                "2026-01-05",
                2026,
                "legacy-1",
                "2026-01-05",
                "img-1",
                "src-1",
            ),
            (
                "sub-2",
                2026,
                "H2IL00009",
                "TWO, CANDIDATE",
                "C00000009",
                "COMMITTEE TWO",
                "JANE DONOR",
                "CHICAGO",
                "IL",
                "60601",
                "ACME",
                "Engineer",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                500.0,
                "2026-01-09",
                2026,
                "legacy-2",
                "2026-01-09",
                "img-2",
                "src-2",
            ),
            (
                "sub-3",
                2026,
                "H2IL00001",
                "ONE, CANDIDATE",
                "C00000001",
                "COMMITTEE ONE",
                "Alex Donor",
                "Springfield",
                "IL",
                "62701",
                "STATE",
                "Attorney",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                200.0,
                "2026-01-11",
                2026,
                "legacy-3",
                "2026-01-11",
                "img-3",
                "src-3",
            ),
        ],
    )
    conn.commit()

    races = get_federal_race_analytics(conn, cycle=2026, limit=20)
    assert len(races) == 2
    race_one = next(row for row in races if row["district_code"] == "01")
    assert race_one["candidate_count"] == 1
    assert race_one["donor_count"] == 2
    assert race_one["total_amount"] == 1200.0

    network = get_federal_network_graph(conn, cycle=2026, min_edge_amount=0.0, limit=100)
    assert network["summary"]["edge_count"] == 3
    assert network["summary"]["donor_count"] == 2
    assert network["summary"]["candidate_count"] == 2
    candidate_nodes = [row for row in network["nodes"] if row["node_type"] == "candidate"]
    assert candidate_nodes
    assert any(row["race_label"] == "U.S. House - IL-01" for row in candidate_nodes)
    assert any(row["party_display"] == "Democratic" for row in candidate_nodes)
    assert any(edge["party_display"] in {"Democratic", "Republican"} for edge in network["edges"])

    house_01_network = get_federal_network_graph(
        conn,
        cycle=2026,
        office_code="H",
        district_code="01",
        min_edge_amount=0.0,
        limit=100,
    )
    assert house_01_network["summary"]["candidate_count"] == 1
    assert house_01_network["summary"]["edge_count"] == 2

    conn.close()


def test_federal_advanced_analytics(tmp_path: Path):
    """Test segmentation, clustering, influence, tracing, geo concentration, and overlap matching."""
    db_path = str(tmp_path / "fec_advanced.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.executemany(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "seed-a",
                "2026-02-07",
                2026,
                "U.S. House",
                "H",
                "IL-01",
                "01",
                "Democratic",
                "DEM",
                "Primary",
                "Candidate Alpha",
                "CANDIDATE ALPHA",
                0,
                0,
                "seed.csv",
                2,
            ),
            (
                "seed-b",
                "2026-02-07",
                2026,
                "U.S. House",
                "H",
                "IL-09",
                "09",
                "Republican",
                "REP",
                "Primary",
                "Candidate Beta",
                "CANDIDATE BETA",
                0,
                0,
                "seed.csv",
                3,
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle,
            fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "seed-a",
                "Candidate Alpha",
                "U.S. House",
                "H",
                "IL-01",
                "01",
                "Democratic",
                "DEM",
                "Primary",
                2026,
                "H2IL00001",
                "ALPHA, CANDIDATE",
                "H",
                "IL",
                "01",
                "DEM",
                "matched",
                98.0,
                "test",
            ),
            (
                "seed-b",
                "Candidate Beta",
                "U.S. House",
                "H",
                "IL-09",
                "09",
                "Republican",
                "REP",
                "Primary",
                2026,
                "H2IL00009",
                "BETA, CANDIDATE",
                "H",
                "IL",
                "09",
                "REP",
                "matched",
                97.0,
                "test",
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO fec_schedule_a_contributions (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            contributor_name, contributor_city, contributor_state, contributor_zip,
            contributor_employer, contributor_occupation, contributor_id, is_individual,
            line_number, receipt_type, receipt_type_desc, memo_text,
            contribution_receipt_amount, contribution_receipt_date, two_year_transaction_period,
            donor_key, donor_entity_key, donor_entity_method, load_date, image_number, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "sub-a1",
                2026,
                "H2IL00001",
                "ALPHA, CANDIDATE",
                "C111",
                "ALPHA COMMITTEE",
                "Jane Donor",
                "Chicago",
                "IL",
                "60601",
                "ACME",
                "Engineer",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                1000.0,
                "2026-01-05",
                2026,
                "legacy-jane-1",
                "jane_il_60601",
                "name_state_zip",
                "2026-01-05",
                "img-a1",
                "src-a1",
            ),
            (
                "sub-a2",
                2026,
                "H2IL00009",
                "BETA, CANDIDATE",
                "C222",
                "BETA COMMITTEE",
                "JANE DONOR",
                "CHICAGO",
                "IL",
                "60601",
                "ACME",
                "Engineer",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                700.0,
                "2026-01-09",
                2026,
                "legacy-jane-2",
                "jane_il_60601",
                "name_state_zip",
                "2026-01-09",
                "img-a2",
                "src-a2",
            ),
            (
                "sub-a3",
                2026,
                "H2IL00001",
                "ALPHA, CANDIDATE",
                "C111",
                "ALPHA COMMITTEE",
                "Blake Donor",
                "Chicago",
                "IL",
                "60601",
                "STATE",
                "Attorney",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                300.0,
                "2026-01-12",
                2026,
                "legacy-blake-1",
                "blake_il_60601",
                "name_state_zip",
                "2026-01-12",
                "img-a3",
                "src-a3",
            ),
            (
                "sub-a4",
                2026,
                "H2IL00001",
                "ALPHA, CANDIDATE",
                "C111",
                "ALPHA COMMITTEE",
                "Alex Donor",
                "Springfield",
                "IL",
                "62701",
                "STATE",
                "Attorney",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                200.0,
                "2026-01-15",
                2026,
                "legacy-alex-1",
                "alex_il_62701",
                "name_state_zip",
                "2026-01-15",
                "img-a4",
                "src-a4",
            ),
            (
                "sub-a5",
                2026,
                "H2IL00009",
                "BETA, CANDIDATE",
                "C222",
                "BETA COMMITTEE",
                "Nora Donor",
                "San Francisco",
                "CA",
                "94105",
                "PACIFIC",
                "Manager",
                None,
                1,
                "11AI",
                "IND",
                "Individual contribution",
                "",
                500.0,
                "2026-01-18",
                2026,
                "legacy-nora-1",
                "nora_ca_94105",
                "name_state_zip",
                "2026-01-18",
                "img-a5",
                "src-a5",
            ),
        ],
    )

    conn.executemany(
        """
        INSERT INTO analytics_donor_summary (
            source, donor_key, local_donor_id, donor_name, donor_address, donor_city, donor_state,
            occupation, employer, total_amount, contribution_count, committee_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "bulk_receipts",
                "local-jane",
                None,
                "Jane Donor",
                "10 Main St, Chicago, IL 60601",
                "Chicago",
                "IL",
                "Engineer",
                "ACME",
                5000.0,
                12,
                2,
            ),
            (
                "bulk_receipts",
                "local-alex",
                None,
                "Alex Donor",
                "20 Oak St, Springfield, IL 62701",
                "Springfield",
                "IL",
                "Attorney",
                "State",
                1500.0,
                6,
                1,
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO analytics_donor_committee_agg (
            source, donor_key, donor_name, donor_address, donor_city, donor_state,
            occupation, employer, committee_id, committee_name, total_amount, contribution_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "bulk_receipts",
                "local-jane",
                "Jane Donor",
                "10 Main St, Chicago, IL 60601",
                "Chicago",
                "IL",
                "Engineer",
                "ACME",
                "L001",
                "CITY ACTION FUND",
                1100.0,
                3,
            ),
            (
                "bulk_receipts",
                "local-alex",
                "Alex Donor",
                "20 Oak St, Springfield, IL 62701",
                "Springfield",
                "IL",
                "Attorney",
                "State",
                "L002",
                "DOWNSTATE REFORM PAC",
                450.0,
                2,
            ),
        ],
    )
    conn.commit()

    segmentation = get_federal_donor_segmentation(
        conn,
        cycle=2026,
        method="kmeans",
        donor_limit=1000,
        kmeans_k=3,
    )
    assert segmentation["method"] == "kmeans"
    assert segmentation["donor_count"] == 4
    assert segmentation["cluster_count"] >= 1
    assert sum(cluster["donor_count"] for cluster in segmentation["clusters"]) == segmentation["donor_count"]

    segmentation_dbscan = get_federal_donor_segmentation(
        conn,
        cycle=2026,
        method="dbscan",
        donor_limit=1000,
        dbscan_eps=2.5,
        dbscan_min_samples=2,
    )
    assert segmentation_dbscan["method"] == "dbscan"
    assert segmentation_dbscan["donor_count"] == 4

    network_clusters = get_federal_donor_network_clusters(
        conn,
        cycle=2026,
        min_edge_amount=0.0,
        limit=200,
    )
    assert network_clusters["cluster_count"] >= 1
    assert network_clusters["clusters"][0]["edge_count"] >= 1

    influence = get_federal_influence_scores(
        conn,
        cycle=2026,
        min_edge_amount=0.0,
        limit=200,
    )
    assert influence["donors"]
    assert influence["candidates"]
    assert influence["donors"][0]["influence_score"] >= influence["donors"][-1]["influence_score"]
    assert influence["candidates"][0]["influence_score"] >= influence["candidates"][-1]["influence_score"]

    follow = get_federal_follow_the_money(
        conn,
        donor_entity_key="jane_il_60601",
        cycle=2026,
        max_hops=3,
        min_edge_amount=0.0,
    )
    assert follow["start_node_found"] is True
    assert any(row["candidate_id"] == "H2IL00001" for row in follow["reachable_candidates"])
    assert any(row["candidate_id"] == "H2IL00009" for row in follow["reachable_candidates"])
    assert follow["summary"]["connected_donor_count"] >= 2
    assert any(edge["edge_type"] == "donor_committee" for edge in follow["edges"])
    assert any(edge["edge_type"] == "committee_candidate" for edge in follow["edges"])

    geo = get_federal_geographic_concentration(conn, cycle=2026, limit_states=10, limit_cities=10, limit_races=10)
    chicago_rows = [row for row in geo["cities"] if row["city"] == "Chicago" and row["state"] == "IL"]
    assert len(chicago_rows) == 1
    assert chicago_rows[0]["total_amount"] == 2000.0
    assert any(row["state"] == "IL" for row in geo["states"])

    matches = get_federal_local_donor_matches(
        conn,
        cycle=2026,
        federal_donor_limit=1000,
        local_donor_limit=10000,
        match_limit=500,
    )
    assert matches["federal_donors_considered"] == 4
    assert matches["federal_donors_matched"] >= 2
    assert any(row["match_method"] == "name_state_zip" for row in matches["matches"])
    assert any(
        row["federal_donor_entity_key"] == "jane_il_60601" and row["local_donor_key"] == "local-jane"
        for row in matches["matches"]
    )

    persisted = refresh_fec_local_donor_matches(
        conn,
        cycle=2026,
        federal_donor_limit=1000,
        local_donor_limit=10000,
        match_limit=500,
    )
    assert persisted["rows_written"] >= 2
    assert persisted["federal_donors_matched"] >= 2
    assert persisted["local_donors_matched"] >= 2
    persisted_count = conn.execute(
        "SELECT COUNT(*) AS count FROM fec_local_donor_matches"
    ).fetchone()["count"]
    assert persisted_count == persisted["rows_written"]

    overlap = get_federal_local_overlap_network(
        conn,
        cycle=2026,
        min_edge_amount=0.0,
        edge_limit=400,
        federal_donor_limit=1000,
        local_donor_limit=10000,
    )
    assert overlap["summary"]["matched_donors"] >= 2
    assert overlap["summary"]["federal_candidate_count"] >= 1
    assert overlap["summary"]["local_committee_count"] >= 1
    edge_types = {edge["edge_type"] for edge in overlap["edges"]}
    assert "donor_federal_candidate" in edge_types
    assert "donor_local_committee" in edge_types
    assert any(node["node_type"] == "matched_donor" for node in overlap["nodes"])
    overlap_candidates = [node for node in overlap["nodes"] if node["node_type"] == "federal_candidate"]
    assert overlap_candidates
    assert any(node["race_label"] == "U.S. House - IL-01" for node in overlap_candidates)
    assert any(node["party_display"] in {"Democratic", "Republican"} for node in overlap_candidates)

    conn.close()
