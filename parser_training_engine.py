"""Offer-centric parser training engine — one row per detected offer."""

from __future__ import annotations

import logging
import time
from typing import Any

from parser_confidence import (
    attach_parser_confidence_metadata,
    compute_field_confidences,
    compute_training_overall_confidence,
)
from parser_safety_gates import evaluate_offer_safety, should_block_active_offer, watch_passes_training_gates
from parser_training_classification import (
    build_training_parser_explanation,
    optional_condition_confidence,
)

Record = dict[str, Any]

TRAINING_ROW_STATUSES = frozenset({
    "pending_review",
    "approved",
    "valid",
    "corrected",
    "ignored",
    "failed",
})

APPROVED_CONTAINER_STATUSES = frozenset({"approved", "valid", "corrected"})

REFERENCE_REEVALUATE_BATCH_SIZE = 50

LEARN_MODES = frozenset({"row_only", "global", "dealer", "group"})

AI_SUGGESTION_DEFAULTS: Record = {
    "suggested_brand": None,
    "suggested_condition": None,
    "suggested_price": None,
    "suggested_action": None,
    "suggestion_confidence": None,
}


def _watch_to_detected_fields(watch: Record) -> Record:
    return {
        "detected_brand": watch.get("brand"),
        "detected_reference": watch.get("reference"),
        "detected_condition": watch.get("condition"),
        "detected_year": str(watch.get("production_year") or "") or None,
        "detected_card_date": watch.get("card_date"),
        "detected_price": watch.get("original_price") or watch.get("price"),
        "detected_currency": watch.get("original_currency") or watch.get("currency"),
    }


def _watch_to_audit_detected_fields(watch: Record) -> Record:
    return {
        "brand": watch.get("brand"),
        "reference": watch.get("reference"),
        "condition": watch.get("condition"),
        "year": str(watch.get("production_year") or "") or None,
        "card_date": watch.get("card_date"),
        "price": watch.get("original_price") or watch.get("price"),
        "currency": watch.get("original_currency") or watch.get("currency"),
    }


def _normalized_fields_from_watch(watch: Record) -> Record:
    return {
        "normalized_brand": watch.get("brand"),
        "normalized_reference": watch.get("reference"),
        "normalized_condition": watch.get("condition"),
        "usd_price": watch.get("usd_price"),
    }


def apply_reference_brand_mapping_to_watch(watch: Record) -> Record:
    """Fill brand from reference knowledge or learned reference_brand_mappings."""
    updated = dict(watch)
    reference = updated.get("reference")
    if not reference:
        return updated

    brand = updated.get("brand")
    if (
        brand
        and updated.get("reference_high_confidence")
        and not updated.get("reference_needs_review")
        and updated.get("reference_status") != "Unknown"
    ):
        return updated

    from brand_resolver import (
        BRAND_SOURCE_REFERENCE,
        BrandResolution,
        apply_brand_resolution_to_watch,
    )
    from watch_knowledge import resolve_reference_brand_identity

    mapped_brand, confident = resolve_reference_brand_identity(reference)
    if not mapped_brand or not confident:
        return updated

    if brand and brand != mapped_brand:
        if updated.get("brand_source") in {"explicit"} or updated.get("brand_learned_rule_id"):
            return updated

    resolution = BrandResolution(
        brand=mapped_brand,
        source=BRAND_SOURCE_REFERENCE,
        priority=1,
    )
    updated = apply_brand_resolution_to_watch(updated, resolution)
    updated.pop("reference_needs_review", None)
    updated.pop("reference_status", None)
    return updated


def _apply_manual_correction_trust(watch: Record, corrections: Record) -> Record:
    """Treat explicit Save Row field edits as trusted parser-training input."""
    updated = dict(watch)
    manual_reference = bool(str(corrections.get("reference") or "").strip())
    manual_brand = bool(str(corrections.get("brand") or "").strip())

    if manual_reference:
        updated["reference_high_confidence"] = True
        updated.pop("reference_needs_review", None)
        updated.pop("reference_status", None)
        identification = dict(updated.get("watch_identification") or {})
        identification.pop("reference_status", None)
        if identification:
            updated["watch_identification"] = identification
        else:
            updated.pop("watch_identification", None)
        model_alias = dict(updated.get("model_alias") or {})
        model_alias.pop("reference_status", None)
        if model_alias:
            updated["model_alias"] = model_alias
        else:
            updated.pop("model_alias", None)

    if manual_brand:
        updated.pop("unknown_brand_text", None)

    if manual_brand and manual_reference:
        updated.pop("reference_brand_conflict", None)

    return updated


def enrich_watch_for_training_evaluation(
    watch: Record,
    *,
    message_type: str | None = None,
) -> Record:
    """Apply reference mappings and confidence metadata before safety checks."""
    updated = apply_reference_brand_mapping_to_watch(dict(watch))
    attach_parser_confidence_metadata(updated, message_type=message_type)
    from market_price_confidence import attach_market_price_metadata

    attach_market_price_metadata(updated)
    return updated


def _resolve_training_row_status(
    row: Record,
    *,
    blocked: bool,
    issue_types: list[str],
) -> str:
    existing = str(row.get("status") or "pending_review")
    if existing == "ignored":
        return "ignored"
    if existing == "failed" and blocked:
        return "failed"
    if blocked:
        return "pending_review"
    if row.get("created_offer_id") and existing == "corrected":
        return "corrected"
    return "approved"


def _training_row_update_fields(
    row: Record,
    watch: Record,
    *,
    message_type: str | None = None,
    blocked: bool,
    issue_types: list[str],
    preserve_parser_confidence: bool = False,
    audit: Record | None = None,
) -> Record:
    field_confidences = compute_field_confidences(watch, message_type=message_type)
    status = _resolve_training_row_status(row, blocked=blocked, issue_types=issue_types)
    explanations = build_training_parser_explanation(watch, message_type=message_type)
    if audit:
        explanations = {**explanations, "audit": audit}

    updates: Record = {
        "normalized_brand": watch.get("brand"),
        "normalized_reference": watch.get("reference"),
        "normalized_condition": watch.get("condition"),
        "usd_price": watch.get("usd_price"),
        "parser_explanation": explanations,
        "status": status,
        "issue_types": issue_types,
    }
    if not preserve_parser_confidence:
        updates.update(
            {
                "confidence_overall": compute_training_overall_confidence(field_confidences),
                "confidence_brand": field_confidences.get("brand_confidence"),
                "confidence_reference": field_confidences.get("reference_confidence"),
                "confidence_condition": optional_condition_confidence(watch),
                "confidence_price": field_confidences.get("price_confidence"),
                "confidence_intent": field_confidences.get("intent_confidence"),
            }
        )
    return updates


def compute_training_row_updates(
    row: Record,
    *,
    message_type: str | None = None,
) -> Record | None:
    """Return DB fields after mapping + safety re-eval, or None when unchanged."""
    if str(row.get("status") or "") == "ignored":
        return None

    watch = enrich_watch_for_training_evaluation(
        watch_from_training_row(row),
        message_type=message_type,
    )
    blocked, issue_types = evaluate_offer_safety(watch, message_type=message_type)
    updates = _training_row_update_fields(
        row,
        watch,
        message_type=message_type,
        blocked=blocked,
        issue_types=issue_types,
    )

    changed = False
    for key, value in updates.items():
        if row.get(key) != value:
            changed = True
            break
    return updates if changed else None


def row_needs_status_reevaluation(row: Record, *, message_type: str | None = None) -> bool:
    """Return True when a stored row status/issues are stale."""
    if str(row.get("status") or "") in {"ignored", "failed"}:
        return False
    return compute_training_row_updates(row, message_type=message_type) is not None


def re_evaluate_training_row_in_memory(
    row: Record,
    *,
    message_type: str | None = None,
) -> Record:
    """Apply mapping + safety re-eval for display without persisting."""
    updates = compute_training_row_updates(row, message_type=message_type)
    if updates:
        return {**row, **updates}
    return row


def bucket_training_row_for_container_stats(row: Record) -> str:
    """Map a parser_training_rows record to a container stats bucket."""
    status = str(row.get("status") or "pending_review").lower()
    if status in APPROVED_CONTAINER_STATUSES:
        return "approved"
    if status == "ignored":
        return "ignored"
    if status == "failed":
        return "failed"
    return "pending_review"


def summarize_training_rows_by_status(rows: list[Record]) -> Record:
    """Aggregate container counts from parser_training_rows records."""
    summary: Record = {
        "total_rows": 0,
        "approved_rows": 0,
        "pending_review_rows": 0,
        "ignored_rows": 0,
        "failed_rows": 0,
        "corrected_rows": 0,
    }
    for row in rows:
        summary["total_rows"] += 1
        bucket = bucket_training_row_for_container_stats(row)
        if bucket == "approved":
            summary["approved_rows"] += 1
        elif bucket == "pending_review":
            summary["pending_review_rows"] += 1
        elif bucket == "ignored":
            summary["ignored_rows"] += 1
        elif bucket == "failed":
            summary["failed_rows"] += 1
    return summary


def container_stats_match_row_totals(summary: Record) -> bool:
    """Return True when bucket counts add up to total_rows."""
    counted = (
        int(summary.get("approved_rows") or 0)
        + int(summary.get("pending_review_rows") or 0)
        + int(summary.get("ignored_rows") or 0)
        + int(summary.get("failed_rows") or 0)
    )
    return counted == int(summary.get("total_rows") or 0)


def summarize_training_rows_from_display(rows: list[Record]) -> Record:
    """Count container buckets from formatted training row displays."""
    return summarize_training_rows_by_status(
        [{"status": row.get("status")} for row in rows]
    )


def build_container_summary_for_import(
    rows: list[Record],
    *,
    import_log_id: str,
) -> Record:
    """Build per-import container stats from parser_training_rows (read-only)."""
    summary = summarize_training_rows_by_status(rows)
    summary["import_log_id"] = import_log_id
    return summary


def empty_container_summary(import_log_id: str) -> Record:
    """Return zeroed container stats for an import with no training rows."""
    return {
        "import_log_id": import_log_id,
        "total_rows": 0,
        "approved_rows": 0,
        "pending_review_rows": 0,
        "ignored_rows": 0,
        "failed_rows": 0,
        "corrected_rows": 0,
    }


def re_evaluate_parser_training_import(
    import_log_id: str,
    *,
    message_type: str | None = None,
) -> Record:
    """Explicitly persist re-evaluated statuses for one import."""
    result = re_evaluate_parser_training_rows(
        import_log_id=import_log_id,
        message_type=message_type,
    )
    return {"import_log_id": import_log_id, **result}


def re_evaluate_parser_training_imports(
    import_log_ids: list[str],
    *,
    message_types_by_id: dict[str, str | None] | None = None,
) -> Record:
    """Explicitly re-evaluate multiple imports (admin action only)."""
    message_types_by_id = message_types_by_id or {}
    totals: Record = {
        "imports_processed": 0,
        "rows_checked": 0,
        "rows_updated": 0,
        "import_log_ids": [],
    }
    for import_log_id in import_log_ids:
        if not import_log_id:
            continue
        result = re_evaluate_parser_training_import(
            import_log_id,
            message_type=message_types_by_id.get(import_log_id),
        )
        totals["imports_processed"] += 1
        totals["rows_checked"] += int(result.get("rows_checked") or 0)
        totals["rows_updated"] += int(result.get("rows_updated") or 0)
        totals["import_log_ids"].append(import_log_id)
    return totals


def _save_reference_brand_mappings(
    brand_name: str,
    references: list[str],
    *,
    source: str = "parser_training",
) -> list[str]:
    """Create reference→brand mappings and return normalized references saved."""
    from database import create_reference_brand_mapping, reference_brand_mappings_supported
    from watch_knowledge import invalidate_reference_brand_mapping_cache, normalize_reference

    saved: list[str] = []
    if not reference_brand_mappings_supported():
        return saved

    brand = brand_name.strip()
    if not brand:
        return saved

    seen: set[str] = set()
    for reference in references:
        normalized = normalize_reference(reference)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        create_reference_brand_mapping(
            reference=normalized,
            brand_name=brand,
            source=source,
        )
        saved.append(normalized)
    if saved:
        invalidate_reference_brand_mapping_cache()
    return saved


def re_evaluate_parser_training_reference_batch(
    reference: str,
    *,
    limit: int = REFERENCE_REEVALUATE_BATCH_SIZE,
    offset: int = 0,
    message_type: str | None = None,
) -> Record:
    """Re-evaluate up to `limit` training rows for one reference."""
    from database import (
        list_parser_training_rows_for_reference,
        parser_training_rows_write_guard,
        update_parser_training_row,
    )

    batch_limit = max(1, min(int(limit), REFERENCE_REEVALUATE_BATCH_SIZE))
    batch_offset = max(0, int(offset))

    logger = logging.getLogger(__name__)
    started = time.perf_counter()

    with parser_training_rows_write_guard():
        rows = list_parser_training_rows_for_reference(
            reference,
            limit=batch_limit,
            offset=batch_offset,
        )
        updated = 0
        for row in rows:
            updates = compute_training_row_updates(row, message_type=message_type)
            if updates and row.get("id"):
                update_parser_training_row(str(row["id"]), **updates)
                updated += 1

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "re_evaluate_parser_training_reference_batch reference=%s offset=%s "
            "limit=%s rows_checked=%s rows_updated=%s elapsed_ms=%s",
            reference,
            batch_offset,
            batch_limit,
            len(rows),
            updated,
            elapsed_ms,
        )

        return {
            "reference": reference,
            "offset": batch_offset,
            "limit": batch_limit,
            "rows_checked": len(rows),
            "rows_updated": updated,
            "has_more": len(rows) == batch_limit,
            "next_offset": batch_offset + len(rows),
        }


def re_evaluate_parser_training_rows(
    *,
    import_log_id: str | None = None,
    reference: str | None = None,
    message_type: str | None = None,
    limit: int = REFERENCE_REEVALUATE_BATCH_SIZE,
    offset: int = 0,
) -> Record:
    """Re-evaluate stored training rows after mapping or rule changes."""
    from database import (
        list_parser_training_rows_for_import,
        parser_training_rows_write_guard,
        update_parser_training_row,
    )

    if reference:
        return re_evaluate_parser_training_reference_batch(
            reference,
            limit=limit,
            offset=offset,
            message_type=message_type,
        )

    with parser_training_rows_write_guard():
        if import_log_id:
            rows = list_parser_training_rows_for_import(import_log_id)
        else:
            return {"rows_checked": 0, "rows_updated": 0}

        updated = 0
        for row in rows:
            updates = compute_training_row_updates(row, message_type=message_type)
            if updates and row.get("id"):
                update_parser_training_row(str(row["id"]), **updates)
                updated += 1

        return {"rows_checked": len(rows), "rows_updated": updated}


def build_training_row_payload(
    *,
    import_log_id: str,
    source_message_id: str | None,
    row_index: int,
    watch: Record,
    message_type: str | None = None,
    created_offer_id: str | None = None,
    created_by_user_id: str | None = None,
) -> Record:
    """Build a parser_training_rows record from a parsed watch."""
    enriched = enrich_watch_for_training_evaluation(dict(watch), message_type=message_type)
    blocked, issue_types = evaluate_offer_safety(enriched, message_type=message_type)

    if created_offer_id:
        status = "approved"
    elif blocked:
        status = "pending_review"
    else:
        status = "approved"

    field_confidences = compute_field_confidences(enriched, message_type=message_type)
    overall_confidence = compute_training_overall_confidence(field_confidences)
    explanations = build_training_parser_explanation(enriched, message_type=message_type)
    explanations["audit"] = {
        "original_confidence_overall": overall_confidence,
        "original_detected": _watch_to_audit_detected_fields(enriched),
        "reviewed_by_human": False,
        "corrected_fields": [],
        "approval_status": "parsed",
    }

    return {
        "import_log_id": import_log_id,
        "source_message_id": source_message_id,
        "row_index": row_index,
        "raw_row_text": str(enriched.get("source_line") or ""),
        **_watch_to_detected_fields(enriched),
        **_normalized_fields_from_watch(enriched),
        "confidence_overall": overall_confidence,
        "confidence_brand": field_confidences.get("brand_confidence"),
        "confidence_reference": field_confidences.get("reference_confidence"),
        "confidence_condition": optional_condition_confidence(enriched),
        "confidence_price": field_confidences.get("price_confidence"),
        "confidence_intent": field_confidences.get("intent_confidence"),
        "parser_explanation": explanations,
        "status": status,
        "issue_types": issue_types,
        "created_offer_id": created_offer_id,
        "created_by_user_id": created_by_user_id,
    }


def sync_training_rows_after_ingest(
    import_log: Record,
    *,
    message_id: str,
    watches: list[Record],
    offer_ids_by_index: dict[int, str | None],
    message_type: str | None = None,
    created_by_user_id: str | None = None,
) -> list[Record]:
    """Persist parser_training_rows for every offer row in an import."""
    from database import bulk_upsert_parser_training_rows, is_valid_uuid, parser_training_rows_supported, parser_training_rows_write_guard

    if not parser_training_rows_supported():
        return []

    import_log_id = str(import_log.get("id") or "")
    if not is_valid_uuid(import_log_id):
        return []

    message_id_value = str(message_id) if is_valid_uuid(message_id) else None
    payloads = [
        build_training_row_payload(
            import_log_id=import_log_id,
            source_message_id=message_id_value,
            row_index=index,
            watch=watch,
            message_type=message_type,
            created_offer_id=offer_ids_by_index.get(index)
            if is_valid_uuid(str(offer_ids_by_index.get(index) or ""))
            else None,
            created_by_user_id=created_by_user_id
            if is_valid_uuid(str(created_by_user_id or ""))
            else None,
        )
        for index, watch in enumerate(watches)
    ]
    with parser_training_rows_write_guard():
        return bulk_upsert_parser_training_rows(payloads)


def _offer_ids_by_index_from_summary(summary: Record) -> dict[int, str | None]:
    offer_ids: dict[int, str | None] = {}
    rows = summary.get("rows") or []
    for index, row in enumerate(rows):
        if isinstance(row, dict) and row.get("offer_id"):
            offer_ids[index] = str(row["offer_id"])
    return offer_ids


def backfill_parser_training_rows_for_recent_imports(
    *,
    limit: int = 50,
) -> Record:
    """Create missing parser_training_rows from existing import_logs.summary data."""
    from database import (
        IMPORT_LOG_LIST_LIMIT_PARSER_REVIEW,
        attach_import_log_summaries,
        list_activity_import_logs,
        list_parser_training_candidate_import_logs,
        list_parser_training_rows_for_import,
        parser_training_rows_supported,
    )

    if not parser_training_rows_supported():
        raise RuntimeError(
            "Parser training rows require Sprint 50.0 migration. Apply "
            "docs/migrations/sprint_50_0_parser_training_rows.sql in Supabase."
        )

    scan_limit = max(1, min(limit, IMPORT_LOG_LIST_LIMIT_PARSER_REVIEW))
    by_id: dict[str, Record] = {}
    for import_log in list_parser_training_candidate_import_logs(limit=scan_limit):
        import_id = str(import_log.get("id") or "")
        if import_id:
            by_id[import_id] = import_log
    for import_log in list_activity_import_logs(tab="all", offset=0, limit=scan_limit):
        import_id = str(import_log.get("id") or "")
        if import_id:
            by_id[import_id] = import_log

    import_logs = attach_import_log_summaries(list(by_id.values()))
    result: Record = {
        "scanned": len(import_logs),
        "processed": 0,
        "skipped_existing": 0,
        "skipped_no_rows": 0,
        "rows_created": 0,
        "import_log_ids": [],
        "errors": [],
    }

    for import_log in import_logs:
        import_id = str(import_log.get("id") or "")
        if not import_id:
            continue

        existing = list_parser_training_rows_for_import(import_id)
        if existing:
            result["skipped_existing"] += 1
            continue

        summary = import_log.get("summary") or {}
        watches = list(summary.get("offer_watches") or summary.get("parsed_watches") or [])
        if not watches:
            result["skipped_no_rows"] += 1
            continue

        message_id = str(summary.get("message_id") or import_log.get("message_id") or "")
        try:
            created = sync_training_rows_after_ingest(
                import_log,
                message_id=message_id,
                watches=watches,
                offer_ids_by_index=_offer_ids_by_index_from_summary(summary),
                message_type=summary.get("message_type"),
                created_by_user_id=str(import_log.get("imported_by_user_id") or "") or None,
            )
        except Exception as exc:
            result["errors"].append({"import_log_id": import_id, "error": str(exc)})
            continue

        result["processed"] += 1
        result["rows_created"] += len(created)
        result["import_log_ids"].append(import_id)

    for import_id in result["import_log_ids"]:
        re_evaluate_parser_training_rows(import_log_id=import_id)

    return result


def prepare_parser_training_rows_for_import(import_log: Record) -> Record:
    """Create parser_training_rows from stored import summary without re-ingesting offers."""
    from database import (
        list_parser_training_rows_for_import,
        parser_training_rows_supported,
        parser_training_rows_write_guard,
    )

    import_id = str(import_log.get("id") or "")
    if not import_id:
        return {"status": "missing_import", "rows_created": 0, "import_log_id": import_id}

    if not parser_training_rows_supported():
        return {"status": "unsupported", "rows_created": 0, "import_log_id": import_id}

    existing = list_parser_training_rows_for_import(import_id)
    if existing:
        return {
            "status": "already_prepared",
            "rows_created": 0,
            "existing_rows": len(existing),
            "import_log_id": import_id,
        }

    summary = import_log.get("summary") or {}
    watches = list(summary.get("offer_watches") or summary.get("parsed_watches") or [])
    if not watches:
        return {"status": "no_rows", "rows_created": 0, "import_log_id": import_id}

    message_id = str(summary.get("message_id") or import_log.get("message_id") or "")
    with parser_training_rows_write_guard():
        created = sync_training_rows_after_ingest(
            import_log,
            message_id=message_id,
            watches=watches,
            offer_ids_by_index=_offer_ids_by_index_from_summary(summary),
            message_type=summary.get("message_type"),
            created_by_user_id=str(import_log.get("imported_by_user_id") or "") or None,
        )

    if created:
        re_evaluate_parser_training_rows(import_log_id=import_id)

    return {
        "status": "prepared",
        "rows_created": len(created),
        "import_log_id": import_id,
    }


def watch_from_training_row(row: Record) -> Record:
    """Reconstruct the current final offer payload from a training row."""
    from final_offer_payload import build_final_offer_payload

    return build_final_offer_payload(row)


def apply_corrections_to_watch(watch: Record, corrections: Record) -> Record:
    """Apply user corrections to a watch dict."""
    from final_offer_payload import build_final_offer_payload

    row = {
        "detected_brand": watch.get("brand"),
        "detected_reference": watch.get("reference"),
        "detected_condition": watch.get("condition"),
        "detected_year": watch.get("production_year"),
        "detected_card_date": watch.get("card_date"),
        "detected_price": watch.get("original_price") or watch.get("price"),
        "detected_currency": watch.get("original_currency") or watch.get("currency"),
        "normalized_brand": watch.get("brand"),
        "normalized_reference": watch.get("reference"),
        "normalized_condition": watch.get("condition"),
        "usd_price": watch.get("usd_price"),
        "raw_row_text": watch.get("source_line"),
    }
    return build_final_offer_payload(row, corrections)


def create_offer_for_training_row(
    row: Record,
    *,
    import_log_id: str,
    message_id: str,
    dealer_id: str,
    line_index: int,
    final_watch: Record | None = None,
) -> tuple[Record | None, bool]:
    """Create an active offer for one training row when it passes safety gates."""
    from database import find_offer_by_message_line_index, find_or_create_watch, insert_offer, link_offer_to_import_source
    from final_offer_payload import final_offer_training_context

    existing_at_line = find_offer_by_message_line_index(message_id, line_index)
    if existing_at_line:
        return existing_at_line, False

    base_watch = dict(final_watch) if final_watch else watch_from_training_row(row)
    watch = enrich_watch_for_training_evaluation(base_watch)
    evaluation_context = final_offer_training_context(row, watch)
    if should_block_active_offer(evaluation_context):
        return None, False

    watch_row, _ = find_or_create_watch(
        brand=watch.get("brand"),
        reference=watch.get("reference"),
        model=watch.get("model"),
        dial=watch.get("dial"),
        bracelet=watch.get("bracelet"),
    )
    offer_row, created = insert_offer(
        message_id=message_id,
        watch_id=watch_row["id"],
        dealer_id=dealer_id,
        condition=watch.get("condition"),
        production_year=watch.get("production_year") if isinstance(watch.get("production_year"), int) else None,
        card_date=watch.get("card_date"),
        notes=watch.get("notes"),
        original_price=watch.get("original_price") or watch.get("price"),
        original_currency=watch.get("original_currency") or watch.get("currency"),
        usd_price=watch.get("usd_price"),
        line_index=line_index,
    )
    link_offer_to_import_source(
        str(offer_row["id"]),
        message_id=message_id,
        source_import_log_id=import_log_id,
    )
    return offer_row, created


def correct_training_row(
    row_id: str,
    corrections: Record,
    *,
    learn_mode: str = "row_only",
    created_by_user_id: str | None = None,
    dealer_id: str | None = None,
    group_id: str | None = None,
) -> Record:
    """Correct one training row and optionally teach the parser."""
    from database import parser_training_rows_write_guard

    with parser_training_rows_write_guard():
        return _correct_training_row_impl(
            row_id,
            corrections,
            learn_mode=learn_mode,
            created_by_user_id=created_by_user_id,
            dealer_id=dealer_id,
            group_id=group_id,
        )


def sync_import_log_summary_for_training_row(
    import_log: Record,
    *,
    row_index: int,
    final_watch: Record,
    offer_id: str | None,
    market_price_debug: Record | None = None,
) -> Record:
    """Refresh import summary rows and offer_watches with corrected final values."""
    from database import patch_import_log

    summary = dict(import_log.get("summary") or {})
    row_updates = {
        "brand": final_watch.get("brand"),
        "reference": final_watch.get("reference"),
        "condition": final_watch.get("condition"),
        "raw_condition": final_watch.get("raw_condition"),
        "condition_source": final_watch.get("condition_source"),
        "condition_confidence": final_watch.get("condition_confidence"),
        "condition_explicit": final_watch.get("condition_explicit"),
        "production_year": final_watch.get("production_year"),
        "card_date": final_watch.get("card_date"),
        "original_price": final_watch.get("original_price") or final_watch.get("price"),
        "original_currency": final_watch.get("original_currency") or final_watch.get("currency"),
        "usd_price": final_watch.get("usd_price"),
        "offer_id": offer_id,
        "reviewed_by_human": True,
    }
    if market_price_debug:
        row_updates.update(
            {
                "parser_confidence": market_price_debug.get("parser_confidence"),
                "market_price_confidence": market_price_debug.get("market_price_confidence"),
                "market_price_eligible": market_price_debug.get("market_price_eligible"),
                "market_price_exclusion_reasons": market_price_debug.get("market_price_exclusion_reasons"),
                "market_price_threshold": market_price_debug.get("market_price_threshold"),
            }
        )

    rows = list(summary.get("rows") or [])
    if 0 <= row_index < len(rows) and isinstance(rows[row_index], dict):
        rows[row_index] = {**rows[row_index], **row_updates}
        summary["rows"] = rows

    for key in ("offer_watches", "parsed_watches"):
        watches = list(summary.get(key) or [])
        if 0 <= row_index < len(watches) and isinstance(watches[row_index], dict):
            watches[row_index] = {
                **watches[row_index],
                **final_watch,
                "reviewed_by_human": True,
            }
            summary[key] = watches

    return patch_import_log(str(import_log["id"]), summary=summary)


def _resolve_training_row_offer(
    *,
    row: Record,
    message_id: str,
    line_index: int,
) -> Record | None:
    """Resolve the existing offer that should be updated for one training row."""
    from database import (
        OfferSourceIdentityConflictError,
        find_offer_by_message_line_index,
        get_offer_by_id,
        is_valid_uuid,
    )

    linked_id = str(row.get("created_offer_id") or "").strip()
    linked = get_offer_by_id(linked_id) if linked_id and is_valid_uuid(linked_id) else None
    by_source = (
        find_offer_by_message_line_index(message_id, line_index)
        if message_id and is_valid_uuid(message_id)
        else None
    )

    if linked and by_source and str(linked.get("id")) != str(by_source.get("id")):
        linked_line = linked.get("line_index")
        raise OfferSourceIdentityConflictError(
            "Could not save this row because it is linked to offer "
            f"{linked.get('id')} (message line {linked_line}), but message line "
            f"{line_index} is already owned by offer {by_source.get('id')}. "
            "Review the linked offer mapping for this import before saving again."
        )

    if linked:
        return linked
    if by_source:
        return by_source
    return None


def _persist_corrected_offer(
    *,
    row: Record,
    final_watch: Record,
    import_log: Record,
    message: Record | None,
    blocked: bool,
) -> str | None:
    """Create or update the live offer for a corrected training row."""
    from database import (
        get_offer_by_id,
        is_valid_uuid,
        link_offer_to_import_source,
        update_offer_from_training,
    )

    line_index = int(row.get("row_index") or 0)
    message_id = str((message or {}).get("id") or import_log.get("message_id") or "")
    import_log_id = str(import_log.get("id") or "")

    if blocked:
        offer_id = row.get("created_offer_id")
        return str(offer_id) if offer_id else None

    offer_to_update = _resolve_training_row_offer(
        row=row,
        message_id=message_id,
        line_index=line_index,
    )

    if offer_to_update:
        offer_id = str(offer_to_update["id"])
        updated = update_offer_from_training(offer_id, watch=final_watch)
        if message_id and import_log_id:
            link_offer_to_import_source(
                offer_id,
                message_id=message_id,
                source_import_log_id=import_log_id,
            )
        return str(updated.get("id") or offer_id)

    dealer_id = None
    if message:
        dealer_id = message.get("dealer_id")
    prior_offer_id = row.get("created_offer_id")
    if not dealer_id and prior_offer_id:
        existing = get_offer_by_id(str(prior_offer_id))
        if existing:
            dealer_id = existing.get("dealer_id")

    if dealer_id and message_id and import_log_id:
        offer_row, _ = create_offer_for_training_row(
            row,
            import_log_id=import_log_id,
            message_id=message_id,
            dealer_id=str(dealer_id),
            line_index=line_index,
            final_watch=final_watch,
        )
        if offer_row:
            return str(offer_row["id"])
    return str(prior_offer_id) if prior_offer_id else None


def _correct_training_row_impl(
    row_id: str,
    corrections: Record,
    *,
    learn_mode: str = "row_only",
    created_by_user_id: str | None = None,
    dealer_id: str | None = None,
    group_id: str | None = None,
) -> Record:
    """Internal implementation for correct_training_row (writes allowed)."""
    from database import (
        get_import_log,
        get_message_by_id,
        get_parser_training_row,
        update_parser_training_row,
    )
    from final_offer_payload import (
        apply_manual_review_trust,
        build_final_offer_payload,
        build_training_row_audit,
        final_offer_training_context,
    )
    from market_price_confidence import build_market_price_debug

    row = get_parser_training_row(row_id)
    if row is None:
        raise ValueError("Training row not found")

    import_log = get_import_log(str(row.get("import_log_id") or ""))
    if import_log is None:
        raise ValueError("Import log not found")

    message_type = (import_log.get("summary") or {}).get("message_type")
    message = get_message_by_id(
        str(row.get("source_message_id") or import_log.get("message_id") or "")
    )

    final_payload = build_final_offer_payload(row, corrections)
    watch = enrich_watch_for_training_evaluation(dict(final_payload), message_type=message_type)
    watch = apply_manual_review_trust(watch, corrections)

    if corrections.get("learn_reference_brand") and corrections.get("brand"):
        ref = str(
            watch.get("reference")
            or row.get("normalized_reference")
            or row.get("detected_reference")
            or ""
        )
        _save_reference_brand_mappings(str(corrections["brand"]), [ref])
        watch = enrich_watch_for_training_evaluation(dict(watch), message_type=message_type)
        watch = apply_manual_review_trust(watch, corrections)

    if learn_mode != "row_only":
        _apply_learning_from_correction(
            corrections,
            learn_mode=learn_mode,
            import_log_id=str(row.get("import_log_id") or ""),
            dealer_id=dealer_id,
            group_id=group_id,
            created_by_user_id=created_by_user_id,
        )

    evaluation_context = final_offer_training_context(row, watch)
    blocked, issue_types = evaluate_offer_safety(evaluation_context, message_type=message_type)
    market_price_debug = build_market_price_debug(evaluation_context)
    audit = build_training_row_audit(
        row,
        corrections=corrections,
        created_by_user_id=created_by_user_id,
        final_watch=watch,
        market_price_debug=market_price_debug,
    )

    previous_offer_id = row.get("created_offer_id")
    try:
        offer_id = _persist_corrected_offer(
            row=row,
            final_watch=watch,
            import_log=import_log,
            message=message,
            blocked=blocked,
        )
    except Exception:
        if previous_offer_id and previous_offer_id != row.get("created_offer_id"):
            raise
        raise

    row_index = int(row.get("row_index") or 0)
    sync_import_log_summary_for_training_row(
        import_log,
        row_index=row_index,
        final_watch=watch,
        offer_id=offer_id,
        market_price_debug=market_price_debug,
    )

    updates = _training_row_update_fields(
        row,
        watch,
        message_type=message_type,
        blocked=blocked,
        issue_types=issue_types,
        preserve_parser_confidence=True,
        audit=audit,
    )
    updates.update(
        {
            "status": "pending_review" if blocked else "corrected",
            "created_offer_id": offer_id,
            "created_by_user_id": created_by_user_id or row.get("created_by_user_id"),
        }
    )
    return update_parser_training_row(row_id, **updates)


def _apply_learning_from_correction(
    corrections: Record,
    *,
    learn_mode: str,
    import_log_id: str,
    dealer_id: str | None,
    group_id: str | None,
    created_by_user_id: str | None,
) -> None:
    from database import create_parser_learning_rule, invalidate_parser_learning_rules_cache

    scope = learn_mode if learn_mode in {"global", "dealer", "group"} else "global"
    if corrections.get("condition_term") and corrections.get("condition"):
        create_parser_learning_rule(
            field_type="condition",
            term=str(corrections["condition_term"]),
            normalized_value=str(corrections["condition"]),
            scope=scope,
            dealer_id=dealer_id if scope == "dealer" else None,
            group_id=group_id if scope == "group" else None,
            source_import_log_id=import_log_id,
            created_by_user_id=created_by_user_id,
        )
    if corrections.get("brand_header_term") and corrections.get("brand"):
        create_parser_learning_rule(
            field_type="brand_header",
            term=str(corrections["brand_header_term"]),
            normalized_value=str(corrections["brand"]),
            scope=scope,
            dealer_id=dealer_id if scope == "dealer" else None,
            group_id=group_id if scope == "group" else None,
            source_import_log_id=import_log_id,
            created_by_user_id=created_by_user_id,
        )
    if corrections.get("currency_term") and corrections.get("currency"):
        create_parser_learning_rule(
            field_type="currency",
            term=str(corrections["currency_term"]),
            normalized_value=str(corrections["currency"]),
            scope=scope,
            dealer_id=dealer_id if scope == "dealer" else None,
            group_id=group_id if scope == "group" else None,
            source_import_log_id=import_log_id,
            created_by_user_id=created_by_user_id,
        )
    invalidate_parser_learning_rules_cache()


def bulk_training_row_action(
    import_log_id: str,
    action: str,
    *,
    row_ids: list[str],
    brand_name: str = "",
    condition_value: str = "",
    condition_term: str = "",
    currency: str = "",
    reference_brand_mappings: list[Record] | None = None,
    created_by_user_id: str | None = None,
    learn_mode: str = "row_only",
) -> list[Record]:
    """Apply bulk actions to selected training rows only."""
    from database import parser_training_rows_write_guard

    with parser_training_rows_write_guard():
        return _bulk_training_row_action_impl(
            import_log_id,
            action,
            row_ids=row_ids,
            brand_name=brand_name,
            condition_value=condition_value,
            condition_term=condition_term,
            currency=currency,
            reference_brand_mappings=reference_brand_mappings,
            created_by_user_id=created_by_user_id,
            learn_mode=learn_mode,
        )


def _bulk_training_row_action_impl(
    import_log_id: str,
    action: str,
    *,
    row_ids: list[str],
    brand_name: str = "",
    condition_value: str = "",
    condition_term: str = "",
    currency: str = "",
    reference_brand_mappings: list[Record] | None = None,
    created_by_user_id: str | None = None,
    learn_mode: str = "row_only",
) -> list[Record]:
    """Internal implementation for bulk_training_row_action (writes allowed)."""
    from database import get_parser_training_row, update_parser_training_row

    cleaned_action = action.strip().lower()
    updated_rows: list[Record] = []

    from database import get_import_log, get_message_by_id

    import_log = get_import_log(import_log_id)
    message = (
        get_message_by_id(str(import_log.get("message_id") or ""))
        if import_log
        else None
    )
    dealer_id = str((message or {}).get("dealer_id") or "") or None
    group_id = str((message or {}).get("group_id") or "") or None

    if cleaned_action == "ignore_rows":
        for row_id in row_ids:
            updated_rows.append(
                update_parser_training_row(row_id, status="ignored", issue_types=[])
            )
        return updated_rows

    if cleaned_action == "approve_rows":
        for row_id in row_ids:
            row = get_parser_training_row(row_id)
            if row is None:
                continue
            watch = watch_from_training_row(row)
            if watch_passes_training_gates(watch):
                result = correct_training_row(
                    row_id,
                    {},
                    learn_mode="row_only",
                    created_by_user_id=created_by_user_id,
                    dealer_id=dealer_id,
                    group_id=group_id,
                )
                updated_rows.append(
                    update_parser_training_row(
                        row_id,
                        status="approved",
                        issue_types=[],
                        created_offer_id=result.get("created_offer_id"),
                    )
                )
        return updated_rows

    if cleaned_action == "set_brand":
        if not brand_name.strip():
            raise ValueError("Brand is required")
        refs_to_learn = [
            str(item.get("reference") or "").strip()
            for item in (reference_brand_mappings or [])
            if item.get("selected") and str(item.get("reference") or "").strip()
        ]
        if not refs_to_learn:
            refs_to_learn = [
                str(
                    (get_parser_training_row(row_id) or {}).get("normalized_reference")
                    or (get_parser_training_row(row_id) or {}).get("detected_reference")
                    or ""
                ).strip()
                for row_id in row_ids
            ]
        _save_reference_brand_mappings(brand_name.strip(), refs_to_learn)
        for row_id in row_ids:
            updated_rows.append(
                correct_training_row(
                    row_id,
                    {"brand": brand_name.strip()},
                    learn_mode=learn_mode,
                    created_by_user_id=created_by_user_id,
                    dealer_id=dealer_id,
                    group_id=group_id,
                )
            )
        return updated_rows

    if cleaned_action == "set_condition":
        if not condition_value.strip():
            raise ValueError("Condition is required")
        corrections: Record = {"condition": condition_value.strip()}
        if condition_term.strip():
            corrections["condition_term"] = condition_term.strip()
        for row_id in row_ids:
            updated_rows.append(
                correct_training_row(
                    row_id,
                    corrections,
                    learn_mode=learn_mode,
                    created_by_user_id=created_by_user_id,
                    dealer_id=dealer_id,
                    group_id=group_id,
                )
            )
        return updated_rows

    if cleaned_action == "set_currency":
        if not currency.strip():
            raise ValueError("Currency is required")
        for row_id in row_ids:
            updated_rows.append(
                correct_training_row(
                    row_id,
                    {"currency": currency.strip()},
                    learn_mode=learn_mode,
                    created_by_user_id=created_by_user_id,
                    dealer_id=dealer_id,
                    group_id=group_id,
                )
            )
        return updated_rows

    if cleaned_action == "map_references":
        if not brand_name.strip():
            raise ValueError("Brand is required")
        refs_to_learn = [
            str(mapping.get("reference") or "").strip()
            for mapping in (reference_brand_mappings or [])
            if str(mapping.get("reference") or "").strip()
        ]
        _save_reference_brand_mappings(brand_name.strip(), refs_to_learn)
        for row_id in row_ids:
            row = get_parser_training_row(row_id)
            if row is None:
                continue
            from watch_knowledge import normalize_reference

            ref = normalize_reference(
                str(row.get("normalized_reference") or row.get("detected_reference") or "")
            )
            selected_refs = {
                normalize_reference(str(item.get("reference") or ""))
                for item in (reference_brand_mappings or [])
                if item.get("selected")
            }
            selected_refs.discard(None)
            if ref and (not selected_refs or ref in selected_refs):
                updated_rows.append(
                    correct_training_row(
                        row_id,
                        {"brand": brand_name.strip(), "reference": ref},
                        learn_mode=learn_mode,
                        created_by_user_id=created_by_user_id,
                        dealer_id=dealer_id,
                        group_id=group_id,
                    )
                )
        return updated_rows

    raise ValueError(f"Unsupported bulk action: {action}")
