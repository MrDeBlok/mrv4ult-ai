"""Normalize dealer watch wear conditions to canonical values."""

from __future__ import annotations

import re
from typing import Any

Record = dict[str, Any]

NEW_CONDITION = "New"
PRE_OWNED_CONDITION = "Pre-Owned"

NEW_ALIASES: dict[str, str] = {
    "brand new": NEW_CONDITION,
    "bn": NEW_CONDITION,
    "new": NEW_CONDITION,
    "unworn": NEW_CONDITION,
    "bnib": NEW_CONDITION,
    "nos": NEW_CONDITION,
    "unworn complete": NEW_CONDITION,
    "sticker": NEW_CONDITION,
    "stickers": NEW_CONDITION,
    "full stickers": NEW_CONDITION,
    "stickered": NEW_CONDITION,
}

PRE_OWNED_ALIASES: dict[str, str] = {
    "mint": PRE_OWNED_CONDITION,
    "worn": PRE_OWNED_CONDITION,
    "pre owned": PRE_OWNED_CONDITION,
    "pre-owned": PRE_OWNED_CONDITION,
    "preowned": PRE_OWNED_CONDITION,
    "used": PRE_OWNED_CONDITION,
    "lnib": PRE_OWNED_CONDITION,
    "second hand": PRE_OWNED_CONDITION,
}

ACCESSORY_CONDITIONS = frozenset(
    {
        "full set",
        "watch only",
        "box only",
        "papers",
        "papers only",
        "complete",
    }
)


def _condition_key(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", value.strip().lower())


def normalize_wear_condition(value: str | None) -> tuple[str | None, str | None]:
    """Return normalized wear condition and optional raw parsed text."""
    if value is None:
        return None, None

    raw = str(value).strip()
    if not raw:
        return None, None

    key = _condition_key(raw)
    normalized = NEW_ALIASES.get(key) or PRE_OWNED_ALIASES.get(key)
    if normalized:
        raw_condition = None if raw == normalized else raw
        return normalized, raw_condition

    if key in ACCESSORY_CONDITIONS:
        return None, raw

    return None, raw


def normalize_watch_condition(watch: Record) -> Record:
    """Apply wear-condition normalization to a parsed watch dict in place."""
    normalized, raw_condition = normalize_wear_condition(watch.get("condition"))
    watch["condition"] = normalized
    if raw_condition:
        watch["raw_condition"] = raw_condition
    return watch


def display_condition(value: str | None) -> str:
    """Format a stored or parsed condition for UI display."""
    normalized, _ = normalize_wear_condition(value)
    return normalized or "N/A"


def normalize_condition_value(value: str | None) -> str | None:
    """Normalize a condition value for database storage."""
    normalized, _ = normalize_wear_condition(value)
    return normalized


REQUEST_CONDITION_ANY_LABEL = "Any / Unknown"

REQUEST_CONDITION_FORM_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", REQUEST_CONDITION_ANY_LABEL),
    (NEW_CONDITION, NEW_CONDITION),
    (PRE_OWNED_CONDITION, PRE_OWNED_CONDITION),
)


def request_condition_form_value(value: str | None) -> str:
    """Map stored request condition to a form select value."""
    normalized = normalize_condition_value(value)
    if normalized in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return normalized
    return ""


def parse_request_condition_form(value: str | None) -> str | None:
    """Parse a client request condition form value for storage."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered in {"any", "unknown", "any / unknown"}:
        return None
    return normalize_condition_value(cleaned)


def request_condition_display(value: str | None) -> str:
    """Format stored request condition for cards and detail views."""
    normalized = normalize_condition_value(value)
    if normalized in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return normalized
    return REQUEST_CONDITION_ANY_LABEL


def parse_condition_filter(value: str | None) -> str | None:
    """Parse a search condition filter into a normalized wear condition."""
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned or cleaned.lower() == "all":
        return None

    normalized, _ = normalize_wear_condition(cleaned)
    if normalized in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return normalized

    lowered = cleaned.lower()
    if lowered == "new":
        return NEW_CONDITION
    if lowered in {"pre-owned", "preowned", "pre owned"}:
        return PRE_OWNED_CONDITION

    raise ValueError("Invalid condition filter. Use All, New, or Pre-Owned.")


def offer_matches_condition_filter(
    stored_condition: str | None,
    condition_filter: str | None,
) -> bool:
    """Return whether an offer matches the normalized condition filter."""
    if condition_filter is None:
        return True
    normalized, _ = normalize_wear_condition(stored_condition)
    return normalized == condition_filter


def offer_condition_display(stored_condition: str | None) -> tuple[str, str | None]:
    """Return normalized condition label and optional raw condition text."""
    normalized, raw_condition = normalize_wear_condition(stored_condition)
    return (normalized or "N/A", raw_condition)


def normalized_wear_condition_for_comparison(value: str | None) -> str | None:
    """Return a canonical wear condition suitable for market comparison."""
    normalized, _ = normalize_wear_condition(value)
    if normalized in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return normalized
    return None


def resolve_offer_wear_condition(*values: str | None) -> str | None:
    """Return the first canonical wear condition found across stored values."""
    for value in values:
        normalized = normalized_wear_condition_for_comparison(value)
        if normalized is not None:
            return normalized
    return None


def deal_condition_label(value: str | None) -> str:
    """Return New, Pre-Owned, or Unknown for deal analysis display."""
    normalized = normalized_wear_condition_for_comparison(value)
    if normalized == NEW_CONDITION:
        return NEW_CONDITION
    if normalized == PRE_OWNED_CONDITION:
        return PRE_OWNED_CONDITION
    return "Unknown"


def import_row_has_safe_price_comparison(row: Record) -> bool:
    """Return whether import price intelligence used a same-condition market."""
    offer_condition = resolve_offer_wear_condition(row.get("condition"), row.get("raw_condition"))
    market_condition = normalize_condition_value(row.get("market_condition"))
    if offer_condition is None:
        return False
    if market_condition not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return False
    if offer_condition != market_condition:
        return False
    if row.get("price_label") == "No comparables":
        return False
    previous_lowest = row.get("previous_lowest_usd")
    if previous_lowest in {None, "", "N/A"}:
        return False
    return True
