"""Canonical final-offer payload builder for parser training corrections."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from condition_normalizer import (
    CONDITION_CONFIDENCE_HIGH,
    CONDITION_SOURCE_EXPLICIT,
    normalize_condition_value,
)

Record = dict[str, Any]

AUDIT_KEY = "audit"
CORRECTABLE_FIELDS = (
    "brand",
    "reference",
    "model",
    "case_material",
    "edition",
    "dial_variant",
    "size_mm",
    "condition",
    "production_year",
    "year",
    "card_date",
    "price",
    "currency",
    "condition_term",
)


def training_row_audit(row: Record) -> Record:
    explanation = row.get("parser_explanation") or {}
    audit = explanation.get(AUDIT_KEY)
    return dict(audit) if isinstance(audit, dict) else {}


def is_human_reviewed_training_row(row: Record) -> bool:
    audit = training_row_audit(row)
    if audit.get("reviewed_by_human"):
        return True
    return str(row.get("status") or "") in {"corrected", "approved", "valid"}


def original_parser_confidence(row: Record) -> int | None:
    audit = training_row_audit(row)
    original = audit.get("original_confidence_overall")
    if original is not None:
        try:
            return int(round(float(original)))
        except (TypeError, ValueError):
            return None
    confidence = row.get("confidence_overall")
    if confidence is None:
        return None
    try:
        return int(round(float(confidence)))
    except (TypeError, ValueError):
        return None


def _detected_fields_from_row(row: Record) -> Record:
    return {
        "brand": row.get("detected_brand"),
        "reference": row.get("detected_reference"),
        "condition": row.get("detected_condition"),
        "production_year": (
            int(row["detected_year"])
            if str(row.get("detected_year") or "").isdigit()
            else row.get("detected_year")
        ),
        "card_date": row.get("detected_card_date"),
        "original_price": row.get("detected_price"),
        "price": row.get("detected_price"),
        "original_currency": row.get("detected_currency"),
        "currency": row.get("detected_currency"),
    }


def _normalized_fields_from_row(row: Record) -> Record:
    audit = training_row_audit(row)
    snapshot = audit.get("final_offer_snapshot") or {}
    year_value = snapshot.get("production_year")
    if year_value is None:
        year_value = row.get("detected_year")
    if str(year_value or "").isdigit():
        year_value = int(str(year_value))
    return {
        "brand": row.get("normalized_brand"),
        "reference": row.get("normalized_reference"),
        "condition": row.get("normalized_condition"),
        "production_year": year_value if isinstance(year_value, int) else None,
        "card_date": snapshot.get("card_date") or row.get("detected_card_date"),
        "original_price": snapshot.get("original_price") or row.get("detected_price"),
        "price": snapshot.get("original_price") or row.get("detected_price"),
        "original_currency": snapshot.get("original_currency") or row.get("detected_currency"),
        "currency": snapshot.get("original_currency") or row.get("detected_currency"),
        "usd_price": row.get("usd_price"),
    }


def _merge_field_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def build_final_offer_payload(
    row: Record,
    corrections: Record | None = None,
) -> Record:
    """Merge parser output, saved normalized values, and manual overrides."""
    detected = _detected_fields_from_row(row)
    normalized = _normalized_fields_from_row(row)
    payload: Record = {
        "brand": _merge_field_value(normalized.get("brand"), detected.get("brand")),
        "reference": _merge_field_value(normalized.get("reference"), detected.get("reference")),
        "condition": _merge_field_value(normalized.get("condition"), detected.get("condition")),
        "production_year": _merge_field_value(normalized.get("production_year"), detected.get("production_year")),
        "card_date": _merge_field_value(normalized.get("card_date"), detected.get("card_date")),
        "original_price": _merge_field_value(normalized.get("original_price"), detected.get("original_price")),
        "price": _merge_field_value(normalized.get("price"), detected.get("price")),
        "original_currency": _merge_field_value(
            normalized.get("original_currency"),
            detected.get("original_currency"),
        ),
        "currency": _merge_field_value(normalized.get("currency"), detected.get("currency")),
        "usd_price": normalized.get("usd_price"),
        "source_line": row.get("raw_row_text"),
        "row_index": row.get("row_index"),
        "offer_id": row.get("created_offer_id"),
    }

    if corrections:
        from parser_workbench import apply_row_field_overrides

        apply_row_field_overrides(payload, corrections)
        if corrections.get("year"):
            year = str(corrections["year"]).strip()
            if year.isdigit():
                payload["production_year"] = int(year)
        if corrections.get("card_date"):
            payload["card_date"] = str(corrections["card_date"]).strip()
        for field in ("model", "case_material", "edition", "dial_variant"):
            if corrections.get(field) not in (None, ""):
                payload[field] = str(corrections[field]).strip()
        if str(corrections.get("size_mm") or "").strip().isdigit():
            payload["size_mm"] = int(str(corrections["size_mm"]).strip())

    from fpj_model_knowledge import apply_fpj_enrichment, build_model_identity_key, fpj_storage_identity_fields

    source_text = str(payload.get("source_line") or row.get("raw_row_text") or "")
    payload = apply_fpj_enrichment(payload, source_text)
    payload["model_identity_key"] = build_model_identity_key(payload)
    identity = fpj_storage_identity_fields(payload)
    payload["dial"] = identity.get("dial") or payload.get("dial")

    recalculate_offer_usd_price(payload)
    return payload


def recalculate_offer_usd_price(watch: Record) -> None:
    """Recompute USD normalization from the final offer price and currency."""
    from watch_parser import EXCHANGE_RATES_TO_USD

    original_price = watch.get("original_price")
    if original_price is None:
        original_price = watch.get("price")
    currency = watch.get("original_currency") or watch.get("currency")
    if original_price is None or not currency:
        watch["usd_price"] = None
        watch["exchange_rate_to_usd"] = None
        return

    amount = int(original_price)
    currency_code = str(currency).strip().upper()
    rate = EXCHANGE_RATES_TO_USD.get(currency_code)
    watch["original_price"] = amount
    watch["price"] = amount
    watch["original_currency"] = currency_code
    watch["currency"] = currency_code
    watch["exchange_rate_to_usd"] = rate
    watch["usd_price"] = int(round(amount * rate)) if rate is not None else None


def collect_corrected_field_names(
    row: Record,
    corrections: Record,
) -> list[str]:
    """Return field names that differ from the current final payload."""
    if not corrections:
        return list(training_row_audit(row).get("corrected_fields") or [])

    before = build_final_offer_payload(row)
    after = build_final_offer_payload(row, corrections)
    changed: list[str] = []
    field_map = {
        "brand": "brand",
        "reference": "reference",
        "condition": "condition",
        "year": "production_year",
        "production_year": "production_year",
        "card_date": "card_date",
        "price": "original_price",
        "currency": "original_currency",
    }
    for key, payload_key in field_map.items():
        if corrections.get(key) in (None, ""):
            continue
        if before.get(payload_key) != after.get(payload_key):
            changed.append(payload_key)
    return sorted(set(changed))


def apply_manual_review_trust(watch: Record, corrections: Record) -> Record:
    """Mark admin-corrected values as verified for downstream safety checks."""
    updated = dict(watch)
    manual_reference = bool(str(corrections.get("reference") or "").strip())
    manual_brand = bool(str(corrections.get("brand") or "").strip())
    manual_condition = bool(str(corrections.get("condition") or "").strip())
    manual_price = bool(str(corrections.get("price") or "").strip())
    manual_currency = bool(str(corrections.get("currency") or "").strip())

    if manual_reference:
        updated.pop("reference_needs_review", None)
        updated.pop("reference_status", None)

    if manual_brand:
        updated.pop("unknown_brand_text", None)

    if manual_brand and manual_reference:
        from brand_resolver import reference_confidently_conflicts_with_brand

        reference = str(updated.get("reference") or "").strip()
        brand = str(updated.get("brand") or "").strip()
        if reference and brand and reference_confidently_conflicts_with_brand(reference, brand):
            updated["reference_brand_conflict"] = {
                "reference": reference,
                "rejected_brand": brand,
                "source": "manual_correction",
            }
            updated.pop("reference_high_confidence", None)
        else:
            updated.pop("reference_brand_conflict", None)
            updated.pop("reference_brand_mismatch", None)
            updated["reference_high_confidence"] = True

    if manual_condition:
        normalized = normalize_condition_value(updated.get("condition"))
        updated["condition"] = normalized
        updated["condition_source"] = CONDITION_SOURCE_EXPLICIT
        updated["condition_explicit"] = True
        updated["condition_confidence"] = CONDITION_CONFIDENCE_HIGH

    if manual_price or manual_currency:
        updated.pop("suspicious_price", None)

    updated["reviewed_by_human"] = True
    updated["human_verified"] = True
    return updated


def build_training_row_audit(
    row: Record,
    *,
    corrections: Record,
    created_by_user_id: str | None,
    final_watch: Record,
    market_price_debug: Record,
) -> Record:
    """Build audit metadata stored under parser_explanation.audit."""
    existing = training_row_audit(row)
    original_confidence = existing.get("original_confidence_overall")
    if original_confidence is None:
        row_confidence = row.get("confidence_overall")
        if row_confidence is not None:
            try:
                original_confidence = int(round(float(row_confidence)))
            except (TypeError, ValueError):
                original_confidence = None
    if original_confidence is None:
        original_confidence = original_parser_confidence(row)

    corrected_fields = collect_corrected_field_names(row, corrections)
    prior_corrected = list(existing.get("corrected_fields") or [])
    merged_corrected = sorted(set(prior_corrected + corrected_fields))

    audit = {
        **existing,
        "original_confidence_overall": existing.get("original_confidence_overall", original_confidence),
        "original_detected": existing.get("original_detected") or _detected_fields_from_row(row),
        "reviewed_by_human": True,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": created_by_user_id,
        "corrected_fields": merged_corrected,
        "approval_status": "corrected",
        "market_price_confidence": market_price_debug.get("market_price_confidence"),
        "market_price_eligible": market_price_debug.get("market_price_eligible"),
        "market_price_exclusion_reasons": market_price_debug.get("market_price_exclusion_reasons"),
        "market_price_threshold": market_price_debug.get("market_price_threshold"),
        "final_offer_snapshot": {
            "brand": final_watch.get("brand"),
            "reference": final_watch.get("reference"),
            "model": final_watch.get("model"),
            "case_material": final_watch.get("case_material"),
            "edition": final_watch.get("edition"),
            "dial_variant": final_watch.get("dial_variant"),
            "size_mm": final_watch.get("size_mm"),
            "model_identity_key": final_watch.get("model_identity_key"),
            "condition": final_watch.get("condition"),
            "production_year": final_watch.get("production_year"),
            "card_date": final_watch.get("card_date"),
            "original_price": final_watch.get("original_price"),
            "original_currency": final_watch.get("original_currency"),
            "usd_price": final_watch.get("usd_price"),
        },
    }
    return audit


def final_offer_training_context(row: Record, watch: Record) -> Record:
    """Attach training-row audit flags to a final offer watch for downstream checks."""
    audit = training_row_audit(row)
    context = dict(watch)
    if audit.get("reviewed_by_human") or is_human_reviewed_training_row(row):
        context["reviewed_by_human"] = True
        context["human_verified"] = True
        context["parser_training_status"] = row.get("status")
    return context
