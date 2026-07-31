"""Shared Supabase mocks for search offer loading tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from search import SEARCH_ACTIVE_OFFERS_RPC, SEARCH_OFFERS_PAGE_SIZE


def mock_search_offers_client(
    offers: list[dict[str, Any]],
    *,
    total_count: int | None = None,
) -> MagicMock:
    """Mock get_client() for server-side search_active_offers RPC queries."""
    resolved_total = total_count if total_count is not None else len(offers)

    def _rpc_result(offset: int, limit: int) -> dict[str, Any]:
        batch = offers[offset : offset + limit]
        return {
            "offers": batch,
            "total_count": resolved_total,
        }

    mock_execute = MagicMock()

    def _execute() -> MagicMock:
        return mock_execute

    mock_rpc = MagicMock()
    mock_rpc.execute.side_effect = _execute

    def _rpc(name: str, payload: dict[str, Any]) -> MagicMock:
        if name != SEARCH_ACTIVE_OFFERS_RPC:
            raise AssertionError(f"Unexpected RPC: {name}")
        offset = int(payload.get("page_offset") or 0)
        limit = int(payload.get("page_limit") or SEARCH_OFFERS_PAGE_SIZE)
        mock_execute.data = _rpc_result(offset, limit)
        return mock_rpc

    mock_client = MagicMock()
    mock_client.rpc.side_effect = _rpc
    return mock_client


def mock_search_offers_full_scan_client(
    offers: list[dict[str, Any]],
    *,
    total_count: int | None = None,
) -> MagicMock:
    """Mock get_client() for diagnostic full-table active offer scans."""
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
