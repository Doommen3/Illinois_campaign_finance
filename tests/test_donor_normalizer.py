"""Tests for donor normalizer."""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.donor_normalizer import DonorNormalizer


class TestDonorNormalizer:
    """Tests for DonorNormalizer class."""

    def test_normalize_name_lowercase(self):
        """Test that names are lowercased."""
        assert DonorNormalizer.normalize_name("JOHN SMITH") == "john smith"

    def test_normalize_name_trim_whitespace(self):
        """Test that whitespace is trimmed."""
        assert DonorNormalizer.normalize_name("  John Smith  ") == "john smith"

    def test_normalize_name_extra_whitespace(self):
        """Test that extra whitespace is collapsed."""
        assert DonorNormalizer.normalize_name("John    Smith") == "john smith"

    def test_normalize_name_last_first_format(self):
        """Test conversion of 'Last, First' to 'First Last'."""
        assert DonorNormalizer.normalize_name("Smith, John") == "john smith"

    def test_normalize_name_with_suffix(self):
        """Test that names with suffixes are handled correctly."""
        # "Smith, Jr" should NOT swap because Jr is a suffix
        result = DonorNormalizer.normalize_name("Smith, Jr")
        # Jr is recognized as suffix, so it shouldn't swap
        assert "jr" in result

    def test_normalize_name_removes_punctuation(self):
        """Test that punctuation is removed."""
        assert DonorNormalizer.normalize_name("John. Smith!") == "john smith"

    def test_normalize_name_preserves_apostrophe(self):
        """Test that apostrophes in names are preserved."""
        assert DonorNormalizer.normalize_name("O'Brien") == "o'brien"

    def test_normalize_name_empty(self):
        """Test that empty string returns empty."""
        assert DonorNormalizer.normalize_name("") == ""
        assert DonorNormalizer.normalize_name(None) == ""

    def test_normalize_address_lowercase(self):
        """Test that addresses are lowercased."""
        assert "main" in DonorNormalizer.normalize_address("123 MAIN STREET")

    def test_normalize_address_abbreviations(self):
        """Test that street abbreviations are standardized."""
        result = DonorNormalizer.normalize_address("123 Main Street")
        assert "st" in result
        assert "street" not in result

    def test_normalize_address_avenue(self):
        """Test Avenue abbreviation."""
        result = DonorNormalizer.normalize_address("456 Oak Avenue")
        assert "ave" in result

    def test_normalize_address_boulevard(self):
        """Test Boulevard abbreviation."""
        result = DonorNormalizer.normalize_address("789 Sunset Boulevard")
        assert "blvd" in result

    def test_normalize_address_directions(self):
        """Test direction abbreviations."""
        result = DonorNormalizer.normalize_address("100 North Main Street")
        assert result == "100 n main st"

    def test_normalize_address_empty(self):
        """Test that empty string returns empty."""
        assert DonorNormalizer.normalize_address("") == ""
        assert DonorNormalizer.normalize_address(None) == ""

    def test_are_same_donor_exact_match(self):
        """Test that identical donors match."""
        assert DonorNormalizer.are_same_donor(
            "John Smith", "123 Main St",
            "John Smith", "123 Main St"
        )

    def test_are_same_donor_case_insensitive(self):
        """Test that matching is case insensitive."""
        assert DonorNormalizer.are_same_donor(
            "JOHN SMITH", "123 MAIN STREET",
            "john smith", "123 main street"
        )

    def test_are_same_donor_different_address(self):
        """Test that same name with different address doesn't match."""
        assert not DonorNormalizer.are_same_donor(
            "John Smith", "123 Main St",
            "John Smith", "456 Oak Ave"
        )

    def test_are_same_donor_different_name(self):
        """Test that different names with same address doesn't match."""
        assert not DonorNormalizer.are_same_donor(
            "John Smith", "123 Main St",
            "Jane Smith", "123 Main St"
        )

    def test_normalize_returns_tuple(self):
        """Test that normalize returns both values."""
        name, address = DonorNormalizer.normalize("John Smith", "123 Main Street")
        assert name == "john smith"
        assert "main" in address and "st" in address


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
