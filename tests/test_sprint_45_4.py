"""Regression tests for dealers page offer aggregation (Sprint 45.4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from contact_classification import CONTACT_TYPE_DEALER, filter_dealer_list_rows_by_search
from dealer_intelligence import build_dealer_list_rows, compute_dealer_stats
from database import list_dealers, list_offer_intelligence_rows


class TestListOfferIntelligenceRows:
    @patch("database.contact_type_column_supported", return_value=True)
    @patch("database.get_client")
    def test_rows_use_offer_dealer_id_when_nested_dealer_join_missing(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type_supported: MagicMock,
    ) -> None:
        client = MagicMock()
        mock_get_client.return_value = client

        offers_execute = MagicMock()
        offers_execute.data = [
            {
                "dealer_id": "dealer-1",
                "watch_id": "watch-1",
                "status": "active",
                "usd_price": 72000,
                "messages": {"received_at": "2026-06-27T12:00:00+00:00"},
            },
            {
                "dealer_id": "client-1",
                "watch_id": "watch-2",
                "status": "active",
                "usd_price": 50000,
                "messages": {"received_at": "2026-06-26T10:00:00+00:00"},
            },
        ]
        offers_table = MagicMock()
        offers_table.select.return_value.execute.return_value = offers_execute

        dealers_execute = MagicMock()
        dealers_execute.data = [
            {"id": "dealer-1", "contact_type": CONTACT_TYPE_DEALER},
            {"id": "client-1", "contact_type": "client"},
        ]
        dealers_table = MagicMock()
        dealers_table.select.return_value.execute.return_value = dealers_execute

        dealer_ids_execute = MagicMock()
        dealer_ids_execute.data = [
            {"dealer_id": "dealer-1"},
            {"dealer_id": "client-1"},
        ]

        def table(name: str) -> MagicMock:
            if name == "offers":
                return offers_table
            if name == "dealers":
                return dealers_table
            raise AssertionError(f"Unexpected table: {name}")

        client.table.side_effect = table
        offers_table.select.return_value.execute.side_effect = [
            dealer_ids_execute,
            offers_execute,
        ]

        rows = list_offer_intelligence_rows()

        assert len(rows) == 1
        assert rows[0]["dealer_id"] == "dealer-1"
        assert "dealers" not in rows[0]


class TestDealerListAggregation:
    def test_dealer_with_offers_shows_non_zero_counts(self) -> None:
        rows = build_dealer_list_rows(
            [{"id": "dealer-1", "display_name": "HK Dealer"}],
            [
                {
                    "dealer_id": "dealer-1",
                    "watch_id": "watch-1",
                    "status": "active",
                    "usd_price": 70000,
                    "messages": {"received_at": "2026-06-25T10:00:00+00:00"},
                },
                {
                    "dealer_id": "dealer-1",
                    "watch_id": "watch-2",
                    "status": "sold",
                    "usd_price": 65000,
                    "messages": {"received_at": "2026-06-20T08:00:00+00:00"},
                },
            ],
        )

        assert len(rows) == 1
        assert rows[0]["total_offers"] == 2
        assert rows[0]["active_offers"] == 1
        assert rows[0]["average_asking_price"] == "$70,000"

    def test_active_offers_counted_correctly(self) -> None:
        stats = compute_dealer_stats(
            [
                {"dealer_id": "dealer-1", "status": "active", "usd_price": 10000},
                {"dealer_id": "dealer-1", "status": "active", "usd_price": 20000},
                {"dealer_id": "dealer-1", "status": "sold", "usd_price": 15000},
            ]
        )

        assert stats["total_offers"] == 3
        assert stats["active_offers"] == 2
        assert stats["average_usd"] == 15000

    def test_dealer_without_offers_still_shown_with_zero_stats(self) -> None:
        rows = build_dealer_list_rows(
            [
                {"id": "dealer-with-offers", "display_name": "Active Dealer"},
                {"id": "dealer-empty", "display_name": "Quiet Dealer"},
            ],
            [
                {
                    "dealer_id": "dealer-with-offers",
                    "watch_id": "watch-1",
                    "status": "active",
                    "usd_price": 50000,
                    "messages": {"received_at": "2026-06-27T12:00:00+00:00"},
                }
            ],
        )

        by_id = {row["id"]: row for row in rows}
        assert by_id["dealer-with-offers"]["total_offers"] == 1
        assert by_id["dealer-empty"]["total_offers"] == 0
        assert by_id["dealer-empty"]["active_offers"] == 0
        assert by_id["dealer-empty"]["average_asking_price"] == "N/A"

    def test_dealer_stats_join_on_string_dealer_id(self) -> None:
        rows = build_dealer_list_rows(
            [{"id": "abc-123", "display_name": "UUID Dealer"}],
            [
                {
                    "dealer_id": "abc-123",
                    "watch_id": "watch-1",
                    "status": "active",
                    "usd_price": 88000,
                    "messages": {"received_at": "2026-06-27T12:00:00+00:00"},
                }
            ],
        )

        assert rows[0]["total_offers"] == 1
        assert rows[0]["lowest_asking_price"] == "$88,000"


class TestListDealersVisibility:
    @patch("database.contact_type_column_supported", return_value=True)
    @patch("database.get_client")
    def test_list_dealers_includes_zero_offer_business_dealers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type_supported: MagicMock,
    ) -> None:
        client = MagicMock()
        mock_get_client.return_value = client

        offers_execute = MagicMock()
        offers_execute.data = [{"dealer_id": "dealer-1"}]
        offers_table = MagicMock()
        offers_table.select.return_value.execute.return_value = offers_execute

        dealers_execute = MagicMock()
        dealers_execute.data = [
            {"id": "dealer-1", "display_name": "Active Dealer", "contact_type": CONTACT_TYPE_DEALER},
            {"id": "dealer-2", "display_name": "Quiet Dealer", "contact_type": CONTACT_TYPE_DEALER},
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

        assert len(dealers) == 2
        assert {dealer["id"] for dealer in dealers} == {"dealer-1", "dealer-2"}


class TestDealersPageSearch:
    def test_search_filters_dealer_rows(self) -> None:
        filtered = filter_dealer_list_rows_by_search(
            [
                {
                    "name": "Hong Kong Dealer",
                    "display_name": "Hong Kong Dealer",
                    "phone_number": "+85291234567",
                    "whatsapp_id": "85291234567",
                    "groups": "HK Dealers",
                },
                {
                    "name": "Geneva Dealer",
                    "display_name": "Geneva Dealer",
                    "phone_number": "+41791234567",
                    "whatsapp_id": "41791234567",
                    "groups": "EU Dealers",
                },
            ],
            "85291234567",
        )
        assert len(filtered) == 1
        assert filtered[0]["name"] == "Hong Kong Dealer"

    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs", return_value=[])
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_dealers_page_passes_search_query_to_filter(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [
            {"id": "dealer-1", "display_name": "HK Dealer", "phone_number": "+85291234567"},
            {"id": "dealer-2", "display_name": "Geneva Dealer", "phone_number": "+41791234567"},
        ]

        client = TestClient(app)
        response = client.get("/dealers?q=85291234567")

        assert response.status_code == 200
        assert "HK Dealer" in response.text
        assert "Geneva Dealer" not in response.text
