"""Unit tests for the smart activity feed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from activity_feed import (
    activity_feed_counts,
    build_ignored_activity_row,
    filter_activity_feed_imports,
    filter_ignored_import_logs,
    message_preview,
)
from database import cleanup_ignored_messages


def _import_log(
    *,
    import_id: str,
    status: str,
    watches_parsed: int = 0,
    message_id: str = "msg-1",
) -> dict:
    return {
        "id": import_id,
        "status": status,
        "watches_parsed": watches_parsed,
        "message_id": message_id,
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+85291234567",
        "import_time": "2026-06-27T12:00:00+00:00",
    }


class TestActivityFeedFilters:
    def test_main_feed_includes_success_and_warning_only(self) -> None:
        logs = [
            _import_log(import_id="1", status="success", watches_parsed=1),
            _import_log(import_id="2", status="warning", watches_parsed=1),
            _import_log(import_id="3", status="no_watch_detected"),
            _import_log(import_id="4", status="error"),
        ]

        filtered = filter_activity_feed_imports(logs)

        assert [row["id"] for row in filtered] == ["1", "2"]

    def test_ignored_feed_includes_no_watch_detected_only(self) -> None:
        logs = [
            _import_log(import_id="1", status="success", watches_parsed=1),
            _import_log(import_id="2", status="warning", watches_parsed=1),
            _import_log(import_id="3", status="no_watch_detected"),
            _import_log(import_id="4", status="warning", watches_parsed=0),
        ]

        filtered = filter_ignored_import_logs(logs)

        assert [row["id"] for row in filtered] == ["3", "4"]


class TestActivityFeedCounts:
    def test_counts_offers_needs_review_and_ignored(self) -> None:
        logs = [
            _import_log(import_id="1", status="success", watches_parsed=1),
            _import_log(import_id="2", status="success", watches_parsed=2),
            _import_log(import_id="3", status="warning", watches_parsed=1),
            _import_log(import_id="4", status="no_watch_detected"),
            _import_log(import_id="5", status="error"),
        ]

        assert activity_feed_counts(logs) == {
            "offers": 2,
            "needs_review": 1,
            "ignored": 1,
        }


class TestIgnoredActivityRow:
    def test_message_preview_truncates_to_eighty_characters(self) -> None:
        text = "A" * 100
        preview = message_preview(text)

        assert len(preview) == 80
        assert preview.endswith("…")

    def test_build_ignored_row_includes_required_columns(self) -> None:
        row = build_ignored_activity_row(
            _import_log(import_id="ignored-1", status="no_watch_detected"),
            {"raw_text": "Just a chat message about lunch plans today"},
        )

        assert row["group_name"] == "HK Dealers"
        assert row["dealer"] == "Private contact"
        assert row["dealer_redacted"] is True
        assert row["message_preview"] == "Just a chat message about lunch plans today"
        assert row["status_reason"] == "No watch offer was detected in this message."


class TestCleanupIgnoredMessages:
    def test_rejects_negative_days(self) -> None:
        with pytest.raises(ValueError, match="days must be zero or greater"):
            cleanup_ignored_messages(days=-1)

    @patch("database.get_client")
    def test_deletes_old_ignored_import_logs(self, mock_get_client: MagicMock) -> None:
        mock_table = MagicMock()
        mock_delete = MagicMock()
        mock_eq = MagicMock()
        mock_lt = MagicMock()

        mock_get_client.return_value.table.return_value = mock_table
        mock_table.delete.return_value = mock_delete
        mock_delete.eq.return_value = mock_eq
        mock_eq.lt.return_value = mock_lt
        mock_lt.execute.return_value = MagicMock(data=[{"id": "1"}, {"id": "2"}])

        deleted = cleanup_ignored_messages(days=30)

        mock_get_client.return_value.table.assert_called_once_with("import_logs")
        mock_table.delete.assert_called_once()
        mock_delete.eq.assert_called_once_with("status", "no_watch_detected")
        cutoff = mock_eq.lt.call_args.args[1]
        assert datetime.fromisoformat(cutoff) <= datetime.now(timezone.utc) - timedelta(days=29)
        assert deleted == 2
