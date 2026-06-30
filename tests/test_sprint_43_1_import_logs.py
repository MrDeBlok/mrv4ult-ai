"""Tests for Sprint 43.1 import_logs query optimization."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import _parser_review_import_logs
from activity_feed import load_activity_page
from database import (
    IMPORT_LOG_LIST_COLUMNS_LIGHT,
    IMPORT_LOG_LIST_LIMIT_DASHBOARD,
    IMPORT_LOG_LIST_LIMIT_MARKET_REQUESTS,
    IMPORT_LOG_LIST_LIMIT_PARSER_REVIEW,
    import_log_detail_columns_full,
    import_log_list_columns,
    import_log_list_columns_light,
    list_import_logs,
    list_market_request_import_logs,
    list_parser_review_import_log_candidates,
)
from performance_profiler import (
    PROFILE_STATUS_OK,
    SPRINT_43_0_IMPORT_LOGS_BASELINE_MS,
    PageProfileResult,
    ProfilerSession,
    _result_from_session,
)
from tests.conftest import ADMIN_USER


class TestImportLogListColumns:
    def test_list_columns_exclude_summary_json(self) -> None:
        columns = import_log_list_columns_light()
        assert "summary" not in columns
        assert "watches_parsed" in columns
        assert "status" in columns
        assert columns.startswith(IMPORT_LOG_LIST_COLUMNS_LIGHT.split(",")[0])

    def test_detail_columns_include_summary_json(self) -> None:
        columns = import_log_detail_columns_full()
        assert "summary" in columns
        assert "watches_parsed" in columns

    def test_list_columns_alias_matches_light_projection(self) -> None:
        assert import_log_list_columns() == import_log_list_columns_light()

    @patch("database.user_ownership_columns_supported", return_value=True)
    def test_list_columns_include_imported_by_user_id_when_supported(
        self,
        _mock_ownership: MagicMock,
    ) -> None:
        columns = import_log_list_columns_light()
        assert "imported_by_user_id" in columns
        assert "summary" not in columns


class TestImportLogQueryShape:
    def _mock_query_chain(self) -> tuple[MagicMock, MagicMock]:
        mock_execute = MagicMock()
        mock_execute.data = [{"id": "log-1"}]
        mock_query = MagicMock()
        mock_query.execute.return_value = mock_execute
        mock_table = MagicMock()
        mock_table.select.return_value.order.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.limit.return_value = mock_query
        return mock_table, mock_query

    @patch("database.get_client")
    @patch("database.import_log_list_columns_light", return_value="id,status")
    def test_list_import_logs_uses_column_projection_limit_and_order(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        rows = list_import_logs(limit=IMPORT_LOG_LIST_LIMIT_DASHBOARD)

        mock_get_client.return_value.table.assert_called_with("import_logs")
        mock_table.select.assert_called_once_with("id,status")
        mock_table.select.return_value.order.assert_called_once_with("import_time", desc=True)
        mock_query.limit.assert_called_once_with(IMPORT_LOG_LIST_LIMIT_DASHBOARD)
        mock_query.eq.assert_not_called()
        assert rows == [{"id": "log-1"}]

    @patch("database.get_client")
    @patch("database.import_log_list_columns_light", return_value="id,status")
    def test_list_market_request_import_logs_filters_status(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        list_market_request_import_logs()

        mock_query.eq.assert_called_once_with("status", "request_intent")
        mock_query.limit.assert_called_once_with(IMPORT_LOG_LIST_LIMIT_MARKET_REQUESTS)

    @patch("database.get_client")
    @patch("database.import_log_list_columns_light", return_value="id,status")
    def test_list_parser_review_candidates_filters_warning_status(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_table, mock_query = self._mock_query_chain()
        mock_get_client.return_value.table.return_value = mock_table

        list_parser_review_import_log_candidates()

        mock_query.eq.assert_called_once_with("status", "warning")
        mock_query.limit.assert_called_once_with(IMPORT_LOG_LIST_LIMIT_PARSER_REVIEW)


class TestImportLogCallSiteLimits:
    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs", return_value=[])
    def test_activity_uses_bounded_tab_filtered_queries(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        load_activity_page(ADMIN_USER, "active", page=1)

        mock_list_activity.assert_called_once_with(tab="active", offset=0, limit=60)

    @patch("app.filter_business_import_logs", side_effect=lambda logs, _lookup: logs)
    @patch("app.build_dealer_lookup_by_whatsapp", return_value={})
    @patch("app.list_contacts_for_import_lookup", return_value=[])
    @patch("app.filter_discarded_import_logs", side_effect=lambda logs: logs)
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app.list_parser_review_import_log_candidates")
    def test_parser_review_uses_warning_candidates_query(
        self,
        mock_candidates: MagicMock,
        _mock_filter_user: MagicMock,
        _mock_filter_discarded: MagicMock,
        _mock_contacts: MagicMock,
        _mock_lookup: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_candidates.return_value = []

        _parser_review_import_logs(ADMIN_USER)

        mock_candidates.assert_called_once_with()


class TestProfilerImportLogsBaseline:
    def test_session_tracks_import_logs_query_ms(self) -> None:
        session = ProfilerSession(page="Dashboard", path="/dashboard")
        session.record_query("GET dealers?select=id", 10.0)
        session.record_query(
            "GET import_logs?select=id,summary&order=import_time.desc&limit=400",
            120.0,
        )

        assert session.import_logs_query_ms() == 120.0

    def test_report_row_includes_before_after_import_logs_delta(self) -> None:
        session = ProfilerSession(page="Dashboard", path="/dashboard")
        session.record_query("GET import_logs?select=id&limit=400", 250.0)
        result = _result_from_session(
            session,
            status=PROFILE_STATUS_OK,
            status_code=200,
            total_response_ms=500.0,
        )
        row = result.to_report_row()

        assert row["import_logs_query_ms"] == 250.0
        assert row["import_logs_baseline_ms"] == SPRINT_43_0_IMPORT_LOGS_BASELINE_MS["Dashboard"]
        assert row["import_logs_delta_ms"] == pytest.approx(5367.0)

    def test_page_profile_result_parser_review_query_count_delta(self) -> None:
        result = PageProfileResult(
            page="Parser Review",
            path="/parser-review",
            status=PROFILE_STATUS_OK,
            status_code=200,
            total_response_ms=100.0,
            python_ms=50.0,
            render_ms=10.0,
            database_ms=40.0,
            query_count=12,
            slowest_query="GET import_logs?select=id&limit=400",
            slowest_query_ms=30.0,
            import_logs_query_ms=30.0,
        )
        row = result.to_report_row()
        assert row["query_count_baseline"] == 227
        assert row["query_count_delta"] == 215
