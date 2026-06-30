"""Tests for Sprint 40.5 USDT treated as USD currency."""

from __future__ import annotations

import pytest

from ingest import _watch_missing_fields
from parser_review import detect_watch_issues
from watch_parser import _extract_price, parse_watch_line

EXAMPLE_LINE = "AP 26239OR Blue - 2021 Full set - 101.000 Usdt"


class TestUsdtCurrencyParsing:
    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("101000 USDT", 101_000, "USD"),
            ("101000 usdt", 101_000, "USD"),
            ("101k usdt", 101_000, "USD"),
            ("101.5k USDT", 101_500, "USD"),
            (EXAMPLE_LINE, 101_000, "USD"),
        ],
    )
    def test_usdt_parses_as_usd(
        self,
        line: str,
        expected_price: int,
        expected_currency: str,
    ) -> None:
        price, currency = _extract_price(line)
        assert price == expected_price
        assert currency == expected_currency

        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == expected_currency
        assert watch["usd_price"] == expected_price

    def test_ustd_typo_parses_as_usd(self) -> None:
        price, currency = _extract_price("99000 USTD")
        assert price == 99_000
        assert currency == "USD"

    def test_example_line_no_longer_missing_price(self) -> None:
        watch = parse_watch_line(EXAMPLE_LINE)
        assert watch is not None
        assert "price" not in _watch_missing_fields(watch)

        issues, missing = detect_watch_issues(watch)
        assert "missing_price" not in issues
        assert "Price" not in missing

    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("74000usd", 74_000, "USD"),
            ("118000hkd", 118_000, "HKD"),
            ("15500ST €52k full set", 52_000, "EUR"),
            ("10.600 EUR Rolex Explorer 124273 full set", 10_600, "EUR"),
            ("305k", 305_000, "EUR"),
        ],
    )
    def test_existing_currency_parsing_unchanged(
        self,
        line: str,
        expected_price: int,
        expected_currency: str,
    ) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == expected_currency
