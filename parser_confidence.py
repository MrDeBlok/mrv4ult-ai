"""Per-field parser confidence scores and explanations for the Training Center."""

from __future__ import annotations

from typing import Any

from condition_normalizer import (
    CONDITION_CONFIDENCE_HIGH,
    CONDITION_CONFIDENCE_MEDIUM,
    resolve_offer_wear_condition,
)

Record = dict[str, Any]

PARSER_CONFIDENCE_THRESHOLD = 60

FIELD_KEYS = (
    "brand_confidence",
    "reference_confidence",
    "price_confidence",
    "condition_confidence",
    "intent_confidence",
)


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _brand_confidence(watch: Record) -> tuple[int, str]:
    if watch.get("brand_learned_rule_id"):
        return 95, "Brand set from a learned parser rule."
    if watch.get("brand"):
        if watch.get("reference_high_confidence"):
            return 90, "Brand confirmed from reference knowledge mapping."
        if watch.get("reference_needs_review") or watch.get("reference_status") == "Unknown":
            return 35, "Brand present but reference mapping is uncertain."
        return 75, "Brand extracted from dealer message text."
    if watch.get("unknown_brand_text"):
        return 15, "Dealer used an unrecognized brand alias."
    return 0, "No brand was detected in the message."


def _reference_confidence(watch: Record) -> tuple[int, str]:
    if watch.get("reference_learned_rule_id"):
        return 95, "Reference set from a learned parser rule."
    if watch.get("reference_high_confidence"):
        return 92, "Reference matched canonical brand knowledge."
    if watch.get("reference"):
        if watch.get("reference_needs_review") or watch.get("reference_status") == "Unknown":
            return 30, "Reference text found but knowledge mapping is unknown."
        identification = watch.get("watch_identification") or {}
        if identification.get("reference_status") == "Unknown":
            return 30, "Reference candidate conflicts with known catalog data."
        return 70, "Reference extracted from dealer message text."
    return 0, "No reference number was detected."


def _price_confidence(watch: Record) -> tuple[int, str]:
    from parser_safety_gates import is_suspicious_price

    price = watch.get("original_price") or watch.get("price")
    usd_price = watch.get("usd_price")
    currency = watch.get("original_currency") or watch.get("currency")

    if price is None and usd_price is None:
        return 0, "No offer price was parsed from the message."
    if is_suspicious_price(watch):
        return 10, "Parsed price looks unrealistic or placeholder-like."
    if not currency and usd_price is None:
        return 40, "Price amount found but currency is missing."
    if watch.get("retail_price_only"):
        return 45, "Only a retail/list price was found, not a dealer offer price."
    return 85, "Offer price and currency parsed from dealer text."


def _condition_confidence(watch: Record) -> tuple[int, str]:
    if watch.get("condition_needs_training"):
        term = watch.get("condition_training_term") or "unknown term"
        return 20, f"Condition word '{term}' needs parser training before going live."
    if watch.get("condition_learned_rule_id"):
        return 95, "Condition resolved from a learned parser rule."
    if watch.get("condition_confidence") == CONDITION_CONFIDENCE_HIGH:
        return 90, "Dealer explicitly stated wear condition in the message."
    if watch.get("condition_confidence") == CONDITION_CONFIDENCE_MEDIUM:
        return 55, "Condition inferred as Pre-Owned because dealer did not specify New/Unworn."
    if resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition")):
        return 80, "Wear condition normalized from dealer wording."
    return 0, "Wear condition is missing or unknown."


def _intent_confidence(watch: Record, *, message_type: str | None = None) -> tuple[int, str]:
    resolved_type = message_type or watch.get("message_type") or "unknown"
    if resolved_type == "offer":
        return 85, "Message classified as a dealer sell offer."
    if resolved_type == "request":
        return 80, "Message classified as a buyer request (WTB)."
    if resolved_type == "noise":
        return 90, "Message classified as chat noise with no tradable offer."
    if resolved_type == "unknown":
        return 35, "Message intent could not be classified confidently."
    return 60, f"Message classified as {resolved_type}."


def compute_field_confidences(
    watch: Record,
    *,
    message_type: str | None = None,
) -> dict[str, int]:
    """Return per-field confidence scores (0-100)."""
    brand_score, _ = _brand_confidence(watch)
    reference_score, _ = _reference_confidence(watch)
    price_score, _ = _price_confidence(watch)
    condition_score, _ = _condition_confidence(watch)
    intent_score, _ = _intent_confidence(watch, message_type=message_type)
    return {
        "brand_confidence": _clamp_score(brand_score),
        "reference_confidence": _clamp_score(reference_score),
        "price_confidence": _clamp_score(price_score),
        "condition_confidence": _clamp_score(condition_score),
        "intent_confidence": _clamp_score(intent_score),
    }


def compute_field_explanations(
    watch: Record,
    *,
    message_type: str | None = None,
) -> dict[str, str]:
    """Return human-readable explanations for each parser field."""
    brand_score, brand_expl = _brand_confidence(watch)
    reference_score, reference_expl = _reference_confidence(watch)
    price_score, price_expl = _price_confidence(watch)
    condition_score, condition_expl = _condition_confidence(watch)
    intent_score, intent_expl = _intent_confidence(watch, message_type=message_type)
    return {
        "brand": brand_expl,
        "reference": reference_expl,
        "price": price_expl,
        "condition": condition_expl,
        "intent": intent_expl,
        "brand_confidence": str(brand_score),
        "reference_confidence": str(reference_score),
        "price_confidence": str(price_score),
        "condition_confidence": str(condition_score),
        "intent_confidence": str(intent_score),
    }


def compute_overall_confidence(field_confidences: dict[str, int]) -> int:
    """Weighted overall parser confidence."""
    weights = {
        "brand_confidence": 0.25,
        "reference_confidence": 0.25,
        "price_confidence": 0.2,
        "condition_confidence": 0.15,
        "intent_confidence": 0.15,
    }
    total = 0.0
    for key, weight in weights.items():
        total += field_confidences.get(key, 0) * weight
    return _clamp_score(int(round(total)))


def compute_training_overall_confidence(field_confidences: dict[str, int]) -> int:
    """Overall confidence for parser training — required fields only."""
    weights = {
        "brand_confidence": 1 / 3,
        "reference_confidence": 1 / 3,
        "price_confidence": 1 / 3,
    }
    total = 0.0
    for key, weight in weights.items():
        total += field_confidences.get(key, 0) * weight
    return _clamp_score(int(round(total)))


def attach_parser_confidence_metadata(
    watch: Record,
    *,
    message_type: str | None = None,
) -> Record:
    """Attach per-field confidence, explanations, and overall score to a watch."""
    prior_condition_confidence = watch.get("condition_confidence")
    condition_confidence_label = (
        prior_condition_confidence
        if isinstance(prior_condition_confidence, str)
        else None
    )

    field_confidences = compute_field_confidences(watch, message_type=message_type)
    watch.update(field_confidences)
    watch["condition_confidence_score"] = field_confidences["condition_confidence"]
    if condition_confidence_label:
        watch["condition_confidence_label"] = condition_confidence_label
        watch["condition_confidence"] = condition_confidence_label
    watch["field_explanations"] = compute_field_explanations(watch, message_type=message_type)
    watch["overall_confidence"] = compute_overall_confidence(field_confidences)
    watch["confidence"] = watch["overall_confidence"]
    return watch


def is_training_ready(field_confidences: dict[str, int]) -> bool:
    """Return True when confidence is high enough to leave the training queue."""
    overall = compute_training_overall_confidence(field_confidences)
    return overall >= PARSER_CONFIDENCE_THRESHOLD
