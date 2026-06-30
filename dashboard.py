"""Trader dashboard helpers — legacy exports and shared import scoping."""

from __future__ import annotations

from typing import Any

from dashboard_data import load_trading_desk, parser_review_import_logs_for_user

Record = dict[str, Any]

ACTIVE_REQUEST_STATUSES = frozenset({"open", "active"})


def count_active_requests(requests: list[Record]) -> int:
    """Return the number of open client requests."""
    return sum(
        1
        for request in requests
        if (request.get("status") or "").lower() in ACTIVE_REQUEST_STATUSES
    )


def load_dashboard_cards(user: Record | None, *, format_timestamp) -> list[Record]:
    """Legacy wrapper returning KPI cards from the trading desk loader."""
    return load_trading_desk(user, format_timestamp=format_timestamp)["kpis"]
