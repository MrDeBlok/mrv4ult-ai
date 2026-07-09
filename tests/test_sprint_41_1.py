"""Tests for Sprint 41.1 watch evidence gating before watch/offer creation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from activity_feed import filter_ignored_activity_imports
from app import build_deal_analysis_cards
from contact_classification import CONTACT_TYPE_DEALER
from import_status import format_import_status, import_status_reason
from ingest import ingest_message
from parser_review import is_parser_review_pending
from watch_evidence import (
    INSUFFICIENT_EVIDENCE_REASON,
    compute_watch_evidence_score,
    describe_evidence_gaps,
    has_sufficient_watch_evidence,
    is_valid_watch_reference,
    partition_watches_by_evidence,
)
from watch_knowledge import enrich_parsed_watch
from condition_normalizer import normalize_watch_condition
from watch_parser import parse_message

SWATCH_AP_MESSAGE = "Swatch x AP Royal Pop 2026y new ready in HK"


def _enriched_watch(message: str) -> dict:
    parsed = parse_message(message)
    return normalize_watch_condition(enrich_parsed_watch(parsed["watches"][0]))


class TestWatchEvidenceScoring:
    def test_swatch_ap_collaboration_line_is_not_valid_reference(self) -> None:
        watch = _enriched_watch(SWATCH_AP_MESSAGE)

        assert is_valid_watch_reference(watch) is False
        assert has_sufficient_watch_evidence(watch) is False
        assert compute_watch_evidence_score(watch) < 50
        assert "No reference." in describe_evidence_gaps(watch)
        assert "No price." in describe_evidence_gaps(watch)

    def test_rolex_with_reference_but_no_price_still_has_sufficient_evidence(self) -> None:
        watch = _enriched_watch("Rolex Submariner 124060")

        assert has_sufficient_watch_evidence(watch) is True

    def test_partition_keeps_only_sufficient_offer_lines(self) -> None:
        sufficient, insufficient = partition_watches_by_evidence(
            [
                _enriched_watch("ROLEX 126200 green jub n6/26 74000usd"),
                _enriched_watch(SWATCH_AP_MESSAGE),
            ]
        )

        assert len(sufficient) == 1
        assert len(insufficient) == 1


class TestInsufficientEvidenceIngest:
    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_informational_watch_line_is_ignored_without_creating_watch_or_offer(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_import_log.return_value = {"id": "log-insufficient"}

        summary = ingest_message(
            SWATCH_AP_MESSAGE,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "insufficient_evidence"
        assert summary["status_reason"] == INSUFFICIENT_EVIDENCE_REASON
        assert summary["watches_parsed"] == 0
        assert summary["new_offers"] == 0
        assert summary["import_classification"] == "insufficient_evidence"
        mock_find_watch.assert_not_called()
        mock_insert_offer.assert_not_called()
        mock_process_matches.assert_not_called()
        mock_record_notifications.assert_not_called()

        import_log = {
            "id": "log-insufficient",
            "status": summary["status"],
            "watches_parsed": 0,
            "new_offers": 0,
            "summary": summary,
        }
        assert import_log in filter_ignored_activity_imports([import_log])
        assert is_parser_review_pending(import_log) is False
        assert format_import_status("insufficient_evidence") == "Ignored"
        assert import_status_reason(import_log) == INSUFFICIENT_EVIDENCE_REASON
        assert build_deal_analysis_cards(summary) == []

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_incomplete_watch_with_reference_still_creates_offer(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-incomplete"}

        summary = ingest_message(
            "Rolex Submariner 124060",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "warning"
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 0
        mock_insert_offer.assert_not_called()

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_normal_priced_offer_still_works(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-offer"}

        summary = ingest_message(
            "ROLEX 126200 green jub n6/26 74000usd",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        mock_insert_offer.assert_called_once()
        assert build_deal_analysis_cards(summary)

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_offer_list_skips_low_evidence_lines_but_keeps_valid_offers(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-mixed"}

        summary = ingest_message(
            "\n".join(
                [
                    "ROLEX 126200 green jub n6/26 74000usd",
                    SWATCH_AP_MESSAGE,
                ]
            ),
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 1
        assert summary["new_offers"] == 1
        assert summary["insufficient_evidence_watches"] == 1
        mock_insert_offer.assert_called_once()
