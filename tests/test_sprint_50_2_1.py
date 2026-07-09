"""Tests for Sprint 50.2.1 — Parser Training container statistics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from parser_training_center import load_parser_training_containers, load_parser_training_rows_for_import
from parser_training_engine import (
    build_container_summary_for_import,
    bucket_training_row_for_container_stats,
    container_stats_match_row_totals,
    summarize_training_rows_by_status,
    summarize_training_rows_from_display,
)

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _row(status: str, **extra) -> dict:
    return {"status": status, "import_log_id": IMPORT_LOG_ID, **extra}


def _import_log() -> dict:
    return {
        "id": IMPORT_LOG_ID,
        "import_time": "2026-06-27T12:00:00+00:00",
        "summary": {"offer_watches": [{}] * 13, "message_type": "offer"},
        "watches_parsed": 13,
        "dealer_alias": "Dealer A",
        "group_name": "HK Dealers",
    }


class TestContainerBucketRules:
    def test_corrected_counts_as_approved(self) -> None:
        assert bucket_training_row_for_container_stats({"status": "corrected"}) == "approved"

    def test_status_buckets_sum_to_total(self) -> None:
        rows = (
            [_row("approved")] * 8
            + [_row("valid")] * 2
            + [_row("corrected")] * 3
            + [_row("pending_review")] * 2
            + [_row("ignored")]
            + [_row("failed")]
        )
        summary = summarize_training_rows_by_status(rows)

        assert summary["total_rows"] == 17
        assert summary["approved_rows"] == 13
        assert summary["pending_review_rows"] == 2
        assert summary["ignored_rows"] == 1
        assert summary["failed_rows"] == 1
        assert container_stats_match_row_totals(summary)


class TestContainerOverviewMatchesRows:
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("database.list_parser_training_rows_for_import")
    def test_opening_rows_matches_overview_counts(
        self,
        mock_list_rows: MagicMock,
        _supported: MagicMock,
    ) -> None:
        db_rows = (
            [_row("approved", row_index=index) for index in range(8)]
            + [_row("valid", row_index=index) for index in range(8, 10)]
            + [_row("corrected", row_index=index) for index in range(10, 11)]
            + [_row("pending_review", row_index=index) for index in range(11, 13)]
            + [_row("ignored", row_index=13)]
        )
        mock_list_rows.return_value = db_rows

        import_log = _import_log()
        training_rows, row_stats = load_parser_training_rows_for_import(import_log)
        display_stats = summarize_training_rows_from_display(training_rows)

        assert row_stats["total_rows"] == 14
        assert row_stats["approved_rows"] == 11
        assert row_stats["pending_review_rows"] == 2
        assert row_stats["ignored_rows"] == 1
        assert display_stats["approved_rows"] == row_stats["approved_rows"]
        assert display_stats["pending_review_rows"] == row_stats["pending_review_rows"]
        assert display_stats["ignored_rows"] == row_stats["ignored_rows"]
        assert container_stats_match_row_totals(row_stats)

    @patch("database.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("database.list_parser_training_import_summaries")
    def test_container_overview_uses_training_rows_not_summary_fallback(
        self,
        mock_summaries: MagicMock,
        _supported: MagicMock,
        _attach: MagicMock,
    ) -> None:
        mock_summaries.return_value = [
            {
                "import_log_id": IMPORT_LOG_ID,
                "total_rows": 278,
                "approved_rows": 260,
                "pending_review_rows": 18,
                "ignored_rows": 0,
                "failed_rows": 0,
                "corrected_rows": 0,
            }
        ]

        containers, totals = load_parser_training_containers(
            [_import_log()],
            format_timestamp=lambda value: value or "",
        )

        assert len(containers) == 1
        assert containers[0]["total_rows"] == 278
        assert containers[0]["approved_rows"] == 260
        assert containers[0]["pending_review_rows"] == 18
        assert containers[0]["approved_rows"] != 0
        assert containers[0]["pending_review_rows"] != 278
        assert totals["approved_rows"] == 260
        assert totals["pending_review_rows"] == 18
        assert container_stats_match_row_totals(containers[0])

    def test_build_container_summary_recalculates_from_current_status(self) -> None:
        rows = [_row("approved")] * 260 + [_row("pending_review")] * 18
        summary = build_container_summary_for_import(
            rows,
            import_log_id=IMPORT_LOG_ID,
        )

        assert summary["approved_rows"] == 260
        assert summary["pending_review_rows"] == 18
        assert summary["total_rows"] == 278
        assert container_stats_match_row_totals(summary)
