"""Admin routes for local donor merge review and approvals."""
from flask import Blueprint, render_template, request, current_app, abort, redirect, url_for, flash

from database.federal_fec import (
    get_federal_disbursement_mismatch_flags,
    get_federal_receipt_mismatch_flags,
)
from database import committee_families as families_db
from database.models import Donor
from webapp.auth import login_required


admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/")
@login_required
def admin_hub():
    """Consolidated admin hub with links to all tools."""
    return render_template("admin/hub.html")


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


@admin_bp.route("/federal-disbursement-audit")
@login_required
def federal_disbursement_audit():
    """Internal audit view for FEC reported disbursements vs synced Schedule B subtotals."""
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

    audit = get_federal_disbursement_mismatch_flags(
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
        "admin/federal_disbursement_audit.html",
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


def _evidence_badges(evidence: dict) -> list[str]:
    """Badge text for the evidence flags that hold ("same address"; similar name carries its score)."""
    badges = []
    if evidence.get("same_name"):
        badges.append("same name")
    if evidence.get("similar_name"):
        badges.append(f"similar name {float(evidence['similar_name']):.2f}")
    if evidence.get("shared_officer"):
        badges.append("shared officer")
    if evidence.get("same_address"):
        badges.append("same address")
    return badges


@admin_bp.route("/committee-families")
@login_required
def committee_family_queue():
    """List proposed committee families by review status."""
    conn = current_app.get_database()

    status = (request.args.get("status") or "").strip().lower()
    if status not in families_db.STATUSES:
        status = "pending"

    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 50

    status_counts = families_db.status_counts(conn)
    total = status_counts[status]
    total_pages = (total + per_page - 1) // per_page if total else 0
    rows = families_db.list_families(conn, status=status, limit=per_page, offset=(page - 1) * per_page)
    for row in rows:
        row["badges"] = _evidence_badges(row["evidence"])

    return render_template(
        "admin/committee_families_list.html",
        families=rows,
        status=status,
        status_counts=status_counts,
        page=page,
        total=total,
        total_pages=total_pages,
    )


@admin_bp.route("/committee-families/<int:family_id>")
@login_required
def committee_family_detail(family_id):
    """Show one proposed family: its evidence, members and review actions."""
    conn = current_app.get_database()

    family = families_db.get_family(conn, family_id)
    if not family:
        abort(404)

    members = families_db.family_members(conn, family_id)
    for member in members:
        member["badges"] = _evidence_badges(member["evidence"])
    candidate_names = {c["id"]: c["name"] for m in members for c in m["candidates"]}
    return render_template(
        "admin/committee_family_detail.html",
        family=family,
        badges=_evidence_badges(family["evidence"]),
        shared_candidates=[
            (cid, candidate_names.get(cid, f"Candidate {cid}"))
            for cid in family["evidence"].get("shared_candidates", [])
        ],
        members=members,
    )


@admin_bp.route("/committee-families/<int:family_id>/decision", methods=["POST"])
@login_required
def committee_family_decision(family_id):
    """Accept or reject a family, or remove one committee from it."""
    conn = current_app.get_database()

    family = families_db.get_family(conn, family_id)
    if not family:
        abort(404)

    decision = (request.form.get("decision") or "").strip().lower()
    detail = redirect(url_for("admin.committee_family_detail", family_id=family_id))

    if decision == "remove_member":
        committee_id = request.form.get("committee_id", type=int)
        if committee_id is None:
            flash("Choose a committee to remove.", "error")
            return detail
        refusal = families_db.remove_member(conn, family_id, committee_id)
        if refusal:
            flash(refusal, "error")
        else:
            conn.commit()
            flash(f"Removed committee {committee_id} from the family.", "success")
        return detail

    if decision not in {"accepted", "rejected"}:
        flash("Invalid review decision.", "error")
        return detail

    if decision == "accepted":
        conflicts = families_db.accept_conflicts(conn, family_id)
        if conflicts:
            clashes = "; ".join(
                f"committee {c['committee_id']} is already in the accepted family \"{c['label']}\"" for c in conflicts
            )
            flash(f"Cannot accept: {clashes}. A committee can belong to only one accepted family.", "error")
            return detail

    families_db.set_status(conn, family_id, decision)
    conn.commit()
    flash(f"Family marked {decision}.", "success")
    return detail


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())
