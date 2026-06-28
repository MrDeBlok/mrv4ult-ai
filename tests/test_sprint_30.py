"""Tests for Sprint 30 Watch Knowledge Engine 2.0."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from brand_registry import (
    get_brand_aliases,
    get_brand_pattern,
    invalidate_brand_registry_cache,
    list_canonical_brands,
    lookup_brand,
    normalize_brand_alias,
)
from model_aliases import invalidate_alias_cache
from unknown_brand_intelligence import (
    extract_unknown_brand_text,
    normalize_unknown_brand_text,
    record_unknown_brands_for_watches,
    watch_has_parse_signal,
)
from watch_parser import parse_watch_line


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_30_watch_knowledge.sql"
)


class TestSprint30MigrationFile:
    def test_migration_creates_watch_knowledge_tables(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert "CREATE TABLE IF NOT EXISTS brand_aliases" in sql
        assert "CREATE TABLE IF NOT EXISTS unknown_brands" in sql


class TestBrandRegistryExpansion:
    @pytest.mark.parametrize(
        ("line", "expected_brand"),
        [
            ("Greubel Forsey GMT 850k usd", "Greubel Forsey"),
            ("GF Tourbillon 24 Seconds 850k", "Greubel Forsey"),
            ("Moser Streamliner 420k chf", "H. Moser & Cie."),
            ("JLC Master Ultra Thin 45k", "Jaeger-LeCoultre"),
            ("FP Journe Chronomètre Bleu 220k chf", "F.P. Journe"),
            ("Cartier Santos 8500 usd", "Cartier"),
            ("Omega Speedmaster 6500", "Omega"),
            ("Tudor Black Bay 4100", "Tudor"),
        ],
    )
    def test_expanded_brand_recognition(self, line: str, expected_brand: str) -> None:
        watch = parse_watch_line(line)

        assert watch is not None
        assert watch["brand"] == expected_brand

    def test_gf_alias_maps_to_greubel_forsey(self) -> None:
        assert lookup_brand("GF") == "Greubel Forsey"
        assert lookup_brand("gf") == "Greubel Forsey"

    def test_moser_alias_maps_to_h_moser(self) -> None:
        assert lookup_brand("moser") == "H. Moser & Cie."

    def test_legacy_brand_aliases_remain_available(self) -> None:
        aliases = get_brand_aliases()

        assert aliases["pp"] == "Patek Philippe"
        assert aliases["ap"] == "Audemars Piguet"
        assert aliases["als"] == "A. Lange & Söhne"

    def test_canonical_brand_list_includes_required_names(self) -> None:
        brands = list_canonical_brands()

        for brand in (
            "Greubel Forsey",
            "H. Moser & Cie.",
            "F.P. Journe",
            "Jaeger-LeCoultre",
            "Urwerk",
            "Jacob & Co.",
        ):
            assert brand in brands

    def test_brand_pattern_matches_multi_word_aliases(self) -> None:
        pattern = get_brand_pattern()

        assert pattern.search("Greubel Forsey 850k")
        assert pattern.search("fp journe cb 220k")


class TestUnknownBrandIntelligence:
    def test_extract_unknown_brand_text_from_unbranded_offer(self) -> None:
        watch = {
            "brand": None,
            "reference": None,
            "original_price": 850000,
            "source_line": "MysteryMaker 1234 steel 850k usd",
        }

        detected = extract_unknown_brand_text(watch)

        assert detected == "MysteryMaker"

    def test_extract_unknown_brand_text_skips_when_brand_present(self) -> None:
        watch = {
            "brand": "Rolex",
            "source_line": "Rolex 126500LN 305k",
            "original_price": 305000,
        }

        assert extract_unknown_brand_text(watch) is None

    def test_watch_has_parse_signal_requires_content(self) -> None:
        assert watch_has_parse_signal({"reference": "116508"}) is True
        assert watch_has_parse_signal({}) is False

    @patch("database.record_unknown_brand_sighting")
    @patch("database.watch_knowledge_supported", return_value=True)
    def test_record_unknown_brands_for_watches_persists_sightings(
        self,
        mock_supported: MagicMock,
        mock_record: MagicMock,
    ) -> None:
        mock_record.return_value = {"id": "unknown-1"}
        watches = [
            {
                "brand": None,
                "original_price": 850000,
                "source_line": "MysteryMaker 850k",
            }
        ]

        recorded = record_unknown_brands_for_watches(
            watches,
            example_message="MysteryMaker 850k",
            dealer_id="dealer-1",
        )

        assert len(recorded) == 1
        mock_record.assert_called_once()


class TestDatabaseBrandAliases:
    @patch("database.list_active_brand_aliases")
    def test_database_aliases_merge_into_registry(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            {"alias_key": "mystery", "brand_name": "Mystery Maker", "status": "active"}
        ]
        invalidate_brand_registry_cache()

        assert lookup_brand("mystery") == "Mystery Maker"

    @patch("database.get_client")
    @patch("database.watch_knowledge_supported", return_value=True)
    def test_create_brand_alias_inserts_active_row(
        self,
        mock_supported: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_table = MagicMock()
        mock_get_client.return_value.table.return_value = mock_table
        mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[]
        )
        mock_table.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "alias-1", "alias_key": "mystery", "brand_name": "Mystery Maker"}]
        )

        from database import create_brand_alias

        row = create_brand_alias(alias_key="Mystery", brand_name="Mystery Maker")

        assert row["brand_name"] == "Mystery Maker"
        mock_table.insert.assert_called_once()


class TestAutoLearningReparse:
    @patch("brand_registry._load_database_brand_aliases")
    def test_reparse_recognizes_newly_added_alias(self, mock_db_aliases: MagicMock) -> None:
        mock_db_aliases.return_value = {}
        invalidate_brand_registry_cache()
        assert parse_watch_line("Mystery 850k usd")["brand"] is None

        mock_db_aliases.return_value = {
            normalize_brand_alias("mystery"): "Mystery Maker",
        }
        invalidate_brand_registry_cache()
        invalidate_alias_cache()

        watch = parse_watch_line("Mystery 850k usd")

        assert watch is not None
        assert watch["brand"] == "Mystery Maker"


class TestUnknownBrandsPage:
    @patch("app.get_dealer_by_id", return_value=None)
    @patch("app.build_unknown_brand_rows")
    @patch("app.list_pending_unknown_brands")
    @patch("app.watch_knowledge_supported", return_value=True)
    def test_unknown_brands_page_renders_rows(
        self,
        mock_supported: MagicMock,
        mock_list: MagicMock,
        mock_build_rows: MagicMock,
        mock_get_dealer: MagicMock,
    ) -> None:
        mock_list.return_value = [{"id": "unknown-1", "dealer_id": "dealer-1"}]
        mock_build_rows.return_value = [
            {
                "id": "unknown-1",
                "detected_text": "MysteryMaker",
                "example_message": "MysteryMaker 850k",
                "occurrence_count": 2,
                "first_seen": "2026-06-01 10:00",
                "last_seen": "2026-06-27 12:00",
                "dealer_name": "Gold Source",
                "status": "pending",
            }
        ]

        response = TestClient(app).get("/knowledge/unknown-brands")

        assert response.status_code == 200
        assert "Unknown Brands" in response.text
        assert "MysteryMaker" in response.text
        assert "Add as alias" in response.text
        assert "Mark as ignored" in response.text

    @patch("app.invalidate_brand_registry_cache")
    @patch("app.resolve_unknown_brand_with_alias")
    def test_add_alias_action_resolves_unknown_brand(
        self,
        mock_resolve: MagicMock,
        mock_invalidate: MagicMock,
    ) -> None:
        response = TestClient(app).post(
            "/knowledge/unknown-brands/unknown-1/add-alias",
            data={"brand_name": "Mystery Maker"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        mock_resolve.assert_called_once()
        mock_invalidate.assert_called_once()


class TestModelAliasDatabaseLoading:
    @patch("database.list_active_brand_aliases")
    @patch("database.watch_knowledge_supported", return_value=True)
    def test_model_alias_index_includes_database_brand_aliases(
        self,
        mock_supported: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        from model_aliases import find_alias_match

        mock_list.return_value = [
            {"alias_key": "mystery", "brand_name": "Mystery Maker", "status": "active"}
        ]
        invalidate_alias_cache()
        match = find_alias_match("mystery dial 850k")

        assert match is not None
        assert match[1]["brand"] == "Mystery Maker"
