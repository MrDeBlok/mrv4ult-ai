"""Regression tests for multi-brand dealer list section condition inheritance."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import build_deal_analysis_cards
from condition_normalizer import (
    CONDITION_SOURCE_EXPLICIT,
    CONDITION_SOURCE_INHERITED_SECTION,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    apply_inferred_pre_owned_defaults,
    detect_section_condition_header,
    mark_explicit_condition_metadata,
    normalize_watch_condition,
    propagate_message_batch_condition,
)
from ingest import _build_price_intelligence, _build_watch_row
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message

DEALER_LIST_MESSAGE = """Sell new 🔥🔥🔥
RM
⌚ RM07-01 White ceramic 2026-6 HKD:4,280,000
PP
⌚ 5723/1R-010 blue dial 2024 HKD:3,850,000
AP
⌚ 15500ST blue dial 2026 HKD:180,000
Rolex
⌚ 126200 green jubilee 2026 HKD:74,000

----------------

Used
PP
⌚ Good condition 2020 Fullset 6103R green HKD:500,000
AP
⌚ Like new 2021 full set 15510ST blue HKD:200,000
Rolex
⌚ Used 2020 submariner 126610LN HKD:80,000
RM
⌚ Good condition watch only RM11-03 titanium 2017 HKD:1,000,000
⌚ Brand new 2025 full set RM53-01 HKD:2,000,000
"""


def _parse_full_pipeline(message: str) -> list[dict]:
    watches = [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]
    watches = propagate_message_batch_condition(message, watches)
    watches = apply_inferred_pre_owned_defaults(watches)
    return [mark_explicit_condition_metadata(watch) for watch in watches]


class TestSectionHeaderDetection:
    def test_sell_new_with_emoji_is_recognized(self) -> None:
        condition, raw = detect_section_condition_header("Sell new 🔥🔥🔥")
        assert condition == NEW_CONDITION
        assert raw == "Sell new"

    def test_used_header_is_recognized(self) -> None:
        condition, raw = detect_section_condition_header("Used")
        assert condition == PRE_OWNED_CONDITION
        assert raw == "Used"

    @pytest.mark.parametrize(
        "line",
        ["RM", "PP", "AP", "Rolex", "F.P.Journe"],
    )
    def test_brand_headers_are_not_condition_headers(self, line: str) -> None:
        assert detect_section_condition_header(line) == (None, None)

    def test_fpj_brand_header_does_not_clear_section_condition(self) -> None:
        watches = _parse_full_pipeline(
            "Sell new\nF.P.Journe\nPP\n⌚ 5723/1R-010 2024 HKD:3,850,000"
        )
        assert len(watches) == 1
        assert watches[0]["condition"] == NEW_CONDITION
        assert watches[0].get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION

    def test_product_row_with_brand_new_is_not_header(self) -> None:
        assert detect_section_condition_header(
            "Brand new 2025 full set RM53-01 HKD:2,000,000"
        ) == (None, None)


class TestDealerListSectionConditionInheritance:
    def test_new_section_survives_brand_headers(self) -> None:
        watches = _parse_full_pipeline(DEALER_LIST_MESSAGE)
        new_rows = watches[:4]

        assert len(new_rows) == 4
        assert all(watch["condition"] == NEW_CONDITION for watch in new_rows)
        assert all(
            watch.get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION
            for watch in new_rows
        )
        brands = {watch.get("brand") for watch in new_rows}
        assert brands == {
            "Richard Mille",
            "Patek Philippe",
            "Audemars Piguet",
            "Rolex",
        }

    def test_blank_lines_and_dividers_do_not_clear_section_condition(self) -> None:
        watches = _parse_full_pipeline(
            "Sell new\nRM\n⌚ RM07-01 White ceramic 2026-6 HKD:4,280,000\n\n----------------\nPP\n⌚ 5723/1R-010 2024 HKD:3,850,000"
        )
        assert len(watches) == 2
        assert all(watch["condition"] == NEW_CONDITION for watch in watches)

    def test_used_section_survives_brand_headers(self) -> None:
        watches = _parse_full_pipeline(DEALER_LIST_MESSAGE)
        used_rows = watches[4:]

        assert len(used_rows) == 5
        assert used_rows[0]["reference"] == "6103R"
        assert used_rows[0]["condition"] == PRE_OWNED_CONDITION
        assert used_rows[0].get("condition_source") == CONDITION_SOURCE_EXPLICIT
        assert used_rows[1]["condition"] == PRE_OWNED_CONDITION
        assert used_rows[1].get("condition_source") == CONDITION_SOURCE_EXPLICIT
        assert used_rows[2]["condition"] == PRE_OWNED_CONDITION
        assert used_rows[2].get("condition_source") == CONDITION_SOURCE_EXPLICIT
        assert used_rows[3]["condition"] == PRE_OWNED_CONDITION
        assert used_rows[3].get("condition_source") == CONDITION_SOURCE_EXPLICIT
        assert used_rows[3].get("watch_only") is True
        assert used_rows[4]["condition"] == NEW_CONDITION
        assert used_rows[4].get("condition_source") == CONDITION_SOURCE_EXPLICIT

    def test_date_only_row_inherits_section_condition(self) -> None:
        watches = _parse_full_pipeline(DEALER_LIST_MESSAGE)
        rm_row = watches[0]

        assert rm_row.get("brand") == "Richard Mille"
        assert rm_row["condition"] == NEW_CONDITION
        assert rm_row.get("condition_source") == CONDITION_SOURCE_INHERITED_SECTION
        assert rm_row.get("production_year") == 2026

    def test_explicit_used_overrides_new_section(self) -> None:
        watches = _parse_full_pipeline(
            "Sell new 🔥🔥🔥\nRolex\n⌚ Used 2020 submariner 126610LN HKD:80,000"
        )
        assert len(watches) == 1
        assert watches[0]["condition"] == PRE_OWNED_CONDITION
        assert watches[0].get("condition_source") == CONDITION_SOURCE_EXPLICIT

    def test_brand_new_overrides_used_section(self) -> None:
        watches = _parse_full_pipeline(
            "Used\nRM\n⌚ Brand new 2025 full set RM53-01 HKD:2,000,000"
        )
        assert len(watches) == 1
        assert watches[0]["condition"] == NEW_CONDITION
        assert watches[0].get("condition_source") == CONDITION_SOURCE_EXPLICIT

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("Good condition 2020 Fullset 6103R HKD:500,000", PRE_OWNED_CONDITION),
            ("Like new 2021 full set 15510ST HKD:200,000", PRE_OWNED_CONDITION),
            ("Brand new 2025 full set RM53-01 HKD:2,000,000", NEW_CONDITION),
        ],
    )
    def test_inline_condition_phrases(self, phrase: str, expected: str) -> None:
        watches = _parse_full_pipeline(f"Used\nPP\n⌚ {phrase}")
        assert len(watches) == 1
        assert watches[0]["condition"] == expected
        assert watches[0].get("condition_source") == CONDITION_SOURCE_EXPLICIT

    def test_watch_only_does_not_set_condition_without_section(self) -> None:
        watches = _parse_full_pipeline("PP\n⌚ watch only 6103R HKD:500,000")
        assert len(watches) == 1
        assert watches[0].get("watch_only") is True
        assert watches[0].get("condition") in {None, PRE_OWNED_CONDITION}

    @patch("app.load_trading_desk")
    def test_deal_analysis_does_not_show_unknown_for_section_rows(
        self,
        mock_load_trading_desk: MagicMock,
    ) -> None:
        mock_load_trading_desk.return_value = MagicMock()
        watches = _parse_full_pipeline(DEALER_LIST_MESSAGE)
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
            for index, watch in enumerate(watches[:3])
        ]
        analyses = build_deal_analysis_cards(
            {"rows": rows, "parsed_watches": watches[:3], "offer_watches": watches[:3]},
            include_debug=True,
        )
        assert all(analysis["condition_label"] in {"New", "Pre-Owned"} for analysis in analyses)
        assert all(analysis["debug"]["normalized_condition"] != "Unknown" for analysis in analyses)
