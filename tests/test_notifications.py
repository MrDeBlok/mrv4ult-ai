"""Tests for the notification center."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app, build_notification_rows
from notifications import (
    NOTIFICATION_TYPE_LABELS,
    build_notification_display,
    load_message_previews_by_import_log_id,
    notify_excellent_buy,
    notify_needs_review,
    notify_new_lowest_price,
    notify_request_match,
    record_import_notifications,
)
from tests.notification_mocks import patch_notification_import_queries

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
IMPORT_LOG_ID_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
MESSAGE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
NOTIFICATION_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


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
                        "condition": "New",
                        "market_condition": "New",
                        "previous_lowest_usd": "$74,000",
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
        with patch_notification_import_queries():
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


class TestNotificationPreviewResilience:
    @patch("database.get_messages_by_ids")
    @patch("database.get_import_logs_by_ids")
    def test_load_message_previews_uses_lightweight_import_log_projection(
        self,
        mock_get_import_logs: MagicMock,
        mock_get_messages: MagicMock,
    ) -> None:
        from database import IMPORT_LOG_PREVIEW_COLUMNS

        mock_get_import_logs.return_value = {
            IMPORT_LOG_ID: {"id": IMPORT_LOG_ID, "message_id": MESSAGE_ID}
        }
        mock_get_messages.return_value = {
            MESSAGE_ID: {"id": MESSAGE_ID, "raw_text": "Rolex 126610LN New 2024 USD14500"}
        }

        previews, failed = load_message_previews_by_import_log_id(
            [
                {
                    "id": NOTIFICATION_ID,
                    "related_import_log_id": IMPORT_LOG_ID,
                }
            ]
        )

        assert failed is False
        assert "Rolex" in previews[IMPORT_LOG_ID]
        mock_get_import_logs.assert_called_once_with(
            [IMPORT_LOG_ID],
            select_fields=IMPORT_LOG_PREVIEW_COLUMNS,
        )

    @patch(
        "notifications._load_message_previews_by_import_log_id",
        side_effect=RuntimeError("PostgREST APIError"),
    )
    def test_preview_loader_failure_returns_empty_without_raising(
        self,
        _mock_load: MagicMock,
    ) -> None:
        previews, failed = load_message_previews_by_import_log_id(
            [{"id": NOTIFICATION_ID, "related_import_log_id": IMPORT_LOG_ID}]
        )

        assert previews == {}
        assert failed is True

    @patch(
        "app.load_message_previews_by_import_log_id",
        return_value=({}, True),
    )
    def test_build_notification_rows_marks_preview_unavailable_on_failure(
        self,
        _mock_previews: MagicMock,
    ) -> None:
        rows = build_notification_rows(
            [
                {
                    "id": NOTIFICATION_ID,
                    "type": "needs_review",
                    "title": "Needs review",
                    "message": "Missing fields",
                    "related_import_log_id": IMPORT_LOG_ID,
                    "is_read": False,
                    "created_at": "2026-06-27T12:00:00+00:00",
                }
            ]
        )

        assert rows[0]["message_preview_unavailable"] is True
        assert "message_preview" not in rows[0]

    @patch("app.get_import_logs_by_ids", return_value={})
    @patch(
        "app.load_message_previews_by_import_log_id",
        return_value=({}, False),
    )
    def test_missing_import_log_still_renders_notification_row(
        self,
        _mock_previews: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        rows = build_notification_rows(
            [
                {
                    "id": NOTIFICATION_ID,
                    "type": "excellent_buy",
                    "title": "Excellent Buy",
                    "message": "Deal alert",
                    "related_import_log_id": IMPORT_LOG_ID,
                    "is_read": False,
                    "created_at": "2026-06-27T12:00:00+00:00",
                }
            ]
        )

        assert rows[0]["title"] == "Excellent Buy"
        assert "message_preview" not in rows[0]

    @patch("app.build_notification_rows")
    @patch("app.list_notifications")
    def test_notifications_page_returns_200_when_preview_enrichment_fails(
        self,
        mock_list: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list.return_value = [
            {
                "id": NOTIFICATION_ID,
                "type": "needs_review",
                "title": "Needs review",
                "message": "Missing fields",
                "related_import_log_id": IMPORT_LOG_ID,
                "is_read": False,
                "created_at": "2026-06-27T12:00:00+00:00",
            }
        ]
        mock_build_rows.return_value = [
            {
                "id": NOTIFICATION_ID,
                "type": "needs_review",
                "type_label": "Needs review",
                "type_class": "warning",
                "title": "Needs review",
                "message": "Missing fields",
                "created_at": "2026-06-27T12:00:00+00:00",
                "is_read": False,
                "link_url": f"/activity/{IMPORT_LOG_ID}",
                "link_label": "View import",
                "message_preview_unavailable": True,
                "show_quick_fix": True,
            }
        ]

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "Message preview unavailable." in response.text

    @patch("database._query_table_in_id_chunks", side_effect=RuntimeError("PostgREST APIError"))
    @patch("app.list_notifications")
    def test_notifications_page_survives_import_log_lookup_failure(
        self,
        mock_list: MagicMock,
        _mock_chunk_lookup: MagicMock,
    ) -> None:
        mock_list.return_value = [
            {
                "id": NOTIFICATION_ID,
                "type": "needs_review",
                "title": "Needs review",
                "message": "Missing fields",
                "related_import_log_id": IMPORT_LOG_ID,
                "is_read": False,
                "created_at": "2026-06-27T12:00:00+00:00",
            }
        ]

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "Needs review" in response.text
        assert "Message preview unavailable." in response.text


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

        with patch_notification_import_queries():
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


class TestNotificationCleanup:
    @patch("app.delete_notification")
    def test_delete_single_notification(self, mock_delete: MagicMock) -> None:
        client = TestClient(app)
        response = client.post(
            "/notifications/n-1/delete",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/notifications"
        mock_delete.assert_called_once_with("n-1")

    @patch("app.delete_notification")
    def test_delete_single_notification_requires_confirmation(
        self,
        mock_delete: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post("/notifications/n-1/delete", data={"confirm": "0"})

        assert response.status_code == 400
        mock_delete.assert_not_called()

    @patch("app.delete_read_notifications")
    def test_clear_read_notifications(self, mock_clear_read: MagicMock) -> None:
        client = TestClient(app)
        response = client.post(
            "/notifications/clear-read",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/notifications"
        mock_clear_read.assert_called_once()

    @patch("app.delete_all_notifications")
    def test_clear_all_notifications(self, mock_clear_all: MagicMock) -> None:
        client = TestClient(app)
        response = client.post(
            "/notifications/clear-all",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/notifications"
        mock_clear_all.assert_called_once()

    @patch("app.mark_notification_read")
    def test_mark_read_remains_team_wide(self, mock_mark_read: MagicMock) -> None:
        client = TestClient(app)
        response = client.post("/notifications/n-1/read", follow_redirects=False)

        assert response.status_code == 303
        mock_mark_read.assert_called_once_with("n-1")

    @patch("app.list_notifications")
    def test_unread_badge_updates_after_delete(self, mock_list: MagicMock) -> None:
        from app import templates

        mock_list.return_value = []
        templates.env.globals["unread_notification_count"] = lambda: 0

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "notification-nav-badge" not in response.text
        assert "No notifications." in response.text

    @patch("app.list_notifications")
    def test_deleted_notifications_no_longer_render(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            {
                "id": "n-remaining",
                "type": "needs_review",
                "title": "Still here",
                "message": "Visible",
                "related_import_log_id": "log-1",
                "is_read": False,
                "created_at": "2026-06-27T12:00:00+00:00",
            }
        ]

        with patch_notification_import_queries():
            client = TestClient(app)
            response = client.get("/notifications")

        assert response.status_code == 200
        assert "Still here" in response.text
        assert "Deleted alert" not in response.text
        assert "Delete" in response.text
        assert "Clear read notifications" not in response.text

    @patch("app.list_notifications")
    def test_notifications_page_shows_clear_actions(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            {
                "id": "n-read",
                "type": "excellent_buy",
                "title": "Read alert",
                "message": "Done",
                "related_import_log_id": "log-1",
                "is_read": True,
                "created_at": "2026-06-27T12:00:00+00:00",
            },
            {
                "id": "n-unread",
                "type": "needs_review",
                "title": "Unread alert",
                "message": "Pending",
                "related_import_log_id": "log-2",
                "is_read": False,
                "created_at": "2026-06-27T12:00:00+00:00",
            },
        ]

        with patch_notification_import_queries():
            client = TestClient(app)
            response = client.get("/notifications")

        assert response.status_code == 200
        assert "Clear read notifications" in response.text
        assert "Clear all notifications" in response.text
        assert "Mark all read" in response.text


class TestSprint327ClearAllNotifications:
    @patch("database.get_client")
    def test_delete_all_notifications_does_not_compare_uuid_to_empty_string(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        from database import delete_all_notifications

        mock_table = MagicMock()
        mock_delete = MagicMock()
        mock_filter = MagicMock()
        mock_get_client.return_value.table.return_value = mock_table
        mock_table.delete.return_value = mock_delete
        mock_delete.not_.is_.return_value = mock_filter
        mock_filter.execute.return_value = MagicMock(data=[])

        delete_all_notifications()

        mock_get_client.return_value.table.assert_called_once_with("notifications")
        mock_table.delete.assert_called_once()
        mock_delete.neq.assert_not_called()
        mock_delete.not_.is_.assert_called_once_with("created_at", "null")
        mock_filter.execute.assert_called_once()

    @patch("database.get_client")
    def test_delete_all_notifications_deletes_all_rows(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        from database import delete_all_notifications

        mock_table = MagicMock()
        mock_delete = MagicMock()
        mock_filter = MagicMock()
        mock_get_client.return_value.table.return_value = mock_table
        mock_table.delete.return_value = mock_delete
        mock_delete.not_.is_.return_value = mock_filter
        mock_filter.execute.return_value = MagicMock(
            data=[{"id": "n-1"}, {"id": "n-2"}, {"id": "n-3"}]
        )

        deleted = delete_all_notifications()

        assert deleted == 3
        mock_delete.not_.is_.assert_called_once_with("created_at", "null")

    @patch("app.delete_all_notifications", return_value=3)
    def test_clear_all_notifications_route_redirects_successfully(
        self,
        mock_clear_all: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post(
            "/notifications/clear-all",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/notifications"
        mock_clear_all.assert_called_once()

    def test_delete_notification_rejects_empty_id(self) -> None:
        from database import delete_notification

        with pytest.raises(ValueError, match="Notification id is required"):
            delete_notification("   ")
