"""Regression tests for Sprint 36.1 implicit EUR price detection."""

from __future__ import annotations

import pytest

from ingest import _import_status
from watch_parser import parse_message, parse_watch_line


EXAMPLE_MESSAGE = (
    "Rolex Submariner 126610LN\n"
    "New\n"
    "12.700 + your label"
)

PLAIN_MULTILINE_MESSAGE = (
    "Rolex Submariner 126610LN\n"
    "2025\n"
    "Full set\n"
    "12700"
)


class TestImplicitEurPriceDetection:
    def test_european_price_without_currency_defaults_to_eur(self) -> None:
        watch = parse_watch_line("126500LN 12.700 full set")

        assert watch is not None
        assert watch["original_price"] == 12_700
        assert watch["original_currency"] == "EUR"
        assert watch["usd_price"] == 13_716

    @pytest.mark.parametrize(
        ("line", "expected_price"),
        [
            ("126500LN 12700 full set", 12_700),
            ("126500LN 12,700 full set", 12_700),
            ("126500LN 12.7k full set", 12_700),
            ("126500LN 12700 net full set", 12_700),
            ("126500LN 12700 shipped", 12_700),
            ("126500LN 12700 + ship", 12_700),
            ("126500LN 12700 + label", 12_700),
        ],
    )
    def test_common_dealer_price_formats_default_to_eur(
        self,
        line: str,
        expected_price: int,
    ) -> None:
        watch = parse_watch_line(line)

        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == "EUR"

    def test_multiline_offer_with_label_suffix_becomes_success(self) -> None:
        parsed = parse_message(EXAMPLE_MESSAGE)
        summary = {
            "watches_parsed": len(parsed["watches"]),
            "duplicate_offers": 0,
        }

        watch = parsed["watches"][0]
        status, _reason = _import_status(summary, "success", parsed["watches"])

        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "126610LN"
        assert watch["original_price"] == 12_700
        assert watch["original_currency"] == "EUR"
        assert watch["usd_price"] == 13_716
        assert status == "success"

    def test_multiline_plain_price_line_becomes_success(self) -> None:
        parsed = parse_message(PLAIN_MULTILINE_MESSAGE)
        summary = {
            "watches_parsed": len(parsed["watches"]),
            "duplicate_offers": 0,
        }

        watch = parsed["watches"][0]
        status, _reason = _import_status(summary, "success", parsed["watches"])

        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "126610LN"
        assert watch["production_year"] == 2025
        assert watch["full_set"] is True
        assert watch["original_price"] == 12_700
        assert watch["original_currency"] == "EUR"
        assert watch["usd_price"] == 13_716
        assert status == "success"


class TestExplicitCurrencyUnchanged:
    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("126500LN 305k usd", 305_000, "USD"),
            ("126500LN $305k full set", 305_000, "USD"),
            ("5980R CHF 220k", 220_000, "CHF"),
            ("5711/1A HK$1,880,000 full set", 1_880_000, "HKD"),
            ("15500ST €52k full set", 52_000, "EUR"),
        ],
    )
    def test_explicit_currencies_are_not_overridden(
        self,
        line: str,
        expected_price: int,
        expected_currency: str,
    ) -> None:
        watch = parse_watch_line(line)

        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == expected_currency


class TestNonPriceNumbersIgnored:
    def test_production_year_is_not_interpreted_as_price(self) -> None:
        watch = parse_watch_line("Rolex Explorer 124273 2024 full set")

        assert watch is not None
        assert watch["production_year"] == 2024
        assert watch["original_price"] is None
        assert watch["original_currency"] is None

    def test_reference_is_not_interpreted_as_price(self) -> None:
        watch = parse_watch_line("Rolex Submariner 126610LN New full set")

        assert watch is not None
        assert watch["reference"] == "126610LN"
        assert watch["original_price"] is None
        assert watch["original_currency"] is None
