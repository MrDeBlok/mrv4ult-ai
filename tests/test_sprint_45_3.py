"""Tests for Sprint 45.3 dealer contact on match detail."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from match_detail import (
    build_match_detail,
    clean_whatsapp_number_for_link,
    resolve_match_dealer_contact,
)
from request_profit import attach_profit_to_matches
from tests.conftest import ADMIN_USER
from tests.test_sprint_45_2 import ENRICHED_MATCH, MATCH_ID, MESSAGE, REQUEST

PRIVATE_IMPORT_LOG = {
    "id": "import-private",
    "import_time": "2026-06-27T12:00:00+00:00",
    "group_name": "Private Chat",
    "dealer_alias": "",
    "dealer_whatsapp": "+85291234567",
    "message_id": "msg-private",
}


class TestResolveMatchDealerContact:
    def test_prefers_import_log_whatsapp(self) -> None:
        contact = resolve_match_dealer_contact(
            {
                "dealer_alias": "HK Dealer",
                "dealer_whatsapp": "+85212345678",
                "group_name": "HK Dealers",
            },
            dealer={
                "display_name": "Stored Dealer",
                "phone_number": "+441234567890",
                "whatsapp_id": "441234567890",
            },
        )

        assert contact["name"] == "HK Dealer"
        assert contact["contact_number"] == "+85212345678"
        assert contact["group_name"] == "HK Dealers"
        assert contact["message_dealer_url"] == "https://wa.me/85212345678"

    def test_private_contact_still_shows_number(self) -> None:
        contact = resolve_match_dealer_contact(PRIVATE_IMPORT_LOG)

        assert contact["name"] == "Private contact"
        assert contact["contact_number"] == "+85291234567"
        assert contact["has_contact_number"] is True

    def test_falls_back_to_dealer_phone_and_whatsapp_id(self) -> None:
        contact = resolve_match_dealer_contact(
            {"dealer_alias": "Alias Only", "dealer_whatsapp": "", "group_name": "EU"},
            dealer={
                "display_name": "EU Dealer",
                "phone_number": "",
                "whatsapp_id": "31612345678",
            },
        )

        assert contact["contact_number"] == "31612345678"
        assert contact["message_dealer_url"] == "https://wa.me/31612345678"

    def test_no_number_shows_fallback(self) -> None:
        contact = resolve_match_dealer_contact(
            {"dealer_alias": "", "dealer_whatsapp": "", "group_name": "Unknown"},
        )

        assert contact["contact_number"] == "No contact number available"
        assert contact["message_dealer_url"] is None
        assert contact["has_contact_number"] is False

    def test_clean_whatsapp_number_for_link(self) -> None:
        assert clean_whatsapp_number_for_link("+852 9123-4567") == "85291234567"


class TestMatchDetailDealerContactPage:
    def _detail_with_import_log(self, import_log: dict) -> dict:
        enriched = {
            **ENRICHED_MATCH,
            "import_log": {**ENRICHED_MATCH["import_log"], **import_log},
        }
        match = attach_profit_to_matches(REQUEST, [enriched])[0]
        return build_match_detail(REQUEST, match, message=MESSAGE, user=ADMIN_USER)

    @patch("app.load_match_detail")
    def test_match_detail_shows_dealer_whatsapp_number(
        self,
        mock_load_detail: MagicMock,
    ) -> None:
        mock_load_detail.return_value = self._detail_with_import_log(
            {
                "dealer_alias": "HK Dealer",
                "dealer_whatsapp": "+85212345678",
                "group_name": "HK Dealers",
            }
        )

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 200
        assert "HK Dealer" in response.text
        assert "+85212345678" in response.text
        assert "HK Dealers" in response.text

    @patch("app.load_match_detail")
    def test_private_contact_still_shows_number_on_page(
        self,
        mock_load_detail: MagicMock,
    ) -> None:
        mock_load_detail.return_value = self._detail_with_import_log(PRIVATE_IMPORT_LOG)

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 200
        assert "Private contact" in response.text
        assert "+85291234567" in response.text

    @patch("app.load_match_detail")
    def test_message_dealer_link_renders_with_cleaned_number(
        self,
        mock_load_detail: MagicMock,
    ) -> None:
        mock_load_detail.return_value = self._detail_with_import_log(
            {
                "dealer_alias": "HK Dealer",
                "dealer_whatsapp": "+852 9123-4567",
                "group_name": "HK Dealers",
            }
        )

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 200
        assert 'href="https://wa.me/85291234567"' in response.text
        assert "Message dealer" in response.text

    @patch("app.load_match_detail")
    def test_no_number_shows_fallback_on_page(
        self,
        mock_load_detail: MagicMock,
    ) -> None:
        detail = self._detail_with_import_log(
            {"dealer_alias": "", "dealer_whatsapp": "", "group_name": "Unknown"},
        )
        mock_load_detail.return_value = detail

        client = TestClient(app)
        response = client.get(f"/matches/{MATCH_ID}")

        assert response.status_code == 200
        assert "No contact number available" in response.text
        assert "Message dealer" not in response.text
