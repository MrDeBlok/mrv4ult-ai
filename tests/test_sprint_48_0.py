"""Tests for Sprint 48.0 inferred Pre-Owned default condition."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import _build_deal_analysis, build_deal_analysis_cards, build_watch_offer_cards
from condition_normalizer import (
    CONDITION_CONFIDENCE_MEDIUM,
    CONDITION_INFERENCE_NOTE,
    CONDITION_SOURCE_EXPLICIT,
    CONDITION_SOURCE_INFERRED_DEFAULT,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    apply_inferred_pre_owned_default,
    apply_inferred_pre_owned_defaults,
    mark_explicit_condition_metadata,
    normalize_wear_condition,
    resolve_effective_watch_condition,
    resolve_offer_wear_condition,
)
from deal_market_lookup import resolve_deal_market_context
from import_classification import split_offer_watches
from ingest import ingest_message
from parser_review import detect_watch_issues
from watch_parser import parse_message


GMT_MESSAGE = (
    "WTS: Rolex GMT Master II 126710BLRO Pepsi 2021 Watch + Box 19.777,-€ + label"
)


class TestConditionInferenceRules:
    def test_offer_with_no_condition_and_price_infers_pre_owned(self) -> None:
        watch = apply_inferred_pre_owned_default(
            {
                "brand": "Rolex",
                "reference": "126710BLRO",
                "usd_price": 21_000,
            }
        )

        assert watch["condition"] == PRE_OWNED_CONDITION
        assert watch["condition_source"] == CONDITION_SOURCE_INFERRED_DEFAULT
        assert watch["condition_confidence"] == CONDITION_CONFIDENCE_MEDIUM
        assert watch["condition_explicit"] is False

    @pytest.mark.parametrize(
        "raw",
        ["new", "brand new", "unworn", "nos", "sticker", "stickered", "full stickers"],
    )
    def test_explicit_new_overrides_inferred_default(self, raw: str) -> None:
        normalized, _ = normalize_wear_condition(raw)
        watch = mark_explicit_condition_metadata({"condition": normalized, "raw_condition": raw, "usd_price": 10_000})
        watch = apply_inferred_pre_owned_default(watch)

        assert watch["condition"] == NEW_CONDITION
        assert watch["condition_source"] == CONDITION_SOURCE_EXPLICIT

    @pytest.mark.parametrize("raw", ["used", "pre-owned", "pre owned", "worn", "serviced", "polished"])
    def test_explicit_pre_owned_remains_explicit(self, raw: str) -> None:
        normalized, raw_condition = normalize_wear_condition(raw)
        watch = mark_explicit_condition_metadata(
            {"condition": normalized, "raw_condition": raw_condition or raw, "usd_price": 10_000}
        )

        assert watch["condition"] == PRE_OWNED_CONDITION
        assert watch["condition_source"] == CONDITION_SOURCE_EXPLICIT

    def test_wtb_message_without_condition_does_not_infer(self) -> None:
        parsed = parse_message("WTB Rolex Daytona 116500LN budget 30k")
        watches = apply_inferred_pre_owned_defaults(parsed["watches"])
        offer_watches, classification = split_offer_watches(
            "WTB Rolex Daytona 116500LN budget 30k",
            parsed,
            watches,
        )

        assert classification == "request_intent"
        assert offer_watches == []

    def test_sold_order_message_without_condition_does_not_infer(self) -> None:
        parsed = parse_message("Sold order for client need Rolex 126331 full set")
        watches = apply_inferred_pre_owned_defaults(parsed["watches"])
        offer_watches, classification = split_offer_watches(
            "Sold order for client need Rolex 126331 full set",
            parsed,
            watches,
        )

        assert classification == "request_intent"
        assert offer_watches == []


class TestDealAnalysisWithInferredCondition:
    @patch("deal_market_lookup._load_active_offer_pool")
    @patch("deal_market_lookup.get_offers_by_ids")
    def test_deal_analysis_uses_pre_owned_comparables_for_inferred_condition(
        self,
        mock_get_offers: MagicMock,
        mock_pool: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {"offer-1": {"watch_id": "watch-1"}}
        mock_pool.return_value = [("offer-2", 22_000, PRE_OWNED_CONDITION)]

        row = {
            "brand": "Rolex",
            "reference": "126710BLRO",
            "usd_price": 21_000,
            "offer_id": "offer-1",
            "condition_source": CONDITION_SOURCE_INFERRED_DEFAULT,
            "condition_confidence": CONDITION_CONFIDENCE_MEDIUM,
            "condition_explicit": False,
        }
        watch = {
            "brand": "Rolex",
            "reference": "126710BLRO",
            "usd_price": 21_000,
            "condition_source": CONDITION_SOURCE_INFERRED_DEFAULT,
        }

        context = resolve_deal_market_context(row, watch)
        assert context.offer_condition == PRE_OWNED_CONDITION
        assert context.comparison_safe is True
        assert context.market_usd == 22_000

    @patch("deal_market_lookup._load_active_offer_pool")
    @patch("deal_market_lookup.get_offers_by_ids")
    def test_inferred_pre_owned_never_compares_against_new(
        self,
        mock_get_offers: MagicMock,
        mock_pool: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {"offer-1": {"watch_id": "watch-1"}}
        mock_pool.return_value = [("offer-2", 25_000, NEW_CONDITION)]

        context = resolve_deal_market_context(
            {
                "brand": "Rolex",
                "reference": "126710BLRO",
                "usd_price": 21_000,
                "offer_id": "offer-1",
            },
            {
                "brand": "Rolex",
                "reference": "126710BLRO",
                "usd_price": 21_000,
            },
        )

        assert context.offer_condition == PRE_OWNED_CONDITION
        assert context.comparison_safe is False
        assert context.insufficient_market_data is True

    def test_deal_analysis_card_shows_inferred_note(self) -> None:
        analysis = _build_deal_analysis(
            {
                "brand": "Rolex",
                "reference": "126710BLRO",
                "usd_price": 21_000,
                "previous_lowest_usd": "$22,000",
                "price_label": "Good price",
                "rank": "2",
                "market_condition": PRE_OWNED_CONDITION,
                "condition_source": CONDITION_SOURCE_INFERRED_DEFAULT,
                "condition_confidence": CONDITION_CONFIDENCE_MEDIUM,
                "condition_explicit": False,
            },
            {
                "brand": "Rolex",
                "reference": "126710BLRO",
                "usd_price": 21_000,
                "confidence": 90,
            },
            0,
        )

        assert analysis["condition_label"] == PRE_OWNED_CONDITION
        assert analysis["condition_is_inferred"] is True
        assert analysis["condition_inference_note"] == CONDITION_INFERENCE_NOTE
        assert analysis["recommendation"] == "Good Buy"

    def test_inferred_condition_caps_excellent_buy(self) -> None:
        analysis = _build_deal_analysis(
            {
                "brand": "Rolex",
                "reference": "126710BLRO",
                "usd_price": 19_000,
                "previous_lowest_usd": "$22,000",
                "price_label": "New lowest price",
                "rank": "1",
                "market_condition": PRE_OWNED_CONDITION,
                "condition_source": CONDITION_SOURCE_INFERRED_DEFAULT,
                "condition_confidence": CONDITION_CONFIDENCE_MEDIUM,
                "condition_explicit": False,
            },
            {
                "brand": "Rolex",
                "reference": "126710BLRO",
                "usd_price": 19_000,
                "confidence": 95,
            },
            0,
        )

        assert analysis["recommendation"] == "Good Buy"


class TestPresentationAndWorkbench:
    def test_watch_offer_card_shows_pre_owned_inferred_label(self) -> None:
        cards = build_watch_offer_cards(
            {
                "rows": [
                    {
                        "brand": "Rolex",
                        "reference": "126710BLRO",
                        "usd_price": 21_000,
                        "condition": PRE_OWNED_CONDITION,
                        "condition_source": CONDITION_SOURCE_INFERRED_DEFAULT,
                    }
                ],
                "parsed_watches": [
                    {
                        "brand": "Rolex",
                        "reference": "126710BLRO",
                        "usd_price": 21_000,
                        "condition_source": CONDITION_SOURCE_INFERRED_DEFAULT,
                    }
                ],
            }
        )

        condition_field = next(field for field in cards[0]["fields"] if field["label"] == "Condition")
        assert condition_field["value"] == "Pre-Owned (inferred)"

    def test_parser_review_does_not_flag_inferred_condition_as_missing(self) -> None:
        watch = apply_inferred_pre_owned_default(
            {"brand": "Rolex", "reference": "126710BLRO", "usd_price": 21_000}
        )
        issues, missing = detect_watch_issues(watch)

        assert "missing_condition" not in issues
        assert "condition" not in missing

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[("other", 22_000, PRE_OWNED_CONDITION)])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", "dealer"))
    def test_ingest_stores_inferred_condition_metadata_on_summary_row(
        self,
        _mock_dealer: MagicMock,
        _mock_group: MagicMock,
        _mock_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        _mock_import_log: MagicMock,
        _mock_active: MagicMock,
        _mock_matches: MagicMock,
        _mock_notifications: MagicMock,
        _mock_brands: MagicMock,
        _mock_nicknames: MagicMock,
    ) -> None:
        mock_find_watch.return_value = ({"id": "watch-1"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)

        summary = ingest_message(
            GMT_MESSAGE,
            group_name="EU Dealers",
            dealer_whatsapp="+31612345678",
        )

        row = summary["rows"][0]
        assert row["condition"] == PRE_OWNED_CONDITION
        assert row["condition_source"] == CONDITION_SOURCE_INFERRED_DEFAULT
        assert row["condition_confidence"] == CONDITION_CONFIDENCE_MEDIUM
        assert row["condition_explicit"] is False

    def test_gmt_example_resolves_effective_pre_owned_condition(self) -> None:
        parsed = parse_message(GMT_MESSAGE)["watches"][0]
        effective = resolve_effective_watch_condition(
            {"usd_price": parsed.get("usd_price") or 21_777},
            parsed,
        )

        assert effective["condition"] == PRE_OWNED_CONDITION
        assert effective["condition_source"] == CONDITION_SOURCE_INFERRED_DEFAULT
        assert resolve_offer_wear_condition(effective.get("condition")) == PRE_OWNED_CONDITION
