"""Tests for Sprint 33.1 People page visibility by logged-in user."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from tests.conftest import ADMIN_USER, TRADER_ONE
from user_visibility import can_view_contact


ADMIN_REMOVED_CONTACT = {
    "id": "removed-admin",
    "display_name": "Admin Removed Contact",
    "whatsapp_id": "+441234567890",
    "phone_number": "+441234567890",
    "contact_type": "removed",
    "owner_user_id": ADMIN_USER["id"],
    "classified_by_user_id": ADMIN_USER["id"],
}

SHARED_CONTACTS = [
    {"id": "dealer-1", "display_name": "Shared Dealer", "whatsapp_id": "+1", "contact_type": "dealer"},
    {"id": "client-1", "display_name": "Shared Client", "whatsapp_id": "+2", "contact_type": "client"},
    ADMIN_REMOVED_CONTACT,
    {
        "id": "removed-trader1",
        "display_name": "Trader One Removed",
        "whatsapp_id": "+3",
        "contact_type": "removed",
        "owner_user_id": TRADER_ONE["id"],
    },
]


class TestSprint331ContactVisibilityRules:
    def test_trader_cannot_view_admin_removed_contact(self) -> None:
        assert can_view_contact(TRADER_ONE, ADMIN_REMOVED_CONTACT) is False

    def test_admin_can_view_admin_removed_contact(self) -> None:
        assert can_view_contact(ADMIN_USER, ADMIN_REMOVED_CONTACT) is True

    def test_removed_contact_without_owner_is_admin_only(self) -> None:
        orphan_removed = {
            "id": "orphan",
            "contact_type": "removed",
            "display_name": "Orphan Removed",
            "whatsapp_id": "+9",
        }

        assert can_view_contact(TRADER_ONE, orphan_removed) is False
        assert can_view_contact(ADMIN_USER, orphan_removed) is True


class TestSprint331ContactsPageIntegration:
    @patch("app.list_contacts")
    def test_trader_cannot_see_admin_removed_contact_on_any_people_tab(
        self,
        mock_list_contacts: MagicMock,
    ) -> None:
        mock_list_contacts.return_value = SHARED_CONTACTS

        with patch("app.get_current_user", return_value=TRADER_ONE):
            client = TestClient(app)
            for path in ("/contacts", "/contacts?filter=all", "/contacts?filter=removed"):
                response = client.get(path)

                assert response.status_code == 200
                assert "Admin Removed Contact" not in response.text

            active_response = client.get("/contacts")
            all_response = client.get("/contacts?filter=all")
            removed_response = client.get("/contacts?filter=removed")

        assert "Shared Dealer" in active_response.text
        assert "Shared Client" in active_response.text
        assert "Shared Dealer" in all_response.text
        assert "Shared Client" in all_response.text
        assert "Trader One Removed" in removed_response.text
        assert "Admin Removed Contact" not in removed_response.text

    @patch("app.list_contacts")
    def test_admin_can_see_admin_removed_contact_on_people_tabs(
        self,
        mock_list_contacts: MagicMock,
    ) -> None:
        mock_list_contacts.return_value = SHARED_CONTACTS

        client = TestClient(app)
        removed_response = client.get("/contacts?filter=removed")
        active_response = client.get("/contacts")

        assert removed_response.status_code == 200
        assert active_response.status_code == 200
        assert "Admin Removed Contact" in removed_response.text
        assert "Shared Dealer" in active_response.text
        assert "Shared Client" in active_response.text
