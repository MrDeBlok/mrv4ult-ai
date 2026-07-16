"""Tests for Sprint 37.2 team management and role permissions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from permissions import USER_ROLE_VIEWER, USER_STATUS_DISABLED
from tests.conftest import ADMIN_USER, TRADER_ONE, VIEWER_USER


def _client() -> TestClient:
    with patch("app.start_whatsapp_listener"), patch("app.stop_whatsapp_listener"):
        return TestClient(app)


class TestTeamSettingsAccess:
    @patch("app.list_users")
    def test_admin_can_access_team_settings(self, mock_list_users: MagicMock) -> None:
        mock_list_users.return_value = [ADMIN_USER, TRADER_ONE]
        response = _client().get("/settings/team")

        assert response.status_code == 200
        assert "Team" in response.text
        assert "Admin User" in response.text

    @patch("app.get_current_user", return_value=TRADER_ONE)
    @patch("app.list_users")
    def test_trader_is_denied_team_settings(
        self,
        mock_list_users: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        response = _client().get("/settings/team")

        assert response.status_code == 403
        mock_list_users.assert_not_called()

    @patch("app.get_current_user", return_value=VIEWER_USER)
    @patch("app.list_users")
    def test_viewer_is_denied_team_settings(
        self,
        mock_list_users: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        response = _client().get("/settings/team")

        assert response.status_code == 403
        mock_list_users.assert_not_called()


class TestTeamManagementActions:
    @patch("app.create_user")
    def test_admin_can_create_user(self, mock_create_user: MagicMock) -> None:
        mock_create_user.return_value = {
            **TRADER_ONE,
            "email": "newtrader@mrvault.local",
            "name": "New Trader",
        }
        client = _client()

        response = client.post(
            "/settings/team/create",
            data={
                "name": "New Trader",
                "email": "newtrader@mrvault.local",
                "role": "trader",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/settings/team?created=1"
        mock_create_user.assert_called_once_with(
            name="New Trader",
            email="newtrader@mrvault.local",
            role="trader",
        )

    @patch("app.update_user")
    def test_admin_can_change_user_role(self, mock_update_user: MagicMock) -> None:
        mock_update_user.return_value = {**TRADER_ONE, "role": USER_ROLE_VIEWER}
        client = _client()

        response = client.post(
            f"/settings/team/{TRADER_ONE['id']}/update",
            data={"name": "Trader One", "role": "viewer"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/settings/team?updated=1"
        mock_update_user.assert_called_once_with(
            TRADER_ONE["id"],
            name="Trader One",
            role="viewer",
        )

    @patch("app.set_user_status")
    def test_admin_can_disable_user(self, mock_set_user_status: MagicMock) -> None:
        mock_set_user_status.return_value = {**TRADER_ONE, "status": USER_STATUS_DISABLED}
        client = _client()

        response = client.post(
            f"/settings/team/{TRADER_ONE['id']}/toggle-status",
            data={"action": "disable"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/settings/team?status_changed=1"
        mock_set_user_status.assert_called_once_with(TRADER_ONE["id"], USER_STATUS_DISABLED)


class TestViewerReadOnlyRestrictions:
    @patch("app.get_current_user", return_value=VIEWER_USER)
    def test_viewer_cannot_access_import_page(self, _mock_user: MagicMock) -> None:
        response = _client().get("/import")

        assert response.status_code == 403

    @patch("database.list_activity_import_logs", return_value=[])
    @patch("app.get_current_user", return_value=VIEWER_USER)
    def test_viewer_can_access_search_and_activity(
        self,
        _mock_user: MagicMock,
        _mock_activity_logs: MagicMock,
    ) -> None:
        client = _client()

        search_response = client.get("/")
        activity_response = client.get("/activity")

        assert search_response.status_code == 200
        assert activity_response.status_code == 200

    @patch("app.get_current_user", return_value=VIEWER_USER)
    @patch("app.update_dealer_contact_type")
    @patch("app.get_dealer_by_id")
    def test_viewer_cannot_classify_contacts(
        self,
        mock_get_dealer: MagicMock,
        mock_update_contact_type: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_dealer.return_value = {
            "id": "dealer-1",
            "contact_type": "dealer",
        }
        client = _client()

        response = client.post(
            "/contacts/dealer-1/contact-type",
            data={"contact_type": "client", "filter": "all"},
            follow_redirects=False,
        )

        assert response.status_code == 403
        mock_update_contact_type.assert_not_called()

    @patch("app.get_current_user", return_value=VIEWER_USER)
    @patch("app.create_user")
    def test_viewer_cannot_create_users(
        self,
        mock_create_user: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        response = _client().post(
            "/settings/team/create",
            data={
                "name": "Blocked",
                "email": "blocked@mrvault.local",
                "role": "viewer",
            },
            follow_redirects=False,
        )

        assert response.status_code == 403
        mock_create_user.assert_not_called()

    @patch("app.get_current_user", return_value=TRADER_ONE)
    @patch("app.create_user")
    def test_trader_cannot_create_users(
        self,
        mock_create_user: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        response = _client().post(
            "/settings/team/create",
            data={
                "name": "Blocked",
                "email": "blocked@mrvault.local",
                "role": "trader",
            },
            follow_redirects=False,
        )

        assert response.status_code == 403
        mock_create_user.assert_not_called()
