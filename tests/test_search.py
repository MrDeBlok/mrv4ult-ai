"""Tests for search offers and condition filtering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_result_rows
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION
from search import search_offers


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
    mock_client = MagicMock()
    mock_execute = MagicMock()
    mock_execute.data = offers
    mock_eq = MagicMock()
    mock_eq.execute.return_value = mock_execute
    mock_select = MagicMock()
    mock_select.eq.return_value = mock_eq
    mock_table = MagicMock()
    mock_table.select.return_value = mock_select
    mock_client.table.return_value = mock_table
    return mock_client


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
    def test_build_result_rows_displays_condition_and_card_date(self) -> None:
        rows = build_result_rows(
            [
                {
                    "watch_id": "w-new",
                    "watch": _watch(),
                    "lowest_usd": 74000,
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

        assert rows[0]["condition"] == NEW_CONDITION
        assert rows[0]["raw_condition"] == "Unworn"
        assert rows[0]["card_date"] == "06/2026"

    @patch("app.search_offers")
    def test_search_page_renders_condition_fields(self, mock_search_offers: MagicMock) -> None:
        mock_search_offers.return_value = (
            [
                {
                    "watch_id": "w-new",
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
        assert "Pre-Owned" in response.text
        assert "Used" in response.text
        assert "06/2026" in response.text
        mock_search_offers.assert_called_once()
        assert mock_search_offers.call_args.kwargs["condition"] == PRE_OWNED_CONDITION
