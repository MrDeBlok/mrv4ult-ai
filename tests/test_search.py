"""Tests for search offers and condition filtering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_result_rows
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION
from search import (
    SEARCH_ACTIVE_OFFERS_RPC,
    SEARCH_OFFERS_PAGE_SIZE,
    search_offers,
)
from tests.search_mock_helpers import mock_search_offers_client, mock_search_offers_full_scan_client


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
    condition: str | None,
    reference: str = "126200",
    card_date: str | None = None,
) -> dict:
    return {
        "watch_id": watch_id,
        "original_price": 74000,
        "original_currency": "USD",
        "usd_price": 74000,
        "card_date": card_date,
        "condition": condition,
        "watches": _watch(reference=reference),
        "dealers": {"display_name": "Dealer A", "contact_type": "dealer", "whatsapp_id": "+85290000001"},
    }


def _mock_offers_response(offers: list[dict]) -> MagicMock:
    return mock_search_offers_client(offers)


class TestSearchConditionFilter:
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_all_conditions(
        self,
        mock_get_client: MagicMock,
        mock_contact_type_supported: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(
            [
                _offer(watch_id="w-new", condition="New", card_date="06/2026"),
                _offer(watch_id="w-used", condition="Used"),
                _offer(watch_id="w-none", condition=None),
            ]
        )

        offers, _ = search_offers("126200")

        assert len(offers) == 3

    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_filter_new_only(
        self,
        mock_get_client: MagicMock,
        mock_contact_type_supported: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(
            [
                _offer(watch_id="w-new", condition="New"),
                _offer(watch_id="w-unworn", condition="Unworn"),
                _offer(watch_id="w-used", condition="Used"),
            ]
        )

        offers, _ = search_offers("126200", condition=NEW_CONDITION)

        assert len(offers) == 2
        assert all(offer["watch_id"] in {"w-new", "w-unworn"} for offer in offers)

    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_filter_pre_owned_only(
        self,
        mock_get_client: MagicMock,
        mock_contact_type_supported: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(
            [
                _offer(watch_id="w-new", condition="New"),
                _offer(watch_id="w-used", condition="Used"),
                _offer(watch_id="w-mint", condition="Mint"),
                _offer(watch_id="w-stored", condition=PRE_OWNED_CONDITION),
            ]
        )

        offers, _ = search_offers("126200", condition=PRE_OWNED_CONDITION)

        assert len(offers) == 3
        assert {offer["watch_id"] for offer in offers} == {"w-used", "w-mint", "w-stored"}


class TestSearchResultDisplay:
    def test_build_result_rows_displays_reference_index_fields(self) -> None:
        rows = build_result_rows(
            [
                {
                    "watch_id": "w-new",
                    "watch": _watch(),
                    "lowest_usd": 74000,
                    "offer_count": 2,
                    "unique_dealers": 1,
                    "conditions_available": [NEW_CONDITION, PRE_OWNED_CONDITION],
                    "offers": [
                        {
                            "usd_price": 74000,
                            "condition": "Unworn",
                            "card_date": "06/2026",
                            "dealer": {"display_name": "Dealer A"},
                        }
                    ],
                }
            ]
        )

        assert rows[0]["brand"] == "Rolex"
        assert rows[0]["reference"] == "126200"
        assert rows[0]["lowest_price"] == "$74,000"
        assert rows[0]["offer_count"] == 2
        assert rows[0]["unique_dealers"] == 1
        assert rows[0]["conditions_label"] == "New / Pre-Owned"
        assert rows[0]["watch_url"] == "/watch-reference?brand=Rolex&reference=126200"

    @patch("app.search_offer_groups_page")
    def test_search_page_renders_grouped_reference_index(
        self,
        mock_search_offer_groups_page: MagicMock,
    ) -> None:
        from tests.search_mock_helpers import search_groups_page_from_offers

        mock_search_offer_groups_page.return_value = search_groups_page_from_offers(
            [
                {
                    "watch_id": "w-new",
                    "dealer_id": "dealer-1",
                    "usd_price": 74000,
                    "condition": "Used",
                    "card_date": "06/2026",
                    "watch": _watch(),
                    "dealer": {"display_name": "Dealer A", "phone_number": "+85291234567"},
                }
            ]
        )

        client = TestClient(app)
        response = client.get("/?q=126200&condition=pre-owned")

        assert response.status_code == 200
        assert "Rolex" in response.text
        assert "126200" in response.text
        assert "Active offers" in response.text
        assert "Dealer A" not in response.text
        mock_search_offer_groups_page.assert_called_once()
        assert mock_search_offer_groups_page.call_args.kwargs["condition"] == PRE_OWNED_CONDITION


class TestSearchRpcArchitecture:
    def _rm35_fixture(self) -> list[dict]:
        return [
            _offer(
                watch_id="w-rm35",
                condition="Used",
                reference="RM35-01",
            )
            | {
                "id": "offer-rm35",
                "watches": _watch(brand="Richard Mille", reference="RM35-01"),
            },
            _offer(
                watch_id="w-other",
                condition="New",
                reference="5711/1A",
            )
            | {
                "id": "offer-other",
                "watches": _watch(brand="Patek Philippe", reference="5711/1A"),
            },
        ]

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search._load_all_active_offers_for_diagnostics")
    @patch("search.get_client")
    def test_search_offers_does_not_use_diagnostic_full_scan_loader(
        self,
        mock_get_client: MagicMock,
        mock_full_scan: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offers_client(self._rm35_fixture())

        offers, _ = search_offers("RM35")

        mock_full_scan.assert_not_called()
        mock_get_client.return_value.rpc.assert_called()
        assert mock_get_client.return_value.table.call_count == 0
        assert len(offers) == 1
        assert offers[0]["watch"]["reference"] == "RM35-01"

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_reference_search_rm35_returns_matching_offers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        mock_get_client.return_value = mock_search_offers_client(self._rm35_fixture())

        offers, _ = search_offers("RM35")

        assert len(offers) == 1
        assert offers[0]["watch"]["brand"] == "Richard Mille"
        assert offers[0]["watch"]["reference"] == "RM35-01"

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_max_price_filter_still_works(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        offers_fixture = [
            _offer(watch_id="w-low", condition="New", reference="RM35-01")
            | {
                "usd_price": 200000,
                "watches": _watch(brand="Richard Mille", reference="RM35-01"),
            },
            _offer(watch_id="w-high", condition="New", reference="RM35-02")
            | {
                "usd_price": 400000,
                "watches": _watch(brand="Richard Mille", reference="RM35-02"),
            },
        ]
        mock_get_client.return_value = mock_search_offers_client(offers_fixture)

        offers, _ = search_offers("RM35 under 250000")

        assert len(offers) == 1
        assert offers[0]["watch_id"] == "w-low"
        rpc_payload = mock_get_client.return_value.rpc.call_args.args[1]
        assert rpc_payload["max_usd_price"] == 250000

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_condition_filter_still_works_with_rpc_search(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        offers_fixture = [
            _offer(watch_id="w-new", condition="New", reference="RM35-01")
            | {"watches": _watch(brand="Richard Mille", reference="RM35-01")},
            _offer(watch_id="w-used", condition="Used", reference="RM35-01")
            | {"watches": _watch(brand="Richard Mille", reference="RM35-01")},
        ]
        mock_get_client.return_value = mock_search_offers_client(offers_fixture)

        offers, _ = search_offers("RM35", condition=NEW_CONDITION)

        assert len(offers) == 1
        assert offers[0]["watch_id"] == "w-new"
        rpc_payload = mock_get_client.return_value.rpc.call_args.args[1]
        assert rpc_payload["condition_filter"] == NEW_CONDITION

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_rpc_pagination_is_bounded_to_matching_results(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        offers_fixture = [
            _offer(watch_id=f"w-{index}", condition="New", reference=f"RM35-0{index}")
            | {
                "id": f"offer-{index}",
                "watches": _watch(brand="Richard Mille", reference=f"RM35-0{index}"),
            }
            for index in range(SEARCH_OFFERS_PAGE_SIZE + 5)
        ]
        mock_get_client.return_value = mock_search_offers_client(
            offers_fixture,
            total_count=len(offers_fixture),
        )

        offers, _ = search_offers("RM35")

        assert len(offers) == SEARCH_OFFERS_PAGE_SIZE + 5
        rpc_calls = mock_get_client.return_value.rpc.call_args_list
        assert len(rpc_calls) == 2
        assert rpc_calls[0].args[0] == SEARCH_ACTIVE_OFFERS_RPC
        assert rpc_calls[0].args[1]["page_offset"] == 0
        assert rpc_calls[1].args[1]["page_offset"] == SEARCH_OFFERS_PAGE_SIZE

    @patch("search.contact_type_column_supported", return_value=True)
    @patch("search.get_client")
    def test_diagnostic_full_scan_still_available_for_trace_tooling(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
    ) -> None:
        from search import _load_all_active_offers_for_diagnostics

        offers_fixture = [_offer(watch_id="w-1", condition="New")]
        mock_get_client.return_value = mock_search_offers_full_scan_client(offers_fixture)

        loaded, total_count = _load_all_active_offers_for_diagnostics()

        assert len(loaded) == 1
        assert total_count == 1
        mock_get_client.return_value.table.assert_called_with("offers")
        mock_get_client.return_value.rpc.assert_not_called()
