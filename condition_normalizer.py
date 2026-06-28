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
}

PRE_OWNED_ALIASES: dict[str, str] = {
    "mint": PRE_OWNED_CONDITION,
    "worn": PRE_OWNED_CONDITION,
    "pre owned": PRE_OWNED_CONDITION,
    "pre-owned": PRE_OWNED_CONDITION,
    "preowned": PRE_OWNED_CONDITION,
    "used": PRE_OWNED_CONDITION,
    "lnib": PRE_OWNED_CONDITION,
}

ACCESSORY_CONDITIONS = frozenset(
    {
        "full set",
        "watch only",
        "box only",
        "papers",
        "papers only",
        "complete",
        "stickered",
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
