"""Regression tests for Sprint 51.6 watch detail pagination."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import (
    app,
    build_watch_detail_pagination,
    build_watch_reference_url,
    filter_watch_detail_offers_for_stats,
)
from watch_detail_filters import (
    WATCH_DETAIL_PAGE_SIZE,
    paginate_watch_detail_offers,
    parse_watch_detail_page,
    resolve_watch_detail_page,
    sort_offers_for_watch_detail,
)
from tests.conftest import ADMIN_USER

NOW = __import__("datetime").datetime(
    2026,
    6,
    15,
    12,
    0,
    tzinfo=__import__("timezone_utils", fromlist=["DISPLAY_TIMEZONE"]).DISPLAY_TIMEZONE,
)

WATCH_URL = "/watch-reference?brand=Patek+Philippe&reference=5711%2F1A"


def _detail_offer(
    *,
    offer_id: str,
    usd_price: int | None,
    condition: str = "New",
    received_at: str = "2026-06-15T08:00:00+00:00",
    dealer_name: str = "Dealer",
) -> dict:
    return {
        "id": offer_id,
        "message_id": f"msg-{offer_id[:8]}",
        "dealer_id": f"dealer-{offer_id[:8]}",
        "watch_id": "w-1",
        "usd_price": usd_price,
        "condition": condition,
        "original_price": usd_price,
        "original_currency": "USD",
        "card_date": "06/2026",
        "watches": {"dial": "Blue", "brand": "Patek Philippe", "reference": "5711/1A"},
        "dealers": {"display_name": dealer_name, "phone_number": "+85290000001"},
        "messages": {
            "id": f"msg-{offer_id[:8]}",
            "received_at": received_at,
            "group_id": "g-1",
            "groups": {"name": "Group A"},
        },
    }


def _offer_id(index: int) -> str:
    return f"{index:08x}-aaaa-4aaa-8aaa-{index:012x}"


def _build_offers(count: int) -> list[dict]:
    return [
        _detail_offer(
            offer_id=_offer_id(index),
            usd_price=100000 + index,
            dealer_name=f"Dealer {index}",
            received_at=f"2026-06-{min(index + 1, 28):02d}T08:00:00+00:00",
        )
        for index in range(count)
    ]


class TestWatchDetailPaginationHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, 1),
            ("", 1),
            ("0", 1),
            ("-3", 1),
            ("abc", 1),
            ("1", 1),
            ("2", 2),
        ],
    )
    def test_parse_watch_detail_page_normalizes_invalid_values(
        self,
        value: str | None,
        expected: int,
    ) -> None:
        assert parse_watch_detail_page(value) == expected

    def test_resolve_watch_detail_page_clamps_beyond_final_page(self) -> None:
        page, total_pages = resolve_watch_detail_page(99, total_items=60, page_size=50)
        assert page == 2
        assert total_pages == 2

    def test_paginate_returns_non_overlapping_slices(self) -> None:
        offers = [{"id": str(index)} for index in range(75)]
        page_one, page_num, total_pages, total_items = paginate_watch_detail_offers(
            offers,
            page_input=1,
        )
        page_two, page_two_num, _, _ = paginate_watch_detail_offers(offers, page_input=2)

        assert total_items == 75
        assert total_pages == 2
        assert page_num == 1
        assert page_two_num == 2
        assert len(page_one) == 50
        assert len(page_two) == 25
        assert {row["id"] for row in page_one}.isdisjoint({row["id"] for row in page_two})


class TestWatchDetailPaginationRoute:
    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_default_page_renders_at_most_fifty_offers(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        offers = _build_offers(75)
        mock_get_offers.return_value = offers
        mock_load_lookups.return_value = ({}, {}, {})
        mock_attach_sources.side_effect = lambda rows, *_args, **_kwargs: rows

        client = TestClient(app)
        response = client.get(WATCH_URL)

        assert response.status_code == 200
        assert response.text.count('class="form-check-input offer-select-checkbox"') == 50
        mock_load_lookups.assert_called_once()
        assert len(mock_load_lookups.call_args.args[0]) == 50

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_page_two_renders_next_offers(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        offers = _build_offers(75)
        mock_get_offers.return_value = offers
        mock_load_lookups.return_value = ({}, {}, {})
        mock_attach_sources.side_effect = lambda rows, *_args, **_kwargs: rows

        client = TestClient(app)
        page_one = client.get(f"{WATCH_URL}&sort=price_asc")
        page_two = client.get(f"{WATCH_URL}&sort=price_asc&page=2")

        assert page_one.status_code == 200
        assert page_two.status_code == 200
        assert f'value="{_offer_id(0)}"' in page_one.text
        assert f'value="{_offer_id(49)}"' in page_one.text
        assert f'value="{_offer_id(50)}"' in page_two.text
        assert f'value="{_offer_id(0)}"' not in page_two.text

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_sorting_happens_before_pagination(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        offers = _build_offers(60)
        mock_get_offers.return_value = offers
        mock_load_lookups.return_value = ({}, {}, {})
        enriched: list[dict] = []

        def _capture_enriched(rows: list[dict], *_args, **_kwargs) -> list[dict]:
            enriched.extend(rows)
            return rows

        mock_attach_sources.side_effect = _capture_enriched

        client = TestClient(app)
        response = client.get(f"{WATCH_URL}&sort=price_asc")

        assert response.status_code == 200
        assert len(enriched) == 50
        assert enriched[0]["usd_price"] == 100000
        assert enriched[-1]["usd_price"] == 100049

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_condition_filtering_happens_before_pagination(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        offers = [
            _detail_offer(
                offer_id=_offer_id(0),
                usd_price=100000,
                condition="New",
                dealer_name="Only New Dealer",
            ),
            _detail_offer(
                offer_id=_offer_id(1),
                usd_price=110000,
                condition="Pre-Owned",
                dealer_name="Only Preowned Dealer",
            ),
        ]
        mock_get_offers.return_value = offers
        mock_load_lookups.return_value = ({}, {}, {})
        mock_attach_sources.side_effect = lambda rows, *_args, **_kwargs: rows

        client = TestClient(app)
        response = client.get(f"{WATCH_URL}&condition=pre-owned")

        assert response.status_code == 200
        assert "Only New Dealer" not in response.text
        assert "Only Preowned Dealer" in response.text

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_statistics_use_complete_filtered_dataset(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        offers = _build_offers(75)
        mock_get_offers.return_value = offers
        mock_load_lookups.return_value = ({}, {}, {})
        mock_attach_sources.side_effect = lambda rows, *_args, **_kwargs: rows

        client = TestClient(app)
        response = client.get(WATCH_URL)

        assert response.status_code == 200
        assert ">75<" in response.text
        assert "1–50 of 75 offers" in response.text

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_source_enrichment_receives_only_current_page(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        offers = _build_offers(75)
        mock_get_offers.return_value = offers
        mock_load_lookups.return_value = ({}, {}, {})
        mock_attach_sources.side_effect = lambda rows, *_args, **_kwargs: rows

        client = TestClient(app)
        client.get(f"{WATCH_URL}&page=2")

        mock_attach_sources.assert_called_once()
        assert len(mock_attach_sources.call_args.args[0]) == 25

    def test_pagination_links_preserve_filters_and_sort(self) -> None:
        pagination = build_watch_detail_pagination(
            brand="Patek Philippe",
            reference="5711/1A",
            condition="new",
            date="7d",
            date_from="",
            date_to="",
            sort="price_asc",
            page=2,
            total_pages=3,
            total_items=120,
        )

        assert "condition=new" in pagination["previous_url"]
        assert "date=7d" in pagination["previous_url"]
        assert "sort=price_asc" in pagination["previous_url"]
        assert "page=2" not in pagination["previous_url"]
        assert "page=3" in pagination["next_url"]

    def test_changing_filters_resets_page_to_one_in_urls(self) -> None:
        url = build_watch_reference_url(
            "Patek Philippe",
            "5711/1A",
            condition="new",
            date="7d",
            sort="price_asc",
        )
        assert url is not None
        assert "page=" not in url

    @patch("app.filter_watch_detail_offers_for_stats")
    @patch("app.bulk_soft_remove_offers")
    def test_bulk_remove_redirect_preserves_page_and_filters(
        self,
        mock_bulk_remove: MagicMock,
        mock_filter_offers: MagicMock,
    ) -> None:
        from offer_removal import BulkRemoveResult

        mock_bulk_remove.return_value = BulkRemoveResult(
            success_count=1,
            skipped_count=0,
            removed_ids=("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",),
            skipped_ids=(),
        )
        mock_filter_offers.return_value = [{}] * 51

        return_url = (
            f"{WATCH_URL}&condition=new&date=7d&sort=price_asc&page=2"
        )
        client = TestClient(app)
        response = client.post(
            "/offers/bulk-remove",
            data={
                "confirm": "1",
                "removal_reason": "incorrect_price",
                "return_to": return_url,
                "offer_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            },
            follow_redirects=False,
        )

        location = response.headers["location"]
        assert response.status_code == 303
        assert "bulk_removed=1" in location
        assert "condition=new" in location
        assert "date=7d" in location
        assert "sort=price_asc" in location
        assert "removed_id=" in location or "removed_ids=" in location

    @patch("app.filter_watch_detail_offers_for_stats")
    @patch("app.soft_remove_offer")
    def test_single_remove_redirects_to_previous_page_when_current_page_empty(
        self,
        mock_soft_remove: MagicMock,
        mock_filter_offers: MagicMock,
    ) -> None:
        mock_soft_remove.return_value = {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
        mock_filter_offers.return_value = [{}] * 51

        client = TestClient(app)
        response = client.post(
            "/offers/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/remove",
            data={
                "confirm": "1",
                "removal_reason": "incorrect_price",
                "return_to": f"{WATCH_URL}&page=2",
            },
            follow_redirects=False,
        )

        location = response.headers["location"]
        assert response.status_code == 303
        assert "removed=1" in location
        assert "page=2" not in location

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_null_price_ordering_remains_correct_with_pagination(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        offers = [
            _detail_offer(offer_id=_offer_id(0), usd_price=None),
            _detail_offer(offer_id=_offer_id(1), usd_price=150000),
            _detail_offer(offer_id=_offer_id(2), usd_price=140000),
        ]
        mock_get_offers.return_value = offers
        mock_load_lookups.return_value = ({}, {}, {})
        enriched: list[dict] = []

        def _capture(rows: list[dict], *_args, **_kwargs) -> list[dict]:
            enriched.extend(rows)
            return rows

        mock_attach_sources.side_effect = _capture

        client = TestClient(app)
        response = client.get(f"{WATCH_URL}&sort=price_asc")

        assert response.status_code == 200
        assert [row["usd_price"] for row in enriched] == [140000, 150000, None]

    @patch("watch_detail_filters.watch_detail_filter_now", return_value=NOW)
    @patch("app.attach_dealer_offer_source_urls")
    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_select_all_markup_only_on_visible_rows(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
        mock_attach_sources: MagicMock,
        _mock_filter_now: MagicMock,
    ) -> None:
        mock_get_offers.return_value = _build_offers(75)
        mock_load_lookups.return_value = ({}, {}, {})
        mock_attach_sources.side_effect = lambda rows, *_args, **_kwargs: rows

        client = TestClient(app)
        response = client.get(WATCH_URL)

        assert response.status_code == 200
        assert 'aria-label="Select all on this page"' in response.text
        assert response.text.count('class="form-check-input offer-select-checkbox"') == WATCH_DETAIL_PAGE_SIZE
