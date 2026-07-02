"""Tests for Sprint 47.1 Deal Analysis card identity display."""

from __future__ import annotations

from app import build_deal_analysis_cards
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION
from ingest import _comparable_usd_prices


def _summary(*, row: dict, parsed_watch: dict | None = None, offer_watch: dict | None = None) -> dict:
    summary: dict = {"rows": [row]}
    if parsed_watch is not None:
        summary["parsed_watches"] = [parsed_watch]
    if offer_watch is not None:
        summary["offer_watches"] = [offer_watch]
    return summary


class TestDealAnalysisCardTitles:
    def test_vacheron_offer_with_reference_renders_brand_reference(self) -> None:
        row = {
            "brand": "Vacheron Constantin",
            "reference": "4520V",
            "condition": NEW_CONDITION,
            "usd_price": 85_000,
            "previous_lowest_usd": "N/A",
            "price_label": "No comparables",
            "market_condition": None,
        }
        watch = {
            "brand": "Vacheron Constantin",
            "reference": "4520V",
            "model": "Overseas",
            "condition": NEW_CONDITION,
        }

        analysis = build_deal_analysis_cards(_summary(row=row, parsed_watch=watch, offer_watch=watch))[0]

        assert analysis["title"] == "Vacheron Constantin · 4520V"

    def test_patek_offer_with_reference_renders_brand_reference(self) -> None:
        row = {
            "brand": "Patek Philippe",
            "reference": "5267/200A",
            "condition": NEW_CONDITION,
            "usd_price": 180_000,
            "previous_lowest_usd": "N/A",
            "price_label": "No comparables",
            "market_condition": None,
        }
        watch = {"brand": "Patek Philippe", "reference": "5267/200A", "condition": NEW_CONDITION}

        analysis = build_deal_analysis_cards(_summary(row=row, parsed_watch=watch, offer_watch=watch))[0]

        assert analysis["title"] == "Patek Philippe · 5267/200A"

    def test_rolex_offer_with_reference_still_renders_correctly(self) -> None:
        row = {
            "brand": "Rolex",
            "reference": "126610LN",
            "condition": NEW_CONDITION,
            "usd_price": 12_500,
            "previous_lowest_usd": "$13,000",
            "price_label": "Good price",
            "market_condition": NEW_CONDITION,
        }
        watch = {"brand": "Rolex", "reference": "126610LN", "condition": NEW_CONDITION}

        analysis = build_deal_analysis_cards(_summary(row=row, parsed_watch=watch, offer_watch=watch))[0]

        assert analysis["title"] == "Rolex · 126610LN"

    def test_reference_on_row_only_when_watch_match_lacks_reference(self) -> None:
        row = {
            "brand": "Vacheron Constantin",
            "reference": "4520V",
            "model": "Overseas",
            "condition": NEW_CONDITION,
            "usd_price": 85_000,
            "previous_lowest_usd": "N/A",
            "price_label": "No comparables",
            "market_condition": None,
        }
        mismatched_watch = {"brand": "Vacheron Constantin", "reference": None, "model": "Overseas"}

        analysis = build_deal_analysis_cards(
            _summary(row=row, parsed_watch=mismatched_watch, offer_watch=mismatched_watch)
        )[0]

        assert analysis["title"] == "Vacheron Constantin · 4520V"

    def test_reference_in_model_field_renders_brand_model(self) -> None:
        row = {
            "brand": "Vacheron Constantin",
            "reference": "N/A",
            "model": "4520V",
            "condition": NEW_CONDITION,
            "usd_price": 85_000,
            "previous_lowest_usd": "N/A",
            "price_label": "No comparables",
            "market_condition": None,
        }
        watch = {
            "brand": "Vacheron Constantin",
            "reference": None,
            "model": "4520V",
            "condition": NEW_CONDITION,
        }

        analysis = build_deal_analysis_cards(_summary(row=row, parsed_watch=watch, offer_watch=watch))[0]

        assert analysis["title"] == "Vacheron Constantin · 4520V"

    def test_missing_reference_but_model_renders_brand_model(self) -> None:
        row = {
            "brand": "Rolex",
            "reference": "N/A",
            "model": "Submariner",
            "condition": NEW_CONDITION,
            "usd_price": 12_500,
            "previous_lowest_usd": "N/A",
            "price_label": "No comparables",
            "market_condition": None,
        }
        watch = {"brand": "Rolex", "reference": None, "model": "Submariner"}

        analysis = build_deal_analysis_cards(_summary(row=row, parsed_watch=watch, offer_watch=watch))[0]

        assert analysis["title"] == "Rolex · Submariner"

    def test_missing_reference_and_model_renders_unknown_reference(self) -> None:
        row = {
            "brand": "Vacheron Constantin",
            "reference": "N/A",
            "condition": NEW_CONDITION,
            "usd_price": 85_000,
            "previous_lowest_usd": "N/A",
            "price_label": "No comparables",
            "market_condition": None,
        }
        watch = {"brand": "Vacheron Constantin", "reference": None}

        analysis = build_deal_analysis_cards(_summary(row=row, parsed_watch=watch, offer_watch=watch))[0]

        assert analysis["title"] == "Vacheron Constantin · Unknown reference"


class TestDealAnalysisConditionSafety:
    def test_deal_analysis_still_uses_same_condition_comparables_only(self) -> None:
        comparables, market_condition = _comparable_usd_prices(
            [("new", 13_500, NEW_CONDITION), ("used", 10_800, PRE_OWNED_CONDITION)],
            exclude_offer_ids=set(),
            offer_condition=NEW_CONDITION,
        )
        assert comparables == [13_500]
        assert market_condition == NEW_CONDITION

        analysis = build_deal_analysis_cards(
            _summary(
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
