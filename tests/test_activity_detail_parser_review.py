"""Tests for Parser Review action on Activity Import Detail."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app, build_activity_detail
from parser_training_center import build_activity_parser_review_action
from parser_training_engine import prepare_parser_training_rows_for_import
from tests.conftest import ADMIN_USER, TRADER_ONE

pytestmark = pytest.mark.no_auto_login

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MESSAGE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OFFER_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _duplicate_only_import_log(*, watches_count: int = 3) -> dict:
    watches = [
        {
            "brand": "Rolex",
            "reference": "126331",
            "condition": "New",
            "original_price": 23_000,
            "original_currency": "USD",
            "source_line": f"Rolex 126331 new ${23000 + index}",
        }
        for index in range(watches_count)
    ]
    rows = [
        {
            "brand": "Rolex",
            "reference": "126331",
            "offer_id": OFFER_ID,
            "results": ["Duplicate offer"],
        }
        for _ in range(watches_count)
    ]
    return {
        "id": IMPORT_LOG_ID,
        "message_id": MESSAGE_ID,
        "import_time": "2026-07-16T10:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+85291234567",
        "watches_parsed": watches_count,
        "new_offers": 0,
        "duplicate_offers": watches_count,
        "summary": {
            "status": "success",
            "message_type": "offer",
            "offer_watches": watches,
            "parsed_watches": watches,
            "rows": rows,
        },
    }


def _training_row(*, row_index: int = 0) -> dict:
    return {
        "id": f"row-{row_index}",
        "import_log_id": IMPORT_LOG_ID,
        "row_index": row_index,
        "status": "approved",
        "raw_row_text": "Rolex 126331 new $23000",
        "detected_reference": "126331",
        "detected_price": 23_000,
        "detected_currency": "USD",
        "created_offer_id": OFFER_ID,
    }


class TestActivityParserReviewAction:
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_review_action_when_training_rows_exist(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
    ) -> None:
        mock_list_rows.return_value = [_training_row(), _training_row(row_index=1)]
        import_log = _duplicate_only_import_log(watches_count=2)

        action = build_activity_parser_review_action(
            import_log,
            {"raw_text": "dealer list"},
            show_parser_review=True,
        )

        assert action["show"] is True
        assert action["action"] == "review"
        assert action["label"] == "Review parsed rows"
        assert action["url"] == f"/parser-training/{IMPORT_LOG_ID}/rows"
        assert "2 parsed rows" in action["status_text"]
        assert "0 pending" in action["status_text"]
        assert "2 approved" in action["status_text"]

    @patch("database.list_parser_training_rows_for_import", return_value=[])
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_prepare_action_when_rows_missing_but_summary_exists(
        self,
        _supported: MagicMock,
        _mock_list_rows: MagicMock,
    ) -> None:
        import_log = _duplicate_only_import_log()

        action = build_activity_parser_review_action(
            import_log,
            {"raw_text": "dealer list"},
            show_parser_review=True,
        )

        assert action["show"] is True
        assert action["action"] == "prepare"
        assert action["label"] == "Prepare rows for review"
        assert action["url"] == f"/parser-training/{IMPORT_LOG_ID}/prepare-rows"
        assert "3 parsed rows" in action["status_text"]

    def test_non_admin_action_hidden(self) -> None:
        action = build_activity_parser_review_action(
            _duplicate_only_import_log(),
            {"raw_text": "dealer list"},
            show_parser_review=False,
        )

        assert action["show"] is False


class TestActivityDetailParserReviewUI:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_import_detail_shows_review_parsed_rows_when_rows_exist(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_get_message.return_value = {"raw_text": "Rolex list"}
        mock_list_rows.return_value = [_training_row() for _ in range(3)]

        response = TestClient(app).get(f"/activity/{IMPORT_LOG_ID}")

        assert response.status_code == 200
        html = response.text
        assert "Review parsed rows" in html
        assert f'href="/parser-training/{IMPORT_LOG_ID}/rows"' in html
        assert "3 parsed rows" in html
        assert "0 pending" in html
        assert "3 approved" in html

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_duplicate_only_import_still_shows_review_action(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_get_message.return_value = {"raw_text": "Rolex list"}
        mock_list_rows.return_value = [_training_row() for _ in range(3)]

        response = TestClient(app).get(f"/activity/{IMPORT_LOG_ID}")

        assert response.status_code == 200
        assert "Duplicate offers" in response.text
        assert "Review parsed rows" in response.text

    @patch("app.get_current_user", return_value=TRADER_ONE)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_non_admin_does_not_see_parser_review_action(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_get_message.return_value = {"raw_text": "Rolex list"}
        mock_list_rows.return_value = [_training_row()]

        response = TestClient(app).get(f"/activity/{IMPORT_LOG_ID}")

        assert response.status_code == 200
        assert "Review parsed rows" not in response.text
        assert "Prepare rows for review" not in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("database.update_parser_training_row")
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("parser_workbench.reprocess_import_log")
    @patch("ingest.ingest_message")
    def test_opening_review_link_does_not_reprocess_or_create_offers(
        self,
        mock_ingest: MagicMock,
        mock_reprocess: MagicMock,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
        mock_update: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_get_message.return_value = {"raw_text": "Rolex list"}
        mock_list_rows.return_value = [_training_row() for _ in range(3)]

        response = TestClient(app).get(f"/parser-training/{IMPORT_LOG_ID}/rows")

        assert response.status_code == 200
        assert IMPORT_LOG_ID in response.text
        mock_ingest.assert_not_called()
        mock_reprocess.assert_not_called()
        mock_update.assert_not_called()

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_review_page_shows_back_to_import_detail_link(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_get_message.return_value = {"raw_text": "Rolex list"}
        mock_list_rows.return_value = [_training_row()]

        response = TestClient(app).get(f"/parser-training/{IMPORT_LOG_ID}/rows")

        assert response.status_code == 200
        assert "Back to import detail" in response.text
        assert f"/activity/{IMPORT_LOG_ID}" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("database.list_parser_training_rows_for_import", return_value=[])
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_missing_review_rows_shows_prepare_action(
        self,
        _supported: MagicMock,
        _mock_list_rows: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_get_message.return_value = {"raw_text": "Rolex list"}

        response = TestClient(app).get(f"/activity/{IMPORT_LOG_ID}")

        assert response.status_code == 200
        assert "Prepare rows for review" in response.text
        assert f'action="/parser-training/{IMPORT_LOG_ID}/prepare-rows"' in response.text


class TestPrepareParserTrainingRows:
    @patch("parser_training_engine.re_evaluate_parser_training_rows")
    @patch("parser_training_engine.sync_training_rows_after_ingest")
    @patch("database.list_parser_training_rows_for_import", return_value=[])
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_prepare_creates_rows_without_reingest(
        self,
        _supported: MagicMock,
        _mock_list_rows: MagicMock,
        mock_sync: MagicMock,
        mock_re_evaluate: MagicMock,
    ) -> None:
        mock_sync.return_value = [{"id": "row-1"}, {"id": "row-2"}]

        result = prepare_parser_training_rows_for_import(_duplicate_only_import_log())

        assert result["status"] == "prepared"
        assert result["rows_created"] == 2
        mock_sync.assert_called_once()
        mock_re_evaluate.assert_called_once_with(import_log_id=IMPORT_LOG_ID)

    @patch("parser_training_engine.sync_training_rows_after_ingest")
    @patch("database.list_parser_training_rows_for_import")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_prepare_is_idempotent_when_rows_already_exist(
        self,
        _supported: MagicMock,
        mock_list_rows: MagicMock,
        mock_sync: MagicMock,
    ) -> None:
        mock_list_rows.return_value = [_training_row()]

        result = prepare_parser_training_rows_for_import(_duplicate_only_import_log())

        assert result["status"] == "already_prepared"
        assert result["rows_created"] == 0
        mock_sync.assert_not_called()

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_import_log")
    @patch("app.prepare_parser_training_rows_for_import")
    @patch("ingest.ingest_message")
    def test_prepare_route_redirects_to_import_rows_page(
        self,
        mock_ingest: MagicMock,
        mock_prepare: MagicMock,
        mock_get_import_log: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_prepare.return_value = {"status": "prepared", "rows_created": 3}

        response = TestClient(app).post(
            f"/parser-training/{IMPORT_LOG_ID}/prepare-rows",
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == f"/parser-training/{IMPORT_LOG_ID}/rows"
        mock_prepare.assert_called_once()
        mock_ingest.assert_not_called()


class TestSaveRowWorkflowStillUsed:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._can_access_import_log", return_value=True)
    @patch("app.get_message_by_id")
    @patch("app.get_import_log")
    @patch("app.correct_training_row")
    @patch("database.get_parser_training_row")
    def test_save_row_correction_uses_existing_final_offer_workflow(
        self,
        mock_get_row: MagicMock,
        mock_correct: MagicMock,
        mock_get_import_log: MagicMock,
        mock_get_message: MagicMock,
        _mock_access: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_row.return_value = {
            "id": "row-1",
            "import_log_id": IMPORT_LOG_ID,
        }
        mock_get_import_log.return_value = _duplicate_only_import_log()
        mock_get_message.return_value = {"raw_text": "Rolex list"}
        mock_correct.return_value = {"id": "row-1", "status": "corrected"}

        response = TestClient(app).post(
            "/parser-training/rows/row-1/correct",
            data={"reference": "126331", "condition": "New"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        mock_correct.assert_called_once()
        assert mock_correct.call_args.args[1]["reference"] == "126331"

    def test_build_activity_detail_includes_parser_review_for_admin(self) -> None:
        with (
            patch("database.parser_training_rows_supported", return_value=True),
            patch("database.list_parser_training_rows_for_import", return_value=[_training_row()]),
        ):
            detail = build_activity_detail(
                _duplicate_only_import_log(),
                {"raw_text": "Rolex list"},
                show_parser_review=True,
            )

        assert detail["parser_review"]["action"] == "review"
        assert re.search(rf"/parser-training/{IMPORT_LOG_ID}/rows", detail["parser_review"]["url"])
