"""P0 hotfix tests for WhatsApp webhook ingest pipeline tracing and routing."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from evolution_client import EvolutionAPIError
from evolution_webhook import (
    WEBHOOK_DECISION_PREFIX,
    WEBHOOK_TRACE_PREFIX,
    _group_name_cache,
    build_webhook_trace,
    handle_evolution_webhook,
    jid_to_contact_id,
    log_webhook_decision,
    log_webhook_trace,
    reset_group_name_cache,
    resolve_group_name,
)
from whatsapp_ingest_config import set_app_started_at_for_tests

APP_STARTED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
FRESH_MESSAGE_AT = datetime(2024, 6, 1, 12, 5, 0, tzinfo=timezone.utc)


def _working_group_payload() -> dict:
    return {
        "event": "messages.upsert",
        "instance": "mrv4ult",
        "data": {
            "key": {
                "remoteJid": "120363000000000000@g.us",
                "fromMe": False,
                "id": "WA-WORKING-1",
                "participantAlt": "+85291234567",
            },
            "message": {"conversation": "WTS Rolex Datejust 126331 / Pre-Owned / €11.950"},
            "messageTimestamp": int(FRESH_MESSAGE_AT.timestamp()),
            "pushName": "HK Dealer",
            "subject": "HK Dealers",
            "messageType": "conversation",
        },
    }


def _lid_group_payload(*, sender_pn: str | None = None) -> dict:
    key = {
        "remoteJid": "120363111111111111@g.us",
        "fromMe": False,
        "id": "WA-LID-GROUP-1",
        "participant": "252497000181916@lid",
    }
    if sender_pn is not None:
        key["senderPn"] = sender_pn
    return {
        "event": "messages.upsert",
        "instance": "mrv4ult",
        "data": {
            "key": key,
            "message": {"conversation": "WTS AP Royal Oak 15500ST blue dial 42k USD"},
            "messageTimestamp": int(FRESH_MESSAGE_AT.timestamp()),
            "pushName": "Secondary Dealer",
            "subject": "EU Dealers",
            "messageType": "conversation",
        },
        "sender": "17786809043@s.whatsapp.net",
    }


def _private_chat_payload() -> dict:
    return {
        "event": "messages.upsert",
        "instance": "mrv4ult",
        "data": {
            "key": {
                "remoteJid": "69385314111689@lid",
                "remoteJidAlt": "85291234567@s.whatsapp.net",
                "fromMe": False,
                "id": "WA-PRIVATE-1",
            },
            "message": {"conversation": "WTS Rolex 126331 full set 2021 11950 eur"},
            "messageTimestamp": int(FRESH_MESSAGE_AT.timestamp()),
            "pushName": "Private Dealer",
            "messageType": "conversation",
        },
    }


@pytest.fixture(autouse=True)
def _reset_startup_time() -> None:
    set_app_started_at_for_tests(APP_STARTED_AT)
    yield
    set_app_started_at_for_tests(None)


@pytest.fixture(autouse=True)
def _isolated_group_name_cache() -> None:
    reset_group_name_cache()
    yield
    reset_group_name_cache()


@pytest.fixture(autouse=True)
def _mock_fetch_group_info() -> MagicMock:
    with patch(
        "evolution_webhook.fetch_group_info",
        side_effect=EvolutionAPIError("group metadata unavailable in tests"),
    ) as mock_fetch:
        yield mock_fetch


class TestWebhookPipelineIngest:
    @patch("evolution_webhook.collect_message")
    def test_working_group_payload_ingests(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "group": "HK Dealers",
            "dealer_whatsapp": "+85291234567",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-working",
            "saved": True,
        }

        result = handle_evolution_webhook(_working_group_payload())

        assert result["status"] == "imported"
        assert result["trace"]["group_name"] == "HK Dealers"
        mock_collect.assert_called_once()
        assert mock_collect.call_args.args[0].group_name == "HK Dealers"

    @patch("evolution_webhook.collect_message")
    def test_non_working_group_lid_participant_ingests(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "group": "EU Dealers",
            "dealer_whatsapp": "lid:252497000181916",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-lid-group",
            "saved": True,
        }

        result = handle_evolution_webhook(_lid_group_payload())

        assert result["status"] == "imported"
        whatsapp_message = mock_collect.call_args.args[0]
        assert whatsapp_message.dealer_whatsapp == "lid:252497000181916"
        assert whatsapp_message.group_name == "EU Dealers"

    @patch("evolution_webhook.collect_message")
    def test_lid_group_prefers_sender_pn_over_lid(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "group": "EU Dealers",
            "dealer_whatsapp": "85299887766",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-lid-sender",
            "saved": True,
        }

        handle_evolution_webhook(_lid_group_payload(sender_pn="85299887766"))

        assert mock_collect.call_args.args[0].dealer_whatsapp == "85299887766"

    @patch("evolution_webhook.collect_message")
    def test_private_chat_payload_with_offer_text_ingests(self, mock_collect: MagicMock) -> None:
        mock_collect.return_value = {
            "status": "success",
            "group": "Private Offers",
            "dealer_whatsapp": "85291234567",
            "watches_parsed": 1,
            "new_offers": 1,
            "duplicate_offers": 0,
            "import_log_id": "log-private",
            "saved": True,
        }

        result = handle_evolution_webhook(_private_chat_payload())

        assert result["status"] == "imported"
        whatsapp_message = mock_collect.call_args.args[0]
        assert whatsapp_message.group_name == "Private Offers"
        assert whatsapp_message.dealer_whatsapp == "85291234567"


class TestWebhookPipelineSkips:
    @patch("evolution_webhook.collect_message")
    def test_skipped_payload_includes_skip_reason(self, mock_collect: MagicMock) -> None:
        payload = _working_group_payload()
        payload["data"]["key"]["fromMe"] = True

        result = handle_evolution_webhook(payload)

        assert result["status"] == "ignored"
        assert result["reason"] == "outgoing message"
        assert result["trace"]["filters"]["from_me_filter"] is True
        mock_collect.assert_not_called()

    @patch("evolution_webhook.collect_message")
    @patch("database.find_message_by_whatsapp_id")
    def test_duplicate_still_skipped_with_reason(
        self,
        mock_find_message: MagicMock,
        mock_collect: MagicMock,
    ) -> None:
        mock_find_message.return_value = {"id": "message-existing", "whatsapp_message_id": "WA-WORKING-1"}

        result = handle_evolution_webhook(_working_group_payload())

        assert result["status"] == "already_imported"
        assert result["reason"] == "duplicate whatsapp_message_id"
        assert result["trace"]["filters"]["duplicate_message_filter"] is True
        mock_collect.assert_not_called()

    def test_trace_logging_includes_skip_reason(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level("INFO", logger="mrv4ult.whatsapp.ingest")
        trace = build_webhook_trace({"event": "connection.update", "instance": "mrv4ult", "data": {}})
        log_webhook_trace(trace, decision="skipped", skip_reason="unsupported event")

        assert any(
            WEBHOOK_TRACE_PREFIX in record.message and "skip_reason=unsupported event" in record.message
            for record in caplog.records
        )

    def test_decision_logging_includes_remote_jid_participant_and_import_log_id(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="mrv4ult.whatsapp.ingest")
        trace = build_webhook_trace(_working_group_payload())
        log_webhook_decision(
            trace,
            {
                "status": "imported",
                "import_log_id": "log-123",
                "already_processed": False,
            },
        )

        message = next(record.message for record in caplog.records if WEBHOOK_DECISION_PREFIX in record.message)
        assert "decision=accepted" in message
        assert "remote_jid=120363000000000000@g.us" in message
        assert "participant_alt=+85291234567" in message
        assert "import_log_id=log-123" in message
        assert "already_processed=False" in message

    @patch("evolution_webhook.collect_message")
    @patch.dict("os.environ", {"ENABLE_BACKLOG_INGEST": "false"}, clear=False)
    def test_backlog_skip_emits_decision_log_with_reason(
        self,
        mock_collect: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="mrv4ult.whatsapp.ingest")
        from datetime import datetime, timezone

        old_message_at = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        payload = _working_group_payload()
        payload["data"]["messageTimestamp"] = int(old_message_at.timestamp())
        payload["data"]["key"]["id"] = "WA-BACKLOG-SKIP"

        result = handle_evolution_webhook(payload)

        assert result["status"] == "skipped_backlog"
        assert result["reason"] == "backlog ingest disabled"
        mock_collect.assert_not_called()
        assert any(
            WEBHOOK_DECISION_PREFIX in record.message
            and "decision=skipped" in record.message
            and "skip_reason=backlog ingest disabled" in record.message
            for record in caplog.records
        )


class TestContactIdResolution:
    def test_lid_participant_maps_to_stable_contact_id(self) -> None:
        assert jid_to_contact_id("252497000181916@lid") == "lid:252497000181916"

    def test_private_phone_jid_maps_to_digits(self) -> None:
        assert jid_to_contact_id("85291234567@s.whatsapp.net") == "85291234567"


class TestGroupNameResolution:
    GROUP_JID = "120363000000000000@g.us"

    def test_payload_subject_wins_over_cached_jid_fallback(self) -> None:
        _group_name_cache[self.GROUP_JID] = self.GROUP_JID

        resolved = resolve_group_name(
            self.GROUP_JID,
            {"subject": "HK Dealers"},
        )

        assert resolved == "HK Dealers"
        assert _group_name_cache[self.GROUP_JID] == "HK Dealers"

    def test_cached_display_name_used_when_payload_has_no_subject(self) -> None:
        _group_name_cache[self.GROUP_JID] = "HK Dealers"

        resolved = resolve_group_name(self.GROUP_JID, {})

        assert resolved == "HK Dealers"

    def test_falls_back_to_remote_jid_when_no_group_name_exists(
        self,
        _mock_fetch_group_info: MagicMock,
    ) -> None:
        resolved = resolve_group_name(self.GROUP_JID, {})

        assert resolved == self.GROUP_JID
        _mock_fetch_group_info.assert_called_once()
