"""CLI commands for Illinois Campaign Finance Tracker."""
import asyncio
import click
import logging
import sys
from pathlib import Path

from database.connection import get_db, init_db
from database.models import Report, ScrapeState, AppUser
from database.maintenance import (
    find_garbage_committees,
    delete_committees_and_related,
    normalize_donor_metadata,
    data_quality_summary,
    find_reports_for_detail_rescrape,
    requeue_reports_for_detail_scrape,
)
from database.bulk_download_loader import import_bulk_download
from database.analytics import (
    build_dashboard_full_snapshot,
    refresh_analytics_materialized,
    save_dashboard_snapshot,
)
from database.federal_fec import (
    backfill_fec_missing_schedule_a,
    backfill_fec_schedule_b,
    backfill_fec_schedule_e,
    rebuild_fec_donor_identities,
    refresh_fec_local_donor_matches,
    sync_il_federal_fec,
)
from database.local_donor_entities import rebuild_local_donor_entities
from database.lobbying_loader import load_lobbying_csv
from database.irs527_loader import load_irs527_full_file
from database.cross_matching import (
    match_lobbying_to_donors,
    match_lobbying_to_expenditure_payees,
    match_527_to_committees,
    match_527_expenditures_to_committees,
    match_527_directors_to_donors,
    match_lobbying_to_527,
    run_all_cross_matching,
)
from scraper.main_list_scraper import MainListScraper
from scraper.detail_scraper import DetailScraper
from scraper.committee_scraper import CommitteeReportScraper, D2DetailScraper, CommitteeUrlSeeder
from scraper.rate_limiter import RateLimiter
import config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _resolve_internal_committee_ids(conn, committee_ids, committee_ids_sbe):
    """Resolve internal committee IDs from optional internal + SBE ID filters."""
    resolved = list(committee_ids or [])
    unresolved_sbe = []

    if committee_ids_sbe:
        unique_sbe = list(dict.fromkeys(committee_ids_sbe))
        placeholders = ",".join(["?"] * len(unique_sbe))
        rows = conn.execute(
            f"""
            SELECT id, committee_id_sbe
            FROM committees
            WHERE committee_id_sbe IN ({placeholders})
            ORDER BY id ASC
            """,
            unique_sbe,
        ).fetchall()

        by_sbe = {}
        for row in rows:
            by_sbe.setdefault(row["committee_id_sbe"], []).append(row["id"])

        for sbe_id in unique_sbe:
            ids = by_sbe.get(sbe_id, [])
            if ids:
                resolved.extend(ids)
            else:
                unresolved_sbe.append(sbe_id)

    deduped = list(dict.fromkeys(resolved))
    return deduped or None, unresolved_sbe


def _count_pending_d2_details(conn, committee_ids=None):
    """Count pending D-2 detail rows, optionally filtered by committee IDs."""
    query = "SELECT COUNT(*) AS count FROM d2_reports WHERE detail_scrape_status = 'pending'"
    params = []
    if committee_ids:
        placeholders = ",".join(["?"] * len(committee_ids))
        query += f" AND committee_id IN ({placeholders})"
        params.extend(committee_ids)
    return conn.execute(query, params).fetchone()["count"]


def _count_pending_d2_itemized_links(conn, committee_ids=None):
    """Count pending D-2 itemized links, optionally filtered by committee IDs."""
    if committee_ids:
        placeholders = ",".join(["?"] * len(committee_ids))
        query = f"""
            SELECT COUNT(*) AS count
            FROM d2_itemized_links l
            JOIN d2_reports d2 ON d2.id = l.d2_report_id
            WHERE l.status = 'pending'
              AND d2.committee_id IN ({placeholders})
        """
        return conn.execute(query, committee_ids).fetchone()["count"]

    return conn.execute(
        "SELECT COUNT(*) AS count FROM d2_itemized_links WHERE status = 'pending'"
    ).fetchone()["count"]


@click.group()
def cli():
    """Illinois Campaign Finance Tracker CLI."""
    pass


@cli.command('init-db')
def init_db_command():
    """Initialize the database with schema."""
    click.echo('Initializing database...')
    try:
        init_db(config.DATABASE_PATH)
        click.echo(f'Database initialized at: {config.DATABASE_PATH}')
    except Exception as e:
        click.echo(f'Error initializing database: {e}', err=True)
        sys.exit(1)


@cli.command('create-user')
@click.option('--username', prompt=True, help='Username for manual entry login')
@click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True,
              help='Password for manual entry login')
@click.option('--inactive', is_flag=True, help='Create user as inactive')
def create_user_command(username, password, inactive):
    """Create or update a manual-entry web user."""
    conn = get_db(config.DATABASE_PATH)
    try:
        user = AppUser.create_or_update_password(
            conn,
            username=username.strip(),
            password=password,
            is_active=not inactive,
        )
        status = 'active' if user.is_active else 'inactive'
        click.echo(f'User "{user.username}" saved ({status}).')
    except Exception as e:
        click.echo(f'Error creating user: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('import-bulk-download')
@click.option('--directory', default='Bulk_download', show_default=True,
              help='Directory containing bulk TXT download files')
@click.option('--refresh-analytics/--skip-refresh-analytics', default=True, show_default=True,
              help='Rebuild materialized analytics after import')
def import_bulk_download_command(directory, refresh_analytics):
    """Import and normalize bulk committees/D2/candidate/link/receipts/expenditures files into joined tables."""
    conn = get_db(config.DATABASE_PATH)
    try:
        results = import_bulk_download(conn, Path(directory))
        click.echo('Bulk download import completed:')
        for key, value in results.items():
            click.echo(f'  {key}: {value}')
        if refresh_analytics:
            click.echo('Refreshing materialized analytics tables...')
            analytics_stats = refresh_analytics_materialized(conn)
            for key, value in analytics_stats.items():
                click.echo(f'  analytics_{key}: {value}')
    except Exception as e:
        click.echo(f'Error importing bulk download files: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('clean-data')
@click.option('--apply', is_flag=True, help='Apply changes (default is dry-run)')
@click.option('--only', 'scope', type=click.Choice(['all', 'committees', 'donors']),
              default='all', show_default=True,
              help='Limit cleanup scope')
def clean_data_command(apply, scope):
    """Clean garbage committee rows and normalize donor occupation/employer metadata."""
    conn = get_db(config.DATABASE_PATH)
    try:
        if scope in ('all', 'committees'):
            garbage_rows = find_garbage_committees(conn)
            click.echo(f'Garbage committees found: {len(garbage_rows)}')
            for row in garbage_rows[:20]:
                click.echo(f'  - [{row["id"]}] {row["name"]!r}')
            if len(garbage_rows) > 20:
                click.echo(f'  ... and {len(garbage_rows) - 20} more')

            if apply and garbage_rows:
                stats = delete_committees_and_related(conn, [row['id'] for row in garbage_rows])
                click.echo('Deleted garbage committees and related rows:')
                for key, value in stats.items():
                    click.echo(f'  {key}: {value}')

        if scope in ('all', 'donors'):
            if apply:
                donor_stats = normalize_donor_metadata(conn)
                click.echo('Donor metadata normalization complete:')
                for key, value in donor_stats.items():
                    click.echo(f'  {key}: {value}')
            else:
                candidates = conn.execute(
                    """
                    SELECT COUNT(*) as count
                    FROM donors
                    WHERE name LIKE '%Occupation:%' OR name LIKE '%Employer:%'
                    """
                ).fetchone()['count']
                click.echo(f'Donor rows likely needing normalization: {candidates}')

        if not apply:
            click.echo('Dry-run complete. Re-run with --apply to execute changes.')

    except Exception as e:
        click.echo(f'Error during cleanup: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('data-quality')
def data_quality_command():
    """Show data quality and completeness metrics."""
    conn = get_db(config.DATABASE_PATH)
    try:
        summary = data_quality_summary(conn)

        click.echo('Totals:')
        for key, value in summary['totals'].items():
            click.echo(f'  {key}: {value}')

        click.echo('\nQuality Flags:')
        for key, value in summary['quality_flags'].items():
            click.echo(f'  {key}: {value}')

        click.echo('\nRaw Extractions By Source:')
        if summary['raw_extractions_by_source']:
            for key, value in summary['raw_extractions_by_source'].items():
                click.echo(f'  {key}: {value}')
        else:
            click.echo('  none')

    except Exception as e:
        click.echo(f'Error generating data quality summary: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('requeue-details')
@click.option('--missing-transaction-date', is_flag=True,
              help='Only requeue reports with contributions missing transaction_date')
@click.option('--limit', type=int, default=None,
              help='Limit number of reports to requeue')
@click.option('--apply', is_flag=True,
              help='Apply changes (default is dry-run)')
def requeue_details_command(missing_transaction_date, limit, apply):
    """Requeue reports for detail scraping and rebuild contribution rows."""
    conn = get_db(config.DATABASE_PATH)
    try:
        report_ids = find_reports_for_detail_rescrape(
            conn,
            missing_transaction_date_only=missing_transaction_date,
            limit=limit,
        )
        click.echo(f'Reports selected for requeue: {len(report_ids)}')

        if not apply:
            click.echo('Dry-run complete. Re-run with --apply to execute changes.')
            return

        stats = requeue_reports_for_detail_scrape(conn, report_ids)
        click.echo('Requeue complete:')
        for key, value in stats.items():
            click.echo(f'  {key}: {value}')

    except Exception as e:
        click.echo(f'Error requeueing detail reports: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('scrape-main')
@click.option('--start-page', default=config.DEFAULT_START_PAGE, type=int,
              help='Starting page number')
@click.option('--end-page', default=config.DEFAULT_END_PAGE, type=int,
              help='Ending page number')
@click.option('--resume', is_flag=True, help='Resume from last interrupted scrape')
def scrape_main_command(start_page, end_page, resume):
    """Scrape the main reports list."""
    click.echo(f'Scraping main list from page {start_page} to {end_page}...')

    conn = get_db(config.DATABASE_PATH)
    rate_limiter = RateLimiter(
        requests_per_minute=config.RATE_LIMIT_RPM,
        min_delay=config.RATE_LIMIT_MIN_DELAY,
        max_delay=config.RATE_LIMIT_MAX_DELAY
    )

    scraper = MainListScraper(conn, rate_limiter)

    def progress_callback(current, total):
        click.echo(f'  Page {current}/{total}')

    try:
        results = asyncio.run(scraper.scrape(
            start_page=start_page,
            end_page=end_page,
            resume=resume,
            progress_callback=progress_callback
        ))

        click.echo('\nScrape completed!')
        click.echo(f'  Pages scraped: {results["pages_scraped"]}')
        click.echo(f'  Reports found: {results["reports_found"]}')
        click.echo(f'  Paper-filed: {results["paper_filed"]}')

        if results['errors']:
            click.echo(f'  Errors: {len(results["errors"])}')
            for error in results['errors'][:5]:
                click.echo(f'    - {error}')

    except Exception as e:
        click.echo(f'Error during scraping: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('scrape-details')
@click.option('--batch-size', default=config.DEFAULT_BATCH_SIZE, type=int,
              help='Number of reports to process')
@click.option('--resume', is_flag=True, help='Resume from last interrupted scrape')
def scrape_details_command(batch_size, resume):
    """Scrape contribution details from report pages."""
    click.echo(f'Scraping details for up to {batch_size} reports...')

    conn = get_db(config.DATABASE_PATH)
    rate_limiter = RateLimiter(
        requests_per_minute=config.RATE_LIMIT_RPM,
        min_delay=config.RATE_LIMIT_MIN_DELAY,
        max_delay=config.RATE_LIMIT_MAX_DELAY
    )

    scraper = DetailScraper(conn, rate_limiter)

    def progress_callback(current, total):
        click.echo(f'  Report {current}/{total}')

    try:
        results = asyncio.run(scraper.scrape(
            batch_size=batch_size,
            resume=resume,
            progress_callback=progress_callback
        ))

        click.echo('\nScrape completed!')
        click.echo(f'  Reports processed: {results["reports_processed"]}')
        click.echo(f'  Contributions found: {results["contributions_found"]}')
        click.echo(f'  New donors: {results["donors_created"]}')
        click.echo(f'  Skipped: {results["skipped"]}')

        if results['errors']:
            click.echo(f'  Errors: {len(results["errors"])}')
            for error in results['errors'][:5]:
                click.echo(f'    - {error}')

    except Exception as e:
        click.echo(f'Error during scraping: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('scrape-status')
def scrape_status_command():
    """Show current scrape status."""
    conn = get_db(config.DATABASE_PATH)

    try:
        # Main list status
        main_state = ScrapeState.get_latest(conn, 'main_list')
        click.echo('Main List Scrape:')
        if main_state:
            click.echo(f'  Status: {main_state.status}')
            click.echo(f'  Last page: {main_state.last_page}')
            click.echo(f'  Total pages: {main_state.total_pages}')
            if main_state.error_message:
                click.echo(f'  Error: {main_state.error_message}')
        else:
            click.echo('  No scrape history')

        # Details status
        details_state = ScrapeState.get_latest(conn, 'details')
        click.echo('\nDetails Scrape:')
        if details_state:
            click.echo(f'  Status: {details_state.status}')
            click.echo(f'  Last report ID: {details_state.last_report_id}')
            if details_state.error_message:
                click.echo(f'  Error: {details_state.error_message}')
        else:
            click.echo('  No scrape history')

        # Report counts
        status_counts = Report.count_by_status(conn)
        click.echo('\nReport Status:')
        for status, count in sorted(status_counts.items()):
            click.echo(f'  {status}: {count}')

        total = Report.count(conn)
        paper_filed = Report.count(conn, paper_filed=True)
        click.echo(f'\nTotal reports: {total}')
        click.echo(f'Paper-filed: {paper_filed}')

    finally:
        conn.close()


@cli.command('seed-committee-urls')
@click.option('--committee-id-sbe', 'committee_ids_sbe', multiple=True, type=int,
              help='Illinois SBE committee ID(s) to seed (repeat option for multiple)')
@click.option('--batch-size', default=200, type=int,
              help='Number of committees to process when SBE IDs are not provided')
@click.option('--include-existing', is_flag=True,
              help='Re-resolve committees even when detail_url already exists')
def seed_committee_urls_command(committee_ids_sbe, batch_size, include_existing):
    """Resolve and store CommitteeDetail URLs via CommitteeSearch.aspx using committee_id_sbe."""
    committee_ids_sbe = list(committee_ids_sbe) if committee_ids_sbe else None
    if committee_ids_sbe:
        click.echo(f'Seeding committee detail URLs for SBE IDs: {committee_ids_sbe}')
    else:
        click.echo(f'Seeding committee detail URLs for up to {batch_size} committees...')

    conn = get_db(config.DATABASE_PATH)
    rate_limiter = RateLimiter(
        requests_per_minute=config.RATE_LIMIT_RPM,
        min_delay=config.RATE_LIMIT_MIN_DELAY,
        max_delay=config.RATE_LIMIT_MAX_DELAY
    )
    scraper = CommitteeUrlSeeder(conn, rate_limiter)

    def progress_callback(current, total):
        click.echo(f'  Committee {current}/{total}')

    try:
        results = asyncio.run(scraper.seed_urls(
            committee_ids_sbe=committee_ids_sbe,
            batch_size=batch_size,
            include_existing=include_existing,
            progress_callback=progress_callback,
        ))

        click.echo('\nCommittee URL seed completed!')
        click.echo(f'  Committees targeted: {results["committees_targeted"]}')
        click.echo(f'  URLs seeded: {results["urls_seeded"]}')
        click.echo(f'  URLs unchanged: {results["urls_unchanged"]}')
        click.echo(f'  Missing search results: {results["missing_results"]}')
        if results['errors']:
            click.echo(f'  Errors: {len(results["errors"])}')
            for error in results['errors'][:5]:
                click.echo(f'    - {error}')

    except Exception as e:
        click.echo(f'Error during committee URL seeding: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('scrape-committee-reports')
@click.option('--committee-id', 'committee_ids', multiple=True, type=int,
              help='Internal committee ID(s) to scrape (repeat option for multiple)')
@click.option('--committee-id-sbe', 'committee_ids_sbe', multiple=True, type=int,
              help='Illinois SBE committee ID(s) to scrape (repeat option for multiple)')
@click.option('--batch-size', default=20, type=int,
              help='Number of committees to scrape when committee IDs are not provided')
@click.option('--filed-cutoff', default='2025-06-01',
              help='Stop per committee when filed date is older than this date (YYYY-MM-DD)')
def scrape_committee_reports_command(committee_ids, committee_ids_sbe, batch_size, filed_cutoff):
    """Scrape committee pages and collect A-1 + D-2 reports."""
    committee_ids = list(committee_ids) if committee_ids else None
    committee_ids_sbe = list(committee_ids_sbe) if committee_ids_sbe else None
    if committee_ids and committee_ids_sbe:
        target_label = f"internal IDs {committee_ids} + SBE IDs {committee_ids_sbe}"
    elif committee_ids:
        target_label = f"internal IDs {committee_ids}"
    elif committee_ids_sbe:
        target_label = f"SBE IDs {committee_ids_sbe}"
    else:
        target_label = f"batch of {batch_size}"
    click.echo(f'Scraping committee reports for {target_label} until filed date cutoff {filed_cutoff}...')

    conn = get_db(config.DATABASE_PATH)
    rate_limiter = RateLimiter(
        requests_per_minute=config.RATE_LIMIT_RPM,
        min_delay=config.RATE_LIMIT_MIN_DELAY,
        max_delay=config.RATE_LIMIT_MAX_DELAY
    )
    scraper = CommitteeReportScraper(conn, rate_limiter)

    def progress_callback(current, total):
        click.echo(f'  Committee {current}/{total}')

    try:
        results = asyncio.run(scraper.scrape_committees(
            committee_ids=committee_ids,
            committee_ids_sbe=committee_ids_sbe,
            batch_size=batch_size,
            filed_cutoff=filed_cutoff,
            progress_callback=progress_callback
        ))

        click.echo('\nCommittee scrape completed!')
        click.echo(f'  Committees processed: {results["committees_processed"]}')
        click.echo(f'  A-1 reports saved: {results["a1_reports_saved"]}')
        click.echo(f'  D-2 reports saved: {results["d2_reports_saved"]}')
        click.echo(f'  Committees stopped by cutoff: {results["stopped_by_cutoff"]}')
        if results['errors']:
            click.echo(f'  Errors: {len(results["errors"])}')
            for error in results['errors'][:5]:
                click.echo(f'    - {error}')

    except Exception as e:
        click.echo(f'Error during committee scrape: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('scrape-d2-details')
@click.option('--committee-id', 'committee_ids', multiple=True, type=int,
              help='Internal committee ID(s) to target')
@click.option('--committee-id-sbe', 'committee_ids_sbe', multiple=True, type=int,
              help='Illinois SBE committee ID(s) to target')
@click.option('--batch-size', default=20, type=int,
              help='Number of D-2 reports to process')
@click.option('--with-itemized', is_flag=True,
              help='Immediately scrape itemized pages for links found on each D-2 detail page')
def scrape_d2_details_command(committee_ids, committee_ids_sbe, batch_size, with_itemized):
    """Scrape D-2 detail pages and discover itemized links."""
    committee_ids = list(committee_ids) if committee_ids else None
    committee_ids_sbe = list(committee_ids_sbe) if committee_ids_sbe else None
    click.echo(f'Scraping up to {batch_size} D-2 detail pages...')

    conn = get_db(config.DATABASE_PATH)
    committee_ids, unresolved_sbe = _resolve_internal_committee_ids(conn, committee_ids, committee_ids_sbe)
    if unresolved_sbe:
        click.echo(f'  Warning: no local committee rows for SBE IDs {unresolved_sbe}')

    rate_limiter = RateLimiter(
        requests_per_minute=config.RATE_LIMIT_RPM,
        min_delay=config.RATE_LIMIT_MIN_DELAY,
        max_delay=config.RATE_LIMIT_MAX_DELAY
    )
    scraper = D2DetailScraper(conn, rate_limiter)

    def progress_callback(current, total):
        click.echo(f'  D-2 report {current}/{total}')

    try:
        results = asyncio.run(scraper.scrape_d2_details(
            committee_ids=committee_ids,
            batch_size=batch_size,
            scrape_itemized=with_itemized,
            progress_callback=progress_callback
        ))

        click.echo('\nD-2 detail scrape completed!')
        click.echo(f'  Reports processed: {results["reports_processed"]}')
        click.echo(f'  Itemized links found: {results["itemized_links_found"]}')
        click.echo(f'  Itemized rows saved: {results["itemized_rows_saved"]}')
        if results['errors']:
            click.echo(f'  Errors: {len(results["errors"])}')
            for error in results['errors'][:5]:
                click.echo(f'    - {error}')

    except Exception as e:
        click.echo(f'Error during D-2 detail scrape: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('scrape-d2-itemized')
@click.option('--committee-id', 'committee_ids', multiple=True, type=int,
              help='Internal committee ID(s) to target')
@click.option('--committee-id-sbe', 'committee_ids_sbe', multiple=True, type=int,
              help='Illinois SBE committee ID(s) to target')
@click.option('--batch-size', default=50, type=int,
              help='Number of pending itemized links to scrape')
def scrape_d2_itemized_command(committee_ids, committee_ids_sbe, batch_size):
    """Scrape pending D-2 itemized links as a separate step."""
    committee_ids = list(committee_ids) if committee_ids else None
    committee_ids_sbe = list(committee_ids_sbe) if committee_ids_sbe else None
    click.echo(f'Scraping up to {batch_size} D-2 itemized links...')

    conn = get_db(config.DATABASE_PATH)
    committee_ids, unresolved_sbe = _resolve_internal_committee_ids(conn, committee_ids, committee_ids_sbe)
    if unresolved_sbe:
        click.echo(f'  Warning: no local committee rows for SBE IDs {unresolved_sbe}')

    rate_limiter = RateLimiter(
        requests_per_minute=config.RATE_LIMIT_RPM,
        min_delay=config.RATE_LIMIT_MIN_DELAY,
        max_delay=config.RATE_LIMIT_MAX_DELAY
    )
    scraper = D2DetailScraper(conn, rate_limiter)

    def progress_callback(current, total):
        click.echo(f'  Itemized link {current}/{total}')

    try:
        results = asyncio.run(scraper.scrape_pending_itemized(
            committee_ids=committee_ids,
            batch_size=batch_size,
            progress_callback=progress_callback
        ))

        click.echo('\nD-2 itemized scrape completed!')
        click.echo(f'  Links processed: {results["links_processed"]}')
        click.echo(f'  Rows saved: {results["rows_saved"]}')
        if results['errors']:
            click.echo(f'  Errors: {len(results["errors"])}')
            for error in results['errors'][:5]:
                click.echo(f'    - {error}')

    except Exception as e:
        click.echo(f'Error during D-2 itemized scrape: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('scrape-d2-all-pending')
@click.option('--committee-id', 'committee_ids', multiple=True, type=int,
              help='Internal committee ID(s) to target')
@click.option('--committee-id-sbe', 'committee_ids_sbe', multiple=True, type=int,
              help='Illinois SBE committee ID(s) to target')
@click.option('--detail-batch-size', default=500, type=int, show_default=True,
              help='Number of pending D-2 detail rows to process per cycle')
@click.option('--itemized-batch-size', default=1000, type=int, show_default=True,
              help='Number of pending itemized links to process per cycle')
@click.option('--max-cycles', default=100, type=int, show_default=True,
              help='Safety cap on detail/itemized loop cycles')
def scrape_d2_all_pending_command(
    committee_ids,
    committee_ids_sbe,
    detail_batch_size,
    itemized_batch_size,
    max_cycles,
):
    """Run D-2 detail + itemized scraping until pending queues are empty."""
    committee_ids = list(committee_ids) if committee_ids else None
    committee_ids_sbe = list(committee_ids_sbe) if committee_ids_sbe else None

    conn = get_db(config.DATABASE_PATH)
    committee_ids, unresolved_sbe = _resolve_internal_committee_ids(conn, committee_ids, committee_ids_sbe)
    if unresolved_sbe:
        click.echo(f'  Warning: no local committee rows for SBE IDs {unresolved_sbe}')

    detail_batch_size = max(1, int(detail_batch_size))
    itemized_batch_size = max(1, int(itemized_batch_size))
    max_cycles = max(1, int(max_cycles))

    click.echo('Scraping all pending D-2 rows until queues are empty...')
    if committee_ids:
        click.echo(f'  Target internal committee IDs: {committee_ids}')

    rate_limiter = RateLimiter(
        requests_per_minute=config.RATE_LIMIT_RPM,
        min_delay=config.RATE_LIMIT_MIN_DELAY,
        max_delay=config.RATE_LIMIT_MAX_DELAY
    )
    scraper = D2DetailScraper(conn, rate_limiter)

    totals = {
        "detail_reports_processed": 0,
        "itemized_links_found_during_detail": 0,
        "itemized_rows_saved_during_detail": 0,
        "itemized_links_processed": 0,
        "itemized_rows_saved": 0,
        "errors": [],
    }

    try:
        # Pass 1: keep scraping pending D-2 detail rows.
        detail_cycles = 0
        while detail_cycles < max_cycles:
            pending_details = _count_pending_d2_details(conn, committee_ids=committee_ids)
            if pending_details == 0:
                break

            detail_cycles += 1
            click.echo(f'  Detail cycle {detail_cycles}: pending detail rows={pending_details}')

            results = asyncio.run(scraper.scrape_d2_details(
                committee_ids=committee_ids,
                batch_size=detail_batch_size,
                scrape_itemized=True,
            ))
            totals["detail_reports_processed"] += results["reports_processed"]
            totals["itemized_links_found_during_detail"] += results["itemized_links_found"]
            totals["itemized_rows_saved_during_detail"] += results["itemized_rows_saved"]
            totals["errors"].extend(results.get("errors", []))

            if results["reports_processed"] == 0:
                click.echo('  Detail cycle made no progress; stopping detail loop.')
                break

        if detail_cycles >= max_cycles:
            click.echo(f'  Reached max detail cycles ({max_cycles}); stopping detail loop.')

        # Pass 2: drain any remaining pending itemized links.
        itemized_cycles = 0
        while itemized_cycles < max_cycles:
            pending_links = _count_pending_d2_itemized_links(conn, committee_ids=committee_ids)
            if pending_links == 0:
                break

            itemized_cycles += 1
            click.echo(f'  Itemized cycle {itemized_cycles}: pending itemized links={pending_links}')
            results = asyncio.run(scraper.scrape_pending_itemized(
                committee_ids=committee_ids,
                batch_size=itemized_batch_size,
            ))
            totals["itemized_links_processed"] += results["links_processed"]
            totals["itemized_rows_saved"] += results["rows_saved"]
            totals["errors"].extend(results.get("errors", []))

            if results["links_processed"] == 0:
                click.echo('  Itemized cycle made no progress; stopping itemized loop.')
                break

        if itemized_cycles >= max_cycles:
            click.echo(f'  Reached max itemized cycles ({max_cycles}); stopping itemized loop.')

        remaining_details = _count_pending_d2_details(conn, committee_ids=committee_ids)
        remaining_links = _count_pending_d2_itemized_links(conn, committee_ids=committee_ids)

        click.echo('\nD-2 all-pending scrape completed!')
        click.echo(f'  Detail reports processed: {totals["detail_reports_processed"]}')
        click.echo(f'  Itemized links found during detail pass: {totals["itemized_links_found_during_detail"]}')
        click.echo(f'  Itemized rows saved during detail pass: {totals["itemized_rows_saved_during_detail"]}')
        click.echo(f'  Itemized links processed in drain pass: {totals["itemized_links_processed"]}')
        click.echo(f'  Itemized rows saved in drain pass: {totals["itemized_rows_saved"]}')
        click.echo(f'  Remaining pending detail rows: {remaining_details}')
        click.echo(f'  Remaining pending itemized links: {remaining_links}')
        if totals["errors"]:
            click.echo(f'  Errors: {len(totals["errors"])}')
            for error in totals["errors"][:5]:
                click.echo(f'    - {error}')

    except Exception as e:
        click.echo(f'Error during D-2 all-pending scrape: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('refresh-analytics')
@click.option('--with-snapshot/--skip-snapshot', default=True, show_default=True,
              help='Also build and cache a full dashboard snapshot')
@click.option('--min-edge-amount', default=1000.0, type=float, show_default=True)
@click.option('--network-limit', default=200, type=int, show_default=True)
@click.option('--anomaly-limit', default=25, type=int, show_default=True)
@click.option('--concentration-limit', default=25, type=int, show_default=True)
@click.option('--months', default=24, type=int, show_default=True)
@click.option('--geo-state-limit', default=15, type=int, show_default=True)
@click.option('--geo-city-limit', default=25, type=int, show_default=True)
@click.option('--nlp-limit', default=20, type=int, show_default=True)
@click.option('--recon-limit', default=20, type=int, show_default=True)
@click.option('--recon-min-abs-diff', default=1000.0, type=float, show_default=True)
def refresh_analytics_command(
    with_snapshot,
    min_edge_amount,
    network_limit,
    anomaly_limit,
    concentration_limit,
    months,
    geo_state_limit,
    geo_city_limit,
    nlp_limit,
    recon_limit,
    recon_min_abs_diff,
):
    """Rebuild materialized analytics tables and optionally refresh the full snapshot cache."""
    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Refreshing materialized analytics tables...')
        stats = refresh_analytics_materialized(conn)
        for key, value in stats.items():
            click.echo(f'  {key}: {value}')

        if with_snapshot:
            params = {
                "min_edge_amount": float(min_edge_amount),
                "network_limit": int(network_limit),
                "anomaly_limit": int(anomaly_limit),
                "concentration_limit": int(concentration_limit),
                "months": int(months),
                "geo_state_limit": int(geo_state_limit),
                "geo_city_limit": int(geo_city_limit),
                "nlp_limit": int(nlp_limit),
                "recon_limit": int(recon_limit),
                "recon_min_abs_diff": float(recon_min_abs_diff),
                "snapshot_version": 1,
            }
            click.echo('Building full analytics snapshot...')
            payload = build_dashboard_full_snapshot(conn, params=params, rebuild_materialized=False)
            snapshot_meta = save_dashboard_snapshot(
                conn,
                params=params,
                status='completed',
                payload=payload,
                error_message=None,
            )
            click.echo('Snapshot saved:')
            click.echo(f'  cache_key: {snapshot_meta["cache_key"]}')
            click.echo(f'  status: {snapshot_meta["status"]}')
            click.echo(f'  completed_at: {snapshot_meta["completed_at"]}')

    except Exception as e:
        click.echo(f'Error refreshing analytics: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('rebuild-local-donor-entities')
@click.option('--source', default='bulk_receipts', show_default=True,
              help='analytics_donor_summary source to process')
@click.option('--medium-threshold', default=0.70, type=float, show_default=True,
              help='Minimum pair score to link rows into the same candidate entity')
@click.option('--high-threshold', default=0.82, type=float, show_default=True,
              help='Pair score threshold counted as high-confidence')
@click.option('--auto-threshold', default=0.90, type=float, show_default=True,
              help='Cluster confidence threshold for automatic merges')
@click.option('--max-group-size', default=400, type=int, show_default=True,
              help='Guardrail: skip probabilistic linking for larger canonical-name groups')
@click.option('--preview-limit', default=25, type=int, show_default=True,
              help='Number of top review entities to print')
@click.option('--dry-run', is_flag=True, help='Analyze only; do not write donor entity tables')
def rebuild_local_donor_entities_command(
    source,
    medium_threshold,
    high_threshold,
    auto_threshold,
    max_group_size,
    preview_limit,
    dry_run,
):
    """Build confidence-scored local donor entities and review candidates."""
    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Rebuilding local donor entities...')
        stats = rebuild_local_donor_entities(
            conn,
            source=(source or '').strip() or 'bulk_receipts',
            medium_threshold=float(medium_threshold),
            high_threshold=float(high_threshold),
            auto_threshold=float(auto_threshold),
            max_group_size=max(2, int(max_group_size)),
            dry_run=bool(dry_run),
            preview_limit=max(0, int(preview_limit)),
        )

        top_review_entities = stats.pop('top_review_entities', [])
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')

        if top_review_entities:
            click.echo('  top_review_entities:')
            for row in top_review_entities:
                click.echo(
                    '    - '
                    f"{row.get('canonical_name', '')}: members={row.get('member_count', 0)}, "
                    f"total_amount={row.get('total_amount', 0)}, "
                    f"confidence={row.get('confidence_score', 0)}, "
                    f"peak={row.get('peak_confidence_score', 0)}"
                )

    except Exception as exc:
        click.echo(f'Error rebuilding local donor entities: {exc}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('sync-fec-il-federal')
@click.option(
    '--candidates-csv',
    default='docs/il_federal_candidates_2026_asof_2026-02-07.csv',
    show_default=True,
    help='CSV of Illinois federal candidate rows used as sync seed',
)
@click.option(
    '--api-key',
    default='',
    help='FEC API key (defaults to FEC_API_KEY env var)',
)
@click.option('--cycle', default=2026, type=int, show_default=True, help='Two-year transaction period (e.g., 2026)')
@click.option(
    '--contributor-state',
    default='IL',
    show_default=True,
    help='Filter Schedule A contributions by contributor state (blank for all states)',
)
@click.option('--per-page', default=100, type=int, show_default=True, help='API per-page size (max 100)')
@click.option('--max-calls', default=900, type=int, show_default=True, help='Stop sync when API calls exceed this budget')
@click.option(
    '--max-pages-per-committee',
    default=250,
    type=int,
    show_default=True,
    help='Pagination cap for each committee Schedule A sync',
)
@click.option('--include-all-committees', is_flag=True, help='Include non-principal committees for each candidate')
@click.option('--skip-donations', is_flag=True, help='Only resolve candidates/committees; skip Schedule A pulls')
@click.option('--refresh-local-matches/--skip-local-matches', default=True, show_default=True,
              help='Rebuild persisted federal/local donor match pairs after sync')
@click.option('--refresh-cache', is_flag=True, help='Ignore cached raw endpoint payloads and re-request from API')
@click.option('--max-committees', type=int, default=None, help='Optional committee cap for partial sync/debug runs')
def sync_fec_il_federal_command(
    candidates_csv,
    api_key,
    cycle,
    contributor_state,
    per_page,
    max_calls,
    max_pages_per_committee,
    include_all_committees,
    skip_donations,
    refresh_local_matches,
    refresh_cache,
    max_committees,
):
    """Sync Illinois federal candidate IDs and contribution detail from FEC APIs."""
    resolved_api_key = (api_key or '').strip() or (config.FEC_API_KEY or '').strip()
    if not resolved_api_key:
        click.echo('Error: missing FEC API key. Provide --api-key or set FEC_API_KEY.', err=True)
        sys.exit(1)

    csv_path = Path(candidates_csv)
    if not csv_path.exists():
        click.echo(f'Error: candidates CSV not found: {csv_path}', err=True)
        sys.exit(1)

    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Starting FEC Illinois federal sync...')
        stats = sync_il_federal_fec(
            conn,
            candidates_csv=csv_path,
            api_key=resolved_api_key,
            cycle=int(cycle),
            contributor_state=(contributor_state or '').strip() or None,
            per_page=int(per_page),
            max_calls=int(max_calls),
            max_pages_per_committee=int(max_pages_per_committee),
            include_all_committees=bool(include_all_committees),
            refresh_cache=bool(refresh_cache),
            skip_donations=bool(skip_donations),
            max_committees=max_committees,
            refresh_local_matches=bool(refresh_local_matches),
        )
        click.echo('FEC sync completed:')
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')
    except Exception as exc:
        click.echo(f'Error syncing FEC federal data: {exc}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('backfill-fec-schedule-a')
@click.option('--api-key', default='', help='FEC API key (defaults to FEC_API_KEY env var)')
@click.option('--cycle', default=2026, type=int, show_default=True, help='Two-year transaction period (e.g., 2026)')
@click.option('--max-calls', default=1000, type=int, show_default=True, help='Max FEC API calls for this run')
@click.option('--per-page', default=100, type=int, show_default=True, help='API per-page size (max 100)')
@click.option(
    '--max-pages-per-committee',
    default=25,
    type=int,
    show_default=True,
    help='Max schedule pages to request per committee during this run',
)
@click.option(
    '--min-abs-gap',
    default=500.0,
    type=float,
    show_default=True,
    help='Only target candidates where reported-vs-synced gap exceeds this amount',
)
@click.option(
    '--tolerance',
    default=0.01,
    type=float,
    show_default=True,
    help='Treat smaller differences as matched (for status classification)',
)
@click.option(
    '--include-completed',
    is_flag=True,
    help='Also revisit committees already marked as completed in backfill state',
)
@click.option('--refresh-cache', is_flag=True, help='Ignore cached raw payloads and request fresh API pages')
@click.option('--max-committees', type=int, default=None, help='Optional committee cap for this run')
def backfill_fec_schedule_a_command(
    api_key,
    cycle,
    max_calls,
    per_page,
    max_pages_per_committee,
    min_abs_gap,
    tolerance,
    include_completed,
    refresh_cache,
    max_committees,
):
    """Backfill missing FEC Schedule A rows for candidates with receipt mismatches."""
    resolved_api_key = (api_key or '').strip() or (config.FEC_API_KEY or '').strip()
    if not resolved_api_key:
        click.echo('Error: missing FEC API key. Provide --api-key or set FEC_API_KEY.', err=True)
        sys.exit(1)

    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Starting FEC Schedule A backfill run...')
        stats = backfill_fec_missing_schedule_a(
            conn,
            api_key=resolved_api_key,
            cycle=int(cycle),
            max_calls=int(max_calls),
            per_page=int(per_page),
            max_pages_per_committee=int(max_pages_per_committee),
            min_abs_gap=float(min_abs_gap),
            tolerance=float(tolerance),
            include_completed=bool(include_completed),
            refresh_cache=bool(refresh_cache),
            max_committees=max_committees,
        )
        click.echo('FEC Schedule A backfill completed:')
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')
    except Exception as exc:
        click.echo(f'Error backfilling FEC Schedule A rows: {exc}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('backfill-fec-schedule-b')
@click.option('--api-key', default='', help='FEC API key (defaults to FEC_API_KEY env var)')
@click.option('--cycle', default=2026, type=int, show_default=True, help='Two-year transaction period (e.g., 2026)')
@click.option('--max-calls', default=1000, type=int, show_default=True, help='Max FEC API calls for this run')
@click.option('--per-page', default=100, type=int, show_default=True, help='API per-page size (max 100)')
@click.option(
    '--max-pages-per-committee',
    default=25,
    type=int,
    show_default=True,
    help='Max schedule pages to request per committee during this run',
)
@click.option(
    '--include-completed',
    is_flag=True,
    help='Also revisit committees already marked as completed in Schedule B backfill state',
)
@click.option(
    '--principal-only',
    is_flag=True,
    help='Only fetch Schedule B rows for principal committees',
)
@click.option('--refresh-cache', is_flag=True, help='Ignore cached raw payloads and request fresh API pages')
@click.option('--max-committees', type=int, default=None, help='Optional committee cap for this run')
def backfill_fec_schedule_b_command(
    api_key,
    cycle,
    max_calls,
    per_page,
    max_pages_per_committee,
    include_completed,
    principal_only,
    refresh_cache,
    max_committees,
):
    """Backfill FEC Schedule B disbursement rows for IL federal candidate committees."""
    resolved_api_key = (api_key or '').strip() or (config.FEC_API_KEY or '').strip()
    if not resolved_api_key:
        click.echo('Error: missing FEC API key. Provide --api-key or set FEC_API_KEY.', err=True)
        sys.exit(1)

    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Starting FEC Schedule B backfill run...')
        stats = backfill_fec_schedule_b(
            conn,
            api_key=resolved_api_key,
            cycle=int(cycle),
            max_calls=int(max_calls),
            per_page=int(per_page),
            max_pages_per_committee=int(max_pages_per_committee),
            include_completed=bool(include_completed),
            refresh_cache=bool(refresh_cache),
            include_all_committees=not bool(principal_only),
            max_committees=max_committees,
        )
        click.echo('FEC Schedule B backfill completed:')
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')
    except Exception as exc:
        click.echo(f'Error backfilling FEC Schedule B rows: {exc}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('backfill-fec-schedule-e')
@click.option('--api-key', default='', help='FEC API key (defaults to FEC_API_KEY env var)')
@click.option('--cycle', default=2026, type=int, show_default=True, help='Two-year transaction period (e.g., 2026)')
@click.option('--max-calls', default=1000, type=int, show_default=True, help='Max FEC API calls for this run')
@click.option('--per-page', default=100, type=int, show_default=True, help='API per-page size (max 100)')
@click.option(
    '--max-pages-per-candidate',
    default=25,
    type=int,
    show_default=True,
    help='Max schedule pages to request per candidate during this run',
)
@click.option(
    '--include-completed',
    is_flag=True,
    help='Also revisit candidates already marked as completed in Schedule E backfill state',
)
@click.option('--refresh-cache', is_flag=True, help='Ignore cached raw payloads and request fresh API pages')
@click.option('--max-candidates', type=int, default=None, help='Optional candidate cap for this run')
def backfill_fec_schedule_e_command(
    api_key,
    cycle,
    max_calls,
    per_page,
    max_pages_per_candidate,
    include_completed,
    refresh_cache,
    max_candidates,
):
    """Backfill FEC Schedule E independent expenditure rows for IL federal candidates."""
    resolved_api_key = (api_key or '').strip() or (config.FEC_API_KEY or '').strip()
    if not resolved_api_key:
        click.echo('Error: missing FEC API key. Provide --api-key or set FEC_API_KEY.', err=True)
        sys.exit(1)

    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Starting FEC Schedule E backfill run...')
        stats = backfill_fec_schedule_e(
            conn,
            api_key=resolved_api_key,
            cycle=int(cycle),
            max_calls=int(max_calls),
            per_page=int(per_page),
            max_pages_per_candidate=int(max_pages_per_candidate),
            include_completed=bool(include_completed),
            refresh_cache=bool(refresh_cache),
            max_candidates=max_candidates,
        )
        click.echo('FEC Schedule E backfill completed:')
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')
    except Exception as exc:
        click.echo(f'Error backfilling FEC Schedule E rows: {exc}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('refresh-fec-local-donor-matches')
@click.option('--cycle', type=int, default=None, help='Optional cycle filter (e.g., 2026)')
@click.option('--office-code', default=None, help='Optional office filter (e.g., H, S, P)')
@click.option('--district-code', default=None, help='Optional district filter (e.g., 07)')
@click.option('--federal-donor-limit', type=int, default=5000, show_default=True,
              help='Max federal donor entities to scan')
@click.option('--local-donor-limit', type=int, default=100000, show_default=True,
              help='Max local donor rows to scan')
@click.option('--match-limit', type=int, default=5000, show_default=True,
              help='Max merged matches to materialize before expanding local keys')
def refresh_fec_local_donor_matches_command(
    cycle,
    office_code,
    district_code,
    federal_donor_limit,
    local_donor_limit,
    match_limit,
):
    """Materialize federal/local donor match pairs for dashboard and quick lookups."""
    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Refreshing persisted federal/local donor matches...')
        stats = refresh_fec_local_donor_matches(
            conn,
            cycle=cycle,
            office_code=(office_code or "").strip() or None,
            district_code=(district_code or "").strip() or None,
            federal_donor_limit=max(100, int(federal_donor_limit)),
            local_donor_limit=max(1000, int(local_donor_limit)),
            match_limit=max(100, int(match_limit)),
        )
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')
    except Exception as exc:
        click.echo(f'Error refreshing federal/local donor matches: {exc}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('rebuild-fec-donor-identities')
@click.option('--cycle', type=int, default=None, help='Optional cycle filter (e.g., 2026)')
@click.option('--only-missing/--all-rows', default=True, show_default=True,
              help='Update only rows missing donor_entity_key or rebuild all rows')
@click.option('--batch-size', type=int, default=5000, show_default=True, help='Rows per update batch')
def rebuild_fec_donor_identities_command(cycle, only_missing, batch_size):
    """Rebuild donor entity IDs used for cross-candidate donor drill-down."""
    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo('Rebuilding FEC donor identities...')
        stats = rebuild_fec_donor_identities(
            conn,
            cycle=cycle,
            only_missing=bool(only_missing),
            batch_size=max(100, int(batch_size)),
        )
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')
    except Exception as exc:
        click.echo(f'Error rebuilding FEC donor identities: {exc}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('import-lobbying')
@click.option('--file', 'file_path', required=True,
              help='Path to IL SOS lobbying CSV file')
def import_lobbying_command(file_path):
    """Import IL Secretary of State lobbying entity/client data from CSV."""
    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo(f'Importing lobbying data from {file_path}...')
        stats = load_lobbying_csv(conn, Path(file_path))
        click.echo('Lobbying import completed:')
        for key, value in stats.items():
            click.echo(f'  {key}: {value}')
    except Exception as e:
        click.echo(f'Error importing lobbying data: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('import-irs527')
@click.option('--file', 'file_path', required=True,
              help='Path to IRS 527 FullDataFile.txt')
@click.option('--illinois-only/--all-states', default=True, show_default=True,
              help='Only load orgs with IL addresses or IL expenditures')
def import_irs527_command(file_path, illinois_only):
    """Import IRS 527 political organization filings from pipe-delimited file."""
    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo(f'Importing IRS 527 data from {file_path}...')
        if illinois_only:
            click.echo('  Filtering to Illinois-related records only.')
        stats = load_irs527_full_file(conn, Path(file_path), illinois_only=illinois_only)
        click.echo('IRS 527 import completed:')
        for key, value in stats.items():
            click.echo(f'  {key}: {value}')
    except Exception as e:
        click.echo(f'Error importing IRS 527 data: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('run-cross-matching')
@click.option('--threshold', default=0.80, type=float, show_default=True,
              help='Minimum Jaccard similarity score for matches')
@click.option('--only', 'only_match', default='all', show_default=True,
              type=click.Choice([
                  'all', 'lobbying-donors', 'lobbying-expenditures',
                  '527-committees', '527-expenditures', '527-directors', 'lobbying-527',
              ]),
              help='Run only a specific matching function')
def run_cross_matching_command(threshold, only_match):
    """Run cross-matching between lobbying, IRS 527, and campaign finance data."""
    conn = get_db(config.DATABASE_PATH)
    try:
        click.echo(f'Running cross-matching (threshold={threshold}, only={only_match})...')

        match_funcs = {
            'lobbying-donors': ('lobbying_donors', match_lobbying_to_donors),
            'lobbying-expenditures': ('lobbying_expenditures', match_lobbying_to_expenditure_payees),
            '527-committees': ('527_committees', match_527_to_committees),
            '527-expenditures': ('527_expenditures', match_527_expenditures_to_committees),
            '527-directors': ('527_directors', match_527_directors_to_donors),
            'lobbying-527': ('lobbying_527', match_lobbying_to_527),
        }

        if only_match == 'all':
            results = run_all_cross_matching(conn, threshold=threshold)
        else:
            label, func = match_funcs[only_match]
            results = {label: func(conn, threshold=threshold)}

        click.echo('Cross-matching completed:')
        for key, value in results.items():
            click.echo(f'  {key}: {value}')
    except Exception as e:
        click.echo(f'Error during cross-matching: {e}', err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command('runserver')
@click.option('--host', default='127.0.0.1', help='Host to bind to')
@click.option('--port', default=5000, type=int, help='Port to bind to')
@click.option('--debug', is_flag=True, help='Enable debug mode')
def runserver_command(host, port, debug):
    """Run the web server."""
    from webapp.app import create_app

    app = create_app({
        'SECRET_KEY': config.FLASK_SECRET_KEY,
        'DATABASE_PATH': config.DATABASE_PATH,
        'PUBLIC_CONTACT_EMAIL': config.PUBLIC_CONTACT_EMAIL,
    })

    click.echo(f'Starting server at http://{host}:{port}')
    app.run(host=host, port=port, debug=debug or config.FLASK_DEBUG)


if __name__ == '__main__':
    cli()
