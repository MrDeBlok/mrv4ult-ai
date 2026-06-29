"""Trader dashboard summary cards for logged-in users."""

from __future__ import annotations

from typing import Any

from contact_classification import build_dealer_lookup_by_whatsapp, filter_business_import_logs
from database import list_clients, list_contacts_for_import_lookup, list_dealers, list_import_logs, list_requests
from import_status import filter_discarded_import_logs
from notifications import get_unread_notification_count
from parser_review import parser_review_counts
from user_visibility import filter_imports_for_user

Record = dict[str, Any]

ACTIVE_REQUEST_STATUSES = frozenset({"open", "active"})


def count_active_requests(requests: list[Record]) -> int:
    """Return the number of open client requests."""
    return sum(
        1
        for request in requests
        if (request.get("status") or "").lower() in ACTIVE_REQUEST_STATUSES
    )


def parser_review_import_logs_for_user(user: Record | None) -> list[Record]:
    """Return business imports eligible for parser review, scoped to the user."""
    visible_imports = filter_discarded_import_logs(
        filter_imports_for_user(list_import_logs(), user)
    )
    lookup = build_dealer_lookup_by_whatsapp(list_contacts_for_import_lookup())
    return filter_business_import_logs(visible_imports, lookup)


def build_dashboard_cards(
    *,
    parser_review_count: int,
    active_requests_count: int,
    clients_count: int,
    dealers_count: int,
    notifications_count: int,
) -> list[Record]:
    """Build linked summary cards for the trader dashboard."""
    return [
        {
            "key": "parser_reviews",
            "title": "Parser Reviews",
            "count": parser_review_count,
            "url": "/parser-review",
            "description": "Imports that still need parser review.",
        },
        {
            "key": "active_requests",
            "title": "Active Requests",
            "count": active_requests_count,
            "url": "/requests?status=open",
            "description": "Open client buy requests.",
        },
        {
            "key": "clients",
            "title": "Clients",
            "count": clients_count,
            "url": "/clients",
            "description": "CRM client contacts.",
        },
        {
            "key": "dealers",
            "title": "Dealers",
            "count": dealers_count,
            "url": "/dealers",
            "description": "Dealers with stored offers.",
        },
        {
            "key": "notifications",
            "title": "Notifications",
            "count": notifications_count,
            "url": "/notifications",
            "description": "Unread alerts and matches.",
        },
    ]


def load_dashboard_cards(user: Record | None) -> list[Record]:
    """Load dashboard cards using existing visibility rules."""
    parser_logs = parser_review_import_logs_for_user(user)
    parser_review_count = parser_review_counts(parser_logs)["total"]

    return build_dashboard_cards(
        parser_review_count=parser_review_count,
        active_requests_count=count_active_requests(list_requests()),
        clients_count=len(list_clients()),
        dealers_count=len(list_dealers()),
        notifications_count=get_unread_notification_count(),
    )
