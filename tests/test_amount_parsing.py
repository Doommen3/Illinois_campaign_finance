"""Tests for amount parsing in the detail scraper."""
import pytest
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_amount(amount_str: str) -> float:
    """Parse an amount string, handling various formats.

    This mirrors the logic in detail_scraper.py
    """
    if not amount_str:
        return None

    # Check for negative amount format: ($1,234.56) or $(1,234.56)
    # The parentheses must be around the number, not elsewhere
    is_negative = bool(re.search(r'\(\s*\$?[\d,]+\.?\d*\s*\)', amount_str))

    # Extract just the currency amount using regex
    # Match patterns like $1,234.56 or (1,234.56) or 1234.56
    amount_match = re.search(r'\$?\(?([\d,]+\.?\d*)\)?', amount_str)
    if amount_match:
        amount_clean = amount_match.group(1).replace(',', '')
        try:
            amount = float(amount_clean)
            if is_negative:
                amount = -amount
            return amount
        except ValueError:
            pass
    return None


class TestAmountParsing:
    """Tests for amount parsing."""

    def test_simple_amount(self):
        """Test simple dollar amount."""
        assert parse_amount("$1,234.56") == 1234.56

    def test_amount_with_date(self):
        """Test amount followed by date on same line."""
        assert parse_amount("$32,500.00 2/6/2026") == 32500.00

    def test_amount_with_newline_date(self):
        """Test amount followed by date on new line."""
        assert parse_amount("$32,500.00\n2/6/2026") == 32500.00

    def test_amount_without_dollar_sign(self):
        """Test amount without dollar sign."""
        assert parse_amount("1,234.56") == 1234.56

    def test_amount_no_cents(self):
        """Test whole dollar amount."""
        assert parse_amount("$5,000") == 5000.0

    def test_negative_amount_parentheses(self):
        """Test negative amount in parentheses."""
        assert parse_amount("($1,234.56)") == -1234.56

    def test_amount_with_text(self):
        """Test amount with trailing text."""
        assert parse_amount("$500.00 donation") == 500.00

    def test_large_amount(self):
        """Test large amount with commas."""
        assert parse_amount("$1,234,567.89") == 1234567.89

    def test_empty_string(self):
        """Test empty string returns None."""
        assert parse_amount("") is None

    def test_no_amount(self):
        """Test string with no amount returns None."""
        assert parse_amount("pending") is None

    def test_amount_with_dated_suffix(self):
        """Test amount with 'dated' suffix."""
        assert parse_amount("$32,500.00 (dated 2/6/2026)") == 32500.00


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
