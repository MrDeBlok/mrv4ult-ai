"""Market Price confidence and comparable-offer eligibility policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from condition_normalizer import (
    CONDITION_CONFIDENCE_HIGH,
    CONDITION_SOURCE_EXPLICIT,
    CONDITION_SOURCE_INFERRED_DEFAULT,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    resolve_offer_wear_condition,
)

Record = dict[str, Any]

MARKET_PRICE_CONFIDENCE_THRESHOLD = 85

MARKET_PRICE_WEIGHTS: dict[str, int] = {
    "trusted_reference": 35,
    "explicit_condition": 20,
    "year": 15,
    "valid_price": 20,
    "supported_currency": 10,
}

SUPPORTED_MARKET_CURRENCIES = frozenset({"USD", "HKD", "EUR", "CHF", "GBP", "SGD", "AED", "JPY"})

MARKET_PRICE_APPROVED_STATUSES = frozenset({"approved", "valid", "corrected"})
MARKET_PRICE_BLOCKED_STATUSES = frozenset(
    {
        "pending_review",
        "ignored",
        "rejected",
        "failed",
    }
)


@dataclass(frozen=True)
class MarketPriceEligibility:
    """Derived Market Price confidence and eligibility decision."""

    market_price_confidence: int
    eligible: bool
    exclusion_reasons: tuple[str, ...] = ()
    component_scores: dict[str, int] = field(default_factory=dict)
    parser_confidence: int | None = None
    threshold: int = MARKET_PRICE_CONFIDENCE_THRESHOLD


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _merge_market_context(*sources: Record | None) -> Record:
    merged: Record = {}
    for source in sources:
        if not source:
            continue
        merged.update(source)
    return merged


def _reference_value(context: Record) -> str | None:
    reference = context.get("reference")
    if reference is None:
        return None
    cleaned = str(reference).strip()
    if not cleaned or cleaned.upper() in {"N/A", "UNKNOWN"}:
        return None
    return cleaned


def is_reference_trusted_for_market_price(context: Record) -> bool:
    """Return True when a reference is trusted enough for Market Price."""
    reference = _reference_value(context)
    if not reference:
        return False
    if context.get("reference_needs_review") or context.get("reference_status") == "Unknown":
        return False
    if has_reference_brand_conflict(context):
        return False
    if context.get("reviewed_by_human") or context.get("human_verified"):
        return True
    if context.get("reference_high_confidence") or context.get("reference_learned_rule_id"):
        return True

    from watch_knowledge import resolve_reference_brand_identity

    _brand, confident = resolve_reference_brand_identity(reference)
    if confident:
        return True

    brand = context.get("brand")
    return bool(brand and str(brand).strip())


def has_explicit_market_condition(context: Record) -> bool:
    """Return True when wear condition is explicitly New or Pre-Owned."""
    condition = resolve_offer_wear_condition(
        context.get("condition"),
        context.get("raw_condition"),
    )
    if condition not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return False
    if context.get("reviewed_by_human") or context.get("human_verified"):
        return True
    if context.get("condition_source") == CONDITION_SOURCE_INFERRED_DEFAULT:
        return False
    if context.get("condition_explicit") is True:
        return True
    if context.get("condition_confidence") == CONDITION_CONFIDENCE_HIGH:
        return True
    if context.get("condition_source") == CONDITION_SOURCE_EXPLICIT:
        return True
    if context.get("condition_learned_rule_id"):
        return True
    # Active DB offers often only store normalized condition text.
    return condition in {NEW_CONDITION, PRE_OWNED_CONDITION}


def _has_valid_market_price(context: Record) -> bool:
    from parser_safety_gates import is_suspicious_price

    price = context.get("original_price")
    if price is None:
        price = context.get("price")
    usd_price = context.get("usd_price")
    if is_suspicious_price(context):
        return False
    if isinstance(price, (int, float)) and int(price) > 0:
        return True
    return isinstance(usd_price, (int, float)) and int(usd_price) > 0


def _has_supported_market_currency(context: Record) -> bool:
    currency = context.get("original_currency") or context.get("currency")
    if not currency:
        return isinstance(context.get("usd_price"), (int, float)) and int(context.get("usd_price")) > 0
    return str(currency).strip().upper() in SUPPORTED_MARKET_CURRENCIES


def _has_normalized_comparison_price(context: Record) -> bool:
    usd_price = context.get("usd_price")
    return isinstance(usd_price, (int, float)) and int(usd_price) > 0


def _has_market_year(context: Record) -> bool:
    year = context.get("production_year")
    if isinstance(year, int) and 1900 <= year <= 2035:
        return True
    card_date = context.get("card_date")
    if isinstance(card_date, str):
        import re

        match = re.search(r"/(\d{4})\b", card_date)
        if match:
            year_value = int(match.group(1))
            return 1900 <= year_value <= 2035
    return False


def _training_status(context: Record) -> str | None:
    for key in ("training_status", "parser_training_status"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def has_reference_brand_conflict(context: Record) -> bool:
    """Return True when reference and brand clearly conflict."""
    if context.get("reference_brand_conflict") or context.get("reference_brand_mismatch"):
        return True
    reference = _reference_value(context)
    brand = context.get("brand")
    if not reference or not brand:
        return False
    from brand_resolver import reference_confidently_conflicts_with_brand

    return reference_confidently_conflicts_with_brand(reference, str(brand).strip())


def compute_market_price_confidence(context: Record) -> tuple[int, dict[str, int]]:
    """Compute Market Price confidence from core market fields only."""
    component_scores = {
        "trusted_reference": (
            MARKET_PRICE_WEIGHTS["trusted_reference"]
            if is_reference_trusted_for_market_price(context)
            else 0
        ),
        "explicit_condition": (
            MARKET_PRICE_WEIGHTS["explicit_condition"]
            if has_explicit_market_condition(context)
            else 0
        ),
        "year": MARKET_PRICE_WEIGHTS["year"] if _has_market_year(context) else 0,
        "valid_price": MARKET_PRICE_WEIGHTS["valid_price"] if _has_valid_market_price(context) else 0,
        "supported_currency": (
            MARKET_PRICE_WEIGHTS["supported_currency"]
            if _has_supported_market_currency(context)
            else 0
        ),
    }
    total = sum(component_scores.values())
    return _clamp_score(total), component_scores


def evaluate_market_price_eligibility(
    watch_or_row: Record,
    *,
    extra_context: Record | None = None,
    threshold: int = MARKET_PRICE_CONFIDENCE_THRESHOLD,
) -> MarketPriceEligibility:
    """Evaluate whether an offer may be used as a Market Price comparable."""
    context = _merge_market_context(watch_or_row, extra_context)
    confidence, component_scores = compute_market_price_confidence(context)
    parser_confidence = context.get("confidence")
    if not isinstance(parser_confidence, int):
        parser_confidence = context.get("overall_confidence")
        if not isinstance(parser_confidence, int):
            parser_confidence = None

    reasons: list[str] = []

    training_status = _training_status(context)
    if training_status:
        if training_status in MARKET_PRICE_BLOCKED_STATUSES:
            reasons.append(f"status_{training_status}")
        elif training_status not in MARKET_PRICE_APPROVED_STATUSES:
            reasons.append(f"status_not_approved:{training_status}")
    elif context.get("status") in MARKET_PRICE_BLOCKED_STATUSES:
        reasons.append(f"status_{context.get('status')}")

    if not _reference_value(context):
        reasons.append("reference_missing")
    elif not is_reference_trusted_for_market_price(context):
        reasons.append("reference_not_trusted")

    if context.get("reference_needs_review") or context.get("reference_status") == "Unknown":
        reasons.append("reference_pending_review")

    if has_reference_brand_conflict(context):
        reasons.append("reference_brand_conflict")

    if not has_explicit_market_condition(context):
        condition = resolve_offer_wear_condition(
            context.get("condition"),
            context.get("raw_condition"),
        )
        if condition not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
            reasons.append("condition_unknown")
        elif context.get("condition_source") == CONDITION_SOURCE_INFERRED_DEFAULT:
            reasons.append("condition_inferred")
        else:
            reasons.append("condition_not_explicit")

    if not _has_market_year(context):
        reasons.append("year_missing")

    if not _has_valid_market_price(context):
        reasons.append("price_invalid")

    from parser_safety_gates import is_suspicious_price

    if is_suspicious_price(context):
        reasons.append("suspicious_price")

    if not _has_supported_market_currency(context):
        reasons.append("currency_unsupported")

    if not _has_normalized_comparison_price(context):
        reasons.append("normalized_usd_price_missing")

    if confidence < threshold:
        reasons.append(f"market_price_confidence_below_threshold:{confidence}<{threshold}")

    eligible = not reasons
    return MarketPriceEligibility(
        market_price_confidence=confidence,
        eligible=eligible,
        exclusion_reasons=tuple(reasons),
        component_scores=component_scores,
        parser_confidence=parser_confidence,
        threshold=threshold,
    )


def build_market_price_debug(
    watch_or_row: Record,
    *,
    extra_context: Record | None = None,
    threshold: int = MARKET_PRICE_CONFIDENCE_THRESHOLD,
) -> Record:
    """Return admin/debug metadata for Market Price decisions."""
    evaluation = evaluate_market_price_eligibility(
        watch_or_row,
        extra_context=extra_context,
        threshold=threshold,
    )
    return {
        "parser_confidence": evaluation.parser_confidence,
        "market_price_confidence": evaluation.market_price_confidence,
        "market_price_eligible": evaluation.eligible,
        "market_price_exclusion_reasons": list(evaluation.exclusion_reasons),
        "market_price_threshold": evaluation.threshold,
        "market_price_component_scores": dict(evaluation.component_scores),
    }


def attach_market_price_metadata(
    watch: Record,
    *,
    extra_context: Record | None = None,
    threshold: int = MARKET_PRICE_CONFIDENCE_THRESHOLD,
) -> Record:
    """Attach derived Market Price confidence without altering parser confidence."""
    evaluation = evaluate_market_price_eligibility(
        watch,
        extra_context=extra_context,
        threshold=threshold,
    )
    watch["market_price_confidence"] = evaluation.market_price_confidence
    watch["market_price_eligible"] = evaluation.eligible
    watch["market_price_exclusion_reasons"] = list(evaluation.exclusion_reasons)
    watch["market_price_component_scores"] = dict(evaluation.component_scores)
    watch["market_price_threshold"] = evaluation.threshold
    return watch


def offer_record_to_market_context(offer: Record) -> Record:
    """Build a market-price evaluation context from an offers row."""
    watch = offer.get("watches")
    if isinstance(watch, list):
        watch = watch[0] if watch else {}
    if not isinstance(watch, dict):
        watch = {}

    context: Record = {
        "reference": watch.get("reference") or offer.get("reference"),
        "brand": watch.get("brand") or offer.get("brand"),
        "model": watch.get("model") or offer.get("model"),
        "condition": offer.get("condition"),
        "production_year": offer.get("production_year"),
        "card_date": offer.get("card_date"),
        "original_price": offer.get("original_price"),
        "original_currency": offer.get("original_currency"),
        "usd_price": offer.get("usd_price"),
        "offer_id": offer.get("id"),
    }
    return context


def is_market_price_comparable_context(context: Record) -> bool:
    """Return True when a comparable offer passes the centralized eligibility policy."""
    return evaluate_market_price_eligibility(context).eligible


def filter_market_eligible_offer_rows(
    rows: list[Record],
) -> list[Record]:
    """Return offer rows eligible for Market Price comparable selection."""
    eligible_rows: list[Record] = []
    for row in rows:
        context = offer_record_to_market_context(row)
        if is_market_price_comparable_context(context):
            eligible_rows.append(row)
    return eligible_rows


def filter_market_eligible_comparable_pool(
    pool: list[tuple[str, int, str | None]],
    *,
    row_contexts: dict[str, Record],
) -> list[tuple[str, int, str | None]]:
    """Filter a comparable pool using per-offer market contexts keyed by offer id."""
    filtered: list[tuple[str, int, str | None]] = []
    for offer_id, usd_price, condition in pool:
        context = row_contexts.get(offer_id)
        if context is None:
            continue
        if is_market_price_comparable_context(context):
            filtered.append((offer_id, usd_price, condition))
    return filtered
