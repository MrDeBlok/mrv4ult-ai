"""Filter and format import logs for the Activity dashboard."""

from __future__ import annotations

from typing import Any

from contact_classification import format_import_sender_label, should_redact_import_sender
from dealer_intelligence import format_activity_timestamp
from import_status import import_status_reason, is_discarded_no_watch_import, normalize_import_status

ACTIVITY_TABS = frozenset({"active", "reviewed", "ignored", "all"})
IGNORED_ACTIVITY_STATUSES = frozenset({"noise", "request_intent", "insufficient_evidence"})


def filter_discarded_activity_imports(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop legacy no-watch imports that should never appear in Activity."""
    return [
        import_log
        for import_log in import_logs
        if not is_discarded_no_watch_import(import_log)
    ]


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


def import_summary(import_log: dict[str, Any]) -> dict[str, Any]:
    summary = import_log.get("summary")
    return summary if isinstance(summary, dict) else {}


def is_parser_reviewed(import_log: dict[str, Any]) -> bool:
    return bool(import_summary(import_log).get("parser_reviewed"))


def is_parser_review_ignored(import_log: dict[str, Any]) -> bool:
    return bool(import_summary(import_log).get("parser_review_ignored"))


def has_real_offers(import_log: dict[str, Any]) -> bool:
    """Return True when an import created or parsed watch offers."""
    return import_log.get("new_offers", 0) > 0 or import_log.get("watches_parsed", 0) > 0


def is_active_success_import(import_log: dict[str, Any]) -> bool:
    if normalize_import_status(import_log) != "success":
        return False
    if is_parser_reviewed(import_log):
        return False
    return has_real_offers(import_log)


def is_active_needs_review(import_log: dict[str, Any]) -> bool:
    if normalize_import_status(import_log) != "warning":
        return False
    if is_parser_reviewed(import_log) or is_parser_review_ignored(import_log):
        return False
    return True


def filter_active_activity_imports(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return imports for the default Activity tab."""
    import_logs = filter_discarded_activity_imports(import_logs)
    return [
        import_log
        for import_log in import_logs
        if is_active_success_import(import_log) or is_active_needs_review(import_log)
    ]


def filter_reviewed_activity_imports(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return imports marked as reviewed after parser review."""
    import_logs = filter_discarded_activity_imports(import_logs)
    return [import_log for import_log in import_logs if is_parser_reviewed(import_log)]


def filter_ignored_activity_imports(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return dismissed parser issues and non-offer imports."""
    import_logs = filter_discarded_activity_imports(import_logs)
    return [
        import_log
        for import_log in import_logs
        if is_parser_review_ignored(import_log)
        or normalize_import_status(import_log) in IGNORED_ACTIVITY_STATUSES
    ]


def filter_all_activity_imports(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the full business-visible audit trail."""
    return filter_discarded_activity_imports(import_logs)


def activity_feed_counts(import_logs: list[dict[str, Any]]) -> dict[str, int]:
    """Count offers, needs-review, and ignored imports."""
    import_logs = filter_discarded_activity_imports(import_logs)
    return {
        "offers": sum(1 for import_log in import_logs if is_active_success_import(import_log)),
        "needs_review": sum(1 for import_log in import_logs if is_active_needs_review(import_log)),
        "ignored": len(filter_ignored_activity_imports(import_logs)),
    }


def filter_activity_feed_imports(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return imports that belong on the main Activity page."""
    return filter_active_activity_imports(import_logs)


def filter_ignored_import_logs(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return imports hidden from the default Activity page."""
    return filter_ignored_activity_imports(import_logs)


def build_ignored_activity_row(
    import_log: dict[str, Any],
    message: dict[str, Any] | None,
) -> dict[str, Any]:
    """Format one ignored import for the ignored-messages page."""
    return {
        "id": import_log["id"],
        "import_time": format_activity_timestamp(import_log.get("import_time")),
        "group_name": import_log.get("group_name") or "N/A",
        "dealer": format_dealer_label(import_log),
        "dealer_redacted": should_redact_import_sender(import_log),
        "message_preview": message_preview(message.get("raw_text") if message else None),
        "status_reason": import_status_reason(import_log),
    }
