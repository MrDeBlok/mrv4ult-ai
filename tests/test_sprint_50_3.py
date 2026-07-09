"""Tests for Sprint 50.3 — paginated parser training overview performance."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from parser_training_center import (
    PARSER_TRAINING_PAGE_SIZE,
    load_parser_training_overview_page,
    load_parser_training_rows_for_import,
    parser_training_page_url,
)
from tests.conftest import ADMIN_USER

pytestmark = pytest.mark.no_auto_login

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _import_log(
    *,
    import_id: str = IMPORT_LOG_ID,
    watches_count: int = 3,
    import_time: str = "2026-06-27T12:00:00+00:00",
) -> dict:
    return {
        "id": import_id,
        "import_time": import_time,
        "summary": {"offer_watches": [{}] * watches_count, "message_type": "offer"},
        "watches_parsed": watches_count,
        "dealer_alias": "Dealer A",
        "group_name": "HK Dealers",
        "status": "success",
    }


def _many_import_logs(count: int) -> list[dict]:
    return [
        _import_log(
            import_id=f"import-{index:04d}",
            watches_count=2,
            import_time=f"2026-06-{27 - (index % 20):02d}T12:00:00+00:00",
        )
        for index in range(count)
    ]


def _summary_for(import_id: str, *, pending: int = 0, approved: int = 2) -> dict:
    return {
        "import_log_id": import_id,
        "total_rows": pending + approved,
        "approved_rows": approved,
        "pending_review_rows": pending,
        "ignored_rows": 0,
        "failed_rows": 0,
        "corrected_rows": 0,
    }


@pytest.fixture
def mock_visible_imports():
    with patch(
        "parser_training_center._visible_parser_training_import_logs",
        side_effect=lambda logs, _user: logs,
    ) as mock_visible:
        yield mock_visible


class TestPaginatedOverview:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("database.list_parser_training_import_summaries")
    @patch("database.list_parser_training_import_logs")
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    def test_get_parser_training_loads_at_most_25_imports_by_default(
        self,
        _schema: MagicMock,
        _supported: MagicMock,
        mock_import_logs: MagicMock,
        mock_summaries: MagicMock,
        _mock_user: MagicMock,
        mock_visible_imports: MagicMock,
    ) -> None:
        mock_import_logs.return_value = _many_import_logs(60)
        mock_summaries.side_effect = lambda ids: [
            _summary_for(import_id) for import_id in ids
        ]

        client = TestClient(app)
        response = client.get("/parser-training")

        assert response.status_code == 200
        mock_import_logs.assert_called_once()
        assert mock_import_logs.call_args.kwargs["limit"] <= 75
        mock_summaries.assert_called_once()
        assert len(mock_summaries.call_args[0][0]) <= PARSER_TRAINING_PAGE_SIZE

    @patch("database.list_parser_training_import_summaries")
    @patch("database.list_parser_training_import_logs")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_overview_queries_rows_only_for_visible_imports(
        self,
        _supported: MagicMock,
        mock_import_logs: MagicMock,
        mock_summaries: MagicMock,
        mock_visible_imports: MagicMock,
    ) -> None:
        mock_import_logs.return_value = _many_import_logs(40)
        mock_summaries.side_effect = lambda ids: [
            _summary_for(import_id) for import_id in ids
        ]

        result = load_parser_training_overview_page(
            ADMIN_USER,
            page=1,
            filter_name="all",
            format_timestamp=lambda value: value or "",
        )

        assert len(result.containers) <= PARSER_TRAINING_PAGE_SIZE
        mock_summaries.assert_called_once()
        queried_ids = mock_summaries.call_args[0][0]
        assert len(queried_ids) <= PARSER_TRAINING_PAGE_SIZE

    @patch("database.get_client")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_overview_does_not_scan_rows_with_high_offset(
        self,
        _supported: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        table = MagicMock()
        query = MagicMock()
        mock_get_client.return_value.table.return_value = table
        table.select.return_value = query
        query.in_.return_value = query
        query.range.return_value.execute.return_value = MagicMock(data=[])

        from database import list_parser_training_import_summaries

        list_parser_training_import_summaries(["import-0001", "import-0002"])

        query.range.assert_called_once_with(0, 999)

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("database.list_parser_training_import_summaries")
    @patch("database.list_parser_training_import_logs")
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    def test_next_page_returns_later_imports(
        self,
        _schema: MagicMock,
        _supported: MagicMock,
        mock_import_logs: MagicMock,
        mock_summaries: MagicMock,
        _mock_user: MagicMock,
        mock_visible_imports: MagicMock,
    ) -> None:
        imports = _many_import_logs(50)
        mock_import_logs.return_value = imports
        mock_summaries.side_effect = lambda ids: [
            _summary_for(import_id) for import_id in ids
        ]

        client = TestClient(app)
        page_one = client.get("/parser-training")
        page_two = client.get("/parser-training?page=2")

        assert page_one.status_code == 200
        assert page_two.status_code == 200
        assert "page=2" in parser_training_page_url(2)
        assert page_one.text != page_two.text

    @patch("database.list_parser_training_import_summaries")
    @patch("database.list_parser_training_import_logs")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_pending_filter_shows_only_imports_with_pending_rows(
        self,
        _supported: MagicMock,
        mock_import_logs: MagicMock,
        mock_summaries: MagicMock,
        mock_visible_imports: MagicMock,
    ) -> None:
        imports = _many_import_logs(10)
        mock_import_logs.return_value = imports
        mock_summaries.side_effect = lambda ids: [
            _summary_for(
                import_id,
                pending=1 if import_id.endswith("0003") else 0,
            )
            for import_id in ids
        ]

        result = load_parser_training_overview_page(
            ADMIN_USER,
            page=1,
            filter_name="pending",
            format_timestamp=lambda value: value or "",
        )

        assert len(result.containers) == 1
        assert result.containers[0]["pending_review_rows"] == 1

    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_row_detail_still_loads_all_rows_for_one_import(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
    ) -> None:
        mock_list_rows.return_value = [
            {"id": f"row-{index}", "row_index": index, "status": "approved"}
            for index in range(120)
        ]

        rows, stats = load_parser_training_rows_for_import(_import_log(watches_count=120))

        assert len(rows) == 120
        assert stats["total_rows"] == 120
        mock_list_rows.assert_called_once_with(IMPORT_LOG_ID)
