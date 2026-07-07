"""Tests for Sprint 48.5 grouped reference search and watch detail condition filters."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_offer_rows, build_result_rows, build_watch_reference_condition_urls, build_watch_reference_url, build_watch_stats
from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    UNKNOWN_CONDITION,
    offer_matches_watch_detail_condition,
)
from search import group_offers_by_brand_reference


def _watch(
    *,
    brand: str,
    reference: str,
    dial: str = "",
    bracelet: str = "",
) -> dict:
    return {
        "brand": brand,
        "reference": reference,
        "dial": dial,
        "bracelet": bracelet,
    }


def _search_offer(
    *,
    watch_id: str,
    dealer_id: str,
    watch: dict,
    usd_price: int,
    condition: str | None = "New",
) -> dict:
    return {
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "usd_price": usd_price,
        "condition": condition,
        "watch": watch,
        "dealer": {"display_name": f"Dealer {dealer_id}", "phone_number": "+85290000001"},
    }


def _detail_offer(
    *,
    watch_id: str,
    dealer_id: str,
    usd_price: int,
    condition: str | None,
    dial: str = "Blue",
) -> dict:
    return {
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "usd_price": usd_price,
        "condition": condition,
        "original_price": usd_price,
        "original_currency": "USD",
        "card_date": "06/2026",
        "watches": {"dial": dial},
        "dealers": {"display_name": f"Dealer {dealer_id}", "phone_number": "+85290000001"},
        "messages": {"received_at": "2026-06-01T12:00:00+00:00", "group_id": "g-1", "groups": {"name": "Group A"}},
    }


PAtek_5711_OFFERS = [
    _search_offer(
        watch_id="w-5711-1r-a",
        dealer_id="dealer-1",
        watch=_watch(brand="Patek Philippe", reference="5711/1R", dial="Brown"),
        usd_price=180000,
        condition="New",
    ),
    _search_offer(
        watch_id="w-5711-1r-b",
        dealer_id="dealer-2",
        watch=_watch(brand="Patek Philippe", reference="5711/1R", dial="Brown"),
        usd_price=185000,
        condition="Used",
    ),
    _search_offer(
        watch_id="w-5711-1a",
        dealer_id="dealer-3",
        watch=_watch(brand="Patek Philippe", reference="5711/1A", dial="Blue"),
        usd_price=150000,
        condition="New",
    ),
    _search_offer(
        watch_id="w-5711-1p",
        dealer_id="dealer-4",
        watch=_watch(brand="Patek Philippe", reference="5711/1P", dial=""),
        usd_price=200000,
        condition=None,
    ),
    _search_offer(
        watch_id="w-rolex-5711",
        dealer_id="dealer-5",
        watch=_watch(brand="Rolex", reference="5711/1R", dial="Black"),
        usd_price=120000,
        condition="New",
    ),
]


class TestGroupedReferenceSearch:
    def test_exact_reference_returns_one_group(self) -> None:
        offers = [offer for offer in PAtek_5711_OFFERS if offer["watch"]["reference"] == "5711/1R" and offer["watch"]["brand"] == "Patek Philippe"]

        groups = group_offers_by_brand_reference(offers)

        assert len(groups) == 1
        group = groups[0]
        assert group["watch"]["brand"] == "Patek Philippe"
        assert group["watch"]["reference"] == "5711/1R"
        assert group["offer_count"] == 2
        assert group["unique_dealers"] == 2
        assert group["lowest_usd"] == 180000

    def test_partial_reference_returns_multiple_groups_not_every_offer(self) -> None:
        patek_offers = [offer for offer in PAtek_5711_OFFERS if offer["watch"]["brand"] == "Patek Philippe"]

        groups = group_offers_by_brand_reference(patek_offers)

        assert len(groups) == 3
        references = {group["watch"]["reference"] for group in groups}
        assert references == {"5711/1A", "5711/1R", "5711/1P"}
        assert sum(group["offer_count"] for group in groups) == 4

    def test_groups_separated_by_brand_and_reference(self) -> None:
        groups = group_offers_by_brand_reference(PAtek_5711_OFFERS)

        keys = {(group["watch"]["brand"], group["watch"]["reference"]) for group in groups}
        assert ("Patek Philippe", "5711/1R") in keys
        assert ("Rolex", "5711/1R") in keys
        assert len(keys) == 4

    def test_build_result_rows_shows_reference_index_fields(self) -> None:
        groups = group_offers_by_brand_reference(
            [offer for offer in PAtek_5711_OFFERS if offer["watch"]["reference"] == "5711/1R" and offer["watch"]["brand"] == "Patek Philippe"]
        )
        rows = build_result_rows(groups)

        assert len(rows) == 1
        row = rows[0]
        assert row["brand"] == "Patek Philippe"
        assert row["reference"] == "5711/1R"
        assert row["lowest_price"] == "$180,000"
        assert row["offer_count"] == 2
        assert row["unique_dealers"] == 2
        assert row["conditions_label"] == "New / Pre-Owned"
        assert row["watch_url"] == build_watch_reference_url("Patek Philippe", "5711/1R")

    @patch("app.get_import_logs_by_message_ids", return_value={})
    @patch("app.search_offers")
    def test_search_page_renders_grouped_reference_rows(
        self,
        mock_search_offers: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (PAtek_5711_OFFERS, False)

        client = TestClient(app)
        response = client.get("/?q=5711")

        assert response.status_code == 200
        assert "Patek Philippe" in response.text
        assert "5711/1A" in response.text
        assert "5711/1R" in response.text
        assert "5711/1P" in response.text
        assert "Rolex" in response.text
        assert "Active offers" in response.text
        assert "Dealer A" not in response.text

    @patch("app.get_import_logs_by_message_ids", return_value={})
    @patch("app.search_offers")
    def test_exact_reference_search_shows_one_grouped_row(
        self,
        mock_search_offers: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        exact_offers = [
            offer
            for offer in PAtek_5711_OFFERS
            if offer["watch"]["reference"] == "5711/1R" and offer["watch"]["brand"] == "Patek Philippe"
        ]
        mock_search_offers.return_value = (exact_offers, False)

        client = TestClient(app)
        response = client.get("/?q=5711/1R")

        assert response.status_code == 200
        assert response.text.count("5711/1R") >= 1
        assert 'data-href="/watch-reference?' in response.text


class TestWatchDetailConditionFilters:
    WATCH = {
        "id": "w-5711-1r-a",
        "brand": "Patek Philippe",
        "reference": "5711/1R",
        "model": "Nautilus",
        "dial": "Brown",
        "bracelet": "Bracelet",
    }

    DETAIL_OFFERS = [
        _detail_offer(watch_id="w-5711-1r-a", dealer_id="dealer-1", usd_price=180000, condition="New", dial="Brown"),
        _detail_offer(watch_id="w-5711-1r-b", dealer_id="dealer-2", usd_price=185000, condition="Used", dial="Brown"),
        _detail_offer(watch_id="w-5711-1r-c", dealer_id="dealer-3", usd_price=190000, condition=None, dial=""),
    ]

    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_shows_all_offers_for_brand_reference(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.DETAIL_OFFERS

        client = TestClient(app)
        response = client.get("/watch-reference?brand=Patek+Philippe&reference=5711%2F1R")

        assert response.status_code == 200
        assert "Dealer dealer-1" in response.text
        assert "Dealer dealer-2" in response.text
        assert "Dealer dealer-3" in response.text
        mock_get_offers.assert_called_once_with("Patek Philippe", "5711/1R")

    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_new_filter(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.DETAIL_OFFERS

        client = TestClient(app)
        response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R&condition=new"
        )

        assert response.status_code == 200
        assert "Dealer dealer-1" in response.text
        assert "Dealer dealer-2" not in response.text
        assert "Dealer dealer-3" not in response.text
        assert "Active offers" in response.text
        assert ">1<" in response.text.replace(" ", "")

    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_pre_owned_filter(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.DETAIL_OFFERS

        client = TestClient(app)
        response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R&condition=pre-owned"
        )

        assert response.status_code == 200
        assert "Dealer dealer-2" in response.text
        assert "Dealer dealer-1" not in response.text

    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_unknown_filter(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.DETAIL_OFFERS

        client = TestClient(app)
        response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R&condition=unknown"
        )

        assert response.status_code == 200
        assert "Dealer dealer-3" in response.text
        assert "Dealer dealer-1" not in response.text
        assert "Dealer dealer-2" not in response.text

    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_stats_update_with_condition_filter(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.DETAIL_OFFERS

        client = TestClient(app)
        all_response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R&condition=all"
        )
        new_response = client.get(
            "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R&condition=new"
        )

        assert all_response.status_code == 200
        assert new_response.status_code == 200
        assert ">3<" in all_response.text.replace(" ", "")
        assert ">1<" in new_response.text.replace(" ", "")
        assert "$180,000" in new_response.text
        assert "$185,000" not in new_response.text

    def test_build_offer_rows_displays_dial_without_filtering(self) -> None:
        from app import normalize_watch_detail_offer

        offers = [normalize_watch_detail_offer(offer) for offer in self.DETAIL_OFFERS]
        rows = build_offer_rows(offers)

        dials = [row["dial"] for row in rows]
        assert "Brown" in dials
        assert "N/A" in dials

    def test_build_watch_stats_respects_filtered_offers(self) -> None:
        from app import normalize_watch_detail_offer

        offers = [normalize_watch_detail_offer(offer) for offer in self.DETAIL_OFFERS]
        filtered = [
            offer
            for offer in offers
            if offer_matches_watch_detail_condition(offer.get("condition"), NEW_CONDITION)
        ]
        stats = build_watch_stats(filtered)

        assert stats["offer_count"] == 1
        assert stats["unique_dealers"] == 1
        assert stats["lowest_usd"] == "$180,000"

    @patch("app.get_active_offers_for_brand_reference", return_value=[])
    def test_watch_detail_renders_condition_filter_buttons(
        self,
        _mock_get_offers: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/watch-reference?brand=Patek+Philippe&reference=5711%2F1R")

        assert response.status_code == 200
        condition_urls = build_watch_reference_condition_urls("Patek Philippe", "5711/1R")
        assert f'href="{condition_urls["all"].replace("&", "&amp;")}"' in response.text
        assert f'href="{condition_urls["new"].replace("&", "&amp;")}"' in response.text
        assert f'href="{condition_urls["pre-owned"].replace("&", "&amp;")}"' in response.text
        assert f'href="{condition_urls["unknown"].replace("&", "&amp;")}"' in response.text
