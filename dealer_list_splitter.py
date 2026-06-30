"""Pre-parser for multi-line dealer inventory list messages."""

from __future__ import annotations

import re

DECORATION_PATTERN = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\uFE0F]+|[\u2600-\u27BF]|✅|✔️",
)
FULLSET_PATTERN = re.compile(r"\bfull\s*set\b", re.I)


def clean_dealer_list_line(line: str) -> str:
    """Remove decorative emoji/checkmarks and normalize whitespace."""
    cleaned = DECORATION_PATTERN.sub(" ", line)
    return re.sub(r"\s+", " ", cleaned).strip()


def is_buy_side_list_message(message: str) -> bool:
    """Return True when the message is primarily a buyer request list."""
    from watch_parser import OFFER_PATTERN, REQUEST_PATTERN

    if OFFER_PATTERN.search(message):
        return False
    return bool(REQUEST_PATTERN.search(message))


def is_dealer_list_offer_line(line: str) -> bool:
    """Return True when a line looks like a standalone dealer offer row."""
    from watch_parser import (
        STANDALONE_YEAR_PATTERN,
        _detect_wear_condition,
        _extract_price,
        _extract_reference,
    )

    cleaned = clean_dealer_list_line(line)
    if not cleaned:
        return False
    if not _extract_reference(cleaned)[0]:
        return False
    if _extract_price(cleaned)[0] is None:
        return False

    has_year = bool(STANDALONE_YEAR_PATTERN.search(cleaned))
    has_condition = _detect_wear_condition(cleaned) is not None
    has_fullset = bool(FULLSET_PATTERN.search(cleaned))
    return has_year or has_condition or has_fullset


def detect_brand_header_line(line: str) -> str | None:
    """Return a brand name when the line is a header without offer details."""
    from watch_parser import _extract_brand, _extract_price, _extract_reference

    cleaned = clean_dealer_list_line(line)
    if not cleaned:
        return None

    brand = _extract_brand(cleaned)
    if brand is None:
        return None
    if _extract_reference(cleaned)[0]:
        return None
    if _extract_price(cleaned)[0] is not None:
        return None
    if is_dealer_list_offer_line(cleaned):
        return None
    return brand


def split_dealer_list_message(message: str) -> tuple[str | None, list[str]] | None:
    """Split a dealer inventory list into a brand header and offer lines.

    Returns None when the message is not a dealer list.
    """
    if is_buy_side_list_message(message):
        return None

    raw_lines = [line.strip() for line in message.splitlines() if line.strip()]
    if len(raw_lines) < 2:
        return None

    cleaned_lines = [clean_dealer_list_line(line) for line in raw_lines]
    offer_lines = [
        raw_lines[index]
        for index, cleaned in enumerate(cleaned_lines)
        if is_dealer_list_offer_line(cleaned)
    ]
    if len(offer_lines) < 2:
        return None

    brand_headers = [
        header_brand
        for cleaned in cleaned_lines
        if (header_brand := detect_brand_header_line(cleaned))
    ]
    if len(brand_headers) != 1:
        return None

    brand: str | None = brand_headers[0]
    return brand, offer_lines
