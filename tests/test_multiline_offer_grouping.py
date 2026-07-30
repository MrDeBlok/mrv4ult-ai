"""Regression tests for multi-line watch offer grouping."""

from __future__ import annotations

import pytest

from offer_line_classifier import (
    PRICE_LINE,
    WATCH_IDENTITY_LINE,
    YEAR_SET_METADATA_LINE,
    classify_offer_line,
)
from watch_parser import parse_message, parse_watch_line

RM35_MULTILINE = """RM35-01 ntpt
2016Full set
USDT:253000"""


class TestOfferLineClassification:
    def test_identity_line_for_rm_reference(self) -> None:
        assert classify_offer_line("RM35-01 ntpt") == WATCH_IDENTITY_LINE

    def test_year_set_metadata_line(self) -> None:
        assert classify_offer_line("2016Full set") == YEAR_SET_METADATA_LINE
        assert classify_offer_line("2016 Full Set") == YEAR_SET_METADATA_LINE

    @pytest.mark.parametrize(
        "line",
        [
            "USDT:253000",
            "USDT 253000",
            "253000 USDT",
            "US$253,000",
        ],
    )
    def test_price_lines(self, line: str) -> None:
        assert classify_offer_line(line) == PRICE_LINE


class TestRm35MultilineOffer:
    def test_exact_three_line_input_parses_one_watch(self) -> None:
        result = parse_message(RM35_MULTILINE)

        assert len(result["watches"]) == 1
        watch = result["watches"][0]
        assert watch["brand"] == "Richard Mille"
        assert watch["reference"] == "RM35-01"
        assert watch["production_year"] == 2016
        assert watch["full_set"] is True
        assert watch["original_price"] == 253_000
        assert watch["original_currency"] == "USDT"

    @pytest.mark.parametrize(
        ("message", "expected_price", "expected_currency"),
        [
            (RM35_MULTILINE, 253_000, "USDT"),
            (
                "RM35-01 ntpt\n2016 Full Set\nUSDT 253000",
                253_000,
                "USDT",
            ),
            (
                "RM35-01 ntpt\n2016 Full Set\n253000 USDT",
                253_000,
                "USDT",
            ),
            (
                "rm35-01 ntpt\n2016 full set\nusdt:253000",
                253_000,
                "USDT",
            ),
        ],
    )
    def test_price_and_metadata_variants(
        self,
        message: str,
        expected_price: int,
        expected_currency: str,
    ) -> None:
        result = parse_message(message)
        assert len(result["watches"]) == 1
        watch = result["watches"][0]
        assert watch["brand"] == "Richard Mille"
        assert watch["reference"] == "RM35-01"
        assert watch["production_year"] == 2016
        assert watch["full_set"] is True
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == expected_currency

    def test_no_phantom_ap_or_rolex_records(self) -> None:
        result = parse_message(RM35_MULTILINE)
        brands = {watch.get("brand") for watch in result["watches"]}
        references = {watch.get("reference") for watch in result["watches"]}

        assert brands == {"Richard Mille"}
        assert "2016FULL" not in references
        assert "253000" not in references

    def test_ntpt_does_not_corrupt_reference(self) -> None:
        watch = parse_watch_line("RM35-01 ntpt")
        assert watch is not None
        assert watch["reference"] == "RM35-01"
        assert watch.get("nickname") == "ntpt" or "ntpt" in (watch.get("notes") or "")


class TestMultipleWatchesWithFollowingMetadata:
    def test_two_watches_each_with_metadata_lines(self) -> None:
        message = """RM35-01 ntpt
2016Full set
USDT:253000
15500ST blue
2023 Full set
520000 HKD"""
        result = parse_message(message)

        assert len(result["watches"]) == 2
        rm, ap = result["watches"]
        assert rm["brand"] == "Richard Mille"
        assert rm["reference"] == "RM35-01"
        assert rm["production_year"] == 2016
        assert rm["original_price"] == 253_000
        assert rm["original_currency"] == "USDT"

        assert ap["brand"] == "Audemars Piguet"
        assert ap["reference"] == "15500ST"
        assert ap["production_year"] == 2023
        assert ap["original_price"] == 520_000
        assert ap["original_currency"] == "HKD"
