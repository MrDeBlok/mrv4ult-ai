"""Tests for Sprint 43.4 activity pagination filtering optimization."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from activity_feed import (
    ACTIVITY_MAX_SCANNED_ROWS,
    ACTIVITY_OVERFETCH_MULTIPLIER,
    ACTIVITY_PAGE_SIZE,
    ACTIVITY_STATS_SCAN_LIMIT,
    activity_scan_budget,
    load_activity_page,
)
from tests.conftest import ADMIN_USER, TRADER_ONE
from tests.test_sprint_43_3 import _import_log


class TestActivityScanBudget:
    def test_page_one_scan_budget_is_page_size_times_three(self) -> None:
        assert activity_scan_budget(1) == ACTIVITY_PAGE_SIZE * ACTIVITY_OVERFETCH_MULTIPLIER

    def test_scan_budget_is_capped_by_hard_max(self) -> None:
        assert activity_scan_budget(10) == ACTIVITY_MAX_SCANNED_ROWS


class TestActivityBoundedLoading:
    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_page_one_uses_single_bounded_query(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.return_value = [_import_log(f"log-{index}") for index in range(25)]

        load_activity_page(ADMIN_USER, "active", page=1)

        assert mock_list_activity.call_count == 1
        assert mock_list_activity.call_args.kwargs["limit"] == activity_scan_budget(1)
        assert mock_list_activity.call_args.kwargs["tab"] == "active"
        assert mock_list_activity.call_args.kwargs["offset"] == 0

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_page_load_uses_at_most_two_queries(
        self,
        mock_list_activity: MagicMock,
        mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.return_value = []

        load_activity_page(ADMIN_USER, "all", page=1)

        assert mock_list_activity.call_count == 1
        mock_stats.assert_called_once()

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_visibility_filter_returns_fewer_rows_without_extra_scanning(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.return_value = [
            _import_log("shared", imported_by_user_id=TRADER_ONE["id"]),
            _import_log(
                "private",
                status="noise",
                watches_parsed=0,
                new_offers=0,
                imported_by_user_id=ADMIN_USER["id"],
            ),
        ]

        result = load_activity_page(TRADER_ONE, "all", page=1)

        assert mock_list_activity.call_count == 1
        assert len(result.imports) == 1
        assert result.imports[0]["id"] == "shared"

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_page_two_still_paginates_within_scan_budget(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        rows = [_import_log(f"log-{index:02d}") for index in range(45)]
        mock_list_activity.return_value = rows

        page_two = load_activity_page(ADMIN_USER, "all", page=2)

        assert mock_list_activity.call_args.kwargs["limit"] == activity_scan_budget(2)
        assert len(page_two.imports) == ACTIVITY_PAGE_SIZE
        assert page_two.imports[0]["id"] == "log-20"


class TestActivityDatabaseFilters:
    def _mock_query_chain(self) -> tuple[MagicMock, MagicMock]:
        mock_execute = MagicMock()
        mock_execute.data = [{"id": "log-1"}]
        mock_query = MagicMock()
        mock_query.execute.return_value = mock_execute
        mock_query.or_.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.neq.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_table = MagicMock()
        mock_table.select.return_value.order.return_value = mock_query
        return mock_table, mock_query

    @patch("database.get_client")
    @patch("database.import_log_list_columns_light", return_value="id,status")
    def test_active_tab_applies_database_status_filter(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from database import list_activity_import_logs

        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        list_activity_import_logs(tab="active", offset=0, limit=60)

        mock_query.neq.assert_called_once_with("status", "no_watch_detected")
        mock_query.or_.assert_called_once()
        assert "status.eq.success" in mock_query.or_.call_args.args[0]

    @patch("database.get_client")
    @patch("database.import_log_list_columns_light", return_value="id,status")
    def test_stats_scan_uses_all_tab_without_no_watch_status(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from activity_feed import load_activity_stats_bounded
        from database import list_activity_import_logs

        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        with patch(
            "database.list_activity_import_logs",
            wraps=list_activity_import_logs,
        ) as mock_list:
            load_activity_stats_bounded(ADMIN_USER)

        mock_list.assert_called_once_with(
            tab="all",
            offset=0,
            limit=ACTIVITY_STATS_SCAN_LIMIT,
        )
        mock_query.neq.assert_called_with("status", "no_watch_detected")
