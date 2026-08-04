"""Regression tests for Sprint 52.1 search currency filter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_search_page_url
from condition_normalizer import PRE_OWNED_CONDITION
from search import (
    SEARCH_ACTIVE_OFFER_GROUPS_RPC,
    parse_search_currency_filter,
    search_offer_groups_page,
)
from tests.search_mock_helpers import mock_search_offer_groups_client_from_offers


def _watch(brand: str = "Rolex", reference: str = "126200") -> dict:
    return {
        "brand": brand,
        "reference": reference,
        "dial": "Blue",
        "bracelet": "Jubilee",
    }


def _offer(
    *,
    watch_id: str,
    brand: str = "Rolex",
    reference: str = "126200",
    usd_price: int = 74000,
    original_currency: str = "USD",
    condition: str = "New",
    dealer_id: str = "dealer-1",
) -> dict:
    return {
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "usd_price": usd_price,
        "original_currency": original_currency,
        "condition": condition,
        "watch": _watch(brand=brand, reference=reference),
        "watches": _watch(brand=brand, reference=reference),
        "dealers": {"display_name": "Dealer A", "contact_type": "dealer"},
    }


class TestParseSearchCurrencyFilter:
    def test_accepts_canonical_codes(self) -> None:
        assert parse_search_currency_filter("EUR") == "EUR"
        assert parse_search_currency_filter("hkd") == "HKD"

    def test_invalid_values_fall_back_to_no_filter(self) -> None:
        assert parse_search_currency_filter("USDT") is None
        assert parse_search_currency_filter("bogus") is None
        assert parse_search_currency_filter("") is None
        assert parse_search_currency_filter(None) is None


class TestSearchPageUrls:
    def test_preserves_currency_filter(self) -> None:
        url = build_search_page_url(
            page=2,
            search_text="Rolex",
            condition_filter="pre-owned",
            max_price="80000",
            currency_filter="EUR",
        )
        assert "currency=EUR" in url
        assert "condition=pre-owned" in url
        assert "max_price=80000" in url
        assert "page=2" in url


class TestSearchOfferGroupsCurrencyRpc:
    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_eur_filter_forwards_to_rpc(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        offers = [
            _offer(watch_id="w-eur", brand="Patek Philippe", reference="5711/1A", usd_price=120000, original_currency="EUR"),
            _offer(watch_id="w-usd", brand="Patek Philippe", reference="5711/1A", usd_price=90000, original_currency="USD", dealer_id="dealer-2"),
        ]
        mock_get_client.return_value = mock_search_offer_groups_client_from_offers(offers)

        result = search_offer_groups_page("5711", currency="EUR")

        payload = mock_get_client.return_value.rpc.call_args.args[1]
        assert payload["p_currency_filter"] == "EUR"
        assert len(result.groups) == 1
        assert result.groups[0]["lowest_usd"] == 120000
        assert result.groups[0]["offer_count"] == 1

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_usd_filter_returns_only_usd_offers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        offers = [
            _offer(watch_id="w-eur", original_currency="EUR", usd_price=100000),
            _offer(watch_id="w-usd", original_currency="USD", usd_price=80000, dealer_id="dealer-2"),
        ]
        mock_get_client.return_value = mock_search_offer_groups_client_from_offers(offers)

        result = search_offer_groups_page("126200", currency="USD")

        assert len(result.groups) == 1
        assert result.groups[0]["lowest_usd"] == 80000

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_hkd_filter_returns_only_hkd_offers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        offers = [
            _offer(watch_id="w-hkd", original_currency="HKD", usd_price=95000),
            _offer(watch_id="w-usd", original_currency="USD", usd_price=70000, dealer_id="dealer-2"),
        ]
        mock_get_client.return_value = mock_search_offer_groups_client_from_offers(offers)

        result = search_offer_groups_page("126200", currency="HKD")

        assert len(result.groups) == 1
        assert result.groups[0]["lowest_usd"] == 95000

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_group_aggregates_use_only_filtered_currency_offers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        offers = [
            _offer(watch_id="w-eur-1", original_currency="EUR", usd_price=130000, dealer_id="dealer-1"),
            _offer(watch_id="w-eur-2", original_currency="EUR", usd_price=125000, dealer_id="dealer-2"),
            _offer(watch_id="w-usd-1", original_currency="USD", usd_price=90000, dealer_id="dealer-3"),
        ]
        mock_get_client.return_value = mock_search_offer_groups_client_from_offers(offers)

        result = search_offer_groups_page("126200", currency="EUR")

        group = result.groups[0]
        assert group["lowest_usd"] == 125000
        assert group["offer_count"] == 2
        assert group["unique_dealers"] == 2

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_production_route_uses_one_bounded_rpc(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offer_groups_client_from_offers(
            [_offer(watch_id="w-1", original_currency="EUR")]
        )

        client = TestClient(app)
        response = client.get("/?q=Rolex&currency=EUR")

        assert response.status_code == 200
        assert mock_get_client.return_value.rpc.call_count == 1
        payload = mock_get_client.return_value.rpc.call_args.args
        assert payload[0] == SEARCH_ACTIVE_OFFER_GROUPS_RPC
        assert payload[1]["p_currency_filter"] == "EUR"


class TestProductionSearchRouteCurrency:
    @patch("app.get_import_logs_by_message_ids")
    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_invalid_currency_falls_back_to_all(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        mock_import_logs: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offer_groups_client_from_offers(
            [
                _offer(watch_id="w-eur", original_currency="EUR"),
                _offer(watch_id="w-usd", original_currency="USD", dealer_id="dealer-2"),
            ]
        )

        client = TestClient(app)
        response = client.get("/?q=Rolex&currency=USDT")

        assert response.status_code == 200
        payload = mock_get_client.return_value.rpc.call_args.args[1]
        assert payload["p_currency_filter"] is None
        mock_import_logs.assert_not_called()

    @patch("app.search_offer_groups_page")
    def test_pagination_preserves_currency(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        from search import SEARCH_GROUP_PAGE_SIZE, SearchGroupsPageResult

        mock_search_offer_groups_page.return_value = SearchGroupsPageResult(
            groups=[
                {
                    "watch_id": "w-1",
                    "watch": {"brand": "Rolex", "reference": "126500"},
                    "offers": [],
                    "lowest_usd": 10000,
                    "offer_count": 1,
                    "unique_dealers": 1,
                    "conditions_available": ["New"],
                }
            ],
            page=1,
            page_size=SEARCH_GROUP_PAGE_SIZE,
            has_more=True,
        )

        client = TestClient(app)
        response = client.get("/?q=Rolex&currency=EUR&page=1")

        assert response.status_code == 200
        assert "currency=EUR" in response.text
        assert "page=2" in response.text
        mock_search_offer_groups_page.assert_called_once()
        assert mock_search_offer_groups_page.call_args.kwargs["currency"] == "EUR"

    @patch("app.search_offer_groups_page")
    def test_condition_and_currency_combination(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        from search import SEARCH_GROUP_PAGE_SIZE, SearchGroupsPageResult

        mock_search_offer_groups_page.return_value = SearchGroupsPageResult(
            groups=[],
            page=1,
            page_size=SEARCH_GROUP_PAGE_SIZE,
            has_more=False,
        )

        client = TestClient(app)
        response = client.get("/?q=126200&condition=pre-owned&currency=EUR")

        assert response.status_code == 200
        mock_search_offer_groups_page.assert_called_once()
        kwargs = mock_search_offer_groups_page.call_args.kwargs
        assert kwargs["condition"] == PRE_OWNED_CONDITION
        assert kwargs["currency"] == "EUR"

    @patch("app.search_offer_groups_page")
    def test_max_price_and_currency_combination(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        from search import SEARCH_GROUP_PAGE_SIZE, SearchGroupsPageResult

        mock_search_offer_groups_page.return_value = SearchGroupsPageResult(
            groups=[],
            page=1,
            page_size=SEARCH_GROUP_PAGE_SIZE,
            has_more=False,
        )

        client = TestClient(app)
        response = client.get("/?q=Rolex&max_price=80000&currency=HKD")

        assert response.status_code == 200
        mock_search_offer_groups_page.assert_called_once()
        assert mock_search_offer_groups_page.call_args.kwargs["currency"] == "HKD"

    @patch("search.group_offers_by_brand_reference")
    @patch("app.search_offer_groups_page")
    def test_no_python_side_currency_filtering_on_route(
        self,
        mock_search_offer_groups_page: MagicMock,
        mock_group_offers: MagicMock,
    ) -> None:
        from search import SEARCH_GROUP_PAGE_SIZE, SearchGroupsPageResult

        mock_search_offer_groups_page.return_value = SearchGroupsPageResult(
            groups=[],
            page=1,
            page_size=SEARCH_GROUP_PAGE_SIZE,
            has_more=False,
        )

        client = TestClient(app)
        response = client.get("/?q=Rolex&currency=EUR")

        assert response.status_code == 200
        mock_group_offers.assert_not_called()


class TestSprint521MigrationSql:
    def test_migration_adds_currency_filter_parameter(self) -> None:
        from pathlib import Path

        sql = Path("docs/migrations/sprint_52_1_search_currency_filter.sql").read_text(encoding="utf-8")
        assert "p_currency_filter text DEFAULT NULL" in sql
        assert "upper(btrim(o.original_currency)) = upper(btrim(p_currency_filter))" in sql
        assert "CREATE OR REPLACE FUNCTION search_active_offer_groups" in sql
