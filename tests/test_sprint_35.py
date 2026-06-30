"""Tests for Sprint 35 Market Requests page."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from contact_classification import CONTACT_TYPE_DEALER
from dealer_intelligence import build_dealer_list_rows
from ingest import ingest_message
from market_requests import (
    build_market_request_row,
    dedupe_market_request_rows,
    filter_market_request_imports,
    filter_market_request_rows,
    is_market_request_import,
    load_market_request_detail,
    load_market_request_rows,
    market_request_import_logs_for_user,
    resolve_market_request_contact,
)
from tests.conftest import ADMIN_USER, TRADER_ONE, TRADER_TWO


def _market_request_log(
    *,
    import_id: str,
    group_name: str = "HK Dealers",
    brand: str = "Rolex",
    reference: str = "126500LN",
    model: str = "Daytona",
    nickname: str | None = None,
    price: int | None = 25000,
    currency: str = "USD",
    owner_user_id: str | None = None,
    message_id: str = "msg-1",
    import_time: str = "2026-06-27T12:00:00+00:00",
    dealer_whatsapp: str = "+85291234567",
    dealer_alias: str | None = "HK Dealer",
    raw_message: str = "WTB Rolex Daytona 126500LN budget 25k",
) -> dict:
    watch: dict = {
        "brand": brand,
        "reference": reference,
        "model": model,
        "price": price,
        "currency": currency,
    }
    if nickname:
        watch["nickname"] = nickname

    import_log = {
        "id": import_id,
        "status": "request_intent",
        "watches_parsed": 0,
        "new_offers": 0,
        "message_id": message_id,
        "group_name": group_name,
        "dealer_whatsapp": dealer_whatsapp,
        "dealer_alias": dealer_alias,
        "import_time": import_time,
        "summary": {
            "parsed_watches": [watch],
            "import_classification": "request_intent",
            "message_text": raw_message,
        },
    }
    if owner_user_id:
        import_log["imported_by_user_id"] = owner_user_id
    return import_log


class TestMarketRequestRules:
    def test_is_market_request_import(self) -> None:
        assert is_market_request_import(_market_request_log(import_id="req-1")) is True
        assert is_market_request_import({"id": "offer-1", "status": "success"}) is False

    def test_filter_market_request_imports(self) -> None:
        logs = [
            _market_request_log(import_id="req-1"),
            {"id": "offer-1", "status": "success"},
            _market_request_log(import_id="req-2", group_name="EU Dealers"),
        ]

        filtered = filter_market_request_imports(logs)

        assert [row["id"] for row in filtered] == ["req-1", "req-2"]

    def test_build_market_request_row_includes_watch_fields(self) -> None:
        row = build_market_request_row(
            _market_request_log(import_id="req-1", nickname="Panda"),
            {"raw_text": "WTB Rolex Daytona 126500LN budget 25k"},
        )

        assert row["brand"] == "Rolex"
        assert row["model"] == "Daytona"
        assert row["reference"] == "126500LN"
        assert row["nickname"] == "Panda"
        assert row["budget"] == "$25,000"
        assert row["group_name"] == "HK Dealers"
        assert row["source_contact"] == "HK Dealer"
        assert row["source_whatsapp"] == "+85291234567"
        assert row["detail_url"] == "/market-requests/req-1"
        assert "WTB Rolex Daytona" in row["message_preview"]

    def test_resolve_market_request_contact_uses_sender_details(self) -> None:
        contact = resolve_market_request_contact(
            _market_request_log(import_id="req-1"),
        )

        assert contact["name"] == "HK Dealer"
        assert contact["whatsapp"] == "+85291234567"
        assert contact["redacted"] is False

    def test_dedupe_market_request_rows_collapses_cross_group_duplicates(self) -> None:
        shared_message = {"raw_text": "WTB Rolex Daytona 126500LN budget 25k"}
        rows = [
            build_market_request_row(
                _market_request_log(
                    import_id="older",
                    group_name="HK Dealers",
                    import_time="2026-06-27T10:00:00+00:00",
                ),
                shared_message,
            ),
            build_market_request_row(
                _market_request_log(
                    import_id="newer",
                    group_name="EU Dealers",
                    import_time="2026-06-27T13:00:00+00:00",
                ),
                shared_message,
            ),
        ]

        deduped = dedupe_market_request_rows(rows)

        assert len(deduped) == 1
        assert deduped[0]["id"] == "newer"
        assert deduped[0]["groups_seen_label"] == "Seen in 2 groups"
        assert deduped[0]["detail_url"] == "/market-requests/newer"

    def test_filter_market_request_rows_by_brand_reference_and_group(self) -> None:
        rows = [
            build_market_request_row(_market_request_log(import_id="1", brand="Rolex", reference="126500LN")),
            build_market_request_row(
                _market_request_log(import_id="2", brand="Audemars Piguet", reference="15510", group_name="EU Dealers"),
            ),
        ]

        assert [row["id"] for row in filter_market_request_rows(rows, brand="Rolex")] == ["1"]
        assert [row["id"] for row in filter_market_request_rows(rows, reference="15510")] == ["2"]
        assert [row["id"] for row in filter_market_request_rows(rows, group="EU")] == ["2"]

    @patch("market_requests.get_message_by_id", return_value=None)
    @patch("market_requests.list_import_logs")
    def test_market_request_visibility_is_user_scoped(
        self,
        mock_list_import_logs: MagicMock,
        _mock_get_message: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            _market_request_log(import_id="team", owner_user_id=TRADER_ONE["id"]),
            _market_request_log(import_id="private", owner_user_id=TRADER_TWO["id"]),
        ]

        trader_rows = load_market_request_rows(TRADER_ONE)
        admin_rows = load_market_request_rows(ADMIN_USER)

        assert [row["id"] for row in trader_rows] == ["team"]
        assert {row["id"] for row in admin_rows} == {"team", "private"}


class TestMarketRequestRoutes:
    @pytest.mark.no_auto_login
    def test_market_requests_requires_login(self) -> None:
        client = TestClient(app)
        response = client.get("/market-requests", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    @patch("app.load_market_request_rows")
    def test_admin_can_access_market_requests(self, mock_load_rows: MagicMock) -> None:
        mock_load_rows.return_value = [
            build_market_request_row(_market_request_log(import_id="req-1")),
        ]

        client = TestClient(app)
        response = client.get("/market-requests")

        assert response.status_code == 200
        assert "Market Requests" in response.text
        assert "126500LN" in response.text

    @pytest.mark.no_auto_login
    @patch("app.load_market_request_rows")
    def test_trader_can_access_market_requests(
        self,
        mock_load_rows: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_load_rows.return_value = [
            build_market_request_row(_market_request_log(import_id="req-1")),
        ]
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get("/market-requests")

        assert response.status_code == 200
        assert "Market Requests" in response.text

    @patch("market_requests.get_message_by_id", return_value={"raw_text": "WTB Rolex Daytona"})
    @patch("market_requests.list_import_logs")
    def test_request_intent_imports_appear_on_page(
        self,
        mock_list_import_logs: MagicMock,
        _mock_get_message: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            _market_request_log(import_id="req-1"),
            {"id": "offer-1", "status": "success", "watches_parsed": 1, "new_offers": 1, "summary": {}},
        ]

        client = TestClient(app)
        response = client.get("/market-requests")

        assert response.status_code == 200
        assert "126500LN" in response.text
        assert "offer-1" not in response.text

    @patch("market_requests.get_message_by_id", return_value=None)
    @patch("market_requests.list_import_logs")
    def test_market_request_filters_work_in_route(
        self,
        mock_list_import_logs: MagicMock,
        _mock_get_message: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            _market_request_log(import_id="req-1", brand="Rolex", group_name="HK Dealers"),
            _market_request_log(
                import_id="req-2",
                brand="Audemars Piguet",
                reference="15510",
                group_name="EU Dealers",
            ),
        ]

        client = TestClient(app)
        response = client.get("/market-requests?brand=Rolex&group=HK")

        assert response.status_code == 200
        assert "126500LN" in response.text
        assert "15510" not in response.text

    def test_navbar_contains_market_requests_link(self) -> None:
        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'href="/market-requests"' in response.text
        assert ">Market Requests<" in response.text


class TestMarketRequestSideEffects:
    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_request_intent_messages_do_not_create_offers(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "WTB Rolex Daytona",
            group_name="Requests",
            dealer_whatsapp="+31612345678",
        )

        assert summary["status"] == "request_intent"
        mock_insert_offer.assert_not_called()
        mock_process_matches.assert_not_called()

    def test_request_intent_imports_are_not_included_in_dealer_intelligence(self) -> None:
        dealers = [{"id": "dealer-1", "display_name": "HK Dealer"}]
        offers = [
            {
                "dealer_id": "dealer-1",
                "watch_id": "watch-1",
                "status": "active",
                "usd_price": 25000,
                "messages": {"received_at": "2026-06-27T12:00:00+00:00"},
            }
        ]

        rows = build_dealer_list_rows(dealers, offers)

        assert len(rows) == 1
        assert rows[0]["total_offers"] == 1

        empty_rows = build_dealer_list_rows(dealers, [])
        assert empty_rows[0]["total_offers"] == 0

    @patch("market_requests.list_import_logs")
    def test_non_request_imports_do_not_appear_in_market_request_loader(
        self,
        mock_list_import_logs: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            {"id": "success-1", "status": "success", "watches_parsed": 1, "new_offers": 1, "summary": {}},
            {"id": "noise-1", "status": "noise", "summary": {}},
        ]

        rows = market_request_import_logs_for_user(ADMIN_USER)

        assert rows == []


class TestMarketRequest351Routes:
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_duplicate_requests_collapse_into_one_list_row(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        shared_message = "WTB Rolex Daytona 126500LN budget 25k"
        mock_list_import_logs.return_value = [
            _market_request_log(
                import_id="11111111-1111-4111-8111-111111111111",
                group_name="HK Dealers",
                import_time="2026-06-27T10:00:00+00:00",
                message_id="msg-1",
            ),
            _market_request_log(
                import_id="22222222-2222-4222-8222-222222222222",
                group_name="EU Dealers",
                import_time="2026-06-27T13:00:00+00:00",
                message_id="msg-2",
            ),
        ]
        mock_get_message.side_effect = lambda message_id: {
            "msg-1": {"raw_text": shared_message},
            "msg-2": {"raw_text": shared_message},
        }[message_id]

        client = TestClient(app)
        response = client.get("/market-requests")

        assert response.status_code == 200
        assert response.text.count('class="activity-row"') == 1
        assert "Seen in 2 groups" in response.text
        assert 'data-href="/market-requests/22222222-2222-4222-8222-222222222222"' in response.text

    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_list_row_links_to_detail_page(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        import_id = "11111111-1111-4111-8111-111111111111"
        mock_list_import_logs.return_value = [_market_request_log(import_id=import_id)]
        mock_get_message.return_value = {"raw_text": "WTB Rolex Daytona 126500LN budget 25k"}

        client = TestClient(app)
        response = client.get("/market-requests")

        assert response.status_code == 200
        assert f'data-href="/market-requests/{import_id}"' in response.text

    @patch("market_requests.get_import_log")
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_detail_page_shows_full_message_and_contact(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
    ) -> None:
        import_id = "11111111-1111-4111-8111-111111111111"
        import_log = _market_request_log(import_id=import_id)
        mock_get_import_log.return_value = import_log
        mock_list_import_logs.return_value = [import_log]
        mock_get_message.return_value = {
            "raw_text": "WTB Rolex Daytona 126500LN budget 25k please ping me",
        }

        client = TestClient(app)
        response = client.get(f"/market-requests/{import_id}")

        assert response.status_code == 200
        assert "WTB Rolex Daytona 126500LN budget 25k please ping me" in response.text
        assert "HK Dealer" in response.text
        assert "+85291234567" in response.text
        assert 'href="/activity/' in response.text

    @pytest.mark.no_auto_login
    @patch("market_requests.get_import_log")
    @patch("market_requests.list_import_logs")
    def test_unauthorized_trader_cannot_open_hidden_request(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_import_log: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        hidden = _market_request_log(
            import_id="33333333-3333-4333-8333-333333333333",
            owner_user_id=TRADER_TWO["id"],
        )
        mock_get_import_log.return_value = hidden
        mock_list_import_logs.return_value = [hidden]
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get(f"/market-requests/{hidden['id']}")

        assert response.status_code == 404

    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_filters_still_work_after_deduping(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = [
            _market_request_log(
                import_id="req-rolex-hk",
                brand="Rolex",
                group_name="HK Dealers",
                message_id="msg-rolex",
            ),
            _market_request_log(
                import_id="req-rolex-eu",
                brand="Rolex",
                group_name="EU Dealers",
                message_id="msg-rolex",
                import_time="2026-06-27T11:00:00+00:00",
            ),
            _market_request_log(
                import_id="req-ap",
                brand="Audemars Piguet",
                reference="15510",
                group_name="EU Dealers",
                message_id="msg-ap",
            ),
        ]
        mock_get_message.side_effect = lambda message_id: {
            "msg-rolex": {"raw_text": "WTB Rolex Daytona 126500LN"},
            "msg-ap": {"raw_text": "Looking for AP 15510"},
        }[message_id]

        client = TestClient(app)
        response = client.get("/market-requests?brand=Rolex&group=EU")

        assert response.status_code == 200
        assert "126500LN" in response.text
        assert "15510" not in response.text
        assert "Seen in 1 groups" not in response.text

    @patch("market_requests.get_import_log")
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_load_market_request_detail_returns_none_for_hidden_request(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
    ) -> None:
        hidden = _market_request_log(
            import_id="33333333-3333-4333-8333-333333333333",
            owner_user_id=TRADER_TWO["id"],
        )
        mock_get_import_log.return_value = hidden
        mock_list_import_logs.return_value = [hidden]
        mock_get_message.return_value = {"raw_text": "WTB Rolex Daytona"}

        assert load_market_request_detail(TRADER_ONE, hidden["id"]) is None


def _matching_offer(
    *,
    offer_id: str = "offer-1",
    watch_id: str = "watch-1",
    dealer_id: str = "dealer-1",
    brand: str = "Rolex",
    reference: str = "126500LN",
    usd_price: int = 25000,
    received_at: str = "2026-06-27T12:00:00+00:00",
    dealer_name: str = "HK Dealer",
    country: str = "Hong Kong",
    contact_type: str = "dealer",
    owner_user_id: str | None = None,
) -> dict:
    dealer: dict = {
        "id": dealer_id,
        "display_name": dealer_name,
        "phone_number": "+85291234567",
        "whatsapp_id": "85291234567",
        "contact_type": contact_type,
        "country": country,
    }
    if owner_user_id:
        dealer["owner_user_id"] = owner_user_id
        dealer["classified_by_user_id"] = owner_user_id
    return {
        "id": offer_id,
        "dealer_id": dealer_id,
        "watch_id": watch_id,
        "original_price": usd_price,
        "original_currency": "USD",
        "usd_price": usd_price,
        "condition": "new",
        "watches": {
            "brand": brand,
            "reference": reference,
            "model": "Daytona",
        },
        "dealers": dealer,
        "messages": {
            "received_at": received_at,
            "groups": {"name": "HK Dealers", "country": country},
        },
    }


class TestMarketRequest352Matching:
    def test_reference_match_finds_offer(self) -> None:
        from market_request_matching import find_matching_offers_for_market_request, offer_matches_market_request

        import_log = _market_request_log(import_id="req-1", reference="126500LN")
        offer = _matching_offer(reference="126500LN", usd_price=24000)

        assert offer_matches_market_request(
            {"brand": "Rolex", "reference": "126500LN", "model": "Daytona", "nickname": None},
            offer,
        ) is True

        matches = find_matching_offers_for_market_request(
            ADMIN_USER,
            import_log,
            offers=[offer],
        )

        assert len(matches) == 1
        assert matches[0]["dealer_name"] == "HK Dealer"
        assert matches[0]["asking_price"] == "$24,000"
        assert matches[0]["offer_url"] == "/watch/watch-1"

    def test_alias_match_uses_nickname_identification(self) -> None:
        from market_request_matching import find_matching_offers_for_market_request, offer_matches_market_request

        import_log = _market_request_log(
            import_id="req-pepsi",
            reference="",
            nickname="Pepsi",
            raw_message="WTB Rolex Pepsi",
        )
        import_log["summary"]["parsed_watches"][0]["reference"] = None
        offer = _matching_offer(reference="126710BLRO", brand="Rolex")

        criteria = {
            "brand": "Rolex",
            "reference": None,
            "model": "GMT-Master II",
            "nickname": "Pepsi",
        }
        assert offer_matches_market_request(criteria, offer) is True

        matches = find_matching_offers_for_market_request(
            ADMIN_USER,
            import_log,
            offers=[offer],
        )
        assert len(matches) == 1

    def test_no_matches_returns_empty_list(self) -> None:
        from market_request_matching import find_matching_offers_for_market_request

        import_log = _market_request_log(import_id="req-1", reference="126500LN")
        other_offer = _matching_offer(reference="116500LN")

        matches = find_matching_offers_for_market_request(
            ADMIN_USER,
            import_log,
            offers=[other_offer],
        )

        assert matches == []

    def test_matching_offers_sorted_newest_then_lowest_price(self) -> None:
        from market_request_matching import find_matching_offers_for_market_request

        import_log = _market_request_log(import_id="req-1", reference="126500LN")
        offers = [
            _matching_offer(
                offer_id="older-high",
                usd_price=30000,
                received_at="2026-06-26T12:00:00+00:00",
            ),
            _matching_offer(
                offer_id="newer-high",
                usd_price=28000,
                received_at="2026-06-27T14:00:00+00:00",
            ),
            _matching_offer(
                offer_id="newer-low",
                usd_price=24000,
                received_at="2026-06-27T14:00:00+00:00",
            ),
        ]

        matches = find_matching_offers_for_market_request(
            ADMIN_USER,
            import_log,
            offers=offers,
        )

        assert [row["offer_id"] for row in matches] == ["newer-low", "newer-high", "older-high"]

    def test_trader_cannot_see_matching_offers_from_hidden_dealer(self) -> None:
        from market_request_matching import find_matching_offers_for_market_request

        import_log = _market_request_log(import_id="req-1", reference="126500LN")

        hidden_dealer_offer = _matching_offer(
            dealer_id="dealer-hidden",
            contact_type="removed",
            owner_user_id=TRADER_TWO["id"],
        )
        visible_offer = _matching_offer(dealer_id="dealer-visible")

        trader_matches = find_matching_offers_for_market_request(
            TRADER_ONE,
            import_log,
            offers=[hidden_dealer_offer, visible_offer],
        )
        admin_matches = find_matching_offers_for_market_request(
            ADMIN_USER,
            import_log,
            offers=[hidden_dealer_offer, visible_offer],
        )

        assert len(trader_matches) == 1
        assert trader_matches[0]["dealer_id"] == "dealer-visible"
        assert len(admin_matches) == 2

    @patch("market_requests.build_market_request_opportunity_bundle")
    @patch("market_requests.get_import_log")
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_detail_page_renders_matching_offers_section(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
        mock_opportunity_bundle: MagicMock,
    ) -> None:
        import_id = "11111111-1111-4111-8111-111111111111"
        import_log = _market_request_log(import_id=import_id)
        mock_get_import_log.return_value = import_log
        mock_list_import_logs.return_value = [import_log]
        mock_get_message.return_value = {"raw_text": "WTB Rolex Daytona 126500LN"}
        mock_opportunity_bundle.return_value = (
            [
                {
                    "offer_id": "offer-1",
                    "watch_id": "watch-1",
                    "dealer_id": "dealer-1",
                    "dealer_name": "HK Dealer",
                    "asking_price": "$24,000",
                    "net_price": "$24,000",
                    "retail_price": "—",
                    "condition": "New",
                    "country": "Hong Kong",
                    "import_date": "2026-06-27 12:00",
                    "last_seen": "2026-06-27 12:00",
                    "offer_url": "/watch/watch-1",
                }
            ],
            {
                "has_opportunities": True,
                "empty_message": None,
                "opportunity_score": 93,
                "confidence_label": "Excellent",
                "potential_spread": "$1,000",
                "reasons": [],
                "recommended_action": "Contact dealer immediately",
                "best_match": None,
            },
        )

        client = TestClient(app)
        response = client.get(f"/market-requests/{import_id}")

        assert response.status_code == 200
        assert "Matching Offers" in response.text
        assert "HK Dealer" in response.text
        assert 'href="/watch/watch-1"' in response.text

    @patch("market_requests.build_market_request_opportunity_bundle")
    @patch("market_requests.get_import_log")
    @patch("market_requests.get_message_by_id")
    @patch("market_requests.list_import_logs")
    def test_detail_page_shows_no_matches_message(
        self,
        mock_list_import_logs: MagicMock,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
        mock_opportunity_bundle: MagicMock,
    ) -> None:
        import_id = "11111111-1111-4111-8111-111111111111"
        import_log = _market_request_log(import_id=import_id)
        mock_get_import_log.return_value = import_log
        mock_list_import_logs.return_value = [import_log]
        mock_get_message.return_value = {"raw_text": "WTB Rolex Daytona 126500LN"}
        mock_opportunity_bundle.return_value = (
            [],
            {
                "has_opportunities": False,
                "empty_message": "No opportunity found yet.",
                "opportunity_score": None,
                "confidence_label": None,
                "potential_spread": None,
                "reasons": [],
                "recommended_action": None,
                "best_match": None,
            },
        )

        client = TestClient(app)
        response = client.get(f"/market-requests/{import_id}")

        assert response.status_code == 200
        assert "No matching offers found." in response.text
