"""Dedicated match detail page payload for client request ↔ offer matches."""

from __future__ import annotations

from typing import Any, Callable

from condition_normalizer import display_condition
from contact_classification import format_import_sender_label
from dashboard_data import (
    MATCH_STRENGTH_BADGE_CLASSES,
    MATCH_STRENGTH_LABELS,
    _request_watch_label,
)
from dealer_intelligence import format_activity_timestamp
from permissions import can_view_page
from request_profit import attach_profit_to_matches
from search import format_price
from user_visibility import can_view_import

Record = dict[str, Any]


def _format_request_budget(max_price: Any, currency: Any) -> str:
    if max_price is None:
        return "—"
    return format_price(int(max_price), str(currency) if currency else None)


def _format_year_range(min_year: Any, max_year: Any) -> str:
    if min_year and max_year:
        return f"{min_year}–{max_year}"
    if min_year:
        return f"{min_year}+"
    if max_year:
        return f"Up to {max_year}"
    return "—"


def _request_status_label(status: str | None) -> str:
    labels = {
        "open": "Open",
        "active": "Open",
        "matched": "Matched",
        "closed": "Closed",
    }
    if not status:
        return "Unknown"
    return labels.get(status.lower(), status.replace("_", " ").title())


def _request_status_class(status: str | None) -> str:
    return {
        "open": "primary",
        "active": "primary",
        "matched": "success",
        "closed": "secondary",
    }.get((status or "").lower(), "secondary")


def build_match_detail(
    request: Record,
    match: Record,
    *,
    message: Record | None = None,
    user: Record | None = None,
    format_timestamp: Callable[[str | None], str] | None = None,
) -> Record:
    """Build template payload for one enriched request match."""
    format_timestamp = format_timestamp or format_activity_timestamp
    profit = match.get("profit") or {}
    offer = match.get("offer") or {}
    watch = match.get("watch") or {}
    import_log = match.get("import_log") or {}
    strength = str(match.get("match_strength") or "")
    raw_message = (message or {}).get("raw_text") or ""

    request_url = "/requests" if can_view_page(user, "/requests") else None
    import_log_id = import_log.get("id")
    activity_url = f"/activity/{import_log_id}" if import_log_id else None

    return {
        "match_id": match.get("id"),
        "header": {
            "client_name": request.get("client_name") or "Client",
            "watch_label": _request_watch_label(request, watch),
            "status_label": profit.get("status_label") or "—",
            "status_class": profit.get("status_class") or "secondary",
            "confidence_label": MATCH_STRENGTH_LABELS.get(strength, "Match"),
            "confidence_class": MATCH_STRENGTH_BADGE_CLASSES.get(strength, "secondary"),
            "match_age": format_timestamp(match.get("created_at")),
        },
        "request": {
            "client_name": request.get("client_name") or "Client",
            "brand": request.get("brand") or "—",
            "reference": request.get("reference") or "—",
            "model": request.get("model") or "—",
            "alias": request.get("alias") or "—",
            "dial": request.get("dial") or "—",
            "condition": display_condition(request.get("condition"))
            if request.get("condition")
            else "—",
            "year_range": _format_year_range(request.get("min_year"), request.get("max_year")),
            "budget": _format_request_budget(request.get("max_price"), request.get("currency")),
            "request_date": format_timestamp(request.get("created_at")),
            "status_label": _request_status_label(request.get("status")),
            "status_class": _request_status_class(request.get("status")),
            "original_text": (request.get("notes") or "").strip(),
        },
        "offer": {
            "dealer": format_import_sender_label(import_log),
            "brand": watch.get("brand") or "—",
            "reference": watch.get("reference") or watch.get("model") or "—",
            "model": watch.get("model") or "—",
            "dial": watch.get("dial") or "—",
            "price": profit.get("offer_price") or "N/A",
            "currency": offer.get("original_currency") or "USD",
            "original_price": format_price(
                offer.get("original_price"),
                offer.get("original_currency"),
            ),
            "condition": display_condition(offer.get("condition"))
            if offer.get("condition")
            else "—",
            "year": offer.get("production_year") or "—",
            "card_date": offer.get("card_date") or "—",
            "offer_date": format_timestamp(import_log.get("import_time")),
            "group_name": import_log.get("group_name") or "—",
            "raw_message": raw_message.strip(),
        },
        "deal": {
            "potential_profit": profit.get("potential_profit") or "—",
            "budget": profit.get("budget") or "—",
            "budget_difference": profit.get("budget_difference") or "—",
            "margin": profit.get("margin") or "—",
            "status_label": profit.get("status_label") or "—",
            "status_class": profit.get("status_class") or "secondary",
            "confidence_label": MATCH_STRENGTH_LABELS.get(strength, "Match"),
            "confidence_class": MATCH_STRENGTH_BADGE_CLASSES.get(strength, "secondary"),
            "match_reason": match.get("match_reason") or "—",
            "match_strength": strength,
        },
        "actions": {
            "dashboard_url": "/dashboard",
            "request_url": request_url,
            "activity_url": activity_url,
        },
    }


def load_match_detail(
    user: Record | None,
    match_id: str,
    *,
    format_timestamp: Callable[[str | None], str] | None = None,
) -> Record | None:
    """Load one match detail payload when the user can view linked data."""
    from database import (
        get_message_by_id,
        get_request,
        get_request_match,
        load_enriched_request_match_batch,
    )

    match = get_request_match(match_id)
    if match is None:
        return None

    enriched_matches = load_enriched_request_match_batch([match])
    if not enriched_matches:
        return None

    enriched = enriched_matches[0]
    import_log = enriched.get("import_log") or {}
    if not import_log or not can_view_import(user, import_log):
        return None

    request = get_request(str(enriched.get("request_id") or ""))
    if request is None:
        return None

    profit_match = attach_profit_to_matches(request, [enriched])[0]
    message = None
    message_id = import_log.get("message_id")
    if message_id:
        message = get_message_by_id(str(message_id))

    return build_match_detail(
        request,
        profit_match,
        message=message,
        user=user,
        format_timestamp=format_timestamp,
    )
