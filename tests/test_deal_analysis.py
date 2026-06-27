"""Unit tests for import detail deal analysis cards."""

from __future__ import annotations

from app import _build_deal_analysis, _resolve_deal_recommendation


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
                "price_label": "New lowest price",
            },
            {"confidence": 90},
            0,
        )

        assert analysis["offer_price"] == "$95,000"
        assert analysis["market_price"] == "$100,000"
        assert analysis["difference"] == "-$5,000"
        assert analysis["difference_pct"] == "-5.0%"
        assert analysis["market_rank_display"] == "#1"
        assert analysis["recommendation"] == "Excellent Buy"
        assert analysis["recommendation_class"] == "excellent"
        assert analysis["potential_profit"] == "$5,000"
        assert analysis["potential_profit_positive"] is True
        assert analysis["confidence"] >= 70
