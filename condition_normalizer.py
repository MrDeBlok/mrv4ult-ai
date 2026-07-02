"""Normalize dealer watch wear conditions to canonical values."""

from __future__ import annotations

import re
from typing import Any

Record = dict[str, Any]

NEW_CONDITION = "New"
PRE_OWNED_CONDITION = "Pre-Owned"

CONDITION_SOURCE_EXPLICIT = "explicit"
CONDITION_SOURCE_INFERRED_DEFAULT = "inferred_default"
CONDITION_CONFIDENCE_HIGH = "high"
CONDITION_CONFIDENCE_MEDIUM = "medium"
CONDITION_INFERENCE_NOTE = (
    "Condition inferred as Pre-Owned because dealer did not specify New/Unworn."
)

CONDITION_METADATA_FIELDS = (
    "condition",
    "raw_condition",
    "condition_source",
    "condition_confidence",
    "condition_explicit",
)

MESSAGE_ALL_BATCH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\ball\s+new\b", re.I), "All new"),
    (re.compile(r"\ball\s+unworn\b", re.I), "All unworn"),
    (re.compile(r"\ball\s+(?:pre[-\s]?owned|preowned)\b", re.I), "All pre-owned"),
    (re.compile(r"\ball\s+used\b", re.I), "All used"),
    (re.compile(r"\ball\s+worn\b", re.I), "All worn"),
    (re.compile(r"\ball\s+second\s+hand\b", re.I), "All second hand"),
]

MESSAGE_NEW_YEAR_BATCH_PATTERN = re.compile(
    r"\bnew\s+(?:\d{4}\s*/\s*)?\d{4}(?:\s*/\s*\d{4})?\b",
    re.I,
)

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
    "serviced": PRE_OWNED_CONDITION,
    "polished": PRE_OWNED_CONDITION,
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


def watch_has_explicit_wear_condition(watch: Record) -> bool:
    """Return True when a parsed watch already has a dealer-confirmed wear condition."""
    if watch.get("condition_explicit") is True:
        return True
    if watch.get("condition_source") == CONDITION_SOURCE_EXPLICIT:
        return True
    if watch.get("condition_source") == CONDITION_SOURCE_INFERRED_DEFAULT:
        return False
    return resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition")) is not None


def watch_has_offer_price(watch: Record) -> bool:
    """Return True when a parsed watch includes an offer price."""
    return bool(
        watch.get("original_price") is not None
        or watch.get("price") is not None
        or watch.get("usd_price") is not None
    )


def mark_explicit_condition_metadata(watch: Record) -> Record:
    """Mark parsed condition metadata when wear condition came from explicit dealer text."""
    if watch.get("condition_source") == CONDITION_SOURCE_INFERRED_DEFAULT:
        return watch
    if resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition")):
        watch["condition_source"] = CONDITION_SOURCE_EXPLICIT
        watch["condition_confidence"] = CONDITION_CONFIDENCE_HIGH
        watch["condition_explicit"] = True
    return watch


def apply_inferred_pre_owned_default(watch: Record) -> Record:
    """Infer Pre-Owned for active priced offers missing explicit wear condition."""
    updated = dict(watch)
    if updated.get("condition_explicit") or updated.get("condition_source") == CONDITION_SOURCE_EXPLICIT:
        return updated
    if resolve_offer_wear_condition(updated.get("condition"), updated.get("raw_condition")):
        return mark_explicit_condition_metadata(updated)
    if not watch_has_offer_price(updated):
        return updated
    updated["condition"] = PRE_OWNED_CONDITION
    updated["condition_source"] = CONDITION_SOURCE_INFERRED_DEFAULT
    updated["condition_confidence"] = CONDITION_CONFIDENCE_MEDIUM
    updated["condition_explicit"] = False
    return updated


def apply_inferred_pre_owned_defaults(watches: list[Record]) -> list[Record]:
    """Apply default Pre-Owned inference to active offer watches."""
    return [apply_inferred_pre_owned_default(watch) for watch in watches]


def resolve_effective_watch_condition(row: Record, watch: Record) -> Record:
    """Resolve wear condition for display and market comparison, including safe inference."""
    merged = dict(watch)
    for key in CONDITION_METADATA_FIELDS:
        row_value = row.get(key)
        if row_value is not None:
            merged[key] = row_value

    if resolve_offer_wear_condition(merged.get("condition"), merged.get("raw_condition")):
        if merged.get("condition_source") is None:
            mark_explicit_condition_metadata(merged)
        return merged

    if watch_has_offer_price(merged):
        return apply_inferred_pre_owned_default(merged)
    return merged


def condition_display_metadata(row: Record, watch: Record | None = None) -> dict[str, Any]:
    """Return UI metadata for condition labels and inference notes."""
    effective = resolve_effective_watch_condition(row, watch or {})
    label = deal_condition_label(effective.get("condition"))
    is_inferred = effective.get("condition_source") == CONDITION_SOURCE_INFERRED_DEFAULT
    is_known = label != "Unknown"
    display_label = f"{label} (inferred)" if is_inferred and is_known else label
    return {
        "label": label,
        "display_label": display_label,
        "icon": "🟢" if label == NEW_CONDITION else "🟡" if label == PRE_OWNED_CONDITION else "⚪",
        "is_known": is_known,
        "is_inferred": is_inferred,
        "is_explicit": effective.get("condition_explicit") is True
        or effective.get("condition_source") == CONDITION_SOURCE_EXPLICIT,
        "condition_source": effective.get("condition_source"),
        "condition_confidence": effective.get("condition_confidence"),
        "inference_note": CONDITION_INFERENCE_NOTE if is_inferred else None,
        "effective_watch": effective,
    }


def _normalize_batch_condition_text(raw: str) -> tuple[str | None, str | None]:
    normalized, raw_condition = normalize_wear_condition(raw)
    if normalized:
        return normalized, raw_condition or raw

    without_all = re.sub(r"^all\s+", "", raw.strip(), flags=re.I)
    if without_all != raw.strip():
        normalized, raw_condition = normalize_wear_condition(without_all)
        if normalized:
            return normalized, raw or raw_condition

    return None, None


def _clean_batch_condition_line(line: str) -> str:
    try:
        from dealer_list_splitter import clean_dealer_list_line
    except ImportError:  # pragma: no cover
        return line.strip()
    return clean_dealer_list_line(line)


def _line_is_watch_offer_line(line: str) -> bool:
    """Return True when a line looks like an individual watch offer row."""
    try:
        from dealer_list_splitter import clean_dealer_list_line, is_dealer_list_offer_line
        from watch_parser import _extract_price, _extract_reference
    except ImportError:  # pragma: no cover
        return False

    cleaned = clean_dealer_list_line(line)
    if not cleaned:
        return False
    if is_dealer_list_offer_line(cleaned):
        return True
    return bool(_extract_reference(cleaned)[0] and _extract_price(cleaned)[0] is not None)


def detect_message_batch_condition(message: str) -> tuple[str | None, str | None]:
    """Detect a shared wear condition declared once for a multi-watch message."""
    text = message.strip()
    if not text:
        return None, None

    for pattern, raw_label in MESSAGE_ALL_BATCH_PATTERNS:
        if pattern.search(text):
            normalized, raw = _normalize_batch_condition_text(raw_label)
            if normalized:
                return normalized, raw

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _line_is_watch_offer_line(line):
            continue
        cleaned = _clean_batch_condition_line(line)
        if not cleaned:
            continue

        year_match = MESSAGE_NEW_YEAR_BATCH_PATTERN.search(cleaned)
        if year_match:
            raw = year_match.group(0).strip()
            normalized, _ = normalize_wear_condition("New")
            if normalized:
                return normalized, raw

        normalized, raw = _normalize_batch_condition_text(cleaned)
        if normalized:
            return normalized, raw

    return None, None


def propagate_message_batch_condition(message: str, watches: list[Record]) -> list[Record]:
    """Apply a message-level wear condition to watches missing their own condition."""
    batch_condition, batch_raw = detect_message_batch_condition(message)
    if batch_condition is None or not watches:
        return watches

    updated: list[Record] = []
    for watch in watches:
        row = dict(watch)
        if watch_has_explicit_wear_condition(row):
            updated.append(row)
            continue
        row["condition"] = batch_condition
        if batch_raw and batch_raw != batch_condition:
            row["raw_condition"] = batch_raw
        updated.append(row)
    return updated


def sync_summary_row_conditions(rows: list[Record], watches: list[Record]) -> list[Record]:
    """Copy propagated watch conditions onto aligned summary rows when rows are still unknown."""
    synced: list[Record] = []
    for index, row in enumerate(rows):
        updated = dict(row)
        if index >= len(watches):
            synced.append(updated)
            continue
        if resolve_offer_wear_condition(updated.get("condition"), updated.get("raw_condition")):
            synced.append(updated)
            continue
        watch = watches[index]
        watch_condition = resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition"))
        if watch_condition is None:
            synced.append(updated)
            continue
        updated["condition"] = watch.get("condition")
        if watch.get("raw_condition"):
            updated["raw_condition"] = watch.get("raw_condition")
        for key in ("condition_source", "condition_confidence", "condition_explicit"):
            if watch.get(key) is not None:
                updated[key] = watch.get(key)
        synced.append(updated)
    return synced


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
