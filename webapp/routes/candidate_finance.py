"""Candidate/committee aggregate finance routes."""
import csv
from io import StringIO

from flask import Blueprint, Response, render_template, request, current_app

from database.models import CandidateCommitteeFinanceAgg, CandidateCommitteeItemizedReceipt

candidate_finance_bp = Blueprint('candidate_finance', __name__)


def _parse_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


@candidate_finance_bp.route('/')
def list_candidate_finance():
    """List candidate-committee aggregate finance rows from bulk imports."""
    conn = current_app.get_database()

    page = max(request.args.get('page', 1, type=int), 1)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'sum_total_receipts')
    sort_dir = request.args.get('dir', 'desc')
    query = request.args.get('q', '').strip()
    office = request.args.get('office', '').strip()
    candidate_party = request.args.get('candidate_party', '').strip()
    committee_party = request.args.get('committee_party', '').strip()
    year_raw = request.args.get('year', '').strip()
    cycle_raw = request.args.get('cycle', '').strip()
    min_receipts_raw = request.args.get('min_receipts', '').strip()
    min_expenditures_raw = request.args.get('min_expenditures', '').strip()

    year = _parse_int(year_raw)
    cycle = _parse_int(cycle_raw)
    min_receipts = _parse_float(min_receipts_raw)
    min_expenditures = _parse_float(min_expenditures_raw)
    output_format = request.args.get('format', 'html').strip().lower()

    table_available = CandidateCommitteeFinanceAgg.is_available(conn)
    period_values = {"years": [], "cycles": []}

    rows = []
    total = 0
    total_pages = 0
    if table_available:
        period_values = CandidateCommitteeFinanceAgg.list_period_values(conn)
        rows = CandidateCommitteeFinanceAgg.get_all(
            conn,
            limit=per_page,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=query,
            office=office,
            candidate_party=candidate_party,
            committee_party=committee_party,
            year=year,
            cycle=cycle,
            min_receipts=min_receipts,
            min_expenditures=min_expenditures,
        )
        total = CandidateCommitteeFinanceAgg.count(
            conn,
            search=query,
            office=office,
            candidate_party=candidate_party,
            committee_party=committee_party,
            year=year,
            cycle=cycle,
            min_receipts=min_receipts,
            min_expenditures=min_expenditures,
        )
        total_pages = (total + per_page - 1) // per_page

    if output_format == 'csv':
        if not table_available:
            return Response("candidate finance table unavailable\n", mimetype='text/plain', status=404)

        csv_rows = CandidateCommitteeFinanceAgg.get_all(
            conn,
            limit=500000,
            offset=0,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=query,
            office=office,
            candidate_party=candidate_party,
            committee_party=committee_party,
            year=year,
            cycle=cycle,
            min_receipts=min_receipts,
            min_expenditures=min_expenditures,
        )
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'candidate_id',
            'candidate_full_name',
            'office_sought',
            'district_type',
            'district',
            'candidate_party_affiliation',
            'committee_id_sbe',
            'committee_name',
            'committee_type',
            'committee_party_affiliation',
            'period_year',
            'election_cycle',
            'filing_count',
            'sum_total_receipts',
            'sum_total_expenditures',
            'max_ending_funds_available',
            'archived_filing_count',
            'period_start_date',
            'period_end_date',
        ])
        for row in csv_rows:
            writer.writerow([
                row.candidate_id,
                row.candidate_full_name,
                row.office_sought,
                row.district_type,
                row.district,
                row.candidate_party_affiliation,
                row.committee_id_sbe,
                row.committee_name,
                row.committee_type,
                row.committee_party_affiliation,
                row.period_year,
                row.election_cycle,
                row.filing_count,
                row.sum_total_receipts,
                row.sum_total_expenditures,
                row.max_ending_funds_available,
                row.archived_filing_count,
                row.period_start_date,
                row.period_end_date,
            ])

        response = Response(output.getvalue(), mimetype='text/csv')
        response.headers['Content-Disposition'] = 'attachment; filename=candidate_committee_finance.csv'
        return response

    return render_template(
        'candidate_finance/list.html',
        rows=rows,
        page=page,
        total=total,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        query=query,
        office=office,
        candidate_party=candidate_party,
        committee_party=committee_party,
        year=year_raw,
        cycle=cycle_raw,
        available_years=period_values["years"],
        available_cycles=period_values["cycles"],
        min_receipts=min_receipts_raw,
        min_expenditures=min_expenditures_raw,
        table_available=table_available,
    )


@candidate_finance_bp.route('/<int:candidate_id>/<int:committee_id>/itemized')
def candidate_committee_itemized(candidate_id: int, committee_id: int):
    """Show itemized receipt rows for one candidate/committee pair."""
    conn = current_app.get_database()

    page = max(request.args.get('page', 1, type=int), 1)
    per_page = 100
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'received_date')
    sort_dir = request.args.get('dir', 'desc')
    query = request.args.get('q', '').strip()
    d2_part = request.args.get('d2_part', '').strip()
    archived = request.args.get('archived', 'no').strip().lower()
    min_amount_raw = request.args.get('min_amount', '').strip()
    max_amount_raw = request.args.get('max_amount', '').strip()
    output_format = request.args.get('format', 'html').strip().lower()

    min_amount = _parse_float(min_amount_raw)
    max_amount = _parse_float(max_amount_raw)

    table_available = CandidateCommitteeItemizedReceipt.is_available(conn)
    context = None
    rows = []
    total = 0
    total_pages = 0

    if table_available:
        context = CandidateCommitteeItemizedReceipt.get_context(conn, candidate_id, committee_id)
        if context:
            rows = CandidateCommitteeItemizedReceipt.get_all(
                conn,
                candidate_id=candidate_id,
                committee_id=committee_id,
                limit=per_page,
                offset=offset,
                sort_by=sort_by,
                sort_dir=sort_dir,
                search=query,
                d2_part=d2_part,
                min_amount=min_amount,
                max_amount=max_amount,
                archived=archived,
            )
            total = CandidateCommitteeItemizedReceipt.count(
                conn,
                candidate_id=candidate_id,
                committee_id=committee_id,
                search=query,
                d2_part=d2_part,
                min_amount=min_amount,
                max_amount=max_amount,
                archived=archived,
            )
            total_pages = (total + per_page - 1) // per_page

    if output_format == 'csv':
        if not table_available or not context:
            return Response("candidate committee itemized table unavailable\n", mimetype='text/plain', status=404)

        csv_rows = CandidateCommitteeItemizedReceipt.get_all(
            conn,
            candidate_id=candidate_id,
            committee_id=committee_id,
            limit=500000,
            offset=0,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=query,
            d2_part=d2_part,
            min_amount=min_amount,
            max_amount=max_amount,
            archived=archived,
        )
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'receipt_record_id',
            'committee_id_sbe',
            'candidate_id',
            'candidate_full_name',
            'committee_name',
            'filed_doc_id',
            'received_date',
            'd2_part_code',
            'donor_name',
            'occupation',
            'employer',
            'donor_address',
            'amount',
            'aggregate_amount',
            'loan_amount',
            'description',
            'vendor_name',
            'vendor_address',
            'is_archived',
            'country',
            'redaction_requested',
        ])
        for row in csv_rows:
            writer.writerow([
                row.receipt_record_id,
                row.committee_id_sbe,
                context['candidate_id'],
                context['candidate_full_name'],
                context['committee_name'],
                row.filed_doc_id,
                row.received_date,
                row.d2_part_code,
                row.donor_name,
                row.occupation,
                row.employer,
                row.donor_address,
                row.amount,
                row.aggregate_amount,
                row.loan_amount,
                row.description,
                row.vendor_name,
                row.vendor_address,
                row.is_archived,
                row.country,
                row.redaction_requested,
            ])
        response = Response(output.getvalue(), mimetype='text/csv')
        response.headers['Content-Disposition'] = (
            f"attachment; filename=candidate_{candidate_id}_committee_{committee_id}_itemized.csv"
        )
        return response

    return render_template(
        'candidate_finance/itemized.html',
        candidate_id=candidate_id,
        committee_id=committee_id,
        table_available=table_available,
        context=context,
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        query=query,
        d2_part=d2_part,
        archived=archived,
        min_amount=min_amount_raw,
        max_amount=max_amount_raw,
    )
