"""Tests for Sprint 50.0 — Offer-Centric Parser Training Engine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from navigation import visible_nav_groups
from parser_training_center import (
    format_training_row_display,
    load_parser_training_containers,
    trace_parser_training_import,
    unique_references_from_rows,
)
from parser_training_engine import (
    backfill_parser_training_rows_for_recent_imports,
    build_training_row_payload,
    bulk_training_row_action,
    correct_training_row,
    sync_training_rows_after_ingest,
)
from permissions import can_view_page
from tests.conftest import ADMIN_USER, TRADER_ONE

pytestmark = pytest.mark.no_auto_login

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MESSAGE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ROW_ID_VALID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ROW_ID_INVALID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OFFER_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _valid_watch() -> dict:
    return {
        "brand": "Rolex",
        "reference": "126610LN",
        "condition": "Pre-Owned",
        "condition_explicit": True,
        "condition_confidence": "high",
        "reference_high_confidence": True,
        "original_price": 12500,
        "original_currency": "USD",
        "usd_price": 12500,
        "source_line": "Rolex 126610LN used 12500 USD",
        "dealer_list_line": True,
    }


def _invalid_watch() -> dict:
    return {
        "brand": "Rolex",
        "reference": None,
        "original_price": 12500,
        "original_currency": "USD",
        "source_line": "Rolex ??? 12500 USD",
        "dealer_list_line": True,
    }


def _import_log(*, status: str = "warning", watches_count: int = 0) -> dict:
    watches = [_valid_watch() for _ in range(watches_count)] if watches_count else []
    return {
        "id": IMPORT_LOG_ID,
        "message_id": MESSAGE_ID,
        "import_time": "2026-06-27T12:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+31612345678",
        "status": status,
        "watches_parsed": watches_count,
        "summary": {
            "offer_watches": watches,
            "parsed_watches": watches,
            "bulk_import": bool(watches),
            "message_id": MESSAGE_ID,
        } if watches else {},
    }


class TestTrainingRowPayload:
    def test_valid_row_payload_is_approved_with_offer(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_valid_watch(),
            created_offer_id=OFFER_ID,
        )

        assert payload["status"] == "approved"
        assert payload["created_offer_id"] == OFFER_ID
        assert payload["row_index"] == 0

    def test_invalid_row_payload_is_pending_without_offer(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=1,
            watch=_invalid_watch(),
            created_offer_id=None,
        )

        assert payload["status"] == "pending_review"
        assert payload["created_offer_id"] is None
        assert payload["issue_types"]

    def test_multi_row_import_builds_one_payload_per_watch(self) -> None:
        watches = [_valid_watch(), _invalid_watch(), _valid_watch()]
        offer_ids = {0: OFFER_ID, 1: None, 2: "offer-2"}

        payloads = [
            build_training_row_payload(
                import_log_id=IMPORT_LOG_ID,
                source_message_id=MESSAGE_ID,
                row_index=index,
                watch=watch,
                created_offer_id=offer_ids.get(index),
            )
            for index, watch in enumerate(watches)
        ]

        assert len(payloads) == 3
        assert payloads[0]["status"] == "approved"
        assert payloads[1]["status"] == "pending_review"
        assert payloads[2]["status"] == "approved"


class TestSyncTrainingRowsAfterIngest:
    @patch("database.bulk_upsert_parser_training_rows")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_sync_upserts_all_rows(
        self,
        _supported: MagicMock,
        mock_bulk: MagicMock,
    ) -> None:
        mock_bulk.return_value = [{"id": ROW_ID_VALID}]

        result = sync_training_rows_after_ingest(
            _import_log(),
            message_id=MESSAGE_ID,
            watches=[_valid_watch(), _invalid_watch()],
            offer_ids_by_index={0: OFFER_ID, 1: None},
        )

        assert len(result) == 1
        assert mock_bulk.call_count == 1
        payloads = mock_bulk.call_args[0][0]
        assert len(payloads) == 2
        assert payloads[0]["created_offer_id"] == OFFER_ID
        assert payloads[1]["created_offer_id"] is None


class TestRowCorrection:
    @patch("database.update_parser_training_row")
    @patch("parser_training_engine.create_offer_for_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    def test_row_correction_creates_offer_for_pending_row(
        self,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_create_offer: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        mock_get_row.return_value = {
            "id": ROW_ID_INVALID,
            "import_log_id": IMPORT_LOG_ID,
            "source_message_id": MESSAGE_ID,
            "row_index": 1,
            "detected_brand": "Rolex",
            "detected_reference": "126610LN",
            "detected_price": 12500,
            "detected_currency": "USD",
            "status": "pending_review",
            "issue_types": ["missing_reference"],
        }
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_create_offer.return_value = ({"id": OFFER_ID}, True)
        mock_update.return_value = {"id": ROW_ID_INVALID, "status": "corrected", "created_offer_id": OFFER_ID}

        result = correct_training_row(
            ROW_ID_INVALID,
            {"reference": "126610LN", "condition": "Pre-Owned"},
            learn_mode="row_only",
        )

        mock_create_offer.assert_called_once()
        assert result["status"] == "corrected"


class TestBulkActions:
    @patch("parser_training_engine.correct_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    def test_bulk_brand_updates_selected_rows_only(
        self,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_correct: MagicMock,
    ) -> None:
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_correct.side_effect = [
            {"id": ROW_ID_VALID, "normalized_brand": "Patek Philippe"},
            {"id": ROW_ID_INVALID, "normalized_brand": "Patek Philippe"},
        ]

        bulk_training_row_action(
            IMPORT_LOG_ID,
            "set_brand",
            row_ids=[ROW_ID_VALID, ROW_ID_INVALID],
            brand_name="Patek Philippe",
        )

        assert mock_correct.call_count == 2
        assert mock_correct.call_args_list[0][0][0] == ROW_ID_VALID
        assert mock_correct.call_args_list[1][0][0] == ROW_ID_INVALID

    @patch("parser_training_engine.correct_training_row")
    @patch("database.create_reference_brand_mapping")
    @patch("database.reference_brand_mappings_supported", return_value=True)
    @patch("database.get_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    def test_map_references_creates_mappings_for_selected_refs(
        self,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_get_row: MagicMock,
        _supported: MagicMock,
        mock_create_mapping: MagicMock,
        mock_correct: MagicMock,
    ) -> None:
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID, "dealer_id": "dealer-1"}
        mock_get_row.return_value = {
            "id": ROW_ID_VALID,
            "normalized_reference": "4020T",
            "detected_reference": "4020T",
        }
        mock_correct.return_value = {"id": ROW_ID_VALID}

        bulk_training_row_action(
            IMPORT_LOG_ID,
            "map_references",
            row_ids=[ROW_ID_VALID],
            brand_name="Vacheron Constantin",
            reference_brand_mappings=[{"reference": "4020T", "selected": True}],
        )

        mock_create_mapping.assert_called_once_with(
            reference="4020T",
            brand_name="Vacheron Constantin",
            source="parser_training",
        )
        mock_correct.assert_called_once()


class TestParserTrainingRoutes:
    @staticmethod
    def _mock_overview_imports(import_logs: list[dict]) -> tuple:
        return (
            patch("database.list_parser_training_import_logs", return_value=import_logs),
            patch(
                "parser_training_center._visible_parser_training_import_logs",
                side_effect=lambda logs, _user: logs,
            ),
        )

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    def test_admin_sees_offer_centric_training_center(
        self,
        _schema: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        imports_patch, visible_patch = self._mock_overview_imports([_import_log(watches_count=3)])
        with imports_patch, visible_patch, patch(
            "database.list_parser_training_import_summaries",
            return_value=[
                {
                    "import_log_id": IMPORT_LOG_ID,
                    "total_rows": 3,
                    "approved_rows": 2,
                    "pending_review_rows": 1,
                    "ignored_rows": 0,
                    "failed_rows": 0,
                    "corrected_rows": 0,
                }
            ],
        ):
            client = TestClient(app)
            response = client.get("/parser-training")

        assert response.status_code == 200
        assert "Parser Training Center" in response.text
        assert "Open rows" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "missing", "message": "table missing"},
    )
    def test_missing_migration_shows_warning_banner(
        self,
        _schema: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        imports_patch, visible_patch = self._mock_overview_imports([_import_log(watches_count=2)])
        with imports_patch, visible_patch:
            client = TestClient(app)
            response = client.get("/parser-training")

        assert response.status_code == 200
        assert "Migration required" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    def test_activity_success_import_appears_in_training_center(
        self,
        _schema: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        imports_patch, visible_patch = self._mock_overview_imports(
            [_import_log(status="success", watches_count=12)]
        )
        with imports_patch, visible_patch:
            client = TestClient(app)
            response = client.get("/parser-training")

        assert response.status_code == 200
        assert "Dealer A" in response.text
        assert "Open rows" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    @patch("app.backfill_parser_training_rows_for_recent_imports")
    def test_training_shows_containers_after_backfill(
        self,
        mock_backfill: MagicMock,
        _schema: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        imports_patch, visible_patch = self._mock_overview_imports(
            [_import_log(status="success", watches_count=5)]
        )
        mock_backfill.return_value = {
            "scanned": 1,
            "processed": 1,
            "rows_created": 5,
            "skipped_existing": 0,
            "skipped_no_rows": 0,
            "errors": [],
        }
        with imports_patch, visible_patch:
            client = TestClient(app)
            page = client.get("/parser-training")
            assert page.status_code == 200
            assert "Open rows" in page.text

            redirect = client.post("/parser-training/backfill-recent", data={"limit": 50}, follow_redirects=False)
            assert redirect.status_code == 303

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._parser_review_import_logs", return_value=[])
    @patch("database.get_messages_by_ids", return_value={})
    def test_legacy_parser_review_still_works(
        self,
        _mock_messages: MagicMock,
        _mock_logs: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert "Parser Training Center" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_ai_workbench_redirects_to_new_training_ui(self, _mock_user: MagicMock) -> None:
        client = TestClient(app)
        response = client.get("/ai-workbench", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/parser-training"

    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_cannot_access_parser_training(self, _mock_user: MagicMock) -> None:
        assert can_view_page(TRADER_ONE, "/parser-training") is False

        client = TestClient(app)
        response = client.get("/parser-training")

        assert response.status_code == 403

    def test_admin_nav_points_to_parser_training(self) -> None:
        groups = visible_nav_groups(ADMIN_USER)
        ai_group = next(group for group in groups if group["label"] == "AI")
        training_link = next(
            link for link in ai_group["links"] if link["label"] == "Parser Training Center"
        )

        assert training_link["path"] == "/parser-training"


class TestTrainingCenterHelpers:
    def test_format_training_row_display_includes_suggestions_placeholder(self) -> None:
        row = format_training_row_display(
            {
                "id": ROW_ID_VALID,
                "row_index": 0,
                "raw_row_text": "4020T 12500 USD",
                "detected_reference": "4020T",
                "detected_price": 12500,
                "detected_currency": "USD",
                "confidence_overall": 72,
                "status": "pending_review",
                "issue_types": ["brand_confidence_low"],
                "parser_explanation": {"suggestions": {"suggested_brand": None}},
            }
        )

        assert row["needs_review"] is True
        assert row["suggestions"]["suggested_brand"] is None
        assert "Brand confidence low" in row["issues"][0]

    def test_unique_references_from_rows(self) -> None:
        refs = unique_references_from_rows(
            [
                {"reference": "4020T"},
                {"reference": "4300V"},
                {"reference": "4020T"},
            ]
        )

        assert refs == ["4020T", "4300V"]

    @patch("database.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("database.list_parser_training_import_summaries")
    def test_load_containers_merges_import_metadata(
        self,
        mock_summaries: MagicMock,
        _supported: MagicMock,
        _attach: MagicMock,
    ) -> None:
        mock_summaries.return_value = [
            {
                "import_log_id": IMPORT_LOG_ID,
                "total_rows": 5,
                "approved_rows": 3,
                "pending_review_rows": 2,
                "ignored_rows": 0,
                "failed_rows": 0,
                "corrected_rows": 0,
            }
        ]
        containers, totals = load_parser_training_containers(
            [_import_log(watches_count=5)],
            format_timestamp=lambda value: value or "",
        )

        assert totals["total_imports"] == 1
        assert containers[0]["dealer"]
        assert containers[0]["rows_url"] == f"/parser-training/{IMPORT_LOG_ID}/rows"
        assert containers[0]["approved_rows"] == 3
        assert containers[0]["pending_review_rows"] == 2

    @patch("database.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("database.parser_training_rows_supported", return_value=True)
    @patch("database.list_parser_training_import_summaries")
    def test_all_approved_import_still_appears_in_training_center(
        self,
        mock_summaries: MagicMock,
        _supported: MagicMock,
        _attach: MagicMock,
    ) -> None:
        mock_summaries.return_value = [
            {
                "import_log_id": IMPORT_LOG_ID,
                "total_rows": 8,
                "approved_rows": 8,
                "pending_review_rows": 0,
                "ignored_rows": 0,
                "failed_rows": 0,
                "corrected_rows": 0,
            }
        ]
        containers, totals = load_parser_training_containers(
            [_import_log(status="success", watches_count=8)],
            format_timestamp=lambda value: value or "",
        )

        assert totals["total_imports"] == 1
        assert containers[0]["approved_rows"] == 8
        assert containers[0]["pending_review_rows"] == 0


class TestParserTrainingBackfillRoute:
    def test_url_for_parser_training_backfill_resolves(self) -> None:
        assert app.url_path_for("parser_training_backfill_recent") == "/parser-training/backfill-recent"

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    def test_parser_training_template_renders_successfully(
        self,
        _schema: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        imports_patch, visible_patch = TestParserTrainingRoutes._mock_overview_imports([])
        with imports_patch, visible_patch:
            client = TestClient(app)
            response = client.get("/parser-training")

        assert response.status_code == 200
        assert "Parser Training Center" in response.text
        assert "/parser-training/backfill-recent" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_post_backfill_works(
        self,
        _mock_user: MagicMock,
    ) -> None:
        with patch(
            "app.backfill_parser_training_rows_for_recent_imports",
            return_value={
                "scanned": 4,
                "processed": 2,
                "rows_created": 10,
                "skipped_existing": 1,
                "skipped_no_rows": 1,
                "errors": [],
            },
        ):
            client = TestClient(app)
            response = client.post(
                "/parser-training/backfill-recent",
                data={"limit": 50},
                follow_redirects=False,
            )

        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith("/parser-training?")
        assert "backfill=1" in location
        assert "scanned=4" in location
        assert "rows=10" in location
        assert "imports=2" in location

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("database.list_parser_training_import_summaries", return_value=[])
    @patch("database.parser_training_rows_supported", return_value=False)
    @patch("app.parser_training_rows_schema_status", return_value={"status": "supported", "message": "ok"})
    @patch("database.list_parser_training_import_logs")
    @patch("database._execute_import_log_summary_batch")
    def test_parser_training_page_renders_when_summary_batch_fails(
        self,
        mock_batch: MagicMock,
        mock_import_logs: MagicMock,
        _schema: MagicMock,
        _supported: MagicMock,
        _summaries: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_import_logs.return_value = [
            {
                "id": "log-1",
                "message_id": "msg-1",
                "import_time": "2026-06-27T12:00:00+00:00",
                "group_name": "HK Dealers",
                "dealer_alias": "Dealer A",
                "dealer_whatsapp": "+31612345678",
                "watches_parsed": 3,
                "status": "success",
                "summary": {"offer_watches": [{}, {}, {}], "message_type": "offer"},
            }
        ]
        mock_batch.side_effect = Exception("JSON could not be generated - Cloudflare 521")

        with patch(
            "parser_training_center._visible_parser_training_import_logs",
            side_effect=lambda logs, _user: logs,
        ):
            client = TestClient(app)
            response = client.get("/parser-training")

        assert response.status_code == 200
        assert "Parser Training Center" in response.text
        assert "Dealer A" in response.text


class TestP0ParserTrainingDebug:
    @patch("database.list_parser_training_rows_for_import", return_value=[])
    @patch("database.attach_import_log_summaries", side_effect=lambda logs: logs)
    @patch("database.get_import_log")
    @patch(
        "database.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_trace_reports_missing_sync_reason(
        self,
        _supported: MagicMock,
        _schema: MagicMock,
        mock_get_import: MagicMock,
        _attach: MagicMock,
        _rows: MagicMock,
    ) -> None:
        mock_get_import.return_value = _import_log(status="success", watches_count=4)

        trace = trace_parser_training_import(IMPORT_LOG_ID, user=ADMIN_USER)

        assert trace["import_log_found"] is True
        assert trace["summary_row_count"] == 4
        assert trace["parser_training_rows_count"] == 0
        assert "parser_training_rows_not_synced" in trace["hidden_reason"]
        assert trace["visible_in_training_center"] is True

    @patch("database.bulk_upsert_parser_training_rows")
    @patch("database.list_activity_import_logs", return_value=[])
    @patch("database.list_parser_training_candidate_import_logs")
    @patch("database.list_parser_training_rows_for_import", return_value=[])
    @patch("database.attach_import_log_summaries")
    @patch("database.parser_training_rows_supported", return_value=True)
    def test_backfill_recent_creates_missing_rows(
        self,
        _supported: MagicMock,
        mock_attach: MagicMock,
        _existing: MagicMock,
        mock_candidates: MagicMock,
        _activity: MagicMock,
        mock_bulk: MagicMock,
    ) -> None:
        import_log = _import_log(status="success", watches_count=2)
        import_log["summary"]["rows"] = [
            {"offer_id": OFFER_ID},
            {"offer_id": None},
        ]
        mock_candidates.return_value = [import_log]
        mock_attach.return_value = [import_log]
        mock_bulk.return_value = [{"id": ROW_ID_VALID}, {"id": ROW_ID_INVALID}]

        result = backfill_parser_training_rows_for_recent_imports(limit=10)

        assert result["scanned"] == 1
        assert result["processed"] == 1
        assert result["rows_created"] == 2
        assert IMPORT_LOG_ID in result["import_log_ids"]
        mock_bulk.assert_called_once()

    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_backfill_route_exists_and_returns_redirect(
        self,
        _mock_user: MagicMock,
    ) -> None:
        with patch(
            "app.backfill_parser_training_rows_for_recent_imports",
            return_value={
                "scanned": 5,
                "processed": 1,
                "rows_created": 3,
                "skipped_existing": 2,
                "skipped_no_rows": 1,
                "errors": [],
            },
        ):
            client = TestClient(app)
            response = client.post(
                "/parser-training/backfill-recent",
                data={"limit": 50},
                follow_redirects=False,
            )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/parser-training?")
        assert "backfill=1" in response.headers["location"]

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch(
        "app.parser_training_rows_schema_status",
        return_value={"status": "supported", "message": "ok"},
    )
    def test_training_template_posts_to_backfill_route(
        self,
        _schema: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        imports_patch, visible_patch = TestParserTrainingRoutes._mock_overview_imports(
            [_import_log(status="success", watches_count=3)]
        )
        with imports_patch, visible_patch:
            client = TestClient(app)
            response = client.get("/parser-training")

        assert response.status_code == 200
        assert "parser-training/backfill-recent" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.trace_parser_training_import")
    def test_debug_route_returns_trace_json(
        self,
        mock_trace: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_trace.return_value = {
            "import_log_found": True,
            "summary_row_count": 4,
            "parser_training_rows_count": 0,
            "hidden_reason": "parser_training_rows_not_synced",
        }
        client = TestClient(app)
        response = client.get(f"/parser-training/debug/{IMPORT_LOG_ID}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["import_log_found"] is True
        assert payload["parser_training_rows_count"] == 0

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.backfill_parser_training_rows_for_recent_imports")
    def test_backfill_recent_route_redirects_with_counts(
        self,
        mock_backfill: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_backfill.return_value = {
            "scanned": 10,
            "processed": 2,
            "rows_created": 15,
            "skipped_existing": 5,
            "skipped_no_rows": 2,
            "import_log_ids": [IMPORT_LOG_ID],
            "errors": [],
        }
        client = TestClient(app)
        response = client.post("/parser-training/backfill-recent", data={"limit": 50}, follow_redirects=False)

        assert response.status_code == 303
        assert "backfill=1" in response.headers["location"]
        assert "scanned=10" in response.headers["location"]
        assert "rows=15" in response.headers["location"]
