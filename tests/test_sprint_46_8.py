"""Tests for Sprint 46.8 Deal Analysis condition/market-data regression fix."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import build_deal_analysis_cards
from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    normalize_wear_condition,
    resolve_offer_wear_condition,
)
from ingest import _build_price_intelligence, _comparable_usd_prices, ingest_message
from watch_parser import parse_message


class TestConditionNormalizationCoverage:
    @staticmethod
    def _expect_new(raw: str) -> None:
        normalized, _ = normalize_wear_condition(raw)
        assert normalized == NEW_CONDITION

    @staticmethod
    def _expect_pre_owned(raw: str) -> None:
        normalized, _ = normalize_wear_condition(raw)
        assert normalized == PRE_OWNED_CONDITION

    def test_new_aliases(self) -> None:
        for raw in ("new", "brand new", "unworn", "sticker", "stickers", "full stickers", "nos"):
            self._expect_new(raw)

    def test_pre_owned_aliases(self) -> None:
        for raw in ("used", "worn", "pre-owned", "pre owned", "second hand"):
            self._expect_pre_owned(raw)

    def test_parser_detects_sticker_condition(self) -> None:
        watch = parse_message("Patek 5267/200A full stickers 180000 USD")["watches"][0]
        assert watch.get("condition") == "Full stickers"
        assert resolve_offer_wear_condition(watch.get("condition")) == NEW_CONDITION


class TestComparableMarketSelection:
    def test_new_offer_uses_only_new_comparables(self) -> None:
        comparables, market_condition = _comparable_usd_prices(
            [("new", 13_500, NEW_CONDITION), ("used", 10_800, PRE_OWNED_CONDITION)],
            exclude_offer_ids=set(),
            offer_condition=NEW_CONDITION,
        )
        assert comparables == [13_500]
        assert market_condition == NEW_CONDITION

    def test_pre_owned_offer_uses_only_pre_owned_comparables(self) -> None:
        comparables, market_condition = _comparable_usd_prices(
            [("new", 13_500, NEW_CONDITION), ("used", 10_800, PRE_OWNED_CONDITION)],
            exclude_offer_ids=set(),
            offer_condition=PRE_OWNED_CONDITION,
        )
        assert comparables == [10_800]
        assert market_condition == PRE_OWNED_CONDITION

    def test_new_offer_ignores_pre_owned_comparables(self) -> None:
        comparables, _ = _comparable_usd_prices(
            [("used", 10_800, PRE_OWNED_CONDITION)],
            exclude_offer_ids=set(),
            offer_condition=NEW_CONDITION,
        )
        assert comparables == []

    def test_pre_owned_offer_ignores_new_comparables(self) -> None:
        comparables, _ = _comparable_usd_prices(
            [("new", 13_500, NEW_CONDITION)],
            exclude_offer_ids=set(),
            offer_condition=PRE_OWNED_CONDITION,
        )
        assert comparables == []


class TestDealAnalysisDataFlow:
    def _summary(
        self,
        *,
        row: dict,
        parsed_watch: dict | None = None,
        extra_parsed_watch: dict | None = None,
        offer_watch: dict | None = None,
    ) -> dict:
        parsed_watches = []
        if extra_parsed_watch is not None:
            parsed_watches.append(extra_parsed_watch)
        if parsed_watch is not None:
            parsed_watches.append(parsed_watch)
        summary = {"rows": [row]}
        if parsed_watches:
            summary["parsed_watches"] = parsed_watches
        if offer_watch is not None:
            summary["offer_watches"] = [offer_watch]
        return summary

    def test_deal_analysis_gets_condition_from_row_when_parsed_mismatch(self) -> None:
        row = {
            "brand": "Richard Mille",
            "reference": "RM30-01",
            "condition": NEW_CONDITION,
            "raw_condition": "Unworn",
            "usd_price": 250_000,
            "previous_lowest_usd": "$255,000",
            "price_label": "Good price",
            "rank": "2",
            "market_condition": NEW_CONDITION,
        }
        summary = self._summary(
            row=row,
            extra_parsed_watch={"brand": "Rolex", "reference": "126610LN", "condition": None},
            parsed_watch={"brand": "Richard Mille", "reference": "RM30-01", "condition": NEW_CONDITION},
            offer_watch={"brand": "Richard Mille", "reference": "RM30-01", "condition": NEW_CONDITION},
        )

        analysis = build_deal_analysis_cards(summary)[0]

        assert analysis["condition_label"] == "New"
        assert analysis["market_price"] == "$255,000"
        assert analysis["recommendation"] == "Good Buy"

    def test_missing_condition_remains_needs_review(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                row={
                    "brand": "Audemars Piguet",
                    "reference": "5168G",
                    "condition": None,
                    "usd_price": 85_000,
                    "previous_lowest_usd": "N/A",
                    "price_label": "No comparables",
                    "market_condition": None,
                },
                parsed_watch={"brand": "Audemars Piguet", "reference": "5168G"},
            )
        )[0]

        assert analysis["condition_label"] == "Unknown"
        assert analysis["recommendation"] == "Needs Review"
        assert analysis["market_price"] == "Unknown"

    def test_missing_market_condition_on_old_import_stays_safe(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                row={
                    "brand": "Patek Philippe",
                    "reference": "5267/200A",
                    "condition": NEW_CONDITION,
                    "usd_price": 180_000,
                    "previous_lowest_usd": "$185,000",
                    "price_label": "Good price",
                    "rank": "2",
                    "market_condition": None,
                },
                parsed_watch={
                    "brand": "Patek Philippe",
                    "reference": "5267/200A",
                    "condition": NEW_CONDITION,
                },
            )
        )[0]

        assert analysis["condition_label"] == "New"
        assert analysis["market_price"] == "$185,000"
        assert analysis["recommendation"] == "Good Buy"

    def test_condition_mismatch_never_becomes_buy(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                row={
                    "brand": "Rolex",
                    "reference": "126334",
                    "condition": PRE_OWNED_CONDITION,
                    "usd_price": 10_500,
                    "previous_lowest_usd": "$13,500",
                    "price_label": "New lowest price",
                    "market_condition": NEW_CONDITION,
                },
                parsed_watch={
                    "brand": "Rolex",
                    "reference": "126334",
                    "condition": PRE_OWNED_CONDITION,
                },
            )
        )[0]

        assert analysis["recommendation"] == "Needs Review"
        assert analysis["market_price"] == "Unknown"

    def test_only_one_card_per_import_row(self) -> None:
        analyses = build_deal_analysis_cards(
            self._summary(
                row={
                    "brand": "Richard Mille",
                    "reference": "RM35-03",
                    "condition": NEW_CONDITION,
                    "usd_price": 1_200_000,
                    "previous_lowest_usd": "$1,250,000",
                    "price_label": "Good price",
                    "market_condition": NEW_CONDITION,
                },
                extra_parsed_watch={"brand": "Rolex", "reference": "126610LN"},
                parsed_watch={"brand": "Richard Mille", "reference": "RM35-03", "condition": NEW_CONDITION},
                offer_watch={"brand": "Richard Mille", "reference": "RM35-03", "condition": NEW_CONDITION},
            )
        )

        assert len(analyses) == 1
        assert analyses[0]["condition_label"] == "New"


class TestIngestStoresAlignedOfferWatches:
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
    def test_reprocessed_style_import_stores_offer_watches_and_market_condition(
        self,
        _mock_find_dealer: MagicMock,
        _mock_find_group: MagicMock,
        _mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        _mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        _mock_process_matches: MagicMock,
        _mock_record_notifications: MagicMock,
        _mock_unknown_brands: MagicMock,
        _mock_unknown_nicknames: MagicMock,
    ) -> None:
        mock_find_watch.return_value = ({"id": "watch-1"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_get_active_offers.return_value = [("other-offer", 255_000, NEW_CONDITION)]

        summary = ingest_message(
            "Richard Mille RM30-01 blue dial unworn 250k USD",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["offer_watches"]
        assert len(summary["offer_watches"]) == len(summary["rows"]) == 1
        assert summary["rows"][0]["condition"] == NEW_CONDITION
        assert summary["rows"][0]["market_condition"] == NEW_CONDITION

        analysis = build_deal_analysis_cards(summary)[0]
        assert analysis["condition_label"] == "New"
        assert analysis["market_price"] != "Unknown"
        assert analysis["recommendation"] in {"Good Buy", "Excellent Buy", "Fair Price"}

    def test_price_intelligence_keeps_market_condition(self) -> None:
        intelligence = _build_price_intelligence(
            250_000,
            [255_000],
            is_duplicate=False,
            market_condition=NEW_CONDITION,
        )
        assert intelligence["market_condition"] == NEW_CONDITION
