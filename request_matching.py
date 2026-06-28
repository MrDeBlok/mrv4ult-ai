"""Offer-to-request matching rules for client wanted lists."""

from __future__ import annotations

import re
from typing import Any

from condition_normalizer import normalize_wear_condition
from watch_identifier import identify_text

EXCHANGE_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "HKD": 0.128,
    "EUR": 1.08,
    "CHF": 1.12,
    "GBP": 1.27,
    "SGD": 0.74,
    "AED": 0.272,
    "JPY": 0.0064,
}


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def normalize_reference(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper().replace(" ", "").replace("-", "")
    return cleaned or None


def extract_offer_year(offer: dict[str, Any]) -> int | None:
    production_year = offer.get("production_year")
    if isinstance(production_year, int):
        return production_year

    card_date = offer.get("card_date")
    if isinstance(card_date, str):
        match = re.search(r"/(\d{4})", card_date)
        if match:
            return int(match.group(1))
        match = re.search(r"\b(19|20)\d{2}\b", card_date)
        if match:
            return int(match.group(0))
    return None


def extract_offer_alias(offer: dict[str, Any]) -> str | None:
    nickname = offer.get("nickname")
    if isinstance(nickname, str) and nickname.strip():
        return nickname.strip()

    model_alias = offer.get("model_alias")
    if isinstance(model_alias, dict):
        for key in ("nickname", "alias"):
            value = model_alias.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _price_within_budget(offer: dict[str, Any], request: dict[str, Any]) -> bool:
    max_price = request.get("max_price")
    if max_price is None:
        return True

    request_currency = (request.get("currency") or "USD").upper()
    offer_price = offer.get("original_price") or offer.get("price")
    offer_currency = (offer.get("original_currency") or offer.get("currency") or "USD").upper()

    if offer_price is None:
        offer_usd = offer.get("usd_price")
        if offer_usd is None:
            return False
        max_usd = _convert_amount(int(max_price), request_currency, "USD")
        return max_usd is not None and offer_usd <= max_usd

    if offer_currency == request_currency:
        return int(offer_price) <= int(max_price)

    offer_usd = offer.get("usd_price")
    if offer_usd is None:
        converted = _convert_amount(int(offer_price), offer_currency, request_currency)
        return converted is not None and converted <= int(max_price)

    max_usd = _convert_amount(int(max_price), request_currency, "USD")
    return max_usd is not None and offer_usd <= max_usd


def _convert_amount(amount: int, from_currency: str, to_currency: str) -> int | None:
    from_rate = EXCHANGE_RATES_TO_USD.get(from_currency.upper())
    to_rate = EXCHANGE_RATES_TO_USD.get(to_currency.upper())
    if from_rate is None or to_rate is None:
        return None
    usd_amount = amount * from_rate
    return int(round(usd_amount / to_rate))


def _year_within_range(offer: dict[str, Any], request: dict[str, Any]) -> bool:
    min_year = request.get("min_year")
    max_year = request.get("max_year")
    if min_year is None and max_year is None:
        return True

    offer_year = extract_offer_year(offer)
    if offer_year is None:
        return False

    if min_year is not None and offer_year < int(min_year):
        return False
    if max_year is not None and offer_year > int(max_year):
        return False
    return True


def _dial_matches(offer: dict[str, Any], request: dict[str, Any]) -> bool:
    request_dial = normalize_text(request.get("dial"))
    if request_dial is None:
        return True
    return normalize_text(offer.get("dial")) == request_dial


def _wear_condition(value: str | None) -> str | None:
    normalized, _ = normalize_wear_condition(value)
    return normalized


def _condition_matches(offer: dict[str, Any], request: dict[str, Any]) -> bool:
    request_condition = _wear_condition(request.get("condition"))
    if request_condition is None:
        return True
    offer_condition = _wear_condition(offer.get("condition"))
    if offer_condition is None:
        return False
    return offer_condition == request_condition


def _brand_matches(offer: dict[str, Any], request: dict[str, Any]) -> bool:
    request_brand = normalize_text(request.get("brand"))
    if request_brand is None:
        return True
    return normalize_text(offer.get("brand")) == request_brand


def _model_or_alias_matches(offer: dict[str, Any], request: dict[str, Any]) -> bool:
    request_model = normalize_text(request.get("model"))
    request_alias = normalize_text(request.get("alias"))
    if request_model is None and request_alias is None:
        return False

    offer_model = normalize_text(offer.get("model"))
    offer_alias = normalize_text(extract_offer_alias(offer))

    if request_model is not None and offer_model == request_model:
        return True
    if request_alias is not None and offer_alias == request_alias:
        return True
    if request_alias is not None and offer_model == request_alias:
        return True
    if request_model is not None and offer_alias == request_model:
        return True
    return False


def _request_identification_text(request: dict[str, Any]) -> str:
    parts = [request.get("alias"), request.get("model"), request.get("reference")]
    return " ".join(str(part) for part in parts if part)


def _offer_matches_request_identification(
    offer: dict[str, Any],
    request: dict[str, Any],
) -> tuple[bool, float]:
    """Return True when an offer reference matches a nickname-identified request."""
    if normalize_reference(request.get("reference")):
        return False, 0.0

    text = _request_identification_text(request)
    if not text.strip():
        return False, 0.0

    identification = identify_text(text)
    if not identification:
        return False, 0.0

    offer_reference = normalize_reference(offer.get("reference"))
    if not offer_reference:
        return False, 0.0

    likely_references = [
        normalize_reference(reference)
        for reference in identification.get("likely_references") or []
    ]
    likely_references = [reference for reference in likely_references if reference]
    if offer_reference not in likely_references:
        return False, 0.0

    return True, float(identification.get("confidence") or 0.85)


def evaluate_request_match(
    offer: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, str] | None:
    """Return match metadata when an offer satisfies a request."""
    if not _price_within_budget(offer, request):
        return None
    if not _year_within_range(offer, request):
        return None
    if not _dial_matches(offer, request):
        return None
    if not _condition_matches(offer, request):
        return None

    request_reference = normalize_reference(request.get("reference"))
    offer_reference = normalize_reference(offer.get("reference"))

    if request_reference:
        if offer_reference and offer_reference == request_reference:
            if not _brand_matches(offer, request):
                return None
            return {
                "request_id": str(request["id"]),
                "match_strength": "strong",
                "match_reason": f"Reference match: {request.get('reference')}",
            }
        return None

    if not _brand_matches(offer, request):
        return None

    identified, confidence = _offer_matches_request_identification(offer, request)
    if identified:
        label = request.get("alias") or request.get("model") or request.get("brand")
        strength = "strong" if confidence >= 0.9 else "medium"
        return {
            "request_id": str(request["id"]),
            "match_strength": strength,
            "match_reason": (
                f"Nickname identification match: {label} → {offer.get('reference')}"
            ),
        }

    if not _model_or_alias_matches(offer, request):
        return None

    label = request.get("alias") or request.get("model") or request.get("brand")
    return {
        "request_id": str(request["id"]),
        "match_strength": "medium",
        "match_reason": f"Brand and model/alias match: {label}",
    }


def match_offer_against_requests(
    offer: dict[str, Any],
    requests: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Return all request matches for a single offer."""
    matches: list[dict[str, str]] = []
    for request in requests:
        if (request.get("status") or "").lower() not in {"open", "active"}:
            continue
        result = evaluate_request_match(offer, request)
        if result:
            matches.append(result)
    return matches


def _core_match_without_budget(
    offer: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, str] | None:
    """Return match metadata when an offer satisfies a request except budget."""
    if not _year_within_range(offer, request):
        return None
    if not _dial_matches(offer, request):
        return None
    if not _condition_matches(offer, request):
        return None

    request_reference = normalize_reference(request.get("reference"))
    offer_reference = normalize_reference(offer.get("reference"))

    if request_reference:
        if offer_reference and offer_reference == request_reference:
            if not _brand_matches(offer, request):
                return None
            return {
                "request_id": str(request["id"]),
                "match_strength": "strong",
                "match_reason": f"Reference match: {request.get('reference')}",
            }
        return None

    if not _brand_matches(offer, request):
        return None

    identified, confidence = _offer_matches_request_identification(offer, request)
    if identified:
        label = request.get("alias") or request.get("model") or request.get("brand")
        strength = "strong" if confidence >= 0.9 else "medium"
        return {
            "request_id": str(request["id"]),
            "match_strength": strength,
            "match_reason": (
                f"Nickname identification match: {label} → {offer.get('reference')}"
            ),
        }

    if not _model_or_alias_matches(offer, request):
        return None

    label = request.get("alias") or request.get("model") or request.get("brand")
    return {
        "request_id": str(request["id"]),
        "match_strength": "medium",
        "match_reason": f"Brand and model/alias match: {label}",
    }


def _sourcing_bonus_points(offer: dict[str, Any], request: dict[str, Any]) -> int:
    bonus = 0
    if normalize_text(request.get("dial")) and _dial_matches(offer, request):
        bonus += 5
    if _wear_condition(request.get("condition")) and _condition_matches(offer, request):
        bonus += 5
    if _price_within_budget(offer, request):
        bonus += 5
    return bonus


def match_badge_class(badge: str) -> str:
    return {
        "Excellent Match": "success",
        "Good Match": "primary",
        "Budget Exceeded": "warning",
    }.get(badge, "secondary")


def evaluate_sourcing_match(
    offer: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any] | None:
    """Return sourcing match metadata with score and badge for client workspace."""
    result = evaluate_request_match(offer, request)
    if result:
        strength = result["match_strength"]
        score = 100 if strength == "strong" else 75
        score += _sourcing_bonus_points(offer, request)
        badge = "Excellent Match" if strength == "strong" else "Good Match"
        return {
            **result,
            "match_score": score,
            "match_badge": badge,
            "match_badge_class": match_badge_class(badge),
            "budget_exceeded": False,
        }

    core = _core_match_without_budget(offer, request)
    if core and not _price_within_budget(offer, request):
        score = 45 if core["match_strength"] == "strong" else 35
        score += _sourcing_bonus_points(offer, request)
        badge = "Budget Exceeded"
        return {
            **core,
            "match_reason": f"{core['match_reason']} (exceeds budget)",
            "match_score": score,
            "match_badge": badge,
            "match_badge_class": match_badge_class(badge),
            "budget_exceeded": True,
        }
    return None
