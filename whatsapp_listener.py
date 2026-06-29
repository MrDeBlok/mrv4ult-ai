"""WhatsApp ingestion listener startup checks for MRV4ULT AI.

Evolution API delivers messages via HTTP webhooks — there is no separate polling
loop. This module runs on app startup to verify the WhatsApp session and webhook
configuration, and logs clear status for live debugging.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from evolution_client import (
    EvolutionAPIError,
    ensure_webhook_registered,
    find_webhook,
    get_default_instance_name,
    get_instance_status,
)

logger = logging.getLogger("mrv4ult.whatsapp.listener")

_started = False


def start_whatsapp_listener() -> dict[str, Any]:
    """Verify WhatsApp session + webhook on app startup."""
    global _started

    instance_name = get_default_instance_name()
    summary: dict[str, Any] = {
        "started": True,
        "instance": instance_name,
        "connected": False,
        "webhook_enabled": None,
        "webhook_url": None,
    }

    logger.info("[WhatsApp listener] Starting ingestion listener for instance=%s", instance_name)

    try:
        status = get_instance_status(instance_name)
        summary["connected"] = bool(status.get("connected"))
        if summary["connected"]:
            logger.info(
                "[WhatsApp listener] Connected WhatsApp session: instance=%s phone=%s state=%s",
                instance_name,
                status.get("phone_number") or "unknown",
                status.get("state"),
            )
        else:
            logger.warning(
                "[WhatsApp listener] WhatsApp session not connected: instance=%s state=%s "
                "(scan QR at /whatsapp before expecting live messages)",
                instance_name,
                status.get("state"),
            )
    except EvolutionAPIError as exc:
        logger.warning(
            "[WhatsApp listener] Could not verify WhatsApp session (Evolution API unreachable?): %s",
            exc,
        )

    webhook_url = os.getenv("MRV4ULT_WEBHOOK_URL", "").strip()
    if webhook_url:
        try:
            registration = ensure_webhook_registered(instance_name, webhook_url)
            summary["webhook_enabled"] = registration.get("enabled")
            summary["webhook_url"] = registration.get("url")
            action = "updated" if registration.get("updated") else "already configured"
            logger.info(
                "[WhatsApp listener] Webhook %s: enabled=%s url=%s",
                action,
                registration.get("enabled"),
                registration.get("url"),
            )
        except EvolutionAPIError as exc:
            logger.warning("[WhatsApp listener] Webhook auto-registration failed: %s", exc)
    else:
        try:
            existing = find_webhook(instance_name)
            summary["webhook_enabled"] = existing.get("enabled")
            summary["webhook_url"] = existing.get("url")
            if existing.get("enabled") and existing.get("url"):
                logger.info(
                    "[WhatsApp listener] Existing webhook: enabled=%s url=%s",
                    existing.get("enabled"),
                    existing.get("url"),
                )
            else:
                logger.warning(
                    "[WhatsApp listener] No active webhook found. Set MRV4ULT_WEBHOOK_URL in .env "
                    "(e.g. http://host.docker.internal:8000/webhook/evolution) or register manually — "
                    "see docs/evolution_webhook_setup.md"
                )
        except EvolutionAPIError as exc:
            logger.warning("[WhatsApp listener] Could not read webhook configuration: %s", exc)

    _started = True
    logger.info(
        "[WhatsApp listener] Listener ready — Evolution API should POST to /webhook/evolution"
    )
    return summary


def stop_whatsapp_listener() -> None:
    """Log listener shutdown."""
    global _started
    if _started:
        logger.info("[WhatsApp listener] Shutting down")
    _started = False


def is_listener_started() -> bool:
    return _started
