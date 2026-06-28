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
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_REMOVED,
    IMPORT_PLACEHOLDER_WHATSAPP_ID,
    is_business_contact,
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


def reset_contact_type_column_cache() -> None:
    """Reset cached contact_type column detection (for tests)."""
    global _contact_type_column_supported
    _contact_type_column_supported = None


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


def _first_row(data: list[Record] | None, table: str) -> Record:
    if not data:
        raise RuntimeError(f"Supabase returned no rows for {table}.")
    return data[0]
