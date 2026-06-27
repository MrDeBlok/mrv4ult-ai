"""Unit tests for import price intelligence market comparisons."""

from __future__ import annotations

from ingest import (
    _build_price_intelligence,
    _comparable_usd_prices,
    _price_intelligence_label,
)


class TestComparableOffers:
    def test_excludes_current_offer_id(self) -> None:
        active_offers = [
            ("offer-current", 21060),
            ("offer-other", 22500),
        ]

        comparables = _comparable_usd_prices(
            active_offers,
            exclude_offer_ids={"offer-current"},
        )

        assert comparables == [22500]

    def test_first_offer_ever_has_no_comparables(self) -> None:
        comparables = _comparable_usd_prices([], exclude_offer_ids={"offer-new"})

        assert comparables == []


class TestPriceIntelligence:
    def test_first_offer_ever_imported(self) -> None:
        intelligence = _build_price_intelligence(
            21060,
            [],
            is_duplicate=False,
        )

        assert intelligence["previous_lowest_usd"] == "N/A"
        assert intelligence["price_difference"] == "N/A"
        assert intelligence["rank"] == "N/A"
        assert intelligence["label"] == "No comparables"

    def test_duplicate_offer_excludes_self_from_market(self) -> None:
        intelligence = _build_price_intelligence(
            21060,
            _comparable_usd_prices(
                [("duplicate-offer", 21060)],
                exclude_offer_ids={"duplicate-offer"},
            ),
            is_duplicate=True,
        )

        assert intelligence["previous_lowest_usd"] == "N/A"
        assert intelligence["price_difference"] == "N/A"
        assert intelligence["rank"] == "N/A"
        assert intelligence["label"] == "Duplicate offer"

    def test_compares_against_other_active_offers_only(self) -> None:
        intelligence = _build_price_intelligence(
            20000,
            [22500, 24000],
            is_duplicate=False,
        )

        assert intelligence["previous_lowest_usd"] == "$22,500"
        assert intelligence["price_difference"] == "-$2,500"
        assert intelligence["rank"] == "1"
        assert _price_intelligence_label(20000, [22500, 24000]) == "New lowest price"
