"""Admin routes for local donor merge review and approvals."""
from flask import Blueprint, render_template, request, current_app, abort, redirect, url_for, flash

from database.federal_fec import get_federal_receipt_mismatch_flags
from database.models import Donor
from webapp.auth import login_required


admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/federal-receipt-audit")
@login_required
def federal_receipt_audit():
    """Internal audit view for FEC reported totals vs synced Schedule A subtotals."""
    conn = current_app.get_database()

    cycle_raw = (request.args.get("cycle") or "").strip()
    cycle = 2026
    if cycle_raw:
        try:
            parsed_cycle = int(cycle_raw)
            if 1970 <= parsed_cycle <= 2100:
                cycle = parsed_cycle
        except ValueError:
            cycle = 2026

    status = (_clean_text(request.args.get("status")) or "flagged").lower()
    min_abs_diff = request.args.get("min_abs_diff", 1000.0, type=float)
    tolerance = request.args.get("tolerance", 0.01, type=float)

    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 100
    offset = (page - 1) * per_page

    audit = get_federal_receipt_mismatch_flags(
        conn,
        cycle=cycle,
        status=status,
        min_abs_diff=max(0.0, float(min_abs_diff or 0.0)),
        tolerance=max(0.0, float(tolerance or 0.0)),
        limit=per_page,
        offset=offset,
    )
    total_rows = int(audit.get("total_rows") or 0)
    total_pages = (total_rows + per_page - 1) // per_page if total_rows else 0

    return render_template(
        "admin/federal_receipt_audit.html",
        rows=audit.get("rows", []),
        status_counts=audit.get("status_counts", {}),
        cycle=cycle,
        status=audit.get("status", status),
        min_abs_diff=float(audit.get("min_abs_diff") or 0.0),
        tolerance=float(audit.get("tolerance") or 0.0),
        page=page,
        total_rows=total_rows,
        total_pages=total_pages,
    )


@admin_bp.route("/donor-merges")
@login_required
def donor_merge_queue():
    """List local donor entities that need merge approval review."""
    conn = current_app.get_database()

    source = (request.args.get("source") or "").strip() or "bulk_receipts"
    status = Donor._normalize_review_status_filter(request.args.get("status"))
    query = (request.args.get("q") or "").strip()
    sort_by = (request.args.get("sort") or "total_amount").strip()
    sort_dir = "asc" if (request.args.get("dir") or "").strip().lower() == "asc" else "desc"

    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 50
    offset = (page - 1) * per_page

    entities = Donor.get_local_entity_review_queue(
        conn,
        source=source,
        status=status,
        query=query,
        limit=per_page,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    total = Donor.count_local_entity_review_queue(
        conn,
        source=source,
        status=status,
        query=query,
    )
    status_counts = Donor.get_local_entity_review_status_counts(conn, source=source)
    total_pages = (total + per_page - 1) // per_page if total else 0

    return render_template(
        "admin/donor_merges_list.html",
        entities=entities,
        source=source,
        status=status,
        status_counts=status_counts,
        query=query,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        total=total,
        total_pages=total_pages,
    )


@admin_bp.route("/donor-merges/<path:entity_id>")
@login_required
def donor_merge_detail(entity_id):
    """Show detail and member rows for one local donor merge entity."""
    conn = current_app.get_database()
    source = (request.args.get("source") or "").strip() or "bulk_receipts"

    entity = Donor.get_local_entity_review_entity(conn, entity_id=entity_id, source=source)
    if not entity:
        abort(404)

    members = Donor.get_local_entity_review_members(conn, entity_id=entity_id, source=source)
    return render_template(
        "admin/donor_merge_detail.html",
        entity=entity,
        members=members,
        source=source,
    )


@admin_bp.route("/donor-merges/<path:entity_id>/decision", methods=["POST"])
@login_required
def donor_merge_decision(entity_id):
    """Apply an approval/rejection decision to an entity or one member row."""
    conn = current_app.get_database()

    source = (request.form.get("source") or "").strip() or "bulk_receipts"
    decision = (request.form.get("decision") or "").strip().lower()
    donor_key = (request.form.get("donor_key") or "").strip() or None

    if decision not in {"pending", "approved", "rejected", "not_needed"}:
        flash("Invalid review decision.", "error")
        return redirect(url_for("admin.donor_merge_detail", entity_id=entity_id, source=source))

    updated_count = Donor.update_local_entity_review_status(
        conn,
        source=source,
        entity_id=entity_id,
        review_status=decision,
        donor_key=donor_key,
    )
    if updated_count <= 0:
        flash("No rows were updated.", "error")
    else:
        label = "member row" if donor_key else "entity members"
        flash(f"Updated {updated_count} {label} to {decision}.", "success")

    return redirect(url_for("admin.donor_merge_detail", entity_id=entity_id, source=source))


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())
