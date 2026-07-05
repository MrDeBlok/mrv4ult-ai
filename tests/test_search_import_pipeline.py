"""P0 regression: freshly imported offers must be searchable by reference."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contact_classification import CONTACT_TYPE_DEALER
from ingest import ingest_message
from search import search_offers

FRESH_OFFER_MESSAGE = "Rolex Submariner 126610LN New full set 12700 eur"
FRESH_OFFER_REFERENCE = "126610LN"


def _mock_offers_response(offers: list[dict[str, Any]]) -> MagicMock:
    mock_client = MagicMock()
    mock_execute = MagicMock()
    mock_execute.data = offers
    mock_eq = MagicMock()
    mock_eq.execute.return_value = mock_execute
    mock_select = MagicMock()
    mock_select.eq.return_value = mock_eq
    mock_table = MagicMock()
    mock_table.select.return_value = mock_select
    mock_client.table.return_value = mock_table
    return mock_client


class TestFreshImportSearchPipeline:
    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log", return_value={"id": "log-fresh-1"})
    @patch("ingest.insert_message", return_value={"id": "message-fresh-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.contact_type_column_supported", return_value=True)
    def test_fresh_import_offer_is_searchable_by_exact_reference(
        self,
        _mock_contact_type_supported: MagicMock,
        _mock_find_group: MagicMock,
        _mock_insert_message: MagicMock,
        mock_insert_import_log: MagicMock,
        _mock_active_offers: MagicMock,
        _mock_matches: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unknown_brands: MagicMock,
        _mock_unknown_nicknames: MagicMock,
    ) -> None:
        captured: dict[str, Any] = {
            "watch_row": None,
            "offer_row": None,
            "dealer_contact_type": CONTACT_TYPE_DEALER,
        }

        def mock_find_or_create_watch(**kwargs: Any) -> tuple[dict[str, Any], bool]:
            captured["watch_row"] = {
                "id": "watch-fresh-1",
                "brand": kwargs.get("brand"),
                "reference": kwargs.get("reference"),
                "model": kwargs.get("model"),
                "dial": kwargs.get("dial"),
                "bracelet": kwargs.get("bracelet"),
            }
            return captured["watch_row"], True

        def mock_insert_offer(**kwargs: Any) -> tuple[dict[str, Any], bool]:
            captured["offer_row"] = {
                "id": "offer-fresh-1",
                "status": "active",
                "watch_id": kwargs["watch_id"],
                "dealer_id": kwargs["dealer_id"],
                "condition": kwargs.get("condition"),
                "usd_price": kwargs.get("usd_price"),
                "original_price": kwargs.get("original_price"),
                "original_currency": kwargs.get("original_currency"),
            }
            return captured["offer_row"], True

        def mock_find_or_create_dealer(
            whatsapp_number: str,
            *,
            display_name: str | None = None,
            default_contact_type: str = CONTACT_TYPE_DEALER,
        ) -> tuple[str, str]:
            del whatsapp_number, display_name, default_contact_type
            captured["dealer_contact_type"] = CONTACT_TYPE_DEALER
            return "dealer-1", CONTACT_TYPE_DEALER

        with (
            patch("ingest.find_or_create_watch", side_effect=mock_find_or_create_watch),
            patch("ingest.insert_offer", side_effect=mock_insert_offer),
            patch("ingest.find_or_create_dealer", side_effect=mock_find_or_create_dealer),
        ):
            summary = ingest_message(
                FRESH_OFFER_MESSAGE,
                group_name="HK Dealers",
                dealer_whatsapp="+85291234567",
            )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        assert mock_insert_import_log.called
        assert captured["offer_row"] is not None
        assert captured["offer_row"]["status"] == "active"
        assert captured["watch_row"] is not None
        assert captured["watch_row"]["reference"] == FRESH_OFFER_REFERENCE

        search_offer = {
            "watch_id": captured["offer_row"]["watch_id"],
            "original_price": captured["offer_row"]["original_price"],
            "original_currency": captured["offer_row"]["original_currency"],
            "usd_price": captured["offer_row"]["usd_price"],
            "condition": captured["offer_row"]["condition"],
            "messages": {"id": "message-fresh-1"},
            "watches": {
                "brand": captured["watch_row"]["brand"],
                "reference": captured["watch_row"]["reference"],
                "model": captured["watch_row"]["model"],
                "dial": captured["watch_row"]["dial"],
                "bracelet": captured["watch_row"]["bracelet"],
            },
            "dealers": {
                "display_name": "HK Dealer",
                "contact_type": captured["dealer_contact_type"],
                "whatsapp_id": "+85291234567",
            },
        }

        with (
            patch("search.get_client", return_value=_mock_offers_response([search_offer])),
            patch("search.contact_type_column_supported", return_value=True),
        ):
            offers, _ = search_offers(FRESH_OFFER_REFERENCE)

        assert len(offers) == 1
        assert offers[0]["watch_id"] == "watch-fresh-1"
        assert offers[0]["watch"]["reference"] == FRESH_OFFER_REFERENCE

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log", return_value={"id": "log-fresh-2"})
    @patch("ingest.insert_message", return_value={"id": "message-fresh-2"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.contact_type_column_supported", return_value=True)
    def test_unknown_contact_type_offer_is_still_searchable(
        self,
        _mock_contact_type_supported: MagicMock,
        _mock_find_group: MagicMock,
        _mock_insert_message: MagicMock,
        _mock_insert_import_log: MagicMock,
        _mock_active_offers: MagicMock,
        _mock_matches: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unknown_brands: MagicMock,
        _mock_unknown_nicknames: MagicMock,
    ) -> None:
        from search_import_debug import trace_fresh_offer_searchability

        watch_row = {
            "id": "watch-fresh-2",
            "brand": "Rolex",
            "reference": FRESH_OFFER_REFERENCE,
            "model": "Submariner",
            "dial": None,
            "bracelet": None,
        }
        offer_row = {
            "id": "offer-fresh-2",
            "status": "active",
            "watch_id": watch_row["id"],
            "dealer_id": "dealer-1",
            "condition": "New",
            "usd_price": 13700,
            "original_price": 12700,
            "original_currency": "EUR",
        }
        dealer_row = {
            "display_name": "HK Dealer",
            "contact_type": "unknown",
            "whatsapp_id": "+85291234567",
        }
        search_offer = {
            **offer_row,
            "messages": {"id": "message-fresh-2"},
            "watches": {
                "brand": watch_row["brand"],
                "reference": watch_row["reference"],
                "model": watch_row["model"],
                "dial": watch_row["dial"],
                "bracelet": watch_row["bracelet"],
            },
            "dealers": dealer_row,
        }

        trace = trace_fresh_offer_searchability(
            reference_query=FRESH_OFFER_REFERENCE,
            import_summary={
                "import_log_id": "log-fresh-2",
                "watches_parsed": 1,
                "new_offers": 1,
            },
            offer_row=offer_row,
            watch_row=watch_row,
            dealer_row=dealer_row,
        )
        assert trace["search_should_include_offer"] is True
        assert trace["dealer_visible_in_search_with_offers_context"] is True
        assert trace["dealer_visible_in_search_without_offers_context"] is False

        with (
            patch("search.get_client", return_value=_mock_offers_response([search_offer])),
            patch("search.contact_type_column_supported", return_value=True),
        ):
            offers, _ = search_offers(FRESH_OFFER_REFERENCE)

        assert len(offers) == 1
        assert offers[0]["watch_id"] == watch_row["id"]
