"""Tests for Sprint 45.2 dedicated match detail page."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from match_detail import build_match_detail, load_match_detail
from tests.conftest import ADMIN_USER, TRADER_TWO, VIEWER_USER

MATCH_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
REQUEST_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
IMPORT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
MESSAGE_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

REQUEST = {
    "id": REQUEST_ID,
    "client_name": "John Smith",
    "brand": "Rolex",
    "reference": "126500LN",
    "model": "Daytona",
    "alias": "",
    "dial": "White",
    "condition": "New",
    "min_year": None,
    "max_year": None,
    "max_price": 350000,
    "currency": "USD",
    "notes": "Looking for white dial Panda, full set preferred.",
    "status": "open",
    "created_at": "2026-06-27T10:00:00+00:00",
}

ENRICHED_MATCH = {
    "id": MATCH_ID,
    "request_id": REQUEST_ID,
    "offer_id": "offer-1",
    "import_log_id": IMPORT_ID,
    "match_strength": "strong",
    "match_reason": "Reference match: 126500LN",
    "created_at": "2026-06-27T12:00:00+00:00",
    "offer": {
        "id": "offer-1",
        "original_price": 305000,
        "original_currency": "USD",
        "usd_price": 305000,
        "condition": "New",
        "production_year": 2025,
        "card_date": "06/2026",
    },
    "watch": {
        "brand": "Rolex",
        "reference": "126500LN",
        "model": "Daytona",
        "dial": "White",
    },
    "import_log": {
        "id": IMPORT_ID,
        "import_time": "2026-06-27T12:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_alias": "HK Dealer",
        "dealer_whatsapp": "+85212345678",
        "message_id": MESSAGE_ID,
    },
}

MESSAGE = {
    "id": MESSAGE_ID,
    "raw_text": "ROLEX 126500LN white n9/25 305k usd full set",
}


def _detail_payload() -> dict:
    from request_profit import attach_profit_to_matches

    match = attach_profit_to_matches(REQUEST, [ENRICHED_MATCH])[0]
    return build_match_detail(
        REQUEST,
        match,
        message=MESSAGE,
        user=ADMIN_USER,
        format_timestamp=lambda value: value or "—",
    )


class TestDashboardMatchLinks:
    @patch("app.load_trading_desk")
    def test_dashboard_matched_request_links_to_match_detail(
        self,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [
                {
                    "match_id": MATCH_ID,
                    "match_url": f"/matches/{MATCH_ID}",
                    "client_name": "John Smith",
                    "watch_label": "Rolex 126500LN",
                    "dealer": "HK Dealer",
                    "offer_price": "$305,000",
                    "potential_profit": "$45,000",
                    "match_age": "2026-06-27 12:00",
                    "status_label": "Below budget",
                    "status_class": "success",
                    "confidence_label": "Strong match",
                    "confidence_class": "success",
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
        assert f'href="/matches/{MATCH_ID}"' in response.text
        assert "View match" in response.text
        assert "Open request" not in response.text


class TestMatchDetailPage:
    @patch("app.load_match_detail")
    def test_match_detail_page_renders_request_and_offer_data(
        self,
        mock_load_detail: MagicMock,
    ) -> None:
        mock_load_detail.return_value = _detail_payload()

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 200
        assert "John Smith · Rolex 126500LN" in response.text
        assert "Client request" in response.text
        assert "Dealer offer" in response.text
        assert "Looking for white dial Panda, full set preferred." in response.text
        assert "ROLEX 126500LN white n9/25 305k usd full set" in response.text
        assert "Reference match: 126500LN" in response.text
        assert 'href="/activity/' in response.text
        assert 'href="/requests"' in response.text
        assert 'href="/dashboard"' in response.text

    @patch("app.load_match_detail")
    def test_match_detail_page_shows_potential_profit(
        self,
        mock_load_detail: MagicMock,
    ) -> None:
        mock_load_detail.return_value = _detail_payload()

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 200
        assert "Potential profit" in response.text
        assert "$45,000" in response.text
        assert "Below budget" in response.text

    @patch("app.load_match_detail", return_value=None)
    def test_missing_match_returns_404(self, _mock_load_detail: MagicMock) -> None:
        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 404


class TestMatchDetailVisibility:
    @pytest.mark.no_auto_login
    @patch("database.get_message_by_id")
    @patch("database.get_request")
    @patch("database.load_enriched_request_match_batch")
    @patch("database.get_request_match")
    @patch("match_detail.can_view_import")
    def test_visibility_respected_for_private_import(
        self,
        mock_can_view_import: MagicMock,
        mock_get_match: MagicMock,
        mock_enriched: MagicMock,
        mock_get_request: MagicMock,
        mock_get_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_TWO)
        mock_get_match.return_value = {
            "id": MATCH_ID,
            "request_id": REQUEST_ID,
            "offer_id": "offer-1",
            "import_log_id": IMPORT_ID,
            "match_strength": "strong",
            "match_reason": "Reference match: 126500LN",
            "created_at": "2026-06-27T12:00:00+00:00",
        }
        mock_enriched.return_value = [ENRICHED_MATCH]
        mock_get_request.return_value = REQUEST
        mock_get_message.return_value = MESSAGE
        mock_can_view_import.return_value = False

        detail = load_match_detail(TRADER_TWO, MATCH_ID)

        assert detail is None

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 404

    @pytest.mark.no_auto_login
    @patch("database.get_message_by_id")
    @patch("database.get_request")
    @patch("database.load_enriched_request_match_batch")
    @patch("database.get_request_match")
    @patch("match_detail.can_view_import", return_value=True)
    def test_viewer_can_open_visible_match_without_requests_page_link(
        self,
        _mock_can_view_import: MagicMock,
        mock_get_match: MagicMock,
        mock_enriched: MagicMock,
        mock_get_request: MagicMock,
        mock_get_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.get_current_user", lambda _request: VIEWER_USER)
        mock_get_match.return_value = {
            "id": MATCH_ID,
            "request_id": REQUEST_ID,
            "offer_id": "offer-1",
            "import_log_id": IMPORT_ID,
            "match_strength": "strong",
            "match_reason": "Reference match: 126500LN",
            "created_at": "2026-06-27T12:00:00+00:00",
        }
        mock_enriched.return_value = [ENRICHED_MATCH]
        mock_get_request.return_value = REQUEST
        mock_get_message.return_value = MESSAGE

        detail = load_match_detail(VIEWER_USER, MATCH_ID)

        assert detail is not None
        assert detail["actions"]["request_url"] is None
        assert detail["actions"]["activity_url"] == f"/activity/{IMPORT_ID}"

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 200
        assert "View offer/import" in response.text
        assert 'href="/requests"' not in response.text


class TestMatchDetailLoader:
    @patch("database.get_message_by_id", return_value=MESSAGE)
    @patch("database.get_request", return_value=REQUEST)
    @patch("database.load_enriched_request_match_batch")
    @patch("database.get_request_match")
    @patch("match_detail.can_view_import", return_value=True)
    def test_load_match_detail_builds_single_match_payload(
        self,
        _mock_can_view_import: MagicMock,
        mock_get_match: MagicMock,
        mock_enriched: MagicMock,
        _mock_get_request: MagicMock,
        _mock_get_message: MagicMock,
    ) -> None:
        mock_get_match.return_value = {
            "id": MATCH_ID,
            "request_id": REQUEST_ID,
            "offer_id": "offer-1",
            "import_log_id": IMPORT_ID,
            "match_strength": "strong",
            "match_reason": "Reference match: 126500LN",
            "created_at": "2026-06-27T12:00:00+00:00",
        }
        mock_enriched.return_value = [ENRICHED_MATCH]

        detail = load_match_detail(ADMIN_USER, MATCH_ID)

        assert detail is not None
        assert detail["match_id"] == MATCH_ID
        assert detail["offer"]["raw_message"] == MESSAGE["raw_text"]
        assert detail["deal"]["potential_profit"] == "$45,000"
        mock_get_match.assert_called_once_with(MATCH_ID)
        mock_enriched.assert_called_once()
