"""Tests for Sprint 32.4 noise and WTB import classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from contact_classification import CONTACT_TYPE_DEALER
from import_classification import (
    is_buyer_request_message,
    is_noise_watch,
    split_offer_watches,
)
from ingest import ingest_message
from watch_parser import parse_message


def _ingest_patches():
    return (
        patch("ingest.record_unknown_nicknames_for_watches", return_value=[]),
        patch("ingest.record_unknown_brands_for_watches", return_value=[]),
        patch("ingest.record_import_notifications"),
        patch("ingest.process_offer_request_matches", return_value=[]),
        patch("ingest._get_active_offers", return_value=[]),
        patch("ingest.insert_import_log"),
        patch("ingest.insert_offer"),
        patch("ingest.find_or_create_watch"),
        patch("ingest.insert_message"),
        patch("ingest.find_or_create_group", return_value="group-1"),
        patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER)),
    )


class TestNoiseClassification:
    def test_price_only_line_is_noise(self) -> None:
        parsed = parse_message("10.600 Euro")
        watch = parsed["watches"][0]

        assert is_noise_watch(watch) is True

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
    def test_numeric_only_message_ignored(
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

        summary = ingest_message("14k", group_name="Chat", dealer_whatsapp="+31612345678")

        assert summary["status"] == "no_watch_detected"
        assert summary["saved"] is False
        assert summary["import_log_id"] is None
        mock_insert_message.assert_not_called()
        mock_insert_import_log.assert_not_called()
        mock_insert_offer.assert_not_called()

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
    def test_price_only_message_classified_as_noise(
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

        summary = ingest_message("10.600 Euro", group_name="EU Dealers", dealer_whatsapp="+31612345678")

        assert summary["status"] == "noise"
        assert summary["new_offers"] == 0
        mock_insert_offer.assert_not_called()
        mock_record_notifications.assert_not_called()

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
    def test_dutch_chat_with_amount_classified_as_noise(
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
            "Ik las 14k uur",
            group_name="Chat",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "noise"
        mock_insert_offer.assert_not_called()

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
    def test_dutch_chat_without_watch_line_is_not_needs_review(
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
            "Ik kan 10.5 doen",
            group_name="Chat",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] != "warning"
        mock_insert_offer.assert_not_called()

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
    def test_english_chat_with_amount_classified_as_noise(
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
            "En die is 14,000$",
            group_name="Chat",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "noise"
        mock_insert_offer.assert_not_called()


class TestBuyerRequestClassification:
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
    def test_wtb_message_classified_as_request_intent(
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
            "WTB Rolex Daytona",
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        assert summary["new_offers"] == 0
        assert summary["parsed_watches"]
        mock_insert_offer.assert_not_called()
        mock_process_matches.assert_not_called()
        mock_record_notifications.assert_not_called()

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
    def test_want_to_buy_message_classified_as_request_intent(
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
            "Want to buy 126500LN",
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        mock_insert_offer.assert_not_called()

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
    def test_looking_for_message_classified_as_request_intent(
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
            "Looking for AP 15510",
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        mock_insert_offer.assert_not_called()

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
    def test_lf_message_classified_as_request_intent(
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
            "LF Patek 5711",
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        mock_insert_offer.assert_not_called()

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
    def test_iso_message_classified_as_request_intent(
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
            "ISO Pepsi",
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        mock_insert_offer.assert_not_called()


class TestRealOfferClassification:
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
    def test_real_offer_without_price_goes_to_needs_review(
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
        mock_find_watch.return_value = (
            {"id": "watch-1", "brand": "Rolex", "reference": "124060", "model": "Submariner"},
            True,
        )
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "Rolex Submariner 124060",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "warning"
        assert summary["new_offers"] == 0
        mock_insert_offer.assert_not_called()

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
    def test_real_offer_with_price_creates_offer(
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
        mock_find_watch.return_value = (
            {"id": "watch-1", "brand": "Rolex", "reference": "124060", "model": "Submariner"},
            True,
        )
        mock_insert_offer.return_value = (
            {"id": "offer-1", "original_price": 14500, "original_currency": "USD"},
            True,
        )
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "Rolex Submariner 124060 14500 usd",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "warning"
        assert summary["new_offers"] == 1
        assert summary.get("parser_quality", {}).get("failed_fields") == ["condition"]
        mock_insert_offer.assert_called_once()


class TestImportClassificationHelpers:
    def test_split_offer_watches_detects_request_intent(self) -> None:
        parsed = parse_message("ISO Pepsi")
        offer_watches, classification = split_offer_watches("ISO Pepsi", parsed, parsed["watches"])

        assert classification == "request_intent"
        assert offer_watches == []

    def test_is_buyer_request_message_detects_want_to_buy(self) -> None:
        parsed = parse_message("Want to buy 126500LN")

        assert is_buyer_request_message("Want to buy 126500LN", parsed) is True
