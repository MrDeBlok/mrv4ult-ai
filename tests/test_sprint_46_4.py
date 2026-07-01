"""Tests for Sprint 46.4 — split market/client requests and admin-only AI tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from dashboard_data import build_quick_actions, build_trading_desk_kpis, load_trading_desk
from navigation import visible_nav_groups
from permissions import can_access_admin_tools, can_view_page
from tests.conftest import ADMIN_USER, TRADER_ONE

TRADING_DESK_KPI_TITLES = (
    "New offers today",
    "High opportunities",
    "Active market requests",
    "Active client requests",
    "AI needs help",
    "Unread notifications",
)


class TestAdminOnlyPermissions:
    def test_admin_can_access_parser_review(self) -> None:
        assert can_view_page(ADMIN_USER, "/parser-review") is True
        assert can_access_admin_tools(ADMIN_USER) is True

    def test_trader_cannot_access_parser_review_or_knowledge(self) -> None:
        assert can_view_page(TRADER_ONE, "/parser-review") is False
        assert can_view_page(TRADER_ONE, "/knowledge/unknown-brands") is False
        assert can_access_admin_tools(TRADER_ONE) is False

    @pytest.mark.no_auto_login
    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_blocked_from_parser_review_route(self, _mock_user: MagicMock) -> None:
        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 403

    @pytest.mark.no_auto_login
    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_blocked_from_knowledge_route(self, _mock_user: MagicMock) -> None:
        client = TestClient(app)
        response = client.get("/knowledge/unknown-brands")

        assert response.status_code == 403


class TestNavigationAiVisibility:
    def test_admin_nav_includes_ai_group(self) -> None:
        groups = visible_nav_groups(ADMIN_USER)
        labels = {group["label"] for group in groups}

        assert "AI" in labels

    def test_trader_nav_excludes_ai_group(self) -> None:
        groups = visible_nav_groups(TRADER_ONE)
        labels = {group["label"] for group in groups}
        item_paths = {link["path"] for group in groups for link in group["links"]}

        assert "AI" not in labels
        assert "/parser-review" not in item_paths
        assert "/knowledge/unknown-brands" not in item_paths

    def test_market_nav_labels_client_requests(self) -> None:
        groups = visible_nav_groups(TRADER_ONE)
        market_group = next(group for group in groups if group["label"] == "Market")
        labels = [link["label"] for link in market_group["links"]]

        assert "Market Requests" in labels
        assert "Client Requests" in labels
        assert "Requests" not in labels


class TestTradingDeskRequestSplit:
    def test_build_trading_desk_kpis_include_split_request_cards(self) -> None:
        cards = build_trading_desk_kpis(
            new_offers_today=1,
            high_opportunities=2,
            active_market_requests=3,
            active_client_requests=4,
            ai_needs_help=5,
            unread_notifications=6,
        )

        assert [card["title"] for card in cards] == list(TRADING_DESK_KPI_TITLES)
        assert cards[2]["url"] == "/market-requests"
        assert cards[3]["url"] == "/requests"
        assert cards[4]["url"] == "/parser-review"

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
    @patch("dashboard_data.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[{"id": "log-1"}])
    @patch("dashboard_data.list_dashboard_market_request_import_logs")
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    @patch("dashboard_data.list_requests")
    @patch("dashboard_data.list_recent_request_matches", return_value=[])
    def test_load_trading_desk_counts_market_and_client_requests_separately(
        self,
        _mock_matches: MagicMock,
        mock_list_requests: MagicMock,
        _mock_recent: MagicMock,
        _mock_today: MagicMock,
        mock_market: MagicMock,
        _mock_parser: MagicMock,
        _mock_filter: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_business_filter: MagicMock,
        _mock_notifications: MagicMock,
        _mock_offers: MagicMock,
        _mock_unread: MagicMock,
        _mock_attach: MagicMock,
        _mock_messages: MagicMock,
    ) -> None:
        mock_market.return_value = [
            {"id": "m-1", "status": "request_intent"},
            {"id": "m-2", "status": "request_intent"},
        ]
        mock_list_requests.return_value = [
            {"id": "r-1", "status": "open"},
            {"id": "r-2", "status": "closed"},
            {"id": "r-3", "status": "active"},
        ]

        desk = load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value)
        counts = {card["key"]: card["count"] for card in desk["kpis"]}

        assert counts["active_market_requests"] == 2
        assert counts["active_client_requests"] == 2
        assert "ai_needs_help" not in counts

    def test_quick_actions_label_new_client_request_for_traders(self) -> None:
        actions = build_quick_actions(TRADER_ONE)
        labels = {action["label"] for action in actions}

        assert "New Client Request" in labels
        assert "AI Workbench" not in labels

    def test_quick_actions_include_ai_workbench_for_admin(self) -> None:
        actions = build_quick_actions(ADMIN_USER)
        labels = {action["label"] for action in actions}

        assert "AI Workbench" in labels


class TestTradingDeskUiVisibility:
    @patch("app.load_trading_desk")
    def test_admin_dashboard_shows_ai_tools(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=1,
                high_opportunities=0,
                active_market_requests=2,
                active_client_requests=3,
                ai_needs_help=4,
                unread_notifications=0,
            ),
            "quick_actions": build_quick_actions(ADMIN_USER),
            "matched_requests": [],
            "top_opportunities": [],
            "ai_needs_help": [{"reason": "missing price", "message_preview": "Rolex", "group_name": "HK", "dealer": "A", "review_url": "/activity/log-1", "review_label": "Open"}],
            "live_market": [],
            "show_ai_needs_help": True,
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'data-nav-group="ai"' in response.text
        assert "AI needs help" in response.text
        assert "AI Workbench" in response.text
        assert "Active client requests" in response.text
        assert "Active market requests" in response.text

    @patch("app.load_trading_desk")
    def test_trader_dashboard_hides_ai_tools(self, mock_load_desk: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_load_desk.return_value = {
            "kpis": [
                card
                for card in build_trading_desk_kpis(
                    new_offers_today=1,
                    high_opportunities=0,
                    active_market_requests=2,
                    active_client_requests=3,
                    ai_needs_help=4,
                    unread_notifications=0,
                )
                if card["key"] != "ai_needs_help"
            ],
            "quick_actions": build_quick_actions(TRADER_ONE),
            "matched_requests": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": False,
            "show_write_actions": True,
        }
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'data-nav-group="ai"' not in response.text
        assert ">AI needs help<" not in response.text
        assert "AI Workbench" not in response.text
        assert "New Client Request" in response.text
        assert "Client Requests" in response.text

    @patch("app.load_trading_desk")
    def test_matched_requests_table_shows_type_column(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [
                {
                    "match_id": "match-1",
                    "match_url": "/matches/match-1",
                    "request_type": "Client",
                    "request_type_class": "primary",
                    "client_name": "Yury",
                    "watch_label": "Rolex 126200",
                    "dealer": "Dealer A",
                    "offer_price": "$10,000",
                    "potential_profit": "$1,000",
                    "match_age": "2h ago",
                    "status_label": "Profit",
                    "status_class": "success",
                    "confidence_label": "Strong match",
                    "confidence_class": "success",
                }
            ],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": True,
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert ">Type<" in response.text
        assert ">Client<" in response.text
        assert ">Yury<" in response.text
