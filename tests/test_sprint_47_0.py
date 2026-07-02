"""Tests for Sprint 47.0 AI Health dashboard redesign."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from parser_accuracy import (
    health_badge_level,
    is_actionable_watch_import,
    is_discarded_import,
    is_duplicate_import,
    is_ignored_import,
    load_ai_health_dashboard,
)
from tests.conftest import ADMIN_USER, TRADER_ONE

pytestmark = pytest.mark.no_auto_login


def _review_import(
    *,
    import_id: str,
    status: str = "warning",
    watches: list[dict] | None = None,
    import_time: str = "2026-06-25T10:00:00+00:00",
    parser_reviewed: bool = False,
    parser_review_ignored: bool = False,
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
        "status_reason": "Important fields are missing",
        "parsed_watches": parsed_watches,
        "rows": list(parsed_watches),
    }
    if parser_reviewed:
        summary["parser_reviewed"] = True
    if parser_review_ignored:
        summary["parser_review_ignored"] = True
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


class TestActionableClassification:
    def test_ignored_imports_are_excluded_from_actionable(self) -> None:
        ignored = _review_import(import_id="ignored", parser_review_ignored=True)
        assert is_ignored_import(ignored) is True
        assert is_actionable_watch_import(ignored) is False

    def test_discarded_imports_are_excluded_from_actionable(self) -> None:
        discarded = {
            "id": "discarded",
            "status": "warning",
            "watches_parsed": 0,
            "summary": {},
        }
        assert is_discarded_import(discarded) is True
        assert is_actionable_watch_import(discarded) is False

    def test_duplicate_imports_are_excluded_from_actionable(self) -> None:
        duplicate = {
            "id": "duplicate",
            "status": "success",
            "watches_parsed": 1,
            "summary": {"already_processed": True, "parsed_watches": [{"brand": "Rolex"}]},
        }
        assert is_duplicate_import(duplicate) is True
        assert is_actionable_watch_import(duplicate) is False


class TestAIHealthMetrics:
    def test_accuracy_calculation_reconciles(self) -> None:
        logs = [
            _success_import("ok-1"),
            _success_import("ok-2"),
            _review_import(import_id="review-1"),
            _review_import(import_id="ignored", parser_review_ignored=True),
            {
                "id": "duplicate",
                "status": "success",
                "watches_parsed": 1,
                "summary": {"already_processed": True},
            },
            {
                "id": "noise",
                "status": "noise",
                "watches_parsed": 0,
                "summary": {},
            },
        ]
        dashboard = load_ai_health_dashboard(
            logs,
            now=datetime(2026, 6, 25, 15, 0, tzinfo=timezone.utc),
        )
        summary = dashboard["processing_summary"]

        assert summary["total_actionable"] == summary["successfully_parsed"] + summary["needs_review"]
        assert summary["parser_accuracy_pct"] == pytest.approx(
            (summary["successfully_parsed"] / summary["total_actionable"]) * 100,
            abs=0.1,
        )
        assert dashboard["health_score"] == summary["parser_accuracy_pct"]
        assert summary["successfully_parsed"] == 2
        assert summary["needs_review"] == 1
        assert summary["ignored"] >= 1
        assert summary["duplicates"] == 1

    def test_ignored_imports_do_not_reduce_health_score(self) -> None:
        with_ignored = load_ai_health_dashboard(
            [
                _success_import("ok-1"),
                _success_import("ok-2"),
                _review_import(import_id="ignored", parser_review_ignored=True),
            ]
        )
        without_ignored = load_ai_health_dashboard(
            [_success_import("ok-1"), _success_import("ok-2")]
        )

        assert with_ignored["health_score"] == without_ignored["health_score"] == 100.0

    def test_needs_review_counts_pending_actionable_only(self) -> None:
        logs = [
            _review_import(import_id="pending"),
            _review_import(import_id="ignored", parser_review_ignored=True),
            _success_import("ok"),
        ]
        dashboard = load_ai_health_dashboard(logs)

        assert dashboard["needs_review"] == 1
        assert dashboard["training_queue_total"] == 1

    def test_processing_summary_totals_reconcile(self) -> None:
        logs = [
            _success_import("ok"),
            _review_import(import_id="pending"),
            _review_import(import_id="ignored", parser_review_ignored=True),
            {"id": "dup", "status": "success", "watches_parsed": 0, "summary": {"already_processed": True}},
            {"id": "discard", "status": "warning", "watches_parsed": 0, "summary": {}},
            {"id": "noise", "status": "noise", "watches_parsed": 0, "summary": {}},
        ]
        dashboard = load_ai_health_dashboard(logs)
        summary = dashboard["processing_summary"]

        assert (
            summary["successfully_parsed"]
            + summary["needs_review"]
            + summary["ignored"]
            + summary["discarded"]
            + summary["duplicates"]
            == summary["total_scanned"]
        )


class TestTrainingQueue:
    def test_training_queue_groups_primary_reasons(self) -> None:
        logs = [
            _review_import(
                import_id="brand",
                watches=[{"brand": None, "source_line": "Cubitus blue", "original_price": 40000}],
            ),
            _review_import(
                import_id="ref",
                watches=[{"brand": "Rolex", "reference": None, "original_price": 12500, "original_currency": "USD"}],
            ),
            _review_import(
                import_id="nickname",
                watches=[
                    {
                        "brand": "Rolex",
                        "nickname": "Starbucks",
                        "reference": None,
                        "original_price": 12500,
                        "original_currency": "USD",
                    }
                ],
            ),
        ]
        dashboard = load_ai_health_dashboard(logs)
        queue = {row["key"]: row["count"] for row in dashboard["training_queue"]}

        assert queue["unknown_brand"] == 1
        assert queue["missing_reference"] == 1
        assert queue["unknown_nickname"] == 1


class TestHealthBadges:
    @pytest.mark.parametrize(
        ("metric", "value", "expected"),
        [
            ("health_score", 96, "healthy"),
            ("health_score", 80, "attention"),
            ("health_score", 60, "critical"),
            ("needs_review", 4, "healthy"),
            ("needs_review", 43, "attention"),
            ("needs_review", 127, "critical"),
        ],
    )
    def test_health_badge_thresholds(self, metric: str, value: int, expected: str) -> None:
        assert health_badge_level(metric, value) == expected


class TestBrandTable:
    def test_brand_table_hidden_when_empty(self) -> None:
        dashboard = load_ai_health_dashboard([_success_import("generic", brand="Generic Brand")])
        assert dashboard["brand_accuracy"] == []

    def test_brand_accuracy_uses_actionable_only(self) -> None:
        logs = [
            _success_import("rm-ok", brand="Richard Mille"),
            _review_import(
                import_id="rm-review",
                watches=[{"brand": "Richard Mille", "reference": None, "usd_price": 250000}],
            ),
        ]
        dashboard = load_ai_health_dashboard(logs)
        rm = next(row for row in dashboard["brand_accuracy"] if row["brand"] == "Richard Mille")

        assert rm["total"] == 2
        assert rm["fully_parsed"] == 1
        assert rm["needs_review"] == 1
        assert rm["accuracy_pct"] == 50.0


class TestAIHealthAccess:
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
        assert "Processing Summary" in response.text
        assert "Training Queue" in response.text

    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_cannot_open_ai_health_dashboard(self, _mock_user: MagicMock) -> None:
        client = TestClient(app)
        response = client.get("/ai-health")

        assert response.status_code == 403
