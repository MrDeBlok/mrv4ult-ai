"""Tests for Sprint 50.4 — async reference-brand re-evaluate (learn globally P0 fix)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from database import (
    PARSER_TRAINING_REFERENCE_BATCH_SIZE,
    PARSER_TRAINING_REFERENCE_QUERY_MAX,
)
from parser_training_engine import (
    REFERENCE_REEVALUATE_BATCH_SIZE,
    correct_training_row,
    re_evaluate_parser_training_reference_batch,
    re_evaluate_parser_training_rows,
)
from tests.conftest import ADMIN_USER

pytestmark = pytest.mark.no_auto_login

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MESSAGE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ROW_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OTHER_ROW_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
PATEK = "Patek Philippe"
REFERENCE = "5524G"


def _training_db_row(**overrides) -> dict:
    base = {
        "id": ROW_ID,
        "import_log_id": IMPORT_LOG_ID,
        "source_message_id": MESSAGE_ID,
        "row_index": 0,
        "raw_row_text": "5524G cream white n1 380000hkd",
        "detected_brand": None,
        "detected_reference": REFERENCE,
        "detected_price": 380000,
        "detected_currency": "HKD",
        "normalized_brand": None,
        "normalized_reference": REFERENCE,
        "status": "pending_review",
        "issue_types": ["unknown_reference", "missing_brand"],
        "created_offer_id": None,
        "usd_price": 48500,
    }
    base.update(overrides)
    return base


class TestLearnGloballyRowSave:
    @patch("parser_training_engine.create_offer_for_training_row", return_value=({"id": "offer-1"}, True))
    @patch("parser_training_engine.re_evaluate_parser_training_rows")
    @patch("database.update_parser_training_row")
    @patch("database.create_reference_brand_mapping")
    @patch("database.reference_brand_mappings_supported", return_value=True)
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    def test_learn_globally_does_not_sync_reevaluate_other_rows(
        self,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        _supported: MagicMock,
        mock_create_mapping: MagicMock,
        mock_update: MagicMock,
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
        mock_update.return_value = {"id": ROW_ID, "status": "approved", "normalized_brand": PATEK}
        mock_create_mapping.return_value = {"id": "mapping-1"}

        correct_training_row(
            ROW_ID,
            {"brand": PATEK, "learn_reference_brand": True},
            learn_mode="row_only",
        )

        mock_create_mapping.assert_called_once()
        mock_re_eval.assert_not_called()
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["normalized_brand"] == PATEK

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.correct_training_row")
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("database.get_parser_training_row")
    def test_row_save_redirects_with_mapping_saved_notice(
        self,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        _mock_message: MagicMock,
        mock_correct: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_row.return_value = _training_db_row()
        mock_get_import.return_value = {
            "id": IMPORT_LOG_ID,
            "message_id": MESSAGE_ID,
            "summary": {"message_type": "offer"},
        }
        mock_correct.return_value = {"id": ROW_ID, "status": "approved"}

        client = TestClient(app)
        response = client.post(
            f"/parser-training/rows/{ROW_ID}/correct",
            data={
                "brand": PATEK,
                "learn_reference_brand": "1",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "mapping_saved=1" in response.headers["location"]
        assert "saved=1" in response.headers["location"]


class TestPaginatedReferenceListing:
    @patch("database.get_client")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_list_parser_training_rows_for_reference_uses_range_and_cap(
        self,
        _supported: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        from database import list_parser_training_rows_for_reference

        table = MagicMock()
        query = MagicMock()
        mock_get_client.return_value.table.return_value = table
        table.select.return_value = query
        query.or_.return_value = query
        query.order.return_value = query
        query.range.return_value.execute.return_value = MagicMock(data=[])

        list_parser_training_rows_for_reference(REFERENCE, limit=200, offset=10)

        query.range.assert_called_once_with(10, 10 + PARSER_TRAINING_REFERENCE_QUERY_MAX - 1)

    def test_batch_size_constants_match(self) -> None:
        assert REFERENCE_REEVALUATE_BATCH_SIZE == PARSER_TRAINING_REFERENCE_BATCH_SIZE == 50


class TestReferenceBatchReevaluate:
    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_rows_for_reference")
    def test_manual_reference_reevaluate_processes_one_batch(
        self,
        mock_list_rows: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        rows = [_training_db_row(id=f"row-{index}") for index in range(50)]
        mock_list_rows.return_value = rows
        mock_update.side_effect = lambda row_id, **fields: {"id": row_id, **fields}

        with patch(
            "watch_knowledge._load_reference_brand_mapping_index",
            return_value={REFERENCE: PATEK},
        ):
            result = re_evaluate_parser_training_reference_batch(
                REFERENCE,
                limit=50,
                offset=0,
                message_type="offer",
            )

        mock_list_rows.assert_called_once_with(REFERENCE, limit=50, offset=0)
        assert result["rows_checked"] == 50
        assert result["rows_updated"] == 50
        assert result["has_more"] is True
        assert result["next_offset"] == 50
        assert mock_update.call_count == 50

    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_rows_for_reference")
    def test_partial_batch_sets_has_more_false(
        self,
        mock_list_rows: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        mock_list_rows.return_value = [_training_db_row()]
        mock_update.return_value = {"id": ROW_ID}

        with patch(
            "watch_knowledge._load_reference_brand_mapping_index",
            return_value={REFERENCE: PATEK},
        ):
            result = re_evaluate_parser_training_reference_batch(REFERENCE)

        assert result["rows_checked"] == 1
        assert result["has_more"] is False

    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_rows_for_reference")
    def test_reference_reevaluate_only_updates_queried_rows(
        self,
        mock_list_rows: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        mock_list_rows.return_value = [_training_db_row(id=OTHER_ROW_ID)]
        mock_update.return_value = {"id": OTHER_ROW_ID, "normalized_brand": PATEK}

        with patch(
            "watch_knowledge._load_reference_brand_mapping_index",
            return_value={REFERENCE: PATEK},
        ):
            re_evaluate_parser_training_rows(reference=REFERENCE, limit=50, offset=0)

        mock_list_rows.assert_called_once()
        mock_update.assert_called_once()
        assert mock_update.call_args[0][0] == OTHER_ROW_ID


class TestReevaluateReferenceEndpoint:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.re_evaluate_parser_training_reference_batch")
    @patch("app.get_import_log")
    def test_post_re_evaluate_reference_redirects_with_counts(
        self,
        mock_get_import: MagicMock,
        mock_batch: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import.return_value = {
            "id": IMPORT_LOG_ID,
            "summary": {"message_type": "offer"},
        }
        mock_batch.return_value = {
            "reference": REFERENCE,
            "offset": 0,
            "limit": 50,
            "rows_checked": 50,
            "rows_updated": 12,
            "has_more": True,
            "next_offset": 50,
        }

        client = TestClient(app)
        response = client.post(
            "/parser-training/re-evaluate-reference",
            data={
                "reference": REFERENCE,
                "import_id": IMPORT_LOG_ID,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers["location"]
        assert IMPORT_LOG_ID in location
        assert "ref_reevaluated=1" in location
        assert "reference=5524G" in location
        assert "checked=50" in location
        assert "updated=12" in location
        assert "has_more=1" in location
        mock_batch.assert_called_once()

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.re_evaluate_parser_training_reference_batch")
    def test_post_re_evaluate_reference_without_import_redirects_to_overview(
        self,
        mock_batch: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_batch.return_value = {
            "reference": REFERENCE,
            "rows_checked": 3,
            "rows_updated": 1,
            "has_more": False,
        }

        client = TestClient(app)
        response = client.post(
            "/parser-training/re-evaluate-reference",
            data={"reference": REFERENCE},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/parser-training?")
        assert "ref_reevaluated=1" in response.headers["location"]
