"""Tests for Sprint 43.2 parser review N+1 elimination."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from parser_review import load_parser_review_page_data
from performance_profiler import (
    PROFILE_STATUS_OK,
    SPRINT_43_0_PARSER_REVIEW_QUERY_COUNT,
    SPRINT_43_2_PARSER_REVIEW_BASELINE_MS,
    PageProfileResult,
)
from tests.test_sprint_31 import _review_import


class TestParserReviewBatchLoading:
    @patch("database.get_messages_by_ids")
    def test_load_parser_review_page_data_batches_messages_once(
        self,
        mock_get_messages: MagicMock,
    ) -> None:
        logs = [
            _review_import(
                import_id="log-1",
                watches=[{"brand": "Rolex", "reference": None, "condition": "New", "original_price": 10000}],
            ),
            _review_import(
                import_id="log-2",
                watches=[{"brand": "Omega", "reference": None, "condition": "New", "original_price": 5000}],
            ),
        ]
        logs[0]["message_id"] = "msg-1"
        logs[1]["message_id"] = "msg-2"
        mock_get_messages.return_value = {
            "msg-1": {"raw_text": "Rolex without ref"},
            "msg-2": {"raw_text": "Rolex with ref"},
        }

        rows, counts = load_parser_review_page_data(
            logs,
            "all",
            format_timestamp=lambda value: value,
        )

        mock_get_messages.assert_called_once_with(["msg-1", "msg-2"])
        assert counts["total"] == 2
        assert len(rows) == 2
        assert rows[0]["original_message"] == "Rolex without ref"
        assert rows[1]["original_message"] == "Rolex with ref"

    @patch("database.get_messages_by_ids")
    def test_filtered_parser_review_only_loads_visible_message_ids(
        self,
        mock_get_messages: MagicMock,
    ) -> None:
        logs = [
            _review_import(
                import_id="missing-price",
                watches=[{"brand": "Rolex", "reference": "126610LN", "condition": "New"}],
            ),
            _review_import(import_id="missing-reference"),
        ]
        logs[0]["message_id"] = "msg-price"
        logs[1]["message_id"] = "msg-reference"
        mock_get_messages.return_value = {"msg-price": {"raw_text": "priced"}}

        rows, counts = load_parser_review_page_data(
            logs,
            "missing_price",
            format_timestamp=lambda value: value,
        )

        mock_get_messages.assert_called_once_with(["msg-price"])
        assert counts["total"] == 2
        assert len(rows) == 1
        assert rows[0]["id"] == "missing-price"

    @patch("database.get_messages_by_ids", return_value={})
    def test_issue_detection_runs_once_per_import_log(
        self,
        _mock_get_messages: MagicMock,
    ) -> None:
        logs = [_review_import(import_id="log-1")]
        with patch(
            "parser_review.detect_import_issues",
            wraps=__import__("parser_review").detect_import_issues,
        ) as mock_detect:
            load_parser_review_page_data(
                logs,
                "all",
                format_timestamp=lambda value: value,
            )

        assert mock_detect.call_count == 1


class TestParserReviewProfilerBaselines:
    def test_parser_review_baseline_constants(self) -> None:
        assert SPRINT_43_0_PARSER_REVIEW_QUERY_COUNT == 227
        assert SPRINT_43_2_PARSER_REVIEW_BASELINE_MS == 15300.0

    def test_report_row_includes_parser_review_timing_delta(self) -> None:
        result = PageProfileResult(
            page="Parser Review",
            path="/parser-review",
            status=PROFILE_STATUS_OK,
            status_code=200,
            total_response_ms=850.0,
            python_ms=400.0,
            render_ms=50.0,
            database_ms=400.0,
            query_count=8,
            slowest_query="GET messages?select=id,raw_text",
            slowest_query_ms=120.0,
            import_logs_query_ms=80.0,
        )
        row = result.to_report_row()
        assert row["query_count_baseline"] == 227
        assert row["query_count_delta"] == 219
        assert row["parser_review_baseline_ms"] == 15300.0
        assert row["parser_review_saved_ms"] == pytest.approx(14450.0)


class TestParserReviewRouteUsesBatchLoader:
    @patch("app.load_parser_review_page_data")
    @patch("app._parser_review_import_logs")
    def test_parser_review_page_delegates_to_batch_loader(
        self,
        mock_import_logs: MagicMock,
        mock_load_page: MagicMock,
    ) -> None:
        from fastapi.testclient import TestClient

        from app import app

        mock_import_logs.return_value = [_review_import()]
        mock_load_page.return_value = (
            [{"id": "log-1", "issue_labels": ["Missing reference"]}],
            {"total": 1, "missing_price": 0, "missing_brand": 0, "missing_reference": 1, "unknown_brand": 0},
        )

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        mock_load_page.assert_called_once()
        assert mock_load_page.call_args.args[1] == "all"
