"""Tests for Sprint 50.2.2 — read-only parser training page rendering."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from parser_training_center import load_parser_training_containers
from database import parser_training_rows_write_guard
from parser_training_engine import (
    build_container_summary_for_import,
    re_evaluate_parser_training_import,
)
from tests.conftest import ADMIN_USER

pytestmark = pytest.mark.no_auto_login

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _import_log() -> dict:
    return {
        "id": IMPORT_LOG_ID,
        "import_time": "2026-06-27T12:00:00+00:00",
        "summary": {"offer_watches": [{}], "message_type": "offer"},
        "watches_parsed": 1,
        "dealer_alias": "Dealer A",
        "group_name": "HK Dealers",
    }


class TestReadOnlyPageRendering:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("database.list_parser_training_import_logs", return_value=[_import_log()])
    @patch("parser_training_center._visible_parser_training_import_logs", side_effect=lambda logs, _user: logs)
    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_import_summaries")
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("database.attach_import_log_summaries", side_effect=lambda logs: logs)
    def test_get_parser_training_performs_zero_updates(
        self,
        _attach: MagicMock,
        _supported: MagicMock,
        mock_summaries: MagicMock,
        mock_update: MagicMock,
        _mock_visible: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_summaries.return_value = [
            {
                "import_log_id": IMPORT_LOG_ID,
                "total_rows": 3,
                "approved_rows": 2,
                "pending_review_rows": 1,
                "ignored_rows": 0,
                "failed_rows": 0,
                "corrected_rows": 0,
            }
        ]

        client = TestClient(app)
        response = client.get("/parser-training")

        assert response.status_code == 200
        mock_update.assert_not_called()

    @patch("database.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("database.list_parser_training_import_summaries")
    def test_overview_handles_twenty_thousand_rows_without_updates(
        self,
        mock_summaries: MagicMock,
        _supported: MagicMock,
        _attach: MagicMock,
    ) -> None:
        mock_summaries.return_value = [
            {
                "import_log_id": IMPORT_LOG_ID,
                "total_rows": 20_000,
                "approved_rows": 18_000,
                "pending_review_rows": 2_000,
                "ignored_rows": 0,
                "failed_rows": 0,
                "corrected_rows": 0,
            }
        ]

        started = time.perf_counter()
        containers, totals = load_parser_training_containers(
            [_import_log()],
            format_timestamp=lambda value: value or "",
        )
        elapsed = time.perf_counter() - started

        assert elapsed < 2.0
        assert totals["total_rows"] == 20_000
        assert totals["approved_rows"] == 18_000
        assert totals["pending_review_rows"] == 2_000
        assert containers[0]["approved_rows"] == 18_000

    def test_build_container_summary_is_pure(self) -> None:
        rows = [{"status": "approved"}, {"status": "pending_review"}]
        with patch("database.update_parser_training_row") as mock_update:
            summary = build_container_summary_for_import(rows, import_log_id=IMPORT_LOG_ID)

        assert summary["total_rows"] == 2
        assert summary["approved_rows"] == 1
        assert summary["pending_review_rows"] == 1
        mock_update.assert_not_called()

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_import_log")
    @patch("app.get_message_by_id")
    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_get_parser_training_rows_performs_zero_updates(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
        mock_update: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import.return_value = {
            "id": IMPORT_LOG_ID,
            "message_id": "msg-1",
            "summary": {"message_type": "offer"},
        }
        mock_get_message.return_value = {"id": "msg-1", "raw_text": "test"}
        mock_list_rows.return_value = [
            {
                "id": "row-1",
                "row_index": 0,
                "status": "approved",
                "raw_row_text": "5524G 380000hkd",
                "detected_reference": "5524G",
                "detected_price": 380000,
                "detected_currency": "HKD",
            }
        ]

        client = TestClient(app)
        response = client.get(f"/parser-training/{IMPORT_LOG_ID}/rows")

        assert response.status_code == 200
        mock_update.assert_not_called()


class TestExplicitReEvaluate:
    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_rows_for_import")
    def test_manual_re_evaluate_updates_rows(
        self,
        mock_list_rows: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        mock_list_rows.return_value = [
            {
                "id": "row-1",
                "import_log_id": IMPORT_LOG_ID,
                "status": "pending_review",
                "detected_reference": "5524G",
                "detected_price": 380000,
                "detected_currency": "HKD",
            }
        ]
        mock_update.return_value = {"id": "row-1", "status": "approved"}

        with (
            patch(
                "parser_training_engine.compute_training_row_updates",
                return_value={"status": "approved", "issue_types": []},
            ),
            parser_training_rows_write_guard(),
        ):
            result = re_evaluate_parser_training_import(
                IMPORT_LOG_ID,
                message_type="offer",
            )

        assert result["rows_checked"] == 1
        assert result["rows_updated"] == 1
        mock_update.assert_called_once()

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.re_evaluate_parser_training_imports")
    @patch("app._parser_training_import_logs", return_value=[_import_log()])
    def test_post_re_evaluate_recent_redirects(
        self,
        _mock_logs: MagicMock,
        mock_re_eval: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_re_eval.return_value = {
            "imports_processed": 1,
            "rows_checked": 10,
            "rows_updated": 4,
        }

        client = TestClient(app)
        response = client.post(
            "/parser-training/re-evaluate-recent",
            data={"limit": "50"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "reevaluate=1" in response.headers["location"]
        mock_re_eval.assert_called_once()

    def test_update_parser_training_row_blocked_without_write_guard(self) -> None:
        from database import update_parser_training_row

        with pytest.raises(RuntimeError, match="Blocked parser_training_rows update"):
            update_parser_training_row("row-1", status="approved")
