"""Shared Supabase mocks for search offer loading tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def mock_search_offers_client(
    offers: list[dict[str, Any]],
    *,
    total_count: int | None = None,
) -> MagicMock:
    """Mock get_client() for paginated search offer queries."""
    mock_client = MagicMock()
    mock_execute = MagicMock()
    mock_execute.data = offers
    mock_execute.count = total_count if total_count is not None else len(offers)
    mock_range = MagicMock()
    mock_range.execute.return_value = mock_execute
    mock_eq = MagicMock()
    mock_eq.range.return_value = mock_range
    mock_select = MagicMock()
    mock_select.eq.return_value = mock_eq
    mock_table = MagicMock()
    mock_table.select.return_value = mock_select
    mock_client.table.return_value = mock_table
    return mock_client
