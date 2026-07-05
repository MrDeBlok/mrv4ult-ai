"""Debug helpers for tracing import-to-search pipeline failures."""

from __future__ import annotations

from typing import Any

from database import (
    contact_type_column_supported,
    dealer_contact_type,
    find_watches_by_reference,
    get_client,
    get_dealer_by_id,
    is_business_dealer_relation,
)
from search import (
    _filter_search_offers,
    _normalize_search_reference,
    _reference_contains_token,
    _resolve_search_dealer,
    _resolve_search_watch,
    _watch_matches_tokens,
    parse_query,
    trace_search_query,
)
import search as search_module

Record = dict[str, Any]

FINAL_REASONS = (
    "no_watch",
    "no_active_offer",
    "hidden_by_dealer_visibility",
    "reference_token_mismatch",
    "max_price_filter",
    "condition_filter",
    "search_row_limit_truncated",
    "cheapest_only",
    "found",
    "other",
)


def _active_offers_for_watch_ids(watch_ids: list[str]) -> list[Record]:
    if not watch_ids:
        return []

    dealer_fields = (
        "dealers(id, display_name, contact_type, whatsapp_id)"
        if contact_type_column_supported()
        else "dealers(id, display_name, whatsapp_id)"
    )
    response = (
        get_client()
        .table("offers")
        .select(
            "id, status, watch_id, dealer_id, condition, usd_price, original_price, original_currency, "
            f"{dealer_fields}"
        )
        .in_("watch_id", watch_ids)
        .eq("status", "active")
        .execute()
    )
    return response.data or []


def _summarize_offer_row(offer: Record) -> Record[str, Any]:
    dealer = _resolve_search_dealer(offer, cache={})
    if not dealer and offer.get("dealer_id"):
        loaded = get_dealer_by_id(str(offer["dealer_id"]))
        if loaded:
            dealer = loaded
    return {
        "offer_id": offer.get("id"),
        "status": offer.get("status"),
        "dealer_id": offer.get("dealer_id"),
        "dealer_contact_type": dealer.get("contact_type"),
        "dealer_contact_type_effective": dealer_contact_type(dealer, has_offers=True),
        "dealer_visible_in_search": is_business_dealer_relation(dealer, has_offers=True),
        "condition": offer.get("condition"),
        "usd_price": offer.get("usd_price"),
    }


def _summarize_watch_row(watch: Record) -> Record[str, Any]:
    return {
        "watch_id": watch.get("id"),
        "brand": watch.get("brand"),
        "reference": watch.get("reference"),
        "normalized_reference": _normalize_search_reference(watch.get("reference")),
    }


def _determine_final_reason(
    *,
    reference_query: str,
    matching_watches: list[Record],
    active_offers: list[Record],
    search_trace: Record[str, Any],
    filtered_offers: list[Record],
    loaded_offers: list[Record],
) -> str:
    if not matching_watches:
        return "no_watch"
    if not active_offers:
        return "no_active_offer"

    if filtered_offers:
        return "found"

    counts = search_trace.get("counts") or {}
    loaded = int(counts.get("loaded") or 0)
    after_dealer = int(counts.get("after_dealer_visibility") or 0)
    after_reference = int(counts.get("after_reference_matching") or 0)
    after_price = int(counts.get("after_max_price") or 0)
    after_condition = int(counts.get("after_condition_filter") or 0)

    if search_trace.get("search_row_limit_truncated"):
        watch_ids = {str(watch.get("id")) for watch in matching_watches if watch.get("id")}
        loaded_watch_ids = {
            str(offer.get("watch_id")) for offer in loaded_offers if offer.get("watch_id")
        }
        if watch_ids - loaded_watch_ids:
            return "search_row_limit_truncated"

    if loaded > 0 and after_dealer == 0:
        return "hidden_by_dealer_visibility"
    if after_dealer > 0 and after_reference == 0:
        return "reference_token_mismatch"
    if after_reference > 0 and after_price == 0:
        return "max_price_filter"
    if after_price > 0 and after_condition == 0:
        return "condition_filter"

    tokens, _, cheapest_only = parse_query(reference_query)
    if cheapest_only and after_condition > 0:
        return "cheapest_only"

    target_watch_ids = {str(watch.get("id")) for watch in matching_watches if watch.get("id")}
    watch_cache: dict[str, Record] = {}
    dealer_cache: dict[str, Record] = {}
    for offer in loaded_offers:
        if str(offer.get("watch_id")) not in target_watch_ids:
            continue
        dealer = _resolve_search_dealer(offer, cache=dealer_cache)
        if not is_business_dealer_relation(dealer, has_offers=True):
            return "hidden_by_dealer_visibility"
        watch = _resolve_search_watch(offer, cache=watch_cache)
        if not _watch_matches_tokens(watch, tokens):
            return "reference_token_mismatch"

    return "other"


def trace_fresh_offer_searchability(
    reference_query: str,
    *,
    condition: str | None = None,
    import_summary: Record | None = None,
    offer_row: Record | None = None,
    watch_row: Record | None = None,
    dealer_row: Record | None = None,
) -> Record[str, Any]:
    """Trace one reference from DB watches/offers through the Search filter pipeline."""
    reference_query = reference_query.strip()
    matching_watches = find_watches_by_reference(reference_query)
    if watch_row and watch_row.get("id"):
        known_ids = {str(watch.get("id")) for watch in matching_watches if watch.get("id")}
        if str(watch_row["id"]) not in known_ids:
            matching_watches.append(watch_row)

    watch_ids = [str(watch["id"]) for watch in matching_watches if watch.get("id")]
    active_offers = _active_offers_for_watch_ids(watch_ids)
    if offer_row and offer_row.get("id"):
        known_offer_ids = {str(offer.get("id")) for offer in active_offers if offer.get("id")}
        if str(offer_row["id"]) not in known_offer_ids and offer_row.get("status") == "active":
            active_offers.append(offer_row)

    tokens, max_usd_price, cheapest_only = parse_query(reference_query)
    loaded_offers, total_count = search_module._load_active_offers_for_search()
    search_trace = trace_search_query(
        reference_query,
        condition=condition,
        offers=loaded_offers,
        total_count=total_count,
    )
    filtered_offers = _filter_search_offers(
        loaded_offers,
        tokens=tokens,
        max_usd_price=max_usd_price,
        condition=condition,
    )

    final_reason = _determine_final_reason(
        reference_query=reference_query,
        matching_watches=matching_watches,
        active_offers=active_offers,
        search_trace=search_trace,
        filtered_offers=filtered_offers,
        loaded_offers=loaded_offers,
    )

    result: Record[str, Any] = {
        "reference_query": reference_query,
        "matching_watches": [_summarize_watch_row(watch) for watch in matching_watches],
        "active_offers": [_summarize_offer_row(offer) for offer in active_offers],
        "search_query_path": search_trace,
        "search_results_count": len(filtered_offers),
        "final_reason": final_reason,
        "search_should_include_offer": final_reason == "found",
    }

    if import_summary is not None:
        result["import_log_created"] = bool(import_summary.get("import_log_id"))
        result["watches_parsed"] = int(import_summary.get("watches_parsed") or 0)
        result["new_offers"] = int(import_summary.get("new_offers") or 0)

    if offer_row is not None:
        result["offer_exists"] = bool(offer_row.get("id"))
        result["offer_status"] = offer_row.get("status")
        result["offer_status_active"] = offer_row.get("status") == "active"

    if watch_row is not None:
        result["watch_reference"] = watch_row.get("reference")
        result["reference_token_matches_watch"] = _reference_contains_token(
            watch_row.get("reference"),
            reference_query,
        )

    if dealer_row is not None:
        result["dealer_contact_type"] = dealer_row.get("contact_type")
        result["dealer_contact_type_effective"] = dealer_contact_type(dealer_row, has_offers=True)
        result["dealer_visible_in_search_with_offers_context"] = is_business_dealer_relation(
            dealer_row,
            has_offers=True,
        )

    if cheapest_only and final_reason not in {"found", "cheapest_only"}:
        result["cheapest_only_note"] = (
            "cheapest_only is applied when grouping results in the UI, not in search_offers()."
        )

    return result
