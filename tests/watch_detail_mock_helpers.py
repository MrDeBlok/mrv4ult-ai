"""Shared helpers for watch-detail RPC pagination tests."""

from __future__ import annotations

from typing import Any

from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION, UNKNOWN_CONDITION
from database import WATCH_REFERENCE_OFFERS_PAGE_RPC, WatchReferenceOffersPageResult
from watch_detail_filters import (
    WATCH_DETAIL_PAGE_SIZE,
    offer_matches_watch_detail_date_filter,
    paginate_watch_detail_offers,
    resolve_watch_detail_page,
    sort_offers_for_watch_detail,
)


def _condition_counts(offers: list[dict[str, Any]]) -> dict[str, int]:
    from condition_normalizer import offer_condition_category

    counts = {NEW_CONDITION: 0, PRE_OWNED_CONDITION: 0, "Unknown": 0}
    for offer in offers:
        category = offer_condition_category(offer.get("condition"))
        counts[category] = counts.get(category, 0) + 1
    return counts


def _statistics_for_offers(offers: list[dict[str, Any]]) -> dict[str, Any]:
    from price_review import offer_effective_usd_price, offer_includes_price_in_market_calculations

    usd_prices = [
        price
        for offer in offers
        if offer_includes_price_in_market_calculations(offer)
        for price in [offer_effective_usd_price(offer)]
        if price is not None
    ]
    dealer_ids = {offer.get("dealer_id") for offer in offers if offer.get("dealer_id")}
    group_keys = {
        offer.get("group_id") or offer.get("group_name")
        for offer in offers
        if offer.get("group_id") or offer.get("group_name")
    }
    average = round(sum(usd_prices) / len(usd_prices)) if usd_prices else None
    return {
        "lowest_usd_price": min(usd_prices) if usd_prices else None,
        "average_usd_price": average,
        "highest_usd_price": max(usd_prices) if usd_prices else None,
        "active_offer_count": len(offers),
        "unique_dealer_count": len(dealer_ids),
        "unique_group_count": len(group_keys),
        "condition_counts": _condition_counts(offers),
    }


def build_watch_reference_page_result(
    offers: list[dict[str, Any]],
    *,
    condition_filter: str | None = None,
    date_range: Any = None,
    sort_filter: str = "",
    currency_filter: str | None = None,
    page: int = 1,
    page_limit: int = WATCH_DETAIL_PAGE_SIZE,
) -> WatchReferenceOffersPageResult:
    """Simulate the watch-detail RPC page result using Python filter/sort rules."""
    from app import normalize_watch_detail_offer
    from condition_normalizer import offer_matches_watch_detail_condition

    raw_by_id = {str(offer.get("id")): offer for offer in offers if offer.get("id") is not None}
    normalized = [normalize_watch_detail_offer(offer) for offer in offers]
    filtered = [
        offer
        for offer in normalized
        if offer_matches_watch_detail_condition(offer.get("condition"), condition_filter)
    ]
    if currency_filter:
        filtered = [
            offer
            for offer in filtered
            if str(offer.get("original_currency") or "").strip().upper() == currency_filter.upper()
        ]
    if date_range is not None:
        filtered = [
            offer
            for offer in filtered
            if offer_matches_watch_detail_date_filter(offer, date_range)
        ]
    sorted_offers = sort_offers_for_watch_detail(filtered, sort_filter)
    page_offers, resolved_page, total_pages, total_count = paginate_watch_detail_offers(
        sorted_offers,
        page_input=page,
        page_size=page_limit,
    )
    statistics = _statistics_for_offers(filtered)
    page_offset = (resolved_page - 1) * page_limit
    raw_page_offers = tuple(
        raw_by_id.get(str(offer.get("id")), offer) for offer in page_offers
    )
    return WatchReferenceOffersPageResult(
        offers=raw_page_offers,
        total_count=total_count,
        has_more=(page_offset + page_limit) < total_count,
        page=resolved_page,
        total_pages=total_pages,
        page_limit=page_limit,
        page_offset=page_offset,
        statistics=statistics,
    )


def mock_watch_reference_offers_page_client(
    offers: list[dict[str, Any]],
) -> Any:
    """Mock get_client().rpc() for get_watch_reference_offers_page."""
    from datetime import datetime
    from unittest.mock import MagicMock

    from timezone_utils import parse_utc_timestamp
    from watch_detail_filters import WatchDetailDateRange

    def _resolve_payload(payload: dict[str, Any]) -> WatchReferenceOffersPageResult:
        condition_filter = payload.get("p_condition_filter") or None
        sort_filter = payload.get("p_sort_filter") or ""
        currency_filter = payload.get("p_currency_filter") or None
        page = int(payload.get("p_page") or 1)
        page_limit = int(payload.get("p_page_limit") or WATCH_DETAIL_PAGE_SIZE)
        date_from = parse_utc_timestamp(payload.get("p_date_from"))
        date_to = parse_utc_timestamp(payload.get("p_date_to"))
        date_range = None
        if date_from or date_to:
            date_range = WatchDetailDateRange(start=date_from, end=date_to)
        return build_watch_reference_page_result(
            offers,
            condition_filter=condition_filter,
            date_range=date_range,
            sort_filter=sort_filter,
            currency_filter=currency_filter,
            page=page,
            page_limit=page_limit,
        )

    mock_execute = MagicMock()

    def _execute() -> MagicMock:
        return mock_execute

    mock_rpc = MagicMock()
    mock_rpc.execute.side_effect = _execute

    def _rpc(name: str, payload: dict[str, Any]) -> MagicMock:
        assert name == WATCH_REFERENCE_OFFERS_PAGE_RPC
        result = _resolve_payload(payload)
        mock_execute.data = {
            "offers": list(result.offers),
            "total_count": result.total_count,
            "has_more": result.has_more,
            "page": result.page,
            "total_pages": result.total_pages,
            "page_limit": result.page_limit,
            "page_offset": result.page_offset,
            "statistics": result.statistics,
        }
        return mock_rpc

    mock_client = MagicMock()
    mock_client.rpc.side_effect = _rpc
    return mock_client


def watch_detail_page_side_effect(offers: list[dict[str, Any]]):
    """Return a side_effect for patching app.get_watch_reference_offers_page."""

    def _get_page(
        brand: str,
        reference: str,
        *,
        condition_filter: str | None = None,
        date_from: Any = None,
        date_to: Any = None,
        sort_filter: str = "",
        currency_filter: str | None = None,
        page: int = 1,
        page_limit: int = WATCH_DETAIL_PAGE_SIZE,
    ) -> WatchReferenceOffersPageResult:
        from watch_detail_filters import WatchDetailDateRange

        date_range = None
        if date_from or date_to:
            date_range = WatchDetailDateRange(start=date_from, end=date_to)
        return build_watch_reference_page_result(
            offers,
            condition_filter=condition_filter,
            date_range=date_range,
            sort_filter=sort_filter,
            currency_filter=currency_filter,
            page=page,
            page_limit=page_limit,
        )

    return _get_page
