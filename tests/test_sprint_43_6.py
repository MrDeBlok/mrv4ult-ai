"""Tests for Sprint 43.6 dashboard targeted query optimization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import app
from dashboard_data import (
    AI_NEEDS_HELP_LIMIT,
    LIVE_MARKET_LIMIT,
    MATCHED_REQUESTS_LIMIT,
    load_dashboard_matched_requests,
    load_trading_desk,
)
from database import (
    IMPORT_LOG_LIST_LIMIT_DASHBOARD,
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE,
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_MARKET,
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_PARSER,
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_TODAY,
)
from tests.conftest import TRADER_ONE, VIEWER_USER

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
TODAY_NOON = datetime(2026, 6, 27, 12, 0, tzinfo=AMSTERDAM)


class TestDashboardTargetedQueries:
    @patch("database.list_import_logs")
    @patch("dashboard_data.load_dashboard_matched_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.build_quick_actions", return_value=[])
    @patch("dashboard_data.load_live_market_rows", return_value=[])
    @patch("dashboard_data.load_ai_needs_help_items", return_value=[])
    @patch("dashboard_data.load_dashboard_todays_best_deals", return_value=([], 0))
    @patch("dashboard_data.parser_review_counts", return_value={"total": 0})
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    def test_dashboard_no_longer_calls_list_import_logs_limit_400(
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
        mock_list_import_logs: MagicMock,
    ) -> None:
        load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value, now=TODAY_NOON)

        mock_list_import_logs.assert_not_called()
        mock_recent.assert_called_once()
        assert mock_recent.call_args.kwargs["limit"] == IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE
        mock_today.assert_called_once()
        assert "since_iso" in mock_today.call_args.kwargs
        mock_market.assert_called_once_with()
        mock_parser.assert_called_once_with()

    @patch("dashboard_data.load_dashboard_matched_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.build_quick_actions", return_value=[])
    @patch("dashboard_data.load_live_market_rows")
    @patch("dashboard_data.load_ai_needs_help_items", return_value=[])
    @patch("dashboard_data.load_dashboard_todays_best_deals", return_value=([], 0))
    @patch("dashboard_data.parser_review_counts", return_value={"total": 0})
    @patch("dashboard_data.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("dashboard_data.filter_business_import_logs", side_effect=lambda logs, _lookup: logs)
    @patch("dashboard_data.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("dashboard_data.filter_discarded_import_logs", side_effect=lambda logs: logs)
    @patch("dashboard_data.filter_market_request_imports", side_effect=lambda logs: logs)
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs")
    def test_dashboard_uses_bounded_recent_activity_fetch(
        self,
        mock_recent: MagicMock,
        _mock_today: MagicMock,
        _mock_market: MagicMock,
        _mock_parser: MagicMock,
        _mock_lookup: MagicMock,
        _mock_contacts: MagicMock,
        _mock_market_filter: MagicMock,
        _mock_discard: MagicMock,
        _mock_visibility: MagicMock,
        _mock_business: MagicMock,
        _mock_attach: MagicMock,
        _mock_parser_counts: MagicMock,
        _mock_top_opportunities: MagicMock,
        _mock_ai: MagicMock,
        mock_live_market: MagicMock,
        _mock_actions: MagicMock,
        _mock_unread: MagicMock,
        _mock_matched: MagicMock,
    ) -> None:
        recent_rows = [{"id": f"log-{index}", "import_time": "2026-06-27T10:00:00+00:00"} for index in range(3)]
        mock_recent.return_value = recent_rows

        load_trading_desk(TRADER_ONE, format_timestamp=lambda value: value, now=TODAY_NOON)

        mock_recent.assert_called_once()
        assert mock_recent.call_args.kwargs["limit"] <= 20
        mock_live_market.assert_called_once()
        assert mock_live_market.call_args.args[0] == recent_rows

    @patch("dashboard_data.logger")
    @patch("dashboard_data.load_dashboard_matched_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    @patch("dashboard_data.build_quick_actions", return_value=[])
    @patch("dashboard_data.load_live_market_rows", return_value=[])
    @patch("dashboard_data.load_ai_needs_help_items", return_value=[])
    @patch("dashboard_data.load_dashboard_todays_best_deals", return_value=([], 0))
    @patch("dashboard_data.parser_review_counts", return_value={"total": 0})
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("dashboard_data.list_dashboard_parser_review_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_market_request_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_today_import_logs", return_value=[])
    @patch("dashboard_data.list_dashboard_recent_import_logs", return_value=[])
    def test_dashboard_logs_section_timings(
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
        assert "kpi_cards" in logged_sections
        assert "matched_requests" in logged_sections
        assert "todays_best_deals" in logged_sections
        assert "ai_needs_help" in logged_sections
        assert "live_market" in logged_sections


class TestDashboardRouteBehavior:
    @patch("app.load_trading_desk")
    def test_kpi_cards_still_render(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [
                {
                    "key": "new_offers_today",
                    "title": "New offers today",
                    "count": 7,
                    "url": "/activity",
                    "description": "Fresh dealer offers imported today.",
                }
            ],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "New offers today" in response.text
        assert ">7<" in response.text

    @patch("app.load_trading_desk")
    def test_ai_needs_help_section_limited_to_five(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [
                {
                    "reason": f"Reason {index}",
                    "message_preview": f"Preview {index}",
                    "group_name": "HK",
                    "dealer": "Dealer",
                    "review_url": f"/activity/log-{index}",
                    "review_label": "Review",
                }
                for index in range(AI_NEEDS_HELP_LIMIT)
            ],
            "live_market": [],
            "show_ai_needs_help": True,
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert response.text.count("Reason ") == AI_NEEDS_HELP_LIMIT

    @patch("app.load_trading_desk")
    def test_live_market_section_limited_to_ten(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [
                {
                    "import_time": "2026-06-27 12:00",
                    "status": "Success",
                    "status_class": "success",
                    "group_name": "HK",
                    "dealer": "Dealer",
                    "message_preview": f"Preview {index}",
                    "detail_url": f"/activity/log-{index}",
                }
                for index in range(LIVE_MARKET_LIMIT)
            ],
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert response.text.count("Preview ") == LIVE_MARKET_LIMIT

    @pytest.mark.no_auto_login
    @patch("app.load_trading_desk")
    def test_viewer_dashboard_hides_write_actions(
        self,
        mock_load_desk: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [
                {"key": "search", "label": "Search", "url": "/", "style": "primary", "visible": True},
            ],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": False,
        }
        monkeypatch.setattr("app.get_current_user", lambda _request: VIEWER_USER)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'href="/import"' not in response.text
        assert 'href="/"' in response.text


class TestDashboardQueryLimits:
    def test_dashboard_query_limits_stay_bounded(self) -> None:
        assert IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE <= 20
        assert IMPORT_LOG_LIST_LIMIT_DASHBOARD_TODAY <= 50
        assert IMPORT_LOG_LIST_LIMIT_DASHBOARD_MARKET <= 25
        assert IMPORT_LOG_LIST_LIMIT_DASHBOARD_PARSER <= 25
        assert IMPORT_LOG_LIST_LIMIT_DASHBOARD == 400


class TestDashboardMatchedRequests:
    @patch("app.load_trading_desk")
    def test_dashboard_renders_matched_requests_section(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [
                {
                    "client_name": "John Smith",
                    "watch_label": "Rolex 116508",
                    "dealer": "HK Dealer",
                    "offer_price": "$45,000",
                    "potential_profit": "+$5,000",
                    "match_age": "2026-06-27 12:00",
                    "status_label": "Below budget",
                    "status_class": "success",
                    "confidence_label": "Strong match",
                    "confidence_class": "success",
                    "match_id": "match-1",
                    "match_url": "/matches/match-1",
                    "request_url": "/requests",
                }
            ],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Matched Requests" in response.text
        assert "John Smith" in response.text
        assert "Rolex 116508" in response.text
        assert "HK Dealer" in response.text
        assert "$45,000" in response.text
        assert 'href="/matches/match-1"' in response.text
        assert "View match" in response.text

    @patch("dashboard_data.can_view_page", return_value=True)
    @patch("dashboard_data.can_view_import", return_value=True)
    @patch("dashboard_data.get_requests_by_ids")
    @patch("dashboard_data.load_enriched_request_match_batch")
    @patch("dashboard_data.list_recent_request_matches")
    def test_matched_requests_limited_to_ten(
        self,
        mock_list_matches: MagicMock,
        mock_enriched: MagicMock,
        mock_get_requests: MagicMock,
        _mock_view_import: MagicMock,
        _mock_view_page: MagicMock,
    ) -> None:
        request_id = "req-1"
        mock_list_matches.return_value = [
            {
                "id": f"match-{index}",
                "request_id": request_id,
                "offer_id": f"offer-{index}",
                "import_log_id": f"log-{index}",
                "match_strength": "strong",
                "match_reason": "Reference match",
                "created_at": f"2026-06-27T{index:02d}:00:00+00:00",
            }
            for index in range(MATCHED_REQUESTS_LIMIT + 5)
        ]
        mock_enriched.side_effect = lambda matches: [
            {
                **match,
                "offer": {"usd_price": 45000},
                "watch": {"brand": "Rolex", "reference": "116508"},
                "import_log": {
                    "id": match["import_log_id"],
                    "dealer_alias": "Dealer",
                    "dealer_whatsapp": "+1",
                },
            }
            for match in matches
        ]
        mock_get_requests.return_value = {
            request_id: {
                "id": request_id,
                "client_name": "Client",
                "brand": "Rolex",
                "reference": "116508",
                "max_price": 50000,
                "currency": "USD",
            }
        }

        rows = load_dashboard_matched_requests(TRADER_ONE)

        assert len(rows) == MATCHED_REQUESTS_LIMIT

    @patch("app.load_trading_desk")
    def test_matched_requests_empty_state_renders(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "No matched requests yet." in response.text

    @pytest.mark.no_auto_login
    @patch("app.load_trading_desk")
    def test_viewer_dashboard_is_read_only(
        self,
        mock_load_desk: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [
                {"key": "search", "label": "Search", "url": "/", "style": "primary", "visible": True},
            ],
            "matched_requests": [
                {
                    "client_name": "John Smith",
                    "watch_label": "Rolex 116508",
                    "dealer": "HK Dealer",
                    "offer_price": "$45,000",
                    "potential_profit": "—",
                    "match_age": "2026-06-27 12:00",
                    "status_label": "Below budget",
                    "status_class": "success",
                    "confidence_label": "Strong match",
                    "confidence_class": "success",
                    "request_url": None,
                }
            ],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": False,
        }
        monkeypatch.setattr("app.get_current_user", lambda _request: VIEWER_USER)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Matched Requests" in response.text
        assert 'href="/import"' not in response.text
        assert "Open request" not in response.text
