"""Tests for Sprint 27.2 simplified contact model."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from contact_classification import (
    CONTACT_TYPE_CLIENT,
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_REMOVED,
    DEFAULT_CONTACTS_FILTER,
    build_dealer_lookup_by_whatsapp,
    filter_business_import_logs,
    filter_contact_rows,
    is_business_contact,
    is_client_contact,
    is_removed_contact,
    normalize_contact_type,
)
from database import dealer_contact_type, dealer_is_business_visible, list_dealers, reset_contact_type_column_cache
from ingest import find_or_create_dealer, ingest_message


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "migrations"
    / "sprint_27_2_simplify_contact_types.sql"
)


def _migration_step_positions(sql: str) -> dict[str, int]:
    lines = sql.lower().splitlines()
    drop_positions = [
        index
        for index, line in enumerate(lines)
        if "drop constraint if exists dealers_contact_type_check" in line
    ]
    add_positions = [
        index
        for index, line in enumerate(lines)
        if "add constraint dealers_contact_type_check" in line
    ]
    first_removed_update = next(
        (
            index
            for index, line in enumerate(sql.splitlines())
            if "set contact_type = 'removed'" in line.lower()
        ),
        -1,
    )
    last_mapping_update = max(
        (
            index
            for index, line in enumerate(sql.splitlines())
            if line.strip().lower().startswith("update dealers")
        ),
        default=-1,
    )
    return {
        "first_drop": min(drop_positions) if drop_positions else -1,
        "first_removed_update": first_removed_update,
        "last_mapping_update": last_mapping_update,
        "add_constraint": add_positions[-1] if add_positions else -1,
    }


def _sample_contact_rows() -> list[dict]:
    return [
        {
            "id": "dealer-1",
            "name": "HK Dealer",
            "whatsapp_id": "85291234567",
            "phone_number": "85291234567",
            "contact_type": CONTACT_TYPE_DEALER,
            "contact_type_label": "Dealer",
            "contact_type_class": "success",
        },
        {
            "id": "client-1",
            "name": "Wishlist Client",
            "whatsapp_id": "85299998888",
            "phone_number": "85299998888",
            "contact_type": CONTACT_TYPE_CLIENT,
            "contact_type_label": "Client",
            "contact_type_class": "info",
        },
        {
            "id": "removed-1",
            "name": "Removed Person",
            "whatsapp_id": "85288887777",
            "phone_number": "85288887777",
            "contact_type": CONTACT_TYPE_REMOVED,
            "contact_type_label": "Removed",
            "contact_type_class": "dark",
        },
        {
            "id": "placeholder-1",
            "name": "Import Placeholder",
            "whatsapp_id": "import-placeholder",
            "phone_number": "N/A",
            "contact_type": CONTACT_TYPE_REMOVED,
            "contact_type_label": "Removed",
            "contact_type_class": "dark",
        },
    ]


class TestSprint272MigrationFile:
    def test_migration_file_maps_legacy_types_and_updates_constraint(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")

        assert MIGRATION_PATH.is_file()
        assert "SET contact_type = 'removed'" in sql
        assert "SET contact_type = 'dealer'" in sql
        assert "'client', 'removed'" in sql

    def test_migration_drops_constraint_before_legacy_removed_updates(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        positions = _migration_step_positions(sql)

        assert positions["first_drop"] >= 0
        assert positions["first_removed_update"] >= 0
        assert positions["first_drop"] < positions["first_removed_update"]

    def test_migration_adds_new_constraint_after_legacy_mapping(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        positions = _migration_step_positions(sql)

        assert positions["add_constraint"] >= 0
        assert positions["last_mapping_update"] >= 0
        assert positions["last_mapping_update"] < positions["add_constraint"]

    def test_migration_is_idempotent_and_preserves_mapping_rules(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

        assert sql.count("drop constraint if exists dealers_contact_type_check") >= 2
        assert "where contact_type in ('private', 'ignored', 'deleted')" in sql
        assert "where contact_type = 'unknown'" in sql
        assert "alter column contact_type set default 'removed'" in sql
        assert "delete from dealers" not in sql


class TestLegacyContactTypeMapping:
    def test_maps_old_types_to_new_model(self) -> None:
        assert normalize_contact_type("dealer") == CONTACT_TYPE_DEALER
        assert normalize_contact_type("client") == CONTACT_TYPE_CLIENT
        assert normalize_contact_type("removed") == CONTACT_TYPE_REMOVED
        assert normalize_contact_type("private") == CONTACT_TYPE_REMOVED
        assert normalize_contact_type("ignored") == CONTACT_TYPE_REMOVED
        assert normalize_contact_type("deleted") == CONTACT_TYPE_REMOVED
        assert normalize_contact_type("unknown", has_offers=False) == CONTACT_TYPE_REMOVED
        assert normalize_contact_type("unknown", has_offers=True) == CONTACT_TYPE_DEALER

    def test_business_and_client_helpers(self) -> None:
        assert is_business_contact(CONTACT_TYPE_DEALER) is True
        assert is_business_contact(CONTACT_TYPE_CLIENT) is False
        assert is_business_contact("private") is False
        assert is_client_contact(CONTACT_TYPE_CLIENT) is True
        assert is_removed_contact("deleted") is True


class TestContactsPageFilters:
    def test_default_filter_shows_dealers_and_clients(self) -> None:
        filtered = filter_contact_rows(_sample_contact_rows(), filter_key=DEFAULT_CONTACTS_FILTER)
        names = {row["name"] for row in filtered}
        assert names == {"HK Dealer", "Wishlist Client"}

    def test_default_filter_hides_removed_contacts(self) -> None:
        filtered = filter_contact_rows(_sample_contact_rows(), filter_key=DEFAULT_CONTACTS_FILTER)
        assert "Removed Person" not in {row["name"] for row in filtered}

    def test_removed_filter_shows_only_removed_contacts(self) -> None:
        filtered = filter_contact_rows(_sample_contact_rows(), filter_key="removed")
        assert [row["name"] for row in filtered] == ["Removed Person"]

    def test_all_filter_excludes_removed_and_placeholder(self) -> None:
        filtered = filter_contact_rows(_sample_contact_rows(), filter_key="all")
        names = {row["name"] for row in filtered}
        assert names == {"HK Dealer", "Wishlist Client"}


class TestContactsPeoplePage:
    @patch("app.build_contact_rows")
    @patch("app.list_contacts")
    def test_people_page_default_shows_dealers_and_clients(
        self,
        mock_list_contacts: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_contacts.return_value = []
        mock_build_rows.return_value = _sample_contact_rows()

        client = TestClient(app)
        response = client.get("/contacts")

        assert response.status_code == 200
        assert "People" in response.text
        assert "HK Dealer" in response.text
        assert "Wishlist Client" in response.text
        assert "Removed Person" not in response.text
        assert "Set as Dealer" in response.text
        assert "Set as Client" in response.text
        assert "Private" not in response.text

    @patch("app.build_contact_rows")
    @patch("app.list_contacts")
    def test_removed_filter_shows_restore_actions_only(
        self,
        mock_list_contacts: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_contacts.return_value = []
        mock_build_rows.return_value = _sample_contact_rows()

        client = TestClient(app)
        response = client.get("/contacts?filter=removed")

        assert response.status_code == 200
        assert "Removed Person" in response.text
        assert "Restore" in response.text
        assert "Remove from system" not in response.text

    @patch("app.update_dealer_contact_type")
    @patch("app.get_dealer_by_id")
    def test_remove_marks_contact_removed(
        self,
        mock_get_dealer: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        mock_get_dealer.return_value = {"id": "dealer-1", "contact_type": CONTACT_TYPE_DEALER}

        client = TestClient(app)
        response = client.post(
            "/contacts/dealer-1/remove",
            data={"confirm": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        mock_update.assert_called_once_with("dealer-1", CONTACT_TYPE_REMOVED)

    @patch("app.update_dealer_contact_type")
    @patch("app.get_dealer_by_id")
    def test_restore_removed_contact_as_client(
        self,
        mock_get_dealer: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        mock_get_dealer.return_value = {"id": "removed-1", "contact_type": CONTACT_TYPE_REMOVED}

        client = TestClient(app)
        response = client.post(
            "/contacts/removed-1/restore",
            data={"contact_type": CONTACT_TYPE_CLIENT, "filter": "removed"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/contacts?filter=removed&restored=1"
        mock_update.assert_called_once_with("removed-1", CONTACT_TYPE_CLIENT)


class TestIngestContactRules:
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.get_import_placeholder_dealer_id", return_value=("placeholder-1", CONTACT_TYPE_REMOVED))
    @patch("ingest.find_or_create_dealer")
    @patch("ingest.parse_message")
    def test_message_without_valid_offer_does_not_create_contact(
        self,
        mock_parse: MagicMock,
        mock_find_dealer: MagicMock,
        mock_placeholder: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        mock_parse.return_value = {"message_type": "unknown", "watches": []}
        mock_insert_message.return_value = {"id": "message-1"}
        mock_insert_import_log.return_value = {"id": "log-1"}

        ingest_message(
            "Hey, are we still on for dinner?",
            group_name="Private Offers",
            dealer_whatsapp="+85299998888",
            dealer_alias="Friend",
        )

        mock_find_dealer.assert_not_called()
        mock_placeholder.assert_called_once()

    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    @patch("ingest.parse_message")
    def test_valid_offer_creates_dealer_contact(
        self,
        mock_parse: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        mock_parse.return_value = {
            "message_type": "offer_list",
            "watches": [
                {
                    "brand": "Rolex",
                    "reference": "126200",
                    "original_price": 72000,
                    "original_currency": "USD",
                    "usd_price": 72000,
                }
            ],
        }
        mock_insert_message.return_value = {"id": "message-1"}
        mock_find_watch.return_value = ({"id": "watch-1"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-1"}

        ingest_message(
            "Rolex 126200 72000usd",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        mock_find_dealer.assert_called_once()
        assert mock_find_dealer.call_args.kwargs["default_contact_type"] == CONTACT_TYPE_DEALER
        mock_process_matches.assert_called_once()
        mock_record_notifications.assert_called_once()

    @patch("database.contact_type_column_supported", return_value=True)
    @patch("ingest.contact_type_column_supported", return_value=True)
    @patch("ingest.get_client")
    def test_find_or_create_dealer_promotes_removed_contact_on_valid_offer(
        self,
        mock_get_client: MagicMock,
        mock_ingest_contact_type_supported: MagicMock,
        mock_database_contact_type_supported: MagicMock,
    ) -> None:
        client = MagicMock()
        mock_get_client.return_value = client

        existing = MagicMock()
        existing.data = [
            {"id": "removed-1", "display_name": "Old Dealer", "contact_type": CONTACT_TYPE_REMOVED}
        ]
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = existing

        dealer_id, contact_type = find_or_create_dealer(
            "+85291234567",
            display_name="Old Dealer",
            default_contact_type=CONTACT_TYPE_DEALER,
        )

        assert dealer_id == "removed-1"
        assert contact_type == CONTACT_TYPE_DEALER
        update_payload = client.table.return_value.update.call_args.args[0]
        assert update_payload["contact_type"] == CONTACT_TYPE_DEALER


class TestRemovedContactBusinessExclusion:
    @patch("database.contact_type_column_supported", return_value=True)
    @patch("database.get_client")
    def test_list_dealers_excludes_clients_and_removed(
        self,
        mock_get_client: MagicMock,
        mock_contact_type_supported: MagicMock,
    ) -> None:
        reset_contact_type_column_cache()

        client = MagicMock()
        mock_get_client.return_value = client

        offers_execute = MagicMock()
        offers_execute.data = [
            {"dealer_id": "dealer-1"},
            {"dealer_id": "client-1"},
            {"dealer_id": "removed-1"},
        ]
        offers_table = MagicMock()
        offers_table.select.return_value.execute.return_value = offers_execute

        dealers_execute = MagicMock()
        dealers_execute.data = [
            {"id": "dealer-1", "display_name": "HK Dealer", "contact_type": CONTACT_TYPE_DEALER},
            {"id": "client-1", "display_name": "Client", "contact_type": CONTACT_TYPE_CLIENT},
            {"id": "removed-1", "display_name": "Removed", "contact_type": CONTACT_TYPE_REMOVED},
        ]
        dealers_query = MagicMock()
        dealers_query.eq.return_value.execute.return_value = dealers_execute
        dealers_table = MagicMock()
        dealers_table.select.return_value.order.return_value = dealers_query

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

    def test_clients_are_not_business_visible(self) -> None:
        assert dealer_is_business_visible({"contact_type": CONTACT_TYPE_CLIENT}) is False
        assert dealer_is_business_visible({"contact_type": CONTACT_TYPE_DEALER}) is True

    def test_filter_business_import_logs_excludes_clients_and_removed(self) -> None:
        lookup = build_dealer_lookup_by_whatsapp(
            [
                {"whatsapp_id": "111", "contact_type": CONTACT_TYPE_DEALER},
                {"whatsapp_id": "222", "contact_type": CONTACT_TYPE_CLIENT},
                {"whatsapp_id": "333", "contact_type": CONTACT_TYPE_REMOVED},
            ]
        )
        import_logs = [
            {"dealer_whatsapp": "111", "watches_parsed": 1},
            {"dealer_whatsapp": "222", "watches_parsed": 1},
            {"dealer_whatsapp": "333", "watches_parsed": 1},
        ]

        filtered = filter_business_import_logs(import_logs, lookup)

        assert len(filtered) == 1
        assert filtered[0]["dealer_whatsapp"] == "111"

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_search_excludes_clients_and_removed(
        self,
        mock_get_client: MagicMock,
        mock_contact_type_supported: MagicMock,
    ) -> None:
        from search import search_offers

        mock_execute = MagicMock()
        mock_execute.data = [
            {
                "watch_id": "watch-1",
                "usd_price": 70000,
                "watches": {"brand": "Rolex", "reference": "126200"},
                "dealers": {"display_name": "Client", "contact_type": CONTACT_TYPE_CLIENT},
            },
            {
                "watch_id": "watch-2",
                "usd_price": 71000,
                "watches": {"brand": "Rolex", "reference": "126200"},
                "dealers": {"display_name": "Removed", "contact_type": CONTACT_TYPE_REMOVED},
            },
            {
                "watch_id": "watch-3",
                "usd_price": 72000,
                "watches": {"brand": "Rolex", "reference": "126200"},
                "dealers": {"display_name": "HK Dealer", "contact_type": CONTACT_TYPE_DEALER},
            },
        ]
        mock_eq = MagicMock()
        mock_eq.execute.return_value = mock_execute
        mock_select = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_table = MagicMock()
        mock_table.select.return_value = mock_select
        mock_get_client.return_value.table.return_value = mock_table

        offers, _ = search_offers("126200")

        assert len(offers) == 1
        assert offers[0]["watch_id"] == "watch-3"

    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer")
    @patch("ingest.parse_message")
    def test_client_contact_skips_matching_and_notifications(
        self,
        mock_parse: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        mock_parse.return_value = {
            "message_type": "offer_list",
            "watches": [
                {
                    "brand": "Rolex",
                    "reference": "126200",
                    "original_price": 72000,
                    "original_currency": "USD",
                    "usd_price": 72000,
                }
            ],
        }
        mock_find_dealer.return_value = ("client-1", CONTACT_TYPE_CLIENT)
        mock_insert_message.return_value = {"id": "message-1"}
        mock_find_watch.return_value = ({"id": "watch-1"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-1"}

        ingest_message(
            "Rolex 126200 72000usd",
            group_name="HK Dealers",
            dealer_whatsapp="+85299998888",
        )

        mock_process_matches.assert_not_called()
        mock_record_notifications.assert_not_called()

    def test_legacy_contact_type_maps_placeholder_to_removed(self) -> None:
        with patch("database.contact_type_column_supported", return_value=False):
            assert dealer_contact_type({"whatsapp_id": "import-placeholder"}) == CONTACT_TYPE_REMOVED
