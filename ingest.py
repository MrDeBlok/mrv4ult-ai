"""Ingest parsed WhatsApp messages into Supabase."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Any

from database import (
    find_or_create_watch,
    get_client,
    insert_message,
    insert_offer,
)
from watch_parser import parse_message, read_message

PARSER_VERSION = "watch_parser_v1"
DEFAULT_GROUP_NAME = "Default Group"
DEFAULT_DEALER_WHATSAPP_ID = "default-dealer"
DEFAULT_DEALER_NAME = "Default Dealer"

IngestSummary = dict[str, Any]


def find_or_create_group(group_name: str) -> str:
    """Return a group id for the given name, creating the group if needed."""
    name = group_name.strip()
    if not name:
        raise ValueError("Group name is required.")

    client = get_client()
    existing = client.table("groups").select("id").eq("name", name).limit(1).execute()
    if existing.data:
        return existing.data[0]["id"]

    created = client.table("groups").insert({"name": name}).execute()
    if not created.data:
        raise RuntimeError(f"Failed to create group: {name}")
    return created.data[0]["id"]


def find_or_create_dealer(
    whatsapp_number: str,
    *,
    display_name: str | None = None,
) -> str:
    """Return a dealer id for the given WhatsApp number, creating or updating as needed."""
    whatsapp_id = _normalize_whatsapp_number(whatsapp_number)
    if not whatsapp_id:
        raise ValueError("Dealer WhatsApp number is required.")

    alias = display_name.strip() if display_name else None
    if alias == "":
        alias = None

    client = get_client()
    existing = (
        client.table("dealers")
        .select("id, display_name")
        .eq("whatsapp_id", whatsapp_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        dealer = existing.data[0]
        updates: dict[str, Any] = {"phone_number": whatsapp_id}
        if alias is not None:
            updates["display_name"] = alias
        client.table("dealers").update(updates).eq("id", dealer["id"]).execute()
        return dealer["id"]

    payload: dict[str, Any] = {
        "whatsapp_id": whatsapp_id,
        "phone_number": whatsapp_id,
        "is_active": True,
    }
    if alias is not None:
        payload["display_name"] = alias

    created = client.table("dealers").insert(payload).execute()
    if not created.data:
        raise RuntimeError(f"Failed to create dealer: {whatsapp_id}")
    return created.data[0]["id"]


def get_default_group_id() -> str:
    """Return the default group, creating it if needed."""
    return find_or_create_group(DEFAULT_GROUP_NAME)


def get_default_dealer_id() -> str:
    """Return the default dealer, creating it if needed."""
    return find_or_create_dealer(
        DEFAULT_DEALER_WHATSAPP_ID,
        display_name=DEFAULT_DEALER_NAME,
    )


def ingest_message(
    text: str,
    *,
    group_name: str | None = None,
    dealer_whatsapp: str | None = None,
    dealer_alias: str | None = None,
    received_at: datetime | None = None,
) -> IngestSummary:
    """Parse a message and save it with all offers to Supabase."""
    started_at = time.perf_counter()
    parsed = parse_message(text)

    if group_name is not None and dealer_whatsapp is not None:
        normalized_group_name = group_name.strip()
        normalized_whatsapp = _normalize_whatsapp_number(dealer_whatsapp)
        normalized_alias = dealer_alias.strip() if dealer_alias else None
        if normalized_alias == "":
            normalized_alias = None

        group_id = find_or_create_group(normalized_group_name)
        dealer_id = find_or_create_dealer(
            normalized_whatsapp,
            display_name=normalized_alias,
        )
        summary_group = normalized_group_name
        summary_whatsapp = normalized_whatsapp
        summary_alias = normalized_alias
    else:
        group_id = get_default_group_id()
        dealer_id = get_default_dealer_id()
        summary_group = DEFAULT_GROUP_NAME
        summary_whatsapp = DEFAULT_DEALER_WHATSAPP_ID
        summary_alias = DEFAULT_DEALER_NAME

    now = datetime.now(timezone.utc)
    message_received_at = received_at or now

    message = insert_message(
        group_id=group_id,
        dealer_id=dealer_id,
        raw_text=text,
        message_type=parsed["message_type"],
        received_at=message_received_at,
        parsed_at=now,
        parser_version=PARSER_VERSION,
        parse_status=_parse_status(parsed),
    )

    summary: IngestSummary = {
        "messages_imported": 1,
        "watches_parsed": 0,
        "new_watches": 0,
        "new_offers": 0,
        "duplicate_offers": 0,
        "matched_requests": 0,
        "processing_time": "",
        "group": summary_group,
        "dealer_whatsapp": summary_whatsapp,
        "dealer_alias": summary_alias,
        "rows": [],
    }

    for line_index, watch in enumerate(parsed["watches"]):
        summary["watches_parsed"] += 1
        watch_row, watch_created = find_or_create_watch(
            brand=watch.get("brand"),
            reference=watch.get("reference"),
            model=watch.get("model"),
            dial=watch.get("dial"),
            bracelet=watch.get("bracelet"),
        )
        if watch_created:
            summary["new_watches"] += 1

        existing_usd_prices = _get_active_usd_prices(watch_row["id"])

        _, offer_created, matched_requests = insert_offer(
            message_id=message["id"],
            watch_id=watch_row["id"],
            dealer_id=dealer_id,
            condition=watch.get("condition"),
            production_year=watch.get("production_year"),
            card_date=watch.get("card_date"),
            notes=watch.get("notes"),
            original_price=watch.get("original_price") or watch.get("price"),
            original_currency=watch.get("original_currency") or watch.get("currency"),
            usd_price=watch.get("usd_price"),
            exchange_rate_to_usd=watch.get("exchange_rate_to_usd"),
            line_index=line_index,
        )
        if offer_created:
            summary["new_offers"] += 1
        else:
            summary["duplicate_offers"] += 1
        summary["matched_requests"] += matched_requests

        summary["rows"].append(
            _build_watch_row(
                watch,
                watch_created=watch_created,
                offer_created=offer_created,
                request_matched=matched_requests > 0,
                price_intelligence=_build_price_intelligence(
                    watch.get("usd_price"),
                    existing_usd_prices,
                    is_duplicate=not offer_created,
                ),
            )
        )

    elapsed = time.perf_counter() - started_at
    summary["processing_time"] = _format_processing_time(elapsed)
    return summary


def _normalize_whatsapp_number(value: str) -> str:
    return value.strip()


def _get_active_usd_prices(watch_id: str) -> list[int]:
    """Return USD prices for all active offers on a watch."""
    response = (
        get_client()
        .table("offers")
        .select("usd_price")
        .eq("watch_id", watch_id)
        .eq("status", "active")
        .execute()
    )
    return [
        price
        for row in response.data or []
        if (price := row.get("usd_price")) is not None
    ]


def _build_price_intelligence(
    usd_price: int | None,
    existing_usd_prices: list[int],
    *,
    is_duplicate: bool,
) -> dict[str, str]:
    """Compare an imported offer against existing active offers for the same watch."""
    if is_duplicate:
        rank_prices = existing_usd_prices
        label = "Duplicate offer"
    else:
        rank_prices = existing_usd_prices + ([usd_price] if usd_price is not None else [])
        label = _price_intelligence_label(usd_price, existing_usd_prices)

    previous_lowest = min(existing_usd_prices) if existing_usd_prices else None

    return {
        "rank": _format_rank(_price_rank(usd_price, rank_prices)),
        "previous_lowest_usd": _format_usd_amount(previous_lowest),
        "price_difference": _format_price_difference(usd_price, previous_lowest),
        "label": label,
        "label_class": _price_label_class(label),
    }


def _price_intelligence_label(
    usd_price: int | None,
    existing_usd_prices: list[int],
) -> str:
    if usd_price is None:
        return "Normal price"
    if not existing_usd_prices:
        return "New lowest price"

    previous_lowest = min(existing_usd_prices)
    if usd_price < previous_lowest:
        return "New lowest price"
    if usd_price <= previous_lowest * 1.03:
        return "Good price"
    if usd_price <= previous_lowest * 1.10:
        return "Normal price"
    return "Expensive"


def _price_rank(usd_price: int | None, prices: list[int]) -> int | None:
    if usd_price is None or not prices:
        return None
    return sum(1 for price in prices if price < usd_price) + 1


def _format_rank(rank: int | None) -> str:
    if rank is None:
        return "N/A"
    return str(rank)


def _format_usd_amount(amount: int | None) -> str:
    if amount is None:
        return "N/A"
    return f"${amount:,}"


def _format_price_difference(usd_price: int | None, previous_lowest: int | None) -> str:
    if usd_price is None or previous_lowest is None:
        return "N/A"

    difference = usd_price - previous_lowest
    if difference == 0:
        return "$0"
    if difference > 0:
        return f"+${difference:,}"
    return f"-${abs(difference):,}"


def _price_label_class(label: str) -> str:
    return {
        "New lowest price": "success",
        "Good price": "info",
        "Normal price": "secondary",
        "Expensive": "danger",
        "Duplicate offer": "dark",
    }.get(label, "secondary")


def _build_watch_row(
    watch: dict[str, Any],
    *,
    watch_created: bool,
    offer_created: bool,
    request_matched: bool,
    price_intelligence: dict[str, str],
) -> dict[str, Any]:
    results = [
        "New watch" if watch_created else "Existing watch",
        "New offer" if offer_created else "Duplicate offer",
    ]
    if request_matched:
        results.append("Request matched")

    return {
        "reference": _display_value(watch.get("reference")),
        "brand": _display_value(watch.get("brand")),
        "price": _format_price(
            watch.get("original_price") or watch.get("price"),
            watch.get("original_currency") or watch.get("currency"),
        ),
        "results": results,
        "rank": price_intelligence["rank"],
        "previous_lowest_usd": price_intelligence["previous_lowest_usd"],
        "price_difference": price_intelligence["price_difference"],
        "price_label": price_intelligence["label"],
        "price_label_class": price_intelligence["label_class"],
    }


def _display_value(value: str | None) -> str:
    if not value:
        return "N/A"
    return value.title() if value.islower() else value


def _format_price(amount: int | None, currency: str | None) -> str:
    if amount is None:
        return "N/A"

    formatted = f"{amount:,}"
    if currency == "USD":
        return f"${formatted}"
    if currency == "EUR":
        return f"€{formatted}"
    if currency == "GBP":
        return f"£{formatted}"
    if currency == "CHF":
        return f"CHF {formatted}"
    if currency == "HKD":
        return f"HK${formatted}"
    if currency:
        return f"{formatted} {currency}"
    return formatted


def _format_processing_time(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def _parse_status(parsed: dict[str, Any]) -> str:
    if parsed["message_type"] == "unknown":
        return "partial"
    if parsed["watches"]:
        return "success"
    return "partial"


def main() -> None:
    try:
        text = read_message()
        summary = ingest_message(text)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Saved {summary['new_offers']} offer(s).")


if __name__ == "__main__":
    main()
