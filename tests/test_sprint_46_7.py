"""Tests for Sprint 46.7 — AI Training Workbench."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from navigation import visible_nav_groups
from parser_review import filter_parser_review_imports, is_parser_review_pending, parser_review_counts
from parser_workbench import (
    apply_workbench_fix,
    determine_primary_fix_action,
    enrich_workbench_row,
    reprocess_import_log,
)
from permissions import can_view_page
from tests.conftest import ADMIN_USER, TRADER_ONE

pytestmark = pytest.mark.no_auto_login


def _review_import(
    *,
    import_id: str = "log-1",
    message_id: str = "msg-1",
    watches: list[dict] | None = None,
    parser_review_ignored: bool = False,
    workbench_fix_applied: bool = False,
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
        "rows": list(parsed_watches),
    }
    if parser_review_ignored:
        summary["parser_review_ignored"] = True
    if workbench_fix_applied:
        summary["workbench_fix_applied"] = True
    return {
        "id": import_id,
        "message_id": message_id,
        "status": status,
        "watches_parsed": len(parsed_watches),
        "import_time": "2026-06-25T10:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_whatsapp": "+85200000000",
        "dealer_alias": "Dealer A",
        "summary": summary,
    }


class TestAiWorkbenchAccess:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._parser_review_import_logs", return_value=[])
    @patch("database.get_messages_by_ids", return_value={})
    def test_admin_sees_ai_workbench(
        self,
        _mock_messages: MagicMock,
        _mock_logs: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert "Parser Training Center" in response.text

    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_cannot_access_ai_workbench(self, _mock_user: MagicMock) -> None:
        assert can_view_page(TRADER_ONE, "/parser-review") is False

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 403

    def test_admin_nav_shows_ai_workbench_label(self) -> None:
        groups = visible_nav_groups(ADMIN_USER)
        ai_group = next(group for group in groups if group["label"] == "AI")
        labels = {link["label"] for link in ai_group["links"]}

        assert "Parser Training Center" in labels
        assert "Unknown Brands" in labels
        assert "Unknown Nicknames" in labels


class TestWorkbenchFixActions:
    def test_determine_primary_fix_action_priority(self) -> None:
        assert determine_primary_fix_action({"missing_price", "unknown_brand"}) == "unknown_brand"
        assert determine_primary_fix_action({"missing_condition", "missing_price"}) == "missing_condition"

    def test_enrich_workbench_row_enables_mark_reviewed_after_fix(self) -> None:
        import_log = _review_import(workbench_fix_applied=True)
        row = enrich_workbench_row(
            {
                "issues": ["missing_reference"],
                "issue_labels": ["Missing reference"],
                "original_message": "Rolex 126610LN",
            },
            import_log,
        )

        assert row["primary_fix_action"] == "missing_reference"
        assert row["can_mark_reviewed"] is True

    @patch("parser_workbench.reprocess_import_log")
    @patch("database.get_import_log")
    def test_missing_condition_fix_applies_override(
        self,
        mock_get_import_log: MagicMock,
        mock_reprocess: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _review_import()
        mock_reprocess.return_value = _review_import(status="success", workbench_fix_applied=True)

        result = apply_workbench_fix("log-1", "missing_condition", condition="New")

        assert result["status"] == "success"
        mock_reprocess.assert_called_once_with(
            "log-1",
            field_overrides={"condition": "New"},
        )

    @patch("parser_workbench.reprocess_import_log")
    @patch("database.get_import_log")
    def test_missing_price_fix_applies_override(
        self,
        mock_get_import_log: MagicMock,
        mock_reprocess: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _review_import()
        mock_reprocess.return_value = _review_import(status="success", workbench_fix_applied=True)

        apply_workbench_fix("log-1", "missing_price", price="12500", currency="USD")

        mock_reprocess.assert_called_once_with(
            "log-1",
            field_overrides={"price": "12500", "currency": "USD"},
        )

    @patch("parser_workbench.reprocess_import_log")
    @patch("parser_workbench.teach_watch_mapping_from_quick_fix")
    @patch("database.get_import_log")
    def test_unknown_nickname_fix_maps_and_reprocesses(
        self,
        mock_get_import_log: MagicMock,
        mock_teach: MagicMock,
        mock_reprocess: MagicMock,
    ) -> None:
        import_log = _review_import(
            watches=[{"brand": "Rolex", "reference": None, "model": "Hulk", "condition": "New"}]
        )
        mock_get_import_log.return_value = import_log
        mock_reprocess.return_value = _review_import(status="success", workbench_fix_applied=True)

        apply_workbench_fix(
            "log-1",
            "unknown_model",
            brand_name="Rolex",
            reference="116610LV",
            alias_text="Hulk",
            model="Submariner",
        )

        mock_teach.assert_called_once()
        mock_reprocess.assert_called_once_with(
            "log-1",
            field_overrides={
                "brand": "Rolex",
                "reference": "116610LV",
                "model": "Submariner",
            },
        )

    @patch("parser_workbench.reprocess_import_log")
    @patch("database.create_brand_alias")
    @patch("database.watch_knowledge_supported", return_value=True)
    @patch("database.get_import_log")
    def test_unknown_brand_fix_adds_alias_and_reprocesses(
        self,
        mock_get_import_log: MagicMock,
        _mock_supported: MagicMock,
        mock_create_alias: MagicMock,
        mock_reprocess: MagicMock,
    ) -> None:
        import_log = _review_import(
            watches=[{"brand": None, "source_line": "Cubitus blue", "original_price": 40000}]
        )
        mock_get_import_log.return_value = import_log
        mock_reprocess.return_value = _review_import(status="success", workbench_fix_applied=True)

        apply_workbench_fix(
            "log-1",
            "unknown_brand",
            brand_name="Patek Philippe",
            alias_text="Cubitus",
        )

        mock_create_alias.assert_called_once()
        mock_reprocess.assert_called_once_with("log-1")

    @patch("database.patch_import_log")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("watch_parser.parse_message")
    def test_reprocessed_success_leaves_review_queue(
        self,
        mock_parse_message: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        mock_patch_import_log: MagicMock,
    ) -> None:
        import_log = _review_import(
            watches=[{"brand": "Rolex", "reference": None, "condition": "New"}]
        )
        mock_get_import_log.return_value = import_log
        mock_get_message.return_value = {"id": "msg-1", "raw_text": "Rolex Submariner 12500 USD"}
        mock_parse_message.return_value = {
            "message_type": "offer",
            "watches": [{"brand": "Rolex", "reference": "126610LN", "condition": "New", "original_price": 12500, "original_currency": "USD"}],
        }
        mock_patch_import_log.return_value = _review_import(
            status="success",
            watches=[{"brand": "Rolex", "reference": "126610LN", "condition": "New", "original_price": 12500, "original_currency": "USD"}],
            workbench_fix_applied=True,
        )

        updated = reprocess_import_log(
            "log-1",
            field_overrides={"reference": "126610LN", "price": "12500", "currency": "USD"},
        )

        assert updated["status"] == "success"
        assert is_parser_review_pending(updated) is False
        assert filter_parser_review_imports([updated]) == []


class TestWorkbenchRoutes:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.apply_workbench_fix_and_finalize")
    @patch("app.get_import_log")
    def test_fix_route_reprocesses_import(
        self,
        mock_get_import_log: MagicMock,
        mock_apply_fix: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _review_import()
        mock_apply_fix.return_value = _review_import(status="success", workbench_fix_applied=True)

        client = TestClient(app)
        response = client.post(
            "/parser-review/log-1/fix",
            data={"fix_action": "missing_condition", "condition": "Pre-Owned"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/parser-review?fixed=1"
        mock_apply_fix.assert_called_once()

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.get_import_log")
    def test_mark_reviewed_requires_prior_fix(
        self,
        mock_get_import_log: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _review_import()

        client = TestClient(app)
        response = client.post("/parser-review/log-1/reviewed")

        assert response.status_code == 400

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.mark_import_parser_reviewed")
    @patch("app.get_import_log")
    def test_mark_reviewed_after_fix(
        self,
        mock_get_import_log: MagicMock,
        mock_mark_reviewed: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _review_import(workbench_fix_applied=True)

        client = TestClient(app)
        response = client.post("/parser-review/log-1/reviewed", follow_redirects=False)

        assert response.status_code == 303
        mock_mark_reviewed.assert_called_once_with("log-1")

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.mark_import_parser_issue_ignored")
    @patch("app.get_import_log")
    def test_ignore_issue_stores_reason(
        self,
        mock_get_import_log: MagicMock,
        mock_ignore: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _review_import()
        ignored_log = _review_import(parser_review_ignored=True)
        mock_ignore.return_value = ignored_log

        client = TestClient(app)
        response = client.post(
            "/parser-review/log-1/ignore",
            data={"reason": "Duplicate noise"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        mock_ignore.assert_called_once_with("log-1", reason="Duplicate noise")
        assert is_parser_review_pending(ignored_log) is False

    def test_parser_review_counts_include_missing_condition(self) -> None:
        logs = [
            _review_import(
                import_id="cond",
                watches=[
                    {
                        "brand": "Rolex",
                        "reference": "126610LN",
                        "model": "Submariner",
                        "original_price": 12500,
                        "original_currency": "USD",
                    }
                ],
            )
        ]

        counts = parser_review_counts(logs)
        assert counts["missing_condition"] == 1
