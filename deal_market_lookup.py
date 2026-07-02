"""Live and stored market comparable resolution for Deal Analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION, normalize_condition_value, resolve_offer_wear_condition
from database import get_offers_by_ids

Record = dict[str, Any]

INSUFFICIENT_MARKET_DATA = "Insufficient Market Data"


@dataclass
class DealMarketContext:
    effective_row: Record
    comparison_safe: bool
    market_usd: int | None
    offer_condition: str | None
    market_condition: str | None
    needs_review: bool = False
    insufficient_market_data: bool = False
    market_status_message: str | None = None
    debug: Record = field(default_factory=dict)


def _parse_usd_amount(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned or cleaned.upper() == "N/A":
        return None
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def _has_display_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and (not value.strip() or value.strip().upper() == "N/A"):
        return False
    return True


def _resolve_watch_id(row: Record) -> str | None:
    offer_id = row.get("offer_id")
    if not offer_id:
        return None
    try:
        offer = get_offers_by_ids([str(offer_id)]).get(str(offer_id))
    except Exception:
        return None
    if not offer:
        return None
    watch_id = offer.get("watch_id")
    return str(watch_id) if watch_id else None


def _load_active_offer_pool(
    watch_id: str | None,
    *,
    exclude_offer_ids: set[str],
) -> list[tuple[str, int, str | None]]:
    if not watch_id:
        return []

    from ingest import _get_active_offers

    pool: list[tuple[str, int, str | None]] = []
    for offer_id, usd_price, condition in _get_active_offers(watch_id):
        if offer_id in exclude_offer_ids:
            continue
        if usd_price is None or usd_price <= 0:
            continue
        pool.append((offer_id, int(usd_price), condition))
    return pool


def _partition_comparables(
    pool: list[tuple[str, int, str | None]],
    *,
    offer_condition: str,
) -> tuple[list[tuple[str, int, str | None]], list[tuple[str, int, str | None]]]:
    same_condition: list[tuple[str, int, str | None]] = []
    excluded_missing_condition: list[tuple[str, int, str | None]] = []
    excluded_other_condition: list[tuple[str, int, str | None]] = []

    for entry in pool:
        offer_id, usd_price, condition = entry
        normalized = normalize_condition_value(condition)
        if normalized not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
            excluded_missing_condition.append(entry)
            continue
        if normalized != offer_condition:
            excluded_other_condition.append(entry)
            continue
        same_condition.append(entry)

    return same_condition, excluded_missing_condition + excluded_other_condition


def _stored_market_usd(row: Record, *, offer_condition: str | None) -> int | None:
    if row.get("price_label") == "No comparables":
        return None
    if not _has_display_value(row.get("previous_lowest_usd")):
        return None
    market_usd = _parse_usd_amount(row.get("previous_lowest_usd"))
    if market_usd is None or market_usd <= 0:
        return None

    stored_market_condition = normalize_condition_value(row.get("market_condition"))
    effective_market_condition = stored_market_condition or offer_condition
    if offer_condition is None or effective_market_condition != offer_condition:
        return None
    return market_usd


def _unknown_market_reason(
    *,
    offer_condition: str | None,
    watch_id: str | None,
    before_count: int,
    after_count: int,
    same_condition_entries: list[tuple[str, int, str | None]],
    excluded_entries: list[tuple[str, int, str | None]],
) -> str:
    if offer_condition is None:
        return "offer_condition_unknown"
    if not watch_id:
        return "watch_id_not_resolved_from_offer"
    if before_count == 0:
        return "no_other_active_offers_for_watch"
    if after_count == 0 and excluded_entries:
        missing_condition = sum(
            1
            for _offer_id, _price, condition in excluded_entries
            if normalize_condition_value(condition) not in {NEW_CONDITION, PRE_OWNED_CONDITION}
        )
        if missing_condition:
            return "active_offers_missing_condition_excluded"
        return "no_same_condition_comparables"
    if after_count == 0:
        return "no_same_condition_comparables"
    if not same_condition_entries:
        return "comparable_prices_invalid_or_zero"
    return "market_price_unavailable"


def _build_debug(
    *,
    row: Record,
    watch: Record,
    watch_id: str | None,
    offer_condition: str | None,
    market_condition: str | None,
    before_count: int,
    after_count: int,
    same_condition_entries: list[tuple[str, int, str | None]],
    reason: str | None,
) -> Record:
    return {
        "watch_id": watch_id or "—",
        "brand": row.get("brand") or watch.get("brand") or "—",
        "reference": row.get("reference") or watch.get("reference") or "—",
        "normalized_condition": offer_condition or "Unknown",
        "market_condition": market_condition or "—",
        "active_comparables_before_condition_filter": before_count,
        "active_comparables_after_condition_filter": after_count,
        "comparable_offer_ids": [entry[0] for entry in same_condition_entries],
        "comparable_prices": [entry[1] for entry in same_condition_entries],
        "comparable_conditions": [
            normalize_condition_value(entry[2]) or entry[2] or "Unknown"
            for entry in same_condition_entries
        ],
        "market_price_unknown_reason": reason or "—",
    }


def resolve_deal_market_context(
    row: Record,
    watch: Record,
    *,
    include_debug: bool = False,
) -> DealMarketContext:
    """Resolve market comparables from live active offers with stored fallback."""
    offer_condition = resolve_offer_wear_condition(
        row.get("condition"),
        watch.get("condition"),
        row.get("raw_condition"),
        watch.get("raw_condition"),
    )

    offer_id = str(row.get("offer_id") or "")
    exclude_offer_ids = {offer_id} if offer_id else set()
    watch_id = _resolve_watch_id(row)
    pool = _load_active_offer_pool(watch_id, exclude_offer_ids=exclude_offer_ids)
    before_count = len(pool)

    if offer_condition is None:
        debug = _build_debug(
            row=row,
            watch=watch,
            watch_id=watch_id,
            offer_condition=offer_condition,
            market_condition=normalize_condition_value(row.get("market_condition")),
            before_count=before_count,
            after_count=0,
            same_condition_entries=[],
            reason="offer_condition_unknown",
        ) if include_debug else {}
        return DealMarketContext(
            effective_row=dict(row),
            comparison_safe=False,
            market_usd=None,
            offer_condition=None,
            market_condition=normalize_condition_value(row.get("market_condition")),
            needs_review=True,
            market_status_message="Condition unknown. Market comparison unavailable.",
            debug=debug,
        )

    stored_market_condition = normalize_condition_value(row.get("market_condition"))
    if (
        stored_market_condition in {NEW_CONDITION, PRE_OWNED_CONDITION}
        and stored_market_condition != offer_condition
    ):
        debug = _build_debug(
            row=row,
            watch=watch,
            watch_id=watch_id,
            offer_condition=offer_condition,
            market_condition=stored_market_condition,
            before_count=before_count,
            after_count=0,
            same_condition_entries=[],
            reason="stored_market_condition_mismatch",
        ) if include_debug else {}
        return DealMarketContext(
            effective_row=dict(row),
            comparison_safe=False,
            market_usd=None,
            offer_condition=offer_condition,
            market_condition=stored_market_condition,
            needs_review=True,
            market_status_message="Stored market condition does not match offer condition.",
            debug=debug,
        )

    same_condition_entries, excluded_entries = _partition_comparables(
        pool,
        offer_condition=offer_condition,
    )
    comparable_prices = [price for _offer_id, price, _condition in same_condition_entries if price > 0]
    after_count = len(comparable_prices)

    from ingest import _build_price_intelligence

    if comparable_prices:
        price_intelligence = _build_price_intelligence(
            row.get("usd_price") if row.get("usd_price") is not None else watch.get("usd_price"),
            comparable_prices,
            is_duplicate=row.get("price_label") == "Duplicate offer",
            market_condition=offer_condition,
        )
        effective_row = {
            **row,
            "rank": price_intelligence["rank"],
            "previous_lowest_usd": price_intelligence["previous_lowest_usd"],
            "price_difference": price_intelligence["price_difference"],
            "price_label": price_intelligence["label"],
            "price_label_class": price_intelligence["label_class"],
            "market_condition": price_intelligence.get("market_condition"),
        }
        market_usd = _parse_usd_amount(effective_row.get("previous_lowest_usd"))
        comparison_safe = market_usd is not None and market_usd > 0
        debug = _build_debug(
            row=row,
            watch=watch,
            watch_id=watch_id,
            offer_condition=offer_condition,
            market_condition=offer_condition,
            before_count=before_count,
            after_count=after_count,
            same_condition_entries=same_condition_entries,
            reason=None if comparison_safe else "market_price_unavailable",
        ) if include_debug else {}
        return DealMarketContext(
            effective_row=effective_row,
            comparison_safe=comparison_safe,
            market_usd=market_usd if comparison_safe else None,
            offer_condition=offer_condition,
            market_condition=offer_condition,
            debug=debug,
        )

    stored_market_usd = _stored_market_usd(row, offer_condition=offer_condition)
    if stored_market_usd is not None:
        effective_row = {
            **row,
            "market_condition": offer_condition,
        }
        debug = _build_debug(
            row=row,
            watch=watch,
            watch_id=watch_id,
            offer_condition=offer_condition,
            market_condition=offer_condition,
            before_count=before_count,
            after_count=after_count,
            same_condition_entries=same_condition_entries,
            reason=None,
        ) if include_debug else {}
        return DealMarketContext(
            effective_row=effective_row,
            comparison_safe=True,
            market_usd=stored_market_usd,
            offer_condition=offer_condition,
            market_condition=offer_condition,
            debug=debug,
        )

    reason = _unknown_market_reason(
        offer_condition=offer_condition,
        watch_id=watch_id,
        before_count=before_count,
        after_count=after_count,
        same_condition_entries=same_condition_entries,
        excluded_entries=excluded_entries,
    )
    effective_row = {
        **row,
        "market_condition": offer_condition,
        "previous_lowest_usd": "N/A",
        "price_label": "No comparables",
    }
    debug = _build_debug(
        row=row,
        watch=watch,
        watch_id=watch_id,
        offer_condition=offer_condition,
        market_condition=offer_condition,
        before_count=before_count,
        after_count=after_count,
        same_condition_entries=same_condition_entries,
        reason=reason,
    ) if include_debug else {}
    status_message = "No same-condition comparables found yet."
    if reason == "no_other_active_offers_for_watch":
        status_message = "No other same-condition comparables yet."
    return DealMarketContext(
        effective_row=effective_row,
        comparison_safe=False,
        market_usd=None,
        offer_condition=offer_condition,
        market_condition=offer_condition,
        insufficient_market_data=True,
        market_status_message=status_message,
        debug=debug,
    )
