"""Sprint 45.1 — Brand context beats embedded reference matches."""

from __future__ import annotations

import pytest

from brand_knowledge import extract_reference_from_brand_knowledge, is_embedded_in_compound_reference_token
from watch_parser import parse_message, parse_watch_line


class TestHublotReferenceExtraction:
    @pytest.mark.parametrize(
        "reference",
        [
            "451.EX.5123.EX",
            "642.NX.0170.RX.1104",
            "411.JX.1170.RX",
            "525.HI.0170.RW.ORL21",
        ],
    )
    def test_hublot_dotted_references(self, reference: str) -> None:
        line = f"{reference} 21Y 94300hkd"
        watch = parse_watch_line(line, current_brand="Hublot")

        assert watch is not None
        assert watch["brand"] == "Hublot"
        assert watch["reference"] == reference
        assert watch["reference_high_confidence"] is True

    def test_hublot_example_from_bug_report(self) -> None:
        watch = parse_watch_line("451.EX.5123.EX 21Y 94300hkd", current_brand="Hublot")

        assert watch is not None
        assert watch["brand"] == "Hublot"
        assert watch["reference"] == "451.EX.5123.EX"
        assert watch["reference"] != "5123"
        assert watch["original_price"] == 94_300
        assert watch["original_currency"] == "HKD"


class TestEmbeddedSubstringSuppression:
    def test_5123_inside_hublot_reference_is_not_patek(self) -> None:
        watch = parse_watch_line("451.EX.5123.EX 21Y 94300hkd", current_brand="Hublot")

        assert watch is not None
        assert watch["brand"] == "Hublot"
        assert watch["reference"] == "451.EX.5123.EX"

    def test_5711_inside_compound_reference_is_ignored_without_brand_context(self) -> None:
        watch = parse_watch_line("442.EX.5711.EX 120k")

        assert watch is not None
        assert watch["reference"] is None
        assert watch["brand"] is None

    def test_compound_token_helper_detects_embedding(self) -> None:
        text = "451.EX.5123.EX"
        assert is_embedded_in_compound_reference_token(text, text.index("5123"), text.index("5123") + 4)


class TestPatekStandaloneStillWorks:
    @pytest.mark.parametrize(
        "line",
        [
            "5123 120k",
            "PP 5123 full set 180k",
            "Patek Philippe 5123 120k",
        ],
    )
    def test_patek_5123_without_wrong_brand_context(self, line: str) -> None:
        watch = parse_watch_line(line)

        assert watch is not None
        assert watch["brand"] == "Patek Philippe"
        assert watch["reference"] == "5123"

    def test_patek_5123_with_patek_brand_context(self) -> None:
        watch = parse_watch_line("5123 full set 180k", current_brand="Patek Philippe")

        assert watch is not None
        assert watch["brand"] == "Patek Philippe"
        assert watch["reference"] == "5123"


class TestMultiLineHublotDealerList:
    DEALER_LIST = """Used Hublot
451.EX.5123.EX 21Y 94300hkd
642.NX.0170.RX.1104 55000hkd"""

    def test_only_hublot_offers(self) -> None:
        result = parse_message(self.DEALER_LIST)

        assert result["message_type"] == "offer_list"
        assert len(result["watches"]) >= 2
        assert all(watch["brand"] == "Hublot" for watch in result["watches"])
        assert "Patek Philippe" not in {watch["brand"] for watch in result["watches"]}

    def test_full_hublot_references_parsed(self) -> None:
        result = parse_message(self.DEALER_LIST)
        references = {watch["reference"] for watch in result["watches"] if watch["reference"]}

        assert "451.EX.5123.EX" in references
        assert "642.NX.0170.RX.1104" in references
        assert "5123" not in references


class TestBrandKnowledgeHublot:
    def test_brand_knowledge_extracts_full_hublot_reference(self) -> None:
        match = extract_reference_from_brand_knowledge(
            "451.EX.5123.EX 21Y 94300hkd",
            brand_hint="Hublot",
        )

        assert match is not None
        assert match[0] == "451.EX.5123.EX"
        assert match[1] == "Hublot"
