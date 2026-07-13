"""Tests for Sprint 43.4 activity pagination filtering optimization."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from postgrest.exceptions import APIError

from activity_feed import (
    ACTIVITY_DB_MAX_LIMIT,
    ACTIVITY_PAGE_SIZE,
    ACTIVITY_STATS_SCAN_LIMIT,
    load_activity_page,
    load_activity_stats_bounded,
)
from database import ACTIVITY_IMPORT_LOG_MAX_LIMIT, activity_import_log_list_columns
from tests.conftest import ADMIN_USER, TRADER_ONE
from tests.test_sprint_43_3 import _import_log


class TestActivityDirectPagination:
    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_page_one_requests_offset_zero_and_page_size(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.return_value = [_import_log(f"log-{index}") for index in range(20)]

        load_activity_page(ADMIN_USER, "active", page=1)

        assert mock_list_activity.call_count == 1
        assert mock_list_activity.call_args.kwargs == {
            "tab": "active",
            "offset": 0,
            "limit": ACTIVITY_PAGE_SIZE,
        }

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_page_two_uses_database_offset(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.return_value = [_import_log("log-20")]

        page_two = load_activity_page(ADMIN_USER, "all", page=2)

        assert mock_list_activity.call_args.kwargs["offset"] == ACTIVITY_PAGE_SIZE
        assert mock_list_activity.call_args.kwargs["limit"] == ACTIVITY_PAGE_SIZE
        assert len(page_two.imports) == 1
        assert page_two.has_previous is True

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
    @patch("database.list_activity_import_logs", return_value=[])
    def test_timeout_returns_empty_page_without_crashing(
        self,
        _mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        result = load_activity_page(ADMIN_USER, "active", page=1)

        assert result.imports == []
        assert result.empty_message == "No activity yet."


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
        mock_query.order.return_value = mock_query
        mock_table = MagicMock()
        mock_table.select.return_value = mock_query
        return mock_table, mock_query

    @patch("database.get_client")
    @patch("database.activity_import_log_list_columns", return_value="id,status,created_at")
    def test_active_tab_applies_database_status_filter(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from database import list_activity_import_logs

        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        list_activity_import_logs(tab="active", offset=0, limit=20)

        mock_table.select.assert_called_once_with("id,status,created_at")
        mock_query.order.assert_called_once_with("created_at", desc=True)
        mock_query.neq.assert_called_once_with("status", "no_watch_detected")
        mock_query.or_.assert_called_once()
        assert "status.eq.success" in mock_query.or_.call_args.args[0]
        mock_query.range.assert_called_once_with(0, 19)

    @patch("database.get_client")
    @patch("database.activity_import_log_list_columns", return_value="id,status,created_at")
    def test_limit_is_capped_at_fifty(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from database import list_activity_import_logs

        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        list_activity_import_logs(tab="all", offset=0, limit=200)

        mock_query.range.assert_called_once_with(0, ACTIVITY_IMPORT_LOG_MAX_LIMIT - 1)

    @patch("database.get_client")
    @patch("database.activity_import_log_list_columns", return_value="id,status,created_at")
    def test_statement_timeout_returns_empty_list(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from database import list_activity_import_logs

        mock_table, mock_query = self._mock_query_chain()
        mock_query.execute.side_effect = APIError(
            {"message": "canceling statement due to statement timeout", "code": "57014", "details": "", "hint": ""}
        )
        mock_get_client.return_value.table.return_value = mock_table

        rows = list_activity_import_logs(tab="all", offset=0, limit=20)

        assert rows == []

    @patch("database.get_client")
    @patch("database.activity_import_log_list_columns", return_value="id,status,created_at")
    def test_stats_scan_uses_all_tab_without_no_watch_status(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        with patch(
            "database.list_activity_import_logs",
            wraps=__import__("database").list_activity_import_logs,
        ) as mock_list:
            load_activity_stats_bounded(ADMIN_USER)

        mock_list.assert_called_once_with(
            tab="all",
            offset=0,
            limit=ACTIVITY_STATS_SCAN_LIMIT,
        )
        mock_query.neq.assert_called_with("status", "no_watch_detected")


class TestActivityListColumns:
    def test_activity_columns_include_rendered_fields_only(self) -> None:
        columns = set(activity_import_log_list_columns().split(","))
        assert columns >= {
            "id",
            "import_time",
            "created_at",
            "group_name",
            "dealer_whatsapp",
            "dealer_alias",
            "watches_parsed",
            "new_offers",
            "duplicate_offers",
            "matched_requests",
            "processing_time",
            "status",
        }
        assert "summary" not in columns
        assert "*" not in columns

    def test_db_max_limit_matches_activity_feed(self) -> None:
        assert ACTIVITY_IMPORT_LOG_MAX_LIMIT == ACTIVITY_DB_MAX_LIMIT == 50
