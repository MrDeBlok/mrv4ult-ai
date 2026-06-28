"""Classify import messages as offers, buyer requests, or chat noise."""

from __future__ import annotations

import re
from typing import Any

Record = dict[str, Any]

BUYER_REQUEST_PATTERN = re.compile(
    r"\b("
    r"wtb|"
    r"want\s+to\s+buy|"
    r"looking\s+for|"
    r"lf\b|"
    r"iso\b|"
    r"need\s+(?:a\s+)?|"
    r"searching\s+for"
    r")\b",
    re.I,
)

OFFER_INTENT_PATTERN = re.compile(
    r"\b(fs|for\s+sale|asking|avail(?:able)?|stock|sell(?:ing)?)\b",
    re.I,
)


def watch_has_price_signal(watch: Record) -> bool:
    return bool(
        watch.get("original_price") is not None
        or watch.get("price") is not None
        or watch.get("usd_price") is not None
    )


def watch_has_identity_signal(watch: Record) -> bool:
    """Return True when a parsed watch looks like a real watch offer line."""
    if watch.get("brand") or watch.get("reference") or watch.get("model") or watch.get("nickname"):
        return True

    model_alias = watch.get("model_alias") or {}
    if model_alias.get("nickname") or model_alias.get("alias") or model_alias.get("model"):
        return True

    watch_identification = watch.get("watch_identification") or {}
    if watch_identification.get("brand") or watch_identification.get("nickname"):
        return True
    if watch_identification.get("likely_references"):
        return True

    try:
        from watch_identifier import identify_text

        source_line = watch.get("source_line") or ""
        if source_line:
            result = identify_text(source_line)
            if result and (result.get("brand") or result.get("likely_references")):
                return True
    except ImportError:  # pragma: no cover
        pass

    return False


def is_noise_watch(watch: Record) -> bool:
    """Return True for price-only lines without brand/reference/model signals."""
    if not watch_has_price_signal(watch):
        return False
    return not watch_has_identity_signal(watch)


def is_buyer_request_message(text: str, parsed: Record) -> bool:
    """Return True when the message is a buyer request rather than a dealer offer."""
    if parsed.get("message_type") == "request":
        return True
    if not BUYER_REQUEST_PATTERN.search(text):
        return False
    if OFFER_INTENT_PATTERN.search(text):
        return False
    return True


def split_offer_watches(text: str, parsed: Record, watches: list[Record]) -> tuple[list[Record], str | None]:
    """Split parsed watches into offer candidates and optional classification."""
    if is_buyer_request_message(text, parsed):
        return [], "request_intent"

    if not watches:
        return [], None

    offer_watches = [watch for watch in watches if not is_noise_watch(watch)]
    if offer_watches:
        return offer_watches, None

    if any(is_noise_watch(watch) for watch in watches):
        return [], "noise"

    return [], None


def looks_like_parser_review_offer(import_log: Record) -> bool:
    """Return True when an import belongs on Parser Review."""
    summary = import_log.get("summary") or {}
    watches = summary.get("parsed_watches") or summary.get("rows") or []
    if not watches:
        return False
    return any(watch_has_identity_signal(watch) for watch in watches)
