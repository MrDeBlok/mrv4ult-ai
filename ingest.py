"""Ingest parsed WhatsApp messages into Supabase."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from database import (
    find_or_create_watch,
    get_client,
    insert_message,
    insert_offer,
)
from watch_parser import parse_message, read_message

PARSER_VERSION = "watch_parser_v1"
DEFAULT_GROUP_NAME = "Default Group"
DEFAULT_DEALER_WHATSAPP_ID = "default-dealer"
DEFAULT_DEALER_NAME = "Default Dealer"


def get_default_group_id() -> str:
    """Return the default group, creating it if needed."""
    client = get_client()
    existing = (
        client.table("groups")
        .select("id")
        .eq("name", DEFAULT_GROUP_NAME)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    created = client.table("groups").insert({"name": DEFAULT_GROUP_NAME}).execute()
    if not created.data:
        raise RuntimeError("Failed to create default group.")
    return created.data[0]["id"]


def get_default_dealer_id() -> str:
    """Return the default dealer, creating it if needed."""
    client = get_client()
    existing = (
        client.table("dealers")
        .select("id")
        .eq("whatsapp_id", DEFAULT_DEALER_WHATSAPP_ID)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    created = (
        client.table("dealers")
        .insert(
            {
                "whatsapp_id": DEFAULT_DEALER_WHATSAPP_ID,
                "display_name": DEFAULT_DEALER_NAME,
                "is_active": True,
            }
        )
        .execute()
    )
    if not created.data:
        raise RuntimeError("Failed to create default dealer.")
    return created.data[0]["id"]


def ingest_message(text: str) -> int:
    """Parse a message and save it with all offers to Supabase."""
    parsed = parse_message(text)
    group_id = get_default_group_id()
    dealer_id = get_default_dealer_id()
    now = datetime.now(timezone.utc)

    message = insert_message(
        group_id=group_id,
        dealer_id=dealer_id,
        raw_text=text,
        message_type=parsed["message_type"],
        parsed_at=now,
        parser_version=PARSER_VERSION,
        parse_status=_parse_status(parsed),
    )

    saved = 0
    for line_index, watch in enumerate(parsed["watches"]):
        watch_row = find_or_create_watch(
            brand=watch.get("brand"),
            reference=watch.get("reference"),
            model=watch.get("model"),
            dial=watch.get("dial"),
            bracelet=watch.get("bracelet"),
        )
        _, created = insert_offer(
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
        if created:
            saved += 1

    return saved


def _parse_status(parsed: dict) -> str:
    if parsed["message_type"] == "unknown":
        return "partial"
    if parsed["watches"]:
        return "success"
    return "partial"


def main() -> None:
    try:
        text = read_message()
        saved = ingest_message(text)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Saved {saved} offer(s).")


if __name__ == "__main__":
    main()
