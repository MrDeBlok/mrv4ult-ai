"""Tests for Sprint 47.9 De Bethune brand support."""

from __future__ import annotations

import pytest

from brand_registry import invalidate_brand_registry_cache, lookup_brand
from parser_review import detect_watch_issues
from search import _watch_matches_tokens
from unknown_brand_intelligence import extract_unknown_brand_text
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message, parse_watch_line

DEALER_SPELLINGS = (
    "De Bethune",
    "DeBethune",
    "DE BETHUNE",
    "debethune",
)

CANONICAL_BRAND = "De Bethune"


def _parse_full(message: str) -> list[dict]:
    return [
        enrich_parsed_watch(watch)
        for watch in parse_message(message)["watches"]
    ]


class TestDeBethuneBrandRegistry:
    @pytest.mark.parametrize("spelling", DEALER_SPELLINGS)
    def test_lookup_recognizes_dealer_spellings(self, spelling: str) -> None:
        invalidate_brand_registry_cache()

        assert lookup_brand(spelling) == CANONICAL_BRAND


class TestDeBethuneInlineParsing:
    @pytest.mark.parametrize("spelling", DEALER_SPELLINGS)
    def test_inline_offer_lines_use_canonical_brand(self, spelling: str) -> None:
        invalidate_brand_registry_cache()

        watch = parse_watch_line(f"{spelling} DB28 Kind of Blue 220k usd")

        assert watch is not None
        assert watch["brand"] == CANONICAL_BRAND


class TestDeBethuneHeaderPropagation:
    def test_brand_header_applies_to_following_offer_lines(self) -> None:
        invalidate_brand_registry_cache()
        message = (
            "De Bethune\n"
            "\n"
            "DB28 Kind of Blue 220k usd\n"
            "DB25 Starry Varius 180k usd\n"
            "\n"
            "Rolex\n"
            "126500LN 305k usd"
        )

        watches = _parse_full(message)

        assert len(watches) == 2
        assert watches[0]["brand"] == CANONICAL_BRAND
        assert "DB28 Kind of Blue 220k usd" in watches[0]["source_line"]
        assert "DB25 Starry Varius 180k usd" in watches[0]["source_line"]
        assert watches[1]["brand"] == "Rolex"

    @pytest.mark.parametrize("header", ("De Bethune", "DeBethune", "debethune"))
    def test_glued_header_spellings_establish_brand_context(self, header: str) -> None:
        invalidate_brand_registry_cache()
        message = f"{header}\n\nDB28 220k usd"

        watches = _parse_full(message)

        assert len(watches) == 1
        assert watches[0]["brand"] == CANONICAL_BRAND

    def test_propagated_brand_applies_to_reference_only_line(self) -> None:
        invalidate_brand_registry_cache()

        watch = parse_watch_line("DB28 220k usd", current_brand=CANONICAL_BRAND)

        assert watch is not None
        assert watch["brand"] == CANONICAL_BRAND


class TestDeBethuneSearch:
    def _sample_watch(self) -> dict:
        return {
            "brand": CANONICAL_BRAND,
            "reference": "DB28",
            "model": "Kind of Blue",
            "dial": None,
            "bracelet": None,
        }

    @pytest.mark.parametrize(
        "query",
        ("De Bethune", "debethune", "DE BETHUNE"),
    )
    def test_search_matches_de_bethune_watches(self, query: str) -> None:
        watch = self._sample_watch()

        assert _watch_matches_tokens(watch, query.split()) is True


class TestDeBethuneAiWorkbench:
    @pytest.mark.parametrize("spelling", DEALER_SPELLINGS)
    def test_recognized_brand_does_not_trigger_unknown_brand(self, spelling: str) -> None:
        invalidate_brand_registry_cache()
        watch = parse_watch_line(f"{spelling} DB28 220k usd")

        assert watch is not None
        enriched = enrich_parsed_watch(watch)
        issues, _missing = detect_watch_issues(enriched)

        assert "unknown_brand" not in issues
        assert extract_unknown_brand_text(enriched) is None

    def test_parsed_message_does_not_surface_unknown_brand_issue(self) -> None:
        invalidate_brand_registry_cache()
        watches = _parse_full("De Bethune DB28 220k usd\nDB25 180k usd")

        assert watches
        for watch in watches:
            issues, _missing = detect_watch_issues(watch)
            assert "unknown_brand" not in issues
