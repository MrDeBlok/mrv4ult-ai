"""Parser Training Center: actionable parser review fixes and single-import reprocess."""

from __future__ import annotations

from typing import Any

from brand_registry import invalidate_brand_registry_cache
from condition_normalizer import normalize_watch_condition, propagate_message_batch_condition, sync_summary_row_conditions
from notification_quick_fix import build_quick_fix_prefill, teach_watch_mapping_from_quick_fix
from parser_review import _parsed_watches, detect_import_issues

Record = dict[str, Any]

FIX_ACTION_PRIORITY: tuple[str, ...] = (
    "unknown_brand",
    "unknown_reference",
    "unknown_model",
    "missing_brand",
    "missing_reference",
    "missing_condition",
    "missing_price",
)

WORKBENCH_CURRENCIES: tuple[str, ...] = (
    "USD",
    "EUR",
    "HKD",
    "CHF",
    "GBP",
    "SGD",
    "AED",
    "JPY",
)

CONDITION_FIX_OPTIONS: tuple[tuple[str, str], ...] = (
    ("New", "New"),
    ("Pre-Owned", "Pre-Owned"),
    ("Unknown", "Unknown"),
)


def determine_primary_fix_action(issues: set[str]) -> str | None:
    """Pick the highest-priority fix action for a parser review row."""
    for action in FIX_ACTION_PRIORITY:
        if action in issues:
            return action
    if "multiple_fields_missing" in issues:
        for action in ("missing_brand", "missing_reference", "missing_condition", "missing_price"):
            if action in issues:
                return action
    return None


def enrich_workbench_row(
    row: Record,
    import_log: Record,
    *,
    message: Record | None = None,
) -> Record:
    """Attach workbench fix metadata to a parser review row."""
    from parser_confidence import attach_parser_confidence_metadata

    summary = import_log.get("summary") or {}
    issues = set(row.get("issues") or [])
    preview = (message or {}).get("raw_text") or row.get("original_message") or ""
    row["primary_fix_action"] = determine_primary_fix_action(issues)
    row["fix_prefill"] = build_quick_fix_prefill(import_log, message_preview=preview)
    row["can_mark_reviewed"] = bool(
        summary.get("workbench_fix_applied") or summary.get("parser_review_ignored")
    )
    row["fix_applied"] = bool(summary.get("workbench_fix_applied"))

    watches = _parsed_watches(import_log)
    training_watches: list[Record] = []
    for watch in watches:
        enriched = attach_parser_confidence_metadata(
            dict(watch),
            message_type=summary.get("message_type"),
        )
        training_watches.append(enriched)

    row["parsed_watches"] = training_watches
    from dealer_list_training import compute_dealer_list_stats
    from ingest import is_large_dealer_list_import_log

    if is_large_dealer_list_import_log(import_log):
        row["is_dealer_list"] = True
        row["dealer_list_stats"] = summary.get("dealer_list_stats") or compute_dealer_list_stats(
            training_watches,
            summary,
            message_type=summary.get("message_type"),
        )
    if training_watches:
        primary = training_watches[0]
        row["field_confidences"] = {
            key: primary.get(key)
            for key in (
                "brand_confidence",
                "reference_confidence",
                "price_confidence",
                "condition_confidence",
                "intent_confidence",
                "overall_confidence",
            )
        }
        row["field_explanations"] = primary.get("field_explanations") or {}
        row["condition_needs_training"] = bool(primary.get("condition_needs_training"))
        row["condition_training_term"] = primary.get("condition_training_term")
        if row["condition_needs_training"]:
            issues.add("condition_needs_training")
            if "condition_needs_training" not in row.get("issues", []):
                row["issues"] = sorted(set(row.get("issues") or []) | {"condition_needs_training"})
    return row


def _parse_workbench_price(value: str) -> int | None:
    from watch_parser import parse_compact_price_amount

    return parse_compact_price_amount(value)


def _apply_field_overrides(watch: Record, overrides: Record) -> None:
    brand = str(overrides.get("brand") or "").strip()
    if brand:
        watch["brand"] = brand

    reference = str(overrides.get("reference") or "").strip()
    if reference:
        watch["reference"] = reference.upper()

    model = str(overrides.get("model") or "").strip()
    if model:
        watch["model"] = model

    condition = overrides.get("condition")
    if condition is not None:
        cond = str(condition).strip()
        if cond.lower() == "unknown":
            watch["condition"] = None
            watch["workbench_condition_unknown"] = True
        elif cond:
            watch["condition"] = cond
            normalize_watch_condition(watch)

    price_raw = str(overrides.get("price") or "").strip()
    if price_raw:
        price = _parse_workbench_price(price_raw)
        if price is not None:
            watch["original_price"] = price
            watch["price"] = price

    currency = str(overrides.get("currency") or "").strip()
    if currency:
        watch["original_currency"] = currency.upper()
        watch["currency"] = currency.upper()

    year_raw = str(overrides.get("production_year") or overrides.get("year") or "").strip()
    if year_raw.isdigit():
        watch["production_year"] = int(year_raw)

    card_date = str(overrides.get("card_date") or "").strip()
    if card_date:
        watch["card_date"] = card_date


def apply_row_field_overrides(watch: Record, overrides: Record) -> Record:
    """Apply per-row dealer-list corrections to one parsed watch."""
    _apply_field_overrides(watch, overrides)
    return watch


def _merge_summary_training_state(summary: Record, old_summary: Record) -> Record:
    for key in (
        "row_corrections",
        "ignored_row_indexes",
        "approved_row_indexes",
        "dealer_list_stats",
    ):
        if key in old_summary:
            summary[key] = old_summary[key]
    return summary


def _store_row_correction(summary: Record, line_index: int, overrides: Record) -> None:
    corrections = dict(summary.get("row_corrections") or {})
    existing = dict(corrections.get(str(line_index)) or {})
    existing.update({key: value for key, value in overrides.items() if value not in (None, "")})
    corrections[str(line_index)] = existing
    summary["row_corrections"] = corrections


def apply_dealer_list_row_fix(
    import_log_id: str,
    line_index: int,
    *,
    brand: str = "",
    reference: str = "",
    condition: str = "",
    production_year: str = "",
    card_date: str = "",
    price: str = "",
    currency: str = "",
) -> Record:
    """Save a per-row dealer-list correction and reprocess the full message."""
    from database import get_import_log, patch_import_log
    from dealer_list_training import compute_dealer_list_stats

    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    summary = dict(import_log.get("summary") or {})
    _store_row_correction(
        summary,
        line_index,
        {
            "brand": brand.strip(),
            "reference": reference.strip(),
            "condition": condition.strip(),
            "production_year": production_year.strip(),
            "card_date": card_date.strip(),
            "price": price.strip(),
            "currency": currency.strip(),
        },
    )
    patch_import_log(import_log_id, summary=summary)
    import_log = reprocess_import_log(import_log_id)
    refreshed_summary = import_log.get("summary") or {}
    watches = list(refreshed_summary.get("offer_watches") or refreshed_summary.get("parsed_watches") or [])
    refreshed_summary["dealer_list_stats"] = compute_dealer_list_stats(
        watches,
        refreshed_summary,
        message_type=refreshed_summary.get("message_type"),
    )
    return patch_import_log(import_log_id, summary=refreshed_summary)


def apply_dealer_list_bulk_action(
    import_log_id: str,
    action: str,
    *,
    row_indexes: list[int],
    brand_name: str = "",
    condition_term: str = "",
    condition_value: str = "",
    teach_scope: str = "global",
    created_by_user_id: str | None = None,
) -> Record:
    """Apply a bulk dealer-list training action to selected rows."""
    from database import get_import_log, mark_import_parser_issue_ignored, patch_import_log
    from dealer_list_training import compute_dealer_list_stats
    from parser_learning import teach_condition_rule

    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")
    if not row_indexes:
        raise ValueError("Select at least one row")

    summary = dict(import_log.get("summary") or {})
    ignored = set(summary.get("ignored_row_indexes") or [])
    approved = set(summary.get("approved_row_indexes") or [])
    cleaned_action = action.strip().lower()

    if cleaned_action == "ignore_rows":
        ignored.update(row_indexes)
        summary["ignored_row_indexes"] = sorted(ignored)
        return patch_import_log(import_log_id, summary=summary)

    if cleaned_action == "approve_rows":
        approved.update(row_indexes)
        summary["approved_row_indexes"] = sorted(approved)
        patch_import_log(import_log_id, summary=summary)
        import_log = reprocess_import_log(import_log_id)
        from parser_training_reprocess import finalize_training_import

        return finalize_training_import(import_log_id)

    if cleaned_action == "apply_brand":
        brand = brand_name.strip()
        if not brand:
            raise ValueError("Brand is required")
        for line_index in row_indexes:
            _store_row_correction(summary, line_index, {"brand": brand})
        patch_import_log(import_log_id, summary=summary)
        import_log = reprocess_import_log(import_log_id)
        refreshed_summary = import_log.get("summary") or {}
        watches = list(refreshed_summary.get("offer_watches") or refreshed_summary.get("parsed_watches") or [])
        refreshed_summary["dealer_list_stats"] = compute_dealer_list_stats(
            watches,
            refreshed_summary,
            message_type=refreshed_summary.get("message_type"),
        )
        return patch_import_log(import_log_id, summary=refreshed_summary)

    if cleaned_action == "teach_condition":
        term = condition_term.strip()
        value = condition_value.strip()
        if not term or not value:
            raise ValueError("Condition term and value are required")
        teach_condition_rule(
            term=term,
            normalized_value=value,
            scope=teach_scope,
            source_import_log_id=import_log_id,
            created_by_user_id=created_by_user_id,
        )
        return reprocess_import_log(import_log_id)

    if cleaned_action == "teach_brand_header":
        term = brand_name.strip()
        if not term:
            raise ValueError("Brand header text is required")
        from database import create_parser_learning_rule, invalidate_parser_learning_rules_cache

        create_parser_learning_rule(
            field_type="brand_header",
            term=term,
            normalized_value=term if not condition_value else condition_value,
            scope=teach_scope,
            source_import_log_id=import_log_id,
            created_by_user_id=created_by_user_id,
        )
        invalidate_parser_learning_rules_cache()
        return reprocess_import_log(import_log_id)

    raise ValueError(f"Unsupported bulk action: {action}")


def reprocess_import_log(
    import_log_id: str,
    *,
    field_overrides: Record | None = None,
) -> Record:
    """Re-parse one import message and refresh its stored parse summary."""
    from database import get_import_log, get_message_by_id, patch_import_log
    from import_classification import (
        is_buyer_request_message,
        split_offer_watches,
    )
    from ingest import (
        _import_status,
        _parse_status,
        enrich_sold_order_watches,
        is_dealer_list_bulk_import,
        is_sold_order_message,
        partition_watches_by_evidence,
        sold_order_has_actionable_identity,
    )
    from watch_knowledge import enrich_parsed_watch
    from watch_parser import parse_message

    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    message_id = import_log.get("message_id")
    if not message_id:
        raise ValueError("Import log has no linked message")

    message = get_message_by_id(str(message_id))
    if message is None:
        raise ValueError("Message not found")

    text = str(message.get("raw_text") or "")
    if not text.strip():
        raise ValueError("Message has no text to reprocess")

    old_summary = dict(import_log.get("summary") or {})

    parsed = parse_message(text)
    parsed_watches = [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parsed["watches"]
    ]
    parsed_watches = propagate_message_batch_condition(text, parsed_watches)
    sold_order_message = is_sold_order_message(text)
    insufficient_evidence_watches: list[Record] = []
    if sold_order_message:
        parsed_watches = enrich_sold_order_watches(parsed_watches)
        offer_watches, import_classification = [], "request_intent"
    else:
        offer_watches, import_classification = split_offer_watches(text, parsed, parsed_watches)
        if import_classification is None and offer_watches:
            from condition_normalizer import apply_inferred_pre_owned_defaults
            from dealer_currency_resolution import apply_dealer_currency_resolution
            from parser_confidence import attach_parser_confidence_metadata
            from parser_learning import prepare_watch_for_ingest

            message = get_message_by_id(str(message_id))
            dealer_id = str((message or {}).get("dealer_id") or "") or None
            group_id = str((message or {}).get("group_id") or "") or None
            dealer_record = None
            dealer_whatsapp = (
                old_summary.get("dealer_whatsapp") or import_log.get("dealer_whatsapp")
            )
            if dealer_id:
                from dealer_currency_resolution import load_dealer_record_for_currency_resolution

                dealer_record = load_dealer_record_for_currency_resolution(dealer_id)
            for watch in offer_watches:
                prepare_watch_for_ingest(
                    watch,
                    message_text=text,
                    dealer_id=dealer_id,
                    group_id=group_id,
                )
                apply_dealer_currency_resolution(
                    watch,
                    dealer=dealer_record,
                    dealer_whatsapp=dealer_whatsapp,
                    message_text=text,
                )
            offer_watches = apply_inferred_pre_owned_defaults(offer_watches)
            for watch in offer_watches:
                attach_parser_confidence_metadata(watch, message_type=parsed.get("message_type"))
        if import_classification is None and offer_watches:
            offer_watches, insufficient_evidence_watches = partition_watches_by_evidence(offer_watches)
            if not offer_watches and insufficient_evidence_watches:
                import_classification = "insufficient_evidence"

    target_watches = offer_watches if offer_watches else parsed_watches
    if field_overrides and target_watches:
        _apply_field_overrides(target_watches[0], field_overrides)
        if not offer_watches and parsed_watches:
            offer_watches = [parsed_watches[0]]

    from dealer_list_training import apply_row_corrections_to_watches, compute_dealer_list_stats

    status_watches = offer_watches if offer_watches else parsed_watches
    status_watches = apply_row_corrections_to_watches(status_watches, old_summary)
    if offer_watches:
        offer_watches = status_watches
    bulk_mode = is_dealer_list_bulk_import(offer_watches)
    parse_status = _parse_status(parsed)

    summary: Record = {
        "messages_imported": 1,
        "watches_parsed": len(status_watches),
        "new_watches": old_summary.get("new_watches", 0),
        "new_offers": old_summary.get("new_offers", 0),
        "duplicate_offers": old_summary.get("duplicate_offers", 0),
        "matched_requests": old_summary.get("matched_requests", 0),
        "group": old_summary.get("group") or import_log.get("group_name"),
        "dealer_whatsapp": old_summary.get("dealer_whatsapp") or import_log.get("dealer_whatsapp"),
        "dealer_alias": old_summary.get("dealer_alias") or import_log.get("dealer_alias"),
        "rows": sync_summary_row_conditions(
            list(old_summary.get("rows") or []),
            status_watches,
        ),
        "bulk_import": bulk_mode,
        "parsed_watches": list(parsed_watches),
        "offer_watches": list(offer_watches),
        "message_type": parsed.get("message_type") or "unknown",
    }
    summary = _merge_summary_training_state(summary, old_summary)
    if bulk_mode and status_watches:
        summary["dealer_list_stats"] = compute_dealer_list_stats(
            status_watches,
            summary,
            message_type=summary.get("message_type"),
        )

    if is_buyer_request_message(text, parsed):
        summary["message_type"] = "request"
    if import_classification:
        summary["import_classification"] = import_classification
    if sold_order_message:
        summary["request_intent_kind"] = "sold_order"
        summary["request_urgency"] = "high"
        if not sold_order_has_actionable_identity(parsed_watches):
            summary["request_intent_needs_review"] = True

    import_status, status_reason = _import_status(
        summary,
        parse_status,
        status_watches,
        classification=import_classification,
        bulk_mode=bulk_mode,
        sold_order_message=sold_order_message,
        sold_order_needs_review=sold_order_message
        and not sold_order_has_actionable_identity(parsed_watches),
    )
    summary["status_reason"] = status_reason
    summary["workbench_fix_applied"] = True
    if field_overrides:
        summary["workbench_corrections"] = dict(field_overrides)

    if import_status == "success":
        summary.pop("parser_review_ignored", None)

    return patch_import_log(
        import_log_id,
        status=import_status,
        watches_parsed=summary["watches_parsed"],
        summary=summary,
    )


def apply_workbench_fix(
    import_log_id: str,
    fix_action: str,
    *,
    brand_name: str = "",
    reference: str = "",
    model: str = "",
    alias_text: str = "",
    condition: str = "",
    price: str = "",
    currency: str = "",
) -> Record:
    """Apply a workbench correction, update knowledge when needed, and reprocess."""
    from database import (
        create_brand_alias,
        create_reference_brand_mapping,
        get_import_log,
        reference_brand_mappings_supported,
        watch_knowledge_supported,
    )
    from watch_knowledge import invalidate_reference_brand_mapping_cache

    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    action = fix_action.strip()
    if action == "unknown_brand":
        alias = alias_text.strip() or str(detect_import_issues(import_log)[2] or "").strip()
        brand = brand_name.strip()
        if not alias:
            raise ValueError("Unknown brand text is required")
        if not brand:
            raise ValueError("Brand is required")
        if watch_knowledge_supported():
            create_brand_alias(alias_key=alias, brand_name=brand, source="parser_workbench")
            invalidate_brand_registry_cache()
        return reprocess_import_log(import_log_id)

    if action == "unknown_reference":
        brand = brand_name.strip()
        ref = reference.strip().upper()
        if not brand:
            raise ValueError("Brand is required")
        if not ref:
            watches = _parsed_watches(import_log)
            watch = watches[0] if watches else {}
            ref = str(watch.get("reference") or "").strip().upper()
        if not ref:
            raise ValueError("Reference is required")
        if watch_knowledge_supported() and reference_brand_mappings_supported():
            create_reference_brand_mapping(
                reference=ref,
                brand_name=brand,
                source="parser_workbench",
            )
            invalidate_reference_brand_mapping_cache()
        return reprocess_import_log(
            import_log_id,
            field_overrides={"brand": brand, "reference": ref},
        )

    if action == "unknown_model":
        brand = brand_name.strip()
        ref = reference.strip().upper()
        alias = alias_text.strip()
        if not brand:
            raise ValueError("Brand is required")
        if not ref:
            raise ValueError("Reference is required")
        if not alias:
            watches = _parsed_watches(import_log)
            watch = watches[0] if watches else {}
            alias = str(watch.get("model") or watch.get("source_line") or "").strip()
        if not alias:
            raise ValueError("Nickname or model text is required")
        _, _, unknown_brand_text = detect_import_issues(import_log)
        teach_watch_mapping_from_quick_fix(
            brand_name=brand,
            reference=ref,
            alias_text=alias,
            unknown_brand_text=unknown_brand_text,
        )
        overrides: Record = {"brand": brand, "reference": ref}
        if model.strip():
            overrides["model"] = model.strip()
        return reprocess_import_log(import_log_id, field_overrides=overrides)

    if action == "missing_brand":
        brand = brand_name.strip()
        if not brand:
            raise ValueError("Brand is required")
        return reprocess_import_log(import_log_id, field_overrides={"brand": brand})

    if action == "missing_reference":
        ref = reference.strip().upper()
        if not ref:
            raise ValueError("Reference is required")
        return reprocess_import_log(import_log_id, field_overrides={"reference": ref})

    if action == "missing_condition":
        cond = condition.strip()
        if not cond:
            raise ValueError("Condition is required")
        return reprocess_import_log(import_log_id, field_overrides={"condition": cond})

    if action == "missing_price":
        if not price.strip():
            raise ValueError("Price is required")
        if not currency.strip():
            raise ValueError("Currency is required")
        return reprocess_import_log(
            import_log_id,
            field_overrides={"price": price.strip(), "currency": currency.strip()},
        )

    raise ValueError(f"Unsupported fix action: {fix_action}")


def _finalize_workbench_fix(import_log_id: str, import_log: Record) -> Record:
    """Mark resolved imports reviewed once reprocess succeeds."""
    from database import mark_import_parser_reviewed
    from import_status import normalize_import_status

    if normalize_import_status(import_log) == "success":
        return mark_import_parser_reviewed(import_log_id)
    return import_log


def apply_workbench_fix_and_finalize(
    import_log_id: str,
    fix_action: str,
    **kwargs: Any,
) -> Record:
    """Apply a workbench fix and finalize the import queue state."""
    from parser_training_reprocess import finalize_training_import

    import_log = apply_workbench_fix(import_log_id, fix_action, **kwargs)
    finalize_training_import(import_log_id)
    return _finalize_workbench_fix(import_log_id, import_log)
