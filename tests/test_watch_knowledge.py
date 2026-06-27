"""Unit tests for the watch reference knowledge layer."""

from __future__ import annotations

from app import build_watch_offer_cards
from watch_knowledge import (
    enrich_parsed_watch,
    knowledge_display_fields,
    lookup_reference,
    normalize_reference,
)


class TestReferenceNormalization:
    def test_normalizes_case_and_spacing(self) -> None:
        assert normalize_reference(" 126713grnr ") == "126713GRNR"


class TestReferenceLookup:
    def test_returns_gmt_zombie_knowledge(self) -> None:
        knowledge = lookup_reference("126713GRNR")

        assert knowledge is not None
        assert knowledge["brand"] == "Rolex"
        assert knowledge["collection"] == "GMT-Master II"
        assert knowledge["nickname"] == "Zombie"
        assert knowledge["metal"] == "Steel / Yellow Gold"
        assert knowledge["bezel"] == "Black & Grey Cerachrom"
        assert knowledge["bracelet"] == "Jubilee"
        assert knowledge["case_size"] == "40 mm"
        assert knowledge["movement"] == "3285"
        assert knowledge["production_status"] == "Current Production"
        assert knowledge["launch_year"] == 2024

    def test_returns_none_for_unknown_reference(self) -> None:
        assert lookup_reference("999999XX") is None


class TestParsedWatchEnrichment:
    def test_enriches_known_reference(self) -> None:
        watch = enrich_parsed_watch(
            {
                "brand": "Rolex",
                "reference": "126713GRNR",
                "nickname": "zombie",
                "usd_price": 20412,
            }
        )

        assert "knowledge" in watch
        assert watch["knowledge"]["collection"] == "GMT-Master II"
        assert watch["nickname"] == "zombie"

    def test_leaves_unknown_reference_untouched(self) -> None:
        watch = enrich_parsed_watch({"brand": "Rolex", "reference": "999999XX"})

        assert "knowledge" not in watch


class TestKnowledgeDisplay:
    def test_builds_labeled_fields(self) -> None:
        knowledge = lookup_reference("126713GRNR")
        assert knowledge is not None

        labels = [field["label"] for field in knowledge_display_fields(knowledge)]
        assert labels == [
            "Brand",
            "Collection",
            "Model",
            "Nickname",
            "Metal",
            "Bezel",
            "Dial color",
            "Bracelet",
            "Case",
            "Movement",
            "Status",
            "Launch",
        ]


class TestWatchCardKnowledge:
    def test_watch_card_includes_knowledge_fields(self) -> None:
        cards = build_watch_offer_cards(
            {
                "parsed_watches": [
                    enrich_parsed_watch(
                        {
                            "brand": "Rolex",
                            "reference": "126713GRNR",
                            "nickname": "zombie",
                        }
                    )
                ],
                "rows": [{"reference": "126713GRNR", "brand": "Rolex"}],
            }
        )

        assert len(cards) == 1
        assert cards[0]["knowledge_fields"]
        assert cards[0]["knowledge_fields"][0] == {"label": "Brand", "value": "Rolex"}
        assert any(field["label"] == "Collection" for field in cards[0]["knowledge_fields"])

    def test_watch_card_omits_knowledge_for_unknown_reference(self) -> None:
        cards = build_watch_offer_cards(
            {
                "parsed_watches": [{"brand": "Rolex", "reference": "999999XX"}],
                "rows": [{"reference": "999999XX", "brand": "Rolex"}],
            }
        )

        assert cards[0]["knowledge_fields"] == []
