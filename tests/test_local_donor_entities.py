"""Tests for local donor entity resolution."""

import json
from pathlib import Path

from database.connection import get_db, init_db
from database.local_donor_entities import rebuild_local_donor_entities


def _insert_summary_row(
    conn,
    *,
    donor_key: str,
    donor_name: str,
    donor_address: str,
    donor_city: str,
    donor_state: str,
    occupation: str,
    employer: str,
    total_amount: float,
    contribution_count: int,
    committee_count: int,
    source: str = "bulk_receipts",
) -> None:
    conn.execute(
        """
        INSERT INTO analytics_donor_summary (
            source,
            donor_key,
            local_donor_id,
            donor_name,
            donor_address,
            donor_city,
            donor_state,
            occupation,
            employer,
            total_amount,
            contribution_count,
            committee_count
        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            donor_key,
            donor_name,
            donor_address,
            donor_city,
            donor_state,
            occupation,
            employer,
            float(total_amount),
            int(contribution_count),
            int(committee_count),
        ),
    )


def test_rebuild_local_donor_entities_links_multi_home_variants(tmp_path: Path):
    db_path = str(tmp_path / "local_entities.db")
    init_db(db_path)
    conn = get_db(db_path)

    _insert_summary_row(
        conn,
        donor_key="kenneth|griffin|131 s dearborn st||chicago|il|60603-5517",
        donor_name="Kenneth Griffin",
        donor_address="131 S Dearborn St, Chicago, IL, 60603-5517",
        donor_city="Chicago",
        donor_state="IL",
        occupation="Founder and CEO",
        employer="Citadel LLC",
        total_amount=55_016_400.0,
        contribution_count=8,
        committee_count=3,
    )
    _insert_summary_row(
        conn,
        donor_key="kenneth|griffin|131 s. dearborn st||chicago|il|60603",
        donor_name="Kenneth Griffin",
        donor_address="131 S. Dearborn St, Chicago, IL, 60603",
        donor_city="Chicago",
        donor_state="IL",
        occupation="CEO",
        employer="The Citadel, LLC",
        total_amount=53_817_500.0,
        contribution_count=10,
        committee_count=6,
    )
    _insert_summary_row(
        conn,
        donor_key="kenneth|griffin|800 n michigan ave|apt 67ph|chicago|il|60611-2105",
        donor_name="Kenneth Griffin",
        donor_address="800 N Michigan Ave, Apt 67PH, Chicago, IL, 60611-2105",
        donor_city="Chicago",
        donor_state="IL",
        occupation="CEO",
        employer="Citadel",
        total_amount=33_500_000.0,
        contribution_count=9,
        committee_count=1,
    )
    _insert_summary_row(
        conn,
        donor_key="kenneth|griffin|1010 jericho road||aurora|il|60506",
        donor_name="Kenneth Griffin",
        donor_address="1010 Jericho Road, Aurora, IL, 60506",
        donor_city="Aurora",
        donor_state="IL",
        occupation="",
        employer="",
        total_amount=500.0,
        contribution_count=1,
        committee_count=1,
    )
    conn.commit()

    stats = rebuild_local_donor_entities(
        conn,
        source="bulk_receipts",
        medium_threshold=0.70,
        high_threshold=0.82,
        auto_threshold=0.90,
        max_group_size=400,
        dry_run=False,
        preview_limit=10,
    )

    assert stats["processed_groups"] == 1
    assert stats["duplicate_rows_medium"] >= 2

    rows = conn.execute(
        """
        SELECT donor_key, entity_id, merge_action
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
        ORDER BY donor_key
        """
    ).fetchall()
    assert len(rows) == 4

    by_key = {row["donor_key"]: row for row in rows}

    entity_1 = by_key["kenneth|griffin|131 s dearborn st||chicago|il|60603-5517"]["entity_id"]
    entity_2 = by_key["kenneth|griffin|131 s. dearborn st||chicago|il|60603"]["entity_id"]
    entity_3 = by_key["kenneth|griffin|800 n michigan ave|apt 67ph|chicago|il|60611-2105"]["entity_id"]
    entity_4 = by_key["kenneth|griffin|1010 jericho road||aurora|il|60506"]["entity_id"]

    assert entity_1 == entity_2
    assert entity_2 == entity_3
    assert entity_4 != entity_1

    assert by_key["kenneth|griffin|131 s dearborn st||chicago|il|60603-5517"]["merge_action"] in {
        "review",
        "auto_merge",
    }
    assert by_key["kenneth|griffin|1010 jericho road||aurora|il|60506"]["merge_action"] == "singleton"

    conn.close()


def test_rebuild_local_donor_entities_dry_run_does_not_write(tmp_path: Path):
    db_path = str(tmp_path / "local_entities_dry_run.db")
    init_db(db_path)
    conn = get_db(db_path)

    _insert_summary_row(
        conn,
        donor_key="jane|doe|1 main st||chicago|il|60601",
        donor_name="Jane Doe",
        donor_address="1 Main St, Chicago, IL, 60601",
        donor_city="Chicago",
        donor_state="IL",
        occupation="Engineer",
        employer="Acme",
        total_amount=1000.0,
        contribution_count=1,
        committee_count=1,
    )
    _insert_summary_row(
        conn,
        donor_key="jane|doe|1 main street||chicago|il|60601-0001",
        donor_name="Jane Doe",
        donor_address="1 Main Street, Chicago, IL, 60601-0001",
        donor_city="Chicago",
        donor_state="IL",
        occupation="Engineer",
        employer="Acme Inc",
        total_amount=1200.0,
        contribution_count=2,
        committee_count=1,
    )
    conn.commit()

    stats = rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=True)
    assert stats["dry_run"] is True
    assert stats["candidate_rows"] == 2

    member_count = conn.execute(
        "SELECT COUNT(*) AS count FROM donor_entity_local_member WHERE source = 'bulk_receipts'"
    ).fetchone()["count"]
    entity_count = conn.execute(
        "SELECT COUNT(*) AS count FROM donor_entity_local WHERE source = 'bulk_receipts'"
    ).fetchone()["count"]

    assert member_count == 0
    assert entity_count == 0

    conn.close()


def test_rebuild_local_donor_entities_preserves_review_status(tmp_path: Path):
    db_path = str(tmp_path / "local_entities_review_status.db")
    init_db(db_path)
    conn = get_db(db_path)

    _insert_summary_row(
        conn,
        donor_key="kenneth|griffin|131 s dearborn st||chicago|il|60603-5517",
        donor_name="Kenneth Griffin",
        donor_address="131 S Dearborn St, Chicago, IL, 60603-5517",
        donor_city="Chicago",
        donor_state="IL",
        occupation="Founder and CEO",
        employer="Citadel LLC",
        total_amount=100000.0,
        contribution_count=2,
        committee_count=1,
    )
    _insert_summary_row(
        conn,
        donor_key="kenneth|griffin|800 n michigan ave|apt 67ph|chicago|il|60611-2105",
        donor_name="Kenneth Griffin",
        donor_address="800 N Michigan Ave, Apt 67PH, Chicago, IL, 60611-2105",
        donor_city="Chicago",
        donor_state="IL",
        occupation="CEO",
        employer="Citadel",
        total_amount=50000.0,
        contribution_count=1,
        committee_count=1,
    )
    conn.commit()

    rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=False)

    conn.execute(
        """
        UPDATE donor_entity_local_member
        SET review_status = 'approved'
        WHERE source = 'bulk_receipts'
          AND donor_key = 'kenneth|griffin|800 n michigan ave|apt 67ph|chicago|il|60611-2105'
        """
    )
    conn.commit()

    rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=False)

    status = conn.execute(
        """
        SELECT review_status
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
          AND donor_key = 'kenneth|griffin|800 n michigan ave|apt 67ph|chicago|il|60611-2105'
        """
    ).fetchone()["review_status"]

    assert status == "approved"
    conn.close()


def test_rebuild_local_donor_entities_bridge_rule_links_high_dollar_same_state_employer(tmp_path: Path):
    db_path = str(tmp_path / "local_entities_bridge_rule.db")
    init_db(db_path)
    conn = get_db(db_path)

    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|1396 n. waukegan road||lake forest|il|60045",
        donor_name="Richard Uihlein",
        donor_address="1396 N. Waukegan Road, Lake Forest, IL, 60045",
        donor_city="Lake Forest",
        donor_state="IL",
        occupation="small business owner",
        employer="Uline Company",
        total_amount=26_404_670.51,
        contribution_count=465,
        committee_count=254,
    )
    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|po box 52||lake bluff|il|60044",
        donor_name="Richard Uihlein",
        donor_address="PO Box 52, Lake Bluff, IL, 60044",
        donor_city="Lake Bluff",
        donor_state="IL",
        occupation="Uline",
        employer="Uline Shipping",
        total_amount=21_674_532.0,
        contribution_count=55,
        committee_count=40,
    )
    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|1010 jericho rd||aurora|il|60506",
        donor_name="Richard Uihlein",
        donor_address="1010 Jericho Road, Aurora, IL, 60506",
        donor_city="Aurora",
        donor_state="IL",
        occupation="retired",
        employer="",
        total_amount=500.0,
        contribution_count=1,
        committee_count=1,
    )
    conn.commit()

    stats = rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=False)
    assert stats["processed_groups"] == 1

    rows = conn.execute(
        """
        SELECT donor_key, entity_id, merge_action, confidence_score, reasons_json
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
        ORDER BY donor_key
        """
    ).fetchall()
    assert len(rows) == 3

    by_key = {row["donor_key"]: row for row in rows}
    richard_home = by_key["richard|uihlein|1396 n. waukegan road||lake forest|il|60045"]
    richard_po_box = by_key["richard|uihlein|po box 52||lake bluff|il|60044"]
    richard_small = by_key["richard|uihlein|1010 jericho rd||aurora|il|60506"]

    assert richard_home["entity_id"] == richard_po_box["entity_id"]
    assert richard_home["entity_id"] != richard_small["entity_id"]
    assert richard_home["merge_action"] == "review"
    assert float(richard_home["confidence_score"]) >= 0.70
    assert float(richard_po_box["confidence_score"]) >= 0.70

    home_reasons = json.loads(richard_home["reasons_json"])
    po_box_reasons = json.loads(richard_po_box["reasons_json"])
    assert home_reasons["signals"]["bridge_rule"] is True
    assert po_box_reasons["signals"]["bridge_rule"] is True

    conn.close()


def test_rebuild_local_donor_entities_bridge_rule_does_not_link_low_dollar_pairs(tmp_path: Path):
    db_path = str(tmp_path / "local_entities_bridge_rule_safety.db")
    init_db(db_path)
    conn = get_db(db_path)

    _insert_summary_row(
        conn,
        donor_key="john|doe|10 main st||chicago|il|60601",
        donor_name="John Doe",
        donor_address="10 Main St, Chicago, IL, 60601",
        donor_city="Chicago",
        donor_state="IL",
        occupation="Engineer",
        employer="Acme Company",
        total_amount=700.0,
        contribution_count=2,
        committee_count=1,
    )
    _insert_summary_row(
        conn,
        donor_key="john|doe|44 oak ave||evanston|il|60201",
        donor_name="John Doe",
        donor_address="44 Oak Ave, Evanston, IL, 60201",
        donor_city="Evanston",
        donor_state="IL",
        occupation="Analyst",
        employer="Acme Logistics",
        total_amount=500.0,
        contribution_count=1,
        committee_count=1,
    )
    conn.commit()

    rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=False)

    rows = conn.execute(
        """
        SELECT donor_key, entity_id, merge_action
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
        ORDER BY donor_key
        """
    ).fetchall()
    assert len(rows) == 2
    by_key = {row["donor_key"]: row for row in rows}
    left = by_key["john|doe|10 main st||chicago|il|60601"]
    right = by_key["john|doe|44 oak ave||evanston|il|60201"]

    assert left["entity_id"] != right["entity_id"]
    assert left["merge_action"] == "singleton"
    assert right["merge_action"] == "singleton"

    conn.close()


def test_rebuild_local_donor_entities_bridge_rule_survives_large_group_penalty(tmp_path: Path):
    db_path = str(tmp_path / "local_entities_bridge_rule_large_group.db")
    init_db(db_path)
    conn = get_db(db_path)

    # Target pair that should link via bridge rule.
    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|1396 n. waukegan road||lake forest|il|60045",
        donor_name="Richard Uihlein",
        donor_address="1396 N. Waukegan Road, Lake Forest, IL, 60045",
        donor_city="Lake Forest",
        donor_state="IL",
        occupation="small business owner",
        employer="Uline Company",
        total_amount=26_404_670.51,
        contribution_count=465,
        committee_count=254,
    )
    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|po box 52||lake bluff|il|60044",
        donor_name="Richard Uihlein",
        donor_address="PO Box 52, Lake Bluff, IL, 60044",
        donor_city="Lake Bluff",
        donor_state="IL",
        occupation="Uline",
        employer="Uline Shipping",
        total_amount=21_674_532.0,
        contribution_count=55,
        committee_count=40,
    )

    # Add many low-signal rows with same canonical name to trigger large-group penalties.
    for idx in range(50):
        _insert_summary_row(
            conn,
            donor_key=f"richard|uihlein|{1000 + idx} elm st||springfield|il|6270{idx % 10}",
            donor_name="Richard Uihlein",
            donor_address=f"{1000 + idx} Elm St, Springfield, IL, 6270{idx % 10}",
            donor_city="Springfield",
            donor_state="IL",
            occupation="retired",
            employer=f"Other Employer {idx}",
            total_amount=100.0 + idx,
            contribution_count=1,
            committee_count=1,
        )
    conn.commit()

    stats = rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=False)
    assert stats["processed_groups"] == 1

    rows = conn.execute(
        """
        SELECT donor_key, entity_id, merge_action, confidence_score
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
          AND donor_key IN (
            'richard|uihlein|1396 n. waukegan road||lake forest|il|60045',
            'richard|uihlein|po box 52||lake bluff|il|60044'
          )
        ORDER BY donor_key
        """
    ).fetchall()
    assert len(rows) == 2

    assert rows[0]["entity_id"] == rows[1]["entity_id"]
    assert rows[0]["merge_action"] == "review"
    assert float(rows[0]["confidence_score"]) >= 0.70
    assert float(rows[1]["confidence_score"]) >= 0.70

    conn.close()


def test_rebuild_local_donor_entities_bridge_rule_links_cross_state_company_from_occupation(tmp_path: Path):
    db_path = str(tmp_path / "local_entities_bridge_rule_cross_state.db")
    init_db(db_path)
    conn = get_db(db_path)

    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|po box 52||lake bluff|il|60044",
        donor_name="Richard Uihlein",
        donor_address="PO Box 52, Lake Bluff, IL, 60044",
        donor_city="Lake Bluff",
        donor_state="IL",
        occupation="Uline",
        employer="Uline Shipping",
        total_amount=48_177_202.51,
        contribution_count=524,
        committee_count=280,
    )
    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|12575 uline drive||pleasant prairie|wi|53158",
        donor_name="Richard Uihlein",
        donor_address="12575 Uline Drive, Pleasant Prairie, WI, 53158",
        donor_city="Pleasant Prairie",
        donor_state="WI",
        occupation="President",
        employer="Uline Corp",
        total_amount=5_182_000.0,
        contribution_count=16,
        committee_count=8,
    )
    _insert_summary_row(
        conn,
        donor_key="richard|uihlein|77 state st||madison|wi|53703",
        donor_name="Richard Uihlein",
        donor_address="77 State St, Madison, WI, 53703",
        donor_city="Madison",
        donor_state="WI",
        occupation="Investor",
        employer="Another Company",
        total_amount=900.0,
        contribution_count=1,
        committee_count=1,
    )
    conn.commit()

    rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=False)

    rows = conn.execute(
        """
        SELECT donor_key, entity_id, merge_action, confidence_score, reasons_json
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
          AND donor_key IN (
            'richard|uihlein|po box 52||lake bluff|il|60044',
            'richard|uihlein|12575 uline drive||pleasant prairie|wi|53158'
          )
        ORDER BY donor_key
        """
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["entity_id"] == rows[1]["entity_id"]
    assert rows[0]["merge_action"] == "review"
    assert float(rows[0]["confidence_score"]) >= 0.70
    assert float(rows[1]["confidence_score"]) >= 0.70

    signal0 = json.loads(rows[0]["reasons_json"])["signals"]
    signal1 = json.loads(rows[1]["reasons_json"])["signals"]
    assert signal0["bridge_rule"] is True
    assert signal1["bridge_rule"] is True
    assert signal0["cross_field_company_match"] is True
    assert signal1["cross_field_company_match"] is True

    conn.close()


def test_rebuild_local_donor_entities_cross_state_company_without_cross_field_stays_separate(tmp_path: Path):
    db_path = str(tmp_path / "local_entities_bridge_rule_cross_state_safety.db")
    init_db(db_path)
    conn = get_db(db_path)

    _insert_summary_row(
        conn,
        donor_key="john|doe|10 main st||chicago|il|60601",
        donor_name="John Doe",
        donor_address="10 Main St, Chicago, IL, 60601",
        donor_city="Chicago",
        donor_state="IL",
        occupation="Engineer",
        employer="Acme Corp",
        total_amount=8_000_000.0,
        contribution_count=12,
        committee_count=7,
    )
    _insert_summary_row(
        conn,
        donor_key="john|doe|200 market st||milwaukee|wi|53202",
        donor_name="John Doe",
        donor_address="200 Market St, Milwaukee, WI, 53202",
        donor_city="Milwaukee",
        donor_state="WI",
        occupation="President",
        employer="Acme Corporation",
        total_amount=7_500_000.0,
        contribution_count=9,
        committee_count=5,
    )
    conn.commit()

    rebuild_local_donor_entities(conn, source="bulk_receipts", dry_run=False)

    rows = conn.execute(
        """
        SELECT donor_key, entity_id, merge_action
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
        ORDER BY donor_key
        """
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["entity_id"] != rows[1]["entity_id"]
    assert rows[0]["merge_action"] == "singleton"
    assert rows[1]["merge_action"] == "singleton"

    conn.close()
