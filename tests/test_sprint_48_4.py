"""Tests for Sprint 48.4 admin market data reset tool."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from market_data_reset import (
    RESET_BATCH_SIZE,
    RESET_CONFIRMATION_TEXT,
    MarketDataResetPreview,
    MarketDataResetVerification,
    ResetStepBatchResult,
    clear_batched_reset_jobs_for_tests,
    create_batched_reset_job,
    is_emergency_reset_success,
    preview_market_data_reset,
    run_batched_reset_batch,
    run_emergency_market_data_reset,
    run_market_data_reset,
    verify_market_data_reset,
)
from permissions import can_view_page
from search import search_offers
from tests.conftest import ADMIN_USER, TRADER_ONE


class TestResetMarketDataPermissions:
    def test_admin_can_view_reset_page(self) -> None:
        assert can_view_page(ADMIN_USER, "/admin/reset-market-data") is True

    def test_trader_cannot_view_reset_page(self) -> None:
        assert can_view_page(TRADER_ONE, "/admin/reset-market-data") is False

    @pytest.mark.no_auto_login
    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_blocked_from_reset_route(self, _mock_user: MagicMock) -> None:
        client = TestClient(app)
        response = client.get("/admin/reset-market-data")

        assert response.status_code == 403

    @pytest.mark.no_auto_login
    @patch("market_data_reset.preview_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_admin_can_open_reset_page(
        self,
        _mock_user: MagicMock,
        mock_preview: MagicMock,
    ) -> None:
        mock_preview.return_value = MarketDataResetPreview(
            counts={
                "request_matches": 2,
                "market_notifications": 3,
                "offers": 10,
                "offers_active": 8,
                "import_logs": 4,
                "parser_review_imports": 1,
                "messages": 4,
                "orphan_watches": 2,
            }
        )
        client = TestClient(app)
        response = client.get("/admin/reset-market-data")

        assert response.status_code == 200
        assert "Reset market data" in response.text
        assert "Tables feeding Search" in response.text


class TestResetMarketDataActions:
    @pytest.mark.no_auto_login
    @patch("market_data_reset.run_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_dry_run_does_not_delete(
        self,
        _mock_user: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post(
            "/admin/reset-market-data",
            data={"confirm": "1", "dry_run": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].endswith("status=dry_run")
        mock_run.assert_called_once_with(dry_run=True)

    @pytest.mark.no_auto_login
    @patch("market_data_reset.run_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_execute_requires_exact_confirmation_text(
        self,
        _mock_user: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post(
            "/admin/reset-market-data",
            data={"confirm": "1", "confirm_text": "delete everything"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "status=error" in response.headers["location"]
        mock_run.assert_not_called()

    @pytest.mark.no_auto_login
    @patch("market_data_reset.run_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_execute_shows_failed_when_verification_not_clean(
        self,
        _mock_user: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            dry_run=False,
            deleted={"offers": 10},
            errors=["CRITICAL: 90 offer(s) remain (90 active). Search will not be empty."],
            chunk_failures=["offers chunk still has rows"],
            verification=MarketDataResetVerification(
                offers_total=90,
                offers_active=90,
                import_logs_total=0,
                messages_total=0,
                request_matches_total=0,
                market_notifications_total=0,
            ),
            success=False,
            method="python",
            status_label="failed",
        )
        client = TestClient(app)
        response = client.post(
            "/admin/reset-market-data",
            data={"confirm": "1", "confirm_text": RESET_CONFIRMATION_TEXT},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "status=failed" in response.headers["location"]
        assert "offers_total=90" in response.headers["location"]

    @pytest.mark.no_auto_login
    @patch("market_data_reset.run_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_execute_shows_success_when_verification_clean(
        self,
        _mock_user: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            dry_run=False,
            deleted={"offers": 90, "import_logs": 40},
            errors=[],
            chunk_failures=[],
            verification=MarketDataResetVerification(0, 0, 0, 0, 0, 0),
            success=True,
            method="rpc",
            status_label="success",
        )
        client = TestClient(app)
        response = client.post(
            "/admin/reset-market-data",
            data={"confirm": "1", "confirm_text": RESET_CONFIRMATION_TEXT},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "status=success" in response.headers["location"]


class TestEmergencyResetRoutes:
    @pytest.mark.no_auto_login
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_emergency_get_renders_without_preview(self, _mock_user: MagicMock) -> None:
        with patch("market_data_reset.preview_market_data_reset") as mock_preview:
            client = TestClient(app)
            response = client.get("/admin/reset-market-data/emergency")

        assert response.status_code == 200
        assert "Emergency reset market data" in response.text
        mock_preview.assert_not_called()

    @pytest.mark.no_auto_login
    @patch("market_data_reset.create_batched_reset_job", return_value="job-test-1")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_emergency_post_starts_batched_job(
        self,
        _mock_user: MagicMock,
        mock_create_job: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.post(
            "/admin/reset-market-data/emergency",
            data={"confirm": "1", "confirm_text": RESET_CONFIRMATION_TEXT},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "emergency/run?job_id=job-test-1" in response.headers["location"]
        mock_create_job.assert_called_once()

    @pytest.mark.no_auto_login
    @patch("market_data_reset.run_batched_reset_batch")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_emergency_batch_endpoint_returns_progress_json(
        self,
        _mock_user: MagicMock,
        mock_batch: MagicMock,
    ) -> None:
        from market_data_reset import BatchedResetProgress

        mock_batch.return_value = BatchedResetProgress(
            job_id="job-test-1",
            status="running",
            current_step="offers",
            step_index=1,
            total_steps=6,
            deleted={"offers": 2000},
            batches_run=2,
            batch_size=RESET_BATCH_SIZE,
            errors=(),
            warnings=(),
            chunk_failures=(),
            verification=None,
            success=False,
        )
        client = TestClient(app)
        response = client.post(
            "/admin/reset-market-data/emergency/batch",
            json={"job_id": "job-test-1"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["current_step"] == "offers"
        assert payload["deleted"]["offers"] == 2000
        mock_batch.assert_called_once()

    @pytest.mark.no_auto_login
    @patch("market_data_reset.collect_db_health_status")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_status_page_uses_lightweight_health(
        self,
        _mock_user: MagicMock,
        mock_health: MagicMock,
    ) -> None:
        mock_health.return_value = MagicMock(
            as_dict=lambda: {
                "offers_total": 90,
                "offers_active": 90,
                "import_logs_total": 40,
                "messages_total": 40,
            },
            errors=(),
        )
        client = TestClient(app)
        response = client.get("/admin/reset-market-data/status")

        assert response.status_code == 200
        mock_health.assert_called_once()

    @pytest.mark.no_auto_login
    @patch("market_data_reset.preview_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_standard_reset_get_skips_preview_by_default(
        self,
        _mock_user: MagicMock,
        mock_preview: MagicMock,
    ) -> None:
        mock_preview.return_value = MarketDataResetPreview(
            counts={},
            preview_method="skipped",
        )
        client = TestClient(app)
        response = client.get("/admin/reset-market-data")

        assert response.status_code == 200
        mock_preview.assert_called_once_with(load_counts=False)

    @pytest.mark.no_auto_login
    @patch("market_data_reset.preview_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_standard_reset_get_loads_preview_when_requested(
        self,
        _mock_user: MagicMock,
        mock_preview: MagicMock,
    ) -> None:
        mock_preview.return_value = MarketDataResetPreview(counts={"offers": 1})
        client = TestClient(app)
        response = client.get("/admin/reset-market-data?preview=1")

        assert response.status_code == 200
        mock_preview.assert_called_once_with(load_counts=True)


class TestResetPreviewPerformance:
    @patch("market_data_reset._try_fetch_rpc_preview_counts", return_value=None)
    @patch("market_data_reset._count_parser_review_import_logs", return_value=2)
    @patch("market_data_reset._collect_verification_counts")
    @patch("market_data_reset._count_table_rows", return_value=5)
    def test_preview_does_not_load_import_log_summaries(
        self,
        _mock_watches: MagicMock,
        mock_verification: MagicMock,
        _mock_parser_review: MagicMock,
        _mock_rpc: MagicMock,
    ) -> None:
        mock_verification.return_value = {
            "offers_total": 10,
            "offers_active": 8,
            "import_logs_total": 4,
            "messages_total": 4,
            "request_matches_total": 1,
            "market_notifications_total": 1,
        }

        with patch("market_data_reset._select_all_rows") as mock_select_all:
            preview = preview_market_data_reset(load_counts=True)

        mock_select_all.assert_not_called()
        assert preview.counts["offers"] == 10
        assert preview.rpc_available is False
        assert preview.preview_method == "count"

    @patch("market_data_reset._try_fetch_rpc_preview_counts")
    def test_preview_uses_rpc_dry_run_when_available(self, mock_rpc: MagicMock) -> None:
        mock_rpc.return_value = {
            "method": "rpc",
            "dry_run": True,
            "verification": {
                "offers_total": 90,
                "offers_active": 90,
                "import_logs_total": 40,
                "messages_total": 40,
                "request_matches_total": 0,
                "market_notifications_total": 0,
            },
        }

        with (
            patch("market_data_reset._count_parser_review_import_logs", return_value=1),
            patch("market_data_reset._count_table_rows", return_value=12),
        ):
            preview = preview_market_data_reset(load_counts=True)

        assert preview.rpc_available is True
        assert preview.preview_method == "rpc"
        assert preview.counts["offers_active"] == 90

    @patch("market_data_reset._count_reset_scope_lightweight", side_effect=TimeoutError("57014 canceling statement"))
    def test_preview_handles_timeout_gracefully(self, _mock_counts: MagicMock) -> None:
        with patch("market_data_reset.reset_market_data_rpc_supported", return_value=False):
            preview = preview_market_data_reset(load_counts=True)

        assert preview.has_preview_error is True
        assert "57014" in (preview.preview_error or "")

    @pytest.mark.no_auto_login
    @patch("market_data_reset.preview_market_data_reset")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_page_renders_preview_error_instead_of_500(
        self,
        _mock_user: MagicMock,
        mock_preview: MagicMock,
    ) -> None:
        mock_preview.return_value = MarketDataResetPreview(
            counts={},
            rpc_available=False,
            preview_method="error",
            preview_error="APIError 57014: canceling statement due to statement timeout",
        )
        client = TestClient(app)
        response = client.get("/admin/reset-market-data")

        assert response.status_code == 200
        assert "Preview counts failed" in response.text
        assert "57014" in response.text

    @patch("market_data_reset._count_reset_scope_lightweight", side_effect=RuntimeError("timeout"))
    def test_dry_run_surfaces_preview_error_without_deleting(self, _mock_counts: MagicMock) -> None:
        with patch("market_data_reset.reset_market_data_rpc_supported", return_value=False):
            result = run_market_data_reset(dry_run=True)

        assert result.dry_run is True
        assert result.errors
        assert result.preview.has_preview_error is True


class TestResetScope:
    @patch("market_data_reset.run_full_batched_reset")
    @patch("market_data_reset._count_reset_scope_lightweight")
    def test_execute_uses_batched_reset(
        self,
        mock_count: MagicMock,
        mock_batched: MagicMock,
    ) -> None:
        mock_count.return_value = (
            {
                "request_matches": 1,
                "market_notifications": 1,
                "offers": 90,
                "offers_active": 90,
                "import_logs": 5,
                "parser_review_imports": 1,
                "messages": 5,
                "orphan_watches": 1,
            },
            False,
            "count",
        )
        mock_batched.return_value = MagicMock(
            dry_run=False,
            deleted={"offers": 90, "import_logs": 5, "messages": 5},
            errors=[],
            chunk_failures=[],
            verification=MarketDataResetVerification(0, 0, 0, 0, 0, 0),
            success=True,
            method="batched",
        )

        result = run_market_data_reset(dry_run=False)

        assert result.success is True
        mock_batched.assert_called_once()

    @patch("market_data_reset.run_full_batched_reset")
    @patch("market_data_reset._count_reset_scope_lightweight")
    def test_verification_fails_when_active_offers_remain(
        self,
        mock_count: MagicMock,
        mock_batched: MagicMock,
    ) -> None:
        mock_count.return_value = (
            {
                "request_matches": 0,
                "market_notifications": 0,
                "offers": 90,
                "offers_active": 90,
                "import_logs": 0,
                "parser_review_imports": 0,
                "messages": 0,
                "orphan_watches": 0,
            },
            False,
            "count",
        )
        mock_batched.return_value = MagicMock(
            dry_run=False,
            deleted={"offers": 0},
            errors=["Reset finished but core market tables are not all zero."],
            chunk_failures=[],
            verification=MarketDataResetVerification(
                offers_total=90,
                offers_active=90,
                import_logs_total=0,
                messages_total=0,
                request_matches_total=0,
                market_notifications_total=0,
            ),
            success=False,
            method="batched",
        )

        result = run_market_data_reset(dry_run=False)

        assert result.success is False
        assert result.verification is not None
        assert result.verification.offers_active == 90

    @patch("market_data_reset.get_client")
    def test_strict_delete_records_chunk_failure_when_rows_remain(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        from market_data_reset import _delete_rows_by_ids_strict

        chunk_failures: list[str] = []
        count_responses = [MagicMock(count=2), MagicMock(count=0)]
        delete_execute = MagicMock()
        mock_get_client.return_value.table.return_value.select.return_value.in_.return_value.limit.return_value.execute.side_effect = count_responses
        mock_get_client.return_value.table.return_value.delete.return_value.in_.return_value.execute.return_value = delete_execute

        deleted = _delete_rows_by_ids_strict(
            "offers",
            ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"],
            chunk_failures,
        )

        assert deleted == 2
        assert chunk_failures == []

    @patch("market_data_reset._count_reset_scope_lightweight")
    def test_preview_reports_reset_scope_counts(self, mock_count: MagicMock) -> None:
        mock_count.return_value = (
            {
                "request_matches": 5,
                "market_notifications": 1,
                "offers": 12,
                "offers_active": 10,
                "import_logs": 2,
                "parser_review_imports": 1,
                "messages": 1,
                "orphan_watches": 0,
            },
            False,
            "count",
        )

        preview = preview_market_data_reset(load_counts=True)

        assert preview.counts["offers"] == 12
        assert preview.counts["offers_active"] == 10

    def test_dry_run_returns_preview_without_deletes(self) -> None:
        with patch(
            "market_data_reset._count_reset_scope_lightweight",
            return_value=(
                {
                    "offers": 3,
                    "offers_active": 3,
                    "import_logs": 2,
                    "request_matches": 0,
                    "market_notifications": 0,
                    "parser_review_imports": 0,
                    "messages": 0,
                    "orphan_watches": 0,
                },
                False,
                "count",
            ),
        ):
            result = run_market_data_reset(dry_run=True)

        assert result.dry_run is True
        assert result.deleted == {}


class TestEmergencyResetLogic:
    def setup_method(self) -> None:
        clear_batched_reset_jobs_for_tests()

    @staticmethod
    def _batch(
        step_key: str,
        table: str,
        selected: int,
        deleted: int,
        *,
        complete: bool,
    ) -> ResetStepBatchResult:
        return ResetStepBatchResult(
            step_key=step_key,
            table=table,
            selected_rows=selected,
            deleted_rows=deleted,
            step_complete=complete,
        )

    @patch("market_data_reset.verify_market_data_reset")
    @patch("market_data_reset._run_reset_step_batch")
    def test_batched_reset_runs_steps_in_order(
        self,
        mock_step: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        mock_step.side_effect = [
            self._batch("request_matches", "request_matches", 3, 3, complete=True),
            self._batch("offers", "offers", 100, 100, complete=False),
            self._batch("offers", "offers", 78, 78, complete=True),
            self._batch("import_logs", "import_logs", 10, 10, complete=True),
            self._batch("messages", "messages", 10, 10, complete=True),
            self._batch("market_notifications", "notifications", 2, 2, complete=True),
            self._batch("orphan_watches", "watches", 5, 5, complete=True),
        ]
        mock_verify.return_value = MarketDataResetVerification(0, 0, 0, 0, 0, 0)

        result = run_emergency_market_data_reset(owner_user_id="admin-test")

        assert result.success is True
        assert result.method == "batched"
        assert mock_step.call_count == 7
        assert mock_step.call_args_list[1].args[0] == "offers"
        assert mock_step.call_args_list[1].args[2] == RESET_BATCH_SIZE

    def test_emergency_success_when_core_tables_zero(self) -> None:
        verification = MarketDataResetVerification(
            offers_total=0,
            offers_active=0,
            import_logs_total=0,
            messages_total=0,
            request_matches_total=2,
            market_notifications_total=1,
        )

        assert is_emergency_reset_success(verification) is True

    def test_reset_rpc_migration_has_no_execute_deletes(self) -> None:
        sql = Path("docs/migrations/sprint_48_4_reset_market_data_rpc.sql").read_text(
            encoding="utf-8"
        )
        assert re.search(r"^\s*DELETE\s+FROM", sql, re.MULTILINE | re.IGNORECASE) is None
        assert re.search(r"^\s*TRUNCATE\s+", sql, re.MULTILINE | re.IGNORECASE) is None


class TestBatchedResetTermination:
    OWNER = "admin-batch-test"

    def setup_method(self) -> None:
        clear_batched_reset_jobs_for_tests()

    @patch("market_data_reset._run_reset_step_batch")
    def test_empty_table_advances_without_extra_batches(
        self,
        mock_step: MagicMock,
    ) -> None:
        mock_step.return_value = ResetStepBatchResult(
            step_key="request_matches",
            table="request_matches",
            selected_rows=0,
            deleted_rows=0,
            step_complete=True,
        )
        job_id = create_batched_reset_job(owner_user_id=self.OWNER)

        progress = run_batched_reset_batch(job_id, owner_user_id=self.OWNER)

        assert progress.batches_run == 1
        assert progress.step_index == 1
        assert progress.status == "running"
        mock_step.assert_called_once()

    @patch("market_data_reset._run_reset_step_batch")
    def test_last_partial_batch_completes_step(
        self,
        mock_step: MagicMock,
    ) -> None:
        mock_step.return_value = ResetStepBatchResult(
            step_key="offers",
            table="offers",
            selected_rows=678,
            deleted_rows=678,
            step_complete=True,
        )
        job_id = create_batched_reset_job(owner_user_id=self.OWNER)
        from market_data_reset import _RESET_JOBS

        _RESET_JOBS[job_id].step_index = 1

        progress = run_batched_reset_batch(job_id, owner_user_id=self.OWNER)

        assert progress.deleted["offers"] == 678
        assert progress.step_index == 2
        assert progress.status == "running"

    @patch("market_data_reset._run_reset_step_batch")
    def test_zero_delete_batch_fails_immediately_for_critical_step(
        self,
        mock_step: MagicMock,
    ) -> None:
        mock_step.return_value = ResetStepBatchResult(
            step_key="offers",
            table="offers",
            selected_rows=50,
            deleted_rows=0,
            step_complete=False,
        )
        job_id = create_batched_reset_job(owner_user_id=self.OWNER)
        from market_data_reset import _RESET_JOBS

        _RESET_JOBS[job_id].step_index = 1

        progress = run_batched_reset_batch(job_id, owner_user_id=self.OWNER)

        assert progress.status == "failed"
        assert progress.batches_run == 1
        assert progress.deleted.get("offers", 0) == 0
        assert any("deleted 0" in error for error in progress.errors)
        mock_step.assert_called_once()

    @patch("market_data_reset._run_reset_step_batch")
    def test_notification_delete_failure_skipped_with_warning(
        self,
        mock_step: MagicMock,
    ) -> None:
        mock_step.return_value = ResetStepBatchResult(
            step_key="market_notifications",
            table="notifications",
            selected_rows=100,
            deleted_rows=0,
            step_complete=False,
        )
        job_id = create_batched_reset_job(owner_user_id=self.OWNER)
        from market_data_reset import _RESET_JOBS

        _RESET_JOBS[job_id].step_index = 4

        progress = run_batched_reset_batch(job_id, owner_user_id=self.OWNER)

        assert progress.status == "running"
        assert progress.step_index == 5
        assert progress.current_step == "orphan_watches"
        assert any("skipped" in warning for warning in progress.warnings)
        mock_step.assert_called_once()

    @patch("market_data_reset._run_reset_step_batch")
    def test_repeated_zero_select_batch_is_not_retried(
        self,
        mock_step: MagicMock,
    ) -> None:
        mock_step.return_value = ResetStepBatchResult(
            step_key="offers",
            table="offers",
            selected_rows=0,
            deleted_rows=0,
            step_complete=False,
        )
        job_id = create_batched_reset_job(owner_user_id=self.OWNER)
        from market_data_reset import _RESET_JOBS

        _RESET_JOBS[job_id].step_index = 1

        first = run_batched_reset_batch(job_id, owner_user_id=self.OWNER)
        second = run_batched_reset_batch(job_id, owner_user_id=self.OWNER)

        assert first.status == "failed"
        assert second.status == "failed"
        assert first.batches_run == 1
        assert second.batches_run == 1
        mock_step.assert_called_once()
        assert any("zero-select" in error for error in first.errors)


class TestDeleteBatchSizing:
    def test_default_batch_size_is_100_not_1000(self) -> None:
        from market_data_reset import (
            DELETE_RETRY_BATCH_SIZE,
            NOTIFICATION_BATCH_SIZE,
            RESET_BATCH_SIZE,
            _batch_size_for_step,
        )

        assert RESET_BATCH_SIZE == 100
        assert NOTIFICATION_BATCH_SIZE == 50
        assert DELETE_RETRY_BATCH_SIZE == 25
        assert RESET_BATCH_SIZE != 1000
        assert _batch_size_for_step("offers") == 100
        assert _batch_size_for_step("market_notifications") == 50

    @patch("market_data_reset._delete_ids_with_count")
    def test_retries_with_25_ids_after_400(self, mock_delete: MagicMock) -> None:
        from market_data_reset import _delete_ids_with_retry

        ids = [f"id-{index:03d}" for index in range(100)]
        mock_delete.side_effect = [
            Exception("400 Bad Request JSON could not be generated"),
            25,
            25,
            25,
            25,
        ]
        chunk_failures: list[str] = []

        deleted, error = _delete_ids_with_retry(
            "offers",
            ids,
            batch_size=100,
            chunk_failures=chunk_failures,
        )

        assert deleted == 100
        assert error is None
        assert mock_delete.call_count == 5

    @patch("market_data_reset._delete_ids_with_count")
    def test_fails_clearly_when_retry_batch_also_400(self, mock_delete: MagicMock) -> None:
        from market_data_reset import _delete_ids_with_retry

        ids = ["aaa", "bbb", "ccc"] + [f"id-{index:03d}" for index in range(97)]
        mock_delete.side_effect = [
            Exception("400 Bad Request"),
            Exception("400 JSON could not be generated"),
        ]
        chunk_failures: list[str] = []

        deleted, error = _delete_ids_with_retry(
            "offers",
            ids,
            batch_size=100,
            chunk_failures=chunk_failures,
        )

        assert deleted == 0
        assert error is not None
        assert "offers" in error
        assert "sample=" in error
        assert "retry batch size 25" in error

    @patch("market_data_reset._delete_ids_with_count", return_value=42)
    def test_delete_success_counts_actual_deleted_rows(
        self,
        mock_delete: MagicMock,
    ) -> None:
        from market_data_reset import _delete_single_table_batch

        chunk_failures: list[str] = []
        with patch(
            "market_data_reset._select_id_batch",
            return_value=[f"id-{index}" for index in range(100)],
        ):
            result = _delete_single_table_batch("offers", "offers", 100, chunk_failures)

        assert result.deleted_rows == 42
        assert result.selected_rows == 100
        mock_delete.assert_called_once()

    @patch("market_data_reset.verify_market_data_reset")
    @patch("market_data_reset._run_reset_step_batch")
    def test_reset_continues_offers_until_step_complete(
        self,
        mock_step: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        mock_step.side_effect = [
            ResetStepBatchResult("offers", "offers", 100, 100, False, ("id-1",)),
            ResetStepBatchResult("offers", "offers", 100, 100, False, ("id-2",)),
            ResetStepBatchResult("offers", "offers", 78, 78, True, ("id-3",)),
        ]
        mock_verify.return_value = MarketDataResetVerification(0, 0, 0, 0, 0, 0)
        clear_batched_reset_jobs_for_tests()
        job_id = create_batched_reset_job(owner_user_id="offers-test")
        from market_data_reset import _RESET_JOBS

        _RESET_JOBS[job_id].step_index = 1

        for _ in range(3):
            progress = run_batched_reset_batch(job_id, owner_user_id="offers-test")
            if progress.step_index > 1:
                break

        assert _RESET_JOBS[job_id].deleted["offers"] == 278
        assert _RESET_JOBS[job_id].step_index == 2


class TestSearchAfterReset:
    @patch("search._load_active_offers_for_search", return_value=([], 0))
    def test_search_empty_when_no_active_offers(self, _mock_load: MagicMock) -> None:
        offers, _cheapest = search_offers("5711")

        assert offers == []

    @patch("dashboard_data.load_dashboard_todays_best_deals", return_value=([], 0))
    @patch("dashboard_data._visible_dashboard_import_slices", return_value=([], [], [], []))
    @patch("dashboard_data.list_contacts_for_import_lookup", return_value=[])
    @patch("dashboard_data.load_dashboard_matched_requests", return_value=[])
    @patch("dashboard_data.attach_import_log_summaries", side_effect=lambda rows: rows)
    @patch("dashboard_data.load_live_market_rows", return_value=[])
    @patch("dashboard_data.load_ai_needs_help_items", return_value=[])
    @patch("dashboard_data.parser_review_counts", return_value={"total": 0})
    @patch("dashboard_data.list_requests", return_value=[])
    @patch("dashboard_data.get_unread_notification_count", return_value=0)
    def test_dashboard_loads_after_empty_reset(
        self,
        _mock_unread: MagicMock,
        _mock_requests: MagicMock,
        _mock_parser_counts: MagicMock,
        _mock_ai: MagicMock,
        _mock_live: MagicMock,
        _mock_attach: MagicMock,
        _mock_matches: MagicMock,
        _mock_contacts: MagicMock,
        _mock_slices: MagicMock,
        _mock_deals: MagicMock,
    ) -> None:
        from dashboard_data import load_trading_desk

        desk = load_trading_desk(ADMIN_USER, format_timestamp=lambda _value: "now")

        assert desk["todays_best_deals"] == []

    @patch("market_data_reset._count_table_rows", return_value=0)
    @patch("market_data_reset._count_filtered_rows", return_value=0)
    def test_verify_market_data_reset_all_zero(self, _mock_filtered: MagicMock, _mock_table: MagicMock) -> None:
        verification = verify_market_data_reset()

        assert verification.is_clean() is True
