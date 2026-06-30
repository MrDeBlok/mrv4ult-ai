"""Feature flags and startup timing for WhatsApp webhook ingest."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from timezone_utils import ensure_utc_datetime

load_dotenv()

logger = logging.getLogger("mrv4ult.whatsapp.ingest")

ENABLE_WHATSAPP_WEBHOOK_INGEST_ENV = "ENABLE_WHATSAPP_WEBHOOK_INGEST"
ENABLE_BACKLOG_INGEST_ENV = "ENABLE_BACKLOG_INGEST"

_app_started_at: datetime | None = None


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_whatsapp_webhook_ingest_enabled() -> bool:
    """Return True when Evolution webhooks should trigger ingest."""
    return _parse_bool_env(ENABLE_WHATSAPP_WEBHOOK_INGEST_ENV, default=True)


def is_backlog_ingest_enabled() -> bool:
    """Return True when pre-startup WhatsApp messages may be ingested."""
    return _parse_bool_env(ENABLE_BACKLOG_INGEST_ENV, default=False)


def mark_app_started(now: datetime | None = None) -> datetime:
    """Record application startup time for backlog filtering."""
    global _app_started_at
    _app_started_at = ensure_utc_datetime(now or datetime.now(timezone.utc))
    return _app_started_at


def get_app_started_at() -> datetime | None:
    return _app_started_at


def set_app_started_at_for_tests(value: datetime | None) -> None:
    """Pin startup time in tests."""
    global _app_started_at
    _app_started_at = ensure_utc_datetime(value) if value is not None else None


def is_backlog_message(received_at: datetime) -> bool:
    """Return True when the message timestamp predates app startup."""
    started_at = get_app_started_at()
    if started_at is None:
        return False
    return ensure_utc_datetime(received_at) < ensure_utc_datetime(started_at)


def should_skip_backlog_message(received_at: datetime) -> bool:
    """Skip historical backlog unless explicitly enabled."""
    return is_backlog_message(received_at) and not is_backlog_ingest_enabled()


def log_startup_ingest_config() -> None:
    """Log ingest feature flags at application startup."""
    started_at = get_app_started_at()
    logger.info(
        "WhatsApp ingest startup: app_started_at=%s backlog_ingest=%s webhook_ingest=%s",
        started_at.isoformat() if started_at else "unknown",
        is_backlog_ingest_enabled(),
        is_whatsapp_webhook_ingest_enabled(),
    )
