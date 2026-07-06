"""Regression tests for dealer active offer source message links."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, normalize_dealer_offer
from dealer_intelligence import (
    activity_detail_url_for_import_log,
    attach_dealer_offer_source_urls,
    build_dealer_offer_rows,
)
from tests.conftest import ADMIN_USER, TRADER_ONE


def _raw_dealer_offer(*, message_id: str | None = "msg-1") -> dict:
    message = {
        "received_at": "2026-06-27T12:00:00+00:00",
        "group_id": "group-1",
        "groups": {"name": "HK Dealers"},
    }
    if message_id is not None:
        message["id"] = message_id
    return {
        "id": "offer-1",
        "watch_id": "watch-1",
        "original_price": 560000,
        "original_currency": "HKD",
        "usd_price": 72000,
        "card_date": "2024-01",
        "condition": "new",
        "watches": {"brand": "Rolex", "reference": "126200", "model": "Datejust"},
        "messages": message,
        "dealers": {"contact_type": "dealer"},
    }


def _import_log(import_log_id: str = "log-1", *, watches_parsed: int = 1) -> dict:
    return {
        "id": import_log_id,
        "message_id": "msg-1",
        "watches_parsed": watches_parsed,
        "status": "success",
    }


class TestDealerOfferSourceHelpers:
    def test_normalize_dealer_offer_includes_message_id(self) -> None:
        normalized = normalize_dealer_offer(_raw_dealer_offer())
        assert normalized["message_id"] == "msg-1"

    def test_activity_detail_url_for_visible_import_log(self) -> None:
        url = activity_detail_url_for_import_log(_import_log(), user=ADMIN_USER)
        assert url == "/activity/log-1"

    def test_activity_detail_url_hidden_for_private_import(self) -> None:
        private_log = {
            **_import_log(),
            "imported_by_user_id": "owner-2",
            "watches_parsed": 0,
            "status": "noise",
        }
        url = activity_detail_url_for_import_log(private_log, user=TRADER_ONE)
        assert url is None

    def test_attach_dealer_offer_source_urls(self) -> None:
        offers = attach_dealer_offer_source_urls(
            [normalize_dealer_offer(_raw_dealer_offer())],
            {"msg-1": _import_log("log-99")},
            user=ADMIN_USER,
        )
        assert offers[0]["source_url"] == "/activity/log-99"

    def test_build_dealer_offer_rows_includes_source_url(self) -> None:
        rows = build_dealer_offer_rows(
            [
                {
                    **normalize_dealer_offer(_raw_dealer_offer()),
                    "source_url": "/activity/log-1",
                }
            ]
        )
        assert rows[0]["source_url"] == "/activity/log-1"
        assert rows[0]["reference"] == "126200"

    def test_build_dealer_offer_rows_without_source_url(self) -> None:
        rows = build_dealer_offer_rows([normalize_dealer_offer(_raw_dealer_offer(message_id=None))])
        assert rows[0]["source_url"] is None


class TestDealerDetailOfferLinks:
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.dealer_has_offers", return_value=True)
    @patch("app.get_dealer_by_id")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_dealer")
    def test_active_offer_reference_links_to_activity_detail(
        self,
        mock_get_active_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_get_dealer: MagicMock,
        _mock_has_offers: MagicMock,
        _mock_list_offers: MagicMock,
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
        mock_get_active_offers.return_value = [_raw_dealer_offer()]
        import_log = _import_log("log-offer-1")
        mock_load_lookups.return_value = (
            {"msg-1": import_log},
            {"log-offer-1": import_log},
            {},
        )

        client = TestClient(app)
        response = client.get("/dealers/dealer-1")

        assert response.status_code == 200
        assert 'href="/activity/log-offer-1"' in response.text
        assert "View original" in response.text
        assert 'href="/watch/watch-1"' in response.text
        assert "126200" in response.text
        assert "Statistics" not in response.text

    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.dealer_has_offers", return_value=True)
    @patch("app.get_dealer_by_id")
    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_active_offers_for_dealer")
    def test_active_offer_without_source_renders_plain_reference(
        self,
        mock_get_active_offers: MagicMock,
        _mock_load_lookups: MagicMock,
        mock_get_dealer: MagicMock,
        _mock_has_offers: MagicMock,
        _mock_list_offers: MagicMock,
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
        mock_get_active_offers.return_value = [_raw_dealer_offer(message_id=None)]

        client = TestClient(app)
        response = client.get("/dealers/dealer-1")

        assert response.status_code == 200
        assert "126200" in response.text
        assert 'href="/activity/' not in response.text
        assert "View original" not in response.text
