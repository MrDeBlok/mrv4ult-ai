"""Watch identification evidence checks before creating watches and offers."""

from __future__ import annotations

import re
from typing import Any

Record = dict[str, Any]

INSUFFICIENT_EVIDENCE_REASON = "Not enough information to identify a watch."
WATCH_EVIDENCE_THRESHOLD = 50

YEAR_REFERENCE_PATTERN = re.compile(r"^(?:19|20)\d{2}[yY]?$")

EVIDENCE_WEIGHTS: dict[str, int] = {
    "brand": 15,
    "reference": 25,
    "price": 20,
    "condition": 10,
    "collection": 10,
    "nickname": 15,
    "currency": 5,
}


def has_watch_price_signal(watch: Record) -> bool:
    return bool(
        watch.get("original_price") is not None
        or watch.get("price") is not None
        or watch.get("usd_price") is not None
    )


def has_watch_currency_signal(watch: Record) -> bool:
    return bool(
        watch.get("original_currency")
        or watch.get("currency")
        or watch.get("retail_currency")
    )


def is_valid_watch_reference(watch: Record) -> bool:
    """Return True when a parsed reference looks like a real watch reference."""
    reference = watch.get("reference")
    if reference is None or reference == "":
        return False

    cleaned = str(reference).strip()
    if not cleaned:
        return False
    if YEAR_REFERENCE_PATTERN.match(cleaned):
        return False
    if cleaned.isdigit() and len(cleaned) == 4:
        year = int(cleaned)
        if 1990 <= year <= 2035:
            return False
    return True


def has_known_nickname(watch: Record) -> bool:
    if watch.get("nickname"):
        return True

    model_alias = watch.get("model_alias") or {}
    if model_alias.get("nickname") or model_alias.get("alias"):
        return True

    watch_identification = watch.get("watch_identification") or {}
    if watch_identification.get("nickname"):
        return True

    knowledge = watch.get("knowledge") or {}
    return bool(knowledge.get("nickname"))


def has_collection_signal(watch: Record) -> bool:
    if watch.get("model"):
        return True

    watch_identification = watch.get("watch_identification") or {}
    if watch_identification.get("model") or watch_identification.get("collection"):
        return True

    model_alias = watch.get("model_alias") or {}
    return bool(model_alias.get("model"))


def compute_watch_evidence_score(watch: Record) -> int:
    """Score how much evidence supports creating a watch from parsed data."""
    score = 0
    if watch.get("brand"):
        score += EVIDENCE_WEIGHTS["brand"]
    if is_valid_watch_reference(watch):
        score += EVIDENCE_WEIGHTS["reference"]
    if has_watch_price_signal(watch):
        score += EVIDENCE_WEIGHTS["price"]
    if watch.get("condition"):
        score += EVIDENCE_WEIGHTS["condition"]
    if has_collection_signal(watch):
        score += EVIDENCE_WEIGHTS["collection"]
    if has_known_nickname(watch):
        score += EVIDENCE_WEIGHTS["nickname"]
    if has_watch_currency_signal(watch) and (
        has_watch_price_signal(watch) or watch.get("currency")
    ):
        score += EVIDENCE_WEIGHTS["currency"]
    return score


def has_strong_watch_identity(watch: Record) -> bool:
    """Return True for combinations that clearly identify one watch offer."""
    has_reference = is_valid_watch_reference(watch)
    has_price = has_watch_price_signal(watch)
    has_brand = bool(watch.get("brand"))
    has_nickname = has_known_nickname(watch)

    if has_reference and has_brand:
        return True
    if has_reference and has_price:
        return True
    if has_brand and has_price:
        return True
    if has_nickname and has_price:
        return True
    return False


def has_sufficient_watch_evidence(watch: Record) -> bool:
    """Return True when parsed data is strong enough to create a watch and offer."""
    if has_strong_watch_identity(watch):
        return True
    return compute_watch_evidence_score(watch) >= WATCH_EVIDENCE_THRESHOLD


def describe_evidence_gaps(watch: Record) -> list[str]:
    """Return short human-readable gaps for ignored low-evidence watches."""
    gaps: list[str] = []
    if not is_valid_watch_reference(watch):
        gaps.append("No reference.")
    if not has_watch_price_signal(watch):
        gaps.append("No price.")
    if not watch.get("brand"):
        gaps.append("No brand.")
    if not watch.get("condition"):
        gaps.append("No condition.")
    if not has_collection_signal(watch) and not has_known_nickname(watch):
        gaps.append("Likely informational message.")
    return gaps


def partition_watches_by_evidence(
    watches: list[Record],
) -> tuple[list[Record], list[Record]]:
    """Split parsed watches into offer candidates and insufficient-evidence lines."""
    sufficient: list[Record] = []
    insufficient: list[Record] = []
    for watch in watches:
        if has_sufficient_watch_evidence(watch):
            sufficient.append(watch)
        else:
            insufficient.append(watch)
    return sufficient, insufficient
