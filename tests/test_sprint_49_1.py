"""Tests for Sprint 49.1 — Dealer List Parser Training."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from dealer_list_splitter import (
    detect_brand_header_line,
    split_multi_brand_dealer_list_message,
)
from dealer_list_training import (
    build_dealer_list_training_rows,
    compute_dealer_list_stats,
    dealer_list_has_rows_needing_review,
)
from ingest import _import_status
from parser_review import is_parser_review_pending
from tests.conftest import ADMIN_USER

pytestmark = pytest.mark.no_auto_login

MULTI_BRAND_DEALER_LIST = """ROLEX
126334 blue jub n3/26 full set 118000hkd
126300 black oys n12/25 82000hkd

AUDEMARS PIGUET
15500ST blue 2022 used 2022y watch only 265k hkd
15510ST blue 2023 used 310k hkd"""

DECORATED_HEADER_LIST = """❤️❤️❤️ROLEX❤️❤️❤️
126334 blue jub n3/26 full set 118000hkd
126300 black oys n12/25 82000hkd
126610LN 2011 used fullset 12500 usd"""


def _bulk_import_log_with_issues(*, watches_count: int = 12) -> dict:
    watches = [
        {
            "brand": "Rolex",
            "reference": None,
            "dealer_list_line": True,
            "original_price": 12500,
            "original_currency": "USD",
        }
        for _ in range(watches_count)
    ]
    return {
        "id": "log-bulk",
        "status": "warning",
        "message_id": "msg-bulk",
        "import_time": "2026-06-27T12:00:00+00:00",
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+31612345678",
        "watches_parsed": watches_count,
        "new_offers": 0,
        "duplicate_offers": 0,
        "summary": {
            "bulk_import": True,
            "status_reason": "Dealer list parsed 12 row(s): 0 valid, 12 need parser training.",
            "parsed_watches": watches,
            "offer_watches": watches,
            "rows": watches,
            "dealer_list_stats": {
                "total_rows": watches_count,
                "valid_rows": 0,
                "rows_needing_review": watches_count,
                "ignored_rows": 0,
            },
        },
    }


class TestMultiBrandDealerListSplit:
    def test_split_multi_brand_dealer_list_message(self) -> None:
        rows = split_multi_brand_dealer_list_message(MULTI_BRAND_DEALER_LIST)

        assert rows is not None
        assert len(rows) == 4
        assert rows[0][0] == "Rolex"
        assert rows[2][0] == "Audemars Piguet"

    def test_decorated_brand_header_detected(self) -> None:
        assert detect_brand_header_line("❤️❤️❤️ROLEX❤️❤️❤️") == "Rolex"

    def test_split_decorated_header_list(self) -> None:
        rows = split_multi_brand_dealer_list_message(DECORATED_HEADER_LIST)

        assert rows is not None
        assert len(rows) == 3
        assert all(brand == "Rolex" for brand, _ in rows)


class TestDealerListTrainingStats:
    def test_compute_dealer_list_stats_splits_valid_and_review_rows(self) -> None:
        watches = [
            {
                "brand": "Rolex",
                "reference": "126610LN",
                "original_price": 12500,
                "original_currency": "USD",
                "usd_price": 12500,
                "condition": "Pre-Owned",
                "condition_explicit": True,
                "condition_confidence": "high",
                "reference_high_confidence": True,
                "dealer_list_line": True,
            },
            {
                "brand": "Rolex",
                "reference": None,
                "original_price": 12500,
                "original_currency": "USD",
                "dealer_list_line": True,
            },
        ]

        stats = compute_dealer_list_stats(watches, message_type="offer_list")

        assert stats["total_rows"] == 2
        assert stats["valid_rows"] == 1
        assert stats["rows_needing_review"] == 1

    def test_bulk_import_with_review_rows_is_pending(self) -> None:
        import_log = _bulk_import_log_with_issues()

        assert dealer_list_has_rows_needing_review(import_log) is True
        assert is_parser_review_pending(import_log) is True

    def test_bulk_import_status_partial_warning(self) -> None:
        watches = [
            {
                "brand": "Rolex",
                "reference": None,
                "dealer_list_line": True,
                "original_price": 12500,
                "original_currency": "USD",
            }
            for _ in range(12)
        ]
        summary = {"watches_parsed": 12, "duplicate_offers": 0}

        status, reason = _import_status(summary, "success", watches, bulk_mode=True)

        assert status == "warning"
        assert "12 need parser training" in reason


class TestDealerListTrainingUI:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.get_import_log")
    @patch("app.get_message_by_id")
    def test_dealer_list_training_page_renders_row_table(
        self,
        mock_get_message: MagicMock,
        mock_get_import_log: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        import_log = _bulk_import_log_with_issues(watches_count=3)
        mock_get_import_log.return_value = import_log
        mock_get_message.return_value = {"raw_text": MULTI_BRAND_DEALER_LIST}

        client = TestClient(app)
        response = client.get("/parser-review/log-bulk/dealer-list")

        assert response.status_code == 200
        assert "Dealer List Training" in response.text
        assert "rows detected" in response.text.lower() or "Rows" in response.text
        assert "Open row table" not in response.text
        assert "Fix row" in response.text or "Edit" in response.text

    def test_build_dealer_list_training_rows_include_line_indexes(self) -> None:
        import_log = _bulk_import_log_with_issues(watches_count=2)
        rows = build_dealer_list_training_rows(import_log)

        assert len(rows) == 2
        assert rows[0]["line_index"] == 0
        assert rows[1]["line_index"] == 1
        assert rows[0]["needs_review"] is True

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.apply_dealer_list_bulk_action")
    @patch("app.get_import_log")
    def test_bulk_apply_brand_route(
        self,
        mock_get_import_log: MagicMock,
        mock_bulk_action: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_import_log.return_value = _bulk_import_log_with_issues(watches_count=2)
        mock_bulk_action.return_value = _bulk_import_log_with_issues(watches_count=2)

        client = TestClient(app)
        response = client.post(
            "/parser-review/log-bulk/bulk-action",
            data={
                "action": "apply_brand",
                "brand_name": "Rolex",
                "row_indexes": ["0", "1"],
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/parser-review/log-bulk/dealer-list?bulk_saved=1"
        mock_bulk_action.assert_called_once()
