"""Debug helpers for tracing import-to-search pipeline failures."""

from __future__ import annotations

from typing import Any

from database import dealer_contact_type, is_business_dealer_relation
from search import _reference_contains_token, _watch_matches_tokens

Record = dict[str, Any]


def trace_fresh_offer_searchability(
    *,
    reference_query: str,
    import_summary: Record | None = None,
    offer_row: Record | None = None,
    watch_row: Record | None = None,
    dealer_row: Record | None = None,
) -> Record[str, Any]:
    """Return a checklist explaining whether one imported offer should appear in Search."""
    watch = watch_row or {}
    dealer = dealer_row or {}
    offer = offer_row or {}
    tokens = reference_query.split()

    dealer_type = dealer_contact_type(dealer, has_offers=True)
    visible_without_offers = is_business_dealer_relation(dealer, has_offers=False)
    visible_with_offers = is_business_dealer_relation(dealer, has_offers=True)
    reference_matches = _reference_contains_token(watch.get("reference"), reference_query)
    watch_matches = _watch_matches_tokens(watch, tokens)

    return {
        "import_log_created": bool((import_summary or {}).get("import_log_id")),
        "watches_parsed": int((import_summary or {}).get("watches_parsed") or 0),
        "new_offers": int((import_summary or {}).get("new_offers") or 0),
        "offer_exists": bool(offer.get("id")),
        "offer_status": offer.get("status"),
        "offer_status_active": offer.get("status") == "active",
        "watch_id": offer.get("watch_id") or watch.get("id"),
        "watch_reference": watch.get("reference"),
        "reference_query": reference_query,
        "reference_token_matches_watch": reference_matches,
        "watch_matches_search_tokens": watch_matches,
        "dealer_contact_type": dealer.get("contact_type"),
        "dealer_contact_type_effective": dealer_type,
        "dealer_visible_in_search_without_offers_context": visible_without_offers,
        "dealer_visible_in_search_with_offers_context": visible_with_offers,
        "search_should_include_offer": bool(
            offer.get("status") == "active"
            and visible_with_offers
            and watch_matches
        ),
    }
