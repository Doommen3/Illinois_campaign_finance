"""Tests for local donor entity resolution."""

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
