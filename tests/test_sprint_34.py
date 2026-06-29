"""Tests for Sprint 34 trader dashboard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from dashboard import build_dashboard_cards, load_dashboard_cards
from tests.conftest import ADMIN_USER, TRADER_ONE


DASHBOARD_CARD_TITLES = (
    "Parser Reviews",
    "Active Requests",
    "Clients",
    "Dealers",
    "Notifications",
)


class TestSprint34DashboardRules:
    def test_build_dashboard_cards_include_expected_links(self) -> None:
        cards = build_dashboard_cards(
            parser_review_count=2,
            active_requests_count=3,
            clients_count=4,
            dealers_count=5,
            notifications_count=6,
        )

        assert [card["title"] for card in cards] == list(DASHBOARD_CARD_TITLES)
        assert cards[0]["url"] == "/parser-review"
        assert cards[1]["url"] == "/requests?status=open"
        assert cards[2]["url"] == "/clients"
        assert cards[3]["url"] == "/dealers"
        assert cards[4]["url"] == "/notifications"

    @patch("dashboard.get_unread_notification_count", return_value=0)
    @patch("dashboard.list_dealers", return_value=[{"id": "d-1"}])
    @patch("dashboard.list_clients", return_value=[{"id": "c-1"}, {"id": "c-2"}])
    @patch("dashboard.list_requests", return_value=[{"id": "r-1", "status": "open"}])
    @patch("dashboard.parser_review_import_logs_for_user")
    def test_parser_review_count_uses_user_scoped_import_logs(
        self,
        mock_parser_logs: MagicMock,
        _mock_list_requests: MagicMock,
        _mock_list_clients: MagicMock,
        _mock_list_dealers: MagicMock,
        _mock_unread: MagicMock,
    ) -> None:
        mock_parser_logs.side_effect = lambda user: (
            [{"id": "shared"}, {"id": "admin-only"}]
            if user == ADMIN_USER
            else [{"id": "shared"}]
        )

        with patch(
            "dashboard.parser_review_counts",
            side_effect=lambda logs: {"total": len(logs)},
        ):
            trader_cards = load_dashboard_cards(TRADER_ONE)
            admin_cards = load_dashboard_cards(ADMIN_USER)

        trader_parser_count = next(
            card["count"] for card in trader_cards if card["key"] == "parser_reviews"
        )
        admin_parser_count = next(
            card["count"] for card in admin_cards if card["key"] == "parser_reviews"
        )

        assert trader_parser_count == 1
        assert admin_parser_count == 2
        assert mock_parser_logs.call_args_list[0].args[0] == TRADER_ONE
        assert mock_parser_logs.call_args_list[1].args[0] == ADMIN_USER


class TestSprint34DashboardRoutes:
    @pytest.mark.no_auto_login
    def test_dashboard_requires_login(self) -> None:
        client = TestClient(app)
        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    @patch("app.load_dashboard_cards")
    def test_admin_can_access_dashboard(self, mock_load_cards: MagicMock) -> None:
        mock_load_cards.return_value = build_dashboard_cards(
            parser_review_count=1,
            active_requests_count=2,
            clients_count=3,
            dealers_count=4,
            notifications_count=5,
        )

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Dashboard" in response.text
        for title in DASHBOARD_CARD_TITLES:
            assert title in response.text

    @pytest.mark.no_auto_login
    @patch("app.load_dashboard_cards")
    def test_trader_can_access_dashboard(
        self,
        mock_load_cards: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_load_cards.return_value = build_dashboard_cards(
            parser_review_count=0,
            active_requests_count=1,
            clients_count=2,
            dealers_count=3,
            notifications_count=4,
        )
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Welcome back, Trader One" in response.text

    @patch("app.load_dashboard_cards")
    def test_dashboard_shows_expected_cards_with_links(
        self,
        mock_load_cards: MagicMock,
    ) -> None:
        mock_load_cards.return_value = build_dashboard_cards(
            parser_review_count=7,
            active_requests_count=8,
            clients_count=9,
            dealers_count=10,
            notifications_count=11,
        )

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'href="/parser-review"' in response.text
        assert 'href="/requests?status=open"' in response.text
        assert 'href="/clients"' in response.text
        assert 'href="/dealers"' in response.text
        assert 'href="/notifications"' in response.text
        assert ">7<" in response.text
        assert ">11<" in response.text

    @pytest.mark.no_auto_login
    @patch("database.get_user_by_id")
    @patch("database.get_user_by_email")
    @patch("database.users_table_supported", return_value=True)
    def test_login_redirects_to_dashboard(
        self,
        _mock_supported: MagicMock,
        mock_get_user: MagicMock,
        mock_get_user_by_id: MagicMock,
    ) -> None:
        mock_get_user.return_value = TRADER_ONE
        mock_get_user_by_id.return_value = TRADER_ONE
        client = TestClient(app)

        login = client.post("/login", data={"email": TRADER_ONE["email"]}, follow_redirects=False)

        assert login.status_code == 303
        assert login.headers["location"] == "/dashboard"

        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200
