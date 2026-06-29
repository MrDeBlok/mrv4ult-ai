"""Regression tests for brand-only conversational mentions vs real watch imports."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from activity_feed import filter_ignored_activity_imports, is_active_needs_review
from contact_classification import CONTACT_TYPE_DEALER
from import_classification import split_offer_watches, watch_has_substantive_identity
from ingest import ingest_message
from parser_review import is_parser_review_pending
from watch_parser import parse_message


BRAND_ONLY_MESSAGES = [
    "Pick up Patek today.",
    "Send the Rolex brochure.",
    "AP invoice attached.",
    "Meet at Rolex service center.",
]


class TestBrandMentionClassification:
    def test_brand_only_message_has_no_substantive_identity(self) -> None:
        for message in BRAND_ONLY_MESSAGES:
            parsed = parse_message(message)
            assert parsed["watches"], f"expected parser to detect brand in: {message}"
            assert watch_has_substantive_identity(parsed["watches"][0]) is False

    def test_brand_only_messages_classified_as_noise(self) -> None:
        for message in BRAND_ONLY_MESSAGES:
            parsed = parse_message(message)
            offer_watches, classification = split_offer_watches(message, parsed, parsed["watches"])
            assert offer_watches == []
            assert classification == "noise"

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
    def test_brand_mention_only_import_is_ignored(
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
        mock_insert_import_log.return_value = {"id": "log-brand-chat"}

        summary = ingest_message(
            "Pick up Patek today.",
            group_name="Chat",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "noise"
        assert summary["watches_parsed"] == 0
        assert summary["new_offers"] == 0
        mock_insert_offer.assert_not_called()

        import_log = {
            "id": "log-brand-chat",
            "status": summary["status"],
            "watches_parsed": 0,
            "new_offers": 0,
            "summary": {"status_reason": summary.get("status_reason", "")},
        }
        assert is_active_needs_review(import_log) is False
        assert import_log in filter_ignored_activity_imports([import_log])
        assert is_parser_review_pending(import_log) is False

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_real_incomplete_watch_still_needs_review(
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
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-incomplete"}

        summary = ingest_message(
            "Rolex Submariner 124060",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "warning"
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()

        import_log = {
            "id": "log-incomplete",
            "status": summary["status"],
            "watches_parsed": summary["watches_parsed"],
            "new_offers": summary["new_offers"],
            "summary": {
                "parsed_watches": summary["rows"],
                "status_reason": summary.get("status_reason", ""),
            },
        }
        assert is_active_needs_review(import_log) is True
        assert is_parser_review_pending(import_log) is True

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_normal_offer_still_works(
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
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-offer"}

        summary = ingest_message(
            "ROLEX 126200 green jub n6/26 74000usd",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()

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
    def test_buyer_request_still_works(
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
        mock_insert_import_log.return_value = {"id": "log-wtb"}

        summary = ingest_message(
            "WTB Rolex Daytona 116500LN budget 145k",
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        assert summary["new_offers"] == 0
        mock_insert_offer.assert_not_called()
