"""Unknown nickname detection and recording during imports."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from watch_identifier import identify_text, normalize_identifier_key

Record = dict[str, Any]

logger = logging.getLogger(__name__)

MAX_UNKNOWN_NICKNAME_SIGHTINGS_PER_IMPORT = 5

UNKNOWN_NICKNAME_STOP_WORDS = frozenset(
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
        "usd",
        "hkd",
        "eur",
        "chf",
        "gbp",
        "dial",
        "jub",
        "oys",
        "oyster",
        "jubilee",
        "blue",
        "black",
        "green",
        "white",
        "grey",
        "gray",
        "gold",
        "steel",
    )
)


def watch_has_nickname_signal(watch: Record) -> bool:
    return bool(
        watch.get("reference")
        or watch.get("model")
        or watch.get("original_price")
        or watch.get("price")
        or watch.get("usd_price")
    )


def is_structured_dealer_list_offer(watch: Record) -> bool:
    """Return True for inventory rows parsed from a dealer list split."""
    if watch.get("dealer_list_line"):
        return True
    if not watch.get("brand") or not watch.get("reference"):
        return False
    return bool(
        watch.get("original_price")
        or watch.get("price")
        or watch.get("usd_price")
        or watch.get("production_year")
        or watch.get("condition")
    )


def should_learn_unknown_nickname(watch: Record) -> bool:
    """Structured dealer inventory rows should not pollute nickname learning."""
    return not is_structured_dealer_list_offer(watch)


def extract_unknown_nickname_text(watch: Record) -> str | None:
    """Extract likely unknown nickname text when identification failed."""
    if is_structured_dealer_list_offer(watch):
        return None
    if identify_text(
        " ".join(
            str(part)
            for part in (
                watch.get("source_line"),
                watch.get("nickname"),
                watch.get("model"),
                watch.get("notes"),
            )
            if part
        )
    ):
        return None

    if watch.get("nickname"):
        return str(watch["nickname"]).strip()

    source_line = (watch.get("source_line") or "").strip()
    if not source_line:
        return None

    tokens = re.findall(r"[A-Za-z][A-Za-z0-9&\.\-']*", source_line)
    collected: list[str] = []
    for token in tokens[:4]:
        normalized = token.lower()
        if normalized in UNKNOWN_NICKNAME_STOP_WORDS:
            continue
        if watch.get("reference") and normalized == str(watch["reference"]).lower():
            continue
        if watch.get("brand") and normalized in str(watch["brand"]).lower():
            continue
        collected.append(token)
        if len(collected) >= 2:
            break

    if not collected:
        return None
    return " ".join(collected)


def record_unknown_nicknames_for_watches(
    watches: list[Record],
    *,
    example_message: str,
    dealer_id: str | None,
    seen_at: datetime | None = None,
) -> list[Record]:
    """Persist unknown nickname sightings for watches without identification."""
    try:
        from database import record_unknown_nickname_sighting, watch_identification_supported
    except ImportError:  # pragma: no cover
        return []

    if not watch_identification_supported():
        return []

    recorded: list[Record] = []
    seen: set[str] = set()
    capped = False
    for watch in watches:
        if not should_learn_unknown_nickname(watch):
            continue
        if not watch_has_nickname_signal(watch):
            continue
        detected_text = extract_unknown_nickname_text(watch)
        if not detected_text:
            continue
        normalized = normalize_identifier_key(detected_text)
        if normalized in seen:
            continue
        if len(recorded) >= MAX_UNKNOWN_NICKNAME_SIGHTINGS_PER_IMPORT:
            if not capped:
                logger.warning(
                    "Capped unknown nickname learning at %s sightings for one import",
                    MAX_UNKNOWN_NICKNAME_SIGHTINGS_PER_IMPORT,
                )
                capped = True
            break
        seen.add(normalized)
        row = record_unknown_nickname_sighting(
            detected_text=detected_text,
            example_message=example_message,
            dealer_id=dealer_id,
            seen_at=seen_at,
        )
        if row:
            recorded.append(row)
    return recorded
