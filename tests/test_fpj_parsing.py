"""Tests for F.P. Journe parsing, identity, and Market Price policy."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from brand_registry import invalidate_brand_registry_cache, lookup_brand
from condition_normalizer import (
    CONDITION_CONFIDENCE_HIGH,
    CONDITION_SOURCE_EXPLICIT,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
)
from final_offer_payload import build_final_offer_payload
from fpj_model_knowledge import (
    FPJ_CANONICAL_BRAND,
    build_model_identity_key,
    fpj_models_are_exact_comparable,
    is_blocked_year_reference,
)
from market_price_confidence import (
    FPJ_MARKET_PRICE_CONFIDENCE_THRESHOLD,
    evaluate_market_price_eligibility,
)
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_watch_line


def _parse_fpj_line(line: str) -> dict:
    watch = parse_watch_line(line)
    assert watch is not None
    return enrich_parsed_watch(watch)


class TestFpjBrandAliases:
    @pytest.mark.parametrize(
        "alias",
        [
            "FPJ",
            "fpj",
            "F.P. Journe",
            "f.p. journe",
            "FP Journe",
            "F P Journe",
            "FPJourne",
            "fpjourne",
            "François-Paul Journe",
            "Francois-Paul Journe",
            "francois paul journe",
        ],
    )
    def test_aliases_resolve_to_canonical_brand(self, alias: str) -> None:
        invalidate_brand_registry_cache()
        assert lookup_brand(alias) == FPJ_CANONICAL_BRAND

    def test_explicit_fpj_outranks_ap_heuristic(self) -> None:
        watch = _parse_fpj_line(
            "FPJ Chronometre Resonance Platinum grey 40mm 2006Y 510k Usdt"
        )
        assert watch["brand"] == FPJ_CANONICAL_BRAND
        assert watch["reference"] is None

    def test_legitimate_ap_reference_still_resolves(self) -> None:
        watch = _parse_fpj_line("AP 15500ST blue 2023 62k usd")
        assert watch["brand"] == "Audemars Piguet"
        assert watch["reference"] == "15500ST"


class TestFpjYearSuffixNotation:
    @pytest.mark.parametrize(
        ("token", "expected_year"),
        [
            ("2006Y", 2006),
            ("2014Y", 2014),
            ("2021Y", 2021),
            ("2025y", 2025),
        ],
    )
    def test_year_suffix_tokens_are_years_not_references(
        self,
        token: str,
        expected_year: int,
    ) -> None:
        assert is_blocked_year_reference(token) is True
        watch = _parse_fpj_line(
            f"FPJ Chronometre Optimum Platinum 40mm {token} 850k Usdt"
        )
        assert watch["reference"] is None
        assert watch["production_year"] == expected_year

    def test_authoritative_reference_ending_in_y_is_preserved(self) -> None:
        with patch(
            "reference_knowledge.lookup_authoritative_reference",
            return_value={"brand": "Audemars Piguet", "reference": "2006Y"},
        ):
            assert is_blocked_year_reference("2006Y") is False


class TestFpjPipelineExamples:
    def test_resonance_example(self) -> None:
        watch = _parse_fpj_line(
            "FPJ Chronometre Resonance Platinum grey 40mm 2006Y 510k Usdt"
        )
        assert watch["brand"] == FPJ_CANONICAL_BRAND
        assert watch["reference"] is None
        assert watch["model"] == "Chronomètre à Résonance"
        assert watch["case_material"] == "Platinum"
        assert watch["dial_variant"] == "Grey"
        assert watch["size_mm"] == 40
        assert watch["production_year"] == 2006
        assert watch["original_price"] == 510_000
        assert watch["original_currency"] == "USDT"
        assert watch["model_identity_key"]

    def test_optimum_black_label_example(self) -> None:
        watch = _parse_fpj_line(
            "FPJ Chronometre Optimum Black Label Platinum 42mm 2021Y 850k Usdt"
        )
        assert watch["brand"] == FPJ_CANONICAL_BRAND
        assert watch["reference"] is None
        assert watch["model"] == "Chronomètre Optimum"
        assert watch["edition"] == "Black Label"
        assert watch["case_material"] == "Platinum"
        assert watch["size_mm"] == 42
        assert watch["production_year"] == 2021
        assert watch["original_price"] == 850_000

    def test_octa_calendrier_example(self) -> None:
        watch = _parse_fpj_line(
            "FPJ Journe Octa Calendrier 2014Y 40mm 303k Usdt"
        )
        assert watch["brand"] == FPJ_CANONICAL_BRAND
        assert watch["reference"] is None
        assert watch["model"] == "Octa Calendrier"
        assert watch["size_mm"] == 40
        assert watch["production_year"] == 2014
        assert watch["original_price"] == 303_000
        assert watch["model_identity_key"]


class TestFpjMarketPricePolicy:
    def _fpj_core(self, **overrides) -> dict:
        base = {
            "brand": FPJ_CANONICAL_BRAND,
            "reference": None,
            "model": "Chronomètre à Résonance",
            "model_identity_complete": True,
            "condition": PRE_OWNED_CONDITION,
            "condition_source": CONDITION_SOURCE_EXPLICIT,
            "condition_confidence": CONDITION_CONFIDENCE_HIGH,
            "production_year": 2006,
            "case_material": "Platinum",
            "dial_variant": "Grey",
            "size_mm": 40,
            "original_price": 510_000,
            "original_currency": "USD",
            "usd_price": 510_000,
        }
        base.update(overrides)
        return base

    def test_fpj_without_reference_can_be_eligible_with_model_identity(self) -> None:
        evaluation = evaluate_market_price_eligibility(self._fpj_core())
        assert evaluation.market_price_confidence == 100
        assert evaluation.threshold == FPJ_MARKET_PRICE_CONFIDENCE_THRESHOLD
        assert evaluation.eligible is True

    def test_unknown_fpj_model_is_not_market_price_eligible(self) -> None:
        evaluation = evaluate_market_price_eligibility(
            self._fpj_core(model=None, model_identity_complete=False)
        )
        assert evaluation.eligible is False
        assert "fpj_model_missing_or_ambiguous" in evaluation.exclusion_reasons

    def test_rolex_without_reference_remains_ineligible(self) -> None:
        evaluation = evaluate_market_price_eligibility(
            {
                "brand": "Rolex",
                "reference": None,
                "condition": NEW_CONDITION,
                "condition_source": CONDITION_SOURCE_EXPLICIT,
                "production_year": 2024,
                "original_price": 14_500,
                "original_currency": "USD",
                "usd_price": 14_500,
            }
        )
        assert evaluation.eligible is False
        assert "reference_missing" in evaluation.exclusion_reasons

    def test_usdt_is_not_silently_treated_as_usd(self) -> None:
        watch = _parse_fpj_line(
            "FPJ Chronometre Resonance Platinum grey 40mm 2006Y 510k Usdt"
        )
        assert watch["original_currency"] == "USDT"
        evaluation = evaluate_market_price_eligibility(watch)
        assert evaluation.eligible is False
        assert "currency_unsupported" in evaluation.exclusion_reasons


class TestFpjComparableIdentity:
    def test_black_label_and_standard_are_not_exact_comparables(self) -> None:
        standard = {
            "brand": FPJ_CANONICAL_BRAND,
            "model": "Chronomètre Optimum",
            "case_material": "Platinum",
            "edition": None,
            "size_mm": 42,
            "production_year": 2021,
        }
        black_label = {
            **standard,
            "edition": "Black Label",
        }
        assert fpj_models_are_exact_comparable(standard, black_label) is False

    def test_platinum_and_rose_gold_are_not_exact_comparables(self) -> None:
        platinum = {
            "brand": FPJ_CANONICAL_BRAND,
            "model": "Chronomètre à Résonance",
            "case_material": "Platinum",
            "dial_variant": "Grey",
            "size_mm": 40,
            "production_year": 2006,
        }
        rose_gold = {
            **platinum,
            "case_material": "Rose Gold",
        }
        assert build_model_identity_key(platinum) != build_model_identity_key(rose_gold)
        assert fpj_models_are_exact_comparable(platinum, rose_gold) is False


class TestFpjSaveRowIdentityRebuild:
    def test_save_row_rebuilds_model_identity_from_manual_corrections(self) -> None:
        row = {
            "raw_row_text": "FPJ Chronometre Resonance Platinum grey 40mm 2006Y 510k Usdt",
            "detected_brand": "Audemars Piguet",
            "detected_reference": "2006Y",
            "detected_condition": PRE_OWNED_CONDITION,
            "detected_year": 2006,
            "detected_price": 510_000,
            "detected_currency": "USDT",
            "parser_explanation": {},
        }
        payload = build_final_offer_payload(
            row,
            {
                "brand": FPJ_CANONICAL_BRAND,
                "reference": "",
                "model": "Chronomètre à Résonance",
                "case_material": "Platinum",
                "dial_variant": "Grey",
                "size_mm": "40",
                "year": "2006",
                "price": "510000",
                "currency": "USDT",
            },
        )
        assert payload["brand"] == FPJ_CANONICAL_BRAND
        assert payload.get("reference") in (None, "")
        assert payload["model"] == "Chronomètre à Résonance"
        assert payload["model_identity_key"]
        assert "black label" not in (payload.get("model_identity_key") or "").lower()
