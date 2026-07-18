"""Regression tests for Reference Results detail database availability handling."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from database import (
    DatabaseUnavailableError,
    _execute_import_log_summary_batch,
    find_watch_ids_for_brand_reference,
    get_active_offers_for_brand_reference,
)
from database_availability import (
    DATABASE_UNAVAILABLE_MESSAGE,
    execute_postgrest_read,
    is_postgrest_validation_error,
    is_transient_database_error,
    summarize_exception_message,
)

try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover
    APIError = Exception  # type: ignore[misc, assignment]


CLOUDFLARE_521_HTML = (
    "<!DOCTYPE html><html><head><title>521: Web server is not responding</title></head>"
    "<body>Cloudflare Ray ID: abc123</body></html>"
)


def _cloudflare_521_exc() -> Exception:
    return Exception(f"JSON could not be generated: {CLOUDFLARE_521_HTML}")


def _detail_offer(
    *,
    watch_id: str = "w-1",
    dealer_id: str = "dealer-1",
    usd_price: int = 180000,
    condition: str | None = "New",
) -> dict:
    return {
        "id": f"offer-{watch_id}",
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "message_id": "msg-1",
        "usd_price": usd_price,
        "condition": condition,
        "original_price": usd_price,
        "original_currency": "USD",
        "card_date": "06/2026",
        "watches": {"dial": "Grey", "model": "Nautilus"},
        "dealers": {"display_name": f"Dealer {dealer_id}", "phone_number": "+85290000001"},
        "messages": {
            "received_at": "2026-06-01T12:00:00+00:00",
            "group_id": "g-1",
            "groups": {"name": "Group A"},
        },
    }


class TestDatabaseAvailabilityHelpers:
    def test_cloudflare_html_is_transient_not_validation(self) -> None:
        exc = _cloudflare_521_exc()
        assert is_transient_database_error(exc) is True
        assert is_postgrest_validation_error(exc) is False

    def test_postgres_unique_violation_is_validation_not_transient(self) -> None:
        exc = APIError({"message": "duplicate key", "code": "23505", "details": "", "hint": ""})
        assert is_postgrest_validation_error(exc) is True
        assert is_transient_database_error(exc) is False

    def test_summarize_exception_message_strips_html_body(self) -> None:
        summary = summarize_exception_message(_cloudflare_521_exc())
        assert CLOUDFLARE_521_HTML not in summary
        assert "upstream_html_error_page" in summary


class TestExecutePostgrestRead:
    @patch("database_availability.time.sleep")
    def test_read_retries_at_most_once(self, mock_sleep: MagicMock) -> None:
        mock_fn = MagicMock(side_effect=[_cloudflare_521_exc(), _cloudflare_521_exc()])

        with pytest.raises(DatabaseUnavailableError):
            execute_postgrest_read("test.read", mock_fn)

        assert mock_fn.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("database_availability.time.sleep")
    def test_read_recovers_on_second_attempt(self, mock_sleep: MagicMock) -> None:
        mock_response = MagicMock()
        mock_fn = MagicMock(side_effect=[_cloudflare_521_exc(), mock_response])

        result = execute_postgrest_read("test.read", mock_fn)

        assert result is mock_response
        assert mock_fn.call_count == 2
        mock_sleep.assert_called_once()

    def test_validation_error_is_not_retried(self) -> None:
        exc = APIError({"message": "bad request", "code": "PGRST100", "details": "", "hint": ""})
        mock_fn = MagicMock(side_effect=exc)

        with pytest.raises(APIError):
            execute_postgrest_read("test.read", mock_fn)

        assert mock_fn.call_count == 1


class TestWatchReferenceDetailRoute:
    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_active_offers_for_brand_reference")
    def test_reference_detail_loads_normally_with_valid_database_data(
        self,
        mock_get_offers: MagicMock,
        _mock_lookups: MagicMock,
    ) -> None:
        mock_get_offers.return_value = [_detail_offer()]

        client = TestClient(app)
        response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5990%2F1A&condition=all"
        )

        assert response.status_code == 200
        assert DATABASE_UNAVAILABLE_MESSAGE not in response.text
        assert "Dealer dealer-1" in response.text

    @patch("app.get_active_offers_for_brand_reference")
    def test_cloudflare_521_does_not_return_500(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.side_effect = DatabaseUnavailableError(
            operation="find_watch_ids_for_brand_reference.watch_scan",
            status_code=521,
        )

        client = TestClient(app)
        response = client.get("/watch-reference?brand=Rolex&reference=126200")

        assert response.status_code == 503
        assert response.status_code != 500

    @patch("app.get_active_offers_for_brand_reference")
    def test_user_sees_temporary_unavailable_message(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.side_effect = DatabaseUnavailableError(
            operation="offers.chunk_lookup",
            status_code=521,
        )

        client = TestClient(app)
        response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5990%2F1A&condition=new&date=7d"
        )

        assert response.status_code == 503
        assert DATABASE_UNAVAILABLE_MESSAGE in response.text

    @patch("app.get_active_offers_for_brand_reference")
    def test_reference_context_preserved_on_unavailable_response(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.side_effect = DatabaseUnavailableError(
            operation="find_watch_ids_for_brand_reference.watch_scan",
        )

        client = TestClient(app)
        response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5990%2F1A&condition=new&date=7d"
        )

        assert response.status_code == 503
        assert "Patek Philippe" in response.text
        assert "5990/1A" in response.text
        assert 'name="brand" value="Patek Philippe"' in response.text
        assert 'name="reference" value="5990/1A"' in response.text
        assert "condition=new" in response.text or 'name="condition" value="new"' in response.text

    @patch("app.get_active_offers_for_brand_reference")
    def test_cloudflare_html_not_in_page_response(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.side_effect = DatabaseUnavailableError(
            operation="find_watch_ids_for_brand_reference.watch_scan",
            status_code=521,
        )

        client = TestClient(app)
        response = client.get("/watch-reference?brand=Rolex&reference=126200")

        assert CLOUDFLARE_521_HTML not in response.text
        assert "Cloudflare Ray ID" not in response.text


class TestWatchReferenceDatabaseCalls:
    @patch("database.execute_postgrest_read")
    def test_find_watch_ids_uses_read_wrapper(self, mock_execute: MagicMock) -> None:
        mock_response = MagicMock(data=[])
        mock_execute.return_value = mock_response

        find_watch_ids_for_brand_reference("Rolex", "126200")

        mock_execute.assert_called()
        assert mock_execute.call_args.args[0] == "find_watch_ids_for_brand_reference.watch_scan"

    @patch("database.find_watch_ids_for_brand_reference", return_value=["550e8400-e29b-41d4-a716-446655440000"])
    @patch("database.execute_postgrest_read")
    def test_get_active_offers_uses_chunk_read_wrapper(
        self,
        mock_execute: MagicMock,
        _mock_watch_ids: MagicMock,
    ) -> None:
        mock_execute.return_value = MagicMock(
            data=[
                {
                    **_detail_offer(watch_id="550e8400-e29b-41d4-a716-446655440000"),
                    "dealers": {
                        "display_name": "Dealer dealer-1",
                        "phone_number": "+85290000001",
                        "whatsapp_id": "85290000001",
                        "contact_type": "dealer",
                    },
                }
            ]
        )

        offers = get_active_offers_for_brand_reference("Rolex", "126200")

        assert len(offers) == 1
        assert mock_execute.call_args.args[0] == "offers.chunk_lookup"

    def test_cloudflare_html_not_logged_verbatim(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_fn = MagicMock(side_effect=_cloudflare_521_exc())

        with caplog.at_level(logging.WARNING, logger="database_availability"):
            with pytest.raises(DatabaseUnavailableError):
                execute_postgrest_read(
                    "find_watch_ids_for_brand_reference.watch_scan",
                    mock_fn,
                )

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert CLOUDFLARE_521_HTML not in logged
        assert "database_unavailable" in logged
        assert "upstream_html_error_page" in logged


class TestWriteOperationsNeverRetried:
    @patch("database.get_offer_by_id")
    @patch("database.find_or_create_watch")
    @patch("database.get_client")
    def test_update_offer_from_training_does_not_retry_transient_failure(
        self,
        mock_get_client: MagicMock,
        mock_find_or_create_watch: MagicMock,
        mock_get_offer_by_id: MagicMock,
    ) -> None:
        from database import update_offer_from_training

        mock_get_offer_by_id.return_value = {
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "message_id": "550e8400-e29b-41d4-a716-446655440002",
            "line_index": 0,
            "watch_id": "550e8400-e29b-41d4-a716-446655440000",
        }
        mock_find_or_create_watch.return_value = (
            {"id": "550e8400-e29b-41d4-a716-446655440000"},
            False,
        )
        execute_mock = MagicMock(side_effect=_cloudflare_521_exc())
        mock_get_client.return_value.table.return_value.update.return_value.eq.return_value.execute = (
            execute_mock
        )

        with pytest.raises(Exception) as raised:
            update_offer_from_training(
                "550e8400-e29b-41d4-a716-446655440001",
                watch={
                    "brand": "Rolex",
                    "reference": "126200",
                    "condition": "New",
                    "original_price": 10000,
                    "original_currency": "USD",
                },
            )

        assert execute_mock.call_count == 1
        assert "JSON could not be generated" in str(raised.value)
        assert not isinstance(raised.value, DatabaseUnavailableError)


class TestImportLogSummaryBatching:
    @patch("database.execute_postgrest_read")
    def test_summary_batch_uses_central_read_wrapper(self, mock_execute: MagicMock) -> None:
        mock_execute.return_value = MagicMock(data=[{"id": "log-1", "summary": {}}])

        rows = _execute_import_log_summary_batch(["log-1"])

        assert rows == [{"id": "log-1", "summary": {}}]
        mock_execute.assert_called_once()
        assert mock_execute.call_args.args[0] == "import_logs.summary_batch"


class TestWebhookIngestionUnaffected:
    @pytest.mark.no_auto_login
    @patch("evolution_webhook.collect_message")
    def test_evolution_webhook_still_returns_200(
        self,
        mock_collect: MagicMock,
    ) -> None:
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

        with patch("app.start_whatsapp_listener"), patch("app.stop_whatsapp_listener"):
            client = TestClient(app)

        response = client.post(
            "/webhook/evolution",
            json={
                "event": "messages.upsert",
                "instance": "mrv4ult",
                "data": {
                    "key": {
                        "remoteJid": "120363000000000000@g.us",
                        "fromMe": False,
                        "participant": "31612345678@s.whatsapp.net",
                    },
                    "message": {"conversation": "ROLEX 126200 green jub 74000usd"},
                    "messageTimestamp": 1719496800,
                    "pushName": "Dealer A",
                },
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "imported"
