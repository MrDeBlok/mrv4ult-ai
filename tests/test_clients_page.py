"""Tests for client CRM pages and helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from client_intelligence import (
    build_client_list_row,
    build_client_list_rows,
    build_client_profile,
    build_client_wishlist,
    compute_client_stats,
    format_budget_range,
)
from contact_classification import CONTACT_TYPE_CLIENT
from database import update_client_profile


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_28_clients.sql"
)


class TestSprint28MigrationFile:
    def test_migration_creates_client_profiles_and_request_link(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert MIGRATION_PATH.is_file()
        assert "CREATE TABLE IF NOT EXISTS client_profiles" in sql
        assert "ADD COLUMN IF NOT EXISTS client_id" in sql
        assert "budget_min" in sql
        assert "budget_max" in sql


class TestClientIntelligence:
    def test_compute_client_stats_counts_requests_and_last_activity(self) -> None:
        stats = compute_client_stats(
            [
                {
                    "created_at": "2026-06-20T08:00:00+00:00",
                    "updated_at": "2026-06-21T10:00:00+00:00",
                },
                {
                    "created_at": "2026-06-25T12:00:00+00:00",
                    "updated_at": "2026-06-27T12:00:00+00:00",
                },
            ]
        )

        assert stats["request_count"] == 2
        assert stats["last_activity"] == "2026-06-27T12:00:00+00:00"

    def test_build_client_list_row_formats_status_and_counts(self) -> None:
        row = build_client_list_row(
            {"id": "client-1", "display_name": "Anna Buyer"},
            {"status": "active"},
            {"request_count": 3, "last_activity": "2026-06-27T12:00:00+00:00"},
        )

        assert row["name"] == "Anna Buyer"
        assert row["request_count"] == 3
        assert row["status"] == "Active"
        assert row["status_class"] == "success"

    def test_build_client_list_rows_groups_requests_by_client_name(self) -> None:
        rows = build_client_list_rows(
            [{"id": "client-1", "display_name": "Anna Buyer"}],
            {"client-1": {"status": "active"}},
            [
                {"client_name": "Anna Buyer", "created_at": "2026-06-27T12:00:00+00:00"},
                {"client_name": "Someone Else", "created_at": "2026-06-20T08:00:00+00:00"},
            ],
        )

        assert len(rows) == 1
        assert rows[0]["request_count"] == 1

    def test_build_client_wishlist_formats_budget_fields(self) -> None:
        wishlist = build_client_wishlist(
            {
                "preferred_brands": "Rolex, Patek Philippe",
                "preferred_models": "126200",
                "budget_min": 50000,
                "budget_max": 90000,
                "preferred_condition": "new",
                "preferred_dial": "Blue",
            }
        )

        assert wishlist["preferred_brands"] == "Rolex, Patek Philippe"
        assert wishlist["budget_range"] == format_budget_range(50000, 90000)
        assert wishlist["preferred_condition"] == "New"

    def test_build_client_profile_includes_notes_and_dates(self) -> None:
        profile = build_client_profile(
            {
                "id": "client-1",
                "display_name": "Anna Buyer",
                "created_at": "2026-06-01T10:00:00+00:00",
                "updated_at": "2026-06-27T12:00:00+00:00",
            },
            {
                "notes": "Looking for Datejust under 80k",
                "status": "inactive",
                "updated_at": "2026-06-27T12:00:00+00:00",
            },
        )

        assert profile["name"] == "Anna Buyer"
        assert profile["title"] == "Anna Buyer"
        assert profile["notes"] == "Looking for Datejust under 80k"
        assert profile["status"] == "Inactive"


class TestClientsPage:
    @patch("app.build_client_list_rows")
    @patch("app.list_requests", return_value=[])
    @patch("app.list_client_profiles_by_client_ids", return_value={})
    @patch("app.list_clients")
    def test_clients_page_renders_table(
        self,
        mock_list_clients: MagicMock,
        mock_list_profiles: MagicMock,
        mock_list_requests: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_clients.return_value = [
            {"id": "client-1", "display_name": "Anna Buyer", "contact_type": CONTACT_TYPE_CLIENT}
        ]
        mock_build_rows.return_value = [
            {
                "id": "client-1",
                "name": "Anna Buyer",
                "created_at": "2026-06-01 10:00",
                "last_activity": "2026-06-27 12:00",
                "request_count": 2,
                "status": "Active",
                "status_class": "success",
            }
        ]

        client = TestClient(app)
        response = client.get("/clients")

        assert response.status_code == 200
        assert "Clients" in response.text
        assert "Anna Buyer" in response.text
        assert 'data-href="/clients/client-1"' in response.text
        assert 'href="/clients"' in response.text

    @patch("app.build_client_list_rows", return_value=[])
    @patch("app.list_requests", return_value=[])
    @patch("app.list_client_profiles_by_client_ids", return_value={})
    @patch("app.list_clients", return_value=[])
    def test_clients_page_shows_empty_state(
        self,
        mock_list_clients: MagicMock,
        mock_list_profiles: MagicMock,
        mock_list_requests: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/clients")

        assert response.status_code == 200
        assert "No clients found yet." in response.text


class TestClientDetailPage:
    @patch("app.list_active_sourcing_offers", return_value=[])
    @patch("app.build_client_match_rows", return_value=[])
    @patch("app.list_client_match_history", return_value=[])
    @patch("app.build_client_request_rows")
    @patch("app.list_requests_for_client")
    @patch("app.get_client_profile")
    @patch("app.get_client_by_id")
    def test_client_detail_page_renders_profile_and_history(
        self,
        mock_get_client: MagicMock,
        mock_get_profile: MagicMock,
        mock_list_client_requests: MagicMock,
        mock_build_request_rows: MagicMock,
        mock_list_match_history: MagicMock,
        mock_build_match_rows: MagicMock,
        mock_list_offers: MagicMock,
    ) -> None:
        mock_get_client.return_value = {
            "id": "client-1",
            "display_name": "Anna Buyer",
            "contact_type": CONTACT_TYPE_CLIENT,
            "created_at": "2026-06-01T10:00:00+00:00",
            "updated_at": "2026-06-27T12:00:00+00:00",
        }
        mock_get_profile.return_value = {
            "notes": "Prefers blue dials",
            "preferred_brands": "Rolex",
            "preferred_models": "126200",
            "budget_min": 50000,
            "budget_max": 80000,
            "preferred_condition": "new",
            "preferred_dial": "Blue",
            "status": "active",
        }
        mock_list_client_requests.return_value = [
            {
                "id": "request-1",
                "brand": "Rolex",
                "reference": "126200",
                "model": "Datejust",
                "status": "open",
                "max_price": 80000,
                "created_at": "2026-06-27T12:00:00+00:00",
            }
        ]
        mock_build_request_rows.return_value = [
            {
                "brand": "Rolex",
                "reference": "126200",
                "model": "Datejust",
                "status": "open",
                "max_price": "$80,000",
                "created_at": "2026-06-27 12:00",
            }
        ]

        client = TestClient(app)
        response = client.get("/clients/client-1")

        assert response.status_code == 200
        assert "Anna Buyer" in response.text
        assert "Prefers blue dials" in response.text
        assert "126200" in response.text
        assert "Open Requests" in response.text
        assert response.text.index("Open Requests") < response.text.index("Matching Offers")
        assert "Purchased Watches" in response.text

    @patch("app.get_client_by_id", return_value=None)
    def test_client_detail_page_returns_404_for_missing_client(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/clients/missing-client")

        assert response.status_code == 404


class TestClientEditing:
    @patch("app.update_client_profile")
    @patch("app.update_client_name")
    @patch("app.get_client_by_id")
    def test_client_edit_updates_profile_fields(
        self,
        mock_get_client: MagicMock,
        mock_update_name: MagicMock,
        mock_update_profile: MagicMock,
    ) -> None:
        mock_get_client.return_value = {
            "id": "client-1",
            "display_name": "Anna Buyer",
            "contact_type": CONTACT_TYPE_CLIENT,
        }

        client = TestClient(app)
        response = client.post(
            "/clients/client-1/edit",
            data={
                "name": "Anna Updated",
                "notes": "VIP buyer",
                "preferred_brands": "Rolex",
                "preferred_models": "126200",
                "budget_min": "50000",
                "budget_max": "90000",
                "preferred_condition": "new",
                "preferred_dial": "Blue",
                "status": "inactive",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/clients/client-1?saved=1"
        mock_update_name.assert_called_once_with("client-1", "Anna Updated")
        mock_update_profile.assert_called_once_with(
            "client-1",
            notes="VIP buyer",
            preferred_brands="Rolex",
            preferred_models="126200",
            budget_min=50000,
            budget_max=90000,
            preferred_condition="new",
            preferred_dial="Blue",
            status="inactive",
        )

    @patch("database.client_profiles_supported", return_value=True)
    @patch("database.get_client")
    def test_update_client_profile_persists_wishlist_fields(
        self,
        mock_get_client: MagicMock,
        mock_profiles_supported: MagicMock,
    ) -> None:
        client = MagicMock()
        mock_get_client.return_value = client

        select_execute = MagicMock()
        select_execute.data = [{"client_id": "client-1", "status": "active"}]
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = select_execute

        update_execute = MagicMock()
        update_execute.data = [
            {
                "client_id": "client-1",
                "preferred_brands": "Rolex",
                "preferred_models": "126200",
                "budget_min": 50000,
                "budget_max": 90000,
                "preferred_condition": "new",
                "preferred_dial": "Blue",
                "status": "active",
            }
        ]
        client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_execute

        profile = update_client_profile(
            "client-1",
            preferred_brands="Rolex",
            preferred_models="126200",
            budget_min=50000,
            budget_max=90000,
            preferred_condition="new",
            preferred_dial="Blue",
        )

        assert profile["budget_min"] == 50000
        assert profile["budget_max"] == 90000
        assert profile["preferred_brands"] == "Rolex"

    @patch("app.create_client_contact")
    def test_clients_create_adds_new_client(
        self,
        mock_create_client: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post(
            "/clients",
            data={"name": "New Buyer"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/clients?saved=1"
        mock_create_client.assert_called_once_with(name="New Buyer")


class TestClientDealerSeparation:
    @patch("app.build_dealer_list_rows")
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.list_dealers", return_value=[])
    def test_clients_do_not_appear_on_dealers_page(
        self,
        mock_list_dealers: MagicMock,
        mock_list_offers: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_build_rows.return_value = []

        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert "Anna Buyer" not in response.text
        mock_list_dealers.assert_called_once()

    @patch("database.get_client")
    def test_create_request_accepts_optional_client_id(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        from database import create_request

        client = MagicMock()
        mock_get_client.return_value = client
        insert_execute = MagicMock()
        insert_execute.data = [{"id": "request-1", "client_name": "Anna Buyer", "client_id": "client-1"}]
        client.table.return_value.insert.return_value.execute.return_value = insert_execute

        created = create_request(client_name="Anna Buyer", client_id="client-1")

        assert created["client_id"] == "client-1"
        insert_payload = client.table.return_value.insert.call_args.args[0]
        assert insert_payload["client_id"] == "client-1"
