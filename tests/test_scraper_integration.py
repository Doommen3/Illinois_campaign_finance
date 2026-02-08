"""Integration tests for the scraper against the live website."""
import pytest
import pytest_asyncio
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright


class TestWebsiteStructure:
    """Tests to verify the website structure matches our expectations."""

    @pytest_asyncio.fixture
    async def page(self):
        """Create a browser page for testing."""
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        yield page
        await browser.close()
        await p.stop()

    @pytest.mark.asyncio
    async def test_main_page_loads(self, page):
        """Test that the main reports page loads."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        # Verify page title or content
        title = await page.title()
        assert "Report" in title or "Campaign" in title or "Election" in title

    @pytest.mark.asyncio
    async def test_table_exists(self, page):
        """Test that the reports table exists on the page."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        # Look for the GridView table
        table = await page.query_selector('table[id*="gvReportsFiled"]')
        assert table is not None, "Reports GridView table not found"

    @pytest.mark.asyncio
    async def test_table_has_header_row(self, page):
        """Test that the table has a header row with expected columns."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        table = await page.query_selector('table[id*="gvReportsFiled"]')
        assert table is not None

        header_cells = await table.query_selector_all('tr:first-child th')
        header_texts = []
        for cell in header_cells:
            text = await cell.inner_text()
            header_texts.append(text.strip().lower())

        # Verify expected columns exist
        combined = ' '.join(header_texts)
        assert 'committee' in combined, f"Expected 'committee' column, got: {header_texts}"
        assert 'report' in combined or 'type' in combined, f"Expected 'report type' column, got: {header_texts}"

    @pytest.mark.asyncio
    async def test_data_rows_have_two_links(self, page):
        """Test that data rows have links in first and second columns."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        table = await page.query_selector('table[id*="gvReportsFiled"]')
        assert table is not None

        # Get first data row (skip header)
        rows = await table.query_selector_all('tr')
        data_row = None
        for row in rows[1:]:  # Skip header
            cells = await row.query_selector_all('td')
            if len(cells) >= 2:
                data_row = row
                break

        assert data_row is not None, "No data rows found"

        cells = await data_row.query_selector_all('td')

        # First cell should have CommitteeDetail link
        first_cell_link = await cells[0].query_selector('a')
        assert first_cell_link is not None, "First cell should have a link"
        first_href = await first_cell_link.get_attribute('href')
        assert 'CommitteeDetail' in first_href, f"First link should be CommitteeDetail, got: {first_href}"

        # Second cell should have report detail link (A1List or CDPDFViewer)
        second_cell_link = await cells[1].query_selector('a')
        assert second_cell_link is not None, "Second cell should have a link"
        second_href = await second_cell_link.get_attribute('href')
        assert 'A1List' in second_href or 'CDPDFViewer' in second_href, \
            f"Second link should be A1List or CDPDFViewer, got: {second_href}"

    @pytest.mark.asyncio
    async def test_detail_url_in_second_column(self, page):
        """Test that detail URLs are extracted from second column."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        table = await page.query_selector('table[id*="gvReportsFiled"]')
        rows = await table.query_selector_all('tr')

        detail_urls_found = 0
        for row in rows[1:6]:  # Check first 5 data rows
            cells = await row.query_selector_all('td')
            if len(cells) >= 2:
                second_cell_link = await cells[1].query_selector('a')
                if second_cell_link:
                    href = await second_cell_link.get_attribute('href')
                    if href and ('A1List' in href or 'CDPDFViewer' in href):
                        detail_urls_found += 1

        assert detail_urls_found > 0, "Should find at least one detail URL in second column"

    @pytest.mark.asyncio
    async def test_pagination_exists(self, page):
        """Test that pagination controls exist."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        # Look for page number links
        page_links = await page.query_selector_all('a[href*="Page$"]')
        assert len(page_links) > 0, "Pagination links not found"

    @pytest.mark.asyncio
    async def test_pagination_has_ellipsis(self, page):
        """Test that pagination has ellipsis for accessing more pages."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        # Look for "..." link
        ellipsis_link = await page.query_selector('a:text-is("...")')
        assert ellipsis_link is not None, "Ellipsis (...) pagination link not found"

        # Verify it's a postback link
        href = await ellipsis_link.get_attribute('href')
        assert '__doPostBack' in href, f"Ellipsis should be a postback link, got: {href}"

    @pytest.mark.asyncio
    async def test_page_numbers_visible(self, page):
        """Test that page numbers 1-10 are visible initially."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        # Page 1 should be current (shown as span, not link)
        page1_span = await page.query_selector('td span:text-is("1")')
        assert page1_span is not None, "Page 1 indicator not found"

        # Page 2 should be a link
        page2_link = await page.query_selector('a:text-is("2")')
        assert page2_link is not None, "Page 2 link not found"

    @pytest.mark.asyncio
    async def test_ellipsis_click_shows_more_pages(self, page):
        """Test that clicking ellipsis reveals more page numbers."""
        await page.goto("https://www.elections.il.gov/CampaignDisclosure/ReportsFiled.aspx")
        await page.wait_for_load_state('networkidle')

        # Click the ellipsis
        ellipsis_link = await page.query_selector('a:text-is("...")')
        if ellipsis_link:
            await ellipsis_link.click()
            await page.wait_for_load_state('networkidle')

            # After clicking, page 11 should be visible (either as current or as link)
            page11 = await page.query_selector('span:text-is("11")') or \
                     await page.query_selector('a:text-is("11")')
            assert page11 is not None, "Page 11 should be visible after clicking ellipsis"


class TestRateLimiter:
    """Tests for the rate limiter."""

    def test_rate_limiter_backoff(self):
        """Test that rate limiter increases backoff on errors."""
        from scraper.rate_limiter import RateLimiter

        limiter = RateLimiter(min_delay=0.1, max_delay=0.2, backoff_multiplier=2.0, max_backoff=10.0)

        assert limiter.current_backoff == 0
        assert limiter.consecutive_errors == 0

        limiter.record_error()
        assert limiter.consecutive_errors == 1
        assert limiter.current_backoff > 0

        limiter.record_error()
        assert limiter.consecutive_errors == 2
        assert limiter.current_backoff >= 0.2  # Should have increased

    def test_rate_limiter_success_resets(self):
        """Test that success resets backoff."""
        from scraper.rate_limiter import RateLimiter

        limiter = RateLimiter(min_delay=0.1)

        limiter.record_error()
        limiter.record_error()
        assert limiter.consecutive_errors == 2

        limiter.record_success()
        assert limiter.consecutive_errors == 0
        assert limiter.current_backoff == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
