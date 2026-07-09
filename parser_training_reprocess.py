"""Reprocess imports after training and sync offers when safe."""

from __future__ import annotations

from typing import Any

from parser_confidence import attach_parser_confidence_metadata
from parser_learning import apply_condition_once, prepare_watch_for_ingest, teach_condition_rule
from parser_safety_gates import should_block_active_offer, watch_passes_training_gates
from parser_workbench import reprocess_import_log

Record = dict[str, Any]


def _sync_offers_for_import(
    import_log: Record,
    *,
    offer_watches: list[Record],
    message_id: str,
    dealer_id: str | None,
) -> int:
    """Create active offers for a reprocessed import when safety gates pass."""
    from database import find_or_create_watch, get_offers_by_message_id, insert_offer

    if not dealer_id:
        return 0

    existing_offers = get_offers_by_message_id(message_id)
    existing_line_indexes = {
        int(offer.get("line_index") or 0) for offer in existing_offers
    }
    created = 0

    for line_index, watch in enumerate(offer_watches):
        if should_block_active_offer(watch, message_type=import_log.get("summary", {}).get("message_type")):
            continue
        if line_index in existing_line_indexes:
            continue

        watch_row, _ = find_or_create_watch(
            brand=watch.get("brand"),
            reference=watch.get("reference"),
            model=watch.get("model"),
            dial=watch.get("dial"),
            bracelet=watch.get("bracelet"),
        )
        _, offer_created = insert_offer(
            message_id=message_id,
            watch_id=watch_row["id"],
            dealer_id=dealer_id,
            condition=watch.get("condition"),
            production_year=watch.get("production_year"),
            card_date=watch.get("card_date"),
            notes=watch.get("notes"),
            original_price=watch.get("original_price") or watch.get("price"),
            original_currency=watch.get("original_currency") or watch.get("currency"),
            usd_price=watch.get("usd_price"),
            exchange_rate_to_usd=watch.get("exchange_rate_to_usd"),
            line_index=line_index,
        )
        if offer_created:
            created += 1

    return created


def finalize_training_import(import_log_id: str) -> Record:
    """Sync offers and clear the training queue after an import was already reprocessed."""
    from database import get_import_log, get_message_by_id, link_import_log_to_summary_offers, mark_import_parser_reviewed

    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    summary = import_log.get("summary") or {}
    offer_watches = list(summary.get("offer_watches") or summary.get("parsed_watches") or [])
    message_id = str(import_log.get("message_id") or summary.get("message_id") or "")
    message = get_message_by_id(message_id) if message_id else None
    dealer_id = str((message or {}).get("dealer_id") or "") or None

    created_offers = 0
    if message_id and offer_watches and import_log.get("status") == "success":
        created_offers = _sync_offers_for_import(
            import_log,
            offer_watches=offer_watches,
            message_id=message_id,
            dealer_id=dealer_id,
        )
        if created_offers:
            summary["new_offers"] = int(summary.get("new_offers") or 0) + created_offers
            import_log = get_import_log(import_log_id) or import_log
            link_import_log_to_summary_offers(import_log_id, message_id, summary)

    if offer_watches and all(
        watch_passes_training_gates(watch, message_type=summary.get("message_type"))
        for watch in offer_watches
    ):
        return mark_import_parser_reviewed(import_log_id)

    return get_import_log(import_log_id) or import_log


def reprocess_import_with_offer_sync(
    import_log_id: str,
    *,
    field_overrides: Record | None = None,
) -> Record:
    """Re-parse an import, refresh summary, and create offers when safe."""
    from database import get_import_log, get_message_by_id, link_import_log_to_summary_offers, mark_import_parser_reviewed

    import_log = reprocess_import_log(import_log_id, field_overrides=field_overrides)
    return finalize_training_import(import_log_id)


def teach_condition_and_reprocess(
    import_log_id: str,
    *,
    term: str,
    normalized_value: str,
    action: str,
    scope: str = "global",
    dealer_id: str | None = None,
    group_id: str | None = None,
    created_by_user_id: str | None = None,
) -> Record:
    """Handle Training Center condition quick actions."""
    from database import get_import_log, mark_import_parser_issue_ignored, patch_import_log

    cleaned_action = action.strip().lower()
    cleaned_term = term.strip()
    if not cleaned_term:
        raise ValueError("Condition term is required")

    if cleaned_action == "ignore":
        return mark_import_parser_issue_ignored(import_log_id, reason=f"Ignored condition term: {cleaned_term}")

    if cleaned_action == "apply_once":
        import_log = get_import_log(import_log_id)
        if import_log is None:
            raise ValueError("Import log not found")
        summary = dict(import_log.get("summary") or {})
        watches = list(summary.get("offer_watches") or summary.get("parsed_watches") or [])
        if not watches:
            raise ValueError("No parsed watches to update")
        apply_condition_once(watches[0], term=cleaned_term, normalized_value=normalized_value)
        attach_parser_confidence_metadata(watches[0], message_type=summary.get("message_type"))
        summary["offer_watches"] = watches
        summary["parsed_watches"] = watches
        patch_import_log(import_log_id, summary=summary)
        return reprocess_import_with_offer_sync(
            import_log_id,
            field_overrides={"condition": normalized_value},
        )

    if cleaned_action in {"teach_new", "teach_pre_owned"}:
        value = "New" if cleaned_action == "teach_new" else "Pre-Owned"
        teach_condition_rule(
            term=cleaned_term,
            normalized_value=value if not normalized_value else normalized_value,
            scope=scope,
            dealer_id=dealer_id,
            group_id=group_id,
            source_import_log_id=import_log_id,
            created_by_user_id=created_by_user_id,
        )
        return reprocess_import_with_offer_sync(import_log_id)

    raise ValueError(f"Unsupported condition teach action: {action}")
