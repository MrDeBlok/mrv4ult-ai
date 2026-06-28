"""Unknown brand detection and recording during imports."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

Record = dict[str, Any]

UNKNOWN_BRAND_STOP_WORDS = frozenset(
    word.lower()
    for word in (
        "new",
        "used",
        "full",
        "set",
        "watch",
        "only",
        "box",
        "papers",
        "bnib",
        "nos",
        "mint",
        "unworn",
        "usd",
        "hkd",
        "eur",
        "chf",
        "gbp",
    )
)


def normalize_unknown_brand_text(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def watch_has_parse_signal(watch: Record) -> bool:
    return bool(
        watch.get("reference")
        or watch.get("model")
        or watch.get("original_price")
        or watch.get("price")
        or watch.get("usd_price")
    )


def extract_unknown_brand_text(watch: Record) -> str | None:
    """Extract likely unknown brand text from a parsed watch without a brand."""
    if watch.get("brand"):
        return None
    if not watch_has_parse_signal(watch):
        return None

    source_line = (watch.get("source_line") or "").strip()
    if not source_line:
        return None

    tokens = re.findall(r"[A-Za-z][A-Za-z0-9&\.\-]*", source_line)
    collected: list[str] = []
    for token in tokens[:3]:
        normalized = token.lower()
        if normalized in UNKNOWN_BRAND_STOP_WORDS:
            continue
        if watch.get("reference") and normalized == str(watch["reference"]).lower():
            continue
        collected.append(token)
        if len(collected) >= 2:
            break

    if collected:
        return collected[0]
    return tokens[0] if tokens else source_line[:80]


def record_unknown_brands_for_watches(
    watches: list[Record],
    *,
    example_message: str,
    dealer_id: str | None,
    seen_at: datetime | None = None,
) -> list[Record]:
    """Persist unknown brand sightings for watches missing brand recognition."""
    try:
        from database import record_unknown_brand_sighting, watch_knowledge_supported
    except ImportError:  # pragma: no cover
        return []

    if not watch_knowledge_supported():
        return []

    recorded: list[Record] = []
    seen: set[str] = set()
    for watch in watches:
        detected_text = extract_unknown_brand_text(watch)
        if not detected_text:
            continue
        normalized = normalize_unknown_brand_text(detected_text)
        if normalized in seen:
            continue
        seen.add(normalized)
        row = record_unknown_brand_sighting(
            detected_text=detected_text,
            example_message=example_message,
            dealer_id=dealer_id,
            seen_at=seen_at,
        )
        if row:
            recorded.append(row)
    return recorded
