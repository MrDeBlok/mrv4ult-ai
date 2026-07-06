"""Debug helpers for tracing offer → import log source URL resolution."""

from __future__ import annotations

from typing import Any

from app import normalize_watch_detail_offer
from database import (
    _normalize_uuid_key,
    find_import_log_by_message_id,
    find_import_logs_by_summary_offer_ids,
    get_import_logs_by_message_ids,
    get_offer_by_id,
    get_request_matches_for_offer_ids,
    source_import_log_id_column_supported,
)
from dealer_intelligence import (
    attach_dealer_offer_source_urls,
    load_offer_source_import_log_lookups,
    resolve_offer_source_url,
)

Record = dict[str, Any]

SOURCE_OFFER_FIELDS = (
    "id",
    "message_id",
    "import_log_id",
    "source_import_log_id",
    "created_from_import_log_id",
    "original_message_id",
    "dealer_id",
    "watch_id",
    "status",
    "is_duplicate",
    "duplicate_of_id",
)


def _summarize_import_log(import_log: Record | None) -> Record[str, Any] | None:
    if not import_log:
        return None
    return {
        "id": import_log.get("id"),
        "message_id": import_log.get("message_id"),
        "status": import_log.get("status"),
        "watches_parsed": import_log.get("watches_parsed"),
        "imported_by_user_id": import_log.get("imported_by_user_id"),
    }


def trace_offer_source_resolution(
    offer_id: str,
    *,
    user: Record | None = None,
) -> Record[str, Any]:
    """Trace one offer row through the watch detail source URL resolution pipeline."""
    cleaned_offer_id = offer_id.strip()
    offer_row = get_offer_by_id(cleaned_offer_id)
    if offer_row is None:
        return {
            "offer_id": cleaned_offer_id,
            "found": False,
            "failure_reason": "offer_not_found",
        }

    normalized_offer = normalize_watch_detail_offer(
        {
            **offer_row,
            "dealers": {},
            "messages": None,
            "watches": {},
        }
    )
    message_id = normalized_offer.get("message_id")
    normalized_message_id = _normalize_uuid_key(message_id)

    import_logs_by_message_id = (
        get_import_logs_by_message_ids([normalized_message_id])
        if normalized_message_id
        else {}
    )
    import_logs_by_summary = find_import_logs_by_summary_offer_ids([cleaned_offer_id])
    request_matches = get_request_matches_for_offer_ids([cleaned_offer_id])

    import_logs_by_message_id_matches = [
        _summarize_import_log(import_log)
        for import_log in import_logs_by_message_id.values()
    ]
    import_logs_by_summary_matches = [
        _summarize_import_log(import_log)
        for import_log in import_logs_by_summary.values()
    ]

    lookups = load_offer_source_import_log_lookups([normalized_offer])
    source_url, resolution_path, failure_reason = resolve_offer_source_url(
        normalized_offer,
        user=user,
        import_logs_by_message_id=lookups[0],
        import_logs_by_id=lookups[1],
        import_logs_by_offer_id=lookups[2],
    )
    enriched = attach_dealer_offer_source_urls(
        [normalized_offer],
        lookups[0],
        user=user,
        import_logs_by_id=lookups[1],
        import_logs_by_offer_id=lookups[2],
    )
    watch_detail_source_url = enriched[0].get("source_url")

    latest_message_import_log = (
        find_import_log_by_message_id(normalized_message_id)
        if normalized_message_id
        else None
    )

    offer_fields = {
        field: offer_row.get(field)
        for field in SOURCE_OFFER_FIELDS
        if field in offer_row or field in {
            "import_log_id",
            "source_import_log_id",
            "created_from_import_log_id",
            "original_message_id",
        }
    }
    offer_fields["id"] = offer_row.get("id")
    offer_fields["message_id"] = offer_row.get("message_id")
    offer_fields["dealer_id"] = offer_row.get("dealer_id")
    offer_fields["watch_id"] = offer_row.get("watch_id")
    if source_import_log_id_column_supported():
        offer_fields["source_import_log_id"] = offer_row.get("source_import_log_id")
    else:
        offer_fields["source_import_log_id"] = None
        offer_fields["source_import_log_id_column_supported"] = False

    if not failure_reason and not source_url:
        failure_reason = "no_import_log_resolved"

    return {
        "found": True,
        "offer": offer_fields,
        "normalized_message_id": normalized_message_id or None,
        "matching_import_logs_by_message_id": import_logs_by_message_id_matches,
        "matching_import_logs_by_summary_offer_id": import_logs_by_summary_matches,
        "matching_request_matches": request_matches,
        "latest_import_log_for_message_id": _summarize_import_log(latest_message_import_log),
        "resolution_path": resolution_path,
        "source_url": source_url,
        "watch_detail_source_url": watch_detail_source_url,
        "failure_reason": failure_reason,
    }
