"""WhatsApp message collector for MRV4ULT AI.

Receives incoming WhatsApp messages and forwards them to the ingest pipeline.
No parsing or database logic lives here.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from ingest import IngestSummary, ingest_message

logger = logging.getLogger("mrv4ult.whatsapp.ingest")


@dataclass(frozen=True)
class WhatsAppMessage:
    """Normalized WhatsApp message payload from a future collector integration."""

    group_name: str
    dealer_whatsapp: str
    message_text: str
    received_at: datetime
    dealer_alias: str | None = None
    whatsapp_message_id: str | None = None


def collect_message(message: WhatsAppMessage) -> IngestSummary:
    """Forward a collected WhatsApp message into the existing ingest pipeline."""
    group_name = message.group_name.strip()
    dealer_whatsapp = message.dealer_whatsapp.strip()
    message_text = message.message_text.strip()

    if not group_name:
        raise ValueError("Group name is required.")
    if not dealer_whatsapp:
        raise ValueError("Dealer WhatsApp number is required.")
    if not message_text:
        raise ValueError("Message text is required.")

    received_at = message.received_at
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)

    dealer_alias = message.dealer_alias.strip() if message.dealer_alias else None
    if dealer_alias == "":
        dealer_alias = None

    logger.info(
        "[WhatsApp ingest] collect_message: whatsapp_message_id=%s group=%s dealer=%s alias=%s chars=%s",
        message.whatsapp_message_id or "N/A",
        group_name,
        dealer_whatsapp,
        dealer_alias or "N/A",
        len(message_text),
    )

    summary = ingest_message(
        message_text,
        group_name=group_name,
        dealer_whatsapp=dealer_whatsapp,
        dealer_alias=dealer_alias,
        received_at=received_at,
        whatsapp_message_id=message.whatsapp_message_id,
        source="whatsapp_webhook",
    )
    logger.info(
        "[WhatsApp ingest] collect_message complete: whatsapp_message_id=%s status=%s message_id=%s import_log_id=%s already_processed=%s",
        message.whatsapp_message_id or "N/A",
        summary.get("status"),
        summary.get("message_id"),
        summary.get("import_log_id"),
        summary.get("already_processed", False),
    )
    return summary


def simulate_incoming_message() -> WhatsAppMessage:
    """Return a simulated WhatsApp message for local development."""
    return WhatsAppMessage(
        group_name="HK Dealers",
        dealer_whatsapp="+85291234567",
        dealer_alias="Sample Dealer",
        message_text=(
            "ROLEX\n"
            "126200 green jub n6/26 74000usd\n"
            "126231g champ jub used 2024y 147500usd"
        ),
        received_at=datetime.now(timezone.utc),
    )


def main() -> None:
    try:
        incoming = simulate_incoming_message()
        summary = collect_message(incoming)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("WhatsApp message collected and imported.")
    print(f"Group: {summary['group']}")
    print(f"Dealer WhatsApp: {summary['dealer_whatsapp']}")
    if summary.get("dealer_alias"):
        print(f"Dealer alias: {summary['dealer_alias']}")
    print(f"Watches parsed: {summary['watches_parsed']}")
    print(f"New offers: {summary['new_offers']}")
    print(f"Duplicate offers: {summary['duplicate_offers']}")


if __name__ == "__main__":
    main()
