"""Supabase database helpers for MRV4ULT AI."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)

from condition_normalizer import normalize_condition_value
from contact_classification import (
    ALL_CONTACT_TYPES,
    CONTACT_TYPE_CLIENT,
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_REMOVED,
    IMPORT_PLACEHOLDER_WHATSAPP_ID,
    is_business_contact,
    is_client_contact,
    normalize_contact_type,
)
from request_matching import match_offer_against_requests

try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover - test environments without postgrest
    APIError = Exception  # type: ignore[misc, assignment]

Record = dict[str, Any]

REQUEST_STATUSES = frozenset({"open", "matched", "closed", "active"})
OPEN_REQUEST_STATUSES = ("open", "active")

WATCH_IDENTITY_FIELDS = ("brand", "reference", "dial", "bracelet")

_client: Client | None = None


def get_client() -> Client:
    """Return a shared Supabase client configured from environment variables."""
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url:
        raise RuntimeError("SUPABASE_URL is not set. Add it to your .env file.")
    if not key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is not set. Add it to your .env file."
        )

    _client = create_client(url, key)
    return _client


_contact_type_column_supported: bool | None = None
_client_profiles_supported: bool | None = None
_watch_knowledge_supported: bool | None = None
_watch_identification_supported: bool | None = None


def reset_contact_type_column_cache() -> None:
    """Reset cached contact_type column detection (for tests)."""
    global _contact_type_column_supported
    _contact_type_column_supported = None


def reset_client_profiles_cache() -> None:
    """Reset cached client_profiles table detection (for tests)."""
    global _client_profiles_supported
    _client_profiles_supported = None


def reset_watch_knowledge_cache() -> None:
    """Reset cached watch knowledge table detection (for tests)."""
    global _watch_knowledge_supported, _watch_identification_supported
    _watch_knowledge_supported = None
    _watch_identification_supported = None


def contact_type_column_supported() -> bool:
    """Return True when dealers.contact_type exists in the connected database."""
    global _contact_type_column_supported
    if _contact_type_column_supported is not None:
        return _contact_type_column_supported

    try:
        get_client().table("dealers").select("contact_type").limit(1).execute()
        _contact_type_column_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code == "42703" or "contact_type" in message:
            _contact_type_column_supported = False
            logger.warning(
                "dealers.contact_type column missing; apply "
                "docs/migrations/sprint_27_1_contact_classification.sql"
            )
        else:
            raise
    return _contact_type_column_supported


def client_profiles_supported() -> bool:
    """Return True when client_profiles exists in the connected database."""
    global _client_profiles_supported
    if _client_profiles_supported is not None:
        return _client_profiles_supported

    try:
        get_client().table("client_profiles").select("id").limit(1).execute()
        _client_profiles_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code in {"42P01", "PGRST205"} or "client_profiles" in message:
            _client_profiles_supported = False
        else:
            raise
    return _client_profiles_supported


def watch_knowledge_supported() -> bool:
    """Return True when Sprint 30 watch knowledge tables exist."""
    global _watch_knowledge_supported
    if _watch_knowledge_supported is not None:
        return _watch_knowledge_supported

    try:
        get_client().table("brand_aliases").select("id").limit(1).execute()
        get_client().table("unknown_brands").select("id").limit(1).execute()
        _watch_knowledge_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code in {"42P01", "PGRST205"} or "brand_aliases" in message or "unknown_brands" in message:
            _watch_knowledge_supported = False
        else:
            raise
    return _watch_knowledge_supported


def _legacy_contact_type(dealer: Record) -> str:
    """Infer contact type before the Sprint 27.1 migration is applied."""
    whatsapp_id = str(dealer.get("whatsapp_id") or "").strip()
    if whatsapp_id == IMPORT_PLACEHOLDER_WHATSAPP_ID:
        return CONTACT_TYPE_REMOVED
    return CONTACT_TYPE_DEALER


def dealer_contact_type(dealer: Record | None) -> str:
    """Return the effective normalized contact type for one dealer row."""
    if not dealer:
        return CONTACT_TYPE_REMOVED
    if contact_type_column_supported():
        return normalize_contact_type(str(dealer.get("contact_type") or ""))
    return normalize_contact_type(_legacy_contact_type(dealer))


def dealer_is_business_visible(dealer: Record | None) -> bool:
    """Return True when a dealer may appear in business-facing views."""
    return is_business_contact(dealer_contact_type(dealer))


def is_business_dealer_relation(dealer_data: Any) -> bool:
    """Return True when nested Supabase dealer data belongs in business views."""
    return dealer_is_business_visible(_nested_dealer_record(dealer_data))


def _nested_dealer_record(dealer: Any) -> Record:
    if isinstance(dealer, list) and dealer:
        dealer = dealer[0]
    if isinstance(dealer, dict):
        return dealer
    return {}


def _offer_from_business_dealer(offer: Record) -> bool:
    return dealer_is_business_visible(_nested_dealer_record(offer.get("dealers")))


def insert_message(
    group_id: str,
    dealer_id: str,
    raw_text: str,
    message_type: str,
    *,
    source: str = "whatsapp",
    whatsapp_message_id: str | None = None,
    received_at: datetime | None = None,
    parsed_at: datetime | None = None,
    parser_version: str | None = None,
    parse_status: str | None = None,
    parse_error: str | None = None,
) -> Record:
    """Insert a row into messages and return the created record."""
    payload: Record = {
        "group_id": group_id,
        "dealer_id": dealer_id,
        "raw_text": raw_text,
        "message_type": message_type,
        "source": source,
        "whatsapp_message_id": whatsapp_message_id,
        "received_at": (received_at or datetime.now(timezone.utc)).isoformat(),
        "parsed_at": parsed_at.isoformat() if parsed_at else None,
        "parser_version": parser_version,
        "parse_status": parse_status,
        "parse_error": parse_error,
    }

    response = get_client().table("messages").insert(payload).execute()
    return _first_row(response.data, "messages")


def find_or_create_watch(
    brand: str | None = None,
    reference: str | None = None,
    model: str | None = None,
    dial: str | None = None,
    bracelet: str | None = None,
) -> tuple[Record, bool]:
    """Find an existing watch by identity fields, or create a new one."""
    _ensure_logging()

    identity = {
        field: _normalize_watch_value(locals()[field])
        for field in WATCH_IDENTITY_FIELDS
    }

    for row in _find_watch_candidates(identity):
        if _watch_identity_matches(row, identity):
            logger.info(
                "Found existing watch: brand=%s reference=%s dial=%s bracelet=%s id=%s",
                row.get("brand"),
                row.get("reference"),
                row.get("dial"),
                row.get("bracelet"),
                row.get("id"),
            )
            return row, False

    payload: Record = {
        "brand": _storage_value(brand),
        "reference": _storage_value(reference),
        "model": _storage_value(model),
        "dial": _storage_value(dial),
        "bracelet": _storage_value(bracelet),
    }
    response = get_client().table("watches").insert(payload).execute()
    created = _first_row(response.data, "watches")
    logger.info(
        "Created new watch: brand=%s reference=%s dial=%s bracelet=%s id=%s",
        created.get("brand"),
        created.get("reference"),
        created.get("dial"),
        created.get("bracelet"),
        created.get("id"),
    )
    return created, True


def _normalize_watch_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned.lower()


def _storage_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _watch_identity_matches(row: Record, identity: dict[str, str | None]) -> bool:
    for field in WATCH_IDENTITY_FIELDS:
        row_value = _normalize_watch_value(row.get(field))
        if row_value != identity[field]:
            return False
    return True


def _find_watch_candidates(identity: dict[str, str | None]) -> list[Record]:
    query = get_client().table("watches").select("*")

    if identity["reference"]:
        query = query.ilike("reference", identity["reference"])
    elif identity["brand"]:
        query = query.ilike("brand", identity["brand"])
    else:
        query = query.is_("brand", "null").is_("reference", "null")

    response = query.execute()
    return response.data or []


def _ensure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")


def find_duplicate_offer(
    watch_id: str,
    dealer_id: str,
    *,
    original_price: int | None = None,
    original_currency: str | None = None,
    condition: str | None = None,
    card_date: str | None = None,
    production_year: int | None = None,
) -> Record | None:
    """Return an existing active offer with the same listing fields, if any."""
    query = (
        get_client()
        .table("offers")
        .select("*")
        .eq("status", "active")
        .eq("watch_id", watch_id)
        .eq("dealer_id", dealer_id)
    )

    for field, value in (
        ("original_price", original_price),
        ("original_currency", _storage_value(original_currency)),
        ("condition", _storage_value(normalize_condition_value(condition))),
        ("card_date", _storage_value(card_date)),
        ("production_year", production_year),
    ):
        if value is None:
            query = query.is_(field, "null")
        else:
            query = query.eq(field, value)

    response = query.limit(1).execute()
    if response.data:
        return response.data[0]
    return None


def get_watch_by_id(watch_id: str) -> Record | None:
    """Return a watch row by id, or None if it does not exist."""
    response = (
        get_client().table("watches").select("*").eq("id", watch_id).limit(1).execute()
    )
    if not response.data:
        return None
    return response.data[0]


def get_active_offers_for_watch(watch_id: str) -> list[Record]:
    """Return active offers for a watch with dealer, group, and message metadata."""
    dealer_fields = (
        "dealers(display_name, phone_number, whatsapp_id, contact_type)"
        if contact_type_column_supported()
        else "dealers(display_name, phone_number, whatsapp_id)"
    )
    response = (
        get_client()
        .table("offers")
        .select(
            "dealer_id, original_price, original_currency, usd_price, card_date, condition, "
            f"{dealer_fields}, "
            "messages(received_at, group_id, groups(name))"
        )
        .eq("watch_id", watch_id)
        .eq("status", "active")
        .execute()
    )
    return [
        offer
        for offer in response.data or []
        if _offer_from_business_dealer(offer)
    ]


def create_request(
    *,
    client_name: str,
    brand: str | None = None,
    reference: str | None = None,
    model: str | None = None,
    alias: str | None = None,
    dial: str | None = None,
    condition: str | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    max_price: int | None = None,
    currency: str | None = None,
    notes: str | None = None,
    status: str = "open",
    client_id: str | None = None,
) -> Record:
    """Create a manual client request."""
    payload: Record = {
        "client_name": client_name.strip(),
        "brand": _storage_value(brand),
        "reference": _storage_value(reference),
        "model": _storage_value(model),
        "alias": _storage_value(alias),
        "dial": _storage_value(dial),
        "condition": _storage_value(normalize_condition_value(condition)),
        "min_year": min_year,
        "max_year": max_year,
        "max_price": max_price,
        "currency": _storage_value(currency),
        "notes": _storage_value(notes),
        "status": status,
    }
    if client_id:
        payload["client_id"] = client_id
    response = get_client().table("requests").insert(payload).execute()
    return _first_row(response.data, "requests")


def list_requests(*, status: str | None = None) -> list[Record]:
    """Return client requests, optionally filtered by status."""
    query = get_client().table("requests").select("*").order("created_at", desc=True)
    if status:
        if status == "open":
            query = query.in_("status", list(OPEN_REQUEST_STATUSES))
        else:
            query = query.eq("status", status)
    response = query.execute()
    return response.data or []


def get_open_requests() -> list[Record]:
    """Return requests eligible for offer matching."""
    response = (
        get_client()
        .table("requests")
        .select("*")
        .in_("status", list(OPEN_REQUEST_STATUSES))
        .execute()
    )
    return response.data or []


def update_request_status(request_id: str, status: str) -> Record:
    """Update the status of a client request."""
    response = (
        get_client()
        .table("requests")
        .update({"status": status})
        .eq("id", request_id)
        .execute()
    )
    return _first_row(response.data, "requests")


def create_request_match(
    *,
    request_id: str,
    offer_id: str,
    import_log_id: str | None,
    match_strength: str,
    match_reason: str,
) -> Record:
    """Persist a match between a request and an offer."""
    payload: Record = {
        "request_id": request_id,
        "offer_id": offer_id,
        "import_log_id": import_log_id,
        "match_strength": match_strength,
        "match_reason": match_reason,
    }
    response = get_client().table("request_matches").insert(payload).execute()
    return _first_row(response.data, "request_matches")


def list_request_matches_by_request_ids(request_ids: list[str]) -> list[Record]:
    """Return raw request_matches rows for the given request ids."""
    if not request_ids:
        return []

    response = (
        get_client()
        .table("request_matches")
        .select("*")
        .in_("request_id", request_ids)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def get_offers_by_ids(offer_ids: list[str]) -> dict[str, Record]:
    """Return offers keyed by id."""
    if not offer_ids:
        return {}

    response = (
        get_client()
        .table("offers")
        .select("id, watch_id, original_price, original_currency, usd_price, condition, card_date")
        .in_("id", offer_ids)
        .execute()
    )
    return {str(row["id"]): row for row in response.data or []}


def get_watches_by_ids(watch_ids: list[str]) -> dict[str, Record]:
    """Return watches keyed by id."""
    if not watch_ids:
        return {}

    response = (
        get_client()
        .table("watches")
        .select("id, brand, reference, model, dial")
        .in_("id", watch_ids)
        .execute()
    )
    return {str(row["id"]): row for row in response.data or []}


def get_import_logs_by_ids(import_log_ids: list[str]) -> dict[str, Record]:
    """Return import logs keyed by id."""
    if not import_log_ids:
        return {}

    response = (
        get_client()
        .table("import_logs")
        .select("id, import_time, group_name, dealer_alias, dealer_whatsapp")
        .in_("id", import_log_ids)
        .execute()
    )
    return {str(row["id"]): row for row in response.data or []}


def combine_request_match_records(
    matches: list[Record],
    *,
    offers_by_id: dict[str, Record],
    watches_by_id: dict[str, Record],
    import_logs_by_id: dict[str, Record],
) -> list[Record]:
    """Attach offer, watch, and import log data to request match rows."""
    enriched: list[Record] = []
    for match in matches:
        offer = offers_by_id.get(str(match.get("offer_id")), {})
        watch_id = offer.get("watch_id")
        watch = watches_by_id.get(str(watch_id), {}) if watch_id else {}
        import_log_id = match.get("import_log_id")
        import_log = (
            import_logs_by_id.get(str(import_log_id), {})
            if import_log_id
            else {}
        )
        enriched.append(
            {
                **match,
                "offer": offer,
                "watch": watch,
                "import_log": import_log,
            }
        )
    return enriched


def load_enriched_request_matches_by_request_ids(
    request_ids: list[str],
) -> dict[str, list[Record]]:
    """Load request matches with related offer, watch, and import log data."""
    matches = list_request_matches_by_request_ids(request_ids)
    if not matches:
        return {}

    offer_ids = sorted({str(match["offer_id"]) for match in matches if match.get("offer_id")})
    import_log_ids = sorted(
        {str(match["import_log_id"]) for match in matches if match.get("import_log_id")}
    )

    offers_by_id = get_offers_by_ids(offer_ids)
    watch_ids = sorted(
        {str(offer["watch_id"]) for offer in offers_by_id.values() if offer.get("watch_id")}
    )
    watches_by_id = get_watches_by_ids(watch_ids)
    import_logs_by_id = get_import_logs_by_ids(import_log_ids)

    enriched = combine_request_match_records(
        matches,
        offers_by_id=offers_by_id,
        watches_by_id=watches_by_id,
        import_logs_by_id=import_logs_by_id,
    )

    grouped: dict[str, list[Record]] = {}
    for match in enriched:
        request_id = str(match["request_id"])
        grouped.setdefault(request_id, []).append(match)
    return grouped


def list_request_matches_for_request(request_id: str) -> list[Record]:
    """Return enriched offer matches linked to a client request."""
    return load_enriched_request_matches_by_request_ids([request_id]).get(request_id, [])


def list_request_matches_for_import(import_log_id: str) -> list[Record]:
    """Return all request matches recorded during an import."""
    response = (
        get_client()
        .table("request_matches")
        .select("*, requests(id, client_name, brand, reference, model, alias, status)")
        .eq("import_log_id", import_log_id)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def list_request_matches_for_offer(offer_id: str) -> list[Record]:
    """Return request matches linked to a single offer."""
    response = (
        get_client()
        .table("request_matches")
        .select("*, requests(id, client_name, brand, reference, model, alias, status)")
        .eq("offer_id", offer_id)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def process_offer_request_matches(
    *,
    import_log_id: str,
    offer_id: str,
    offer: Record,
) -> list[Record]:
    """Match a new offer against open requests and persist matches."""
    open_requests = get_open_requests()
    request_lookup = {str(request["id"]): request for request in open_requests}
    matches = match_offer_against_requests(offer, open_requests)
    created: list[Record] = []
    for match in matches:
        record = create_request_match(
            request_id=match["request_id"],
            offer_id=offer_id,
            import_log_id=import_log_id,
            match_strength=match["match_strength"],
            match_reason=match["match_reason"],
        )
        update_request_status(match["request_id"], "matched")
        request = request_lookup.get(match["request_id"], {})
        created.append(
            {
                **record,
                "request_id": match["request_id"],
                "client_name": request.get("client_name") or "Client",
                "match_strength": match["match_strength"],
                "match_reason": match["match_reason"],
            }
        )
        logger.info(
            "Request match: request=%s offer=%s strength=%s",
            match["request_id"],
            offer_id,
            match["match_strength"],
        )
    return created


def create_notification(
    *,
    type: str,
    title: str,
    message: str,
    related_import_log_id: str | None = None,
    related_request_id: str | None = None,
    related_offer_id: str | None = None,
) -> Record:
    """Persist a dashboard notification."""
    payload: Record = {
        "type": type,
        "title": title.strip(),
        "message": message.strip(),
        "related_import_log_id": related_import_log_id,
        "related_request_id": related_request_id,
        "related_offer_id": related_offer_id,
        "is_read": False,
    }
    response = get_client().table("notifications").insert(payload).execute()
    return _first_row(response.data, "notifications")


def list_notifications() -> list[Record]:
    """Return notifications with unread items first."""
    response = (
        get_client()
        .table("notifications")
        .select("*")
        .order("is_read")
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def count_unread_notifications() -> int:
    """Return the number of unread notifications."""
    response = (
        get_client()
        .table("notifications")
        .select("id", count="exact")
        .eq("is_read", False)
        .limit(0)
        .execute()
    )
    return int(response.count or 0)


def mark_notification_read(notification_id: str) -> Record:
    """Mark one notification as read."""
    response = (
        get_client()
        .table("notifications")
        .update({"is_read": True})
        .eq("id", notification_id)
        .execute()
    )
    return _first_row(response.data, "notifications")


def mark_all_notifications_read() -> int:
    """Mark every unread notification as read."""
    response = (
        get_client()
        .table("notifications")
        .update({"is_read": True})
        .eq("is_read", False)
        .execute()
    )
    return len(response.data or [])


def delete_notification(notification_id: str) -> None:
    """Delete one notification for the whole team."""
    cleaned_id = notification_id.strip()
    if not cleaned_id:
        raise ValueError("Notification id is required")
    get_client().table("notifications").delete().eq("id", cleaned_id).execute()


def delete_read_notifications() -> int:
    """Delete all read notifications for the whole team."""
    response = (
        get_client()
        .table("notifications")
        .delete()
        .eq("is_read", True)
        .execute()
    )
    return len(response.data or [])


def delete_all_notifications() -> int:
    """Delete every notification for the whole team."""
    response = (
        get_client()
        .table("notifications")
        .delete()
        .not_.is_("created_at", "null")
        .execute()
    )
    return len(response.data or [])


def update_import_log(
    import_log_id: str,
    *,
    matched_requests: int,
    summary: Record,
) -> Record:
    """Update import log counters and summary after request matching."""
    response = (
        get_client()
        .table("import_logs")
        .update({"matched_requests": matched_requests, "summary": summary})
        .eq("id", import_log_id)
        .execute()
    )
    return _first_row(response.data, "import_logs")


def patch_import_log(import_log_id: str, **fields: Any) -> Record:
    """Update selected import log fields."""
    if not fields:
        raise ValueError("No fields to update")
    response = (
        get_client()
        .table("import_logs")
        .update(fields)
        .eq("id", import_log_id)
        .execute()
    )
    return _first_row(response.data, "import_logs")


def mark_import_parser_reviewed(import_log_id: str) -> Record:
    """Mark a needs-review import as reviewed for the whole team."""
    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    summary = dict(import_log.get("summary") or {})
    summary["parser_reviewed"] = True
    return patch_import_log(import_log_id, status="success", summary=summary)


def mark_import_parser_issue_ignored(import_log_id: str) -> Record:
    """Hide a parser review issue from the default review queue."""
    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    summary = dict(import_log.get("summary") or {})
    summary["parser_review_ignored"] = True
    return patch_import_log(import_log_id, summary=summary)


def get_request(request_id: str) -> Record | None:
    """Return one client request by id."""
    response = (
        get_client()
        .table("requests")
        .select("*")
        .eq("id", request_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def insert_import_log(
    *,
    message_id: str,
    import_time: datetime,
    group_name: str,
    dealer_whatsapp: str,
    dealer_alias: str | None,
    watches_parsed: int,
    new_offers: int,
    duplicate_offers: int,
    matched_requests: int,
    processing_time: str,
    processing_time_ms: int,
    status: str,
    summary: Record,
) -> Record:
    """Persist a completed import for the activity dashboard."""
    payload: Record = {
        "message_id": message_id,
        "import_time": import_time.isoformat(),
        "group_name": group_name,
        "dealer_whatsapp": dealer_whatsapp,
        "dealer_alias": dealer_alias,
        "watches_parsed": watches_parsed,
        "new_offers": new_offers,
        "duplicate_offers": duplicate_offers,
        "matched_requests": matched_requests,
        "processing_time": processing_time,
        "processing_time_ms": processing_time_ms,
        "status": status,
        "summary": summary,
    }
    response = get_client().table("import_logs").insert(payload).execute()
    return _first_row(response.data, "import_logs")


def list_import_logs() -> list[Record]:
    """Return all import logs in reverse chronological order."""
    response = (
        get_client()
        .table("import_logs")
        .select("*")
        .order("import_time", desc=True)
        .execute()
    )
    return response.data or []


def get_import_log(import_log_id: str) -> Record | None:
    """Return one import log by id."""
    response = (
        get_client()
        .table("import_logs")
        .select("*")
        .eq("id", import_log_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def cleanup_ignored_messages(days: int = 30) -> int:
    """Delete ignored import logs older than the given number of days."""
    if days < 0:
        raise ValueError("days must be zero or greater")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    response = (
        get_client()
        .table("import_logs")
        .delete()
        .eq("status", "no_watch_detected")
        .lt("import_time", cutoff.isoformat())
        .execute()
    )
    return len(response.data or [])


def get_message_by_id(message_id: str) -> Record | None:
    """Return a message row by id."""
    response = (
        get_client()
        .table("messages")
        .select("id, raw_text, received_at")
        .eq("id", message_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def _get_watch_by_id(watch_id: str) -> Record:
    watch = get_watch_by_id(watch_id)
    if watch is None:
        raise RuntimeError(f"Watch not found: {watch_id}")
    return watch


def insert_offer(
    message_id: str,
    watch_id: str,
    dealer_id: str,
    *,
    condition: str | None = None,
    production_year: int | None = None,
    card_date: str | None = None,
    notes: str | None = None,
    original_price: int | None = None,
    original_currency: str | None = None,
    usd_price: int | None = None,
    exchange_rate_to_usd: float | None = None,
    source_line: str | None = None,
    line_index: int = 0,
    is_duplicate: bool = False,
    duplicate_of_id: str | None = None,
    status: str = "active",
) -> tuple[Record, bool]:
    """Insert an offer unless an identical active offer already exists."""
    normalized_currency = _storage_value(original_currency)
    normalized_condition = _storage_value(normalize_condition_value(condition))
    normalized_card_date = _storage_value(card_date)

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
        print("Duplicate offer found")
        return existing, False

    payload: Record = {
        "message_id": message_id,
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "condition": normalized_condition,
        "production_year": production_year,
        "card_date": normalized_card_date,
        "notes": _storage_value(notes),
        "original_price": original_price,
        "original_currency": normalized_currency,
        "usd_price": usd_price,
        "exchange_rate_to_usd": exchange_rate_to_usd,
        "source_line": _storage_value(source_line),
        "line_index": line_index,
        "is_duplicate": is_duplicate,
        "duplicate_of_id": duplicate_of_id,
        "status": status,
    }

    response = get_client().table("offers").insert(payload).execute()
    created = _first_row(response.data, "offers")
    print("Created new offer")
    return created, True


def list_dealers() -> list[Record]:
    """Return business dealer contacts with at least one offer."""
    dealer_ids = _dealer_ids_with_offers()
    query = get_client().table("dealers").select("*").order("display_name")
    if contact_type_column_supported():
        query = query.eq("contact_type", "dealer")
    response = query.execute()
    return [
        dealer
        for dealer in response.data or []
        if str(dealer.get("id")) in dealer_ids and dealer_is_business_visible(dealer)
    ]


def dealer_has_offers(dealer_id: str) -> bool:
    """Return True when a dealer has at least one stored offer."""
    response = (
        get_client()
        .table("offers")
        .select("id")
        .eq("dealer_id", dealer_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def _dealer_ids_with_offers() -> set[str]:
    response = get_client().table("offers").select("dealer_id").execute()
    return {
        str(row["dealer_id"])
        for row in response.data or []
        if row.get("dealer_id")
    }


def dealer_ids_with_offers() -> set[str]:
    """Return dealer ids that have at least one stored offer."""
    return _dealer_ids_with_offers()


def list_contacts() -> list[Record]:
    """Return all WhatsApp contacts for classification management."""
    response = (
        get_client()
        .table("dealers")
        .select("*")
        .order("display_name")
        .execute()
    )
    return response.data or []


def list_contacts_for_import_lookup() -> list[Record]:
    """Return contact classification fields used to filter business import logs."""
    fields = (
        "id, whatsapp_id, phone_number, contact_type"
        if contact_type_column_supported()
        else "id, whatsapp_id, phone_number"
    )
    response = get_client().table("dealers").select(fields).execute()
    rows: list[Record] = []
    for dealer in response.data or []:
        row = dict(dealer)
        if not contact_type_column_supported():
            row["contact_type"] = _legacy_contact_type(row)
        rows.append(row)
    return rows


def update_dealer_contact_type(dealer_id: str, contact_type: str) -> Record:
    """Update the privacy classification for one contact."""
    if not contact_type_column_supported():
        raise RuntimeError(
            "Contact classification requires dealers.contact_type. Apply "
            "docs/migrations/sprint_27_1_contact_classification.sql in Supabase."
        )
    if contact_type not in ALL_CONTACT_TYPES:
        raise ValueError(f"Unsupported contact type: {contact_type}")

    response = (
        get_client()
        .table("dealers")
        .update({"contact_type": contact_type})
        .eq("id", dealer_id)
        .execute()
    )
    return _first_row(response.data, "dealers")


def get_dealer_by_id(dealer_id: str) -> Record | None:
    """Return a dealer row by id, or None if it does not exist."""
    response = (
        get_client().table("dealers").select("*").eq("id", dealer_id).limit(1).execute()
    )
    if not response.data:
        return None
    return response.data[0]


def list_offer_intelligence_rows(*, dealer_id: str | None = None) -> list[Record]:
    """Return offer rows used for dealer intelligence aggregation."""
    dealer_fields = (
        "dealers(contact_type)"
        if contact_type_column_supported()
        else "dealers(whatsapp_id)"
    )
    query = (
        get_client()
        .table("offers")
        .select(f"dealer_id, watch_id, status, usd_price, messages(received_at), {dealer_fields}")
    )
    if dealer_id:
        query = query.eq("dealer_id", dealer_id)
    response = query.execute()
    return [
        offer
        for offer in response.data or []
        if _offer_from_business_dealer(offer)
    ]


def get_active_offers_for_dealer(dealer_id: str) -> list[Record]:
    """Return active offers for a business dealer with watch, group, and message metadata."""
    dealer_fields = (
        "dealers(contact_type)"
        if contact_type_column_supported()
        else "dealers(whatsapp_id)"
    )
    response = (
        get_client()
        .table("offers")
        .select(
            "id, watch_id, original_price, original_currency, usd_price, card_date, condition, "
            "watches(brand, reference, model, dial, bracelet), "
            "messages(received_at, group_id, groups(name)), "
            f"{dealer_fields}"
        )
        .eq("dealer_id", dealer_id)
        .eq("status", "active")
        .execute()
    )
    return [
        offer
        for offer in response.data or []
        if _offer_from_business_dealer(offer)
    ]


def list_active_sourcing_offers() -> list[Record]:
    """Return active business offers with watch, dealer, and message metadata for sourcing."""
    dealer_fields = (
        "dealers(id, display_name, phone_number, whatsapp_id, contact_type)"
        if contact_type_column_supported()
        else "dealers(id, display_name, phone_number, whatsapp_id)"
    )
    response = (
        get_client()
        .table("offers")
        .select(
            "id, dealer_id, watch_id, original_price, original_currency, usd_price, "
            "card_date, condition, production_year, "
            "watches(brand, reference, model, dial, bracelet), "
            "messages(received_at), "
            f"{dealer_fields}"
        )
        .eq("status", "active")
        .execute()
    )
    return [
        offer
        for offer in response.data or []
        if _offer_from_business_dealer(offer)
    ]


def list_clients() -> list[Record]:
    """Return CRM client contacts."""
    query = get_client().table("dealers").select("*").order("display_name")
    if contact_type_column_supported():
        query = query.eq("contact_type", CONTACT_TYPE_CLIENT)
    response = query.execute()
    return [
        client
        for client in response.data or []
        if is_client_contact(dealer_contact_type(client))
    ]


def get_client_by_id(client_id: str) -> Record | None:
    """Return one client contact row, or None when not a client."""
    client = get_dealer_by_id(client_id)
    if client is None or not is_client_contact(dealer_contact_type(client)):
        return None
    return client


def list_client_profiles_by_client_ids(client_ids: list[str]) -> dict[str, Record]:
    """Return client profiles keyed by client id."""
    if not client_ids or not client_profiles_supported():
        return {}

    response = (
        get_client()
        .table("client_profiles")
        .select("*")
        .in_("client_id", client_ids)
        .execute()
    )
    return {str(row["client_id"]): row for row in response.data or []}


def get_client_profile(client_id: str) -> Record:
    """Return the CRM profile for one client, creating a default row if needed."""
    if not client_profiles_supported():
        from client_intelligence import default_client_profile

        return default_client_profile()

    response = (
        get_client()
        .table("client_profiles")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    )
    if response.data:
        return response.data[0]
    return create_client_profile(client_id)


def create_client_profile(client_id: str) -> Record:
    """Create a default CRM profile for one client."""
    if not client_profiles_supported():
        raise RuntimeError(
            "Client profiles require client_profiles table. Apply "
            "docs/migrations/sprint_28_clients.sql in Supabase."
        )

    response = (
        get_client()
        .table("client_profiles")
        .insert({"client_id": client_id, "status": "active"})
        .execute()
    )
    return _first_row(response.data, "client_profiles")


def create_client_contact(*, name: str, phone_number: str | None = None) -> Record:
    """Create a manual CRM client contact and profile."""
    from uuid import uuid4

    if not name.strip():
        raise ValueError("Client name is required.")

    payload: Record = {
        "whatsapp_id": f"crm-{uuid4()}",
        "phone_number": _storage_value(phone_number),
        "display_name": name.strip(),
        "is_active": True,
    }
    if contact_type_column_supported():
        payload["contact_type"] = CONTACT_TYPE_CLIENT

    response = get_client().table("dealers").insert(payload).execute()
    client = _first_row(response.data, "dealers")
    if client_profiles_supported():
        create_client_profile(str(client["id"]))
    return client


def update_client_name(client_id: str, name: str) -> Record:
    """Update the display name for one client contact."""
    if not name.strip():
        raise ValueError("Client name is required.")

    response = (
        get_client()
        .table("dealers")
        .update({"display_name": name.strip()})
        .eq("id", client_id)
        .execute()
    )
    return _first_row(response.data, "dealers")


def update_client_profile(
    client_id: str,
    *,
    notes: str | None = None,
    preferred_brands: str | None = None,
    preferred_models: str | None = None,
    budget_min: int | None = None,
    budget_max: int | None = None,
    preferred_condition: str | None = None,
    preferred_dial: str | None = None,
    status: str | None = None,
) -> Record:
    """Update CRM profile and wishlist fields for one client."""
    if not client_profiles_supported():
        raise RuntimeError(
            "Client profiles require client_profiles table. Apply "
            "docs/migrations/sprint_28_clients.sql in Supabase."
        )

    profile = get_client_profile(client_id)
    payload: Record = {}
    if notes is not None:
        payload["notes"] = _storage_value(notes)
    if preferred_brands is not None:
        payload["preferred_brands"] = _storage_value(preferred_brands)
    if preferred_models is not None:
        payload["preferred_models"] = _storage_value(preferred_models)
    if budget_min is not None:
        payload["budget_min"] = budget_min
    if budget_max is not None:
        payload["budget_max"] = budget_max
    if preferred_condition is not None:
        payload["preferred_condition"] = _storage_value(
            normalize_condition_value(preferred_condition)
        )
    if preferred_dial is not None:
        payload["preferred_dial"] = _storage_value(preferred_dial)
    if status is not None:
        normalized_status = status.strip().lower()
        if normalized_status not in {"active", "inactive"}:
            raise ValueError(f"Unsupported client status: {status}")
        payload["status"] = normalized_status

    if not payload:
        return profile

    response = (
        get_client()
        .table("client_profiles")
        .update(payload)
        .eq("client_id", client_id)
        .execute()
    )
    return _first_row(response.data, "client_profiles")


def list_requests_for_client(*, client_id: str, client_name: str) -> list[Record]:
    """Return requests linked by client_id or matching client_name."""
    requests = list_requests()
    normalized_name = client_name.strip().lower()
    matched: list[Record] = []

    for request in requests:
        request_client_id = request.get("client_id")
        if request_client_id and str(request_client_id) == client_id:
            matched.append(request)
            continue
        if request_client_id:
            continue
        if (request.get("client_name") or "").strip().lower() == normalized_name:
            matched.append(request)

    return matched


def list_client_match_history(client_id: str, *, client_name: str) -> list[Record]:
    """Return enriched offer matches for all requests belonging to one client."""
    requests = list_requests_for_client(client_id=client_id, client_name=client_name)
    request_ids = [str(request["id"]) for request in requests if request.get("id")]
    if not request_ids:
        return []

    grouped = load_enriched_request_matches_by_request_ids(request_ids)
    matches: list[Record] = []
    for request_id in request_ids:
        matches.extend(grouped.get(request_id, []))
    matches.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return matches


CLIENT_DELETE_BLOCKED_MESSAGE = (
    "This client has linked history. Remove from system instead."
)


class ClientDeleteBlockedError(Exception):
    """Raised when a client with linked history cannot be hard-deleted."""

    def __init__(self, message: str = CLIENT_DELETE_BLOCKED_MESSAGE) -> None:
        self.message = message
        super().__init__(message)


def client_has_messages(client_id: str) -> bool:
    """Return True when a contact has stored WhatsApp messages."""
    response = (
        get_client()
        .table("messages")
        .select("id")
        .eq("dealer_id", client_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def client_has_linked_history(client_id: str, *, client_name: str) -> bool:
    """Return True when a client has requests, offers, or messages."""
    if list_requests_for_client(client_id=client_id, client_name=client_name):
        return True
    if dealer_has_offers(client_id):
        return True
    if client_has_messages(client_id):
        return True
    return False


def delete_client_permanently(client_id: str, *, client_name: str) -> None:
    """Hard-delete a CRM client contact and profile when no linked history exists."""
    if get_client_by_id(client_id) is None:
        raise ValueError("Client not found")
    if client_has_linked_history(client_id, client_name=client_name):
        raise ClientDeleteBlockedError()

    if client_profiles_supported():
        get_client().table("client_profiles").delete().eq("client_id", client_id).execute()

    get_client().table("dealers").delete().eq("id", client_id).execute()


def list_active_brand_aliases() -> list[Record]:
    """Return active brand aliases from the database."""
    if not watch_knowledge_supported():
        return []

    response = (
        get_client()
        .table("brand_aliases")
        .select("*")
        .eq("status", "active")
        .order("alias_key")
        .execute()
    )
    return response.data or []


def create_brand_alias(
    *,
    alias_key: str,
    brand_name: str,
    source: str = "manual",
) -> Record:
    """Create or reactivate a brand alias."""
    if not watch_knowledge_supported():
        raise RuntimeError(
            "Brand aliases require watch knowledge tables. Apply "
            "docs/migrations/sprint_30_watch_knowledge.sql in Supabase."
        )

    normalized_key = alias_key.strip().lower()
    if not normalized_key:
        raise ValueError("Alias key is required")
    if not brand_name.strip():
        raise ValueError("Brand name is required")

    existing = (
        get_client()
        .table("brand_aliases")
        .select("*")
        .eq("alias_key", normalized_key)
        .limit(1)
        .execute()
    )
    payload = {
        "alias_key": normalized_key,
        "brand_name": brand_name.strip(),
        "status": "active",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing.data:
        response = (
            get_client()
            .table("brand_aliases")
            .update(payload)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        response = get_client().table("brand_aliases").insert(payload).execute()
    return _first_row(response.data, "brand_aliases")


def list_pending_unknown_brands() -> list[Record]:
    """Return pending unknown brand sightings."""
    if not watch_knowledge_supported():
        return []

    response = (
        get_client()
        .table("unknown_brands")
        .select("*")
        .eq("status", "pending")
        .order("last_seen_at", desc=True)
        .execute()
    )
    return response.data or []


def get_unknown_brand_by_id(unknown_brand_id: str) -> Record | None:
    if not watch_knowledge_supported():
        return None

    response = (
        get_client()
        .table("unknown_brands")
        .select("*")
        .eq("id", unknown_brand_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def record_unknown_brand_sighting(
    *,
    detected_text: str,
    example_message: str,
    dealer_id: str | None,
    seen_at: datetime | None = None,
) -> Record | None:
    """Insert or increment an unknown brand sighting."""
    if not watch_knowledge_supported():
        return None

    normalized_text = detected_text.strip().lower()
    if not normalized_text:
        return None

    timestamp = (seen_at or datetime.now(timezone.utc)).isoformat()
    existing = (
        get_client()
        .table("unknown_brands")
        .select("*")
        .eq("normalized_text", normalized_text)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        if row.get("status") != "pending":
            return row
        response = (
            get_client()
            .table("unknown_brands")
            .update(
                {
                    "occurrence_count": int(row.get("occurrence_count") or 0) + 1,
                    "last_seen_at": timestamp,
                    "example_message": example_message[:2000],
                    "dealer_id": dealer_id or row.get("dealer_id"),
                }
            )
            .eq("id", row["id"])
            .execute()
        )
        return _first_row(response.data, "unknown_brands")

    response = (
        get_client()
        .table("unknown_brands")
        .insert(
            {
                "detected_text": detected_text.strip(),
                "normalized_text": normalized_text,
                "example_message": example_message[:2000],
                "dealer_id": dealer_id,
                "occurrence_count": 1,
                "first_seen_at": timestamp,
                "last_seen_at": timestamp,
                "status": "pending",
            }
        )
        .execute()
    )
    return _first_row(response.data, "unknown_brands")


def mark_unknown_brand_ignored(unknown_brand_id: str) -> Record:
    if not watch_knowledge_supported():
        raise RuntimeError("Watch knowledge tables are not available.")

    response = (
        get_client()
        .table("unknown_brands")
        .update({"status": "ignored"})
        .eq("id", unknown_brand_id)
        .execute()
    )
    return _first_row(response.data, "unknown_brands")


def resolve_unknown_brand_with_alias(
    *,
    unknown_brand_id: str,
    brand_name: str,
) -> tuple[Record, Record]:
    """Create a brand alias from an unknown brand sighting."""
    unknown = get_unknown_brand_by_id(unknown_brand_id)
    if unknown is None:
        raise ValueError("Unknown brand entry not found")

    alias = create_brand_alias(
        alias_key=str(unknown.get("detected_text") or ""),
        brand_name=brand_name,
        source="unknown_brand",
    )
    response = (
        get_client()
        .table("unknown_brands")
        .update({"status": "resolved"})
        .eq("id", unknown_brand_id)
        .execute()
    )
    updated = _first_row(response.data, "unknown_brands")
    return updated, alias


def watch_identification_supported() -> bool:
    """Return True when Sprint 32 watch identification tables exist."""
    global _watch_identification_supported
    if _watch_identification_supported is not None:
        return _watch_identification_supported

    try:
        get_client().table("nickname_aliases").select("id").limit(1).execute()
        get_client().table("unknown_nicknames").select("id").limit(1).execute()
        _watch_identification_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code in {"42P01", "PGRST205"} or "nickname_aliases" in message or "unknown_nicknames" in message:
            _watch_identification_supported = False
        else:
            raise
    return _watch_identification_supported


def list_active_nickname_aliases() -> list[Record]:
    if not watch_identification_supported():
        return []

    response = (
        get_client()
        .table("nickname_aliases")
        .select("*")
        .eq("status", "active")
        .order("alias_key")
        .execute()
    )
    return response.data or []


def create_nickname_alias(
    *,
    alias_key: str,
    brand_name: str,
    collection: str | None = None,
    model_name: str | None = None,
    nickname: str | None = None,
    likely_references: list[str] | None = None,
    confidence: float = 0.9,
    source: str = "manual",
) -> Record:
    if not watch_identification_supported():
        raise RuntimeError(
            "Nickname aliases require watch identification tables. Apply "
            "docs/migrations/sprint_32_watch_identification.sql in Supabase."
        )

    normalized_key = alias_key.strip().lower()
    if not normalized_key:
        raise ValueError("Alias key is required")
    if not brand_name.strip():
        raise ValueError("Brand name is required")

    existing = (
        get_client()
        .table("nickname_aliases")
        .select("*")
        .eq("alias_key", normalized_key)
        .limit(1)
        .execute()
    )
    payload = {
        "alias_key": normalized_key,
        "brand_name": brand_name.strip(),
        "collection": collection,
        "model_name": model_name,
        "nickname": nickname,
        "likely_references": likely_references or [],
        "confidence": confidence,
        "status": "active",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing.data:
        response = (
            get_client()
            .table("nickname_aliases")
            .update(payload)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        response = get_client().table("nickname_aliases").insert(payload).execute()
    return _first_row(response.data, "nickname_aliases")


def list_pending_unknown_nicknames() -> list[Record]:
    if not watch_identification_supported():
        return []

    response = (
        get_client()
        .table("unknown_nicknames")
        .select("*")
        .eq("status", "pending")
        .order("last_seen_at", desc=True)
        .execute()
    )
    return response.data or []


def get_unknown_nickname_by_id(unknown_nickname_id: str) -> Record | None:
    if not watch_identification_supported():
        return None

    response = (
        get_client()
        .table("unknown_nicknames")
        .select("*")
        .eq("id", unknown_nickname_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def record_unknown_nickname_sighting(
    *,
    detected_text: str,
    example_message: str,
    dealer_id: str | None,
    seen_at: datetime | None = None,
) -> Record | None:
    if not watch_identification_supported():
        return None

    normalized_text = detected_text.strip().lower()
    if not normalized_text:
        return None

    timestamp = (seen_at or datetime.now(timezone.utc)).isoformat()
    existing = (
        get_client()
        .table("unknown_nicknames")
        .select("*")
        .eq("normalized_text", normalized_text)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        if row.get("status") != "pending":
            return row
        response = (
            get_client()
            .table("unknown_nicknames")
            .update(
                {
                    "occurrence_count": int(row.get("occurrence_count") or 0) + 1,
                    "last_seen_at": timestamp,
                    "example_message": example_message[:2000],
                    "dealer_id": dealer_id or row.get("dealer_id"),
                }
            )
            .eq("id", row["id"])
            .execute()
        )
        return _first_row(response.data, "unknown_nicknames")

    response = (
        get_client()
        .table("unknown_nicknames")
        .insert(
            {
                "detected_text": detected_text.strip(),
                "normalized_text": normalized_text,
                "example_message": example_message[:2000],
                "dealer_id": dealer_id,
                "occurrence_count": 1,
                "first_seen_at": timestamp,
                "last_seen_at": timestamp,
                "status": "pending",
            }
        )
        .execute()
    )
    return _first_row(response.data, "unknown_nicknames")


def mark_unknown_nickname_ignored(unknown_nickname_id: str) -> Record:
    if not watch_identification_supported():
        raise RuntimeError("Watch identification tables are not available.")

    response = (
        get_client()
        .table("unknown_nicknames")
        .update({"status": "ignored"})
        .eq("id", unknown_nickname_id)
        .execute()
    )
    return _first_row(response.data, "unknown_nicknames")


def resolve_unknown_nickname_with_alias(
    *,
    unknown_nickname_id: str,
    brand_name: str,
    collection: str | None = None,
    model_name: str | None = None,
    nickname: str | None = None,
    likely_references: list[str] | None = None,
) -> tuple[Record, Record]:
    """Create a nickname alias from an unknown nickname sighting."""
    unknown = get_unknown_nickname_by_id(unknown_nickname_id)
    if unknown is None:
        raise ValueError("Unknown nickname entry not found")

    alias = create_nickname_alias(
        alias_key=str(unknown.get("detected_text") or ""),
        brand_name=brand_name,
        collection=collection,
        model_name=model_name,
        nickname=nickname or unknown.get("detected_text"),
        likely_references=likely_references or [],
        source="unknown_nickname",
    )
    response = (
        get_client()
        .table("unknown_nicknames")
        .update({"status": "resolved"})
        .eq("id", unknown_nickname_id)
        .execute()
    )
    updated = _first_row(response.data, "unknown_nicknames")
    return updated, alias


def _first_row(data: list[Record] | None, table: str) -> Record:
    if not data:
        raise RuntimeError(f"Supabase returned no rows for {table}.")
    return data[0]
