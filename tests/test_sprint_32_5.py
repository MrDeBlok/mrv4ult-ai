"""Tests for Sprint 32.5 Activity feed cleanup after parser review."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from activity_feed import activity_feed_counts, filter_active_activity_imports
from app import app


def _import_log(
    *,
    import_id: str,
    status: str,
    watches_parsed: int = 0,
    new_offers: int = 0,
    summary: dict | None = None,
) -> dict:
    return {
        "id": import_id,
        "status": status,
        "watches_parsed": watches_parsed,
        "new_offers": new_offers,
        "duplicate_offers": 0,
        "matched_requests": 0,
        "processing_time": "120 ms",
        "message_id": f"msg-{import_id}",
        "group_name": "HK Dealers",
        "dealer_alias": "Dealer A",
        "dealer_whatsapp": "+85291234567",
        "import_time": "2026-06-27T12:00:00+00:00",
        "summary": summary or {},
    }


class TestSprint325ActivityFilters:
    def test_reviewed_imports_disappear_from_default_activity(self) -> None:
        logs = [
            _import_log(import_id="active", status="success", watches_parsed=1, new_offers=1),
            _import_log(
                import_id="reviewed",
                status="success",
                watches_parsed=1,
                new_offers=1,
                summary={"parser_reviewed": True},
            ),
        ]

        assert [row["id"] for row in filter_active_activity_imports(logs)] == ["active"]

    def test_ignored_imports_disappear_from_default_activity(self) -> None:
        logs = [
            _import_log(import_id="active", status="warning", watches_parsed=1),
            _import_log(
                import_id="ignored",
                status="warning",
                watches_parsed=1,
                summary={"parser_review_ignored": True},
            ),
            _import_log(import_id="noise", status="noise"),
            _import_log(import_id="request", status="request_intent"),
            _import_log(import_id="empty", status="no_watch_detected"),
        ]

        assert [row["id"] for row in filter_active_activity_imports(logs)] == ["active"]

    def test_success_offers_remain_visible_on_active_tab(self) -> None:
        logs = [
            _import_log(import_id="offer", status="success", watches_parsed=1, new_offers=1),
            _import_log(import_id="reviewed", status="success", watches_parsed=1, summary={"parser_reviewed": True}),
        ]

        assert [row["id"] for row in filter_active_activity_imports(logs)] == ["offer"]

    def test_active_needs_review_remains_visible(self) -> None:
        logs = [
            _import_log(import_id="needs-review", status="warning", watches_parsed=1),
            _import_log(
                import_id="ignored",
                status="warning",
                watches_parsed=1,
                summary={"parser_review_ignored": True},
            ),
        ]

        assert [row["id"] for row in filter_active_activity_imports(logs)] == ["needs-review"]

    def test_counters_reflect_filtered_categories(self) -> None:
        logs = [
            _import_log(import_id="1", status="success", watches_parsed=1, new_offers=1),
            _import_log(import_id="2", status="warning", watches_parsed=1),
            _import_log(import_id="3", status="noise"),
            _import_log(import_id="4", status="request_intent"),
            _import_log(
                import_id="5",
                status="warning",
                watches_parsed=1,
                summary={"parser_review_ignored": True},
            ),
            _import_log(
                import_id="6",
                status="success",
                watches_parsed=1,
                summary={"parser_reviewed": True},
            ),
        ]

        assert activity_feed_counts(logs) == {
            "offers": 1,
            "needs_review": 1,
            "ignored": 3,
        }


class TestSprint325ActivityPages:
    @staticmethod
    def _sample_logs() -> list[dict]:
        return [
            _import_log(import_id="offer", status="success", watches_parsed=1, new_offers=1),
            _import_log(import_id="needs-review", status="warning", watches_parsed=1),
            _import_log(
                import_id="reviewed",
                status="success",
                watches_parsed=1,
                summary={"parser_reviewed": True},
            ),
            _import_log(
                import_id="ignored-warning",
                status="warning",
                watches_parsed=1,
                summary={"parser_review_ignored": True},
            ),
            _import_log(import_id="noise", status="noise"),
            _import_log(import_id="request", status="request_intent"),
        ]

    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_import_logs")
    def test_active_page_shows_only_active_items(
        self,
        mock_list_import_logs: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = self._sample_logs()

        client = TestClient(app)
        response = client.get("/activity")

        assert response.status_code == 200
        assert 'data-href="/activity/offer"' in response.text
        assert 'data-href="/activity/needs-review"' in response.text
        assert 'data-href="/activity/reviewed"' not in response.text
        assert 'data-href="/activity/noise"' not in response.text
        assert 'href="/activity/reviewed"' in response.text
        assert "<strong>Offers:</strong> 1" in response.text
        assert "<strong>Needs review:</strong> 1" in response.text
        assert "<strong>Ignored:</strong> 3" in response.text

    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_import_logs")
    def test_reviewed_tab_shows_reviewed_imports(
        self,
        mock_list_import_logs: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = self._sample_logs()

        client = TestClient(app)
        response = client.get("/activity/reviewed")

        assert response.status_code == 200
        assert 'data-href="/activity/reviewed"' in response.text
        assert 'data-href="/activity/offer"' not in response.text

    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_import_logs")
    def test_ignored_tab_shows_noise_and_request_intent(
        self,
        mock_list_import_logs: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        mock_list_import_logs.return_value = self._sample_logs()

        client = TestClient(app)
        response = client.get("/activity/ignored")

        assert response.status_code == 200
        assert 'data-href="/activity/noise"' in response.text
        assert 'data-href="/activity/request"' in response.text
        assert 'data-href="/activity/ignored-warning"' in response.text
        assert 'data-href="/activity/offer"' not in response.text

    @patch("app._business_import_logs", side_effect=lambda logs: logs)
    @patch("app.list_import_logs")
    def test_all_tab_shows_everything(
        self,
        mock_list_import_logs: MagicMock,
        _mock_business: MagicMock,
    ) -> None:
        logs = self._sample_logs()
        mock_list_import_logs.return_value = logs

        client = TestClient(app)
        response = client.get("/activity/all")

        assert response.status_code == 200
        for import_log in logs:
            assert f'data-href="/activity/{import_log["id"]}"' in response.text
