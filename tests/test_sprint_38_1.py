"""Tests for Sprint 38.1 AI Opportunity Scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from opportunity_engine import (
    build_market_request_opportunity_bundle,
    build_opportunity_analysis,
    score_market_request_opportunity,
)
from tests.conftest import ADMIN_USER, TRADER_ONE, TRADER_TWO


def _market_request_log(
    *,
    import_id: str = "req-1",
    reference: str = "126500LN",
    brand: str = "Rolex",
    model: str = "Daytona",
    nickname: str | None = None,
    price: int | None = 25000,
    currency: str = "USD",
    import_time: str = "2026-06-27T12:00:00+00:00",
) -> dict:
    watch: dict = {
        "brand": brand,
        "reference": reference,
        "model": model,
        "currency": currency,
    }
    if price is not None:
        watch["price"] = price
    if nickname:
        watch["nickname"] = nickname

    return {
        "id": import_id,
        "status": "request_intent",
        "watches_parsed": 0,
        "new_offers": 0,
        "message_id": "msg-1",
        "group_name": "HK Dealers",
        "dealer_whatsapp": "+85291234567",
        "dealer_alias": "HK Dealer",
        "import_time": import_time,
        "summary": {
            "parsed_watches": [watch],
            "import_classification": "request_intent",
            "message_text": "WTB Rolex Daytona 126500LN",
        },
    }


def _matching_offer(
    *,
    offer_id: str = "offer-1",
    watch_id: str = "watch-1",
    dealer_id: str = "dealer-1",
    brand: str = "Rolex",
    reference: str = "126500LN",
    usd_price: int = 24000,
    received_at: str = "2026-06-27T12:00:00+00:00",
    dealer_name: str = "HK Dealer",
    country: str = "Hong Kong",
    contact_type: str = "dealer",
    owner_user_id: str | None = None,
) -> dict:
    dealer: dict = {
        "id": dealer_id,
        "display_name": dealer_name,
        "phone_number": "+85291234567",
        "whatsapp_id": "85291234567",
        "contact_type": contact_type,
        "country": country,
    }
    if owner_user_id:
        dealer["owner_user_id"] = owner_user_id
        dealer["classified_by_user_id"] = owner_user_id
    return {
        "id": offer_id,
        "dealer_id": dealer_id,
        "watch_id": watch_id,
        "original_price": usd_price,
        "original_currency": "USD",
        "usd_price": usd_price,
        "condition": "new",
        "watches": {
            "brand": brand,
            "reference": reference,
            "model": "Daytona",
        },
        "dealers": dealer,
        "messages": {
            "received_at": received_at,
            "groups": {"name": "HK Dealers", "country": country},
        },
    }


NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)


class TestOpportunityScoring:
    def test_exact_reference_opportunity_scores_high(self) -> None:
        import_log = _market_request_log(reference="126500LN", price=25000)
        offer = _matching_offer(reference="126500LN", usd_price=24000)

        scored, analysis = build_market_request_opportunity_bundle(
            ADMIN_USER,
            import_log,
            offers=[offer],
            now=NOW,
        )

        assert len(scored) == 1
        assert scored[0]["opportunity_score"] >= 90
        assert scored[0]["confidence_label"] == "Excellent"
        assert "✔ Exact reference" in scored[0]["reasons"]
        assert analysis["has_opportunities"] is True
        assert analysis["confidence_label"] == "Excellent"

    def test_budget_spread_calculation(self) -> None:
        import_log = _market_request_log(price=25000)
        offer = _matching_offer(usd_price=24000)

        result = score_market_request_opportunity(
            import_log,
            offer,
            match_type="exact_reference",
            now=NOW,
        )

        assert result["potential_spread_usd"] == 1000
        assert result["potential_profit_value"] == "+$1,000"
        assert "✔ Budget above ask price" in result["positive_reasons"]

    def test_no_budget_fallback_still_scores_without_spread(self) -> None:
        import_log = _market_request_log(price=None)
        offer = _matching_offer(usd_price=24000)

        scored, analysis = build_market_request_opportunity_bundle(
            ADMIN_USER,
            import_log,
            offers=[offer],
            now=NOW,
        )

        assert len(scored) == 1
        assert scored[0]["potential_spread"] == "—"
        assert scored[0]["potential_spread_usd"] is None
        assert "⚠ Budget missing" in scored[0]["warning_reasons"]
        assert scored[0]["potential_profit_title"] == "Budget unknown"
        assert scored[0]["opportunity_score"] >= 50
        assert analysis["potential_profit_title"] == "Budget unknown"

    def test_no_matches_returns_empty_analysis(self) -> None:
        import_log = _market_request_log(reference="126500LN")
        other_offer = _matching_offer(reference="116500LN")

        scored, analysis = build_market_request_opportunity_bundle(
            ADMIN_USER,
            import_log,
            offers=[other_offer],
            now=NOW,
        )

        assert scored == []
        assert analysis["has_opportunities"] is False
        assert analysis["empty_message"] == "No opportunity found yet."
        assert build_opportunity_analysis([])["empty_message"] == "No opportunity found yet."

    def test_hidden_offer_is_not_scored_for_other_trader(self) -> None:
        import_log = _market_request_log(reference="126500LN")
        hidden_offer = _matching_offer(
            offer_id="hidden-offer",
            dealer_id="dealer-hidden",
            contact_type="removed",
            owner_user_id=TRADER_TWO["id"],
        )
        visible_offer = _matching_offer(offer_id="visible-offer", dealer_id="dealer-visible")

        trader_scored, trader_analysis = build_market_request_opportunity_bundle(
            TRADER_ONE,
            import_log,
            offers=[hidden_offer, visible_offer],
            now=NOW,
        )
        admin_scored, _ = build_market_request_opportunity_bundle(
            ADMIN_USER,
            import_log,
            offers=[hidden_offer, visible_offer],
            now=NOW,
        )

        assert [row["offer_id"] for row in trader_scored] == ["visible-offer"]
        assert len(admin_scored) == 2
        assert trader_analysis["has_opportunities"] is True
        assert all(row["offer_id"] != "hidden-offer" for row in trader_scored)


class TestOpportunityDetailPage:
    @patch("market_requests.build_market_request_opportunity_bundle")
    @patch("market_requests.get_import_log")
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_detail_page_renders_opportunity_analysis(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
        mock_opportunity_bundle: MagicMock,
    ) -> None:
        import_id = "11111111-1111-4111-8111-111111111111"
        import_log = _market_request_log(import_id=import_id)
        mock_get_import_log.return_value = import_log
        mock_list_import_logs.return_value = [import_log]
        mock_get_message.return_value = {"raw_text": "WTB Rolex Daytona 126500LN budget 25k"}
        mock_opportunity_bundle.return_value = (
            [
                {
                    "offer_id": "offer-1",
                    "watch_id": "watch-1",
                    "dealer_id": "dealer-1",
                    "dealer_name": "HK Dealer",
                    "asking_price": "$24,000",
                    "net_price": "$24,000",
                    "retail_price": "—",
                    "condition": "New",
                    "country": "Hong Kong",
                    "import_date": "2026-06-27 12:00",
                    "last_seen": "2026-06-27 12:00",
                    "offer_url": "/watch/watch-1",
                    "opportunity_score": 93,
                    "score_label": "Excellent",
                    "confidence_label": "Excellent",
                    "confidence_badge_class": "success",
                    "health": "Excellent",
                    "health_badge_class": "success",
                    "dealer_rating": "Trusted Dealer",
                    "dealer_rating_badge_class": "success",
                    "budget_known": True,
                    "urgency": "HOT",
                    "urgency_badge_class": "danger",
                    "potential_profit_value": "+$1,000",
                    "potential_profit_title": "Potential Profit",
                    "potential_profit": "+$1,000",
                    "potential_spread_usd": 1000,
                    "positive_reasons": ["✔ Exact reference", "✔ Budget above ask price"],
                    "warning_reasons": [],
                    "reasons": ["✔ Exact reference", "✔ Budget above ask price"],
                    "recommended_action": "BUY NOW",
                    "recommendation": "BUY NOW",
                    "recommendation_badge_class": "success",
                }
            ],
            {
                "has_opportunities": True,
                "empty_message": None,
                "ai_advisor_summary": "This looks like a promising opportunity.",
                "opportunity_score": 93,
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
                "potential_profit_value": "+$1,000",
                "potential_profit": "+$1,000",
                "positive_reasons": ["✔ Exact reference", "✔ Budget above ask price"],
                "warning_reasons": [],
                "reasons": ["✔ Exact reference", "✔ Budget above ask price"],
                "recommended_action": "BUY NOW",
                "recommendation": "BUY NOW",
                "recommendation_badge_class": "success",
                "best_match": {
                    "dealer_name": "HK Dealer",
                    "asking_price": "$24,000",
                    "offer_url": "/watch/watch-1",
                    "dealer_rating": "Trusted Dealer",
                    "dealer_rating_badge_class": "success",
                },
            },
        )

        client = TestClient(app)
        response = client.get(f"/market-requests/{import_id}")

        assert response.status_code == 200
        assert "Opportunity Analysis" in response.text
        assert "Opportunity Score" in response.text
        assert "Best Match" in response.text
        assert "Potential Profit" in response.text
        assert "Recommendation" in response.text
        assert "BUY NOW" in response.text
        assert "+$1,000" in response.text
        assert "HK Dealer" in response.text
        assert "AI Trading Advisor" in response.text
