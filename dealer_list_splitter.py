"""Pre-parser for multi-line dealer inventory list messages."""

from __future__ import annotations

import re
from typing import Any

Record = dict[str, Any]

DECORATION_PATTERN = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\uFE0F]+|[\u2600-\u27BF]|✅|✔️|❤️|♥️",
)
FULLSET_PATTERN = re.compile(r"\bfull\s*set\b", re.I)
WATCH_ROW_SEPARATOR = re.compile(
    r"[\u231A\u23F1\u23F2]|\u231A\uFE0F|\U0001F48E|\U0001F48F|\U0001F4B0|\U0001F4B5"
)
BULLET_ROW_PREFIX = re.compile(r"^[-*•▪▫]\s+")


def clean_dealer_list_line(line: str) -> str:
    """Remove decorative emoji/checkmarks and normalize whitespace."""
    from watch_parser import _normalize_parser_text

    cleaned = DECORATION_PATTERN.sub(" ", line)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _normalize_parser_text(cleaned)


def expand_dealer_list_raw_lines(message: str) -> list[str]:
    """Split a dealer list on newlines, watch emoji, and bullet row markers."""
    lines: list[str] = []
    for chunk in WATCH_ROW_SEPARATOR.split(message):
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = BULLET_ROW_PREFIX.sub("", line)
            if line:
                lines.append(line)
    return lines


def resolve_brand_header_alias(
    header_text: str,
    *,
    rules: list[Record] | None = None,
) -> str | None:
    """Apply learned brand-header aliases such as JLC -> Jaeger-LeCoultre."""
    from parser_learning import find_matching_learning_rule

    cleaned = clean_dealer_list_line(header_text)
    if not cleaned:
        return None

    active_rules = rules
    if active_rules is None:
        from database import list_active_parser_learning_rules

        active_rules = list_active_parser_learning_rules()

    for field_type in ("brand_header", "brand"):
        rule = find_matching_learning_rule(
            active_rules,
            field_type=field_type,
            term=cleaned,
        )
        if rule:
            return str(rule.get("normalized_value") or "").strip() or None
    return None


def is_buy_side_list_message(message: str) -> bool:
    """Return True when the message is primarily a buyer request list."""
    from watch_parser import OFFER_PATTERN, REQUEST_PATTERN, _normalize_parser_text

    normalized = _normalize_parser_text(message)
    if OFFER_PATTERN.search(normalized):
        return False
    return bool(REQUEST_PATTERN.search(normalized))


def is_dealer_list_offer_line(line: str) -> bool:
    """Return True when a line looks like a standalone dealer offer row."""
    from watch_parser import _extract_price, _extract_reference

    cleaned = clean_dealer_list_line(line)
    if not cleaned:
        return False
    reference, _, _ = _extract_reference(cleaned)
    if not reference:
        return False
    price, _ = _extract_price(cleaned)
    return price is not None


def detect_brand_header_line(line: str, *, header_rules: list[Record] | None = None) -> str | None:
    """Return a brand name when the line is a header without offer details."""
    from watch_parser import _extract_brand, _extract_price, _extract_reference

    cleaned = clean_dealer_list_line(line)
    if not cleaned:
        return None

    alias = resolve_brand_header_alias(cleaned, rules=header_rules)
    if alias:
        return alias

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


def split_multi_brand_dealer_list_message(
    message: str,
    *,
    header_rules: list[Record] | None = None,
) -> list[tuple[str | None, str]] | None:
    """Split a dealer inventory list into (active brand, offer line) rows.

    Supports multiple brand headers such as JLC / PANERAI / CARTIER blocks.
    Returns None when the message is not a structured dealer list.
    """
    if is_buy_side_list_message(message):
        return None

    raw_lines = expand_dealer_list_raw_lines(message)
    if len(raw_lines) < 2:
        return None

    active_brand: str | None = None
    offer_rows: list[tuple[str | None, str]] = []

    for raw_line in raw_lines:
        cleaned = clean_dealer_list_line(raw_line)
        if not cleaned:
            continue

        header_brand = detect_brand_header_line(cleaned, header_rules=header_rules)
        if header_brand:
            active_brand = header_brand
            continue

        if is_dealer_list_offer_line(cleaned):
            offer_rows.append((active_brand, raw_line))

    if len(offer_rows) < 2:
        return None
    return offer_rows


def split_dealer_list_message(message: str) -> tuple[str | None, list[str]] | None:
    """Split a single-brand dealer inventory list into header brand and offer lines."""
    offer_rows = split_multi_brand_dealer_list_message(message)
    if offer_rows is None:
        return None

    brands = {brand for brand, _ in offer_rows if brand}
    if len(brands) != 1:
        return None

    brand = next(iter(brands)) if brands else None
    return brand, [line for _, line in offer_rows]
