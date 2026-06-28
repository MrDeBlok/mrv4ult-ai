"""Tests for Sprint 29 client sourcing workspace."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from client_sourcing import (
    build_client_sourcing_dashboard,
    build_matching_offer_row,
    build_matching_offer_rows,
    build_sourcing_offer_payload,
    count_open_requests,
    find_best_sourcing_match_for_offer,
    find_matching_offers_for_client,
    watch_display_label,
)
from contact_classification import CONTACT_TYPE_CLIENT
from request_matching import evaluate_sourcing_match, match_badge_class


def _request(**kwargs) -> dict:
    base = {
        "id": "req-1",
        "status": "open",
        "client_name": "Anna Buyer",
        "currency": "USD",
    }
    base.update(kwargs)
    return base


def _offer_row(**kwargs) -> dict:
    base = {
        "id": "offer-1",
        "dealer_id": "dealer-1",
        "watch_id": "watch-1",
        "original_price": 45000,
        "original_currency": "USD",
        "usd_price": 45000,
        "condition": "new",
        "watches": {
            "brand": "Rolex",
            "reference": "116508",
            "model": "Daytona",
            "dial": "Black",
        },
        "dealers": {"id": "dealer-1", "display_name": "Gold Source"},
        "messages": {"received_at": "2026-06-27T10:00:00+00:00"},
    }
    base.update(kwargs)
    return base


class TestEvaluateSourcingMatch:
    def test_strong_match_gets_excellent_badge(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "original_price": 45000,
            "original_currency": "USD",
            "dial": "Black",
            "condition": "new",
        }
        request = _request(
            brand="Rolex",
            reference="116508",
            max_price=50000,
            dial="Black",
            condition="new",
        )

        match = evaluate_sourcing_match(offer, request)

        assert match is not None
        assert match["match_badge"] == "Excellent Match"
        assert match["match_badge_class"] == "success"
        assert match["match_score"] >= 100
        assert match["budget_exceeded"] is False

    def test_medium_match_gets_good_badge(self) -> None:
        offer = {
            "brand": "Rolex",
            "model": "GMT-Master II",
            "nickname": "Pepsi",
            "original_price": 18000,
            "original_currency": "USD",
        }
        request = _request(
            brand="Rolex",
            alias="Pepsi",
            max_price=20000,
        )

        match = evaluate_sourcing_match(offer, request)

        assert match is not None
        assert match["match_badge"] == "Good Match"
        assert match["match_badge_class"] == "primary"

    def test_budget_exceeded_still_returns_match(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "original_price": 60000,
            "original_currency": "USD",
        }
        request = _request(
            brand="Rolex",
            reference="116508",
            max_price=50000,
        )

        match = evaluate_sourcing_match(offer, request)

        assert match is not None
        assert match["match_badge"] == "Budget Exceeded"
        assert match["match_badge_class"] == "warning"
        assert match["budget_exceeded"] is True

    def test_no_match_when_reference_differs(self) -> None:
        offer = {
            "brand": "Rolex",
            "reference": "116508",
            "original_price": 45000,
            "original_currency": "USD",
        }
        request = _request(
            brand="Rolex",
            reference="126200",
            max_price=50000,
        )

        assert evaluate_sourcing_match(offer, request) is None


class TestClientSourcingHelpers:
    def test_build_sourcing_offer_payload_flattens_watch_fields(self) -> None:
        payload = build_sourcing_offer_payload(_offer_row())

        assert payload["brand"] == "Rolex"
        assert payload["reference"] == "116508"
        assert payload["usd_price"] == 45000

    def test_build_sourcing_offer_payload_enriches_nickname_without_db_column(self) -> None:
        payload = build_sourcing_offer_payload(
            _offer_row(
                watches={
                    "brand": "Rolex",
                    "model": "GMT-Master II Pepsi",
                    "reference": None,
                    "dial": "Blue",
                }
            )
        )

        assert payload["nickname"] == "Pepsi"
        assert payload["model_alias"]["alias"] == "pepsi"

    def test_watch_display_label_prefers_reference(self) -> None:
        label = watch_display_label({"brand": "Rolex", "reference": "116508", "model": "Daytona"})

        assert label == "Rolex · 116508"

    def test_count_open_requests_ignores_closed(self) -> None:
        assert count_open_requests([_request(status="open"), _request(id="req-2", status="closed")]) == 1

    def test_find_best_sourcing_match_picks_highest_score(self) -> None:
        offer_payload = build_sourcing_offer_payload(_offer_row())
        requests = [
            _request(id="req-1", brand="Rolex", reference="116508", max_price=50000),
            _request(id="req-2", brand="Rolex", alias="Pepsi", max_price=50000),
        ]

        match, request = find_best_sourcing_match_for_offer(offer_payload, requests)

        assert match is not None
        assert request is not None
        assert request["id"] == "req-1"
        assert match["match_badge"] == "Excellent Match"

    def test_find_matching_offers_sorts_by_match_score(self) -> None:
        requests = [_request(brand="Rolex", reference="116508", max_price=50000)]
        offers = [
            _offer_row(
                id="offer-low",
                watches={"brand": "Rolex", "reference": "126200", "model": "Datejust"},
                original_price=40000,
                usd_price=40000,
            ),
            _offer_row(
                id="offer-high",
                watches={"brand": "Rolex", "reference": "116508", "model": "Daytona"},
                original_price=45000,
                usd_price=45000,
            ),
        ]

        matches = find_matching_offers_for_client(requests=requests, offers=offers)

        assert len(matches) == 1
        assert matches[0]["offer_id"] == "offer-high"

    def test_build_matching_offer_row_includes_actions_fields(self) -> None:
        matches = find_matching_offers_for_client(
            requests=[_request(brand="Rolex", reference="116508", max_price=50000)],
            offers=[_offer_row()],
        )
        row = build_matching_offer_row(matches[0])

        assert row["dealer_name"] == "Gold Source"
        assert row["dealer_id"] == "dealer-1"
        assert row["watch_id"] == "watch-1"
        assert row["match_badge"] == "Excellent Match"
        assert row["asking_price"] == "$45,000"
        assert row["potential_profit"] == "$5,000"

    def test_build_matching_offer_rows_strips_internal_fields(self) -> None:
        matches = find_matching_offers_for_client(
            requests=[_request(brand="Rolex", reference="116508", max_price=50000)],
            offers=[_offer_row()],
        )
        rows = build_matching_offer_rows(matches)

        assert "_offer_date_raw" not in rows[0]

    def test_build_client_sourcing_dashboard_summarizes_offers(self) -> None:
        requests = [
            _request(status="open", brand="Rolex", reference="116508", max_price=50000),
            _request(id="req-2", status="closed"),
        ]
        matches = find_matching_offers_for_client(
            requests=requests,
            offers=[_offer_row()],
        )
        dashboard = build_client_sourcing_dashboard(requests=requests, matching_offers=matches)

        assert dashboard["open_requests"] == 1
        assert dashboard["matching_offers_count"] == 1
        assert dashboard["best_potential_profit"] == "$5,000"
        assert dashboard["latest_matching_offer"] == "Rolex · 116508"

    def test_match_badge_class_maps_known_badges(self) -> None:
        assert match_badge_class("Excellent Match") == "success"
        assert match_badge_class("Good Match") == "primary"
        assert match_badge_class("Budget Exceeded") == "warning"

    def test_find_matching_offers_includes_budget_exceeded_offer(self) -> None:
        requests = [_request(brand="Rolex", reference="116508", max_price=40000)]
        offers = [_offer_row(original_price=60000, usd_price=60000)]

        matches = find_matching_offers_for_client(requests=requests, offers=offers)

        assert len(matches) == 1
        assert matches[0]["match"]["match_badge"] == "Budget Exceeded"

    def test_build_client_sourcing_dashboard_empty_when_no_matches(self) -> None:
        dashboard = build_client_sourcing_dashboard(requests=[], matching_offers=[])

        assert dashboard["open_requests"] == 0
        assert dashboard["matching_offers_count"] == 0
        assert dashboard["best_potential_profit"] == "—"
        assert dashboard["latest_matching_offer"] == "—"


class TestClientDetailSourcingUI:
    @patch("app.build_matching_offer_rows")
    @patch("app.build_client_sourcing_dashboard")
    @patch("app.find_matching_offers_for_client", return_value=[])
    @patch("app.list_active_sourcing_offers", return_value=[])
    @patch("app.build_client_match_rows", return_value=[])
    @patch("app.list_client_match_history", return_value=[])
    @patch("app.build_client_request_rows", return_value=[])
    @patch("app.list_requests_for_client", return_value=[_request()])
    @patch("app.get_client_profile", return_value={"status": "active"})
    @patch("app.get_client_by_id")
    def test_client_detail_renders_sourcing_sections(
        self,
        mock_get_client: MagicMock,
        mock_get_profile: MagicMock,
        mock_list_requests: MagicMock,
        mock_build_request_rows: MagicMock,
        mock_list_match_history: MagicMock,
        mock_build_match_rows: MagicMock,
        mock_list_offers: MagicMock,
        mock_find_matches: MagicMock,
        mock_build_dashboard: MagicMock,
        mock_build_offer_rows: MagicMock,
    ) -> None:
        mock_get_client.return_value = {
            "id": "client-1",
            "display_name": "Anna Buyer",
            "contact_type": CONTACT_TYPE_CLIENT,
            "created_at": "2026-06-01T10:00:00+00:00",
        }
        mock_build_dashboard.return_value = {
            "open_requests": 1,
            "matching_offers_count": 1,
            "best_potential_profit": "$5,000",
            "latest_matching_offer": "Rolex · 116508",
        }
        mock_build_offer_rows.return_value = [
            {
                "dealer_id": "dealer-1",
                "dealer_name": "Gold Source",
                "watch_id": "watch-1",
                "watch_label": "Rolex · 116508",
                "reference": "116508",
                "asking_price": "$45,000",
                "match_score": 110,
                "match_badge": "Excellent Match",
                "match_badge_class": "success",
                "potential_profit": "$5,000",
                "offer_date": "2026-06-27 10:00",
            }
        ]

        client = TestClient(app)
        response = client.get("/clients/client-1")

        assert response.status_code == 200
        assert "Matching Offers" in response.text
        assert "Open requests" in response.text
        assert "Best potential profit" in response.text
        assert "Excellent Match" in response.text
        assert 'href="/watch/watch-1"' in response.text
        assert 'href="/dealers/dealer-1"' in response.text
        assert "View watch" in response.text
        assert "Open Dealer" in response.text
        assert "Open Watch" in response.text


class TestListActiveSourcingOffersSchema:
    @patch("database.get_client")
    def test_select_uses_production_watch_columns(self, mock_get_client: MagicMock) -> None:
        mock_table = MagicMock()
        mock_get_client.return_value.table.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])

        from database import list_active_sourcing_offers

        list_active_sourcing_offers()

        select_query = mock_table.select.call_args[0][0]
        assert "watches(brand, reference, model, dial, bracelet)" in select_query
        assert "nickname" not in select_query
        assert "model_alias" not in select_query
