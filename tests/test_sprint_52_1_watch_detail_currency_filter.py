"""Regression tests for Sprint 52.1 watch-reference detail currency filter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_watch_reference_url
from condition_normalizer import PRE_OWNED_CONDITION
from database import WATCH_REFERENCE_OFFERS_PAGE_RPC
from search import parse_search_currency_filter
from tests.conftest import ADMIN_USER
from tests.watch_detail_mock_helpers import (
    build_watch_reference_page_result,
    mock_watch_reference_offers_page_client,
)

WATCH_URL = "/watch-reference?brand=Patek+Philippe&reference=5711%2F1A"
WATCH_URL_EUR = WATCH_URL + "&currency=EUR"
OFFER_ID_EUR = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OFFER_ID_USD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OFFER_ID_HKD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OFFER_ID_EUR_LOW = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OFFER_ID_EUR_HIGH = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _offer(
    offer_id: str,
    *,
    original_currency: str = "USD",
    usd_price: int = 100_000,
    condition: str = "Pre-Owned",
    dealer_id: str = "dealer-1",
) -> dict:
    return {
        "id": offer_id,
        "dealer_id": dealer_id,
        "original_price": usd_price,
        "original_currency": original_currency,
        "usd_price": usd_price,
        "condition": condition,
        "watches": {"brand": "Patek Philippe", "reference": "5711/1A", "dial": "Blue"},
        "dealers": {"display_name": "Dealer A", "contact_type": "dealer"},
        "messages": {"received_at": "2026-01-15T10:00:00+00:00", "groups": {"name": "HK Group"}},
    }


class TestWatchDetailCurrencyValidation:
    def test_invalid_currency_falls_back_to_all(self) -> None:
        assert parse_search_currency_filter("USDT") is None
        assert parse_search_currency_filter("bogus") is None


class TestWatchDetailCurrencyRpc:
    @patch("database.contact_type_column_supported", return_value=True)
    @patch("database.get_client")
    def test_rpc_receives_p_currency_filter(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        from database import get_watch_reference_offers_page

        mock_get_client.return_value = mock_watch_reference_offers_page_client(
            [_offer(OFFER_ID_EUR, original_currency="EUR", usd_price=120_000)]
        )

        get_watch_reference_offers_page(
            "Patek Philippe",
            "5711/1A",
            currency_filter="EUR",
        )

        payload = mock_get_client.return_value.rpc.call_args.args[1]
        assert payload["p_currency_filter"] == "EUR"

    @patch("database.contact_type_column_supported", return_value=True)
    @patch("database.get_client")
    def test_eur_filter_returns_only_eur_offers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        from database import get_watch_reference_offers_page

        offers = [
            _offer(OFFER_ID_EUR, original_currency="EUR", usd_price=120_000),
            _offer(OFFER_ID_USD, original_currency="USD", usd_price=90_000, dealer_id="dealer-2"),
        ]
        mock_get_client.return_value = mock_watch_reference_offers_page_client(offers)

        result = get_watch_reference_offers_page(
            "Patek Philippe",
            "5711/1A",
            currency_filter="EUR",
        )

        assert result.total_count == 1
        assert result.offers[0]["original_currency"] == "EUR"

    @patch("database.contact_type_column_supported", return_value=True)
    @patch("database.get_client")
    def test_hkd_filter_returns_only_hkd_offers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        from database import get_watch_reference_offers_page

        offers = [
            _offer(OFFER_ID_HKD, original_currency="HKD", usd_price=95_000),
            _offer(OFFER_ID_EUR, original_currency="EUR", usd_price=120_000, dealer_id="dealer-2"),
        ]
        mock_get_client.return_value = mock_watch_reference_offers_page_client(offers)

        result = get_watch_reference_offers_page(
            "Patek Philippe",
            "5711/1A",
            currency_filter="HKD",
        )

        assert result.total_count == 1
        assert result.offers[0]["original_currency"] == "HKD"

    def test_statistics_use_only_selected_currency(self) -> None:
        offers = [
            _offer(OFFER_ID_EUR, original_currency="EUR", usd_price=130_000, dealer_id="d1"),
            _offer(OFFER_ID_USD, original_currency="EUR", usd_price=125_000, dealer_id="d2"),
            _offer(OFFER_ID_HKD, original_currency="USD", usd_price=90_000, dealer_id="d3"),
        ]
        result = build_watch_reference_page_result(offers, currency_filter="EUR")

        assert result.statistics["active_offer_count"] == 2
        assert result.statistics["unique_dealer_count"] == 2
        assert result.statistics["lowest_usd_price"] == 125_000
        assert result.statistics["highest_usd_price"] == 130_000


class TestWatchDetailCurrencyRoute:
    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_watch_reference_offers_page")
    def test_invalid_currency_falls_back_on_route(
        self,
        mock_get_page: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_get_page.return_value = build_watch_reference_page_result([])
        client = TestClient(app)

        response = client.get(WATCH_URL + "&currency=USDT")

        assert response.status_code == 200
        mock_get_page.assert_called_once()
        assert mock_get_page.call_args.kwargs["currency_filter"] is None

    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_watch_reference_offers_page")
    def test_eur_filter_on_watch_detail(
        self,
        mock_get_page: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_get_page.return_value = build_watch_reference_page_result(
            [_offer(OFFER_ID_EUR, original_currency="EUR", usd_price=120_000)],
            currency_filter="EUR",
        )
        client = TestClient(app)

        response = client.get(WATCH_URL_EUR)

        assert response.status_code == 200
        assert mock_get_page.call_args.kwargs["currency_filter"] == "EUR"
        assert "EUR" in response.text

    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_watch_reference_offers_page")
    def test_pagination_preserves_currency(
        self,
        mock_get_page: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        from database import WatchReferenceOffersPageResult
        from watch_detail_filters import WATCH_DETAIL_PAGE_SIZE

        page_result = build_watch_reference_page_result(
            [_offer(OFFER_ID_EUR, original_currency="EUR")],
            currency_filter="EUR",
            page=1,
        )
        mock_get_page.return_value = WatchReferenceOffersPageResult(
            offers=page_result.offers,
            total_count=60,
            has_more=True,
            page=1,
            total_pages=2,
            page_limit=WATCH_DETAIL_PAGE_SIZE,
            page_offset=0,
            statistics=page_result.statistics,
        )
        client = TestClient(app)

        response = client.get(WATCH_URL_EUR)

        assert response.status_code == 200
        assert "currency=EUR" in response.text
        assert "page=2" in response.text

    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_watch_reference_offers_page")
    def test_condition_and_currency_combination(
        self,
        mock_get_page: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_get_page.return_value = build_watch_reference_page_result([])
        client = TestClient(app)

        response = client.get(WATCH_URL + "&condition=pre-owned&currency=EUR")

        assert response.status_code == 200
        kwargs = mock_get_page.call_args.kwargs
        assert kwargs["condition_filter"] == PRE_OWNED_CONDITION
        assert kwargs["currency_filter"] == "EUR"

    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_watch_reference_offers_page")
    def test_date_and_currency_combination(
        self,
        mock_get_page: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_get_page.return_value = build_watch_reference_page_result([])
        client = TestClient(app)

        response = client.get(WATCH_URL + "&date=7d&currency=HKD")

        assert response.status_code == 200
        assert mock_get_page.call_args.kwargs["currency_filter"] == "HKD"

    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.filter_watch_detail_offers_for_stats")
    @patch("app.get_watch_reference_offers_page")
    def test_no_python_side_currency_filtering_on_route(
        self,
        mock_get_page: MagicMock,
        mock_filter_stats: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_get_page.return_value = build_watch_reference_page_result([])
        client = TestClient(app)

        response = client.get(WATCH_URL_EUR)

        assert response.status_code == 200
        mock_filter_stats.assert_not_called()

    @patch("app.load_offer_source_import_log_lookups", return_value=({}, {}, {}))
    @patch("app.get_watch_reference_offers_page")
    def test_sorting_within_selected_currency(
        self,
        mock_get_page: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_get_page.return_value = build_watch_reference_page_result(
            [
                _offer(OFFER_ID_EUR_HIGH, original_currency="EUR", usd_price=140_000),
                _offer(OFFER_ID_EUR_LOW, original_currency="EUR", usd_price=120_000, dealer_id="d2"),
            ],
            currency_filter="EUR",
            sort_filter="price_asc",
        )
        client = TestClient(app)

        response = client.get(WATCH_URL_EUR + "&sort=price_asc")

        assert response.status_code == 200
        assert mock_get_page.call_args.kwargs["sort_filter"] == "price_asc"
        assert mock_get_page.call_args.kwargs["currency_filter"] == "EUR"


class TestWatchDetailCurrencyRedirects:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.count_watch_detail_filtered_offers", return_value=10)
    @patch("app.soft_remove_offer")
    def test_remove_redirect_preserves_currency(
        self,
        _mock_remove: MagicMock,
        _mock_count: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        return_to = build_watch_reference_url(
            "Patek Philippe",
            "5711/1A",
            condition="new",
            date="7d",
            sort="price_asc",
            currency="EUR",
            page=2,
        )
        response = client.post(
            "/offers/offer-1/remove",
            data={
                "confirm": "1",
                "removal_reason": "duplicate",
                "return_to": return_to or WATCH_URL,
            },
            follow_redirects=False,
        )

        location = response.headers["location"]
        assert "currency=EUR" in location
        assert "condition=new" in location
        assert "date=7d" in location
        assert "sort=price_asc" in location

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.count_watch_detail_filtered_offers", return_value=10)
    @patch("app.bulk_soft_remove_offers")
    def test_bulk_remove_redirect_preserves_currency(
        self,
        mock_bulk_remove: MagicMock,
        _mock_count: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        from offer_removal import BulkRemoveResult

        mock_bulk_remove.return_value = BulkRemoveResult(
            success_count=1,
            skipped_count=0,
            removed_ids=(OFFER_ID_EUR,),
            skipped_ids=(),
        )
        client = TestClient(app)
        return_to = build_watch_reference_url(
            "Patek Philippe",
            "5711/1A",
            condition="new",
            date="7d",
            sort="price_asc",
            currency="EUR",
        )
        response = client.post(
            "/offers/bulk-remove",
            data={
                "confirm": "1",
                "removal_reason": "duplicate",
                "return_to": return_to or WATCH_URL,
                "offer_ids": [OFFER_ID_EUR],
            },
            follow_redirects=False,
        )

        location = response.headers["location"]
        assert "currency=EUR" in location
        assert "condition=new" in location


class TestWatchDetailCurrencyMigrationSql:
    def test_migration_adds_currency_filter_to_watch_detail_rpc(self) -> None:
        from pathlib import Path

        sql = Path("docs/migrations/sprint_52_1_watch_detail_currency_filter.sql").read_text(
            encoding="utf-8"
        )
        assert "get_watch_reference_offers_page" in sql
        assert "p_currency_filter text DEFAULT NULL" in sql
        assert "upper(btrim(o.original_currency)) = upper(btrim(p_currency_filter))" in sql
        assert "search_active_offer_groups" not in sql

    def test_build_watch_reference_url_preserves_currency(self) -> None:
        url = build_watch_reference_url(
            "Patek Philippe",
            "5711/1A",
            condition="new",
            date="7d",
            sort="price_asc",
            currency="EUR",
            page=2,
        )
        assert url is not None
        assert "currency=EUR" in url
        assert "page=2" in url
