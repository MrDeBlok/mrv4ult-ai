"""Tests for Sprint 38.2 Opportunity Intelligence v2."""

from __future__ import annotations

from datetime import datetime, timezone

from opportunity_engine import score_market_request_opportunity, build_market_request_opportunity_bundle
from opportunity_intelligence import (
    URGENCY_HOT,
    URGENCY_NORMAL,
    URGENCY_OLD,
    build_dealer_quality_index,
    calculate_urgency,
    recommend_action,
    score_bracelet_attribute,
    score_condition_attribute,
    score_dealer_quality,
    score_dial_attribute,
    score_full_set_attribute,
    score_production_year_attribute,
    sort_opportunity_rows,
)
from tests.conftest import ADMIN_USER


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
    dealer = {
        "id": overrides.pop("dealer_id", "dealer-1"),
        "display_name": "HK Dealer",
        "contact_type": "dealer",
    }
    usd_price = overrides.pop("usd_price", 24000)
    return {
        "id": overrides.pop("offer_id", "offer-1"),
        "dealer_id": dealer["id"],
        "watch_id": "watch-1",
        "status": "active",
        "usd_price": usd_price,
        "original_price": usd_price,
        "original_currency": "USD",
        "condition": overrides.pop("condition", "new"),
        "watches": watch,
        "dealers": dealer,
        "messages": {
            "received_at": overrides.pop("received_at", "2026-06-28T11:45:00+00:00"),
            "groups": {"name": "HK Dealers", "country": "Hong Kong"},
        },
        **overrides,
    }


NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)


class TestAttributeScoring:
    def test_dial_scoring(self) -> None:
        request = {"dial": "white"}
        assert score_dial_attribute(request, {"dial": "white"}) == (12, "Matching dial")
        assert score_dial_attribute(request, {"dial": "black"}) == (-15, "Different dial")
        assert score_dial_attribute(request, {}) == (0, None)

    def test_bracelet_scoring(self) -> None:
        request = {"bracelet": "jubilee"}
        assert score_bracelet_attribute(request, {"bracelet": "jub"}) == (8, "Matching bracelet")
        assert score_bracelet_attribute(request, {"bracelet": "oyster"}) == (-8, "Different bracelet")
        assert score_bracelet_attribute(request, {}) == (0, None)

    def test_condition_scoring(self) -> None:
        request = {"condition": "New"}
        assert score_condition_attribute(request, {"condition": "new"}, {}) == (10, "Matching condition")
        assert score_condition_attribute(request, {"condition": "used"}, {}) == (-12, "Different condition")
        assert score_condition_attribute({}, {"condition": "new"}, {}) == (0, None)

    def test_full_set_scoring(self) -> None:
        request = {"full_set": True}
        offer_watch = {"full_set": True}
        assert score_full_set_attribute(request, offer_watch, {}) == (8, "Full set")
        assert score_full_set_attribute({}, offer_watch, {"condition": "full set"}) == (4, "Full set")
        assert score_full_set_attribute({"full_set": True}, {"full_set": False}, {}) == (-6, "Different completeness")

    def test_year_scoring(self) -> None:
        request = {"production_year": 2024}
        assert score_production_year_attribute(request, {"production_year": 2024}, {}) == (6, "Same production year")
        assert score_production_year_attribute(request, {"production_year": 2023}, {}) == (3, "Production year within 2 years")
        assert score_production_year_attribute(request, {"production_year": 2018}, {}) == (-5, "Production year far apart")


class TestDealerQuality:
    def test_trusted_dealer_bonus(self) -> None:
        offers = [
            _offer(offer_id=f"offer-{index}", dealer_id="trusted-dealer")
            for index in range(12)
        ]
        index = build_dealer_quality_index(offers)
        points, label = score_dealer_quality("trusted-dealer", index)
        assert points == 8
        assert label == "Trusted dealer"

    def test_new_dealer_has_no_bonus(self) -> None:
        index = build_dealer_quality_index([_offer(dealer_id="new-dealer")])
        assert score_dealer_quality("new-dealer", index) == (0, None)


class TestUrgencyAndRecommendation:
    def test_urgency_hot(self) -> None:
        urgency = calculate_urgency(
            offer_received_at="2026-06-28T11:30:00+00:00",
            request_import_time="2026-06-28T11:00:00+00:00",
            opportunity_score=90,
            now=NOW,
        )
        assert urgency == URGENCY_HOT

    def test_urgency_old(self) -> None:
        urgency = calculate_urgency(
            offer_received_at="2026-06-01T12:00:00+00:00",
            request_import_time="2026-06-01T12:00:00+00:00",
            opportunity_score=90,
            now=NOW,
        )
        assert urgency == URGENCY_OLD

    def test_urgency_normal(self) -> None:
        urgency = calculate_urgency(
            offer_received_at="2026-06-27T12:00:00+00:00",
            request_import_time="2026-06-27T12:00:00+00:00",
            opportunity_score=70,
            now=NOW,
        )
        assert urgency == URGENCY_NORMAL

    def test_recommendation_selection(self) -> None:
        assert recommend_action(92, URGENCY_HOT) == "BUY NOW"
        assert recommend_action(80, URGENCY_HOT) == "CALL TODAY"
        assert recommend_action(80, URGENCY_NORMAL) == "GOOD OPPORTUNITY"
        assert recommend_action(60, URGENCY_NORMAL) == "WATCH"
        assert recommend_action(40, URGENCY_NORMAL) == "IGNORE"
        assert recommend_action(80, URGENCY_OLD) == "WATCH"


class TestSortingAndIntegration:
    def test_sorting_by_score_urgency_profit_and_newest(self) -> None:
        rows = [
            {
                "offer_id": "low-score",
                "opportunity_score": 55,
                "urgency": URGENCY_NORMAL,
                "potential_spread_usd": 500,
                "_received_at_raw": "2026-06-27T12:00:00+00:00",
            },
            {
                "offer_id": "best",
                "opportunity_score": 95,
                "urgency": URGENCY_HOT,
                "potential_spread_usd": 1000,
                "_received_at_raw": "2026-06-28T11:45:00+00:00",
            },
            {
                "offer_id": "same-score-lower-profit",
                "opportunity_score": 95,
                "urgency": URGENCY_NORMAL,
                "potential_spread_usd": 200,
                "_received_at_raw": "2026-06-28T10:00:00+00:00",
            },
        ]

        sorted_rows = sort_opportunity_rows(rows)
        assert [row["offer_id"] for row in sorted_rows] == ["best", "same-score-lower-profit", "low-score"]

    def test_full_scoring_includes_trader_reasons(self) -> None:
        import_log = _market_request_log()
        offer = _offer()
        dealer_index = build_dealer_quality_index([offer] * 12)

        result = score_market_request_opportunity(
            import_log,
            offer,
            match_type="exact_reference",
            dealer_index=dealer_index,
            now=NOW,
        )

        assert result["opportunity_score"] >= 90
        assert "✔ Exact reference" in result["reasons"]
        assert "✔ Matching dial" in result["reasons"]
        assert "✔ Full set" in result["reasons"]
        assert any("Offer posted" in reason for reason in result["reasons"])
        assert "✔ Trusted dealer" in result["reasons"]
        assert result["urgency"] == URGENCY_HOT
        assert result["recommendation"] == "BUY NOW"

    def test_bundle_sorts_hot_high_score_offer_first(self) -> None:
        import_log = _market_request_log(import_time="2026-06-28T11:00:00+00:00")
        hot_offer = _offer(
            offer_id="hot-offer",
            usd_price=23000,
            received_at="2026-06-28T11:45:00+00:00",
        )
        stale_offer = _offer(
            offer_id="stale-offer",
            usd_price=22000,
            received_at="2026-06-01T12:00:00+00:00",
        )

        scored, analysis = build_market_request_opportunity_bundle(
            ADMIN_USER,
            import_log,
            offers=[stale_offer, hot_offer],
            now=NOW,
        )

        assert scored[0]["offer_id"] == "hot-offer"
        assert analysis["urgency"] == URGENCY_HOT
        assert analysis["recommendation"] in {"BUY NOW", "CALL TODAY", "GOOD OPPORTUNITY"}
