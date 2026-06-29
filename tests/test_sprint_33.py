"""Tests for Sprint 33 user accounts and private contacts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import app
from auth import authenticate_email, get_current_user, login_user, logout_user
from contact_classification import CONTACT_TYPE_CLIENT, CONTACT_TYPE_DEALER, CONTACT_TYPE_REMOVED
from tests.conftest import ADMIN_USER, TRADER_ONE, TRADER_TWO
from user_visibility import (
    can_view_contact,
    can_view_import,
    filter_contacts_for_user,
    filter_imports_for_user,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_33_users_private_contacts.sql"
)


class TestSprint33MigrationFile:
    def test_migration_creates_users_and_ownership_columns(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert MIGRATION_PATH.is_file()
        assert "CREATE TABLE IF NOT EXISTS users" in sql
        assert "imported_by_user_id" in sql
        assert "owner_user_id" in sql
        assert "classified_by_user_id" in sql
        assert "'admin'" in sql
        assert "'trader'" in sql


class TestAuthHelpers:
    @pytest.mark.no_auto_login
    def test_login_and_logout_update_session(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "session": {},
        }
        request = Request(scope)

        with patch("auth.authenticate_email", return_value=TRADER_ONE):
            login_user(request, TRADER_ONE)

        with patch("database.get_user_by_id", return_value=TRADER_ONE):
            with patch("database.users_table_supported", return_value=True):
                assert get_current_user(request) == TRADER_ONE

        logout_user(request)
        assert get_current_user(request) is None

    @patch("database.get_user_by_email")
    @patch("database.users_table_supported", return_value=True)
    def test_authenticate_email_looks_up_user(
        self,
        _mock_supported: MagicMock,
        mock_get_user: MagicMock,
    ) -> None:
        mock_get_user.return_value = TRADER_ONE

        assert authenticate_email("trader1@mrvault.local") == TRADER_ONE
        mock_get_user.assert_called_once_with("trader1@mrvault.local")


class TestUserVisibilityRules:
    def test_dealer_and_client_contacts_visible_to_all_users(self) -> None:
        dealer = {"id": "d-1", "contact_type": CONTACT_TYPE_DEALER}
        client = {"id": "c-1", "contact_type": CONTACT_TYPE_CLIENT}

        assert can_view_contact(TRADER_ONE, dealer) is True
        assert can_view_contact(TRADER_TWO, client) is True

    def test_removed_contact_visible_only_to_owner_or_admin(self) -> None:
        removed = {
            "id": "r-1",
            "contact_type": CONTACT_TYPE_REMOVED,
            "owner_user_id": TRADER_ONE["id"],
        }
        orphan_removed = {
            "id": "r-orphan",
            "contact_type": CONTACT_TYPE_REMOVED,
        }

        assert can_view_contact(TRADER_ONE, removed) is True
        assert can_view_contact(TRADER_TWO, removed) is False
        assert can_view_contact(ADMIN_USER, removed) is True
        assert can_view_contact(TRADER_ONE, orphan_removed) is False
        assert can_view_contact(ADMIN_USER, orphan_removed) is True

    def test_shared_business_import_visible_to_all_users(self) -> None:
        shared = {
            "id": "log-shared",
            "status": "success",
            "watches_parsed": 1,
            "imported_by_user_id": TRADER_ONE["id"],
        }

        assert can_view_import(TRADER_ONE, shared) is True
        assert can_view_import(TRADER_TWO, shared) is True

    def test_private_import_visible_only_to_owner_or_admin(self) -> None:
        noise = {
            "id": "log-noise",
            "status": "noise",
            "watches_parsed": 0,
            "imported_by_user_id": TRADER_ONE["id"],
        }
        request_intent = {
            "id": "log-request",
            "status": "request_intent",
            "watches_parsed": 0,
            "imported_by_user_id": TRADER_TWO["id"],
        }

        assert can_view_import(TRADER_ONE, noise) is True
        assert can_view_import(TRADER_TWO, noise) is False
        assert can_view_import(ADMIN_USER, noise) is True
        assert can_view_import(TRADER_ONE, request_intent) is False
        assert can_view_import(TRADER_TWO, request_intent) is True

    def test_filter_helpers_hide_other_users_private_records(self) -> None:
        contacts = [
            {"id": "1", "contact_type": CONTACT_TYPE_DEALER},
            {"id": "2", "contact_type": CONTACT_TYPE_REMOVED, "owner_user_id": TRADER_ONE["id"]},
            {"id": "3", "contact_type": CONTACT_TYPE_REMOVED, "owner_user_id": TRADER_TWO["id"]},
        ]
        imports = [
            {"id": "a", "status": "success", "watches_parsed": 1, "imported_by_user_id": TRADER_ONE["id"]},
            {"id": "b", "status": "noise", "watches_parsed": 0, "imported_by_user_id": TRADER_TWO["id"]},
        ]

        trader_one_contacts = filter_contacts_for_user(contacts, TRADER_ONE)
        trader_one_imports = filter_imports_for_user(imports, TRADER_ONE)

        assert [row["id"] for row in trader_one_contacts] == ["1", "2"]
        assert [row["id"] for row in trader_one_imports] == ["a"]


class TestSprint33Routes:
    @pytest.mark.no_auto_login
    @patch("database.get_user_by_id")
    @patch("database.get_user_by_email")
    @patch("database.users_table_supported", return_value=True)
    def test_login_and_logout_routes(
        self,
        _mock_supported: MagicMock,
        mock_get_user: MagicMock,
        mock_get_user_by_id: MagicMock,
    ) -> None:
        mock_get_user.return_value = TRADER_ONE
        mock_get_user_by_id.return_value = TRADER_ONE
        client = TestClient(app)

        protected = client.get("/", follow_redirects=False)
        assert protected.status_code == 303
        assert protected.headers["location"] == "/login"

        login = client.post("/login", data={"email": TRADER_ONE["email"]}, follow_redirects=False)
        assert login.status_code == 303
        assert login.headers["location"] == "/dashboard"

        home = client.get("/dashboard")
        assert home.status_code == 200

        logout = client.post("/logout", follow_redirects=False)
        assert logout.status_code == 303
        assert logout.headers["location"] == "/login"

    @patch("app.list_users")
    def test_users_page_requires_admin(self, mock_list_users: MagicMock) -> None:
        mock_list_users.return_value = [ADMIN_USER, TRADER_ONE]
        client = TestClient(app)

        admin_response = client.get("/settings/team")
        assert admin_response.status_code == 200
        assert "Admin User" in admin_response.text

    @patch("app.get_current_user", return_value=TRADER_ONE)
    @patch("app.list_users")
    def test_users_page_blocks_trader(
        self,
        mock_list_users: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/settings/team")

        assert response.status_code == 403
        mock_list_users.assert_not_called()

    @patch("app.list_contacts")
    def test_contacts_page_hides_other_users_removed_contacts(
        self,
        mock_list_contacts: MagicMock,
    ) -> None:
        mock_list_contacts.return_value = [
            {"id": "d-1", "display_name": "Shared Dealer", "whatsapp_id": "+1", "contact_type": "dealer"},
            {
                "id": "r-1",
                "display_name": "Removed A",
                "whatsapp_id": "+2",
                "contact_type": "removed",
                "owner_user_id": TRADER_ONE["id"],
            },
            {
                "id": "r-2",
                "display_name": "Removed B",
                "whatsapp_id": "+3",
                "contact_type": "removed",
                "owner_user_id": TRADER_TWO["id"],
            },
        ]

        with patch("app.get_current_user", return_value=TRADER_ONE):
            client = TestClient(app)
            response = client.get("/contacts?filter=removed")

        assert response.status_code == 200
        assert "Removed A" in response.text
        assert "Removed B" not in response.text

    @patch("app.list_import_logs")
    def test_activity_hides_other_users_private_imports(
        self,
        mock_list_import_logs: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            {
                "id": "shared",
                "status": "success",
                "watches_parsed": 1,
                "new_offers": 1,
                "imported_by_user_id": TRADER_ONE["id"],
                "import_time": "2026-06-27T12:00:00+00:00",
                "group_name": "HK",
                "dealer_alias": "Dealer",
                "dealer_whatsapp": "+85291234567",
                "duplicate_offers": 0,
                "matched_requests": 0,
                "processing_time": "10 ms",
            },
            {
                "id": "noise",
                "status": "noise",
                "watches_parsed": 0,
                "new_offers": 0,
                "imported_by_user_id": TRADER_TWO["id"],
                "import_time": "2026-06-27T12:00:00+00:00",
                "group_name": "HK",
                "dealer_alias": None,
                "dealer_whatsapp": "",
                "duplicate_offers": 0,
                "matched_requests": 0,
                "processing_time": "10 ms",
            },
        ]

        with patch("app.get_current_user", return_value=TRADER_ONE):
            client = TestClient(app)
            response = client.get("/activity/ignored")

        assert response.status_code == 200
        assert 'data-href="/activity/noise"' not in response.text

    @patch("app.list_import_logs")
    def test_admin_can_see_all_private_imports(
        self,
        mock_list_import_logs: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            {
                "id": "noise",
                "status": "noise",
                "watches_parsed": 0,
                "new_offers": 0,
                "imported_by_user_id": TRADER_TWO["id"],
                "import_time": "2026-06-27T12:00:00+00:00",
                "group_name": "HK",
                "dealer_alias": None,
                "dealer_whatsapp": "",
                "duplicate_offers": 0,
                "matched_requests": 0,
                "processing_time": "10 ms",
            },
        ]

        client = TestClient(app)
        response = client.get("/activity/ignored")

        assert response.status_code == 200
        assert 'data-href="/activity/noise"' in response.text
