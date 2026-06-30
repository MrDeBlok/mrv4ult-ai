"""Import log status labels and reasons for the activity dashboard."""

from __future__ import annotations

from typing import Any

from watch_evidence import INSUFFICIENT_EVIDENCE_REASON


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
        "insufficient_evidence": "Ignored",
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
        "insufficient_evidence": "info",
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
    if status == "insufficient_evidence":
        return INSUFFICIENT_EVIDENCE_REASON
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


def is_discarded_no_watch_import(import_log: dict[str, Any]) -> bool:
    """Return True when an import should never appear in Activity or import history."""
    return normalize_import_status(import_log) == "no_watch_detected"


def filter_discarded_import_logs(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove discarded no-watch imports from app-visible import history."""
    return [import_log for import_log in import_logs if not is_discarded_no_watch_import(import_log)]
