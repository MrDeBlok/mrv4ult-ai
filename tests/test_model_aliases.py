"""Unit tests for the model alias enrichment engine."""

from __future__ import annotations

from app import build_watch_offer_cards
from ingest import _import_status, _watch_missing_fields
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_watch_line


def _enrich_line(line: str) -> dict:
    watch = parse_watch_line(line)
    assert watch is not None
    return enrich_parsed_watch(watch)


class TestModelAliasEnrichment:
    def test_als_odysseus_blue_dial(self) -> None:
        watch = _enrich_line("ALS Odysseus blue dial €40000")

        assert watch["brand"] == "A. Lange & Söhne"
        assert watch["model"] == "Odysseus"
        assert watch["dial"] == "Blue"
        assert watch["original_price"] == 40000
        assert watch["reference"] is None
        assert watch["model_alias"]["collection"] == "Odysseus"
        assert watch["model_alias"]["reference_status"] == "Unknown"
        assert "Odysseus" in watch["model_alias"]["confidence_note"]

    def test_pepsi_full_set_without_forced_reference(self) -> None:
        watch = _enrich_line("Pepsi 2023 full set 18000")

        assert watch["brand"] == "Rolex"
        assert watch["model"] == "GMT-Master II"
        assert watch["nickname"] == "Pepsi"
        assert watch["reference"] is None
        assert watch["model_alias"]["reference_status"] == "Unknown"
        assert watch["condition"] is None
        assert watch["full_set"] is True
        assert watch["notes"].startswith("full set")
        assert watch["production_year"] == 2023

    def test_john_mayer_keeps_explicit_reference(self) -> None:
        watch = _enrich_line("John Mayer 116508 green dial")

        assert watch["brand"] == "Rolex"
        assert watch["model"] == "Cosmograph Daytona"
        assert watch["nickname"] == "John Mayer"
        assert watch["reference"] == "116508"
        assert watch["model_alias"]["possible_reference"] == "116508"
        assert watch["dial"] == "Green"

    def test_cubitus_infers_patek_model(self) -> None:
        watch = _enrich_line("Cubitus blue 2025")

        assert watch["brand"] == "Patek Philippe"
        assert watch["model"] == "Cubitus"
        assert watch["dial"] == "Blue"
        assert watch["production_year"] == 2025
        assert watch["model_alias"]["collection"] == "Cubitus"
        assert watch["model_alias"]["reference_status"] == "Unknown"


class TestNeedsReviewStatus:
    def test_missing_reference_stays_needs_review(self) -> None:
        watch = _enrich_line("ALS Odysseus blue dial €40000")
        summary = {
            "watches_parsed": 1,
            "duplicate_offers": 0,
        }

        status, _reason = _import_status(summary, "success", [watch])

        assert status == "warning"
        assert "reference" in _watch_missing_fields(watch)


class TestWatchCardAliasDisplay:
    def test_watch_card_shows_model_alias_fields(self) -> None:
        watch = _enrich_line("ALS Odysseus blue dial €40000")
        cards = build_watch_offer_cards(
            {
                "parsed_watches": [watch],
                "rows": [
                    {
                        "brand": watch["brand"],
                        "reference": "Unknown",
                        "usd_price": watch["usd_price"],
                        "price_label": "No comparables",
                    }
                ],
            }
        )

        assert cards[0]["alias_fields"]
        labels = [field["label"] for field in cards[0]["alias_fields"]]
        assert "Collection" in labels
        assert "Model" in labels
        assert "Confidence note" in labels
        assert any(field["value"] == "Unknown" for field in cards[0]["fields"] if field["label"] == "Reference")
