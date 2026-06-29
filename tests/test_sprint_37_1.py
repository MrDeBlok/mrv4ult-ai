"""Tests for Sprint 37.1 team access pilot health check."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from auth import is_public_path


def _test_client() -> TestClient:
    from app import app as fastapi_app

    return TestClient(fastapi_app)


class TestHealthCheckRoute:
    def test_is_public_path_includes_health(self) -> None:
        assert is_public_path("/health") is True
        assert is_public_path("/activity") is False

    @patch("app.start_whatsapp_listener")
    @patch("app.stop_whatsapp_listener")
    def test_health_returns_ok_json(
        self,
        _mock_stop: patch,
        _mock_start: patch,
    ) -> None:
        response = _test_client().get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "app": "MRV4ULT AI"}

    @pytest.mark.no_auto_login
    @patch("app.start_whatsapp_listener")
    @patch("app.stop_whatsapp_listener")
    def test_health_does_not_require_login(
        self,
        _mock_stop: patch,
        _mock_start: patch,
    ) -> None:
        response = _test_client().get("/health")

        assert response.status_code == 200
        assert response.headers.get("location") != "/login"
        assert response.json()["status"] == "ok"

    @pytest.mark.no_auto_login
    @patch("app.start_whatsapp_listener")
    @patch("app.stop_whatsapp_listener")
    def test_private_pages_still_require_login(
        self,
        _mock_stop: patch,
        _mock_start: patch,
    ) -> None:
        response = _test_client().get("/dashboard", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"
