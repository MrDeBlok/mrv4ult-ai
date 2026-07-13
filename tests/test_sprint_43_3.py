"""Tests for Sprint 43.3 activity pagination."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from activity_feed import (
    ACTIVITY_PAGE_SIZE,
    activity_page_url,
    load_activity_page,
    parse_activity_page,
)
from app import app
from tests.conftest import ADMIN_USER, TRADER_ONE


def _import_log(
    import_id: str,
    *,
    status: str = "success",
    watches_parsed: int = 1,
    new_offers: int = 1,
    imported_by_user_id: str | None = None,
) -> dict:
    suffix = "".join(character for character in import_id if character.isdigit()) or "1"
    return {
        "id": import_id,
        "message_id": f"msg-{import_id}",
        "status": status,
        "watches_parsed": watches_parsed,
        "new_offers": new_offers,
        "duplicate_offers": 0,
        "matched_requests": 0,
        "group_name": "HK",
        "dealer_alias": "Dealer",
        "dealer_whatsapp": "+1",
        "import_time": f"2026-06-27T{int(suffix) % 24:02d}:00:00+00:00",
        "processing_time": "0.1s",
        "summary": {},
        "imported_by_user_id": imported_by_user_id,
    }


class TestActivityPaginationHelpers:
    def test_parse_activity_page_defaults_invalid_to_one(self) -> None:
        assert parse_activity_page(None) == 1
        assert parse_activity_page("0") == 1
        assert parse_activity_page("abc") == 1
        assert parse_activity_page("3") == 3

    def test_activity_page_url_preserves_tab_and_page(self) -> None:
        assert activity_page_url("active", 1) == "/activity"
        assert activity_page_url("active", 2) == "/activity?page=2"
        assert activity_page_url("ignored", 3) == "/activity/ignored?page=3"
        assert activity_page_url("all", 4) == "/activity/all?page=4"


class TestLoadActivityPage:
    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 20, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_default_page_returns_at_most_twenty_rows(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        rows = [_import_log(f"log-{index}") for index in range(20)]
        mock_list_activity.return_value = rows

        result = load_activity_page(ADMIN_USER, "active", page=1)

        assert len(result.imports) == ACTIVITY_PAGE_SIZE
        assert result.page == 1
        assert result.has_next is True
        assert result.showing_from == 1
        assert result.showing_to == ACTIVITY_PAGE_SIZE
        assert mock_list_activity.call_count == 1

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 45, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_page_two_uses_database_offset(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.side_effect = [
            [_import_log(f"log-{index:02d}") for index in range(20)],
            [_import_log(f"log-{index:02d}") for index in range(20, 40)],
        ]

        page_one = load_activity_page(ADMIN_USER, "all", page=1)
        page_two = load_activity_page(ADMIN_USER, "all", page=2)

        assert len(page_one.imports) == ACTIVITY_PAGE_SIZE
        assert page_one.imports[0]["id"] == "log-00"
        assert len(page_two.imports) == ACTIVITY_PAGE_SIZE
        assert page_two.imports[0]["id"] == "log-20"
        assert page_two.has_previous is True
        assert mock_list_activity.call_args_list[1].kwargs["offset"] == ACTIVITY_PAGE_SIZE

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_page_two_requests_offset_twenty(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.return_value = [
            _import_log(f"log-{index:02d}") for index in range(20, 40)
        ]

        load_activity_page(ADMIN_USER, "all", page=2)

        assert mock_list_activity.call_count == 1
        assert mock_list_activity.call_args.kwargs["offset"] == ACTIVITY_PAGE_SIZE
        assert mock_list_activity.call_args.kwargs["limit"] == ACTIVITY_PAGE_SIZE

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_empty_first_page_message(self, mock_list_activity: MagicMock, _mock_stats: MagicMock) -> None:
        mock_list_activity.return_value = []

        result = load_activity_page(ADMIN_USER, "active", page=1)

        assert result.imports == []
        assert result.empty_message == "No activity yet."

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_empty_later_page_message(self, mock_list_activity: MagicMock, _mock_stats: MagicMock) -> None:
        mock_list_activity.return_value = []

        result = load_activity_page(ADMIN_USER, "all", page=3)

        assert result.imports == []
        assert result.empty_message == "No more activity."

    @patch("activity_feed.load_activity_stats_bounded", return_value={"offers": 0, "needs_review": 0, "ignored": 0})
    @patch("database.list_activity_import_logs")
    def test_visibility_filter_still_applies(
        self,
        mock_list_activity: MagicMock,
        _mock_stats: MagicMock,
    ) -> None:
        mock_list_activity.return_value = [
            _import_log("shared", imported_by_user_id=TRADER_ONE["id"]),
            _import_log(
                "private",
                status="noise",
                watches_parsed=0,
                new_offers=0,
                imported_by_user_id=ADMIN_USER["id"],
            ),
        ]

        result = load_activity_page(TRADER_ONE, "all", page=1)

        assert [row["id"] for row in result.imports] == ["shared"]


class TestActivityPaginationRoutes:
    @patch("app.load_activity_page")
    def test_activity_route_renders_pagination_controls(
        self,
        mock_load_page: MagicMock,
    ) -> None:
        mock_load_page.return_value = MagicMock(
            imports=[_import_log("log-1")],
            stats={"offers": 1, "needs_review": 0, "ignored": 0},
            page=2,
            page_size=ACTIVITY_PAGE_SIZE,
            has_previous=True,
            has_next=True,
            showing_from=21,
            showing_to=21,
            empty_message="",
        )

        client = TestClient(app)
        response = client.get("/activity?page=2")

        assert response.status_code == 200
        assert "Page 2" in response.text
        assert 'href="/activity"' in response.text
        assert 'href="/activity?page=3"' in response.text
        assert "Showing 21-21" in response.text
        mock_load_page.assert_called_once()
        assert mock_load_page.call_args.kwargs["page"] == 2

    @patch("app.load_activity_page")
    def test_activity_all_route_paginates(self, mock_load_page: MagicMock) -> None:
        mock_load_page.return_value = MagicMock(
            imports=[],
            stats={"offers": 0, "needs_review": 0, "ignored": 0},
            page=2,
            page_size=ACTIVITY_PAGE_SIZE,
            has_previous=True,
            has_next=False,
            showing_from=0,
            showing_to=0,
            empty_message="No more activity.",
        )

        client = TestClient(app)
        response = client.get("/activity/all?page=2")

        assert response.status_code == 200
        assert "No more activity." in response.text
        assert mock_load_page.call_args.args[1] == "all"
        assert mock_load_page.call_args.kwargs["page"] == 2

    @patch("app.load_activity_page")
    def test_ignored_tab_next_link_preserves_tab(self, mock_load_page: MagicMock) -> None:
        mock_load_page.return_value = MagicMock(
            imports=[_import_log("ignored-1", status="noise", watches_parsed=0, new_offers=0)],
            stats={"offers": 0, "needs_review": 0, "ignored": 1},
            page=1,
            page_size=ACTIVITY_PAGE_SIZE,
            has_previous=False,
            has_next=True,
            showing_from=1,
            showing_to=1,
            empty_message="",
        )

        client = TestClient(app)
        response = client.get("/activity/ignored")

        assert response.status_code == 200
        assert 'href="/activity/ignored?page=2"' in response.text
