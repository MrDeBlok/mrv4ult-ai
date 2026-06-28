"""Tests for the /requests page and enriched request match loading."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app, build_request_row, build_request_rows
from database import combine_request_match_records


class TestCombineRequestMatchRecords:
    def test_combines_offer_watch_and_import_log_without_nested_join(self) -> None:
        matches = [
            {
                "id": "match-1",
                "request_id": "req-1",
                "offer_id": "offer-1",
                "import_log_id": "log-1",
                "match_strength": "strong",
                "match_reason": "Reference match: 116508",
            }
        ]
        offers_by_id = {
            "offer-1": {
                "id": "offer-1",
                "watch_id": "watch-1",
                "usd_price": 45000,
            }
        }
        watches_by_id = {
            "watch-1": {
                "id": "watch-1",
                "brand": "Rolex",
                "reference": "116508",
                "model": "Cosmograph Daytona",
            }
        }
        import_logs_by_id = {
            "log-1": {
                "id": "log-1",
                "import_time": "2026-06-27T12:00:00+00:00",
                "group_name": "HK Dealers",
                "dealer_alias": "John Dealer",
            }
        }

        enriched = combine_request_match_records(
            matches,
            offers_by_id=offers_by_id,
            watches_by_id=watches_by_id,
            import_logs_by_id=import_logs_by_id,
        )

        assert len(enriched) == 1
        assert enriched[0]["offer"]["usd_price"] == 45000
        assert enriched[0]["watch"]["reference"] == "116508"
        assert enriched[0]["import_log"]["dealer_alias"] == "John Dealer"


class TestBuildRequestRow:
    def test_renders_matched_offers_with_profit_metrics(self) -> None:
        row = build_request_row(
            {
                "id": "req-1",
                "client_name": "John Smith",
                "brand": "Rolex",
                "reference": "116508",
                "max_price": 50000,
                "currency": "USD",
                "status": "matched",
                "created_at": "2026-06-27T12:00:00+00:00",
            },
            matches=[
                {
                    "match_strength": "strong",
                    "match_reason": "Reference match: 116508",
                    "offer": {"usd_price": 45000},
                    "watch": {
                        "brand": "Rolex",
                        "reference": "116508",
                        "model": "Cosmograph Daytona",
                    },
                    "import_log": {
                        "id": "log-1",
                        "import_time": "2026-06-27T12:00:00+00:00",
                        "group_name": "HK Dealers",
                        "dealer_alias": "John Dealer",
                    },
                }
            ],
        )

        assert row["has_matches"] is True
        assert row["best_offer"] == "Rolex · 116508"
        assert row["best_potential_profit"] == "$5,000"
        assert row["best_margin"] == "10.0%"
        assert row["match_count"] == 1
        assert row["matched_offers"][0]["dealer"] == "John Dealer"
        assert row["matched_offers"][0]["price"] == "$45,000"
        assert row["matched_offers"][0]["budget"] == "$50,000"
        assert row["matched_offers"][0]["potential_profit"] == "$5,000"
        assert row["matched_offers"][0]["margin"] == "10.0%"
        assert row["matched_offers"][0]["status_class"] == "success"
        assert row["matched_offers"][0]["import_log_id"] == "log-1"


class TestRequestsPage:
    @patch("app.build_request_rows")
    @patch("app.list_requests")
    def test_requests_page_renders_profit_dashboard(
        self,
        mock_list_requests,
        mock_build_request_rows,
    ) -> None:
        mock_list_requests.return_value = [
            {
                "id": "req-1",
                "client_name": "John Smith",
                "status": "matched",
            }
        ]
        mock_build_request_rows.return_value = [
            {
                "id": "req-1",
                "client_name": "John Smith",
                "brand": "Rolex",
                "reference": "116508",
                "model": "—",
                "alias": "—",
                "dial": "—",
                "condition": "—",
                "year_range": "—",
                "max_price": "$50,000",
                "notes": "",
                "status": "Matched",
                "status_class": "success",
                "created_at": "Jun 27, 2026",
                "has_matches": True,
                "best_offer": "Rolex · 116508",
                "best_potential_profit": "$5,000",
                "best_margin": "10.0%",
                "match_count": 1,
                "matched_offers": [
                    {
                        "dealer": "John Dealer",
                        "offer_label": "Rolex · 116508",
                        "price": "$45,000",
                        "budget": "$50,000",
                        "potential_profit": "$5,000",
                        "margin": "10.0%",
                        "import_time": "Jun 27, 2026",
                        "import_log_id": "log-1",
                        "status_label": "Below budget",
                        "status_class": "success",
                    }
                ],
            }
        ]

        client = TestClient(app)
        response = client.get("/requests")

        assert response.status_code == 200
        assert "Total potential profit" in response.text
        assert "Biggest opportunity" in response.text
        assert "Best offer" in response.text
        assert "Potential profit" in response.text
        assert "John Dealer" in response.text
        assert "$5,000" in response.text
        assert "Below budget" in response.text

    @patch("app.build_request_rows")
    @patch("app.list_requests")
    def test_requests_page_uses_batched_row_builder(
        self,
        mock_list_requests,
        mock_build_request_rows,
    ) -> None:
        mock_list_requests.return_value = [{"id": "req-1", "client_name": "Jane", "status": "open"}]
        mock_build_request_rows.return_value = [
            {
                "id": "req-1",
                "client_name": "Jane",
                "brand": "—",
                "reference": "—",
                "model": "—",
                "alias": "—",
                "dial": "—",
                "condition": "—",
                "year_range": "—",
                "max_price": "—",
                "notes": "",
                "status": "Open",
                "status_class": "primary",
                "created_at": "N/A",
                "best_offer": "—",
                "best_potential_profit": "—",
                "best_margin": "—",
                "match_count": 0,
                "matched_offers": [],
                "has_matches": False,
            }
        ]

        client = TestClient(app)
        response = client.get("/requests")

        assert response.status_code == 200
        mock_build_request_rows.assert_called_once_with(mock_list_requests.return_value)
