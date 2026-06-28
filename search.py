"""Search CLI for active watch offers in MRV4ULT AI."""

from __future__ import annotations

import re
import sys
from typing import Any

from database import contact_type_column_supported, get_client, is_business_dealer_relation
from condition_normalizer import (
    display_condition,
    offer_condition_display,
    offer_matches_condition_filter,
)

Record = dict[str, Any]
WatchGroup = dict[str, Any]

BRAND_ALIASES: dict[str, str] = {
    "rolex": "Rolex",
    "rlx": "Rolex",
    "patek": "Patek Philippe",
    "pp": "Patek Philippe",
    "ap": "Audemars Piguet",
    "audemars": "Audemars Piguet",
    "rm": "Richard Mille",
    "richard": "Richard Mille",
    "fpj": "FP Journe",
    "fp": "FP Journe",
}

PRICE_FILTER_WORDS = frozenset({"under", "below", "max"})

TERM_ALIASES: dict[str, str] = {
    "jub": "jubilee",
    "oys": "oyster",
    "black": "black",
    "blue": "blue",
    "green": "green",
    "white": "white",
}


def read_query() -> str:
    query = input("Search: ").strip()
    if not query:
        print("Error: empty search query.", file=sys.stderr)
        sys.exit(1)
    return query


def search_offers(
    query: str,
    *,
    condition: str | None = None,
) -> tuple[list[Record], bool]:
    """Search active offers by watch fields matching all query tokens."""
    tokens, max_usd_price, cheapest_only = parse_query(query)
    dealer_fields = (
        "dealers(display_name, contact_type)"
        if contact_type_column_supported()
        else "dealers(display_name, whatsapp_id)"
    )
    response = (
        get_client()
        .table("offers")
        .select(
            "watch_id, original_price, original_currency, usd_price, card_date, condition, "
            f"watches(brand, reference, dial, bracelet), {dealer_fields}"
        )
        .eq("status", "active")
        .execute()
    )

    matches: list[Record] = []
    for offer in response.data or []:
        if not is_business_dealer_relation(offer.get("dealers")):
            continue
        watch = _nested_record(offer.get("watches"))
        if not _watch_matches_tokens(watch, tokens):
            continue
        if not _offer_within_max_usd_price(offer, max_usd_price):
            continue
        if not offer_matches_condition_filter(offer.get("condition"), condition):
            continue
        offer["watch"] = watch
        offer["dealer"] = _nested_record(offer.get("dealers"))
        matches.append(offer)

    return matches, cheapest_only


def parse_query(query: str) -> tuple[list[str], int | None, bool]:
    """Split a search query into watch tokens, optional max USD price, and cheapest mode."""
    parts = re.split(r"\s+", query.strip())
    tokens: list[str] = []
    max_usd_price: int | None = None
    cheapest_only = False
    index = 0

    while index < len(parts):
        word = parts[index].lower()
        if word == "cheapest":
            cheapest_only = True
            index += 1
            continue
        if word in PRICE_FILTER_WORDS:
            if index + 1 >= len(parts):
                raise ValueError(f"Missing price after '{word}'.")
            max_usd_price = _parse_max_usd_price(parts[index + 1])
            index += 2
            continue
        tokens.append(parts[index])
        index += 1

    return tokens, max_usd_price, cheapest_only


def _parse_max_usd_price(value: str) -> int:
    cleaned = value.replace(",", "").strip()
    if not cleaned.isdigit():
        raise ValueError(f"Invalid max USD price: {value}")
    return int(cleaned)


def _offer_within_max_usd_price(offer: Record, max_usd_price: int | None) -> bool:
    if max_usd_price is None:
        return True
    usd_price = offer.get("usd_price")
    if usd_price is None:
        return False
    return usd_price <= max_usd_price


def group_offers_by_watch(
    offers: list[Record],
    *,
    cheapest_only: bool = False,
) -> list[WatchGroup]:
    """Group matching offers by watch_id with price statistics."""
    grouped: dict[str, list[Record]] = {}
    for offer in offers:
        watch_id = offer.get("watch_id")
        if not watch_id:
            continue
        grouped.setdefault(watch_id, []).append(offer)

    groups: list[WatchGroup] = []
    for watch_id, watch_offers in grouped.items():
        watch_offers.sort(key=_sort_key_usd_price)
        if cheapest_only:
            cheapest_offer = _pick_cheapest_offer(watch_offers)
            watch_offers = [cheapest_offer] if cheapest_offer else []

        usd_prices = [
            price for price in (offer.get("usd_price") for offer in watch_offers) if price is not None
        ]
        if not watch_offers:
            continue

        watch = watch_offers[0].get("watch") or {}
        groups.append(
            {
                "watch_id": watch_id,
                "watch": watch,
                "offers": watch_offers,
                "lowest_usd": min(usd_prices) if usd_prices else None,
                "average_usd": round(sum(usd_prices) / len(usd_prices)) if usd_prices else None,
                "highest_usd": max(usd_prices) if usd_prices else None,
                "offer_count": len(watch_offers),
            }
        )

    groups.sort(key=lambda group: (group["lowest_usd"] is None, group["lowest_usd"] or 0))
    return groups


def _pick_cheapest_offer(offers: list[Record]) -> Record | None:
    if not offers:
        return None
    priced_offers = [offer for offer in offers if offer.get("usd_price") is not None]
    if priced_offers:
        return min(priced_offers, key=lambda offer: offer["usd_price"])
    return offers[0]


def _nested_record(value: Any) -> Record:
    if isinstance(value, list):
        return value[0] if value else {}
    if isinstance(value, dict):
        return value
    return {}


def _watch_matches_tokens(watch: Record, tokens: list[str]) -> bool:
    if not tokens:
        return True
    return all(_token_matches_watch(token, watch) for token in tokens)


def _token_matches_watch(token: str, watch: Record) -> bool:
    token_lower = token.lower()
    expanded = {token_lower, TERM_ALIASES.get(token_lower, token_lower)}

    brand_alias = BRAND_ALIASES.get(token_lower)
    if brand_alias:
        expanded.add(brand_alias.lower())

    fields = [
        watch.get("brand"),
        watch.get("reference"),
        watch.get("dial"),
        watch.get("bracelet"),
    ]

    for field in fields:
        if not field:
            continue
        field_lower = field.lower()
        field_compact = re.sub(r"\s+", "", field_lower)
        for term in expanded:
            if term in field_lower or term in field_compact:
                return True
    return False


def _sort_key_usd_price(offer: Record) -> tuple[int, int]:
    usd_price = offer.get("usd_price")
    if usd_price is None:
        return (1, 0)
    return (0, usd_price)


def format_price(amount: int | None, currency: str | None) -> str:
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


def format_usd_price(amount: int | None) -> str:
    if amount is None:
        return "N/A"
    return f"${amount:,}"


def _display_value(value: str | None) -> str:
    if not value:
        return "N/A"
    return value.title() if value.islower() else value


def print_watch_group(group: WatchGroup) -> None:
    watch = group["watch"]

    print(f"Brand: {_display_value(watch.get('brand'))}")
    print(f"Reference: {_display_value(watch.get('reference'))}")
    print(f"Dial: {_display_value(watch.get('dial'))}")
    print(f"Bracelet: {_display_value(watch.get('bracelet'))}")
    print(f"Lowest USD: {format_usd_price(group['lowest_usd'])}")
    print(f"Average USD: {format_usd_price(group['average_usd'])}")
    print(f"Highest USD: {format_usd_price(group['highest_usd'])}")
    print(f"Active offers: {group['offer_count']}")
    print("Offers:")

    for offer in group["offers"]:
        original = format_price(offer.get("original_price"), offer.get("original_currency"))
        usd_price = format_usd_price(offer.get("usd_price"))
        dealer_name = offer.get("dealer", {}).get("display_name") or "Unknown dealer"
        card_date = offer.get("card_date") or "N/A"
        condition = display_condition(offer.get("condition"))
        print(
            f"  - Original: {original} | USD price: {usd_price} | "
            f"Dealer: {dealer_name} | Card: {card_date} | Condition: {condition}"
        )


def main() -> None:
    try:
        query = read_query()
        offers, cheapest_only = search_offers(query)
        groups = group_offers_by_watch(offers, cheapest_only=cheapest_only)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not groups:
        print("No matching offers found.")
        return

    for index, group in enumerate(groups):
        if index:
            print()
        print_watch_group(group)


if __name__ == "__main__":
    main()
