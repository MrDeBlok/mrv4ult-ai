"""Ingest parsed WhatsApp messages into Supabase."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from timezone_utils import ensure_utc_datetime
from typing import Any

from database import (
    contact_type_column_supported,
    dealer_contact_type,
    find_duplicate_offer,
    find_import_log_by_message_id,
    find_message_by_whatsapp_id,
    find_or_create_watch,
    get_client,
    insert_import_log,
    insert_message,
    insert_offer,
    is_business_dealer_relation,
    process_offer_request_matches,
    update_import_log,
)
from contact_classification import (
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_REMOVED,
    IMPORT_PLACEHOLDER_WHATSAPP_ID,
    has_valid_parsed_offers,
    should_process_business_import,
)
from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    normalize_condition_value,
    normalize_watch_condition,
)
from watch_knowledge import enrich_parsed_watch
from import_classification import (
    enrich_sold_order_watches,
    is_buyer_request_message,
    is_sold_order_message,
    sold_order_has_actionable_identity,
    split_offer_watches,
)
from ingest_lifecycle import (
    bind_import_log_id,
    end_import_trace,
    start_import_trace,
)
from watch_evidence import (
    INSUFFICIENT_EVIDENCE_REASON,
    describe_evidence_gaps,
    partition_watches_by_evidence,
)
from watch_parser import parse_message
from notifications import notify_request_match, record_import_notifications
from unknown_brand_intelligence import record_unknown_brands_for_watches
from unknown_nickname_intelligence import record_unknown_nicknames_for_watches

PARSER_VERSION = "watch_parser_v1"
DEFAULT_GROUP_NAME = "Default Group"
DEFAULT_DEALER_WHATSAPP_ID = "default-dealer"
DEFAULT_DEALER_NAME = "Default Dealer"
DEALER_LIST_BULK_THRESHOLD = 10
BULK_IMPORT_THRESHOLD = DEALER_LIST_BULK_THRESHOLD

logger = logging.getLogger(__name__)

IngestSummary = dict[str, Any]


def dealer_list_line_count(watches: list[dict[str, Any]]) -> int:
    """Return how many watches came from a dealer inventory list split."""
    return sum(1 for watch in watches if watch.get("dealer_list_line"))


def is_large_dealer_list_import_log(import_log: dict[str, Any]) -> bool:
    """Return True when an import used bulk ingest for a large dealer list."""
    summary = import_log.get("summary") or {}
    if summary.get("bulk_import"):
        return True
    if int(import_log.get("watches_parsed") or 0) >= BULK_IMPORT_THRESHOLD:
        return True
    watches = summary.get("parsed_watches") or summary.get("rows") or []
    if len(watches) >= BULK_IMPORT_THRESHOLD:
        return True
    return dealer_list_line_count(watches) >= BULK_IMPORT_THRESHOLD


def is_dealer_list_bulk_import(offer_watches: list[dict[str, Any]]) -> bool:
    """Use fast ingest when a message contains a large structured dealer list."""
    if len(offer_watches) >= BULK_IMPORT_THRESHOLD:
        return True
    return dealer_list_line_count(offer_watches) >= BULK_IMPORT_THRESHOLD


def _bulk_deferred_price_intelligence(*, is_duplicate: bool) -> dict[str, str]:
    """Placeholder price intelligence while bulk ingest skips market lookups."""
    label = "Duplicate offer" if is_duplicate else "Deferred for bulk import"
    return {
        "rank": "N/A",
        "previous_lowest_usd": "N/A",
        "price_difference": "N/A",
        "label": label,
        "label_class": _price_label_class(label),
        "market_condition": None,
    }


def _normalize_watch_cache_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned.lower()


def _normalize_offer_cache_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _watch_cache_key(
    *,
    brand: str | None,
    reference: str | None,
    dial: str | None,
    bracelet: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    return (
        _normalize_watch_cache_value(brand),
        _normalize_watch_cache_value(reference),
        _normalize_watch_cache_value(dial),
        _normalize_watch_cache_value(bracelet),
    )


def _duplicate_offer_cache_key(
    *,
    watch_id: str,
    dealer_id: str,
    original_price: int | None,
    original_currency: str | None,
    condition: str | None,
    card_date: str | None,
    production_year: int | None,
) -> tuple[Any, ...]:
    return (
        watch_id,
        dealer_id,
        original_price,
        _normalize_offer_cache_value(original_currency),
        _normalize_offer_cache_value(normalize_condition_value(condition)),
        _normalize_offer_cache_value(card_date),
        production_year,
    )


def _cached_find_or_create_watch(
    watch_cache: dict[tuple[str | None, str | None, str | None, str | None], dict[str, Any]],
    *,
    brand: str | None,
    reference: str | None,
    model: str | None,
    dial: str | None,
    bracelet: str | None,
) -> tuple[dict[str, Any], bool]:
    key = _watch_cache_key(brand=brand, reference=reference, dial=dial, bracelet=bracelet)
    cached = watch_cache.get(key)
    if cached is not None:
        return cached, False

    watch_row, watch_created = find_or_create_watch(
        brand=brand,
        reference=reference,
        model=model,
        dial=dial,
        bracelet=bracelet,
    )
    watch_cache[key] = watch_row
    return watch_row, watch_created


def _cached_insert_offer(
    duplicate_cache: dict[tuple[Any, ...], dict[str, Any]],
    *,
    message_id: str,
    watch_id: str,
    dealer_id: str,
    condition: str | None,
    production_year: int | None,
    card_date: str | None,
    notes: str | None,
    original_price: int | None,
    original_currency: str | None,
    usd_price: int | None,
    exchange_rate_to_usd: float | None,
    line_index: int,
) -> tuple[dict[str, Any], bool]:
    """Insert offers while avoiding repeated duplicate checks in one bulk import."""
    normalized_currency = _normalize_offer_cache_value(original_currency)
    normalized_condition = _normalize_offer_cache_value(normalize_condition_value(condition))
    normalized_card_date = _normalize_offer_cache_value(card_date)
    cache_key = _duplicate_offer_cache_key(
        watch_id=watch_id,
        dealer_id=dealer_id,
        original_price=original_price,
        original_currency=normalized_currency,
        condition=normalized_condition,
        card_date=normalized_card_date,
        production_year=production_year,
    )

    cached_offer = duplicate_cache.get(cache_key)
    if cached_offer is not None:
        return cached_offer, False

    existing = find_duplicate_offer(
        watch_id,
        dealer_id,
        original_price=original_price,
        original_currency=normalized_currency,
        condition=normalized_condition,
        card_date=normalized_card_date,
        production_year=production_year,
    )
    if existing:
        duplicate_cache[cache_key] = existing
        return existing, False

    offer_row, offer_created = insert_offer(
        message_id,
        watch_id,
        dealer_id,
        condition=condition,
        production_year=production_year,
        card_date=card_date,
        notes=notes,
        original_price=original_price,
        original_currency=normalized_currency,
        usd_price=usd_price,
        exchange_rate_to_usd=exchange_rate_to_usd,
        line_index=line_index,
        skip_duplicate_check=True,
    )
    duplicate_cache[cache_key] = offer_row
    return offer_row, offer_created


def find_or_create_group(group_name: str) -> str:
    """Return a group id for the given name, creating the group if needed."""
    name = group_name.strip()
    if not name:
        raise ValueError("Group name is required.")

    client = get_client()
    existing = client.table("groups").select("id").eq("name", name).limit(1).execute()
    if existing.data:
        return existing.data[0]["id"]

    created = client.table("groups").insert({"name": name}).execute()
    if not created.data:
        raise RuntimeError(f"Failed to create group: {name}")
    return created.data[0]["id"]


def find_or_create_dealer(
    whatsapp_number: str,
    *,
    display_name: str | None = None,
    default_contact_type: str = CONTACT_TYPE_DEALER,
) -> tuple[str, str]:
    """Return a dealer id and contact type for the given WhatsApp number."""
    whatsapp_id = _normalize_whatsapp_number(whatsapp_number)
    if not whatsapp_id:
        raise ValueError("Dealer WhatsApp number is required.")

    alias = display_name.strip() if display_name else None
    if alias == "":
        alias = None

    client = get_client()
    select_fields = (
        "id, display_name, contact_type"
        if contact_type_column_supported()
        else "id, display_name, whatsapp_id"
    )
    existing = (
        client.table("dealers")
        .select(select_fields)
        .eq("whatsapp_id", whatsapp_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        dealer = existing.data[0]
        updates: dict[str, Any] = {"phone_number": whatsapp_id}
        if alias is not None:
            updates["display_name"] = alias
        contact_type = dealer_contact_type(dealer)
        if (
            contact_type_column_supported()
            and default_contact_type == CONTACT_TYPE_DEALER
            and contact_type != CONTACT_TYPE_DEALER
        ):
            updates["contact_type"] = CONTACT_TYPE_DEALER
            contact_type = CONTACT_TYPE_DEALER
        if updates:
            client.table("dealers").update(updates).eq("id", dealer["id"]).execute()
        return dealer["id"], contact_type

    payload: dict[str, Any] = {
        "whatsapp_id": whatsapp_id,
        "phone_number": whatsapp_id,
        "is_active": True,
    }
    if contact_type_column_supported():
        payload["contact_type"] = default_contact_type
    if alias is not None:
        payload["display_name"] = alias

    created = client.table("dealers").insert(payload).execute()
    if not created.data:
        raise RuntimeError(f"Failed to create dealer: {whatsapp_id}")
    return created.data[0]["id"], default_contact_type


def get_default_group_id() -> str:
    """Return the default group, creating it if needed."""
    return find_or_create_group(DEFAULT_GROUP_NAME)


def get_default_dealer_id() -> tuple[str, str]:
    """Return the default dealer, creating it if needed."""
    return find_or_create_dealer(
        DEFAULT_DEALER_WHATSAPP_ID,
        display_name=DEFAULT_DEALER_NAME,
        default_contact_type=CONTACT_TYPE_DEALER,
    )


def get_import_placeholder_dealer_id() -> tuple[str, str]:
    """Return the placeholder dealer used for imports without valid watch offers."""
    return find_or_create_dealer(
        IMPORT_PLACEHOLDER_WHATSAPP_ID,
        display_name="Import Placeholder",
        default_contact_type=CONTACT_TYPE_REMOVED,
    )


def _build_discarded_ingest_summary(
    *,
    started_at: float,
    group_name: str | None,
    dealer_whatsapp: str | None,
    dealer_alias: str | None,
    parsed: dict[str, Any],
    status_reason: str,
) -> IngestSummary:
    """Return a summary for private/non-watch messages that are not persisted."""
    elapsed = time.perf_counter() - started_at
    if group_name is not None and dealer_whatsapp is not None:
        summary_group = group_name.strip()
        summary_whatsapp = _normalize_whatsapp_number(dealer_whatsapp)
        summary_alias = dealer_alias.strip() if dealer_alias else None
        if summary_alias == "":
            summary_alias = None
    else:
        summary_group = DEFAULT_GROUP_NAME
        summary_whatsapp = DEFAULT_DEALER_WHATSAPP_ID
        summary_alias = DEFAULT_DEALER_NAME

    return {
        "messages_imported": 0,
        "watches_parsed": 0,
        "new_watches": 0,
        "new_offers": 0,
        "duplicate_offers": 0,
        "matched_requests": 0,
        "processing_time": _format_processing_time(elapsed),
        "processing_time_ms": int(elapsed * 1000),
        "group": summary_group,
        "dealer_whatsapp": "",
        "dealer_alias": None,
        "rows": [],
        "status_reason": status_reason,
        "parsed_watches": list(parsed.get("watches") or []),
        "message_type": parsed.get("message_type") or "unknown",
        "import_log_id": None,
        "status": "no_watch_detected",
        "saved": False,
    }


def _build_already_imported_summary(
    *,
    started_at: float,
    existing_message: dict[str, Any],
    existing_import: dict[str, Any] | None,
    whatsapp_message_id: str,
) -> IngestSummary:
    """Return immediately when Evolution replays an already-imported WhatsApp message."""
    elapsed = time.perf_counter() - started_at
    if existing_import and isinstance(existing_import.get("summary"), dict):
        summary = dict(existing_import["summary"])
    else:
        summary = {
            "messages_imported": 1,
            "watches_parsed": 0,
            "new_watches": 0,
            "new_offers": 0,
            "duplicate_offers": 0,
            "matched_requests": 0,
            "rows": [],
        }

    summary.update(
        {
            "status": "already_imported",
            "status_reason": "WhatsApp message was already imported.",
            "already_processed": True,
            "whatsapp_message_id": whatsapp_message_id,
            "message_id": existing_message["id"],
            "import_log_id": existing_import.get("id") if existing_import else None,
            "processing_time": _format_processing_time(elapsed),
            "processing_time_ms": int(elapsed * 1000),
            "saved": False,
        }
    )
    logger.info(
        "Ingest skipped duplicate WhatsApp message: whatsapp_message_id=%s message_id=%s import_log_id=%s already_processed=True total_ms=%s",
        whatsapp_message_id,
        existing_message["id"],
        summary.get("import_log_id"),
        summary["processing_time_ms"],
    )
    return summary


def _resolve_ingest_source(source: str | None, whatsapp_message_id: str | None) -> str:
    if source:
        return source
    if whatsapp_message_id:
        return "whatsapp"
    return "manual"


def _finish_ingest_trace(
    trace: dict[str, Any],
    *,
    started_at: float,
    summary: IngestSummary,
) -> None:
    bind_import_log_id(trace, summary.get("import_log_id"))
    end_import_trace(
        trace,
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        offers_created=int(summary.get("new_offers") or 0),
        status=str(summary.get("status") or "unknown"),
    )


def ingest_message(
    text: str,
    *,
    group_name: str | None = None,
    dealer_whatsapp: str | None = None,
    dealer_alias: str | None = None,
    received_at: datetime | None = None,
    imported_by_user_id: str | None = None,
    whatsapp_message_id: str | None = None,
    source: str | None = None,
) -> IngestSummary:
    """Parse a message and save it with all offers to Supabase."""
    started_at = time.perf_counter()
    normalized_whatsapp_message_id = whatsapp_message_id.strip() if whatsapp_message_id else None
    if normalized_whatsapp_message_id == "":
        normalized_whatsapp_message_id = None
    resolved_source = _resolve_ingest_source(source, normalized_whatsapp_message_id)
    trace = start_import_trace(
        text=text,
        source=resolved_source,
        whatsapp_message_id=normalized_whatsapp_message_id,
    )

    if normalized_whatsapp_message_id:
        existing_message = find_message_by_whatsapp_id(normalized_whatsapp_message_id)
        if existing_message:
            existing_import = find_import_log_by_message_id(str(existing_message["id"]))
            summary = _build_already_imported_summary(
                started_at=started_at,
                existing_message=existing_message,
                existing_import=existing_import,
                whatsapp_message_id=normalized_whatsapp_message_id,
            )
            _finish_ingest_trace(trace, started_at=started_at, summary=summary)
            return summary

    parse_started_at = time.perf_counter()
    parsed = parse_message(text)
    parsed_watches = [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parsed["watches"]
    ]
    sold_order_message = is_sold_order_message(text)
    if sold_order_message:
        parsed_watches = enrich_sold_order_watches(parsed_watches)
        offer_watches, import_classification = [], "request_intent"
    else:
        offer_watches, import_classification = split_offer_watches(text, parsed, parsed_watches)
    insufficient_evidence_watches: list[dict[str, Any]] = []
    if import_classification is None and offer_watches:
        offer_watches, insufficient_evidence_watches = partition_watches_by_evidence(offer_watches)
        if not offer_watches and insufficient_evidence_watches:
            import_classification = "insufficient_evidence"
    bulk_mode = is_dealer_list_bulk_import(offer_watches)
    parse_elapsed_ms = int((time.perf_counter() - parse_started_at) * 1000)
    if bulk_mode:
        logger.info(
            "Dealer list bulk import: %s structured lines (threshold>%s)",
            dealer_list_line_count(offer_watches),
            DEALER_LIST_BULK_THRESHOLD,
        )
    has_valid_offers = has_valid_parsed_offers(len(offer_watches))
    parse_status = _parse_status(parsed)
    preliminary_status, preliminary_reason = _import_status(
        {"watches_parsed": len(offer_watches), "duplicate_offers": 0},
        parse_status,
        offer_watches,
        classification=import_classification,
        bulk_mode=bulk_mode,
    )
    if preliminary_status == "no_watch_detected" and import_classification is None:
        summary = _build_discarded_ingest_summary(
            started_at=started_at,
            group_name=group_name,
            dealer_whatsapp=dealer_whatsapp,
            dealer_alias=dealer_alias,
            parsed=parsed,
            status_reason=preliminary_reason,
        )
        _finish_ingest_trace(trace, started_at=started_at, summary=summary)
        return summary

    if group_name is not None and dealer_whatsapp is not None:
        normalized_group_name = group_name.strip()
        normalized_whatsapp = _normalize_whatsapp_number(dealer_whatsapp)
        normalized_alias = dealer_alias.strip() if dealer_alias else None
        if normalized_alias == "":
            normalized_alias = None

        group_id = find_or_create_group(normalized_group_name)
        summary_group = normalized_group_name
        summary_whatsapp = normalized_whatsapp
        summary_alias = normalized_alias

        if has_valid_offers:
            dealer_id, contact_type = find_or_create_dealer(
                normalized_whatsapp,
                display_name=normalized_alias,
                default_contact_type=CONTACT_TYPE_DEALER,
            )
        elif import_classification == "request_intent":
            dealer_id, contact_type = find_or_create_dealer(
                normalized_whatsapp,
                display_name=normalized_alias,
                default_contact_type=CONTACT_TYPE_DEALER,
            )
        elif import_classification == "insufficient_evidence":
            dealer_id, contact_type = find_or_create_dealer(
                normalized_whatsapp,
                display_name=normalized_alias,
                default_contact_type=CONTACT_TYPE_DEALER,
            )
        else:
            dealer_id, contact_type = get_import_placeholder_dealer_id()
    else:
        group_id = get_default_group_id()
        summary_group = DEFAULT_GROUP_NAME
        summary_whatsapp = DEFAULT_DEALER_WHATSAPP_ID
        summary_alias = DEFAULT_DEALER_NAME
        if has_valid_offers:
            dealer_id, contact_type = get_default_dealer_id()
        else:
            dealer_id, contact_type = get_import_placeholder_dealer_id()

    business_import = has_valid_offers and should_process_business_import(contact_type)

    now = datetime.now(timezone.utc)
    message_received_at = ensure_utc_datetime(received_at) if received_at else now

    message = insert_message(
        group_id=group_id,
        dealer_id=dealer_id,
        raw_text=text,
        message_type=parsed["message_type"],
        received_at=message_received_at,
        parsed_at=now,
        parser_version=PARSER_VERSION,
        parse_status=parse_status,
        imported_by_user_id=imported_by_user_id,
        whatsapp_message_id=normalized_whatsapp_message_id,
    )
    logger.info(
        "Ingest message stored: whatsapp_message_id=%s message_id=%s import_pending=True",
        normalized_whatsapp_message_id or "manual",
        message["id"],
    )

    if business_import and not bulk_mode:
        record_unknown_brands_for_watches(
            offer_watches,
            example_message=text,
            dealer_id=dealer_id,
            seen_at=message_received_at,
        )
        record_unknown_nicknames_for_watches(
            offer_watches,
            example_message=text,
            dealer_id=dealer_id,
            seen_at=message_received_at,
        )

    summary: IngestSummary = {
        "messages_imported": 1,
        "watches_parsed": 0,
        "new_watches": 0,
        "new_offers": 0,
        "duplicate_offers": 0,
        "matched_requests": 0,
        "processing_time": "",
        "group": summary_group,
        "dealer_whatsapp": summary_whatsapp,
        "dealer_alias": summary_alias,
        "rows": [],
        "bulk_import": bulk_mode,
    }

    new_offers_for_matching: list[dict[str, Any]] = []
    offers_started_at = time.perf_counter()
    watch_cache: dict[tuple[str | None, str | None, str | None, str | None], dict[str, Any]] = {}
    duplicate_cache: dict[tuple[Any, ...], dict[str, Any]] = {}

    for line_index, watch in enumerate(offer_watches):
        summary["watches_parsed"] += 1
        if bulk_mode:
            watch_row, watch_created = _cached_find_or_create_watch(
                watch_cache,
                brand=watch.get("brand"),
                reference=watch.get("reference"),
                model=watch.get("model"),
                dial=watch.get("dial"),
                bracelet=watch.get("bracelet"),
            )
        else:
            watch_row, watch_created = find_or_create_watch(
                brand=watch.get("brand"),
                reference=watch.get("reference"),
                model=watch.get("model"),
                dial=watch.get("dial"),
                bracelet=watch.get("bracelet"),
            )
        if watch_created:
            summary["new_watches"] += 1

        if bulk_mode:
            active_offers: list[tuple[str, int]] = []
        else:
            active_offers = _get_active_offers(watch_row["id"])

        if bulk_mode:
            offer_row, offer_created = _cached_insert_offer(
                duplicate_cache,
                message_id=message["id"],
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
        else:
            offer_row, offer_created = insert_offer(
                message_id=message["id"],
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
            summary["new_offers"] += 1
            new_offers_for_matching.append(
                {
                    "line_index": line_index,
                    "offer_id": offer_row["id"],
                    "offer": _offer_match_payload(watch, watch_row, offer_row),
                }
            )
        else:
            summary["duplicate_offers"] += 1

        if bulk_mode:
            price_intelligence = _bulk_deferred_price_intelligence(is_duplicate=not offer_created)
        else:
            comparable_usd_prices, market_condition = _comparable_usd_prices(
                active_offers,
                exclude_offer_ids={offer_row["id"]},
                offer_condition=watch.get("condition"),
            )
            price_intelligence = _build_price_intelligence(
                watch.get("usd_price"),
                comparable_usd_prices,
                is_duplicate=not offer_created,
                market_condition=market_condition,
            )

        summary["rows"].append(
            _build_watch_row(
                watch,
                watch_created=watch_created,
                offer_created=offer_created,
                offer_id=offer_row["id"],
                request_matches=[],
                price_intelligence=price_intelligence,
            )
        )

    offers_elapsed_ms = int((time.perf_counter() - offers_started_at) * 1000)
    matching_started_at = time.perf_counter()

    import_status, status_reason = _import_status(
        summary,
        parse_status,
        offer_watches,
        classification=import_classification,
        bulk_mode=bulk_mode,
        sold_order_message=sold_order_message,
        sold_order_needs_review=sold_order_message
        and not sold_order_has_actionable_identity(parsed_watches),
    )
    summary["status_reason"] = status_reason
    summary["parsed_watches"] = list(parsed_watches)
    summary["message_type"] = parsed["message_type"]
    if is_buyer_request_message(text, parsed):
        summary["message_type"] = "request"
    if import_classification:
        summary["import_classification"] = import_classification
    if sold_order_message:
        summary["request_intent_kind"] = "sold_order"
        summary["request_urgency"] = "high"
        if not sold_order_has_actionable_identity(parsed_watches):
            summary["request_intent_needs_review"] = True
    if insufficient_evidence_watches:
        summary["insufficient_evidence_watches"] = len(insufficient_evidence_watches)
        summary["insufficient_evidence_details"] = [
            {
                "source_line": watch.get("source_line"),
                "gaps": describe_evidence_gaps(watch),
            }
            for watch in insufficient_evidence_watches
        ]

    preserve_sender = (
        has_valid_offers
        or import_classification in {"request_intent", "insufficient_evidence"}
    )
    log_dealer_whatsapp = summary_whatsapp if preserve_sender else ""
    log_dealer_alias = summary_alias if preserve_sender else None
    if not preserve_sender:
        summary["dealer_whatsapp"] = ""
        summary["dealer_alias"] = None

    elapsed = time.perf_counter() - started_at
    summary["processing_time"] = _format_processing_time(elapsed)
    summary["processing_time_ms"] = int(elapsed * 1000)
    summary["message_id"] = message["id"]
    summary["import_time"] = message_received_at.isoformat()

    import_log = insert_import_log(
        message_id=message["id"],
        import_time=message_received_at,
        group_name=summary_group,
        dealer_whatsapp=log_dealer_whatsapp,
        dealer_alias=log_dealer_alias,
        watches_parsed=summary["watches_parsed"],
        new_offers=summary["new_offers"],
        duplicate_offers=summary["duplicate_offers"],
        matched_requests=0,
        processing_time=summary["processing_time"],
        processing_time_ms=summary["processing_time_ms"],
        status=import_status,
        summary=summary,
        imported_by_user_id=imported_by_user_id,
    )

    matched_request_count = 0
    if business_import and not bulk_mode:
        for item in new_offers_for_matching:
            matches = process_offer_request_matches(
                import_log_id=import_log["id"],
                offer_id=item["offer_id"],
                offer=item["offer"],
            )
            matched_request_count += len(matches)
            row = summary["rows"][item["line_index"]]
            row["request_matches"] = _summary_request_matches(matches)
            row["results"] = _append_request_match_result(row.get("results") or [], matches)
            for match in matches:
                notify_request_match(
                    import_log_id=import_log["id"],
                    request_id=str(match["request_id"]),
                    offer_id=str(item["offer_id"]),
                    client_name=match.get("client_name") or "Client",
                    match_reason=match.get("match_reason") or "Request matched",
                )

    matching_elapsed_ms = int((time.perf_counter() - matching_started_at) * 1000)
    notifications_started_at = time.perf_counter()

    summary["matched_requests"] = matched_request_count
    if matched_request_count:
        update_import_log(
            import_log["id"],
            matched_requests=matched_request_count,
            summary=summary,
        )

    if business_import and not bulk_mode:
        record_import_notifications(
            import_log_id=import_log["id"],
            summary=summary,
            import_status=import_status,
        )

    notifications_elapsed_ms = int((time.perf_counter() - notifications_started_at) * 1000)
    elapsed = time.perf_counter() - started_at
    total_elapsed_ms = int(elapsed * 1000)
    if bulk_mode:
        logger.info(
            "Bulk import complete: bulk_import=True watches_count=%s created_offers=%s duplicate_offers=%s total_ms=%s",
            summary["watches_parsed"],
            summary["new_offers"],
            summary["duplicate_offers"],
            total_elapsed_ms,
        )
    else:
        logger.info(
            "Ingest timing: watches=%s new_offers=%s bulk=%s "
            "parse_ms=%s offers_ms=%s matching_ms=%s notifications_ms=%s total_ms=%s",
            summary["watches_parsed"],
            summary["new_offers"],
            bulk_mode,
            parse_elapsed_ms,
            offers_elapsed_ms,
            matching_elapsed_ms,
            notifications_elapsed_ms,
            total_elapsed_ms,
        )

    summary["import_log_id"] = import_log["id"]
    summary["status"] = import_status
    summary["whatsapp_message_id"] = normalized_whatsapp_message_id
    _finish_ingest_trace(trace, started_at=started_at, summary=summary)
    return summary


def _normalize_whatsapp_number(value: str) -> str:
    return value.strip()


def _get_active_offers(watch_id: str) -> list[tuple[str, int, str | None]]:
    """Return active business-dealer offer ids, USD prices, and conditions."""
    dealer_fields = (
        "dealers(contact_type)"
        if contact_type_column_supported()
        else "dealers(whatsapp_id)"
    )
    response = (
        get_client()
        .table("offers")
        .select(f"id, usd_price, condition, {dealer_fields}")
        .eq("watch_id", watch_id)
        .eq("status", "active")
        .execute()
    )
    offers: list[tuple[str, int, str | None]] = []
    for row in response.data or []:
        if not is_business_dealer_relation(row.get("dealers")):
            continue
        offer_id = row.get("id")
        usd_price = row.get("usd_price")
        if offer_id and usd_price is not None:
            offers.append((str(offer_id), int(usd_price), row.get("condition")))
    return offers


def _comparable_usd_prices(
    active_offers: list[tuple[str, int, str | None]],
    *,
    exclude_offer_ids: set[str],
    offer_condition: str | None,
) -> tuple[list[int], str | None]:
    """Return same-condition market comparables excluding the current offer."""
    normalized_offer = normalize_condition_value(offer_condition)
    if normalized_offer not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return [], None

    comparables: list[int] = []
    for offer_id, usd_price, condition in active_offers:
        if offer_id in exclude_offer_ids:
            continue
        normalized_market = normalize_condition_value(condition)
        if normalized_market != normalized_offer:
            continue
        comparables.append(usd_price)
    return comparables, normalized_offer


def _build_price_intelligence(
    usd_price: int | None,
    comparable_usd_prices: list[int],
    *,
    is_duplicate: bool,
    market_condition: str | None = None,
) -> dict[str, str | None]:
    """Compare an imported offer against same-condition active offers."""
    if is_duplicate:
        label = "Duplicate offer"
    elif not comparable_usd_prices:
        label = "No comparables"
    else:
        label = _price_intelligence_label(usd_price, comparable_usd_prices)

    previous_lowest = min(comparable_usd_prices) if comparable_usd_prices else None

    return {
        "rank": _format_rank(_price_rank(usd_price, comparable_usd_prices)),
        "previous_lowest_usd": _format_usd_amount(previous_lowest),
        "price_difference": _format_price_difference(usd_price, previous_lowest),
        "label": label,
        "label_class": _price_label_class(label),
        "market_condition": market_condition,
    }


def _price_intelligence_label(
    usd_price: int | None,
    comparable_usd_prices: list[int],
) -> str:
    if usd_price is None:
        return "Normal price"
    if not comparable_usd_prices:
        return "No comparables"

    previous_lowest = min(comparable_usd_prices)
    if usd_price < previous_lowest:
        return "New lowest price"
    if usd_price <= previous_lowest * 1.03:
        return "Good price"
    if usd_price <= previous_lowest * 1.10:
        return "Normal price"
    return "Expensive"


def _price_rank(usd_price: int | None, comparable_usd_prices: list[int]) -> int | None:
    if usd_price is None or not comparable_usd_prices:
        return None
    return sum(1 for price in comparable_usd_prices if price < usd_price) + 1


def _format_rank(rank: int | None) -> str:
    if rank is None:
        return "N/A"
    return str(rank)


def _format_usd_amount(amount: int | None) -> str:
    if amount is None:
        return "N/A"
    return f"${amount:,}"


def _format_price_difference(usd_price: int | None, previous_lowest: int | None) -> str:
    if usd_price is None or previous_lowest is None:
        return "N/A"

    difference = usd_price - previous_lowest
    if difference == 0:
        return "$0"
    if difference > 0:
        return f"+${difference:,}"
    return f"-${abs(difference):,}"


def _price_label_class(label: str) -> str:
    return {
        "New lowest price": "success",
        "Good price": "info",
        "Normal price": "secondary",
        "Expensive": "danger",
        "Duplicate offer": "dark",
        "No comparables": "secondary",
    }.get(label, "secondary")


def _offer_match_payload(
    watch: dict[str, Any],
    watch_row: dict[str, Any],
    offer_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "brand": watch.get("brand") or watch_row.get("brand"),
        "reference": watch.get("reference") or watch_row.get("reference"),
        "model": watch.get("model") or watch_row.get("model"),
        "dial": watch.get("dial") or watch_row.get("dial"),
        "nickname": watch.get("nickname"),
        "model_alias": watch.get("model_alias"),
        "condition": watch.get("condition") or offer_row.get("condition"),
        "production_year": watch.get("production_year") or offer_row.get("production_year"),
        "card_date": watch.get("card_date") or offer_row.get("card_date"),
        "original_price": watch.get("original_price") or watch.get("price"),
        "original_currency": watch.get("original_currency") or watch.get("currency"),
        "price": watch.get("price"),
        "currency": watch.get("currency"),
        "usd_price": watch.get("usd_price") or offer_row.get("usd_price"),
    }


def _summary_request_matches(matches: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "client_name": match.get("client_name") or "Client",
            "match_strength": match.get("match_strength", ""),
            "match_reason": match.get("match_reason", ""),
            "request_id": match.get("request_id", ""),
        }
        for match in matches
    ]


def _append_request_match_result(
    results: list[str],
    matches: list[dict[str, Any]],
) -> list[str]:
    if matches and "Request matched" not in results:
        results = [*results, "Request matched"]
    return results


def _build_watch_row(
    watch: dict[str, Any],
    *,
    watch_created: bool,
    offer_created: bool,
    offer_id: str,
    request_matches: list[dict[str, str]],
    price_intelligence: dict[str, str],
) -> dict[str, Any]:
    results = [
        "New watch" if watch_created else "Existing watch",
        "New offer" if offer_created else "Duplicate offer",
    ]
    if request_matches:
        results.append("Request matched")

    return {
        "reference": _reference_row_value(watch),
        "brand": _display_value(watch.get("brand")),
        "model": _optional_display_value(watch.get("model")),
        "nickname": watch.get("nickname"),
        "dial": _optional_display_value(watch.get("dial")),
        "bracelet": _optional_display_value(watch.get("bracelet")),
        "condition": watch.get("condition"),
        "raw_condition": watch.get("raw_condition"),
        "card_date": watch.get("card_date"),
        "original_price": watch.get("original_price") or watch.get("price"),
        "original_currency": watch.get("original_currency") or watch.get("currency"),
        "usd_price": watch.get("usd_price"),
        "notes": watch.get("notes"),
        "price": _format_price(
            watch.get("original_price") or watch.get("price"),
            watch.get("original_currency") or watch.get("currency"),
        ),
        "results": results,
        "rank": price_intelligence["rank"],
        "previous_lowest_usd": price_intelligence["previous_lowest_usd"],
        "price_difference": price_intelligence["price_difference"],
        "price_label": price_intelligence["label"],
        "price_label_class": price_intelligence["label_class"],
        "market_condition": price_intelligence.get("market_condition"),
        "offer_id": offer_id,
        "request_matches": request_matches,
    }


def _reference_row_value(watch: dict[str, Any]) -> str:
    reference = watch.get("reference")
    if reference:
        return _display_value(str(reference))

    model_alias = watch.get("model_alias")
    if isinstance(model_alias, dict) and model_alias.get("reference_status") == "Unknown":
        return "Unknown"

    return "N/A"


def _optional_display_value(value: str | None) -> str | None:
    if not value:
        return None
    return _display_value(value)


def _display_value(value: str | None) -> str:
    if not value:
        return "N/A"
    return value.title() if value.islower() else value


def _format_price(amount: int | None, currency: str | None) -> str:
    if amount is None:
        return "N/A"

    formatted = f"{amount:,}"
    if currency == "USD":
        return f"${formatted}"
    if currency == "EUR":
        return f"€{formatted}"
    if currency == "GBP":
        return f"£{formatted}"
    if currency == "CHF":
        return f"CHF {formatted}"
    if currency == "HKD":
        return f"HK${formatted}"
    if currency:
        return f"{formatted} {currency}"
    return formatted


def _format_processing_time(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def _parse_status(parsed: dict[str, Any]) -> str:
    if parsed["message_type"] == "unknown":
        return "partial"
    if parsed["watches"]:
        return "success"
    return "partial"


def _watch_missing_fields(watch: dict[str, Any]) -> list[str]:
    """Return important watch fields missing from a parsed watch (ingest-level check only)."""
    missing: list[str] = []
    if not watch.get("brand"):
        missing.append("brand")
    if not watch.get("reference"):
        missing.append("reference")
    if (
        watch.get("original_price") is None
        and watch.get("price") is None
        and watch.get("usd_price") is None
    ):
        missing.append("price")
    return missing


def _import_status(
    summary: IngestSummary,
    parse_status: str,
    watches: list[dict[str, Any]],
    *,
    classification: str | None = None,
    bulk_mode: bool = False,
    sold_order_message: bool = False,
    sold_order_needs_review: bool = False,
) -> tuple[str, str]:
    if parse_status == "failed":
        return "error", "Technical failure during parsing."

    if classification == "request_intent":
        if sold_order_message and sold_order_needs_review:
            return (
                "request_intent",
                "Sold order WTB needs review — missing reference or brand details.",
            )
        if sold_order_message:
            return (
                "request_intent",
                "Sold order detected. Dealer urgently needs to source this watch.",
            )
        return "request_intent", "Buyer request detected. Offer was not created."

    if classification == "noise":
        return "noise", "Chat noise detected. No watch offer was identified."

    if classification == "insufficient_evidence":
        return "insufficient_evidence", INSUFFICIENT_EVIDENCE_REASON

    if summary["watches_parsed"] == 0:
        return "no_watch_detected", "No watch offer was detected in this message."

    watches_needing_review: list[str] = []
    for line_index, watch in enumerate(watches, start=1):
        if watch.get("retail_price_only"):
            watches_needing_review.append(f"watch {line_index}: retail price only")
            continue
        missing = _watch_missing_fields(watch)
        if missing:
            watches_needing_review.append(
                f"watch {line_index}: missing {', '.join(missing)}"
            )

    if watches_needing_review:
        if bulk_mode:
            watches_parsed = summary["watches_parsed"]
            duplicate_count = summary["duplicate_offers"]
            reason = f"Successfully parsed {watches_parsed} watch offer(s)."
            if duplicate_count:
                reason += f" {duplicate_count} duplicate offer(s) were skipped."
            return "success", reason
        reason = "Important fields are missing — " + "; ".join(watches_needing_review)
        return "warning", reason

    duplicate_count = summary["duplicate_offers"]
    if duplicate_count:
        return (
            "success",
            f"Successfully parsed {summary['watches_parsed']} watch offer(s). "
            f"{duplicate_count} duplicate offer(s) were skipped.",
        )

    return (
        "success",
        f"Successfully parsed {summary['watches_parsed']} watch offer(s).",
    )


def main() -> None:
    try:
        text = read_message()
        summary = ingest_message(text)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Saved {summary['new_offers']} offer(s).")


if __name__ == "__main__":
    main()
