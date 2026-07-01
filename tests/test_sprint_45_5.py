"""Regression tests for Sprint 45.5 trader-focused dealers page."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import app
from contact_classification import filter_dealer_list_rows_by_search
from dealer_intelligence import (
    build_trader_dealer_list_row,
    build_trader_dealer_list_rows,
    classify_dealer_activity_label,
    clean_whatsapp_number_for_link,
    format_dealer_country_display,
    format_relative_time_dutch,
    resolve_dealer_contact_number,
)


def _dealer() -> dict:
    return {
        "id": "dealer-1",
        "display_name": "HK Dealer",
        "phone_number": "+852 9123-4567",
        "whatsapp_id": "85291234567",
        "country": "Hong Kong",
    }


class TestTraderDealerListRow:
    def test_contact_number_prefers_phone_then_whatsapp(self) -> None:
        assert resolve_dealer_contact_number(_dealer()) == "+852 9123-4567"
        assert (
            resolve_dealer_contact_number(
                {"phone_number": "", "whatsapp_id": "85299998888"},
            )
            == "85299998888"
        )
        assert resolve_dealer_contact_number({}) == "No number"

    def test_message_url_uses_cleaned_digits(self) -> None:
        row = build_trader_dealer_list_row(
            _dealer(),
            {
                "last_message_at": "2026-06-27T12:00:00+00:00",
                "groups": ["HK Dealers"],
                "dealer_whatsapp": "+85291234567",
            },
            {"dealer-1": {"active_offers": 1}},
        )

        assert row["message_url"] == "https://wa.me/85291234567"
        assert row["contact_number"] == "+85291234567"

    def test_trusted_dealer_shows_checkmark_label(self) -> None:
        row = build_trader_dealer_list_row(
            _dealer(),
            None,
            {"dealer-1": {"active_offers": 12}},
        )
        assert row["quality_label"] == "Trusted dealer"
        assert row["quality_show_check"] is True

    def test_clean_whatsapp_number_for_link(self) -> None:
        assert clean_whatsapp_number_for_link("+852 9123-4567") == "85291234567"

    def test_activity_label_active_today(self) -> None:
        now = datetime(2026, 6, 27, 15, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        label, badge = classify_dealer_activity_label(
            "2026-06-27T10:00:00+00:00",
            now=now,
        )
        assert label == "Active today"
        assert badge == "success"

    def test_activity_label_no_activity(self) -> None:
        label, badge = classify_dealer_activity_label(None)
        assert label == "No activity"
        assert badge == "secondary"

    def test_country_display_includes_flag(self) -> None:
        assert format_dealer_country_display("Italy") == "🇮🇹 Italy"
        assert format_dealer_country_display("") == "—"

    def test_relative_time_dutch_hours(self) -> None:
        now = datetime(2026, 6, 27, 14, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        assert (
            format_relative_time_dutch("2026-06-27T10:00:00+00:00", now=now)
            == "2 uur geleden"
        )

    def test_build_trader_dealer_list_rows_includes_groups(self) -> None:
        rows = build_trader_dealer_list_rows(
            [_dealer()],
            [
                {
                    "dealer_whatsapp": "85291234567",
                    "group_name": "HK Dealers",
                    "import_time": "2026-06-27T12:00:00+00:00",
                },
                {
                    "dealer_whatsapp": "85291234567",
                    "group_name": "Asia Watches",
                    "import_time": "2026-06-20T08:00:00+00:00",
                },
            ],
            {"dealer-1": {"active_offers": 4}},
        )

        assert rows[0]["groups"] == "HK Dealers, Asia Watches"
        assert rows[0]["last_group"] == "HK Dealers"
        assert rows[0]["quality_label"] == "Established dealer"
        assert rows[0]["country_display"] == "🇭🇰 Hong Kong"


class TestDealersPageTraderLayout:
    @patch("app.filter_dealer_list_rows_by_search", side_effect=lambda rows, _q: rows)
    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs", return_value=[])
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_dealers_page_shows_card_layout(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
        _mock_filter_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [_dealer()]

        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert "dealer-card" in response.text
        assert "Laatste bericht:" in response.text
        assert "Laatste groep:" in response.text
        assert "WhatsApp:" in response.text
        assert "Message dealer" in response.text
        assert "Total offers" not in response.text
        assert "Average asking price" not in response.text

    @patch("app.filter_dealer_list_rows_by_search", side_effect=lambda rows, _q: rows)
    @patch("app.list_dealer_offer_counts", return_value={"dealer-1": {"active_offers": 1}})
    @patch("app.list_dealer_import_activity_logs")
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_dealers_page_shows_phone_and_message_button(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
        _mock_filter_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [_dealer()]
        mock_import_logs.return_value = [
            {
                "dealer_whatsapp": "85291234567",
                "group_name": "HK Dealers",
                "import_time": "2026-06-27T12:00:00+00:00",
                "watches_parsed": 1,
            }
        ]

        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert "9123" in response.text
        assert 'href="https://wa.me/85291234567"' in response.text
        assert "Message dealer" in response.text
        assert "Laatste bericht:" in response.text

    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs")
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_dealers_page_search_by_group(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [
            _dealer(),
            {
                "id": "dealer-2",
                "display_name": "Geneva Dealer",
                "phone_number": "+41791234567",
                "whatsapp_id": "41791234567",
            },
        ]
        mock_import_logs.return_value = [
            {
                "dealer_whatsapp": "41791234567",
                "group_name": "Asia Watches",
                "import_time": "2026-06-27T12:00:00+00:00",
                "watches_parsed": 1,
            }
        ]

        client = TestClient(app)
        response = client.get("/dealers?q=Asia+Watches")

        assert response.status_code == 200
        assert "Geneva Dealer" in response.text
        assert "HK Dealer" not in response.text


class TestDealerListSearch:
    def test_filter_dealer_list_rows_by_phone(self) -> None:
        rows = [
            {
                "name": "HK Dealer",
                "display_name": "HK Dealer",
                "phone_number": "+85291234567",
                "whatsapp_id": "85291234567",
                "groups": "HK Dealers",
            },
            {
                "name": "Geneva Dealer",
                "display_name": "Geneva Dealer",
                "phone_number": "+41791234567",
                "whatsapp_id": "41791234567",
                "groups": "EU Dealers",
            },
        ]

        filtered = filter_dealer_list_rows_by_search(rows, "85291234567")
        assert len(filtered) == 1
        assert filtered[0]["name"] == "HK Dealer"

    def test_filter_dealer_list_rows_by_group(self) -> None:
        rows = [
            {"name": "HK Dealer", "groups": "HK Dealers"},
            {"name": "Geneva Dealer", "groups": "EU Dealers"},
        ]

        filtered = filter_dealer_list_rows_by_search(rows, "eu dealers")
        assert len(filtered) == 1
        assert filtered[0]["name"] == "Geneva Dealer"
