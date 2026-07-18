"""Regression tests for HK multi-brand dealer stock condition parsing."""

from __future__ import annotations

import pytest

from app import build_deal_analysis_cards
from condition_normalizer import (
    CONDITION_SOURCE_EXPLICIT,
    CONDITION_SOURCE_INHERITED_SECTION,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    apply_inferred_pre_owned_default,
    apply_inferred_pre_owned_default,
    apply_inferred_pre_owned_defaults,
    deal_condition_label,
    detect_section_condition_header,
    mark_explicit_condition_metadata,
    normalize_watch_condition,
    propagate_message_batch_condition,
)
from dealer_list_splitter import clean_dealer_list_line, expand_dealer_list_raw_lines
from ingest import _build_watch_row, _build_price_intelligence
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message, parse_watch_line

PP_BRAND_NEW_LIST = """PP brand new Hong Kong ready stock list

● 5160/500R new 2026 HKD 1.2M
● 5980/60G N11/2025 NOS HKD 980K
● 5961P N6/2026 HKD 850K
● 5924G Green N7/2026 HKD 720K"""

HK_STOCK_MESSAGE = """🇭🇰 HK STOCK【HKD】
⌚️ RM STOCK
⌚️📌 RM64-01 N7 USD 2.7M
⌚️ RM07-01 WG Snow Onyx N4-26 USDT 358K
⌚️ AP STOCK
⌚️ 4910/1200A Blue N7 HKD 121K
⌚️ PP STOCK
⌚️ 7042/100R-010 N8/25 HKD 1.505M
‼️ ⌚️ ROLEX NO BOX
⌚️ USED Rolex
⌚️ 1167R Used 2023 HKD 877K
⌚️ 1712R 2021 Used HKD 1.065M
⌚️🛫 116505 Ivory Only Watch HKD 314K
⌚️126500 Black 237K HKD N5
⌚️🛫 15210ST Green 🆕 2025 HKD 128K"""


def _full_pipeline(message: str) -> list[dict]:
    watches = [
        mark_explicit_condition_metadata(
            apply_inferred_pre_owned_default(
                normalize_watch_condition(enrich_parsed_watch(watch))
            )
        )
        for watch in parse_message(message)["watches"]
    ]
    return propagate_message_batch_condition(message, watches)


def _deal_cards(message: str) -> list[dict]:
    watches = _full_pipeline(message)
    rows = [
        _build_watch_row(
            watch,
            watch_created=False,
            offer_created=True,
            offer_id=f"offer-{index}",
            request_matches=[],
            price_intelligence=_build_price_intelligence(
                watch.get("usd_price"),
                [],
                is_duplicate=False,
                market_condition=watch.get("condition"),
            ),
        )
        for index, watch in enumerate(watches)
    ]
    return build_deal_analysis_cards(
        {
            "status": "success",
            "watches_parsed": len(watches),
            "parsed_watches": watches,
            "rows": rows,
            "new_offers": len(watches),
        }
    )


class TestCanonicalLineNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected_cleaned"),
        [
            ("⌚️ 4910/1200A Blue N7 HKD 121K", "4910/1200A Blue N7 HKD 121K"),
            ("⌚️📌 RM64-01 N7 USD 2.7M", "RM64-01 N7 USD 2.7M"),
            ("15210ST Green 🆕 2025 HKD 128K", "15210ST Green 🆕 2025 HKD 128K"),
            ("⌚️126500 Black 237K HKD N5", "126500 Black 237K HKD N5"),
        ],
    )
    def test_watch_emoji_variants_normalize_to_same_cleaned_line(
        self,
        raw: str,
        expected_cleaned: str,
    ) -> None:
        assert clean_dealer_list_line(raw) == expected_cleaned

    def test_expanded_lines_clean_to_same_offer_keys(self) -> None:
        raw = "⌚️ 4910/1200A Blue N7 HKD 121K"
        expanded = expand_dealer_list_raw_lines(raw)
        assert len(expanded) == 1
        assert clean_dealer_list_line(expanded[0]) == clean_dealer_list_line(raw)


class TestInlineConditionExtraction:
    @pytest.mark.parametrize(
        ("line", "expected_condition", "expected_card_date", "expected_raw"),
        [
            ("4910/1200A Blue N7 HKD 121K", NEW_CONDITION, "07/2026", "N7"),
            ("RM07-01 WG Snow Onyx N4-26 USDT 358K", NEW_CONDITION, "04/2026", "N4-26"),
            ("7042/100R-010 N8/25 HKD 1.505M", NEW_CONDITION, "08/2025", "N8/25"),
            ("1167R Used 2023 HKD 877K", PRE_OWNED_CONDITION, None, "Used"),
            ("1712R 2021 Used HKD 1.065M", PRE_OWNED_CONDITION, None, "Used"),
            ("126500 Black 237K HKD N5", NEW_CONDITION, "05/2026", "N5"),
            ("15210ST Green 2025 HKD 128K", None, None, None),
        ],
    )
    def test_targeted_offer_lines(
        self,
        line: str,
        expected_condition: str | None,
        expected_card_date: str | None,
        expected_raw: str | None,
    ) -> None:
        watch = mark_explicit_condition_metadata(
            normalize_watch_condition(parse_watch_line(line) or {})
        )
        assert watch.get("condition") == expected_condition
        assert watch.get("card_date") == expected_card_date
        if expected_raw:
            assert watch.get("raw_condition") == expected_raw

    def test_new_emoji_before_year(self) -> None:
        watch = parse_watch_line("15210ST Green 🆕 2025 HKD 128K")
        assert watch is not None
        assert watch["condition"] == "New"
        assert watch["production_year"] == 2025

    def test_new_emoji_after_year(self) -> None:
        watch = parse_watch_line("15210ST Green 2025 🆕 HKD 128K")
        assert watch is not None
        assert watch["condition"] == "New"
        assert watch["production_year"] == 2025


class TestSectionConditionInheritance:
    def test_used_rolex_banner_sets_section_condition(self) -> None:
        assert detect_section_condition_header("USED Rolex") == (PRE_OWNED_CONDITION, "Used")

    def test_no_box_banner_does_not_set_condition(self) -> None:
        assert detect_section_condition_header("ROLEX NO BOX") == (None, None)

    def test_used_rolex_section_inherits_pre_owned_for_following_rows(self) -> None:
        message = "⌚️ USED Rolex\n⌚️🛫 116505 Ivory Only Watch HKD 314K"
        watches = _full_pipeline(message)

        assert len(watches) == 1
        assert watches[0]["brand"] == "Rolex"
        assert watches[0]["condition"] == PRE_OWNED_CONDITION
        assert watches[0].get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION

    def test_stock_banner_does_not_infer_new(self) -> None:
        message = "⌚️ RM STOCK\n⌚️ RM64-01 N7 USD 2.7M\n⌚️ RM07-01 N4-26 USDT 358K"
        watches = _full_pipeline(message)

        assert len(watches) == 2
        assert all(watch["condition"] == NEW_CONDITION for watch in watches)
        assert all(watch.get("condition_source") == CONDITION_SOURCE_EXPLICIT for watch in watches)


class TestFullHkStockMessage:
    def test_all_offers_receive_known_conditions(self) -> None:
        watches = _full_pipeline(HK_STOCK_MESSAGE)

        assert len(watches) == 9
        assert all(watch.get("condition") in {NEW_CONDITION, PRE_OWNED_CONDITION} for watch in watches)

    def test_deal_analysis_cards_show_new_or_pre_owned_not_unknown(self) -> None:
        cards = _deal_cards(HK_STOCK_MESSAGE)

        assert len(cards) == 9
        assert all(card["condition_label"] in {NEW_CONDITION, PRE_OWNED_CONDITION} for card in cards)

    def test_new_notation_rows(self) -> None:
        watches = _full_pipeline(HK_STOCK_MESSAGE)
        by_ref = {watch.get("reference"): watch for watch in watches}

        assert by_ref["RM64-01"]["condition"] == NEW_CONDITION
        assert by_ref["RM07-01"]["raw_condition"] == "N4-26"
        assert by_ref["126500"]["condition"] == NEW_CONDITION
        assert by_ref["15210ST"]["condition"] == NEW_CONDITION
        assert by_ref["15210ST"]["production_year"] == 2025

    def test_used_rows(self) -> None:
        watches = _full_pipeline(HK_STOCK_MESSAGE)
        by_ref = {watch.get("reference"): watch for watch in watches}

        assert by_ref["1167R"]["condition"] == PRE_OWNED_CONDITION
        assert by_ref["1712R"]["condition"] == PRE_OWNED_CONDITION
        assert by_ref["116505"]["condition"] == PRE_OWNED_CONDITION

    def test_only_watch_row_under_used_rolex_is_pre_owned(self) -> None:
        watches = _full_pipeline(HK_STOCK_MESSAGE)
        by_ref = {watch.get("reference"): watch for watch in watches}

        assert by_ref["116505"]["condition"] == PRE_OWNED_CONDITION
        assert by_ref["116505"].get("condition_source") in {
            CONDITION_SOURCE_INHERITED_SECTION,
            CONDITION_SOURCE_EXPLICIT,
        }


class TestPpBrandNewStockList:
    def test_ready_stock_header_does_not_clear_explicit_line_conditions(self) -> None:
        from import_classification import split_offer_watches
        from parser_learning import prepare_watch_for_ingest

        parsed = parse_message(PP_BRAND_NEW_LIST)
        watches = [
            mark_explicit_condition_metadata(
                normalize_watch_condition(enrich_parsed_watch(watch))
            )
            for watch in parsed["watches"]
        ]
        watches = propagate_message_batch_condition(PP_BRAND_NEW_LIST, watches)
        for watch in watches:
            mark_explicit_condition_metadata(watch)
        offer_watches, _ = split_offer_watches(PP_BRAND_NEW_LIST, parsed, watches)

        for watch in offer_watches:
            prepare_watch_for_ingest(watch, message_text=PP_BRAND_NEW_LIST, rules=[])

        assert len(offer_watches) == 4
        assert all(watch["condition"] == NEW_CONDITION for watch in offer_watches)
        assert all(watch.get("condition_needs_training") is not True for watch in offer_watches)

    def test_deal_analysis_cards_show_new_after_ingest_prepare(self) -> None:
        from import_classification import split_offer_watches
        from parser_learning import prepare_watch_for_ingest

        parsed = parse_message(PP_BRAND_NEW_LIST)
        watches = [
            mark_explicit_condition_metadata(
                normalize_watch_condition(enrich_parsed_watch(watch))
            )
            for watch in parsed["watches"]
        ]
        watches = propagate_message_batch_condition(PP_BRAND_NEW_LIST, watches)
        for watch in watches:
            mark_explicit_condition_metadata(watch)
        offer_watches, _ = split_offer_watches(PP_BRAND_NEW_LIST, parsed, watches)
        for watch in offer_watches:
            prepare_watch_for_ingest(watch, message_text=PP_BRAND_NEW_LIST, rules=[])
        offer_watches = apply_inferred_pre_owned_defaults(offer_watches)
        rows = [
            _build_watch_row(
                watch,
                watch_created=False,
                offer_created=True,
                offer_id=f"offer-{index}",
                request_matches=[],
                price_intelligence=_build_price_intelligence(
                    watch.get("usd_price"),
                    [],
                    is_duplicate=False,
                    market_condition=watch.get("condition"),
                ),
            )
            for index, watch in enumerate(offer_watches)
        ]
        cards = build_deal_analysis_cards(
            {
                "status": "success",
                "watches_parsed": len(offer_watches),
                "parsed_watches": watches,
                "offer_watches": offer_watches,
                "rows": rows,
                "new_offers": len(offer_watches),
            }
        )

        assert len(cards) == 4
        assert all(card["condition_label"] == NEW_CONDITION for card in cards)
