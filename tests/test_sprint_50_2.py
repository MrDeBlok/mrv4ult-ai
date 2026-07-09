"""Tests for Sprint 50.2 — Parser Training counts + reference brand learning."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from parser_training_center import format_training_row_display, load_parser_training_containers
from parser_training_engine import (
    apply_reference_brand_mapping_to_watch,
    build_training_row_payload,
    bucket_training_row_for_container_stats,
    compute_training_row_updates,
    correct_training_row,
    re_evaluate_parser_training_rows,
    summarize_training_rows_by_status,
)
from watch_knowledge import invalidate_reference_brand_mapping_cache

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MESSAGE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ROW_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
PATEK = "Patek Philippe"


def _patek_row_without_brand() -> dict:
    return {
        "brand": None,
        "reference": "5524G",
        "original_price": 380000,
        "original_currency": "HKD",
        "usd_price": 48500,
        "source_line": "5524G cream white n1 380000hkd",
        "dealer_list_line": True,
    }


def _training_db_row(**overrides) -> dict:
    base = {
        "id": ROW_ID,
        "import_log_id": IMPORT_LOG_ID,
        "source_message_id": MESSAGE_ID,
        "row_index": 0,
        "raw_row_text": "5524G cream white n1 380000hkd",
        "detected_brand": None,
        "detected_reference": "5524G",
        "detected_price": 380000,
        "detected_currency": "HKD",
        "normalized_brand": None,
        "normalized_reference": "5524G",
        "status": "pending_review",
        "issue_types": ["unknown_reference", "missing_brand"],
        "created_offer_id": None,
        "usd_price": 48500,
    }
    base.update(overrides)
    return base


class TestContainerStats:
    def test_summarize_rows_by_status_counts(self) -> None:
        rows = (
            [{"status": "approved"}] * 8
            + [{"status": "valid"}] * 2
            + [{"status": "pending_review"}] * 2
            + [{"status": "ignored"}]
        )

        summary = summarize_training_rows_by_status(rows)

        assert summary["total_rows"] == 13
        assert summary["approved_rows"] == 10
        assert summary["pending_review_rows"] == 2
        assert summary["ignored_rows"] == 1
        assert summary["failed_rows"] == 0

    def test_bucket_maps_valid_to_approved(self) -> None:
        assert bucket_training_row_for_container_stats({"status": "valid"}) == "approved"

    def test_bucket_maps_corrected_with_offer_to_approved(self) -> None:
        row = {"status": "corrected", "created_offer_id": "offer-1"}
        assert bucket_training_row_for_container_stats(row) == "approved"

    def test_bucket_maps_corrected_without_offer_to_approved(self) -> None:
        row = {"status": "corrected", "created_offer_id": None}
        assert bucket_training_row_for_container_stats(row) == "approved"


class TestReferenceBrandLearning:
    @patch(
        "watch_knowledge._load_reference_brand_mapping_index",
        return_value={"5524G": PATEK},
    )
    def test_existing_mapping_autofills_brand(self, _mock_index: MagicMock) -> None:
        invalidate_reference_brand_mapping_cache()
        watch = apply_reference_brand_mapping_to_watch(_patek_row_without_brand())

        assert watch["brand"] == PATEK
        assert watch.get("reference_high_confidence") is True

    @patch(
        "watch_knowledge._load_reference_brand_mapping_index",
        return_value={"5524G": PATEK},
    )
    def test_row_with_mapping_becomes_approved(self, _mock_index: MagicMock) -> None:
        invalidate_reference_brand_mapping_cache()
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_patek_row_without_brand(),
            message_type="offer",
        )

        assert payload["status"] == "approved"
        assert payload["normalized_brand"] == PATEK
        assert "missing_brand" not in payload["issue_types"]
        assert "unknown_reference" not in payload["issue_types"]

    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_rows_for_reference")
    @patch(
        "watch_knowledge._load_reference_brand_mapping_index",
        return_value={"5524G": PATEK},
    )
    def test_re_evaluate_updates_matching_rows(
        self,
        _mock_index: MagicMock,
        mock_list_rows: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        invalidate_reference_brand_mapping_cache()
        mock_list_rows.return_value = [_training_db_row()]
        mock_update.side_effect = lambda row_id, **fields: {"id": row_id, **fields}

        result = re_evaluate_parser_training_rows(reference="5524G", message_type="offer")

        assert result["rows_checked"] == 1
        assert result["rows_updated"] == 1
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["status"] == "approved"
        assert mock_update.call_args.kwargs["normalized_brand"] == PATEK

    @patch("parser_training_engine.create_offer_for_training_row", return_value=({"id": "offer-1"}, True))
    @patch("parser_training_engine.re_evaluate_parser_training_rows")
    @patch("database.create_reference_brand_mapping")
    @patch("database.reference_brand_mappings_supported", return_value=True)
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    def test_row_edit_learn_reference_mapping_creates_mapping(
        self,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update: MagicMock,
        _supported: MagicMock,
        mock_create_mapping: MagicMock,
        mock_re_eval: MagicMock,
        _mock_create_offer: MagicMock,
    ) -> None:
        mock_get_row.return_value = _training_db_row()
        mock_get_import.return_value = {
            "id": IMPORT_LOG_ID,
            "message_id": MESSAGE_ID,
            "summary": {"message_type": "offer"},
        }
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_update.return_value = {"id": ROW_ID, "status": "approved"}
        mock_create_mapping.return_value = {"id": "mapping-1"}

        correct_training_row(
            ROW_ID,
            {"brand": PATEK, "learn_reference_brand": True},
            learn_mode="row_only",
        )

        mock_create_mapping.assert_called_once()
        mock_re_eval.assert_called()

    @patch("parser_training_engine.re_evaluate_parser_training_rows")
    @patch("database.create_reference_brand_mapping")
    @patch("database.reference_brand_mappings_supported", return_value=True)
    @patch("database.get_parser_training_row")
    def test_bulk_brand_creates_mappings_for_selected_refs(
        self,
        mock_get_row: MagicMock,
        _supported: MagicMock,
        mock_create_mapping: MagicMock,
        mock_re_eval: MagicMock,
    ) -> None:
        from parser_training_engine import bulk_training_row_action

        mock_get_row.return_value = _training_db_row()
        mock_create_mapping.return_value = {"id": "mapping-1"}

        with patch("parser_training_engine.correct_training_row") as mock_correct:
            mock_correct.return_value = {"id": ROW_ID, "status": "approved"}
            bulk_training_row_action(
                IMPORT_LOG_ID,
                "set_brand",
                row_ids=[ROW_ID],
                brand_name=PATEK,
                reference_brand_mappings=[{"reference": "5524G", "selected": True}],
            )

        mock_create_mapping.assert_called_once()
        mock_re_eval.assert_called()


class TestContainerCountsAfterReclassification:
    @patch("database.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("database.list_parser_training_import_summaries")
    def test_container_uses_re_evaluated_row_statuses(
        self,
        mock_summaries: MagicMock,
        _supported: MagicMock,
        _attach: MagicMock,
    ) -> None:
        import_log = {
            "id": IMPORT_LOG_ID,
            "import_time": "2026-06-27T12:00:00+00:00",
            "summary": {"offer_watches": [{}], "message_type": "offer"},
            "watches_parsed": 13,
        }
        mock_summaries.return_value = [
            {
                "import_log_id": IMPORT_LOG_ID,
                "total_rows": 13,
                "approved_rows": 10,
                "pending_review_rows": 2,
                "ignored_rows": 1,
                "failed_rows": 0,
                "corrected_rows": 0,
            }
        ]

        containers, totals = load_parser_training_containers(
            [import_log],
            format_timestamp=lambda value: str(value),
        )

        assert containers[0]["approved_rows"] == 10
        assert containers[0]["pending_review_rows"] == 2
        assert containers[0]["ignored_rows"] == 1
        assert totals["approved_rows"] == 10
        assert totals["pending_review_rows"] == 2


class TestStalePendingRowsReclassified:
    @patch(
        "watch_knowledge._load_reference_brand_mapping_index",
        return_value={"5524G": PATEK},
    )
    def test_stale_pending_row_updates_to_approved(self, _mock_index: MagicMock) -> None:
        invalidate_reference_brand_mapping_cache()
        updates = compute_training_row_updates(
            _training_db_row(),
            message_type="offer",
        )

        assert updates is not None
        assert updates["status"] == "approved"
        assert updates["normalized_brand"] == PATEK

    def test_display_no_longer_uses_valid_failure_label(self) -> None:
        display = format_training_row_display(
            {
                "id": ROW_ID,
                "row_index": 0,
                "status": "approved",
                "issue_types": [],
                "parser_explanation": {"optional_notes": ["Condition not provided"]},
            }
        )

        assert display["status_label"] == "Approved"
        assert display["failure_label"] == ""
