"""Detail page scraper for Illinois State Board of Elections campaign finance data."""
import logging
from typing import List, Optional, Callable, Tuple
import sqlite3

from playwright.async_api import async_playwright, Page, Browser

from database.models import Report, Donor, Contribution, RawExtraction
from database.identifiers import make_source_identifier
from .rate_limiter import RateLimiter
from .state_manager import StateManager
from .donor_normalizer import DonorNormalizer
from .text_parsing import parse_contributor_metadata, parse_amount_and_date

logger = logging.getLogger(__name__)


class DetailScraper:
    """Scrapes contribution details from A1List.aspx pages."""

    def __init__(self, conn: sqlite3.Connection, rate_limiter: RateLimiter = None):
        """Initialize the scraper.

        Args:
            conn: Database connection
            rate_limiter: Optional rate limiter (creates default if not provided)
        """
        self.conn = conn
        self.rate_limiter = rate_limiter or RateLimiter()
        self.state_manager = StateManager(conn, 'details')
        self.normalizer = DonorNormalizer()
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def _init_browser(self) -> None:
        """Initialize the browser."""
        playwright = await async_playwright().start()
        self._browser = await playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()

    async def _close_browser(self) -> None:
        """Close the browser."""
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()

    async def scrape(self, batch_size: int = 20, resume: bool = False,
                     progress_callback: Callable[[int, int], None] = None) -> dict:
        """Scrape contribution details for pending reports.

        Args:
            batch_size: Number of reports to process
            resume: If True, resume from last interrupted scrape
            progress_callback: Optional callback(current, total)

        Returns:
            Dictionary with scraping results
        """
        results = {
            'reports_processed': 0,
            'contributions_found': 0,
            'donors_created': 0,
            'skipped': 0,
            'errors': []
        }

        try:
            await self._init_browser()

            # Get pending reports
            pending_reports = Report.get_pending(self.conn, limit=batch_size)

            if not pending_reports:
                logger.info("No pending reports to process")
                return results

            # Check for resume
            if resume:
                state = self.state_manager.resume()
                if state and state.last_report_id:
                    # Filter out already processed reports
                    pending_reports = [r for r in pending_reports if r.id > state.last_report_id]
            else:
                self.state_manager.start(total_pages=len(pending_reports))

            total = len(pending_reports)
            logger.info(f"Processing {total} pending reports")

            for i, report in enumerate(pending_reports):
                if progress_callback:
                    progress_callback(i + 1, total)

                logger.info(f"Processing report {report.id}: {report.committee_name}")

                # Skip paper-filed reports (they shouldn't be in pending, but just in case)
                if report.is_paper_filed:
                    logger.info(f"Skipping paper-filed report {report.id}")
                    results['skipped'] += 1
                    continue

                # Skip reports without detail URL
                if not report.detail_url:
                    logger.warning(f"Report {report.id} has no detail URL, skipping")
                    report.scrape_status = 'skipped'
                    report.scrape_error = 'No detail URL'
                    report.save(self.conn)
                    results['skipped'] += 1
                    continue

                try:
                    # Scrape the detail page
                    self.rate_limiter.wait()
                    contributions = await self._scrape_detail_page(report)

                    # Save contributions
                    new_donors = 0
                    for contrib in contributions:
                        parsed_name, occupation, employer = parse_contributor_metadata(
                            contrib.raw_contributed_by or ''
                        )

                        # Get or create donor
                        norm_name, norm_address = self.normalizer.normalize(
                            parsed_name or '',
                            contrib.raw_address or ''
                        )

                        donor = Donor.get_or_create(
                            self.conn,
                            name=parsed_name or '',
                            address=contrib.raw_address or '',
                            normalized_name=norm_name,
                            normalized_address=norm_address,
                            occupation=occupation,
                            employer=employer
                        )

                        # Check if this is a new donor (no contributions yet)
                        existing_count = self.conn.execute(
                            "SELECT COUNT(*) FROM contributions WHERE donor_id = ?",
                            (donor.id,)
                        ).fetchone()[0]
                        if existing_count == 0:
                            new_donors += 1

                        contrib.donor_id = donor.id
                        contrib.raw_occupation = occupation
                        contrib.raw_employer = employer
                        contrib.report_id = report.id
                        contrib.save(self.conn)

                    results['contributions_found'] += len(contributions)
                    results['donors_created'] += new_donors

                    # Update report status
                    report.scrape_status = 'scraped'
                    report.scrape_error = None
                    report.save(self.conn)

                    self.rate_limiter.record_success()
                    results['reports_processed'] += 1

                except Exception as e:
                    error_msg = f"Error processing report {report.id}: {str(e)}"
                    logger.error(error_msg)
                    results['errors'].append(error_msg)

                    report.scrape_status = 'error'
                    report.scrape_error = str(e)
                    report.save(self.conn)

                    self.rate_limiter.record_error()

                    if self.rate_limiter.consecutive_errors > 5:
                        raise Exception("Too many consecutive errors, stopping")

                # Update progress
                self.state_manager.update_progress(report_id=report.id)

            self.state_manager.complete()

        except Exception as e:
            error_msg = f"Scrape failed: {str(e)}"
            logger.error(error_msg)
            results['errors'].append(error_msg)
            self.state_manager.error(error_msg)

        finally:
            await self._close_browser()

        return results

    async def _scrape_detail_page(self, report: Report) -> List[Contribution]:
        """Scrape contributions from a detail page.

        Args:
            report: The report to scrape details for

        Returns:
            List of Contribution objects
        """
        contributions = []

        # Navigate to detail page
        await self._page.goto(report.detail_url)
        await self._page.wait_for_load_state('networkidle')

        # Find the contributions table
        table = await self._page.query_selector('table.GridView, table[id*="GridView"]')
        if not table:
            # Try finding any table with data
            tables = await self._page.query_selector_all('table')
            for t in tables:
                rows = await t.query_selector_all('tr')
                if len(rows) > 1:
                    table = t
                    break

        if not table:
            logger.warning(f"No contributions table found for report {report.id}")
            return contributions

        # Get all rows
        rows = await table.query_selector_all('tr')

        # Try to identify header row and column positions
        headers = []
        header_row_idx = 0

        for i, row in enumerate(rows):
            header_cells = await row.query_selector_all('th')
            if header_cells:
                header_row_idx = i
                for cell in header_cells:
                    text = await cell.inner_text()
                    headers.append(text.strip().lower())
                break

        # Map column positions
        col_map = self._map_columns(headers)

        # Parse data rows
        for i, row in enumerate(rows):
            if i <= header_row_idx:
                continue

            try:
                parsed = await self._parse_contribution_row(row, col_map, report.id)
                if parsed:
                    contrib, raw_cells = parsed
                    contributions.append(contrib)

                    raw_identifier = make_source_identifier(
                        report.detail_url,
                        report.id,
                        i,
                        contrib.raw_contributed_by,
                        contrib.raw_address,
                        contrib.amount,
                        contrib.transaction_date,
                    )
                    raw_row = RawExtraction(
                        source_type='a1_contribution_row',
                        source_identifier=raw_identifier,
                        source_url=report.detail_url,
                        parser_version='a1_detail_v2',
                    )
                    raw_row.payload = {
                        'report_id': report.id,
                        'row_index': i,
                        'col_map': col_map,
                        'cells': raw_cells,
                    }
                    raw_row.save(self.conn)
            except Exception as e:
                logger.warning(f"Error parsing contribution row {i}: {e}")
                continue

        return contributions

    def _map_columns(self, headers: List[str]) -> dict:
        """Map header names to column indices.

        Args:
            headers: List of header names (lowercase)

        Returns:
            Dictionary mapping field names to column indices
        """
        col_map = {}

        # Define possible header variations for each field
        field_patterns = {
            'contributed_by': ['contributed by', 'contributor', 'name', 'donor'],
            'address': ['address', 'addr'],
            'amount': ['amount', 'amt', '$'],
            'received_by': ['received by', 'recipient', 'committee'],
            'description': ['description', 'desc', 'purpose', 'occupation'],
            'vendor_name': ['vendor name', 'vendor'],
            'vendor_address': ['vendor address', 'vendor addr'],
        }

        for i, header in enumerate(headers):
            for field, patterns in field_patterns.items():
                for pattern in patterns:
                    if pattern in header:
                        if field not in col_map:  # Don't overwrite if already found
                            col_map[field] = i
                        break

        return col_map

    async def _parse_contribution_row(
        self, row, col_map: dict, report_id: int
    ) -> Optional[Tuple[Contribution, List[str]]]:
        """Parse a contribution from a table row.

        Args:
            row: The table row element
            col_map: Column position mapping
            report_id: The report ID

        Returns:
            Tuple(Contribution, row cell text list) or None
        """
        cells = await row.query_selector_all('td')

        if not cells:
            return None

        # Extract cell texts
        cell_texts = []
        for cell in cells:
            text = await cell.inner_text()
            cell_texts.append(text.strip())

        # Get values based on column map, with fallbacks
        def get_val(field: str, default_idx: int = None) -> str:
            if field in col_map and col_map[field] < len(cell_texts):
                return cell_texts[col_map[field]]
            elif default_idx is not None and default_idx < len(cell_texts):
                return cell_texts[default_idx]
            return ''

        contributed_by = get_val('contributed_by', 0)
        address = get_val('address', 1)
        amount_str = get_val('amount', 2)
        received_by = get_val('received_by', 3)
        description = get_val('description', 4)
        vendor_name = get_val('vendor_name')
        vendor_address = get_val('vendor_address')

        # Skip empty rows
        if not contributed_by and not amount_str:
            return None

        amount, transaction_date = parse_amount_and_date(amount_str)

        return Contribution(
            report_id=report_id,
            amount=amount,
            transaction_date=transaction_date,
            received_by=received_by if received_by else None,
            description=description if description else None,
            vendor_name=vendor_name if vendor_name else None,
            vendor_address=vendor_address if vendor_address else None,
            raw_contributed_by=contributed_by if contributed_by else None,
            raw_address=address if address else None
        ), cell_texts
