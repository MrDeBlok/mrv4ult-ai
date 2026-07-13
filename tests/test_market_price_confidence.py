"""Tests for Market Price confidence and comparable eligibility policy."""

from __future__ import annotations

from unittest.mock import patch

from condition_normalizer import (
    CONDITION_CONFIDENCE_HIGH,
    CONDITION_SOURCE_EXPLICIT,
    CONDITION_SOURCE_INFERRED_DEFAULT,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
)
from deal_market_lookup import load_active_offer_pools_by_watch_ids
from ingest import _get_active_offers
from market_price_confidence import (
    MARKET_PRICE_CONFIDENCE_THRESHOLD,
    MARKET_PRICE_WEIGHTS,
    attach_market_price_metadata,
    compute_market_price_confidence,
    evaluate_market_price_eligibility,
    filter_market_eligible_offer_rows,
    is_market_price_comparable_context,
)
from parser_confidence import attach_parser_confidence_metadata


def _core_watch(**overrides) -> dict:
    base = {
        "brand": "Rolex",
        "reference": "126610LN",
        "reference_high_confidence": True,
        "condition": NEW_CONDITION,
        "condition_source": CONDITION_SOURCE_EXPLICIT,
        "condition_confidence": CONDITION_CONFIDENCE_HIGH,
        "production_year": 2024,
        "original_price": 14500,
        "original_currency": "USD",
        "usd_price": 14500,
        "message_type": "offer",
    }
    base.update(overrides)
    return base


class TestMarketPriceConfidenceScoring:
    def test_missing_bracelet_does_not_reduce_market_price_confidence(self) -> None:
        watch = _core_watch(bracelet=None)
        score, components = compute_market_price_confidence(watch)
        assert score == 100
        assert components["trusted_reference"] == MARKET_PRICE_WEIGHTS["trusted_reference"]

    def test_missing_dial_does_not_reduce_market_price_confidence(self) -> None:
        watch = _core_watch(dial=None)
        score, _components = compute_market_price_confidence(watch)
        assert score == 100

    def test_missing_card_month_does_not_reduce_market_price_confidence(self) -> None:
        watch = _core_watch(card_date=None)
        score, _components = compute_market_price_confidence(watch)
        assert score == 100

    def test_missing_model_does_not_reduce_market_price_confidence(self) -> None:
        watch = _core_watch(model=None)
        score, _components = compute_market_price_confidence(watch)
        assert score == 100

    def test_trusted_reference_condition_year_price_currency_scores_100(self) -> None:
        watch = _core_watch()
        score, components = compute_market_price_confidence(watch)
        assert score == 100
        assert sum(components.values()) == 100

    def test_missing_year_scores_85_and_is_ineligible(self) -> None:
        watch = _core_watch(production_year=None, card_date=None)
        score, components = compute_market_price_confidence(watch)
        assert score == 85
        assert components["year"] == 0
        evaluation = evaluate_market_price_eligibility(watch)
        assert evaluation.market_price_confidence == 85
        assert evaluation.eligible is False
        assert "year_missing" in evaluation.exclusion_reasons

    def test_unknown_condition_scores_lower_and_is_always_excluded(self) -> None:
        watch = _core_watch(condition="Unknown", condition_source=None)
        score, components = compute_market_price_confidence(watch)
        assert score == 80
        assert components["explicit_condition"] == 0
        evaluation = evaluate_market_price_eligibility(watch)
        assert evaluation.eligible is False
        assert "condition_unknown" in evaluation.exclusion_reasons

    def test_suspicious_price_is_always_excluded(self) -> None:
        watch = _core_watch(original_price=9_999_999_999, usd_price=9_999_999_999)
        evaluation = evaluate_market_price_eligibility(watch)
        assert evaluation.eligible is False
        assert "suspicious_price" in evaluation.exclusion_reasons

    def test_reference_brand_conflict_is_always_excluded(self) -> None:
        watch = _core_watch(
            brand="Rolex",
            reference="4300V/120A-B642",
            reference_high_confidence=False,
            reference_brand_conflict={
                "reference": "4300V/120A-B642",
                "rejected_brand": "Rolex",
                "resolved_brand": "Vacheron Constantin",
            },
        )
        evaluation = evaluate_market_price_eligibility(watch)
        assert evaluation.eligible is False
        assert "reference_brand_conflict" in evaluation.exclusion_reasons


class TestParserConfidenceSeparation:
    def test_low_parser_confidence_does_not_exclude_when_market_price_confidence_is_high(self) -> None:
        watch = _core_watch(bracelet=None, dial=None, card_date=None, model=None)
        attach_market_price_metadata(watch)
        watch["confidence"] = 64

        evaluation = evaluate_market_price_eligibility(watch)
        assert watch["confidence"] < MARKET_PRICE_CONFIDENCE_THRESHOLD
        assert evaluation.market_price_confidence == 100
        assert evaluation.eligible is True

    def test_legacy_watch_parser_confidence_penalizes_optional_fields_but_market_price_does_not(self) -> None:
        from watch_parser import _compute_confidence

        watch = _core_watch(
            bracelet=None,
            dial=None,
            card_date=None,
            reference_high_confidence=False,
        )
        legacy_confidence = _compute_confidence(watch)
        attach_market_price_metadata(watch)

        assert legacy_confidence == 85
        assert watch["market_price_confidence"] == 100
        assert watch["market_price_eligible"] is True

    def test_attach_market_price_metadata_preserves_parser_confidence(self) -> None:
        watch = _core_watch()
        attach_parser_confidence_metadata(watch, message_type="offer")
        parser_confidence = watch["confidence"]
        attach_market_price_metadata(watch)
        assert watch["confidence"] == parser_confidence
        assert watch["market_price_confidence"] == 100


class TestComparableSelectionPolicy:
    def test_filter_market_eligible_offer_rows_uses_central_policy(self) -> None:
        eligible_row = {
            "id": "offer-1",
            "usd_price": 14000,
            "condition": NEW_CONDITION,
            "production_year": 2024,
            "original_price": 14000,
            "original_currency": "USD",
            "watches": {"brand": "Rolex", "reference": "126610LN"},
        }
        ineligible_row = {
            "id": "offer-2",
            "usd_price": 13000,
            "condition": "Unknown",
            "production_year": 2024,
            "original_price": 13000,
            "original_currency": "USD",
            "watches": {"brand": "Rolex", "reference": "126610LN"},
        }

        filtered = filter_market_eligible_offer_rows([eligible_row, ineligible_row])
        assert [row["id"] for row in filtered] == ["offer-1"]

    @patch("ingest.get_client")
    @patch("ingest.is_business_dealer_relation", return_value=True)
    @patch("ingest.contact_type_column_supported", return_value=True)
    def test_get_active_offers_applies_market_price_policy(
        self,
        _mock_contact_type: object,
        _mock_business_dealer: object,
        mock_get_client: object,
    ) -> None:
        mock_get_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": "eligible",
                "usd_price": 15000,
                "condition": NEW_CONDITION,
                "production_year": 2024,
                "card_date": None,
                "original_price": 15000,
                "original_currency": "USD",
                "watches": {"brand": "Rolex", "reference": "126610LN"},
                "dealers": {"contact_type": "business"},
            },
            {
                "id": "ineligible",
                "usd_price": 12000,
                "condition": "Unknown",
                "production_year": 2024,
                "card_date": None,
                "original_price": 12000,
                "original_currency": "USD",
                "watches": {"brand": "Rolex", "reference": "126610LN"},
                "dealers": {"contact_type": "business"},
            },
        ]

        offers = _get_active_offers("watch-1")
        assert offers == [("eligible", 15000, NEW_CONDITION)]

    @patch("database.query_active_offers_for_watch_ids")
    @patch("database.is_business_dealer_relation", return_value=True)
    def test_load_active_offer_pools_use_central_policy(
        self,
        _mock_business_dealer: object,
        mock_query: object,
    ) -> None:
        mock_query.return_value = [
            {
                "id": "offer-a",
                "watch_id": "watch-1",
                "usd_price": 18000,
                "condition": NEW_CONDITION,
                "production_year": 2023,
                "original_price": 18000,
                "original_currency": "USD",
                "watches": {"brand": "Rolex", "reference": "126610LN"},
                "dealers": {"contact_type": "business"},
            },
            {
                "id": "offer-b",
                "watch_id": "watch-1",
                "usd_price": 17000,
                "condition": "Unknown",
                "production_year": 2022,
                "original_price": 17000,
                "original_currency": "USD",
                "watches": {"brand": "Rolex", "reference": "126610LN"},
                "dealers": {"contact_type": "business"},
            },
        ]

        pools = load_active_offer_pools_by_watch_ids(["watch-1"])
        assert pools["watch-1"] == [("offer-a", 18000, NEW_CONDITION)]

    def test_is_market_price_comparable_context_matches_evaluate(self) -> None:
        watch = _core_watch()
        assert is_market_price_comparable_context(watch) == evaluate_market_price_eligibility(watch).eligible
