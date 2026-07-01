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
    r"need\s+(?:a\s+)?(?:rolex|patek|ap|rm|watch)|"
    r"searching\s+for"
    r")\b",
    re.I,
)

SOLD_ORDER_PATTERN = re.compile(
    r"\b("
    r"sold[\s_-]*order|"
    r"sold\s+for\s+client|"
    r"client\s+sold\s+need|"
    r"need\s+for\s+sold\s+client"
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


def watch_has_substantive_identity(watch: Record) -> bool:
    """Return True when parsed data shows the message is actually about a watch."""
    if watch.get("reference"):
        return True
    if watch.get("model"):
        return True
    if watch.get("nickname"):
        return True
    if watch.get("dial") or watch.get("bracelet"):
        return True

    if watch.get("brand") and watch_has_price_signal(watch):
        return True

    model_alias = watch.get("model_alias") or {}
    if model_alias.get("nickname") or model_alias.get("alias") or model_alias.get("model"):
        return True
    if model_alias.get("reference_status") == "Unknown":
        return True

    watch_identification = watch.get("watch_identification") or {}
    if watch_identification.get("nickname"):
        return True
    if watch_identification.get("likely_references"):
        return True
    if watch_identification.get("model") or watch_identification.get("collection"):
        return True

    try:
        from unknown_brand_intelligence import extract_unknown_brand_text

        if extract_unknown_brand_text(watch):
            return True
    except ImportError:  # pragma: no cover
        pass

    source_line = watch.get("source_line") or ""
    if source_line:
        try:
            from watch_identifier import identify_text

            result = identify_text(source_line)
            if result:
                if result.get("likely_references"):
                    return True
                if result.get("nickname") or result.get("model") or result.get("collection"):
                    return True
        except ImportError:  # pragma: no cover
            pass

    return False


def watch_has_identity_signal(watch: Record) -> bool:
    """Return True when a parsed watch looks like a real watch offer line."""
    return watch_has_substantive_identity(watch)


def is_noise_watch(watch: Record) -> bool:
    """Return True for price-only lines without brand/reference/model signals."""
    if not watch_has_price_signal(watch):
        return False

    hard_fields = ("brand", "reference", "model", "nickname", "dial", "bracelet")
    if not any(watch.get(field) for field in hard_fields):
        return True

    return not watch_has_substantive_identity(watch)


def is_sold_order_message(text: str) -> bool:
    """Return True when the message is an urgent sold-order sourcing request."""
    return bool(SOLD_ORDER_PATTERN.search(text))


def sold_order_has_actionable_identity(watches: list[Record]) -> bool:
    """Return True when a sold-order message has enough data for market matching."""
    for watch in watches:
        if watch.get("reference"):
            return True
        if watch.get("brand") and (
            watch.get("model") or watch.get("nickname") or watch.get("dial")
        ):
            return True
    return False


def enrich_sold_order_watches(watches: list[Record]) -> list[Record]:
    """Infer missing brand/model from reference knowledge for sold-order WTB messages."""
    from watch_knowledge import lookup_reference

    enriched: list[Record] = []
    for watch in watches:
        row = dict(watch)
        if not row.get("brand") and row.get("reference"):
            knowledge = lookup_reference(str(row.get("reference")))
            if knowledge and knowledge.get("brand"):
                row["brand"] = knowledge["brand"]
                if not row.get("model") and knowledge.get("model"):
                    row["model"] = knowledge["model"]
                if not row.get("nickname") and knowledge.get("nickname"):
                    row["nickname"] = knowledge["nickname"]
                if not row.get("dial") and knowledge.get("dial_color"):
                    row["dial"] = knowledge["dial_color"]
        enriched.append(row)
    return enriched


def is_buyer_request_message(text: str, parsed: Record) -> bool:
    """Return True when the message is a buyer request rather than a dealer offer."""
    if OFFER_INTENT_PATTERN.search(text):
        return False
    if is_sold_order_message(text):
        return True
    if BUYER_REQUEST_PATTERN.search(text):
        return True
    if parsed.get("message_type") == "request":
        return True
    return False


def split_offer_watches(text: str, parsed: Record, watches: list[Record]) -> tuple[list[Record], str | None]:
    """Split parsed watches into offer candidates and optional classification."""
    if is_sold_order_message(text) or is_buyer_request_message(text, parsed):
        return [], "request_intent"

    if not watches:
        return [], None

    substantive_watches = [watch for watch in watches if watch_has_substantive_identity(watch)]
    brand_only_watches = [
        watch
        for watch in watches
        if watch.get("brand") and not watch_has_substantive_identity(watch)
    ]

    if not substantive_watches:
        if brand_only_watches or any(is_noise_watch(watch) for watch in watches):
            return [], "noise"
        return [], None

    offer_watches = [watch for watch in substantive_watches if not is_noise_watch(watch)]
    if offer_watches:
        return offer_watches, None

    if brand_only_watches or any(is_noise_watch(watch) for watch in watches):
        return [], "noise"

    return [], None


def looks_like_parser_review_offer(import_log: Record) -> bool:
    """Return True when an import belongs on Parser Review."""
    summary = import_log.get("summary") or {}
    if summary.get("import_classification") == "request_intent":
        return False
    watches = summary.get("parsed_watches") or summary.get("rows") or []
    if not watches:
        return False
    return any(watch_has_substantive_identity(watch) for watch in watches)
