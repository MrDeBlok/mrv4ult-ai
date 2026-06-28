"""Tests for Sprint 32.4 import status migration."""

from __future__ import annotations

from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_32_4_import_statuses.sql"
)

LEGACY_STATUSES = (
    "'success'",
    "'no_watch_detected'",
    "'warning'",
    "'error'",
)

NEW_STATUSES = (
    "'noise'",
    "'request_intent'",
)


class TestSprint324ImportStatusMigrationFile:
    def test_migration_file_exists(self) -> None:
        assert MIGRATION_PATH.is_file()

    def test_migration_is_idempotent(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert "DROP CONSTRAINT IF EXISTS import_logs_status_check" in sql
        assert sql.count("DROP CONSTRAINT IF EXISTS import_logs_status_check") == 1

    def test_migration_updates_import_logs_status_check(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert "ALTER TABLE import_logs" in sql
        assert "ADD CONSTRAINT import_logs_status_check" in sql
        for status in LEGACY_STATUSES + NEW_STATUSES:
            assert status in sql

    def test_migration_preserves_existing_statuses(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        for status in LEGACY_STATUSES:
            assert status in sql

    def test_migration_adds_noise_and_request_intent(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        for status in NEW_STATUSES:
            assert status in sql
