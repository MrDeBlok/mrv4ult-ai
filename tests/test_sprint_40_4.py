"""Tests for Sprint 40.4 notification page filters."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from notifications import (
    filter_notifications_by_type,
    normalize_notification_filter,
    notification_filter_counts,
)
from tests.notification_mocks import patch_notification_import_queries

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
    },
}

ALL_NOTIFICATIONS = [
    {
        "id": "n-review",
        "type": "needs_review",
        "title": "Needs review · HK Dealers",
        "message": "Important fields are missing — watch 1: missing reference",
        "related_import_log_id": "log-1",
        "is_read": False,
        "created_at": "2026-06-27T12:00:00+00:00",
    },
    {
        "id": "n-buy",
        "type": "excellent_buy",
        "title": "Excellent Buy · Rolex 126200",
        "message": "Imported offer at $72,000 is an Excellent Buy.",
        "related_import_log_id": "log-2",
        "is_read": True,
        "created_at": "2026-06-27T11:00:00+00:00",
    },
    {
        "id": "n-low",
        "type": "new_lowest_price",
        "title": "New lowest market price · Rolex 126200",
        "message": "Imported offer is below the previous lowest.",
        "related_import_log_id": "log-3",
        "is_read": False,
        "created_at": "2026-06-27T10:00:00+00:00",
    },
    {
        "id": "n-match",
        "type": "request_match",
        "title": "Client request matched · John Smith",
        "message": "Reference match: 126200",
        "related_request_id": "req-1",
        "is_read": False,
        "created_at": "2026-06-27T09:00:00+00:00",
    },
]


class TestNotificationFilterHelpers:
    def test_notification_filter_counts(self) -> None:
        counts = notification_filter_counts(ALL_NOTIFICATIONS)

        assert counts == {
            "all": 4,
            "needs_review": 1,
            "excellent_buy": 1,
            "new_lowest_price": 1,
        }

    def test_normalize_notification_filter_defaults_to_all(self) -> None:
        assert normalize_notification_filter(None) == "all"
        assert normalize_notification_filter("unknown") == "all"

    def test_filter_notifications_by_type(self) -> None:
        filtered = filter_notifications_by_type(ALL_NOTIFICATIONS, "excellent_buy")
        assert len(filtered) == 1
        assert filtered[0]["id"] == "n-buy"


class TestNotificationsPageFilters:
    @patch("app.list_notifications")
    def test_filter_all_shows_every_notification(self, mock_list: MagicMock) -> None:
        mock_list.return_value = ALL_NOTIFICATIONS

        with patch_notification_import_queries(import_logs={"log-1": IMPORT_LOG}, messages={}):
            client = TestClient(app)
            response = client.get("/notifications")

        assert response.status_code == 200
        assert "All (4)" in response.text
        assert "Needs Review (1)" in response.text
        assert "Excellent Buy (1)" in response.text
        assert "New Lowest Price (1)" in response.text
        assert "Needs review · HK Dealers" in response.text
        assert "Excellent Buy · Rolex 126200" in response.text
        assert "Client request matched" in response.text

    @patch("app.list_notifications")
    def test_filter_needs_review(self, mock_list: MagicMock) -> None:
        mock_list.return_value = ALL_NOTIFICATIONS

        with patch_notification_import_queries(import_logs={"log-1": IMPORT_LOG}, messages={}):
            client = TestClient(app)
            response = client.get("/notifications?type=needs_review")

        assert response.status_code == 200
        assert "Needs review · HK Dealers" in response.text
        assert "Excellent Buy · Rolex 126200" not in response.text
        assert "New lowest market price" not in response.text
        assert "Client request matched" not in response.text

    @patch("app.list_notifications")
    def test_filter_excellent_buy(self, mock_list: MagicMock) -> None:
        mock_list.return_value = ALL_NOTIFICATIONS

        with patch_notification_import_queries():
            client = TestClient(app)
            response = client.get("/notifications?type=excellent_buy")

        assert response.status_code == 200
        assert "Excellent Buy · Rolex 126200" in response.text
        assert "Needs review · HK Dealers" not in response.text

    @patch("app.list_notifications")
    def test_filter_new_lowest_price(self, mock_list: MagicMock) -> None:
        mock_list.return_value = ALL_NOTIFICATIONS

        with patch_notification_import_queries():
            client = TestClient(app)
            response = client.get("/notifications?type=new_lowest_price")

        assert response.status_code == 200
        assert "New lowest market price · Rolex 126200" in response.text
        assert "Excellent Buy · Rolex 126200" not in response.text

    @patch("app.list_notifications")
    def test_empty_filter_state_message(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [ALL_NOTIFICATIONS[2]]

        with patch_notification_import_queries():
            client = TestClient(app)
            response = client.get("/notifications?type=needs_review")

        assert response.status_code == 200
        assert "No notifications for this filter." in response.text
        assert "Needs Review (0)" in response.text
        assert "All (1)" in response.text

    @patch("app.list_notifications", return_value=[])
    def test_navbar_unread_badge_unchanged_with_filter(self, mock_list: MagicMock) -> None:
        from app import templates

        templates.env.globals["unread_notification_count"] = lambda: 5
        client = TestClient(app)
        response = client.get("/notifications?type=excellent_buy")

        assert response.status_code == 200
        assert "notification-nav-badge" in response.text
        assert ">5<" in response.text

    @patch("app.list_notifications")
    def test_quick_fix_still_available_on_needs_review_filter(
        self,
        mock_list: MagicMock,
    ) -> None:
        mock_list.return_value = [ALL_NOTIFICATIONS[0]]

        with patch_notification_import_queries(import_logs={"log-1": IMPORT_LOG}, messages={}):
            client = TestClient(app)
            response = client.get("/notifications?type=needs_review")

        assert response.status_code == 200
        assert "Quick fix" in response.text
        assert 'name="type" value="needs_review"' in response.text

    @patch("app.list_notifications")
    def test_view_import_link_still_works(self, mock_list: MagicMock) -> None:
        mock_list.return_value = ALL_NOTIFICATIONS

        with patch_notification_import_queries(import_logs={"log-1": IMPORT_LOG}, messages={}):
            client = TestClient(app)
            response = client.get("/notifications?type=needs_review")

        assert response.status_code == 200
        assert "/activity/log-1" in response.text
        assert "View import" in response.text
