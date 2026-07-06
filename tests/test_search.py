"""Tests for search offers and condition filtering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_result_rows
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION
from search import search_offers
from tests.search_mock_helpers import mock_search_offers_client


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
        assert rows[0]["watch_url"] == "/watch/w-new"

    @patch("app.get_import_logs_by_message_ids", return_value={})
    @patch("app.search_offers")
    def test_search_page_renders_grouped_reference_index(
        self,
        mock_search_offers: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (
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
        mock_search_offers.assert_called_once()
        assert mock_search_offers.call_args.kwargs["condition"] == PRE_OWNED_CONDITION
