"""Trader dashboard helpers — legacy exports and shared import scoping."""

from __future__ import annotations

from typing import Any

from dashboard_data import (
    ACTIVE_CLIENT_REQUEST_STATUSES,
    count_active_client_requests,
    load_trading_desk,
    parser_review_import_logs_for_user,
)

Record = dict[str, Any]

ACTIVE_REQUEST_STATUSES = ACTIVE_CLIENT_REQUEST_STATUSES
count_active_requests = count_active_client_requests


def load_dashboard_cards(user: Record | None, *, format_timestamp) -> list[Record]:
    """Legacy wrapper returning KPI cards from the trading desk loader."""
    return load_trading_desk(user, format_timestamp=format_timestamp)["kpis"]
