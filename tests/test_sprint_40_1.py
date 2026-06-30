"""Tests for Sprint 40.1 WhatsApp backlog-safe webhook ingest."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from evolution_webhook import handle_evolution_webhook
from whatsapp_ingest_config import set_app_started_at_for_tests

APP_STARTED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
FRESH_MESSAGE_AT = datetime(2024, 6, 1, 12, 5, 0, tzinfo=timezone.utc)
OLD_MESSAGE_AT = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _payload(
    *,
    whatsapp_message_id: str = "WA-MSG-FRESH",
    received_at: datetime = FRESH_MESSAGE_AT,
    text: str = "ROLEX 126200 green jub 74000usd",
) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "mrv4ult",
        "data": {
            "key": {
                "remoteJid": "120363000000000000@g.us",
                "fromMe": False,
                "id": whatsapp_message_id,
                "participantAlt": "+31612345678",
            },
            "message": {"conversation": text},
            "messageTimestamp": int(received_at.timestamp()),
            "pushName": "Dealer A",
            "subject": "HK Dealers",
        },
    }


@pytest.fixture(autouse=True)
def _reset_startup_time() -> None:
    set_app_started_at_for_tests(APP_STARTED_AT)
    yield
    set_app_started_at_for_tests(None)


class TestBacklogSafeWebhookIngest:
    @patch("evolution_webhook.collect_message")
    @patch.dict("os.environ", {"ENABLE_BACKLOG_INGEST": "false"}, clear=False)
    def test_fresh_webhook_is_ingested(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-fresh",
        }

        result = handle_evolution_webhook(_payload())

        assert result["status"] == "imported"
        mock_collect.assert_called_once()

    @patch("evolution_webhook.collect_message")
    @patch.dict("os.environ", {"ENABLE_BACKLOG_INGEST": "false"}, clear=False)
    def test_old_webhook_skipped_when_backlog_disabled(self, mock_collect: MagicMock) -> None:
        result = handle_evolution_webhook(
            _payload(
                whatsapp_message_id="WA-MSG-OLD",
                received_at=OLD_MESSAGE_AT,
            )
        )

        assert result["status"] == "skipped_backlog"
        assert result["reason"] == "backlog ingest disabled"
        mock_collect.assert_not_called()

    @patch("evolution_webhook.collect_message")
    @patch.dict("os.environ", {"ENABLE_BACKLOG_INGEST": "true"}, clear=False)
    def test_old_webhook_ingested_when_backlog_enabled(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-backlog",
        }

        result = handle_evolution_webhook(
            _payload(
                whatsapp_message_id="WA-MSG-BACKLOG",
                received_at=OLD_MESSAGE_AT,
            )
        )

        assert result["status"] == "imported"
        mock_collect.assert_called_once()

    @patch("evolution_webhook.collect_message")
    @patch("database.find_message_by_whatsapp_id")
    def test_duplicate_whatsapp_message_id_is_skipped(
        self,
        mock_find_message: MagicMock,
        mock_collect: MagicMock,
    ) -> None:
        mock_find_message.return_value = {
            "id": "message-existing",
            "whatsapp_message_id": "WA-MSG-DUP",
        }

        result = handle_evolution_webhook(
            _payload(whatsapp_message_id="WA-MSG-DUP")
        )

        assert result["status"] == "already_imported"
        assert result["already_processed"] is True
        mock_collect.assert_not_called()

    @pytest.mark.no_auto_login
    @patch("evolution_webhook.collect_message")
    @patch.dict("os.environ", {"ENABLE_BACKLOG_INGEST": "false"}, clear=False)
    def test_skipped_backlog_still_returns_http_200(self, mock_collect: MagicMock) -> None:
        from app import app as fastapi_app

        with patch("app.start_whatsapp_listener"), patch("app.stop_whatsapp_listener"):
            client = TestClient(fastapi_app)

        response = client.post(
            "/webhook/evolution",
            json=_payload(
                whatsapp_message_id="WA-MSG-SKIP-HTTP",
                received_at=OLD_MESSAGE_AT,
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "skipped_backlog"
        mock_collect.assert_not_called()
