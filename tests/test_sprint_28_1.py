"""Tests for Sprint 28.1 client delete and search."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from contact_classification import (
    CONTACT_TYPE_CLIENT,
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_REMOVED,
    build_contact_rows,
    build_contacts_filter_options,
    filter_contact_rows,
    filter_records_by_contact_search,
    matches_contact_search,
)
from database import (
    ClientDeleteBlockedError,
    client_has_linked_history,
    delete_client_permanently,
)


def _people_rows() -> list[dict]:
    return build_contact_rows(
        [
            {
                "id": "dealer-1",
                "display_name": "HK Dealer",
                "whatsapp_id": "85291234567",
                "phone_number": "85291234567",
                "contact_type": CONTACT_TYPE_DEALER,
            },
            {
                "id": "client-1",
                "display_name": "Anna Buyer",
                "whatsapp_id": "85299998888",
                "phone_number": "85299998888",
                "contact_type": CONTACT_TYPE_CLIENT,
            },
            {
                "id": "removed-1",
                "display_name": "Removed Person",
                "whatsapp_id": "85288887777",
                "phone_number": "85288887777",
                "contact_type": CONTACT_TYPE_REMOVED,
            },
        ]
    )


class TestContactSearchHelpers:
    def test_matches_contact_search_by_name(self) -> None:
        row = {"name": "Anna Buyer", "whatsapp_id": "85299998888", "phone_number": "85299998888"}
        assert matches_contact_search(row, "anna") is True
        assert matches_contact_search(row, "Buyer") is True
        assert matches_contact_search(row, "Other") is False

    def test_matches_contact_search_by_phone(self) -> None:
        row = {"name": "HK Dealer", "whatsapp_id": "85291234567", "phone_number": "85291234567"}
        assert matches_contact_search(row, "91234567") is True
        assert matches_contact_search(row, "+85291234567") is True

    def test_filter_contact_rows_respects_active_filter_and_search(self) -> None:
        rows = _people_rows()

        active_filtered = filter_contact_rows(rows, filter_key="active", search_query="Anna")
        assert [row["name"] for row in active_filtered] == ["Anna Buyer"]

        dealer_filtered = filter_contact_rows(rows, filter_key="dealers", search_query="8529123")
        assert [row["name"] for row in dealer_filtered] == ["HK Dealer"]

        removed_filtered = filter_contact_rows(rows, filter_key="removed", search_query="Removed")
        assert [row["name"] for row in removed_filtered] == ["Removed Person"]

    def test_build_contacts_filter_options_preserves_search_query(self) -> None:
        options = build_contacts_filter_options("clients", "Anna")
        clients_option = next(option for option in options if option["key"] == "clients")
        dealers_option = next(option for option in options if option["key"] == "dealers")

        assert "q=Anna" in clients_option["href"]
        assert "filter=dealers" in dealers_option["href"]
        assert "q=Anna" in dealers_option["href"]


class TestPeoplePageSearch:
    @patch("app.list_contacts")
    def test_people_search_finds_dealer_and_client_by_name(
        self,
        mock_list_contacts: MagicMock,
    ) -> None:
        mock_list_contacts.return_value = [
            {
                "id": "dealer-1",
                "display_name": "HK Dealer",
                "whatsapp_id": "85291234567",
                "phone_number": "85291234567",
                "contact_type": CONTACT_TYPE_DEALER,
            },
            {
                "id": "client-1",
                "display_name": "Anna Buyer",
                "whatsapp_id": "85299998888",
                "phone_number": "85299998888",
                "contact_type": CONTACT_TYPE_CLIENT,
            },
        ]

        client = TestClient(app)
        dealer_response = client.get("/contacts?q=HK+Dealer")
        client_response = client.get("/contacts?q=Anna")

        assert dealer_response.status_code == 200
        assert "HK Dealer" in dealer_response.text
        assert "Anna Buyer" not in dealer_response.text
        assert client_response.status_code == 200
        assert "Anna Buyer" in client_response.text
        assert "HK Dealer" not in client_response.text

    @patch("app.list_contacts")
    def test_people_search_finds_by_phone_and_respects_filter(
        self,
        mock_list_contacts: MagicMock,
    ) -> None:
        mock_list_contacts.return_value = [
            {
                "id": "dealer-1",
                "display_name": "HK Dealer",
                "whatsapp_id": "85291234567",
                "phone_number": "85291234567",
                "contact_type": CONTACT_TYPE_DEALER,
            },
            {
                "id": "client-1",
                "display_name": "Anna Buyer",
                "whatsapp_id": "85299998888",
                "phone_number": "85299998888",
                "contact_type": CONTACT_TYPE_CLIENT,
            },
        ]

        client = TestClient(app)
        phone_response = client.get("/contacts?q=99998888")
        dealer_filter_response = client.get("/contacts?filter=dealers&q=99998888")

        assert phone_response.status_code == 200
        assert "Anna Buyer" in phone_response.text
        assert dealer_filter_response.status_code == 200
        assert "Anna Buyer" not in dealer_filter_response.text


class TestClientsAndDealersSearch:
    @patch("app.build_client_list_rows")
    @patch("app.list_requests", return_value=[])
    @patch("app.list_client_profiles_by_client_ids", return_value={})
    @patch("app.list_clients")
    def test_clients_search_by_name_and_phone(
        self,
        mock_list_clients: MagicMock,
        mock_list_profiles: MagicMock,
        mock_list_requests: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_clients.return_value = [
            {
                "id": "client-1",
                "display_name": "Anna Buyer",
                "whatsapp_id": "85299998888",
                "phone_number": "85299998888",
                "contact_type": CONTACT_TYPE_CLIENT,
            },
            {
                "id": "client-2",
                "display_name": "Other Client",
                "whatsapp_id": "85288887777",
                "phone_number": "85288887777",
                "contact_type": CONTACT_TYPE_CLIENT,
            },
        ]
        mock_build_rows.side_effect = lambda clients, profiles, requests: [
            {"id": client["id"], "name": client["display_name"]} for client in clients
        ]

        client = TestClient(app)
        name_response = client.get("/clients?q=Anna")
        phone_response = client.get("/clients?q=85288887777")

        assert name_response.status_code == 200
        assert mock_build_rows.call_args_list[0].args[0][0]["display_name"] == "Anna Buyer"
        assert phone_response.status_code == 200
        assert mock_build_rows.call_args_list[1].args[0][0]["display_name"] == "Other Client"

    @patch("app.build_dealer_list_rows")
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.list_dealers")
    def test_dealers_search_by_name_and_phone(
        self,
        mock_list_dealers: MagicMock,
        mock_list_offers: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [
            {
                "id": "dealer-1",
                "display_name": "HK Dealer",
                "whatsapp_id": "85291234567",
                "phone_number": "85291234567",
                "contact_type": CONTACT_TYPE_DEALER,
            },
            {
                "id": "dealer-2",
                "display_name": "SG Dealer",
                "whatsapp_id": "6591234567",
                "phone_number": "6591234567",
                "contact_type": CONTACT_TYPE_DEALER,
            },
        ]
        mock_build_rows.side_effect = lambda dealers, offers: [
            {"id": dealer["id"], "name": dealer["display_name"]} for dealer in dealers
        ]

        client = TestClient(app)
        name_response = client.get("/dealers?q=HK")
        phone_response = client.get("/dealers?q=6591234567")

        assert name_response.status_code == 200
        assert mock_build_rows.call_args_list[0].args[0][0]["display_name"] == "HK Dealer"
        assert phone_response.status_code == 200
        assert mock_build_rows.call_args_list[1].args[0][0]["display_name"] == "SG Dealer"


class TestClientHardDelete:
    @patch("database.client_profiles_supported", return_value=True)
    @patch("database.get_client_by_id")
    @patch("database.get_client")
    def test_delete_client_without_history_removes_profile_and_contact(
        self,
        mock_get_client: MagicMock,
        mock_get_client_by_id: MagicMock,
        mock_profiles_supported: MagicMock,
    ) -> None:
        mock_get_client_by_id.return_value = {
            "id": "client-1",
            "display_name": "Anna Buyer",
            "contact_type": CONTACT_TYPE_CLIENT,
        }
        client = MagicMock()
        mock_get_client.return_value = client

        with patch("database.list_requests_for_client", return_value=[]), patch(
            "database.dealer_has_offers",
            return_value=False,
        ), patch("database.client_has_messages", return_value=False):
            delete_client_permanently("client-1", client_name="Anna Buyer")

        client.table.return_value.delete.return_value.eq.assert_any_call("client_id", "client-1")
        client.table.return_value.delete.return_value.eq.assert_any_call("id", "client-1")

    @patch("database.get_client_by_id")
    def test_delete_client_with_linked_history_is_blocked(
        self,
        mock_get_client_by_id: MagicMock,
    ) -> None:
        mock_get_client_by_id.return_value = {
            "id": "client-1",
            "display_name": "Anna Buyer",
            "contact_type": CONTACT_TYPE_CLIENT,
        }

        with patch(
            "database.list_requests_for_client",
            return_value=[{"id": "request-1", "client_name": "Anna Buyer"}],
        ):
            assert client_has_linked_history("client-1", client_name="Anna Buyer") is True
            try:
                delete_client_permanently("client-1", client_name="Anna Buyer")
            except ClientDeleteBlockedError as exc:
                assert "linked history" in exc.message
            else:
                raise AssertionError("Expected ClientDeleteBlockedError")

    @patch("app.delete_client_permanently")
    @patch("app.get_client_by_id")
    def test_client_delete_route_succeeds_without_history(
        self,
        mock_get_client: MagicMock,
        mock_delete: MagicMock,
    ) -> None:
        mock_get_client.return_value = {
            "id": "client-1",
            "display_name": "Anna Buyer",
            "contact_type": CONTACT_TYPE_CLIENT,
        }

        client = TestClient(app)
        response = client.post(
            "/clients/client-1/delete",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/clients?deleted=1"
        mock_delete.assert_called_once()

    @patch("app.delete_client_permanently", side_effect=ClientDeleteBlockedError())
    @patch("app.get_client_by_id")
    def test_client_delete_route_blocks_when_history_exists(
        self,
        mock_get_client: MagicMock,
        mock_delete: MagicMock,
    ) -> None:
        mock_get_client.return_value = {
            "id": "client-1",
            "display_name": "Anna Buyer",
            "contact_type": CONTACT_TYPE_CLIENT,
        }

        client = TestClient(app)
        response = client.post(
            "/clients/client-1/delete",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/clients/client-1?delete_blocked=1"

    @patch("app.get_client_by_id", return_value=None)
    def test_dealer_cannot_be_deleted_through_client_flow(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post(
            "/clients/dealer-1/delete",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 404


class TestFilterRecordsByContactSearch:
    def test_filter_records_by_contact_search_on_raw_dealer_rows(self) -> None:
        dealers = [
            {"display_name": "HK Dealer", "whatsapp_id": "85291234567", "phone_number": "85291234567"},
            {"display_name": "SG Dealer", "whatsapp_id": "6591234567", "phone_number": "6591234567"},
        ]

        filtered = filter_records_by_contact_search(dealers, "6591234567")

        assert len(filtered) == 1
        assert filtered[0]["display_name"] == "SG Dealer"
