"""Tests for Sprint 32.6 retail vs net offer price parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from app import build_deal_analysis_cards
from ingest import _import_status, ingest_message
from parser_review import build_parser_review_row
from watch_parser import parse_message, parse_watch_line


class TestRetailAndOfferPriceParsing:
    def test_retail_and_nett_uses_nett_as_offer_price(self) -> None:
        text = (
            "RM37 RG CAO\n"
            "Retail USD: $301,000\n"
            "Nett $275,000\n"
            "New"
        )
        watch = parse_message(text)["watches"][0]

        assert watch["retail_price"] == 301_000
        assert watch["retail_currency"] == "USD"
        assert watch["original_price"] == 275_000
        assert watch["original_currency"] == "USD"
        assert watch["usd_price"] == 275_000
        assert watch["retail_price_only"] is False

    def test_msrp_and_asking_uses_asking_as_offer_price(self) -> None:
        watch = parse_watch_line("Rolex 126500LN MSRP USD 350,000 Asking $320,000")

        assert watch is not None
        assert watch["retail_price"] == 350_000
        assert watch["original_price"] == 320_000
        assert watch["usd_price"] == 320_000

    def test_list_price_and_our_price_uses_our_price(self) -> None:
        text = (
            "AP 15500ST blue dial\n"
            "List price EUR 180,000\n"
            "Our price EUR 165,000"
        )
        watch = parse_message(text)["watches"][0]

        assert watch["retail_price"] == 180_000
        assert watch["retail_currency"] == "EUR"
        assert watch["original_price"] == 165_000
        assert watch["original_currency"] == "EUR"

    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("126500LN 305k usd", 305_000, "USD"),
            ("15500ST €52k full set", 52_000, "EUR"),
            ("5711/1A HK$1,880,000 full set", 1_880_000, "HKD"),
            ("5980R CHF 220k", 220_000, "CHF"),
        ],
    )
    def test_single_price_offers_still_parse_normally(
        self,
        line: str,
        expected_price: int,
        expected_currency: str,
    ) -> None:
        watch = parse_watch_line(line)

        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == expected_currency
        assert watch.get("retail_price") is None
        assert watch.get("retail_price_only") is False

    def test_retail_only_does_not_set_offer_price(self) -> None:
        text = (
            "RM37 RG CAO\n"
            "Retail USD: $301,000\n"
            "New"
        )
        watch = parse_message(text)["watches"][0]

        assert watch["retail_price"] == 301_000
        assert watch["original_price"] is None
        assert watch["usd_price"] is None
        assert watch["retail_price_only"] is True

    def test_retail_only_marks_import_as_needs_review(self) -> None:
        text = (
            "RM37 RG CAO\n"
            "Retail USD: $301,000\n"
            "New"
        )
        parsed = parse_message(text)
        summary = {
            "watches_parsed": len(parsed["watches"]),
            "duplicate_offers": 0,
        }

        status, reason = _import_status(summary, "success", parsed["watches"])

        assert status == "warning"
        assert "retail price only" in reason.lower()

    def test_deal_analysis_uses_offer_price_not_retail(self) -> None:
        text = (
            "RM37 RG CAO\n"
            "Retail USD: $301,000\n"
            "Nett $275,000\n"
            "New"
        )
        watch = parse_message(text)["watches"][0]
        summary = {
            "parsed_watches": [watch],
            "rows": [
                {
                    "usd_price": watch["usd_price"],
                    "previous_lowest_usd": "$280,000",
                    "price_label": "Good price",
                    "rank": "2",
                }
            ],
        }

        analysis = build_deal_analysis_cards(summary)[0]

        assert analysis["offer_price"] == "$275,000"

    def test_parser_review_shows_retail_and_offer_prices(self) -> None:
        watch = parse_message(
            "RM37 RG CAO\nRetail USD: $301,000\nNett $275,000\nNew"
        )["watches"][0]
        import_log = {
            "id": "log-1",
            "status": "warning",
            "summary": {"parsed_watches": [watch]},
            "group_name": "HK Dealers",
            "dealer_alias": "Dealer A",
            "dealer_whatsapp": "+85291234567",
            "import_time": "2026-06-27T12:00:00+00:00",
        }

        row = build_parser_review_row(
            import_log,
            {"raw_text": "Retail and net message"},
            format_timestamp=lambda value: value or "N/A",
        )

        assert any(entry.startswith("Retail price:") for entry in row["parsed_fields"])
        assert any(entry.startswith("Offer price:") for entry in row["parsed_fields"])


class TestRetailOfferPriceIngestIntegration:
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
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", "dealer"))
    @patch("ingest.parse_message")
    def test_ingest_stores_net_price_on_offer(
        self,
        mock_parse_message: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        _mock_get_active_offers: MagicMock,
        _mock_process_matches: MagicMock,
        _mock_record_notifications: MagicMock,
        _mock_unknown_brands: MagicMock,
        _mock_unknown_nicknames: MagicMock,
    ) -> None:
        parsed_watch = parse_message(
            "RM37 RG CAO\nRetail USD: $301,000\nNett $275,000\nNew"
        )["watches"][0]
        mock_parse_message.return_value = {
            "message_type": "offer",
            "watches": [parsed_watch],
        }
        mock_find_dealer.return_value = ("dealer-1", "dealer")
        mock_insert_message.return_value = {"id": "msg-1"}
        mock_find_watch.return_value = ({"id": "watch-1", "brand": "Richard Mille"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "RM37 RG CAO\nRetail USD: $301,000\nNett $275,000\nNew",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        offer_kwargs = mock_insert_offer.call_args.kwargs
        assert offer_kwargs["original_price"] == 275_000
        assert offer_kwargs["usd_price"] == 275_000
        assert summary["rows"][0]["usd_price"] == 275_000
        assert summary["parsed_watches"][0]["retail_price"] == 301_000
