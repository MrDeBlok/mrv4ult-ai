"""Tests for Sprint 47.4 Today's Best Deals KPI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from dashboard_data import build_trading_desk_kpis, load_trading_desk
from tests.conftest import ADMIN_USER, TRADER_ONE

def _best_deal_row(import_id: str = "log-1") -> dict:
    return {
        "brand": "Rolex",
        "reference": "126334",
        "condition": "New",
        "dealer": "Dealer A",
        "offer_price": "$10,500",
        "market_price": "$10,800",
        "potential_profit": "$300",
        "show_potential_profit": True,
        "recommendation": "Good Buy",
        "recommendation_badge_class": "primary",
        "confidence": "90%",
        "deal_url": f"/activity/{import_id}",
        "message_dealer_url": None,
    }


def _kpi_snippet(html: str, key: str, *, lookback: int = 120, radius: int = 400) -> str:
    marker = f'data-kpi-key="{key}"'
    start = html.index(marker)
    return html[max(0, start - lookback) : start + radius]


class TestTodaysBestDealsKpi:
    def test_build_trading_desk_kpis_use_todays_best_deals_card(self) -> None:
        cards = build_trading_desk_kpis(
            new_offers_today=1,
            todays_best_deals=3,
            active_market_requests=2,
            active_client_requests=4,
            ai_needs_help=5,
            unread_notifications=6,
        )

        assert [card["title"] for card in cards] == [
            "New offers today",
            "Today's Best Deals",
            "Active market requests",
            "Active client requests",
            "AI needs help",
            "Unread notifications",
        ]
        best_deals = cards[1]
        assert best_deals["key"] == "todays_best_deals"
        assert best_deals["count"] == 3
        assert best_deals["url"] == "#todays-best-deals"
        assert best_deals["description"] == "Condition-safe offers worth reviewing today."
        assert cards[2]["url"] == "/market-requests"

    @patch("dashboard_data.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("dashboard_data.get_messages_by_ids", return_value={})
    @patch("dashboard_data.list_recent_request_matches", return_value=[])
    @patch("dashboard_data.list_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.list_recent_notifications", return_value=[])
    @patch("dashboard_data.filter_business_import_logs", side_effect=lambda logs, _lookup: logs)
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("dashboard_data.filter_discarded_import_logs", side_effect=lambda logs: logs)
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    @patch("dashboard_data.load_dashboard_todays_best_deals")
    def test_kpi_count_matches_best_deals_count(
        self,
        mock_best_deals: MagicMock,
        _mock_recent: MagicMock,
        _mock_today: MagicMock,
        _mock_market: MagicMock,
        _mock_parser: MagicMock,
        _mock_discard: MagicMock,
        _mock_filter: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_business: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unread: MagicMock,
        _mock_requests: MagicMock,
        _mock_matches: MagicMock,
        _mock_messages: MagicMock,
        _mock_attach: MagicMock,
    ) -> None:
        deals = [_best_deal_row("log-1"), _best_deal_row("log-2")]
        mock_best_deals.return_value = (deals, 2)

        desk = load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value)
        kpi = next(card for card in desk["kpis"] if card["key"] == "todays_best_deals")

        assert kpi["count"] == len(desk["todays_best_deals"]) == 2

    @patch("dashboard_data.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("dashboard_data.get_messages_by_ids", return_value={})
    @patch("dashboard_data.list_recent_request_matches", return_value=[])
    @patch("dashboard_data.list_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.list_recent_notifications", return_value=[])
    @patch("dashboard_data.filter_business_import_logs", side_effect=lambda logs, _lookup: logs)
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("dashboard_data.filter_discarded_import_logs", side_effect=lambda logs: logs)
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    @patch("dashboard_data.load_dashboard_todays_best_deals", return_value=([], 0))
    def test_zero_best_deals_shows_zero_kpi(
        self,
        _mock_best_deals: MagicMock,
        _mock_recent: MagicMock,
        _mock_today: MagicMock,
        _mock_market: MagicMock,
        _mock_parser: MagicMock,
        _mock_discard: MagicMock,
        _mock_filter: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_business: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unread: MagicMock,
        _mock_requests: MagicMock,
        _mock_matches: MagicMock,
        _mock_messages: MagicMock,
        _mock_attach: MagicMock,
    ) -> None:
        desk = load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value)
        kpi = next(card for card in desk["kpis"] if card["key"] == "todays_best_deals")

        assert kpi["count"] == 0
        assert desk["todays_best_deals"] == []


class TestTodaysBestDealsDashboardUi:
    @patch("app.load_trading_desk")
    def test_dashboard_shows_todays_best_deals_kpi_not_high_opportunities(
        self,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=1,
                todays_best_deals=2,
                active_market_requests=3,
                active_client_requests=4,
                ai_needs_help=0,
                unread_notifications=0,
            ),
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [_best_deal_row()],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": False,
            "show_write_actions": True,
        }

        response = TestClient(app).get("/dashboard")

        assert response.status_code == 200
        assert "Today's Best Deals" in response.text
        assert "High opportunities" not in response.text
        assert 'href="#todays-best-deals"' in response.text
        assert 'id="todays-best-deals"' in response.text

    @patch("app.load_trading_desk")
    def test_market_requests_kpi_still_links_to_market_requests(
        self,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=0,
                todays_best_deals=0,
                active_market_requests=5,
                active_client_requests=0,
                ai_needs_help=0,
                unread_notifications=0,
            ),
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": False,
            "show_write_actions": True,
        }

        response = TestClient(app).get("/dashboard")

        market_kpi = _kpi_snippet(response.text, "active_market_requests")
        best_deals_kpi = _kpi_snippet(response.text, "todays_best_deals")

        assert 'href="/market-requests"' in market_kpi
        assert 'href="#todays-best-deals"' in best_deals_kpi
        assert "/market-requests" not in best_deals_kpi

    @pytest.mark.no_auto_login
    @patch("app.load_trading_desk")
    def test_trader_dashboard_renders_todays_best_deals_kpi(
        self,
        mock_load_desk: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=1,
                todays_best_deals=1,
                active_market_requests=0,
                active_client_requests=0,
                ai_needs_help=0,
                unread_notifications=0,
            ),
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [_best_deal_row()],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": False,
            "show_write_actions": True,
        }
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        response = TestClient(app).get("/dashboard")

        assert response.status_code == 200
        assert "Today's Best Deals" in response.text
        assert 'href="#todays-best-deals"' in response.text

    @patch("app.load_trading_desk")
    def test_admin_dashboard_renders_todays_best_deals_kpi(
        self,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": build_trading_desk_kpis(
                new_offers_today=2,
                todays_best_deals=3,
                active_market_requests=1,
                active_client_requests=1,
                ai_needs_help=4,
                unread_notifications=0,
            ),
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [_best_deal_row()],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": True,
            "show_write_actions": True,
        }

        response = TestClient(app).get("/dashboard")

        assert response.status_code == 200
        assert "Today's Best Deals" in response.text
        assert "High opportunities" not in response.text


LEGACY_HIGH_OPPORTUNITIES_LABELS = (
    "High Opportunities",
    "High opportunities",
    "strong market request matches",
    "Top opportunities",
)


def _dashboard_payload(**overrides) -> dict:
    payload = {
        "kpis": build_trading_desk_kpis(
            new_offers_today=1,
            todays_best_deals=2,
            active_market_requests=3,
            active_client_requests=4,
            ai_needs_help=0,
            unread_notifications=0,
        ),
        "quick_actions": [],
        "matched_requests": [],
        "todays_best_deals": [_best_deal_row()],
        "ai_needs_help": [],
        "live_market": [],
        "show_ai_needs_help": False,
        "show_write_actions": True,
    }
    payload.update(overrides)
    return payload


class TestLegacyHighOpportunitiesRemoved:
    @patch("app.load_trading_desk")
    def test_dashboard_html_excludes_legacy_high_opportunities_copy(
        self,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = _dashboard_payload()

        response = TestClient(app).get("/dashboard")

        assert response.status_code == 200
        html_lower = response.text.lower()
        for label in LEGACY_HIGH_OPPORTUNITIES_LABELS:
            assert label.lower() not in html_lower

    @patch("app.load_trading_desk")
    def test_todays_best_deals_kpi_links_to_section_anchor(
        self,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = _dashboard_payload()

        response = TestClient(app).get("/dashboard")
        best_deals_kpi = _kpi_snippet(response.text, "todays_best_deals", radius=600)

        assert "Today&#39;s Best Deals" in best_deals_kpi
        assert "Condition-safe offers worth reviewing today." in best_deals_kpi
        assert 'href="#todays-best-deals"' in best_deals_kpi
        assert "/market-requests" not in best_deals_kpi
