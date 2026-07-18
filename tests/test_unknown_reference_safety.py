"""Tests for unknown reference brand safety and workbench mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from brand_resolver import (
    apply_reference_brand_safety,
    reference_confidently_belongs_to_brand,
    reference_confidently_conflicts_with_brand,
)
from condition_normalizer import normalize_watch_condition
from ingest import ingest_message, is_dealer_list_bulk_import
from parser_review import (
    detect_primary_failure_reason,
    detect_watch_failure_reasons,
    is_parser_review_pending,
)
from parser_workbench import apply_workbench_fix, determine_primary_fix_action
from watch_knowledge import enrich_parsed_watch, invalidate_reference_brand_mapping_cache, resolve_reference_brand_identity
from watch_parser import parse_message, parse_watch_line

AP_HEADER = "Audemars Piguet"
PATEK_HEADER = "Patek Philippe"
LANGE_HEADER = "A. Lange & Söhne"


def _enrich_line(line: str, *, brand: str | None = None) -> dict:
    watch = parse_watch_line(line, current_brand=brand)
    assert watch is not None
    return normalize_watch_condition(enrich_parsed_watch(watch))


class TestReferenceBrandSafety:
    def test_rolex_reference_under_ap_header_keeps_inherited_brand_with_conflict(self) -> None:
        watch = _enrich_line("126334 blue jubilee new 14500 eur", brand=AP_HEADER)

        assert watch["reference"] == "126334"
        assert watch["brand"] == AP_HEADER
        assert watch.get("reference_brand_conflict")
        assert not watch.get("reference_needs_review")

    def test_confident_ap_reference_under_ap_header_keeps_brand(self) -> None:
        watch = _enrich_line("15500ST blue full set 320k", brand=AP_HEADER)

        assert watch["reference"] == "15500ST"
        assert watch["brand"] == AP_HEADER
        assert not watch.get("reference_needs_review")

    def test_known_cross_brand_reference_keeps_section_brand_with_conflict(self) -> None:
        watch = _enrich_line("5968A blue dial 120k", brand=AP_HEADER)

        assert watch["reference"] == "5968A"
        assert watch["brand"] == AP_HEADER
        assert watch.get("reference_brand_conflict")
        assert watch.get("reference_needs_review") is not True

    def test_reference_confidently_belongs_uses_saved_mapping(self) -> None:
        with patch(
            "watch_knowledge._load_reference_brand_mapping_index",
            return_value={"126334": "Rolex"},
        ):
            invalidate_reference_brand_mapping_cache()
            brand, confident = resolve_reference_brand_identity("126334")

        assert confident is True
        assert brand == "Rolex"
        assert reference_confidently_belongs_to_brand("126334", "Rolex") is True
        assert reference_confidently_belongs_to_brand("126334", AP_HEADER) is False

    def test_apply_reference_brand_safety_keeps_inherited_brand_with_conflict(self) -> None:
        watch = apply_reference_brand_safety(
            {
                "reference": "126334",
                "brand": AP_HEADER,
                "brand_source": "inherited",
                "reference_brand_conflict": {
                    "inherited_brand": AP_HEADER,
                    "inferred_reference_brand": "Rolex",
                },
            }
        )

        assert watch["brand"] == AP_HEADER
        assert watch["reference"] == "126334"
        assert watch.get("reference_brand_conflict")
        assert watch.get("reference_needs_review") is not True

    def test_unknown_ap_reference_under_ap_header_is_allowed(self) -> None:
        watch = _enrich_line("3661B blue dial 32000 usd", brand=AP_HEADER)

        assert watch["reference"] == "3661B"
        assert watch["brand"] == AP_HEADER
        assert not watch.get("reference_needs_review")

    def test_unknown_patek_reference_under_patek_header_is_allowed(self) -> None:
        watch = _enrich_line("6020 blue dial 120k", brand=PATEK_HEADER)

        assert watch["reference"] == "6020"
        assert watch["brand"] == PATEK_HEADER
        assert not watch.get("reference_needs_review")

    def test_no_brand_unknown_reference_still_needs_review(self) -> None:
        watch = apply_reference_brand_safety(
            {
                "reference": "UNKNOWN99",
                "brand": None,
                "source_line": "UNKNOWN99 32000 USD",
            }
        )

        assert watch["reference"] == "UNKNOWN99"
        assert watch["brand"] is None
        assert watch["reference_needs_review"] is True

    def test_reference_confidently_conflicts_detects_rolex_under_ap(self) -> None:
        assert reference_confidently_conflicts_with_brand("126334", AP_HEADER) is True
        assert reference_confidently_conflicts_with_brand("101.021", LANGE_HEADER) is False

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.insert_import_log", return_value={"id": "log-1"})
    @patch("ingest.insert_offer", return_value=({"id": "offer-1"}, True))
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", "dealer"))
    def test_unknown_reference_under_ap_header_creates_offer(
        self,
        _mock_dealer: MagicMock,
        _mock_group: MagicMock,
        mock_insert_offer: MagicMock,
        _mock_insert_import_log: MagicMock,
        _mock_insert_message: MagicMock,
        _mock_find_watch: MagicMock,
        _mock_active: MagicMock,
        _mock_matches: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unknown: MagicMock,
        _mock_nicknames: MagicMock,
    ) -> None:
        message = f"{AP_HEADER}\n3661B blue dial 32000 usd"
        summary = ingest_message(message, group_name="HK", dealer_whatsapp="+85291234567")

        assert summary["status"] == "warning"
        assert summary["parsed_watches"][0]["brand"] == AP_HEADER
        assert summary["parsed_watches"][0]["reference"] == "3661B"
        assert summary["new_offers"] == 1
        assert "condition" in (summary.get("parser_quality") or {}).get("failed_fields", [])
        mock_insert_offer.assert_called_once()

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.insert_import_log", return_value={"id": "log-1"})
    @patch("ingest.insert_offer", return_value=({"id": "offer-1"}, True))
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", "dealer"))
    def test_lange_dealer_list_bulk_import_stays_success(
        self,
        _mock_dealer: MagicMock,
        _mock_group: MagicMock,
        _mock_insert_offer: MagicMock,
        _mock_insert_import_log: MagicMock,
        _mock_insert_message: MagicMock,
        _mock_find_watch: MagicMock,
        _mock_active: MagicMock,
        _mock_matches: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unknown: MagicMock,
        _mock_nicknames: MagicMock,
    ) -> None:
        from tests.test_sprint_40_0 import LANGE_DEALER_LIST

        assert is_dealer_list_bulk_import(LANGE_DEALER_LIST) is True
        summary = ingest_message(LANGE_DEALER_LIST, group_name="HK", dealer_whatsapp="+85291234567")

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 9
        assert summary["new_offers"] == 9


class TestUnknownReferenceWorkbench:
    def test_cross_brand_reference_conflict_is_detected(self) -> None:
        watch = _enrich_line("126334 blue jubilee new 14500 eur", brand=AP_HEADER)
        import_log = {
            "id": "log-1",
            "status": "success",
            "watches_parsed": 1,
            "summary": {
                "status": "success",
                "watches_parsed": 1,
                "parsed_watches": [watch],
                "rows": [watch],
            },
        }

        assert watch.get("reference_brand_conflict")
        assert "unknown_reference" not in detect_watch_failure_reasons(watch)
        assert detect_primary_failure_reason(import_log) != "unknown_reference"

    @patch("parser_workbench.reprocess_import_log")
    @patch("database.reference_brand_mappings_supported", return_value=True)
    @patch("database.watch_knowledge_supported", return_value=True)
    @patch("database.create_reference_brand_mapping")
    @patch("database.get_import_log")
    def test_workbench_unknown_reference_saves_mapping_and_reprocesses(
        self,
        mock_get_import_log: MagicMock,
        mock_create_mapping: MagicMock,
        _mock_watch_knowledge: MagicMock,
        _mock_mappings_supported: MagicMock,
        mock_reprocess: MagicMock,
    ) -> None:
        watch = _enrich_line("126334 blue jubilee new 14500 eur", brand=AP_HEADER)
        mock_get_import_log.return_value = {
            "id": "log-1",
            "summary": {"parsed_watches": [watch], "rows": [watch]},
        }
        mock_reprocess.return_value = {"id": "log-1", "status": "success"}

        apply_workbench_fix(
            "log-1",
            "unknown_reference",
            brand_name="Rolex",
            reference="126334",
        )

        mock_create_mapping.assert_called_once_with(
            reference="126334",
            brand_name="Rolex",
            source="parser_workbench",
        )
        mock_reprocess.assert_called_once_with(
            "log-1",
            field_overrides={"brand": "Rolex", "reference": "126334"},
        )

    @patch("watch_knowledge._load_reference_brand_mapping_index", return_value={"126334": "Rolex"})
    def test_saved_mapping_does_not_override_inherited_section_brand(self, _mock_index: MagicMock) -> None:
        invalidate_reference_brand_mapping_cache()
        watch = _enrich_line("126334 blue jubilee new 14500 eur", brand=AP_HEADER)

        assert watch["reference"] == "126334"
        assert watch["brand"] == AP_HEADER
        assert watch.get("reference_brand_conflict")
        assert not watch.get("reference_needs_review")

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.insert_import_log", return_value={"id": "log-1"})
    @patch("ingest.insert_offer", return_value=({"id": "offer-1"}, True))
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", "dealer"))
    def test_mismatched_reference_import_stays_in_parser_review(
        self,
        _mock_dealer: MagicMock,
        _mock_group: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        _mock_insert_message: MagicMock,
        _mock_find_watch: MagicMock,
        _mock_active: MagicMock,
        _mock_matches: MagicMock,
        _mock_notifications: MagicMock,
        _mock_unknown: MagicMock,
        _mock_nicknames: MagicMock,
    ) -> None:
        from ingest import ingest_message

        message = f"{AP_HEADER}\n126334 blue jubilee new 14500 eur"
        summary = ingest_message(message, group_name="HK", dealer_whatsapp="+85291234567")

        assert summary["status"] == "success"
        assert summary["parsed_watches"][0]["reference"] == "126334"
        assert summary["parsed_watches"][0]["brand"] == AP_HEADER
        assert summary["parsed_watches"][0].get("reference_brand_conflict")
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()
        import_log = {
            "id": "log-1",
            "status": summary["status"],
            "watches_parsed": summary["watches_parsed"],
            "summary": summary,
        }
        assert is_parser_review_pending(import_log) is False
