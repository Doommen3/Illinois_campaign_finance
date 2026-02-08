"""Rate limiter for web scraping."""
import time
import random
import os


class RateLimiter:
    """Rate limiter with configurable delays and exponential backoff."""

    def __init__(self,
                 requests_per_minute: int = None,
                 min_delay: float = None,
                 max_delay: float = None,
                 backoff_multiplier: float = None,
                 max_backoff: float = None):
        """Initialize the rate limiter.

        Args:
            requests_per_minute: Maximum requests per minute (default: 30)
            min_delay: Minimum delay between requests in seconds (default: 1.0)
            max_delay: Maximum delay between requests in seconds (default: 3.0)
            backoff_multiplier: Multiplier for exponential backoff (default: 2.0)
            max_backoff: Maximum backoff delay in seconds (default: 60.0)
        """
        self.requests_per_minute = requests_per_minute or int(
            os.environ.get('RATE_LIMIT_RPM', 30)
        )
        self.min_delay = min_delay or float(
            os.environ.get('RATE_LIMIT_MIN_DELAY', 1.0)
        )
        self.max_delay = max_delay or float(
            os.environ.get('RATE_LIMIT_MAX_DELAY', 3.0)
        )
        self.backoff_multiplier = backoff_multiplier or float(
            os.environ.get('RATE_LIMIT_BACKOFF_MULTIPLIER', 2.0)
        )
        self.max_backoff = max_backoff or float(
            os.environ.get('RATE_LIMIT_MAX_BACKOFF', 60.0)
        )

        self._last_request_time = 0
        self._request_times = []
        self._current_backoff = 0
        self._consecutive_errors = 0

    def wait(self) -> None:
        """Wait before making the next request.

        Applies random delay within configured range, plus any backoff from errors.
        Also ensures we don't exceed requests_per_minute.
        """
        now = time.time()

        # Clean up old request times (older than 1 minute)
        self._request_times = [t for t in self._request_times if now - t < 60]

        # Check if we're at the rate limit
        if len(self._request_times) >= self.requests_per_minute:
            # Wait until the oldest request is more than 1 minute old
            wait_time = 60 - (now - self._request_times[0])
            if wait_time > 0:
                time.sleep(wait_time)

        # Apply random delay
        delay = random.uniform(self.min_delay, self.max_delay)

        # Add backoff if there were errors
        delay += self._current_backoff

        # Ensure minimum time between requests
        elapsed = now - self._last_request_time
        if elapsed < delay:
            time.sleep(delay - elapsed)

        # Record this request
        self._last_request_time = time.time()
        self._request_times.append(self._last_request_time)

    def record_success(self) -> None:
        """Record a successful request, resetting backoff."""
        self._consecutive_errors = 0
        self._current_backoff = 0

    def record_error(self) -> None:
        """Record an error, increasing backoff."""
        self._consecutive_errors += 1
        if self._current_backoff == 0:
            self._current_backoff = self.min_delay
        else:
            self._current_backoff = min(
                self._current_backoff * self.backoff_multiplier,
                self.max_backoff
            )

    @property
    def current_backoff(self) -> float:
        """Get the current backoff delay."""
        return self._current_backoff

    @property
    def consecutive_errors(self) -> int:
        """Get the number of consecutive errors."""
        return self._consecutive_errors

    def reset(self) -> None:
        """Reset the rate limiter state."""
        self._last_request_time = 0
        self._request_times = []
        self._current_backoff = 0
        self._consecutive_errors = 0
