"""Regression tests for Sprint 51.1 bounded search pagination."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_search_page_url
from condition_normalizer import PRE_OWNED_CONDITION
from search import (
    SEARCH_ACTIVE_OFFER_GROUPS_RPC,
    SEARCH_GROUP_PAGE_SIZE,
    SearchGroupsPageResult,
    normalize_search_page,
    search_offer_groups_page,
)
from tests.conftest import ADMIN_USER
from tests.search_mock_helpers import (
    mock_search_offer_groups_client,
    mock_search_offers_client,
    search_groups_page_from_offers,
)


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
    condition: str = "New",
    dealer_id: str = "dealer-1",
) -> dict:
    return {
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "usd_price": usd_price,
        "condition": condition,
        "watch": _watch(brand=brand, reference=reference),
        "watches": _watch(brand=brand, reference=reference),
        "dealers": {"display_name": "Dealer A", "contact_type": "dealer"},
    }


def _rpc_group(
    *,
    brand: str,
    reference: str,
    watch_id: str,
    lowest_usd: int,
    offer_count: int = 1,
    unique_dealers: int = 1,
    conditions: list[str] | None = None,
) -> dict:
    return {
        "brand": brand,
        "reference": reference,
        "watch_id": watch_id,
        "lowest_usd": lowest_usd,
        "offer_count": offer_count,
        "unique_dealers": unique_dealers,
        "condition_categories": conditions or ["New"],
    }


class TestNormalizeSearchPage:
    def test_defaults_to_first_page(self) -> None:
        assert normalize_search_page(None) == 1
        assert normalize_search_page("") == 1

    def test_rejects_invalid_values(self) -> None:
        assert normalize_search_page("abc") == 1
        assert normalize_search_page("-3") == 1

    def test_normalizes_valid_page(self) -> None:
        assert normalize_search_page("2") == 2
        assert normalize_search_page(5) == 5


class TestSearchPageUrls:
    def test_preserves_active_filters(self) -> None:
        url = build_search_page_url(
            page=2,
            search_text="Rolex",
            cheapest_only=True,
            max_price="80000",
            condition_filter="pre-owned",
        )

        assert "q=Rolex" in url
        assert "page=2" in url
        assert "cheapest=1" in url
        assert "max_price=80000" in url
        assert "condition=pre-owned" in url

    def test_first_page_omits_page_param(self) -> None:
        url = build_search_page_url(page=1, search_text="RM35")
        assert "page=" not in url
        assert "q=RM35" in url


class TestSearchOfferGroupsPage:
    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_requests_single_rpc_page(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offer_groups_client(
            [_rpc_group(brand="Rolex", reference="126500LN", watch_id="w-1", lowest_usd=25000)],
            has_more=True,
        )

        result = search_offer_groups_page("Rolex", page=1)

        assert len(result.groups) == 1
        assert result.has_more is True
        assert result.page == 1
        rpc_calls = mock_get_client.return_value.rpc.call_args_list
        assert len(rpc_calls) == 1
        assert rpc_calls[0].args[0] == SEARCH_ACTIVE_OFFER_GROUPS_RPC
        assert rpc_calls[0].args[1]["page_limit"] == SEARCH_GROUP_PAGE_SIZE
        assert rpc_calls[0].args[1]["page_offset"] == 0

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_second_page_uses_offset(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offer_groups_client(
            [_rpc_group(brand="Rolex", reference="126600LN", watch_id="w-2", lowest_usd=30000)],
        )

        search_offer_groups_page("Rolex", page=2)

        payload = mock_get_client.return_value.rpc.call_args.args[1]
        assert payload["page_offset"] == SEARCH_GROUP_PAGE_SIZE


class TestProductionSearchRoute:
    @patch("app.get_import_logs_by_message_ids")
    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_broad_rolex_search_uses_one_groups_rpc(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        mock_import_logs: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offer_groups_client(
            [
                _rpc_group(
                    brand="Rolex",
                    reference=f"126{index}",
                    watch_id=f"w-{index}",
                    lowest_usd=20000 + index,
                )
                for index in range(3)
            ],
            has_more=True,
        )

        client = TestClient(app)
        response = client.get("/?q=Rolex")

        assert response.status_code == 200
        assert mock_get_client.return_value.rpc.call_count == 1
        mock_import_logs.assert_not_called()
        assert "Next" in response.text
        assert "Page 1" in response.text

    @patch("app.get_import_logs_by_message_ids")
    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_search_offers_path_not_used_for_full_pagination(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        mock_import_logs: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offer_groups_client(
            [
                _rpc_group(
                    brand="Rolex",
                    reference=f"126{index}",
                    watch_id=f"w-{index}",
                    lowest_usd=10000 + index,
                )
                for index in range(3)
            ],
            has_more=True,
        )

        with patch("search.search_offers") as mock_search_offers:
            client = TestClient(app)
            response = client.get("/?q=Rolex")

        assert response.status_code == 200
        mock_search_offers.assert_not_called()
        assert mock_get_client.return_value.rpc.call_count == 1
        mock_import_logs.assert_not_called()

    @patch("app.search_offer_groups_page")
    def test_pagination_links_preserve_filters(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
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
        response = client.get("/?q=Rolex&condition=pre-owned&max_price=80000&cheapest=1")

        assert response.status_code == 200
        assert "condition=pre-owned" in response.text
        assert "max_price=80000" in response.text
        assert "cheapest=1" in response.text
        assert "page=2" in response.text

    @patch("app.search_offer_groups_page")
    def test_empty_results_still_render(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        mock_search_offer_groups_page.return_value = SearchGroupsPageResult(
            groups=[],
            page=1,
            page_size=SEARCH_GROUP_PAGE_SIZE,
            has_more=False,
        )

        client = TestClient(app)
        response = client.get("/?q=DoesNotExist999")

        assert response.status_code == 200
        assert "No matching references found." in response.text

    @patch("app.search_offer_groups_page")
    def test_invalid_page_is_normalized(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        mock_search_offer_groups_page.return_value = SearchGroupsPageResult(
            groups=[],
            page=1,
            page_size=SEARCH_GROUP_PAGE_SIZE,
            has_more=False,
        )

        client = TestClient(app)
        response = client.get("/?q=Rolex&page=0")

        assert response.status_code == 200
        mock_search_offer_groups_page.assert_called_once()
        assert mock_search_offer_groups_page.call_args.kwargs["page"] == 1

    @patch("app.search_offer_groups_page")
    def test_narrow_search_renders_expected_group(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        mock_search_offer_groups_page.return_value = search_groups_page_from_offers(
            [_offer(watch_id="w-rm35", brand="Richard Mille", reference="RM35-02", usd_price=180000)]
        )

        client = TestClient(app)
        response = client.get("/?q=RM35")

        assert response.status_code == 200
        assert "Richard Mille" in response.text
        assert "RM35-02" in response.text

    @patch("app.search_offer_groups_page")
    def test_pages_are_non_overlapping(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        page_one = SearchGroupsPageResult(
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
        page_two = SearchGroupsPageResult(
            groups=[
                {
                    "watch_id": "w-2",
                    "watch": {"brand": "Rolex", "reference": "126600"},
                    "offers": [],
                    "lowest_usd": 20000,
                    "offer_count": 1,
                    "unique_dealers": 1,
                    "conditions_available": ["New"],
                }
            ],
            page=2,
            page_size=SEARCH_GROUP_PAGE_SIZE,
            has_more=False,
        )
        mock_search_offer_groups_page.side_effect = [page_one, page_two]

        client = TestClient(app)
        first = client.get("/?q=Rolex")
        second = client.get("/?q=Rolex&page=2")

        assert first.status_code == 200
        assert second.status_code == 200
        assert "126500" in first.text
        assert "126600" in second.text
        assert "126600" not in first.text
        assert "126500" not in second.text

    @patch("app.get_import_logs_by_message_ids")
    @patch("app.search_offer_groups_page")
    def test_import_log_enrichment_not_called_for_search_index(
        self,
        mock_search_offer_groups_page: MagicMock,
        mock_import_logs: MagicMock,
    ) -> None:
        mock_search_offer_groups_page.return_value = search_groups_page_from_offers(
            [_offer(watch_id="w-1", reference="126500")]
        )

        client = TestClient(app)
        response = client.get("/?q=126500")

        assert response.status_code == 200
        mock_import_logs.assert_not_called()

    @patch("app.search_offer_groups_page")
    def test_condition_filter_is_forwarded(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        mock_search_offer_groups_page.return_value = search_groups_page_from_offers(
            [_offer(watch_id="w-1", condition="Used")]
        )

        client = TestClient(app)
        response = client.get("/?q=126200&condition=pre-owned")

        assert response.status_code == 200
        mock_search_offer_groups_page.assert_called_once()
        assert mock_search_offer_groups_page.call_args.kwargs["condition"] == PRE_OWNED_CONDITION
