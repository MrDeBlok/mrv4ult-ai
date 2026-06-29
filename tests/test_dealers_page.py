"""Tests for dealer intelligence and dealer pages."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from dealer_intelligence import (
    aggregate_offers_by_dealer,
    build_dealer_list_row,
    build_dealer_list_rows,
    build_dealer_offer_rows,
    build_dealer_profile,
    compute_dealer_stats,
    flatten_offer_intelligence_row,
    format_activity_timestamp,
)


class TestOfferIntelligenceFlattening:
    def test_flatten_offer_intelligence_row_extracts_received_at(self) -> None:
        row = flatten_offer_intelligence_row(
            {
                "dealer_id": "dealer-1",
                "watch_id": "watch-1",
                "status": "active",
                "usd_price": 72000,
                "messages": {"received_at": "2026-06-27T12:00:00+00:00"},
            }
        )

        assert row["dealer_id"] == "dealer-1"
        assert row["received_at"] == "2026-06-27T12:00:00+00:00"


class TestDealerStats:
    def test_compute_dealer_stats_aggregates_offer_metrics(self) -> None:
        stats = compute_dealer_stats(
            [
                {
                    "dealer_id": "dealer-1",
                    "watch_id": "watch-1",
                    "status": "active",
                    "usd_price": 70000,
                    "received_at": "2026-06-25T10:00:00+00:00",
                },
                {
                    "dealer_id": "dealer-1",
                    "watch_id": "watch-2",
                    "status": "active",
                    "usd_price": 80000,
                    "received_at": "2026-06-27T12:00:00+00:00",
                },
                {
                    "dealer_id": "dealer-1",
                    "watch_id": "watch-3",
                    "status": "sold",
                    "usd_price": 65000,
                    "received_at": "2026-06-20T08:00:00+00:00",
                },
            ]
        )

        assert stats["total_offers"] == 3
        assert stats["active_offers"] == 2
        assert stats["average_usd"] == 75000
        assert stats["lowest_usd"] == 70000
        assert stats["highest_usd"] == 80000
        assert stats["unique_watches"] == 2
        assert stats["last_activity"] == "2026-06-27T12:00:00+00:00"

    def test_aggregate_offers_by_dealer_groups_rows(self) -> None:
        grouped = aggregate_offers_by_dealer(
            [
                {"dealer_id": "dealer-1", "status": "active"},
                {"dealer_id": "dealer-2", "status": "active"},
                {"dealer_id": "dealer-1", "status": "sold"},
            ]
        )

        assert len(grouped["dealer-1"]) == 2
        assert len(grouped["dealer-2"]) == 1


class TestDealerRowBuilders:
    def test_build_dealer_list_row_formats_prices(self) -> None:
        last_activity_utc = "2026-06-27T12:00:00+00:00"
        last_activity_amsterdam = "2026-06-27 14:00"

        row = build_dealer_list_row(
            {"id": "dealer-1", "display_name": "HK Dealer"},
            {
                "total_offers": 4,
                "active_offers": 3,
                "average_usd": 75500,
                "lowest_usd": 70000,
                "highest_usd": 82000,
                "last_activity": last_activity_utc,
                "unique_watches": 2,
            },
        )

        assert row["name"] == "HK Dealer"
        assert row["total_offers"] == 4
        assert row["active_offers"] == 3
        assert row["average_asking_price"] == "$75,500"
        assert row["lowest_asking_price"] == "$70,000"
        assert row["highest_asking_price"] == "$82,000"
        assert row["last_activity"] == format_activity_timestamp(last_activity_utc)
        assert row["last_activity"] == last_activity_amsterdam

    def test_build_dealer_list_rows_sorts_by_last_activity(self) -> None:
        rows = build_dealer_list_rows(
            [
                {"id": "dealer-a", "display_name": "Alpha"},
                {"id": "dealer-b", "display_name": "Beta"},
            ],
            [
                {
                    "dealer_id": "dealer-a",
                    "watch_id": "watch-1",
                    "status": "active",
                    "usd_price": 70000,
                    "messages": {"received_at": "2026-06-20T08:00:00+00:00"},
                },
                {
                    "dealer_id": "dealer-b",
                    "watch_id": "watch-2",
                    "status": "active",
                    "usd_price": 80000,
                    "messages": {"received_at": "2026-06-27T12:00:00+00:00"},
                },
            ],
        )

        assert rows[0]["name"] == "Beta"
        assert rows[1]["name"] == "Alpha"

    def test_build_dealer_profile_uses_fallback_name(self) -> None:
        profile = build_dealer_profile(
            {
                "display_name": "",
                "phone_number": "+85212345678",
                "whatsapp_id": "85212345678",
                "company_name": "Elite Watches",
                "country": "Hong Kong",
                "is_active": True,
                "created_at": "2026-06-01T10:00:00+00:00",
                "updated_at": "2026-06-27T12:00:00+00:00",
            }
        )

        assert profile["name"] == "+85212345678"
        assert profile["company_name"] == "Elite Watches"
        assert profile["country"] == "Hong Kong"
        assert profile["is_active"] == "Active"

    def test_build_dealer_offer_rows_includes_watch_fields(self) -> None:
        rows = build_dealer_offer_rows(
            [
                {
                    "watch_id": "watch-1",
                    "watch": {
                        "brand": "Rolex",
                        "reference": "126200",
                        "model": "Datejust",
                    },
                    "group_name": "HK Dealers",
                    "original_price": 560000,
                    "original_currency": "HKD",
                    "usd_price": 72000,
                    "card_date": "2024-01",
                    "condition": "new",
                    "received_at": "2026-06-27T12:00:00+00:00",
                }
            ]
        )

        assert rows[0]["brand"] == "Rolex"
        assert rows[0]["reference"] == "126200"
        assert rows[0]["watch_id"] == "watch-1"
        assert rows[0]["usd_price"] == "$72,000"


class TestDealersPage:
    @patch("app.build_dealer_list_rows")
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.list_dealers", return_value=[{"id": "dealer-1", "display_name": "HK Dealer"}])
    def test_dealers_page_renders_table(
        self,
        mock_list_dealers: MagicMock,
        mock_list_offers: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_build_rows.return_value = [
            {
                "id": "dealer-1",
                "name": "HK Dealer",
                "total_offers": 5,
                "active_offers": 4,
                "average_asking_price": "$75,000",
                "lowest_asking_price": "$70,000",
                "highest_asking_price": "$82,000",
                "last_activity": "2026-06-27 12:00",
            }
        ]

        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert "Dealers" in response.text
        assert "HK Dealer" in response.text
        assert "$75,000" in response.text
        assert 'data-href="/dealers/dealer-1"' in response.text
        mock_build_rows.assert_called_once_with(
            mock_list_dealers.return_value,
            mock_list_offers.return_value,
        )

    @patch("app.build_dealer_list_rows", return_value=[])
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.list_dealers", return_value=[])
    def test_dealers_page_shows_empty_state(
        self,
        mock_list_dealers: MagicMock,
        mock_list_offers: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert "No dealers found yet." in response.text


class TestDealerDetailPage:
    @patch("app.build_dealer_offer_rows")
    @patch("app.get_active_offers_for_dealer", return_value=[])
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.dealer_has_offers", return_value=True)
    @patch("app.get_dealer_by_id")
    def test_dealer_detail_page_renders_profile_and_stats(
        self,
        mock_get_dealer: MagicMock,
        mock_has_offers: MagicMock,
        mock_list_offers: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_build_offer_rows: MagicMock,
    ) -> None:
        mock_get_dealer.return_value = {
            "id": "dealer-1",
            "display_name": "HK Dealer",
            "phone_number": "+85212345678",
            "whatsapp_id": "85212345678",
            "company_name": "Elite Watches",
            "country": "Hong Kong",
            "is_active": True,
            "contact_type": "dealer",
            "created_at": "2026-06-01T10:00:00+00:00",
            "updated_at": "2026-06-27T12:00:00+00:00",
        }
        mock_build_offer_rows.return_value = [
            {
                "watch_id": "watch-1",
                "brand": "Rolex",
                "reference": "126200",
                "model": "Datejust",
                "group_name": "HK Dealers",
                "original_price": "HKD 560,000",
                "usd_price": "$72,000",
                "card_date": "2024-01",
                "condition": "New",
                "received_at": "2026-06-27 12:00",
            }
        ]

        client = TestClient(app)
        response = client.get("/dealers/dealer-1")

        assert response.status_code == 200
        assert "HK Dealer" in response.text
        assert "Elite Watches" in response.text
        assert "Active offers" in response.text
        assert "126200" in response.text
        assert "/watch/watch-1" in response.text

    @patch("app.get_dealer_by_id", return_value=None)
    def test_dealer_detail_page_returns_404_for_missing_dealer(
        self,
        mock_get_dealer: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/dealers/missing-dealer")

        assert response.status_code == 404

    @patch("app.build_dealer_list_rows")
    @patch("app.list_offer_intelligence_rows")
    @patch("app.list_dealers")
    def test_navbar_includes_dealers_link(
        self,
        mock_list_dealers: MagicMock,
        mock_list_offers: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = []
        mock_list_offers.return_value = []
        mock_build_rows.return_value = []

        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert 'href="/dealers"' in response.text
