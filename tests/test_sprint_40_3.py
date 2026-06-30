"""Tests for Sprint 40.3 Quick fix on Needs Review notifications."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_notification_rows
from notification_quick_fix import build_quick_fix_prefill, detect_likely_reference_token
from tests.conftest import VIEWER_USER

NEEDS_REVIEW_NOTIFICATION = {
    "id": "n-review",
    "type": "needs_review",
    "title": "Needs review · HK Dealers",
    "message": "Important fields are missing — watch 1: missing reference",
    "related_import_log_id": "log-1",
    "is_read": False,
    "created_at": "2026-06-27T12:00:00+00:00",
}

EXCELLENT_BUY_NOTIFICATION = {
    "id": "n-buy",
    "type": "excellent_buy",
    "title": "Excellent Buy · Rolex 126200",
    "message": "Imported offer at $72,000 is an Excellent Buy.",
    "related_import_log_id": "log-2",
    "is_read": False,
    "created_at": "2026-06-27T12:00:00+00:00",
}

IMPORT_LOG = {
    "id": "log-1",
    "message_id": "msg-1",
    "summary": {
        "parsed_watches": [
            {
                "brand": "Rolex",
                "reference": None,
                "source_line": "Rolex 9659 only Watch 3.000€",
            }
        ],
        "status_reason": "Important fields are missing — watch 1: missing reference",
    },
}


class TestQuickFixPrefill:
    def test_prefill_brand_and_reference_from_preview(self) -> None:
        prefill = build_quick_fix_prefill(
            IMPORT_LOG,
            message_preview="Rolex 9659 only Watch 3.000€",
        )

        assert prefill["brand"] == "Rolex"
        assert prefill["reference"] == "9659"

    def test_detect_likely_reference_token_from_shorthand_line(self) -> None:
        reference = detect_likely_reference_token(
            "Rolex 9659 only Watch 3.000€",
            brand="Rolex",
        )

        assert reference == "9659"


class TestNotificationQuickFixRows:
    @patch("database.get_messages_by_ids")
    @patch("database.get_import_logs_by_ids")
    @patch("app.get_import_logs_by_ids")
    def test_needs_review_notification_includes_quick_fix_prefill(
        self,
        mock_app_import_logs: MagicMock,
        mock_db_import_logs: MagicMock,
        mock_messages: MagicMock,
    ) -> None:
        mock_app_import_logs.return_value = {"log-1": IMPORT_LOG}
        mock_db_import_logs.return_value = {"log-1": IMPORT_LOG}
        mock_messages.return_value = {
            "msg-1": {"id": "msg-1", "raw_text": "Rolex 9659 only Watch 3.000€"},
        }

        rows = build_notification_rows([NEEDS_REVIEW_NOTIFICATION])

        assert rows[0]["show_quick_fix"] is True
        assert rows[0]["quick_fix_prefill"]["brand"] == "Rolex"
        assert rows[0]["quick_fix_prefill"]["reference"] == "9659"

    @patch("database.get_messages_by_ids")
    @patch("database.get_import_logs_by_ids")
    def test_non_needs_review_notification_does_not_show_quick_fix(
        self,
        mock_import_logs: MagicMock,
        mock_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = {}
        mock_messages.return_value = {}

        rows = build_notification_rows([EXCELLENT_BUY_NOTIFICATION])

        assert "show_quick_fix" not in rows[0]


class TestNotificationsPageQuickFix:
    @patch("database.get_messages_by_ids")
    @patch("database.get_import_logs_by_ids")
    @patch("app.get_import_logs_by_ids")
    @patch("app.list_notifications")
    def test_needs_review_notification_shows_quick_fix_button(
        self,
        mock_list: MagicMock,
        mock_app_import_logs: MagicMock,
        mock_db_import_logs: MagicMock,
        mock_messages: MagicMock,
    ) -> None:
        mock_list.return_value = [NEEDS_REVIEW_NOTIFICATION]
        mock_app_import_logs.return_value = {"log-1": IMPORT_LOG}
        mock_db_import_logs.return_value = {"log-1": IMPORT_LOG}
        mock_messages.return_value = {
            "msg-1": {"id": "msg-1", "raw_text": "Rolex 9659 only Watch 3.000€"},
        }

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "Quick fix" in response.text
        assert 'name="reference"' in response.text
        assert "9659" in response.text

    @patch("database.get_messages_by_ids", return_value={})
    @patch("database.get_import_logs_by_ids", return_value={})
    @patch("app.list_notifications")
    def test_non_needs_review_notification_does_not_show_quick_fix(
        self,
        mock_list: MagicMock,
        _mock_db_import_logs: MagicMock,
        _mock_messages: MagicMock,
    ) -> None:
        mock_list.return_value = [EXCELLENT_BUY_NOTIFICATION]

        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "Quick fix" not in response.text

    @patch("app.apply_notification_quick_fix")
    @patch("app.get_notification_by_id")
    @patch("app.list_notifications", return_value=[])
    def test_submit_calls_existing_quick_fix_flow(
        self,
        _mock_list: MagicMock,
        mock_get_notification: MagicMock,
        mock_apply: MagicMock,
    ) -> None:
        mock_get_notification.return_value = NEEDS_REVIEW_NOTIFICATION
        mock_apply.return_value = {"brand_name": "Rolex", "reference": "9659"}

        client = TestClient(app)
        response = client.post(
            "/notifications/n-review/quick-fix",
            data={
                "brand_name": "Rolex",
                "reference": "9659",
                "alias_text": "9659 only",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/notifications?quick_fix_saved=n-review"
        mock_apply.assert_called_once_with(
            import_log_id="log-1",
            brand_name="Rolex",
            reference="9659",
            alias_text="9659 only",
        )

    @patch("notification_quick_fix.teach_watch_mapping_from_quick_fix")
    @patch("database.mark_import_parser_reviewed")
    @patch("database.get_import_log")
    def test_submit_uses_existing_knowledge_mapping_function(
        self,
        mock_get_import_log: MagicMock,
        mock_mark_reviewed: MagicMock,
        mock_teach: MagicMock,
    ) -> None:
        from notification_quick_fix import apply_notification_quick_fix

        mock_get_import_log.return_value = IMPORT_LOG
        mock_teach.return_value = {"brand_name": "Rolex", "reference": "9659"}
        mock_mark_reviewed.return_value = {"id": "log-1", "status": "success"}

        apply_notification_quick_fix(
            import_log_id="log-1",
            brand_name="Rolex",
            reference="9659",
            alias_text="9659 only",
        )

        mock_teach.assert_called_once()
        mock_mark_reviewed.assert_called_once_with("log-1")

    @patch("app.get_notification_by_id")
    @patch("app.get_current_user", return_value=VIEWER_USER)
    def test_viewer_cannot_submit_quick_fix(
        self,
        _mock_user: MagicMock,
        mock_get_notification: MagicMock,
    ) -> None:
        mock_get_notification.return_value = NEEDS_REVIEW_NOTIFICATION

        client = TestClient(app)
        response = client.post(
            "/notifications/n-review/quick-fix",
            data={
                "brand_name": "Rolex",
                "reference": "9659",
            },
        )

        assert response.status_code == 403
