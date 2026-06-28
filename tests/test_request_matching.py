"""Unit tests for client request matching rules."""

from __future__ import annotations

from request_matching import match_offer_against_requests


def _request(**kwargs) -> dict:
    base = {
        "id": "req-1",
        "status": "open",
        "client_name": "Client A",
    }
    base.update(kwargs)
    return base


class TestReferenceMatching:
    def test_exact_reference_match(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "original_price": 45000,
            "original_currency": "USD",
        }
        requests = [
            _request(
                id="req-1",
                brand="Rolex",
                reference="116508",
                max_price=50000,
                currency="USD",
            )
        ]

        matches = match_offer_against_requests(offer, requests)

        assert len(matches) == 1
        assert matches[0]["match_strength"] == "strong"
        assert "116508" in matches[0]["match_reason"]


class TestAliasMatching:
    def test_brand_and_alias_match_without_reference(self) -> None:
        offer = {
            "brand": "Rolex",
            "model": "GMT-Master II",
            "nickname": "Pepsi",
            "original_price": 18000,
            "original_currency": "USD",
            "production_year": 2023,
        }
        requests = [
            _request(
                id="req-2",
                brand="Rolex",
                alias="Pepsi",
                max_price=20000,
                currency="USD",
            )
        ]

        matches = match_offer_against_requests(offer, requests)

        assert len(matches) == 1
        assert matches[0]["match_strength"] == "medium"


class TestPriceMatching:
    def test_price_too_high_no_match(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "original_price": 60000,
            "original_currency": "USD",
        }
        requests = [
            _request(
                id="req-3",
                brand="Rolex",
                reference="116508",
                max_price=50000,
                currency="USD",
            )
        ]

        assert match_offer_against_requests(offer, requests) == []


class TestYearMatching:
    def test_year_mismatch_no_match(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "production_year": 2018,
            "original_price": 45000,
            "original_currency": "USD",
        }
        requests = [
            _request(
                id="req-4",
                brand="Rolex",
                reference="116508",
                min_year=2020,
                max_year=2024,
                max_price=50000,
                currency="USD",
            )
        ]

        assert match_offer_against_requests(offer, requests) == []
