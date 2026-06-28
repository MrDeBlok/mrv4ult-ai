"""Shared pytest fixtures for the MRV4ULT AI test suite."""

from __future__ import annotations

import pytest

from database import reset_client_profiles_cache, reset_contact_type_column_cache


@pytest.fixture(autouse=True)
def _reset_contact_type_column_cache() -> None:
    reset_contact_type_column_cache()
    reset_client_profiles_cache()
    yield
    reset_contact_type_column_cache()
    reset_client_profiles_cache()
