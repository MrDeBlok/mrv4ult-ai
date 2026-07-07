"""Tests for Sprint 48.5.3 reference-level watch detail and search count alignment."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import (
    app,
    build_reference_detail_display,
    build_result_rows,
    build_watch_reference_condition_urls,
    build_watch_reference_url,
)
from database import find_watch_ids_for_brand_reference, trace_brand_reference_lookup
from search import (
    brand_reference_group_key,
    group_offers_by_brand_reference,
    reference_lookup_tokens,
)


def _watch(
    *,
    brand: str,
    reference: str,
    dial: str = "",
    model: str = "Nautilus",
) -> dict:
    return {
        "brand": brand,
        "reference": reference,
        "dial": dial,
        "model": model,
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
    dial: str = "Grey",
) -> dict:
    return {
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "usd_price": usd_price,
        "condition": condition,
        "original_price": usd_price,
        "original_currency": "USD",
        "card_date": "06/2026",
        "watches": {"dial": dial, "model": "Nautilus"},
        "dealers": {"display_name": f"Dealer {dealer_id}", "phone_number": "+85290000001"},
        "messages": {
            "received_at": "2026-06-01T12:00:00+00:00",
            "group_id": "g-1",
            "groups": {"name": "Group A"},
        },
    }


PAtek_5990_OFFERS = [
    _search_offer(
        watch_id="w-5990-grey",
        dealer_id=f"dealer-{index}",
        watch=_watch(brand="Patek Philippe", reference="5990/1A", dial="Grey"),
        usd_price=180000 + index * 1000,
        condition="New" if index % 2 == 0 else "Used",
    )
    for index in range(74)
]


class TestReferenceLevelWatchDetail:
    def test_build_watch_reference_url_encodes_brand_and_reference(self) -> None:
        url = build_watch_reference_url("Patek Philippe", "5990/1A", condition="new")

        assert url is not None
        assert "/watch-reference?" in url
        assert "brand=Patek+Philippe" in url
        assert "reference=5990%2F1A" in url
        assert "condition=new" in url

    def test_build_result_rows_link_to_reference_route_not_watch_id(self) -> None:
        groups = group_offers_by_brand_reference(PAtek_5990_OFFERS)
        rows = build_result_rows(groups)

        assert len(rows) == 1
        row = rows[0]
        assert row["offer_count"] == 74
        assert row["watch_url"] == build_watch_reference_url("Patek Philippe", "5990/1A")
        assert "/watch/w-" not in (row["watch_url"] or "")

    def test_search_and_detail_offer_counts_match_for_brand_reference(self) -> None:
        groups = group_offers_by_brand_reference(PAtek_5990_OFFERS)
        search_count = groups[0]["offer_count"]

        detail_offers = [
            _detail_offer(
                watch_id=f"w-5990-{index}",
                dealer_id=f"dealer-{index}",
                usd_price=180000 + index * 1000,
                condition="New" if index % 2 == 0 else "Used",
                dial="Grey" if index % 2 == 0 else "Blue",
            )
            for index in range(search_count)
        ]

        with patch("app.get_active_offers_for_brand_reference", return_value=detail_offers):
            client = TestClient(app)
            response = client.get(
                "/watch-reference?brand=Patek+Philippe&reference=5990%2F1A&condition=all"
            )

        assert response.status_code == 200
        assert f">{search_count}<" in response.text.replace(" ", "")

    @patch("app.get_active_offers_for_brand_reference")
    def test_reference_detail_loads_all_dial_variants(
        self,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = [
            _detail_offer(watch_id="w-grey", dealer_id="dealer-1", usd_price=180000, condition="New", dial="Grey"),
            _detail_offer(watch_id="w-blue", dealer_id="dealer-2", usd_price=185000, condition="Used", dial="Blue"),
        ]

        client = TestClient(app)
        response = client.get("/watch-reference?brand=Patek+Philippe&reference=5990%2F1A")

        assert response.status_code == 200
        assert "Dealer dealer-1" in response.text
        assert "Dealer dealer-2" in response.text
        mock_get_offers.assert_called_once_with("Patek Philippe", "5990/1A")

    @patch("app.get_active_offers_for_brand_reference", return_value=[])
    def test_reference_detail_header_does_not_show_single_dial(
        self,
        _mock_get_offers: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/watch-reference?brand=Patek+Philippe&reference=5990%2F1A")

        assert response.status_code == 200
        assert "Dial</div>" not in response.text
        assert "Bracelet</div>" not in response.text

    def test_build_reference_detail_display_varies_model_when_multiple(self) -> None:
        from app import normalize_watch_detail_offer

        offers = [
            normalize_watch_detail_offer(
                _detail_offer(
                    watch_id="w-1",
                    dealer_id="dealer-1",
                    usd_price=180000,
                    condition="New",
                    dial="Grey",
                )
            ),
            normalize_watch_detail_offer(
                {
                    **_detail_offer(
                        watch_id="w-2",
                        dealer_id="dealer-2",
                        usd_price=185000,
                        condition="Used",
                        dial="Blue",
                    ),
                    "watches": {"dial": "Blue", "model": "Aquanaut"},
                }
            ),
        ]

        display = build_reference_detail_display("Patek Philippe", "5990/1A", offers)

        assert display["brand"] == "Patek Philippe"
        assert display["reference"] == "5990/1A"
        assert display["model"] == "Varies by offer"
        assert "dial" not in display
        assert "bracelet" not in display

    @patch("app.get_active_offers_for_brand_reference", return_value=[])
    def test_condition_filter_links_use_reference_route(
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

    @patch("app.get_watch_by_id")
    def test_watch_id_route_redirects_to_reference_route(
        self,
        mock_get_watch: MagicMock,
    ) -> None:
        mock_get_watch.return_value = {
            "id": "w-grey",
            "brand": "Patek Philippe",
            "reference": "5990/1A",
            "dial": "Grey",
            "bracelet": "Bracelet",
        }

        client = TestClient(app, follow_redirects=False)
        response = client.get("/watch/w-grey?condition=new")

        assert response.status_code == 307
        assert response.headers["location"] == build_watch_reference_url(
            "Patek Philippe",
            "5990/1A",
            condition="new",
        )

    @patch("app.get_import_logs_by_message_ids", return_value={})
    @patch("app.search_offers")
    def test_search_page_links_grouped_row_to_reference_route(
        self,
        mock_search_offers: MagicMock,
        _mock_import_logs: MagicMock,
    ) -> None:
        mock_search_offers.return_value = (PAtek_5990_OFFERS[:5], False)

        client = TestClient(app)
        response = client.get("/?q=5990%2F1A")

        assert response.status_code == 200
        expected_href = (build_watch_reference_url("Patek Philippe", "5990/1A") or "").replace(
            "&", "&amp;"
        )
        assert f'data-href="{expected_href}"' in response.text


class TestLegacyWatchIdRedirect:
    WATCH = {
        "id": "offer-watch-1",
        "brand": "Patek Philippe",
        "reference": "5990/1A",
        "dial": "Grey",
        "bracelet": "Bracelet",
    }

    @patch("app.get_watch_by_id")
    def test_legacy_watch_id_redirects_to_watch_reference(
        self,
        mock_get_watch: MagicMock,
    ) -> None:
        mock_get_watch.return_value = self.WATCH

        client = TestClient(app, follow_redirects=False)
        response = client.get("/watch/offer-watch-1?condition=new")

        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith("/watch-reference?")
        assert "brand=Patek+Philippe" in location
        assert "reference=5990%2F1A" in location
        assert "condition=new" in location
        assert "Statistics" not in response.text

    @patch("app.get_watch_by_id", return_value=None)
    def test_legacy_watch_id_not_found_returns_404(self, _mock_get_watch: MagicMock) -> None:
        client = TestClient(app, follow_redirects=False)
        response = client.get("/watch/missing-watch")

        assert response.status_code == 404

    @patch("app.get_watch_by_id")
    def test_legacy_watch_id_missing_brand_reference_returns_404(
        self,
        mock_get_watch: MagicMock,
    ) -> None:
        mock_get_watch.return_value = {"id": "offer-watch-1", "brand": None, "reference": None}

        client = TestClient(app, follow_redirects=False)
        response = client.get("/watch/offer-watch-1")

        assert response.status_code == 404

    @patch("app.get_active_offers_for_brand_reference", return_value=[])
    @patch("app.get_watch_by_id")
    def test_legacy_watch_id_follow_redirect_renders_reference_detail(
        self,
        mock_get_watch: MagicMock,
        _mock_get_offers: MagicMock,
    ) -> None:
        mock_get_watch.return_value = self.WATCH

        client = TestClient(app, follow_redirects=True)
        response = client.get("/watch/offer-watch-1")

        assert response.status_code == 200
        assert "Patek Philippe" in response.text
        assert "5990/1A" in response.text
        assert "Statistics" in response.text


class TestBrandReferenceNormalization:
    BRAND = "Patek Philippe"

    def test_punctuation_variants_share_group_key(self) -> None:
        variants = ["5990/1A", "5990-1A", "5990 1A", "59901A"]
        keys = {
            brand_reference_group_key({"brand": self.BRAND, "reference": reference})
            for reference in variants
        }

        assert len(keys) == 1
        assert keys.pop() == ("patek philippe", "59901A")

    def test_reference_lookup_tokens_split_punctuated_references(self) -> None:
        assert reference_lookup_tokens("5990/1A") == ["5990", "1A"]
        assert reference_lookup_tokens("5990-1A") == ["5990", "1A"]

    def test_reference_lookup_tokens_use_digit_prefix_for_compact_reference(self) -> None:
        assert reference_lookup_tokens("59901A") == ["5990", "1A"]
        assert reference_lookup_tokens("57111R") == ["5711", "1R"]

    def test_different_references_do_not_share_group_key(self) -> None:
        key_1r = brand_reference_group_key({"brand": self.BRAND, "reference": "5711/1R"})
        key_1a = brand_reference_group_key({"brand": self.BRAND, "reference": "5711/1A"})
        key_suffix = brand_reference_group_key({"brand": self.BRAND, "reference": "5990/1A-010"})

        assert key_1r != key_1a
        assert key_suffix != brand_reference_group_key({"brand": self.BRAND, "reference": "5990/1A"})

    def test_find_watch_ids_matches_punctuated_and_compact_references(self) -> None:
        watches = [
            {"id": "w-slash", "brand": self.BRAND, "reference": "5990/1A"},
            {"id": "w-dash", "brand": self.BRAND, "reference": "5990-1A"},
            {"id": "w-compact", "brand": self.BRAND, "reference": "59901A"},
            {"id": "w-other-ref", "brand": self.BRAND, "reference": "5711/1A"},
            {"id": "w-suffix", "brand": self.BRAND, "reference": "5990/1A-010"},
            {"id": "w-rolex", "brand": "Rolex", "reference": "5990/1A"},
        ]

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.ilike.return_value = mock_table
        mock_table.range.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=watches)

        with patch("database.get_client", return_value=mock_client):
            slash_ids = find_watch_ids_for_brand_reference(self.BRAND, "5990/1A")
            dash_ids = find_watch_ids_for_brand_reference(self.BRAND, "5990-1A")
            compact_ids = find_watch_ids_for_brand_reference(self.BRAND, "59901A")

        assert slash_ids == ["w-slash", "w-dash", "w-compact"]
        assert dash_ids == slash_ids
        assert compact_ids == slash_ids

    def test_search_and_detail_counts_match_for_mixed_reference_formats(self) -> None:
        offers = [
            _search_offer(
                watch_id=f"w-{index}",
                dealer_id=f"dealer-{index}",
                watch=_watch(
                    brand=self.BRAND,
                    reference=reference,
                    dial="Grey" if index % 2 == 0 else "Blue",
                ),
                usd_price=180000 + index * 1000,
                condition="New" if index % 2 == 0 else "Used",
            )
            for index, reference in enumerate(["5990/1A", "5990-1A", "59901A"])
        ]
        groups = group_offers_by_brand_reference(offers)
        search_count = groups[0]["offer_count"]

        watches = [
            {"id": f"w-{index}", "brand": self.BRAND, "reference": reference}
            for index, reference in enumerate(["5990/1A", "5990-1A", "59901A"])
        ]
        detail_offers = [
            _detail_offer(
                watch_id=watch["id"],
                dealer_id=f"dealer-{index}",
                usd_price=180000 + index * 1000,
                condition="New" if index % 2 == 0 else "Used",
                dial="Grey",
            )
            for index, watch in enumerate(watches)
        ]

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.ilike.return_value = mock_table
        mock_table.range.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=watches)

        with (
            patch("database.get_client", return_value=mock_client),
            patch("database._query_table_in_id_chunks", return_value=detail_offers),
            patch("database._offer_from_business_dealer", return_value=True),
        ):
            trace = trace_brand_reference_lookup(self.BRAND, "5990/1A")

        assert search_count == 3
        assert trace["normalized_reference"] == "59901A"
        assert trace["lookup_tokens"] == ["5990", "1A"]
        assert trace["watch_count"] == 3
        assert trace["offer_count"] == 3

    def test_unpunctuated_reference_still_works(self) -> None:
        watches = [{"id": "w-126200", "brand": "Rolex", "reference": "126200"}]

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.ilike.return_value = mock_table
        mock_table.range.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=watches)

        with patch("database.get_client", return_value=mock_client):
            watch_ids = find_watch_ids_for_brand_reference("Rolex", "126200")

        assert watch_ids == ["w-126200"]


class TestFindWatchIdsForBrandReference:
    def test_matches_watches_across_dial_variants(self) -> None:
        watches = [
            {"id": "w-grey", "brand": "Patek Philippe", "reference": "5990/1A"},
            {"id": "w-blue", "brand": "Patek Philippe", "reference": "5990-1A"},
            {"id": "w-other", "brand": "Rolex", "reference": "5990/1A"},
        ]

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.ilike.return_value = mock_table
        mock_table.range.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=watches)

        with patch("database.get_client", return_value=mock_client):
            watch_ids = find_watch_ids_for_brand_reference("Patek Philippe", "5990/1A")

        assert watch_ids == ["w-grey", "w-blue"]

    def test_paginates_watch_lookup(self) -> None:
        first_batch = [
            {"id": f"w-{index}", "brand": "Patek Philippe", "reference": "5990/1A"}
            for index in range(1000)
        ]
        second_batch = [
            {"id": "w-final", "brand": "Patek Philippe", "reference": "5990/1A"},
        ]

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.ilike.return_value = mock_table
        mock_table.range.return_value = mock_table
        mock_table.execute.side_effect = [
            MagicMock(data=first_batch),
            MagicMock(data=second_batch),
        ]

        with patch("database.get_client", return_value=mock_client):
            watch_ids = find_watch_ids_for_brand_reference("Patek Philippe", "5990/1A")

        assert len(watch_ids) == 1001
        assert watch_ids[-1] == "w-final"
        assert mock_table.range.call_count == 2
