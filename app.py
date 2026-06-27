"""MRV4ULT AI internal dashboard."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import (
    get_active_offers_for_watch,
    get_client,
    get_import_log,
    get_message_by_id,
    get_watch_by_id,
    list_import_logs,
)
from evolution_client import (
    EvolutionAPIError,
    create_instance,
    get_default_instance_name,
    get_instance_status,
    get_whatsapp_page_state,
)
from ingest import ingest_message
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
DEFAULT_WHATSAPP_INSTANCE = get_default_instance_name()

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
    """Flatten nested Supabase dealer and message data onto an offer record."""
    normalized = dict(offer)
    normalized["dealer"] = _nested_record(normalized.pop("dealers", None))
    message = _nested_record(normalized.pop("messages", None))
    normalized["received_at"] = message.get("received_at")
    normalized["group_id"] = message.get("group_id")
    group = _nested_record(message.get("groups"))
    normalized["group_name"] = group.get("name")
    return normalized


def build_watch_stats(offers: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute USD price statistics for a watch's active offers."""
    usd_prices = [
        price for price in (offer.get("usd_price") for offer in offers) if price is not None
    ]
    average_usd = round(sum(usd_prices) / len(usd_prices)) if usd_prices else None
    dealer_ids = {offer.get("dealer_id") for offer in offers if offer.get("dealer_id")}
    group_keys = {
        offer.get("group_id") or offer.get("group_name")
        for offer in offers
        if offer.get("group_id") or offer.get("group_name")
    }
    return {
        "lowest_usd": format_usd_price(min(usd_prices) if usd_prices else None),
        "average_usd": format_usd_price(average_usd),
        "highest_usd": format_usd_price(max(usd_prices) if usd_prices else None),
        "offer_count": len(offers),
        "unique_dealers": len(dealer_ids),
        "unique_groups": len(group_keys),
    }


def format_received_at(value: str | None) -> str:
    if not value:
        return "N/A"
    try:
        received_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return received_at.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def build_offer_rows(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format active offers for the watch detail table."""
    rows: list[dict[str, Any]] = []

    for offer in sorted(offers, key=_sort_key_usd_price):
        dealer = offer.get("dealer") or {}
        rows.append(
            {
                "dealer_name": dealer.get("display_name") or "Unknown dealer",
                "dealer_whatsapp": dealer.get("phone_number") or dealer.get("whatsapp_id") or "N/A",
                "group_name": offer.get("group_name") or "N/A",
                "original_price": format_price(
                    offer.get("original_price"),
                    offer.get("original_currency"),
                ),
                "usd_price": format_usd_price(offer.get("usd_price")),
                "card_date": offer.get("card_date") or "N/A",
                "condition": offer.get("condition") or "N/A",
                "received_at": format_received_at(offer.get("received_at")),
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


def format_timestamp(value: str | None) -> str:
    if not value:
        return "N/A"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def format_import_status(status: str | None) -> str:
    if not status:
        return "Unknown"
    return status.capitalize()


def import_status_class(status: str | None) -> str:
    return {
        "success": "success",
        "warning": "warning",
        "error": "danger",
    }.get(status or "", "secondary")


def build_activity_row(import_log: dict[str, Any]) -> dict[str, Any]:
    """Format one import log for the activity list."""
    return {
        "id": import_log["id"],
        "import_time": format_timestamp(import_log.get("import_time")),
        "group_name": import_log.get("group_name") or "N/A",
        "dealer_alias": import_log.get("dealer_alias") or "N/A",
        "dealer_whatsapp": import_log.get("dealer_whatsapp") or "N/A",
        "watches_parsed": import_log.get("watches_parsed", 0),
        "new_offers": import_log.get("new_offers", 0),
        "duplicate_offers": import_log.get("duplicate_offers", 0),
        "matched_requests": import_log.get("matched_requests", 0),
        "processing_time": import_log.get("processing_time") or "N/A",
        "status": format_import_status(import_log.get("status")),
        "status_class": import_status_class(import_log.get("status")),
    }


def build_activity_detail(
    import_log: dict[str, Any],
    message: dict[str, Any] | None,
) -> dict[str, Any]:
    """Format one import log for the detail page."""
    summary = import_log.get("summary") or {}
    message = message or {}
    return {
        "id": import_log["id"],
        "import_time": format_timestamp(import_log.get("import_time")),
        "group_name": import_log.get("group_name") or "N/A",
        "dealer_alias": import_log.get("dealer_alias"),
        "dealer_whatsapp": import_log.get("dealer_whatsapp") or "N/A",
        "watches_parsed": import_log.get("watches_parsed", 0),
        "new_watches": summary.get("new_watches", 0),
        "new_offers": import_log.get("new_offers", 0),
        "duplicate_offers": import_log.get("duplicate_offers", 0),
        "matched_requests": import_log.get("matched_requests", 0),
        "processing_time": import_log.get("processing_time") or "N/A",
        "status": format_import_status(import_log.get("status")),
        "status_class": import_status_class(import_log.get("status")),
        "raw_message": message.get("raw_text") or "",
        "rows": summary.get("rows") or [],
    }


@app.get("/whatsapp", response_class=HTMLResponse, name="whatsapp_page")
async def whatsapp_page(request: Request, error: str = "") -> HTMLResponse:
    page_error = error.strip() or None
    state: dict[str, Any]

    try:
        state = get_whatsapp_page_state(DEFAULT_WHATSAPP_INSTANCE)
    except EvolutionAPIError as exc:
        page_error = str(exc)
        state = {
            "instance_name": DEFAULT_WHATSAPP_INSTANCE,
            "exists": False,
            "connected": False,
            "state": "close",
            "status_label": "Unavailable",
            "phone_number": None,
            "profile_name": None,
            "last_connection_time": None,
            "qr_base64": None,
        }

    return templates.TemplateResponse(
        request,
        "whatsapp.html",
        {
            "state": state,
            "error": page_error,
        },
    )


@app.post("/whatsapp/create")
async def whatsapp_create_instance() -> RedirectResponse:
    try:
        create_instance(DEFAULT_WHATSAPP_INSTANCE)
    except EvolutionAPIError as exc:
        status = get_instance_status(DEFAULT_WHATSAPP_INSTANCE)
        if not status["exists"]:
            return RedirectResponse(
                url=f"/whatsapp?error={quote(str(exc))}",
                status_code=303,
            )

    return RedirectResponse(url="/whatsapp", status_code=303)


@app.get("/whatsapp/status")
async def whatsapp_status() -> JSONResponse:
    try:
        state = get_whatsapp_page_state(DEFAULT_WHATSAPP_INSTANCE)
    except EvolutionAPIError as exc:
        return JSONResponse(
            {"error": str(exc), "connected": False, "exists": False},
            status_code=502,
        )

    return JSONResponse(state)


@app.get("/activity", response_class=HTMLResponse, name="activity_list")
async def activity_list(request: Request) -> HTMLResponse:
    imports = [build_activity_row(import_log) for import_log in list_import_logs()]
    return templates.TemplateResponse(
        request,
        "activity.html",
        {"imports": imports},
    )


@app.get("/activity/{import_id}", response_class=HTMLResponse, name="activity_detail")
async def activity_detail(request: Request, import_id: str) -> HTMLResponse:
    import_log = get_import_log(import_id)
    if import_log is None:
        raise HTTPException(status_code=404, detail="Import not found")

    message = get_message_by_id(import_log["message_id"])
    return templates.TemplateResponse(
        request,
        "activity_detail.html",
        {"detail": build_activity_detail(import_log, message)},
    )


@app.get("/import", response_class=HTMLResponse, name="import_page")
async def import_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "import.html",
        {
            "message_text": "",
            "group_name": "",
            "dealer_whatsapp": "",
            "dealer_alias": "",
            "summary": None,
            "error": None,
        },
    )


@app.post("/import", response_class=HTMLResponse)
async def import_submit(
    request: Request,
    message: str = Form(""),
    group_name: str = Form(""),
    dealer_whatsapp: str = Form(""),
    dealer_alias: str = Form(""),
) -> HTMLResponse:
    message_text = message.strip()
    group_name_value = group_name.strip()
    dealer_whatsapp_value = dealer_whatsapp.strip()
    dealer_alias_value = dealer_alias.strip()
    error: str | None = None
    summary: dict[str, Any] | None = None

    if not group_name_value:
        error = "Group name is required."
    elif not dealer_whatsapp_value:
        error = "Dealer WhatsApp number is required."
    elif not message_text:
        error = "Message text is required."
    else:
        try:
            summary = ingest_message(
                message_text,
                group_name=group_name_value,
                dealer_whatsapp=dealer_whatsapp_value,
                dealer_alias=dealer_alias_value or None,
            )
        except Exception as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request,
        "import.html",
        {
            "message_text": message_text,
            "group_name": group_name_value,
            "dealer_whatsapp": dealer_whatsapp_value,
            "dealer_alias": dealer_alias_value,
            "summary": summary,
            "error": error,
        },
    )


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
