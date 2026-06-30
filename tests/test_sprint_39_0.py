"""Tests for Sprint 39.0 AI Trading Advisor."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from advisor import generate_trading_advisor_summary
from app import app
from opportunity_engine import (
    build_profit_display,
    build_score_card,
    calculate_data_quality_confidence,
    score_market_request_opportunity,
)
from opportunity_intelligence import (
    dealer_rating,
    health_for_score,
    recommendation_badge_class,
)


def _market_request_log(**overrides) -> dict:
    watch = {
        "brand": "Rolex",
        "reference": "126500LN",
        "model": "Daytona",
        "price": 25000,
        "currency": "USD",
        "dial": "white",
        "bracelet": "oyster",
        "condition": "New",
        "full_set": True,
        "production_year": 2024,
    }
    watch.update(overrides.pop("watch", {}))
    return {
        "id": "req-1",
        "status": "request_intent",
        "import_time": overrides.pop("import_time", "2026-06-28T11:30:00+00:00"),
        "summary": {"parsed_watches": [watch]},
        **overrides,
    }


def _offer(**overrides) -> dict:
    watch = {
        "brand": "Rolex",
        "reference": "126500LN",
        "model": "Daytona",
        "dial": "white",
        "bracelet": "oyster",
        "condition": "New",
        "full_set": True,
        "production_year": 2024,
    }
    watch.update(overrides.pop("watch", {}))
    dealer_id = overrides.pop("dealer_id", "dealer-1")
    usd_price = overrides.pop("usd_price", 22600)
    return {
        "id": overrides.pop("offer_id", "offer-1"),
        "dealer_id": dealer_id,
        "watch_id": "watch-1",
        "status": "active",
        "usd_price": usd_price,
        "original_price": usd_price,
        "original_currency": "USD",
        "condition": "new",
        "watches": watch,
        "dealers": {
            "id": dealer_id,
            "display_name": "HK Dealer",
            "contact_type": "dealer",
        },
        "messages": {
            "received_at": overrides.pop("received_at", "2026-06-28T11:45:00+00:00"),
            "groups": {"name": "HK Dealers", "country": "Hong Kong"},
        },
        **overrides,
    }


NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)


class TestAdvisorSummary:
    def test_ai_summary_generation_from_scoring(self) -> None:
        analysis = {
            "has_opportunities": True,
            "opportunity_score": 96,
            "urgency": "HOT",
            "recommendation": "BUY NOW",
            "positive_reasons": [
                "✔ Exact reference",
                "✔ Offer posted 15 minutes ago",
            ],
            "warning_reasons": ["⚠ Budget missing"],
            "best_match": {"dealer_rating": "Trusted Dealer"},
        }

        summary = generate_trading_advisor_summary(analysis)

        assert "promising opportunity" in summary.lower() or "strong opportunity" in summary.lower()
        assert "reference matches exactly" in summary.lower()
        assert "buyer's budget" in summary.lower()
        assert "acting immediately" in summary.lower() or "recommend" in summary.lower()

    def test_ai_summary_for_empty_state(self) -> None:
        summary = generate_trading_advisor_summary({"has_opportunities": False})
        assert "no matching offers" in summary.lower()


class TestConfidenceAndProfit:
    def test_confidence_calculation_with_missing_budget(self) -> None:
        import_log = _market_request_log(watch={"price": None, "currency": "USD"})

        result = calculate_data_quality_confidence(import_log)

        assert result["data_quality_confidence_pct"] == 94
        assert result["data_quality_confidence_reason"] == "Missing client budget"

    def test_budget_unknown_profit_display(self) -> None:
        display = build_profit_display(None, None)

        assert display["budget_known"] is False
        assert display["potential_profit_title"] == "Budget unknown"
        assert display["potential_profit_subtitle"] == "Unable to calculate profit"

    def test_budget_known_profit_display(self) -> None:
        display = build_profit_display(25000, 2400)

        assert display["budget_known"] is True
        assert display["potential_profit_value"] == "+$2,400"


class TestDealerHealthAndRecommendation:
    def test_dealer_rating_from_existing_stats(self) -> None:
        from opportunity_intelligence import build_dealer_quality_index

        trusted_offers = [_offer(offer_id=f"o-{index}", dealer_id="dealer-trusted") for index in range(12)]
        index = build_dealer_quality_index(trusted_offers)

        label, badge = dealer_rating("dealer-trusted", index)
        assert label == "Trusted Dealer"
        assert badge == "success"

        label, badge = dealer_rating("dealer-new", build_dealer_quality_index([_offer(dealer_id="dealer-new")]))
        assert label == "New Dealer"
        assert badge == "warning"

    def test_health_level_mapping(self) -> None:
        assert health_for_score(96) == ("Excellent", "success")
        assert health_for_score(80) == ("Good", "primary")
        assert health_for_score(60) == ("Average", "warning")
        assert health_for_score(40) == ("Weak", "secondary")
        assert health_for_score(10) == ("Critical", "danger")

    def test_recommendation_badges(self) -> None:
        assert recommendation_badge_class("BUY NOW") == "success"
        assert recommendation_badge_class("CALL TODAY") == "danger"
        assert recommendation_badge_class("GOOD OPPORTUNITY") == "primary"
        assert recommendation_badge_class("WATCH") == "warning"
        assert recommendation_badge_class("IGNORE") == "secondary"


class TestOpportunityCardRendering:
    @patch("market_requests.build_market_request_opportunity_bundle")
    @patch("market_requests.get_import_log")
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_opportunity_card_and_matching_table_render(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
        mock_bundle: MagicMock,
    ) -> None:
        import_id = "11111111-1111-4111-8111-111111111111"
        import_log = _market_request_log(import_id=import_id)
        mock_get_import_log.return_value = import_log
        mock_list_import_logs.return_value = [import_log]
        mock_get_message.return_value = {"raw_text": "WTB Rolex Daytona 126500LN budget 25k"}

        row = {
            "offer_id": "offer-1",
            "watch_id": "watch-1",
            "dealer_id": "dealer-1",
            "dealer_name": "HK Dealer",
            "asking_price": "$22,600",
            "offer_url": "/watch/watch-1",
            "opportunity_score": 96,
            "score_label": "Excellent",
            "confidence_badge_class": "success",
            "health": "Excellent",
            "health_badge_class": "success",
            "dealer_rating": "Trusted Dealer",
            "dealer_rating_badge_class": "success",
            "budget_known": True,
            "potential_profit_value": "+$2,400",
            "potential_profit_title": "Potential Profit",
            "urgency": "HOT",
            "urgency_badge_class": "danger",
            "recommendation": "BUY NOW",
            "recommendation_badge_class": "success",
            "positive_reasons": ["✔ Exact reference", "✔ Matching dial"],
            "warning_reasons": [],
            "reasons": ["✔ Exact reference", "✔ Matching dial"],
        }
        row["score_card"] = build_score_card(row)
        analysis = {
            "has_opportunities": True,
            "empty_message": None,
            "ai_advisor_summary": generate_trading_advisor_summary(
                {
                    "has_opportunities": True,
                    "opportunity_score": 96,
                    "urgency": "HOT",
                    "recommendation": "BUY NOW",
                    "positive_reasons": row["positive_reasons"],
                    "warning_reasons": [],
                    "best_match": {"dealer_rating": "Trusted Dealer"},
                }
            ),
            "opportunity_score": 96,
            "score_label": "Excellent",
            "confidence_label": "Excellent",
            "confidence_badge_class": "success",
            "health": "Excellent",
            "health_badge_class": "success",
            "data_quality_confidence_pct": 100,
            "data_quality_confidence_reason": "Complete request data",
            "urgency": "HOT",
            "urgency_badge_class": "danger",
            "budget_known": True,
            "potential_profit_title": "Potential Profit",
            "potential_profit_value": "+$2,400",
            "potential_profit_subtitle": None,
            "potential_profit": "+$2,400",
            "positive_reasons": row["positive_reasons"],
            "warning_reasons": [],
            "reasons": row["reasons"],
            "recommendation": "BUY NOW",
            "recommendation_badge_class": "success",
            "score_card": row["score_card"],
            "best_match": {
                "dealer_name": "HK Dealer",
                "asking_price": "$22,600",
                "offer_url": "/watch/watch-1",
                "dealer_rating": "Trusted Dealer",
                "dealer_rating_badge_class": "success",
            },
        }
        mock_bundle.return_value = ([row], analysis)

        response = TestClient(app).get(f"/market-requests/{import_id}")

        assert response.status_code == 200
        assert "AI Trading Advisor" in response.text
        assert "Opportunity Score" in response.text
        assert ">96<" in response.text
        assert "Confidence" in response.text
        assert "100%" in response.text
        assert "Dealer Rating" in response.text
        assert "Trusted Dealer" in response.text
        assert "Budget unknown" not in response.text
        assert "+$2,400" in response.text
        assert "BUY NOW" in response.text
        assert "✔ Exact reference" in response.text
        assert "Dealer Rating" in response.text
        assert "Matching Offers" in response.text
        assert "Potential Profit" in response.text

    def test_score_card_structure_from_engine(self) -> None:
        from opportunity_intelligence import build_dealer_quality_index

        import_log = _market_request_log()
        offer = _offer()
        dealer_index = build_dealer_quality_index([offer] * 12)
        scored = score_market_request_opportunity(
            import_log,
            offer,
            match_type="exact_reference",
            dealer_index=dealer_index,
            now=NOW,
        )
        card = build_score_card(scored)

        assert card["opportunity_score"] >= 90
        assert card["score_label"] == "Excellent"
        assert card["health"] == "Excellent"
        assert card["recommendation_badge_class"] == "success"
