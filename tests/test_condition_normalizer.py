"""Tests for watch wear condition normalization."""

from __future__ import annotations

import pytest

from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    display_condition,
    normalize_condition_value,
    normalize_watch_condition,
    normalize_wear_condition,
)
from ingest import _build_watch_row
from request_matching import match_offer_against_requests


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Brand New", NEW_CONDITION),
        ("Brand new", NEW_CONDITION),
        ("BN", NEW_CONDITION),
        ("New", NEW_CONDITION),
        ("Unworn", NEW_CONDITION),
        ("unworn", NEW_CONDITION),
        ("bnib", NEW_CONDITION),
    ],
)
def test_maps_new_conditions(raw: str, expected: str) -> None:
    normalized, raw_condition = normalize_wear_condition(raw)
    assert normalized == expected
    if raw != expected:
        assert raw_condition == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mint", PRE_OWNED_CONDITION),
        ("Worn", PRE_OWNED_CONDITION),
        ("Pre-Owned", PRE_OWNED_CONDITION),
        ("Preowned", PRE_OWNED_CONDITION),
        ("Pre owned", PRE_OWNED_CONDITION),
        ("Used", PRE_OWNED_CONDITION),
        ("used", PRE_OWNED_CONDITION),
    ],
)
def test_maps_pre_owned_conditions(raw: str, expected: str) -> None:
    normalized, raw_condition = normalize_wear_condition(raw)
    assert normalized == expected
    if raw != expected:
        assert raw_condition == raw


def test_accessory_conditions_keep_raw_without_normalized_wear() -> None:
    normalized, raw_condition = normalize_wear_condition("full set")
    assert normalized is None
    assert raw_condition == "full set"


def test_normalize_watch_condition_updates_parsed_watch() -> None:
    watch = normalize_watch_condition({"condition": "Used", "brand": "Rolex"})
    assert watch["condition"] == PRE_OWNED_CONDITION
    assert watch["raw_condition"] == "Used"


def test_normalize_watch_condition_clears_accessory_only_condition() -> None:
    watch = normalize_watch_condition({"condition": "watch only", "watch_only": True})
    assert watch["condition"] is None
    assert watch["raw_condition"] == "watch only"


def test_display_condition_normalizes_legacy_values() -> None:
    assert display_condition("Used") == PRE_OWNED_CONDITION
    assert display_condition("full set") == "N/A"


def test_normalize_condition_value_for_storage() -> None:
    assert normalize_condition_value("Mint") == PRE_OWNED_CONDITION
    assert normalize_condition_value("full set") is None


def test_build_watch_row_stores_raw_condition_in_summary() -> None:
    row = _build_watch_row(
        {
            "brand": "Rolex",
            "reference": "116508",
            "condition": NEW_CONDITION,
            "raw_condition": "Unworn",
        },
        watch_created=False,
        offer_created=True,
        offer_id="offer-1",
        request_matches=[],
        price_intelligence={
            "rank": "1",
            "previous_lowest_usd": "N/A",
            "price_difference": "N/A",
            "label": "No comparables",
            "label_class": "secondary",
            "market_condition": NEW_CONDITION,
        },
    )

    assert row["condition"] == NEW_CONDITION
    assert row["raw_condition"] == "Unworn"


class TestRequestMatchingConditions:
    def test_used_offer_matches_pre_owned_request(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "condition": "Used",
            "original_price": 45000,
            "original_currency": "USD",
        }
        requests = [
            {
                "id": "req-1",
                "status": "open",
                "brand": "Rolex",
                "reference": "116508",
                "condition": "Pre-Owned",
                "max_price": 50000,
                "currency": "USD",
            }
        ]

        matches = match_offer_against_requests(offer, requests)
        assert len(matches) == 1

    def test_unworn_offer_matches_new_request(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "condition": "Unworn",
            "original_price": 45000,
            "original_currency": "USD",
        }
        requests = [
            {
                "id": "req-1",
                "status": "open",
                "brand": "Rolex",
                "reference": "116508",
                "condition": "New",
                "max_price": 50000,
                "currency": "USD",
            }
        ]

        matches = match_offer_against_requests(offer, requests)
        assert len(matches) == 1

    def test_accessory_only_offer_does_not_match_new_request(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "condition": "full set",
            "original_price": 45000,
            "original_currency": "USD",
        }
        requests = [
            {
                "id": "req-1",
                "status": "open",
                "brand": "Rolex",
                "reference": "116508",
                "condition": "New",
                "max_price": 50000,
                "currency": "USD",
            }
        ]

        assert match_offer_against_requests(offer, requests) == []
