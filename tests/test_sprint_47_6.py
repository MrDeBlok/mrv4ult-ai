"""Tests for Sprint 47.6 strict numeric reference search."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from search import search_offers
from tests.search_mock_helpers import mock_search_offers_client


def _watch(
    *,
    brand: str,
    reference: str,
    model: str = "",
    dial: str = "",
    bracelet: str = "",
) -> dict:
    return {
        "brand": brand,
        "reference": reference,
        "model": model,
        "dial": dial,
        "bracelet": bracelet,
    }


def _offer(*, watch_id: str, watch: dict) -> dict:
    return {
        "watch_id": watch_id,
        "original_price": 50000,
        "original_currency": "USD",
        "usd_price": 50000,
        "condition": "New",
        "watches": watch,
        "dealers": {"display_name": "Dealer A", "contact_type": "dealer", "whatsapp_id": "+85290000001"},
    }


def _mock_offers_response(offers: list[dict]) -> MagicMock:
    return mock_search_offers_client(offers)


STRICT_5711_FIXTURE = [
    _offer(watch_id="w-5711", watch=_watch(brand="Patek Philippe", reference="5711/1A", model="Nautilus")),
    _offer(watch_id="w-5711r", watch=_watch(brand="Patek Philippe", reference="5711/1R", model="Nautilus")),
    _offer(watch_id="w-5711g", watch=_watch(brand="Patek Philippe", reference="5711G", model="Nautilus")),
    _offer(watch_id="w-5712", watch=_watch(brand="Patek Philippe", reference="5712/1A", model="Nautilus")),
    _offer(watch_id="w-5980a", watch=_watch(brand="Patek Philippe", reference="5980/1A", model="Nautilus")),
    _offer(watch_id="w-5980ar", watch=_watch(brand="Patek Philippe", reference="5980/1AR", model="Nautilus")),
]

ROLEX_REFERENCE_FIXTURE = [
    _offer(watch_id="w-126500", watch=_watch(brand="Rolex", reference="126500LN", model="Daytona", dial="White")),
    _offer(watch_id="w-126508", watch=_watch(brand="Rolex", reference="126508", model="Daytona", dial="Green")),
    _offer(watch_id="w-126610", watch=_watch(brand="Rolex", reference="126610LV", model="Submariner", dial="Green")),
]

BROAD_SEARCH_FIXTURE = [
    _offer(watch_id="w-rolex-1", watch=_watch(brand="Rolex", reference="126334", model="Datejust", dial="Blue")),
    _offer(watch_id="w-rolex-2", watch=_watch(brand="Rolex", reference="126300", model="Datejust", dial="Black")),
    _offer(watch_id="w-nautilus", watch=_watch(brand="Patek Philippe", reference="5711/1A", model="Nautilus", dial="Blue")),
    _offer(watch_id="w-aquanaut", watch=_watch(brand="Patek Philippe", reference="5167A", model="Aquanaut", dial="Black")),
]


class TestStrictReferenceSearch:
    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_5711_returns_only_references_containing_5711(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(STRICT_5711_FIXTURE)

        offers, _ = search_offers("5711")
        references = {offer["watch"]["reference"] for offer in offers}

        assert references == {"5711/1A", "5711/1R", "5711G"}

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_5711_excludes_5712(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(STRICT_5711_FIXTURE)

        offers, _ = search_offers("5711")
        references = {offer["watch"]["reference"] for offer in offers}

        assert "5712/1A" not in references

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_5711_excludes_5980(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(STRICT_5711_FIXTURE)

        offers, _ = search_offers("5711")
        references = {offer["watch"]["reference"] for offer in offers}

        assert "5980/1A" not in references
        assert "5980/1AR" not in references

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_126500_excludes_126508(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(ROLEX_REFERENCE_FIXTURE)

        offers, _ = search_offers("126500")
        references = {offer["watch"]["reference"] for offer in offers}

        assert references == {"126500LN"}
        assert "126508" not in references

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_1266_matches_substring_in_reference(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(ROLEX_REFERENCE_FIXTURE)

        offers, _ = search_offers("1266")
        references = {offer["watch"]["reference"] for offer in offers}

        assert references == {"126610LV"}

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_rolex_still_returns_brand_results(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(BROAD_SEARCH_FIXTURE)

        offers, _ = search_offers("Rolex")
        brands = {offer["watch"]["brand"] for offer in offers}

        assert brands == {"Rolex"}
        assert len(offers) == 2

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_nautilus_still_returns_model_results(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(BROAD_SEARCH_FIXTURE)

        offers, _ = search_offers("Nautilus")
        watch_ids = {offer["watch_id"] for offer in offers}

        assert watch_ids == {"w-nautilus"}

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_blue_still_returns_dial_results(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(BROAD_SEARCH_FIXTURE)

        offers, _ = search_offers("Blue")
        watch_ids = {offer["watch_id"] for offer in offers}

        assert watch_ids == {"w-rolex-1", "w-nautilus"}
