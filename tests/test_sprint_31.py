"""Tests for Sprint 31 parser review center."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from parser_review import (
    build_parser_review_row,
    detect_import_issues,
    detect_watch_issues,
    filter_parser_review_by_issue,
    filter_parser_review_imports,
    is_parser_review_pending,
    parser_review_counts,
)


def _review_import(
    *,
    import_id: str = "log-1",
    watches: list[dict] | None = None,
    parser_reviewed: bool = False,
    parser_review_ignored: bool = False,
    status: str = "warning",
) -> dict:
    parsed_watches = watches or [
        {
            "brand": "Rolex",
            "reference": None,
            "model": "Submariner",
            "original_price": 12500,
            "original_currency": "USD",
        }
    ]
    summary: dict = {
        "status_reason": "Important fields are missing — watch 1: missing reference",
        "parsed_watches": parsed_watches,
    }
    if parser_reviewed:
        summary["parser_reviewed"] = True
    if parser_review_ignored:
        summary["parser_review_ignored"] = True
    return {
        "id": import_id,
        "status": status,
        "watches_parsed": len(parsed_watches),
        "message_id": "msg-1",
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+85291234567",
        "import_time": "2026-06-27T12:00:00+00:00",
        "summary": summary,
    }


class TestParserReviewIssueGrouping:
    def test_detect_watch_issues_groups_missing_reference(self) -> None:
        issues, missing = detect_watch_issues(
            {
                "brand": "Rolex",
                "reference": None,
                "original_price": 12500,
                "condition": "New",
            }
        )

        assert "missing_reference" in issues
        assert "reference" in missing

    def test_detect_watch_issues_groups_unknown_brand(self) -> None:
        issues, _missing = detect_watch_issues(
            {
                "brand": None,
                "source_line": "Cubitus blue dial 40000",
                "original_price": 40000,
            }
        )

        assert "unknown_brand" in issues
        assert "missing_brand" in issues

    def test_detect_watch_issues_groups_unknown_model(self) -> None:
        issues, _missing = detect_watch_issues(
            {
                "brand": "Rolex",
                "model": None,
                "reference": "126610LN",
            }
        )

        assert "unknown_model" in issues

    def test_detect_watch_issues_groups_multiple_fields_missing(self) -> None:
        issues, _missing = detect_watch_issues(
            {
                "brand": None,
                "reference": None,
                "source_line": "Offer line without details",
            }
        )

        assert "multiple_fields_missing" in issues

    def test_detect_import_issues_collects_unknown_brand_text(self) -> None:
        import_log = _review_import(
            watches=[
                {
                    "brand": None,
                    "source_line": "Cubitus blue dial 40000",
                    "original_price": 40000,
                }
            ]
        )

        issues, missing, unknown_text = detect_import_issues(import_log)

        assert "unknown_brand" in issues
        assert "price" not in missing
        assert unknown_text == "Cubitus"


class TestParserReviewFiltersAndCounts:
    def test_filter_parser_review_imports_excludes_reviewed_and_ignored(self) -> None:
        logs = [
            _review_import(import_id="pending"),
            _review_import(import_id="reviewed", parser_reviewed=True),
            _review_import(import_id="ignored", parser_review_ignored=True),
            _review_import(import_id="success", status="success"),
        ]

        pending = filter_parser_review_imports(logs)

        assert [row["id"] for row in pending] == ["pending"]

    def test_filter_parser_review_by_issue(self) -> None:
        logs = [
            _review_import(
                import_id="missing-price",
                watches=[{"brand": "Rolex", "reference": "126610LN", "condition": "New"}],
            ),
            _review_import(
                import_id="missing-reference",
                watches=[
                    {
                        "brand": "Rolex",
                        "reference": None,
                        "original_price": 12500,
                        "condition": "New",
                    }
                ],
            ),
        ]

        filtered = filter_parser_review_by_issue(logs, "missing_price")

        assert [row["id"] for row in filtered] == ["missing-price"]

    def test_parser_review_counts(self) -> None:
        logs = [
            _review_import(
                import_id="one",
                watches=[
                    {
                        "brand": None,
                        "reference": None,
                        "source_line": "Cubitus blue 40000",
                        "original_price": 40000,
                    }
                ],
            ),
            _review_import(
                import_id="two",
                watches=[
                    {
                        "brand": "Rolex",
                        "reference": None,
                        "original_price": 12500,
                        "condition": "New",
                    }
                ],
            ),
        ]

        assert parser_review_counts(logs) == {
            "total": 2,
            "missing_price": 0,
            "missing_brand": 1,
            "missing_reference": 2,
            "missing_condition": 1,
            "unknown_brand": 1,
            "unknown_reference": 0,
        }


class TestParserReviewRow:
    def test_build_parser_review_row_includes_detail_link_and_fields(self) -> None:
        import_log = _review_import()
        row = build_parser_review_row(
            import_log,
            {"raw_text": "Rolex Submariner 12500 USD"},
            format_timestamp=lambda value: value or "N/A",
        )

        assert row["detail_url"] == "/activity/log-1"
        assert row["dealer"] == "Dealer A"
        assert row["group_name"] == "HK Dealers"
        assert "Rolex Submariner 12500 USD" in row["original_message"]
        assert "Reference" in row["missing_fields"]
        assert any(entry.startswith("Brand:") for entry in row["parsed_fields"])


class TestParserReviewDatabaseActions:
    @patch("database.get_client")
    @patch("database.get_import_log")
    def test_mark_import_parser_reviewed(
        self,
        mock_get_import_log: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from database import mark_import_parser_reviewed

        mock_get_import_log.return_value = _review_import()
        mock_table = MagicMock()
        mock_update = MagicMock()
        mock_eq = MagicMock()
        mock_get_client.return_value.table.return_value = mock_table
        mock_table.update.return_value = mock_update
        mock_update.eq.return_value = mock_eq
        mock_eq.execute.return_value = MagicMock(
            data=[{"id": "log-1", "status": "success", "summary": {"parser_reviewed": True}}]
        )

        row = mark_import_parser_reviewed("log-1")

        assert row["status"] == "success"
        update_payload = mock_table.update.call_args.args[0]
        assert update_payload["summary"]["parser_reviewed"] is True

    @patch("database.get_client")
    @patch("database.get_import_log")
    def test_mark_import_parser_issue_ignored(
        self,
        mock_get_import_log: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from database import mark_import_parser_issue_ignored

        mock_get_import_log.return_value = _review_import()
        mock_table = MagicMock()
        mock_update = MagicMock()
        mock_eq = MagicMock()
        mock_get_client.return_value.table.return_value = mock_table
        mock_table.update.return_value = mock_update
        mock_update.eq.return_value = mock_eq
        mock_eq.execute.return_value = MagicMock(
            data=[{"id": "log-1", "summary": {"parser_review_ignored": True}}]
        )

        mark_import_parser_issue_ignored("log-1")

        update_payload = mock_table.update.call_args.args[0]
        assert update_payload["summary"]["parser_review_ignored"] is True


class TestParserReviewPage:
    @patch("database.get_messages_by_ids")
    @patch("app._parser_review_import_logs")
    def test_parser_review_page_renders(
        self,
        mock_import_logs: MagicMock,
        mock_get_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = [_review_import()]
        mock_get_messages.return_value = {"msg-1": {"raw_text": "Rolex Submariner offer"}}

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert "Parser Training Center" in response.text
        assert "Needs review:" in response.text
        assert "Missing reference" in response.text
        assert "Rolex Submariner offer" in response.text
        assert "/activity/log-1" in response.text

    @patch("database.get_messages_by_ids")
    @patch("app._parser_review_import_logs")
    def test_parser_review_filters_work(
        self,
        mock_import_logs: MagicMock,
        mock_get_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = [
            _review_import(
                import_id="missing-price",
                watches=[{"brand": "Rolex", "reference": "126610LN", "condition": "New"}],
            ),
            _review_import(import_id="missing-reference"),
        ]
        mock_get_messages.return_value = {"msg-1": {"raw_text": "Sample message"}}

        client = TestClient(app)
        response = client.get("/parser-review?filter=missing_price")

        assert response.status_code == 200
        assert response.text.count("Open import detail") == 1
        assert "Missing price" in response.text
        assert "/parser-review/missing-reference/" not in response.text

    @patch("database.get_messages_by_ids")
    @patch("app._parser_review_import_logs")
    def test_parser_review_dashboard_counts_work(
        self,
        mock_import_logs: MagicMock,
        mock_get_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = [
            _review_import(
                import_id="one",
                watches=[
                    {
                        "brand": None,
                        "reference": None,
                        "source_line": "Cubitus blue 40000",
                        "original_price": 40000,
                    }
                ],
            )
        ]
        mock_get_messages.return_value = {"msg-1": {"raw_text": "Cubitus blue 40000"}}

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert "<strong>Needs review:</strong> 1" in response.text
        assert "<strong>AI Health:</strong>" in response.text
        assert "<strong>Unknown brand:</strong> 1" in response.text

    @patch("app.mark_import_parser_reviewed")
    @patch("app.get_import_log")
    def test_mark_as_reviewed_works(
        self,
        mock_get_import_log: MagicMock,
        mock_mark_reviewed: MagicMock,
    ) -> None:
        import_log = _review_import()
        import_log["summary"]["workbench_fix_applied"] = True
        mock_get_import_log.return_value = import_log

        client = TestClient(app)
        response = client.post("/parser-review/log-1/reviewed", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/parser-review?reviewed=1"
        mock_mark_reviewed.assert_called_once_with("log-1")

    @patch("app.mark_import_parser_issue_ignored")
    @patch("app.get_import_log")
    def test_ignore_issue_works(
        self,
        mock_get_import_log: MagicMock,
        mock_ignore: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _review_import()

        client = TestClient(app)
        response = client.post("/parser-review/log-1/ignore", follow_redirects=False)

        assert response.status_code == 303
        mock_ignore.assert_called_once_with("log-1", reason="")

    def test_ignored_issue_disappears_from_default_review_list(self) -> None:
        logs = [
            _review_import(import_id="visible"),
            _review_import(import_id="hidden", parser_review_ignored=True),
        ]

        assert is_parser_review_pending(logs[0]) is True
        assert is_parser_review_pending(logs[1]) is False
        assert [row["id"] for row in filter_parser_review_imports(logs)] == ["visible"]

    @patch("database.get_messages_by_ids")
    @patch("app._parser_review_import_logs")
    def test_import_detail_links_work(
        self,
        mock_import_logs: MagicMock,
        mock_get_messages: MagicMock,
    ) -> None:
        mock_import_logs.return_value = [_review_import(import_id="detail-log")]
        mock_get_messages.return_value = {"msg-1": {"raw_text": "Detail message"}}

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert 'href="/activity/detail-log"' in response.text
        assert "Open import detail" in response.text

    @patch("app.get_import_log")
    def test_parser_review_mark_reviewed_returns_404_for_missing_import(
        self,
        mock_get_import_log: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = None

        client = TestClient(app)
        response = client.post("/parser-review/missing/reviewed")

        assert response.status_code == 404
