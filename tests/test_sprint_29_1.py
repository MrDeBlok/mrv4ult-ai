"""Tests for Sprint 29.1 client sourcing workspace polish."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from client_intelligence import (
    UNNAMED_CLIENT_TITLE,
    build_client_create_request_url,
    build_client_profile,
    client_profile_title,
)
from contact_classification import CONTACT_TYPE_CLIENT


class TestClientProfileTitle:
    def test_unnamed_client_uses_fallback_title(self) -> None:
        client = {"phone_number": "+31612345678", "whatsapp_id": "31612345678@lid"}

        assert client_profile_title(client) == UNNAMED_CLIENT_TITLE

    def test_named_client_uses_display_name(self) -> None:
        client = {"display_name": "Anna Buyer", "phone_number": "+31612345678"}

        assert client_profile_title(client) == "Anna Buyer"

    def test_build_client_profile_shows_phone_separately_for_unnamed_client(self) -> None:
        profile = build_client_profile(
            {
                "id": "client-1",
                "phone_number": "+31612345678",
                "created_at": "2026-06-01T10:00:00+00:00",
            },
            {"status": "active"},
        )

        assert profile["title"] == UNNAMED_CLIENT_TITLE
        assert profile["name"] == ""
        assert profile["show_contact_phone"] is True
        assert profile["contact_phone"] == "+31612345678"

    def test_build_client_create_request_url_includes_client_id_and_name(self) -> None:
        url = build_client_create_request_url(
            "client-1",
            {"display_name": "Anna Buyer"},
        )

        assert "client_id=client-1" in url
        assert "client_name=Anna+Buyer" in url or "client_name=Anna%20Buyer" in url


class TestClientDetailPolish:
    @patch("app.build_matching_offer_rows")
    @patch("app.build_client_sourcing_dashboard")
    @patch("app.find_matching_offers_for_client", return_value=[])
    @patch("app.list_active_sourcing_offers", return_value=[])
    @patch("app.build_client_match_rows", return_value=[])
    @patch("app.list_client_match_history", return_value=[])
    @patch("app.build_client_request_rows", return_value=[])
    @patch("app.list_requests_for_client", return_value=[])
    @patch("app.get_client_profile", return_value={"status": "active"})
    @patch("app.get_client_by_id")
    def test_unnamed_client_shows_title_and_phone_separately(
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
            "phone_number": "+31612345678",
            "contact_type": CONTACT_TYPE_CLIENT,
            "created_at": "2026-06-01T10:00:00+00:00",
        }
        mock_build_dashboard.return_value = {
            "open_requests": 0,
            "matching_offers_count": 0,
            "best_potential_profit": "—",
            "latest_matching_offer": "—",
        }
        mock_build_offer_rows.return_value = []

        response = TestClient(app).get("/clients/client-1")

        assert response.status_code == 200
        assert UNNAMED_CLIENT_TITLE in response.text
        assert "+31612345678" in response.text
        assert response.text.index(UNNAMED_CLIENT_TITLE) < response.text.index("+31612345678")

    @patch("app.build_matching_offer_rows")
    @patch("app.build_client_sourcing_dashboard")
    @patch("app.find_matching_offers_for_client", return_value=[])
    @patch("app.list_active_sourcing_offers", return_value=[])
    @patch("app.build_client_match_rows", return_value=[])
    @patch("app.list_client_match_history", return_value=[])
    @patch("app.build_client_request_rows", return_value=[])
    @patch("app.list_requests_for_client", return_value=[])
    @patch("app.get_client_profile", return_value={"status": "active"})
    @patch("app.get_client_by_id")
    def test_open_requests_section_appears_before_matching_offers(
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
            "open_requests": 0,
            "matching_offers_count": 0,
            "best_potential_profit": "—",
            "latest_matching_offer": "—",
        }
        mock_build_offer_rows.return_value = []

        response = TestClient(app).get("/clients/client-1")
        text = response.text

        assert text.index("Open Requests") < text.index("Matching Offers")

    @patch("app.build_matching_offer_rows")
    @patch("app.build_client_sourcing_dashboard")
    @patch("app.find_matching_offers_for_client", return_value=[])
    @patch("app.list_active_sourcing_offers", return_value=[])
    @patch("app.build_client_match_rows", return_value=[])
    @patch("app.list_client_match_history", return_value=[])
    @patch("app.build_client_request_rows", return_value=[])
    @patch("app.list_requests_for_client", return_value=[])
    @patch("app.get_client_profile", return_value={"status": "active"})
    @patch("app.get_client_by_id")
    def test_no_requests_empty_state_includes_create_request(
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
            "open_requests": 0,
            "matching_offers_count": 0,
            "best_potential_profit": "—",
            "latest_matching_offer": "—",
        }
        mock_build_offer_rows.return_value = []

        response = TestClient(app).get("/clients/client-1")

        assert "This client has no active requests yet." in response.text
        assert 'href="/requests?client_id=client-1' in response.text
        assert "Create Request" in response.text

    @patch("app.build_matching_offer_rows")
    @patch("app.build_client_sourcing_dashboard")
    @patch("app.find_matching_offers_for_client", return_value=[])
    @patch("app.list_active_sourcing_offers", return_value=[])
    @patch("app.build_client_match_rows", return_value=[])
    @patch("app.list_client_match_history", return_value=[])
    @patch("app.build_client_request_rows", return_value=[])
    @patch("app.list_requests_for_client", return_value=[])
    @patch("app.get_client_profile", return_value={"status": "active"})
    @patch("app.get_client_by_id")
    def test_no_matching_offers_empty_state_includes_create_request(
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
            "open_requests": 0,
            "matching_offers_count": 0,
            "best_potential_profit": "—",
            "latest_matching_offer": "—",
        }
        mock_build_offer_rows.return_value = []

        response = TestClient(app).get("/clients/client-1")

        assert "No matching offers found yet." in response.text
        assert "Add a request or wait for new dealer offers to arrive." in response.text
        assert "Create Request" in response.text

    @patch("app.build_matching_offer_rows")
    @patch("app.build_client_sourcing_dashboard")
    @patch("app.find_matching_offers_for_client", return_value=[{"offer_id": "offer-1"}])
    @patch("app.list_active_sourcing_offers", return_value=[])
    @patch("app.build_client_match_rows", return_value=[])
    @patch("app.list_client_match_history", return_value=[])
    @patch("app.build_client_request_rows", return_value=[])
    @patch("app.list_requests_for_client", return_value=[{"id": "req-1", "status": "open"}])
    @patch("app.get_client_profile", return_value={"status": "active"})
    @patch("app.get_client_by_id")
    def test_matching_offer_rows_still_render_when_matches_exist(
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

        response = TestClient(app).get("/clients/client-1")

        assert "Gold Source" in response.text
        assert "Excellent Match" in response.text
        assert "View watch" in response.text
