"""Regression tests for Sprint 32.3 parser review price formatting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from parser_review import (
    _format_parsed_value,
    _parsed_field_entries,
    build_parser_review_row,
)


class TestParserReviewPriceFormatting:
    def test_format_int_price(self) -> None:
        assert _format_parsed_value("original_price", {"original_price": 10600}) == "10,600"

    def test_format_float_price(self) -> None:
        assert _format_parsed_value("original_price", {"original_price": 10600.0}) == "10,600"
        assert _format_parsed_value("original_price", {"original_price": 10600.5}) == "10,600.5"

    def test_format_numeric_string_price(self) -> None:
        assert _format_parsed_value("original_price", {"original_price": "10600"}) == "10,600"
        assert (
            _format_parsed_value(
                "original_price",
                {"original_price": "10,600", "original_currency": "EUR"},
            )
            == "10,600 EUR"
        )

    def test_format_malformed_string_price(self) -> None:
        assert _format_parsed_value("original_price", {"original_price": "POA"}) == "POA"
        assert (
            _format_parsed_value(
                "original_price",
                {"original_price": "Call", "original_currency": "USD"},
            )
            == "Call USD"
        )

    def test_format_none_price(self) -> None:
        assert _format_parsed_value("original_price", {"original_price": None}) is None
        assert _format_parsed_value("original_price", {"original_price": ""}) is None
        assert _format_parsed_value("usd_price", {"usd_price": None}) is None

    def test_format_usd_price_from_string(self) -> None:
        assert _format_parsed_value("usd_price", {"usd_price": "12500"}) == "$12,500"

    def test_parsed_field_entries_with_string_price_does_not_crash(self) -> None:
        entries = _parsed_field_entries(
            [
                {
                    "brand": "Rolex",
                    "reference": None,
                    "original_price": "10.600",
                    "original_currency": "EUR",
                }
            ]
        )

        assert any(entry.startswith("Price:") for entry in entries)


class TestParserReviewPageLoads:
    @patch("app.get_message_by_id")
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_import_logs")
    def test_parser_review_page_loads_with_string_price(
        self,
        mock_list_import_logs: MagicMock,
        _mock_business: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            {
                "id": "log-price-string",
                "status": "warning",
                "watches_parsed": 1,
                "message_id": "msg-1",
                "group_name": "EU Dealers",
                "dealer_alias": "Euro Dealer",
                "dealer_whatsapp": "+31612345678",
                "import_time": "2026-06-27T12:00:00+00:00",
                "summary": {
                    "status_reason": "Important fields are missing — watch 1: missing reference",
                    "parsed_watches": [
                        {
                            "brand": "Rolex",
                            "reference": None,
                            "original_price": "10.600",
                            "original_currency": "EUR",
                            "source_line": "10.600 Euro Rolex Explorer 124273",
                        }
                    ],
                },
            }
        ]
        mock_get_message.return_value = {"raw_text": "10.600 Euro Rolex Explorer 124273"}

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert "Parser review" in response.text
        assert "Price:" in response.text

    @patch("app.get_message_by_id")
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_import_logs")
    def test_build_parser_review_row_with_malformed_price(
        self,
        mock_list_import_logs: MagicMock,
        _mock_business: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        import_log = {
            "id": "log-poa",
            "status": "warning",
            "watches_parsed": 1,
            "message_id": "msg-2",
            "group_name": "HK Dealers",
            "dealer_alias": "Dealer A",
            "dealer_whatsapp": "+85291234567",
            "import_time": "2026-06-27T12:00:00+00:00",
            "summary": {
                "parsed_watches": [
                    {
                        "brand": "Rolex",
                        "reference": None,
                        "original_price": "POA",
                    }
                ],
            },
        }
        row = build_parser_review_row(
            import_log,
            {"raw_text": "Rolex Sub POA"},
            format_timestamp=lambda value: value or "N/A",
        )

        assert any("POA" in entry for entry in row["parsed_fields"])
