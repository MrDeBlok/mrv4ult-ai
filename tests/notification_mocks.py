"""Shared mocks for notification page and row builder tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

Record = dict[str, Any]


@contextmanager
def patch_notification_import_queries(
    *,
    import_logs: dict[str, Record] | None = None,
    messages: dict[str, Record] | None = None,
):
    """Prevent notification builders from hitting Supabase import/message lookups."""
    import_logs = import_logs or {}
    messages = messages or {}
    with (
        patch("app.get_import_logs_by_ids", return_value=import_logs),
        patch("database.get_import_logs_by_ids", return_value=import_logs),
        patch("database.get_messages_by_ids", return_value=messages),
    ):
        yield
