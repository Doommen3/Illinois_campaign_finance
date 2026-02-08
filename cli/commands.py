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
from database.federal_fec import rebuild_fec_donor_identities, sync_il_federal_fec
from scraper.main_list_scraper import MainListScraper
from scraper.detail_scraper import DetailScraper
from scraper.committee_scraper import CommitteeReportScraper, D2DetailScraper
from scraper.rate_limiter import RateLimiter
import config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
    """Import and normalize bulk committees/D2/candidate/link/receipts files into joined tables."""
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


@cli.command('scrape-committee-reports')
@click.option('--committee-id', 'committee_ids', multiple=True, type=int,
              help='Committee ID(s) to scrape (repeat option for multiple)')
@click.option('--batch-size', default=20, type=int,
              help='Number of committees to scrape when committee IDs are not provided')
@click.option('--filed-cutoff', default='2025-06-01',
              help='Stop per committee when filed date is older than this date (YYYY-MM-DD)')
def scrape_committee_reports_command(committee_ids, batch_size, filed_cutoff):
    """Scrape committee pages and collect A-1 + D-2 reports."""
    committee_ids = list(committee_ids) if committee_ids else None
    target_label = f"IDs {committee_ids}" if committee_ids else f"batch of {batch_size}"
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
              help='Committee ID(s) to target')
@click.option('--batch-size', default=20, type=int,
              help='Number of D-2 reports to process')
@click.option('--with-itemized', is_flag=True,
              help='Immediately scrape itemized pages for links found on each D-2 detail page')
def scrape_d2_details_command(committee_ids, batch_size, with_itemized):
    """Scrape D-2 detail pages and discover itemized links."""
    committee_ids = list(committee_ids) if committee_ids else None
    click.echo(f'Scraping up to {batch_size} D-2 detail pages...')

    conn = get_db(config.DATABASE_PATH)
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
              help='Committee ID(s) to target')
@click.option('--batch-size', default=50, type=int,
              help='Number of pending itemized links to scrape')
def scrape_d2_itemized_command(committee_ids, batch_size):
    """Scrape pending D-2 itemized links as a separate step."""
    committee_ids = list(committee_ids) if committee_ids else None
    click.echo(f'Scraping up to {batch_size} D-2 itemized links...')

    conn = get_db(config.DATABASE_PATH)
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
        )
        click.echo('FEC sync completed:')
        for key in sorted(stats.keys()):
            click.echo(f'  {key}: {stats[key]}')
    except Exception as exc:
        click.echo(f'Error syncing FEC federal data: {exc}', err=True)
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


@cli.command('runserver')
@click.option('--host', default='127.0.0.1', help='Host to bind to')
@click.option('--port', default=5000, type=int, help='Port to bind to')
@click.option('--debug', is_flag=True, help='Enable debug mode')
def runserver_command(host, port, debug):
    """Run the web server."""
    from webapp.app import create_app

    app = create_app({
        'SECRET_KEY': config.FLASK_SECRET_KEY,
        'DATABASE_PATH': config.DATABASE_PATH
    })

    click.echo(f'Starting server at http://{host}:{port}')
    app.run(host=host, port=port, debug=debug or config.FLASK_DEBUG)


if __name__ == '__main__':
    cli()
