"""Tests for Sprint 51.2 watch reference detail price sorting."""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import (
    app,
    build_offer_rows,
    build_watch_reference_filter_urls,
    build_watch_reference_url,
    normalize_watch_detail_offer,
)
from timezone_utils import DISPLAY_TIMEZONE
from watch_detail_filters import (
    parse_watch_detail_sort_filter,
    sort_key_watch_detail_offer,
    sort_key_watch_detail_offer_price_asc,
    sort_offers_for_watch_detail,
)

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=DISPLAY_TIMEZONE)


def _detail_offer(
    *,
    offer_id: str,
    dealer_id: str,
    usd_price: int | None,
    condition: str | None,
    received_at: str = "2026-06-01T12:00:00+00:00",
    message_id: str | None = None,
    source_url: str | None = None,
) -> dict:
    return {
        "id": offer_id,
        "message_id": message_id or f"msg-{offer_id}",
        "dealer_id": dealer_id,
        "watch_id": "w-1",
        "usd_price": usd_price,
        "condition": condition,
        "original_price": usd_price,
        "original_currency": "USD",
        "card_date": "06/2026",
        "watches": {"dial": "Blue"},
        "dealers": {"display_name": f"Dealer {dealer_id}", "phone_number": "+85290000001"},
        "messages": {
            "id": message_id or f"msg-{offer_id}",
            "received_at": received_at,
            "group_id": "g-1",
            "groups": {"name": "Group A"},
        },
        "source_url": source_url,
    }


def _count_offer_rows(html: str) -> int:
    tbody_match = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
    if not tbody_match:
        return 0
    return tbody_match.group(1).count("<tr>")


def _dealer_order(html: str) -> list[str]:
    return re.findall(r"<td>Dealer ([^<]+)</td>", html)


def _mock_enrich_side_effect(offers: list[dict], **_: object) -> list[dict]:
    return [
        {
            **offer,
            "recency_at": offer.get("received_at"),
            "source_url": offer.get("source_url"),
        }
        for offer in offers
    ]


class TestWatchDetailSortHelpers:
    def test_parse_sort_filter_values(self) -> None:
        assert parse_watch_detail_sort_filter(None) == ""
        assert parse_watch_detail_sort_filter("") == ""
        assert parse_watch_detail_sort_filter("price_asc") == "price_asc"
        assert parse_watch_detail_sort_filter("price_desc") == "price_desc"
        assert parse_watch_detail_sort_filter("invalid") == ""

    def test_price_asc_orders_lowest_first_with_nulls_last(self) -> None:
        offers = [
            {"id": "high", "usd_price": 200000, "recency_at": "2026-06-01T12:00:00+00:00"},
            {"id": "low", "usd_price": 150000, "recency_at": "2026-06-02T12:00:00+00:00"},
            {"id": "null", "usd_price": None, "recency_at": "2026-06-03T12:00:00+00:00"},
        ]

        ordered = sort_offers_for_watch_detail(offers, "price_asc")

        assert [offer["id"] for offer in ordered] == ["low", "high", "null"]

    def test_price_desc_orders_highest_first_with_nulls_last(self) -> None:
        offers = [
            {"id": "high", "usd_price": 200000, "recency_at": "2026-06-01T12:00:00+00:00"},
            {"id": "low", "usd_price": 150000, "recency_at": "2026-06-02T12:00:00+00:00"},
            {"id": "null", "usd_price": None, "recency_at": "2026-06-03T12:00:00+00:00"},
        ]

        ordered = sort_offers_for_watch_detail(offers, "price_desc")

        assert [offer["id"] for offer in ordered] == ["high", "low", "null"]

    def test_equal_prices_use_newest_received_at(self) -> None:
        older = {
            "id": "older",
            "usd_price": 150000,
            "recency_at": "2026-06-01T12:00:00+00:00",
        }
        newer = {
            "id": "newer",
            "usd_price": 150000,
            "recency_at": "2026-06-10T12:00:00+00:00",
        }

        assert sort_key_watch_detail_offer_price_asc(newer) < sort_key_watch_detail_offer_price_asc(older)
        ordered = sort_offers_for_watch_detail([older, newer], "price_asc")

        assert ordered[0]["id"] == "newer"

    def test_default_sort_unchanged(self) -> None:
        newer_cheaper = {"recency_at": "2026-06-15T10:00:00+00:00", "usd_price": 180000}
        newer_dearer = {"recency_at": "2026-06-15T09:00:00+00:00", "usd_price": 170000}
        older = {"recency_at": "2026-06-01T10:00:00+00:00", "usd_price": 150000}

        ordered = sort_offers_for_watch_detail([older, newer_dearer, newer_cheaper], "")

        assert ordered[0] is newer_cheaper
        assert ordered[1] is newer_dearer
        assert ordered[2] is older


class TestWatchReferenceDetailSortFilter:
    BRAND = "Patek Philippe"
    REFERENCE = "5711/1R"
    DETAIL_URL = "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R"

    OFFERS = [
        _detail_offer(
            offer_id="offer-new-mid",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
            received_at="2026-06-15T08:00:00+00:00",
            source_url="/activity/log-1",
        ),
        _detail_offer(
            offer_id="offer-used-low",
            dealer_id="dealer-2",
            usd_price=170000,
            condition="Used",
            received_at="2026-06-14T08:00:00+00:00",
            source_url="/activity/log-2",
        ),
        _detail_offer(
            offer_id="offer-new-high",
            dealer_id="dealer-3",
            usd_price=190000,
            condition="New",
            received_at="2026-06-13T08:00:00+00:00",
            source_url="/activity/log-3",
        ),
        _detail_offer(
            offer_id="offer-unpriced",
            dealer_id="dealer-4",
            usd_price=None,
            condition="New",
            received_at="2026-06-16T08:00:00+00:00",
        ),
    ]

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_price_asc_shows_all_rows_in_order(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&sort=price_asc")

        assert response.status_code == 200
        assert _count_offer_rows(response.text) == 4
        assert _dealer_order(response.text) == [
            "dealer-2",
            "dealer-1",
            "dealer-3",
            "dealer-4",
        ]

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_price_desc_shows_all_rows_in_order(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&sort=price_desc")

        assert response.status_code == 200
        assert _count_offer_rows(response.text) == 4
        assert _dealer_order(response.text) == [
            "dealer-3",
            "dealer-1",
            "dealer-2",
            "dealer-4",
        ]

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_default_ordering_without_sort_param(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        response = client.get(self.DETAIL_URL)

        assert response.status_code == 200
        assert _count_offer_rows(response.text) == 4
        assert _dealer_order(response.text)[0] == "dealer-4"

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_condition_date_and_sort_combined(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        response = client.get(
            f"{self.DETAIL_URL}&condition=new&date=7d&sort=price_asc"
        )

        assert response.status_code == 200
        assert _count_offer_rows(response.text) == 2
        assert _dealer_order(response.text) == ["dealer-1", "dealer-3"]
        assert ">2<" in response.text.replace(" ", "")

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_statistics_unaffected_by_sort(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        unsorted = client.get(f"{self.DETAIL_URL}&condition=new")
        sorted_response = client.get(f"{self.DETAIL_URL}&condition=new&sort=price_desc")

        assert unsorted.status_code == 200
        assert sorted_response.status_code == 200
        assert ">3<" in unsorted.text.replace(" ", "")
        assert ">3<" in sorted_response.text.replace(" ", "")

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_invalid_sort_falls_back_to_default(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        default_response = client.get(self.DETAIL_URL)
        invalid_response = client.get(f"{self.DETAIL_URL}&sort=not-a-sort")

        assert default_response.status_code == 200
        assert invalid_response.status_code == 200
        assert _dealer_order(default_response.text) == _dealer_order(invalid_response.text)

    def test_filter_urls_preserve_sort_state(self) -> None:
        urls = build_watch_reference_filter_urls(
            self.BRAND,
            self.REFERENCE,
            condition="new",
            date="7d",
            sort="price_asc",
        )

        assert "sort=price_asc" in urls["date_today"]
        assert "condition=new" in urls["date_today"]
        assert "date=7d" in urls["condition_pre_owned"]
        assert "sort=price_asc" in urls["condition_pre_owned"]

    def test_build_watch_reference_url_omits_sort_when_default(self) -> None:
        with_sort = build_watch_reference_url(
            self.BRAND,
            self.REFERENCE,
            condition="new",
            date="7d",
            sort="price_desc",
        )
        without_sort = build_watch_reference_url(
            self.BRAND,
            self.REFERENCE,
            condition="new",
            date="7d",
            sort="",
        )

        assert with_sort is not None
        assert without_sort is not None
        assert "sort=price_desc" in with_sort
        assert "sort" not in without_sort

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_sort_control_renders_and_preserves_filters(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        response = client.get(
            f"{self.DETAIL_URL}&condition=new&date=7d&sort=price_asc"
        )

        assert response.status_code == 200
        assert 'id="offer_sort"' in response.text
        assert "Price: low to high" in response.text
        assert 'name="condition" value="new"' in response.text
        assert 'name="date" value="7d"' in response.text
        assert 'value="price_asc"' in response.text
        assert re.search(r'value="price_asc"[^>]*selected|selected[^>]*value="price_asc"', response.text)

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_source_enrichment_applies_to_all_displayed_offers(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        mock_attach_source: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect
        mock_attach_source.side_effect = lambda offers, *args, **kwargs: offers

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&sort=price_asc")

        assert response.status_code == 200
        mock_attach_source.assert_called_once()
        attached_offers = mock_attach_source.call_args.args[0]
        assert len(attached_offers) == 4

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_cheapest_query_param_is_ignored(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = _mock_enrich_side_effect

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&cheapest=1")

        assert response.status_code == 200
        assert _count_offer_rows(response.text) == 4
        assert "Cheapest offer" not in response.text
        assert "No priced active offers for this filter." not in response.text

    def test_build_offer_rows_respects_sort_filter(self) -> None:
        offers = [
            {
                **normalize_watch_detail_offer(self.OFFERS[2]),
                "recency_at": "2026-06-13T08:00:00+00:00",
            },
            {
                **normalize_watch_detail_offer(self.OFFERS[1]),
                "recency_at": "2026-06-14T08:00:00+00:00",
            },
            {
                **normalize_watch_detail_offer(self.OFFERS[0]),
                "recency_at": "2026-06-15T08:00:00+00:00",
            },
        ]

        default_rows = build_offer_rows(offers)
        asc_rows = build_offer_rows(offers, sort_filter="price_asc")

        assert default_rows[0]["dealer_name"] == "Dealer dealer-1"
        assert asc_rows[0]["dealer_name"] == "Dealer dealer-2"

    def test_default_sort_key_matches_legacy_behavior(self) -> None:
        newer_cheaper = {"recency_at": "2026-06-15T10:00:00+00:00", "usd_price": 180000}
        newer_dearer = {"recency_at": "2026-06-15T09:00:00+00:00", "usd_price": 170000}
        older = {"recency_at": "2026-06-01T10:00:00+00:00", "usd_price": 150000}

        ordered = sorted(
            [older, newer_dearer, newer_cheaper],
            key=sort_key_watch_detail_offer,
        )

        assert ordered[0] is newer_cheaper
        assert ordered[1] is newer_dearer
        assert ordered[2] is older
