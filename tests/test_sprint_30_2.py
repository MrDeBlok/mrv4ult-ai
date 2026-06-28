"""Integration tests for Sprint 30.2 import pipeline price handling."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from contact_classification import CONTACT_TYPE_DEALER
from evolution_webhook import handle_evolution_webhook
from import_pipeline import trace_import_pipeline
from ingest import ingest_message
from whatsapp_collector import WhatsAppMessage, collect_message


EXAMPLE_MESSAGE = "*10.600 Euro 2024 Used Rolex Explorer 36mm 124273* Full Set"


class TestImportPipelineTrace:
    def test_trace_shows_price_survives_line_cleaning_and_parsing(self) -> None:
        trace = trace_import_pipeline(EXAMPLE_MESSAGE)

        assert trace["cleaned_lines"] == [
            "10.600 Euro 2024 Used Rolex Explorer 36mm 124273 Full Set"
        ]
        watch = trace["normalized_watches"][0]
        assert watch["original_price"] == 10_600
        assert watch["original_currency"] == "EUR"
        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "124273"

    def test_number_prefix_does_not_strip_european_thousands(self) -> None:
        from watch_parser import NUMBER_PREFIX

        line = "10.600 Euro 2024 Used Rolex Explorer 36mm 124273 Full Set"
        assert NUMBER_PREFIX.sub("", line) == line
        assert NUMBER_PREFIX.sub("", "1. Rolex 126500LN 305k") == "Rolex 126500LN 305k"


class TestEuropeanPriceImportIntegration:
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
    def test_ingest_message_stores_european_price_for_markdown_message(
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
            {
                "id": "watch-1",
                "brand": "Rolex",
                "reference": "124273",
                "model": "Explorer",
            },
            True,
        )
        mock_insert_offer.return_value = (
            {
                "id": "offer-1",
                "original_price": 10_600,
                "original_currency": "EUR",
                "usd_price": 11448,
            },
            True,
        )
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            EXAMPLE_MESSAGE,
            group_name="EU Dealers",
            dealer_whatsapp="+31612345678",
            dealer_alias="Euro Dealer",
        )

        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        assert summary["status"] != "warning"
        assert summary["rows"][0]["original_price"] == 10_600
        assert summary["rows"][0]["original_currency"] == "EUR"
        assert summary["rows"][0]["brand"] == "Rolex"
        assert summary["rows"][0]["reference"] == "124273"

        offer_kwargs = mock_insert_offer.call_args.kwargs
        assert offer_kwargs["original_price"] == 10_600
        assert offer_kwargs["original_currency"] == "EUR"
        assert offer_kwargs["usd_price"] == 11_448

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
    def test_collect_message_preserves_usd_imports(
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
        mock_find_watch.return_value = ({"id": "watch-2", "brand": "Rolex", "reference": "126500LN"}, True)
        mock_insert_offer.return_value = (
            {"id": "offer-2", "original_price": 305_000, "original_currency": "USD"},
            True,
        )
        mock_insert_import_log.return_value = {"id": "log-2"}

        summary = collect_message(
            WhatsAppMessage(
                group_name="HK Dealers",
                dealer_whatsapp="+85291234567",
                dealer_alias="HK Dealer",
                message_text="126500LN 305k usd",
                received_at=datetime(2026, 6, 27, tzinfo=timezone.utc),
            )
        )

        assert summary["rows"][0]["original_price"] == 305_000
        assert summary["rows"][0]["original_currency"] == "USD"
        assert mock_insert_offer.call_args.kwargs["original_price"] == 305_000

    @patch("whatsapp_collector.ingest_message")
    def test_webhook_flow_passes_raw_message_to_ingest(self, mock_ingest: MagicMock) -> None:
        mock_ingest.return_value = {
            "group": "EU Dealers",
            "dealer_whatsapp": "+31612345678",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-1",
        }
        payload = {
            "event": "messages.upsert",
            "instance": "mrv4ult",
            "data": {
                "key": {
                    "remoteJid": "120363000000000000@g.us",
                    "fromMe": False,
                    "participant": "31612345678@s.whatsapp.net",
                    "participantAlt": "+31612345678",
                },
                "message": {"conversation": EXAMPLE_MESSAGE},
                "messageTimestamp": 1719496800,
                "pushName": "Euro Dealer",
            },
        }

        result = handle_evolution_webhook(payload)

        assert result["status"] == "imported"
        mock_ingest.assert_called_once()
        assert mock_ingest.call_args.args[0] == EXAMPLE_MESSAGE

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
    def test_full_webhook_to_offer_payload_integration(
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
            {"id": "watch-1", "brand": "Rolex", "reference": "124273", "model": "Explorer"},
            True,
        )
        mock_insert_offer.return_value = (
            {"id": "offer-1", "original_price": 10_600, "original_currency": "EUR", "usd_price": 11448},
            True,
        )
        mock_insert_import_log.return_value = {"id": "log-1"}

        payload = {
            "event": "messages.upsert",
            "instance": "mrv4ult",
            "data": {
                "key": {
                    "remoteJid": "120363000000000000@g.us",
                    "fromMe": False,
                    "participant": "31612345678@s.whatsapp.net",
                    "participantAlt": "+31612345678",
                },
                "message": {"conversation": EXAMPLE_MESSAGE},
                "messageTimestamp": 1719496800,
                "pushName": "Euro Dealer",
            },
        }

        result = handle_evolution_webhook(payload)

        assert result["status"] == "imported"
        offer_kwargs = mock_insert_offer.call_args.kwargs
        assert offer_kwargs["original_price"] == 10_600
        assert offer_kwargs["original_currency"] == "EUR"
        assert mock_find_watch.call_args.kwargs["brand"] == "Rolex"
        assert mock_find_watch.call_args.kwargs["reference"] == "124273"
