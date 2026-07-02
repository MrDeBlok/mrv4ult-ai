"""Tests for Sprint 47.5 Watch Search reference filtering regression."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from search import search_offers


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


REFERENCE_FIXTURE = [
    _offer(
        watch_id="w-5711-1a",
        watch=_watch(brand="Patek Philippe", reference="5711/1A", model="Nautilus"),
    ),
    _offer(
        watch_id="w-5711r",
        watch=_watch(brand="Patek Philippe", reference="5711R", model="Nautilus"),
    ),
    _offer(
        watch_id="w-5711g",
        watch=_watch(brand="Patek Philippe", reference="5711G", model="Nautilus"),
    ),
    _offer(
        watch_id="w-5960",
        watch=_watch(brand="Patek Philippe", reference="5960/1A", model="Annual Calendar"),
    ),
    _offer(
        watch_id="w-3520",
        watch=_watch(brand="Patek Philippe", reference="3520", model="Calatrava"),
    ),
    _offer(
        watch_id="w-7300",
        watch=_watch(brand="Patek Philippe", reference="7300/1200R", model="Twenty~4"),
    ),
    _offer(
        watch_id="w-4997",
        watch=_watch(brand="Patek Philippe", reference="4997/1A", model="Calatrava"),
    ),
    _offer(
        watch_id="w-5327",
        watch=_watch(brand="Patek Philippe", reference="5327G", model="Calatrava"),
    ),
]

ROLEX_FIXTURE = [
    _offer(
        watch_id="w-rolex-1",
        watch=_watch(brand="Rolex", reference="126500LN", model="Daytona", dial="White"),
    ),
    _offer(
        watch_id="w-rolex-2",
        watch=_watch(brand="Rolex", reference="126610LV", model="Submariner", dial="Green"),
    ),
    _offer(
        watch_id="w-omega-1",
        watch=_watch(brand="Omega", reference="310.30.42.50.01.001", model="Speedmaster", dial="Black"),
    ),
]

DIAL_MODEL_FIXTURE = [
    _offer(
        watch_id="w-blue",
        watch=_watch(brand="Rolex", reference="126334", model="Datejust", dial="Blue", bracelet="Jubilee"),
    ),
    _offer(
        watch_id="w-black",
        watch=_watch(brand="Rolex", reference="126300", model="Datejust", dial="Black", bracelet="Oyster"),
    ),
    _offer(
        watch_id="w-nautilus",
        watch=_watch(brand="Patek Philippe", reference="5711/1A", model="Nautilus", dial="Blue"),
    ),
    _offer(
        watch_id="w-aquanaut",
        watch=_watch(brand="Patek Philippe", reference="5167A", model="Aquanaut", dial="Black"),
    ),
]


class TestReferenceSearchRegression:
    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_5711_returns_only_matching_references(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(REFERENCE_FIXTURE)

        offers, _ = search_offers("5711")
        references = {offer["watch"]["reference"] for offer in offers}

        assert references == {"5711/1A", "5711R", "5711G"}
        assert "5960/1A" not in references
        assert "3520" not in references
        assert "7300/1200R" not in references
        assert "4997/1A" not in references
        assert "5327G" not in references

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_126500_returns_only_matching_references(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(ROLEX_FIXTURE)

        offers, _ = search_offers("126500")
        references = {offer["watch"]["reference"] for offer in offers}

        assert references == {"126500LN"}
        assert "126610LV" not in references

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_1266_matches_partial_reference_prefix(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(ROLEX_FIXTURE)

        offers, _ = search_offers("1266")
        references = {offer["watch"]["reference"] for offer in offers}

        assert references == {"126610LV"}

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_rolex_returns_all_rolex_offers(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(ROLEX_FIXTURE)

        offers, _ = search_offers("Rolex")
        brands = {offer["watch"]["brand"] for offer in offers}

        assert brands == {"Rolex"}
        assert len(offers) == 2

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_blue_matches_dial(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(DIAL_MODEL_FIXTURE)

        offers, _ = search_offers("Blue")
        watch_ids = {offer["watch_id"] for offer in offers}

        assert watch_ids == {"w-blue", "w-nautilus"}
        assert "w-black" not in watch_ids
        assert "w-aquanaut" not in watch_ids

    @patch("search.is_business_dealer_relation", return_value=True)
    @patch("search.contact_type_column_supported", return_value=False)
    @patch("search.get_client")
    def test_search_nautilus_matches_model(
        self,
        mock_get_client: MagicMock,
        _mock_contact_type: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_offers_response(DIAL_MODEL_FIXTURE)

        offers, _ = search_offers("Nautilus")
        watch_ids = {offer["watch_id"] for offer in offers}

        assert watch_ids == {"w-nautilus"}
        assert "w-aquanaut" not in watch_ids
