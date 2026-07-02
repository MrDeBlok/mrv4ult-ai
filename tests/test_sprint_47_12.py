"""Tests for Sprint 47.12 Deal Analysis market comparable lookup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import build_deal_analysis_cards
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION, normalize_watch_condition, resolve_offer_wear_condition
from deal_market_lookup import INSUFFICIENT_MARKET_DATA, resolve_deal_market_context
from ingest import _comparable_usd_prices
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message

DATEJUST_MESSAGE = (
    "WTS: Rolex Datejust 126331 / Full Set 2021 / €11.950 + Label"
)
DATEJUST_PREOWNED_MESSAGE = (
    "WTS: Rolex Datejust 126331 / Pre-Owned / Full Set 2021 / €11.950 + Label"
)
WATCH_ID = "watch-126331"
CURRENT_OFFER_ID = "offer-current"


def _row(**overrides) -> dict:
    row = {
        "brand": "Rolex",
        "reference": "126331",
        "condition": PRE_OWNED_CONDITION,
        "raw_condition": "Pre-Owned",
        "usd_price": 12_900,
        "previous_lowest_usd": "N/A",
        "price_label": "No comparables",
        "market_condition": None,
        "offer_id": CURRENT_OFFER_ID,
    }
    row.update(overrides)
    return row


def _watch(**overrides) -> dict:
    watch = {
        "brand": "Rolex",
        "reference": "126331",
        "condition": PRE_OWNED_CONDITION,
        "raw_condition": "Pre-Owned",
        "usd_price": 12_900,
    }
    watch.update(overrides)
    return watch


class TestDatejust126331ConditionParsing:
    def test_pre_owned_message_extracts_canonical_condition(self) -> None:
        watch = normalize_watch_condition(
            enrich_parsed_watch(parse_message(DATEJUST_PREOWNED_MESSAGE)["watches"][0])
        )

        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "126331"
        assert resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition")) == PRE_OWNED_CONDITION

    def test_full_set_message_without_condition_stays_unknown_before_inference(self) -> None:
        watch = normalize_watch_condition(
            enrich_parsed_watch(parse_message(DATEJUST_MESSAGE)["watches"][0])
        )

        assert resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition")) is None


class TestDealMarketLookup:
    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_known_condition_with_same_condition_comparable_shows_market_price(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = [
            ("offer-other", 13_500, PRE_OWNED_CONDITION),
        ]

        context = resolve_deal_market_context(_row(), _watch(), include_debug=True)

        assert context.comparison_safe is True
        assert context.market_usd == 13_500
        assert context.effective_row["market_condition"] == PRE_OWNED_CONDITION
        assert context.debug["watch_id"] == WATCH_ID
        assert context.debug["active_comparables_after_condition_filter"] == 1

    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_known_condition_without_comparables_is_insufficient_market_data(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = []

        context = resolve_deal_market_context(_row(), _watch(), include_debug=True)

        assert context.comparison_safe is False
        assert context.insufficient_market_data is True
        assert context.needs_review is False
        assert context.debug["market_price_unknown_reason"] == "no_other_active_offers_for_watch"

    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_unknown_condition_stays_needs_review(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = [("offer-other", 13_500, PRE_OWNED_CONDITION)]

        context = resolve_deal_market_context(
            _row(condition=None, raw_condition=None, usd_price=None, market_condition=None),
            _watch(condition=None, raw_condition=None, usd_price=None),
            include_debug=True,
        )

        assert context.needs_review is True
        assert context.insufficient_market_data is False
        assert context.debug["market_price_unknown_reason"] == "offer_condition_unknown"

    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_current_offer_alone_does_not_create_fake_market_price(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = []

        context = resolve_deal_market_context(_row(), _watch(), include_debug=True)

        assert context.market_usd is None
        assert context.effective_row["previous_lowest_usd"] == "N/A"

    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_zero_comparables_are_ignored(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = [("offer-zero", 0, PRE_OWNED_CONDITION)]

        context = resolve_deal_market_context(_row(), _watch(), include_debug=True)

        assert context.market_usd is None
        assert context.insufficient_market_data is True

    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_lookup_uses_correct_watch_id(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = [("offer-other", 13_500, PRE_OWNED_CONDITION)]

        resolve_deal_market_context(_row(), _watch(), include_debug=True)

        mock_get_offers.assert_called_once_with([CURRENT_OFFER_ID])
        mock_pool.assert_called_once_with(WATCH_ID, exclude_offer_ids={CURRENT_OFFER_ID})

    def test_comparable_lookup_normalizes_conditions(self) -> None:
        comparables, market_condition = _comparable_usd_prices(
            [
                ("offer-new", 14_000, NEW_CONDITION),
                ("offer-used", 13_500, "pre-owned"),
            ],
            exclude_offer_ids=set(),
            offer_condition="Pre-Owned",
        )

        assert comparables == [13_500]
        assert market_condition == PRE_OWNED_CONDITION

    def test_negative_comparables_are_ignored(self) -> None:
        comparables, _ = _comparable_usd_prices(
            [("offer-bad", -100, PRE_OWNED_CONDITION)],
            exclude_offer_ids=set(),
            offer_condition=PRE_OWNED_CONDITION,
        )

        assert comparables == []


class TestDealAnalysisPresentation:
    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_known_condition_with_comparables_shows_market_price(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = [("offer-other", 13_500, PRE_OWNED_CONDITION)]

        analysis = build_deal_analysis_cards(
            {"rows": [_row()], "offer_watches": [_watch()]},
            include_debug=True,
        )[0]

        assert analysis["market_price"] == "$13,500"
        assert analysis["recommendation"] == "Good Buy"
        assert analysis["debug"]["watch_id"] == WATCH_ID

    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_known_condition_without_comparables_shows_insufficient_market_data(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {CURRENT_OFFER_ID: {"watch_id": WATCH_ID}}
        mock_pool.return_value = []

        analysis = build_deal_analysis_cards(
            {"rows": [_row()], "offer_watches": [_watch()]},
        )[0]

        assert analysis["recommendation"] == INSUFFICIENT_MARKET_DATA
        assert analysis["market_price"] == "Unknown"
        assert analysis["market_status_message"] == "No other same-condition comparables yet."

    def test_unknown_condition_without_price_shows_needs_review(self) -> None:
        analysis = build_deal_analysis_cards(
            {
                "rows": [
                    _row(
                        condition=None,
                        raw_condition=None,
                        usd_price=None,
                        market_condition=None,
                        previous_lowest_usd="N/A",
                        price_label="No comparables",
                    )
                ],
                "parsed_watches": [_watch(condition=None, raw_condition=None, usd_price=None)],
            }
        )[0]

        assert analysis["recommendation"] == "Needs Review"
        assert analysis["condition_label"] == "Unknown"

    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup._load_active_offer_pool")
    def test_stored_market_data_used_when_live_lookup_unavailable(
        self,
        mock_pool: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {}
        mock_pool.return_value = []

        analysis = build_deal_analysis_cards(
            {
                "rows": [
                    _row(
                        previous_lowest_usd="$13,500",
                        price_label="Good price",
                        rank="2",
                        market_condition=None,
                    )
                ],
                "parsed_watches": [_watch()],
            }
        )[0]

        assert analysis["market_price"] == "$13,500"
        assert analysis["recommendation"] == "Good Buy"
