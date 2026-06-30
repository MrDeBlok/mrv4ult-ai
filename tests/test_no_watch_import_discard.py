"""Tests for discarding no-watch imports from persistence and Activity."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from activity_feed import (
    filter_all_activity_imports,
    filter_discarded_activity_imports,
    filter_ignored_activity_imports,
)
from app import app
from contact_classification import CONTACT_TYPE_DEALER
from import_status import is_discarded_no_watch_import
from ingest import ingest_message
from watch_parser import parse_message


def _import_log(*, import_id: str, status: str = "no_watch_detected") -> dict:
    return {
        "id": import_id,
        "status": status,
        "watches_parsed": 0,
        "new_offers": 0,
        "message_id": "msg-1",
        "group_name": "Family Chat",
        "dealer_alias": None,
        "dealer_whatsapp": "",
        "import_time": "2026-06-27T12:00:00+00:00",
        "summary": {"status_reason": "No watch offer was detected in this message."},
    }


class TestNoWatchImportDiscard:
    def test_is_discarded_no_watch_import(self) -> None:
        assert is_discarded_no_watch_import(_import_log(import_id="legacy")) is True
        assert is_discarded_no_watch_import(_import_log(import_id="ok", status="success")) is False

    def test_activity_filters_remove_legacy_no_watch_records(self) -> None:
        logs = [
            _import_log(import_id="legacy"),
            _import_log(import_id="noise", status="noise"),
            _import_log(import_id="success", status="success"),
        ]

        assert [row["id"] for row in filter_discarded_activity_imports(logs)] == ["noise", "success"]
        assert [row["id"] for row in filter_all_activity_imports(logs)] == ["noise", "success"]
        assert [row["id"] for row in filter_ignored_activity_imports(logs)] == ["noise"]

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_no_watch_import_is_not_saved(
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
        summary = ingest_message(
            "Hey, are we still meeting for lunch tomorrow?",
            group_name="Family Chat",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "no_watch_detected"
        assert summary["saved"] is False
        assert summary["import_log_id"] is None
        mock_insert_message.assert_not_called()
        mock_insert_import_log.assert_not_called()
        mock_find_group.assert_not_called()
        mock_find_dealer.assert_not_called()

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True))
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_normal_watch_import_is_still_saved(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "ROLEX 126200 green jub n6/26 74000usd",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 1
        assert summary["import_log_id"] == "log-1"
        mock_insert_message.assert_called_once()
        mock_insert_import_log.assert_called_once()

    def test_parser_still_parses_watch_offers(self) -> None:
        parsed = parse_message("ROLEX 126200 green jub n6/26 74000usd")

        assert parsed["message_type"] in {"offer", "offer_list"}
        assert parsed["watches"]
        assert parsed["watches"][0].get("brand")

    @patch("app.get_import_log")
    @patch("database.list_activity_import_logs")
    def test_no_watch_imports_do_not_appear_in_activity(
        self,
        mock_list_activity_import_logs: MagicMock,
        mock_get_import_log: MagicMock,
    ) -> None:
        legacy_import = _import_log(import_id="11111111-1111-4111-8111-111111111111")
        mock_list_activity_import_logs.return_value = [
            legacy_import,
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "status": "success",
                "watches_parsed": 1,
                "new_offers": 1,
                "message_id": "msg-2",
                "group_name": "HK Dealers",
                "dealer_alias": "Dealer A",
                "dealer_whatsapp": "+85291234567",
                "import_time": "2026-06-27T13:00:00+00:00",
                "summary": {},
            },
        ]
        mock_get_import_log.return_value = legacy_import

        client = TestClient(app)
        for path in ("/activity", "/activity/all", "/activity/ignored"):
            response = client.get(path)
            assert response.status_code == 200
            assert legacy_import["id"] not in response.text

        detail = client.get(f"/activity/{legacy_import['id']}")
        assert detail.status_code == 404
        mock_get_import_log.assert_called_once_with(legacy_import["id"])
