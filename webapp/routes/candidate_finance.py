"""Candidate/committee aggregate finance routes."""
import csv
from datetime import date
from io import StringIO
import threading
import time

from flask import Blueprint, Response, render_template, request, current_app

from database.models import (
    CandidateCommitteeFinanceAgg,
    CandidateCommitteeItemizedExpenditure,
    CandidateCommitteeItemizedReceipt,
)
from webapp.utils.time_filter import get_active_period, period_cycle, period_to_date_window

_candidate_finance_cache: dict = {"payload": None, "expires_at": 0.0, "key": None}
_candidate_finance_cache_lock = threading.Lock()

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


def _parse_iso_date(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


@candidate_finance_bp.route('/')
def list_candidate_finance():
    """List candidate-committee aggregate finance rows from bulk imports."""
    conn = current_app.get_database()
    period = get_active_period()

    page = max(request.args.get('page', 1, type=int), 1)
    per_page = 50
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'sum_total_receipts')
    sort_dir = request.args.get('dir', 'desc')
    query = request.args.get('q', '').strip()
    office = request.args.get('office', '').strip()
    candidate_party = request.args.get('candidate_party', '').strip()
    committee_party = request.args.get('committee_party', '').strip()
    start_date_raw = request.args.get('start_date', '').strip()
    end_date_raw = request.args.get('end_date', '').strip()
    year_raw = request.args.get('year', '').strip()
    cycle_raw = request.args.get('cycle', '').strip()
    min_receipts_raw = request.args.get('min_receipts', '').strip()
    min_expenditures_raw = request.args.get('min_expenditures', '').strip()

    resolved_start_date, resolved_end_date = period_to_date_window(period, start_date_raw, end_date_raw)
    start_date = _parse_iso_date(resolved_start_date or "")
    end_date = _parse_iso_date(resolved_end_date or "")
    year = _parse_int(year_raw)
    cycle = _parse_int(cycle_raw)
    if cycle is None:
        cycle = period_cycle(period)
    min_receipts = _parse_float(min_receipts_raw)
    min_expenditures = _parse_float(min_expenditures_raw)
    output_format = request.args.get('format', 'html').strip().lower()

    table_available = CandidateCommitteeFinanceAgg.is_available(conn)
    finance_source_type = CandidateCommitteeFinanceAgg.source_type(conn)
    itemized_available = CandidateCommitteeItemizedReceipt.is_available(conn)
    period_values = {"years": [], "cycles": []}

    # --- TTL cache for expensive view-backed queries ---
    cache_ttl = 180  # 3 minutes
    cache_enabled = bool(current_app.config.get("ROUTE_PERF_CACHE_ENABLED", not current_app.config.get("TESTING", False)))
    cache_key = (
        page, sort_by, sort_dir, query, office, candidate_party, committee_party,
        start_date, end_date, year, cycle, min_receipts, min_expenditures,
    )
    now = time.monotonic()

    cached = None
    if cache_enabled:
        with _candidate_finance_cache_lock:
            if (
                _candidate_finance_cache.get("payload") is not None
                and _candidate_finance_cache.get("key") == cache_key
                and float(_candidate_finance_cache.get("expires_at", 0.0)) > now
            ):
                cached = _candidate_finance_cache["payload"]

    rows = []
    total = 0
    total_pages = 0
    if cached is not None:
        rows = cached["rows"]
        total = cached["total"]
        total_pages = cached["total_pages"]
        period_values = cached["period_values"]
    elif table_available:
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
            start_date=start_date,
            end_date=end_date,
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
            start_date=start_date,
            end_date=end_date,
            year=year,
            cycle=cycle,
            min_receipts=min_receipts,
            min_expenditures=min_expenditures,
        )
        total_pages = (total + per_page - 1) // per_page
        if cache_enabled:
            with _candidate_finance_cache_lock:
                _candidate_finance_cache["payload"] = {
                    "rows": rows,
                    "total": total,
                    "total_pages": total_pages,
                    "period_values": period_values,
                }
                _candidate_finance_cache["key"] = cache_key
                _candidate_finance_cache["expires_at"] = now + float(cache_ttl)

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
            start_date=start_date,
            end_date=end_date,
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
        start_date=start_date_raw,
        end_date=end_date_raw,
        year=year_raw,
        cycle=cycle_raw or (str(cycle) if cycle is not None else ""),
        available_years=period_values["years"],
        available_cycles=period_values["cycles"],
        min_receipts=min_receipts_raw,
        min_expenditures=min_expenditures_raw,
        table_available=table_available,
        finance_source_type=finance_source_type,
        itemized_available=itemized_available,
    )


@candidate_finance_bp.route('/<int:candidate_id>/<int:committee_id>/itemized')
def candidate_committee_itemized(candidate_id: int, committee_id: int):
    """Show itemized receipt rows for one candidate/committee pair."""
    conn = current_app.get_database()
    period = get_active_period()

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
    explicit_date_from = request.args.get('date_from', '').strip()
    explicit_date_to = request.args.get('date_to', '').strip()
    date_from, date_to = period_to_date_window(period, explicit_date_from, explicit_date_to)

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
                date_from=date_from,
                date_to=date_to,
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
                date_from=date_from,
                date_to=date_to,
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
            date_from=date_from,
            date_to=date_to,
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


@candidate_finance_bp.route('/<int:candidate_id>/<int:committee_id>/itemized-expenditures')
def candidate_committee_itemized_expenditures(candidate_id: int, committee_id: int):
    """Show itemized expenditure rows for one candidate/committee pair."""
    conn = current_app.get_database()
    period = get_active_period()

    page = max(request.args.get('page', 1, type=int), 1)
    per_page = 100
    offset = (page - 1) * per_page

    sort_by = request.args.get('sort', 'expended_date')
    sort_dir = request.args.get('dir', 'desc')
    query = request.args.get('q', '').strip()
    d2_part = request.args.get('d2_part', '').strip()
    archived = request.args.get('archived', 'no').strip().lower()
    anomalies_only = request.args.get('anomalies_only', 'no').strip().lower()
    min_amount_raw = request.args.get('min_amount', '').strip()
    max_amount_raw = request.args.get('max_amount', '').strip()
    output_format = request.args.get('format', 'html').strip().lower()
    explicit_date_from = request.args.get('date_from', '').strip()
    explicit_date_to = request.args.get('date_to', '').strip()
    date_from, date_to = period_to_date_window(period, explicit_date_from, explicit_date_to)

    min_amount = _parse_float(min_amount_raw)
    max_amount = _parse_float(max_amount_raw)

    table_available = CandidateCommitteeItemizedExpenditure.is_available(conn)
    context = None
    rows = []
    total = 0
    total_pages = 0

    if table_available:
        context = CandidateCommitteeItemizedExpenditure.get_context(conn, candidate_id, committee_id)
        if context:
            rows = CandidateCommitteeItemizedExpenditure.get_all(
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
                anomalies_only=anomalies_only,
                date_from=date_from,
                date_to=date_to,
            )
            total = CandidateCommitteeItemizedExpenditure.count(
                conn,
                candidate_id=candidate_id,
                committee_id=committee_id,
                search=query,
                d2_part=d2_part,
                min_amount=min_amount,
                max_amount=max_amount,
                archived=archived,
                anomalies_only=anomalies_only,
                date_from=date_from,
                date_to=date_to,
            )
            total_pages = (total + per_page - 1) // per_page

    if output_format == 'csv':
        if not table_available or not context:
            return Response("candidate committee itemized expenditures table unavailable\n", mimetype='text/plain', status=404)

        csv_rows = CandidateCommitteeItemizedExpenditure.get_all(
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
            anomalies_only=anomalies_only,
            date_from=date_from,
            date_to=date_to,
        )
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'expenditure_record_id',
            'committee_id_sbe',
            'candidate_id',
            'candidate_full_name',
            'committee_name',
            'filed_doc_id',
            'expended_date',
            'd2_part_code',
            'payee_name',
            'payee_address',
            'amount',
            'aggregate_amount',
            'purpose',
            'candidate_name',
            'office',
            'is_supporting',
            'is_opposing',
            'is_archived',
            'is_amount_anomalous',
            'anomaly_reason',
            'country',
            'redaction_requested',
        ])
        for row in csv_rows:
            writer.writerow([
                row.expenditure_record_id,
                row.committee_id_sbe,
                context['candidate_id'],
                context['candidate_full_name'],
                context['committee_name'],
                row.filed_doc_id,
                row.expended_date,
                row.d2_part_code,
                row.payee_name,
                row.payee_address,
                row.amount,
                row.aggregate_amount,
                row.purpose,
                row.candidate_name,
                row.office,
                row.is_supporting,
                row.is_opposing,
                row.is_archived,
                row.is_amount_anomalous,
                row.anomaly_reason,
                row.country,
                row.redaction_requested,
            ])
        response = Response(output.getvalue(), mimetype='text/csv')
        response.headers['Content-Disposition'] = (
            f"attachment; filename=candidate_{candidate_id}_committee_{committee_id}_itemized_expenditures.csv"
        )
        return response

    return render_template(
        'candidate_finance/itemized_expenditures.html',
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
        anomalies_only=anomalies_only,
        min_amount=min_amount_raw,
        max_amount=max_amount_raw,
    )
