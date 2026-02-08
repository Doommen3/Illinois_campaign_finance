"""ASP.NET postback helpers for scraping Illinois State Board of Elections website."""
import re
from typing import Optional, Tuple
from playwright.async_api import Page


async def get_viewstate(page: Page) -> dict:
    """Extract ASP.NET ViewState fields from the page.

    Args:
        page: Playwright page object

    Returns:
        Dictionary with __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION
    """
    viewstate = {}

    # Try to get each field
    for field_name in ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION', '__EVENTTARGET', '__EVENTARGUMENT']:
        try:
            element = await page.query_selector(f'input[name="{field_name}"]')
            if element:
                viewstate[field_name] = await element.get_attribute('value') or ''
        except Exception:
            viewstate[field_name] = ''

    return viewstate


async def do_postback(page: Page, event_target: str, event_argument: str = '') -> None:
    """Execute an ASP.NET __doPostBack call.

    This simulates clicking on a link that uses JavaScript postback.

    Args:
        page: Playwright page object
        event_target: The __EVENTTARGET value (e.g., 'ctl00$ContentPlaceHolder1$GridView1')
        event_argument: The __EVENTARGUMENT value (e.g., 'Page$2')
    """
    # Set the hidden form fields
    await page.evaluate(f'''() => {{
        document.getElementById('__EVENTTARGET').value = '{event_target}';
        document.getElementById('__EVENTARGUMENT').value = '{event_argument}';
    }}''')

    # Submit the form
    await page.evaluate('''() => {
        document.forms[0].submit();
    }''')

    # Wait for navigation
    await page.wait_for_load_state('networkidle')


async def click_pagination_link(page: Page, page_number: int) -> bool:
    """Click on a pagination link in an ASP.NET GridView.

    Args:
        page: Playwright page object
        page_number: The page number to navigate to

    Returns:
        True if navigation was successful, False otherwise
    """
    try:
        # Look for pagination links - they're typically in a table row at the bottom
        # The format varies but usually includes the page number in the link text

        # First, try to find a direct link with the page number
        link = await page.query_selector(f'a:text-is("{page_number}")')

        if link:
            # Check if this is a postback link
            href = await link.get_attribute('href')
            if href and '__doPostBack' in href:
                # Extract the parameters from the href
                match = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", href)
                if match:
                    event_target = match.group(1)
                    event_argument = match.group(2)
                    await do_postback(page, event_target, event_argument)
                    return True

            # Otherwise, just click it
            await link.click()
            await page.wait_for_load_state('networkidle')
            return True

        return False

    except Exception as e:
        print(f"Error clicking pagination link for page {page_number}: {e}")
        return False


async def get_current_page_number(page: Page) -> Optional[int]:
    """Get the current page number from pagination controls.

    Args:
        page: Playwright page object

    Returns:
        Current page number or None if not found
    """
    try:
        # Look for the current page indicator (usually a span or disabled link)
        # In ASP.NET GridView, the current page is usually displayed as plain text
        # wrapped in a span, while other pages are links

        # Try to find a span with just a number inside the pager row
        pager_spans = await page.query_selector_all('tr td span')
        for span in pager_spans:
            text = await span.inner_text()
            text = text.strip()
            if text.isdigit():
                return int(text)

        return None

    except Exception:
        return None


async def get_total_pages(page: Page) -> Optional[int]:
    """Get the total number of pages from pagination controls.

    Args:
        page: Playwright page object

    Returns:
        Total page count or None if not found
    """
    try:
        # Look for all pagination links/spans
        # The highest number is typically the total (or close to it)
        max_page = 1

        # Check all links and spans in the pager area
        elements = await page.query_selector_all('tr td a, tr td span')
        for element in elements:
            text = await element.inner_text()
            text = text.strip()
            if text.isdigit():
                max_page = max(max_page, int(text))

        # Also check for "..." links which might have higher page numbers
        # Look for links that might be "Last" or ">>" type
        last_links = await page.query_selector_all('a:text-matches("^(Last|>>|>)$", "i")')
        for link in last_links:
            href = await link.get_attribute('href')
            if href:
                # Try to extract page number from postback
                match = re.search(r"Page\$(\d+)", href)
                if match:
                    max_page = max(max_page, int(match.group(1)))

        return max_page if max_page > 0 else None

    except Exception:
        return None


def parse_postback_href(href: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse a __doPostBack href to extract event target and argument.

    Args:
        href: The href attribute value

    Returns:
        Tuple of (event_target, event_argument)
    """
    if not href or '__doPostBack' not in href:
        return None, None

    match = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", href)
    if match:
        return match.group(1), match.group(2)

    return None, None
