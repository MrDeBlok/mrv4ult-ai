"""Tests for Sprint 46.0 condition-aware Deal Analysis."""

from __future__ import annotations

from app import (
    DEAL_EXCELLENT_CONFIDENCE_THRESHOLD,
    _build_deal_analysis,
    _resolve_deal_recommendation,
    build_deal_analysis_cards,
)
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION
from deal_market_lookup import INSUFFICIENT_MARKET_DATA
from ingest import _build_price_intelligence, _comparable_usd_prices


class TestConditionFilteredMarketComparables:
    def test_new_offer_compares_only_against_new_market(self) -> None:
        active_offers = [
            ("offer-new", 13_500, NEW_CONDITION),
            ("offer-used", 10_800, PRE_OWNED_CONDITION),
        ]

        comparables, market_condition = _comparable_usd_prices(
            active_offers,
            exclude_offer_ids=set(),
            offer_condition=NEW_CONDITION,
        )

        assert comparables == [13_500]
        assert market_condition == NEW_CONDITION

    def test_pre_owned_compares_only_against_pre_owned_market(self) -> None:
        active_offers = [
            ("offer-new", 13_500, NEW_CONDITION),
            ("offer-used", 10_800, PRE_OWNED_CONDITION),
            ("offer-used-2", 10_500, PRE_OWNED_CONDITION),
        ]

        comparables, market_condition = _comparable_usd_prices(
            active_offers,
            exclude_offer_ids=set(),
            offer_condition=PRE_OWNED_CONDITION,
        )

        assert comparables == [10_800, 10_500]
        assert market_condition == PRE_OWNED_CONDITION

    def test_mixed_condition_market_data_ignored(self) -> None:
        active_offers = [
            ("offer-new", 13_500, NEW_CONDITION),
            ("offer-used", 10_800, PRE_OWNED_CONDITION),
        ]

        comparables, market_condition = _comparable_usd_prices(
            active_offers,
            exclude_offer_ids=set(),
            offer_condition=PRE_OWNED_CONDITION,
        )

        assert comparables == [10_800]
        assert market_condition == PRE_OWNED_CONDITION

    def test_unknown_offer_condition_returns_no_comparables(self) -> None:
        active_offers = [
            ("offer-new", 13_500, NEW_CONDITION),
            ("offer-used", 10_800, PRE_OWNED_CONDITION),
        ]

        comparables, market_condition = _comparable_usd_prices(
            active_offers,
            exclude_offer_ids=set(),
            offer_condition=None,
        )

        assert comparables == []
        assert market_condition is None


class TestConditionAwareDealAnalysis:
    def _summary(
        self,
        *,
        condition: str | None,
        market_condition: str | None,
        offer_usd: int | None = 10_500,
        market_usd: str = "$10,800",
        price_label: str = "Good price",
    ) -> dict:
        watch = {
            "brand": "Rolex",
            "reference": "126334",
            "condition": condition,
            "confidence": 90,
        }
        if offer_usd is not None:
            watch["usd_price"] = offer_usd
        row = {
            "brand": "Rolex",
            "reference": "126334",
            "condition": condition,
            "usd_price": offer_usd,
            "previous_lowest_usd": market_usd,
            "price_label": price_label,
            "rank": "2",
            "market_condition": market_condition,
        }
        return {"parsed_watches": [watch], "rows": [row]}

    def test_unknown_condition_renders_unknown(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=None,
                market_condition=None,
                offer_usd=None,
                market_usd="N/A",
                price_label="No comparables",
            )
        )[0]

        assert analysis["condition_label"] == "Unknown"
        assert analysis["condition_icon"] == "⚪"
        assert analysis["condition_is_known"] is False

    def test_unknown_condition_shows_warning(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=None,
                market_condition=None,
                offer_usd=None,
                market_usd="N/A",
                price_label="No comparables",
            )
        )[0]

        assert analysis["show_condition_warning"] is True
        assert analysis["show_no_matching_market"] is False

    def test_unknown_condition_returns_needs_review(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=None,
                market_condition=NEW_CONDITION,
                market_usd="$13,500",
                price_label="New lowest price",
            )
        )[0]

        assert analysis["recommendation"] == "Needs Review"
        assert analysis["recommendation_class"] == "insufficient"

    def test_no_potential_profit_when_condition_unknown(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=None,
                market_condition=NEW_CONDITION,
                offer_usd=None,
                market_usd="$13,500",
                price_label="New lowest price",
            )
        )[0]

        assert analysis["potential_profit"] is None
        assert analysis["show_market_metrics"] is False

    def test_new_offer_with_matching_market_allows_buy_recommendation(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=NEW_CONDITION,
                market_condition=NEW_CONDITION,
                offer_usd=9_500,
                market_usd="$10,000",
                price_label="New lowest price",
            )
        )[0]

        assert analysis["condition_label"] == "New"
        assert analysis["condition_icon"] == "🟢"
        assert analysis["recommendation"] == "Excellent Buy"
        assert analysis["potential_profit"] == "$500"
        assert analysis["show_market_metrics"] is True

    def test_pre_owned_offer_with_matching_market(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=PRE_OWNED_CONDITION,
                market_condition=PRE_OWNED_CONDITION,
                offer_usd=10_500,
                market_usd="$10,800",
                price_label="Good price",
            )
        )[0]

        assert analysis["condition_label"] == "Pre-Owned"
        assert analysis["condition_icon"] == "🟡"
        assert analysis["recommendation"] == "Good Buy"
        assert analysis["show_no_matching_market"] is False

    def test_condition_mismatch_never_becomes_excellent_buy(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=PRE_OWNED_CONDITION,
                market_condition=NEW_CONDITION,
                offer_usd=10_500,
                market_usd="$13,500",
                price_label="New lowest price",
            )
        )[0]

        assert analysis["recommendation"] == "Needs Review"
        assert analysis["recommendation_class"] == "insufficient"
        assert analysis["potential_profit"] is None
        assert analysis["market_price"] == "Unknown"
        assert analysis["show_no_matching_market"] is True

    def test_missing_matching_market_returns_needs_review(self) -> None:
        analysis = build_deal_analysis_cards(
            self._summary(
                condition=NEW_CONDITION,
                market_condition=None,
                market_usd="N/A",
                price_label="No comparables",
            )
        )[0]

        assert analysis["recommendation"] == INSUFFICIENT_MARKET_DATA
        assert analysis["show_no_matching_market"] is True
        assert analysis["market_price"] == "Unknown"

    def test_excellent_buy_requires_high_confidence(self) -> None:
        analysis = _build_deal_analysis(
            {
                "brand": "Rolex",
                "reference": "126334",
                "condition": NEW_CONDITION,
                "usd_price": 9_500,
                "previous_lowest_usd": "$10,000",
                "price_label": "New lowest price",
                "rank": "1",
                "market_condition": NEW_CONDITION,
            },
            {"confidence": 10},
            0,
        )

        assert analysis["recommendation"] == "Good Buy"

    def test_resolve_recommendation_never_excellent_without_safe_comparison(self) -> None:
        assert _resolve_deal_recommendation(
            "New lowest price",
            9_500,
            10_000,
            comparison_safe=False,
            confidence=100,
        ) == ("Needs Review", "insufficient")

    def test_price_intelligence_stores_market_condition(self) -> None:
        intelligence = _build_price_intelligence(
            10_500,
            [10_800],
            is_duplicate=False,
            market_condition=PRE_OWNED_CONDITION,
        )

        assert intelligence["market_condition"] == PRE_OWNED_CONDITION

    def test_low_confidence_threshold_constant(self) -> None:
        assert DEAL_EXCELLENT_CONFIDENCE_THRESHOLD >= 70
