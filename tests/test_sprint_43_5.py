"""Tests for Sprint 43.5 market requests list N+1 elimination."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from market_requests import load_market_request_rows
from tests.conftest import ADMIN_USER, TRADER_ONE
from tests.test_sprint_35 import _market_request_log


class TestMarketRequestListBatchLoading:
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.get_messages_by_ids")
    @patch("market_requests.list_import_logs")
    def test_list_batch_loads_messages_once(
        self,
        mock_list_logs: MagicMock,
        mock_get_messages: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        mock_list_logs.return_value = [
            _market_request_log(import_id="req-1", message_id="msg-1"),
            _market_request_log(import_id="req-2", message_id="msg-2"),
            _market_request_log(import_id="req-3", message_id="msg-1"),
        ]
        mock_get_messages.return_value = {
            "msg-1": {"raw_text": "WTB Rolex Submariner"},
            "msg-2": {"raw_text": "LF AP Royal Oak"},
        }

        rows = load_market_request_rows(ADMIN_USER)

        mock_get_messages.assert_called_once_with(["msg-1", "msg-2"])
        mock_get_message.assert_not_called()
        assert len(rows) == 2
        assert rows[0]["message_preview"] == "WTB Rolex Submariner"

    @patch("market_requests.get_message_by_id")
    @patch("market_requests.get_messages_by_ids", return_value={})
    @patch("market_requests.list_import_logs")
    def test_visibility_filtering_is_preserved_with_batch_loading(
        self,
        mock_list_logs: MagicMock,
        _mock_get_messages: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        mock_list_logs.return_value = [
            _market_request_log(import_id="team", owner_user_id=TRADER_ONE["id"]),
            _market_request_log(import_id="private", owner_user_id=ADMIN_USER["id"]),
        ]

        rows = load_market_request_rows(TRADER_ONE)

        mock_get_message.assert_not_called()
        assert [row["id"] for row in rows] == ["team"]

    @patch("market_requests.get_messages_by_ids")
    @patch("market_requests.list_import_logs")
    def test_dedupe_and_sorting_unchanged_with_batch_loading(
        self,
        mock_list_logs: MagicMock,
        mock_get_messages: MagicMock,
    ) -> None:
        mock_list_logs.return_value = [
            _market_request_log(
                import_id="older",
                message_id="msg-dup",
                import_time="2026-06-27T10:00:00+00:00",
                group_name="HK",
            ),
            _market_request_log(
                import_id="newer",
                message_id="msg-dup",
                import_time="2026-06-27T12:00:00+00:00",
                group_name="EU",
            ),
        ]
        mock_get_messages.return_value = {
            "msg-dup": {"raw_text": "WTB Rolex 126610LN budget 12000"},
        }

        rows = load_market_request_rows(ADMIN_USER)

        assert len(rows) == 1
        assert rows[0]["id"] == "newer"
        assert rows[0]["groups_seen_count"] == 2
        assert "Seen in 2 groups" in rows[0]["groups_seen_label"]

    @patch("market_requests.get_message_by_id")
    @patch("market_requests.get_messages_by_ids", return_value={})
    @patch("market_requests.list_import_logs")
    def test_list_page_uses_at_most_one_message_batch_query(
        self,
        mock_list_logs: MagicMock,
        mock_get_messages: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        mock_list_logs.return_value = [
            _market_request_log(import_id=f"req-{index}", message_id=f"msg-{index}")
            for index in range(5)
        ]

        load_market_request_rows(ADMIN_USER)

        assert mock_get_messages.call_count == 1
        assert mock_get_message.call_count == 0
