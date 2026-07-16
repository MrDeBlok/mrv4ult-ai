"""Regression tests for RM section headers and variant identity."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import build_deal_analysis_cards
from condition_normalizer import (
    CONDITION_SOURCE_INHERITED_SECTION,
    CONDITION_SOURCE_INFERRED_DEFAULT,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    apply_inferred_pre_owned_defaults,
    detect_section_condition_header,
    is_section_condition_header_line,
    mark_explicit_condition_metadata,
    normalize_watch_condition,
    propagate_message_batch_condition,
    propagate_section_condition_context,
    resolve_offer_wear_condition,
)
from final_offer_payload import build_final_offer_payload
from ingest import _build_watch_row, _build_price_intelligence, _watch_identity_from_parsed
from market_price_confidence import evaluate_market_price_eligibility
from rm_model_knowledge import (
    apply_rm_enrichment,
    build_rm_identity_key,
    evaluate_rm_variant_comparability,
    rm_variants_are_exact_comparable,
)
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message, parse_watch_line

RM_MESSAGE = """🦄🦄 Richard Mille NEW 🦄🦄
RM07-01 Starry Night 7/2026 $428k usdt
RM07-01 Bright Night 6/2026 $398k usdt
RM07-01 Misty Night 6/2026 $380k usdt
RM07-01 Black NTPT Jet Black 6/2026 $196k usdt
RM07-01 NTPT Carbon Rose Gold Bracelet 4/2026 $225k usdt
RM07-01 Black Ceramic One Diamond Blk lip 6/2026 $331k usdt
RM07-04 Pink Yuliya Levchenko 6/2026 $563k usdt
RM010 Green NTPT 2026 $270k usdt
RM30-01 Le Mans Ti 5/2026 $330k usdt
RM30-01 White Ceramic 7/2026 $380k usdt
RM65-01 Mclaren 6/2026 $445k usdt
RM21-02 Cotton candy 2026 $1.34m usdt
RM72-01 Black Ceramics 6/2026 $435k usdt

🦄🦄 Richard Mille Used 🦄🦄
RM65-01 Grey 2023 HKD2.74m / $354k usdt
RM011-FM Rose Gold 2015 HKD1.36m / $175k usdt
RM011 Shot Blast Black 2011 HKD1.30m / $168k usdt
RM11-03 Red 2020 HKD1.90m / $245k usdt
RM11-03 Titanium 2017 HKD1.53m / $198k usdt"""


def _parse_full_pipeline(message: str) -> list[dict]:
    watches = [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]
    watches = propagate_message_batch_condition(message, watches)
    watches = apply_inferred_pre_owned_defaults(watches)
    return [mark_explicit_condition_metadata(watch) for watch in watches]


class TestSectionConditionHeaders:
    def test_detects_richard_mille_new_header(self) -> None:
        condition, raw = detect_section_condition_header("🦄🦄 Richard Mille NEW 🦄🦄")
        assert condition == NEW_CONDITION
        assert raw == "Richard Mille NEW"

    def test_detects_richard_mille_used_header(self) -> None:
        condition, raw = detect_section_condition_header("🦄🦄 Richard Mille Used 🦄🦄")
        assert condition == PRE_OWNED_CONDITION
        assert raw == "Richard Mille Used"

    def test_product_line_with_new_and_price_is_not_header(self) -> None:
        assert detect_section_condition_header("NEW OLD STOCK 145k USD") == (None, None)
        assert not is_section_condition_header_line("NEW OLD STOCK 145k USD")

    def test_model_name_with_used_is_not_header(self) -> None:
        assert detect_section_condition_header("RM11-03 Used Look Titanium 2017 $198k") == (None, None)
        assert not is_section_condition_header_line("RM11-03 Used Look Titanium 2017 $198k")

    def test_mclaren_line_does_not_trigger_n_notation_condition(self) -> None:
        watch = _parse_full_pipeline(
            "🦄🦄 Richard Mille NEW 🦄🦄\nRM65-01 Mclaren 6/2026 $445k usdt"
        )[0]
        assert watch["condition"] == NEW_CONDITION
        assert watch.get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION
        assert watch.get("canonical_variant") == "McLaren"

    def test_headers_do_not_create_offer_rows(self) -> None:
        parsed = parse_message(
            "🦄🦄 Richard Mille NEW 🦄🦄\nRM07-01 Starry Night 7/2026 $428k usdt"
        )
        assert len(parsed["watches"]) == 1
        assert parsed["watches"][0]["reference"] == "RM07-01"


class TestRichardMilleFullMessage:
    def test_new_section_rows_are_new_from_header(self) -> None:
        watches = _parse_full_pipeline(RM_MESSAGE)
        new_rows = watches[:13]

        assert len(new_rows) == 13
        assert all(watch["condition"] == NEW_CONDITION for watch in new_rows)
        assert all(
            watch.get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION
            for watch in new_rows
        )
        assert all(
            watch.get("condition_source") != CONDITION_SOURCE_INFERRED_DEFAULT
            for watch in new_rows
        )

    def test_used_section_rows_are_pre_owned_from_header(self) -> None:
        watches = _parse_full_pipeline(RM_MESSAGE)
        used_rows = watches[13:]

        assert len(used_rows) == 5
        assert all(watch["condition"] == PRE_OWNED_CONDITION for watch in used_rows)
        assert all(
            watch.get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION
            for watch in used_rows
        )

    @pytest.mark.parametrize(
        ("line", "expected_variant", "expected_material", "expected_gem", "expected_edition"),
        [
            ("RM07-01 Starry Night 7/2026 $428k usdt", "Starry Night", None, None, "Starry Night"),
            ("RM07-01 Bright Night 6/2026 $398k usdt", "Bright Night", None, None, "Bright Night"),
            ("RM07-01 Misty Night 6/2026 $380k usdt", "Misty Night", None, None, "Misty Night"),
            ("RM07-01 Black NTPT Jet Black 6/2026 $196k usdt", "Jet Black", "NTPT", None, "Jet Black"),
            (
                "RM07-01 NTPT Carbon Rose Gold Bracelet 4/2026 $225k usdt",
                None,
                "Carbon NTPT",
                None,
                None,
            ),
            (
                "RM07-01 Black Ceramic One Diamond Blk lip 6/2026 $331k usdt",
                None,
                "Black Ceramic",
                "One Diamond",
                None,
            ),
            (
                "RM07-04 Pink Yuliya Levchenko 6/2026 $563k usdt",
                "Yuliya Levchenko",
                None,
                None,
                "Yuliya Levchenko",
            ),
            ("RM30-01 Le Mans Ti 5/2026 $330k usdt", "Le Mans", "Titanium", None, "Le Mans"),
            ("RM30-01 White Ceramic 7/2026 $380k usdt", None, "White Ceramic", None, None),
            ("RM65-01 Mclaren 6/2026 $445k usdt", "McLaren", None, None, "McLaren"),
            ("RM21-02 Cotton candy 2026 $1.34m usdt", "Cotton Candy", None, None, "Cotton Candy"),
        ],
    )
    def test_variant_attributes_are_extracted(
        self,
        line: str,
        expected_variant: str | None,
        expected_material: str | None,
        expected_gem: str | None,
        expected_edition: str | None,
    ) -> None:
        watch = apply_rm_enrichment(parse_watch_line(line) or {}, line)
        assert watch.get("canonical_variant") == expected_variant
        assert watch.get("case_material") == expected_material
        assert watch.get("gem_setting") == expected_gem
        assert watch.get("edition") == expected_edition
        assert watch.get("rm_identity_key")

    def test_variant_identities_are_unique_for_rm07_01_family(self) -> None:
        watches = _parse_full_pipeline(RM_MESSAGE)
        rm07_keys = {
            watch["rm_identity_key"]
            for watch in watches
            if watch.get("reference") == "RM07-01" and watch.get("rm_identity_key")
        }
        assert len(rm07_keys) >= 4

    def test_section_context_resets_between_messages(self) -> None:
        first = _parse_full_pipeline("🦄🦄 Richard Mille NEW 🦄🦄\nRM07-01 Starry Night 7/2026 $428k usdt")
        watches = [
            normalize_watch_condition(enrich_parsed_watch(watch))
            for watch in parse_message("RM07-01 Bright Night 6/2026 $398k usdt")["watches"]
        ]
        second = propagate_section_condition_context(
            "RM07-01 Bright Night 6/2026 $398k usdt",
            watches,
        )
        assert first[0]["condition"] == NEW_CONDITION
        assert resolve_offer_wear_condition(second[0].get("condition"), second[0].get("raw_condition")) is None


class TestRichardMilleComparables:
    def _watch(self, **overrides) -> dict:
        base = {
            "brand": "Richard Mille",
            "reference": "RM07-01",
            "condition": NEW_CONDITION,
            "production_year": 2026,
            "original_price": 400_000,
            "original_currency": "USDT",
            "usd_price": 400_000,
            "condition_source": CONDITION_SOURCE_INHERITED_SECTION,
            "condition_explicit": True,
        }
        base.update(overrides)
        enriched = apply_rm_enrichment(base, base.get("source_line") or "")
        base.update(enriched)
        base["rm_identity_key"] = build_rm_identity_key(base)
        return base

    def test_same_reference_and_variant_are_comparable(self) -> None:
        left = self._watch(
            source_line="RM07-01 Starry Night 7/2026 $428k usdt",
            canonical_variant="Starry Night",
            edition="Starry Night",
        )
        right = self._watch(
            source_line="RM07-01 Starry Night 6/2026 $420k usdt",
            canonical_variant="Starry Night",
            edition="Starry Night",
        )
        assert rm_variants_are_exact_comparable(left, right)

    def test_starry_night_and_bright_night_are_not_comparable(self) -> None:
        left = self._watch(
            source_line="RM07-01 Starry Night 7/2026 $428k usdt",
            canonical_variant="Starry Night",
            edition="Starry Night",
        )
        right = self._watch(
            source_line="RM07-01 Bright Night 6/2026 $398k usdt",
            canonical_variant="Bright Night",
            edition="Bright Night",
        )
        result = evaluate_rm_variant_comparability(left, right)
        assert result["exact_match"] is False
        assert "edition_nickname_mismatch" in result["variant_mismatch_reasons"]

    def test_one_diamond_and_plain_are_not_comparable(self) -> None:
        left = self._watch(
            source_line="RM07-01 Black Ceramic One Diamond 6/2026 $331k usdt",
            case_material="Black Ceramic",
            gem_setting="One Diamond",
        )
        right = self._watch(
            source_line="RM07-01 Black Ceramic 6/2026 $300k usdt",
            case_material="Black Ceramic",
            gem_setting=None,
        )
        result = evaluate_rm_variant_comparability(left, right)
        assert result["exact_match"] is False
        assert "gem_setting_mismatch" in result["variant_mismatch_reasons"]

    def test_material_mismatch_excludes_comparables(self) -> None:
        left = self._watch(
            source_line="RM30-01 White Ceramic 7/2026 $380k usdt",
            reference="RM30-01",
            case_material="White Ceramic",
        )
        right = self._watch(
            source_line="RM30-01 Le Mans Ti 5/2026 $330k usdt",
            reference="RM30-01",
            canonical_variant="Le Mans",
            case_material="Titanium",
            edition="Le Mans",
        )
        result = evaluate_rm_variant_comparability(left, right)
        assert result["exact_match"] is False
        assert "material_mismatch" in result["variant_mismatch_reasons"]

    def test_unknown_variant_does_not_merge_with_known_variant(self) -> None:
        left = self._watch(source_line="RM07-01 6/2026 $300k usdt")
        right = self._watch(
            source_line="RM07-01 Starry Night 7/2026 $428k usdt",
            canonical_variant="Starry Night",
            edition="Starry Night",
        )
        assert not rm_variants_are_exact_comparable(left, right)

    def test_mclaren_and_standard_rm65_01_are_not_comparable(self) -> None:
        left = self._watch(
            source_line="RM65-01 Mclaren 6/2026 $445k usdt",
            reference="RM65-01",
            canonical_variant="McLaren",
            edition="McLaren",
        )
        right = self._watch(
            source_line="RM65-01 Grey 2023 $354k usdt",
            reference="RM65-01",
            dial_variant="Grey",
            condition=PRE_OWNED_CONDITION,
        )
        result = evaluate_rm_variant_comparability(left, right)
        assert result["exact_match"] is False
        assert "edition_nickname_mismatch" in result["variant_mismatch_reasons"]

    def test_rose_gold_bracelet_and_plain_are_not_comparable(self) -> None:
        left = self._watch(
            source_line="RM07-01 NTPT Carbon Rose Gold Bracelet 4/2026 $225k usdt",
            case_material="Carbon NTPT",
            bracelet_variant="Rose Gold Bracelet",
        )
        right = self._watch(
            source_line="RM07-01 Black NTPT Jet Black 6/2026 $196k usdt",
            canonical_variant="Jet Black",
            case_material="NTPT",
            edition="Jet Black",
        )
        result = evaluate_rm_variant_comparability(left, right)
        assert result["exact_match"] is False
        assert "bracelet_mismatch" in result["variant_mismatch_reasons"]

    def test_new_and_used_variants_are_never_comparable(self) -> None:
        left = self._watch(
            source_line="RM07-01 Starry Night 7/2026 $428k usdt",
            canonical_variant="Starry Night",
            edition="Starry Night",
            condition=NEW_CONDITION,
        )
        right = self._watch(
            source_line="RM07-01 Starry Night 2020 $350k usdt",
            canonical_variant="Starry Night",
            edition="Starry Night",
            condition=PRE_OWNED_CONDITION,
        )
        assert left["condition"] != right["condition"]


class TestSaveRowAndDealAnalysis:
    def test_save_row_rebuilds_rm_identity_key(self) -> None:
        row = {
            "raw_row_text": "RM07-01 Starry Night 7/2026 $428k usdt",
            "detected_brand": "Richard Mille",
            "detected_reference": "RM07-01",
            "normalized_brand": "Richard Mille",
            "normalized_reference": "RM07-01",
            "normalized_condition": NEW_CONDITION,
            "normalized_price": 428_000,
            "normalized_currency": "USDT",
        }
        payload = build_final_offer_payload(
            row,
            {"condition": NEW_CONDITION, "reference": "RM07-01", "brand": "Richard Mille"},
        )
        assert payload["rm_identity_key"]
        assert payload["canonical_variant"] == "Starry Night"

    def test_deal_analysis_classifies_inherited_new_condition(self) -> None:
        watches = _parse_full_pipeline(
            "🦄🦄 Richard Mille NEW 🦄🦄\nRM07-01 Starry Night 7/2026 $428k usdt"
        )
        rows = [
            _build_watch_row(
                watch,
                watch_created=False,
                offer_created=True,
                offer_id="offer-1",
                request_matches=[],
                price_intelligence=_build_price_intelligence(
                    watch.get("usd_price"),
                    [],
                    is_duplicate=False,
                    market_condition=NEW_CONDITION,
                ),
            )
            for watch in watches
        ]
        analyses = build_deal_analysis_cards(
            {"rows": rows, "parsed_watches": watches, "offer_watches": watches},
            include_debug=True,
        )
        assert analyses[0]["condition_label"] == "New"
        assert analyses[0]["debug"]["normalized_condition"] == "New"

    def test_watch_identity_separates_variants_in_storage(self) -> None:
        starry = apply_rm_enrichment(
            {"brand": "Richard Mille", "reference": "RM07-01"},
            "RM07-01 Starry Night 7/2026 $428k usdt",
        )
        bright = apply_rm_enrichment(
            {"brand": "Richard Mille", "reference": "RM07-01"},
            "RM07-01 Bright Night 6/2026 $398k usdt",
        )
        assert _watch_identity_from_parsed(starry)["dial"] != _watch_identity_from_parsed(bright)["dial"]
