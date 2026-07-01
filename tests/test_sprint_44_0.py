"""Tests for Sprint 44.0 grouped navigation cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app, templates
from navigation import nav_item_visible, visible_nav_groups
from tests.conftest import ADMIN_USER, TRADER_ONE, VIEWER_USER


class TestNavigationGroups:
    def test_trader_nav_excludes_import_and_admin_group(self) -> None:
        groups = visible_nav_groups(TRADER_ONE)
        labels = {group["label"] for group in groups}
        item_paths = {link["path"] for group in groups for link in group["links"]}

        assert "Admin" not in labels
        assert "/import" not in item_paths
        assert "/performance-profile" not in item_paths
        assert "/whatsapp" not in item_paths

    def test_admin_nav_includes_admin_tools(self) -> None:
        groups = visible_nav_groups(ADMIN_USER)
        admin_group = next(group for group in groups if group["label"] == "Admin")
        labels = [link["label"] for link in admin_group["links"]]

        assert labels == ["Team", "WhatsApp", "Performance Profile", "Import"]

    def test_viewer_nav_is_read_only(self) -> None:
        groups = visible_nav_groups(VIEWER_USER)
        labels = {group["label"] for group in groups}
        item_paths = {link["path"] for group in groups for link in group["links"]}

        assert labels == {"Trading", "Market"}
        assert "/import" not in item_paths
        assert "/parser-review" not in item_paths
        assert "/knowledge/unknown-brands" not in item_paths
        assert "/settings/team" not in item_paths
        assert "/dealers" not in item_paths
        assert "/notifications" not in item_paths

    def test_import_item_requires_admin(self) -> None:
        assert nav_item_visible(TRADER_ONE, {"path": "/import", "admin_only": True}) is False
        assert nav_item_visible(ADMIN_USER, {"path": "/import", "admin_only": True}) is True


class TestNavigationRoutes:
    @patch("app.load_trading_desk")
    def test_trader_dashboard_hides_import_link(self, mock_load_desk: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_load_desk.return_value = {"kpis": [], "quick_actions": [], "top_opportunities": [], "ai_needs_help": [], "live_market": [], "matched_requests": [], "show_write_actions": True}
        monkeypatch.setattr("app.get_current_user", lambda _request: TRADER_ONE)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'href="/import"' not in response.text
        assert 'data-nav-group="admin"' not in response.text
        assert 'data-nav-group="trading"' in response.text
        assert 'data-nav-group="market"' in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    def test_admin_can_access_import_directly(self, _mock_user: MagicMock) -> None:
        client = TestClient(app)
        response = client.get("/import")

        assert response.status_code == 200
        assert "Import" in response.text

    @patch("app.load_trading_desk")
    def test_admin_nav_shows_admin_dropdown(self, mock_load_desk: MagicMock) -> None:
        mock_load_desk.return_value = {"kpis": [], "quick_actions": [], "top_opportunities": [], "ai_needs_help": [], "live_market": [], "matched_requests": [], "show_write_actions": True}

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'data-nav-group="admin"' in response.text
        assert 'href="/import"' in response.text
        assert 'href="/performance-profile"' in response.text
        assert 'href="/settings/team"' in response.text

    @patch("app.load_trading_desk")
    def test_viewer_nav_hides_admin_and_write_links(self, mock_load_desk: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_load_desk.return_value = {"kpis": [], "quick_actions": [], "top_opportunities": [], "ai_needs_help": [], "live_market": [], "matched_requests": [], "show_write_actions": False}
        monkeypatch.setattr("app.get_current_user", lambda _request: VIEWER_USER)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert ">Admin<" not in response.text
        assert 'data-nav-group="admin"' not in response.text
        assert 'href="/import"' not in response.text
        assert 'href="/parser-review"' not in response.text
        assert 'href="/dealers"' not in response.text
        assert 'href="/"' in response.text
        assert 'href="/market-requests"' in response.text

    @patch("app.list_notifications", return_value=[])
    def test_notification_badge_renders_in_trading_dropdown(self, _mock_list: MagicMock) -> None:
        templates.env.globals["unread_notification_count"] = lambda: 3
        client = TestClient(app)
        response = client.get("/notifications")

        assert response.status_code == 200
        assert "notification-nav-badge" in response.text
        assert ">3<" in response.text
        assert 'data-nav-group="trading"' in response.text
        assert 'href="/notifications"' in response.text

    @patch("app.load_trading_desk")
    @patch("app.load_market_request_rows", return_value=[])
    def test_market_requests_link_in_market_dropdown(
        self,
        _mock_rows: MagicMock,
        mock_load_desk: MagicMock,
    ) -> None:
        mock_load_desk.return_value = {
            "kpis": [],
            "quick_actions": [],
            "top_opportunities": [],
            "ai_needs_help": [],
            "live_market": [],
            "matched_requests": [],
            "show_write_actions": True,
        }
        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert 'data-nav-group="market"' in response.text
        assert 'href="/market-requests"' in response.text
        assert "Market Requests" in response.text

    @patch("app.build_dealer_list_rows", return_value=[])
    @patch("app.list_offer_intelligence_rows", return_value=[])
    @patch("app.list_dealers", return_value=[])
    def test_dealers_link_in_network_dropdown(
        self,
        _mock_list_dealers: MagicMock,
        _mock_list_offers: MagicMock,
        _mock_build_rows: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/dealers")

        assert response.status_code == 200
        assert 'data-nav-group="network"' in response.text
        assert 'href="/dealers"' in response.text
