"""Tests for Sprint 48.1 glued WTB intent routing and reference parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import build_deal_analysis_cards
from contact_classification import CONTACT_TYPE_DEALER
from import_classification import split_offer_watches
from ingest import ingest_message
from market_requests import build_market_request_row
from watch_parser import _normalize_glued_intent_prefixes, parse_message

GLUED_WTB_MESSAGE = (
    "WTB126334 Black/ Diamond/Jubilee New Unworn/ RLX Story Invoice 01.07.26 for 14.5k€+label"
)
WTS_MESSAGE = "WTS126334 Black/ Diamond/Jubilee New Unworn 14.5k€"
FS_MESSAGE = "FS126334 Black/ Diamond/Jubilee New Unworn 14.5k€"


class TestGluedIntentNormalization:
    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_glued_wtb_message_creates_market_request_not_offer(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_message.return_value = {"id": "message-1"}
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            GLUED_WTB_MESSAGE,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "request_intent"
        assert summary["import_classification"] == "request_intent"
        assert summary["new_offers"] == 0
        assert summary["rows"] == []
        mock_insert_offer.assert_not_called()

    def test_reference_126334_is_extracted(self) -> None:
        parsed = parse_message(GLUED_WTB_MESSAGE)

        assert parsed["message_type"] == "request"
        assert parsed["watches"]
        assert parsed["watches"][0]["reference"] == "126334"
        assert parsed["watches"][0]["brand"] == "Rolex"

    def test_budget_is_14500_eur(self) -> None:
        watch = parse_message(GLUED_WTB_MESSAGE)["watches"][0]

        assert watch["original_price"] == 14500
        assert watch["original_currency"] == "EUR"

    def test_condition_new_is_extracted_from_new_unworn(self) -> None:
        watch = parse_message(GLUED_WTB_MESSAGE)["watches"][0]

        assert watch["condition"] == "New"

    def test_no_deal_analysis_cards_for_glued_wtb_import(self) -> None:
        parsed = parse_message(GLUED_WTB_MESSAGE)
        summary = {
            "status": "request_intent",
            "import_classification": "request_intent",
            "parsed_watches": parsed["watches"],
            "rows": [],
        }

        assert build_deal_analysis_cards(summary) == []

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_wts_glued_reference_still_creates_offer(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-wts"}

        summary = ingest_message(
            WTS_MESSAGE,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_fs_glued_reference_still_creates_offer(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-fs"}

        summary = ingest_message(
            FS_MESSAGE,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()

    def test_market_request_row_shows_budget_and_reference(self) -> None:
        parsed = parse_message(GLUED_WTB_MESSAGE)
        import_log = {
            "id": "log-wtb-126334",
            "import_time": "2026-07-01T10:00:00+00:00",
            "group_name": "HK Dealers",
            "dealer_alias": "HK Dealer",
            "dealer_whatsapp": "+85291234567",
            "status": "request_intent",
            "summary": {
                "import_classification": "request_intent",
                "parsed_watches": parsed["watches"],
            },
        }

        row = build_market_request_row(import_log, {"raw_text": GLUED_WTB_MESSAGE})

        assert row["reference"] == "126334"
        assert row["budget"] == "€14,500"
        assert row["brand"] == "Rolex"

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_existing_spaced_wtb_still_routes_to_market_request(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_message.return_value = {"id": "message-1"}
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "WTB Rolex Daytona 116500LN budget 30k",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "request_intent"
        mock_insert_offer.assert_not_called()


class TestGluedIntentTokenSplitting:
    def test_all_glued_intent_tokens_are_split(self) -> None:
        cases = {
            "WTB126334": "WTB 126334",
            "LTB126334": "LTB 126334",
            "LF126334": "LF 126334",
            "NEED126334": "NEED 126334",
            "WTS126334": "WTS 126334",
            "FS126334": "FS 126334",
        }
        for raw, expected in cases.items():
            assert _normalize_glued_intent_prefixes(raw) == expected

    def test_wts_glued_message_stays_offer_path(self) -> None:
        parsed = parse_message(WTS_MESSAGE)
        offer_watches, classification = split_offer_watches(WTS_MESSAGE, parsed, parsed["watches"])

        assert classification is None
        assert offer_watches
        assert offer_watches[0]["reference"] == "126334"
