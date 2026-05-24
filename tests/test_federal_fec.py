"""Tests for FEC federal ingestion and query helpers."""
from pathlib import Path

import database.federal_fec as federal_fec_module

from database.connection import get_db, init_db
from database.federal_fec import (
    backfill_fec_missing_schedule_a,
    backfill_fec_schedule_b,
    backfill_fec_schedule_e,
    count_federal_candidates,
    federal_data_available,
    get_federal_disbursement_mismatch_flags,
    get_federal_receipt_mismatch_flags,
    get_federal_candidate_detail,
    get_federal_committee_receipts,
    get_federal_donor_detail,
    get_federal_donor_network_clusters,
    get_federal_donor_segmentation,
    get_federal_follow_the_money,
    get_federal_geo_drilldown,
    get_federal_geographic_concentration,
    get_federal_influence_scores,
    get_federal_multilayer_network_graph,
    get_federal_cross_role_organizations,
    get_federal_local_donor_matches,
    get_federal_local_overlap_network,
    get_federal_network_graph,
    get_federal_race_analytics,
    get_top_donor_entities,
    list_federal_candidates,
    refresh_fec_transfer_source_committees,
    refresh_fec_local_donor_matches,
    sync_fec_transfer_committee_receipts,
    sync_il_federal_fec,
)


class _CaptureExecuteConn:
    def __init__(self):
        self.sql = ""
        self.params = ()
        self.sql_history: list[str] = []

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.sql_history.append(sql)
        return self

    def fetchall(self):
        return []


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


class _StaticResult:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _OutsideSpendingCaptureConn:
    def __init__(self):
        self.sql_history: list[str] = []

    def execute(self, sql, params=()):
        self.sql_history.append(sql)
        normalized = " ".join(sql.split()).lower()
        if "from fec_candidate_match m" in normalized:
            return _StaticResult(
                rows=[
                    {
                        "candidate_id": "H2IL00001",
                        "candidate_name": "Candidate One",
                        "office_code": "H",
                        "fec_office": "H",
                        "office": "U.S. House",
                        "district_code": "01",
                        "fec_district": "01",
                        "district": "01",
                    }
                ]
            )
        if "count(*) as transaction_count" in normalized and "fec_schedule_e_independent_expenditures" in normalized:
            return _StaticResult(
                row={"transaction_count": 0, "total_amount": 0.0, "latest_expenditure_date": None}
            )
        if "group by se.committee_id" in normalized:
            return _StaticResult(rows=[])
        if "group by payee_name, payee_state" in normalized:
            return _StaticResult(rows=[])
        if "select se.sub_id" in normalized:
            return _StaticResult(rows=[])
        if "select count(*) as count" in normalized and "fec_schedule_e_independent_expenditures" in normalized:
            return _StaticResult(row={"count": 0})
        return _StaticResult(rows=[], row=None)


def test_federal_edge_rows_groups_by_fallback_expression_not_alias_only():
    conn = _CaptureExecuteConn()
    federal_fec_module._federal_edge_rows(conn, cycle=2026)
    normalized = _normalize_sql(conn.sql)
    assert "GROUP BY COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id), sa.candidate_id" in normalized


def test_federal_donor_committee_edges_groups_by_fallback_expression_not_alias_only():
    conn = _CaptureExecuteConn()
    federal_fec_module._federal_donor_committee_edges(conn, cycle=2026)
    normalized = _normalize_sql(conn.sql)
    assert "GROUP BY COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id), sa.committee_id" in normalized


def test_federal_network_graph_groups_by_fallback_expression_not_alias_only(monkeypatch):
    conn = _CaptureExecuteConn()
    monkeypatch.setattr(federal_fec_module, "_ensure_missing_donor_identities", lambda _conn, cycle=None: None)
    federal_fec_module.get_federal_network_graph(conn, cycle=2026, min_edge_amount=0.0, limit=10)
    normalized = _normalize_sql(conn.sql)
    assert "GROUP BY COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id), sa.candidate_id" in normalized


def test_race_outside_spending_uses_string_agg_for_postgres(monkeypatch):
    class PostgresCompatConnection(_OutsideSpendingCaptureConn):
        pass

    conn = PostgresCompatConnection()
    monkeypatch.setattr(federal_fec_module, "_table_exists", lambda _conn, _table: True)

    federal_fec_module.get_federal_race_outside_spending(
        conn,
        cycle=2026,
        office_code="H",
        district_code="01",
        limit=25,
        aggregate_limit=10,
    )

    rendered_sql = "\n".join(conn.sql_history)
    assert "STRING_AGG(" in rendered_sql


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
                        "disbursements": 550.0,
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
    assert detail["summary"]["reported_total_disbursements"] == 550.0
    assert detail["summary"]["schedule_total_amount"] == 250.0
    assert detail["summary"]["money_in_total"] == 1250.0
    assert detail["summary"]["money_in_source"] == "reported_receipts"
    assert detail["summary"]["money_out_total"] == 550.0
    assert detail["summary"]["money_out_source"] == "reported_disbursements"
    assert detail["summary"]["outside_spending_total"] == 0.0
    assert detail["summary"]["outside_pressure_ratio"] == 0.0
    assert detail["summary"]["net_money_flow"] == 700.0
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


def test_transfer_committee_registry_and_receipts_sync(tmp_path: Path):
    db_path = str(tmp_path / "fec_transfer_committee.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.execute(
        """
        INSERT INTO fec_schedule_b_disbursements (
            sub_id,
            cycle,
            candidate_id,
            candidate_name,
            committee_id,
            committee_name,
            recipient_name,
            recipient_candidate_id,
            disbursement_amount,
            disbursement_date,
            api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sb-transfer-1",
            2026,
            "H2IL01349",
            "JACKSON, JONATHAN",
            "C00011111",
            "JONATHAN JACKSON FOR CONGRESS",
            "FRIENDS OF TARGET",
            "H2IL05555",
            7500.0,
            "2026-02-01",
            "seed-sb-transfer",
        ),
    )
    conn.commit()

    refresh_stats = refresh_fec_transfer_source_committees(conn, cycle=2026)
    assert refresh_stats["rows_written"] == 1
    row = conn.execute(
        """
        SELECT committee_id, transfer_count, transfer_total_amount
        FROM fec_transfer_source_committees
        WHERE cycle = 2026 AND committee_id = 'C00011111'
        """
    ).fetchone()
    assert row is not None
    assert int(row["transfer_count"] or 0) == 1
    assert float(row["transfer_total_amount"] or 0.0) == 7500.0

    sync_stats = sync_fec_transfer_committee_receipts(
        conn,
        api_key="fake-key",
        cycle=2026,
        max_calls=20,
        per_page=100,
        max_pages_per_committee=5,
        include_completed=False,
        refresh_cache=False,
        refresh_registry=False,
        client=FakeFecClient(),
    )
    assert sync_stats["transfer_committees_selected"] == 1
    assert sync_stats["transfer_committees_processed"] == 1
    assert sync_stats["contributions_upserted"] == 1

    transfer_row = conn.execute(
        """
        SELECT receipts_synced, receipts_row_count, receipts_total_amount
        FROM fec_transfer_source_committees
        WHERE cycle = 2026 AND committee_id = 'C00011111'
        """
    ).fetchone()
    assert transfer_row is not None
    assert int(transfer_row["receipts_synced"] or 0) == 1
    assert int(transfer_row["receipts_row_count"] or 0) == 1
    assert float(transfer_row["receipts_total_amount"] or 0.0) == 250.0

    committee_detail = get_federal_committee_receipts(
        conn,
        committee_id="C00011111",
        cycle=2026,
        receipt_limit=50,
        receipt_offset=0,
        receipt_sort="date",
        receipt_dir="desc",
    )
    assert committee_detail is not None
    assert committee_detail["summary"]["committee_id"] == "C00011111"
    assert committee_detail["summary"]["receipt_count"] == 1
    assert committee_detail["summary"]["total_amount"] == 250.0
    assert committee_detail["receipts"][0]["contributor_name"] == "Jane Donor"
    assert committee_detail["transfer_meta"] is not None
    assert committee_detail["transfer_meta"]["transfer_total_amount"] == 7500.0

    conn.close()


def test_federal_receipt_mismatch_flags_and_backfill(tmp_path: Path):
    db_path = str(tmp_path / "fec_backfill.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-backfill-1",
            "2026-02-07",
            2026,
            "U.S. House",
            "H",
            "IL-07",
            "07",
            "Democratic",
            "DEM",
            "Primary",
            "Candidate Gap",
            "CANDIDATE GAP",
            0,
            0,
            "seed.csv",
            2,
        ),
    )

    conn.execute(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle,
            fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-backfill-1",
            "Candidate Gap",
            "U.S. House",
            "H",
            "IL-07",
            "07",
            "Democratic",
            "DEM",
            "Primary",
            2026,
            "H2IL77777",
            "GAP, CANDIDATE",
            "H",
            "IL",
            "07",
            "DEM",
            "matched",
            95.0,
            "test",
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_committees (
            candidate_id, committee_id, cycle, committee_name, committee_type,
            committee_designation, committee_designation_full, filing_frequency,
            committee_party, committee_city, committee_state, committee_zip, is_principal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "H2IL77777",
            "C00999999",
            2026,
            "CANDIDATE GAP COMMITTEE",
            "H",
            "P",
            "Principal campaign committee",
            "Q",
            "DEM",
            "CHICAGO",
            "IL",
            "60601",
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_cycle_totals (
            candidate_id, cycle, receipts, contributions, individual_contributions,
            coverage_start_date, coverage_end_date, transaction_coverage_date, last_report_year,
            last_report_type_full, last_cash_on_hand_end_period, source_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "H2IL77777",
            2026,
            1000.0,
            1000.0,
            900.0,
            "2026-01-01",
            "2026-03-31",
            "2026-03-31",
            2026,
            "Q1",
            2500.0,
            '{"source":"test"}',
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_schedule_a_contributions (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            contributor_name, contributor_city, contributor_state, contributor_zip,
            contributor_employer, contributor_occupation, contributor_id,
            is_individual, line_number, receipt_type, receipt_type_desc, memo_text,
            contribution_receipt_amount, contribution_receipt_date, two_year_transaction_period,
            donor_key, donor_entity_key, donor_entity_method, load_date, image_number, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sub-gap-existing",
            2026,
            "H2IL77777",
            "GAP, CANDIDATE",
            "C00999999",
            "CANDIDATE GAP COMMITTEE",
            "Existing Donor",
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
            100.0,
            "2026-01-15",
            2026,
            "existing-donor-key",
            "existing_donor_il_60601",
            "name_state_zip",
            "2026-01-16",
            "img-existing",
            "src-existing",
        ),
    )
    conn.commit()

    flags = get_federal_receipt_mismatch_flags(
        conn,
        cycle=2026,
        status="missing_schedule_rows",
        min_abs_diff=1.0,
        tolerance=0.01,
        limit=20,
    )
    assert flags["total_rows"] == 1
    assert flags["rows"][0]["candidate_id"] == "H2IL77777"
    assert flags["rows"][0]["flag_status"] == "missing_schedule_rows"
    assert flags["rows"][0]["reported_total_receipts"] == 1000.0
    assert flags["rows"][0]["schedule_total_amount"] == 100.0

    class BackfillClient:
        base_url = "https://api.open.fec.gov/v1"

        def __init__(self):
            self.calls = []

        def request(self, endpoint: str, params: dict):
            self.calls.append((endpoint, dict(params)))
            assert endpoint == "/schedules/schedule_a/"
            if params.get("committee_id") != "C00999999":
                return {"pagination": {"count": 0, "pages": 1, "per_page": params.get("per_page", 100)}, "results": []}

            if params.get("last_index") == "token-1":
                return {
                    "pagination": {"count": 1, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            return {
                "pagination": {
                    "count": 1,
                    "pages": 1,
                    "per_page": params.get("per_page", 100),
                    "last_indexes": {
                        "last_index": "token-1",
                        "last_contribution_receipt_date": "2025-11-30",
                    },
                },
                "results": [
                    {
                        "sub_id": "sub-gap-new-1",
                        "committee_id": "C00999999",
                        "committee_name": "CANDIDATE GAP COMMITTEE",
                        "contribution_receipt_amount": 300.0,
                        "contribution_receipt_date": "2025-12-01",
                        "contributor_name": "Backfill Donor",
                        "contributor_city": "Chicago",
                        "contributor_state": "IL",
                        "contributor_zip": "60602",
                        "contributor_employer": "STATE",
                        "contributor_occupation": "Attorney",
                        "is_individual": True,
                        "line_number": "11AI",
                        "receipt_type": "IND",
                        "receipt_type_desc": "Individual contribution",
                        "memo_text": "",
                        "two_year_transaction_period": 2026,
                        "load_date": "2026-02-13T10:00:00",
                        "image_number": "img-gap-new",
                    }
                ],
            }

    client = BackfillClient()

    run_one = backfill_fec_missing_schedule_a(
        conn,
        api_key="fake-key",
        cycle=2026,
        max_calls=1,
        per_page=100,
        max_pages_per_committee=1,
        min_abs_gap=1.0,
        tolerance=0.01,
        include_completed=False,
        refresh_cache=True,
        client=client,
    )
    assert run_one["api_calls_made"] == 1
    assert run_one["contributions_upserted"] == 1
    assert run_one["committees_processed"] >= 1
    state_row = conn.execute(
        """
        SELECT next_last_index, completed
        FROM fec_schedule_a_backfill_state
        WHERE committee_id = 'C00999999' AND cycle = 2026
        """
    ).fetchone()
    assert state_row is not None
    assert state_row["next_last_index"] == "token-1"
    assert state_row["completed"] == 0

    run_two = backfill_fec_missing_schedule_a(
        conn,
        api_key="fake-key",
        cycle=2026,
        max_calls=1,
        per_page=100,
        max_pages_per_committee=1,
        min_abs_gap=1.0,
        tolerance=0.01,
        include_completed=False,
        refresh_cache=True,
        client=client,
    )
    assert run_two["api_calls_made"] == 1
    final_state = conn.execute(
        """
        SELECT next_last_index, completed
        FROM fec_schedule_a_backfill_state
        WHERE committee_id = 'C00999999' AND cycle = 2026
        """
    ).fetchone()
    assert final_state is not None
    assert final_state["next_last_index"] is None
    assert final_state["completed"] == 1

    conn.close()


def test_backfill_fec_schedule_b(tmp_path: Path):
    db_path = str(tmp_path / "fec_schedule_b.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-sb-1",
            "2026-02-07",
            2026,
            "U.S. House",
            "H",
            "IL-06",
            "06",
            "Democratic",
            "DEM",
            "Primary",
            "Candidate Spend",
            "CANDIDATE SPEND",
            0,
            0,
            "seed.csv",
            2,
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle,
            fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-sb-1",
            "Candidate Spend",
            "U.S. House",
            "H",
            "IL-06",
            "06",
            "Democratic",
            "DEM",
            "Primary",
            2026,
            "H2IL66666",
            "SPEND, CANDIDATE",
            "H",
            "IL",
            "06",
            "DEM",
            "matched",
            98.0,
            "test",
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_committees (
            candidate_id, committee_id, cycle, committee_name, committee_type,
            committee_designation, committee_designation_full, filing_frequency,
            committee_party, committee_city, committee_state, committee_zip, is_principal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "H2IL66666",
            "C00888888",
            2026,
            "CANDIDATE SPEND COMMITTEE",
            "H",
            "P",
            "Principal campaign committee",
            "Q",
            "DEM",
            "CHICAGO",
            "IL",
            "60606",
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_cycle_totals (
            candidate_id, cycle, receipts, disbursements, contributions, individual_contributions,
            coverage_start_date, coverage_end_date, transaction_coverage_date,
            last_report_year, last_report_type_full, last_cash_on_hand_end_period, source_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "H2IL66666",
            2026,
            3000.0,
            2500.0,
            3000.0,
            2800.0,
            "2026-01-01",
            "2026-03-31",
            "2026-03-31",
            2026,
            "Q1",
            4000.0,
            '{"source":"test"}',
        ),
    )
    conn.commit()

    class ScheduleBClient:
        base_url = "https://api.open.fec.gov/v1"

        def __init__(self):
            self.calls = []

        def request(self, endpoint: str, params: dict):
            self.calls.append((endpoint, dict(params)))
            assert endpoint == "/schedules/schedule_b/"
            if params.get("committee_id") != "C00888888":
                return {
                    "pagination": {"count": 0, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            if params.get("last_index") == "sb-token-1":
                return {
                    "pagination": {"count": 1, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            return {
                "pagination": {
                    "count": 1,
                    "pages": 1,
                    "per_page": params.get("per_page", 100),
                    "last_indexes": {
                        "last_index": "sb-token-1",
                        "last_disbursement_date": "2025-11-30",
                    },
                },
                "results": [
                    {
                        "sub_id": "sub-b-new-1",
                        "committee_id": "C00888888",
                        "committee_name": "CANDIDATE SPEND COMMITTEE",
                        "recipient_name": "MEDIA BUY VENDOR LLC",
                        "recipient_city": "CHICAGO",
                        "recipient_state": "IL",
                        "recipient_zip": "60601",
                        "candidate_id": "H0IL00000",
                        "candidate_name": "BENEFICIARY, CANDIDATE",
                        "payee_employer": "N/A",
                        "payee_occupation": "N/A",
                        "line_number": "23",
                        "disbursement_type": "DISB",
                        "disbursement_type_description": "Operating Expenditure",
                        "category_code": "004",
                        "category_code_full": "Advertising Expenses",
                        "election_type": "G",
                        "election_type_full": "GENERAL",
                        "disbursement_description": "Digital advertising",
                        "memo_text": "",
                        "disbursement_amount": 1250.75,
                        "disbursement_date": "2025-12-01",
                        "two_year_transaction_period": 2026,
                        "load_date": "2026-02-13T11:00:00",
                        "image_number": "img-b-new",
                    }
                ],
            }

    client = ScheduleBClient()

    run_one = backfill_fec_schedule_b(
        conn,
        api_key="fake-key",
        cycle=2026,
        max_calls=1,
        per_page=100,
        max_pages_per_committee=1,
        include_completed=False,
        refresh_cache=True,
        include_all_committees=True,
        client=client,
    )
    assert run_one["api_calls_made"] == 1
    assert run_one["disbursements_upserted"] == 1
    assert run_one["committees_processed"] >= 1

    inserted = conn.execute(
        """
        SELECT
            candidate_id,
            committee_id,
            recipient_name,
            recipient_candidate_id,
            disbursement_amount,
            disbursement_date
        FROM fec_schedule_b_disbursements
        WHERE sub_id = 'sub-b-new-1'
        """
    ).fetchone()
    assert inserted is not None
    assert inserted["candidate_id"] == "H2IL66666"
    assert inserted["committee_id"] == "C00888888"
    assert inserted["recipient_name"] == "MEDIA BUY VENDOR LLC"
    assert inserted["recipient_candidate_id"] == "H0IL00000"
    assert inserted["disbursement_amount"] == 1250.75
    assert inserted["disbursement_date"] == "2025-12-01"

    detail = get_federal_candidate_detail(conn, candidate_id="H2IL66666", cycle=2026)
    assert detail is not None
    assert detail["summary"]["reported_total_disbursements"] == 2500.0
    assert detail["summary"]["schedule_b_total_amount"] == 1250.75
    assert detail["summary"]["schedule_b_disbursement_count"] == 1
    assert detail["total_schedule_b_disbursements"] == 1
    assert detail["schedule_b_transfer_chains"]
    assert detail["schedule_b_transfer_chains"][0]["recipient_candidate_id"] == "H0IL00000"
    assert detail["schedule_b_transfer_chains"][0]["match_type"] == "candidate_id"

    state_one = conn.execute(
        """
        SELECT next_last_index, completed
        FROM fec_schedule_b_backfill_state
        WHERE committee_id = 'C00888888' AND cycle = 2026
        """
    ).fetchone()
    assert state_one is not None
    assert state_one["next_last_index"] == "sb-token-1"
    assert state_one["completed"] == 0

    run_two = backfill_fec_schedule_b(
        conn,
        api_key="fake-key",
        cycle=2026,
        max_calls=1,
        per_page=100,
        max_pages_per_committee=1,
        include_completed=False,
        refresh_cache=True,
        include_all_committees=True,
        client=client,
    )
    assert run_two["api_calls_made"] == 1

    state_two = conn.execute(
        """
        SELECT next_last_index, completed
        FROM fec_schedule_b_backfill_state
        WHERE committee_id = 'C00888888' AND cycle = 2026
        """
    ).fetchone()
    assert state_two is not None
    assert state_two["next_last_index"] is None
    assert state_two["completed"] == 1

    conn.close()


def test_federal_disbursement_mismatch_flags(tmp_path: Path):
    db_path = str(tmp_path / "fec_disbursement_audit.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-disb-audit-1",
            "2026-02-07",
            2026,
            "U.S. House",
            "H",
            "IL-08",
            "08",
            "Democratic",
            "DEM",
            "Primary",
            "Candidate Spend Gap",
            "CANDIDATE SPEND GAP",
            0,
            0,
            "seed.csv",
            2,
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle,
            fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-disb-audit-1",
            "Candidate Spend Gap",
            "U.S. House",
            "H",
            "IL-08",
            "08",
            "Democratic",
            "DEM",
            "Primary",
            2026,
            "H2IL08888",
            "SPEND GAP, CANDIDATE",
            "H",
            "IL",
            "08",
            "DEM",
            "matched",
            96.0,
            "test",
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_cycle_totals (
            candidate_id, cycle, receipts, disbursements, contributions, individual_contributions,
            coverage_start_date, coverage_end_date, transaction_coverage_date,
            last_report_year, last_report_type_full, last_cash_on_hand_end_period, source_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "H2IL08888",
            2026,
            8000.0,
            5000.0,
            8000.0,
            7800.0,
            "2026-01-01",
            "2026-03-31",
            "2026-03-31",
            2026,
            "Q1",
            12000.0,
            '{"source":"test"}',
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_schedule_b_disbursements (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            recipient_name, disbursement_amount, disbursement_date, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sub-disb-audit-1",
            2026,
            "H2IL08888",
            "SPEND GAP, CANDIDATE",
            "C00888888",
            "SPEND GAP COMMITTEE",
            "MEDIA BUY VENDOR",
            1200.0,
            "2026-01-15",
            "src-disb-audit",
        ),
    )
    conn.commit()

    flags = get_federal_disbursement_mismatch_flags(
        conn,
        cycle=2026,
        status="flagged",
        min_abs_diff=1.0,
        tolerance=0.01,
        limit=20,
    )
    assert flags["total_rows"] == 1
    assert flags["status_counts"]["missing_schedule_b_rows"] == 1
    assert flags["rows"][0]["candidate_id"] == "H2IL08888"
    assert flags["rows"][0]["flag_status"] == "missing_schedule_b_rows"
    assert flags["rows"][0]["reported_total_disbursements"] == 5000.0
    assert flags["rows"][0]["schedule_total_amount"] == 1200.0

    conn.close()


def test_backfill_fec_schedule_e(tmp_path: Path):
    db_path = str(tmp_path / "fec_schedule_e.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-se-1",
            "2026-02-07",
            2026,
            "U.S. House",
            "H",
            "IL-05",
            "05",
            "Democratic",
            "DEM",
            "Primary",
            "Candidate IE",
            "CANDIDATE IE",
            0,
            0,
            "seed.csv",
            2,
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle,
            fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-se-1",
            "Candidate IE",
            "U.S. House",
            "H",
            "IL-05",
            "05",
            "Democratic",
            "DEM",
            "Primary",
            2026,
            "H2IL05555",
            "IE, CANDIDATE",
            "H",
            "IL",
            "05",
            "DEM",
            "matched",
            95.0,
            "test",
        ),
    )
    conn.commit()

    class ScheduleEClient:
        base_url = "https://api.open.fec.gov/v1"

        def __init__(self):
            self.calls = []

        def request(self, endpoint: str, params: dict):
            self.calls.append((endpoint, dict(params)))
            assert endpoint == "/schedules/schedule_e/"
            if params.get("candidate_id") != "H2IL05555":
                return {
                    "pagination": {"count": 0, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            if params.get("last_index") == "se-token-1":
                return {
                    "pagination": {"count": 1, "pages": 1, "per_page": params.get("per_page", 100)},
                    "results": [],
                }

            return {
                "pagination": {
                    "count": 1,
                    "pages": 1,
                    "per_page": params.get("per_page", 100),
                    "last_indexes": {
                        "last_index": "se-token-1",
                        "last_expenditure_date": "2025-12-15",
                    },
                },
                "results": [
                    {
                        "sub_id": "sub-e-new-1",
                        "candidate_id": "H2IL05555",
                        "candidate_name": "IE, CANDIDATE",
                        "committee_id": "C00555555",
                        "committee_name": "IE COMMITTEE",
                        "payee_name": "COMMUNICATIONS VENDOR LLC",
                        "payee_city": "CHICAGO",
                        "payee_state": "IL",
                        "payee_zip": "60605",
                        "support_oppose_indicator": "S",
                        "category_code": "001",
                        "category_code_full": "Communications",
                        "expenditure_description": "Digital media buy",
                        "expenditure_amount": 980.25,
                        "expenditure_date": "2025-12-31",
                        "filing_date": "2026-01-02",
                        "report_type": "48H3",
                        "line_number": "24A",
                        "image_number": "img-e-new",
                        "load_date": "2026-02-13T12:00:00",
                    }
                ],
            }

    client = ScheduleEClient()

    run_one = backfill_fec_schedule_e(
        conn,
        api_key="fake-key",
        cycle=2026,
        max_calls=1,
        per_page=100,
        max_pages_per_candidate=1,
        include_completed=False,
        refresh_cache=True,
        client=client,
    )
    assert run_one["api_calls_made"] == 1
    assert run_one["expenditures_upserted"] == 1
    assert run_one["candidates_processed"] >= 1

    inserted = conn.execute(
        """
        SELECT
            candidate_id,
            committee_id,
            payee_name,
            support_oppose_indicator,
            expenditure_amount,
            expenditure_date
        FROM fec_schedule_e_independent_expenditures
        WHERE sub_id = 'sub-e-new-1'
        """
    ).fetchone()
    assert inserted is not None
    assert inserted["candidate_id"] == "H2IL05555"
    assert inserted["committee_id"] == "C00555555"
    assert inserted["payee_name"] == "COMMUNICATIONS VENDOR LLC"
    assert inserted["support_oppose_indicator"] == "S"
    assert inserted["expenditure_amount"] == 980.25
    assert inserted["expenditure_date"] == "2025-12-31"

    state_one = conn.execute(
        """
        SELECT next_last_index, completed
        FROM fec_schedule_e_backfill_state
        WHERE candidate_id = 'H2IL05555' AND cycle = 2026
        """
    ).fetchone()
    assert state_one is not None
    assert state_one["next_last_index"] == "se-token-1"
    assert state_one["completed"] == 0

    run_two = backfill_fec_schedule_e(
        conn,
        api_key="fake-key",
        cycle=2026,
        max_calls=1,
        per_page=100,
        max_pages_per_candidate=1,
        include_completed=False,
        refresh_cache=True,
        client=client,
    )
    assert run_two["api_calls_made"] == 1

    state_two = conn.execute(
        """
        SELECT next_last_index, completed
        FROM fec_schedule_e_backfill_state
        WHERE candidate_id = 'H2IL05555' AND cycle = 2026
        """
    ).fetchone()
    assert state_two is not None
    assert state_two["next_last_index"] is None
    assert state_two["completed"] == 1

    detail = get_federal_candidate_detail(conn, candidate_id="H2IL05555", cycle=2026)
    assert detail is not None
    assert detail["summary"]["schedule_e_total_amount"] == 980.25
    assert detail["summary"]["schedule_e_expenditure_count"] == 1
    assert detail["total_schedule_e_expenditures"] == 1

    conn.close()


def test_federal_candidate_detail_schedule_b_e_sorting_and_cross_role(tmp_path: Path):
    db_path = str(tmp_path / "fec_candidate_detail_sorting.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-sort-1",
            "2026-02-07",
            2026,
            "U.S. House",
            "H",
            "IL-04",
            "04",
            "Democratic",
            "DEM",
            "Primary",
            "Candidate Sorting",
            "CANDIDATE SORTING",
            0,
            0,
            "seed.csv",
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "seed-sort-2",
            "2026-02-07",
            2026,
            "U.S. House",
            "H",
            "IL-08",
            "08",
            "Republican",
            "REP",
            "Primary",
            "Recipient Candidate",
            "RECIPIENT CANDIDATE",
            0,
            0,
            "seed.csv",
            2,
        ),
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
                "seed-sort-1",
                "Candidate Sorting",
                "U.S. House",
                "H",
                "IL-04",
                "04",
                "Democratic",
                "DEM",
                "Primary",
                2026,
                "H2IL04444",
                "SORTING, CANDIDATE",
                "H",
                "IL",
                "04",
                "DEM",
                "matched",
                95.0,
                "test",
            ),
            (
                "seed-sort-2",
                "Recipient Candidate",
                "U.S. House",
                "H",
                "IL-08",
                "08",
                "Republican",
                "REP",
                "Primary",
                2026,
                "H2IL08880",
                "RECIPIENT, CANDIDATE",
                "H",
                "IL",
                "08",
                "REP",
                "matched",
                90.0,
                "test",
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO fec_candidate_committees (
            candidate_id, committee_id, cycle, committee_name, committee_type,
            committee_designation, committee_designation_full, filing_frequency,
            committee_party, committee_city, committee_state, committee_zip, is_principal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "H2IL04444",
                "C00444444",
                2026,
                "SORTING COMMITTEE",
                "H",
                "P",
                "Principal campaign committee",
                "Q",
                "DEM",
                "CHICAGO",
                "IL",
                "60601",
                1,
            ),
            (
                "H2IL08880",
                "C00888880",
                2026,
                "RECIPIENT COMMITTEE",
                "H",
                "P",
                "Principal campaign committee",
                "Q",
                "REP",
                "CHICAGO",
                "IL",
                "60602",
                1,
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
                "sort-a-1",
                2026,
                "H2IL04444",
                "SORTING, CANDIDATE",
                "C00444444",
                "SORTING COMMITTEE",
                "DUAL ORG LLC",
                "CHICAGO",
                "IL",
                "60601",
                "",
                "",
                None,
                0,
                "11AI",
                "PAC",
                "Committee contribution",
                "",
                600.0,
                "2026-01-05",
                2026,
                "legacy-sort-1",
                "dual org llc|IL|60601",
                "name_state_zip",
                "2026-01-05",
                "img-sort-1",
                "src-sort-1",
            ),
            (
                "sort-a-2",
                2026,
                "H2IL04444",
                "SORTING, CANDIDATE",
                "C00444444",
                "SORTING COMMITTEE",
                "OTHER DONOR",
                "CHICAGO",
                "IL",
                "60603",
                "",
                "",
                None,
                0,
                "11AI",
                "PAC",
                "Committee contribution",
                "",
                200.0,
                "2026-01-07",
                2026,
                "legacy-sort-2",
                "other donor|IL|60603",
                "name_state_zip",
                "2026-01-07",
                "img-sort-2",
                "src-sort-2",
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO fec_schedule_b_disbursements (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            recipient_name, recipient_city, recipient_state, recipient_zip,
            recipient_committee_id, recipient_candidate_id, recipient_candidate_name,
            disbursement_amount, disbursement_date, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "sort-b-1",
                2026,
                "H2IL04444",
                "SORTING, CANDIDATE",
                "C00444444",
                "SORTING COMMITTEE",
                "DUAL ORG LLC",
                "CHICAGO",
                "IL",
                "60601",
                "C00888880",
                "H2IL08880",
                "RECIPIENT, CANDIDATE",
                300.0,
                "2026-01-12",
                "src-sort-b-1",
            ),
            (
                "sort-b-2",
                2026,
                "H2IL04444",
                "SORTING, CANDIDATE",
                "C00444444",
                "SORTING COMMITTEE",
                "ALPHA VENDOR",
                "CHICAGO",
                "IL",
                "60604",
                None,
                None,
                None,
                80.0,
                "2026-01-08",
                "src-sort-b-2",
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO fec_schedule_e_independent_expenditures (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            payee_name, payee_city, payee_state, payee_zip, support_oppose_indicator,
            expenditure_amount, expenditure_date, report_type, line_number, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "sort-e-1",
                2026,
                "H2IL04444",
                "SORTING, CANDIDATE",
                "C00IE4444",
                "IE COMMITTEE A",
                "DUAL ORG LLC",
                "CHICAGO",
                "IL",
                "60601",
                "S",
                140.0,
                "2026-01-10",
                "48H3",
                "24A",
                "src-sort-e-1",
            ),
            (
                "sort-e-2",
                2026,
                "H2IL04444",
                "SORTING, CANDIDATE",
                "C00IE4445",
                "IE COMMITTEE B",
                "BETA MEDIA",
                "CHICAGO",
                "IL",
                "60604",
                "O",
                60.0,
                "2026-01-09",
                "48H3",
                "24A",
                "src-sort-e-2",
            ),
        ],
    )
    conn.commit()

    detail = get_federal_candidate_detail(
        conn,
        candidate_id="H2IL04444",
        cycle=2026,
        schedule_b_sort="amount",
        schedule_b_dir="asc",
        schedule_e_sort="payee",
        schedule_e_dir="asc",
    )
    assert detail is not None
    assert detail["schedule_b_disbursements"][0]["disbursement_amount"] == 80.0
    assert detail["schedule_b_disbursements"][1]["disbursement_amount"] == 300.0
    assert detail["schedule_e_independent_expenditures"][0]["payee_name"] == "BETA MEDIA"
    assert detail["schedule_e_independent_expenditures"][1]["payee_name"] == "DUAL ORG LLC"
    assert detail["cross_role_summary"]["matched_organization_count"] >= 1
    assert any(row["organization_name"] == "DUAL ORG LLC" for row in detail["cross_role_organizations"])
    assert detail["schedule_b_transfer_chains"]
    assert detail["schedule_b_transfer_chains"][0]["recipient_candidate_id"] == "H2IL08880"
    assert detail["schedule_b_transfer_chains"][0]["match_type"] == "candidate_id + committee_id"

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
            (
                "sub-4",
                2026,
                "H2IL00001",
                "ONE, CANDIDATE",
                "C00000001",
                "COMMITTEE ONE",
                "MEDIA BUY VENDOR LLC",
                "Chicago",
                "IL",
                "60601",
                "MEDIA BUY VENDOR LLC",
                "Services",
                None,
                0,
                "11AI",
                "PAC",
                "Committee contribution",
                "",
                300.0,
                "2026-01-15",
                2026,
                "legacy-4",
                "2026-01-15",
                "img-4",
                "src-4",
            ),
        ],
    )
    conn.execute(
        """
        INSERT INTO fec_schedule_b_disbursements (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            recipient_name, recipient_city, recipient_state, recipient_zip,
            recipient_committee_id, disbursement_amount, disbursement_date, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sb-1",
            2026,
            "H2IL00001",
            "ONE, CANDIDATE",
            "C00000001",
            "COMMITTEE ONE",
            "MEDIA BUY VENDOR LLC",
            "CHICAGO",
            "IL",
            "60601",
            "C00999999",
            400.0,
            "2026-01-20",
            "src-sb-1",
        ),
    )
    conn.execute(
        """
        INSERT INTO fec_schedule_e_independent_expenditures (
            sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
            payee_name, payee_city, payee_state, payee_zip, support_oppose_indicator,
            expenditure_amount, expenditure_date, report_type, line_number, api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "se-1",
            2026,
            "H2IL00001",
            "ONE, CANDIDATE",
            "C00IE0001",
            "INDEPENDENT SPENDING PAC",
            "MEDIA BUY VENDOR LLC",
            "CHICAGO",
            "IL",
            "60601",
            "S",
            250.0,
            "2026-01-21",
            "48H3",
            "24A",
            "src-se-1",
        ),
    )
    conn.commit()

    races = get_federal_race_analytics(conn, cycle=2026, limit=20)
    assert len(races) == 2
    race_one = next(row for row in races if row["district_code"] == "01")
    assert race_one["candidate_count"] == 1
    assert race_one["donor_count"] == 3
    assert race_one["total_amount"] == 1500.0
    assert race_one["outside_spending_total"] == 250.0
    assert race_one["outside_pressure_ratio"] == 0.1667

    network = get_federal_network_graph(conn, cycle=2026, min_edge_amount=0.0, limit=100)
    assert network["summary"]["edge_count"] == 4
    assert network["summary"]["donor_count"] == 3
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
    assert house_01_network["summary"]["edge_count"] == 3

    cross_role = get_federal_cross_role_organizations(conn, cycle=2026, limit=20, min_total_amount=0.0)
    assert cross_role["summary"]["matched_organization_count"] >= 1
    assert any(row["organization_name"] == "MEDIA BUY VENDOR LLC" for row in cross_role["rows"])

    multi = get_federal_multilayer_network_graph(conn, cycle=2026, min_edge_amount=0.0, limit=200)
    edge_types = {edge["edge_type"] for edge in multi["edges"]}
    assert "donor_candidate" in edge_types
    assert "committee_vendor" in edge_types
    assert "ie_committee_candidate" in edge_types
    assert multi["summary"]["candidate_committee_count"] >= 1
    assert multi["summary"]["vendor_count"] >= 1
    assert multi["summary"]["ie_committee_count"] >= 1

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
    geo_drilldown = get_federal_geo_drilldown(
        conn,
        cycle=2026,
        geo_type="state",
        geo_value="IL",
        page=1,
        per_page=25,
        sort_by="total_amount",
        sort_dir="desc",
    )
    assert geo_drilldown["summary"]["sum_check_delta"] == 0.0
    assert geo_drilldown["summary"]["total_amount"] >= geo_drilldown["summary"]["page_total_amount"]

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


def test_get_top_donor_entities_respects_date_window(tmp_path: Path):
    """P2-1 pattern test: date_from/date_to filter Schedule A rows by contribution_receipt_date."""
    db_path = str(tmp_path / "top_donor_date_window.db")
    init_db(db_path)
    conn = get_db(db_path)

    rows = [
        # In-window: 2026-03-15
        ("sub-in-1", 2026, "H2IL00001", "ALPHA", "C111", "ALPHA CMTE",
         "In-Window Donor", "Chicago", "IL", "60601",
         None, None, None, 1,
         "11AI", "IND", "Individual", "",
         500.0, "2026-03-15", 2026,
         "donor-in-1", "donor-in-1-entity", "name_state_zip",
         "2026-03-16", "img-in-1", "src-in-1"),
        # Out-of-window: 2025-06-01 (before window start)
        ("sub-out-pre", 2026, "H2IL00001", "ALPHA", "C111", "ALPHA CMTE",
         "Pre-Window Donor", "Chicago", "IL", "60601",
         None, None, None, 1,
         "11AI", "IND", "Individual", "",
         9000.0, "2025-06-01", 2026,
         "donor-out-pre", "donor-out-pre-entity", "name_state_zip",
         "2025-06-02", "img-out-pre", "src-out-pre"),
        # Out-of-window: 2026-12-31 (after window end)
        ("sub-out-post", 2026, "H2IL00001", "ALPHA", "C111", "ALPHA CMTE",
         "Post-Window Donor", "Chicago", "IL", "60601",
         None, None, None, 1,
         "11AI", "IND", "Individual", "",
         7000.0, "2026-12-31", 2026,
         "donor-out-post", "donor-out-post-entity", "name_state_zip",
         "2027-01-02", "img-out-post", "src-out-post"),
    ]
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
        rows,
    )
    conn.commit()

    # Legacy cycle-only path returns all three rows.
    legacy = get_top_donor_entities(conn, cycle=2026, limit=100)
    legacy_names = {row["donor_name"] for row in legacy}
    assert legacy_names == {"In-Window Donor", "Pre-Window Donor", "Post-Window Donor"}

    # Date-bounded path excludes pre + post.
    windowed = get_top_donor_entities(
        conn,
        cycle=2026,
        limit=100,
        date_from="2026-01-01",
        date_to="2026-06-30",
    )
    windowed_names = {row["donor_name"] for row in windowed}
    assert windowed_names == {"In-Window Donor"}

    # date_from alone (open-ended right side).
    from_only = get_top_donor_entities(conn, cycle=2026, limit=100, date_from="2026-01-01")
    assert {row["donor_name"] for row in from_only} == {"In-Window Donor", "Post-Window Donor"}

    # date_to alone.
    to_only = get_top_donor_entities(conn, cycle=2026, limit=100, date_to="2026-06-30")
    assert {row["donor_name"] for row in to_only} == {"Pre-Window Donor", "In-Window Donor"}

    conn.close()
