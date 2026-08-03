"""Regression tests for priority-based wear condition phrase parsing."""

from __future__ import annotations

import pytest

from condition_normalizer import (
    CONDITION_SOURCE_EXPLICIT,
    CONDITION_SOURCE_INHERITED_SECTION,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    apply_inferred_pre_owned_defaults,
    detect_section_condition_header,
    find_best_wear_condition_phrase,
    mark_explicit_condition_metadata,
    normalize_watch_condition,
    normalize_wear_condition,
    propagate_message_batch_condition,
)
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message

USED_LIKE_NEW_HEADER_MESSAGE = """Used Like New Full Set
5811/1G 2024 HKD1.45m
5821/1G 2023 HKD1.30m
Brand New 2025 5711/1A HKD1.90m
"""


def _parse_full_pipeline(message: str) -> list[dict]:
    watches = [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]
    watches = propagate_message_batch_condition(message, watches)
    watches = apply_inferred_pre_owned_defaults(watches)
    return [mark_explicit_condition_metadata(watch) for watch in watches]


class TestConditionPhrasePriority:
    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("Used Like New", PRE_OWNED_CONDITION),
            ("used like new", PRE_OWNED_CONDITION),
            ("Like New", PRE_OWNED_CONDITION),
            ("like new", PRE_OWNED_CONDITION),
            ("As New", PRE_OWNED_CONDITION),
            ("Almost New", PRE_OWNED_CONDITION),
            ("Mint Condition", PRE_OWNED_CONDITION),
            ("mint condition", PRE_OWNED_CONDITION),
            ("LNIB", PRE_OWNED_CONDITION),
            ("lnib", PRE_OWNED_CONDITION),
            ("Brand New", NEW_CONDITION),
            ("brand new", NEW_CONDITION),
            ("New & Unworn", NEW_CONDITION),
            ("new & unworn", NEW_CONDITION),
            ("Factory New", NEW_CONDITION),
            ("Unworn", NEW_CONDITION),
            ("NOS", NEW_CONDITION),
        ],
    )
    def test_normalize_wear_condition_phrases(self, phrase: str, expected: str) -> None:
        normalized, _raw = normalize_wear_condition(phrase)
        assert normalized == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Used Like New Full Set", PRE_OWNED_CONDITION),
            ("Like New Full Set", PRE_OWNED_CONDITION),
            ("Used, like new condition", PRE_OWNED_CONDITION),
            ("Brand New", NEW_CONDITION),
            ("New & Unworn", NEW_CONDITION),
        ],
    )
    def test_find_best_phrase_prefers_pre_owned_over_bare_new(self, text: str, expected: str) -> None:
        canonical, _label, matched = find_best_wear_condition_phrase(text)
        assert canonical == expected
        assert matched is not None
        assert "new" in matched.casefold()

    def test_used_like_new_header_is_pre_owned_not_new(self) -> None:
        condition, raw = detect_section_condition_header("Used Like New Full Set")
        assert condition == PRE_OWNED_CONDITION
        assert raw == "Used Like New"

    def test_used_like_new_header_inherits_to_offer_lines(self) -> None:
        watches = _parse_full_pipeline(USED_LIKE_NEW_HEADER_MESSAGE)

        assert len(watches) == 3
        assert watches[0]["reference"] == "5811/1G"
        assert watches[0]["condition"] == PRE_OWNED_CONDITION
        assert watches[0].get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION
        assert watches[1]["reference"] == "5821/1G"
        assert watches[1]["condition"] == PRE_OWNED_CONDITION
        assert watches[2]["condition"] == NEW_CONDITION
        assert watches[2].get("condition_source") == CONDITION_SOURCE_EXPLICIT

    def test_offer_line_under_used_like_new_header_without_inline_condition(self) -> None:
        watches = _parse_full_pipeline(
            "Used Like New Full Set\n5811/1G 2024 HKD1.45m"
        )
        assert len(watches) == 1
        assert watches[0]["reference"] == "5811/1G"
        assert watches[0]["condition"] == PRE_OWNED_CONDITION

    def test_explicit_brand_new_overrides_used_like_new_section(self) -> None:
        watches = _parse_full_pipeline(
            "Used Like New Full Set\nBrand New 2025 5711/1A HKD1.90m"
        )
        assert len(watches) == 1
        assert watches[0]["condition"] == NEW_CONDITION
        assert watches[0].get("condition_source") == CONDITION_SOURCE_EXPLICIT

    def test_like_new_inline_does_not_classify_as_new(self) -> None:
        watches = _parse_full_pipeline("PP\n⌚ Like new 2021 full set 15510ST blue HKD:200,000")
        assert len(watches) == 1
        assert watches[0]["condition"] == PRE_OWNED_CONDITION
        assert watches[0].get("condition_source") == CONDITION_SOURCE_EXPLICIT

    def test_no_phantom_new_from_substring_in_used_like_new(self) -> None:
        normalized, _raw = normalize_wear_condition("Used Like New Full Set")
        assert normalized == PRE_OWNED_CONDITION
        assert normalized != NEW_CONDITION
