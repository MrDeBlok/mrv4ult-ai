"""Tests for Sprint 40.2 notification message previews."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_notification_rows
from notifications import format_message_preview


NOTIFICATION = {
    "id": "n-1",
    "type": "needs_review",
    "title": "Needs review · HK Dealers",
    "message": "Important fields are missing.",
    "related_import_log_id": "log-1",
    "is_read": False,
    "created_at": "2026-06-27T12:00:00+00:00",
}


class TestMessagePreviewFormatting:
    def test_preview_collapses_line_breaks(self) -> None:
        preview = format_message_preview("ROLEX 126200\n\ngreen jub\n74000usd")
        assert preview == "ROLEX 126200 green jub 74000usd"

    def test_preview_is_truncated_at_180_characters(self) -> None:
        raw_text = "A" * 200
        preview = format_message_preview(raw_text)
        assert preview is not None
        assert len(preview) == 180
        assert preview.endswith("…")
        assert preview.startswith("A" * 179)

    def test_preview_hidden_for_empty_text(self) -> None:
        assert format_message_preview(None) is None
        assert format_message_preview("") is None
        assert format_message_preview("   \n\t  ") is None


class TestNotificationMessagePreviewRows:
    @patch("database.get_messages_by_ids")
    @patch("database.get_import_logs_by_ids")
    def test_notification_with_linked_import_shows_preview(
        self,
        mock_import_logs: MagicMock,
        mock_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = {"log-1": {"id": "log-1", "message_id": "msg-1"}}
        mock_messages.return_value = {
            "msg-1": {"id": "msg-1", "raw_text": "ROLEX 126200 green jub 74000usd"},
        }

        rows = build_notification_rows([NOTIFICATION])

        assert rows[0]["message_preview"] == "ROLEX 126200 green jub 74000usd"

    @patch("database.get_messages_by_ids")
    @patch("database.get_import_logs_by_ids")
    def test_notification_without_message_does_not_include_preview(
        self,
        mock_import_logs: MagicMock,
        mock_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = {"log-1": {"id": "log-1", "message_id": "msg-1"}}
        mock_messages.return_value = {}

        rows = build_notification_rows([NOTIFICATION])

        assert "message_preview" not in rows[0]


class TestNotificationsPagePreview:
    @patch("database.get_messages_by_ids")
    @patch("database.get_import_logs_by_ids")
    @patch("app.list_notifications")
    def test_notifications_page_renders_message_preview(
        self,
        mock_list: MagicMock,
        mock_import_logs: MagicMock,
        mock_messages: MagicMock,
    ) -> None:
        mock_list.return_value = [NOTIFICATION]
        mock_import_logs.return_value = {"log-1": {"id": "log-1", "message_id": "msg-1"}}
        mock_messages.return_value = {
            "msg-1": {"id": "msg-1", "raw_text": "ROLEX 126200 green jub 74000usd"},
        }

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "notification-message-preview" in response.text
        assert "ROLEX 126200 green jub 74000usd" in response.text

    @patch("database.get_messages_by_ids", return_value={})
    @patch("database.get_import_logs_by_ids")
    @patch("app.list_notifications")
    def test_notifications_page_hides_empty_preview_block(
        self,
        mock_list: MagicMock,
        mock_import_logs: MagicMock,
        mock_messages: MagicMock,
    ) -> None:
        mock_list.return_value = [NOTIFICATION]
        mock_import_logs.return_value = {"log-1": {"id": "log-1", "message_id": "msg-1"}}

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "Needs review · HK Dealers" in response.text
        assert "notification-message-preview" not in response.text

    @patch("app.list_notifications", return_value=[])
    def test_navbar_unread_badge_still_works(self, mock_list: MagicMock) -> None:
        from app import templates

        templates.env.globals["unread_notification_count"] = lambda: 4
        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "notification-nav-badge" in response.text
        assert ">4<" in response.text
