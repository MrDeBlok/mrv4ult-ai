"""Tests for Sprint 34 trader dashboard — updated for Trading Desk KPIs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from dashboard_data import build_trading_desk_kpis, load_trading_desk
from tests.conftest import ADMIN_USER, TRADER_ONE

TRADING_DESK_KPI_TITLES = (
    "New offers today",
    "High opportunities",
    "Active market requests",
    "AI needs help",
    "Unread notifications",
)


class TestSprint34DashboardRules:
    def test_build_trading_desk_kpis_include_expected_links(self) -> None:
        cards = build_trading_desk_kpis(
            new_offers_today=2,
            high_opportunities=1,
            active_market_requests=3,
            ai_needs_help=4,
            unread_notifications=6,
        )

        assert [card["title"] for card in cards] == list(TRADING_DESK_KPI_TITLES)
        assert cards[0]["url"] == "/activity"
        assert cards[3]["url"] == "/parser-review"
        assert cards[4]["url"] == "/notifications"

    @patch("dashboard_data.get_messages_by_ids", return_value={})
    @patch(
        "dashboard_data.attach_import_log_summaries",
        side_effect=lambda logs: [{**log, "summary": log.get("summary") or {}} for log in logs],
    )
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("database.list_active_offers_for_market_matching", return_value=[])
    @patch("dashboard_data.list_recent_notifications", return_value=[])
    @patch("dashboard_data.filter_business_import_logs", side_effect=lambda logs, _lookup: logs)
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.filter_imports_for_user")
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[{"id": "shared"}, {"id": "admin-only"}])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    def test_parser_review_count_uses_user_scoped_import_logs(
        self,
        _mock_recent: MagicMock,
        _mock_today: MagicMock,
        _mock_market: MagicMock,
        _mock_parser: MagicMock,
        mock_filter_imports: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_business_filter: MagicMock,
        _mock_notifications: MagicMock,
        _mock_offers: MagicMock,
        _mock_unread: MagicMock,
        _mock_attach: MagicMock,
        _mock_messages: MagicMock,
    ) -> None:
        mock_filter_imports.side_effect = lambda logs, user: (
            [{"id": "shared"}, {"id": "admin-only"}]
            if user == ADMIN_USER
            else [{"id": "shared"}]
        )

        with patch(
            "dashboard_data.parser_review_counts",
            side_effect=lambda logs: {"total": len(logs)},
        ):
            trader_desk = load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value)
            admin_desk = load_trading_desk(ADMIN_USER, format_timestamp=lambda value: value)

        trader_ai_count = next(
            card["count"] for card in trader_desk["kpis"] if card["key"] == "ai_needs_help"
        )
        admin_ai_count = next(
            card["count"] for card in admin_desk["kpis"] if card["key"] == "ai_needs_help"
        )

        assert trader_ai_count == 1
        assert admin_ai_count == 2


class TestSprint34DashboardRoutes:
    @pytest.mark.no_auto_login
    def test_dashboard_requires_login(self) -> None:
        client = TestClient(app)
        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    @patch("app.load_trading_desk")
    def test_admin_can_access_dashboard(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=1,
                high_opportunities=0,
                active_market_requests=2,
                ai_needs_help=1,
                unread_notifications=5,
            ),
            "quick_actions": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Trading Desk" in response.text
        for title in TRADING_DESK_KPI_TITLES:
            assert title in response.text

    @pytest.mark.no_auto_login
    @patch("app.load_trading_desk")
    def test_trader_can_access_dashboard(
        self,
        mock_load_desk: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=0,
                high_opportunities=0,
                active_market_requests=1,
                ai_needs_help=0,
                unread_notifications=4,
            ),
            "quick_actions": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Welcome back, Trader One" in response.text

    @patch("app.load_trading_desk")
    def test_dashboard_shows_expected_cards_with_links(
        self,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=7,
                high_opportunities=0,
                active_market_requests=8,
                ai_needs_help=7,
                unread_notifications=11,
            ),
            "quick_actions": [
                {"key": "parser_review", "label": "Teach AI / Parser Review", "url": "/parser-review", "style": "outline-dark", "visible": True},
                {"key": "notifications", "label": "Notifications", "url": "/notifications", "style": "outline-dark", "visible": True},
            ],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'href="/parser-review"' in response.text
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
