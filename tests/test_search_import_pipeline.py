"""P0 regression: freshly imported offers must be searchable by reference."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contact_classification import CONTACT_TYPE_DEALER
from ingest import ingest_message
from search import search_offers
from search_import_debug import trace_fresh_offer_searchability

FRESH_OFFER_MESSAGE = "Rolex Submariner 126610LN New full set 12700 eur"
FRESH_OFFER_REFERENCE = "126610LN"


from tests.search_mock_helpers import mock_search_offers_client


def _mock_offers_response(
    offers: list[dict[str, Any]],
    *,
    total_count: int | None = None,
) -> MagicMock:
    return mock_search_offers_client(offers, total_count=total_count)


class TestFreshImportSearchPipeline:
    def test_fresh_import_offer_is_searchable_by_exact_reference(self) -> None:
        captured: dict[str, Any] = {"watch_row": None, "offer_row": None}

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

        with (
            patch("ingest.record_unknown_nicknames_for_watches", return_value=[]),
            patch("ingest.record_unknown_brands_for_watches", return_value=[]),
            patch("ingest.record_import_notifications"),
            patch("ingest.process_offer_request_matches", return_value=[]),
            patch("ingest._get_active_offers", return_value=[]),
            patch("ingest.insert_import_log", return_value={"id": "log-fresh-1"}),
            patch("ingest.insert_message", return_value={"id": "message-fresh-1"}),
            patch("ingest.find_or_create_group", return_value="group-1"),
            patch("ingest.contact_type_column_supported", return_value=True),
            patch("ingest.find_or_create_watch", side_effect=mock_find_or_create_watch),
            patch("ingest.insert_offer", side_effect=mock_insert_offer),
            patch(
                "ingest.find_or_create_dealer",
                return_value=("dealer-1", CONTACT_TYPE_DEALER),
            ),
        ):
            summary = ingest_message(
                FRESH_OFFER_MESSAGE,
                group_name="HK Dealers",
                dealer_whatsapp="+85291234567",
            )

        assert summary["status"] == "success"
        assert summary["new_offers"] == 1
        assert captured["watch_row"]["reference"] == FRESH_OFFER_REFERENCE

        search_offer = {
            "id": "offer-fresh-1",
            "watch_id": captured["offer_row"]["watch_id"],
            "dealer_id": "dealer-1",
            "original_price": captured["offer_row"]["original_price"],
            "original_currency": captured["offer_row"]["original_currency"],
            "usd_price": captured["offer_row"]["usd_price"],
            "condition": captured["offer_row"]["condition"],
            "messages": {"id": "message-fresh-1"},
            "watches": None,
            "dealers": None,
        }

        with (
            patch("search.get_client", return_value=_mock_offers_response([search_offer])),
            patch("database.get_watch_by_id", return_value=captured["watch_row"]),
            patch(
                "database.get_dealer_by_id",
                return_value={
                    "id": "dealer-1",
                    "display_name": "HK Dealer",
                    "contact_type": CONTACT_TYPE_DEALER,
                    "whatsapp_id": "+85291234567",
                },
            ),
            patch("search.contact_type_column_supported", return_value=True),
        ):
            offers, _ = search_offers(FRESH_OFFER_REFERENCE)

        assert len(offers) == 1
        assert offers[0]["watch"]["reference"] == FRESH_OFFER_REFERENCE

    def test_unknown_contact_type_offer_is_still_searchable(self) -> None:
        watch_row = {
            "id": "watch-fresh-2",
            "brand": "Rolex",
            "reference": FRESH_OFFER_REFERENCE,
            "model": "Submariner",
            "dial": None,
            "bracelet": None,
        }
        search_offer = {
            "id": "offer-fresh-2",
            "status": "active",
            "watch_id": watch_row["id"],
            "dealer_id": "dealer-1",
            "condition": "New",
            "usd_price": 13700,
            "original_price": 12700,
            "original_currency": "EUR",
            "messages": {"id": "message-fresh-2"},
            "watches": None,
            "dealers": None,
        }

        with (
            patch("search.get_client", return_value=_mock_offers_response([search_offer])),
            patch("database.get_watch_by_id", return_value=watch_row),
            patch(
                "database.get_dealer_by_id",
                return_value={
                    "id": "dealer-1",
                    "display_name": "HK Dealer",
                    "contact_type": "unknown",
                    "whatsapp_id": "+85291234567",
                },
            ),
            patch("search.contact_type_column_supported", return_value=True),
        ):
            offers, _ = search_offers(FRESH_OFFER_REFERENCE)

        assert len(offers) == 1

    @pytest.mark.parametrize(
        ("stored_reference", "query"),
        [
            ("5711/1A", "5711/1A"),
            ("5711/1A", "5711"),
            ("126610-LN", "126610LN"),
            ("126610 LN", "126610LN"),
        ],
    )
    def test_strict_reference_search_handles_import_punctuation_variants(
        self,
        stored_reference: str,
        query: str,
    ) -> None:
        watch_row = {
            "id": "watch-variant",
            "brand": "Patek Philippe" if "5711" in stored_reference else "Rolex",
            "reference": stored_reference,
            "model": None,
            "dial": None,
            "bracelet": None,
        }
        search_offer = {
            "id": "offer-variant",
            "watch_id": watch_row["id"],
            "dealer_id": "dealer-1",
            "usd_price": 50000,
            "condition": "New",
            "original_price": 50000,
            "original_currency": "USD",
            "messages": {"id": "message-variant"},
            "watches": watch_row,
            "dealers": {"display_name": "Dealer", "contact_type": CONTACT_TYPE_DEALER},
        }

        with (
            patch("search.get_client", return_value=_mock_offers_response([search_offer])),
            patch("search.contact_type_column_supported", return_value=True),
        ):
            offers, _ = search_offers(query)

        assert len(offers) == 1
        assert offers[0]["watch"]["reference"] == stored_reference

    def test_debug_helper_reports_reference_mismatch_when_nested_watch_missing(self) -> None:
        watch_row = {
            "id": "watch-debug-1",
            "brand": "Rolex",
            "reference": FRESH_OFFER_REFERENCE,
        }
        offer_row = {
            "id": "offer-debug-1",
            "status": "active",
            "watch_id": watch_row["id"],
            "dealer_id": "dealer-1",
            "condition": "New",
            "usd_price": 13700,
        }
        loaded_offer = {
            **offer_row,
            "messages": {"id": "message-debug-1"},
            "watches": None,
            "dealers": {
                "display_name": "HK Dealer",
                "contact_type": CONTACT_TYPE_DEALER,
            },
        }

        with (
            patch(
                "search_import_debug.find_watches_by_reference",
                return_value=[watch_row],
            ),
            patch(
                "search_import_debug._active_offers_for_watch_ids",
                return_value=[offer_row],
            ),
            patch(
                "search._load_active_offers_for_search",
                return_value=([loaded_offer], 1),
            ),
            patch("database.get_watch_by_id", return_value=None),
            patch(
                "database.get_dealer_by_id",
                return_value={
                    "id": "dealer-1",
                    "display_name": "HK Dealer",
                    "contact_type": CONTACT_TYPE_DEALER,
                },
            ),
            patch("search.contact_type_column_supported", return_value=True),
        ):
            trace = trace_fresh_offer_searchability(FRESH_OFFER_REFERENCE)

        assert trace["final_reason"] == "reference_token_mismatch"
        assert trace["search_should_include_offer"] is False
        assert trace["search_query_path"]["counts"]["after_dealer_visibility"] == 1
        assert trace["search_query_path"]["counts"]["after_reference_matching"] == 0

    def test_debug_helper_reports_found_when_nested_join_missing_but_fallback_resolves(self) -> None:
        watch_row = {
            "id": "watch-debug-2",
            "brand": "Rolex",
            "reference": FRESH_OFFER_REFERENCE,
        }
        offer_row = {
            "id": "offer-debug-2",
            "status": "active",
            "watch_id": watch_row["id"],
            "dealer_id": "dealer-1",
            "condition": "New",
            "usd_price": 13700,
        }
        loaded_offer = {
            **offer_row,
            "messages": {"id": "message-debug-2"},
            "watches": None,
            "dealers": None,
        }

        with (
            patch(
                "search_import_debug.find_watches_by_reference",
                return_value=[watch_row],
            ),
            patch(
                "search_import_debug._active_offers_for_watch_ids",
                return_value=[offer_row],
            ),
            patch(
                "search._load_active_offers_for_search",
                return_value=([loaded_offer], 1),
            ),
            patch("database.get_watch_by_id", return_value=watch_row),
            patch(
                "database.get_dealer_by_id",
                return_value={
                    "id": "dealer-1",
                    "display_name": "HK Dealer",
                    "contact_type": "unknown",
                },
            ),
            patch("search.contact_type_column_supported", return_value=True),
        ):
            trace = trace_fresh_offer_searchability(FRESH_OFFER_REFERENCE)

        assert trace["final_reason"] == "found"
        assert trace["search_should_include_offer"] is True
