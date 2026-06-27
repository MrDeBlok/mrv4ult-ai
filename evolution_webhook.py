"""Evolution API webhook handling for MRV4ULT AI."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from whatsapp_collector import WhatsAppMessage, collect_message

logger = logging.getLogger(__name__)

_group_name_cache: dict[str, str] = {}


class WebhookProcessingError(Exception):
    """Raised when a webhook payload cannot be imported."""


def log_webhook_payload(payload: dict[str, Any]) -> None:
    """Print the full webhook payload for debugging."""
    print(
        "[Evolution webhook] "
        + json.dumps(payload, ensure_ascii=False, default=str),
        flush=True,
    )


def handle_evolution_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process one Evolution API webhook payload."""
    log_webhook_payload(payload)
    event = normalize_event_name(payload.get("event"))

    if event in {"groups.upsert", "groups.update"}:
        update_group_name_cache(payload.get("data"))
        return {"status": "ignored", "reason": "group metadata event"}

    if event in {"chats.upsert", "chats.update"}:
        update_group_name_cache(payload.get("data"))
        return {"status": "ignored", "reason": "chat metadata event"}

    if event not in {"messages.upsert"}:
        return {"status": "ignored", "reason": f"unsupported event: {event or 'missing'}"}

    data = unwrap_message_data(payload.get("data"))
    if not isinstance(data, dict):
        return {"status": "ignored", "reason": "missing message data"}

    if is_from_me(data):
        return {"status": "ignored", "reason": "outgoing message"}

    message_text = extract_message_text(data)
    if not message_text:
        return {"status": "ignored", "reason": "no text content"}

    whatsapp_message = map_payload_to_whatsapp_message(payload, data, message_text)
    summary = collect_message(whatsapp_message)

    return {
        "status": "imported",
        "group": summary.get("group"),
        "dealer_whatsapp": summary.get("dealer_whatsapp"),
        "watches_parsed": summary.get("watches_parsed"),
        "new_offers": summary.get("new_offers"),
        "duplicate_offers": summary.get("duplicate_offers"),
        "import_log_id": summary.get("import_log_id"),
    }


def unwrap_message_data(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        messages = data.get("messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            return messages[0]
        return data

    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]

    return None


def normalize_event_name(event: Any) -> str:
    if not isinstance(event, str):
        return ""
    return event.strip().lower().replace("_", ".")


def is_from_me(data: dict[str, Any]) -> bool:
    key = data.get("key") or {}
    return bool(key.get("fromMe"))


def extract_message_text(data: dict[str, Any]) -> str | None:
    message = data.get("message")
    if not isinstance(message, dict):
        return None

    for value in (
        message.get("conversation"),
        (message.get("extendedTextMessage") or {}).get("text"),
        (message.get("imageMessage") or {}).get("caption"),
        (message.get("documentMessage") or {}).get("caption"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def map_payload_to_whatsapp_message(
    payload: dict[str, Any],
    data: dict[str, Any],
    message_text: str,
) -> WhatsAppMessage:
    key = data.get("key") or {}
    remote_jid = str(key.get("remoteJid") or "")
    if not is_group_jid(remote_jid):
        raise WebhookProcessingError("Only WhatsApp group messages are imported.")

    group_name = resolve_group_name(remote_jid, data)
    dealer_whatsapp = resolve_dealer_whatsapp(key, data, payload)
    if not dealer_whatsapp:
        raise WebhookProcessingError("Could not determine dealer WhatsApp number.")

    dealer_alias = extract_dealer_alias(data)
    received_at = extract_received_at(data, payload)

    return WhatsAppMessage(
        group_name=group_name,
        dealer_whatsapp=dealer_whatsapp,
        dealer_alias=dealer_alias,
        message_text=message_text,
        received_at=received_at,
    )


def resolve_group_name(group_jid: str, data: dict[str, Any]) -> str:
    for candidate in (
        _group_name_cache.get(group_jid),
        data.get("subject"),
        data.get("groupSubject"),
        data.get("name"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    raise WebhookProcessingError(f"Group name not found for {group_jid}.")


def resolve_dealer_whatsapp(
    key: dict[str, Any],
    data: dict[str, Any],
    payload: dict[str, Any],
) -> str | None:
    candidates = [
        key.get("participantAlt"),
        key.get("participant"),
        data.get("participant"),
        data.get("participantAlt"),
        payload.get("sender"),
    ]

    for candidate in candidates:
        phone = jid_to_phone(str(candidate)) if candidate else None
        if phone:
            return phone

    return None


def extract_dealer_alias(data: dict[str, Any]) -> str | None:
    push_name = data.get("pushName")
    if isinstance(push_name, str) and push_name.strip():
        return push_name.strip()
    return None


def extract_received_at(data: dict[str, Any], payload: dict[str, Any]) -> datetime:
    timestamp = data.get("messageTimestamp")
    if timestamp is not None:
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass

    date_time = payload.get("date_time")
    if isinstance(date_time, str) and date_time.strip():
        try:
            parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return datetime.now(timezone.utc)


def update_group_name_cache(data: Any) -> None:
    records = data if isinstance(data, list) else [data]
    for record in records:
        if not isinstance(record, dict):
            continue

        group_jid = str(
            record.get("id")
            or record.get("remoteJid")
            or record.get("jid")
            or ""
        )
        group_name = record.get("subject") or record.get("name")
        if group_jid and isinstance(group_name, str) and group_name.strip():
            _group_name_cache[group_jid] = group_name.strip()


def is_group_jid(jid: str) -> bool:
    return jid.endswith("@g.us")


def jid_to_phone(jid: str) -> str | None:
    cleaned = jid.strip()
    if not cleaned or "@g.us" in cleaned:
        return None

    if "@lid" in cleaned:
        return None

    user_part = cleaned.split("@", 1)[0]
    digits = re.sub(r"\D", "", user_part)
    if not digits:
        return None

    if cleaned.startswith("+"):
        return f"+{digits}"
    return digits
