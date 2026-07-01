"""Tests for Sprint 43.0 performance profiler."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import contextvars
import httpx
import jinja2
import pytest
import time
from fastapi.testclient import TestClient

from app import app
from performance_profiler import (
    PROFILE_PAGES,
    PROFILE_STATUS_OK,
    PROFILE_STATUS_TIMEOUT,
    PageProfileResult,
    ProfilerSession,
    _python_processing_ms,
    _result_from_session,
    build_performance_report,
    describe_postgrest_request,
    install_profiler_hooks,
    profile_all_pages,
    profile_page,
    reset_profiler_hooks,
)
from tests.conftest import ADMIN_USER, TRADER_ONE, VIEWER_USER

EMPTY_REQUESTS_SUMMARY = {
    "open_requests": 0,
    "matched_requests": 0,
    "total_potential_profit": "—",
    "biggest_opportunity": {
        "client_name": "—",
        "offer_label": "—",
        "potential_profit": "—",
    },
}

REPORT_COLUMNS = (
    "page",
    "path",
    "status",
    "total_response_ms",
    "python_ms",
    "render_ms",
    "database_ms",
    "query_count",
    "slowest_query",
    "error_message",
)


class TestProfilerSession:
    def test_records_queries_and_slowest(self) -> None:
        session = ProfilerSession(page="Search", path="/")
        session.record_query("GET /watches?select=*", 12.5)
        session.record_query("GET /offers?select=*", 45.2)

        assert session.query_count == 2
        assert session.database_ms == 57.7
        slowest = session.slowest_query()
        assert slowest is not None
        assert slowest.description == "GET /offers?select=*"
        assert slowest.duration_ms == 45.2

    def test_describe_postgrest_request_includes_method_and_params(self) -> None:
        request = SimpleNamespace(
            http_method="GET",
            path="https://example.supabase.co/rest/v1/dealers",
            params={"select": "id,name", "limit": "1"},
        )
        description = describe_postgrest_request(request)
        assert description.startswith("GET dealers?")
        assert "select=id,name" in description

    def test_python_processing_ms_is_derived_from_total(self) -> None:
        assert _python_processing_ms(100.0, 60.0, 25.0) == 15.0
        assert _python_processing_ms(50.0, 80.0, 10.0) == 0.0


class TestProfilerHooks:
    def setup_method(self) -> None:
        reset_profiler_hooks()

    def teardown_method(self) -> None:
        reset_profiler_hooks()

    def test_send_with_retry_is_instrumented(self) -> None:
        import performance_profiler as profiler_module

        install_profiler_hooks()
        session = ProfilerSession(page="Dashboard", path="/dashboard")
        token = profiler_module._active_session.set(session)
        request = SimpleNamespace(http_method="GET", path="/notifications", params={})

        def fake_send(req: object) -> object:
            assert req is request
            return SimpleNamespace(is_success=True, status_code=200, content=b"{}")

        original = profiler_module._original_send_with_retry
        profiler_module._original_send_with_retry = fake_send
        try:
            profiler_module._profiled_send_with_retry(request)
        finally:
            profiler_module._original_send_with_retry = original
            profiler_module._active_session.reset(token)

        assert session.query_count == 1
        assert session.queries[0].description == "GET notifications"

    def test_active_session_propagates_to_worker_thread(self) -> None:
        import performance_profiler as profiler_module

        install_profiler_hooks()
        session = ProfilerSession(page="Dashboard", path="/dashboard")
        token = profiler_module._active_session.set(session)
        profile_context = contextvars.copy_context()
        request = SimpleNamespace(
            http_method="GET",
            path="https://example.supabase.co/rest/v1/import_logs",
            params={"select": "*"},
        )

        def run_profiled_query() -> None:
            original = profiler_module._original_send_with_retry

            def fake_send(req: object) -> object:
                assert profiler_module._active_session.get() is session
                assert req is request
                return SimpleNamespace(is_success=True, status_code=200, content=b"{}")

            profiler_module._original_send_with_retry = fake_send
            try:
                profiler_module._profiled_send_with_retry(request)
            finally:
                profiler_module._original_send_with_retry = original

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(profile_context.run, run_profiled_query).result()

        assert session.query_count == 1
        assert session.queries[0].description.startswith("GET import_logs")
        profiler_module._active_session.reset(token)

    def test_template_render_is_instrumented(self) -> None:
        install_profiler_hooks()
        session = ProfilerSession(page="Search", path="/")
        active_session = __import__("performance_profiler", fromlist=["_active_session"])._active_session
        token = active_session.set(session)
        template = jinja2.Template("hello {{ name }}")

        rendered = template.render(name="world")

        assert rendered == "hello world"
        assert session.render_ms >= 0
        active_session.reset(token)


class TestPerformanceReportRoute:
    @patch("database.list_activity_import_logs", return_value=[])
    @patch("app.load_trading_desk")
    @patch("app.list_notifications", return_value=[])
    @patch("app.build_notification_rows", return_value=[])
    @patch("app.notification_filter_counts", return_value={"all": 0})
    @patch("app.build_notification_filter_options", return_value=[])
    @patch("app.list_canonical_brands", return_value=[])
    @patch("app._parser_review_import_logs", return_value=[])
    @patch("database.get_messages_by_ids", return_value={})
    @patch("app.watch_knowledge_supported", return_value=False)
    @patch("app.list_import_logs", return_value=[])
    @patch("app.activity_feed_counts", return_value={"active": 0, "reviewed": 0, "ignored": 0, "all": 0})
    @patch("app.list_requests", return_value=[])
    @patch("app.build_request_rows", return_value=[])
    @patch("app.build_requests_dashboard_summary", return_value=EMPTY_REQUESTS_SUMMARY)
    @patch("app.load_market_request_rows", return_value=[])
    @patch("app.get_unread_notification_count", return_value=0)
    def test_admin_report_lists_all_pages(
        self,
        _mock_unread: MagicMock,
        _mock_market: MagicMock,
        _mock_summary: MagicMock,
        _mock_request_rows: MagicMock,
        _mock_requests: MagicMock,
        _mock_activity_counts: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_knowledge: MagicMock,
        _mock_messages_batch: MagicMock,
        _mock_parser_logs: MagicMock,
        _mock_brands: MagicMock,
        _mock_filter_options: MagicMock,
        _mock_filter_counts: MagicMock,
        _mock_notification_rows: MagicMock,
        _mock_notifications: MagicMock,
        mock_load_desk: MagicMock,
        _mock_list_activity_import_logs: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        client = TestClient(app)
        response = client.get("/performance-profile")

        assert response.status_code == 200
        assert "Performance Profile" in response.text
        for page in PROFILE_PAGES:
            assert page["label"] in response.text
            assert page["path"] in response.text
        for column in ("Status", "Total response", "Database", "Python", "Render", "SQL queries", "Slowest query", "Error"):
            assert column in response.text
        assert "pause WhatsApp ingest during profiling" in response.text

    @pytest.mark.no_auto_login
    @patch("app.load_trading_desk")
    def test_trader_cannot_access_report(self, mock_load_desk: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get("/performance-profile")

        assert response.status_code == 403

    @pytest.mark.no_auto_login
    def test_viewer_cannot_access_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.get_current_user", lambda _request: VIEWER_USER)

        client = TestClient(app)
        response = client.get("/performance-profile")

        assert response.status_code == 403


class TestBuildPerformanceReport:
    @patch("database.list_activity_import_logs", return_value=[])
    @patch("app.load_trading_desk")
    @patch("app.list_notifications", return_value=[])
    @patch("app.build_notification_rows", return_value=[])
    @patch("app.notification_filter_counts", return_value={"all": 0})
    @patch("app.build_notification_filter_options", return_value=[])
    @patch("app.list_canonical_brands", return_value=[])
    @patch("app._parser_review_import_logs", return_value=[])
    @patch("database.get_messages_by_ids", return_value={})
    @patch("app.watch_knowledge_supported", return_value=False)
    @patch("app.list_import_logs", return_value=[])
    @patch("app.activity_feed_counts", return_value={"active": 0, "reviewed": 0, "ignored": 0, "all": 0})
    @patch("app.list_requests", return_value=[])
    @patch("app.build_request_rows", return_value=[])
    @patch("app.build_requests_dashboard_summary", return_value=EMPTY_REQUESTS_SUMMARY)
    @patch("app.load_market_request_rows", return_value=[])
    def test_build_performance_report_returns_seven_rows(
        self,
        _mock_market: MagicMock,
        _mock_summary: MagicMock,
        _mock_request_rows: MagicMock,
        _mock_requests: MagicMock,
        _mock_activity_counts: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_knowledge: MagicMock,
        _mock_messages_batch: MagicMock,
        _mock_parser_logs: MagicMock,
        _mock_brands: MagicMock,
        _mock_filter_options: MagicMock,
        _mock_filter_counts: MagicMock,
        _mock_notification_rows: MagicMock,
        _mock_notifications: MagicMock,
        mock_load_desk: MagicMock,
        _mock_list_activity_import_logs: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        rows = build_performance_report(app, current_user=ADMIN_USER)

        assert len(rows) == len(PROFILE_PAGES)
        assert [row["page"] for row in rows] == [page["label"] for page in PROFILE_PAGES]
        for row in rows:
            for column in REPORT_COLUMNS:
                assert column in row
            assert row["total_response_ms"] >= 0
            assert row["render_ms"] >= 0
            assert row["database_ms"] >= 0
            assert row["query_count"] >= 0

    def test_page_profile_result_formats_report_row(self) -> None:
        session = ProfilerSession(page="Dashboard", path="/dashboard")
        session.record_query("GET import_logs?select=*", 40.0)
        session.render_ms = 15.2
        result = _result_from_session(
            session,
            status=PROFILE_STATUS_OK,
            status_code=200,
            total_response_ms=120.5,
        )
        row = result.to_report_row()
        assert row["status"] == PROFILE_STATUS_OK
        assert row["database_ms"] == 40.0
        assert row["render_ms"] == 15.2
        assert row["python_ms"] == 65.3
        assert row["slowest_query"] == "GET import_logs?select=*"
        assert row["slowest_query_ms"] == 40.0
        assert row["error_message"] == "—"
        assert abs(row["total_response_ms"] - (row["database_ms"] + row["python_ms"] + row["render_ms"])) < 0.1


class TestProfilerTimeouts:
    def test_profile_page_records_timeout_without_crashing(self) -> None:
        client = MagicMock()
        client.get.side_effect = httpx.ReadTimeout("The read operation timed out")

        result = profile_page(
            client,
            page="Dashboard",
            path="/dashboard",
            page_timeout_s=0.01,
        )

        assert result.status == PROFILE_STATUS_TIMEOUT
        assert result.error_message
        assert "timed out" in result.error_message.lower()

    @patch("performance_profiler.profile_page")
    def test_profile_all_pages_continues_after_timeout(self, mock_profile_page: MagicMock) -> None:
        mock_profile_page.side_effect = [
            PageProfileResult(
                page="Dashboard",
                path="/dashboard",
                status=PROFILE_STATUS_TIMEOUT,
                status_code=0,
                total_response_ms=30000.0,
                python_ms=30000.0,
                render_ms=0.0,
                database_ms=0.0,
                query_count=0,
                slowest_query="",
                slowest_query_ms=None,
                error_message="The read operation timed out",
            ),
            PageProfileResult(
                page="Notifications",
                path="/notifications",
                status=PROFILE_STATUS_OK,
                status_code=200,
                total_response_ms=12.0,
                python_ms=4.0,
                render_ms=3.0,
                database_ms=5.0,
                query_count=1,
                slowest_query="GET notifications?select=*",
                slowest_query_ms=5.0,
            ),
        ] + [
            PageProfileResult(
                page=page["label"],
                path=page["path"],
                status=PROFILE_STATUS_OK,
                status_code=200,
                total_response_ms=1.0,
                python_ms=1.0,
                render_ms=0.0,
                database_ms=0.0,
                query_count=0,
                slowest_query="",
                slowest_query_ms=None,
            )
            for page in PROFILE_PAGES[2:]
        ]

        results = profile_all_pages(app, current_user=ADMIN_USER)

        assert len(results) == len(PROFILE_PAGES)
        assert results[0].status == PROFILE_STATUS_TIMEOUT
        assert results[1].status == PROFILE_STATUS_OK
        assert mock_profile_page.call_count == len(PROFILE_PAGES)

    @patch("app.build_performance_report")
    def test_report_page_renders_timeout_row(self, mock_build_report: MagicMock) -> None:
        mock_build_report.return_value = [
            {
                "page": "Dashboard",
                "path": "/dashboard",
                "status": PROFILE_STATUS_TIMEOUT,
                "status_code": 0,
                "total_response_ms": 30000.0,
                "python_ms": 30000.0,
                "render_ms": 0.0,
                "database_ms": 0.0,
                "query_count": 0,
                "slowest_query": "—",
                "slowest_query_ms": None,
                "error_message": "The read operation timed out",
            }
        ]

        client = TestClient(app)
        response = client.get("/performance-profile")

        assert response.status_code == 200
        assert "timeout" in response.text
        assert "The read operation timed out" in response.text
        assert "pause WhatsApp ingest during profiling" in response.text


class TestProfilerDatabaseInstrumentation:
    def _desk_payload(self) -> dict:
        return {
            "kpis": [],
            "quick_actions": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

    def _fake_postgrest_response(self, request: object) -> SimpleNamespace:
        time.sleep(0.005)
        return SimpleNamespace(
            is_success=True,
            status_code=200,
            content=b'{"data":[]}',
            request=SimpleNamespace(headers=getattr(request, "headers", {})),
        )

    def _simulate_postgrest_execute(self, *, table: str) -> None:
        import postgrest._sync.request_builder as postgrest_sync

        postgrest_sync.send_with_retry(
            SimpleNamespace(
                http_method="GET",
                path=f"https://example.supabase.co/rest/v1/{table}",
                params={"select": "*"},
            )
        )

    @patch("notifications.get_unread_notification_count", return_value=0)
    @patch("app.get_unread_notification_count", return_value=0)
    @patch("app.load_trading_desk")
    def test_dashboard_profile_records_non_zero_database_metrics(
        self,
        mock_load_desk: MagicMock,
        _mock_unread: MagicMock,
        _mock_notifications_unread: MagicMock,
    ) -> None:
        import performance_profiler as profiler_module

        reset_profiler_hooks()
        install_profiler_hooks()
        original_send = profiler_module._original_send_with_retry
        profiler_module._original_send_with_retry = self._fake_postgrest_response
        try:
            mock_load_desk.side_effect = lambda _user, **kwargs: (
                self._simulate_postgrest_execute(table="import_logs"),
                self._desk_payload(),
            )[1]

            client = TestClient(app)
            result = profile_page(
                client,
                page="Dashboard",
                path="/dashboard",
                page_timeout_s=5.0,
            )
        finally:
            profiler_module._original_send_with_retry = original_send
            reset_profiler_hooks()

        assert result.status == PROFILE_STATUS_OK
        assert result.query_count >= 1
        assert result.database_ms > 0
        assert result.import_logs_query_ms is not None
        assert result.import_logs_query_ms > 0
        assert result.total_response_ms >= (
            result.database_ms + result.python_ms + result.render_ms - 1.0
        )

    @patch("database.get_messages_by_ids")
    @patch("app.watch_knowledge_supported", return_value=False)
    @patch("app.list_canonical_brands", return_value=[])
    @patch("app._parser_review_import_logs")
    def test_parser_review_profile_records_non_zero_database_metrics(
        self,
        mock_parser_logs: MagicMock,
        _mock_brands: MagicMock,
        _mock_knowledge: MagicMock,
        mock_get_messages: MagicMock,
    ) -> None:
        import performance_profiler as profiler_module

        reset_profiler_hooks()
        install_profiler_hooks()
        original_send = profiler_module._original_send_with_retry
        profiler_module._original_send_with_retry = self._fake_postgrest_response
        try:
            review_log = {
                "id": "log-1",
                "status": "warning",
                "message_id": "msg-1",
                "group_name": "HK",
                "dealer_alias": "Dealer",
                "dealer_whatsapp": "+1",
                "import_time": "2026-06-27T12:00:00+00:00",
                "summary": {
                    "parsed_watches": [
                        {
                            "brand": "Rolex",
                            "reference": None,
                            "condition": "New",
                            "original_price": 10000,
                        }
                    ]
                },
            }

            def parser_review_side_effect(_user: dict) -> list[dict]:
                self._simulate_postgrest_execute(table="import_logs")
                return [review_log]

            def messages_side_effect(_ids: list[str]) -> dict[str, dict]:
                self._simulate_postgrest_execute(table="messages")
                return {"msg-1": {"raw_text": "Rolex offer"}}

            mock_parser_logs.side_effect = parser_review_side_effect
            mock_get_messages.side_effect = messages_side_effect

            client = TestClient(app)
            result = profile_page(
                client,
                page="Parser Review",
                path="/parser-review",
                page_timeout_s=5.0,
            )
        finally:
            profiler_module._original_send_with_retry = original_send
            reset_profiler_hooks()

        assert result.status == PROFILE_STATUS_OK
        assert result.query_count >= 2
        assert result.database_ms > 0
        assert result.slowest_query
        assert result.total_response_ms >= (
            result.database_ms + result.python_ms + result.render_ms - 1.0
        )

