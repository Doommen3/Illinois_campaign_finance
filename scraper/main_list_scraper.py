"""Main list scraper for Illinois State Board of Elections campaign finance data."""
import re
import logging
from typing import List, Optional, Callable
import sqlite3

from playwright.async_api import async_playwright, Page, Browser

from database.models import Committee, Report, ManualEntryQueue, RawExtraction
from database.identifiers import make_source_identifier
from .rate_limiter import RateLimiter
from .state_manager import StateManager
from .aspnet_helpers import do_postback, get_current_page_number, get_total_pages
from .text_parsing import is_garbage_committee_name

logger = logging.getLogger(__name__)

# Base URL for the Illinois State Board of Elections
BASE_URL = "https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx"


class MainListScraper:
    """Scrapes the main list of filed reports from ReportsFiled.aspx."""

    def __init__(self, conn: sqlite3.Connection, rate_limiter: RateLimiter = None):
        """Initialize the scraper.

        Args:
            conn: Database connection
            rate_limiter: Optional rate limiter (creates default if not provided)
        """
        self.conn = conn
        self.rate_limiter = rate_limiter or RateLimiter()
        self.state_manager = StateManager(conn, 'main_list')
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

    async def scrape(self, start_page: int = 1, end_page: int = 40,
                     resume: bool = False,
                     progress_callback: Callable[[int, int], None] = None) -> dict:
        """Scrape reports from the main list.

        Args:
            start_page: Starting page number
            end_page: Ending page number
            resume: If True, resume from last interrupted scrape
            progress_callback: Optional callback(current_page, total_pages)

        Returns:
            Dictionary with scraping results
        """
        results = {
            'pages_scraped': 0,
            'reports_found': 0,
            'paper_filed': 0,
            'errors': []
        }

        try:
            await self._init_browser()

            # Check for resume
            if resume:
                state = self.state_manager.resume()
                if state and state.last_page:
                    start_page = state.last_page + 1
                    logger.info(f"Resuming from page {start_page}")
            else:
                self.state_manager.start(total_pages=end_page - start_page + 1)

            # Navigate to the main page
            self.rate_limiter.wait()
            await self._page.goto(BASE_URL)
            await self._page.wait_for_load_state('networkidle')
            self.rate_limiter.record_success()

            # Get total pages if not specified
            detected_total = await get_total_pages(self._page)
            if detected_total:
                logger.info(f"Detected {detected_total} total pages")

            # Scrape each page
            current_page = 1

            # Navigate to start page if not page 1
            if start_page > 1:
                current_page = await self._navigate_to_page(start_page)
                if current_page != start_page:
                    logger.warning(f"Could not navigate to page {start_page}, starting from {current_page}")

            while current_page <= end_page:
                if progress_callback:
                    progress_callback(current_page, end_page)

                logger.info(f"Scraping page {current_page}")

                try:
                    # Parse the current page
                    reports = await self._parse_page(current_page)
                    results['reports_found'] += len(reports)
                    results['paper_filed'] += sum(1 for r in reports if r.is_paper_filed)

                    # Save reports to database
                    for report in reports:
                        report.save(self.conn)

                        # Add paper-filed reports to manual entry queue
                        if report.is_paper_filed:
                            ManualEntryQueue.add_report(
                                self.conn, report.id,
                                notes="Paper-filed report detected during scrape"
                            )

                    # Update progress
                    self.state_manager.update_progress(page=current_page)
                    results['pages_scraped'] += 1

                    self.rate_limiter.record_success()

                except Exception as e:
                    error_msg = f"Error on page {current_page}: {str(e)}"
                    logger.error(error_msg)
                    results['errors'].append(error_msg)
                    self.rate_limiter.record_error()

                    # Continue to next page
                    if self.rate_limiter.consecutive_errors > 5:
                        raise Exception("Too many consecutive errors, stopping")

                # Navigate to next page
                if current_page < end_page:
                    self.rate_limiter.wait()
                    next_page = await self._navigate_to_page(current_page + 1)
                    if next_page == current_page:
                        logger.info(f"Could not navigate past page {current_page}, may be at end")
                        break
                    current_page = next_page
                else:
                    break

            self.state_manager.complete()

        except Exception as e:
            error_msg = f"Scrape failed: {str(e)}"
            logger.error(error_msg)
            results['errors'].append(error_msg)
            self.state_manager.error(error_msg)

        finally:
            await self._close_browser()

        return results

    async def _navigate_to_page(self, target_page: int) -> int:
        """Navigate to a specific page number.

        Args:
            target_page: The page to navigate to

        Returns:
            The actual page number reached
        """
        current = await get_current_page_number(self._page) or 1

        if current == target_page:
            return current

        # Try to find and click the page link
        try:
            # Look for the page number link directly
            link = await self._page.query_selector(f'a:text-is("{target_page}")')

            if link:
                href = await link.get_attribute('href')
                if href and '__doPostBack' in href:
                    # Extract postback parameters
                    match = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", href)
                    if match:
                        await do_postback(self._page, match.group(1), match.group(2))
                        await self._page.wait_for_load_state('networkidle')
                        return await get_current_page_number(self._page) or target_page
                else:
                    await link.click()
                    await self._page.wait_for_load_state('networkidle')
                    return await get_current_page_number(self._page) or target_page

            # If direct link not found, we need to step through pages
            # This handles the "..." pagination where not all page numbers are visible
            while current < target_page:
                # First check if target page link is now visible
                target_link = await self._page.query_selector(f'a:text-is("{target_page}")')
                if target_link:
                    href = await target_link.get_attribute('href')
                    if href and '__doPostBack' in href:
                        match = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", href)
                        if match:
                            self.rate_limiter.wait()
                            await do_postback(self._page, match.group(1), match.group(2))
                            await self._page.wait_for_load_state('networkidle')
                            return await get_current_page_number(self._page) or target_page

                # Look for next page number, "..." (ellipsis), or ">" link
                next_link = await self._page.query_selector(f'a:text-is("{current + 1}")')

                if not next_link:
                    # Check for "..." which leads to next set of page numbers
                    next_link = await self._page.query_selector('a:text-is("...")')

                if not next_link:
                    next_link = await self._page.query_selector('a:text-is(">")')

                if not next_link:
                    next_link = await self._page.query_selector('a:text-is("Next")')

                if next_link:
                    href = await next_link.get_attribute('href')
                    if href and '__doPostBack' in href:
                        match = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", href)
                        if match:
                            self.rate_limiter.wait()
                            await do_postback(self._page, match.group(1), match.group(2))
                            await self._page.wait_for_load_state('networkidle')
                            new_page = await get_current_page_number(self._page)
                            if new_page:
                                current = new_page
                            else:
                                # If we clicked "...", we might have jumped multiple pages
                                current += 1
                    else:
                        self.rate_limiter.wait()
                        await next_link.click()
                        await self._page.wait_for_load_state('networkidle')
                        new_page = await get_current_page_number(self._page)
                        current = new_page if new_page else (current + 1)
                else:
                    # No navigation link found, we might be at the end
                    logger.warning(f"No navigation link found at page {current}, target was {target_page}")
                    break

            return current

        except Exception as e:
            logger.warning(f"Error navigating to page {target_page}: {e}")
            return current

    async def _parse_page(self, page_number: int) -> List[Report]:
        """Parse reports from the current page.

        Args:
            page_number: Current page number for source tracking

        Returns:
            List of Report objects
        """
        reports = []

        # Find the data table - typically a GridView
        table = await self._page.query_selector('table.GridView, table[id*="GridView"]')
        if not table:
            # Try finding any table with data rows
            table = await self._page.query_selector('table')

        if not table:
            logger.warning("No data table found on page")
            return reports

        # Get all rows except header
        rows = await table.query_selector_all('tr')

        for i, row in enumerate(rows):
            # Skip header row
            if i == 0:
                header_check = await row.query_selector('th')
                if header_check:
                    continue

            try:
                report = await self._parse_row(row, page_number)
                if report:
                    reports.append(report)
            except Exception as e:
                logger.warning(f"Error parsing row {i}: {e}")
                continue

        return reports

    async def _parse_row(self, row, page_number: int) -> Optional[Report]:
        """Parse a single table row into a Report.

        Args:
            row: The table row element
            page_number: Current page number

        Returns:
            Report object or None if parsing fails
        """
        cells = await row.query_selector_all('td')

        if len(cells) < 5:
            return None

        # Extract cell text
        cell_texts = []
        for cell in cells:
            text = await cell.inner_text()
            cell_texts.append(text.strip())

        # Expected columns: Committee Name, Report Type, Reporting Period, Filed, Pages, Clarification
        # The exact order may vary - we'll try to be flexible

        committee_name = cell_texts[0] if len(cell_texts) > 0 else ''
        report_type = cell_texts[1] if len(cell_texts) > 1 else ''
        reporting_period = cell_texts[2] if len(cell_texts) > 2 else ''
        filed_date = cell_texts[3] if len(cell_texts) > 3 else ''
        pages = cell_texts[4] if len(cell_texts) > 4 else ''
        clarification = cell_texts[5] if len(cell_texts) > 5 else ''

        if not committee_name or is_garbage_committee_name(committee_name):
            return None

        # Check for paper-filed indicator
        is_paper_filed = False
        full_row_text = ' '.join(cell_texts).lower()
        if 'filed on paper' in full_row_text or 'paper' in filed_date.lower():
            is_paper_filed = True

        # Try to parse pages as integer
        pages_int = None
        if pages:
            try:
                pages_int = int(re.sub(r'[^\d]', '', pages))
            except ValueError:
                pass

        # Extract committee detail URL from first cell
        committee_detail_url = None
        if len(cells) > 0:
            committee_link = await cells[0].query_selector('a')
            if committee_link:
                href = await committee_link.get_attribute('href')
                if href:
                    if href.startswith('http'):
                        committee_detail_url = href
                    else:
                        committee_detail_url = f"https://www.elections.il.gov/CampaignDisclosure/{href.lstrip('/')}"

        # Extract detail URL from the Report Type column (second cell)
        detail_url = None
        if len(cells) > 1:
            report_type_cell = cells[1]
            link = await report_type_cell.query_selector('a')
            if link:
                href = await link.get_attribute('href')
                if href:
                    detail_url = href
                    if not detail_url.startswith('http'):
                        detail_url = f"https://www.elections.il.gov/CampaignDisclosure/{detail_url.lstrip('/')}"

                    if 'CDPDFViewer.aspx' in detail_url:
                        # This is a paper-filed report
                        is_paper_filed = True

        # Get or create committee
        committee = Committee.get_or_create(
            self.conn,
            committee_name,
            detail_url=committee_detail_url
        )

        # Only A-1 pages are handled by the existing detail scraper.
        report_type_lower = (report_type or '').lower()
        can_scrape_details = bool(detail_url and ('a1list.aspx' in detail_url.lower() or 'a-1' in report_type_lower))
        initial_status = 'pending' if can_scrape_details and not is_paper_filed else 'skipped'

        # Create report
        report = Report(
            committee_id=committee.id,
            report_type=report_type,
            reporting_period=reporting_period,
            filed_date=filed_date,
            pages=pages_int,
            clarification=clarification,
            detail_url=detail_url,
            is_paper_filed=is_paper_filed,
            scrape_status=initial_status,
            source_page=page_number
        )

        raw_identifier = make_source_identifier(
            detail_url,
            committee.id,
            report_type,
            reporting_period,
            filed_date,
            pages_int,
            clarification,
            page_number,
        )
        raw_row = RawExtraction(
            source_type='main_list_row',
            source_identifier=raw_identifier,
            source_url=detail_url,
            parser_version='main_list_v2',
        )
        raw_row.payload = {
            'committee_name': committee_name,
            'committee_detail_url': committee_detail_url,
            'report_type': report_type,
            'reporting_period': reporting_period,
            'filed_date': filed_date,
            'pages': pages_int,
            'clarification': clarification,
            'is_paper_filed': is_paper_filed,
            'source_page': page_number,
            'raw_cells': cell_texts,
        }
        raw_row.save(self.conn)

        return report
