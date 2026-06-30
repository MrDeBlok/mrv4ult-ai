"""Tests for Sprint 41.1 import status migration."""

from __future__ import annotations

from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_41_1_import_status.sql"
)


class TestSprint411ImportStatusMigrationFile:
    def test_migration_file_exists(self) -> None:
        assert MIGRATION_PATH.is_file()

    def test_migration_adds_insufficient_evidence_status(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert "DROP CONSTRAINT IF EXISTS import_logs_status_check" in sql
        assert "'insufficient_evidence'" in sql
        assert "'noise'" in sql
        assert "'request_intent'" in sql
