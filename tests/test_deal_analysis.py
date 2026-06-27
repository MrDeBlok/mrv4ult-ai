"""Unit tests for import detail deal analysis cards."""

from __future__ import annotations

from app import (
    _build_deal_analysis,
    _resolve_deal_recommendation,
    build_deal_analysis_cards,
)


class TestDealRecommendation:
    def test_maps_existing_price_labels(self) -> None:
        assert _resolve_deal_recommendation("New lowest price", 95000, 100000) == (
            "Excellent Buy",
            "excellent",
        )
        assert _resolve_deal_recommendation("Good price", 101000, 100000) == (
            "Good Buy",
            "good",
        )
        assert _resolve_deal_recommendation("Normal price", 108000, 100000) == (
            "Market Price",
            "market",
        )
        assert _resolve_deal_recommendation("Expensive", 120000, 100000) == (
            "Expensive",
            "expensive",
        )

    def test_uses_insufficient_market_data_without_comparables(self) -> None:
        assert _resolve_deal_recommendation("New lowest price", 95000, None) == (
            "Insufficient market data",
            "insufficient",
        )


class TestDealAnalysisCard:
    def test_builds_metrics_from_price_intelligence(self) -> None:
        analysis = _build_deal_analysis(
            {
                "brand": "Rolex",
                "reference": "126500LN",
                "usd_price": 95000,
                "previous_lowest_usd": "$100,000",
                "price_difference": "-$5,000",
                "rank": "1",
                "price_label": "No comparables",
            },
            {"confidence": 90},
            0,
        )

        assert analysis["offer_price"] == "$95,000"
        assert analysis["market_price"] == "$100,000"
        assert analysis["show_market_metrics"] is True
        assert analysis["difference"] == "-$5,000"
        assert analysis["difference_pct"] == "-5.0%"
        assert analysis["market_rank_display"] == "#1"
        assert analysis["recommendation"] == "Excellent Buy"
        assert analysis["recommendation_class"] == "excellent"
        assert analysis["show_market_position"] is True
        assert analysis["market_position_label"] == "Below market"
        assert analysis["market_position_amount"] == "-$5,000"
        assert analysis["potential_profit"] == "$5,000"
        assert analysis["potential_profit_positive"] is True

    def test_caps_potential_profit_when_offer_is_above_market(self) -> None:
        analysis = _build_deal_analysis(
            {
                "brand": "Rolex",
                "reference": "126500LN",
                "usd_price": 22000,
                "previous_lowest_usd": "$20,000",
                "price_difference": "+$2,000",
                "rank": "3",
                "price_label": "Expensive",
            },
            {},
            0,
        )

        assert analysis["show_market_position"] is True
        assert analysis["market_position_label"] == "Above market"
        assert analysis["market_position_amount"] == "+$2,000"
        assert analysis["potential_profit"] == "$0"
        assert analysis["potential_profit_positive"] is False

    def test_hides_market_metrics_without_comparables(self) -> None:
        analysis = _build_deal_analysis(
            {
                "brand": "Rolex",
                "reference": "126713GRNR",
                "usd_price": 19500,
                "previous_lowest_usd": "N/A",
                "price_label": "No comparables",
            },
            {"confidence": 85},
            0,
        )

        assert analysis["show_market_metrics"] is False
        assert analysis["difference"] is None
        assert analysis["difference_pct"] is None
        assert analysis["market_rank_display"] is None
        assert analysis["potential_profit"] is None
        assert analysis["recommendation"] == "Insufficient market data"
        assert analysis["recommendation_class"] == "insufficient"


class TestDealAnalysisSources:
    def test_renders_one_card_per_stored_parsed_watch(self) -> None:
        summary = {
            "parsed_watches": [
                {
                    "brand": "Rolex",
                    "reference": "126713GRNR",
                    "confidence": 90,
                }
            ],
            "rows": [
                {
                    "brand": "Rolex",
                    "reference": "126713GRNR",
                    "usd_price": 19500,
                    "previous_lowest_usd": "N/A",
                    "price_label": "No comparables",
                }
            ],
        }

        analyses = build_deal_analysis_cards(summary)
        assert len(analyses) == 1

    def test_does_not_create_extra_cards_from_rows_only_legacy(self) -> None:
        summary = {
            "rows": [
                {
                    "brand": "Rolex",
                    "reference": "126713GRNR",
                    "usd_price": 19500,
                    "previous_lowest_usd": "N/A",
                    "price_label": "No comparables",
                }
            ]
        }

        analyses = build_deal_analysis_cards(summary)
        assert len(analyses) == 1
