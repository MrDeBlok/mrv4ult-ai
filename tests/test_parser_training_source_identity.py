"""Regression tests for parser training offer source identity safety."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app import app
from condition_normalizer import NEW_CONDITION
from database import OfferSourceIdentityConflictError, update_offer_from_training
from parser_training_engine import (
    _resolve_training_row_offer,
    correct_training_row,
    create_offer_for_training_row,
)
from tests.conftest import ADMIN_USER
from tests.test_parser_training_save_row_workflow import (
    IMPORT_LOG_ID,
    MESSAGE_ID,
    OFFER_ID,
    ROW_ID,
    _import_log,
    _parsed_watch,
    _training_row_from_watch,
)

pytestmark = pytest.mark.no_auto_login

OFFER_LINE_5 = "55555555-5555-4555-8555-555555555555"
OFFER_DUPLICATE = "66666666-6666-4666-8666-666666666666"


class TestUpdateOfferFromTrainingIdentity:
    @patch("database.get_client")
    @patch("database.find_or_create_watch")
    @patch("database.get_offer_by_id")
    def test_update_does_not_change_existing_message_id_or_line_index(
        self,
        mock_get_offer: MagicMock,
        mock_find_watch: MagicMock,
        mock_get_client: MagicMock,
    ) -> None:
        mock_get_offer.return_value = {
            "id": OFFER_DUPLICATE,
            "message_id": MESSAGE_ID,
            "line_index": 2,
            "dealer_id": "dealer-1",
        }
        mock_find_watch.return_value = ({"id": "watch-2"}, False)
        mock_execute = MagicMock()
        mock_execute.data = [{"id": OFFER_DUPLICATE, "line_index": 2, "message_id": MESSAGE_ID}]
        mock_get_client.return_value.table.return_value.update.return_value.eq.return_value.execute = (
            mock_execute
        )

        update_offer_from_training(
            OFFER_DUPLICATE,
            watch=_parsed_watch(brand="Rolex", reference="126331", usd_price=23000),
            message_id=MESSAGE_ID,
            line_index=5,
        )

        payload = mock_get_client.return_value.table.return_value.update.call_args.args[0]
        assert "message_id" not in payload
        assert "line_index" not in payload
        assert payload["watch_id"] == "watch-2"

    @patch("database.find_offer_by_message_line_index")
    @patch("database.get_client")
    @patch("database.find_or_create_watch")
    @patch("database.get_offer_by_id")
    def test_update_raises_conflict_when_target_line_owned_by_other_offer(
        self,
        mock_get_offer: MagicMock,
        mock_find_watch: MagicMock,
        mock_get_client: MagicMock,
        mock_find_by_line: MagicMock,
    ) -> None:
        mock_get_offer.return_value = {
            "id": OFFER_DUPLICATE,
            "message_id": MESSAGE_ID,
            "line_index": None,
            "dealer_id": "dealer-1",
        }
        mock_find_watch.return_value = ({"id": "watch-2"}, False)
        mock_find_by_line.return_value = {"id": OFFER_LINE_5, "line_index": 5}

        with pytest.raises(OfferSourceIdentityConflictError):
            update_offer_from_training(
                OFFER_DUPLICATE,
                watch=_parsed_watch(),
                message_id=MESSAGE_ID,
                line_index=5,
            )

        mock_get_client.return_value.table.return_value.update.assert_not_called()

    @patch("database.find_offer_by_message_line_index")
    @patch("database.get_client")
    @patch("database.find_or_create_watch")
    @patch("database.get_offer_by_id")
    def test_postgres_23505_is_converted_to_conflict_error(
        self,
        mock_get_offer: MagicMock,
        mock_find_watch: MagicMock,
        mock_get_client: MagicMock,
        mock_find_by_line: MagicMock,
    ) -> None:
        mock_get_offer.return_value = {
            "id": OFFER_ID,
            "message_id": MESSAGE_ID,
            "line_index": 0,
            "dealer_id": "dealer-1",
        }
        mock_find_watch.return_value = ({"id": "watch-1"}, False)
        mock_find_by_line.return_value = None
        mock_get_client.return_value.table.return_value.update.return_value.eq.return_value.execute.side_effect = APIError(
            {"message": 'duplicate key value violates unique constraint "offers_message_line_unique"', "code": "23505"}
        )

        with pytest.raises(OfferSourceIdentityConflictError):
            update_offer_from_training(OFFER_ID, watch=_parsed_watch())


class TestResolveTrainingRowOffer:
    @patch("database.get_offer_by_id")
    @patch("database.find_offer_by_message_line_index")
    def test_missing_linked_offer_finds_existing_offer_by_message_line(
        self,
        mock_find_by_line: MagicMock,
        mock_get_offer: MagicMock,
    ) -> None:
        mock_get_offer.return_value = None
        mock_find_by_line.return_value = {"id": OFFER_LINE_5, "line_index": 5}

        resolved = _resolve_training_row_offer(
            row={"created_offer_id": None, "row_index": 5},
            message_id=MESSAGE_ID,
            line_index=5,
        )

        assert resolved is not None
        assert resolved["id"] == OFFER_LINE_5

    @patch("database.get_offer_by_id")
    @patch("database.find_offer_by_message_line_index")
    def test_linked_offer_with_different_line_index_raises_conflict(
        self,
        mock_find_by_line: MagicMock,
        mock_get_offer: MagicMock,
    ) -> None:
        mock_get_offer.return_value = {
            "id": OFFER_DUPLICATE,
            "message_id": MESSAGE_ID,
            "line_index": 2,
        }
        mock_find_by_line.return_value = {"id": OFFER_LINE_5, "line_index": 5}

        with pytest.raises(OfferSourceIdentityConflictError):
            _resolve_training_row_offer(
                row={"created_offer_id": OFFER_DUPLICATE, "row_index": 5},
                message_id=MESSAGE_ID,
                line_index=5,
            )

    @patch("database.get_offer_by_id")
    @patch("database.find_offer_by_message_line_index")
    def test_same_offer_at_source_line_is_allowed(
        self,
        mock_find_by_line: MagicMock,
        mock_get_offer: MagicMock,
    ) -> None:
        mock_get_offer.return_value = {
            "id": OFFER_LINE_5,
            "message_id": MESSAGE_ID,
            "line_index": 5,
        }
        mock_find_by_line.return_value = {
            "id": OFFER_LINE_5,
            "message_id": MESSAGE_ID,
            "line_index": 5,
        }

        resolved = _resolve_training_row_offer(
            row={"created_offer_id": OFFER_LINE_5, "row_index": 5},
            message_id=MESSAGE_ID,
            line_index=5,
        )

        assert resolved["id"] == OFFER_LINE_5


class TestDuplicateImportSaveRow:
    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.find_offer_by_message_line_index")
    @patch("database.get_offer_by_id")
    def test_duplicate_only_import_updates_linked_offer_without_line_index_rewrite(
        self,
        mock_get_offer: MagicMock,
        mock_find_by_line: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        mock_update_offer: MagicMock,
        mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(_parsed_watch(), row_index=5, created_offer_id=OFFER_DUPLICATE)
        row["id"] = ROW_ID
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_get_offer.return_value = {
            "id": OFFER_DUPLICATE,
            "message_id": MESSAGE_ID,
            "line_index": 2,
            "dealer_id": "dealer-1",
        }
        mock_find_by_line.return_value = {"id": OFFER_LINE_5, "line_index": 5}
        mock_update_offer.return_value = {"id": OFFER_DUPLICATE}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}
        mock_sync_summary.return_value = _import_log()

        with pytest.raises(OfferSourceIdentityConflictError):
            correct_training_row(
                ROW_ID,
                {
                    "brand": "Rolex",
                    "reference": "126331",
                    "condition": NEW_CONDITION,
                    "year": "2024",
                    "price": "14500",
                    "currency": "USD",
                },
            )

        mock_update_offer.assert_not_called()

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.find_offer_by_message_line_index")
    @patch("database.get_offer_by_id")
    def test_duplicate_offer_with_unique_line_owner_updates_safely(
        self,
        mock_get_offer: MagicMock,
        mock_find_by_line: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        mock_update_offer: MagicMock,
        mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(_parsed_watch(), row_index=5, created_offer_id=OFFER_DUPLICATE)
        row["id"] = ROW_ID
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_get_offer.return_value = {
            "id": OFFER_DUPLICATE,
            "message_id": MESSAGE_ID,
            "line_index": 2,
            "dealer_id": "dealer-1",
        }
        mock_find_by_line.return_value = None
        mock_update_offer.return_value = {"id": OFFER_DUPLICATE}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}
        mock_sync_summary.return_value = _import_log()

        result = correct_training_row(
            ROW_ID,
            {
                "brand": "Rolex",
                "reference": "126331",
                "condition": NEW_CONDITION,
                "year": "2024",
                "price": "14500",
                "currency": "USD",
            },
        )

        mock_update_offer.assert_called_once_with(OFFER_DUPLICATE, watch=mock_update_offer.call_args.kwargs["watch"])
        assert result["status"] == "corrected"

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.find_offer_by_message_line_index")
    @patch("database.get_offer_by_id")
    def test_sequential_multi_row_save_leaves_prior_rows_saved_when_one_conflicts(
        self,
        mock_get_offer: MagicMock,
        mock_find_by_line: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        mock_update_offer: MagicMock,
        mock_sync_summary: MagicMock,
    ) -> None:
        rows = {
            "row-0": _training_row_from_watch(_parsed_watch(), row_index=0, created_offer_id=OFFER_ID),
            "row-5": _training_row_from_watch(_parsed_watch(), row_index=5, created_offer_id=OFFER_DUPLICATE),
        }
        rows["row-0"]["id"] = "row-0"
        rows["row-5"]["id"] = "row-5"

        def _offer_lookup(offer_id: str) -> dict:
            if offer_id == OFFER_ID:
                return {"id": OFFER_ID, "message_id": MESSAGE_ID, "line_index": 0, "dealer_id": "dealer-1"}
            return {"id": OFFER_DUPLICATE, "message_id": MESSAGE_ID, "line_index": 2, "dealer_id": "dealer-1"}

        mock_get_offer.side_effect = _offer_lookup
        mock_find_by_line.side_effect = lambda message_id, line_index: (
            {"id": OFFER_LINE_5, "line_index": 5} if line_index == 5 else None
        )
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_update_offer.return_value = {"id": OFFER_ID}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **rows[row_id], **fields}
        mock_sync_summary.return_value = _import_log()

        mock_get_row.return_value = rows["row-0"]
        first = correct_training_row(
            "row-0",
            {"brand": "Rolex", "reference": "126331", "condition": NEW_CONDITION, "price": "14500", "currency": "USD"},
        )
        rows["row-0"].update(first)

        mock_get_row.return_value = rows["row-5"]
        with pytest.raises(OfferSourceIdentityConflictError):
            correct_training_row(
                "row-5",
                {"brand": "Rolex", "reference": "126334", "condition": NEW_CONDITION, "price": "15000", "currency": "USD"},
            )

        assert first["status"] == "corrected"
        assert mock_update_row.call_count == 1

    @patch("database.find_offer_by_message_line_index")
    @patch("database.insert_offer")
    @patch("database.find_or_create_watch")
    def test_create_offer_reuses_existing_message_line_offer(
        self,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_find_by_line: MagicMock,
    ) -> None:
        mock_find_by_line.return_value = {"id": OFFER_LINE_5, "line_index": 5}
        row = _training_row_from_watch(_parsed_watch(), row_index=5)

        offer_row, created = create_offer_for_training_row(
            row,
            import_log_id=IMPORT_LOG_ID,
            message_id=MESSAGE_ID,
            dealer_id="dealer-1",
            line_index=5,
            final_watch=_parsed_watch(brand="Rolex", reference="126331"),
        )

        assert created is False
        assert offer_row["id"] == OFFER_LINE_5
        mock_insert_offer.assert_not_called()

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.find_offer_by_message_line_index", return_value=None)
    @patch("database.get_offer_by_id")
    def test_rm_identity_update_changes_watch_fields_not_source_identity(
        self,
        mock_get_offer: MagicMock,
        _mock_find_by_line: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        mock_update_offer: MagicMock,
        mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(
            _parsed_watch(source_line="RM07-01 Starry Night 7/2026 $428k usdt"),
            row_index=4,
            created_offer_id=OFFER_DUPLICATE,
        )
        row["id"] = ROW_ID
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_get_offer.return_value = {
            "id": OFFER_DUPLICATE,
            "message_id": MESSAGE_ID,
            "line_index": 4,
            "dealer_id": "dealer-1",
            "watch_id": "watch-old",
        }
        mock_update_offer.return_value = {"id": OFFER_DUPLICATE, "watch_id": "watch-rm-new"}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}
        mock_sync_summary.return_value = _import_log()

        correct_training_row(
            ROW_ID,
            {
                "brand": "Richard Mille",
                "reference": "RM07-01",
                "condition": NEW_CONDITION,
                "price": "428000",
                "currency": "USD",
            },
        )

        mock_update_offer.assert_called_once()
        assert mock_update_offer.call_args.args[0] == OFFER_DUPLICATE
        assert "line_index" not in mock_update_offer.call_args.kwargs
        assert "message_id" not in mock_update_offer.call_args.kwargs


class TestSaveRowRouteConflictHandling:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("app.correct_training_row")
    @patch("database.get_parser_training_row")
    def test_conflict_returns_redirect_with_error_not_500(
        self,
        mock_get_row: MagicMock,
        mock_correct: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_row.return_value = {"id": ROW_ID, "import_log_id": IMPORT_LOG_ID}
        mock_get_import.return_value = {"id": IMPORT_LOG_ID, "message_id": MESSAGE_ID}
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_correct.side_effect = OfferSourceIdentityConflictError(
            "Could not save this row because another offer already uses the same message line."
        )

        response = TestClient(app).post(
            f"/parser-training/rows/{ROW_ID}/correct",
            data={"reference": "126331"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "save_error=" in response.headers["location"]
        assert response.headers["location"].startswith(f"/parser-training/{IMPORT_LOG_ID}/rows")
