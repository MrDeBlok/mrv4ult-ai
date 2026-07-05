"""Search CLI for active watch offers in MRV4ULT AI."""

from __future__ import annotations

import re
import sys
from typing import Any

from database import contact_type_column_supported, get_client, is_business_dealer_relation
from watch_identifier import expand_search_token, is_reference_like_token
from condition_normalizer import (
    display_condition,
    offer_condition_display,
    offer_matches_condition_filter,
)

Record = dict[str, Any]
WatchGroup = dict[str, Any]
SEARCH_OFFERS_PAGE_SIZE = 1000

WATCH_SEARCH_FIELDS = ("brand", "reference", "model", "dial", "bracelet")

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
    offers, _total_count = _load_active_offers_for_search()
    matches = _filter_search_offers(
        offers,
        tokens=tokens,
        max_usd_price=max_usd_price,
        condition=condition,
    )
    return matches, cheapest_only


def _search_dealer_fields() -> str:
    return (
        "dealers(display_name, contact_type)"
        if contact_type_column_supported()
        else "dealers(display_name, whatsapp_id)"
    )


def _load_active_offers_for_search() -> tuple[list[Record], int | None]:
    """Load active offers for search, paginating past PostgREST row limits."""
    watch_fields = ", ".join(WATCH_SEARCH_FIELDS)
    select_fields = (
        "id, dealer_id, watch_id, original_price, original_currency, usd_price, card_date, condition, "
        "messages(id), "
        f"watches({watch_fields}), "
        f"{_search_dealer_fields()}"
    )

    loaded: list[Record] = []
    total_count: int | None = None
    offset = 0

    while True:
        request = (
            get_client()
            .table("offers")
            .select(select_fields, count="exact" if offset == 0 else None)
            .eq("status", "active")
            .range(offset, offset + SEARCH_OFFERS_PAGE_SIZE - 1)
        )
        response = request.execute()
        if offset == 0:
            total_count = response.count
        batch = response.data or []
        loaded.extend(batch)
        if len(batch) < SEARCH_OFFERS_PAGE_SIZE:
            break
        offset += SEARCH_OFFERS_PAGE_SIZE

    return loaded, total_count


def _resolve_search_watch(
    offer: Record,
    *,
    cache: dict[str, Record],
) -> Record:
    raw = offer.get("watches")
    if raw:
        return _nested_record(raw)

    watch_id = str(offer.get("watch_id") or "")
    if not watch_id:
        return {}

    if watch_id in cache:
        return cache[watch_id]

    from database import get_watch_by_id

    watch = get_watch_by_id(watch_id)
    if watch:
        cache[watch_id] = {field: watch.get(field) for field in WATCH_SEARCH_FIELDS}
    else:
        cache[watch_id] = {}
    return cache[watch_id]


def _resolve_search_dealer(
    offer: Record,
    *,
    cache: dict[str, Record],
) -> Record:
    raw = offer.get("dealers")
    if raw:
        return _nested_record(raw)

    dealer_id = str(offer.get("dealer_id") or "")
    if not dealer_id:
        return {}

    if dealer_id in cache:
        return cache[dealer_id]

    from database import get_dealer_by_id

    dealer = get_dealer_by_id(dealer_id)
    if dealer:
        cache[dealer_id] = {
            "display_name": dealer.get("display_name"),
            "contact_type": dealer.get("contact_type"),
            "whatsapp_id": dealer.get("whatsapp_id"),
        }
    else:
        cache[dealer_id] = {}
    return cache[dealer_id]


def _filter_search_offers(
    offers: list[Record],
    *,
    tokens: list[str],
    max_usd_price: int | None,
    condition: str | None,
) -> list[Record]:
    matches: list[Record] = []
    watch_cache: dict[str, Record] = {}
    dealer_cache: dict[str, Record] = {}

    for offer in offers:
        dealer = _resolve_search_dealer(offer, cache=dealer_cache)
        if not is_business_dealer_relation(dealer, has_offers=True):
            continue

        watch = _resolve_search_watch(offer, cache=watch_cache)
        if not _watch_matches_tokens(watch, tokens):
            continue
        if not _offer_within_max_usd_price(offer, max_usd_price):
            continue
        if not offer_matches_condition_filter(offer.get("condition"), condition):
            continue

        message = _nested_record(offer.get("messages"))
        offer["message_id"] = message.get("id")
        offer["watch"] = watch
        offer["dealer"] = dealer
        matches.append(offer)

    return matches


def trace_search_query(
    query: str,
    *,
    condition: str | None = None,
    offers: list[Record] | None = None,
    total_count: int | None = None,
) -> Record:
    """Return staged search filter counts and metadata for one query."""
    tokens, max_usd_price, cheapest_only = parse_query(query)
    if offers is None:
        offers, total_count = _load_active_offers_for_search()

    watch_cache: dict[str, Record] = {}
    dealer_cache: dict[str, Record] = {}
    after_dealer: list[Record] = []
    after_reference: list[Record] = []
    after_price: list[Record] = []
    after_condition: list[Record] = []

    for offer in offers:
        dealer = _resolve_search_dealer(offer, cache=dealer_cache)
        if not is_business_dealer_relation(dealer, has_offers=True):
            continue
        after_dealer.append(offer)

        watch = _resolve_search_watch(offer, cache=watch_cache)
        if not _watch_matches_tokens(watch, tokens):
            continue
        after_reference.append(offer)

        if not _offer_within_max_usd_price(offer, max_usd_price):
            continue
        after_price.append(offer)

        if not offer_matches_condition_filter(offer.get("condition"), condition):
            continue
        after_condition.append(offer)

    normalized_tokens = [
        {
            "token": token,
            "reference_like": is_reference_like_token(token),
            "normalized": _normalize_search_reference(token),
        }
        for token in tokens
    ]

    return {
        "query": query,
        "tokens": tokens,
        "normalized_tokens": normalized_tokens,
        "condition_filter": condition,
        "max_usd_price": max_usd_price,
        "cheapest_only_requested": cheapest_only,
        "active_offers_loaded": len(offers),
        "active_offers_total": total_count,
        "search_row_limit_truncated": (
            total_count is not None and len(offers) < int(total_count)
        ),
        "counts": {
            "loaded": len(offers),
            "after_dealer_visibility": len(after_dealer),
            "after_reference_matching": len(after_reference),
            "after_max_price": len(after_price),
            "after_condition_filter": len(after_condition),
            "final": len(after_condition),
        },
    }


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


def _normalize_search_reference(value: str | None) -> str:
    """Normalize a reference or token for strict substring matching."""
    if not value or not isinstance(value, str):
        return ""
    return re.sub(r"[\s\-/.]", "", value.strip()).upper()


def _reference_contains_token(reference: str | None, token: str) -> bool:
    """Return True when the normalized reference contains the normalized token."""
    normalized_reference = _normalize_search_reference(reference)
    normalized_token = _normalize_search_reference(token)
    if not normalized_reference or not normalized_token:
        return False
    return normalized_token in normalized_reference


def _watch_matches_tokens(watch: Record, tokens: list[str]) -> bool:
    if not tokens:
        return True
    return all(_token_matches_watch(token, watch) for token in tokens)


def _token_matches_watch(token: str, watch: Record) -> bool:
    if is_reference_like_token(token):
        return _reference_contains_token(watch.get("reference"), token)

    expanded = expand_search_token(token)
    brand_alias = BRAND_ALIASES.get(token.lower())
    if brand_alias:
        expanded.add(brand_alias.lower())

    term_alias = TERM_ALIASES.get(token.lower())
    if term_alias:
        expanded.add(term_alias.lower())

    fields = [
        watch.get("brand"),
        watch.get("reference"),
        watch.get("model"),
        watch.get("dial"),
        watch.get("bracelet"),
    ]

    for field in fields:
        if not field:
            continue
        field_lower = field.lower()
        field_compact = re.sub(r"[\s\-/]", "", field_lower)
        for term in expanded:
            term_compact = re.sub(r"[\s\-/]", "", term)
            if term in field_lower or term in field_compact:
                return True
            if term_compact and (term_compact in field_compact or field_compact.startswith(term_compact)):
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
