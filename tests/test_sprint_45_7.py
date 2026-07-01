"""Regression tests for Sprint 45.7 dealers list pagination."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from dealer_intelligence import (
    DEALERS_PAGE_SIZE,
    dealers_page_url,
    paginate_dealer_list_rows,
)


def _dealer_record(index: int) -> dict:
    return {
        "id": f"dealer-{index:02d}",
        "display_name": f"Dealer {index:02d}",
        "phone_number": f"+8529000{index:04d}",
        "whatsapp_id": f"8529000{index:04d}",
        "country": "Hong Kong",
    }


def _count_dealer_cards(html: str) -> int:
    return len(re.findall(r'data-href="/dealers/dealer-', html))


def _dealer_rows(count: int) -> list[dict]:
    return [
        {
            "id": f"dealer-{index:02d}",
            "name": f"Dealer {index:02d}",
            "display_name": f"Dealer {index:02d}",
            "phone_number": f"+8529000{index:04d}",
            "whatsapp_id": f"8529000{index:04d}",
            "country_display": "🇭🇰 Hong Kong",
            "contact_number": f"+8529000{index:04d}",
            "whatsapp_display": f"+8529000{index:04d}",
            "groups": "HK Dealers",
            "last_group": "HK Dealers",
            "last_message_relative": "—",
            "quality_label": "Unknown dealer",
            "quality_class": "secondary",
            "quality_show_check": False,
            "message_url": f"https://wa.me/8529000{index:04d}",
        }
        for index in range(1, count + 1)
    ]


class TestDealerPaginationHelpers:
    def test_paginate_dealer_list_rows_limits_to_page_size(self) -> None:
        rows = [{"id": f"dealer-{index}", "name": f"Dealer {index}"} for index in range(1, 26)]
        page_one = paginate_dealer_list_rows(rows, 1)
        page_two = paginate_dealer_list_rows(rows, 2)

        assert len(page_one.dealers) == DEALERS_PAGE_SIZE
        assert page_one.page == 1
        assert page_one.has_previous is False
        assert page_one.has_next is True
        assert page_one.showing_from == 1
        assert page_one.showing_to == 20

        assert len(page_two.dealers) == 5
        assert page_two.page == 2
        assert page_two.has_previous is True
        assert page_two.has_next is False
        assert page_two.showing_from == 21
        assert page_two.showing_to == 25

    def test_invalid_page_falls_back_to_first_page(self) -> None:
        rows = [{"id": "dealer-1", "name": "Dealer 1"}]
        assert paginate_dealer_list_rows(rows, 0).page == 1
        assert paginate_dealer_list_rows(rows, -3).page == 1

    def test_dealers_page_url_preserves_search_query(self) -> None:
        assert dealers_page_url(1, "85290000001") == "/dealers?q=85290000001"
        assert dealers_page_url(2, "85290000001") == "/dealers?q=85290000001&page=2"
        assert dealers_page_url(3) == "/dealers?page=3"


class TestDealersPagePagination:
    @patch("app.paginate_dealer_list_rows", wraps=paginate_dealer_list_rows)
    @patch("app.build_trader_dealer_list_rows")
    @patch("app.filter_dealer_list_rows_by_search", side_effect=lambda rows, _q: rows)
    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs", return_value=[])
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_dealers_page_renders_max_twenty_rows(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
        _mock_filter_rows: MagicMock,
        mock_build_rows: MagicMock,
        mock_paginate: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [_dealer_record(index) for index in range(1, 26)]
        mock_build_rows.return_value = _dealer_rows(25)

        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert _count_dealer_cards(response.text) == 20
        assert "Dealer 01" in response.text
        assert "Dealer 20" in response.text
        assert "Dealer 21" not in response.text
        mock_paginate.assert_called_once()
        assert mock_paginate.call_args.args[1] == 1

    @patch("app.build_trader_dealer_list_rows")
    @patch("app.filter_dealer_list_rows_by_search", side_effect=lambda rows, _q: rows)
    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs", return_value=[])
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_page_two_shows_next_dealers(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
        _mock_filter_rows: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [_dealer_record(index) for index in range(1, 26)]
        mock_build_rows.return_value = _dealer_rows(25)

        client = TestClient(app)
        response = client.get("/dealers?page=2")

        assert response.status_code == 200
        assert _count_dealer_cards(response.text) == 5
        assert "Dealer 21" in response.text
        assert "Dealer 25" in response.text
        assert "Dealer 01" not in response.text

    @patch("app.build_trader_dealer_list_rows")
    @patch("app.filter_dealer_list_rows_by_search", side_effect=lambda rows, _q: rows)
    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs", return_value=[])
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_invalid_page_query_falls_back_to_page_one(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
        _mock_filter_rows: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [_dealer_record(index) for index in range(1, 26)]
        mock_build_rows.return_value = _dealer_rows(25)

        client = TestClient(app)
        response = client.get("/dealers?page=0")

        assert response.status_code == 200
        assert "· Page 1" in response.text
        assert "Dealer 01" in response.text
        assert "Dealer 21" not in response.text

    @patch("app.build_trader_dealer_list_rows")
    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs", return_value=[])
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_search_and_pagination_work_together(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [_dealer_record(index) for index in range(1, 26)]
        mock_build_rows.return_value = _dealer_rows(25)

        client = TestClient(app)
        page_one = client.get("/dealers?q=Dealer")
        page_two = client.get("/dealers?q=Dealer&page=2")

        assert page_one.status_code == 200
        assert _count_dealer_cards(page_one.text) == 20
        assert page_two.status_code == 200
        assert _count_dealer_cards(page_two.text) == 5
        assert "Dealer 21" in page_two.text

    @patch("app.build_trader_dealer_list_rows")
    @patch("app.filter_dealer_list_rows_by_search", side_effect=lambda rows, _q: rows)
    @patch("app.list_dealer_offer_counts", return_value={})
    @patch("app.list_dealer_import_activity_logs", return_value=[])
    @patch("app.filter_imports_for_user", side_effect=lambda logs, _user: logs)
    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_dealers")
    def test_pagination_links_preserve_search_query(
        self,
        mock_list_dealers: MagicMock,
        _mock_business_logs: MagicMock,
        _mock_filter_imports: MagicMock,
        _mock_import_logs: MagicMock,
        _mock_offer_counts: MagicMock,
        _mock_filter_rows: MagicMock,
        mock_build_rows: MagicMock,
    ) -> None:
        mock_list_dealers.return_value = [_dealer_record(index) for index in range(1, 26)]
        mock_build_rows.return_value = _dealer_rows(25)

        client = TestClient(app)
        response = client.get("/dealers?q=Dealer&page=2")

        assert response.status_code == 200
        assert 'href="/dealers?q=Dealer"' in response.text
        assert "Total offers" not in response.text
        assert "Average asking price" not in response.text
