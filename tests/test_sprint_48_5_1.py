"""Tests for Sprint 48.5.1 watch detail offer source links."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app, build_offer_rows, normalize_watch_detail_offer
from dealer_intelligence import (
    attach_dealer_offer_source_urls,
    index_import_logs_by_summary_offer_id,
    load_offer_source_import_log_lookups,
)
from tests.conftest import ADMIN_USER


def _import_log(
    import_log_id: str = "log-1",
    *,
    message_id: str = "msg-1",
    summary: dict | None = None,
) -> dict:
    return {
        "id": import_log_id,
        "message_id": message_id,
        "watches_parsed": 1,
        "status": "success",
        "summary": summary or {},
    }


def _detail_offer(
    *,
    offer_id: str = "offer-1",
    watch_id: str,
    dealer_id: str,
    usd_price: int,
    condition: str | None,
    message_id: str | None = "msg-1",
    import_log_id: str | None = None,
    dial: str = "Blue",
) -> dict:
    message = {
        "received_at": "2026-06-01T12:00:00+00:00",
        "group_id": "g-1",
        "groups": {"name": "Group A"},
    }
    if message_id is not None:
        message["id"] = message_id
    offer = {
        "id": offer_id,
        "watch_id": watch_id,
        "dealer_id": dealer_id,
        "message_id": message_id,
        "usd_price": usd_price,
        "condition": condition,
        "original_price": usd_price,
        "original_currency": "USD",
        "card_date": "06/2026",
        "watches": {"dial": dial},
        "dealers": {"display_name": f"Dealer {dealer_id}", "phone_number": "+85290000001"},
        "messages": message,
    }
    if import_log_id is not None:
        offer["import_log_id"] = import_log_id
    return offer


WATCH = {
    "id": "w-5711-1r-a",
    "brand": "Patek Philippe",
    "reference": "5711/1R",
    "model": "Nautilus",
    "dial": "Brown",
    "bracelet": "Bracelet",
}
REFERENCE_DETAIL_URL = "/watch-reference?brand=Patek+Philippe&reference=5711%2F1R"


class TestOfferSourceResolutionHelpers:
    def test_index_import_logs_by_summary_offer_id(self) -> None:
        import_log = _import_log(
            "log-summary",
            summary={"rows": [{"offer_id": "offer-summary", "reference": "5711/1R"}]},
        )

        indexed = index_import_logs_by_summary_offer_id([import_log])

        assert indexed["offer-summary"]["id"] == "log-summary"

    @patch("database.get_import_logs_by_offer_ids", return_value={})
    @patch("database.get_import_logs_for_source_resolution")
    @patch("database.get_import_logs_by_message_ids")
    def test_load_offer_source_import_log_lookups_resolves_message_id(
        self,
        mock_by_message_ids: MagicMock,
        mock_for_source: MagicMock,
        _mock_by_offer_ids: MagicMock,
    ) -> None:
        mock_by_message_ids.return_value = {"msg-1": _import_log("log-message", message_id="msg-1")}
        mock_for_source.return_value = {"log-message": _import_log("log-message", message_id="msg-1")}

        offers = [normalize_watch_detail_offer(_detail_offer(
            watch_id="w-1",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
        ))]

        by_message_id, by_id, by_offer_id = load_offer_source_import_log_lookups(offers)

        assert by_message_id["msg-1"]["id"] == "log-message"
        assert by_id["log-message"]["id"] == "log-message"
        assert by_offer_id == {}

    def test_attach_source_url_from_direct_import_log_id(self) -> None:
        offer = normalize_watch_detail_offer(_detail_offer(
            watch_id="w-1",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
            message_id=None,
            import_log_id="log-direct",
        ))

        enriched = attach_dealer_offer_source_urls(
            [offer],
            {},
            user=ADMIN_USER,
            import_logs_by_id={"log-direct": _import_log("log-direct", message_id="msg-direct")},
        )

        assert enriched[0]["source_url"] == "/activity/log-direct"

    def test_attach_source_url_from_summary_offer_id(self) -> None:
        offer = normalize_watch_detail_offer(_detail_offer(
            offer_id="offer-summary",
            watch_id="w-1",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
            message_id=None,
        ))
        import_log = _import_log(
            "log-summary",
            message_id="msg-summary",
            summary={"rows": [{"offer_id": "offer-summary"}]},
        )

        enriched = attach_dealer_offer_source_urls(
            [offer],
            {},
            user=ADMIN_USER,
            import_logs_by_offer_id={"offer-summary": import_log},
            import_logs_by_id={"log-summary": import_log},
        )

        assert enriched[0]["source_url"] == "/activity/log-summary"

    def test_attach_source_url_without_source(self) -> None:
        offer = normalize_watch_detail_offer(_detail_offer(
            watch_id="w-1",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
            message_id=None,
        ))

        enriched = attach_dealer_offer_source_urls([offer], {}, user=ADMIN_USER)

        assert enriched[0]["source_url"] is None


class TestWatchDetailSourceLinks:
    def test_build_offer_rows_includes_source_url(self) -> None:
        offer = normalize_watch_detail_offer(_detail_offer(
            watch_id="w-5711-1r-a",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
        ))
        enriched = attach_dealer_offer_source_urls(
            [offer],
            {"msg-1": _import_log("log-offer-1")},
            user=ADMIN_USER,
        )
        rows = build_offer_rows(enriched)

        assert rows[0]["source_url"] == "/activity/log-offer-1"

    def test_build_offer_rows_without_source_url(self) -> None:
        offer = normalize_watch_detail_offer(_detail_offer(
            watch_id="w-5711-1r-a",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
            message_id=None,
        ))
        rows = build_offer_rows([offer])

        assert rows[0]["source_url"] is None

    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_shows_view_original_for_linked_offers(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
    ) -> None:
        mock_get_offers.return_value = [
            _detail_offer(
                offer_id="offer-linked",
                watch_id="w-5711-1r-a",
                dealer_id="dealer-1",
                usd_price=180000,
                condition="New",
                message_id="msg-linked",
            ),
            _detail_offer(
                offer_id="offer-plain",
                watch_id="w-5711-1r-b",
                dealer_id="dealer-2",
                usd_price=185000,
                condition="Used",
                message_id=None,
            ),
        ]
        mock_load_lookups.return_value = (
            {"msg-linked": _import_log("log-linked", message_id="msg-linked")},
            {"log-linked": _import_log("log-linked", message_id="msg-linked")},
            {},
        )

        client = TestClient(app)
        response = client.get(REFERENCE_DETAIL_URL)

        assert response.status_code == 200
        assert 'href="/activity/log-linked"' in response.text
        assert "View original" in response.text
        assert response.text.count("View original") == 1
        assert "—" in response.text

    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_resolves_source_from_offer_row_message_id_without_messages_join(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
    ) -> None:
        raw_offer = _detail_offer(
            offer_id="offer-row-message",
            watch_id="w-5711-1r-a",
            dealer_id="dealer-1",
            usd_price=180000,
            condition="New",
            message_id="msg-row",
        )
        raw_offer["messages"] = None
        mock_get_offers.return_value = [raw_offer]
        mock_load_lookups.return_value = (
            {"msg-row": _import_log("log-row", message_id="msg-row")},
            {"log-row": _import_log("log-row", message_id="msg-row")},
            {},
        )

        client = TestClient(app)
        response = client.get(REFERENCE_DETAIL_URL)

        assert response.status_code == 200
        assert 'href="/activity/log-row"' in response.text
        normalized = normalize_watch_detail_offer(raw_offer)
        mock_load_lookups.assert_called_once()
        assert mock_load_lookups.call_args.args[0][0]["message_id"] == "msg-row"

    @patch("app.load_offer_source_import_log_lookups")
    @patch("app.get_active_offers_for_brand_reference")
    def test_watch_detail_condition_filter_keeps_source_links(
        self,
        mock_get_offers: MagicMock,
        mock_load_lookups: MagicMock,
    ) -> None:
        mock_get_offers.return_value = [
            _detail_offer(
                offer_id="offer-new",
                watch_id="w-5711-1r-a",
                dealer_id="dealer-1",
                usd_price=180000,
                condition="New",
                message_id="msg-new",
            ),
            _detail_offer(
                offer_id="offer-used",
                watch_id="w-5711-1r-b",
                dealer_id="dealer-2",
                usd_price=185000,
                condition="Used",
                message_id="msg-used",
            ),
        ]
        mock_load_lookups.return_value = (
            {
                "msg-new": _import_log("log-new", message_id="msg-new"),
                "msg-used": _import_log("log-used", message_id="msg-used"),
            },
            {
                "log-new": _import_log("log-new", message_id="msg-new"),
                "log-used": _import_log("log-used", message_id="msg-used"),
            },
            {},
        )

        client = TestClient(app)
        response = client.get(f"{REFERENCE_DETAIL_URL}&condition=new")

        assert response.status_code == 200
        assert 'href="/activity/log-new"' in response.text
        assert 'href="/activity/log-used"' not in response.text
        assert "View original" in response.text
        assert ">1<" in response.text.replace(" ", "")
