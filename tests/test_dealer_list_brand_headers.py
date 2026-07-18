"""Regression tests for dealer-list brand header precedence and section context."""

from __future__ import annotations

from condition_normalizer import normalize_watch_condition
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message, parse_watch_line

PAtek_5067_MESSAGE = """🦚Patek Philippe USED in HK🦚

5067A 2011 white full set HKD375k
5067A 2015 white full set HKD410k
5067A 2016 white full set HKD429k
5067A 2016 Tiffany white full set HKD570k"""

AP_SECTION_MESSAGE = """Audemars Piguet
15500ST blue dial 2026 HKD:180,000
26239BC fully set 985k usd"""

ROLEX_SECTION_MESSAGE = """Rolex
126200 green jubilee 2026 HKD:74,000
126500LN white dial 305k usd"""

VC_SECTION_MESSAGE = """Vacheron Constantin
5500V blue dial 2024 HKD:280,000
4500V steel 2023 HKD:320,000"""

MIXED_DEALER_MESSAGE = """Sell new 🔥🔥🔥
PP
5723/1R-010 2024 HKD:3,850,000
AP
15500ST blue 2026 HKD:180,000

Used
Rolex
126610LN Used 2020 HKD:80,000
PP
6103R Good condition 2020 Fullset HKD:500,000"""


def _parse_full(message: str) -> list[dict]:
    return [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]


class TestPatekPhilippe5067Section:
    def test_patek_section_header_assigns_brand_context(self) -> None:
        watches = _parse_full(PAtek_5067_MESSAGE)

        assert len(watches) == 4
        assert all(watch["brand"] == "Patek Philippe" for watch in watches)
        assert all(watch["reference"] == "5067A" for watch in watches)
        assert all(watch.get("brand_source") == "inherited" for watch in watches)

    def test_tiffany_variant_keeps_patek_brand(self) -> None:
        watches = _parse_full(PAtek_5067_MESSAGE)

        tiffany_row = watches[-1]
        assert tiffany_row["brand"] == "Patek Philippe"
        assert tiffany_row["reference"] == "5067A"
        assert "Tiffany" in (tiffany_row.get("source_line") or "")


class TestSectionBrandSurvival:
    def test_ap_section_assigns_ap_to_ap_references(self) -> None:
        watches = _parse_full(AP_SECTION_MESSAGE)

        assert len(watches) == 2
        assert all(watch["brand"] == "Audemars Piguet" for watch in watches)

    def test_rolex_section_assigns_rolex(self) -> None:
        watches = _parse_full(ROLEX_SECTION_MESSAGE)

        assert len(watches) == 2
        assert all(watch["brand"] == "Rolex" for watch in watches)

    def test_vc_section_assigns_vacheron_constantin(self) -> None:
        watches = _parse_full(VC_SECTION_MESSAGE)

        assert len(watches) == 2
        assert all(watch["brand"] == "Vacheron Constantin" for watch in watches)

    def test_mixed_message_preserves_brand_until_next_header(self) -> None:
        watches = _parse_full(MIXED_DEALER_MESSAGE)

        assert watches[0]["brand"] == "Patek Philippe"
        assert watches[1]["brand"] == "Audemars Piguet"
        assert watches[2]["brand"] == "Rolex"
        assert watches[3]["brand"] == "Patek Philippe"

    def test_brand_header_with_used_phrase_is_not_lost(self) -> None:
        watches = _parse_full("Patek Philippe USED in HK\n5067A 2011 white full set HKD375k")

        assert len(watches) == 1
        assert watches[0]["brand"] == "Patek Philippe"
        assert watches[0]["reference"] == "5067A"


class TestBrandPrecedence:
    def test_heuristic_does_not_override_inherited_patek_for_5067a(self) -> None:
        watch = parse_watch_line("5067A 2011 white full set HKD375k", current_brand="Patek Philippe")
        assert watch is not None
        assert watch["brand"] == "Patek Philippe"
        assert watch.get("brand_source") == "inherited"

    def test_authoritative_mapping_conflicts_with_section_without_replacing_brand(self) -> None:
        watch = parse_watch_line("5968A 100,000 USD", current_brand="Audemars Piguet")
        assert watch is not None
        assert watch["brand"] == "Audemars Piguet"
        assert watch.get("brand_source") == "inherited"
        assert watch.get("reference_brand_conflict")
