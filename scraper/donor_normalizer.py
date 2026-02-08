"""Donor name and address normalization for matching."""
import re
from typing import Tuple


class DonorNormalizer:
    """Normalizes donor names and addresses for matching."""

    # Common address abbreviations
    ADDRESS_ABBREVIATIONS = {
        'street': 'st',
        'avenue': 'ave',
        'boulevard': 'blvd',
        'drive': 'dr',
        'lane': 'ln',
        'road': 'rd',
        'court': 'ct',
        'circle': 'cir',
        'place': 'pl',
        'terrace': 'ter',
        'highway': 'hwy',
        'parkway': 'pkwy',
        'expressway': 'expy',
        'north': 'n',
        'south': 's',
        'east': 'e',
        'west': 'w',
        'northeast': 'ne',
        'northwest': 'nw',
        'southeast': 'se',
        'southwest': 'sw',
        'apartment': 'apt',
        'suite': 'ste',
        'building': 'bldg',
        'floor': 'fl',
        'room': 'rm',
        'unit': 'unit',
        'number': '#',
        'post office box': 'po box',
        'p.o. box': 'po box',
        'po box': 'po box',
    }

    # Patterns for name normalization
    NAME_SUFFIXES = ['jr', 'sr', 'ii', 'iii', 'iv', 'v', 'md', 'phd', 'esq', 'dds', 'cpa']

    @classmethod
    def normalize_name(cls, name: str) -> str:
        """Normalize a donor name for matching.

        - Converts to lowercase
        - Trims whitespace
        - Removes punctuation
        - Converts "Last, First" to "First Last"

        Args:
            name: The raw donor name

        Returns:
            Normalized name string
        """
        if not name:
            return ''

        # Lowercase and trim
        normalized = name.lower().strip()

        # Remove extra whitespace
        normalized = ' '.join(normalized.split())

        # Check for "Last, First" format and convert
        if ',' in normalized:
            parts = [p.strip() for p in normalized.split(',', 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                # Check if second part looks like a first name (not a suffix)
                second_part = parts[1].split()[0] if parts[1].split() else ''
                if second_part and second_part not in cls.NAME_SUFFIXES:
                    # Swap to "First Last" format
                    normalized = f"{parts[1]} {parts[0]}"

        # Remove punctuation except apostrophes in names (like O'Brien)
        normalized = re.sub(r"[^\w\s']", '', normalized)

        # Remove extra whitespace again
        normalized = ' '.join(normalized.split())

        return normalized

    @classmethod
    def normalize_address(cls, address: str) -> str:
        """Normalize an address for matching.

        - Converts to lowercase
        - Trims whitespace
        - Standardizes abbreviations

        Args:
            address: The raw address

        Returns:
            Normalized address string
        """
        if not address:
            return ''

        # Lowercase and trim
        normalized = address.lower().strip()

        # Remove extra whitespace
        normalized = ' '.join(normalized.split())

        # Remove punctuation except # (for unit numbers)
        normalized = re.sub(r'[^\w\s#]', ' ', normalized)
        normalized = ' '.join(normalized.split())

        # Apply abbreviation substitutions
        words = normalized.split()
        result_words = []

        i = 0
        while i < len(words):
            word = words[i]

            # Check for multi-word abbreviations
            matched = False
            for full, abbrev in cls.ADDRESS_ABBREVIATIONS.items():
                full_words = full.split()
                if len(full_words) > 1:
                    # Check if this starts a multi-word match
                    phrase = ' '.join(words[i:i + len(full_words)])
                    if phrase == full:
                        result_words.append(abbrev)
                        i += len(full_words)
                        matched = True
                        break

            if not matched:
                # Check single-word abbreviations
                if word in cls.ADDRESS_ABBREVIATIONS:
                    result_words.append(cls.ADDRESS_ABBREVIATIONS[word])
                else:
                    result_words.append(word)
                i += 1

        return ' '.join(result_words)

    @classmethod
    def normalize(cls, name: str, address: str) -> Tuple[str, str]:
        """Normalize both name and address.

        Args:
            name: The raw donor name
            address: The raw address

        Returns:
            Tuple of (normalized_name, normalized_address)
        """
        return cls.normalize_name(name), cls.normalize_address(address)

    @classmethod
    def are_same_donor(cls, name1: str, address1: str, name2: str, address2: str) -> bool:
        """Check if two donors are the same based on normalized name and address.

        Two donors are considered the same if both their normalized names AND
        normalized addresses match exactly.

        Args:
            name1: First donor's name
            address1: First donor's address
            name2: Second donor's name
            address2: Second donor's address

        Returns:
            True if donors match, False otherwise
        """
        norm_name1, norm_addr1 = cls.normalize(name1, address1)
        norm_name2, norm_addr2 = cls.normalize(name2, address2)

        return norm_name1 == norm_name2 and norm_addr1 == norm_addr2
