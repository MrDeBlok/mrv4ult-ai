"""Tests for collapsible Admin Debug on the activity detail Deal Analysis UI."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app, build_activity_detail
from condition_normalizer import NEW_CONDITION
from tests.conftest import TRADER_ONE

OFFER_CURRENT = "11111111-1111-4111-8111-111111111111"
OFFER_COMP_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OFFER_COMP_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
WATCH_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

ADMIN_DEBUG_DETAILS_RE = re.compile(r'<details class="deal-analysis-debug">')
ADMIN_DEBUG_OPEN_RE = re.compile(r'<details class="deal-analysis-debug"[^>]*\bopen\b')

DEAL_DEBUG_FIELD_LABELS = (
    "watch_id",
    "brand",
    "reference",
    "normalized condition",
    "market_condition",
    "active comparables (before filter)",
    "active comparables (after filter)",
    "comparable offer IDs",
    "comparable prices",
    "comparable conditions",
    "parser confidence",
    "market price confidence",
    "Market Price eligible",
    "exclusion reasons",
    "configured threshold",
    "market price unknown reason",
)


def _import_log() -> dict:
    return {
        "id": "log-debug-ui",
        "message_id": "msg-debug-ui",
        "import_time": "2026-07-16T10:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+85291234567",
        "watches_parsed": 1,
        "new_offers": 1,
        "duplicate_offers": 0,
        "summary": {
            "status": "success",
            "rows": [
                {
                    "brand": "Rolex",
                    "reference": "126331",
                    "condition": NEW_CONDITION,
                    "raw_condition": NEW_CONDITION,
                    "usd_price": 23_000,
                    "previous_lowest_usd": "$24,000",
                    "price_label": "Good price",
                    "rank": "2",
                    "market_condition": NEW_CONDITION,
                    "offer_id": OFFER_CURRENT,
                }
            ],
            "parsed_watches": [
                {
                    "brand": "Rolex",
                    "reference": "126331",
                    "condition": NEW_CONDITION,
                }
            ],
        },
    }


def _mock_comparable_lookup(mock_get_offers: MagicMock, mock_get_active_offers: MagicMock) -> None:
    mock_get_offers.return_value = {OFFER_CURRENT: {"watch_id": WATCH_ID}}
    mock_get_active_offers.return_value = [
        (OFFER_CURRENT, 23_000, NEW_CONDITION),
        (OFFER_COMP_1, 24_000, NEW_CONDITION),
        (OFFER_COMP_2, 24_500, NEW_CONDITION),
    ]


@patch("ingest._get_active_offers")
@patch("deal_market_lookup.get_offers_by_ids")
@patch("app._can_access_import_log", return_value=True)
@patch("app.get_message_by_id")
@patch("app.get_import_log")
class TestActivityDetailAdminDebugUI:
    def test_admin_debug_is_collapsible_details_collapsed_by_default(
        self,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        mock_get_offers: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        _mock_comparable_lookup(mock_get_offers, mock_get_active_offers)
        mock_get_import_log.return_value = _import_log()
        mock_get_message.return_value = {"raw_text": "Rolex 126331 new $23k"}

        response = TestClient(app).get("/activity/log-debug-ui")

        assert response.status_code == 200
        html = response.text
        assert ADMIN_DEBUG_DETAILS_RE.search(html)
        assert not ADMIN_DEBUG_OPEN_RE.search(html)
        assert "<summary>Admin debug</summary>" in html

    def test_admin_debug_keeps_all_existing_fields(
        self,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        mock_get_offers: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        _mock_comparable_lookup(mock_get_offers, mock_get_active_offers)
        mock_get_import_log.return_value = _import_log()
        mock_get_message.return_value = {"raw_text": "Rolex 126331 new $23k"}

        response = TestClient(app).get("/activity/log-debug-ui")

        assert response.status_code == 200
        html = response.text
        for label in DEAL_DEBUG_FIELD_LABELS:
            assert label in html, f"missing debug field label: {label}"
        assert WATCH_ID in html
        assert OFFER_COMP_1 in html
        assert OFFER_COMP_2 in html
        assert f"<li>{OFFER_COMP_1}</li>" in html
        assert f"<li>{OFFER_COMP_2}</li>" in html

    def test_deal_analysis_summary_stays_visible_outside_debug(
        self,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        mock_get_offers: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        _mock_comparable_lookup(mock_get_offers, mock_get_active_offers)
        mock_get_import_log.return_value = _import_log()
        mock_get_message.return_value = {"raw_text": "Rolex 126331 new $23k"}

        response = TestClient(app).get("/activity/log-debug-ui")

        assert response.status_code == 200
        html = response.text
        assert "Offer price" in html
        assert "Market price" in html
        assert "Difference" in html
        assert "Difference %" in html
        assert "Market rank" in html
        assert "Recommendation" in html
        assert "Rolex" in html
        assert "126331" in html

    @pytest.mark.no_auto_login
    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_non_admin_does_not_see_admin_debug(
        self,
        _mock_user: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        mock_get_offers: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        _mock_comparable_lookup(mock_get_offers, mock_get_active_offers)
        mock_get_import_log.return_value = _import_log()
        mock_get_message.return_value = {"raw_text": "Rolex 126331 new $23k"}

        response = TestClient(app).get("/activity/log-debug-ui")

        assert response.status_code == 200
        html = response.text
        assert "Admin debug" not in html
        assert "deal-analysis-debug" not in html
        assert OFFER_COMP_1 not in html
        assert "Deal analysis" in html
        assert "Recommendation" in html


class TestBuildActivityDetailAdminDebug:
    @patch("ingest._get_active_offers")
    @patch("deal_market_lookup.get_offers_by_ids")
    def test_show_deal_debug_populates_debug_for_admin_rendering(
        self,
        mock_get_offers: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        _mock_comparable_lookup(mock_get_offers, mock_get_active_offers)
        detail = build_activity_detail(
            _import_log(),
            {"raw_text": "Rolex 126331 new $23k"},
            show_deal_debug=True,
        )

        assert detail["show_deal_debug"] is True
        assert detail["deal_analyses"]
        debug = detail["deal_analyses"][0]["debug"]
        assert debug["watch_id"] == WATCH_ID
        assert OFFER_COMP_1 in debug["comparable_offer_ids"]
        assert OFFER_COMP_2 in debug["comparable_offer_ids"]

    def test_show_deal_debug_false_omits_debug_payload(self) -> None:
        detail = build_activity_detail(
            _import_log(),
            {"raw_text": "Rolex 126331 new $23k"},
            show_deal_debug=False,
        )

        assert detail["show_deal_debug"] is False
        assert "debug" not in detail["deal_analyses"][0]
