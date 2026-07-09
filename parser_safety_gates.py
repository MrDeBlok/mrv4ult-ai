"""Safety gates that block uncertain parser output from entering live Search."""

from __future__ import annotations

from typing import Any

from parser_confidence import compute_field_confidences, is_training_ready

Record = dict[str, Any]

BRAND_CONFIDENCE_MIN = 50
REFERENCE_CONFIDENCE_MIN = 50
INTENT_CONFIDENCE_MIN = 50

SUSPICIOUS_USD_THRESHOLDS = {
    "absurd_low": 100,
    "absurd_high": 5_000_000,
}


def _coerce_price(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def is_suspicious_price(watch: Record) -> bool:
    """Detect placeholder or unrealistic parsed prices."""
    price = _coerce_price(watch.get("original_price") or watch.get("price"))
    usd_price = _coerce_price(watch.get("usd_price"))
    currency = str(watch.get("original_currency") or watch.get("currency") or "").upper()

    if price is not None and price <= 3:
        return True
    if usd_price is not None and usd_price <= SUSPICIOUS_USD_THRESHOLDS["absurd_low"]:
        return True
    if usd_price is not None and usd_price >= SUSPICIOUS_USD_THRESHOLDS["absurd_high"]:
        return True

    if currency == "HKD" and price is not None and price <= 10:
        return True
    if currency == "USD" and price is not None and price <= 1:
        return True

    market_price = watch.get("market_reference_usd")
    effective_usd = usd_price
    if effective_usd is None and currency == "USD" and price is not None:
        effective_usd = price
    if (
        market_price
        and effective_usd is not None
        and market_price > 0
        and (
            effective_usd < market_price * 0.01
            or effective_usd > market_price * 100
        )
    ):
        return True
    return False


def has_brand_reference_conflict(watch: Record) -> bool:
    """Detect brand/reference mismatches that should not go live."""
    if watch.get("reference_needs_review"):
        return True
    if watch.get("reference_status") == "Unknown" and watch.get("brand") and watch.get("reference"):
        return True
    identification = watch.get("watch_identification") or {}
    if identification.get("reference_status") == "Unknown" and watch.get("reference"):
        return True
    model_alias = watch.get("model_alias") or {}
    if model_alias.get("reference_status") == "Unknown" and watch.get("reference"):
        return True
    return False


def evaluate_offer_safety(
    watch: Record,
    *,
    message_type: str | None = None,
) -> tuple[bool, list[str]]:
    """Return whether an offer should be blocked and why."""
    reasons: list[str] = []
    field_confidences = compute_field_confidences(watch, message_type=message_type)

    if field_confidences["brand_confidence"] < BRAND_CONFIDENCE_MIN:
        reasons.append("brand_confidence_low")
    if field_confidences["reference_confidence"] < REFERENCE_CONFIDENCE_MIN:
        reasons.append("reference_confidence_low")

    price = watch.get("original_price") or watch.get("price")
    usd_price = watch.get("usd_price")
    currency = str(watch.get("original_currency") or watch.get("currency") or "").strip()
    if price is None and usd_price is None:
        reasons.append("missing_price")
    elif is_suspicious_price(watch):
        reasons.append("suspicious_price")
    elif not currency and usd_price is None:
        reasons.append("missing_currency")

    if has_brand_reference_conflict(watch):
        reasons.append("brand_reference_conflict")

    if not watch.get("brand"):
        reasons.append("missing_brand")
    if not watch.get("reference"):
        reasons.append("missing_reference")
    if watch.get("reference_needs_review") or watch.get("reference_status") == "Unknown":
        if "unknown_reference" not in reasons:
            reasons.append("unknown_reference")

    resolved_type = message_type or watch.get("message_type") or "unknown"
    if resolved_type == "request":
        reasons.append("request_intent")
    elif resolved_type == "noise":
        reasons.append("not_sell_offer")
    elif resolved_type not in ("offer", "unknown") and field_confidences["intent_confidence"] < INTENT_CONFIDENCE_MIN:
        reasons.append("intent_uncertain")

    blocked = bool(reasons)
    return blocked, reasons


def should_block_active_offer(
    watch: Record,
    *,
    message_type: str | None = None,
) -> bool:
    """Return True when a parsed watch must not create an active Search offer."""
    blocked, _ = evaluate_offer_safety(watch, message_type=message_type)
    return blocked


def watch_passes_training_gates(
    watch: Record,
    *,
    message_type: str | None = None,
) -> bool:
    """Return True when a reprocessed watch can leave the training queue."""
    blocked, _ = evaluate_offer_safety(watch, message_type=message_type)
    if blocked:
        return False
    field_confidences = compute_field_confidences(watch, message_type=message_type)
    return is_training_ready(field_confidences)
