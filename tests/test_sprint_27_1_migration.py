"""Tests for Sprint 27.1 migration and contact_type compatibility."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app import app
from contact_classification import CONTACT_TYPE_DEALER, CONTACT_TYPE_REMOVED
from database import (
    contact_type_column_supported,
    dealer_contact_type,
    dealer_is_business_visible,
    list_dealers,
    reset_contact_type_column_cache,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_27_1_contact_classification.sql"
)
DELETED_CONTACTS_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_27_1_deleted_contacts.sql"
)


class TestSprint271MigrationFile:
    def test_migration_file_exists_and_is_idempotent(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert MIGRATION_PATH.is_file()
        assert "ADD COLUMN IF NOT EXISTS contact_type" in sql
        assert "CREATE INDEX IF NOT EXISTS idx_dealers_contact_type" in sql
        assert "SET contact_type = 'dealer'" in sql
        assert "duplicate_object" in sql


class TestDeletedContactsMigrationFile:
    def test_deleted_contacts_migration_updates_check_constraint(self) -> None:
        sql = DELETED_CONTACTS_MIGRATION_PATH.read_text(encoding="utf-8")

        assert DELETED_CONTACTS_MIGRATION_PATH.is_file()
        assert "DROP CONSTRAINT IF EXISTS dealers_contact_type_check" in sql
        assert "'deleted'" in sql


class TestContactTypeCompatibility:
    def setup_method(self) -> None:
        reset_contact_type_column_cache()

    def teardown_method(self) -> None:
        reset_contact_type_column_cache()

    @patch("database.get_client")
    def test_detects_missing_contact_type_column(self, mock_get_client: MagicMock) -> None:
        mock_get_client.return_value.table.return_value.select.return_value.limit.return_value.execute.side_effect = APIError(
            {"message": "column dealers.contact_type does not exist", "code": "42703"}
        )

        assert contact_type_column_supported() is False
        assert contact_type_column_supported() is False

    def test_legacy_contact_type_treats_existing_dealers_as_business(self) -> None:
        with patch("database.contact_type_column_supported", return_value=False):
            assert dealer_contact_type({"whatsapp_id": "+85291234567"}) == CONTACT_TYPE_DEALER
            assert dealer_is_business_visible({"whatsapp_id": "+85291234567"}) is True

    def test_legacy_contact_type_hides_import_placeholder(self) -> None:
        with patch("database.contact_type_column_supported", return_value=False):
            dealer = {"whatsapp_id": "import-placeholder"}
            assert dealer_contact_type(dealer) == CONTACT_TYPE_REMOVED
            assert dealer_is_business_visible(dealer) is False

    @patch("database.get_client")
    def test_list_dealers_without_contact_type_column(self, mock_get_client: MagicMock) -> None:
        with patch("database.contact_type_column_supported", return_value=False):
            client = MagicMock()
            mock_get_client.return_value = client

            offers_execute = MagicMock()
            offers_execute.data = [{"dealer_id": "dealer-1"}]
            offers_table = MagicMock()
            offers_table.select.return_value.execute.return_value = offers_execute

            dealers_execute = MagicMock()
            dealers_execute.data = [
                {"id": "dealer-1", "display_name": "HK Dealer", "whatsapp_id": "+85291234567"},
                {"id": "placeholder-1", "display_name": "Import Placeholder", "whatsapp_id": "import-placeholder"},
            ]
            dealers_table = MagicMock()
            dealers_table.select.return_value.order.return_value.execute.return_value = dealers_execute

            def table(name: str) -> MagicMock:
                if name == "offers":
                    return offers_table
                if name == "dealers":
                    return dealers_table
                raise AssertionError(f"Unexpected table: {name}")

            client.table.side_effect = table

            dealers = list_dealers()

        assert len(dealers) == 1
        assert dealers[0]["id"] == "dealer-1"
        dealers_table.select.return_value.eq.assert_not_called()

    @patch("app.build_dealer_list_rows", return_value=[])
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.list_dealers", return_value=[])
    def test_dealers_page_loads_when_contact_type_column_missing(
        self,
        mock_list_dealers: MagicMock,
        mock_list_offers: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert "Internal Server Error" not in response.text
