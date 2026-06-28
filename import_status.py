"""Import log status labels and reasons for the activity dashboard."""

from __future__ import annotations

from typing import Any


def normalize_import_status(import_log: dict[str, Any]) -> str:
    """Map stored import status to the current status vocabulary."""
    status = (import_log.get("status") or "").strip().lower()
    if status == "warning" and import_log.get("watches_parsed", 0) == 0:
        return "no_watch_detected"
    return status


def format_import_status(status: str | None) -> str:
    labels = {
        "success": "Success",
        "no_watch_detected": "No watch detected",
        "warning": "Needs review",
        "noise": "Ignored noise",
        "request_intent": "Buyer request",
        "error": "Error",
    }
    if not status:
        return "Unknown"
    return labels.get(status, status.replace("_", " ").title())


def import_status_class(status: str | None) -> str:
    return {
        "success": "success",
        "no_watch_detected": "info",
        "warning": "warning",
        "noise": "info",
        "request_intent": "info",
        "error": "danger",
    }.get(status or "", "secondary")


def import_status_reason(import_log: dict[str, Any]) -> str:
    summary = import_log.get("summary") or {}
    stored_reason = summary.get("status_reason")
    if isinstance(stored_reason, str) and stored_reason.strip():
        return stored_reason.strip()

    status = normalize_import_status(import_log)
    watches_parsed = import_log.get("watches_parsed", 0)
    duplicate_offers = import_log.get("duplicate_offers", 0)

    if status == "error":
        return "Technical failure during import."
    if status == "noise":
        return "Chat noise detected. No watch offer was identified."
    if status == "request_intent":
        return "Buyer request detected. Offer was not created."
    if status == "no_watch_detected":
        return "No watch offer was detected in this message."
    if status == "warning":
        return "Parsed watches are missing important fields such as brand, reference, or price."
    if duplicate_offers:
        return (
            f"Successfully parsed {watches_parsed} watch offer(s). "
            f"{duplicate_offers} duplicate offer(s) were skipped."
        )
    if watches_parsed:
        return f"Successfully parsed {watches_parsed} watch offer(s)."
    return "Import completed."
