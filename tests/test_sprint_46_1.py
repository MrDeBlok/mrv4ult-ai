"""Tests for Sprint 46.1 search result source links."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_result_rows
from condition_normalizer import PRE_OWNED_CONDITION
from dealer_intelligence import attach_dealer_offer_source_urls
from tests.conftest import TRADER_ONE, TRADER_TWO


def _watch_group(
    *,
    message_id: str | None = "msg-1",
    dealer_id: str = "dealer-1",
    source_url: str | None = None,
) -> dict:
    offer = {
        "watch_id": "watch-1",
        "dealer_id": dealer_id,
        "message_id": message_id,
        "usd_price": 74000,
        "condition": "New",
        "card_date": "06/2026",
        "dealer": {"display_name": "Dealer A", "phone_number": "+85291234567"},
        "source_url": source_url,
    }
    return {
        "watch_id": "watch-1",
        "watch": {
            "brand": "Rolex",
            "reference": "126200",
            "dial": "Blue",
            "bracelet": "Jubilee",
        },
        "lowest_usd": 74000,
        "offer_count": 1,
        "unique_dealers": 1,
        "conditions_available": ["New"],
        "offers": [offer],
    }


def _import_log(
    import_log_id: str = "log-1",
    *,
    watches_parsed: int = 1,
    status: str = "success",
    imported_by_user_id: str | None = None,
) -> dict:
    row = {
        "id": import_log_id,
        "message_id": "msg-1",
        "watches_parsed": watches_parsed,
        "status": status,
    }
    if imported_by_user_id is not None:
        row["imported_by_user_id"] = imported_by_user_id
    return row


class TestSearchResultSourceLinks:
    def test_build_result_rows_links_to_watch_detail(self) -> None:
        rows = build_result_rows([_watch_group(source_url="/activity/log-1")])

        assert rows[0]["brand"] == "Rolex"
        assert rows[0]["reference"] == "126200"
        assert rows[0]["watch_url"] == "/watch/watch-1"
        assert "source_url" not in rows[0]
        assert "dealer_url" not in rows[0]

    @patch("app.get_import_logs_by_message_ids")
    @patch("app.search_offers")
    def test_search_reference_links_to_activity_detail(
        self,
        mock_search_offers: MagicMock,
        mock_get_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (
            [
                {
                    "watch_id": "watch-1",
                    "dealer_id": "dealer-1",
                    "message_id": "msg-1",
                    "usd_price": 74000,
                    "condition": "New",
                    "card_date": "06/2026",
                    "watch": {
                        "brand": "Rolex",
                        "reference": "126200",
                        "dial": "Blue",
                        "bracelet": "Jubilee",
                    },
                    "dealer": {
                        "display_name": "Dealer A",
                        "phone_number": "+85291234567",
                    },
                }
            ],
            False,
        )
        mock_get_import_logs.return_value = {"msg-1": _import_log("log-offer-1")}

        client = TestClient(app)
        response = client.get("/?q=126200")

        assert response.status_code == 200
        assert 'data-href="/watch/watch-1"' in response.text
        assert "126200" in response.text
        assert 'href="/activity/log-offer-1"' not in response.text
        assert "View original" not in response.text

    @patch("app.get_import_logs_by_message_ids", return_value={})
    @patch("app.search_offers")
    def test_search_without_source_renders_plain_reference(
        self,
        mock_search_offers: MagicMock,
        _mock_get_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (
            [
                {
                    "watch_id": "watch-1",
                    "dealer_id": "dealer-1",
                    "message_id": None,
                    "usd_price": 74000,
                    "condition": "New",
                    "watch": {
                        "brand": "Rolex",
                        "reference": "126200",
                        "dial": "Blue",
                        "bracelet": "Jubilee",
                    },
                    "dealer": {"display_name": "Dealer A"},
                }
            ],
            False,
        )

        client = TestClient(app)
        response = client.get("/?q=126200")

        assert response.status_code == 200
        assert 'href="/activity/' not in response.text
        assert "126200" in response.text
        assert "View original" not in response.text

    @patch("app.get_current_user", return_value=TRADER_ONE)
    @patch("app.get_import_logs_by_message_ids")
    @patch("app.search_offers")
    def test_visibility_hides_forbidden_source_links(
        self,
        mock_search_offers: MagicMock,
        mock_get_import_logs: MagicMock,
        _mock_current_user: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (
            [
                {
                    "watch_id": "watch-1",
                    "dealer_id": "dealer-1",
                    "message_id": "msg-private",
                    "usd_price": 74000,
                    "condition": "New",
                    "watch": {
                        "brand": "Rolex",
                        "reference": "126200",
                        "dial": "Blue",
                        "bracelet": "Jubilee",
                    },
                    "dealer": {"display_name": "Dealer A"},
                }
            ],
            False,
        )
        mock_get_import_logs.return_value = {
            "msg-private": _import_log(
                "log-private",
                status="noise",
                watches_parsed=0,
                imported_by_user_id=TRADER_TWO["id"],
            )
        }

        client = TestClient(app)
        response = client.get("/?q=126200")

        assert response.status_code == 200
        assert 'href="/activity/log-private"' not in response.text
        assert "View original" not in response.text

    @patch("app.get_import_logs_by_message_ids")
    @patch("app.search_offers")
    def test_discarded_import_does_not_create_source_link(
        self,
        mock_search_offers: MagicMock,
        mock_get_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (
            [
                {
                    "watch_id": "watch-1",
                    "dealer_id": "dealer-1",
                    "message_id": "msg-1",
                    "usd_price": 74000,
                    "condition": "New",
                    "watch": {
                        "brand": "Rolex",
                        "reference": "126200",
                        "dial": "Blue",
                        "bracelet": "Jubilee",
                    },
                    "dealer": {"display_name": "Dealer A"},
                }
            ],
            False,
        )
        mock_get_import_logs.return_value = {
            "msg-1": _import_log("log-discarded", watches_parsed=0, status="warning")
        }

        client = TestClient(app)
        response = client.get("/?q=126200")

        assert response.status_code == 200
        assert 'href="/activity/log-discarded"' not in response.text

    def test_attach_search_offer_source_urls_reuses_dealer_logic(self) -> None:
        offers = attach_dealer_offer_source_urls(
            [{"message_id": "msg-1"}],
            {"msg-1": _import_log("log-shared")},
            user=TRADER_ONE,
        )

        assert offers[0]["source_url"] == "/activity/log-shared"


class TestSearchFiltersUnchanged:
    @patch("app.get_import_logs_by_message_ids", return_value={})
    @patch("app.search_offers")
    def test_condition_filter_still_passed_to_search(
        self,
        mock_search_offers: MagicMock,
        _mock_get_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = ([], False)

        client = TestClient(app)
        response = client.get("/?q=126200&condition=pre-owned&cheapest=1&max_price=80000")

        assert response.status_code == 200
        mock_search_offers.assert_called_once()
        assert mock_search_offers.call_args.kwargs["condition"] == PRE_OWNED_CONDITION
        assert "cheapest" in mock_search_offers.call_args.args[0]
        assert "80000" in mock_search_offers.call_args.args[0]

    @patch("app.get_import_logs_by_message_ids", return_value={})
    @patch("app.search_offers")
    def test_search_page_renders_reference_index_not_dealer_rows(
        self,
        mock_search_offers: MagicMock,
        _mock_get_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (
            [
                {
                    "watch_id": "watch-1",
                    "dealer_id": "dealer-1",
                    "message_id": "msg-1",
                    "usd_price": 74000,
                    "condition": "Used",
                    "card_date": "06/2026",
                    "watch": {
                        "brand": "Rolex",
                        "reference": "126200",
                        "dial": "Blue",
                        "bracelet": "Jubilee",
                    },
                    "dealer": {
                        "display_name": "Dealer A",
                        "phone_number": "+85291234567",
                    },
                }
            ],
            False,
        )

        client = TestClient(app)
        response = client.get("/?q=126200&condition=pre-owned")

        assert response.status_code == 200
        assert "Rolex" in response.text
        assert "126200" in response.text
        assert "Active offers" in response.text
        assert "Dealer A" not in response.text
        assert "+85291234567" not in response.text
