"""Tests for Sprint 46.5 client request condition field."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_request_edit_form, build_request_row
from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    REQUEST_CONDITION_ANY_LABEL,
    parse_request_condition_form,
    request_condition_display,
    request_condition_form_value,
)
from database import build_request_storage_payload
from match_detail import build_match_detail
from request_matching import match_offer_against_requests
from tests.conftest import ADMIN_USER


def _request(**overrides) -> dict:
    payload = {
        "id": "req-1",
        "client_name": "Yury",
        "brand": "Rolex",
        "reference": "116508",
        "status": "open",
        "max_price": 50000,
        "currency": "USD",
        "created_at": "2026-06-27T12:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def _offer(**overrides) -> dict:
    payload = {
        "brand": "Rolex",
        "reference": "116508",
        "original_price": 45000,
        "original_currency": "USD",
    }
    payload.update(overrides)
    return payload


class TestRequestConditionHelpers:
    def test_parse_request_condition_form_maps_any_to_none(self) -> None:
        assert parse_request_condition_form("") is None
        assert parse_request_condition_form("Any / Unknown") is None
        assert parse_request_condition_form(NEW_CONDITION) == NEW_CONDITION
        assert parse_request_condition_form(PRE_OWNED_CONDITION) == PRE_OWNED_CONDITION

    def test_request_condition_display_shows_any_when_unset(self) -> None:
        assert request_condition_display(None) == REQUEST_CONDITION_ANY_LABEL
        assert request_condition_display(NEW_CONDITION) == NEW_CONDITION

    def test_request_condition_form_value_round_trips_stored_values(self) -> None:
        assert request_condition_form_value(None) == ""
        assert request_condition_form_value(PRE_OWNED_CONDITION) == PRE_OWNED_CONDITION


class TestRequestConditionMatching:
    def test_new_request_does_not_match_pre_owned_offer(self) -> None:
        matches = match_offer_against_requests(
            _offer(condition="Used"),
            [_request(condition=NEW_CONDITION)],
        )
        assert matches == []

    def test_pre_owned_request_does_not_match_new_offer(self) -> None:
        matches = match_offer_against_requests(
            _offer(condition="Unworn"),
            [_request(condition=PRE_OWNED_CONDITION)],
        )
        assert matches == []

    def test_any_request_matches_new_and_pre_owned_offers(self) -> None:
        request = _request(condition=None)
        new_matches = match_offer_against_requests(_offer(condition="Unworn"), [request])
        used_matches = match_offer_against_requests(_offer(condition="Used"), [request])
        assert len(new_matches) == 1
        assert len(used_matches) == 1


class TestRequestConditionForms:
    @patch("app.build_request_rows", return_value=[])
    @patch("app.list_requests", return_value=[])
    def test_new_request_form_renders_condition_dropdown(
        self,
        _mock_list: MagicMock,
        _mock_rows: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/requests")

        assert response.status_code == 200
        assert 'name="condition"' in response.text
        assert "Any / Unknown" in response.text
        assert ">New<" in response.text
        assert "Pre-Owned" in response.text

    @patch("app.get_request")
    def test_edit_form_renders_current_condition(self, mock_get_request: MagicMock) -> None:
        mock_get_request.return_value = _request(condition=PRE_OWNED_CONDITION)

        client = TestClient(app)
        response = client.get("/requests/req-1/edit")

        assert response.status_code == 200
        assert 'name="condition"' in response.text
        assert 'value="Pre-Owned" selected' in response.text

    def test_build_request_edit_form_includes_condition(self) -> None:
        form = build_request_edit_form(_request(condition=NEW_CONDITION))
        assert form["condition"] == NEW_CONDITION

    @patch("app.create_request")
    def test_create_request_stores_condition(self, mock_create: MagicMock) -> None:
        client = TestClient(app)
        response = client.post(
            "/requests",
            data={
                "client_name": "Benny",
                "brand": "Rolex",
                "reference": "116508",
                "condition": NEW_CONDITION,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["condition"] == NEW_CONDITION

    @patch("app.update_request")
    @patch("app.get_request")
    def test_edit_request_updates_condition(
        self,
        mock_get_request: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        mock_get_request.return_value = _request()

        client = TestClient(app)
        response = client.post(
            "/requests/req-1/edit",
            data={
                "client_name": "Yury",
                "brand": "Rolex",
                "reference": "116508",
                "condition": PRE_OWNED_CONDITION,
                "currency": "USD",
                "status": "open",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert mock_update.call_args.kwargs["condition"] == PRE_OWNED_CONDITION

    def test_build_request_storage_payload_normalizes_condition(self) -> None:
        payload = build_request_storage_payload(
            client_name="Cassie",
            condition="Mint",
        )
        assert payload["condition"] == PRE_OWNED_CONDITION


class TestRequestConditionDisplay:
    def test_request_card_shows_condition(self) -> None:
        row = build_request_row(_request(condition=NEW_CONDITION), matches=[], user=ADMIN_USER)
        assert row["condition"] == NEW_CONDITION

    def test_match_detail_shows_request_condition(self) -> None:
        detail = build_match_detail(
            _request(condition=PRE_OWNED_CONDITION),
            {
                "id": "match-1",
                "match_strength": "strong",
                "match_reason": "Reference match: 116508",
                "profit": {},
                "offer": {},
                "watch": {"brand": "Rolex", "reference": "116508"},
                "import_log": {"id": "log-1"},
            },
            user=ADMIN_USER,
        )
        assert detail["request"]["condition"] == PRE_OWNED_CONDITION

    @patch("app.build_request_rows")
    @patch("app.list_requests")
    def test_requests_page_shows_condition_on_card(
        self,
        mock_list_requests: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_requests.return_value = [_request(condition=NEW_CONDITION)]
        mock_build_rows.return_value = [
            {
                "id": "req-1",
                "client_name": "Yury",
                "brand": "Rolex",
                "reference": "116508",
                "model": "—",
                "alias": "—",
                "dial": "—",
                "condition": NEW_CONDITION,
                "year_range": "—",
                "max_price": "—",
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
        assert ">Condition<" in response.text
        assert ">New<" in response.text
