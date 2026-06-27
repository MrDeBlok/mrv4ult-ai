"""MRV4ULT AI internal dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import get_active_offers_for_watch, get_client, get_watch_by_id
from search import (
    _display_value,
    _nested_record,
    _parse_max_usd_price,
    _sort_key_usd_price,
    format_price,
    format_usd_price,
    group_offers_by_watch,
    search_offers,
)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="MRV4ULT AI Dashboard")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def format_dealer_contact(dealer: dict[str, Any]) -> dict[str, str]:
    """Format dealer name and WhatsApp contact for dashboard display."""
    display_name = (dealer.get("display_name") or "").strip()
    phone_number = (dealer.get("phone_number") or "").strip()
    whatsapp_id = (dealer.get("whatsapp_id") or "").strip()

    if display_name:
        primary = display_name
        secondary = phone_number or whatsapp_id or ""
    else:
        primary = phone_number or whatsapp_id or "Unknown dealer"
        secondary = ""

    return {"primary": primary, "secondary": secondary}


def enrich_offers_dealer_contacts(offers: list[dict[str, Any]]) -> None:
    """Load dealer phone and WhatsApp details for dashboard display."""
    display_names = {
        (offer.get("dealer") or {}).get("display_name")
        for offer in offers
        if (offer.get("dealer") or {}).get("display_name")
    }
    if not display_names:
        return

    response = (
        get_client()
        .table("dealers")
        .select("display_name, phone_number, whatsapp_id")
        .in_("display_name", list(display_names))
        .execute()
    )
    dealers_by_name = {
        row["display_name"]: row
        for row in response.data or []
        if row.get("display_name")
    }

    for offer in offers:
        dealer = offer.get("dealer") or {}
        name = dealer.get("display_name")
        if name and name in dealers_by_name:
            dealer.update(dealers_by_name[name])
            offer["dealer"] = dealer


def build_result_rows(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn grouped search results into read-only dashboard rows."""
    rows: list[dict[str, Any]] = []

    for group in groups:
        watch = group.get("watch") or {}
        offers = group.get("offers") or []
        if not offers:
            continue

        cheapest_offer = min(
            offers,
            key=lambda offer: (
                offer.get("usd_price") is None,
                offer.get("usd_price") if offer.get("usd_price") is not None else 0,
            ),
        )
        dealer = cheapest_offer.get("dealer") or {}

        dealer_contact = format_dealer_contact(dealer)

        rows.append(
            {
                "watch_id": group.get("watch_id"),
                "brand": _display_value(watch.get("brand")),
                "reference": _display_value(watch.get("reference")),
                "dial": _display_value(watch.get("dial")),
                "bracelet": _display_value(watch.get("bracelet")),
                "lowest_price": format_usd_price(group.get("lowest_usd")),
                "dealer_primary": dealer_contact["primary"],
                "dealer_secondary": dealer_contact["secondary"],
                "card_date": cheapest_offer.get("card_date") or "N/A",
            }
        )

    return rows


def build_search_query(search_text: str, *, cheapest_only: bool, max_price: int | None) -> str:
    """Compose a search.py query string from dashboard form fields."""
    parts: list[str] = []
    if cheapest_only:
        parts.append("cheapest")
    if search_text.strip():
        parts.extend(search_text.strip().split())
    if max_price is not None:
        parts.extend(["under", str(max_price)])
    return " ".join(parts)


def _parse_cheapest_only(value: str | None) -> bool:
    return value in {"1", "on", "true", "yes"}


def normalize_offer(offer: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested Supabase dealer data onto an offer record."""
    normalized = dict(offer)
    normalized["dealer"] = _nested_record(normalized.pop("dealers", None))
    return normalized


def build_watch_stats(offers: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute USD price statistics for a watch's active offers."""
    usd_prices = [
        price for price in (offer.get("usd_price") for offer in offers) if price is not None
    ]
    average_usd = round(sum(usd_prices) / len(usd_prices)) if usd_prices else None
    return {
        "lowest_usd": format_usd_price(min(usd_prices) if usd_prices else None),
        "average_usd": format_usd_price(average_usd),
        "highest_usd": format_usd_price(max(usd_prices) if usd_prices else None),
        "offer_count": len(offers),
    }


def build_offer_rows(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format active offers for the watch detail table."""
    rows: list[dict[str, Any]] = []

    for offer in sorted(offers, key=_sort_key_usd_price):
        dealer_contact = format_dealer_contact(offer.get("dealer") or {})
        rows.append(
            {
                "dealer_primary": dealer_contact["primary"],
                "dealer_secondary": dealer_contact["secondary"],
                "original_price": format_price(
                    offer.get("original_price"),
                    offer.get("original_currency"),
                ),
                "usd_price": format_usd_price(offer.get("usd_price")),
                "card_date": offer.get("card_date") or "N/A",
                "condition": offer.get("condition") or "N/A",
            }
        )

    return rows


def build_watch_display(watch: dict[str, Any]) -> dict[str, str]:
    """Format watch identity fields for display."""
    return {
        "brand": _display_value(watch.get("brand")),
        "reference": _display_value(watch.get("reference")),
        "model": _display_value(watch.get("model")),
        "dial": _display_value(watch.get("dial")),
        "bracelet": _display_value(watch.get("bracelet")),
    }


@app.get("/watch/{watch_id}", response_class=HTMLResponse, name="watch_detail")
async def watch_detail(request: Request, watch_id: str) -> HTMLResponse:
    watch = get_watch_by_id(watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="Watch not found")

    offers = [normalize_offer(offer) for offer in get_active_offers_for_watch(watch_id)]

    return templates.TemplateResponse(
        request,
        "watch_detail.html",
        {
            "watch": build_watch_display(watch),
            "stats": build_watch_stats(offers),
            "offers": build_offer_rows(offers),
        },
    )


@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    q: str = "",
    cheapest: str | None = None,
    max_price: str = "",
) -> HTMLResponse:
    search_text = q.strip()
    cheapest_only = _parse_cheapest_only(cheapest)
    max_price_input = max_price.strip()
    searched = bool(request.query_params)
    error: str | None = None
    results: list[dict[str, Any]] = []

    if searched:
        try:
            max_price_value = _parse_max_usd_price(max_price_input) if max_price_input else None
            query = build_search_query(
                search_text,
                cheapest_only=cheapest_only,
                max_price=max_price_value,
            )
            offers, cheapest_only_flag = search_offers(query)
            enrich_offers_dealer_contacts(offers)
            groups = group_offers_by_watch(offers, cheapest_only=cheapest_only_flag)
            results = build_result_rows(groups)
        except ValueError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "search_text": search_text,
            "cheapest_only": cheapest_only,
            "max_price": max_price_input,
            "results": results,
            "searched": searched,
            "error": error,
        },
    )
