"""Tests for the notification center."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_notification_rows
from notifications import (
    NOTIFICATION_TYPE_LABELS,
    build_notification_display,
    notify_excellent_buy,
    notify_needs_review,
    notify_new_lowest_price,
    notify_request_match,
    record_import_notifications,
)


class TestNotificationCreation:
    @patch("notifications.create_notification")
    def test_notify_request_match(self, mock_create: MagicMock) -> None:
        mock_create.return_value = {"id": "n-1", "type": "request_match"}

        notify_request_match(
            import_log_id="log-1",
            request_id="req-1",
            offer_id="offer-1",
            client_name="John Smith",
            match_reason="Reference match: 116508",
        )

        mock_create.assert_called_once_with(
            type="request_match",
            title="Client request matched · John Smith",
            message="Reference match: 116508",
            related_import_log_id="log-1",
            related_request_id="req-1",
            related_offer_id="offer-1",
        )

    @patch("notifications.create_notification")
    def test_notify_new_lowest_price(self, mock_create: MagicMock) -> None:
        mock_create.return_value = {"id": "n-2", "type": "new_lowest_price"}

        notify_new_lowest_price(
            import_log_id="log-1",
            offer_id="offer-1",
            row={
                "brand": "Rolex",
                "reference": "126200",
                "previous_lowest_usd": "$74,000",
                "price_difference": "-$2,000",
            },
        )

        assert mock_create.call_args.kwargs["type"] == "new_lowest_price"
        assert "126200" in mock_create.call_args.kwargs["title"]

    @patch("notifications.create_notification")
    def test_notify_excellent_buy(self, mock_create: MagicMock) -> None:
        mock_create.return_value = {"id": "n-3", "type": "excellent_buy"}

        notify_excellent_buy(
            import_log_id="log-1",
            offer_id="offer-1",
            row={"brand": "Rolex", "reference": "126200", "price": "$72,000"},
        )

        assert mock_create.call_args.kwargs["type"] == "excellent_buy"

    @patch("notifications.create_notification")
    def test_notify_needs_review(self, mock_create: MagicMock) -> None:
        mock_create.return_value = {"id": "n-4", "type": "needs_review"}

        notify_needs_review(
            import_log_id="log-1",
            reason="Important fields are missing — watch 1: missing reference",
            group_name="HK Dealers",
        )

        assert mock_create.call_args.kwargs["type"] == "needs_review"
        assert "HK Dealers" in mock_create.call_args.kwargs["title"]


class TestRecordImportNotifications:
    @patch("notifications.notify_excellent_buy")
    @patch("notifications.notify_new_lowest_price")
    @patch("notifications.notify_needs_review")
    def test_creates_review_and_price_notifications(
        self,
        mock_needs_review: MagicMock,
        mock_new_lowest: MagicMock,
        mock_excellent_buy: MagicMock,
    ) -> None:
        mock_needs_review.return_value = {"id": "n-review"}
        mock_new_lowest.return_value = {"id": "n-low"}
        mock_excellent_buy.return_value = {"id": "n-buy"}

        created = record_import_notifications(
            import_log_id="log-1",
            import_status="warning",
            summary={
                "group": "HK Dealers",
                "status_reason": "Important fields are missing — watch 1: missing reference",
                "rows": [
                    {
                        "offer_id": "offer-1",
                        "brand": "Rolex",
                        "reference": "126200",
                        "price_label": "New lowest price",
                        "results": ["Existing watch", "New offer"],
                    }
                ],
            },
        )

        mock_needs_review.assert_called_once()
        mock_new_lowest.assert_called_once()
        mock_excellent_buy.assert_called_once()
        assert len(created) == 3


class TestNotificationDisplay:
    def test_build_notification_display_links_to_import(self) -> None:
        row = build_notification_display(
            {
                "id": "n-1",
                "type": "request_match",
                "title": "Client request matched",
                "message": "Reference match",
                "related_import_log_id": "log-1",
                "is_read": False,
                "created_at": "2026-06-27T12:00:00+00:00",
            }
        )

        assert row["type_label"] == NOTIFICATION_TYPE_LABELS["request_match"]
        assert row["link_url"] == "/activity/log-1"
        assert row["link_label"] == "View import"

    def test_build_notification_rows_formats_created_at(self) -> None:
        rows = build_notification_rows(
            [
                {
                    "id": "n-1",
                    "type": "excellent_buy",
                    "title": "Excellent Buy",
                    "message": "Deal alert",
                    "related_import_log_id": "log-1",
                    "is_read": False,
                    "created_at": "2026-06-27T12:00:00+00:00",
                }
            ]
        )

        assert rows[0]["title"] == "Excellent Buy"
        assert "2026" in rows[0]["created_at"]


class TestNotificationsPage:
    @patch("app.list_notifications")
    def test_notifications_page_renders_unread_first(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            {
                "id": "n-unread",
                "type": "needs_review",
                "title": "Needs review",
                "message": "Missing fields",
                "related_import_log_id": "log-1",
                "is_read": False,
                "created_at": "2026-06-27T12:00:00+00:00",
            }
        ]

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "Needs review" in response.text
        assert "Mark read" in response.text
        assert "/activity/log-1" in response.text

    @patch("app.mark_notification_read")
    def test_mark_as_read_action(self, mock_mark_read: MagicMock) -> None:
        client = TestClient(app)
        response = client.post("/notifications/n-1/read", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/notifications"
        mock_mark_read.assert_called_once_with("n-1")

    @patch("app.list_notifications", return_value=[])
    def test_navbar_shows_unread_badge(self, mock_list: MagicMock) -> None:
        from app import templates

        templates.env.globals["unread_notification_count"] = lambda: 3
        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "notification-nav-badge" in response.text
        assert ">3<" in response.text
