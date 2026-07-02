"""Tests for Sprint 47.2 batch-level condition propagation."""

from __future__ import annotations

from app import build_deal_analysis_cards
from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    detect_message_batch_condition,
    propagate_message_batch_condition,
    resolve_offer_wear_condition,
    sync_summary_row_conditions,
)
from ingest import _comparable_usd_prices, _build_watch_row, _build_price_intelligence
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message
from condition_normalizer import normalize_watch_condition


def _parse_and_propagate(message: str) -> list[dict]:
    watches = [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]
    return propagate_message_batch_condition(message, watches)


class TestBatchConditionDetection:
    def test_detects_all_new_from_message(self) -> None:
        normalized, raw = detect_message_batch_condition(
            "Audemars Piguet\nAll new\n5164G 180000 USD\n5167A 185000 USD"
        )
        assert normalized == NEW_CONDITION
        assert raw == "All new"

    def test_detects_unworn_from_standalone_line(self) -> None:
        normalized, raw = detect_message_batch_condition("Unworn\n5164G 180000 USD\n5167A 185000 USD")
        assert normalized == NEW_CONDITION
        assert raw == "Unworn"

    def test_detects_pre_owned_from_message(self) -> None:
        normalized, raw = detect_message_batch_condition(
            "Pre-owned\n5164G 180000 USD\n5167A 185000 USD"
        )
        assert normalized == PRE_OWNED_CONDITION
        assert raw == "Pre-owned"

    def test_unknown_message_level_condition_stays_unknown(self) -> None:
        assert detect_message_batch_condition(
            "5164G 180000 USD\n5167A 185000 USD"
        ) == (None, None)


class TestBatchConditionPropagation:
    def test_multi_watch_all_new_applies_new_to_every_watch(self) -> None:
        message = (
            "Audemars Piguet\n"
            "All new\n"
            "5164G 180000 USD\n"
            "5167A 185000 USD\n"
            "5224R 200000 USD"
        )
        watches = _parse_and_propagate(message)

        assert len(watches) >= 3
        assert all(watch.get("condition") == NEW_CONDITION for watch in watches)

    def test_multi_watch_unworn_applies_new_to_every_watch(self) -> None:
        message = "Unworn\n5164G 180000 USD\n5167A 185000 USD"
        watches = _parse_and_propagate(message)

        assert len(watches) >= 2
        assert all(watch.get("condition") == NEW_CONDITION for watch in watches)

    def test_multi_watch_pre_owned_applies_pre_owned_to_every_watch(self) -> None:
        message = "Pre-owned\n5164G 180000 USD\n5167A 185000 USD"
        watches = _parse_and_propagate(message)

        assert len(watches) >= 2
        assert all(watch.get("condition") == PRE_OWNED_CONDITION for watch in watches)

    def test_row_specific_condition_overrides_message_level(self) -> None:
        message = (
            "All new\n"
            "Rolex 126610LN used 12500 USD\n"
            "Rolex 126334 13000 USD"
        )
        watches = _parse_and_propagate(message)

        assert len(watches) >= 2
        assert resolve_offer_wear_condition(watches[0].get("condition"), watches[0].get("raw_condition")) == PRE_OWNED_CONDITION
        assert watches[1].get("condition") == NEW_CONDITION

    def test_unknown_message_level_leaves_watches_without_condition(self) -> None:
        message = "5164G 180000 USD\n5167A 185000 USD"
        watches = _parse_and_propagate(message)

        assert watches
        assert all(
            resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition")) is None
            for watch in watches
        )


class TestDealAnalysisConditionDisplay:
    def _analysis_for_message(self, message: str) -> list[dict]:
        watches = _parse_and_propagate(message)
        rows = [
            _build_watch_row(
                watch,
                watch_created=False,
                offer_created=True,
                offer_id=f"offer-{index}",
                request_matches=[],
                price_intelligence=_build_price_intelligence(
                    watch.get("usd_price"),
                    [watch.get("usd_price", 0) + 5_000],
                    is_duplicate=False,
                    market_condition=NEW_CONDITION,
                ),
            )
            for index, watch in enumerate(watches)
        ]
        return build_deal_analysis_cards(
            {
                "rows": rows,
                "parsed_watches": watches,
                "offer_watches": watches,
            }
        )

    def test_deal_analysis_cards_show_propagated_condition(self) -> None:
        analyses = self._analysis_for_message(
            "Audemars Piguet\nAll new\n5164G 180000 USD\n5167A 185000 USD"
        )

        assert analyses
        assert all(analysis["condition_label"] == "New" for analysis in analyses)

    def test_deal_analysis_still_uses_same_condition_comparables_only(self) -> None:
        comparables, market_condition = _comparable_usd_prices(
            [("new", 13_500, NEW_CONDITION), ("used", 10_800, PRE_OWNED_CONDITION)],
            exclude_offer_ids=set(),
            offer_condition=NEW_CONDITION,
        )
        assert comparables == [13_500]
        assert market_condition == NEW_CONDITION


class TestReprocessRowSync:
    def test_sync_summary_row_conditions_copies_batch_condition(self) -> None:
        watches = [
            {"brand": "Audemars Piguet", "reference": "5164G", "condition": NEW_CONDITION},
            {"brand": "Audemars Piguet", "reference": "5167A", "condition": NEW_CONDITION},
        ]
        rows = [
            {"brand": "Audemars Piguet", "reference": "5164G", "condition": None},
            {"brand": "Audemars Piguet", "reference": "5167A", "condition": None},
        ]

        synced = sync_summary_row_conditions(rows, watches)

        assert synced[0]["condition"] == NEW_CONDITION
        assert synced[1]["condition"] == NEW_CONDITION
