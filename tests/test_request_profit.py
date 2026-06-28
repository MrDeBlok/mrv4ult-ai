"""Tests for client request profit intelligence."""

from __future__ import annotations

from request_profit import (
    attach_profit_to_matches,
    budget_status,
    build_requests_dashboard_summary,
    calculate_match_profit,
    format_margin_pct,
    sort_matches_by_profit,
)


class TestProfitCalculation:
    def test_calculates_potential_profit_and_budget_difference(self) -> None:
        profit = calculate_match_profit(
            {"max_price": 50000, "currency": "USD"},
            {"usd_price": 45000},
        )

        assert profit["budget_usd"] == 50000
        assert profit["offer_usd"] == 45000
        assert profit["potential_profit_usd"] == 5000
        assert profit["budget_difference_usd"] == 5000
        assert profit["potential_profit"] == "$5,000"
        assert profit["budget_difference"] == "$5,000"

    def test_returns_dashes_when_budget_missing(self) -> None:
        profit = calculate_match_profit({}, {"usd_price": 45000})

        assert profit["budget"] == "—"
        assert profit["potential_profit"] == "—"
        assert profit["margin"] == "—"


class TestMarginCalculation:
    def test_calculates_margin_percentage(self) -> None:
        profit = calculate_match_profit(
            {"max_price": 50000, "currency": "USD"},
            {"usd_price": 45000},
        )

        assert profit["margin_pct"] == 10.0
        assert profit["margin"] == "10.0%"
        assert format_margin_pct(10.0) == "10.0%"


class TestBudgetStatusColors:
    def test_below_budget_is_green(self) -> None:
        label, css_class = budget_status(45000, 50000)
        assert label == "Below budget"
        assert css_class == "success"

    def test_within_two_percent_is_orange(self) -> None:
        label, css_class = budget_status(49500, 50000)
        assert label == "Within 2% of budget"
        assert css_class == "warning"

    def test_above_budget_is_red(self) -> None:
        label, css_class = budget_status(51000, 50000)
        assert label == "Above budget"
        assert css_class == "danger"

    def test_calculate_match_profit_applies_status(self) -> None:
        below = calculate_match_profit({"max_price": 50000, "currency": "USD"}, {"usd_price": 45000})
        near = calculate_match_profit({"max_price": 50000, "currency": "USD"}, {"usd_price": 49500})
        above = calculate_match_profit({"max_price": 50000, "currency": "USD"}, {"usd_price": 51000})

        assert below["status_class"] == "success"
        assert near["status_class"] == "warning"
        assert above["status_class"] == "danger"


class TestSorting:
    def test_sorts_matches_by_highest_profit_first(self) -> None:
        request = {"max_price": 50000, "currency": "USD"}
        matches = attach_profit_to_matches(
            request,
            [
                {"offer": {"usd_price": 47000}},
                {"offer": {"usd_price": 44000}},
                {"offer": {"usd_price": 49000}},
            ],
        )

        profits = [match["profit"]["potential_profit_usd"] for match in matches]
        assert profits == [6000, 3000, 1000]

    def test_sort_matches_by_profit_puts_unknown_profit_last(self) -> None:
        sorted_matches = sort_matches_by_profit(
            [
                {"profit": {"potential_profit_usd": 1000}},
                {"profit": {"potential_profit_usd": None}},
                {"profit": {"potential_profit_usd": 5000}},
            ]
        )

        profits = [match["profit"]["potential_profit_usd"] for match in sorted_matches]
        assert profits == [5000, 1000, None]


class TestDashboardSummary:
    def test_builds_portfolio_summary(self) -> None:
        summary = build_requests_dashboard_summary(
            [
                {
                    "client_name": "John",
                    "has_matches": True,
                    "matched_offers": [
                        {
                            "offer_label": "Rolex · 116508",
                            "potential_profit_usd": 5000,
                            "potential_profit": "$5,000",
                        }
                    ],
                },
                {
                    "client_name": "Jane",
                    "has_matches": True,
                    "matched_offers": [
                        {
                            "offer_label": "Patek · 5711",
                            "potential_profit_usd": 8000,
                            "potential_profit": "$8,000",
                        }
                    ],
                },
            ],
            raw_requests=[
                {"status": "open"},
                {"status": "matched"},
                {"status": "closed"},
            ],
        )

        assert summary["open_requests"] == 1
        assert summary["matched_requests"] == 2
        assert summary["total_potential_profit"] == "$13,000"
        assert summary["biggest_opportunity"]["client_name"] == "Jane"
        assert summary["biggest_opportunity"]["potential_profit"] == "$8,000"
