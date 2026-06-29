"""Regression tests for UTC storage and Europe/Amsterdam display formatting."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from activity_feed import build_ignored_activity_row
from app import (
    build_activity_detail,
    build_activity_row,
    build_notification_rows,
    format_timestamp,
)
from dealer_intelligence import format_activity_timestamp
from evolution_webhook import extract_received_at
from market_requests import build_market_request_detail, build_market_request_row
from parser_review import build_parser_review_row
from timezone_utils import (
    ensure_utc_datetime,
    format_display_timestamp,
    parse_utc_timestamp,
    to_utc_isoformat,
)

STORED_UTC = "2026-06-27T09:22:00+00:00"
EXPECTED_AMSTERDAM = "2026-06-27 11:22"


class TestTimezoneUtils:
    def test_parse_utc_timestamp_from_z_suffix(self) -> None:
        parsed = parse_utc_timestamp("2026-06-27T09:22:00Z")

        assert parsed == datetime(2026, 6, 27, 9, 22, tzinfo=timezone.utc)

    def test_parse_utc_timestamp_treats_naive_as_utc(self) -> None:
        parsed = parse_utc_timestamp("2026-06-27T09:22:00")

        assert parsed == datetime(2026, 6, 27, 9, 22, tzinfo=timezone.utc)

    def test_ensure_utc_datetime_normalizes_naive_values(self) -> None:
        naive = datetime(2026, 6, 27, 9, 22)

        assert ensure_utc_datetime(naive) == datetime(2026, 6, 27, 9, 22, tzinfo=timezone.utc)

    def test_to_utc_isoformat_serializes_for_storage(self) -> None:
        assert to_utc_isoformat(STORED_UTC) == STORED_UTC

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            (STORED_UTC, EXPECTED_AMSTERDAM),
            ("2026-06-27T09:22:00Z", EXPECTED_AMSTERDAM),
            ("2026-01-15T09:22:00+00:00", "2026-01-15 10:22"),
        ],
    )
    def test_format_display_timestamp_converts_to_amsterdam(
        self,
        stored: str,
        expected: str,
    ) -> None:
        assert format_display_timestamp(stored) == expected

    def test_format_display_timestamp_handles_missing(self) -> None:
        assert format_display_timestamp(None) == "N/A"
        assert format_display_timestamp("") == "N/A"

    def test_old_utc_only_formatting_would_show_wrong_time(self) -> None:
        timestamp = parse_utc_timestamp(STORED_UTC)

        assert timestamp is not None
        assert timestamp.strftime("%Y-%m-%d %H:%M") == "2026-06-27 09:22"
        assert format_display_timestamp(STORED_UTC) == EXPECTED_AMSTERDAM


def _sample_import_log() -> dict[str, object]:
    return {
        "id": "log-1",
        "import_time": STORED_UTC,
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+85291234567",
        "watches_parsed": 1,
        "new_offers": 1,
        "duplicate_offers": 0,
        "matched_requests": 0,
        "processing_time": "12 ms",
        "status": "success",
        "summary": {"rows": [], "parsed_watches": []},
    }


class TestSharedDisplayFormatters:
    def test_app_and_activity_formatters_match(self) -> None:
        assert format_timestamp(STORED_UTC) == EXPECTED_AMSTERDAM
        assert format_activity_timestamp(STORED_UTC) == EXPECTED_AMSTERDAM

    def test_activity_pages_use_localized_import_time(self) -> None:
        import_log = _sample_import_log()

        row = build_activity_row(import_log)
        detail = build_activity_detail(import_log, {"raw_text": "Offer message"})

        assert row["import_time"] == EXPECTED_AMSTERDAM
        assert detail["import_time"] == EXPECTED_AMSTERDAM

    def test_ignored_activity_row_uses_localized_import_time(self) -> None:
        row = build_ignored_activity_row(
            {
                "id": "log-ignored",
                "import_time": STORED_UTC,
                "group_name": "HK Dealers",
                "dealer_alias": "Dealer A",
                "dealer_whatsapp": "+85291234567",
                "status": "noise",
                "summary": {},
            },
            {"raw_text": "Pick up Patek today"},
        )

        assert row["import_time"] == EXPECTED_AMSTERDAM

    def test_parser_review_uses_localized_import_time(self) -> None:
        row = build_parser_review_row(
            _sample_import_log(),
            {"raw_text": "Offer message"},
            format_timestamp=format_timestamp,
        )

        assert row["import_time"] == EXPECTED_AMSTERDAM

    def test_market_requests_use_localized_import_time(self) -> None:
        import_log = _sample_import_log()
        import_log["status"] = "request_intent"
        import_log["summary"] = {
            "parsed_watches": [
                {
                    "brand": "Rolex",
                    "reference": "126500LN",
                    "max_price": 145000,
                    "currency": "USD",
                }
            ]
        }

        row = build_market_request_row(import_log, {"raw_text": "WTB Rolex 126500LN"})
        detail = build_market_request_detail(
            import_log,
            {"raw_text": "WTB Rolex 126500LN"},
            related_sources=[],
            matching_offers=[],
        )

        assert row["import_time"] == EXPECTED_AMSTERDAM
        assert detail["import_time"] == EXPECTED_AMSTERDAM

    def test_notifications_use_localized_created_at(self) -> None:
        rows = build_notification_rows(
            [
                {
                    "id": "n-1",
                    "type": "needs_review",
                    "title": "Needs review",
                    "message": "Missing price",
                    "created_at": STORED_UTC,
                    "is_read": False,
                }
            ]
        )

        assert rows[0]["created_at"] == EXPECTED_AMSTERDAM


class TestWebhookTimestampStorage:
    def test_evolution_message_timestamp_is_stored_as_utc(self) -> None:
        received_at = extract_received_at(
            {"messageTimestamp": 1_700_000_000},
            {},
        )

        assert received_at.tzinfo == timezone.utc
        assert received_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
        assert format_display_timestamp(received_at.isoformat()) == "2023-11-14 23:13"
