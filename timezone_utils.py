"""UTC storage and Europe/Amsterdam display helpers for timestamps."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DISPLAY_TIMEZONE = ZoneInfo("Europe/Amsterdam")
UTC = timezone.utc


def parse_utc_timestamp(value: str | datetime | None) -> datetime | None:
    """Parse an ISO timestamp and normalize it to UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        text = value.strip()
        if not text:
            return None
        try:
            timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return ensure_utc_datetime(timestamp)


def ensure_utc_datetime(value: datetime) -> datetime:
    """Normalize a datetime to UTC for storage and comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_utc_isoformat(value: str | datetime | None) -> str | None:
    """Serialize a timestamp as a UTC ISO-8601 string for database storage."""
    timestamp = parse_utc_timestamp(value)
    if timestamp is None:
        return None
    return ensure_utc_datetime(timestamp).isoformat()


def format_display_timestamp(value: str | datetime | None, *, missing: str = "N/A") -> str:
    """Format a stored UTC timestamp for display in Europe/Amsterdam."""
    if isinstance(value, str) and value.strip():
        raw = value.strip()
    else:
        raw = None

    timestamp = parse_utc_timestamp(value)
    if timestamp is None:
        return raw or missing

    local = timestamp.astimezone(DISPLAY_TIMEZONE)
    return local.strftime("%Y-%m-%d %H:%M")
