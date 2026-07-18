"""Tests for Vacheron Constantin reference-brand resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from brand_resolver import (
    BRAND_SOURCE_REFERENCE,
    apply_reference_brand_safety,
    infer_brand_from_reference_heuristic,
    resolve_watch_brand,
)
from reference_knowledge import (
    REFERENCE_BRAND_METRICS,
    find_suspicious_vacheron_ap_mappings,
    import_reference_knowledge_dataset,
    invalidate_authoritative_reference_cache,
    is_vacheron_overseas_reference,
    resolve_authoritative_reference_brand,
)
from watch_knowledge import enrich_parsed_watch, invalidate_reference_brand_mapping_cache, resolve_reference_brand_identity
from watch_parser import parse_watch_line

VACHERON = "Vacheron Constantin"
AUDEMARS = "Audemars Piguet"


@pytest.fixture(autouse=True)
def _reset_reference_caches() -> None:
    invalidate_reference_brand_mapping_cache()
    invalidate_authoritative_reference_cache()
    REFERENCE_BRAND_METRICS.exact_mapping_hits = 0
    REFERENCE_BRAND_METRICS.family_pattern_hits = 0
    REFERENCE_BRAND_METRICS.reference_brand_conflicts = 0
    REFERENCE_BRAND_METRICS.vacheron_classified_as_ap = 0


def _enrich_line(line: str, *, brand: str | None = None) -> dict:
    watch = parse_watch_line(line, current_brand=brand)
    assert watch is not None
    return enrich_parsed_watch(watch)


class TestVacheronReferenceFamilies:
    @pytest.mark.parametrize(
        "reference",
        ["4300V", "4520V", "5500V", "7900V", "7920V"],
    )
    def test_vacheron_overseas_family_detection(self, reference: str) -> None:
        assert is_vacheron_overseas_reference(reference) is True

    @pytest.mark.parametrize(
        ("line", "expected_reference"),
        [
            ("Vacheron Constantin 4300V 1.5m usd", "4300V"),
            ("VC 4520V 1.5m usd", "4520V"),
            ("5500V full set 420k hkd", "5500V"),
            ("7900V blue dial 350k", "7900V"),
            ("7920V 410k usd", "7920V"),
        ],
    )
    def test_known_vacheron_references_resolve_to_vacheron(
        self,
        line: str,
        expected_reference: str,
    ) -> None:
        watch = _enrich_line(line)

        assert watch["reference"] == expected_reference
        assert watch["brand"] == VACHERON
        assert watch.get("brand_source") in {BRAND_SOURCE_REFERENCE, "explicit"}

    def test_legitimate_ap_reference_still_resolves_to_ap(self) -> None:
        watch = _enrich_line("15500ST blue full set 320k", brand=AUDEMARS)

        assert watch["reference"] == "15500ST"
        assert watch["brand"] == AUDEMARS

    def test_unknown_numeric_reference_without_evidence_needs_review(self) -> None:
        watch = apply_reference_brand_safety(
            {
                "reference": "UNKNOWN99",
                "brand": None,
                "source_line": "UNKNOWN99 32000 USD",
            }
        )

        assert watch["brand"] is None
        assert watch["reference_needs_review"] is True


class TestVacheronConflictResolution:
    def test_exact_vacheron_mapping_keeps_ap_header_with_conflict(self) -> None:
        watch = _enrich_line("4300V 100,000 USD", brand=AUDEMARS)

        assert watch["reference"] == "4300V"
        assert watch["brand"] == AUDEMARS
        assert watch.get("reference_brand_conflict")

    def test_explicit_vacheron_text_beats_ap_generic_pattern(self) -> None:
        watch = _enrich_line("Vacheron Constantin 7920V 410k usd", brand=AUDEMARS)

        assert watch["brand"] == VACHERON
        assert watch["reference"] == "7920V"

    def test_trusted_mapping_keeps_inherited_ap_with_conflict(self) -> None:
        resolution = resolve_watch_brand(
            reference="4520V",
            text="4520V 420k",
            identification_brand=AUDEMARS,
            inherited_brand=AUDEMARS,
            brand_before_normalization=AUDEMARS,
        )

        assert resolution.brand == AUDEMARS
        assert resolution.source == "inherited"

    def test_apply_reference_brand_safety_keeps_inherited_ap_with_conflict(self) -> None:
        watch = apply_reference_brand_safety(
            {
                "reference": "7900V",
                "brand": AUDEMARS,
                "brand_source": "inherited",
            }
        )

        assert watch["brand"] == AUDEMARS
        assert watch.get("reference_brand_conflict")
        assert watch.get("reference_needs_review") is not True

    def test_infer_heuristic_never_returns_ap_for_vacheron_v_suffix(self) -> None:
        for reference in ("4300V", "4520V", "7900V", "7920V"):
            assert infer_brand_from_reference_heuristic(reference) == VACHERON


class TestReferenceKnowledgeImport:
    def test_authoritative_dataset_resolves_vacheron_references(self) -> None:
        brand, confident = resolve_authoritative_reference_brand("4520V")
        assert brand == VACHERON
        assert confident is True

    def test_import_dataset_dry_run_reports_mappings(self) -> None:
        from reference_knowledge import REFERENCE_KNOWLEDGE_DIR

        report = import_reference_knowledge_dataset(
            REFERENCE_KNOWLEDGE_DIR / "vacheron_constantin_overseas.json",
            dry_run=True,
        )

        assert report["dry_run"] is True
        assert report["imported"] == 9
        assert len(report["proposed_mappings"]) == 9

    def test_find_suspicious_vacheron_ap_mappings(self) -> None:
        suspects = find_suspicious_vacheron_ap_mappings(
            [
                {"reference": "4300V", "brand": AUDEMARS},
                {"reference": "15500ST", "brand": AUDEMARS},
            ]
        )

        assert len(suspects) == 1
        assert suspects[0]["reference"] == "4300V"
        assert suspects[0]["proposed_brand"] == VACHERON


class TestReferenceBrandDatabaseHelpers:
    @patch("database.reference_brand_mappings_supported", return_value=True)
    @patch("database.get_client")
    def test_upsert_reference_brand_mapping_dry_run_insert(
        self,
        mock_get_client,
        _mock_supported,
    ) -> None:
        from database import upsert_reference_brand_mapping

        mock_execute = type("Execute", (), {"data": []})()
        mock_query = type(
            "Query",
            (),
            {
                "select": lambda *args, **kwargs: mock_query,
                "eq": lambda *args, **kwargs: mock_query,
                "limit": lambda *args, **kwargs: mock_query,
                "execute": lambda *args, **kwargs: mock_execute,
            },
        )()
        mock_get_client.return_value.table.return_value = mock_query

        result = upsert_reference_brand_mapping(
            reference="4300V",
            brand_name=VACHERON,
            source="test",
            dry_run=True,
        )

        assert result["action"] == "would_insert"
        assert result["reference_key"] == "4300V"

    @patch("database.reference_brand_mappings_supported", return_value=True)
    @patch("database.get_client")
    def test_upsert_detects_conflicting_brand(
        self,
        mock_get_client,
        _mock_supported,
    ) -> None:
        from database import upsert_reference_brand_mapping

        mock_execute = type(
            "Execute",
            (),
            {"data": [{"id": "1", "brand_name": AUDEMARS, "status": "active", "source": "manual"}]},
        )()
        mock_query = type(
            "Query",
            (),
            {
                "select": lambda *args, **kwargs: mock_query,
                "eq": lambda *args, **kwargs: mock_query,
                "limit": lambda *args, **kwargs: mock_query,
                "execute": lambda *args, **kwargs: mock_execute,
            },
        )()
        mock_get_client.return_value.table.return_value = mock_query

        result = upsert_reference_brand_mapping(
            reference="4300V",
            brand_name=VACHERON,
            source="authority_vacheron_overseas",
            source_confidence="high",
            dry_run=True,
        )

        assert result["conflict"] is not None
        assert result["conflict"]["existing_brand"] == AUDEMARS
        assert result["conflict"]["proposed_brand"] == VACHERON

    def test_resolve_reference_brand_identity_uses_authoritative_mapping(self) -> None:
        brand, confident = resolve_reference_brand_identity("7920V")
        assert brand == VACHERON
        assert confident is True
