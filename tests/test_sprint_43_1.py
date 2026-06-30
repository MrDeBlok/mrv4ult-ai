"""Tests for Sprint 43.1 dashboard performance optimization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest

from dashboard_data import (
    AI_NEEDS_HELP_LIMIT,
    LIVE_MARKET_LIMIT,
    TOP_OPPORTUNITIES_LIMIT,
    TOP_OPPORTUNITIES_SCAN_LIMIT,
    load_ai_needs_help_items,
    load_dashboard_top_opportunities,
    load_live_market_rows,
    load_trading_desk,
)
from database import (
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE,
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_TODAY,
)
from tests.conftest import ADMIN_USER, TRADER_ONE

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
TODAY_NOON = datetime(2026, 6, 27, 12, 0, tzinfo=AMSTERDAM)


def _market_request(import_id: str, *, brand: str = "Rolex", reference: str = "126610LN") -> dict:
    return {
        "id": import_id,
        "status": "request_intent",
        "import_time": "2026-06-27T10:00:00+00:00",
        "summary": {
            "parsed_watches": [
                {
                    "brand": brand,
                    "reference": reference,
                    "original_price": 15000,
                    "original_currency": "USD",
                }
            ]
        },
    }


def _matching_offer(*, reference: str = "126610LN", price: int = 12000) -> dict:
    return {
        "id": "offer-1",
        "dealer_id": "dealer-1",
        "watch_id": "watch-1",
        "original_price": price,
        "original_currency": "USD",
        "usd_price": price,
        "status": "active",
        "watches": {"brand": "Rolex", "reference": reference},
        "dealers": {
            "id": "dealer-1",
            "display_name": "Dealer A",
            "contact_type": "dealer",
        },
        "messages": {"received_at": "2026-06-27T09:00:00+00:00", "groups": {"name": "HK"}},
    }


class TestDashboardSingleFetch:
    @patch("dashboard_data.load_dashboard_matched_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.build_quick_actions", return_value=[])
    @patch("dashboard_data.load_live_market_rows", return_value=[])
    @patch("dashboard_data.load_ai_needs_help_items", return_value=[])
    @patch("dashboard_data.load_dashboard_top_opportunities", return_value=([], 0))
    @patch("dashboard_data.parser_review_counts", return_value={"total": 0})
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    def test_load_trading_desk_uses_targeted_dashboard_queries(
        self,
        mock_recent: MagicMock,
        mock_today: MagicMock,
        mock_market: MagicMock,
        mock_parser: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_parser_counts: MagicMock,
        _mock_top_opportunities: MagicMock,
        _mock_ai: MagicMock,
        _mock_live_market: MagicMock,
        _mock_actions: MagicMock,
        _mock_unread: MagicMock,
        _mock_matched: MagicMock,
    ) -> None:
        load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value, now=TODAY_NOON)

        mock_recent.assert_called_once()
        assert mock_recent.call_args.kwargs["limit"] == IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE
        mock_today.assert_called_once()
        assert "since_iso" in mock_today.call_args.kwargs
        mock_market.assert_called_once_with()
        mock_parser.assert_called_once_with()


class TestDashboardLightweightOpportunities:
    @patch("database.list_active_offers_for_market_matching")
    @patch("dashboard_data.filter_matching_offers_for_user")
    def test_top_opportunities_use_lightweight_matching_only(
        self,
        mock_filter_offers: MagicMock,
        mock_list_offers: MagicMock,
    ) -> None:
        mock_list_offers.return_value = [_matching_offer()]
        mock_filter_offers.return_value = [_matching_offer()]

        with patch(
            "opportunity_engine.build_market_request_opportunity_bundle",
            side_effect=AssertionError("heavy opportunity analysis should not run"),
        ):
            rows, high_count = load_dashboard_top_opportunities(
                ADMIN_USER,
                [_market_request("req-1")],
            )

        assert high_count == 1
        assert len(rows) == 1
        assert rows[0]["watch_label"] == "Rolex 126610LN"
        mock_list_offers.assert_called_once()

    @patch("database.list_active_offers_for_market_matching")
    @patch("dashboard_data.filter_matching_offers_for_user")
    def test_top_opportunities_scan_limited_requests(
        self,
        mock_filter_offers: MagicMock,
        mock_list_offers: MagicMock,
    ) -> None:
        mock_list_offers.return_value = [_matching_offer()]
        mock_filter_offers.return_value = [_matching_offer()]
        market_requests = [
            _market_request(f"req-{index}", reference=f"126610L{index}")
            for index in range(TOP_OPPORTUNITIES_SCAN_LIMIT + 3)
        ]

        rows, _high_count = load_dashboard_top_opportunities(ADMIN_USER, market_requests)

        assert len(rows) <= TOP_OPPORTUNITIES_LIMIT


class TestDashboardRowLimits:
    @patch("dashboard_data.list_recent_notifications", return_value=[])
    @patch("dashboard_data.get_messages_by_ids", return_value={})
    @patch("dashboard_data.build_parser_review_row")
    def test_ai_needs_help_limited_to_five_items(
        self,
        mock_review_row: MagicMock,
        _mock_messages: MagicMock,
        _mock_notifications: MagicMock,
    ) -> None:
        mock_review_row.return_value = {
            "status_reason": "Missing price",
            "message_preview": "Rolex",
            "group_name": "HK",
            "dealer": "Dealer A",
            "detail_url": "/activity/log-1",
        }
        business_imports = [
            {
                "id": f"log-{index}",
                "status": "warning",
                "message_id": f"msg-{index}",
                "import_time": f"2026-06-27T{index:02d}:00:00+00:00",
                "summary": {"parsed_watches": [{"brand": "Rolex"}]},
            }
            for index in range(AI_NEEDS_HELP_LIMIT + 3)
        ]

        with patch("dashboard_data.filter_parser_review_imports", side_effect=lambda logs: logs):
            items = load_ai_needs_help_items(
                    ADMIN_USER,
                    business_imports=business_imports,
                format_timestamp=lambda value: value,
            )

        assert len(items) <= AI_NEEDS_HELP_LIMIT

    @patch("dashboard_data.get_messages_by_ids", return_value={})
    def test_live_market_limited_to_ten_rows(self, _mock_messages: MagicMock) -> None:
        import_logs = [
            {
                "id": f"log-{index}",
                "import_time": "2026-06-27T10:00:00+00:00",
                "new_offers": 1,
                "status": "success",
            }
            for index in range(LIVE_MARKET_LIMIT + 5)
        ]

        rows = load_live_market_rows(import_logs, now=TODAY_NOON)

        assert len(rows) == LIVE_MARKET_LIMIT


class TestDashboardLimitedNotifications:
    @patch("dashboard_data.build_parser_review_row")
    @patch("dashboard_data.get_messages_by_ids", return_value={})
    @patch("dashboard_data.list_recent_notifications", return_value=[])
    @patch("dashboard_data.filter_parser_review_imports", return_value=[])
    def test_ai_needs_help_uses_limited_notification_query(
        self,
        _mock_parser_filter: MagicMock,
        mock_list_recent: MagicMock,
        _mock_messages: MagicMock,
        _mock_review_row: MagicMock,
    ) -> None:
        load_ai_needs_help_items(
            ADMIN_USER,
            business_imports=[],
            format_timestamp=lambda value: value,
        )

        mock_list_recent.assert_called_once_with(
            limit=10,
            notification_type="needs_review",
        )


class TestDashboardTimingLogs:
    @patch("dashboard_data.logger")
    @patch("dashboard_data.load_dashboard_matched_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.build_quick_actions", return_value=[])
    @patch("dashboard_data.load_live_market_rows", return_value=[])
    @patch("dashboard_data.load_ai_needs_help_items", return_value=[])
    @patch("dashboard_data.load_dashboard_top_opportunities", return_value=([], 0))
    @patch("dashboard_data.parser_review_counts", return_value={"total": 0})
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    def test_load_trading_desk_logs_section_timings(
        self,
        _mock_recent: MagicMock,
        _mock_today: MagicMock,
        _mock_market: MagicMock,
        _mock_parser: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_parser_counts: MagicMock,
        _mock_top_opportunities: MagicMock,
        _mock_ai: MagicMock,
        _mock_live_market: MagicMock,
        _mock_actions: MagicMock,
        _mock_unread: MagicMock,
        _mock_matched: MagicMock,
        mock_logger: MagicMock,
    ) -> None:
        load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value, now=TODAY_NOON)

        logged_sections = [call.args[1] for call in mock_logger.info.call_args_list]
        assert "matched_requests" in logged_sections
        assert "kpi_cards" in logged_sections
        assert "top_opportunities" in logged_sections
        assert "ai_needs_help" in logged_sections
        assert "live_market" in logged_sections
