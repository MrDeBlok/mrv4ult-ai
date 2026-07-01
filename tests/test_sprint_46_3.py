"""Tests for Sprint 46.3 sold-order WTB classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_deal_analysis_cards
from contact_classification import CONTACT_TYPE_DEALER
from import_classification import (
    enrich_sold_order_watches,
    is_sold_order_message,
    sold_order_has_actionable_identity,
    split_offer_watches,
)
from ingest import ingest_message
from market_requests import build_market_request_row, market_request_intent_meta
from watch_parser import parse_message


class TestSoldOrderDetection:
    def test_detects_sold_order_phrases(self) -> None:
        assert is_sold_order_message("Sold order 126610LN") is True
        assert is_sold_order_message("sold-order Rolex 126610LN") is True
        assert is_sold_order_message("soldorder AP 15500") is True
        assert is_sold_order_message("Sold for client 5711 blue") is True

    def test_split_offer_watches_classifies_sold_order_as_request_intent(self) -> None:
        text = "Sold order 126610LN"
        parsed = parse_message(text)
        watches = enrich_sold_order_watches(parsed["watches"])

        offer_watches, classification = split_offer_watches(text, parsed, watches)

        assert classification == "request_intent"
        assert offer_watches == []

    def test_sold_order_rolex_message_extracts_brand_and_reference(self) -> None:
        parsed = parse_message("Sold order Rolex Submariner 126610LN")

        assert parsed["message_type"] == "request"
        assert parsed["watches"]
        assert parsed["watches"][0].get("reference") == "126610LN"
        assert parsed["watches"][0].get("brand") == "Rolex"

    def test_unknown_brand_with_known_reference_infers_brand(self) -> None:
        parsed = parse_message("Sold order 126610LN")
        enriched = enrich_sold_order_watches(parsed["watches"])

        assert enriched[0]["reference"] == "126610LN"
        assert enriched[0]["brand"] == "Rolex"
        assert sold_order_has_actionable_identity(enriched) is True

    def test_ambiguous_sold_order_without_reference_needs_review(self) -> None:
        parsed = parse_message("Sold order need urgently")
        watches = enrich_sold_order_watches(parsed["watches"])

        assert sold_order_has_actionable_identity(watches) is False


class TestSoldOrderIngest:
    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_sold_order_creates_market_request_not_offer(
        self,
        _mock_find_dealer: MagicMock,
        _mock_find_group: MagicMock,
        _mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        _mock_get_active_offers: MagicMock,
        _mock_process_matches: MagicMock,
        _mock_record_notifications: MagicMock,
        _mock_unknown_brands: MagicMock,
        _mock_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_import_log.return_value = {"id": "log-sold-1"}

        summary = ingest_message(
            "Sold order 126610LN",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "request_intent"
        assert summary["import_classification"] == "request_intent"
        assert summary["request_intent_kind"] == "sold_order"
        assert summary["request_urgency"] == "high"
        assert summary.get("request_intent_needs_review") is not True
        assert "Sold order detected" in summary["status_reason"]
        assert summary["new_offers"] == 0
        mock_insert_offer.assert_not_called()
        mock_find_watch.assert_not_called()
        assert summary["parsed_watches"][0]["brand"] == "Rolex"
        assert summary["parsed_watches"][0]["reference"] == "126610LN"

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_ambiguous_sold_order_becomes_wtb_needs_review(
        self,
        _mock_find_dealer: MagicMock,
        _mock_find_group: MagicMock,
        _mock_insert_message: MagicMock,
        _mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        _mock_get_active_offers: MagicMock,
        _mock_process_matches: MagicMock,
        _mock_record_notifications: MagicMock,
        _mock_unknown_brands: MagicMock,
        _mock_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_import_log.return_value = {"id": "log-sold-review"}

        summary = ingest_message(
            "Sold order need urgently",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "request_intent"
        assert summary["request_intent_needs_review"] is True
        assert "WTB needs review" in summary["status_reason"]
        assert summary["status"] != "warning"
        mock_insert_offer.assert_not_called()


class TestSoldOrderPresentation:
    def test_market_request_badges_for_sold_order(self) -> None:
        import_log = {
            "id": "log-1",
            "message_id": "msg-1",
            "import_time": "2026-06-27T12:00:00+00:00",
            "group_name": "HK Dealers",
            "dealer_alias": "Dealer A",
            "dealer_whatsapp": "+85291234567",
            "summary": {
                "parsed_watches": [
                    {"brand": "Rolex", "reference": "126610LN", "model": "Submariner Date"}
                ],
                "request_intent_kind": "sold_order",
                "request_urgency": "high",
            },
        }

        meta = market_request_intent_meta(import_log)
        row = build_market_request_row(
            import_log,
            {"raw_text": "Sold order 126610LN"},
        )

        assert meta["intent_kind"] == "sold_order"
        assert {badge["label"] for badge in meta["badges"]} == {"Sold order", "Urgent"}
        assert row["activity_url"] == "/activity/log-1"
        assert row["brand"] == "Rolex"
        assert row["reference"] == "126610LN"

    def test_sold_order_does_not_trigger_deal_analysis(self) -> None:
        summary = {
            "import_classification": "request_intent",
            "request_intent_kind": "sold_order",
            "parsed_watches": [
                {"brand": "Rolex", "reference": "126610LN", "usd_price": None},
            ],
            "rows": [],
        }

        assert build_deal_analysis_cards(summary) == []

    @patch("app.load_market_request_rows")
    def test_market_requests_page_shows_sold_order_badges(
        self,
        mock_load_rows: MagicMock,
    ) -> None:
        mock_load_rows.return_value = [
            {
                "id": "log-1",
                "detail_url": "/market-requests/log-1",
                "import_time": "Jun 27, 2026",
                "brand": "Rolex",
                "model": "Submariner Date",
                "reference": "126610LN",
                "nickname": "N/A",
                "budget": "N/A",
                "source_contact": "Dealer A",
                "source_whatsapp": "+85291234567",
                "group_name": "HK Dealers",
                "message_preview": "Sold order 126610LN",
                "badges": [
                    {"label": "Sold order", "class": "warning"},
                    {"label": "Urgent", "class": "danger"},
                ],
            }
        ]

        client = TestClient(app)
        response = client.get("/market-requests")

        assert response.status_code == 200
        assert "Sold order" in response.text
        assert "Urgent" in response.text
