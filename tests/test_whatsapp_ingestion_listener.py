"""Regression tests for live WhatsApp webhook ingestion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import is_public_path
from evolution_webhook import handle_evolution_webhook
from whatsapp_listener import start_whatsapp_listener


GROUP_MESSAGE_PAYLOAD = {
    "event": "messages.upsert",
    "instance": "mrv4ult",
    "data": {
        "key": {
            "remoteJid": "120363000000000000@g.us",
            "fromMe": False,
            "participant": "31612345678@s.whatsapp.net",
            "participantAlt": "+31612345678",
        },
        "message": {"conversation": "ROLEX 126200 green jub 74000usd"},
        "messageTimestamp": 1719496800,
        "pushName": "Dealer A",
    },
}


class TestWhatsAppPublicWebhookAccess:
    def test_is_public_path_includes_evolution_webhook(self) -> None:
        assert is_public_path("/webhook/evolution") is True
        assert is_public_path("/activity") is False

    @pytest.mark.no_auto_login
    @patch("evolution_webhook.collect_message")
    def test_webhook_route_accessible_without_login(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "group": "HK Dealers",
            "dealer_whatsapp": "+31612345678",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-1",
            "saved": True,
        }

        from app import app as fastapi_app

        with patch("app.start_whatsapp_listener"), patch("app.stop_whatsapp_listener"):
            client = TestClient(fastapi_app)

        response = client.post("/webhook/evolution", json=GROUP_MESSAGE_PAYLOAD)

        assert response.status_code == 200
        assert response.headers.get("location") != "/login"
        body = response.json()
        assert body["status"] == "imported"
        assert body["import_log_id"] == "log-1"
        mock_collect.assert_called_once()


class TestWhatsAppListenerStartup:
    @patch.dict("os.environ", {"MRV4ULT_WEBHOOK_URL": ""}, clear=False)
    @patch("whatsapp_listener.find_webhook")
    @patch("whatsapp_listener.get_instance_status")
    def test_start_whatsapp_listener_reports_connected_session(
        self,
        mock_status: MagicMock,
        mock_find_webhook: MagicMock,
    ) -> None:
        from whatsapp_listener import stop_whatsapp_listener

        stop_whatsapp_listener()
        mock_status.return_value = {
            "connected": True,
            "phone_number": "+31612345678",
            "state": "open",
        }
        mock_find_webhook.return_value = {
            "enabled": True,
            "url": "http://host.docker.internal:8000/webhook/evolution",
        }

        summary = start_whatsapp_listener()

        assert summary["connected"] is True
        assert summary["webhook_enabled"] is True
        mock_status.assert_called_once()
        mock_find_webhook.assert_called_once()


class TestWhatsAppWebhookFlow:
    @patch("evolution_webhook.collect_message")
    def test_handle_evolution_webhook_returns_ingest_status(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "request_intent",
            "group": "Requests",
            "dealer_whatsapp": "+31612345678",
            "watches_parsed": 0,
            "new_offers": 0,
            "duplicate_offers": 0,
            "import_log_id": "log-wtb-1",
            "saved": True,
        }

        result = handle_evolution_webhook(
            {
                **GROUP_MESSAGE_PAYLOAD,
                "data": {
                    **GROUP_MESSAGE_PAYLOAD["data"],
                    "message": {"conversation": "WTB Rolex Daytona 116500LN"},
                },
            }
        )

        assert result["status"] == "imported"
        assert result["ingest_status"] == "request_intent"
        assert result["import_log_id"] == "log-wtb-1"
        mock_collect.assert_called_once()

    @patch("evolution_webhook.collect_message")
    def test_lid_participant_phone_resolved_via_sender_pn(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "group": "HK Dealers",
            "dealer_whatsapp": "85291234567",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-lid-1",
            "saved": True,
        }

        payload = {
            "event": "messages.upsert",
            "instance": "mrv4ult",
            "data": {
                "key": {
                    "remoteJid": "120363000000000000@g.us",
                    "fromMe": False,
                    "participant": "61074569195589@lid",
                    "senderPn": "85291234567",
                },
                "message": {"conversation": "ROLEX 126500LN 145k usd"},
                "messageTimestamp": 1719496800,
                "pushName": "HK Dealer",
            },
        }

        result = handle_evolution_webhook(payload)

        assert result["status"] == "imported"
        whatsapp_message = mock_collect.call_args.args[0]
        assert whatsapp_message.dealer_whatsapp == "85291234567"
