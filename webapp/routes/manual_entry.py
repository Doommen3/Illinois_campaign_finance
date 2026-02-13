"""Manual entry routes for paper-filed reports."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, abort

from database.models import Report, ManualEntryQueue, Donor, Contribution
from scraper.donor_normalizer import DonorNormalizer
from scraper.text_parsing import parse_contributor_metadata
from webapp.auth import login_required

manual_entry_bp = Blueprint('manual_entry', __name__)


@manual_entry_bp.route('/')
@login_required
def queue():
    """Show the manual entry queue."""
    conn = current_app.get_database()

    status = request.args.get('status', 'pending')
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    items = ManualEntryQueue.get_queue(conn, status=status, limit=per_page, offset=offset)
    status_counts = ManualEntryQueue.count_by_status(conn)

    total = status_counts.get(status, 0)
    total_pages = (total + per_page - 1) // per_page

    return render_template('manual_entry/queue.html',
                           items=items,
                           status=status,
                           status_counts=status_counts,
                           page=page,
                           total_pages=total_pages)


@manual_entry_bp.route('/<int:queue_id>', methods=['GET', 'POST'])
@login_required
def entry_form(queue_id):
    """Form for entering contributions manually."""
    conn = current_app.get_database()

    # Get queue item
    cursor = conn.execute("""
        SELECT m.*, c.name as committee_name, r.report_type, r.filed_date, r.reporting_period
        FROM manual_entry_queue m
        JOIN reports r ON m.report_id = r.id
        JOIN committees c ON r.committee_id = c.id
        WHERE m.id = ?
    """, (queue_id,))
    row = cursor.fetchone()

    if not row:
        abort(404)

    queue_item = {
        'id': row['id'],
        'report_id': row['report_id'],
        'status': row['status'],
        'notes': row['notes'],
        'committee_name': row['committee_name'],
        'report_type': row['report_type'],
        'filed_date': row['filed_date'],
        'reporting_period': row['reporting_period']
    }

    # Get existing contributions for this report
    contributions = Contribution.get_by_report(conn, queue_item['report_id'])

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_contribution':
            # Add a new contribution
            contributed_by = request.form.get('contributed_by', '').strip()
            address = request.form.get('address', '').strip()
            amount_str = request.form.get('amount', '').strip()
            received_by = request.form.get('received_by', '').strip()
            description = request.form.get('description', '').strip()

            if contributed_by and amount_str:
                try:
                    amount = float(amount_str.replace(',', '').replace('$', ''))

                    # Normalize donor info
                    parsed_name, occupation, employer = parse_contributor_metadata(contributed_by)
                    normalizer = DonorNormalizer()
                    norm_name, norm_address = normalizer.normalize(parsed_name, address)

                    # Get or create donor
                    donor = Donor.get_or_create(
                        conn,
                        name=parsed_name,
                        address=address,
                        normalized_name=norm_name,
                        normalized_address=norm_address,
                        occupation=occupation,
                        employer=employer
                    )

                    # Create contribution
                    contribution = Contribution(
                        report_id=queue_item['report_id'],
                        donor_id=donor.id,
                        amount=amount,
                        received_by=received_by if received_by else None,
                        description=description if description else None,
                        raw_contributed_by=contributed_by,
                        raw_address=address,
                        raw_occupation=occupation,
                        raw_employer=employer
                    )
                    contribution.save(conn)

                    flash('Contribution added successfully.', 'success')
                except ValueError:
                    flash('Invalid amount format.', 'error')
            else:
                flash('Contributor name and amount are required.', 'error')

            return redirect(url_for('manual_entry.entry_form', queue_id=queue_id))

        elif action == 'mark_complete':
            # Mark the queue item as complete
            conn.execute("""
                UPDATE manual_entry_queue
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (queue_id,))

            # Update report status
            conn.execute("""
                UPDATE reports SET scrape_status = 'scraped' WHERE id = ?
            """, (queue_item['report_id'],))

            conn.commit()

            flash('Report marked as complete.', 'success')
            return redirect(url_for('manual_entry.queue'))

        elif action == 'delete_contribution':
            contribution_id = request.form.get('contribution_id')
            if contribution_id:
                cursor = conn.execute(
                    """
                    DELETE FROM contributions
                    WHERE id = ?
                      AND report_id = ?
                    """,
                    (contribution_id, queue_item['report_id']),
                )
                conn.commit()
                if cursor.rowcount > 0:
                    flash('Contribution deleted.', 'success')
                else:
                    flash('Contribution could not be deleted for this report.', 'error')

            return redirect(url_for('manual_entry.entry_form', queue_id=queue_id))

    # Refresh contributions after any changes
    contributions = Contribution.get_by_report(conn, queue_item['report_id'])

    return render_template('manual_entry/form.html',
                           queue_item=queue_item,
                           contributions=contributions)
