"""Scrapers for committee pages, D-2 reports, and D-2 itemized rows."""
from __future__ import annotations

from datetime import datetime, date
import logging
import re
from typing import Callable, Optional, List
import sqlite3

from playwright.async_api import async_playwright, Page, Browser

from database.identifiers import make_source_identifier, normalize_source_url
from database.models import Committee, Report, D2Report, D2ItemizedLink, D2ItemizedEntry
from .aspnet_helpers import do_postback, get_current_page_number, parse_postback_href
from .rate_limiter import RateLimiter
from .state_manager import StateManager

logger = logging.getLogger(__name__)

COMMITTEE_SEARCH_URL = "https://www.elections.il.gov/CampaignDisclosure/CommitteeSearch.aspx"


class CommitteeReportScraper:
    """Scrape each committee detail page and collect A-1 + D-2 report links."""

    def __init__(self, conn: sqlite3.Connection, rate_limiter: RateLimiter = None):
        self.conn = conn
        self.rate_limiter = rate_limiter or RateLimiter()
        self.state_manager = StateManager(conn, "committee_reports")
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def _init_browser(self) -> None:
        playwright = await async_playwright().start()
        self._browser = await playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()

    async def _close_browser(self) -> None:
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()

    async def scrape_committees(
        self,
        committee_ids: Optional[List[int]] = None,
        committee_ids_sbe: Optional[List[int]] = None,
        batch_size: int = 20,
        filed_cutoff: str = "2025-06-01",
        progress_callback: Callable[[int, int], None] = None,
    ) -> dict:
        """Scrape committee report tables up to the filed-date cutoff."""
        cutoff_date = _parse_date(filed_cutoff) or date(2025, 6, 1)
        results = {
            "committees_processed": 0,
            "a1_reports_saved": 0,
            "d2_reports_saved": 0,
            "stopped_by_cutoff": 0,
            "errors": [],
        }

        committees = self._get_target_committees(
            committee_ids=committee_ids,
            committee_ids_sbe=committee_ids_sbe,
            batch_size=batch_size,
        )
        if not committees:
            return results

        try:
            await self._init_browser()
            self.state_manager.start(total_pages=len(committees))

            for index, committee in enumerate(committees, start=1):
                if progress_callback:
                    progress_callback(index, len(committees))

                logger.info("Scraping committee %s (%s)", committee.id, committee.name)

                try:
                    saved_a1, saved_d2, cutoff_reached = await self._scrape_committee(committee, cutoff_date)
                    results["a1_reports_saved"] += saved_a1
                    results["d2_reports_saved"] += saved_d2
                    if cutoff_reached:
                        results["stopped_by_cutoff"] += 1
                    results["committees_processed"] += 1
                    self.rate_limiter.record_success()
                except Exception as exc:  # pragma: no cover - network path
                    msg = f"Committee {committee.id} failed: {exc}"
                    logger.exception(msg)
                    results["errors"].append(msg)
                    self.rate_limiter.record_error()

                self.state_manager.update_progress(report_id=committee.id)

            self.state_manager.complete()
        except Exception as exc:  # pragma: no cover - network path
            msg = f"Committee scrape failed: {exc}"
            logger.exception(msg)
            results["errors"].append(msg)
            self.state_manager.error(msg)
        finally:
            await self._close_browser()

        return results

    def _get_target_committees(
        self,
        committee_ids: Optional[List[int]],
        committee_ids_sbe: Optional[List[int]],
        batch_size: int,
    ) -> List[Committee]:
        filters = []
        params: List[object] = []

        if committee_ids:
            placeholders = ",".join(["?"] * len(committee_ids))
            filters.append(f"id IN ({placeholders})")
            params.extend(committee_ids)

        if committee_ids_sbe:
            placeholders = ",".join(["?"] * len(committee_ids_sbe))
            filters.append(f"committee_id_sbe IN ({placeholders})")
            params.extend(committee_ids_sbe)

        if filters:
            where_clause = "WHERE " + " OR ".join(filters)
            limit_clause = ""
        else:
            where_clause = "WHERE detail_url IS NOT NULL AND detail_url != ''"
            limit_clause = "LIMIT ?"
            params.append(batch_size)

        rows = self.conn.execute(
            f"""
            SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
            FROM committees
            {where_clause}
            ORDER BY id ASC
            {limit_clause}
            """,
            params,
        ).fetchall()

        return [
            Committee(
                id=row["id"],
                name=row["name"],
                committee_id_sbe=row["committee_id_sbe"] if "committee_id_sbe" in row.keys() else None,
                detail_url=row["detail_url"],
                source_identifier=row["source_identifier"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def _scrape_committee(self, committee: Committee, cutoff_date: date) -> tuple[int, int, bool]:
        if not committee.detail_url:
            return 0, 0, False

        self.rate_limiter.wait()
        await self._page.goto(committee.detail_url)
        await self._page.wait_for_load_state("networkidle")

        current_page = 1
        saved_a1 = 0
        saved_d2 = 0
        cutoff_reached = False

        while True:
            parsed_rows = await self._parse_committee_report_rows()
            if not parsed_rows:
                break

            for row_data in parsed_rows:
                filed_date = _parse_date(row_data.get("filed_date", ""))
                if filed_date and filed_date < cutoff_date:
                    cutoff_reached = True
                    break

                report_type = (row_data.get("report_type") or "").strip()
                report_type_lower = report_type.lower()

                if report_type_lower.startswith("a-1"):
                    report = Report(
                        committee_id=committee.id,
                        report_type=report_type,
                        reporting_period=row_data.get("reporting_period"),
                        filed_date=row_data.get("filed_date"),
                        pages=row_data.get("pages"),
                        clarification=row_data.get("clarification"),
                        detail_url=row_data.get("detail_url"),
                        source_identifier=make_source_identifier(
                            row_data.get("detail_url"),
                            committee.id,
                            report_type,
                            row_data.get("reporting_period"),
                            row_data.get("filed_date"),
                        ),
                        is_paper_filed=False,
                        scrape_status="pending" if row_data.get("detail_url") else "skipped",
                        source_page=current_page,
                    )
                    previous_id = report.id
                    report.save(self.conn)
                    if report.id and report.id != previous_id:
                        saved_a1 += 1

                elif report_type_lower.startswith("d-2"):
                    d2_report = D2Report(
                        committee_id=committee.id,
                        report_type=report_type,
                        reporting_period=row_data.get("reporting_period"),
                        filed_date=row_data.get("filed_date"),
                        pages=row_data.get("pages"),
                        clarification=row_data.get("clarification"),
                        detail_url=row_data.get("detail_url"),
                        source_identifier=make_source_identifier(
                            row_data.get("detail_url"),
                            committee.id,
                            report_type,
                            row_data.get("reporting_period"),
                            row_data.get("filed_date"),
                        ),
                        source_page=current_page,
                    )
                    previous_id = d2_report.id
                    d2_report.save(self.conn)
                    if d2_report.id and d2_report.id != previous_id:
                        saved_d2 += 1

            if cutoff_reached:
                break

            next_page = await _navigate_to_next_page(self._page, current_page=current_page)
            if not next_page:
                break
            current_page = next_page

        return saved_a1, saved_d2, cutoff_reached

    async def _parse_committee_report_rows(self) -> List[dict]:
        table = await self._find_grid_table()
        if not table:
            return []

        rows = await table.query_selector_all("tr")
        parsed = []

        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) < 5:
                continue

            values = []
            for cell in cells:
                text = (await cell.inner_text()).strip()
                values.append(text)

            report_type = values[0] if len(values) > 0 else ""
            reporting_period = values[1] if len(values) > 1 else ""
            filed_date = values[2] if len(values) > 2 else ""
            pages_text = values[3] if len(values) > 3 else ""
            clarification = values[4] if len(values) > 4 else ""

            detail_url = None
            link = await cells[0].query_selector("a")
            if link:
                href = await link.get_attribute("href")
                detail_url = normalize_source_url(href)

            pages_int = None
            if pages_text:
                digits = re.sub(r"[^\d]", "", pages_text)
                if digits:
                    pages_int = int(digits)

            parsed.append(
                {
                    "report_type": report_type,
                    "reporting_period": reporting_period,
                    "filed_date": filed_date,
                    "pages": pages_int,
                    "clarification": clarification,
                    "detail_url": detail_url,
                }
            )

        return parsed

    async def _find_grid_table(self):
        table = await self._page.query_selector('table.GridView, table[id*="GridView"], table[id*="gv"]')
        if table:
            return table

        tables = await self._page.query_selector_all("table")
        for table in tables:
            rows = await table.query_selector_all("tr")
            if len(rows) > 1:
                return table
        return None


class D2DetailScraper:
    """Scrape D-2 detail pages and their itemized pages."""

    def __init__(self, conn: sqlite3.Connection, rate_limiter: RateLimiter = None):
        self.conn = conn
        self.rate_limiter = rate_limiter or RateLimiter()
        self.state_manager = StateManager(conn, "d2_details")
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def _init_browser(self) -> None:
        playwright = await async_playwright().start()
        self._browser = await playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()

    async def _close_browser(self) -> None:
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()

    async def scrape_d2_details(
        self,
        committee_ids: Optional[List[int]] = None,
        batch_size: int = 20,
        progress_callback: Callable[[int, int], None] = None,
        scrape_itemized: bool = False,
    ) -> dict:
        """Scrape pending D-2 report detail pages, optionally including itemized links."""
        pending = D2Report.get_pending_details(self.conn, limit=batch_size, committee_ids=committee_ids)
        results = {
            "reports_processed": 0,
            "itemized_links_found": 0,
            "itemized_rows_saved": 0,
            "errors": [],
        }

        if not pending:
            return results

        try:
            await self._init_browser()
            self.state_manager.start(total_pages=len(pending))

            for idx, report in enumerate(pending, start=1):
                if progress_callback:
                    progress_callback(idx, len(pending))

                try:
                    links = await self._scrape_single_d2_detail(report)
                    results["reports_processed"] += 1
                    results["itemized_links_found"] += len(links)

                    if scrape_itemized and links:
                        for link in links:
                            rows_saved = await self._scrape_itemized_link(link)
                            results["itemized_rows_saved"] += rows_saved

                    self.rate_limiter.record_success()
                except Exception as exc:  # pragma: no cover - network path
                    msg = f"D2 report {report.id} failed: {exc}"
                    logger.exception(msg)
                    report.detail_scrape_status = "error"
                    report.detail_scrape_error = str(exc)
                    report.save(self.conn)
                    results["errors"].append(msg)
                    self.rate_limiter.record_error()

                self.state_manager.update_progress(report_id=report.id)

            self.state_manager.complete()
        except Exception as exc:  # pragma: no cover - network path
            msg = f"D2 detail scrape failed: {exc}"
            logger.exception(msg)
            results["errors"].append(msg)
            self.state_manager.error(msg)
        finally:
            await self._close_browser()

        return results

    async def scrape_pending_itemized(
        self,
        committee_ids: Optional[List[int]] = None,
        batch_size: int = 50,
        progress_callback: Callable[[int, int], None] = None,
    ) -> dict:
        """Scrape pending D-2 itemized links as a separate batch command."""
        pending_links = D2ItemizedLink.get_pending(self.conn, limit=batch_size, committee_ids=committee_ids)
        results = {
            "links_processed": 0,
            "rows_saved": 0,
            "errors": [],
        }
        if not pending_links:
            return results

        try:
            await self._init_browser()
            self.state_manager.start(total_pages=len(pending_links))

            for idx, link in enumerate(pending_links, start=1):
                if progress_callback:
                    progress_callback(idx, len(pending_links))

                try:
                    rows_saved = await self._scrape_itemized_link(link)
                    results["links_processed"] += 1
                    results["rows_saved"] += rows_saved
                    self.rate_limiter.record_success()
                except Exception as exc:  # pragma: no cover - network path
                    msg = f"Itemized link {link.id} failed: {exc}"
                    logger.exception(msg)
                    results["errors"].append(msg)
                    link.status = "error"
                    link.error_message = str(exc)
                    link.save(self.conn)
                    self.rate_limiter.record_error()

                self.state_manager.update_progress(report_id=link.id)

            self.state_manager.complete()
        except Exception as exc:  # pragma: no cover - network path
            msg = f"Itemized scrape failed: {exc}"
            logger.exception(msg)
            results["errors"].append(msg)
            self.state_manager.error(msg)
        finally:
            await self._close_browser()

        return results

    async def _scrape_single_d2_detail(self, report: D2Report) -> List[D2ItemizedLink]:
        if not report.detail_url:
            report.detail_scrape_status = "error"
            report.detail_scrape_error = "Missing detail URL"
            report.save(self.conn)
            return []

        self.rate_limiter.wait()
        await self._page.goto(report.detail_url)
        await self._page.wait_for_load_state("networkidle")

        summary = await self._parse_summary_fields()
        report.summary = summary

        links = await self._extract_itemized_links(report)
        report.detail_scrape_status = "scraped"
        report.detail_scrape_error = None
        report.itemized_scrape_status = "pending" if links else "no_itemized"
        report.itemized_scrape_error = None
        report.save(self.conn)

        return links

    async def _parse_summary_fields(self) -> dict:
        summary = {}

        rows = await self._page.query_selector_all("table tr")
        for row in rows:
            ths = await row.query_selector_all("th")
            tds = await row.query_selector_all("td")

            if ths and tds:
                label = (await ths[0].inner_text()).strip().rstrip(":")
                value = (await tds[0].inner_text()).strip()
                if label and value:
                    summary[label] = value
                continue

            if len(tds) >= 2:
                label = (await tds[0].inner_text()).strip().rstrip(":")
                value = (await tds[1].inner_text()).strip()
                if label and value:
                    summary[label] = value

        return summary

    async def _extract_itemized_links(self, report: D2Report) -> List[D2ItemizedLink]:
        collected: List[D2ItemizedLink] = []
        anchors = await self._page.query_selector_all('a[id*="Itmzd"], a[href*="Itemized"], a:text-matches("itemized", "i")')

        for anchor in anchors:
            href = await anchor.get_attribute("href")
            if not href:
                continue

            label = (await anchor.inner_text()).strip()
            url = normalize_source_url(href)
            itemized_type = _classify_itemized_type(label=label, href=url)

            link = D2ItemizedLink(
                d2_report_id=report.id,
                label=label,
                itemized_type=itemized_type,
                url=url,
                source_identifier=make_source_identifier(url, label),
                status="pending",
            )
            link.save(self.conn)
            collected.append(link)

        return collected

    async def _scrape_itemized_link(self, link: D2ItemizedLink) -> int:
        if not link.url:
            link.status = "error"
            link.error_message = "Missing URL"
            link.save(self.conn)
            return 0

        self.rate_limiter.wait()
        await self._page.goto(link.url)
        await self._page.wait_for_load_state("networkidle")

        current_page = 1
        total_saved = 0

        while True:
            parsed_rows = await self._parse_itemized_rows(link)
            total_saved += parsed_rows

            next_page = await _navigate_to_next_page(self._page, current_page=current_page)
            if not next_page:
                break
            current_page = next_page

        link.status = "scraped"
        link.error_message = None
        link.save(self.conn)

        d2_report = D2Report.get_by_id(self.conn, link.d2_report_id)
        if d2_report:
            d2_report.itemized_scrape_status = "scraped"
            d2_report.itemized_scrape_error = None
            d2_report.save(self.conn)

        return total_saved

    async def _parse_itemized_rows(self, link: D2ItemizedLink) -> int:
        table = await self._find_itemized_table()
        if not table:
            return 0

        rows = await table.query_selector_all("tr")
        if not rows:
            return 0

        headers, header_idx = await _extract_headers(rows)
        if not headers:
            return 0

        entry_type = _entry_type_from_headers(headers) or link.itemized_type or "other"
        col_map = _map_itemized_columns(headers)

        current_page = await get_current_page_number(self._page) or 1
        saved = 0

        for idx, row in enumerate(rows):
            if idx <= header_idx:
                continue

            cells = await row.query_selector_all("td")
            if not cells:
                continue

            values = [(await cell.inner_text()).strip() for cell in cells]
            if not any(values):
                continue

            amount = _parse_amount(values[col_map["amount"]]) if "amount" in col_map and col_map["amount"] < len(values) else None
            row_hash = make_source_identifier(
                None,
                link.id,
                current_page,
                idx,
                *values,
            )

            entry = D2ItemizedEntry(
                d2_report_id=link.d2_report_id,
                itemized_link_id=link.id,
                source_page=current_page,
                source_row=idx,
                row_hash=row_hash,
                entry_type=entry_type,
                contributed_by=_value(values, col_map, "contributed_by"),
                received_by=_value(values, col_map, "received_by"),
                address=_value(values, col_map, "address"),
                amount=amount,
                description=_value(values, col_map, "description"),
                vendor_name=_value(values, col_map, "vendor_name"),
                vendor_address=_value(values, col_map, "vendor_address"),
                expended_by=_value(values, col_map, "expended_by"),
                purpose_beneficiary=_value(values, col_map, "purpose_beneficiary"),
                candidate_name=_value(values, col_map, "candidate_name"),
                office_district=_value(values, col_map, "office_district"),
                supporting_opposing=_value(values, col_map, "supporting_opposing"),
            )
            entry.save(self.conn)
            saved += 1

        return saved

    async def _find_itemized_table(self):
        table = await self._page.query_selector('table.GridView, table[id*="GridView"], table[id*="gv"]')
        if table:
            return table

        tables = await self._page.query_selector_all("table")
        for table in tables:
            rows = await table.query_selector_all("tr")
            if len(rows) > 1:
                return table
        return None


class CommitteeUrlSeeder:
    """Resolve committee detail URLs from CommitteeSearch.aspx using committee_id_sbe."""

    def __init__(self, conn: sqlite3.Connection, rate_limiter: RateLimiter = None):
        self.conn = conn
        self.rate_limiter = rate_limiter or RateLimiter()
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def _init_browser(self) -> None:
        playwright = await async_playwright().start()
        self._browser = await playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()

    async def _close_browser(self) -> None:
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()

    async def seed_urls(
        self,
        committee_ids_sbe: Optional[List[int]] = None,
        batch_size: int = 200,
        include_existing: bool = False,
        progress_callback: Callable[[int, int], None] = None,
    ) -> dict:
        """Seed committee detail URLs by searching for SBE committee IDs."""
        targets = self._get_target_committees(
            committee_ids_sbe=committee_ids_sbe,
            batch_size=batch_size,
            include_existing=include_existing,
        )
        results = {
            "committees_targeted": len(targets),
            "urls_seeded": 0,
            "urls_unchanged": 0,
            "missing_results": 0,
            "errors": [],
        }
        if not targets:
            return results

        try:
            await self._init_browser()
            for idx, committee in enumerate(targets, start=1):
                if progress_callback:
                    progress_callback(idx, len(targets))

                try:
                    detail_url, resolved_name = await self._resolve_committee_detail(committee.committee_id_sbe)
                    if not detail_url:
                        results["missing_results"] += 1
                        continue

                    normalized_url = normalize_source_url(detail_url)
                    normalized_name = (resolved_name or committee.name or f"Committee {committee.committee_id_sbe}").strip()
                    updated = Committee.get_or_create(
                        self.conn,
                        name=normalized_name,
                        committee_id_sbe=committee.committee_id_sbe,
                        detail_url=normalized_url,
                        source_identifier=make_source_identifier(normalized_url, committee.committee_id_sbe),
                    )
                    if updated.detail_url == normalized_url and updated.id:
                        if committee.detail_url != normalized_url:
                            results["urls_seeded"] += 1
                        else:
                            results["urls_unchanged"] += 1

                    self.rate_limiter.record_success()
                except Exception as exc:  # pragma: no cover - network path
                    msg = f"SBE committee {committee.committee_id_sbe} failed: {exc}"
                    logger.exception(msg)
                    results["errors"].append(msg)
                    self.rate_limiter.record_error()
        finally:
            await self._close_browser()

        return results

    def _get_target_committees(
        self,
        committee_ids_sbe: Optional[List[int]],
        batch_size: int,
        include_existing: bool,
    ) -> List[Committee]:
        if committee_ids_sbe:
            unique_sbe = list(dict.fromkeys(committee_ids_sbe))
            placeholders = ",".join(["?"] * len(unique_sbe))
            rows = self.conn.execute(
                f"""
                SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
                FROM committees
                WHERE committee_id_sbe IN ({placeholders})
                ORDER BY id ASC
                """,
                unique_sbe,
            ).fetchall()

            seen = set()
            committees: List[Committee] = []
            for row in rows:
                sbe_id = row["committee_id_sbe"]
                if sbe_id in seen:
                    continue
                seen.add(sbe_id)
                committees.append(Committee._from_row(row))

            for sbe_id in unique_sbe:
                if sbe_id in seen:
                    continue
                committees.append(
                    Committee(
                        id=None,
                        name=f"Committee {sbe_id}",
                        committee_id_sbe=sbe_id,
                        detail_url=None,
                        source_identifier=None,
                        created_at=None,
                        updated_at=None,
                    )
                )
            return committees

        detail_filter = "" if include_existing else "AND (detail_url IS NULL OR detail_url = '')"
        rows = self.conn.execute(
            f"""
            SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
            FROM committees
            WHERE committee_id_sbe IS NOT NULL
              {detail_filter}
            ORDER BY id ASC
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        committees = [Committee._from_row(row) for row in rows]

        # Fallback: bootstrap targets from bulk committee registry when local
        # committees table has not yet been populated with committee_id_sbe.
        remaining = batch_size - len(committees)
        if remaining > 0 and self._table_exists("bulk_committees_clean"):
            existing_sbe = {c.committee_id_sbe for c in committees if c.committee_id_sbe is not None}
            bulk_filter = "" if include_existing else "AND (c.detail_url IS NULL OR TRIM(c.detail_url) = '')"
            bulk_rows = self.conn.execute(
                f"""
                SELECT
                    c.id,
                    COALESCE(c.name, b.committee_name) AS name,
                    b.committee_id_sbe,
                    c.detail_url,
                    c.source_identifier,
                    c.created_at,
                    c.updated_at
                FROM bulk_committees_clean b
                LEFT JOIN committees c
                  ON c.committee_id_sbe = b.committee_id_sbe
                WHERE b.committee_id_sbe IS NOT NULL
                  {bulk_filter}
                ORDER BY b.committee_id_sbe ASC
                LIMIT ?
                """,
                (remaining,),
            ).fetchall()

            for row in bulk_rows:
                sbe_id = row["committee_id_sbe"]
                if sbe_id in existing_sbe:
                    continue
                existing_sbe.add(sbe_id)
                committees.append(
                    Committee(
                        id=row["id"],
                        name=row["name"] or f"Committee {sbe_id}",
                        committee_id_sbe=sbe_id,
                        detail_url=row["detail_url"],
                        source_identifier=row["source_identifier"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )

        return committees

    def _table_exists(self, table_name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    async def _resolve_committee_detail(self, committee_id_sbe: int | None) -> tuple[Optional[str], Optional[str]]:
        if committee_id_sbe is None:
            return None, None

        self.rate_limiter.wait()
        await self._page.goto(COMMITTEE_SEARCH_URL)
        await self._page.wait_for_load_state("networkidle")

        input_box = await self._page.query_selector(
            'input[id*="txtCommitteeID"], input[name*="txtCommitteeID"], input[id*="CommitteeID"]'
        )
        if not input_box:
            return None, None

        await input_box.fill(str(committee_id_sbe))

        search_button = await self._page.query_selector(
            'input[type="submit"][id*="Search"], input[type="submit"][name*="Search"], '
            'button[id*="Search"], button[name*="Search"], a[id*="btnSearch"]'
        )
        if search_button:
            await search_button.click()
        else:
            await input_box.press("Enter")
        await self._page.wait_for_load_state("networkidle")

        current_url = normalize_source_url(self._page.url)
        if current_url and "CommitteeDetail.aspx" in current_url:
            return current_url, await _extract_committee_name(self._page)

        result_link = await self._page.query_selector('a[href*="CommitteeDetail.aspx"]')
        if not result_link:
            return None, None

        href = await result_link.get_attribute("href")
        if href and "__doPostBack" in href:
            target, argument = parse_postback_href(href)
            if target:
                await do_postback(self._page, target, argument or "")
                await self._page.wait_for_load_state("networkidle")
                postback_url = normalize_source_url(self._page.url)
                if postback_url and "CommitteeDetail.aspx" in postback_url:
                    return postback_url, await _extract_committee_name(self._page)

        resolved_url = normalize_source_url(href)
        resolved_name = (await result_link.inner_text()).strip()
        return resolved_url, resolved_name or None


async def _navigate_to_next_page(page: Page, current_page: int) -> Optional[int]:
    """Navigate to the next grid page, handling ASP.NET links and ellipsis sets."""
    link = await page.query_selector(f'a:text-is("{current_page + 1}")')
    if not link:
        link = await page.query_selector('a:text-is("...")')
    if not link:
        link = await page.query_selector('a:text-is(">")')
    if not link:
        link = await page.query_selector('a:text-is("Next")')

    if not link:
        return None

    href = await link.get_attribute("href")
    if href and "__doPostBack" in href:
        target, argument = parse_postback_href(href)
        if target:
            await do_postback(page, target, argument or "")
    else:
        await link.click()
        await page.wait_for_load_state("networkidle")

    new_page = await get_current_page_number(page)
    if not new_page:
        link_text = (await link.inner_text()).strip()
        if link_text == "...":
            new_page = current_page + 10
        else:
            new_page = current_page + 1

    if new_page <= current_page:
        return None
    return new_page


def _parse_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()

    # Most filed-date cells include a date even when additional text exists
    # (e.g., "02/13/2026 1:35 PM Filed electronically").
    match = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", value)
    if not match:
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
    candidate = match.group(0) if match else value

    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%y %I:%M %p", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


async def _extract_committee_name(page: Page) -> Optional[str]:
    candidates = [
        'h1',
        'h2',
        'span[id*="lblCommitteeName"]',
        'span[id*="CommitteeName"]',
        'td:has-text("Committee Name") + td',
    ]
    for selector in candidates:
        element = await page.query_selector(selector)
        if not element:
            continue
        text = (await element.inner_text()).strip()
        if text:
            return text
    return None


def _classify_itemized_type(label: str, href: str | None) -> str:
    text = (label or "").lower()
    href_text = (href or "").lower()

    if "contrib" in text or "contrib" in href_text or "contribution" in href_text:
        return "contribution"
    if "expend" in text or "expend" in href_text:
        return "expenditure"
    return "other"


def _entry_type_from_headers(headers: List[str]) -> Optional[str]:
    joined = " ".join(headers)
    if "contributed by" in joined or "vendor name" in joined:
        return "contribution"
    if "received by" in joined or "expended by" in joined or "purpose/beneficiary" in joined:
        return "expenditure"
    return None


def _map_itemized_columns(headers: List[str]) -> dict:
    patterns = {
        "contributed_by": ["contributed by"],
        "received_by": ["received by"],
        "address": ["address"],
        "amount": ["amount", "$"],
        "description": ["description"],
        "vendor_name": ["vendor name"],
        "vendor_address": ["vendor address"],
        "expended_by": ["expended by"],
        "purpose_beneficiary": ["purpose/beneficiary", "purpose"],
        "candidate_name": ["candidate name"],
        "office_district": ["office - district", "office"],
        "supporting_opposing": ["supporting/opposing", "supporting"],
    }

    col_map = {}
    for idx, header in enumerate(headers):
        for field, options in patterns.items():
            if field in col_map:
                continue
            if any(option in header for option in options):
                col_map[field] = idx
                break
    return col_map


async def _extract_headers(rows) -> tuple[List[str], int]:
    for idx, row in enumerate(rows):
        header_cells = await row.query_selector_all("th")
        if not header_cells:
            continue

        headers = [(await cell.inner_text()).strip().lower() for cell in header_cells]
        if any(headers):
            return headers, idx

    return [], 0


def _parse_amount(value: str) -> Optional[float]:
    if not value:
        return None

    negative = bool(re.search(r"\(\s*\$?[\d,]+\.?\d*\s*\)", value))
    match = re.search(r"\$?\(?([\d,]+\.?\d*)\)?", value)
    if not match:
        return None

    try:
        number = float(match.group(1).replace(",", ""))
        return -number if negative else number
    except ValueError:
        return None


def _value(values: List[str], col_map: dict, key: str) -> Optional[str]:
    idx = col_map.get(key)
    if idx is None:
        return None
    if idx >= len(values):
        return None
    val = values[idx].strip()
    return val if val else None
