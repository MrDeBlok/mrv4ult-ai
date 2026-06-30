"""Tests for Sprint 42.0 Trading Desk dashboard."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import app
from dashboard_data import (
    build_trading_desk_kpis,
    is_import_today,
    load_trading_desk,
)
from tests.conftest import TRADER_ONE, VIEWER_USER

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
TODAY_NOON = datetime(2026, 6, 27, 12, 0, tzinfo=AMSTERDAM)


def _desk_payload(**overrides) -> dict:
    payload = {
        "kpis": build_trading_desk_kpis(
            new_offers_today=4,
            high_opportunities=0,
            active_market_requests=2,
            ai_needs_help=1,
            unread_notifications=3,
        ),
        "quick_actions": [
            {"key": "search", "label": "Search", "url": "/", "style": "primary", "visible": True},
            {"key": "import", "label": "Import", "url": "/import", "style": "outline-dark", "visible": True},
        ],
        "top_opportunities": [],
        "ai_needs_help": [
            {
                "reason": "Important fields are missing — watch 1: missing price",
                "message_preview": "Rolex 126200",
                "group_name": "HK Dealers",
                "dealer": "Dealer A",
                "review_url": "/activity/log-1",
                "review_label": "Open import detail",
            }
        ],
        "live_market": [
            {
                "import_time": "2026-06-27 12:00",
                "status": "Success",
                "status_class": "success",
                "group_name": "HK Dealers",
                "dealer": "Dealer A",
                "message_preview": "ROLEX 126200 green jub",
                "detail_url": "/activity/log-1",
            }
        ],
        "show_write_actions": True,
    }
    payload.update(overrides)
    return payload


class TestTradingDeskData:
    def test_new_offers_today_uses_amsterdam_calendar_day(self) -> None:
        today_log = {
            "import_time": "2026-06-27T10:00:00+00:00",
            "new_offers": 3,
        }
        yesterday_log = {
            "import_time": "2026-06-26T20:00:00+00:00",
            "new_offers": 9,
        }

        assert is_import_today(today_log, now=TODAY_NOON) is True
        assert is_import_today(yesterday_log, now=TODAY_NOON) is False

    def test_kpi_cards_include_expected_links(self) -> None:
        cards = build_trading_desk_kpis(
            new_offers_today=1,
            high_opportunities=2,
            active_market_requests=3,
            ai_needs_help=4,
            unread_notifications=5,
        )

        assert [card["title"] for card in cards] == [
            "New offers today",
            "High opportunities",
            "Active market requests",
            "AI needs help",
            "Unread notifications",
        ]
        assert cards[0]["url"] == "/activity"
        assert cards[3]["url"] == "/parser-review"


class TestTradingDeskPage:
    @patch("app.load_trading_desk")
    def test_dashboard_renders_trading_desk_heading(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = _desk_payload()

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Trading Desk" in response.text
        assert "Your daily overview of offers, requests, opportunities and AI tasks." in response.text

    @patch("app.load_trading_desk")
    def test_kpi_cards_render_with_new_offers_today_count(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = _desk_payload()

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "New offers today" in response.text
        assert "High opportunities" in response.text
        assert "AI needs help" in response.text
        assert ">4<" in response.text

    @patch("app.load_trading_desk")
    def test_ai_needs_help_section_renders(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = _desk_payload()

        client = TestClient(app)
        response = client.get("/dashboard")

        assert "AI needs help" in response.text
        assert "missing price" in response.text
        assert 'href="/activity/log-1"' in response.text

    @patch("app.load_trading_desk")
    def test_top_opportunities_empty_state_renders(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = _desk_payload(top_opportunities=[])

        client = TestClient(app)
        response = client.get("/dashboard")

        assert "Top opportunities" in response.text
        assert "No high opportunities yet." in response.text

    @patch("app.load_trading_desk")
    def test_live_market_section_renders(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = _desk_payload()

        client = TestClient(app)
        response = client.get("/dashboard")

        assert "Live market" in response.text
        assert "ROLEX 126200 green jub" in response.text

    @pytest.mark.no_auto_login
    @patch("app.load_trading_desk")
    def test_viewer_dashboard_hides_write_actions(
        self,
        mock_load_desk: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_load_desk.return_value = _desk_payload(
            quick_actions=[
                {"key": "search", "label": "Search", "url": "/", "style": "primary", "visible": True},
            ],
            show_write_actions=False,
        )
        monkeypatch.setattr("app.get_current_user", lambda _request: VIEWER_USER)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Teach AI / Parser Review" not in response.text
        assert 'href="/import"' not in response.text
        assert 'href="/"' in response.text

    @pytest.mark.no_auto_login
    def test_dashboard_requires_login(self) -> None:
        client = TestClient(app)
        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    @patch("dashboard_data.filter_business_import_logs", side_effect=lambda logs, _lookup: logs)
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.list_recent_notifications", return_value=[])
    @patch("database.list_active_offers_for_market_matching", return_value=[])
    @patch("dashboard_data.list_import_logs", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.parser_review_import_logs_for_user", return_value=[])
    def test_existing_dashboard_route_still_works(
        self,
        _mock_parser_logs: MagicMock,
        _mock_unread: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offers: MagicMock,
        _mock_notifications: MagicMock,
        _mock_business_filter: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
    ) -> None:
        desk = load_trading_desk(TRADER_ONE, format_timestamp=lambda value: "2026-06-27")

        assert "kpis" in desk
        assert len(desk["kpis"]) == 5
        assert desk["top_opportunities"] == []
        assert desk["live_market"] == []

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Trading Desk" in response.text
