"""Tests for Sprint 48.5.2 offer source linking and debug tracing."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, normalize_watch_detail_offer
from dealer_intelligence import resolve_offer_source_url
from ingest import ingest_message
from offer_source_debug import trace_offer_source_resolution
from tests.conftest import ADMIN_USER, TRADER_ONE


OFFER_ID = "11111111-1111-1111-1111-111111111111"
MESSAGE_NEW = "22222222-2222-2222-2222-222222222222"
MESSAGE_OLD = "33333333-3333-3333-3333-333333333333"
IMPORT_LOG_NEW = "44444444-4444-4444-4444-444444444444"
WATCH_ID = "55555555-5555-5555-5555-555555555555"
DEALER_ID = "66666666-6666-6666-6666-666666666666"


def _import_log(*, import_log_id: str = IMPORT_LOG_NEW, message_id: str = MESSAGE_NEW) -> dict[str, Any]:
    return {
        "id": import_log_id,
        "message_id": message_id,
        "watches_parsed": 1,
        "status": "success",
        "summary": {
            "rows": [{"offer_id": OFFER_ID, "reference": "5236P-001", "brand": "Patek Philippe"}],
        },
    }


def _duplicate_offer(*, message_id: str = MESSAGE_OLD) -> dict[str, Any]:
    return {
        "id": OFFER_ID,
        "status": "active",
        "watch_id": WATCH_ID,
        "dealer_id": DEALER_ID,
        "message_id": message_id,
        "original_price": 120000,
        "original_currency": "USD",
        "usd_price": 120000,
        "condition": "New",
    }


class TestIngestOfferSourceLinking:
    def test_ingest_links_duplicate_offer_to_latest_import_log(self) -> None:
        linked: list[dict[str, Any]] = []

        def capture_link(import_log_id: str, message_id: str, summary: dict[str, Any]) -> None:
            linked.append(
                {
                    "import_log_id": import_log_id,
                    "message_id": message_id,
                    "offer_ids": [row.get("offer_id") for row in summary.get("rows") or []],
                }
            )

        with (
            patch("ingest.record_unknown_nicknames_for_watches", return_value=[]),
            patch("ingest.record_unknown_brands_for_watches", return_value=[]),
            patch("ingest.record_import_notifications"),
            patch("ingest.process_offer_request_matches", return_value=[]),
            patch("ingest._get_active_offers", return_value=[]),
            patch("ingest.insert_import_log", return_value={"id": IMPORT_LOG_NEW}),
            patch("ingest.insert_message", return_value={"id": MESSAGE_NEW}),
            patch("ingest.find_or_create_group", return_value="group-1"),
            patch("ingest.contact_type_column_supported", return_value=True),
            patch(
                "ingest.find_or_create_watch",
                return_value=(
                    {
                        "id": WATCH_ID,
                        "brand": "Patek Philippe",
                        "reference": "5236P-001",
                    },
                    False,
                ),
            ),
            patch("ingest.find_duplicate_offer", return_value=_duplicate_offer()),
            patch("ingest.insert_offer", return_value=(_duplicate_offer(), False)) as mock_insert_offer,
            patch(
                "ingest.find_or_create_dealer",
                return_value=(DEALER_ID, "dealer"),
            ),
            patch("database.link_import_log_to_summary_offers", side_effect=capture_link),
        ):
            summary = ingest_message(
                "Patek Philippe 5236P-001 New 120k",
                group_name="HK Dealers",
                dealer_whatsapp="+85291234567",
            )

        mock_insert_offer.assert_called_once()
        assert summary["duplicate_offers"] == 1
        assert linked == [
            {
                "import_log_id": IMPORT_LOG_NEW,
                "message_id": MESSAGE_NEW,
                "offer_ids": [OFFER_ID],
            }
        ]


class TestOfferSourceResolution:
    def test_resolve_from_source_import_log_id(self) -> None:
        offer = normalize_watch_detail_offer(
            {
                "id": OFFER_ID,
                "message_id": MESSAGE_OLD,
                "source_import_log_id": IMPORT_LOG_NEW,
                "dealers": {},
                "messages": None,
                "watches": {},
            }
        )
        import_log = _import_log()

        source_url, resolution_path, failure_reason = resolve_offer_source_url(
            offer,
            user=ADMIN_USER,
            import_logs_by_message_id={},
            import_logs_by_id={IMPORT_LOG_NEW: import_log},
            import_logs_by_offer_id={},
        )

        assert source_url == f"/activity/{IMPORT_LOG_NEW}"
        assert resolution_path == "direct_import_log_id"
        assert failure_reason is None

    def test_resolve_from_message_id(self) -> None:
        offer = normalize_watch_detail_offer(
            {
                "id": OFFER_ID,
                "message_id": MESSAGE_NEW,
                "dealers": {},
                "messages": None,
                "watches": {},
            }
        )
        import_log = _import_log()

        source_url, resolution_path, failure_reason = resolve_offer_source_url(
            offer,
            user=ADMIN_USER,
            import_logs_by_message_id={MESSAGE_NEW.lower(): import_log},
            import_logs_by_id={IMPORT_LOG_NEW: import_log},
            import_logs_by_offer_id={},
        )

        assert source_url == f"/activity/{IMPORT_LOG_NEW}"
        assert resolution_path == "message_id"
        assert failure_reason is None

    def test_resolve_from_summary_offer_id_for_stale_message_id(self) -> None:
        offer = normalize_watch_detail_offer(
            {
                "id": OFFER_ID,
                "message_id": MESSAGE_OLD,
                "dealers": {},
                "messages": None,
                "watches": {},
            }
        )
        import_log = _import_log()

        source_url, resolution_path, failure_reason = resolve_offer_source_url(
            offer,
            user=ADMIN_USER,
            import_logs_by_message_id={},
            import_logs_by_id={IMPORT_LOG_NEW: import_log},
            import_logs_by_offer_id={OFFER_ID.lower(): import_log},
        )

        assert source_url == f"/activity/{IMPORT_LOG_NEW}"
        assert resolution_path == "summary_or_request_match"
        assert failure_reason is None

    def test_missing_source_reports_clear_failure(self) -> None:
        offer = normalize_watch_detail_offer(
            {
                "id": OFFER_ID,
                "message_id": None,
                "dealers": {},
                "messages": None,
                "watches": {},
            }
        )

        source_url, resolution_path, failure_reason = resolve_offer_source_url(
            offer,
            user=ADMIN_USER,
            import_logs_by_message_id={},
            import_logs_by_id={},
            import_logs_by_offer_id={},
        )

        assert source_url is None
        assert resolution_path is None
        assert failure_reason == "offer_message_id_missing"


class TestWatchDetailSourceAfterLinking:
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    @patch("app.get_watch_by_id")
    def test_watch_detail_shows_view_original_for_duplicate_relisted_offer(
        self,
        mock_get_watch: MagicMock,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
    ) -> None:
        mock_get_watch.return_value = {
            "id": WATCH_ID,
            "brand": "Patek Philippe",
            "reference": "5236P-001",
            "model": "Grand Complications",
            "dial": "Blue",
            "bracelet": "Bracelet",
        }
        mock_get_offers.return_value = [
            {
                "id": OFFER_ID,
                "message_id": MESSAGE_NEW,
                "source_import_log_id": IMPORT_LOG_NEW,
                "dealer_id": DEALER_ID,
                "watch_id": WATCH_ID,
                "usd_price": 120000,
                "condition": "New",
                "original_price": 120000,
                "original_currency": "USD",
                "watches": {"dial": "Blue"},
                "dealers": {"display_name": "Max", "phone_number": "+85290000001"},
                "messages": {
                    "id": MESSAGE_NEW,
                    "received_at": "2026-06-01T12:00:00+00:00",
                    "group_id": "g-1",
                    "groups": {"name": "HK Dealers"},
                },
            }
        ]
        import_log = _import_log()
        mock_load_lookups.return_value = (
            {MESSAGE_NEW.lower(): import_log},
            {IMPORT_LOG_NEW: import_log},
            {OFFER_ID.lower(): import_log},
        )

        client = TestClient(app)
        response = client.get(f"/watch/{WATCH_ID}")

        assert response.status_code == 200
        assert f'href="/activity/{IMPORT_LOG_NEW}"' in response.text
        assert "View original" in response.text


class TestOfferSourceDebugTrace:
    @patch("offer_source_debug.attach_dealer_offer_source_urls")
    @patch("offer_source_debug.load_offer_source_import_log_lookups")
    @patch("offer_source_debug.resolve_offer_source_url")
    @patch("offer_source_debug.get_request_matches_for_offer_ids", return_value=[])
    @patch("offer_source_debug.find_import_logs_by_summary_offer_ids")
    @patch("offer_source_debug.get_import_logs_by_message_ids")
    @patch("offer_source_debug.find_import_log_by_message_id")
    @patch("offer_source_debug.get_offer_by_id")
    @patch("offer_source_debug.source_import_log_id_column_supported", return_value=True)
    def test_trace_offer_source_resolution_reports_duplicate_stale_message(
        self,
        _mock_column_supported: MagicMock,
        mock_get_offer: MagicMock,
        mock_find_latest: MagicMock,
        mock_by_message_ids: MagicMock,
        mock_by_summary: MagicMock,
        _mock_request_matches: MagicMock,
        mock_resolve: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach: MagicMock,
    ) -> None:
        mock_get_offer.return_value = {
            "id": OFFER_ID,
            "message_id": MESSAGE_OLD,
            "source_import_log_id": IMPORT_LOG_NEW,
            "dealer_id": DEALER_ID,
            "watch_id": WATCH_ID,
            "status": "active",
            "is_duplicate": True,
        }
        import_log = _import_log()
        mock_by_message_ids.return_value = {}
        mock_by_summary.return_value = {OFFER_ID.lower(): import_log}
        mock_find_latest.return_value = None
        mock_load_lookups.return_value = ({}, {IMPORT_LOG_NEW: import_log}, {OFFER_ID.lower(): import_log})
        mock_resolve.return_value = (f"/activity/{IMPORT_LOG_NEW}", "direct_import_log_id", None)
        mock_attach.return_value = [{"source_url": f"/activity/{IMPORT_LOG_NEW}"}]

        trace = trace_offer_source_resolution(OFFER_ID, user=ADMIN_USER)

        assert trace["found"] is True
        assert trace["offer"]["message_id"] == MESSAGE_OLD
        assert trace["offer"]["source_import_log_id"] == IMPORT_LOG_NEW
        assert trace["matching_import_logs_by_message_id"] == []
        assert trace["matching_import_logs_by_summary_offer_id"][0]["id"] == IMPORT_LOG_NEW
        assert trace["source_url"] == f"/activity/{IMPORT_LOG_NEW}"
        assert trace["failure_reason"] is None

    @patch("offer_source_debug.source_import_log_id_column_supported", return_value=True)
    @patch("offer_source_debug.get_offer_by_id", return_value=None)
    def test_trace_missing_offer(self, _mock_get_offer: MagicMock, _mock_column: MagicMock) -> None:
        trace = trace_offer_source_resolution(OFFER_ID, user=TRADER_ONE)

        assert trace["found"] is False
        assert trace["failure_reason"] == "offer_not_found"
