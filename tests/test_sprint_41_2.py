"""Tests for Sprint 41.2 removal of Bulk Import Review."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_activity_detail
from contact_classification import CONTACT_TYPE_DEALER
from ingest import _import_status, ingest_message, is_large_dealer_list_import_log
from parser_review import (
    filter_parser_review_imports,
    is_parser_review_pending,
    parser_review_counts,
)
from tests.test_sprint_40_0 import LARGE_30_LINE_DEALER_LIST
from tests.test_sprint_41_1 import SWATCH_AP_MESSAGE
from watch_evidence import INSUFFICIENT_EVIDENCE_REASON
from watch_parser import parse_message


def _bulk_import_log(*, watches_count: int = 100, warning_lines: int = 40) -> dict:
    watches = [
        {
            "brand": "Rolex",
            "reference": None,
            "dealer_list_line": True,
            "original_price": None,
        }
        for _ in range(watches_count)
    ]
    return {
        "id": "log-bulk",
        "status": "success",
        "message_id": "msg-bulk",
        "import_time": "2026-06-27T12:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+31612345678",
        "watches_parsed": watches_count,
        "new_offers": 60,
        "duplicate_offers": 40,
        "summary": {
            "bulk_import": True,
            "status_reason": f"Successfully parsed {watches_count} watch offer(s).",
            "parsed_watches": watches,
            "rows": watches,
        },
    }


def _single_missing_price_import(import_id: str = "log-single") -> dict:
    return {
        "id": import_id,
        "status": "warning",
        "message_id": "msg-single",
        "import_time": "2026-06-27T12:00:00+00:00",
        "group_name": "EU Dealers",
        "dealer_alias": "Dealer B",
        "dealer_whatsapp": "+31687654321",
        "watches_parsed": 1,
        "new_offers": 0,
        "duplicate_offers": 0,
        "summary": {
            "status_reason": "Important fields are missing — watch 1: missing price",
            "parsed_watches": [
                {
                    "brand": "Rolex",
                    "reference": "126200",
                    "original_price": None,
                }
            ],
        },
    }


class TestBulkReviewRemoved:
    @patch(
        "database.get_messages_by_ids",
        return_value={
            "msg-single": {"raw_text": "Rolex 126200"},
            "msg-bulk": {"raw_text": "Rolex 126200"},
        },
    )
    @patch("app._parser_review_import_logs")
    def test_parser_review_page_has_no_bulk_section(
        self,
        mock_import_logs: MagicMock,
        _mock_get_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = [_bulk_import_log(), _single_missing_price_import()]

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert "Bulk import review" not in response.text
        assert "View bulk import" not in response.text
        assert "Bulk imports:" not in response.text
        assert "Missing price" in response.text

    def test_large_imports_with_review_rows_appear_in_parser_review(self) -> None:
        import_log = _bulk_import_log(watches_count=100)

        assert is_large_dealer_list_import_log(import_log) is True
        assert is_parser_review_pending(import_log) is True
        assert len(filter_parser_review_imports([import_log])) == 1
        assert parser_review_counts([import_log])["total"] == 1

    def test_normal_single_needs_review_still_appears(self) -> None:
        import_log = _single_missing_price_import()

        assert is_parser_review_pending(import_log) is True
        assert parser_review_counts([import_log])["total"] == 1

    def test_bulk_import_status_uses_warning_when_rows_need_training(self) -> None:
        watches = [
            {"brand": "Rolex", "reference": None, "dealer_list_line": True, "original_price": 12500, "original_currency": "USD"}
            for _ in range(12)
        ]
        summary = {"watches_parsed": 12, "duplicate_offers": 2}

        status, reason = _import_status(
            summary,
            "success",
            watches,
            bulk_mode=True,
        )

        assert status == "warning"
        assert "need parser training" in reason
        assert "Bulk imported with warnings" not in reason

    @patch("app.get_import_log")
    @patch("app.get_message_by_id")
    @patch("app._can_access_import_log", return_value=True)
    def test_import_detail_has_no_bulk_summary_section(
        self,
        _mock_access: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _bulk_import_log()
        mock_get_message.return_value = {"raw_text": "Large dealer list message"}

        client = TestClient(app)
        response = client.get("/activity/log-bulk")

        assert response.status_code == 200
        assert "Bulk import summary" not in response.text
        detail = build_activity_detail(_bulk_import_log(), {"raw_text": "Large dealer list"})
        assert "bulk_summary" not in detail
        assert "Bulk imported with warnings" not in detail["status_reason"]


class TestBulkModeBehaviorRetained:
    def test_multi_offer_parsing_still_works(self) -> None:
        watches = parse_message(LARGE_30_LINE_DEALER_LIST)["watches"]

        assert len(watches) >= 10

    @patch("ingest.record_import_notifications")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_message", return_value={"id": "msg-bulk"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    @patch("ingest._cached_insert_offer")
    @patch("ingest._cached_find_or_create_watch")
    def test_large_dealer_list_ingest_still_uses_bulk_mode(
        self,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_record_notifications: MagicMock,
    ) -> None:
        from ingest import ingest_message as ingest

        watches = parse_message(LARGE_30_LINE_DEALER_LIST)["watches"]
        mock_find_watch.return_value = ({"id": "watch-1"}, False)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-bulk"}

        with patch("ingest.parse_message", return_value={"message_type": "offer_list", "watches": watches}):
            summary = ingest(
                LARGE_30_LINE_DEALER_LIST,
                group_name="HK Dealers",
                dealer_whatsapp="+31612345678",
            )

        assert summary["bulk_import"] is True
        assert summary["status"] == "success"
        assert "Bulk imported with warnings" not in summary.get("status_reason", "")
        mock_record_notifications.assert_not_called()

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
    def test_low_evidence_messages_are_still_ignored(
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
        mock_insert_offer.assert_not_called()
