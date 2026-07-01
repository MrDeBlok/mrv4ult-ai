"""Dealer intelligence helpers built from existing offer data."""

from __future__ import annotations

from timezone_utils import format_display_timestamp
from typing import Any

from search import _display_value, _nested_record, _sort_key_usd_price, format_price, format_usd_price
from condition_normalizer import display_condition

Record = dict[str, Any]


def flatten_offer_intelligence_row(offer: Record) -> Record:
    """Flatten nested message metadata onto an offer intelligence row."""
    message = _nested_record(offer.get("messages"))
    return {
        "dealer_id": offer.get("dealer_id"),
        "watch_id": offer.get("watch_id"),
        "status": offer.get("status"),
        "usd_price": offer.get("usd_price"),
        "received_at": message.get("received_at"),
    }


def flatten_offer_intelligence_rows(offers: list[Record]) -> list[Record]:
    return [flatten_offer_intelligence_row(offer) for offer in offers]


def aggregate_offers_by_dealer(offers: list[Record]) -> dict[str, list[Record]]:
    """Group flattened offer intelligence rows by dealer id."""
    grouped: dict[str, list[Record]] = {}
    for offer in offers:
        dealer_id = offer.get("dealer_id")
        if not dealer_id:
            continue
        grouped.setdefault(str(dealer_id), []).append(offer)
    return grouped


def compute_dealer_stats(offer_rows: list[Record]) -> dict[str, Any]:
    """Compute dealer metrics from flattened offer intelligence rows."""
    total_offers = len(offer_rows)
    active_rows = [row for row in offer_rows if row.get("status") == "active"]
    active_offers = len(active_rows)
    usd_prices = [
        price for price in (row.get("usd_price") for row in active_rows) if price is not None
    ]
    received_times = [row.get("received_at") for row in offer_rows if row.get("received_at")]
    return {
        "total_offers": total_offers,
        "active_offers": active_offers,
        "average_usd": round(sum(usd_prices) / len(usd_prices)) if usd_prices else None,
        "lowest_usd": min(usd_prices) if usd_prices else None,
        "highest_usd": max(usd_prices) if usd_prices else None,
        "last_activity": max(received_times) if received_times else None,
        "unique_watches": len(
            {row.get("watch_id") for row in active_rows if row.get("watch_id")}
        ),
    }


def format_activity_timestamp(value: str | None) -> str:
    return format_display_timestamp(value)


def dealer_display_name(dealer: Record) -> str:
    display_name = (dealer.get("display_name") or "").strip()
    if display_name:
        return display_name
    phone_number = (dealer.get("phone_number") or "").strip()
    whatsapp_id = (dealer.get("whatsapp_id") or "").strip()
    return phone_number or whatsapp_id or "Unknown dealer"


def format_dealer_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_offers": stats.get("total_offers", 0),
        "active_offers": stats.get("active_offers", 0),
        "average_asking_price": format_usd_price(stats.get("average_usd")),
        "lowest_asking_price": format_usd_price(stats.get("lowest_usd")),
        "highest_asking_price": format_usd_price(stats.get("highest_usd")),
        "last_activity": format_activity_timestamp(stats.get("last_activity")),
        "unique_watches": stats.get("unique_watches", 0),
        "_last_activity_raw": stats.get("last_activity"),
    }


def build_dealer_list_row(dealer: Record, stats: dict[str, Any]) -> Record:
    formatted = format_dealer_stats(stats)
    return {
        "id": dealer.get("id"),
        "name": dealer_display_name(dealer),
        "total_offers": formatted["total_offers"],
        "active_offers": formatted["active_offers"],
        "average_asking_price": formatted["average_asking_price"],
        "lowest_asking_price": formatted["lowest_asking_price"],
        "highest_asking_price": formatted["highest_asking_price"],
        "last_activity": formatted["last_activity"],
        "_last_activity_raw": formatted["_last_activity_raw"],
    }


def build_dealer_list_rows(dealers: list[Record], offers: list[Record]) -> list[Record]:
    """Build dealer list rows with aggregated offer intelligence."""
    flattened = flatten_offer_intelligence_rows(offers)
    grouped = aggregate_offers_by_dealer(flattened)
    rows = [
        build_dealer_list_row(
            dealer,
            compute_dealer_stats(grouped.get(str(dealer["id"]), [])),
        )
        for dealer in dealers
        if dealer.get("id")
    ]
    rows.sort(
        key=lambda row: (
            row["_last_activity_raw"] is None,
            row["_last_activity_raw"] or "",
            row["name"].lower(),
        ),
        reverse=True,
    )
    for row in rows:
        row.pop("_last_activity_raw", None)
    return rows


def build_dealer_profile(dealer: Record) -> Record:
    contact = dealer.get("phone_number") or dealer.get("whatsapp_id") or "N/A"
    return {
        "name": dealer_display_name(dealer),
        "whatsapp_id": dealer.get("whatsapp_id") or "N/A",
        "phone_number": dealer.get("phone_number") or "N/A",
        "contact": contact,
        "company_name": _display_value(dealer.get("company_name")),
        "country": _display_value(dealer.get("country")),
        "is_active": "Active" if dealer.get("is_active", True) else "Inactive",
        "is_active_class": "success" if dealer.get("is_active", True) else "secondary",
        "created_at": format_activity_timestamp(dealer.get("created_at")),
        "updated_at": format_activity_timestamp(dealer.get("updated_at")),
    }


def build_dealer_offer_rows(offers: list[Record]) -> list[Record]:
    """Format active dealer offers for the detail page table."""
    rows: list[Record] = []
    for offer in sorted(offers, key=_sort_key_usd_price):
        watch = offer.get("watch") or _nested_record(offer.get("watches"))
        rows.append(
            {
                "watch_id": offer.get("watch_id"),
                "brand": _display_value(watch.get("brand")),
                "reference": _display_value(watch.get("reference")),
                "model": _display_value(watch.get("model")),
                "group_name": offer.get("group_name") or "N/A",
                "original_price": format_price(
                    offer.get("original_price"),
                    offer.get("original_currency"),
                ),
                "usd_price": format_usd_price(offer.get("usd_price")),
                "card_date": offer.get("card_date") or "N/A",
                "condition": display_condition(offer.get("condition")),
                "received_at": format_activity_timestamp(offer.get("received_at")),
            }
        )
    return rows
