"""Tests for Sprint 49.2 database availability hardening."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from auth import SESSION_USER_ID_KEY, get_current_user
from database import DatabaseUnavailableError
from database_availability import DATABASE_UNAVAILABLE_MESSAGE
from evolution_webhook import handle_evolution_webhook
from tests.conftest import ADMIN_USER

try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover
    APIError = Exception  # type: ignore[misc, assignment]


def _session_request(user_id: str) -> MagicMock:
    request = MagicMock()
    request.scope = {"type": "http", "session": {SESSION_USER_ID_KEY: user_id}}
    request.session = request.scope["session"]
    return request


def _working_group_payload(*, message_id: str = "WA-DUP-1") -> dict:
    return {
        "event": "messages.upsert",
        "instance": "mrv4ult",
        "data": {
            "key": {
                "remoteJid": "120363000000000000@g.us",
                "fromMe": False,
                "id": message_id,
                "participantAlt": "+85291234567",
            },
            "message": {"conversation": "WTS Rolex Datejust 126331 / Pre-Owned / €11.950"},
            "messageTimestamp": 1719496800,
            "pushName": "HK Dealer",
            "subject": "HK Dealers",
            "messageType": "conversation",
        },
    }


class TestAvailabilitySafeAuthentication:
    @patch("database.users_table_supported", return_value=True)
    @patch("database.get_user_by_id", return_value=ADMIN_USER)
    def test_current_user_lookup_succeeds(
        self,
        _mock_get_user: MagicMock,
        _mock_supported: MagicMock,
    ) -> None:
        user = get_current_user(_session_request(ADMIN_USER["id"]))

        assert user == ADMIN_USER

    @patch("database.users_table_supported", return_value=True)
    @patch(
        "database.get_user_by_id",
        side_effect=DatabaseUnavailableError(
            operation="users.get_by_id",
            status_code=521,
        ),
    )
    def test_current_user_lookup_returns_anonymous_during_outage(
        self,
        _mock_get_user: MagicMock,
        _mock_supported: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="database_availability"):
            user = get_current_user(_session_request(ADMIN_USER["id"]))

        assert user is None
        assert any(
            "Database temporarily unavailable (HTTP 521). Rendering anonymous layout."
            in record.getMessage()
            for record in caplog.records
        )

    @patch("database.users_table_supported", return_value=True)
    @patch(
        "database.get_user_by_id",
        side_effect=APIError({"message": "bad request", "code": "PGRST100", "details": "", "hint": ""}),
    )
    def test_non_availability_exceptions_still_propagate_from_current_user(
        self,
        _mock_get_user: MagicMock,
        _mock_supported: MagicMock,
    ) -> None:
        with pytest.raises(APIError):
            get_current_user(_session_request(ADMIN_USER["id"]))


@pytest.mark.no_auto_login
class TestWatchReferenceLayoutDuringOutage:
    @patch("database.users_table_supported", return_value=True)
    @patch(
        "database.get_user_by_id",
        side_effect=DatabaseUnavailableError(
            operation="users.get_by_id",
            status_code=521,
        ),
    )
    def test_shared_layout_renders_during_database_outage(
        self,
        _mock_get_user: MagicMock,
        _mock_supported: MagicMock,
    ) -> None:
        from app import templates

        request = _session_request(ADMIN_USER["id"])
        response = templates.TemplateResponse(
            request,
            "watch_detail.html",
            {
                "watch": {
                    "brand": "Rolex",
                    "reference": "126200",
                    "model": "N/A",
                },
                "stats": {"count": 0, "lowest_usd": None, "highest_usd": None},
                "offers": [],
                "condition_filter": "all",
                "date_filter": "all",
                "date_from": "",
                "date_to": "",
                "detail_base_url": "/watch-reference?brand=Rolex&reference=126200",
                "condition_urls": {},
                "filter_urls": {},
                "brand_value": "Rolex",
                "reference_value": "126200",
                "database_unavailable": True,
                "database_unavailable_message": DATABASE_UNAVAILABLE_MESSAGE,
            },
            status_code=503,
        )

        body = response.body.decode("utf-8")

        assert response.status_code == 503
        assert DATABASE_UNAVAILABLE_MESSAGE in body
        assert "Logout" not in body

    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_active_offers_for_brand_reference")
    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_reference_detail_still_renders_normally_with_valid_database_data(
        self,
        _mock_get_current_user: MagicMock,
        mock_get_offers: MagicMock,
        _mock_lookups: MagicMock,
    ) -> None:
        mock_get_offers.return_value = [
            {
                "id": "offer-1",
                "watch_id": "watch-1",
                "dealer_id": "dealer-1",
                "message_id": "msg-1",
                "usd_price": 180000,
                "condition": "New",
                "original_price": 180000,
                "original_currency": "USD",
                "card_date": "06/2026",
                "watches": {"dial": "Grey", "model": "Nautilus"},
                "dealers": {"display_name": "Dealer dealer-1", "phone_number": "+85290000001"},
                "messages": {
                    "received_at": "2026-06-01T12:00:00+00:00",
                    "group_id": "g-1",
                    "groups": {"name": "Group A"},
                },
            }
        ]

        with patch("app.start_whatsapp_listener"), patch("app.stop_whatsapp_listener"):
            client = TestClient(app)

        response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5990%2F1A&condition=all"
        )

        assert response.status_code == 200
        assert DATABASE_UNAVAILABLE_MESSAGE not in response.text


class TestEvolutionWebhookDuplicateLookup:
    @patch("evolution_webhook.collect_message")
    @patch("database.find_message_by_whatsapp_id", return_value={"id": "msg-1"})
    def test_duplicate_lookup_succeeds(
        self,
        mock_find_message: MagicMock,
        mock_collect: MagicMock,
    ) -> None:
        mock_collect.return_value = {"status": "success", "saved": True}

        with patch("evolution_webhook.is_whatsapp_webhook_ingest_enabled", return_value=True):
            result = handle_evolution_webhook(_working_group_payload())

        assert result["status"] == "already_imported"
        assert result["already_processed"] is True
        mock_find_message.assert_called_once_with("WA-DUP-1")
        mock_collect.assert_not_called()

    @patch("database.find_message_by_whatsapp_id")
    def test_duplicate_lookup_gracefully_exits_during_outage(
        self,
        mock_find_message: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_find_message.side_effect = DatabaseUnavailableError(
            operation="messages.find_by_whatsapp_id",
            status_code=521,
        )

        with patch("evolution_webhook.is_whatsapp_webhook_ingest_enabled", return_value=True):
            with caplog.at_level(logging.WARNING, logger="database_availability"):
                result = handle_evolution_webhook(_working_group_payload())

        assert result["status"] == "database_unavailable"
        assert result["reason"] == "database temporarily unavailable"
        assert any(
            "Database temporarily unavailable (HTTP 521). Skipping webhook processing."
            in record.getMessage()
            for record in caplog.records
        )

    @patch("database.find_message_by_whatsapp_id")
    def test_unexpected_duplicate_lookup_exceptions_still_propagate(
        self,
        mock_find_message: MagicMock,
    ) -> None:
        mock_find_message.side_effect = RuntimeError("programming mistake")

        with patch("evolution_webhook.is_whatsapp_webhook_ingest_enabled", return_value=True):
            with pytest.raises(RuntimeError, match="programming mistake"):
                handle_evolution_webhook(_working_group_payload())
