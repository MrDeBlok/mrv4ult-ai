"""Tests for Sprint 43.7 lightweight import_logs list projections."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_activity_row
from database import (
    attach_import_log_summaries,
    get_import_log,
    import_log_detail_columns_full,
    import_log_list_columns_light,
    list_activity_import_logs,
    list_parser_review_import_log_candidates,
)
from parser_review import load_parser_review_page_data
from tests.conftest import ADMIN_USER


class TestImportLogColumnProjections:
    def test_list_projection_omits_summary(self) -> None:
        columns = import_log_list_columns_light()
        assert "summary" not in columns
        assert "message_id" in columns

    def test_detail_projection_includes_summary(self) -> None:
        columns = import_log_detail_columns_full()
        assert "summary" in columns

    @patch("database.get_client")
    @patch("database.import_log_list_columns_light", return_value="id,status,watches_parsed")
    def test_activity_list_query_uses_light_columns(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_execute = MagicMock()
        mock_execute.data = []
        mock_query = MagicMock()
        mock_query.execute.return_value = mock_execute
        mock_query.neq.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_table = MagicMock()
        mock_table.select.return_value.order.return_value = mock_query
        mock_get_client.return_value.table.return_value = mock_table

        list_activity_import_logs(tab="active", offset=0, limit=20)

        mock_table.select.assert_called_once_with("id,status,watches_parsed")

    @patch("database.get_client")
    @patch("database.import_log_detail_columns_full", return_value="id,summary,status")
    def test_get_import_log_uses_full_columns(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_execute = MagicMock()
        mock_execute.data = [{"id": "log-1", "summary": {"parsed_watches": []}}]
        mock_query = MagicMock()
        mock_query.execute.return_value = mock_execute
        mock_query.eq.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_table = MagicMock()
        mock_table.select.return_value = mock_query
        mock_get_client.return_value.table.return_value = mock_table

        row = get_import_log("log-1")

        mock_table.select.assert_called_once_with("id,summary,status")
        assert row is not None
        assert "summary" in row

    @patch("database.get_client")
    @patch("database.import_log_list_columns_light", return_value="id,status")
    def test_parser_review_candidates_use_light_columns(
        self,
        _mock_columns: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_execute = MagicMock()
        mock_execute.data = []
        mock_query = MagicMock()
        mock_query.execute.return_value = mock_execute
        mock_query.eq.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_table = MagicMock()
        mock_table.select.return_value.order.return_value = mock_query
        mock_get_client.return_value.table.return_value = mock_table

        list_parser_review_import_log_candidates()

        mock_table.select.assert_called_once_with("id,status")


class TestAttachImportLogSummaries:
    @patch(
        "database.get_import_log_summaries_by_ids",
        return_value={"log-1": {"parsed_watches": [{"brand": "Rolex"}]}},
    )
    def test_attach_import_log_summaries_batch_loads_summary(
        self,
        mock_summaries: MagicMock,
    ) -> None:
        rows = attach_import_log_summaries(
            [{"id": "log-1", "status": "warning", "watches_parsed": 1}]
        )

        mock_summaries.assert_called_once_with(["log-1"])
        assert rows[0]["summary"]["parsed_watches"][0]["brand"] == "Rolex"

    @patch("database.get_import_log_summaries_by_ids")
    def test_attach_skips_when_summary_already_present(
        self,
        mock_summaries: MagicMock,
    ) -> None:
        rows = attach_import_log_summaries(
            [{"id": "log-1", "summary": {"parsed_watches": []}, "status": "warning"}]
        )

        mock_summaries.assert_not_called()
        assert rows[0]["summary"] == {"parsed_watches": []}


class TestActivityLightRows:
    def test_activity_row_renders_without_summary(self) -> None:
        row = build_activity_row(
            {
                "id": "log-1",
                "import_time": "2026-06-27T12:00:00+00:00",
                "group_name": "HK Dealers",
                "dealer_alias": "Dealer A",
                "dealer_whatsapp": "+85291234567",
                "watches_parsed": 1,
                "new_offers": 1,
                "duplicate_offers": 0,
                "matched_requests": 0,
                "processing_time": "120 ms",
                "status": "success",
            }
        )

        assert row["id"] == "log-1"
        assert row["watches_parsed"] == 1
        assert row["status"] == "Success"

    @patch("app.load_trading_desk")
    def test_activity_page_renders_with_light_import_rows(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "matched_requests": [],
            "todays_best_deals": [],
            "ai_needs_help": [],
            "live_market": [],
            "show_write_actions": True,
        }

        client = TestClient(app)
        with patch(
            "app.load_activity_page",
            return_value=type(
                "ActivityPage",
                (),
                {
                    "imports": [
                        {
                            "id": "log-1",
                            "import_time": "2026-06-27 12:00",
                            "group_name": "HK",
                            "dealer_alias": "Dealer",
                            "dealer_whatsapp": "+1",
                            "watches_parsed": 1,
                            "new_offers": 1,
                            "duplicate_offers": 0,
                            "matched_requests": 0,
                            "processing_time": "100 ms",
                            "status": "Success",
                            "status_class": "success",
                        }
                    ],
                    "stats": {"offers": 1, "needs_review": 0, "ignored": 0},
                    "page": 1,
                    "page_size": 20,
                    "has_previous": False,
                    "has_next": False,
                    "showing_from": 1,
                    "showing_to": 1,
                    "empty_message": "",
                },
            )(),
        ):
            response = client.get("/activity")

        assert response.status_code == 200
        assert 'data-href="/activity/log-1"' in response.text


class TestParserReviewSummaryBatch:
    @patch("database.get_messages_by_ids", return_value={"msg-1": {"raw_text": "Rolex 126200"}})
    @patch(
        "database.attach_import_log_summaries",
        side_effect=lambda logs: [
            {
                **log,
                "summary": {
                    "parsed_watches": [
                        {
                            "brand": "Rolex",
                            "model": "Datejust",
                            "reference": None,
                            "original_price": None,
                        }
                    ]
                },
            }
            for log in logs
        ],
    )
    def test_parser_review_batch_attaches_summary_before_issue_detection(
        self,
        mock_attach: MagicMock,
        _mock_messages: MagicMock,
    ) -> None:
        import_log = {
            "id": "log-1",
            "status": "warning",
            "watches_parsed": 1,
            "message_id": "msg-1",
            "group_name": "HK",
            "dealer_alias": "Dealer",
            "dealer_whatsapp": "+1",
            "import_time": "2026-06-27T12:00:00+00:00",
        }

        rows, counts = load_parser_review_page_data(
            [import_log],
            "all",
            format_timestamp=lambda value: value or "N/A",
        )

        mock_attach.assert_called_once()
        assert counts["total"] == 1
        assert rows
        assert "missing_price" in rows[0]["issues"]
        assert "Missing price" in rows[0]["issue_labels"]
