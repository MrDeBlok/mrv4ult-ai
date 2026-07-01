"""Tests for Sprint 44.1 multi-currency same-offer price handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import build_deal_analysis_cards
from ingest import ingest_message
from watch_parser import parse_message

MULTI_CURRENCY_BLOCK = (
    "279283RBR\n"
    "New N2 2026\n"
    "AED 92.000\n"
    "USD 25.000"
)


class TestMultiCurrencyOfferParsing:
    def test_aed_and_usd_same_block_creates_one_offer(self) -> None:
        parsed = parse_message(MULTI_CURRENCY_BLOCK)

        assert len(parsed["watches"]) == 1

    def test_usd_is_primary_when_present(self) -> None:
        watch = parse_message(MULTI_CURRENCY_BLOCK)["watches"][0]

        assert watch["original_price"] == 25_000
        assert watch["original_currency"] == "USD"
        assert watch["usd_price"] == 25_000

    def test_usd_is_primary_when_listed_before_aed(self) -> None:
        text = (
            "279283RBR\n"
            "New N2 2026\n"
            "USD 25.000\n"
            "AED 92.000"
        )
        watch = parse_message(text)["watches"][0]

        assert watch["original_price"] == 25_000
        assert watch["original_currency"] == "USD"
        assert watch["usd_price"] == 25_000

    def test_single_line_multi_currency_prefers_usd(self) -> None:
        watch = parse_message(
            "279283RBR New N2 2026 AED 92.000 USD 25.000"
        )["watches"][0]

        assert watch["original_price"] == 25_000
        assert watch["original_currency"] == "USD"
        assert watch["usd_price"] == 25_000

    def test_aed_is_not_used_as_retail_price(self) -> None:
        watch = parse_message(MULTI_CURRENCY_BLOCK)["watches"][0]

        assert watch.get("retail_price") is None
        assert watch.get("retail_price_only") is False

    def test_multiple_currencies_do_not_create_multiple_offers(self) -> None:
        variants = [
            MULTI_CURRENCY_BLOCK,
            "279283RBR New N2 2026 AED 92.000 USD 25.000",
            "279283RBR\nUSD 25.000\nAED 92.000",
        ]
        for text in variants:
            parsed = parse_message(text)
            assert len(parsed["watches"]) == 1

    def test_non_usd_offer_uses_supported_currency_when_usd_absent(self) -> None:
        watch = parse_message(
            "279283RBR\nNew N2 2026\nAED 92.000"
        )["watches"][0]

        assert watch["original_price"] == 92_000
        assert watch["original_currency"] == "AED"
        assert watch["usd_price"] == 25_024


class TestMultiCurrencyDealAnalysis:
    def test_deal_analysis_does_not_show_aed_amount_as_market_price(self) -> None:
        watch = parse_message(MULTI_CURRENCY_BLOCK)["watches"][0]
        summary = {
            "parsed_watches": [watch],
            "rows": [
                {
                    "condition": watch.get("condition"),
                    "usd_price": watch["usd_price"],
                    "previous_lowest_usd": "$28,000",
                    "price_label": "Good price",
                    "rank": "2",
                    "market_condition": watch.get("condition"),
                }
            ],
        }

        analysis = build_deal_analysis_cards(summary)[0]

        assert analysis["offer_price"] == "$25,000"
        assert analysis["market_price"] == "$28,000"
        assert "$92,000" not in analysis["market_price"]

    def test_deal_analysis_without_comparables_does_not_invent_92000_market(self) -> None:
        watch = parse_message(MULTI_CURRENCY_BLOCK)["watches"][0]
        summary = {
            "parsed_watches": [watch],
            "rows": [
                {
                    "usd_price": watch["usd_price"],
                    "previous_lowest_usd": "N/A",
                    "price_label": "No comparables",
                    "rank": "N/A",
                }
            ],
        }

        analysis = build_deal_analysis_cards(summary)[0]

        assert analysis["offer_price"] == "$25,000"
        assert analysis["market_price"] == "Unknown"


class TestMultiCurrencyIngestIntegration:
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
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", "dealer"))
    def test_ingest_stores_one_usd_normalized_offer(
        self,
        _mock_find_dealer: MagicMock,
        _mock_find_group: MagicMock,
        _mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        _mock_insert_import_log: MagicMock,
        _mock_get_active_offers: MagicMock,
        _mock_process_matches: MagicMock,
        _mock_record_notifications: MagicMock,
        _mock_unknown_brands: MagicMock,
        _mock_unknown_nicknames: MagicMock,
    ) -> None:
        mock_find_watch.return_value = ({"id": "watch-1"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)

        summary = ingest_message(MULTI_CURRENCY_BLOCK)

        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()
        assert mock_insert_offer.call_args.kwargs["original_price"] == 25_000
        assert mock_insert_offer.call_args.kwargs["original_currency"] == "USD"
        assert mock_insert_offer.call_args.kwargs["usd_price"] == 25_000
