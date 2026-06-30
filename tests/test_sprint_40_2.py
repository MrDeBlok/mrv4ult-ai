"""Tests for Sprint 40.2 notification message previews."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_notification_rows
from notifications import format_message_preview
from tests.notification_mocks import patch_notification_import_queries


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
    def test_notification_with_linked_import_shows_preview(self) -> None:
        import_logs = {"log-1": {"id": "log-1", "message_id": "msg-1"}}
        messages = {
            "msg-1": {"id": "msg-1", "raw_text": "ROLEX 126200 green jub 74000usd"},
        }
        with patch_notification_import_queries(import_logs=import_logs, messages=messages):
            rows = build_notification_rows([NOTIFICATION])

        assert rows[0]["message_preview"] == "ROLEX 126200 green jub 74000usd"

    def test_notification_without_message_does_not_include_preview(self) -> None:
        import_logs = {"log-1": {"id": "log-1", "message_id": "msg-1"}}
        with patch_notification_import_queries(import_logs=import_logs, messages={}):
            rows = build_notification_rows([NOTIFICATION])

        assert "message_preview" not in rows[0]


class TestNotificationsPagePreview:
    @patch("app.list_notifications")
    def test_notifications_page_renders_message_preview(
        self,
        mock_list: MagicMock,
    ) -> None:
        mock_list.return_value = [NOTIFICATION]
        import_logs = {"log-1": {"id": "log-1", "message_id": "msg-1", "summary": {}}}
        messages = {
            "msg-1": {"id": "msg-1", "raw_text": "ROLEX 126200 green jub 74000usd"},
        }

        with patch_notification_import_queries(import_logs=import_logs, messages=messages):
            client = TestClient(app)
            response = client.get("/notifications")

        assert response.status_code == 200
        assert "notification-message-preview" in response.text
        assert "ROLEX 126200 green jub 74000usd" in response.text

    @patch("app.list_notifications")
    def test_notifications_page_hides_empty_preview_block(
        self,
        mock_list: MagicMock,
    ) -> None:
        mock_list.return_value = [NOTIFICATION]
        import_logs = {"log-1": {"id": "log-1", "message_id": "msg-1", "summary": {}}}

        with patch_notification_import_queries(import_logs=import_logs, messages={}):
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
