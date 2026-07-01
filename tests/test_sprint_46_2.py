"""Tests for Sprint 46.2 client request edit and delete."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_request_edit_form, build_request_row
from database import build_request_storage_payload, delete_request, update_request
from tests.conftest import ADMIN_USER, TRADER_ONE, TRADER_TWO
from user_visibility import can_manage_request


def _request(
    *,
    request_id: str = "req-1",
    owner_id: str | None = TRADER_ONE["id"],
    status: str = "open",
) -> dict:
    return {
        "id": request_id,
        "client_name": "Anna Buyer",
        "brand": "Rolex",
        "reference": "116508",
        "model": "Daytona",
        "alias": "Panda",
        "dial": "White",
        "min_year": 2020,
        "max_year": 2024,
        "max_price": 50000,
        "currency": "USD",
        "notes": "Full set preferred",
        "status": status,
        "created_by_user_id": owner_id,
        "created_at": "2026-06-27T12:00:00+00:00",
    }


class TestRequestPermissions:
    def test_owner_trader_can_manage_request(self) -> None:
        assert can_manage_request(TRADER_ONE, _request(owner_id=TRADER_ONE["id"])) is True

    def test_other_trader_cannot_manage_owned_request(self) -> None:
        assert can_manage_request(TRADER_TWO, _request(owner_id=TRADER_ONE["id"])) is False

    def test_admin_can_manage_any_request(self) -> None:
        assert can_manage_request(ADMIN_USER, _request(owner_id=TRADER_ONE["id"])) is True

    def test_legacy_request_without_owner_is_manageable_by_trader(self) -> None:
        assert can_manage_request(TRADER_TWO, _request(owner_id=None)) is True


class TestRequestRowActions:
    def test_build_request_row_exposes_manage_actions_for_owner(self) -> None:
        row = build_request_row(_request(), matches=[], user=TRADER_ONE)
        assert row["can_manage"] is True

    def test_build_request_row_hides_manage_actions_for_other_trader(self) -> None:
        row = build_request_row(_request(), matches=[], user=TRADER_TWO)
        assert row["can_manage"] is False


class TestRequestEditForm:
    def test_build_request_edit_form_loads_existing_values(self) -> None:
        form = build_request_edit_form(_request())

        assert form["client_name"] == "Anna Buyer"
        assert form["reference"] == "116508"
        assert form["max_price"] == 50000
        assert form["status"] == "open"


class TestRequestStorage:
    def test_build_request_storage_payload_normalizes_fields(self) -> None:
        payload = build_request_storage_payload(
            client_name=" Anna ",
            brand="rolex",
            reference="116508",
            status="matched",
        )

        assert payload["client_name"] == "Anna"
        assert payload["brand"] == "rolex"
        assert payload["status"] == "matched"


class TestRequestRoutes:
    @patch("app.build_request_rows")
    @patch("app.list_requests")
    def test_edit_and_delete_buttons_render_for_manageable_request(
        self,
        mock_list_requests: MagicMock,
        mock_build_request_rows: MagicMock,
    ) -> None:
        mock_list_requests.return_value = [_request()]
        mock_build_request_rows.return_value = [
            {
                "id": "req-1",
                "client_name": "Anna Buyer",
                "brand": "Rolex",
                "reference": "116508",
                "model": "Daytona",
                "alias": "Panda",
                "dial": "White",
                "year_range": "2020–2024",
                "max_price": "$50,000",
                "notes": "",
                "status": "Open",
                "status_class": "primary",
                "created_at": "Jun 27, 2026",
                "has_matches": False,
                "best_offer": "—",
                "best_potential_profit": "—",
                "best_margin": "—",
                "match_count": 0,
                "matched_offers": [],
                "can_manage": True,
            }
        ]

        client = TestClient(app)
        response = client.get("/requests")

        assert response.status_code == 200
        assert 'href="/requests/req-1/edit"' in response.text
        assert "Edit" in response.text
        assert "Delete" in response.text
        assert "Mark closed" in response.text

    @patch("app.get_request")
    def test_edit_page_loads_existing_values(self, mock_get_request: MagicMock) -> None:
        mock_get_request.return_value = _request()

        client = TestClient(app)
        response = client.get("/requests/req-1/edit")

        assert response.status_code == 200
        assert 'value="Anna Buyer"' in response.text
        assert 'value="116508"' in response.text
        assert 'value="50000"' in response.text

    @patch("app.update_request")
    @patch("app.get_request")
    def test_post_edit_updates_request_fields(
        self,
        mock_get_request: MagicMock,
        mock_update_request: MagicMock,
    ) -> None:
        mock_get_request.return_value = _request()

        client = TestClient(app)
        response = client.post(
            "/requests/req-1/edit",
            data={
                "client_name": "Anna Buyer",
                "brand": "Rolex",
                "reference": "126500LN",
                "model": "Daytona",
                "alias": "",
                "dial": "Black",
                "min_year": "2021",
                "max_year": "2024",
                "max_price": "48000",
                "currency": "USD",
                "notes": "Updated note",
                "status": "matched",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/requests?updated=1"
        mock_update_request.assert_called_once()
        assert mock_update_request.call_args.kwargs["reference"] == "126500LN"
        assert mock_update_request.call_args.kwargs["status"] == "matched"

    @patch("app.delete_request")
    @patch("app.get_request")
    def test_post_delete_removes_request(
        self,
        mock_get_request: MagicMock,
        mock_delete_request: MagicMock,
    ) -> None:
        mock_get_request.return_value = _request()

        client = TestClient(app)
        response = client.post("/requests/req-1/delete", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/requests?deleted=1"
        mock_delete_request.assert_called_once_with("req-1")

    @patch("app.get_current_user", return_value=TRADER_TWO)
    @patch("app.get_request")
    def test_unauthorized_user_cannot_edit_owned_request(
        self,
        mock_get_request: MagicMock,
        _mock_current_user: MagicMock,
    ) -> None:
        mock_get_request.return_value = _request(owner_id=TRADER_ONE["id"])

        client = TestClient(app)
        response = client.get("/requests/req-1/edit")

        assert response.status_code == 403

    @patch("app.get_current_user", return_value=TRADER_TWO)
    @patch("app.get_request")
    def test_unauthorized_user_cannot_delete_owned_request(
        self,
        mock_get_request: MagicMock,
        _mock_current_user: MagicMock,
    ) -> None:
        mock_get_request.return_value = _request(owner_id=TRADER_ONE["id"])

        client = TestClient(app)
        response = client.post("/requests/req-1/delete")

        assert response.status_code == 403

    @patch("app.update_request_status")
    @patch("app.get_request")
    def test_mark_closed_still_works_for_owner(
        self,
        mock_get_request: MagicMock,
        mock_update_status: MagicMock,
    ) -> None:
        mock_get_request.return_value = _request(owner_id=TRADER_ONE["id"])

        client = TestClient(app)
        response = client.post("/requests/req-1/close", follow_redirects=False)

        assert response.status_code == 303
        mock_update_status.assert_called_once_with("req-1", "closed")

    @patch("app.build_request_rows")
    @patch("app.list_requests")
    def test_status_filters_still_work(
        self,
        mock_list_requests: MagicMock,
        mock_build_request_rows: MagicMock,
    ) -> None:
        mock_list_requests.return_value = [
            _request(request_id="req-open", status="open"),
            _request(request_id="req-closed", status="closed"),
        ]
        mock_build_request_rows.return_value = [
            {"id": "req-open", "client_name": "Anna Buyer", "status": "Open", "can_manage": True},
            {"id": "req-closed", "client_name": "Anna Buyer", "status": "Closed", "can_manage": True},
        ]

        client = TestClient(app)
        response = client.get("/requests?status=closed")

        assert response.status_code == 200
        mock_build_request_rows.assert_called_once()
        assert "Closed" in response.text


class TestRequestDatabaseMutations:
    @patch("database.get_client")
    def test_update_request_writes_normalized_payload(self, mock_get_client: MagicMock) -> None:
        mock_execute = MagicMock()
        mock_execute.data = [{"id": "req-1"}]
        mock_eq = MagicMock()
        mock_eq.execute.return_value = mock_execute
        mock_update = MagicMock()
        mock_update.eq.return_value = mock_eq
        mock_table = MagicMock()
        mock_table.update.return_value = mock_update
        mock_get_client.return_value.table.return_value = mock_table

        update_request(
            "req-1",
            client_name="Anna Buyer",
            brand="Rolex",
            reference="116508",
            status="closed",
        )

        payload = mock_table.update.call_args.args[0]
        assert payload["client_name"] == "Anna Buyer"
        assert payload["status"] == "closed"

    @patch("database.get_client")
    def test_delete_request_hard_deletes_row(self, mock_get_client: MagicMock) -> None:
        mock_eq = MagicMock()
        mock_delete = MagicMock()
        mock_delete.eq.return_value = mock_eq
        mock_table = MagicMock()
        mock_table.delete.return_value = mock_delete
        mock_get_client.return_value.table.return_value = mock_table

        delete_request("req-1")

        mock_table.delete.assert_called_once()
        mock_delete.eq.assert_called_once_with("id", "req-1")
