"""Filter and format import logs for the Activity dashboard."""

from __future__ import annotations

from typing import Any

from import_status import import_status_reason, normalize_import_status
from contact_classification import format_import_sender_label, should_redact_import_sender

ACTIVITY_FEED_STATUSES = frozenset({"success", "warning"})
IGNORED_STATUSES = frozenset({"no_watch_detected", "noise", "request_intent"})


def message_preview(text: str | None, *, max_length: int = 80) -> str:
    """Return a single-line preview of the original message."""
    if not text:
        return "N/A"
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_length:
        return cleaned
    return f"{cleaned[: max_length - 1]}…"


def format_dealer_label(import_log: dict[str, Any]) -> str:
    return format_import_sender_label(import_log)


def activity_feed_counts(import_logs: list[dict[str, Any]]) -> dict[str, int]:
    """Count offers, needs-review, and ignored imports."""
    counts = {"offers": 0, "needs_review": 0, "ignored": 0}
    for import_log in import_logs:
        status = normalize_import_status(import_log)
        if status == "success":
            counts["offers"] += 1
        elif status == "warning":
            counts["needs_review"] += 1
        elif status in IGNORED_STATUSES:
            counts["ignored"] += 1
    return counts


def filter_activity_feed_imports(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return imports that belong on the main Activity page."""
    return [
        import_log
        for import_log in import_logs
        if normalize_import_status(import_log) in ACTIVITY_FEED_STATUSES
    ]


def filter_ignored_import_logs(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return imports with no detected watch offers."""
    return [
        import_log
        for import_log in import_logs
        if normalize_import_status(import_log) in IGNORED_STATUSES
    ]


def build_ignored_activity_row(
    import_log: dict[str, Any],
    message: dict[str, Any] | None,
) -> dict[str, Any]:
    """Format one ignored import for the ignored-messages page."""
    return {
        "id": import_log["id"],
        "import_time": import_log.get("import_time"),
        "group_name": import_log.get("group_name") or "N/A",
        "dealer": format_dealer_label(import_log),
        "dealer_redacted": should_redact_import_sender(import_log),
        "message_preview": message_preview(message.get("raw_text") if message else None),
        "status_reason": import_status_reason(import_log),
    }
