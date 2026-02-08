"""Tests for cleanup/normalization maintenance helpers."""
from pathlib import Path

from database.connection import init_db, get_db
from database.maintenance import (
    find_garbage_committees,
    delete_committees_and_related,
    normalize_donor_metadata,
    data_quality_summary,
    find_reports_for_detail_rescrape,
    requeue_reports_for_detail_scrape,
)
from database.models import Committee, Report, Donor, Contribution


def _conn(tmp_path: Path):
    db_path = str(tmp_path / 'maintenance.db')
    init_db(db_path)
    return get_db(db_path)


def test_delete_committees_and_related(tmp_path):
    conn = _conn(tmp_path)

    garbage = Committee.get_or_create(conn, '. 11 12 13 14 ...')
    clean = Committee.get_or_create(conn, 'Friends of Example')

    g_report = Report(
        committee_id=garbage.id,
        report_type='A-1 ($1000+ Year Round)',
        filed_date='02/01/2026',
        detail_url='https://www.elections.il.gov/CampaignDisclosure/A1List.aspx?FiledDocID=garbage',
        scrape_status='pending',
    ).save(conn)

    c_report = Report(
        committee_id=clean.id,
        report_type='A-1 ($1000+ Year Round)',
        filed_date='02/01/2026',
        detail_url='https://www.elections.il.gov/CampaignDisclosure/A1List.aspx?FiledDocID=clean',
        scrape_status='pending',
    ).save(conn)

    donor = Donor.get_or_create(conn, 'John Doe', '123 Main', 'john doe', '123 main')
    Contribution(
        report_id=g_report.id,
        donor_id=donor.id,
        amount=100.0,
        raw_contributed_by='John Doe',
        raw_address='123 Main',
    ).save(conn)

    garbage_rows = find_garbage_committees(conn)
    assert len(garbage_rows) == 1

    stats = delete_committees_and_related(conn, [garbage.id])
    assert stats['committees_deleted'] == 1
    assert stats['reports_deleted'] == 1
    assert stats['contributions_deleted'] == 1

    remaining_committees = conn.execute('SELECT COUNT(*) FROM committees').fetchone()[0]
    remaining_reports = conn.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
    assert remaining_committees == 1
    assert remaining_reports == 1
    assert conn.execute('SELECT id FROM reports WHERE id = ?', (c_report.id,)).fetchone() is not None

    conn.close()


def test_normalize_donor_metadata_extracts_occupation_employer(tmp_path):
    conn = _conn(tmp_path)

    donor = Donor.get_or_create(
        conn,
        name='Bishop, Elizabeth Occupation: Politics Employer: City of LaSalle',
        address='123 Main',
        normalized_name='bishop, elizabeth occupation: politics employer: city of lasalle',
        normalized_address='123 main',
    )

    stats = normalize_donor_metadata(conn)
    assert stats['donors_updated'] >= 1

    row = conn.execute(
        'SELECT name, occupation, employer FROM donors WHERE id = ?',
        (donor.id,)
    ).fetchone()

    assert row['name'] == 'Bishop, Elizabeth'
    assert row['occupation'] == 'Politics'
    assert row['employer'] == 'City of LaSalle'

    conn.close()


def test_data_quality_summary_contains_expected_keys(tmp_path):
    conn = _conn(tmp_path)

    committee = Committee.get_or_create(conn, 'Quality Committee')
    report = Report(
        committee_id=committee.id,
        report_type='A-1 ($1000+ Year Round)',
        filed_date='02/01/2026',
        detail_url='https://www.elections.il.gov/CampaignDisclosure/A1List.aspx?FiledDocID=quality',
        scrape_status='pending',
    ).save(conn)
    donor = Donor.get_or_create(conn, 'Jane Test', '1 Main', 'jane test', '1 main')
    Contribution(
        report_id=report.id,
        donor_id=donor.id,
        amount=50.0,
        transaction_date='2026-02-01',
        raw_contributed_by='Jane Test',
        raw_address='1 Main',
    ).save(conn)

    summary = data_quality_summary(conn)
    assert 'totals' in summary
    assert 'quality_flags' in summary
    assert 'raw_extractions_by_source' in summary
    assert summary['totals']['committees'] >= 1
    assert summary['quality_flags']['garbage_committees'] == 0

    conn.close()


def test_requeue_reports_for_detail_scrape_missing_transaction_date(tmp_path):
    conn = _conn(tmp_path)

    committee = Committee.get_or_create(conn, 'Requeue Committee')
    report = Report(
        committee_id=committee.id,
        report_type='A-1 ($1000+ Year Round)',
        filed_date='02/01/2026',
        detail_url='https://www.elections.il.gov/CampaignDisclosure/A1List.aspx?FiledDocID=requeue',
        scrape_status='scraped',
    ).save(conn)
    donor = Donor.get_or_create(conn, 'Requeue Donor', '2 Main', 'requeue donor', '2 main')
    Contribution(
        report_id=report.id,
        donor_id=donor.id,
        amount=10.0,
        raw_contributed_by='Requeue Donor',
        raw_address='2 Main',
    ).save(conn)

    selected = find_reports_for_detail_rescrape(conn, missing_transaction_date_only=True)
    assert report.id in selected

    stats = requeue_reports_for_detail_scrape(conn, [report.id])
    assert stats['reports_requeued'] == 1
    assert stats['contributions_deleted'] == 1

    row = conn.execute("SELECT scrape_status FROM reports WHERE id = ?", (report.id,)).fetchone()
    assert row['scrape_status'] == 'pending'
    remaining = conn.execute("SELECT COUNT(*) FROM contributions WHERE report_id = ?", (report.id,)).fetchone()[0]
    assert remaining == 0

    conn.close()
