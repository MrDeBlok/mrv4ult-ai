"""Tests for Sprint 32 AI Watch Identification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from request_matching import match_offer_against_requests
from search import _token_matches_watch, search_offers
from watch_identifier import (
    apply_identification_to_watch,
    expand_search_token,
    identify_text,
    invalidate_identifier_cache,
    nicknames_for_reference,
    references_for_text,
)
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_watch_line


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_32_watch_identification.sql"
)


@pytest.fixture(autouse=True)
def _clear_identifier_cache() -> None:
    invalidate_identifier_cache()


class TestSprint32MigrationFile:
    def test_migration_creates_watch_identification_tables(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert "CREATE TABLE IF NOT EXISTS nickname_aliases" in sql
        assert "CREATE TABLE IF NOT EXISTS unknown_nicknames" in sql


class TestWatchIdentifierEngine:
    @pytest.mark.parametrize(
        ("text", "expected_brand", "expected_nickname"),
        [
            ("Pepsi", "Rolex", "Pepsi"),
            ("John Mayer", "Rolex", "John Mayer"),
            ("Batman", "Rolex", "Batman"),
            ("Bruce Wayne", "Rolex", "Batman"),
            ("Panda", "Rolex", "Panda"),
            ("Reverse Panda", "Rolex", "Reverse Panda"),
            ("Hulk", "Rolex", "Hulk"),
            ("Smurf", "Rolex", "Smurf"),
            ("Cookie Monster", "Rolex", "Cookie Monster"),
            ("Starbucks", "Rolex", "Starbucks"),
            ("Sprite", "Rolex", "Sprite"),
            ("Le Mans", "Rolex", "Le Mans"),
            ("Paul Newman", "Rolex", "Paul Newman"),
            ("Platona", "Rolex", "Platona"),
            ("Rainbow", "Rolex", "Rainbow"),
            ("Ghost", "Rolex", "Ghost"),
            ("Jumbo", "Audemars Piguet", "Jumbo"),
            ("Jumbo Extra Thin", "Audemars Piguet", "Jumbo Extra Thin"),
            ("Skeleton", "Audemars Piguet", "Skeleton"),
            ("Openworked", "Audemars Piguet", "Openworked"),
        ],
    )
    def test_identify_known_nicknames(
        self,
        text: str,
        expected_brand: str,
        expected_nickname: str,
    ) -> None:
        result = identify_text(text)

        assert result is not None
        assert result["brand"] == expected_brand
        assert result["nickname"] == expected_nickname
        assert result["confidence"] >= 0.75
        assert result["likely_references"]

    def test_identify_pepsi_returns_gmt_references(self) -> None:
        result = identify_text("Pepsi")

        assert "126710BLRO" in result["likely_references"]

    def test_identify_panda_returns_daytona_references(self) -> None:
        result = identify_text("Panda")

        assert "116500LN" in result["likely_references"]
        assert "126500LN" in result["likely_references"]

    def test_identify_partial_reference(self) -> None:
        result = identify_text("126710BLRO")

        assert result is not None
        assert result["match_type"] in {"partial_reference", "reference"}
        assert "126710BLRO" in result["likely_references"]

    def test_identify_partial_reference_prefix(self) -> None:
        result = identify_text("126710")

        assert result is not None
        assert any(reference.startswith("126710") for reference in result["likely_references"])

    def test_identify_includes_alternatives_for_ambiguous_entries(self) -> None:
        result = identify_text("Paul Newman")

        assert result is not None
        assert result["alternatives"]

    def test_references_for_text(self) -> None:
        assert "126710BLRO" in references_for_text("Pepsi")

    def test_nicknames_for_reference(self) -> None:
        nicknames = nicknames_for_reference("126710BLRO")

        assert "Pepsi" in nicknames


class TestWatchIdentifierIntegration:
    def test_apply_identification_to_watch_sets_metadata(self) -> None:
        watch = apply_identification_to_watch(
            {"source_line": "Pepsi 2023 full set 18000", "original_price": 18000}
        )

        assert watch["brand"] == "Rolex"
        assert watch["watch_identification"]["nickname"] == "Pepsi"
        assert "126710BLRO" in watch["watch_identification"]["likely_references"]

    def test_enrich_parsed_watch_applies_identification(self) -> None:
        watch = parse_watch_line("Pepsi 2023 full set 18000")
        assert watch is not None

        enriched = enrich_parsed_watch(watch)

        assert enriched["brand"] == "Rolex"
        assert enriched["watch_identification"]["matched_key"] == "pepsi"

    def test_enrich_parsed_watch_identifies_collector_name(self) -> None:
        watch = parse_watch_line("John Mayer 116508 green dial")
        assert watch is not None

        enriched = enrich_parsed_watch(watch)

        assert enriched["brand"] == "Rolex"
        assert enriched.get("reference") == "116508"


class TestSearchIdentification:
    def test_expand_search_token_includes_reference_for_nickname(self) -> None:
        terms = expand_search_token("Pepsi")

        assert "126710blro" in terms or "126710BLRO".lower() in terms

    def test_expand_search_token_includes_nickname_for_reference(self) -> None:
        terms = expand_search_token("126710BLRO")

        assert "pepsi" in terms

    def test_token_matches_watch_for_nickname_against_reference(self) -> None:
        watch = {"brand": "Rolex", "reference": "126710BLRO", "model": "GMT-Master II"}

        assert _token_matches_watch("Pepsi", watch) is True

    def test_token_matches_watch_for_reference_against_nickname_offer(self) -> None:
        watch = {"brand": "Rolex", "reference": "126710BLRO", "model": "GMT-Master II", "nickname": "Pepsi"}

        assert _token_matches_watch("126710BLRO", watch) is True

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_pepsi_finds_reference_offer(
        self,
        mock_get_client: MagicMock,
        mock_contact_type_supported: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_client = MagicMock()
        mock_execute = MagicMock()
        mock_execute.data = [
            {
                "watch_id": "w-pepsi",
                "original_price": 18000,
                "original_currency": "USD",
                "usd_price": 18000,
                "condition": "Used",
                "watches": {
                    "brand": "Rolex",
                    "reference": "126710BLRO",
                    "model": "GMT-Master II",
                    "dial": "Black",
                    "bracelet": "Jubilee",
                },
                "dealers": {"display_name": "Dealer A", "whatsapp_id": "+85290000001"},
            }
        ]
        mock_eq = MagicMock()
        mock_eq.execute.return_value = mock_execute
        mock_select = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_table = MagicMock()
        mock_table.select.return_value = mock_select
        mock_client.table.return_value = mock_table
        mock_get_client.return_value = mock_client

        offers, _ = search_offers("Pepsi")

        assert len(offers) == 1
        assert offers[0]["watch"]["reference"] == "126710BLRO"


class TestRequestIdentificationMatching:
    def _request(self, **kwargs) -> dict:
        base = {"id": "req-1", "status": "open", "client_name": "Client A"}
        base.update(kwargs)
        return base

    def test_panda_request_matches_116500ln(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116500LN",
            "original_price": 35000,
            "original_currency": "USD",
        }
        requests = [
            self._request(
                brand="Rolex",
                alias="Panda",
                max_price=40000,
                currency="USD",
            )
        ]

        matches = match_offer_against_requests(offer, requests)

        assert len(matches) == 1
        assert "116500LN" in matches[0]["match_reason"]

    def test_panda_request_matches_126500ln(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "126500LN",
            "original_price": 42000,
            "original_currency": "USD",
        }
        requests = [
            self._request(
                brand="Rolex",
                alias="Panda",
                max_price=45000,
                currency="USD",
            )
        ]

        matches = match_offer_against_requests(offer, requests)

        assert len(matches) == 1
        assert matches[0]["match_strength"] in {"strong", "medium"}


class TestUnknownNicknameIntelligence:
    def test_extract_unknown_nickname_text_skips_known_nickname(self) -> None:
        from unknown_nickname_intelligence import extract_unknown_nickname_text

        assert extract_unknown_nickname_text({"source_line": "Pepsi 18000", "original_price": 18000}) is None

    def test_extract_unknown_nickname_text_from_unrecognized_line(self) -> None:
        from unknown_nickname_intelligence import extract_unknown_nickname_text

        text = extract_unknown_nickname_text(
            {"source_line": "Thunderbolt blue dial 25000", "original_price": 25000}
        )

        assert text == "Thunderbolt"

    @patch("unknown_nickname_intelligence.identify_text", return_value=None)
    @patch("database.watch_identification_supported", return_value=True)
    @patch("database.record_unknown_nickname_sighting")
    def test_record_unknown_nicknames_for_watches(
        self,
        mock_record: MagicMock,
        _mock_supported: MagicMock,
        _mock_identify: MagicMock,
    ) -> None:
        from unknown_nickname_intelligence import record_unknown_nicknames_for_watches

        mock_record.return_value = {"id": "unk-1"}
        watches = [{"source_line": "Thunderbolt blue dial 25000", "original_price": 25000}]

        recorded = record_unknown_nicknames_for_watches(
            watches,
            example_message="Thunderbolt blue dial 25000",
            dealer_id="dealer-1",
        )

        assert len(recorded) == 1
        mock_record.assert_called_once()


class TestUnknownNicknamesPage:
    @patch("app.build_unknown_nickname_rows")
    @patch("app.list_pending_unknown_nicknames")
    def test_unknown_nicknames_page_renders(
        self,
        mock_list: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list.return_value = [
            {
                "id": "unk-1",
                "detected_text": "Thunderbolt",
                "example_message": "Thunderbolt blue dial",
                "occurrence_count": 2,
                "first_seen_at": "2026-06-27T12:00:00+00:00",
                "last_seen_at": "2026-06-27T12:00:00+00:00",
                "dealer_id": None,
                "status": "pending",
            }
        ]
        mock_build_rows.return_value = [
            {
                "id": "unk-1",
                "detected_text": "Thunderbolt",
                "example_message": "Thunderbolt blue dial",
                "occurrence_count": 2,
                "first_seen": "2026-06-27 12:00",
                "last_seen": "2026-06-27 12:00",
                "dealer_name": "—",
                "status": "pending",
            }
        ]

        client = TestClient(app)
        response = client.get("/knowledge/unknown-nicknames")

        assert response.status_code == 200
        assert "Unknown Nicknames" in response.text
        assert "Thunderbolt" in response.text
        assert "Map nickname" in response.text

    @patch("app.resolve_unknown_nickname_with_alias")
    @patch("app.invalidate_identifier_cache")
    def test_unknown_nickname_map_action(
        self,
        mock_invalidate: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post(
            "/knowledge/unknown-nicknames/unk-1/map",
            data={
                "brand_name": "Rolex",
                "collection": "GMT-Master II",
                "model_name": "GMT-Master II",
                "nickname": "Thunderbolt",
                "likely_references": "126710BLRO",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/knowledge/unknown-nicknames?saved=1"
        mock_resolve.assert_called_once()
        mock_invalidate.assert_called_once()
