"""Tests for Sprint 48.5.4 watch reference detail date filters."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import (
    app,
    build_offer_rows,
    build_watch_reference_filter_urls,
    build_watch_reference_url,
    build_watch_stats,
    normalize_watch_detail_offer,
)
from timezone_utils import DISPLAY_TIMEZONE
from watch_detail_filters import (
    offer_matches_watch_detail_date_filter,
    parse_watch_detail_date_filter,
    resolve_watch_detail_date_range,
    sort_key_watch_detail_offer,
)

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=DISPLAY_TIMEZONE)


def _detail_offer(
    *,
    offer_id: str,
    dealer_id: str,
    usd_price: int,
    condition: str | None,
    received_at: str,
    message_id: str = "msg-1",
    source_url: str | None = "/activity/log-1",
) -> dict:
    return {
        "id": offer_id,
        "message_id": message_id,
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
            "id": message_id,
            "received_at": received_at,
            "group_id": "g-1",
            "groups": {"name": "Group A"},
        },
        "source_url": source_url,
    }


class TestWatchDetailDateFilterHelpers:
    def test_parse_date_filter_values(self) -> None:
        assert parse_watch_detail_date_filter("all") == "all"
        assert parse_watch_detail_date_filter("today") == "today"
        assert parse_watch_detail_date_filter("7d") == "7d"
        assert parse_watch_detail_date_filter("custom", date_from="2026-06-01") == "custom"

    def test_resolve_today_range_uses_display_timezone(self) -> None:
        date_range = resolve_watch_detail_date_range("today", now=NOW)

        assert date_range is not None
        assert date_range.start == datetime(2026, 6, 15, 0, 0, tzinfo=DISPLAY_TIMEZONE).astimezone(
            ZoneInfo("UTC")
        )
        assert date_range.end == datetime(2026, 6, 16, 0, 0, tzinfo=DISPLAY_TIMEZONE).astimezone(
            ZoneInfo("UTC")
        )

    def test_offer_matches_last_7_days(self) -> None:
        date_range = resolve_watch_detail_date_range("7d", now=NOW)
        recent = {"recency_at": "2026-06-14T10:00:00+00:00"}
        old = {"recency_at": "2026-06-01T10:00:00+00:00"}

        assert offer_matches_watch_detail_date_filter(recent, date_range, now=NOW) is True
        assert offer_matches_watch_detail_date_filter(old, date_range, now=NOW) is False

    def test_sort_key_prefers_newest_then_lowest_price(self) -> None:
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


class TestWatchReferenceDetailDateFilters:
    BRAND = "Patek Philippe"
    REFERENCE = "5711/1R"
    DETAIL_URL = "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R"

    OFFERS = [
        _detail_offer(
            offer_id="offer-today",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
            received_at="2026-06-15T08:00:00+00:00",
            message_id="msg-today",
            source_url="/activity/log-today",
        ),
        _detail_offer(
            offer_id="offer-week",
            dealer_id="dealer-2",
            usd_price=185000,
            condition="Used",
            received_at="2026-06-10T08:00:00+00:00",
            message_id="msg-week",
            source_url="/activity/log-week",
        ),
        _detail_offer(
            offer_id="offer-old",
            dealer_id="dealer-3",
            usd_price=190000,
            condition=None,
            received_at="2026-05-01T08:00:00+00:00",
            message_id="msg-old",
            source_url=None,
        ),
    ]

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_last_7_days_only_shows_recent_offers(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = lambda offers, **_: [
            {
                **offer,
                "recency_at": offer.get("received_at"),
                "source_url": offer.get("source_url"),
            }
            for offer in offers
        ]

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&date=7d")

        assert response.status_code == 200
        assert "Dealer dealer-1" in response.text
        assert "Dealer dealer-2" in response.text
        assert "Dealer dealer-3" not in response.text
        assert ">2<" in response.text.replace(" ", "")

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_today_only_shows_todays_offers(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = lambda offers, **_: [
            {**offer, "recency_at": offer.get("received_at"), "source_url": offer.get("source_url")}
            for offer in offers
        ]

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&date=today")

        assert response.status_code == 200
        assert "Dealer dealer-1" in response.text
        assert "Dealer dealer-2" not in response.text
        assert "Dealer dealer-3" not in response.text

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_condition_and_date_filters_work_together(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = lambda offers, **_: [
            {**offer, "recency_at": offer.get("received_at"), "source_url": offer.get("source_url")}
            for offer in offers
        ]

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&date=7d&condition=new")

        assert response.status_code == 200
        assert "Dealer dealer-1" in response.text
        assert "Dealer dealer-2" not in response.text
        assert "Dealer dealer-3" not in response.text

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_stats_update_after_date_filter(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = lambda offers, **_: [
            {**offer, "recency_at": offer.get("received_at"), "source_url": offer.get("source_url")}
            for offer in offers
        ]

        client = TestClient(app)
        all_response = client.get(self.DETAIL_URL)
        filtered_response = client.get(f"{self.DETAIL_URL}&date=today")

        assert all_response.status_code == 200
        assert filtered_response.status_code == 200
        assert ">3<" in all_response.text.replace(" ", "")
        assert ">1<" in filtered_response.text.replace(" ", "")
        assert "$180,000" in filtered_response.text
        assert "$185,000" not in filtered_response.text
        assert "New / Pre-Owned / Unknown" in all_response.text
        assert '<div class="fw-semibold">New</div>' in filtered_response.text
        assert '<div class="fw-semibold">New / Pre-Owned / Unknown</div>' not in filtered_response.text

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.enrich_watch_detail_offer_recency")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_source_links_preserved_after_date_filtering(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_enrich_recency: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = self.OFFERS
        mock_load_lookups.return_value = ({}, {}, {})
        mock_enrich_recency.side_effect = lambda offers, **_: [
            {**offer, "recency_at": offer.get("received_at"), "source_url": offer.get("source_url")}
            for offer in offers
        ]

        client = TestClient(app)
        response = client.get(f"{self.DETAIL_URL}&date=7d")

        assert response.status_code == 200
        assert 'href="/activity/log-today"' in response.text
        assert 'href="/activity/log-week"' in response.text
        assert "View original" in response.text

    def test_filter_urls_preserve_other_dimension(self) -> None:
        urls = build_watch_reference_filter_urls(
            self.BRAND,
            self.REFERENCE,
            condition="new",
            date="7d",
        )

        assert "condition=new" in urls["date_today"]
        assert "date=7d" in urls["condition_pre_owned"]
        assert build_watch_reference_url(self.BRAND, self.REFERENCE, condition="new", date="7d") in urls[
            "condition_new"
        ]

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    def test_build_offer_rows_sort_newest_first_then_price(self, *_mocks: MagicMock) -> None:
        offers = [
            {
                **normalize_watch_detail_offer(self.OFFERS[2]),
                "recency_at": "2026-05-01T08:00:00+00:00",
                "source_url": None,
            },
            {
                **normalize_watch_detail_offer(self.OFFERS[0]),
                "recency_at": "2026-06-15T08:00:00+00:00",
                "source_url": "/activity/log-today",
            },
            {
                **normalize_watch_detail_offer(self.OFFERS[1]),
                "recency_at": "2026-06-10T08:00:00+00:00",
                "source_url": "/activity/log-week",
            },
        ]
        rows = build_offer_rows(offers)

        assert rows[0]["dealer_name"] == "Dealer dealer-1"
        assert rows[1]["dealer_name"] == "Dealer dealer-2"
        assert rows[2]["dealer_name"] == "Dealer dealer-3"

    def test_build_watch_stats_includes_condition_counts(self) -> None:
        offers = [
            {**normalize_watch_detail_offer(offer), "recency_at": offer["messages"]["received_at"]}
            for offer in self.OFFERS[:2]
        ]
        stats = build_watch_stats(offers)

        assert stats["offer_count"] == 2
        assert stats["conditions_label"] == "New / Pre-Owned"
