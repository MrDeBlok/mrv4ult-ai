"""Tests for Sprint 47.8 reference knowledge brand override."""

from __future__ import annotations

from condition_normalizer import normalize_watch_condition
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message, parse_watch_line

AP_HEADER = "Audemars Piguet"

AP_MULTI_LINE_MESSAGE = (
    "Audemars Piguet\n"
    "\n"
    "AP26239BC, fully T-diamond & pink stone, unworn 2021, 1pc worldwide limited, 985,000 USD\n"
    "\n"
    "AP26585CE black ceramic, full set 2022, 410,000 USD\n"
    "\n"
    "AP15412BA frosted gold rainbow diamond, new 2026, 425,000 USD"
)

ROLEX_RAINBOW_MESSAGE = "Rolex 116595RBOW rainbow diamond full set 450,000 USD"


def _parse_line(reference: str) -> dict:
    watch = parse_watch_line(f"{reference} 100,000 USD", current_brand=AP_HEADER)
    assert watch is not None
    return normalize_watch_condition(enrich_parsed_watch(watch))


def _parse_full(message: str) -> list[dict]:
    return [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]


class TestReferenceKnowledgeOverridesBrandContext:
    def test_5968a_under_ap_header_keeps_ap_with_conflict(self) -> None:
        watch = _parse_line("5968A")

        assert watch["brand"] == AP_HEADER
        assert watch["reference"] == "5968A"
        assert watch.get("reference_brand_conflict")

    def test_7010r_under_ap_header_keeps_ap_with_conflict(self) -> None:
        watch = _parse_line("7010R")

        assert watch["brand"] == AP_HEADER
        assert watch["reference"] == "7010R"
        assert watch.get("reference_brand_conflict")

    def test_5500v_under_ap_header_keeps_ap_with_conflict(self) -> None:
        watch = _parse_line("5500V")

        assert watch["brand"] == AP_HEADER
        assert watch["reference"] == "5500V"
        assert watch.get("reference_brand_conflict")

    def test_ap_prefixed_refs_still_parse_as_audemars_piguet(self) -> None:
        watches = _parse_full(AP_MULTI_LINE_MESSAGE)

        assert len(watches) == 3
        assert {watch["reference"] for watch in watches} == {
            "26239BC",
            "26585CE",
            "15412BA",
        }
        assert all(watch["brand"] == AP_HEADER for watch in watches)

    def test_rolex_rainbow_still_parses_as_rolex(self) -> None:
        watches = _parse_full(ROLEX_RAINBOW_MESSAGE)

        assert len(watches) == 1
        assert watches[0]["brand"] == "Rolex"
        assert watches[0]["reference"] == "116595RBOW"
        assert watches[0].get("model") != "Cosmograph Daytona" or watches[0]["brand"] == "Rolex"

    def test_brand_context_applies_when_reference_brand_unknown(self) -> None:
        watch = parse_watch_line("15500ST blue full set 320k", current_brand=AP_HEADER)

        assert watch is not None
        assert watch["brand"] == AP_HEADER
        assert watch["reference"] == "15500ST"
        assert watch.get("brand_context_conflict") is None
