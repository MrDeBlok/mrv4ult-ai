"""Tests for Sprint 46.9 parser accuracy dashboard and failure reasons."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from parser_accuracy import load_parser_accuracy_dashboard, parser_review_business_sort_key, sort_parser_review_imports
from parser_review import (
    detect_primary_failure_reason,
    is_parser_review_pending,
    load_parser_review_page_data,
    primary_failure_label,
)
from parser_workbench import apply_workbench_fix_and_finalize
from tests.conftest import ADMIN_USER, TRADER_ONE

pytestmark = pytest.mark.no_auto_login


def _review_import(
    *,
    import_id: str,
    status: str = "warning",
    watches: list[dict] | None = None,
    import_time: str = "2026-06-25T10:00:00+00:00",
    parser_reviewed: bool = False,
    request_intent_kind: str | None = None,
) -> dict:
    parsed_watches = watches or [
        {"brand": "Rolex", "reference": None, "model": "Submariner", "original_price": 12500, "original_currency": "USD"}
    ]
    summary: dict = {
        "status_reason": "Important fields are missing",
        "parsed_watches": parsed_watches,
        "rows": list(parsed_watches),
    }
    if parser_reviewed:
        summary["parser_reviewed"] = True
    if request_intent_kind:
        summary["request_intent_kind"] = request_intent_kind
    return {
        "id": import_id,
        "status": status,
        "watches_parsed": len(parsed_watches),
        "message_id": f"msg-{import_id}",
        "import_time": import_time,
        "group_name": "HK Dealers",
        "dealer_whatsapp": "+85200000000",
        "dealer_alias": "Dealer A",
        "summary": summary,
    }


def _success_import(import_id: str, *, brand: str = "Rolex") -> dict:
    watch = {
        "brand": brand,
        "reference": "126610LN",
        "model": "Submariner",
        "condition": "New",
        "original_price": 12500,
        "original_currency": "USD",
        "usd_price": 12500,
        "confidence": 90,
    }
    return {
        "id": import_id,
        "status": "success",
        "watches_parsed": 1,
        "message_id": f"msg-{import_id}",
        "import_time": "2026-06-25T12:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_whatsapp": "+85200000000",
        "dealer_alias": "Dealer A",
        "summary": {
            "parsed_watches": [watch],
            "rows": [watch],
            "offer_watches": [watch],
        },
    }


class TestParserAccuracyMetrics:
    def test_accuracy_metrics_calculated_correctly(self) -> None:
        logs = [
            _success_import("ok-1"),
            _success_import("ok-2", brand="Patek Philippe"),
            _review_import(import_id="review-1"),
        ]
        dashboard = load_parser_accuracy_dashboard(
            logs,
            now=datetime(2026, 6, 25, 15, 0, tzinfo=timezone.utc),
        )

        assert dashboard["overall"]["total"] == 3
        assert dashboard["overall"]["fully_parsed"] == 2
        assert dashboard["overall"]["needs_review"] == 1
        assert dashboard["overall"]["accuracy_pct"] == pytest.approx(66.7, abs=0.1)
        assert dashboard["health_score"] == dashboard["overall"]["accuracy_pct"]
        assert dashboard["processing_summary"]["total_actionable"] == 3
        assert dashboard["today"]["total"] == 3
        assert dashboard["week"]["total"] == 3

    def test_training_queue_counts_primary_reasons(self) -> None:
        logs = [
            _review_import(
                import_id="brand",
                watches=[{"brand": None, "source_line": "Cubitus blue", "original_price": 40000}],
            ),
            _review_import(
                import_id="ref",
                watches=[{"brand": "Rolex", "reference": None, "original_price": 12500, "original_currency": "USD"}],
            ),
        ]
        dashboard = load_parser_accuracy_dashboard(logs)
        queue = {row["key"]: row["count"] for row in dashboard["training_queue"]}

        assert queue["unknown_brand"] == 1
        assert queue["missing_reference"] == 1

    def test_brand_accuracy_tracked_for_known_brands(self) -> None:
        logs = [
            _success_import("rm-ok", brand="Richard Mille"),
            _review_import(
                import_id="rm-review",
                watches=[{"brand": "Richard Mille", "reference": None, "usd_price": 250000}],
            ),
        ]
        dashboard = load_parser_accuracy_dashboard(logs)
        rm = next(row for row in dashboard["brand_accuracy"] if row["brand"] == "Richard Mille")

        assert rm["total"] == 2
        assert rm["fully_parsed"] == 1
        assert rm["needs_review"] == 1
        assert rm["accuracy_pct"] == 50.0


class TestFailureReasons:
    def test_needs_review_always_has_primary_reason(self) -> None:
        import_log = _review_import(
            import_id="missing-ref",
            watches=[{"brand": "Rolex", "reference": None, "original_price": 12500, "original_currency": "USD"}],
        )

        reason = detect_primary_failure_reason(import_log)

        assert reason == "missing_reference"
        assert primary_failure_label(reason) == "Missing reference"
        assert primary_failure_label(reason) != "Needs review"

    def test_unknown_brand_reason(self) -> None:
        import_log = _review_import(
            import_id="unknown-brand",
            watches=[{"brand": None, "source_line": "Cubitus 40000", "original_price": 40000}],
        )

        assert detect_primary_failure_reason(import_log) == "unknown_brand"

    def test_missing_currency_reason(self) -> None:
        import_log = _review_import(
            import_id="missing-currency",
            watches=[{"brand": "Rolex", "reference": "126610LN", "original_price": 12500}],
        )

        assert detect_primary_failure_reason(import_log) == "missing_currency"

    def test_multiple_possible_references_reason(self) -> None:
        import_log = _review_import(
            import_id="multi-ref",
            watches=[
                {
                    "brand": "Rolex",
                    "reference": None,
                    "watch_identification": {"likely_references": ["126610LN", "126610LV"]},
                }
            ],
        )

        assert detect_primary_failure_reason(import_log) == "multiple_possible_references"


class TestParserReviewSorting:
    def test_parser_review_sorted_by_business_priority(self) -> None:
        logs = [
            _review_import(
                import_id="old-low",
                import_time="2026-06-20T10:00:00+00:00",
                watches=[{"brand": "Generic", "reference": None, "usd_price": 5000}],
            ),
            _review_import(
                import_id="high-value",
                import_time="2026-06-24T10:00:00+00:00",
                watches=[{"brand": "Richard Mille", "reference": None, "usd_price": 250000}],
            ),
            _review_import(
                import_id="sold-order",
                import_time="2026-06-23T10:00:00+00:00",
                watches=[{"brand": "Rolex", "reference": "126610LN", "usd_price": 15000}],
                request_intent_kind="sold_order",
            ),
        ]

        ordered = sort_parser_review_imports(logs)

        assert [log["id"] for log in ordered] == ["sold-order", "high-value", "old-low"]
        assert parser_review_business_sort_key(ordered[0]) < parser_review_business_sort_key(ordered[1])

    @patch("database.get_messages_by_ids", return_value={})
    def test_load_parser_review_page_data_returns_sorted_rows(self, _mock_messages: MagicMock) -> None:
        logs = [
            _review_import(import_id="a", import_time="2026-06-20T10:00:00+00:00"),
            _review_import(
                import_id="b",
                import_time="2026-06-25T10:00:00+00:00",
                watches=[{"brand": "Richard Mille", "reference": None, "usd_price": 300000}],
            ),
        ]

        rows, _counts = load_parser_review_page_data(logs, "all", format_timestamp=lambda value: value or "N/A")

        assert [row["id"] for row in rows] == ["b", "a"]
        assert rows[0]["primary_failure_label"] != "Needs review"


class TestQuickFixWorkflow:
    @patch("database.mark_import_parser_reviewed")
    @patch("parser_workbench.reprocess_import_log")
    @patch("database.get_import_log")
    def test_reprocessed_success_leaves_queue(
        self,
        mock_get_import_log: MagicMock,
        mock_reprocess: MagicMock,
        mock_mark_reviewed: MagicMock,
    ) -> None:
        pending = _review_import(import_id="fix-1")
        mock_get_import_log.return_value = pending
        resolved = _success_import("fix-1")
        mock_reprocess.return_value = resolved
        mock_mark_reviewed.return_value = {**resolved, "summary": {**resolved["summary"], "parser_reviewed": True}}

        result = apply_workbench_fix_and_finalize("fix-1", "missing_reference", reference="126610LN")

        mock_mark_reviewed.assert_called_once_with("fix-1")
        assert is_parser_review_pending(result) is False

    @patch("database.mark_import_parser_reviewed")
    @patch("parser_workbench.reprocess_import_log")
    @patch("database.get_import_log")
    def test_accuracy_updates_after_fix(
        self,
        mock_get_import_log: MagicMock,
        mock_reprocess: MagicMock,
        _mock_mark_reviewed: MagicMock,
    ) -> None:
        pending = _review_import(import_id="fix-2")
        resolved = _success_import("fix-2")
        mock_get_import_log.return_value = pending
        mock_reprocess.return_value = resolved

        before = load_parser_accuracy_dashboard([pending, _success_import("other")])
        apply_workbench_fix_and_finalize("fix-2", "missing_reference", reference="126610LN")
        after = load_parser_accuracy_dashboard([resolved, _success_import("other")])

        assert before["overall"]["needs_review"] == 1
        assert after["overall"]["needs_review"] == 0
        assert after["overall"]["accuracy_pct"] > before["overall"]["accuracy_pct"]


class TestParserAccuracyAccess:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._parser_accuracy_import_logs", return_value=[])
    def test_admin_can_open_ai_health_dashboard(
        self,
        _mock_logs: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/ai-health")

        assert response.status_code == 200
        assert "AI Health" in response.text
        assert "Training Queue" in response.text

    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_cannot_open_ai_health_dashboard(self, _mock_user: MagicMock) -> None:
        client = TestClient(app)
        response = client.get("/ai-health")

        assert response.status_code == 403
