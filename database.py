"""Supabase database helpers for MRV4ULT AI."""

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from timezone_utils import ensure_utc_datetime
from permissions import USER_STATUS_ACTIVE, USER_STATUS_DISABLED, normalize_role, normalize_status
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)

_parser_training_rows_writes_allowed: ContextVar[bool] = ContextVar(
    "parser_training_rows_writes_allowed",
    default=False,
)


@contextmanager
def parser_training_rows_write_guard():
    """Allow parser_training_rows INSERT/UPDATE/PATCH (POST actions only)."""
    token = _parser_training_rows_writes_allowed.set(True)
    try:
        yield
    finally:
        _parser_training_rows_writes_allowed.reset(token)


def parser_training_rows_writes_enabled() -> bool:
    """Return True when parser_training_rows mutations are permitted."""
    return _parser_training_rows_writes_allowed.get()


def _ensure_parser_training_row_writes_allowed(action: str) -> None:
    if not _parser_training_rows_writes_allowed.get():
        raise RuntimeError(
            f"Blocked parser_training_rows {action} during read-only request. "
            "Use POST training actions (save, bulk, backfill, re-evaluate) to write."
        )


LOOKUP_IDS_CHUNK_SIZE = 50
OFFERS_BY_IDS_CHUNK_SIZE = LOOKUP_IDS_CHUNK_SIZE
WATCH_LOOKUP_PAGE_SIZE = 1000
PARSER_TRAINING_ROWS_PAGE_SIZE = 1000
PARSER_TRAINING_REFERENCE_BATCH_SIZE = 50
PARSER_TRAINING_REFERENCE_QUERY_MAX = 50
PARSER_TRAINING_REFERENCE_ROW_COLUMNS = (
    "id, status, created_offer_id, import_log_id, source_message_id, row_index, "
    "raw_row_text, detected_brand, detected_reference, detected_condition, "
    "detected_year, detected_card_date, detected_price, detected_currency, "
    "normalized_brand, normalized_reference, normalized_condition, usd_price"
)
IMPORT_LOG_SUMMARY_BATCH_SIZE = 100
IMPORT_LOG_SUMMARY_MAX_RETRIES = 3
IMPORT_LOG_SUMMARY_RETRY_BACKOFF_SECONDS = 0.5
IMPORT_LOG_SUMMARY_BATCH_TIMEOUT_SECONDS = 30

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_valid_uuid(value: str | None) -> bool:
    """Return True when value is a canonical UUID string."""
    if not value:
        return False
    return bool(_UUID_PATTERN.match(str(value).strip()))

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
IMPORT_LOG_ACTIVITY_LIST_COLUMNS = (
    "id,import_time,created_at,group_name,dealer_whatsapp,dealer_alias,"
    "watches_parsed,new_offers,duplicate_offers,matched_requests,"
    "processing_time,status"
)
ACTIVITY_IMPORT_LOG_MAX_LIMIT = 50
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
PARSER_TRAINING_IMPORT_PAGE_SIZE = 25
PARSER_TRAINING_MAX_SCANNED_IMPORTS = 400
PARSER_TRAINING_OVERFETCH_MULTIPLIER = 3

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
_dealer_default_currency_columns_supported: bool | None = None
_source_import_log_id_column_supported: bool | None = None
_users_table_supported: bool | None = None
_user_ownership_columns_supported: bool | None = None
_request_created_by_user_id_supported: bool | None = None
_client_profiles_supported: bool | None = None
_watch_knowledge_supported: bool | None = None
_reference_brand_mappings_supported: bool | None = None
_watch_identification_supported: bool | None = None
_parser_learning_rules_supported: bool | None = None
_parser_learning_rules_cache: list[Record] | None = None
_parser_training_rows_supported: bool | None = None


def reset_contact_type_column_cache() -> None:
    """Reset cached contact_type column detection (for tests)."""
    global _contact_type_column_supported, _source_import_log_id_column_supported
    global _dealer_default_currency_columns_supported
    _contact_type_column_supported = None
    _source_import_log_id_column_supported = None
    _dealer_default_currency_columns_supported = None


def reset_user_columns_cache() -> None:
    """Reset cached user table/column detection (for tests)."""
    global _users_table_supported, _user_ownership_columns_supported, _request_created_by_user_id_supported
    _users_table_supported = None
    _user_ownership_columns_supported = None
    _request_created_by_user_id_supported = None


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


def reset_parser_learning_rules_cache() -> None:
    """Reset cached parser learning rules table detection (for tests)."""
    global _parser_learning_rules_supported, _parser_learning_rules_cache, _parser_training_rows_supported
    _parser_learning_rules_supported = None
    _parser_learning_rules_cache = None
    _parser_training_rows_supported = None


def invalidate_parser_learning_rules_cache() -> None:
    """Clear cached active parser learning rules."""
    global _parser_learning_rules_cache
    _parser_learning_rules_cache = None


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


def dealer_default_currency_columns_supported() -> bool:
    """Return True when dealers.default_currency exists in the connected database."""
    global _dealer_default_currency_columns_supported
    if _dealer_default_currency_columns_supported is not None:
        return _dealer_default_currency_columns_supported

    try:
        get_client().table("dealers").select(
            "default_currency, default_currency_confidence, inferred_from_phone_country, "
            "inferred_from_offer_history"
        ).limit(1).execute()
        _dealer_default_currency_columns_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code == "42703" or "default_currency" in message:
            _dealer_default_currency_columns_supported = False
            logger.warning(
                "dealers.default_currency columns missing; apply "
                "docs/migrations/sprint_50_5_dealer_default_currency.sql"
            )
        else:
            raise
    return _dealer_default_currency_columns_supported


def source_import_log_id_column_supported() -> bool:
    """Return True when offers.source_import_log_id exists in the connected database."""
    global _source_import_log_id_column_supported
    if _source_import_log_id_column_supported is not None:
        return _source_import_log_id_column_supported

    try:
        get_client().table("offers").select("source_import_log_id").limit(1).execute()
        _source_import_log_id_column_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code == "42703" or "source_import_log_id" in message:
            _source_import_log_id_column_supported = False
            logger.warning(
                "offers.source_import_log_id column missing; apply "
                "docs/migrations/sprint_48_5_2_offer_source_import_log_id.sql"
            )
        else:
            raise
    return _source_import_log_id_column_supported


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


def request_created_by_user_id_supported() -> bool:
    """Return True when requests.created_by_user_id exists in the connected database."""
    global _request_created_by_user_id_supported
    if _request_created_by_user_id_supported is not None:
        return _request_created_by_user_id_supported

    try:
        get_client().table("requests").select("created_by_user_id").limit(1).execute()
        _request_created_by_user_id_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code in {"42703", "PGRST204"} or "created_by_user_id" in message:
            _request_created_by_user_id_supported = False
            logger.warning(
                "requests.created_by_user_id column missing; apply "
                "docs/migrations/sprint_46_2_request_ownership.sql"
            )
        else:
            raise
    return _request_created_by_user_id_supported


class RequestSchemaError(Exception):
    """Raised when the requests table is missing a required schema upgrade."""

    migration_path = "docs/migrations/sprint_46_2_request_ownership.sql"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "Client request could not be saved because the database schema is out of date. "
                f"Apply {self.migration_path} in the Supabase SQL Editor, then restart the app."
            )
        )


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
        message_id = _normalize_uuid_key(row.get("message_id"))
        if message_id and message_id not in by_message_id:
            by_message_id[message_id] = row
    return by_message_id


def _normalize_uuid_key(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def find_import_logs_by_summary_offer_ids(offer_ids: list[str]) -> dict[str, Record]:
    """Return the newest import log per offer id referenced in summary.rows."""
    if not offer_ids:
        return {}

    unique_offer_ids = list(dict.fromkeys(_normalize_uuid_key(offer_id) for offer_id in offer_ids if offer_id))
    if not unique_offer_ids:
        return {}

    by_offer_id: dict[str, Record] = {}
    for offer_id in unique_offer_ids:
        response = (
            get_client()
            .table("import_logs")
            .select(_import_log_source_select_columns())
            .contains("summary", {"rows": [{"offer_id": offer_id}]})
            .order("import_time", desc=True)
            .limit(1)
            .execute()
        )
        if response.data:
            by_offer_id[offer_id] = response.data[0]
    return by_offer_id


def get_offer_by_id(offer_id: str) -> Record | None:
    """Return one offer row with source-link fields for debugging."""
    cleaned_id = offer_id.strip()
    if not cleaned_id:
        return None

    columns = (
        "id, message_id, dealer_id, watch_id, status, original_price, original_currency, "
        "condition, card_date, created_at, duplicate_of_id, is_duplicate"
    )
    if source_import_log_id_column_supported():
        columns += ", source_import_log_id"

    response = (
        get_client()
        .table("offers")
        .select(columns)
        .eq("id", cleaned_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def link_offer_to_import_source(
    offer_id: str,
    *,
    message_id: str,
    source_import_log_id: str,
) -> None:
    """Persist the WhatsApp import source on an offer after import_log creation."""
    cleaned_offer_id = offer_id.strip()
    cleaned_message_id = message_id.strip()
    cleaned_import_log_id = source_import_log_id.strip()
    if not cleaned_offer_id or not cleaned_message_id or not cleaned_import_log_id:
        return
    if not (
        _is_valid_uuid(cleaned_offer_id)
        and _is_valid_uuid(cleaned_message_id)
        and _is_valid_uuid(cleaned_import_log_id)
    ):
        return

    payload: Record = {"message_id": cleaned_message_id}
    if source_import_log_id_column_supported():
        payload["source_import_log_id"] = cleaned_import_log_id

    get_client().table("offers").update(payload).eq("id", cleaned_offer_id).execute()


def update_offer_from_training(
    offer_id: str,
    *,
    watch: Record,
    message_id: str | None = None,
    line_index: int | None = None,
) -> Record:
    """Update an existing offer from a finalized parser-training payload."""
    from condition_normalizer import normalize_condition_value

    cleaned_offer_id = str(offer_id or "").strip()
    if not _is_valid_uuid(cleaned_offer_id):
        raise ValueError("Invalid offer id")

    existing = get_offer_by_id(cleaned_offer_id)
    if existing is None:
        raise ValueError("Offer not found")

    from fpj_model_knowledge import apply_fpj_enrichment, fpj_storage_identity_fields

    enriched_watch = apply_fpj_enrichment(dict(watch), str(watch.get("source_line") or ""))
    identity = fpj_storage_identity_fields(enriched_watch)

    watch_row, _ = find_or_create_watch(
        brand=identity.get("brand"),
        reference=identity.get("reference"),
        model=identity.get("model"),
        dial=identity.get("dial"),
        bracelet=identity.get("bracelet"),
    )

    production_year = watch.get("production_year")
    if not isinstance(production_year, int):
        production_year = None

    payload: Record = {
        "watch_id": watch_row["id"],
        "condition": _storage_value(normalize_condition_value(watch.get("condition"))),
        "production_year": production_year,
        "card_date": _storage_value(watch.get("card_date")),
        "notes": _storage_value(watch.get("notes")),
        "original_price": watch.get("original_price") or watch.get("price"),
        "original_currency": _storage_value(watch.get("original_currency") or watch.get("currency")),
        "usd_price": watch.get("usd_price"),
        "exchange_rate_to_usd": watch.get("exchange_rate_to_usd"),
    }
    if message_id and _is_valid_uuid(message_id.strip()):
        payload["message_id"] = message_id.strip()
    if line_index is not None:
        payload["line_index"] = line_index

    response = (
        get_client()
        .table("offers")
        .update(payload)
        .eq("id", cleaned_offer_id)
        .execute()
    )
    updated = _first_row(response.data, "offers")
    return updated or {**existing, **payload, "id": cleaned_offer_id}


def link_import_log_to_summary_offers(
    import_log_id: str,
    message_id: str,
    summary: Record,
) -> None:
    """Link every offer listed in an import summary to its import log source."""
    for row in summary.get("rows") or []:
        if not isinstance(row, dict):
            continue
        offer_id = row.get("offer_id")
        if not offer_id:
            continue
        link_offer_to_import_source(
            str(offer_id),
            message_id=str(message_id),
            source_import_log_id=str(import_log_id),
        )


def get_request_matches_for_offer_ids(offer_ids: list[str]) -> list[Record]:
    """Return request_matches rows for the given offer ids."""
    if not offer_ids:
        return []

    unique_offer_ids = list(dict.fromkeys(_normalize_uuid_key(offer_id) for offer_id in offer_ids if offer_id))
    if not unique_offer_ids:
        return []

    response = (
        get_client()
        .table("request_matches")
        .select("id, request_id, offer_id, import_log_id, match_strength, match_reason, created_at")
        .in_("offer_id", unique_offer_ids)
        .execute()
    )
    return response.data or []


def _import_log_source_select_columns() -> str:
    columns = "id, message_id, watches_parsed, status, summary"
    if user_ownership_columns_supported():
        columns += ", imported_by_user_id"
    return columns


def get_import_logs_for_source_resolution(import_log_ids: list[str]) -> dict[str, Record]:
    """Return import logs with fields required for source URL visibility checks."""
    if not import_log_ids:
        return {}

    unique_ids = list(dict.fromkeys(str(import_log_id) for import_log_id in import_log_ids if import_log_id))
    if not unique_ids:
        return {}

    response = (
        get_client()
        .table("import_logs")
        .select(_import_log_source_select_columns())
        .in_("id", unique_ids)
        .execute()
    )
    return {str(row["id"]): row for row in response.data or [] if row.get("id")}


def get_import_logs_by_offer_ids(offer_ids: list[str]) -> dict[str, Record]:
    """Return import logs keyed by offer id via request_matches and summary rows."""
    if not offer_ids:
        return {}

    unique_offer_ids = list(dict.fromkeys(_normalize_uuid_key(offer_id) for offer_id in offer_ids if offer_id))
    if not unique_offer_ids:
        return {}

    by_offer_id: dict[str, Record] = {}
    response = (
        get_client()
        .table("request_matches")
        .select("offer_id, import_log_id")
        .in_("offer_id", unique_offer_ids)
        .execute()
    )
    import_log_ids = list(
        dict.fromkeys(
            str(row["import_log_id"])
            for row in response.data or []
            if row.get("import_log_id")
        )
    )
    import_logs_by_id = get_import_logs_for_source_resolution(import_log_ids)
    for row in response.data or []:
        offer_id = _normalize_uuid_key(row.get("offer_id"))
        import_log_id = str(row.get("import_log_id") or "")
        if not offer_id or not import_log_id:
            continue
        import_log = import_logs_by_id.get(import_log_id)
        if import_log and offer_id not in by_offer_id:
            by_offer_id[offer_id] = import_log

    unresolved_offer_ids = [offer_id for offer_id in unique_offer_ids if offer_id not in by_offer_id]
    for offer_id, import_log in find_import_logs_by_summary_offer_ids(unresolved_offer_ids).items():
        by_offer_id.setdefault(offer_id, import_log)

    return by_offer_id


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
    from search import _reference_contains_token, reference_lookup_tokens

    token = reference_query.strip()
    if not token:
        return []

    matched: list[Record] = []
    seen_ids: set[str] = set()
    offset = 0
    lookup_tokens = reference_lookup_tokens(token)

    while True:
        query = get_client().table("watches").select("*")
        if lookup_tokens:
            for lookup_token in lookup_tokens:
                query = query.ilike("reference", f"%{lookup_token}%")
        else:
            query = query.not_.is_("reference", "null")

        response = query.range(offset, offset + WATCH_LOOKUP_PAGE_SIZE - 1).execute()
        batch = response.data or []
        for watch in batch:
            watch_id = str(watch.get("id") or "")
            if (
                watch_id
                and watch_id not in seen_ids
                and _reference_contains_token(watch.get("reference"), token)
            ):
                seen_ids.add(watch_id)
                matched.append(watch)
        if len(batch) < WATCH_LOOKUP_PAGE_SIZE:
            break
        offset += WATCH_LOOKUP_PAGE_SIZE

    return matched


def _build_watch_brand_reference_scan_query(brand: str | None, reference: str | None):
    """Build a paginated watches scan query for a brand + reference group lookup."""
    from search import reference_lookup_tokens

    query = get_client().table("watches").select("id, brand, reference")
    brand_filter = (brand or "").strip()
    if brand_filter:
        query = query.ilike("brand", f"%{brand_filter}%")

    lookup_tokens = reference_lookup_tokens(reference)
    if lookup_tokens:
        for token in lookup_tokens:
            query = query.ilike("reference", f"%{token}%")
    else:
        query = query.not_.is_("reference", "null")

    return query


def find_watch_ids_for_brand_reference(brand: str | None, reference: str | None) -> list[str]:
    """Return watch ids that share the same brand + reference group key."""
    from search import brand_reference_group_key

    target_key = brand_reference_group_key({"brand": brand, "reference": reference})
    if not target_key[0] or not target_key[1]:
        return []

    matched_ids: list[str] = []
    offset = 0

    while True:
        query = _build_watch_brand_reference_scan_query(brand, reference)
        response = query.range(offset, offset + WATCH_LOOKUP_PAGE_SIZE - 1).execute()
        batch = response.data or []
        for watch in batch:
            if watch.get("id") and brand_reference_group_key(watch) == target_key:
                matched_ids.append(str(watch["id"]))
        if len(batch) < WATCH_LOOKUP_PAGE_SIZE:
            break
        offset += WATCH_LOOKUP_PAGE_SIZE

    return matched_ids


def trace_brand_reference_lookup(brand: str | None, reference: str | None) -> dict[str, Any]:
    """Debug helper comparing search/detail brand + reference resolution."""
    from search import brand_reference_group_key, reference_lookup_tokens

    target_key = brand_reference_group_key({"brand": brand, "reference": reference})
    watch_ids = find_watch_ids_for_brand_reference(brand, reference)
    offers = get_active_offers_for_brand_reference(brand, reference) if watch_ids else []

    return {
        "brand": brand,
        "reference": reference,
        "target_key": target_key,
        "normalized_reference": target_key[1],
        "lookup_tokens": reference_lookup_tokens(reference),
        "watch_ids": watch_ids,
        "watch_count": len(watch_ids),
        "offer_count": len(offers),
    }


def get_active_offers_for_brand_reference(
    brand: str | None,
    reference: str | None,
) -> list[Record]:
    """Return active offers for every watch row matching brand + reference."""
    watch_ids = find_watch_ids_for_brand_reference(brand, reference)
    if not watch_ids:
        return []

    dealer_fields = (
        "dealers(display_name, phone_number, whatsapp_id, contact_type)"
        if contact_type_column_supported()
        else "dealers(display_name, phone_number, whatsapp_id)"
    )
    select_fields = (
        "id, message_id, source_import_log_id, dealer_id, watch_id, original_price, original_currency, usd_price, card_date, condition, "
        f"watches(dial), "
        f"{dealer_fields}, "
        "messages(id, received_at, group_id, groups(name))"
    )
    if not source_import_log_id_column_supported():
        select_fields = (
            "id, message_id, dealer_id, watch_id, original_price, original_currency, usd_price, card_date, condition, "
            f"watches(dial), "
            f"{dealer_fields}, "
            "messages(id, received_at, group_id, groups(name))"
        )
    rows = _query_table_in_id_chunks(
        "offers",
        select_fields,
        watch_ids,
        id_column="watch_id",
        apply_filters=lambda request: request.eq("status", "active"),
    )
    return [offer for offer in rows if _offer_from_business_dealer(offer)]


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
    include_owner = bool(created_by_user_id and request_created_by_user_id_supported())
    if include_owner:
        payload["created_by_user_id"] = created_by_user_id

    try:
        response = get_client().table("requests").insert(payload).execute()
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if include_owner and (code == "PGRST204" or "created_by_user_id" in message):
            global _request_created_by_user_id_supported
            _request_created_by_user_id_supported = False
            logger.warning(
                "requests.created_by_user_id column missing during insert; apply "
                "docs/migrations/sprint_46_2_request_ownership.sql"
            )
            payload.pop("created_by_user_id", None)
            try:
                response = get_client().table("requests").insert(payload).execute()
            except APIError as retry_exc:
                raise RequestSchemaError() from retry_exc
            return _first_row(response.data, "requests")
        if code == "PGRST204":
            raise RequestSchemaError() from exc
        raise
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


def _is_valid_uuid(value: str) -> bool:
    return bool(_UUID_PATTERN.match(value))


def _normalize_lookup_ids(raw_ids: list[str], *, require_uuid: bool = False) -> list[str]:
    """Return deduplicated non-empty ids preserving first-seen order."""
    normalized: list[str] = []
    seen: set[str] = set()
    skipped_invalid = 0
    for raw in raw_ids:
        if raw is None:
            continue
        cleaned = str(raw).strip()
        if not cleaned or cleaned in seen:
            continue
        if require_uuid and not _is_valid_uuid(cleaned):
            skipped_invalid += 1
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    if skipped_invalid:
        logger.warning(
            "Skipped %s invalid lookup id(s) for PostgREST in.(...) query",
            skipped_invalid,
        )
    return normalized


def _query_table_in_id_chunks(
    table_name: str,
    select_fields: str,
    ids: list[str],
    *,
    id_column: str = "id",
    require_uuid: bool = True,
    apply_filters: Any | None = None,
) -> list[Record]:
    """Load rows for many ids using bounded PostgREST in.(...) batches."""
    cleaned_ids = _normalize_lookup_ids(ids, require_uuid=require_uuid)
    if not cleaned_ids:
        return []

    rows: list[Record] = []
    for offset in range(0, len(cleaned_ids), LOOKUP_IDS_CHUNK_SIZE):
        chunk = cleaned_ids[offset : offset + LOOKUP_IDS_CHUNK_SIZE]
        try:
            request = (
                get_client()
                .table(table_name)
                .select(select_fields)
                .in_(id_column, chunk)
            )
            if apply_filters is not None:
                request = apply_filters(request)
            response = request.execute()
        except Exception as exc:
            logger.warning(
                "%s chunk lookup failed on %s (offset=%s, size=%s): %s",
                table_name,
                id_column,
                offset,
                len(chunk),
                exc,
            )
            continue
        rows.extend(response.data or [])
    return rows


def get_offers_by_ids(offer_ids: list[str]) -> dict[str, Record]:
    """Return offers keyed by id, querying in bounded chunks."""
    select_fields = (
        "id, watch_id, original_price, original_currency, usd_price, condition, card_date, production_year"
    )
    rows = _query_table_in_id_chunks("offers", select_fields, offer_ids, id_column="id")
    return {str(row["id"]): row for row in rows if row.get("id")}


def query_active_offers_for_watch_ids(watch_ids: list[str]) -> list[Record]:
    """Return active offer rows for many watch ids using bounded PostgREST batches."""
    dealer_fields = (
        "dealers(contact_type)"
        if contact_type_column_supported()
        else "dealers(whatsapp_id)"
    )
    select_fields = (
        "id, watch_id, usd_price, condition, production_year, card_date, "
        "original_price, original_currency, watches(brand, reference)"
        f", {dealer_fields}"
    )
    return _query_table_in_id_chunks(
        "offers",
        select_fields,
        watch_ids,
        id_column="watch_id",
        apply_filters=lambda request: request.eq("status", "active"),
    )


def get_watches_by_ids(watch_ids: list[str]) -> dict[str, Record]:
    """Return watches keyed by id."""
    rows = _query_table_in_id_chunks(
        "watches",
        "id, brand, reference, model, dial",
        watch_ids,
        id_column="id",
    )
    return {str(row["id"]): row for row in rows if row.get("id")}


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


def activity_import_log_list_columns() -> str:
    """Return columns required to render the Activity list page."""
    if user_ownership_columns_supported():
        return f"{IMPORT_LOG_ACTIVITY_LIST_COLUMNS},imported_by_user_id"
    return IMPORT_LOG_ACTIVITY_LIST_COLUMNS


def _is_supabase_statement_timeout(exc: BaseException) -> bool:
    """Return True when Supabase/Postgres cancelled a query for statement timeout."""
    message = str(exc).lower()
    if "57014" in message or "statement timeout" in message:
        return True
    if isinstance(exc, APIError):
        code = str(getattr(exc, "code", "") or "")
        if code == "57014":
            return True
    return False


def import_log_detail_columns_full() -> str:
    """Return the full column projection for import log detail views."""
    if user_ownership_columns_supported():
        return f"{IMPORT_LOG_LIST_COLUMNS_FULL},imported_by_user_id"
    return IMPORT_LOG_LIST_COLUMNS_FULL


def import_log_list_columns() -> str:
    """Return list-query columns (lightweight; excludes summary JSON)."""
    return import_log_list_columns_light()


def _is_transient_import_summary_error(exc: BaseException) -> bool:
    """Return True for retryable Supabase/Cloudflare gateway failures."""
    message = str(exc).lower()
    if "json could not be generated" in message:
        return True
    if "cloudflare" in message:
        return True
    if "timeout" in message or "timed out" in message:
        return True
    for code in ("521", "502", "503", "504", "500"):
        if code in message:
            return True
    if isinstance(exc, APIError):
        error_code = str(getattr(exc, "code", "") or "")
        if error_code.isdigit() and int(error_code) >= 500:
            return True
    return False


def _execute_import_log_summary_batch(chunk: list[str]) -> list[Record]:
    """Fetch one import_logs summary batch with retry for transient failures."""
    last_exc: Exception | None = None
    for attempt in range(1, IMPORT_LOG_SUMMARY_MAX_RETRIES + 1):
        started = time.perf_counter()
        try:
            response = (
                get_client()
                .table("import_logs")
                .select("id,summary")
                .in_("id", chunk)
                .execute()
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > IMPORT_LOG_SUMMARY_BATCH_TIMEOUT_SECONDS * 1000:
                logger.warning(
                    "import_log summary batch exceeded timeout threshold: "
                    "ids=%s elapsed_ms=%s threshold_ms=%s",
                    len(chunk),
                    elapsed_ms,
                    IMPORT_LOG_SUMMARY_BATCH_TIMEOUT_SECONDS * 1000,
                )
            return response.data or []
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            last_exc = exc
            if attempt < IMPORT_LOG_SUMMARY_MAX_RETRIES and _is_transient_import_summary_error(exc):
                logger.warning(
                    "import_log summary batch transient failure "
                    "(attempt %s/%s, ids=%s, elapsed_ms=%s): %s",
                    attempt,
                    IMPORT_LOG_SUMMARY_MAX_RETRIES,
                    len(chunk),
                    elapsed_ms,
                    exc,
                )
                time.sleep(IMPORT_LOG_SUMMARY_RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return []


def get_import_log_summaries_by_ids(import_log_ids: list[str]) -> dict[str, Record]:
    """Return summary JSON keyed by import log id, loading in bounded batches."""
    if not import_log_ids:
        return {}

    unique_ids = list(dict.fromkeys(str(import_log_id) for import_log_id in import_log_ids if import_log_id))
    started = time.perf_counter()
    logger.info(
        "Loading import_log summaries: requested_ids=%s batch_size=%s",
        len(unique_ids),
        IMPORT_LOG_SUMMARY_BATCH_SIZE,
    )

    summaries: dict[str, Record] = {}
    failed_batches = 0
    for offset in range(0, len(unique_ids), IMPORT_LOG_SUMMARY_BATCH_SIZE):
        chunk = unique_ids[offset : offset + IMPORT_LOG_SUMMARY_BATCH_SIZE]
        batch_index = offset // IMPORT_LOG_SUMMARY_BATCH_SIZE
        try:
            rows = _execute_import_log_summary_batch(chunk)
        except Exception as exc:
            failed_batches += 1
            logger.warning(
                "import_log summary batch failed: batch_index=%s batch_size=%s "
                "sample_ids=%s error=%s",
                batch_index,
                len(chunk),
                chunk[:3],
                exc,
            )
            continue

        for row in rows:
            summary = row.get("summary")
            summaries[str(row["id"])] = summary if isinstance(summary, dict) else {}

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "import_log summaries loaded: requested=%s loaded=%s failed_batches=%s elapsed_ms=%s",
        len(unique_ids),
        len(summaries),
        failed_batches,
        elapsed_ms,
    )
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

    try:
        summaries_by_id = get_import_log_summaries_by_ids(missing_ids)
    except Exception as exc:
        logger.warning(
            "attach_import_log_summaries failed for %s id(s); continuing without summaries: %s",
            len(missing_ids),
            exc,
        )
        summaries_by_id = {}

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
    """Return one page of activity import logs with tab-aware database filters."""
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    if limit < 1:
        raise ValueError("limit must be at least 1")

    capped_limit = min(int(limit), ACTIVITY_IMPORT_LOG_MAX_LIMIT)
    start = max(0, int(offset))
    end = start + capped_limit - 1

    query = (
        get_client()
        .table("import_logs")
        .select(activity_import_log_list_columns())
        .order("created_at", desc=True)
    )
    query = _apply_activity_tab_filters(query, tab)
    try:
        response = query.range(start, end).execute()
    except APIError as exc:
        if _is_supabase_statement_timeout(exc):
            logger.warning(
                "list_activity_import_logs timed out: tab=%s offset=%s limit=%s",
                tab,
                start,
                capped_limit,
            )
            return []
        raise
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


def list_parser_training_candidate_import_logs(
    *,
    limit: int = IMPORT_LOG_LIST_LIMIT_PARSER_REVIEW,
) -> list[Record]:
    """Return recent imports with parsed offer rows for Parser Training Center."""
    return list_parser_training_import_logs(offset=0, limit=limit)


def list_parser_training_import_logs(
    *,
    since_iso: str | None = None,
    offset: int = 0,
    limit: int,
) -> list[Record]:
    """Return bounded parser-training import_logs with optional import_time filter."""
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    if limit < 1:
        raise ValueError("limit must be at least 1")

    query = (
        get_client()
        .table("import_logs")
        .select(import_log_list_columns_light())
        .gt("watches_parsed", 0)
        .order("import_time", desc=True)
    )
    if since_iso:
        query = query.gte("import_time", since_iso)
    response = query.range(offset, offset + limit - 1).execute()
    return response.data or []


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
    if not is_valid_uuid(message_id):
        return None

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


def upsert_reference_brand_mapping(
    *,
    reference: str,
    brand_name: str,
    source: str = "manual",
    source_confidence: str = "high",
    dry_run: bool = True,
) -> Record:
    """Create or update a trusted reference-brand mapping with conflict reporting."""
    from watch_knowledge import normalize_reference

    reference_key = normalize_reference(reference)
    if not reference_key:
        raise ValueError("Reference is required")
    cleaned_brand = brand_name.strip()
    if not cleaned_brand:
        raise ValueError("Brand name is required")

    result: Record = {
        "reference": reference.strip().upper(),
        "reference_key": reference_key,
        "brand_name": cleaned_brand,
        "source": source,
        "source_confidence": source_confidence,
        "dry_run": dry_run,
        "conflict": None,
        "action": "skipped",
    }

    if not reference_brand_mappings_supported():
        result["action"] = "unsupported"
        return result

    existing = (
        get_client()
        .table("reference_brand_mappings")
        .select("id,brand_name,status,source")
        .eq("reference_key", reference_key)
        .limit(1)
        .execute()
    )
    existing_row = (existing.data or [None])[0]
    if existing_row:
        current_brand = str(existing_row.get("brand_name") or "")
        if current_brand and current_brand != cleaned_brand:
            result["conflict"] = {
                "reference_key": reference_key,
                "existing_brand": current_brand,
                "proposed_brand": cleaned_brand,
                "existing_source": existing_row.get("source"),
            }
            if source_confidence not in {"high", "verified", "authority"}:
                result["action"] = "conflict_blocked"
                return result
        if dry_run:
            result["action"] = "would_update" if current_brand != cleaned_brand else "unchanged"
            return result
        return {
            **result,
            "action": "updated",
            "row": create_reference_brand_mapping(
                reference=reference,
                brand_name=cleaned_brand,
                source=source,
            ),
        }

    if dry_run:
        result["action"] = "would_insert"
        return result

    return {
        **result,
        "action": "inserted",
        "row": create_reference_brand_mapping(
            reference=reference,
            brand_name=cleaned_brand,
            source=source,
        ),
    }


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


def parser_learning_rules_supported() -> bool:
    """Return True when parser_learning_rules table exists."""
    global _parser_learning_rules_supported
    if _parser_learning_rules_supported is not None:
        return _parser_learning_rules_supported

    try:
        get_client().table("parser_learning_rules").select("id").limit(1).execute()
        _parser_learning_rules_supported = True
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code in {"42P01", "PGRST205"} or "parser_learning_rules" in message:
            _parser_learning_rules_supported = False
        else:
            raise
    return _parser_learning_rules_supported


def list_parser_learning_rules(*, include_disabled: bool = False) -> list[Record]:
    """Return parser learning rules ordered by newest first."""
    if not parser_learning_rules_supported():
        return []

    query = get_client().table("parser_learning_rules").select("*")
    if not include_disabled:
        query = query.eq("status", "active")
    response = query.order("created_at", desc=True).execute()
    return response.data or []


def list_active_parser_learning_rules() -> list[Record]:
    """Return active parser learning rules with a short-lived in-process cache."""
    global _parser_learning_rules_cache
    if _parser_learning_rules_cache is not None:
        return list(_parser_learning_rules_cache)

    rules = list_parser_learning_rules(include_disabled=False)
    _parser_learning_rules_cache = list(rules)
    return rules


def get_parser_learning_rule(rule_id: str) -> Record | None:
    """Return one parser learning rule by id."""
    if not parser_learning_rules_supported():
        return None

    response = (
        get_client()
        .table("parser_learning_rules")
        .select("*")
        .eq("id", rule_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def create_parser_learning_rule(
    *,
    field_type: str,
    term: str,
    normalized_value: str,
    scope: str = "global",
    dealer_id: str | None = None,
    group_id: str | None = None,
    source_import_log_id: str | None = None,
    created_by_user_id: str | None = None,
) -> Record:
    """Create or reactivate a parser learning rule."""
    if not parser_learning_rules_supported():
        raise RuntimeError(
            "Parser learning rules require the Sprint 49.0 migration. Apply "
            "docs/migrations/sprint_49_0_parser_learning_rules.sql in Supabase."
        )

    cleaned_term = term.strip()
    cleaned_value = normalized_value.strip()
    if not cleaned_term:
        raise ValueError("Term is required")
    if not cleaned_value:
        raise ValueError("Normalized value is required")

    now = datetime.now(timezone.utc).isoformat()
    payload: Record = {
        "field_type": field_type.strip().lower(),
        "term": cleaned_term,
        "normalized_value": cleaned_value,
        "scope": scope.strip().lower(),
        "dealer_id": dealer_id,
        "group_id": group_id,
        "source_import_log_id": source_import_log_id,
        "created_by_user_id": created_by_user_id,
        "status": "active",
        "updated_at": now,
    }

    existing = (
        get_client()
        .table("parser_learning_rules")
        .select("*")
        .eq("field_type", payload["field_type"])
        .eq("term", cleaned_term)
        .eq("scope", payload["scope"])
        .limit(20)
        .execute()
    )
    for row in existing.data or []:
        same_dealer = str(row.get("dealer_id") or "") == str(dealer_id or "")
        same_group = str(row.get("group_id") or "") == str(group_id or "")
        if payload["scope"] == "global" or (
            payload["scope"] == "dealer" and same_dealer
        ) or (
            payload["scope"] == "group" and same_group
        ):
            response = (
                get_client()
                .table("parser_learning_rules")
                .update(payload)
                .eq("id", row["id"])
                .execute()
            )
            invalidate_parser_learning_rules_cache()
            return _first_row(response.data, "parser_learning_rules")

    response = get_client().table("parser_learning_rules").insert(payload).execute()
    invalidate_parser_learning_rules_cache()
    return _first_row(response.data, "parser_learning_rules")


def update_parser_learning_rule(rule_id: str, **fields: Any) -> Record:
    """Update editable parser learning rule fields."""
    if not parser_learning_rules_supported():
        raise RuntimeError("Parser learning rules table is not available")

    allowed = {
        "field_type",
        "term",
        "normalized_value",
        "scope",
        "dealer_id",
        "group_id",
        "status",
    }
    payload = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not payload:
        raise ValueError("No valid fields to update")
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    response = (
        get_client()
        .table("parser_learning_rules")
        .update(payload)
        .eq("id", rule_id)
        .execute()
    )
    invalidate_parser_learning_rules_cache()
    return _first_row(response.data, "parser_learning_rules")


def disable_parser_learning_rule(rule_id: str) -> Record:
    """Disable a parser learning rule without deleting history."""
    return update_parser_learning_rule(rule_id, status="disabled")


def get_offers_by_message_id(message_id: str) -> list[Record]:
    """Return all offers linked to one message."""
    cleaned = message_id.strip()
    if not cleaned:
        return []

    response = (
        get_client()
        .table("offers")
        .select(
            "id, message_id, watch_id, dealer_id, status, line_index, "
            "original_price, original_currency, usd_price, condition"
        )
        .eq("message_id", cleaned)
        .order("line_index")
        .execute()
    )
    return response.data or []


    return response.data or []


def parser_training_rows_schema_status() -> Record:
    """Return parser_training_rows availability for UI banners and debug traces."""
    global _parser_training_rows_supported
    try:
        get_client().table("parser_training_rows").select("id").limit(1).execute()
        _parser_training_rows_supported = True
        return {
            "status": "supported",
            "message": "parser_training_rows table is accessible.",
        }
    except APIError as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).lower()
        if code == "PGRST205" or "schema cache" in message:
            _parser_training_rows_supported = False
            return {
                "status": "schema_cache_stale",
                "message": (
                    "parser_training_rows exists but Supabase PostgREST schema cache is stale. "
                    "Reload the schema in Supabase Dashboard → Settings → API."
                ),
            }
        if code in {"42P01", "PGRST205"} or "parser_training_rows" in message:
            _parser_training_rows_supported = False
            return {
                "status": "missing",
                "message": (
                    "parser_training_rows table is missing. Apply "
                    "docs/migrations/sprint_50_0_parser_training_rows.sql in Supabase."
                ),
            }
        raise
    except Exception as exc:
        _parser_training_rows_supported = False
        return {
            "status": "error",
            "message": f"parser_training_rows check failed: {exc}",
        }


def parser_training_rows_supported() -> bool:
    """Return True when parser_training_rows table exists."""
    global _parser_training_rows_supported
    if _parser_training_rows_supported is not None:
        return _parser_training_rows_supported

    status = parser_training_rows_schema_status()["status"]
    _parser_training_rows_supported = status == "supported"
    return _parser_training_rows_supported


PARSER_TRAINING_ROWS_PAGE_SIZE = 1000


def list_parser_training_rows_for_imports(
    import_log_ids: list[str],
) -> dict[str, list[Record]]:
    """Return training rows grouped by import_log_id."""
    if not parser_training_rows_supported() or not import_log_ids:
        return {}

    unique_ids = [str(import_id) for import_id in import_log_ids if str(import_id).strip()]
    if not unique_ids:
        return {}

    by_import: dict[str, list[Record]] = {import_id: [] for import_id in unique_ids}
    offset = 0
    while True:
        response = (
            get_client()
            .table("parser_training_rows")
            .select("*")
            .in_("import_log_id", unique_ids)
            .order("import_log_id")
            .order("row_index")
            .range(offset, offset + PARSER_TRAINING_ROWS_PAGE_SIZE - 1)
            .execute()
        )
        batch = response.data or []
        if not batch:
            break
        for row in batch:
            import_id = str(row.get("import_log_id") or "")
            if import_id in by_import:
                by_import[import_id].append(row)
        if len(batch) < PARSER_TRAINING_ROWS_PAGE_SIZE:
            break
        offset += PARSER_TRAINING_ROWS_PAGE_SIZE

    for import_id in by_import:
        by_import[import_id].sort(key=lambda item: int(item.get("row_index") or 0))
    return by_import


def list_parser_training_rows_for_import(import_log_id: str) -> list[Record]:
    """Return all training rows for one import, ordered by row_index."""
    return list_parser_training_rows_for_imports([import_log_id]).get(str(import_log_id), [])


def get_parser_training_row(row_id: str) -> Record | None:
    """Return one parser training row by id."""
    if not parser_training_rows_supported() or not is_valid_uuid(row_id):
        return None

    response = (
        get_client()
        .table("parser_training_rows")
        .select("*")
        .eq("id", row_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


def upsert_parser_training_row(payload: Record) -> Record:
    """Insert or update a parser training row by import_log_id + row_index."""
    _ensure_parser_training_row_writes_allowed("upsert")
    if not parser_training_rows_supported():
        raise RuntimeError(
            "Parser training rows require Sprint 50.0 migration. Apply "
            "docs/migrations/sprint_50_0_parser_training_rows.sql in Supabase."
        )

    now = datetime.now(timezone.utc).isoformat()
    import_log_id = str(payload.get("import_log_id") or "")
    row_index = int(payload.get("row_index") or 0)
    if not import_log_id:
        raise ValueError("import_log_id is required")
    if not is_valid_uuid(import_log_id):
        raise ValueError(f"import_log_id must be a UUID, got: {import_log_id}")

    body = dict(payload)
    source_message_id = body.get("source_message_id")
    if source_message_id is not None and not is_valid_uuid(str(source_message_id)):
        body.pop("source_message_id", None)
    created_offer_id = body.get("created_offer_id")
    if created_offer_id is not None and not is_valid_uuid(str(created_offer_id)):
        body["created_offer_id"] = None
    created_by_user_id = body.get("created_by_user_id")
    if created_by_user_id is not None and not is_valid_uuid(str(created_by_user_id)):
        body.pop("created_by_user_id", None)

    existing = (
        get_client()
        .table("parser_training_rows")
        .select("id")
        .eq("import_log_id", import_log_id)
        .eq("row_index", row_index)
        .limit(1)
        .execute()
    )
    body = dict(body)
    body["updated_at"] = now
    if existing.data:
        response = (
            get_client()
            .table("parser_training_rows")
            .update(body)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        body.setdefault("created_at", now)
        response = get_client().table("parser_training_rows").insert(body).execute()
    return _first_row(response.data, "parser_training_rows")


def bulk_upsert_parser_training_rows(rows: list[Record]) -> list[Record]:
    """Upsert multiple parser training rows for one import."""
    return [upsert_parser_training_row(row) for row in rows]


def update_parser_training_row(row_id: str, **fields: Any) -> Record:
    """Update fields on one parser training row."""
    _ensure_parser_training_row_writes_allowed("update")
    if not parser_training_rows_supported():
        raise RuntimeError("Parser training rows table is not available")

    payload = {key: value for key, value in fields.items() if value is not None}
    if not payload:
        raise ValueError("No valid fields to update")
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    response = (
        get_client()
        .table("parser_training_rows")
        .update(payload)
        .eq("id", row_id)
        .execute()
    )
    return _first_row(response.data, "parser_training_rows")


def parser_training_reference_lookup_key(reference: str | None) -> str | None:
    """Normalize a reference for indexed parser_training_rows lookups."""
    if not reference or not isinstance(reference, str):
        return None
    cleaned = reference.strip().upper()
    return cleaned or None


def list_parser_training_rows_for_reference(
    reference: str,
    *,
    limit: int = PARSER_TRAINING_REFERENCE_BATCH_SIZE,
    offset: int = 0,
) -> list[Record]:
    """Return a capped page of training rows that match a reference (any import)."""
    if not parser_training_rows_supported():
        return []

    key = parser_training_reference_lookup_key(reference)
    if not key:
        return []

    batch_limit = max(1, min(int(limit), PARSER_TRAINING_REFERENCE_QUERY_MAX))
    start = max(0, int(offset))
    end = start + batch_limit - 1

    started = time.perf_counter()
    response = (
        get_client()
        .table("parser_training_rows")
        .select(PARSER_TRAINING_REFERENCE_ROW_COLUMNS)
        .eq("normalized_reference", key)
        .order("id")
        .range(start, end)
        .execute()
    )
    rows = response.data or []
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "list_parser_training_rows_for_reference reference=%s offset=%s limit=%s "
        "rows=%s elapsed_ms=%s",
        key,
        start,
        batch_limit,
        len(rows),
        elapsed_ms,
    )
    if elapsed_ms >= 1000:
        logger.warning(
            "Slow parser_training_rows reference lookup reference=%s elapsed_ms=%s "
            "(expected idx_parser_training_rows_norm_reference_id)",
            key,
            elapsed_ms,
        )
    return rows


def list_parser_training_import_summaries(
    import_log_ids: list[str],
) -> list[Record]:
    """Return per-import row counts for the given import_log_ids only."""
    if not parser_training_rows_supported():
        return []

    from parser_training_engine import summarize_training_rows_by_status

    unique_ids = [str(import_id) for import_id in import_log_ids if str(import_id).strip()]
    if not unique_ids:
        return []

    rows: list[Record] = []
    offset = 0
    while True:
        response = (
            get_client()
            .table("parser_training_rows")
            .select("import_log_id, status, created_offer_id")
            .in_("import_log_id", unique_ids)
            .range(offset, offset + PARSER_TRAINING_ROWS_PAGE_SIZE - 1)
            .execute()
        )
        batch = response.data or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PARSER_TRAINING_ROWS_PAGE_SIZE:
            break
        offset += PARSER_TRAINING_ROWS_PAGE_SIZE

    by_import: dict[str, list[Record]] = {import_id: [] for import_id in unique_ids}
    for row in rows:
        import_id = str(row.get("import_log_id") or "")
        if import_id in by_import:
            by_import[import_id].append(row)

    summaries: list[Record] = []
    for import_id in unique_ids:
        summary = summarize_training_rows_by_status(by_import.get(import_id) or [])
        summary["import_log_id"] = import_id
        summaries.append(summary)
    return summaries


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
