"""Client sourcing workspace: live offer matching against client requests."""

from __future__ import annotations

from typing import Any

from dealer_intelligence import dealer_display_name, format_activity_timestamp
from model_aliases import enrich_with_model_alias
from request_matching import evaluate_sourcing_match
from request_profit import calculate_match_profit
from search import _display_value, _nested_record, format_usd_price

Record = dict[str, Any]

OPEN_REQUEST_STATUSES = {"open", "active"}


def is_open_request(request: Record) -> bool:
    return (request.get("status") or "").lower() in OPEN_REQUEST_STATUSES


def count_open_requests(requests: list[Record]) -> int:
    return sum(1 for request in requests if is_open_request(request))


def build_sourcing_offer_payload(offer: Record) -> Record:
    """Build a flat offer payload for the matching engine from a database row."""
    watch = enrich_with_model_alias(_nested_record(offer.get("watches")))
    return {
        "brand": watch.get("brand"),
        "reference": watch.get("reference"),
        "model": watch.get("model"),
        "dial": watch.get("dial"),
        "nickname": watch.get("nickname"),
        "model_alias": watch.get("model_alias"),
        "condition": offer.get("condition"),
        "production_year": offer.get("production_year"),
        "card_date": offer.get("card_date"),
        "original_price": offer.get("original_price"),
        "original_currency": offer.get("original_currency"),
        "usd_price": offer.get("usd_price"),
    }


def watch_display_label(watch: Record) -> str:
    parts = [
        part
        for part in (
            watch.get("brand"),
            watch.get("reference") or watch.get("model"),
        )
        if part
    ]
    return " · ".join(parts) if parts else "Watch"


def find_best_sourcing_match_for_offer(
    offer_payload: Record,
    requests: list[Record],
) -> tuple[Record | None, Record | None]:
    """Return the best sourcing match and request for one offer."""
    best_match: Record | None = None
    best_request: Record | None = None

    for request in requests:
        if not is_open_request(request):
            continue
        match = evaluate_sourcing_match(offer_payload, request)
        if match is None:
            continue
        if best_match is None or int(match.get("match_score") or 0) > int(
            best_match.get("match_score") or 0
        ):
            best_match = match
            best_request = request

    return best_match, best_request


def find_matching_offers_for_client(
    *,
    requests: list[Record],
    offers: list[Record],
) -> list[Record]:
    """Scan active offers against a client's open requests."""
    matches: list[Record] = []

    for offer in offers:
        offer_id = offer.get("id")
        if not offer_id:
            continue

        offer_payload = build_sourcing_offer_payload(offer)
        match, request = find_best_sourcing_match_for_offer(offer_payload, requests)
        if match is None or request is None:
            continue

        watch = _nested_record(offer.get("watches"))
        dealer = _nested_record(offer.get("dealers"))
        message = _nested_record(offer.get("messages"))
        profit = calculate_match_profit(request, offer_payload)

        matches.append(
            {
                "offer_id": offer_id,
                "watch_id": offer.get("watch_id"),
                "dealer_id": offer.get("dealer_id"),
                "match": match,
                "request": request,
                "offer_payload": offer_payload,
                "watch": watch,
                "dealer": dealer,
                "received_at": message.get("received_at"),
                "profit": profit,
            }
        )

    matches.sort(
        key=lambda row: (
            -(int((row.get("match") or {}).get("match_score") or 0)),
            -(int((row.get("profit") or {}).get("potential_profit_usd") or 0)),
        )
    )
    return matches


def build_matching_offer_row(match_row: Record) -> Record:
    """Format one matching offer row for the client detail page."""
    watch = match_row.get("watch") or {}
    dealer = match_row.get("dealer") or {}
    match = match_row.get("match") or {}
    profit = match_row.get("profit") or {}

    return {
        "dealer_id": match_row.get("dealer_id"),
        "dealer_name": dealer_display_name(dealer),
        "watch_id": match_row.get("watch_id"),
        "watch_label": watch_display_label(watch),
        "reference": _display_value(watch.get("reference")),
        "asking_price": profit.get("offer_price") or format_usd_price(match_row.get("offer_payload", {}).get("usd_price")),
        "match_score": match.get("match_score"),
        "match_badge": match.get("match_badge"),
        "match_badge_class": match.get("match_badge_class"),
        "potential_profit": profit.get("potential_profit") or "—",
        "potential_profit_usd": profit.get("potential_profit_usd"),
        "offer_date": format_activity_timestamp(match_row.get("received_at")),
        "_offer_date_raw": match_row.get("received_at"),
    }


def build_matching_offer_rows(matches: list[Record]) -> list[Record]:
    rows = [build_matching_offer_row(match_row) for match_row in matches]
    for row in rows:
        row.pop("_offer_date_raw", None)
    return rows


def build_client_sourcing_dashboard(
    *,
    requests: list[Record],
    matching_offers: list[Record],
) -> Record:
    """Build sourcing summary cards for the client detail page."""
    open_count = count_open_requests(requests)
    offer_rows = build_matching_offer_rows(matching_offers)

    best_profit_usd: int | None = None
    best_profit_label = "—"
    for row in offer_rows:
        profit_usd = row.get("potential_profit_usd")
        if profit_usd is None:
            continue
        if best_profit_usd is None or int(profit_usd) > best_profit_usd:
            best_profit_usd = int(profit_usd)
            best_profit_label = row.get("potential_profit") or "—"

    latest_offer = "—"
    latest_raw = None
    for match_row in matching_offers:
        received_at = match_row.get("received_at")
        if received_at and (latest_raw is None or received_at > latest_raw):
            latest_raw = received_at
            watch = match_row.get("watch") or {}
            latest_offer = watch_display_label(watch)

    return {
        "open_requests": open_count,
        "matching_offers_count": len(offer_rows),
        "best_potential_profit": best_profit_label,
        "latest_matching_offer": latest_offer,
    }
