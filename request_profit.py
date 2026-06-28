"""Profit intelligence for client request matched offers."""

from __future__ import annotations

from typing import Any

from request_matching import EXCHANGE_RATES_TO_USD
from search import format_usd_price

Record = dict[str, Any]

BUDGET_NEAR_THRESHOLD = 0.02


def convert_amount(amount: int, from_currency: str, to_currency: str) -> int | None:
    from_rate = EXCHANGE_RATES_TO_USD.get(from_currency.upper())
    to_rate = EXCHANGE_RATES_TO_USD.get(to_currency.upper())
    if from_rate is None or to_rate is None:
        return None
    usd_amount = amount * from_rate
    return int(round(usd_amount / to_rate))


def request_budget_usd(request: Record) -> int | None:
    max_price = request.get("max_price")
    if max_price is None:
        return None
    currency = (request.get("currency") or "USD").upper()
    if currency == "USD":
        return int(max_price)
    return convert_amount(int(max_price), currency, "USD")


def offer_price_usd(offer: Record) -> int | None:
    usd_price = offer.get("usd_price")
    if usd_price is not None:
        return int(usd_price)

    original_price = offer.get("original_price")
    if original_price is None:
        return None

    currency = (offer.get("original_currency") or "USD").upper()
    if currency == "USD":
        return int(original_price)
    return convert_amount(int(original_price), currency, "USD")


def budget_status(offer_usd: int, budget_usd: int) -> tuple[str, str]:
    """Return human label and Bootstrap color class for offer vs client budget."""
    if offer_usd > budget_usd:
        return "Above budget", "danger"
    if offer_usd >= int(budget_usd * (1 - BUDGET_NEAR_THRESHOLD)):
        return "Within 2% of budget", "warning"
    return "Below budget", "success"


def format_margin_pct(margin_pct: float | None) -> str:
    if margin_pct is None:
        return "—"
    return f"{margin_pct:.1f}%"


def calculate_match_profit(request: Record, offer: Record) -> Record:
    """Calculate profit metrics for a matched offer against a client request."""
    budget_usd = request_budget_usd(request)
    offer_usd = offer_price_usd(offer)

    potential_profit_usd: int | None = None
    margin_pct: float | None = None
    budget_difference_usd: int | None = None
    status_label = "—"
    status_class = "secondary"

    if budget_usd is not None and offer_usd is not None:
        budget_difference_usd = budget_usd - offer_usd
        potential_profit_usd = budget_difference_usd
        if budget_usd > 0:
            margin_pct = (potential_profit_usd / budget_usd) * 100
        status_label, status_class = budget_status(offer_usd, budget_usd)

    return {
        "budget_usd": budget_usd,
        "offer_usd": offer_usd,
        "potential_profit_usd": potential_profit_usd,
        "margin_pct": margin_pct,
        "budget_difference_usd": budget_difference_usd,
        "budget": format_usd_price(budget_usd) if budget_usd is not None else "—",
        "offer_price": format_usd_price(offer_usd) if offer_usd is not None else "N/A",
        "potential_profit": (
            format_usd_price(potential_profit_usd) if potential_profit_usd is not None else "—"
        ),
        "margin": format_margin_pct(margin_pct),
        "budget_difference": (
            format_usd_price(budget_difference_usd) if budget_difference_usd is not None else "—"
        ),
        "status_label": status_label,
        "status_class": status_class,
    }


def sort_matches_by_profit(matches: list[Record]) -> list[Record]:
    """Sort enriched matches by highest potential profit first."""

    def sort_key(match: Record) -> tuple[int, int]:
        profit = match.get("profit") or {}
        profit_usd = profit.get("potential_profit_usd")
        if profit_usd is None:
            return (1, 0)
        return (0, -int(profit_usd))

    return sorted(matches, key=sort_key)


def attach_profit_to_matches(request: Record, matches: list[Record]) -> list[Record]:
    enriched: list[Record] = []
    for match in matches:
        offer = match.get("offer") or {}
        profit = calculate_match_profit(request, offer)
        enriched.append({**match, "profit": profit})
    return sort_matches_by_profit(enriched)


def build_request_profit_summary(request_row: Record) -> Record:
    matched_offers = request_row.get("matched_offers") or []
    best = matched_offers[0] if matched_offers else None
    return {
        "best_offer": best.get("offer_label") if best else "—",
        "best_potential_profit": best.get("potential_profit", "—") if best else "—",
        "best_margin": best.get("margin", "—") if best else "—",
        "match_count": len(matched_offers),
    }


def build_requests_dashboard_summary(
    request_rows: list[Record],
    *,
    raw_requests: list[Record] | None = None,
) -> Record:
    """Build portfolio-level profit summary cards for the requests page."""
    raw_requests = raw_requests or []
    open_count = sum(
        1
        for request in raw_requests
        if (request.get("status") or "").lower() in {"open", "active"}
    )
    matched_count = sum(1 for row in request_rows if row.get("has_matches"))

    best_profit_usd: int | None = None
    best_opportunity: Record | None = None
    total_profit_usd = 0

    for row in request_rows:
        matched_offers = row.get("matched_offers") or []
        if not matched_offers:
            continue

        best_match = matched_offers[0]
        profit_usd = best_match.get("potential_profit_usd")
        if profit_usd is None:
            continue
        if profit_usd > 0:
            total_profit_usd += profit_usd
            if best_profit_usd is None or profit_usd > best_profit_usd:
                best_profit_usd = profit_usd
                best_opportunity = {
                    "client_name": row.get("client_name") or "Client",
                    "offer_label": best_match.get("offer_label") or "Offer",
                    "potential_profit": best_match.get("potential_profit") or "—",
                }

    return {
        "open_requests": open_count,
        "matched_requests": matched_count,
        "total_potential_profit": format_usd_price(total_profit_usd),
        "biggest_opportunity": best_opportunity
        or {
            "client_name": "—",
            "offer_label": "—",
            "potential_profit": "—",
        },
    }
