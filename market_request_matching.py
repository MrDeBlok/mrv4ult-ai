"""Instant offer matching for market request detail pages."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from condition_normalizer import offer_condition_display
from database import list_active_offers_for_market_matching
from dealer_intelligence import dealer_display_name, format_activity_timestamp
from request_matching import extract_offer_alias, normalize_reference, normalize_text
from search import BRAND_ALIASES, _display_value, _nested_record, _token_matches_watch, format_price, format_usd_price
from user_visibility import can_view_contact
from watch_identifier import identify_text
from watch_knowledge import enrich_parsed_watch

Record = dict[str, Any]


def extract_market_request_criteria(import_log: Record) -> Record:
    """Extract watch search criteria from a market request import log."""
    from market_requests import _primary_watch, _watch_nickname

    watch = _primary_watch(import_log)
    return {
        "brand": watch.get("brand"),
        "reference": watch.get("reference"),
        "model": watch.get("model"),
        "nickname": _watch_nickname(watch),
        "model_alias": watch.get("model_alias"),
    }


def _brands_equivalent(left: str | None, right: str | None) -> bool:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True

    left_canonical = BRAND_ALIASES.get(left_norm, left)
    right_canonical = BRAND_ALIASES.get(right_norm, right)
    if left_canonical and right_canonical:
        return normalize_text(left_canonical) == normalize_text(right_canonical)
    return False


def _has_meaningful_reference(value: str | None) -> bool:
    if not value:
        return False
    cleaned = str(value).strip()
    return bool(cleaned) and cleaned.upper() != "N/A"


def _reference_matches_market_request(criteria: Record, offer_watch: Record) -> bool:
    request_reference = normalize_reference(criteria.get("reference"))
    offer_reference = normalize_reference(offer_watch.get("reference"))
    if not request_reference or not offer_reference:
        return False
    if request_reference != offer_reference:
        return False
    request_brand = criteria.get("brand")
    if request_brand and str(request_brand).strip() and str(request_brand).upper() != "N/A":
        return _brands_equivalent(str(request_brand), offer_watch.get("brand"))
    return True


def _alias_matches_market_request(criteria: Record, offer_watch: Record) -> bool:
    request_brand = criteria.get("brand")
    if request_brand and not _brands_equivalent(str(request_brand), offer_watch.get("brand")):
        return False

    identification_text = " ".join(
        str(part)
        for part in (
            criteria.get("nickname"),
            criteria.get("model"),
            criteria.get("brand"),
        )
        if part
    ).strip()
    if identification_text:
        identification = identify_text(identification_text)
        likely_references = [
            normalize_reference(reference)
            for reference in (identification or {}).get("likely_references") or []
        ]
        likely_references = [reference for reference in likely_references if reference]
        offer_reference = normalize_reference(offer_watch.get("reference"))
        if offer_reference and offer_reference in likely_references:
            return True

    request_nickname = normalize_text(criteria.get("nickname"))
    request_model = normalize_text(criteria.get("model"))
    offer_model = normalize_text(offer_watch.get("model"))
    offer_alias = normalize_text(
        extract_offer_alias(
            {
                "nickname": offer_watch.get("nickname"),
                "model_alias": offer_watch.get("model_alias"),
            }
        )
    )

    if request_nickname and request_nickname in {offer_model, offer_alias}:
        return True
    if request_model and request_model in {offer_model, offer_alias}:
        return True

    nickname = criteria.get("nickname")
    if isinstance(nickname, str) and nickname.strip():
        return _token_matches_watch(nickname.strip(), offer_watch)

    model = criteria.get("model")
    if isinstance(model, str) and model.strip():
        return _token_matches_watch(model.strip(), offer_watch)

    return False


def offer_matches_market_request(criteria: Record, offer: Record) -> bool:
    """Return True when an active offer matches a market request."""
    offer_watch = enrich_parsed_watch(_nested_record(offer.get("watches")))

    if _has_meaningful_reference(criteria.get("reference")):
        return _reference_matches_market_request(criteria, offer_watch)

    if not criteria.get("brand") and not criteria.get("nickname") and not criteria.get("model"):
        return False

    return _alias_matches_market_request(criteria, offer_watch)


def classify_market_request_match(criteria: Record, offer: Record) -> str | None:
    """Return match quality: exact_reference, alias, or None."""
    if not offer_matches_market_request(criteria, offer):
        return None

    offer_watch = enrich_parsed_watch(_nested_record(offer.get("watches")))
    if _has_meaningful_reference(criteria.get("reference")) and _reference_matches_market_request(
        criteria,
        offer_watch,
    ):
        return "exact_reference"
    return "alias"


def can_view_matching_offer(user: Record | None, offer: Record) -> bool:
    """Apply contact visibility rules to a candidate matching offer."""
    if user is None:
        return False

    dealer = _nested_record(offer.get("dealers"))
    if not dealer:
        return False
    return can_view_contact(user, dealer)


def filter_matching_offers_for_user(offers: list[Record], user: Record | None) -> list[Record]:
    return [offer for offer in offers if can_view_matching_offer(user, offer)]


def _offer_country(offer: Record, dealer: Record, group: Record) -> str:
    return _display_value(dealer.get("country") or group.get("country"))


def build_market_request_matching_row(offer: Record) -> Record:
    """Format one matching offer row for the market request detail page."""
    dealer = _nested_record(offer.get("dealers"))
    watch = _nested_record(offer.get("watches"))
    message = _nested_record(offer.get("messages"))
    group = _nested_record(message.get("groups"))
    condition_label, _ = offer_condition_display(offer.get("condition"))
    received_at = message.get("received_at")
    asking_price = format_price(offer.get("original_price"), offer.get("original_currency"))
    if asking_price == "N/A":
        asking_price = format_usd_price(offer.get("usd_price"))

    return {
        "offer_id": offer.get("id"),
        "watch_id": offer.get("watch_id"),
        "dealer_id": offer.get("dealer_id"),
        "dealer_name": dealer_display_name(dealer),
        "asking_price": asking_price,
        "net_price": asking_price,
        "retail_price": "—",
        "condition": condition_label,
        "country": _offer_country(offer, dealer, group),
        "import_date": format_activity_timestamp(received_at),
        "last_seen": format_activity_timestamp(received_at),
        "offer_url": f"/watch/{offer.get('watch_id')}",
        "_received_at_raw": received_at or "",
        "_usd_price_sort": (
            1,
            0,
        )
        if offer.get("usd_price") is None
        else (0, int(offer.get("usd_price"))),
    }


def sort_market_request_matching_rows(rows: list[Record]) -> list[Record]:
    """Sort matches newest first, then lowest USD price."""

    def _sort_key(row: Record) -> tuple[float, float]:
        raw = row.get("_received_at_raw")
        timestamp = 0.0
        if raw:
            try:
                timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except ValueError:
                timestamp = 0.0
        price_key = row.get("_usd_price_sort", (1, 0))
        price = float(price_key[1]) if price_key[0] == 0 else float("inf")
        return (-timestamp, price)

    rows.sort(key=_sort_key)
    for row in rows:
        row.pop("_received_at_raw", None)
        row.pop("_usd_price_sort", None)
    return rows


def find_matching_offers_for_market_request(
    user: Record | None,
    import_log: Record,
    *,
    offers: list[Record] | None = None,
) -> list[Record]:
    """Return visible active offers matching a market request."""
    criteria = extract_market_request_criteria(import_log)
    candidate_offers = offers if offers is not None else list_active_offers_for_market_matching()
    visible_offers = filter_matching_offers_for_user(candidate_offers, user)

    matches: list[Record] = []
    for offer in visible_offers:
        if offer_matches_market_request(criteria, offer):
            matches.append(build_market_request_matching_row(offer))

    return sort_market_request_matching_rows(matches)
