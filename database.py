"""Supabase database helpers for MRV4ULT AI."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

from timezone_utils import ensure_utc_datetime
from permissions import USER_STATUS_ACTIVE, USER_STATUS_DISABLED, normalize_role, normalize_status
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)

OFFERS_BY_IDS_CHUNK_SIZE = 100

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

IMPORT_LOG_LIST_COLUMNS_LIGHT = (
    "id,message_id,import_time,group_name,dealer_whatsapp,dealer_alias,"
    "watches_parsed,new_offers,duplicate_offers,matched_requests,"
    "processing_time,status"
)
IMPORT_LOG_LIST_COLUMNS_FULL = f"{IMPORT_LOG_LIST_COLUMNS_LIGHT},summary"
# Backward-compatible alias used by older tests and docs.
IMPORT_LOG_LIST_COLUMNS_BASE = IMPORT_LOG_LIST_COLUMNS_FULL
IMPORT_LOG_LIST_LIMIT_DEFAULT = 1500
IMPORT_LOG_LIST_LIMIT_DASHBOARD = 400
IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE = 20
IMPORT_LOG_LIST_LIMIT_DASHBOARD_MARKET = 25
IMPORT_LOG_LIST_LIMIT_DASHBOARD_PARSER = 25
IMPORT_LOG_LIST_LIMIT_DASHBOARD_TODAY = 50
DASHBOARD_MATCHED_REQUESTS_FETCH_LIMIT = 15
DASHBOARD_MATCHED_REQUESTS_LIMIT = 10
IMPORT_LOG_LIST_LIMIT_ACTIVITY = 1500
IMPORT_LOG_LIST_LIMIT_MARKET_REQUESTS = 250
IMPORT_LOG_LIST_LIMIT_PARSER_REVIEW = 400
IMPORT_LOG_LIST_LIMIT_PARSER_ACCURACY = 400

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
_users_table_supported: bool | None = None
_user_ownership_columns_supported: bool | None = None
_client_profiles_supported: bool | None = None
_watch_knowledge_supported: bool | None = None
_reference_brand_mappings_supported: bool | None = None
_watch_identification_supported: bool | None = None


def reset_contact_type_column_cache() -> None:
    """Reset cached contact_type column detection (for tests)."""
    global _contact_type_column_supported
    _contact_type_column_supported = None


def reset_user_columns_cache() -> None:
    """Reset cached user table/column detection (for tests)."""
    global _users_table_supported, _user_ownership_columns_supported
    _users_table_supported = None
    _user_ownership_columns_supported = None


def reset_client_profiles_cache() -> None:
    """Reset cached client_profiles table detection (for tests)."""
    global _client_profiles_supported
    _client_profiles_supported = None


def reset_watch_knowledge_cache() -> None:
    """Reset cached watch knowledge table detection (for tests)."""
    global _watch_knowledge_supported, _watch_identification_supported, _reference_brand_mappings_supported
    _watch_knowledge_supported = None
    _watch_identification_supported = None
    _reference_brand_mappings_supported = None


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


def users_table_supported() -> bool:
    """Return True when the users table exists in the connected database."""
    global _users_table_supported
    if _users_table_supported is not None:
        return _users_table_supported

    try:
        get_client().table("users").select("id").limit(1).execute()
        _users_table_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code in {"42P01", "PGRST205"} or "users" in message and "does not exist" in message:
            _users_table_supported = False
            logger.warning(
                "users table missing; apply docs/migrations/sprint_33_users_private_contacts.sql"
            )
        else:
            raise
    return _users_table_supported


def user_ownership_columns_supported() -> bool:
    """Return True when import/contact ownership columns exist."""
    global _user_ownership_columns_supported
    if _user_ownership_columns_supported is not None:
        return _user_ownership_columns_supported

    try:
        get_client().table("import_logs").select("imported_by_user_id").limit(1).execute()
        _user_ownership_columns_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code == "42703" or "imported_by_user_id" in message:
            _user_ownership_columns_supported = False
            logger.warning(
                "import ownership columns missing; apply "
                "docs/migrations/sprint_33_users_private_contacts.sql"
            )
        else:
            raise
    return _user_ownership_columns_supported


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


def reference_brand_mappings_supported() -> bool:
    """Return True when reference_brand_mappings table exists."""
    global _reference_brand_mappings_supported
    if _reference_brand_mappings_supported is not None:
        return _reference_brand_mappings_supported

    try:
        get_client().table("reference_brand_mappings").select("id").limit(1).execute()
        _reference_brand_mappings_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code in {"42P01", "PGRST205"} or "reference_brand_mappings" in message:
            _reference_brand_mappings_supported = False
        else:
            raise
    return _reference_brand_mappings_supported


def _legacy_contact_type(dealer: Record) -> str:
    """Infer contact type before the Sprint 27.1 migration is applied."""
    whatsapp_id = str(dealer.get("whatsapp_id") or "").strip()
    if whatsapp_id == IMPORT_PLACEHOLDER_WHATSAPP_ID:
        return CONTACT_TYPE_REMOVED
    return CONTACT_TYPE_DEALER


def dealer_contact_type(dealer: Record | None, *, has_offers: bool = False) -> str:
    """Return the effective normalized contact type for one dealer row."""
    if dealer is None:
        return CONTACT_TYPE_REMOVED
    if not dealer:
        return CONTACT_TYPE_DEALER if has_offers else CONTACT_TYPE_REMOVED
    if contact_type_column_supported():
        return normalize_contact_type(
            str(dealer.get("contact_type") or ""),
            has_offers=has_offers,
        )
    return normalize_contact_type(_legacy_contact_type(dealer), has_offers=has_offers)


def dealer_is_business_visible(dealer: Record | None, *, has_offers: bool = False) -> bool:
    """Return True when a dealer may appear in business-facing views."""
    return is_business_contact(dealer_contact_type(dealer, has_offers=has_offers))


def is_business_dealer_relation(dealer_data: Any, *, has_offers: bool = False) -> bool:
    """Return True when nested Supabase dealer data belongs in business views."""
    return dealer_is_business_visible(_nested_dealer_record(dealer_data), has_offers=has_offers)


def _nested_dealer_record(dealer: Any) -> Record:
    if isinstance(dealer, list) and dealer:
        dealer = dealer[0]
    if isinstance(dealer, dict):
        return dealer
    return {}


def _offer_from_business_dealer(
    offer: Record,
    *,
    visible_dealer_ids: set[str] | None = None,
) -> bool:
    nested = _nested_dealer_record(offer.get("dealers"))
    if nested:
        return dealer_is_business_visible(nested)
    dealer_id = str(offer.get("dealer_id") or "")
    if not dealer_id:
        return False
    if visible_dealer_ids is not None:
        return dealer_id in visible_dealer_ids
    return dealer_id in _business_visible_dealer_ids()


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
    imported_by_user_id: str | None = None,
) -> Record:
    """Insert a row into messages and return the created record."""
    payload: Record = {
        "group_id": group_id,
        "dealer_id": dealer_id,
        "raw_text": raw_text,
        "message_type": message_type,
        "source": source,
        "whatsapp_message_id": whatsapp_message_id,
        "received_at": ensure_utc_datetime(
            received_at or datetime.now(timezone.utc)
        ).isoformat(),
        "parsed_at": ensure_utc_datetime(parsed_at).isoformat() if parsed_at else None,
        "parser_version": parser_version,
        "parse_status": parse_status,
        "parse_error": parse_error,
    }
    if imported_by_user_id and user_ownership_columns_supported():
        payload["imported_by_user_id"] = imported_by_user_id

    response = get_client().table("messages").insert(payload).execute()
    return _first_row(response.data, "messages")


def find_message_by_whatsapp_id(whatsapp_message_id: str) -> Record | None:
    """Return the most recent stored message for an external WhatsApp message id."""
    cleaned = whatsapp_message_id.strip()
    if not cleaned:
        return None
    response = (
        get_client()
        .table("messages")
        .select("*")
        .eq("whatsapp_message_id", cleaned)
        .order("received_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def find_import_log_by_message_id(message_id: str) -> Record | None:
    """Return the latest import log linked to a stored message."""
    response = (
        get_client()
        .table("import_logs")
        .select(import_log_list_columns_light())
        .eq("message_id", message_id)
        .order("import_time", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def get_import_logs_by_message_ids(message_ids: list[str]) -> dict[str, Record]:
    """Return the latest import log per message id keyed by message id."""
    if not message_ids:
        return {}

    unique_ids = list(dict.fromkeys(str(message_id) for message_id in message_ids if message_id))
    if not unique_ids:
        return {}

    columns = "id, message_id, watches_parsed, status"
    if user_ownership_columns_supported():
        columns += ", imported_by_user_id"

    response = (
        get_client()
        .table("import_logs")
        .select(columns)
        .in_("message_id", unique_ids)
        .order("import_time", desc=True)
        .execute()
    )
    by_message_id: dict[str, Record] = {}
    for row in response.data or []:
        message_id = str(row.get("message_id") or "")
        if message_id and message_id not in by_message_id:
            by_message_id[message_id] = row
    return by_message_id


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


def find_watches_by_reference(reference_query: str) -> list[Record]:
    """Return watches whose stored reference matches a search reference token."""
    from search import _normalize_search_reference, _reference_contains_token

    token = reference_query.strip()
    if not token:
        return []

    normalized_token = _normalize_search_reference(token)
    if not normalized_token:
        return []

    prefix = re.sub(r"[^A-Za-z0-9]", "", token)[:6]
    query = get_client().table("watches").select("*")
    if prefix:
        query = query.ilike("reference", f"%{prefix}%")
    else:
        query = query.not_.is_("reference", "null")

    response = query.execute()
    return [
        watch
        for watch in response.data or []
        if _reference_contains_token(watch.get("reference"), token)
    ]


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
    created_by_user_id: str | None = None,
) -> Record:
    """Create a manual client request."""
    payload = build_request_storage_payload(
        client_name=client_name,
        brand=brand,
        reference=reference,
        model=model,
        alias=alias,
        dial=dial,
        condition=condition,
        min_year=min_year,
        max_year=max_year,
        max_price=max_price,
        currency=currency,
        notes=notes,
        status=status,
    )
    if client_id:
        payload["client_id"] = client_id
    if created_by_user_id and user_ownership_columns_supported():
        payload["created_by_user_id"] = created_by_user_id
    response = get_client().table("requests").insert(payload).execute()
    return _first_row(response.data, "requests")


def build_request_storage_payload(
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
    status: str | None = None,
) -> Record:
    """Normalize request fields for create/update storage."""
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
    }
    if status is not None:
        payload["status"] = status
    return payload


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


def update_request(
    request_id: str,
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
    status: str,
) -> Record:
    """Update an existing client request."""
    payload = build_request_storage_payload(
        client_name=client_name,
        brand=brand,
        reference=reference,
        model=model,
        alias=alias,
        dial=dial,
        condition=condition,
        min_year=min_year,
        max_year=max_year,
        max_price=max_price,
        currency=currency,
        notes=notes,
        status=status,
    )
    response = (
        get_client()
        .table("requests")
        .update(payload)
        .eq("id", request_id)
        .execute()
    )
    return _first_row(response.data, "requests")


def delete_request(request_id: str) -> None:
    """Permanently delete a client request and cascade request matches."""
    get_client().table("requests").delete().eq("id", request_id).execute()


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


def list_recent_request_matches(
    *,
    limit: int = DASHBOARD_MATCHED_REQUESTS_FETCH_LIMIT,
) -> list[Record]:
    """Return the newest request_matches rows for dashboard views."""
    response = (
        get_client()
        .table("request_matches")
        .select(
            "id, request_id, offer_id, import_log_id, match_strength, match_reason, created_at"
        )
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def get_request_match(match_id: str) -> Record | None:
    """Return one request_matches row by id."""
    cleaned_id = match_id.strip()
    if not cleaned_id:
        return None
    response = (
        get_client()
        .table("request_matches")
        .select(
            "id, request_id, offer_id, import_log_id, match_strength, match_reason, created_at"
        )
        .eq("id", cleaned_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def get_requests_by_ids(request_ids: list[str]) -> dict[str, Record]:
    """Return client requests keyed by id."""
    if not request_ids:
        return {}

    response = (
        get_client()
        .table("requests")
        .select("*")
        .in_("id", request_ids)
        .execute()
    )
    return {str(row["id"]): row for row in response.data or []}


def _normalize_lookup_ids(raw_ids: list[str]) -> list[str]:
    """Return deduplicated non-empty ids preserving first-seen order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_ids:
        if raw is None:
            continue
        cleaned = str(raw).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def get_offers_by_ids(offer_ids: list[str]) -> dict[str, Record]:
    """Return offers keyed by id, querying in bounded chunks."""
    cleaned_ids = _normalize_lookup_ids(offer_ids)
    if not cleaned_ids:
        return {}

    select_fields = (
        "id, watch_id, original_price, original_currency, usd_price, condition, card_date, production_year"
    )
    results: dict[str, Record] = {}

    for offset in range(0, len(cleaned_ids), OFFERS_BY_IDS_CHUNK_SIZE):
        chunk = cleaned_ids[offset : offset + OFFERS_BY_IDS_CHUNK_SIZE]
        try:
            response = (
                get_client()
                .table("offers")
                .select(select_fields)
                .in_("id", chunk)
                .execute()
            )
        except Exception as exc:
            logger.warning(
                "get_offers_by_ids chunk failed (offset=%s, size=%s): %s",
                offset,
                len(chunk),
                exc,
            )
            continue

        for row in response.data or []:
            row_id = row.get("id")
            if row_id:
                results[str(row_id)] = row

    return results


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
        .select("id, import_time, group_name, dealer_alias, dealer_whatsapp, message_id, summary")
        .in_("id", import_log_ids)
        .execute()
    )
    return {str(row["id"]): row for row in response.data or []}


def get_notification_by_id(notification_id: str) -> Record | None:
    """Return one notification row by id."""
    cleaned_id = notification_id.strip()
    if not cleaned_id:
        return None
    response = (
        get_client()
        .table("notifications")
        .select("*")
        .eq("id", cleaned_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def get_messages_by_ids(message_ids: list[str]) -> dict[str, Record]:
    """Return messages keyed by id."""
    if not message_ids:
        return {}

    response = (
        get_client()
        .table("messages")
        .select("id, raw_text")
        .in_("id", message_ids)
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
    return _group_enriched_request_matches(load_enriched_request_match_batch(matches))


def load_enriched_request_match_batch(matches: list[Record]) -> list[Record]:
    """Attach offer, watch, and import log data to request match rows."""
    if not matches:
        return []

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

    return combine_request_match_records(
        matches,
        offers_by_id=offers_by_id,
        watches_by_id=watches_by_id,
        import_logs_by_id=import_logs_by_id,
    )


def _group_enriched_request_matches(matches: list[Record]) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = {}
    for match in matches:
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


def list_recent_notifications(
    *,
    limit: int = 20,
    notification_type: str | None = None,
) -> list[Record]:
    """Return a limited recent notification slice for dashboard-style views."""
    query = (
        get_client()
        .table("notifications")
        .select("*")
        .order("is_read")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if notification_type:
        query = query.eq("type", notification_type)
    response = query.execute()
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


def mark_import_parser_issue_ignored(
    import_log_id: str,
    *,
    reason: str | None = None,
) -> Record:
    """Hide a parser review issue from the default review queue."""
    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    summary = dict(import_log.get("summary") or {})
    summary["parser_review_ignored"] = True
    cleaned_reason = (reason or "").strip()
    if cleaned_reason:
        summary["parser_review_ignore_reason"] = cleaned_reason
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
    imported_by_user_id: str | None = None,
) -> Record:
    """Persist a completed import for the activity dashboard."""
    payload: Record = {
        "message_id": message_id,
        "import_time": ensure_utc_datetime(import_time).isoformat(),
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
    if imported_by_user_id and user_ownership_columns_supported():
        payload["imported_by_user_id"] = imported_by_user_id
    response = get_client().table("import_logs").insert(payload).execute()
    return _first_row(response.data, "import_logs")


def import_log_list_columns_light() -> str:
    """Return the lightweight column projection for import log list queries."""
    if user_ownership_columns_supported():
        return f"{IMPORT_LOG_LIST_COLUMNS_LIGHT},imported_by_user_id"
    return IMPORT_LOG_LIST_COLUMNS_LIGHT


def import_log_detail_columns_full() -> str:
    """Return the full column projection for import log detail views."""
    if user_ownership_columns_supported():
        return f"{IMPORT_LOG_LIST_COLUMNS_FULL},imported_by_user_id"
    return IMPORT_LOG_LIST_COLUMNS_FULL


def import_log_list_columns() -> str:
    """Return list-query columns (lightweight; excludes summary JSON)."""
    return import_log_list_columns_light()


def get_import_log_summaries_by_ids(import_log_ids: list[str]) -> dict[str, Record]:
    """Return summary JSON keyed by import log id."""
    if not import_log_ids:
        return {}

    unique_ids = list(dict.fromkeys(import_log_ids))
    response = (
        get_client()
        .table("import_logs")
        .select("id,summary")
        .in_("id", unique_ids)
        .execute()
    )
    summaries: dict[str, Record] = {}
    for row in response.data or []:
        summary = row.get("summary")
        summaries[str(row["id"])] = summary if isinstance(summary, dict) else {}
    return summaries


def attach_import_log_summaries(import_logs: list[Record]) -> list[Record]:
    """Merge full summary JSON onto lightweight import log rows."""
    if not import_logs:
        return import_logs

    missing_ids = [
        str(import_log["id"])
        for import_log in import_logs
        if import_log.get("summary") is None
    ]
    if not missing_ids:
        return import_logs

    summaries_by_id = get_import_log_summaries_by_ids(missing_ids)
    merged: list[Record] = []
    for import_log in import_logs:
        row = dict(import_log)
        if row.get("summary") is None:
            row["summary"] = summaries_by_id.get(str(row["id"]), {})
        merged.append(row)
    return merged


def _query_import_logs(
    *,
    limit: int | None,
    status: str | None = None,
) -> list[Record]:
    """Run a bounded import_logs query ordered by indexed import_time."""
    query = (
        get_client()
        .table("import_logs")
        .select(import_log_list_columns_light())
        .order("import_time", desc=True)
    )
    if status is not None:
        query = query.eq("status", status)
    if limit is not None:
        query = query.limit(limit)
    response = query.execute()
    return response.data or []


def list_import_logs(*, limit: int | None = IMPORT_LOG_LIST_LIMIT_DEFAULT) -> list[Record]:
    """Return recent import logs for activity and dashboard list views."""
    return _query_import_logs(limit=limit)


def list_import_logs_page(*, offset: int, limit: int) -> list[Record]:
    """Return one page of import logs ordered by import_time desc."""
    return list_activity_import_logs(tab="all", offset=offset, limit=limit)


def _apply_activity_tab_filters(query: Any, tab: str) -> Any:
    """Apply coarse activity tab filters in Supabase before Python refinement."""
    query = query.neq("status", "no_watch_detected")
    if tab == "all":
        return query
    if tab == "ignored":
        return query.or_(
            "status.in.(noise,request_intent,insufficient_evidence),"
            "summary->parser_review_ignored.eq.true"
        )
    if tab == "reviewed":
        return query.eq("summary->parser_reviewed", "true")
    if tab == "active":
        return query.or_(
            "and(status.eq.success,or(new_offers.gt.0,watches_parsed.gt.0),"
            "or(summary->parser_reviewed.is.null,summary->parser_reviewed.eq.false)),"
            "and(status.eq.warning,watches_parsed.gt.0,"
            "or(summary->parser_review_ignored.is.null,summary->parser_review_ignored.eq.false),"
            "or(summary->parser_reviewed.is.null,summary->parser_reviewed.eq.false))"
        )
    return query


def list_activity_import_logs(
    *,
    tab: str,
    offset: int = 0,
    limit: int,
) -> list[Record]:
    """Return bounded activity import logs with tab-aware database filters."""
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    if limit < 1:
        raise ValueError("limit must be at least 1")

    query = (
        get_client()
        .table("import_logs")
        .select(import_log_list_columns_light())
        .order("import_time", desc=True)
    )
    query = _apply_activity_tab_filters(query, tab)
    response = query.range(offset, offset + limit - 1).execute()
    return response.data or []


def list_market_request_import_logs(
    *,
    limit: int = IMPORT_LOG_LIST_LIMIT_MARKET_REQUESTS,
) -> list[Record]:
    """Return recent buyer-request import logs only."""
    return _query_import_logs(limit=limit, status="request_intent")


def list_parser_review_import_log_candidates(
    *,
    limit: int = IMPORT_LOG_LIST_LIMIT_PARSER_REVIEW,
) -> list[Record]:
    """Return recent needs-review import logs for parser review pages."""
    return _query_import_logs(limit=limit, status="warning")


def list_parser_accuracy_import_logs(
    *,
    limit: int = IMPORT_LOG_LIST_LIMIT_PARSER_ACCURACY,
) -> list[Record]:
    """Return bounded recent import logs for parser accuracy metrics."""
    return _query_import_logs(limit=limit)


def _query_dashboard_import_logs(
    *,
    limit: int,
    status: str | None = None,
    since_iso: str | None = None,
) -> list[Record]:
    """Run a bounded dashboard import_logs query with optional status/day filters."""
    query = (
        get_client()
        .table("import_logs")
        .select(import_log_list_columns_light())
        .neq("status", "no_watch_detected")
        .order("import_time", desc=True)
        .limit(limit)
    )
    if status is not None:
        query = query.eq("status", status)
    if since_iso is not None:
        query = query.gte("import_time", since_iso)
    response = query.execute()
    return response.data or []


def list_dashboard_recent_import_logs(
    *,
    since_iso: str,
    limit: int = IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE,
) -> list[Record]:
    """Return today's recent imports for the dashboard live market section."""
    return _query_dashboard_import_logs(since_iso=since_iso, limit=limit)


def list_dashboard_today_import_logs(
    *,
    since_iso: str,
    limit: int = IMPORT_LOG_LIST_LIMIT_DASHBOARD_TODAY,
) -> list[Record]:
    """Return today's imports for dashboard new-offers KPIs."""
    return _query_dashboard_import_logs(since_iso=since_iso, limit=limit)


def list_dashboard_market_request_import_logs(
    *,
    limit: int = IMPORT_LOG_LIST_LIMIT_DASHBOARD_MARKET,
) -> list[Record]:
    """Return recent buyer-request imports for dashboard opportunities/KPIs."""
    return _query_dashboard_import_logs(limit=limit, status="request_intent")


def list_dashboard_parser_review_import_logs(
    *,
    limit: int = IMPORT_LOG_LIST_LIMIT_DASHBOARD_PARSER,
) -> list[Record]:
    """Return recent warning imports for dashboard AI-needs-help sections."""
    return _query_dashboard_import_logs(limit=limit, status="warning")


def get_import_log(import_log_id: str) -> Record | None:
    """Return one import log by id."""
    response = (
        get_client()
        .table("import_logs")
        .select(import_log_detail_columns_full())
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
    skip_duplicate_check: bool = False,
) -> tuple[Record, bool]:
    """Insert an offer unless an identical active offer already exists."""
    normalized_currency = _storage_value(original_currency)
    normalized_condition = _storage_value(normalize_condition_value(condition))
    normalized_card_date = _storage_value(card_date)

    if not skip_duplicate_check:
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
    """Return business dealer contacts ordered by display name."""
    dealer_ids_with_offers = _dealer_ids_with_offers()
    response = (
        get_client()
        .table("dealers")
        .select("*")
        .order("display_name")
        .execute()
    )
    return [
        dealer
        for dealer in response.data or []
        if dealer_is_business_visible(
            dealer,
            has_offers=str(dealer.get("id")) in dealer_ids_with_offers,
        )
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


def _business_visible_dealer_ids() -> set[str]:
    """Return dealer ids that may appear in business-facing dealer views."""
    dealer_ids_with_offers = _dealer_ids_with_offers()
    response = get_client().table("dealers").select("id, contact_type, whatsapp_id").execute()
    return {
        str(dealer.get("id"))
        for dealer in response.data or []
        if dealer.get("id")
        and dealer_is_business_visible(
            dealer,
            has_offers=str(dealer.get("id")) in dealer_ids_with_offers,
        )
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


def list_users() -> list[Record]:
    """Return all dashboard users."""
    if not users_table_supported():
        return []
    response = (
        get_client()
        .table("users")
        .select("id, name, email, role, status, created_at, last_login_at")
        .order("name")
        .execute()
    )
    return [_normalize_user_record(row) for row in response.data or []]


def _normalize_user_record(row: Record) -> Record:
    normalized = dict(row)
    normalized["role"] = normalize_role(normalized.get("role"))
    normalized["status"] = normalize_status(normalized.get("status"))
    return normalized


def get_user_by_id(user_id: str) -> Record | None:
    """Return one dashboard user by id."""
    if not users_table_supported():
        return None
    response = (
        get_client()
        .table("users")
        .select("id, name, email, role, status, created_at, last_login_at")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return _normalize_user_record(response.data[0])


def get_user_by_email(email: str) -> Record | None:
    """Return one dashboard user by email."""
    if not users_table_supported():
        return None
    response = (
        get_client()
        .table("users")
        .select("id, name, email, role, status, created_at, last_login_at")
        .eq("email", email.strip().lower())
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return _normalize_user_record(response.data[0])


def create_user(*, name: str, email: str, role: str) -> Record:
    """Create a dashboard user invitation/account."""
    payload = {
        "name": name.strip(),
        "email": email.strip().lower(),
        "role": normalize_role(role),
        "status": USER_STATUS_ACTIVE,
    }
    response = get_client().table("users").insert(payload).execute()
    if not response.data:
        raise RuntimeError("Failed to create user.")
    return _normalize_user_record(response.data[0])


def update_user(
    user_id: str,
    *,
    name: str,
    role: str,
) -> Record:
    """Update a user's name and role."""
    payload = {
        "name": name.strip(),
        "role": normalize_role(role),
    }
    response = (
        get_client()
        .table("users")
        .update(payload)
        .eq("id", user_id)
        .execute()
    )
    if not response.data:
        raise RuntimeError("User not found.")
    return _normalize_user_record(response.data[0])


def set_user_status(user_id: str, status: str) -> Record:
    """Enable or disable a dashboard user."""
    normalized_status = normalize_status(status)
    if normalized_status not in {USER_STATUS_ACTIVE, USER_STATUS_DISABLED}:
        raise ValueError("Invalid user status.")
    response = (
        get_client()
        .table("users")
        .update({"status": normalized_status})
        .eq("id", user_id)
        .execute()
    )
    if not response.data:
        raise RuntimeError("User not found.")
    return _normalize_user_record(response.data[0])


def record_user_login(user_id: str) -> None:
    """Persist the latest login timestamp for one user."""
    if not users_table_supported():
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    get_client().table("users").update({"last_login_at": timestamp}).eq("id", user_id).execute()


def reset_user_password(user_id: str) -> str:
    """Placeholder until password auth is enabled."""
    if get_user_by_id(user_id) is None:
        raise RuntimeError("User not found.")
    return "Password reset is not enabled during the pilot. Users sign in with email only."


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


def update_dealer_contact_type(
    dealer_id: str,
    contact_type: str,
    *,
    owner_user_id: str | None = None,
    classified_by_user_id: str | None = None,
) -> Record:
    """Update the privacy classification for one contact."""
    if not contact_type_column_supported():
        raise RuntimeError(
            "Contact classification requires dealers.contact_type. Apply "
            "docs/migrations/sprint_27_1_contact_classification.sql in Supabase."
        )
    if contact_type not in ALL_CONTACT_TYPES:
        raise ValueError(f"Unsupported contact type: {contact_type}")

    updates: Record = {"contact_type": contact_type}
    if user_ownership_columns_supported():
        if contact_type == CONTACT_TYPE_REMOVED:
            if owner_user_id:
                updates["owner_user_id"] = owner_user_id
            if classified_by_user_id:
                updates["classified_by_user_id"] = classified_by_user_id
        elif contact_type in {CONTACT_TYPE_DEALER, CONTACT_TYPE_CLIENT}:
            updates["owner_user_id"] = None

    response = (
        get_client()
        .table("dealers")
        .update(updates)
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


def get_dealer_by_contact_number(number: str) -> Record | None:
    """Return a dealer matched by whatsapp_id or phone_number."""
    cleaned = number.strip()
    if not cleaned:
        return None

    fields = "display_name, phone_number, whatsapp_id"
    for column in ("whatsapp_id", "phone_number"):
        response = (
            get_client()
            .table("dealers")
            .select(fields)
            .eq(column, cleaned)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
    return None


def list_dealer_import_activity_logs(
    *,
    limit: int = IMPORT_LOG_LIST_LIMIT_DEFAULT,
) -> list[Record]:
    """Return recent import logs for dealer list activity aggregation."""
    response = (
        get_client()
        .table("import_logs")
        .select(import_log_list_columns_light())
        .order("import_time", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def list_dealer_offer_counts() -> dict[str, Record]:
    """Return lightweight per-dealer offer counts for list badges."""
    visible_ids = _business_visible_dealer_ids()
    response = get_client().table("offers").select("dealer_id, status").execute()
    counts: dict[str, Record] = {}
    for row in response.data or []:
        dealer_id = str(row.get("dealer_id") or "")
        if not dealer_id or dealer_id not in visible_ids:
            continue
        bucket = counts.setdefault(
            dealer_id,
            {"total_offers": 0, "active_offers": 0},
        )
        bucket["total_offers"] = int(bucket["total_offers"]) + 1
        if row.get("status") == "active":
            bucket["active_offers"] = int(bucket["active_offers"]) + 1
    return counts


def list_offer_intelligence_rows(*, dealer_id: str | None = None) -> list[Record]:
    """Return offer rows used for dealer intelligence aggregation."""
    visible_dealer_ids = _business_visible_dealer_ids()
    query = (
        get_client()
        .table("offers")
        .select("dealer_id, watch_id, status, usd_price, messages(received_at)")
    )
    if dealer_id:
        normalized_dealer_id = str(dealer_id)
        if normalized_dealer_id not in visible_dealer_ids:
            return []
        query = query.eq("dealer_id", dealer_id)
    response = query.execute()
    return [
        offer
        for offer in response.data or []
        if str(offer.get("dealer_id") or "") in visible_dealer_ids
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
            "messages(id, received_at, group_id, groups(name)), "
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


def list_active_offers_for_market_matching() -> list[Record]:
    """Return active offers with dealer, group, and watch metadata for market request matching."""
    if contact_type_column_supported():
        dealer_fields = (
            "dealers(id, display_name, phone_number, whatsapp_id, contact_type, country, "
            "owner_user_id, classified_by_user_id)"
        )
    else:
        dealer_fields = "dealers(id, display_name, phone_number, whatsapp_id, country)"
    response = (
        get_client()
        .table("offers")
        .select(
            "id, dealer_id, watch_id, original_price, original_currency, usd_price, "
            "card_date, condition, production_year, "
            "watches(brand, reference, model, dial, bracelet), "
            "messages(received_at, groups(name, country)), "
            f"{dealer_fields}"
        )
        .eq("status", "active")
        .execute()
    )
    return list(response.data or [])


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


def list_active_reference_brand_mappings() -> list[Record]:
    """Return active reference-to-brand mappings from the database."""
    if not reference_brand_mappings_supported():
        return []

    response = (
        get_client()
        .table("reference_brand_mappings")
        .select("*")
        .eq("status", "active")
        .order("reference_key")
        .execute()
    )
    return response.data or []


def create_reference_brand_mapping(
    *,
    reference: str,
    brand_name: str,
    source: str = "manual",
) -> Record:
    """Create or reactivate a reference-to-brand mapping."""
    if not reference_brand_mappings_supported():
        raise RuntimeError(
            "Reference brand mappings require reference_brand_mappings table. Apply "
            "docs/migrations/sprint_48_3_reference_brand_mappings.sql in Supabase."
        )

    from watch_knowledge import normalize_reference

    reference_key = normalize_reference(reference)
    if not reference_key:
        raise ValueError("Reference is required")
    if not brand_name.strip():
        raise ValueError("Brand name is required")

    existing = (
        get_client()
        .table("reference_brand_mappings")
        .select("*")
        .eq("reference_key", reference_key)
        .limit(1)
        .execute()
    )
    payload = {
        "reference_key": reference_key,
        "brand_name": brand_name.strip(),
        "status": "active",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing.data:
        response = (
            get_client()
            .table("reference_brand_mappings")
            .update(payload)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        response = get_client().table("reference_brand_mappings").insert(payload).execute()
    return _first_row(response.data, "reference_brand_mappings")


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
