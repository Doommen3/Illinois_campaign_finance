"""Tests for dedupe and identifier behavior in models."""
from pathlib import Path

from database.connection import get_db, init_db
from database.models import Committee, Report, D2Report, D2ItemizedLink, D2ItemizedEntry, RawExtraction


def _new_conn(tmp_path: Path):
    db_path = str(tmp_path / "test_models.db")
    init_db(db_path)
    return get_db(db_path)


def test_report_save_dedupes_by_detail_url(tmp_path):
    conn = _new_conn(tmp_path)

    committee = Committee.get_or_create(
        conn,
        "Committee One",
        detail_url="https://www.elections.il.gov/CampaignDisclosure/CommitteeDetail.aspx?FilerID=123"
    )

    report1 = Report(
        committee_id=committee.id,
        report_type="A-1 ($1000+ Year Round)",
        reporting_period="Q1 2026",
        filed_date="02/01/2026",
        pages=4,
        clarification="",
        detail_url="https://www.elections.il.gov/CampaignDisclosure/A1List.aspx?FiledDocID=abc",
        scrape_status="pending",
        source_page=1,
    ).save(conn)

    report2 = Report(
        committee_id=committee.id,
        report_type="A-1 ($1000+ Year Round)",
        reporting_period="Q1 2026",
        filed_date="02/01/2026",
        pages=4,
        clarification="",
        detail_url="https://www.elections.il.gov/CampaignDisclosure/A1List.aspx?FiledDocID=abc",
        scrape_status="scraped",
        source_page=2,
    ).save(conn)

    count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    assert count == 1
    assert report1.id == report2.id

    row = conn.execute("SELECT scrape_status, source_page FROM reports WHERE id = ?", (report1.id,)).fetchone()
    assert row["scrape_status"] == "scraped"
    assert row["source_page"] == 2

    conn.close()


def test_committee_get_or_create_updates_detail_url(tmp_path):
    conn = _new_conn(tmp_path)

    committee = Committee.get_or_create(conn, "Committee Two")
    assert committee.detail_url is None

    updated = Committee.get_or_create(
        conn,
        "Committee Two",
        detail_url="https://www.elections.il.gov/CampaignDisclosure/CommitteeDetail.aspx?FilerID=999"
    )

    fetched = Committee.get_by_id(conn, updated.id)
    assert fetched is not None
    assert fetched.detail_url is not None
    assert "CommitteeDetail.aspx" in fetched.detail_url

    conn.close()


def test_d2_itemized_entry_dedupes_by_row_hash(tmp_path):
    conn = _new_conn(tmp_path)

    committee = Committee.get_or_create(conn, "Committee Three")
    d2_report = D2Report(
        committee_id=committee.id,
        report_type="D-2 Quarterly Report",
        reporting_period="Q1 2026",
        filed_date="02/05/2026",
        detail_url="https://www.elections.il.gov/CampaignDisclosure/D2List.aspx?FiledDocID=def",
    ).save(conn)

    link = D2ItemizedLink(
        d2_report_id=d2_report.id,
        label="a. Itemized",
        itemized_type="contribution",
        url="https://www.elections.il.gov/CampaignDisclosure/ItemizedContributions.aspx?FiledDocID=ghi",
    ).save(conn)

    entry1 = D2ItemizedEntry(
        d2_report_id=d2_report.id,
        itemized_link_id=link.id,
        row_hash="hash:row1",
        entry_type="contribution",
        contributed_by="John Smith",
        address="1 Main St",
        amount=1500.0,
        description="Donation",
    ).save(conn)

    entry2 = D2ItemizedEntry(
        d2_report_id=d2_report.id,
        itemized_link_id=link.id,
        row_hash="hash:row1",
        entry_type="contribution",
        contributed_by="John Smith",
        address="1 Main St",
        amount=1500.0,
        description="Donation updated",
    ).save(conn)

    assert entry1.id == entry2.id
    count = conn.execute("SELECT COUNT(*) FROM d2_itemized_entries").fetchone()[0]
    assert count == 1

    row = conn.execute("SELECT description FROM d2_itemized_entries WHERE id = ?", (entry1.id,)).fetchone()
    assert row["description"] == "Donation updated"

    conn.close()


def test_raw_extraction_upserts_by_source_key(tmp_path):
    conn = _new_conn(tmp_path)

    raw = RawExtraction(
        source_type='main_list_row',
        source_identifier='row-1',
        source_url='https://example.com/a',
        parser_version='v1',
    )
    raw.payload = {'committee': 'Example 1'}
    raw.save(conn)

    raw2 = RawExtraction(
        source_type='main_list_row',
        source_identifier='row-1',
        source_url='https://example.com/a',
        parser_version='v2',
    )
    raw2.payload = {'committee': 'Example 1 Updated'}
    raw2.save(conn)

    assert raw.id == raw2.id
    row = conn.execute(
        "SELECT parser_version, payload_json FROM raw_extractions WHERE id = ?",
        (raw.id,)
    ).fetchone()
    assert row['parser_version'] == 'v2'
    assert 'Updated' in row['payload_json']

    conn.close()
