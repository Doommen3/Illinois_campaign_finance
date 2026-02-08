"""State manager for resumable scraping."""
from datetime import datetime
from typing import Optional
import sqlite3

from database.models import ScrapeState


class StateManager:
    """Manages scraping state for resumability."""

    def __init__(self, conn: sqlite3.Connection, scrape_type: str):
        """Initialize the state manager.

        Args:
            conn: Database connection
            scrape_type: Type of scrape ('main_list' or 'details')
        """
        self.conn = conn
        self.scrape_type = scrape_type
        self._state: Optional[ScrapeState] = None

    def start(self, total_pages: int = None) -> ScrapeState:
        """Start a new scrape session.

        Args:
            total_pages: Total number of pages to scrape (optional)

        Returns:
            The created ScrapeState
        """
        self._state = ScrapeState(
            scrape_type=self.scrape_type,
            total_pages=total_pages,
            status='in_progress',
            started_at=datetime.now().isoformat()
        )
        return self._state.save(self.conn)

    def resume(self) -> Optional[ScrapeState]:
        """Resume from the last scrape session.

        Returns:
            The last ScrapeState if it can be resumed, None otherwise
        """
        state = ScrapeState.get_latest(self.conn, self.scrape_type)

        if state and state.status in ('in_progress', 'error'):
            self._state = state
            self._state.status = 'in_progress'
            self._state.error_message = None
            self._state.save(self.conn)
            return self._state

        return None

    def get_current(self) -> Optional[ScrapeState]:
        """Get the current scrape state."""
        return self._state

    def get_latest(self) -> Optional[ScrapeState]:
        """Get the latest scrape state from the database."""
        return ScrapeState.get_latest(self.conn, self.scrape_type)

    def update_progress(self, page: int = None, report_id: int = None) -> None:
        """Update scrape progress.

        Args:
            page: Current page number
            report_id: Last processed report ID
        """
        if not self._state:
            return

        if page is not None:
            self._state.last_page = page
        if report_id is not None:
            self._state.last_report_id = report_id

        self._state.save(self.conn)

    def complete(self) -> None:
        """Mark the scrape as completed."""
        if not self._state:
            return

        self._state.status = 'completed'
        self._state.completed_at = datetime.now().isoformat()
        self._state.save(self.conn)

    def error(self, message: str) -> None:
        """Mark the scrape as errored.

        Args:
            message: Error message
        """
        if not self._state:
            return

        self._state.status = 'error'
        self._state.error_message = message
        self._state.save(self.conn)

    @property
    def last_page(self) -> Optional[int]:
        """Get the last processed page number."""
        return self._state.last_page if self._state else None

    @property
    def last_report_id(self) -> Optional[int]:
        """Get the last processed report ID."""
        return self._state.last_report_id if self._state else None
