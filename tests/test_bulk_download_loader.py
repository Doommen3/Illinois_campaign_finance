"""Tests for bulk download loader and join table creation."""
from pathlib import Path

from database.connection import get_db, init_db
from database.bulk_download_loader import (
    _to_bool,
    _to_float,
    _to_int,
    import_bulk_download,
)


def test_type_parsers():
    assert _to_int('10') == 10
    assert _to_int('10.0') == 10
    assert _to_int('') is None

    assert _to_float('1.25') == 1.25
    assert _to_float('') is None

    assert _to_bool('True') == 1
    assert _to_bool('false') == 0
    assert _to_bool('') is None


def test_import_bulk_download_creates_joined_tables(tmp_path: Path):
    bulk_dir = tmp_path / 'Bulk_download'
    bulk_dir.mkdir(parents=True)

    committees = bulk_dir / 'committees_1.txt'
    committees.write_text(
        'ID\tTypeOfCommittee\tStateCommittee\tStateID\tLocalCommittee\tLocalID\tReferName\tName\tAddress1\tAddress2\tAddress3\tCity\tState\tZip\tStatus\tStatusDate\tCreationDate\tCreationAmount\tDispFundsReturn\tDispFundsPolComm\tDispFundsCharity\tDispFunds95\tDispFundsDescrip\tCanSuppOpp\tPolicySuppOpp\tPartyAffiliation\tPurpose\n'
        '101\tPolitical Action\tFalse\t0\tFalse\t0\tA1\tCommittee A\t123 Main\t\t\tChicago\tIL\t60601\tA\t2024-01-01 00:00:00\t2020-01-01 00:00:00\t1000\tTrue\tFalse\tFalse\tFalse\t\tS\t\tDemocratic\tPurpose A\n'
        '102\tPolitical Party\tFalse\t0\tFalse\t0\tB1\tCommittee B\t456 Oak\t\t\tSpringfield\tIL\t62701\tF\t2024-02-01 00:00:00\t2021-01-01 00:00:00\t2000\tFalse\tTrue\tFalse\tFalse\t\tO\t\tRepublican\tPurpose B\n',
        encoding='utf-8'
    )

    d2 = bulk_dir / 'd2totals_1.txt'
    d2.write_text(
        'ID\tCommitteeID\tFiledDocID\tBegFundsAvail\tIndivContribI\tIndivContribNI\tXferInI\tXferInNI\tLoanRcvI\tLoanRcvNI\tOtherRctI\tOtherRctNI\tTotalReceipts\tInKindI\tInKindNI\tTotalInKind\tXferOutI\tXferOutNI\tLoanMadeI\tLoanMadeNI\tExpendI\tExpendNI\tIndependentExpI\tIndependentExpNI\tTotalExpend\tDebtsI\tDebtsNI\tTotalDebts\tTotalInvest\tEndFundsAvail\tArchived\n'
        '1\t101\t5001\t100\t25\t5\t0\t0\t0\t0\t0\t0\t30\t0\t0\t0\t0\t0\t0\t0\t10\t2\t0\t0\t12\t0\t0\t0\t0\t118\tFalse\n'
        '2\t102\t5002\t200\t35\t15\t0\t0\t0\t0\t0\t0\t50\t0\t0\t0\t0\t0\t0\t0\t20\t1\t0\t0\t21\t0\t0\t0\t0\t229\tTrue\n',
        encoding='utf-8'
    )

    db_path = str(tmp_path / 'test_bulk.db')
    init_db(db_path)
    conn = get_db(db_path)

    stats = import_bulk_download(conn, bulk_dir)
    assert stats['committees_loaded'] == 2
    assert stats['d2_totals_loaded'] == 2
    assert stats['committee_d2_join_rows'] == 2
    assert stats['candidates_loaded'] == 0
    assert stats['cmte_candidate_links_loaded'] == 0
    assert stats['receipts_loaded'] == 0
    assert stats['unmatched_d2_committee_ids'] == 0
    assert stats['unmatched_receipts_committee_ids'] == 0
    assert stats['unmatched_receipts_d2_filed_docs'] == 0
    assert stats['committee_receipts_rows'] == 0
    assert stats['d2_receipts_recon_rows'] == 2
    assert stats['candidate_committee_receipts_agg_rows'] == 0

    row = conn.execute(
        """
        SELECT committee_name, committee_type, total_receipts, total_expenditures, is_archived
        FROM bulk_committee_d2_totals
        WHERE committee_id_sbe = 101
        """
    ).fetchone()
    assert row['committee_name'] == 'Committee A'
    assert row['committee_type'] == 'Political Action'
    assert row['total_receipts'] == 30.0
    assert row['total_expenditures'] == 12.0
    assert row['is_archived'] == 0

    conn.close()


def test_import_bulk_download_with_candidate_links_creates_candidate_tables(tmp_path: Path):
    bulk_dir = tmp_path / 'Bulk_download'
    bulk_dir.mkdir(parents=True)

    committees = bulk_dir / 'committees_1.txt'
    committees.write_text(
        'ID\tTypeOfCommittee\tStateCommittee\tStateID\tLocalCommittee\tLocalID\tReferName\tName\tAddress1\tAddress2\tAddress3\tCity\tState\tZip\tStatus\tStatusDate\tCreationDate\tCreationAmount\tDispFundsReturn\tDispFundsPolComm\tDispFundsCharity\tDispFunds95\tDispFundsDescrip\tCanSuppOpp\tPolicySuppOpp\tPartyAffiliation\tPurpose\n'
        '101\tPolitical Action\tFalse\t0\tFalse\t0\tA1\tCommittee A\t123 Main\t\t\tChicago\tIL\t60601\tA\t2024-01-01 00:00:00\t2020-01-01 00:00:00\t1000\tTrue\tFalse\tFalse\tFalse\t\tS\t\tDemocratic\tPurpose A\n'
        '102\tPolitical Party\tFalse\t0\tFalse\t0\tB1\tCommittee B\t456 Oak\t\t\tSpringfield\tIL\t62701\tF\t2024-02-01 00:00:00\t2021-01-01 00:00:00\t2000\tFalse\tTrue\tFalse\tFalse\t\tO\t\tRepublican\tPurpose B\n',
        encoding='utf-8'
    )

    d2 = bulk_dir / 'd2totals_1.txt'
    d2.write_text(
        'ID\tCommitteeID\tFiledDocID\tBegFundsAvail\tIndivContribI\tIndivContribNI\tXferInI\tXferInNI\tLoanRcvI\tLoanRcvNI\tOtherRctI\tOtherRctNI\tTotalReceipts\tInKindI\tInKindNI\tTotalInKind\tXferOutI\tXferOutNI\tLoanMadeI\tLoanMadeNI\tExpendI\tExpendNI\tIndependentExpI\tIndependentExpNI\tTotalExpend\tDebtsI\tDebtsNI\tTotalDebts\tTotalInvest\tEndFundsAvail\tArchived\n'
        '1\t101\t5001\t100\t25\t5\t0\t0\t0\t0\t0\t0\t30\t0\t0\t0\t0\t0\t0\t0\t10\t2\t0\t0\t12\t0\t0\t0\t0\t118\tFalse\n'
        '2\t102\t5002\t200\t35\t15\t0\t0\t0\t0\t0\t0\t50\t0\t0\t0\t0\t0\t0\t0\t20\t1\t0\t0\t21\t0\t0\t0\t0\t229\tTrue\n',
        encoding='utf-8'
    )

    candidates = bulk_dir / 'candidates_1.txt'
    candidates.write_text(
        'ID\tLastName\tFirstName\tAddress1\tAddress2\tCity\tState\tZip\tOffice\tDistrictType\tDistrict\tResidenceCounty\tPartyAffiliation\tRedactionRequested\n'
        '201\tSmith\tJordan\t1 Pine\t\tChicago\tIL\t60610\tGovernor\tStatewide\tAt-Large\tCook\tDemocratic\tFalse\n'
        '202\tJones\tCasey\t2 Elm\t\tSpringfield\tIL\t62702\tMayor\tMunicipal\t7\tSangamon\tIndependent\tTrue\n',
        encoding='utf-8'
    )

    links = bulk_dir / 'cmtecandidatelinks_1.txt'
    links.write_text(
        'ID\tCommitteeID\tCandidateID\n'
        '9001\t101\t201\n'
        '9002\t102\t202\n'
        '9003\t101\t999\n',
        encoding='utf-8'
    )

    db_path = str(tmp_path / 'test_bulk_candidates.db')
    init_db(db_path)
    conn = get_db(db_path)

    stats = import_bulk_download(conn, bulk_dir)
    assert stats['committees_loaded'] == 2
    assert stats['d2_totals_loaded'] == 2
    assert stats['committee_d2_join_rows'] == 2
    assert stats['candidates_loaded'] == 2
    assert stats['cmte_candidate_links_loaded'] == 3
    assert stats['receipts_loaded'] == 0
    assert stats['committee_candidate_links_rows'] == 3
    assert stats['candidate_committee_d2_rows'] == 3
    assert stats['candidate_committee_agg_rows'] == 3
    assert stats['committee_receipts_rows'] == 0
    assert stats['d2_receipts_recon_rows'] == 2
    assert stats['candidate_committee_receipts_agg_rows'] == 3
    assert stats['unmatched_links_candidate_ids'] == 1
    assert stats['unmatched_receipts_committee_ids'] == 0
    assert stats['unmatched_receipts_d2_filed_docs'] == 0
    assert stats['links_id_matches_candidates_id_count'] == 0

    link_row = conn.execute(
        """
        SELECT committee_name, candidate_full_name, office_sought
        FROM bulk_committee_candidate_links
        WHERE link_record_id = 9001
        """
    ).fetchone()
    assert link_row['committee_name'] == 'Committee A'
    assert link_row['candidate_full_name'] == 'Jordan Smith'
    assert link_row['office_sought'] == 'Governor'

    agg_row = conn.execute(
        """
        SELECT filing_count, sum_total_receipts, sum_total_expenditures
        FROM bulk_candidate_committee_finance_agg
        WHERE candidate_id = 201 AND committee_id_sbe = 101
        """
    ).fetchone()
    assert agg_row['filing_count'] == 1
    assert agg_row['sum_total_receipts'] == 30.0
    assert agg_row['sum_total_expenditures'] == 12.0

    archived_only_row = conn.execute(
        """
        SELECT filing_count, sum_total_receipts, sum_total_expenditures, archived_filing_count, period_year, election_cycle
        FROM bulk_candidate_committee_finance_agg
        WHERE candidate_id = 202 AND committee_id_sbe = 102
        """
    ).fetchone()
    assert archived_only_row['filing_count'] == 0
    assert archived_only_row['sum_total_receipts'] == 0.0
    assert archived_only_row['sum_total_expenditures'] == 0.0
    assert archived_only_row['archived_filing_count'] == 1
    assert archived_only_row['period_year'] is None
    assert archived_only_row['election_cycle'] is None

    conn.close()


def test_import_bulk_download_with_receipts_creates_receipts_tables(tmp_path: Path):
    bulk_dir = tmp_path / 'Bulk_download'
    bulk_dir.mkdir(parents=True)

    committees = bulk_dir / 'committees_1.txt'
    committees.write_text(
        'ID\tTypeOfCommittee\tStateCommittee\tStateID\tLocalCommittee\tLocalID\tReferName\tName\tAddress1\tAddress2\tAddress3\tCity\tState\tZip\tStatus\tStatusDate\tCreationDate\tCreationAmount\tDispFundsReturn\tDispFundsPolComm\tDispFundsCharity\tDispFunds95\tDispFundsDescrip\tCanSuppOpp\tPolicySuppOpp\tPartyAffiliation\tPurpose\n'
        '101\tPolitical Action\tFalse\t0\tFalse\t0\tA1\tCommittee A\t123 Main\t\t\tChicago\tIL\t60601\tA\t2024-01-01 00:00:00\t2020-01-01 00:00:00\t1000\tTrue\tFalse\tFalse\tFalse\t\tS\t\tDemocratic\tPurpose A\n'
        '102\tPolitical Party\tFalse\t0\tFalse\t0\tB1\tCommittee B\t456 Oak\t\t\tSpringfield\tIL\t62701\tF\t2024-02-01 00:00:00\t2021-01-01 00:00:00\t2000\tFalse\tTrue\tFalse\tFalse\t\tO\t\tRepublican\tPurpose B\n',
        encoding='utf-8'
    )

    d2 = bulk_dir / 'd2totals_1.txt'
    d2.write_text(
        'ID\tCommitteeID\tFiledDocID\tBegFundsAvail\tIndivContribI\tIndivContribNI\tXferInI\tXferInNI\tLoanRcvI\tLoanRcvNI\tOtherRctI\tOtherRctNI\tTotalReceipts\tInKindI\tInKindNI\tTotalInKind\tXferOutI\tXferOutNI\tLoanMadeI\tLoanMadeNI\tExpendI\tExpendNI\tIndependentExpI\tIndependentExpNI\tTotalExpend\tDebtsI\tDebtsNI\tTotalDebts\tTotalInvest\tEndFundsAvail\tArchived\n'
        '1\t101\t5001\t100\t25\t5\t0\t0\t0\t0\t0\t0\t150\t0\t0\t0\t0\t0\t0\t0\t10\t2\t0\t0\t12\t0\t0\t0\t0\t118\tFalse\n'
        '2\t102\t5002\t200\t35\t15\t0\t0\t0\t0\t0\t0\t75\t0\t0\t0\t0\t0\t0\t0\t20\t1\t0\t0\t21\t0\t0\t0\t0\t229\tTrue\n',
        encoding='utf-8'
    )

    candidates = bulk_dir / 'candidates_1.txt'
    candidates.write_text(
        'ID\tLastName\tFirstName\tAddress1\tAddress2\tCity\tState\tZip\tOffice\tDistrictType\tDistrict\tResidenceCounty\tPartyAffiliation\tRedactionRequested\n'
        '201\tSmith\tJordan\t1 Pine\t\tChicago\tIL\t60610\tGovernor\tStatewide\tAt-Large\tCook\tDemocratic\tFalse\n'
        '202\tJones\tCasey\t2 Elm\t\tSpringfield\tIL\t62702\tMayor\tMunicipal\t7\tSangamon\tIndependent\tTrue\n',
        encoding='utf-8'
    )

    links = bulk_dir / 'cmtecandidatelinks_1.txt'
    links.write_text(
        'ID\tCommitteeID\tCandidateID\n'
        '9001\t101\t201\n'
        '9002\t102\t202\n',
        encoding='utf-8'
    )

    receipts = bulk_dir / 'receipts_1.txt'
    receipts.write_text(
        'ID\tCommitteeID\tFiledDocID\tETransID\tLastOnlyName\tFirstName\tRcvDate\tAmount\tAggregateAmount\tLoanAmount\tOccupation\tEmployer\tAddress1\tAddress2\tCity\tState\tZip\tD2Part\tDescription\tVendorLastOnlyName\tVendorFirstName\tVendorAddress1\tVendorAddress2\tVendorCity\tVendorState\tVendorZip\tArchived\tCountry\tRedactionRequested\n'
        '3001\t101\t5001\t\tBishop\tElizabeth\t2025-01-10 00:00:00\t100\t0\t0\tPolitics\tCity of LaSalle\t1 Main\t\tLaSalle\tIL\t61301\t1A\t\t\t\t\t\t\t\t\tFalse\t\tFalse\n'
        '3002\t101\t5001\t\tVendor\tOffice\t2025-01-11 00:00:00\t50\t0\t0\t\t\t2 Main\t\tChicago\tIL\t60601\t5A\tPrinting\tInk Co\t\t2 Main\t\tChicago\tIL\t60601\tFalse\t\tFalse\n'
        '3003\t102\t5002\t\tPeople PAC\t\t2025-01-12 00:00:00\t75\t0\t0\t\t\t3 Main\t\tSpringfield\tIL\t62701\t2A\t\t\t\t\t\t\t\t\tTrue\tUS\tTrue\n'
        '3004\t999\t9999\t\tGhost\tDonor\t2025-01-13 00:00:00\t20\t0\t0\t\t\t4 Main\t\tNowhere\tIL\t60000\t1A\t\t\t\t\t\t\t\t\tFalse\t\tFalse\n',
        encoding='utf-8'
    )

    db_path = str(tmp_path / 'test_bulk_receipts.db')
    init_db(db_path)
    conn = get_db(db_path)

    stats = import_bulk_download(conn, bulk_dir)
    assert stats['committees_loaded'] == 2
    assert stats['d2_totals_loaded'] == 2
    assert stats['candidates_loaded'] == 2
    assert stats['cmte_candidate_links_loaded'] == 2
    assert stats['receipts_loaded'] == 4
    assert stats['committee_receipts_rows'] == 4
    assert stats['d2_receipts_recon_rows'] == 2
    assert stats['candidate_committee_receipts_agg_rows'] == 2
    assert stats['unmatched_receipts_committee_ids'] == 1
    assert stats['unmatched_receipts_d2_filed_docs'] == 1

    receipt = conn.execute(
        """
        SELECT occupation, employer, d2_part_code, amount
        FROM bulk_receipts_clean
        WHERE receipt_record_id = 3001
        """
    ).fetchone()
    assert receipt['occupation'] == 'Politics'
    assert receipt['employer'] == 'City of LaSalle'
    assert receipt['d2_part_code'] == '1A'
    assert receipt['amount'] == 100.0

    recon = conn.execute(
        """
        SELECT receipt_row_count, receipts_amount_sum, receipts_minus_d2_total
        FROM bulk_d2_receipts_recon
        WHERE committee_id_sbe = 101 AND filed_doc_id = 5001
        """
    ).fetchone()
    assert recon['receipt_row_count'] == 2
    assert recon['receipts_amount_sum'] == 150.0
    assert recon['receipts_minus_d2_total'] == 0.0

    recon_archived = conn.execute(
        """
        SELECT receipt_row_count, receipts_amount_sum, receipts_minus_d2_total
        FROM bulk_d2_receipts_recon
        WHERE committee_id_sbe = 102 AND filed_doc_id = 5002
        """
    ).fetchone()
    assert recon_archived['receipt_row_count'] == 0
    assert recon_archived['receipts_amount_sum'] == 0.0
    assert recon_archived['receipts_minus_d2_total'] == -75.0

    candidate_receipts = conn.execute(
        """
        SELECT receipt_count, sum_receipt_amount, sum_amount_part_1_contributions, sum_amount_part_5_expenditures, archived_receipt_count
        FROM bulk_candidate_committee_receipts_agg
        WHERE candidate_id = 201 AND committee_id_sbe = 101
        """
    ).fetchone()
    assert candidate_receipts['receipt_count'] == 2
    assert candidate_receipts['sum_receipt_amount'] == 150.0
    assert candidate_receipts['sum_amount_part_1_contributions'] == 100.0
    assert candidate_receipts['sum_amount_part_5_expenditures'] == 50.0
    assert candidate_receipts['archived_receipt_count'] == 0

    candidate_receipts_archived = conn.execute(
        """
        SELECT receipt_count, sum_receipt_amount, archived_receipt_count
        FROM bulk_candidate_committee_receipts_agg
        WHERE candidate_id = 202 AND committee_id_sbe = 102
        """
    ).fetchone()
    assert candidate_receipts_archived['receipt_count'] == 0
    assert candidate_receipts_archived['sum_receipt_amount'] == 0.0
    assert candidate_receipts_archived['archived_receipt_count'] == 1

    conn.close()
