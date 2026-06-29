"""Regression tests for normal offer ingest vs discard/request_intent paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from activity_feed import filter_active_activity_imports
from app import app
from contact_classification import CONTACT_TYPE_DEALER
from import_classification import is_buyer_request_message, split_offer_watches
from ingest import ingest_message
from watch_parser import parse_message


OFFER_MESSAGE = "ROLEX 126200 green jub n6/26 74000usd"
OFFER_WITH_NEED_PHRASE = (
    "No need to negotiate\nROLEX 126500LN black dial 145000usd"
)
WTB_MESSAGE = "WTB Rolex Daytona 116500LN budget 145k"
CHAT_MESSAGE = "Hey, are we still meeting for lunch tomorrow?"


class TestIngestOfferRegression:
    def test_dealer_offer_with_need_phrase_is_not_buyer_request(self) -> None:
        parsed = parse_message(OFFER_WITH_NEED_PHRASE)

        assert parsed["message_type"] in {"offer", "offer_list"}
        assert is_buyer_request_message(OFFER_WITH_NEED_PHRASE, parsed) is False

        offer_watches, classification = split_offer_watches(
            OFFER_WITH_NEED_PHRASE,
            parsed,
            parsed["watches"],
        )

        assert classification is None
        assert offer_watches

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
    def test_normal_offer_import_is_saved(
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
        mock_insert_import_log.return_value = {"id": "log-offer-1"}

        summary = ingest_message(
            OFFER_MESSAGE,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        assert summary["import_log_id"] == "log-offer-1"
        mock_insert_message.assert_called_once()
        mock_insert_import_log.assert_called_once()
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
    def test_dealer_offer_with_need_phrase_still_creates_offers(
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
        mock_insert_import_log.return_value = {"id": "log-offer-2"}

        summary = ingest_message(
            OFFER_WITH_NEED_PHRASE,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] in {"success", "warning"}
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()
        mock_insert_import_log.assert_called_once()

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
    def test_no_watch_detected_is_discarded(
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
        summary = ingest_message(
            CHAT_MESSAGE,
            group_name="Family Chat",
            dealer_whatsapp="+31612345678",
        )

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
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_request_intent_is_saved_without_offers(
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
        mock_insert_import_log.return_value = {"id": "log-wtb-1"}

        summary = ingest_message(
            WTB_MESSAGE,
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        assert summary["new_offers"] == 0
        assert summary["import_log_id"] == "log-wtb-1"
        mock_insert_message.assert_called_once()
        mock_insert_import_log.assert_called_once()
        mock_insert_offer.assert_not_called()

    def test_normal_offer_appears_in_activity(self) -> None:
        import_log = {
            "id": "log-offer-1",
            "status": "success",
            "watches_parsed": 1,
            "new_offers": 1,
            "message_id": "msg-1",
            "group_name": "HK Dealers",
            "dealer_alias": "Dealer A",
            "dealer_whatsapp": "+85291234567",
            "import_time": "2026-06-27T12:00:00+00:00",
            "summary": {},
        }

        assert [row["id"] for row in filter_active_activity_imports([import_log])] == ["log-offer-1"]

    @patch("app.list_import_logs")
    def test_normal_offer_appears_on_activity_page(self, mock_list_import_logs: MagicMock) -> None:
        mock_list_import_logs.return_value = [
            {
                "id": "log-offer-1",
                "status": "success",
                "watches_parsed": 1,
                "new_offers": 1,
                "message_id": "msg-1",
                "group_name": "HK Dealers",
                "dealer_alias": "Dealer A",
                "dealer_whatsapp": "+85291234567",
                "import_time": "2026-06-27T12:00:00+00:00",
                "summary": {},
            }
        ]

        client = TestClient(app)
        response = client.get("/activity")

        assert response.status_code == 200
        assert 'data-href="/activity/log-offer-1"' in response.text
