"""Shared pytest fixtures for the MRV4ULT AI test suite."""

from __future__ import annotations

import pytest

from database import reset_client_profiles_cache, reset_contact_type_column_cache, reset_parser_learning_rules_cache, reset_user_columns_cache

ADMIN_USER = {
    "id": "11111111-1111-4111-8111-111111111111",
    "name": "Admin User",
    "email": "admin@mrvault.local",
    "role": "admin",
    "status": "active",
    "created_at": "2026-06-27T12:00:00+00:00",
    "last_login_at": None,
}

TRADER_ONE = {
    "id": "22222222-2222-4222-8222-222222222222",
    "name": "Trader One",
    "email": "trader1@mrvault.local",
    "role": "trader",
    "status": "active",
    "created_at": "2026-06-27T12:00:00+00:00",
    "last_login_at": None,
}

TRADER_TWO = {
    "id": "33333333-3333-4333-8333-333333333333",
    "name": "Trader Two",
    "email": "trader2@mrvault.local",
    "role": "trader",
    "status": "active",
    "created_at": "2026-06-27T12:00:00+00:00",
    "last_login_at": None,
}

VIEWER_USER = {
    "id": "44444444-4444-4444-8444-444444444444",
    "name": "Viewer User",
    "email": "viewer@mrvault.local",
    "role": "viewer",
    "status": "active",
    "created_at": "2026-06-27T12:00:00+00:00",
    "last_login_at": None,
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "no_auto_login: disable shared admin auto-login fixture")
    config.addinivalue_line("markers", "unit: fast isolated unit tests")
    config.addinivalue_line("markers", "integration: tests that exercise multi-module workflows")
    config.addinivalue_line("markers", "database: tests that validate database helpers or schema probes")
    config.addinivalue_line("markers", "performance: performance and profiling regression tests")
    config.addinivalue_line(
        "markers",
        "allow_live_supabase: permit live Supabase network access for this test",
    )


@pytest.fixture(autouse=True)
def _reset_contact_type_column_cache() -> None:
    reset_contact_type_column_cache()
    reset_client_profiles_cache()
    reset_user_columns_cache()
    reset_parser_learning_rules_cache()
    yield
    reset_contact_type_column_cache()
    reset_client_profiles_cache()
    reset_user_columns_cache()
    reset_parser_learning_rules_cache()


@pytest.fixture(autouse=True)
def _authenticated_dashboard_user(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("no_auto_login"):
        return
    monkeypatch.setattr("app.get_current_user", lambda _request: ADMIN_USER)
    monkeypatch.setattr("auth.authenticate_email", lambda email: ADMIN_USER if email == ADMIN_USER["email"] else None)
    monkeypatch.setattr("database.users_table_supported", lambda: True)
    monkeypatch.setattr("database.user_ownership_columns_supported", lambda: True)
