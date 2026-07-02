"""Tests for Sprint 47.7 multi-line AP reference parsing."""

from __future__ import annotations

from condition_normalizer import NEW_CONDITION, normalize_watch_condition, resolve_offer_wear_condition
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message

AP_MULTI_LINE_MESSAGE = (
    "Audemars Piguet\n"
    "\n"
    "AP26239BC, fully T-diamond & pink stone, unworn 2021, 1pc worldwide limited, 985,000 USD\n"
    "\n"
    "AP26585CE black ceramic, full set 2022, 410,000 USD\n"
    "\n"
    "AP15412BA frosted gold rainbow diamond, new 2026, 425,000 USD"
)

ROLEX_RAINBOW_MESSAGE = (
    "Rolex 116595RBOW rainbow diamond full set 450,000 USD"
)


def _parse_full(message: str) -> list[dict]:
    watches = [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]
    return watches


def _watch_by_reference(watches: list[dict], reference: str) -> dict:
    for watch in watches:
        if watch.get("reference") == reference:
            return watch
    raise AssertionError(f"Missing watch with reference {reference}")


class TestApMultiLineParsing:
    def test_message_produces_three_offers(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        assert len(watches) == 3

    def test_each_offer_has_correct_reference(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        assert {watch.get("reference") for watch in watches} == {
            "26239BC",
            "26585CE",
            "15412BA",
        }

    def test_no_offer_gets_cosmograph_daytona_model(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        assert all(watch.get("model") != "Cosmograph Daytona" for watch in watches)

    def test_notes_are_not_merged_across_offers(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        notes_by_reference = {watch["reference"]: watch.get("notes") or "" for watch in watches}

        assert "AP26585CE" not in notes_by_reference["26239BC"]
        assert "AP15412BA" not in notes_by_reference["26239BC"]
        assert "AP26239BC" not in notes_by_reference["26585CE"]
        assert "AP26585CE" not in notes_by_reference["15412BA"]
        assert notes_by_reference["26239BC"]
        assert notes_by_reference["26585CE"]
        assert notes_by_reference["26239BC"] != notes_by_reference["26585CE"]

    def test_prices_are_extracted_per_line(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        prices = {watch["reference"]: watch["original_price"] for watch in watches}

        assert prices == {
            "26239BC": 985_000,
            "26585CE": 410_000,
            "15412BA": 425_000,
        }

    def test_conditions_per_offer(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        conditions = {
            watch["reference"]: resolve_offer_wear_condition(
                watch.get("condition"),
                watch.get("raw_condition"),
            )
            for watch in watches
        }

        assert conditions["26239BC"] == NEW_CONDITION
        assert conditions["15412BA"] == NEW_CONDITION
        assert conditions["26585CE"] is None

    def test_brand_header_applies_to_all_offers(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        assert all(watch.get("brand") == "Audemars Piguet" for watch in watches)


class TestBrandAwareNicknameInference:
    def test_rolex_rainbow_daytona_still_maps_to_rolex(self) -> None:
        watches = _parse_full(ROLEX_RAINBOW_MESSAGE)

        assert len(watches) == 1
        watch = watches[0]
        assert watch.get("brand") == "Rolex"
        assert watch.get("reference") == "116595RBOW"
        assert watch.get("model") in {None, "Cosmograph Daytona", "Daytona"}

    def test_ap_rainbow_note_does_not_force_rolex_daytona(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)
        rainbow_watch = _watch_by_reference(watches, "15412BA")

        assert rainbow_watch.get("brand") == "Audemars Piguet"
        assert rainbow_watch.get("model") != "Cosmograph Daytona"
        assert rainbow_watch.get("nickname") != "Rainbow"
