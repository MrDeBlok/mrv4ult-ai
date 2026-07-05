"""Evolution API webhook handling for MRV4ULT AI."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from timezone_utils import ensure_utc_datetime
from typing import Any

from whatsapp_collector import WhatsAppMessage, collect_message
from evolution_client import (
    EvolutionAPIError,
    extract_group_subject,
    fetch_group_info,
    get_default_instance_name,
)
from whatsapp_ingest_config import (
    get_app_started_at,
    is_whatsapp_webhook_ingest_enabled,
    should_skip_backlog_message,
)

logger = logging.getLogger("mrv4ult.whatsapp.ingest")

PRIVATE_OFFERS_GROUP_NAME = "Private Offers"
WEBHOOK_TRACE_PREFIX = "[WhatsApp webhook trace]"

_group_name_cache: dict[str, str] = {}


def reset_group_name_cache() -> None:
    """Clear in-memory group name cache (for tests)."""
    _group_name_cache.clear()


def _payload_group_name(data: dict[str, Any]) -> str | None:
    """Extract a group display name from the current webhook message payload."""
    for field in ("subject", "groupSubject", "name"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_group_jid_fallback_name(value: str, group_jid: str) -> bool:
    cleaned = value.strip()
    return cleaned == group_jid or cleaned.endswith("@g.us")


def _cached_group_name(group_jid: str) -> str | None:
    """Return a cached display name, ignoring jid fallbacks stored from earlier lookups."""
    cached = _group_name_cache.get(group_jid)
    if not isinstance(cached, str) or not cached.strip():
        return None
    cleaned = cached.strip()
    if _is_group_jid_fallback_name(cleaned, group_jid):
        return None
    return cleaned


def _resolve_group_name_from_sources(
    group_jid: str,
    data: dict[str, Any],
) -> str | None:
    """Resolve a group display name from payload fields or a valid cache entry."""
    payload_name = _payload_group_name(data)
    if payload_name:
        _group_name_cache[group_jid] = payload_name
        return payload_name

    cached_name = _cached_group_name(group_jid)
    if cached_name:
        return cached_name
    return None


class WebhookProcessingError(Exception):
    """Raised when a webhook payload cannot be imported."""


def log_webhook_payload(payload: dict[str, Any]) -> None:
    """Log the webhook payload for debugging."""
    logger.debug(
        "[WhatsApp ingest] Raw webhook payload: %s",
        json.dumps(payload, ensure_ascii=False, default=str),
    )


def _text_preview(text: str | None, *, limit: int = 120) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 3]}..."


def build_webhook_trace(payload: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build admin/debug trace fields for one Evolution webhook."""
    message_data = data if isinstance(data, dict) else unwrap_message_data(payload.get("data"))
    key = (message_data or {}).get("key") or {} if isinstance(message_data, dict) else {}
    remote_jid = str(key.get("remoteJid") or "")
    remote_jid_alt = str(key.get("remoteJidAlt") or "")
    participant = str(key.get("participant") or "")
    participant_alt = str(key.get("participantAlt") or key.get("participantPn") or key.get("senderPn") or "")
    chat_id = remote_jid or remote_jid_alt or "—"
    group_name = None
    if isinstance(message_data, dict):
        group_name = _resolve_group_name_from_sources(remote_jid, message_data)
    message_text = extract_message_text(message_data) if isinstance(message_data, dict) else None
    sender_id = resolve_dealer_whatsapp(key, message_data or {}, payload) if isinstance(message_data, dict) else None
    if sender_id is None and isinstance(message_data, dict) and is_private_chat(key):
        sender_id = resolve_private_dealer_whatsapp(key, message_data or {}, payload)

    return {
        "event_type": normalize_event_name(payload.get("event")) or "—",
        "instance_name": str(payload.get("instance") or "—"),
        "chat_id": chat_id,
        "remote_jid": remote_jid or "—",
        "remote_jid_alt": remote_jid_alt or "—",
        "participant": participant or "—",
        "participant_alt": participant_alt or "—",
        "group_name": group_name or ("Private Offers" if is_private_chat(key) else "—"),
        "sender_id": sender_id or "—",
        "sender_name": extract_dealer_alias(message_data or {}) or "—",
        "message_id": extract_whatsapp_message_id(message_data or {}) or "—",
        "message_text_preview": _text_preview(message_text) or "—",
        "message_type": extract_message_type(message_data or {}) or "—",
        "from_me": is_from_me(message_data or {}) if isinstance(message_data, dict) else False,
        "is_group": is_group_jid(remote_jid),
        "is_private": is_private_chat(key),
        "filters": {
            "ignored_private_chats": False,
            "group_whitelist": False,
            "group_blacklist": False,
            "business_group_filter": False,
            "from_me_filter": is_from_me(message_data or {}) if isinstance(message_data, dict) else False,
            "message_type_filter": False,
            "empty_text_filter": not bool(message_text),
            "duplicate_message_filter": False,
            "contact_visibility_filter": False,
            "dealer_classification_filter": False,
            "group_classification_filter": False,
            "unsupported_event_filter": normalize_event_name(payload.get("event")) not in {"messages.upsert"},
        },
    }


WEBHOOK_DECISION_PREFIX = "[WhatsApp webhook decision]"


def log_webhook_decision(trace: dict[str, Any], result: dict[str, Any]) -> None:
    """Log one canonical accepted/skipped decision line for every webhook."""
    status = str(result.get("status") or "unknown")
    reason = result.get("reason") or result.get("status_reason")
    if status in {"imported", "success"}:
        decision = "accepted"
        skip_reason = "—"
    elif status in {"already_imported"}:
        decision = "skipped"
        skip_reason = reason or "duplicate whatsapp_message_id"
    elif status in {"ignored", "skipped_backlog", "error"}:
        decision = "skipped"
        skip_reason = reason or status
    else:
        decision = "skipped" if result.get("already_processed") else "accepted"
        skip_reason = reason or ("duplicate ingest" if result.get("already_processed") else "—")

    ingest_status = result.get("ingest_status")
    duplicate_reason = skip_reason if result.get("already_processed") or status == "already_imported" else "—"

    logger.info(
        "%s decision=%s skip_reason=%s event_type=%s message_id=%s remote_jid=%s "
        "participant=%s participant_alt=%s text_preview=%s webhook_status=%s "
        "ingest_status=%s import_log_id=%s already_processed=%s duplicate_reason=%s",
        WEBHOOK_DECISION_PREFIX,
        decision,
        skip_reason,
        trace.get("event_type"),
        trace.get("message_id") or result.get("whatsapp_message_id") or "—",
        trace.get("remote_jid"),
        trace.get("participant"),
        trace.get("participant_alt"),
        trace.get("message_text_preview"),
        status,
        ingest_status or "—",
        result.get("import_log_id") or "—",
        bool(result.get("already_processed", False)),
        duplicate_reason,
    )


def log_webhook_trace(
    trace: dict[str, Any],
    *,
    decision: str,
    skip_reason: str | None = None,
) -> None:
    """Log one webhook decision with full trace context."""
    filters = trace.get("filters") or {}
    logger.info(
        "%s decision=%s skip_reason=%s event_type=%s instance=%s chat_id=%s "
        "remote_jid=%s participant=%s group_name=%s sender_id=%s sender_name=%s message_id=%s text_preview=%s "
        "message_type=%s from_me=%s is_group=%s is_private=%s "
        "filters={ignored_private:%s group_whitelist:%s group_blacklist:%s business_group:%s "
        "from_me:%s message_type:%s empty_text:%s duplicate:%s contact_visibility:%s "
        "dealer_classification:%s group_classification:%s unsupported_event:%s}",
        WEBHOOK_TRACE_PREFIX,
        decision,
        skip_reason or "—",
        trace.get("event_type"),
        trace.get("instance_name"),
        trace.get("chat_id"),
        trace.get("remote_jid"),
        trace.get("participant"),
        trace.get("group_name"),
        trace.get("sender_id"),
        trace.get("sender_name"),
        trace.get("message_id"),
        trace.get("message_text_preview"),
        trace.get("message_type"),
        trace.get("from_me"),
        trace.get("is_group"),
        trace.get("is_private"),
        filters.get("ignored_private_chats"),
        filters.get("group_whitelist"),
        filters.get("group_blacklist"),
        filters.get("business_group_filter"),
        filters.get("from_me_filter"),
        filters.get("message_type_filter"),
        filters.get("empty_text_filter"),
        filters.get("duplicate_message_filter"),
        filters.get("contact_visibility_filter"),
        filters.get("dealer_classification_filter"),
        filters.get("group_classification_filter"),
        filters.get("unsupported_event_filter"),
    )


def _return_webhook_result(trace: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Attach trace and emit the canonical decision log before returning."""
    payload = {**result, "trace": trace}
    log_webhook_decision(trace, payload)
    return payload


def handle_evolution_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process one Evolution API webhook payload."""
    log_webhook_payload(payload)
    trace = build_webhook_trace(payload)
    log_webhook_trace(trace, decision="received")
    event = trace["event_type"] if trace["event_type"] != "—" else ""
    instance_name = trace["instance_name"] if trace["instance_name"] != "—" else ""

    if event in {"groups.upsert", "groups.update", "group.update"}:
        update_group_name_cache(payload.get("data"))
        reason = "group metadata event"
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    if event in {"chats.upsert", "chats.update"}:
        update_group_name_cache(payload.get("data"))
        reason = "chat metadata event"
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    if event not in {"messages.upsert"}:
        reason = f"unsupported event: {event or 'missing'}"
        trace["filters"]["unsupported_event_filter"] = True
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    if not is_whatsapp_webhook_ingest_enabled():
        reason = "webhook ingest disabled"
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    data = unwrap_message_data(payload.get("data"))
    if not isinstance(data, dict):
        reason = "missing message data"
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    trace = build_webhook_trace(payload, data)

    if is_from_me(data):
        reason = "outgoing message"
        trace["filters"]["from_me_filter"] = True
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    message_text = extract_message_text(data)
    if not message_text:
        reason = "no text content"
        trace["filters"]["empty_text_filter"] = True
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    whatsapp_message_id = extract_whatsapp_message_id(data)
    if whatsapp_message_id:
        from database import find_message_by_whatsapp_id

        if find_message_by_whatsapp_id(whatsapp_message_id):
            reason = "duplicate whatsapp_message_id"
            trace["filters"]["duplicate_message_filter"] = True
            log_webhook_trace(trace, decision="skipped", skip_reason=reason)
            return _return_webhook_result(
                trace,
                {
                    "status": "already_imported",
                    "already_processed": True,
                    "whatsapp_message_id": whatsapp_message_id,
                    "reason": reason,
                },
            )

    received_at = extract_received_at(data, payload)
    if should_skip_backlog_message(received_at):
        reason = "backlog ingest disabled"
        started_at = get_app_started_at()
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        logger.info(
            "Skipped backlog message: whatsapp_message_id=%s received_at=%s app_started_at=%s",
            whatsapp_message_id or "missing",
            received_at.isoformat(),
            started_at.isoformat() if started_at else "unknown",
        )
        return _return_webhook_result(
            trace,
            {
                "status": "skipped_backlog",
                "reason": reason,
                "whatsapp_message_id": whatsapp_message_id,
                "received_at": received_at.isoformat(),
            },
        )

    key = data.get("key") or {}
    remote_jid = str(key.get("remoteJid") or "")
    logger.info(
        "[WhatsApp ingest] New message detected: whatsapp_message_id=%s remote_jid=%s text_len=%s push_name=%s",
        whatsapp_message_id or "missing",
        remote_jid or "unknown",
        len(message_text),
        extract_dealer_alias(data) or "N/A",
    )

    try:
        whatsapp_message = map_payload_to_whatsapp_message(
            payload,
            data,
            message_text,
            whatsapp_message_id=whatsapp_message_id,
        )
    except WebhookProcessingError as exc:
        reason = str(exc)
        log_webhook_trace(trace, decision="skipped", skip_reason=reason)
        return _return_webhook_result(trace, {"status": "ignored", "reason": reason})

    trace = build_webhook_trace(payload, data)
    trace["sender_id"] = whatsapp_message.dealer_whatsapp
    trace["group_name"] = whatsapp_message.group_name
    log_webhook_trace(trace, decision="accepted")
    logger.info(
        "[WhatsApp ingest] Sending to ingest: whatsapp_message_id=%s group=%s dealer=%s alias=%s",
        whatsapp_message.whatsapp_message_id or "missing",
        whatsapp_message.group_name,
        whatsapp_message.dealer_whatsapp,
        whatsapp_message.dealer_alias or "N/A",
    )
    summary = collect_message(whatsapp_message)

    logger.info(
        "[WhatsApp ingest] Ingest result: whatsapp_message_id=%s status=%s message_id=%s watches_parsed=%s new_offers=%s "
        "duplicate_offers=%s import_log_id=%s already_processed=%s saved=%s",
        whatsapp_message.whatsapp_message_id or "missing",
        summary.get("status"),
        summary.get("message_id"),
        summary.get("watches_parsed"),
        summary.get("new_offers"),
        summary.get("duplicate_offers"),
        summary.get("import_log_id"),
        summary.get("already_processed", False),
        summary.get("saved", summary.get("import_log_id") is not None),
    )

    if whatsapp_message.group_name == PRIVATE_OFFERS_GROUP_NAME:
        logger.info(
            "[WhatsApp ingest] Private offer imported: dealer=%s watches_parsed=%s new_offers=%s",
            summary.get("dealer_whatsapp"),
            summary.get("watches_parsed"),
            summary.get("new_offers"),
        )

    ingest_status = summary.get("status")
    already_processed = bool(summary.get("already_processed", False))
    webhook_status = "already_imported" if ingest_status == "already_imported" else "imported"
    duplicate_reason = None
    if already_processed or webhook_status == "already_imported":
        duplicate_reason = summary.get("status_reason") or "duplicate whatsapp_message_id"

    return _return_webhook_result(
        trace,
        {
            "status": webhook_status,
            "ingest_status": ingest_status,
            "reason": duplicate_reason,
            "whatsapp_message_id": whatsapp_message.whatsapp_message_id,
            "message_id": summary.get("message_id"),
            "already_processed": already_processed,
            "group": summary.get("group"),
            "dealer_whatsapp": summary.get("dealer_whatsapp"),
            "watches_parsed": summary.get("watches_parsed"),
            "new_offers": summary.get("new_offers"),
            "duplicate_offers": summary.get("duplicate_offers"),
            "import_log_id": summary.get("import_log_id"),
        },
    )


def extract_whatsapp_message_id(data: dict[str, Any]) -> str | None:
    """Return Evolution/WhatsApp message id used for ingest deduplication."""
    key = data.get("key") or {}
    message_id = key.get("id")
    if isinstance(message_id, str) and message_id.strip():
        return message_id.strip()
    return None


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


def extract_message_type(data: dict[str, Any]) -> str | None:
    message_type = data.get("messageType")
    if isinstance(message_type, str) and message_type.strip():
        return message_type.strip()
    message = data.get("message")
    if isinstance(message, dict):
        for key in message:
            if key.endswith("Message") or key == "conversation":
                return key
    return None


def extract_message_text(data: dict[str, Any]) -> str | None:
    message = data.get("message")
    if not isinstance(message, dict):
        return None

    for wrapper_key in (
        "ephemeralMessage",
        "viewOnceMessage",
        "documentWithCaptionMessage",
        "editedMessage",
    ):
        wrapper = message.get(wrapper_key)
        if isinstance(wrapper, dict) and isinstance(wrapper.get("message"), dict):
            nested = extract_message_text({"message": wrapper["message"]})
            if nested:
                return nested

    for value in (
        message.get("conversation"),
        (message.get("extendedTextMessage") or {}).get("text"),
        (message.get("imageMessage") or {}).get("caption"),
        (message.get("videoMessage") or {}).get("caption"),
        (message.get("documentMessage") or {}).get("caption"),
        (message.get("buttonsResponseMessage") or {}).get("selectedDisplayText"),
        (message.get("listResponseMessage") or {}).get("title"),
        (message.get("templateButtonReplyMessage") or {}).get("selectedDisplayText"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def map_payload_to_whatsapp_message(
    payload: dict[str, Any],
    data: dict[str, Any],
    message_text: str,
    *,
    whatsapp_message_id: str | None = None,
) -> WhatsAppMessage:
    key = data.get("key") or {}
    remote_jid = str(key.get("remoteJid") or "")

    if is_private_chat(key):
        logger.info(
            "[Evolution webhook private] Private message detected: %s",
            remote_jid or key.get("remoteJidAlt") or payload.get("sender"),
        )
        dealer_whatsapp = resolve_private_dealer_whatsapp(key, data, payload)
        if not dealer_whatsapp:
            raise WebhookProcessingError(
                "Could not determine dealer WhatsApp number for private message."
            )

        dealer_alias = extract_dealer_alias(data)
        received_at = extract_received_at(data, payload)
        whatsapp_message = WhatsAppMessage(
            group_name=PRIVATE_OFFERS_GROUP_NAME,
            dealer_whatsapp=dealer_whatsapp,
            dealer_alias=dealer_alias,
            message_text=message_text,
            received_at=received_at,
            whatsapp_message_id=whatsapp_message_id,
        )
        logger.info(
            "[Evolution webhook private] Private offer mapped: dealer=%s alias=%s",
            dealer_whatsapp,
            dealer_alias or "N/A",
        )
        return whatsapp_message

    if not is_group_jid(remote_jid):
        raise WebhookProcessingError("Unsupported WhatsApp chat type.")

    group_name = resolve_group_name(
        remote_jid,
        data,
        instance_name=str(payload.get("instance") or ""),
    )
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
        whatsapp_message_id=whatsapp_message_id,
    )


def resolve_group_name(
    group_jid: str,
    data: dict[str, Any],
    *,
    instance_name: str = "",
) -> str:
    resolved = _resolve_group_name_from_sources(group_jid, data)
    if resolved:
        return resolved

    logger.info("[Evolution webhook group] Group name missing for %s", group_jid)

    instance = instance_name.strip() or get_default_instance_name()
    logger.info(
        "[Evolution webhook group] Fetching group metadata for %s (instance=%s)",
        group_jid,
        instance,
    )

    try:
        response = fetch_group_info(instance, group_jid)
        subject = extract_group_subject(response)
        if subject:
            _group_name_cache[group_jid] = subject
            logger.info(
                "[Evolution webhook group] Group resolved: %s -> %s",
                group_jid,
                subject,
            )
            return subject
        logger.warning(
            "[Evolution webhook group] Group metadata fetched but no subject for %s",
            group_jid,
        )
    except EvolutionAPIError as exc:
        logger.warning(
            "[Evolution webhook group] Could not fetch metadata for %s: %s",
            group_jid,
            exc,
        )

    logger.info("[Evolution webhook group] Fallback to remoteJid: %s", group_jid)
    _group_name_cache[group_jid] = group_jid
    return group_jid


def _participant_candidates(
    key: dict[str, Any],
    data: dict[str, Any],
) -> list[Any]:
    context_info = data.get("contextInfo") if isinstance(data.get("contextInfo"), dict) else {}
    return [
        key.get("participantAlt"),
        key.get("participantPn"),
        key.get("senderPn"),
        key.get("participant"),
        key.get("remoteJidAlt"),
        data.get("participantAlt"),
        data.get("participantPn"),
        data.get("senderPn"),
        data.get("participant"),
        data.get("sender"),
        context_info.get("participant"),
    ]


def resolve_dealer_whatsapp(
    key: dict[str, Any],
    data: dict[str, Any],
    payload: dict[str, Any],
) -> str | None:
    del payload  # Instance-level sender must not be used as message participant.
    for candidate in _participant_candidates(key, data):
        phone = jid_to_contact_id(str(candidate)) if candidate else None
        if phone:
            return phone

    logger.warning(
        "[WhatsApp ingest] Could not resolve dealer phone from key=%s data_participant=%s",
        {field: key.get(field) for field in ("participant", "participantAlt", "participantPn", "senderPn", "remoteJidAlt")},
        data.get("participant"),
    )
    return None


def resolve_private_dealer_whatsapp(
    key: dict[str, Any],
    data: dict[str, Any],
    payload: dict[str, Any],
) -> str | None:
    del payload
    candidates = [
        key.get("remoteJidAlt"),
        key.get("senderPn"),
        key.get("remoteJid"),
        data.get("senderPn"),
        data.get("sender"),
    ]

    for candidate in candidates:
        phone = jid_to_contact_id(str(candidate)) if candidate else None
        if phone:
            return phone

    logger.warning(
        "[WhatsApp ingest] Could not resolve private chat dealer phone from key=%s",
        {field: key.get(field) for field in ("remoteJid", "remoteJidAlt", "senderPn")},
    )
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
            seconds = int(timestamp)
            if seconds > 10_000_000_000:
                seconds //= 1000
            return ensure_utc_datetime(
                datetime.fromtimestamp(seconds, tz=timezone.utc)
            )
        except (TypeError, ValueError, OSError):
            pass

    date_time = payload.get("date_time")
    if isinstance(date_time, str) and date_time.strip():
        try:
            parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
            return ensure_utc_datetime(parsed)
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


def is_private_jid(jid: str) -> bool:
    return jid.endswith("@s.whatsapp.net")


def is_private_chat(key: dict[str, Any]) -> bool:
    remote_jid = str(key.get("remoteJid") or "")
    remote_jid_alt = str(key.get("remoteJidAlt") or "")
    if is_private_jid(remote_jid) or is_private_jid(remote_jid_alt):
        return True
    if remote_jid.endswith("@lid") and not is_group_jid(remote_jid):
        return True
    return False


def jid_to_contact_id(value: str) -> str | None:
    """Return a dealer contact id from a WhatsApp JID, LID, or plain phone."""
    cleaned = value.strip()
    if not cleaned or "@g.us" in cleaned:
        return None

    if "@" not in cleaned:
        digits = re.sub(r"\D", "", cleaned)
        if not digits:
            return None
        return cleaned if cleaned.startswith("+") else digits

    user_part, suffix = cleaned.split("@", 1)
    digits = re.sub(r"\D", "", user_part)
    if not digits:
        return None

    if suffix == "s.whatsapp.net":
        return cleaned if cleaned.startswith("+") else digits

    if suffix == "lid":
        return f"lid:{digits}"

    return None


def jid_to_phone(jid: str) -> str | None:
    """Backward-compatible alias for contact id extraction."""
    contact_id = jid_to_contact_id(jid)
    if contact_id and contact_id.startswith("lid:"):
        return None
    return contact_id
