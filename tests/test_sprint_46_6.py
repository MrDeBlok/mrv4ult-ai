"""Tests for Sprint 46.6 Today's Best Deals dashboard section."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION
from dashboard_data import load_trading_desk
from tests.conftest import ADMIN_USER, TRADER_ONE
from todays_best_deals import load_dashboard_todays_best_deals

TODAY_NOON = "2026-06-27T12:00:00+00:00"


def _offer_import(
    import_id: str,
    *,
    import_time: str = TODAY_NOON,
    condition: str | None = NEW_CONDITION,
    market_condition: str | None = NEW_CONDITION,
    offer_usd: int = 10_500,
    market_usd: int = 10_800,
    price_label: str = "Good price",
    confidence: int = 90,
    brand: str = "Rolex",
    reference: str = "126334",
    dealer_alias: str = "Dealer A",
    dealer_whatsapp: str = "+85212345678",
) -> dict:
    return {
        "id": import_id,
        "import_time": import_time,
        "dealer_alias": dealer_alias,
        "dealer_whatsapp": dealer_whatsapp,
        "group_name": "HK Dealers",
        "summary": {
            "parsed_watches": [
                {
                    "brand": brand,
                    "reference": reference,
                    "condition": condition,
                    "confidence": confidence,
                    "usd_price": offer_usd,
                }
            ],
            "rows": [
                {
                    "brand": brand,
                    "reference": reference,
                    "condition": condition,
                    "usd_price": offer_usd,
                    "previous_lowest_usd": f"${market_usd:,}",
                    "market_condition": market_condition,
                    "price_label": price_label,
                    "rank": "2",
                }
            ],
        },
    }


class TestTodaysBestDealsRanking:
    def test_excellent_buy_ranks_before_good_buy(self) -> None:
        imports = [
            _offer_import(
                "log-good",
                import_time="2026-06-27T11:00:00+00:00",
                offer_usd=10_700,
                market_usd=10_800,
                price_label="Good price",
            ),
            _offer_import(
                "log-excellent",
                import_time="2026-06-27T10:00:00+00:00",
                offer_usd=10_400,
                market_usd=10_800,
                price_label="New lowest price",
            ),
        ]

        rows, _strong_count = load_dashboard_todays_best_deals(TRADER_ONE, imports)

        assert len(rows) == 2
        assert rows[0]["recommendation"] == "Excellent Buy"
        assert rows[1]["recommendation"] == "Good Buy"

    def test_fair_price_included_when_no_excellent_buy_exists(self) -> None:
        imports = [
            _offer_import(
                "log-fair",
                offer_usd=10_790,
                market_usd=10_800,
                price_label="Normal price",
            )
        ]

        rows, _strong_count = load_dashboard_todays_best_deals(TRADER_ONE, imports)

        assert len(rows) == 1
        assert rows[0]["recommendation"] == "Fair Price"

    def test_unknown_condition_deals_are_excluded(self) -> None:
        imports = [
            _offer_import(
                "log-unsafe",
                condition=None,
                market_condition=None,
                offer_usd=10_500,
                market_usd=10_800,
                price_label="No comparables",
            )
        ]

        rows, _strong_count = load_dashboard_todays_best_deals(TRADER_ONE, imports)

        assert rows == []

    def test_condition_mismatch_deals_are_excluded(self) -> None:
        imports = [
            _offer_import(
                "log-mismatch",
                condition=NEW_CONDITION,
                market_condition=PRE_OWNED_CONDITION,
                offer_usd=10_500,
                market_usd=10_800,
                price_label="Good price",
            )
        ]

        rows, _strong_count = load_dashboard_todays_best_deals(TRADER_ONE, imports)

        assert rows == []

    def test_rows_include_condition_and_safe_profit_fields(self) -> None:
        imports = [_offer_import("log-1")]

        rows, _strong_count = load_dashboard_todays_best_deals(TRADER_ONE, imports)
        row = rows[0]

        assert row["condition"] == NEW_CONDITION
        assert row["show_potential_profit"] is True
        assert row["potential_profit"] == "$300"
        assert row["deal_url"] == "/activity/log-1"
        assert row["message_dealer_url"] == "https://wa.me/85212345678"

    def test_loader_limits_to_five_deals(self) -> None:
        imports = [
            _offer_import(f"log-{index}", import_time=f"2026-06-27T{index:02d}:00:00+00:00")
            for index in range(8)
        ]

        rows, _strong_count = load_dashboard_todays_best_deals(TRADER_ONE, imports)

        assert len(rows) == 5


class TestTodaysBestDealsDashboard:
    @patch("app.load_trading_desk")
    def test_dashboard_shows_todays_best_deals_heading(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [
                {
                    "brand": "Rolex",
                    "reference": "126334",
                    "condition": NEW_CONDITION,
                    "dealer": "Dealer A",
                    "offer_price": "$10,500",
                    "market_price": "$10,800",
                    "potential_profit": "$300",
                    "show_potential_profit": True,
                    "recommendation": "Good Buy",
                    "recommendation_badge_class": "primary",
                    "confidence": "90%",
                    "deal_url": "/activity/log-1",
                    "message_dealer_url": None,
                }
            ],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": True,
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Today's Best Deals" in response.text
        assert "Top opportunities" not in response.text
        assert ">Condition<" in response.text
        assert 'href="/activity/log-1"' in response.text
        assert "Open Deal" in response.text

    @patch("app.load_trading_desk")
    def test_empty_state_shows_condition_safe_message(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": True,
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert "No condition-safe deals found yet." in response.text
        assert "matching same-condition market data." in response.text

    @patch("app.load_trading_desk")
    def test_trader_dashboard_still_hides_ai_nav(
        self,
        mock_load_desk: MagicMock,
        monkeypatch,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_ai_needs_help": False,
            "show_write_actions": True,
        }
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Today's Best Deals" in response.text
        assert 'data-nav-group="ai"' not in response.text


class TestTodaysBestDealsIntegration:
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
    @patch("dashboard_data.list_dashboard_recent_import_logs")
    def test_load_trading_desk_builds_todays_best_deals_from_recent_imports(
        self,
        mock_recent: MagicMock,
        _mock_today: MagicMock,
        _mock_market: MagicMock,
        _mock_parser: MagicMock,
        _mock_filter: MagicMock,
        _mock_discard: MagicMock,
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
        offer_import = _offer_import("log-1")
        mock_recent.return_value = [offer_import]

        desk = load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value)

        assert len(desk["todays_best_deals"]) == 1
        assert desk["todays_best_deals"][0]["deal_url"] == "/activity/log-1"

    @patch("database.list_active_offers_for_market_matching")
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
    def test_load_trading_desk_does_not_scan_active_offers_for_best_deals(
        self,
        mock_recent: MagicMock,
        _mock_today: MagicMock,
        _mock_market: MagicMock,
        _mock_parser: MagicMock,
        _mock_filter: MagicMock,
        _mock_discard: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_business: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unread: MagicMock,
        _mock_requests: MagicMock,
        _mock_matches: MagicMock,
        _mock_messages: MagicMock,
        _mock_attach: MagicMock,
        mock_list_offers: MagicMock,
    ) -> None:
        mock_recent.return_value = []

        load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value)

        mock_list_offers.assert_not_called()
