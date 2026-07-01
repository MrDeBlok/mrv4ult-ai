"""Regression tests for Sprint 45.6 dealer detail statistics removal."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app

REMOVED_STAT_LABELS = (
    "Statistics",
    "Total offers",
    "Average asking price",
    "Lowest asking price",
    "Highest asking price",
    "Unique watches",
    "Last activity",
)


def _dealer_record() -> dict:
    return {
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


class TestDealerDetailStatisticsRemoval:
    @patch("app.build_dealer_offer_rows")
    @patch("app.get_active_offers_for_dealer", return_value=[])
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.dealer_has_offers", return_value=True)
    @patch("app.get_dealer_by_id")
    def test_statistics_panel_not_rendered(
        self,
        mock_get_dealer: MagicMock,
        _mock_has_offers: MagicMock,
        _mock_list_offers: MagicMock,
        _mock_get_active_offers: MagicMock,
        mock_build_offer_rows: MagicMock,
    ) -> None:
        mock_get_dealer.return_value = _dealer_record()
        mock_build_offer_rows.return_value = []

        client = TestClient(app)
        response = client.get("/dealers/dealer-1")

        assert response.status_code == 200
        for label in REMOVED_STAT_LABELS:
            assert label not in response.text

    @patch("app.build_dealer_offer_rows")
    @patch("app.get_active_offers_for_dealer", return_value=[])
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.dealer_has_offers", return_value=True)
    @patch("app.get_dealer_by_id")
    def test_dealer_profile_and_active_offers_still_render(
        self,
        mock_get_dealer: MagicMock,
        _mock_has_offers: MagicMock,
        _mock_list_offers: MagicMock,
        _mock_get_active_offers: MagicMock,
        mock_build_offer_rows: MagicMock,
    ) -> None:
        mock_get_dealer.return_value = _dealer_record()
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
        assert "WhatsApp ID" in response.text
        assert "Phone number" in response.text
        assert "Company" in response.text
        assert "Country" in response.text
        assert "Active offers" in response.text
        assert "126200" in response.text
        assert "/watch/watch-1" in response.text

    @patch("app.format_dealer_stats")
    @patch("app.compute_dealer_stats")
    @patch("app.build_dealer_offer_rows", return_value=[])
    @patch("app.get_active_offers_for_dealer", return_value=[])
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.dealer_has_offers", return_value=True)
    @patch("app.get_dealer_by_id")
    def test_backend_stats_helpers_still_called(
        self,
        mock_get_dealer: MagicMock,
        _mock_has_offers: MagicMock,
        _mock_list_offers: MagicMock,
        _mock_get_active_offers: MagicMock,
        _mock_build_offer_rows: MagicMock,
        mock_compute_dealer_stats: MagicMock,
        mock_format_dealer_stats: MagicMock,
    ) -> None:
        mock_get_dealer.return_value = _dealer_record()
        mock_compute_dealer_stats.return_value = {"total_offers": 5}
        mock_format_dealer_stats.return_value = {
            "total_offers": 5,
            "active_offers": 4,
            "average_asking_price": "$75,000",
            "lowest_asking_price": "$70,000",
            "highest_asking_price": "$82,000",
            "last_activity": "2026-06-27 12:00",
            "unique_watches": 2,
        }

        client = TestClient(app)
        response = client.get("/dealers/dealer-1")

        assert response.status_code == 200
        mock_compute_dealer_stats.assert_called_once()
        mock_format_dealer_stats.assert_called_once()
