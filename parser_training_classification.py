"""Parser Training row classification — required vs optional fields."""

from __future__ import annotations

from typing import Any

from condition_normalizer import resolve_offer_wear_condition
from parser_confidence import compute_field_confidences, compute_field_explanations

Record = dict[str, Any]

OPTIONAL_NOTE_TEMPLATES: dict[str, str] = {
    "condition": "Condition not provided",
    "card_date": "Card date not provided",
    "year": "Year not provided",
    "dial": "Dial not provided",
    "bracelet": "Bracelet not provided",
    "model": "Model not provided",
}


def _has_resolved_condition(watch: Record) -> bool:
    return bool(
        resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition"))
    )


def collect_optional_notes(watch: Record) -> list[str]:
    """Return neutral notes for missing optional fields (never blocking issues)."""
    notes: list[str] = []

    if watch.get("condition_needs_training"):
        term = watch.get("condition_training_term") or "unknown term"
        notes.append(f"Condition term '{term}' not mapped (optional)")
    elif not _has_resolved_condition(watch):
        notes.append(OPTIONAL_NOTE_TEMPLATES["condition"])

    if not watch.get("card_date"):
        notes.append(OPTIONAL_NOTE_TEMPLATES["card_date"])
    if not watch.get("production_year"):
        notes.append(OPTIONAL_NOTE_TEMPLATES["year"])
    if not watch.get("dial"):
        notes.append(OPTIONAL_NOTE_TEMPLATES["dial"])
    if not watch.get("bracelet"):
        notes.append(OPTIONAL_NOTE_TEMPLATES["bracelet"])
    if not watch.get("model"):
        notes.append(OPTIONAL_NOTE_TEMPLATES["model"])

    return notes


def _field_detail(
    *,
    field: str,
    value: Any,
    confidence: int | str | None,
    status: str,
    reason: str,
) -> Record:
    return {
        "field": field,
        "value": value,
        "confidence": confidence,
        "status": status,
        "reason": reason,
    }


def build_training_field_details(
    watch: Record,
    *,
    message_type: str | None = None,
) -> list[Record]:
    """Per-field breakdown for training row detail panels."""
    explanations = compute_field_explanations(watch, message_type=message_type)
    field_confidences = compute_field_confidences(watch, message_type=message_type)
    optional_notes = collect_optional_notes(watch)
    details: list[Record] = []

    brand_value = watch.get("brand")
    details.append(
        _field_detail(
            field="Brand",
            value=brand_value,
            confidence=field_confidences["brand_confidence"],
            status="detected" if brand_value else "missing",
            reason=explanations["brand"],
        )
    )

    reference_value = watch.get("reference")
    details.append(
        _field_detail(
            field="Reference",
            value=reference_value,
            confidence=field_confidences["reference_confidence"],
            status="detected" if reference_value else "missing",
            reason=explanations["reference"],
        )
    )

    price_value = watch.get("original_price") or watch.get("price")
    currency_value = watch.get("original_currency") or watch.get("currency")
    price_display = (
        f"{price_value} {currency_value}".strip()
        if price_value is not None
        else (f"USD {watch.get('usd_price')}" if watch.get("usd_price") is not None else None)
    )
    details.append(
        _field_detail(
            field="Price",
            value=price_display,
            confidence=field_confidences["price_confidence"],
            status="detected" if price_value is not None or watch.get("usd_price") is not None else "missing",
            reason=explanations["price"],
        )
    )

    condition_optional = OPTIONAL_NOTE_TEMPLATES["condition"] in optional_notes or any(
        note.startswith("Condition term") for note in optional_notes
    )
    if condition_optional:
        details.append(
            _field_detail(
                field="Condition",
                value=watch.get("condition"),
                confidence="N/A",
                status="optional",
                reason="Not provided / optional",
            )
        )
    else:
        details.append(
            _field_detail(
                field="Condition",
                value=watch.get("condition"),
                confidence=field_confidences["condition_confidence"],
                status="detected",
                reason=explanations["condition"],
            )
        )

    return details


def build_training_parser_explanation(
    watch: Record,
    *,
    message_type: str | None = None,
) -> Record:
    """Parser explanation payload stored on parser_training_rows."""
    explanations = compute_field_explanations(watch, message_type=message_type)
    optional_notes = collect_optional_notes(watch)

    if OPTIONAL_NOTE_TEMPLATES["condition"] in optional_notes or any(
        note.startswith("Condition term") for note in optional_notes
    ):
        explanations["condition"] = "Not provided / optional"
        explanations["condition_confidence"] = "N/A"

    explanations["optional_notes"] = optional_notes
    explanations["field_details"] = build_training_field_details(
        watch,
        message_type=message_type,
    )
    explanations["suggestions"] = {
        "suggested_brand": None,
        "suggested_condition": None,
        "suggested_price": None,
        "suggested_action": None,
        "suggestion_confidence": None,
    }
    return explanations


def optional_condition_confidence(watch: Record) -> int | None:
    """Return None (N/A) when condition is an optional missing field."""
    optional_notes = collect_optional_notes(watch)
    if OPTIONAL_NOTE_TEMPLATES["condition"] in optional_notes or any(
        note.startswith("Condition term") for note in optional_notes
    ):
        return None
    from parser_confidence import compute_field_confidences

    return compute_field_confidences(watch).get("condition_confidence")
