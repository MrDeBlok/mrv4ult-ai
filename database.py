"""Supabase database helpers for MRV4ULT AI."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)

Record = dict[str, Any]

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
        ("condition", _storage_value(condition)),
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


def find_matching_requests(
    *,
    brand: str | None,
    reference: str | None,
    dial: str | None = None,
    bracelet: str | None = None,
    usd_price: int | None = None,
) -> list[Record]:
    """Return active requests that match the given offer watch fields."""
    response = (
        get_client().table("requests").select("*").eq("status", "active").execute()
    )
    requests = response.data or []

    matches: list[Record] = []
    for request in requests:
        if _request_matches_offer(
            request,
            brand=brand,
            reference=reference,
            dial=dial,
            bracelet=bracelet,
            usd_price=usd_price,
        ):
            matches.append(request)
    return matches


def _request_matches_offer(
    request: Record,
    *,
    brand: str | None,
    reference: str | None,
    dial: str | None,
    bracelet: str | None,
    usd_price: int | None,
) -> bool:
    if not _required_field_matches(brand, request.get("brand")):
        return False
    if not _required_field_matches(reference, request.get("reference")):
        return False
    if not _optional_field_matches(dial, request.get("dial")):
        return False
    if not _optional_field_matches(bracelet, request.get("bracelet")):
        return False

    max_usd_price = request.get("max_usd_price")
    if max_usd_price is not None:
        if usd_price is None or usd_price > max_usd_price:
            return False
    return True


def _required_field_matches(
    offer_value: str | None,
    request_value: str | None,
) -> bool:
    return _normalize_watch_value(offer_value) == _normalize_watch_value(request_value)


def _optional_field_matches(
    offer_value: str | None,
    request_value: str | None,
) -> bool:
    request_normalized = _normalize_watch_value(request_value)
    if request_normalized is None:
        return True
    return _normalize_watch_value(offer_value) == request_normalized


def get_watch_by_id(watch_id: str) -> Record | None:
    """Return a watch row by id, or None if it does not exist."""
    response = (
        get_client().table("watches").select("*").eq("id", watch_id).limit(1).execute()
    )
    if not response.data:
        return None
    return response.data[0]


def get_active_offers_for_watch(watch_id: str) -> list[Record]:
    """Return all active offers for a watch with dealer, group, and message metadata."""
    response = (
        get_client()
        .table("offers")
        .select(
            "dealer_id, original_price, original_currency, usd_price, card_date, condition, "
            "dealers(display_name, phone_number, whatsapp_id), "
            "messages(received_at, group_id, groups(name))"
        )
        .eq("watch_id", watch_id)
        .eq("status", "active")
        .execute()
    )
    return response.data or []


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
) -> tuple[Record, bool, int]:
    """Insert an offer unless an identical active offer already exists.

    Returns the offer record, whether it was newly created, and the number of
    matched active requests.
    """
    normalized_currency = _storage_value(original_currency)
    normalized_condition = _storage_value(condition)
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
        return existing, False, 0

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

    watch = _get_watch_by_id(watch_id)
    matches = find_matching_requests(
        brand=watch.get("brand"),
        reference=watch.get("reference"),
        dial=watch.get("dial"),
        bracelet=watch.get("bracelet"),
        usd_price=usd_price,
    )
    for request in matches:
        print(f"Match found for request {request['id']}")

    return created, True, len(matches)


def _first_row(data: list[Record] | None, table: str) -> Record:
    if not data:
        raise RuntimeError(f"Supabase returned no rows for {table}.")
    return data[0]
