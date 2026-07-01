"""Sprint 45.0 — Brand Knowledge Engine reference patterns."""

from __future__ import annotations

import pytest

from brand_knowledge import (
    BRAND_KNOWLEDGE,
    extract_reference_from_brand_knowledge,
    get_brand_knowledge,
    reference_matches_brand_pattern,
)
from watch_parser import parse_message, parse_watch_line


class TestBrandKnowledgeModule:
    def test_piaget_patterns_registered(self) -> None:
        knowledge = get_brand_knowledge("Piaget")
        assert knowledge is not None
        assert any("G0A" in pattern for pattern in knowledge.reference_patterns)

    def test_cartier_patterns_registered(self) -> None:
        knowledge = get_brand_knowledge("Cartier")
        assert knowledge is not None
        assert any("WSSA" in pattern for pattern in knowledge.reference_patterns)

    def test_all_knowledge_lives_in_one_module(self) -> None:
        assert "Piaget" in BRAND_KNOWLEDGE
        assert "Rolex" in BRAND_KNOWLEDGE
        assert "Richard Mille" in BRAND_KNOWLEDGE


class TestBrandKnowledgeReferences:
    @pytest.mark.parametrize(
        ("line", "expected_brand", "expected_reference"),
        [
            ("Piaget G0A49023", "Piaget", "G0A49023"),
            ("Piaget G0A47010 full set 42k", "Piaget", "G0A47010"),
            ("ROLEX 126500LN white 305k", "Rolex", "126500LN"),
            ("AP 26240ST blue 2024 full set", "Audemars Piguet", "26240ST"),
            ("Cartier WSSA0032 steel 8200 usd", "Cartier", "WSSA0032"),
            ("RM 35-02 Rafael Nadal 280k", "Richard Mille", "RM 35-02"),
        ],
    )
    def test_brand_specific_references(
        self,
        line: str,
        expected_brand: str,
        expected_reference: str,
    ) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["brand"] == expected_brand
        assert watch["reference"] == expected_reference
        assert watch["reference_high_confidence"] is True

    def test_piaget_dealer_list_header(self) -> None:
        message = """Piaget
G0A49023
G0A41002
G0A45004"""
        result = parse_message(message)
        assert len(result["watches"]) == 3
        assert all(watch["brand"] == "Piaget" for watch in result["watches"])
        assert [watch["reference"] for watch in result["watches"]] == [
            "G0A49023",
            "G0A41002",
            "G0A45004",
        ]
        assert all(watch["reference_high_confidence"] for watch in result["watches"])


class TestGenericFallback:
    def test_unknown_brand_still_uses_generic_parser(self) -> None:
        watch = parse_watch_line("VC 4500V black 420k hkd")
        assert watch is not None
        assert watch["brand"] == "Vacheron Constantin"
        assert watch["reference"] == "4500V"
        assert watch["reference_high_confidence"] is False

    def test_generic_reference_formats_unchanged(self) -> None:
        cases = [
            ("PP 5711 green jub 620k", "5711"),
            ("PP 5711/1A blue n6/26 580k", "5711/1A"),
            ("AP 5980R grey 410k", "5980R"),
            ("ROLEX 126500LN white 305k", "126500LN"),
            ("116500 black n5/24 240k usd", "116500"),
            ("AP 15500ST blue full set 320k", "15500ST"),
            ("AP 15407 openworked 890k", "15407"),
            ("RM67 titanium 1.2m", "RM67"),
            ("RM 67-01 RG 1.88m", "RM 67-01"),
        ]
        for line, expected_reference in cases:
            watch = parse_watch_line(line)
            assert watch is not None
            assert watch["reference"] == expected_reference


class TestBrandKnowledgeExtraction:
    def test_extract_without_brand_hint_infers_brand(self) -> None:
        match = extract_reference_from_brand_knowledge("G0A49023")
        assert match is not None
        assert match[0] == "G0A49023"
        assert match[1] == "Piaget"

    def test_brand_hint_limits_candidates(self) -> None:
        assert extract_reference_from_brand_knowledge("126500LN", brand_hint="Piaget") is None
        assert reference_matches_brand_pattern("G0A49023", "Piaget") is True
        assert reference_matches_brand_pattern("4500V", "Piaget") is False
