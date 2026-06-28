"""Notification creation helpers for MRV4ULT AI."""

from __future__ import annotations

from typing import Any

from database import create_notification

Record = dict[str, Any]

NOTIFICATION_TYPES = frozenset(
    {
        "request_match",
        "new_lowest_price",
        "excellent_buy",
        "needs_review",
    }
)

NOTIFICATION_TYPE_LABELS: dict[str, str] = {
    "request_match": "Request match",
    "new_lowest_price": "New lowest price",
    "excellent_buy": "Excellent Buy",
    "needs_review": "Needs review",
}

NOTIFICATION_TYPE_CLASSES: dict[str, str] = {
    "request_match": "primary",
    "new_lowest_price": "success",
    "excellent_buy": "success",
    "needs_review": "warning",
}


def _watch_label(row: Record) -> str:
    parts = [part for part in (row.get("brand"), row.get("reference") or row.get("model")) if part]
    return " · ".join(str(part) for part in parts) if parts else "Watch offer"


def notify_request_match(
    *,
    import_log_id: str,
    request_id: str,
    offer_id: str,
    client_name: str,
    match_reason: str,
) -> Record:
    return create_notification(
        type="request_match",
        title=f"Client request matched · {client_name}",
        message=match_reason,
        related_import_log_id=import_log_id,
        related_request_id=request_id,
        related_offer_id=offer_id,
    )


def notify_new_lowest_price(
    *,
    import_log_id: str,
    offer_id: str,
    row: Record,
) -> Record:
    label = _watch_label(row)
    previous_lowest = row.get("previous_lowest_usd") or "market"
    difference = row.get("price_difference") or "N/A"
    return create_notification(
        type="new_lowest_price",
        title=f"New lowest market price · {label}",
        message=f"Imported offer is below the previous lowest ({previous_lowest}, {difference}).",
        related_import_log_id=import_log_id,
        related_offer_id=offer_id,
    )


def notify_excellent_buy(
    *,
    import_log_id: str,
    offer_id: str,
    row: Record,
) -> Record:
    label = _watch_label(row)
    price = row.get("price") or row.get("usd_price") or "N/A"
    return create_notification(
        type="excellent_buy",
        title=f"Excellent Buy · {label}",
        message=f"Imported offer at {price} is an Excellent Buy versus active market comparables.",
        related_import_log_id=import_log_id,
        related_offer_id=offer_id,
    )


def notify_needs_review(
    *,
    import_log_id: str,
    reason: str,
    group_name: str | None = None,
) -> Record:
    source = group_name or "Import"
    return create_notification(
        type="needs_review",
        title=f"Needs review · {source}",
        message=reason,
        related_import_log_id=import_log_id,
    )


def _is_new_offer_row(row: Record) -> bool:
    results = row.get("results") or []
    return "New offer" in results


def record_import_notifications(
    *,
    import_log_id: str,
    summary: Record,
    import_status: str,
) -> list[Record]:
    """Create notifications for notable import outcomes."""
    created: list[Record] = []

    if import_status == "warning":
        created.append(
            notify_needs_review(
                import_log_id=import_log_id,
                reason=str(summary.get("status_reason") or "Import needs review."),
                group_name=summary.get("group"),
            )
        )

    for row in summary.get("rows") or []:
        if not _is_new_offer_row(row):
            continue

        offer_id = row.get("offer_id")
        if not offer_id:
            continue

        label = row.get("price_label")
        if label == "New lowest price":
            created.append(
                notify_new_lowest_price(
                    import_log_id=import_log_id,
                    offer_id=str(offer_id),
                    row=row,
                )
            )
            created.append(
                notify_excellent_buy(
                    import_log_id=import_log_id,
                    offer_id=str(offer_id),
                    row=row,
                )
            )

    return created


def build_notification_display(notification: Record) -> Record:
    """Format a notification row for the dashboard."""
    link_url: str | None = None
    link_label: str | None = None
    if notification.get("related_import_log_id"):
        link_url = f"/activity/{notification['related_import_log_id']}"
        link_label = "View import"
    elif notification.get("related_request_id"):
        link_url = "/requests"
        link_label = "View requests"

    notification_type = str(notification.get("type") or "")
    return {
        "id": notification["id"],
        "type": notification_type,
        "type_label": NOTIFICATION_TYPE_LABELS.get(notification_type, notification_type.title()),
        "type_class": NOTIFICATION_TYPE_CLASSES.get(notification_type, "secondary"),
        "title": notification.get("title") or "",
        "message": notification.get("message") or "",
        "created_at": notification.get("created_at"),
        "is_read": bool(notification.get("is_read")),
        "link_url": link_url,
        "link_label": link_label,
    }


def get_unread_notification_count() -> int:
    """Return unread notification count, falling back to zero outside Supabase."""
    from database import count_unread_notifications

    try:
        return count_unread_notifications()
    except Exception:
        return 0
