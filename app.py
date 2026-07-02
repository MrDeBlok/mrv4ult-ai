"""MRV4ULT AI internal dashboard."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from timezone_utils import format_display_timestamp
from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    REQUEST_CONDITION_FORM_OPTIONS,
    deal_condition_label,
    display_condition,
    normalize_condition_value,
    parse_request_condition_form,
    request_condition_display,
    request_condition_form_value,
    resolve_offer_wear_condition,
)
from model_aliases import alias_display_fields, enrich_with_model_alias
from watch_knowledge import enrich_parsed_watch, knowledge_display_fields, lookup_reference
from activity_feed import (
    activity_feed_counts,
    activity_page_url,
    load_activity_page,
    parse_activity_page,
)
from parser_review import (
    PARSER_REVIEW_FILTERS,
    load_parser_review_page_data,
)
from parser_accuracy import IMPORT_ACCURACY_SCAN_LIMIT, load_ai_health_dashboard, load_parser_accuracy_dashboard
from parser_workbench import (
    CONDITION_FIX_OPTIONS,
    WORKBENCH_CURRENCIES,
    apply_workbench_fix_and_finalize,
)
from import_status import (
    filter_discarded_import_logs,
    format_import_status,
    import_status_class,
    import_status_reason,
    is_discarded_no_watch_import,
    normalize_import_status,
)
from database import (
    ClientDeleteBlockedError,
    create_client_contact,
    create_request,
    delete_client_permanently,
    delete_request,
    get_active_offers_for_watch,
    get_active_offers_for_dealer,
    get_client,
    get_client_by_id,
    get_client_profile,
    get_dealer_by_id,
    get_import_log,
    get_import_logs_by_ids,
    get_import_logs_by_message_ids,
    get_message_by_id,
    get_messages_by_ids,
    get_notification_by_id,
    get_request,
    get_watch_by_id,
    dealer_contact_type,
    dealer_has_offers,
    dealer_is_business_visible,
    list_clients,
    list_active_sourcing_offers,
    list_client_match_history,
    list_contacts,
    list_contacts_for_import_lookup,
    list_client_profiles_by_client_ids,
    list_dealers,
    list_dealer_import_activity_logs,
    list_dealer_offer_counts,
    list_parser_accuracy_import_logs,
    list_parser_review_import_log_candidates,
    list_import_logs,
    list_notifications,
    list_offer_intelligence_rows,
    list_request_matches_for_offer,
    load_enriched_request_matches_by_request_ids,
    list_requests,
    list_requests_for_client,
    list_users,
    create_user,
    update_user,
    set_user_status,
    reset_user_password,
    mark_all_notifications_read,
    mark_notification_read,
    mark_import_parser_issue_ignored,
    mark_import_parser_reviewed,
    delete_all_notifications,
    delete_notification,
    delete_read_notifications,
    create_brand_alias,
    update_client_name,
    update_client_profile,
    update_dealer_contact_type,
    update_request,
    update_request_status,
    list_pending_unknown_brands,
    list_pending_unknown_nicknames,
    mark_unknown_brand_ignored,
    mark_unknown_nickname_ignored,
    resolve_unknown_brand_with_alias,
    resolve_unknown_nickname_with_alias,
    watch_knowledge_supported,
    watch_identification_supported,
)
from brand_registry import invalidate_brand_registry_cache, list_canonical_brands
from watch_identifier import invalidate_identifier_cache
from evolution_client import (
    EvolutionAPIError,
    create_instance,
    get_default_instance_name,
    get_instance_status,
    get_whatsapp_page_state,
)
from evolution_webhook import WebhookProcessingError, handle_evolution_webhook
from ingest import ingest_message
from whatsapp_listener import start_whatsapp_listener, stop_whatsapp_listener
from whatsapp_ingest_config import log_startup_ingest_config, mark_app_started
from client_sourcing import (
    build_client_sourcing_dashboard,
    build_matching_offer_rows,
    find_matching_offers_for_client,
)
from client_intelligence import (
    build_client_list_rows,
    build_client_match_rows,
    build_client_profile,
    build_client_request_rows,
    build_client_wishlist,
    client_display_name,
    compute_client_stats,
    format_activity_timestamp,
)
from dealer_intelligence import (
    attach_dealer_offer_source_urls,
    build_dealer_offer_rows,
    build_dealer_profile,
    build_trader_dealer_list_rows,
    compute_dealer_stats,
    dealer_display_name,
    dealers_page_url,
    flatten_offer_intelligence_rows,
    format_dealer_stats,
    paginate_dealer_list_rows,
)
from dashboard_data import load_trading_desk
from performance_profiler import PROFILE_PAGES, build_performance_report
from market_requests import load_market_request_detail, load_market_request_rows
from match_detail import load_match_detail
from knowledge_intelligence import build_unknown_brand_rows, build_unknown_nickname_rows
from contact_classification import (
    CONTACT_TYPES,
    CONTACT_TYPE_CLIENT,
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_REMOVED,
    CONTACTS_FILTER_REMOVED,
    DEFAULT_CONTACTS_FILTER,
    RESTORE_CONTACT_TYPES,
    build_contact_rows,
    build_contacts_filter_options,
    build_dealer_lookup_by_whatsapp,
    filter_business_import_logs,
    filter_contact_rows,
    filter_dealer_list_rows_by_search,
    filter_records_by_contact_search,
    format_import_sender_label,
    is_business_contact,
    is_removed_contact,
    parse_contacts_filter,
    should_redact_import_sender,
)
from notification_quick_fix import apply_notification_quick_fix, build_quick_fix_prefills
from notifications import (
    build_notification_display,
    build_notification_filter_options,
    filter_notifications_by_type,
    get_unread_notification_count,
    load_message_previews_by_import_log_id,
    normalize_notification_filter,
    notification_filter_counts,
)
from permissions import (
    USER_ROLE_ADMIN,
    USER_ROLE_TRADER,
    USER_ROLE_VIEWER,
    USER_STATUS_ACTIVE,
    USER_STATUS_DISABLED,
    can_access_admin_tools,
    can_manage_team,
    can_quick_fix_notifications,
    can_view_page,
    can_write,
    is_admin,
    is_viewer,
)
from navigation import nav_current_path, nav_group_active, nav_item_active, visible_nav_groups
from team_management import (
    build_team_user_rows,
    validate_new_user,
    validate_user_update,
)
from auth import (
    authenticate_email,
    get_current_user,
    is_public_path,
    login_user,
    logout_user,
    redirect_to_login,
    session_secret_key,
)
from user_visibility import (
    can_manage_request,
    can_view_import,
    filter_contacts_for_user,
    filter_contacts_page_for_user,
    filter_imports_for_user,
)
from request_profit import (
    attach_profit_to_matches,
    build_request_profit_summary,
    build_requests_dashboard_summary,
)
from condition_normalizer import offer_condition_display, parse_condition_filter
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
logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    mark_app_started()
    log_startup_ingest_config()
    start_whatsapp_listener()
    yield
    stop_whatsapp_listener()


app = FastAPI(title="MRV4ULT AI Dashboard", lifespan=app_lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["unread_notification_count"] = get_unread_notification_count


def _template_current_user(request: Request):
    return get_current_user(request)


templates.env.globals["current_user"] = _template_current_user
templates.env.globals["is_admin"] = is_admin
templates.env.globals["is_viewer"] = is_viewer
templates.env.globals["can_manage_team"] = can_manage_team
templates.env.globals["can_view_page"] = can_view_page
templates.env.globals["can_quick_fix"] = can_quick_fix_notifications
templates.env.globals["visible_nav_groups"] = visible_nav_groups
templates.env.globals["nav_current_path"] = nav_current_path
templates.env.globals["nav_group_active"] = nav_group_active
templates.env.globals["nav_item_active"] = nav_item_active
templates.env.globals["request_condition_options"] = REQUEST_CONDITION_FORM_OPTIONS


def _forbidden_response(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


@app.middleware("http")
async def enforce_role_permissions(request: Request, call_next):
    if is_public_path(request.url.path):
        return await call_next(request)

    user = get_current_user(request)
    if user is None:
        return await call_next(request)

    path = request.url.path
    if request.method == "GET" and not can_view_page(user, path):
        return _forbidden_response("Access denied")

    if request.method == "POST":
        if not can_write(user, path, method="POST"):
            return _forbidden_response("Read-only access")
        if path.startswith("/settings/team") and not can_manage_team(user):
            return _forbidden_response("Admin access required")

    return await call_next(request)


@app.middleware("http")
async def require_authenticated_user(request: Request, call_next):
    if is_public_path(request.url.path):
        return await call_next(request)
    if get_current_user(request) is None:
        return redirect_to_login()
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=session_secret_key())


@app.get("/health")
async def health_check() -> JSONResponse:
    return JSONResponse({"status": "ok", "app": "MRV4ULT AI"})


@app.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(request: Request, error: str = "") -> HTMLResponse:
    if get_current_user(request) is not None:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error.strip() or None},
    )


@app.post("/login")
async def login_submit(request: Request, email: str = Form(...)) -> RedirectResponse:
    user = authenticate_email(email)
    if user is None:
        return RedirectResponse(url="/login?error=unknown-email", status_code=303)
    login_user(request, user)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/logout")
async def logout_submit(request: Request) -> RedirectResponse:
    logout_user(request)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/users", response_class=HTMLResponse, name="users_page")
async def users_page(request: Request) -> RedirectResponse:
    user = get_current_user(request)
    if not can_manage_team(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return RedirectResponse(url="/settings/team", status_code=303)


@app.get("/settings/team", response_class=HTMLResponse, name="settings_team")
async def settings_team_page(
    request: Request,
    created: str = "",
    updated: str = "",
    status_changed: str = "",
    password_reset: str = "",
    error: str = "",
) -> HTMLResponse:
    user = get_current_user(request)
    if not can_manage_team(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return templates.TemplateResponse(
        request,
        "settings_team.html",
        {
            "users": build_team_user_rows(list_users()),
            "roles": [
                {"value": USER_ROLE_ADMIN, "label": "Admin"},
                {"value": USER_ROLE_TRADER, "label": "Trader"},
                {"value": USER_ROLE_VIEWER, "label": "Viewer"},
            ],
            "flash_created": created == "1",
            "flash_updated": updated == "1",
            "flash_status_changed": status_changed == "1",
            "flash_password_reset": password_reset,
            "error": error.strip() or None,
        },
    )


@app.post("/settings/team/create")
async def settings_team_create(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(USER_ROLE_TRADER),
) -> RedirectResponse:
    validation_error = validate_new_user(name, email, role)
    if validation_error:
        return RedirectResponse(
            url=f"/settings/team?error={quote(validation_error)}",
            status_code=303,
        )
    create_user(name=name, email=email, role=role)
    return RedirectResponse(url="/settings/team?created=1", status_code=303)


@app.post("/settings/team/{user_id}/update")
async def settings_team_update(
    user_id: str,
    name: str = Form(...),
    role: str = Form(...),
) -> RedirectResponse:
    validation_error = validate_user_update(name, role)
    if validation_error:
        return RedirectResponse(
            url=f"/settings/team?error={quote(validation_error)}",
            status_code=303,
        )
    update_user(user_id, name=name, role=role)
    return RedirectResponse(url="/settings/team?updated=1", status_code=303)


@app.post("/settings/team/{user_id}/toggle-status")
async def settings_team_toggle_status(
    user_id: str,
    action: str = Form(...),
) -> RedirectResponse:
    if action == "disable":
        set_user_status(user_id, USER_STATUS_DISABLED)
    elif action == "enable":
        set_user_status(user_id, USER_STATUS_ACTIVE)
    else:
        raise HTTPException(status_code=400, detail="Invalid status action")
    return RedirectResponse(url="/settings/team?status_changed=1", status_code=303)


@app.post("/settings/team/{user_id}/reset-password")
async def settings_team_reset_password(user_id: str) -> RedirectResponse:
    message = reset_user_password(user_id)
    return RedirectResponse(
        url=f"/settings/team?password_reset={quote(message)}",
        status_code=303,
    )


@app.get("/performance-profile", response_class=HTMLResponse, name="performance_profile")
async def performance_profile_page(request: Request) -> HTMLResponse:
    user = get_current_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    rows = build_performance_report(app, current_user=user)
    return templates.TemplateResponse(
        request,
        "performance_profile.html",
        {
            "rows": rows,
            "profile_pages": PROFILE_PAGES,
        },
    )


@app.get("/dashboard", response_class=HTMLResponse, name="dashboard")
async def dashboard_page(request: Request) -> HTMLResponse:
    user = get_current_user(request)
    desk = load_trading_desk(user, format_timestamp=format_timestamp)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "dashboard_user": user,
            "desk": desk,
        },
    )


@app.get("/market-requests", response_class=HTMLResponse, name="market_requests")
async def market_requests_page(
    request: Request,
    brand: str = "",
    reference: str = "",
    group: str = "",
) -> HTMLResponse:
    user = get_current_user(request)
    brand_filter = brand.strip()
    reference_filter = reference.strip()
    group_filter = group.strip()
    return templates.TemplateResponse(
        request,
        "market_requests.html",
        {
            "market_requests": load_market_request_rows(
                user,
                brand=brand_filter,
                reference=reference_filter,
                group=group_filter,
            ),
            "brand_filter": brand_filter,
            "reference_filter": reference_filter,
            "group_filter": group_filter,
        },
    )


@app.get("/market-requests/{import_id}", response_class=HTMLResponse, name="market_request_detail")
async def market_request_detail_page(request: Request, import_id: str) -> HTMLResponse:
    user = get_current_user(request)
    detail = load_market_request_detail(user, import_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Market request not found")
    return templates.TemplateResponse(
        request,
        "market_request_detail.html",
        {"detail": detail},
    )


@app.get("/matches/{match_id}", response_class=HTMLResponse, name="match_detail")
async def match_detail_page(request: Request, match_id: str) -> HTMLResponse:
    user = get_current_user(request)
    detail = load_match_detail(user, match_id, format_timestamp=format_timestamp)
    if detail is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return templates.TemplateResponse(
        request,
        "match_detail.html",
        {"detail": detail},
    )


def build_notification_rows(notifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previews = load_message_previews_by_import_log_id(notifications)
    needs_review_import_ids = [
        str(notification["related_import_log_id"])
        for notification in notifications
        if notification.get("type") == "needs_review" and notification.get("related_import_log_id")
    ]
    import_logs_by_id = (
        get_import_logs_by_ids(list(dict.fromkeys(needs_review_import_ids)))
        if needs_review_import_ids
        else {}
    )
    quick_fix_prefills = build_quick_fix_prefills(
        notifications,
        import_logs_by_id=import_logs_by_id,
        message_previews_by_import_log_id=previews,
    )
    rows: list[dict[str, Any]] = []
    for notification in notifications:
        row = build_notification_display(notification)
        row["created_at"] = format_timestamp(row.get("created_at"))
        import_log_id = notification.get("related_import_log_id")
        if import_log_id:
            preview = previews.get(str(import_log_id))
            if preview:
                row["message_preview"] = preview
        if notification.get("type") == "needs_review":
            row["show_quick_fix"] = True
            prefill = quick_fix_prefills.get(str(notification["id"]))
            if prefill:
                row["quick_fix_prefill"] = prefill
        rows.append(row)
    return rows


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
        dealer_id = cheapest_offer.get("dealer_id")
        source_url = cheapest_offer.get("source_url")

        dealer_contact = format_dealer_contact(dealer)
        condition_label, raw_condition = offer_condition_display(cheapest_offer.get("condition"))
        watch_id = group.get("watch_id")

        rows.append(
            {
                "watch_id": watch_id,
                "brand": _display_value(watch.get("brand")),
                "reference": _display_value(watch.get("reference")),
                "dial": _display_value(watch.get("dial")),
                "bracelet": _display_value(watch.get("bracelet")),
                "lowest_price": format_usd_price(group.get("lowest_usd")),
                "dealer_primary": dealer_contact["primary"],
                "dealer_secondary": dealer_contact["secondary"],
                "dealer_id": dealer_id,
                "dealer_url": f"/dealers/{dealer_id}" if dealer_id else None,
                "watch_url": f"/watch/{watch_id}" if watch_id else None,
                "source_url": source_url,
                "condition": condition_label,
                "raw_condition": raw_condition,
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
    normalized["message_id"] = message.get("id")
    normalized["received_at"] = message.get("received_at")
    normalized["group_id"] = message.get("group_id")
    group = _nested_record(message.get("groups"))
    normalized["group_name"] = group.get("name")
    return normalized


def normalize_dealer_offer(offer: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested watch and message data onto a dealer offer record."""
    normalized = normalize_offer(offer)
    normalized["watch"] = _nested_record(offer.get("watches"))
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
    return format_display_timestamp(value)


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
                "condition": display_condition(offer.get("condition")),
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
    return format_display_timestamp(value)


WATCH_OFFER_CARD_FIELDS: list[tuple[str, str]] = [
    ("brand", "Brand"),
    ("reference", "Reference"),
    ("model", "Model"),
    ("nickname", "Nickname"),
    ("dial", "Dial"),
    ("bracelet", "Bracelet"),
    ("condition", "Condition"),
    ("card_date", "Card date"),
    ("original_price_display", "Original price"),
    ("original_currency", "Currency"),
    ("usd_price_display", "USD price"),
    ("notes", "Notes"),
]


def _has_display_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and (not value.strip() or value.strip().upper() == "N/A"):
        return False
    return True


def _card_text(row: dict[str, Any], watch: dict[str, Any], row_key: str, watch_key: str) -> str | None:
    value = row.get(row_key)
    if not _has_display_value(value):
        value = watch.get(watch_key)
    if not _has_display_value(value):
        return None
    if row_key in {"brand", "model", "dial", "bracelet", "reference"} and isinstance(value, str):
        return _display_value(value)
    return str(value)


def _reference_card_value(row: dict[str, Any], watch: dict[str, Any]) -> str | None:
    reference = row.get("reference")
    if not _has_display_value(reference) or reference == "N/A":
        reference = watch.get("reference")
    if _has_display_value(reference) and reference != "N/A":
        return _display_value(str(reference))

    model_alias = watch.get("model_alias")
    if isinstance(model_alias, dict):
        if model_alias.get("reference_status") == "Unknown":
            return "Unknown"
        possible_reference = model_alias.get("possible_reference")
        if _has_display_value(possible_reference):
            return str(possible_reference)

    return None


def _build_watch_offer_card(row: dict[str, Any], watch: dict[str, Any], index: int) -> dict[str, Any]:
    enriched_watch = (
        watch
        if isinstance(watch.get("model_alias"), dict)
        else enrich_with_model_alias(dict(watch))
    )

    original_price = row.get("original_price")
    if original_price is None:
        original_price = enriched_watch.get("original_price") or enriched_watch.get("price")
    original_currency = row.get("original_currency") or enriched_watch.get("original_currency") or enriched_watch.get("currency")
    usd_price = row.get("usd_price")
    if usd_price is None:
        usd_price = enriched_watch.get("usd_price")

    merged = {
        "brand": _card_text(row, enriched_watch, "brand", "brand"),
        "reference": _reference_card_value(row, enriched_watch),
        "model": _card_text(row, enriched_watch, "model", "model"),
        "nickname": _card_text(row, enriched_watch, "nickname", "nickname"),
        "dial": _card_text(row, enriched_watch, "dial", "dial"),
        "bracelet": _card_text(row, enriched_watch, "bracelet", "bracelet"),
        "condition": _card_text(row, enriched_watch, "condition", "condition"),
        "card_date": _card_text(row, enriched_watch, "card_date", "card_date"),
        "original_price_display": row.get("price")
        or (format_price(original_price, original_currency) if original_price is not None else None),
        "original_currency": original_currency if _has_display_value(original_currency) else None,
        "usd_price_display": format_usd_price(usd_price) if usd_price is not None else None,
        "notes": _card_text(row, enriched_watch, "notes", "notes"),
    }

    fields = [
        {"label": label, "value": merged[key]}
        for key, label in WATCH_OFFER_CARD_FIELDS
        if _has_display_value(merged.get(key))
    ]

    title = _watch_card_identity_title(row, watch, index, enriched_watch=enriched_watch)

    intelligence_fields: list[dict[str, str]] = []
    for key, label in (
        ("rank", "Rank"),
        ("previous_lowest_usd", "Previous lowest"),
        ("price_difference", "Difference vs lowest"),
    ):
        value = row.get(key)
        if _has_display_value(value):
            intelligence_fields.append({"label": label, "value": str(value)})

    knowledge = enriched_watch.get("knowledge")
    if not isinstance(knowledge, dict):
        knowledge = lookup_reference(enriched_watch.get("reference") or row.get("reference"))
    knowledge_fields = knowledge_display_fields(knowledge) if isinstance(knowledge, dict) else []

    model_alias = enriched_watch.get("model_alias")
    alias_fields = alias_display_fields(model_alias) if isinstance(model_alias, dict) else []

    return {
        "title": title,
        "fields": fields,
        "alias_fields": alias_fields,
        "knowledge_fields": knowledge_fields,
        "intelligence_fields": intelligence_fields,
        "price_label": row.get("price_label"),
        "price_label_class": row.get("price_label_class"),
        "results": row.get("results") or [],
        "matched_requests": _matched_request_fields(row, enriched_watch),
    }


def _matched_request_fields(
    row: dict[str, Any],
    watch: dict[str, Any],
) -> list[dict[str, str]]:
    stored_matches = row.get("request_matches")
    if isinstance(stored_matches, list) and stored_matches:
        return [
            {
                "client_name": match.get("client_name") or "Client",
                "match_strength": match.get("match_strength") or "",
                "match_reason": match.get("match_reason") or "",
            }
            for match in stored_matches
            if isinstance(match, dict)
        ]

    offer_id = row.get("offer_id")
    if not offer_id:
        return []

    return [
        {
            "client_name": (match.get("requests") or {}).get("client_name") or "Client",
            "match_strength": match.get("match_strength") or "",
            "match_reason": match.get("match_reason") or "",
        }
        for match in list_request_matches_for_offer(str(offer_id))
    ]


REQUEST_STATUS_LABELS = {
    "open": "Open",
    "active": "Open",
    "matched": "Matched",
    "closed": "Closed",
}


def request_status_label(status: str | None) -> str:
    if not status:
        return "Unknown"
    return REQUEST_STATUS_LABELS.get(status.lower(), status.replace("_", " ").title())


def request_status_class(status: str | None) -> str:
    return {
        "open": "primary",
        "active": "primary",
        "matched": "success",
        "closed": "secondary",
    }.get((status or "").lower(), "secondary")


def _format_dealer_name(import_log: dict[str, Any]) -> str:
    alias = (import_log.get("dealer_alias") or "").strip()
    if alias:
        return alias
    whatsapp = (import_log.get("dealer_whatsapp") or "").strip()
    if whatsapp:
        return whatsapp
    group_name = (import_log.get("group_name") or "").strip()
    return group_name or "Unknown dealer"


def build_request_row(
    request: dict[str, Any],
    *,
    matches: list[dict[str, Any]] | None = None,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if matches is None:
        matches = load_enriched_request_matches_by_request_ids([str(request["id"])]).get(
            str(request["id"]),
            [],
        )

    profit_matches = attach_profit_to_matches(request, matches)
    matched_offers: list[dict[str, Any]] = []
    for match in profit_matches:
        offer = match.get("offer") or {}
        watch = match.get("watch") or {}
        import_log = match.get("import_log") or {}
        profit = match.get("profit") or {}
        matched_offers.append(
            {
                "match_strength": match.get("match_strength") or "",
                "match_reason": match.get("match_reason") or "",
                "offer_label": " · ".join(
                    part
                    for part in (
                        watch.get("brand"),
                        watch.get("reference") or watch.get("model"),
                    )
                    if part
                )
                or "Offer",
                "dealer": _format_dealer_name(import_log),
                "price": profit.get("offer_price") or "N/A",
                "budget": profit.get("budget") or "—",
                "potential_profit": profit.get("potential_profit") or "—",
                "potential_profit_usd": profit.get("potential_profit_usd"),
                "margin": profit.get("margin") or "—",
                "budget_difference": profit.get("budget_difference") or "—",
                "import_time": format_timestamp(import_log.get("import_time")),
                "import_log_id": import_log.get("id"),
                "status_label": profit.get("status_label") or "—",
                "status_class": profit.get("status_class") or "secondary",
            }
        )

    profit_summary = build_request_profit_summary({"matched_offers": matched_offers})

    return {
        "id": request["id"],
        "client_name": request.get("client_name") or "N/A",
        "brand": request.get("brand") or "—",
        "reference": request.get("reference") or "—",
        "model": request.get("model") or "—",
        "alias": request.get("alias") or "—",
        "dial": request.get("dial") or "—",
        "condition": request_condition_display(request.get("condition")),
        "year_range": _format_year_range(request.get("min_year"), request.get("max_year")),
        "max_price": _format_request_budget(request.get("max_price"), request.get("currency")),
        "notes": request.get("notes") or "",
        "status": request_status_label(request.get("status")),
        "status_class": request_status_class(request.get("status")),
        "created_at": format_timestamp(request.get("created_at")),
        "matched_offers": matched_offers,
        "has_matches": bool(matched_offers),
        "best_offer": profit_summary["best_offer"],
        "best_potential_profit": profit_summary["best_potential_profit"],
        "best_margin": profit_summary["best_margin"],
        "match_count": profit_summary["match_count"],
        "can_manage": can_manage_request(user, request),
    }


def build_request_rows(
    requests: list[dict[str, Any]],
    *,
    user: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    request_ids = [str(request["id"]) for request in requests]
    matches_by_request = load_enriched_request_matches_by_request_ids(request_ids)
    return [
        build_request_row(
            request,
            matches=matches_by_request.get(str(request["id"]), []),
            user=user,
        )
        for request in requests
    ]


def build_request_edit_form(request: dict[str, Any]) -> dict[str, Any]:
    status = (request.get("status") or "open").lower()
    if status == "active":
        status = "open"
    return {
        "id": request["id"],
        "client_name": request.get("client_name") or "",
        "brand": request.get("brand") or "",
        "reference": request.get("reference") or "",
        "model": request.get("model") or "",
        "alias": request.get("alias") or "",
        "dial": request.get("dial") or "",
        "condition": request_condition_form_value(request.get("condition")),
        "min_year": request.get("min_year") or "",
        "max_year": request.get("max_year") or "",
        "max_price": request.get("max_price") or "",
        "currency": request.get("currency") or "USD",
        "notes": request.get("notes") or "",
        "status": status,
    }


def _normalize_request_status(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned in {"open", "matched", "closed"}:
        return cleaned
    raise ValueError("Invalid request status. Use Open, Matched, or Closed.")


def _get_manageable_request(request_id: str, user: dict[str, Any] | None) -> dict[str, Any]:
    request = get_request(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if not can_manage_request(user, request):
        raise HTTPException(status_code=403, detail="Access denied")
    return request


def _format_year_range(min_year: Any, max_year: Any) -> str:
    if min_year and max_year:
        return f"{min_year}–{max_year}"
    if min_year:
        return f"{min_year}+"
    if max_year:
        return f"Up to {max_year}"
    return "—"


def _format_request_budget(max_price: Any, currency: Any) -> str:
    if max_price is None:
        return "—"
    return format_price(int(max_price), str(currency) if currency else None)


def _parse_optional_int(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    return int(cleaned)


def build_deal_analysis_cards(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Build deal analysis cards from watches stored during import."""
    rows = summary.get("rows") or []
    if not rows:
        watches = _deal_analysis_watch_sources(summary)
        return [_build_deal_analysis({}, watch, index) for index, watch in enumerate(watches)]

    parsed_watches = _import_parsed_watches(summary)
    offer_watches = summary.get("offer_watches") or []
    analyses: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        watch = _resolve_deal_analysis_watch(row, offer_watches, parsed_watches, index)
        analyses.append(_build_deal_analysis(row, watch, index))
    return analyses


def build_watch_offer_cards(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Build card view models from watches stored during import."""
    rows = summary.get("rows") or []
    if not rows:
        watches = _deal_analysis_watch_sources(summary)
        return [_build_watch_offer_card({}, watch, index) for index, watch in enumerate(watches)]

    parsed_watches = _import_parsed_watches(summary)
    offer_watches = summary.get("offer_watches") or []
    cards: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        watch = _resolve_deal_analysis_watch(row, offer_watches, parsed_watches, index)
        cards.append(_build_watch_offer_card(row, watch, index))
    return cards


DEAL_RECOMMENDATIONS: dict[str, tuple[str, str]] = {
    "New lowest price": ("Excellent Buy", "excellent"),
    "Good price": ("Good Buy", "good"),
    "Normal price": ("Fair Price", "market"),
    "Expensive": ("Expensive", "expensive"),
    "Duplicate offer": ("Fair Price", "market"),
}

DEAL_EXCELLENT_CONFIDENCE_THRESHOLD = 75

DEAL_CONDITION_ICONS: dict[str, str] = {
    NEW_CONDITION: "🟢",
    PRE_OWNED_CONDITION: "🟡",
    "Unknown": "⚪",
}


def _import_parsed_watches(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Return parsed watches stored at import time."""
    watches = summary.get("parsed_watches")
    if isinstance(watches, list):
        return watches
    return []


def _deal_analysis_watch_sources(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one stored watch object per import offer row."""
    if summary.get("status") == "insufficient_evidence":
        return []
    if summary.get("import_classification") == "insufficient_evidence":
        return []
    if summary.get("import_classification") == "request_intent":
        return []

    rows = summary.get("rows") or []
    if rows:
        parsed_watches = _import_parsed_watches(summary)
        offer_watches = summary.get("offer_watches") or []
        return [
            _resolve_deal_analysis_watch(row, offer_watches, parsed_watches, index)
            for index, row in enumerate(rows)
        ]

    parsed_watches = _import_parsed_watches(summary)
    if parsed_watches:
        return parsed_watches

    return rows


def _normalize_watch_identity_token(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned or cleaned.upper() in {"N/A", "UNKNOWN"}:
        return None
    return cleaned.lower()


def _watch_identity_matches(row: dict[str, Any], watch: dict[str, Any]) -> bool:
    row_ref = _normalize_watch_identity_token(row.get("reference"))
    watch_ref = _normalize_watch_identity_token(watch.get("reference"))
    if row_ref and watch_ref and row_ref != watch_ref:
        return False
    if row_ref and not watch_ref:
        return False
    if watch_ref and not row_ref:
        return False

    row_brand = _normalize_watch_identity_token(row.get("brand"))
    watch_brand = _normalize_watch_identity_token(watch.get("brand"))
    if row_brand and watch_brand and row_brand != watch_brand:
        return False

    return bool(row_ref or watch_ref or row_brand or watch_brand)


def _watch_context_from_row(row: dict[str, Any]) -> dict[str, Any]:
    watch: dict[str, Any] = {}
    for key in ("brand", "reference", "model", "condition", "raw_condition", "usd_price", "confidence"):
        value = row.get(key)
        if value is not None:
            watch[key] = value
    return watch


def _merge_deal_analysis_watch_context(row: dict[str, Any], watch: dict[str, Any]) -> dict[str, Any]:
    """Fill missing identity fields on a resolved watch from the aligned summary row."""
    merged = dict(watch)
    for key in ("brand", "reference", "model", "condition", "raw_condition", "usd_price", "confidence"):
        row_value = row.get(key)
        watch_value = merged.get(key)
        if key == "reference":
            if (
                not _has_display_value(watch_value)
                or watch_value == "N/A"
            ) and _has_display_value(row_value) and row_value != "N/A":
                merged[key] = row_value
            continue
        if not _has_display_value(watch_value) and _has_display_value(row_value):
            merged[key] = row_value
    return merged


def _resolve_deal_analysis_watch(
    row: dict[str, Any],
    offer_watches: list[dict[str, Any]],
    parsed_watches: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    """Match a summary row to the parsed offer watch used during ingest."""
    if index < len(offer_watches):
        candidate = offer_watches[index]
        if _watch_identity_matches(row, candidate):
            return _merge_deal_analysis_watch_context(row, candidate)

    for candidates in (offer_watches, parsed_watches):
        for candidate in candidates:
            if _watch_identity_matches(row, candidate):
                return _merge_deal_analysis_watch_context(row, candidate)

    if index < len(offer_watches):
        return _merge_deal_analysis_watch_context(row, offer_watches[index])
    if index < len(parsed_watches):
        return _merge_deal_analysis_watch_context(row, parsed_watches[index])
    return _watch_context_from_row(row)


def _parse_usd_amount(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if not _has_display_value(value):
        return None
    cleaned = re.sub(r"[^\d]", "", str(value))
    if not cleaned:
        return None
    return int(cleaned)


def _format_signed_usd(amount: int | None) -> str:
    if amount is None:
        return "N/A"
    if amount == 0:
        return "$0"
    if amount > 0:
        return f"+${amount:,}"
    return f"-${abs(amount):,}"


def _recommendation_from_prices(offer_usd: int, market_usd: int) -> tuple[str, str]:
    if offer_usd < market_usd:
        if offer_usd <= market_usd * 0.97:
            return "Excellent Buy", "excellent"
        return "Good Buy", "good"
    if offer_usd <= market_usd * 1.03:
        return "Fair Price", "market"
    if offer_usd <= market_usd * 1.10:
        return "Fair Price", "market"
    return "Expensive", "expensive"


def _resolve_deal_recommendation(
    price_label: str | None,
    offer_usd: int | None,
    market_usd: int | None,
    *,
    comparison_safe: bool,
    confidence: int,
) -> tuple[str, str]:
    if not comparison_safe or market_usd is None:
        return "Needs Review", "insufficient"

    recommendation: tuple[str, str] | None = None
    if price_label and price_label in DEAL_RECOMMENDATIONS:
        recommendation = DEAL_RECOMMENDATIONS[price_label]
    elif offer_usd is not None:
        recommendation = _recommendation_from_prices(offer_usd, market_usd)

    if recommendation is None:
        return "Needs Review", "insufficient"

    label, recommendation_class = recommendation
    return label, recommendation_class


def _deal_recommendation_confidence(
    watch: dict[str, Any],
    row: dict[str, Any],
    *,
    offer_usd: int | None,
    market_usd: int | None,
    price_label: str | None,
) -> int:
    score = 35
    parser_confidence = watch.get("confidence")
    if isinstance(parser_confidence, int):
        score += min(parser_confidence // 4, 20)

    if offer_usd is not None:
        score += 15
    if market_usd is not None:
        score += 20
    if price_label and price_label != "Duplicate offer":
        score += 10
    elif price_label == "Duplicate offer":
        score += 5
    if _has_display_value(row.get("rank")):
        score += 10
    if _has_display_value(row.get("price_difference")):
        score += 5

    return min(score, 100)


def _watch_card_identity_title(
    row: dict[str, Any],
    watch: dict[str, Any],
    index: int,
    *,
    enriched_watch: dict[str, Any] | None = None,
) -> str:
    """Build Brand · Reference/Model/Unknown reference for import cards."""
    enriched = enriched_watch or (
        watch
        if isinstance(watch.get("model_alias"), dict)
        else enrich_with_model_alias(dict(watch))
    )
    brand = _card_text(row, enriched, "brand", "brand")
    if not _has_display_value(brand):
        return f"Watch offer {index + 1}"

    reference = _reference_card_value(row, enriched)
    if _has_display_value(reference) and reference != "Unknown":
        secondary = reference
    else:
        model = _card_text(row, enriched, "model", "model")
        if _has_display_value(model):
            secondary = model
        elif _has_display_value(reference):
            secondary = reference
        else:
            secondary = "Unknown reference"

    return f"{brand} · {secondary}"


def _deal_analysis_title(row: dict[str, Any], watch: dict[str, Any], index: int) -> str:
    return _watch_card_identity_title(row, watch, index)


def _deal_offer_condition(row: dict[str, Any], watch: dict[str, Any]) -> str | None:
    return resolve_offer_wear_condition(
        row.get("condition"),
        watch.get("condition"),
        row.get("raw_condition"),
        watch.get("raw_condition"),
    )


def _deal_condition_display(row: dict[str, Any], watch: dict[str, Any]) -> dict[str, Any]:
    for source in (
        row.get("condition"),
        watch.get("condition"),
        row.get("raw_condition"),
        watch.get("raw_condition"),
    ):
        label = deal_condition_label(source)
        if label != "Unknown":
            return {
                "label": label,
                "icon": DEAL_CONDITION_ICONS[label],
                "is_known": True,
            }
    return {
        "label": "Unknown",
        "icon": DEAL_CONDITION_ICONS["Unknown"],
        "is_known": False,
    }


def _deal_comparison_is_safe(row: dict[str, Any], watch: dict[str, Any]) -> bool:
    offer_condition = _deal_offer_condition(row, watch)
    if offer_condition is None:
        return False
    market_condition = normalize_condition_value(row.get("market_condition"))
    if market_condition not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return False
    if offer_condition != market_condition:
        return False
    if row.get("price_label") == "No comparables":
        return False
    if not _has_display_value(row.get("previous_lowest_usd")):
        return False
    market_usd = _parse_usd_amount(row.get("previous_lowest_usd"))
    if market_usd is None or market_usd <= 0:
        return False
    return True


def _build_deal_analysis(row: dict[str, Any], watch: dict[str, Any], index: int) -> dict[str, Any]:
    offer_usd = row.get("usd_price")
    if offer_usd is None:
        offer_usd = watch.get("usd_price")

    condition = _deal_condition_display(row, watch)
    comparison_safe = _deal_comparison_is_safe(row, watch)
    stored_market_usd = _parse_usd_amount(row.get("previous_lowest_usd"))
    market_usd = stored_market_usd if comparison_safe else None
    price_label = row.get("price_label")
    has_market = market_usd is not None

    difference_usd: int | None = None
    difference_pct: str | None = None
    market_position_label: str | None = None
    market_position_amount: str | None = None
    potential_profit: int | None = None
    if has_market and offer_usd is not None and market_usd > 0:
        difference_usd = offer_usd - market_usd
        difference_pct = f"{((difference_usd / market_usd) * 100):+.1f}%"
        if difference_usd > 0:
            market_position_label = "Above market"
            market_position_amount = _format_signed_usd(difference_usd)
            potential_profit = 0
        elif difference_usd < 0:
            market_position_label = "Below market"
            market_position_amount = _format_signed_usd(difference_usd)
            potential_profit = market_usd - offer_usd
        else:
            potential_profit = 0

    confidence = _deal_recommendation_confidence(
        watch,
        row,
        offer_usd=offer_usd,
        market_usd=market_usd,
        price_label=price_label,
    )
    recommendation, recommendation_class = _resolve_deal_recommendation(
        price_label,
        offer_usd,
        market_usd,
        comparison_safe=comparison_safe,
        confidence=confidence,
    )
    parser_confidence = watch.get("confidence")
    if recommendation == "Excellent Buy" and (
        not isinstance(parser_confidence, int)
        or parser_confidence < DEAL_EXCELLENT_CONFIDENCE_THRESHOLD
    ):
        recommendation, recommendation_class = "Good Buy", "good"

    show_condition_warning = not condition["is_known"]
    show_no_matching_market = condition["is_known"] and not comparison_safe

    if has_market:
        market_price_display = format_usd_price(market_usd)
    else:
        market_price_display = "Unknown"

    return {
        "title": _deal_analysis_title(row, watch, index),
        "condition_label": condition["label"],
        "condition_icon": condition["icon"],
        "condition_is_known": condition["is_known"],
        "show_condition_warning": show_condition_warning,
        "show_no_matching_market": show_no_matching_market,
        "offer_price": format_usd_price(offer_usd) if offer_usd is not None else "N/A",
        "market_price": market_price_display,
        "show_market_metrics": has_market,
        "difference": _format_signed_usd(difference_usd) if has_market else None,
        "difference_pct": difference_pct,
        "market_rank_display": (
            f"#{row.get('rank')}" if has_market and _has_display_value(row.get("rank")) else None
        ),
        "market_position_label": market_position_label,
        "market_position_amount": market_position_amount,
        "show_market_position": market_position_label is not None,
        "recommendation": recommendation,
        "recommendation_class": recommendation_class,
        "potential_profit": format_usd_price(potential_profit) if has_market else None,
        "potential_profit_positive": has_market and potential_profit is not None and potential_profit > 0,
    }


def _parse_signed_usd(value: Any) -> int | None:
    if not _has_display_value(value):
        return None
    text = str(value).strip()
    negative = text.startswith("-")
    amount = _parse_usd_amount(text)
    if amount is None:
        return None
    return -amount if negative else amount


def build_activity_row(import_log: dict[str, Any]) -> dict[str, Any]:
    """Format one import log for the activity list."""
    status = normalize_import_status(import_log)
    sender_redacted = should_redact_import_sender(import_log)
    return {
        "id": import_log["id"],
        "import_time": format_timestamp(import_log.get("import_time")),
        "group_name": import_log.get("group_name") or "N/A",
        "dealer_alias": format_import_sender_label(import_log),
        "dealer_whatsapp": "—" if sender_redacted else (import_log.get("dealer_whatsapp") or "N/A"),
        "watches_parsed": import_log.get("watches_parsed", 0),
        "new_offers": import_log.get("new_offers", 0),
        "duplicate_offers": import_log.get("duplicate_offers", 0),
        "matched_requests": import_log.get("matched_requests", 0),
        "processing_time": import_log.get("processing_time") or "N/A",
        "status": format_import_status(status),
        "status_class": import_status_class(status),
    }


def build_activity_detail(
    import_log: dict[str, Any],
    message: dict[str, Any] | None,
) -> dict[str, Any]:
    """Format one import log for the detail page."""
    summary = import_log.get("summary") or {}
    message = message or {}
    status = normalize_import_status(import_log)
    rows = summary.get("rows") or []
    raw_message = message.get("raw_text") or ""
    sender_redacted = should_redact_import_sender(import_log)
    return {
        "id": import_log["id"],
        "import_time": format_timestamp(import_log.get("import_time")),
        "group_name": import_log.get("group_name") or "N/A",
        "dealer_alias": None if sender_redacted else import_log.get("dealer_alias"),
        "dealer_label": format_import_sender_label(import_log),
        "dealer_whatsapp": "—" if sender_redacted else (import_log.get("dealer_whatsapp") or "N/A"),
        "watches_parsed": import_log.get("watches_parsed", 0),
        "new_watches": summary.get("new_watches", 0),
        "new_offers": import_log.get("new_offers", 0),
        "duplicate_offers": import_log.get("duplicate_offers", 0),
        "matched_requests": import_log.get("matched_requests", 0),
        "processing_time": import_log.get("processing_time") or "N/A",
        "status": format_import_status(status),
        "status_class": import_status_class(status),
        "status_reason": import_status_reason(import_log),
        "raw_message": raw_message,
        "deal_analyses": build_deal_analysis_cards(summary),
        "watch_cards": build_watch_offer_cards(summary),
        "rows": rows,
        "match_notification": (
            f"{import_log.get('matched_requests', 0)} client request(s) matched this import."
            if import_log.get("matched_requests", 0)
            else None
        ),
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


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request) -> JSONResponse:
    logger.info("[WhatsApp ingest] Webhook HTTP POST received")
    try:
        payload = await request.json()
    except Exception:
        logger.warning("[WhatsApp ingest] Message skipped: reason=invalid JSON body")
        return JSONResponse(
            {"status": "error", "reason": "invalid JSON"},
            status_code=400,
        )

    if not isinstance(payload, dict):
        logger.warning("[WhatsApp ingest] Message skipped: reason=payload must be a JSON object")
        return JSONResponse(
            {"status": "error", "reason": "payload must be a JSON object"},
            status_code=400,
        )

    try:
        result = handle_evolution_webhook(payload)
    except WebhookProcessingError as exc:
        logger.warning("[WhatsApp ingest] Message skipped: reason=%s", exc)
        return JSONResponse({"status": "ignored", "reason": str(exc)}, status_code=200)
    except Exception as exc:
        logger.exception("[WhatsApp ingest] Ingest failed with unexpected error")
        return JSONResponse({"status": "error", "reason": str(exc)}, status_code=200)

    logger.info(
        "[WhatsApp ingest] Webhook handled: status=%s watches_parsed=%s new_offers=%s import_log_id=%s",
        result.get("status"),
        result.get("watches_parsed"),
        result.get("new_offers"),
        result.get("import_log_id"),
    )
    return JSONResponse(result, status_code=200)


@app.get("/requests", response_class=HTMLResponse, name="requests_list")
async def requests_list(
    request: Request,
    status: str = "",
    client_id: str = "",
    client_name: str = "",
) -> HTMLResponse:
    status_filter = status.strip().lower() or None
    user = get_current_user(request)
    all_requests = list_requests()
    all_rows = build_request_rows(all_requests, user=user)
    if status_filter:
        filtered_ids = {
            str(item["id"])
            for item in all_requests
            if (item.get("status") or "").lower() == status_filter
        }
        request_rows = [row for row in all_rows if str(row["id"]) in filtered_ids]
    else:
        request_rows = all_rows
    summary = build_requests_dashboard_summary(all_rows, raw_requests=all_requests)
    return templates.TemplateResponse(
        request,
        "requests.html",
        {
            "requests": request_rows,
            "summary": summary,
            "status_filter": status_filter or "all",
            "saved": request.query_params.get("saved") == "1",
            "updated": request.query_params.get("updated") == "1",
            "deleted": request.query_params.get("deleted") == "1",
            "prefill_client_id": client_id.strip(),
            "prefill_client_name": client_name.strip(),
        },
    )


@app.post("/requests", response_class=HTMLResponse)
async def requests_create(
    request: Request,
    client_name: str = Form(""),
    client_id: str = Form(""),
    brand: str = Form(""),
    reference: str = Form(""),
    model: str = Form(""),
    alias: str = Form(""),
    dial: str = Form(""),
    condition: str = Form(""),
    min_year: str = Form(""),
    max_year: str = Form(""),
    max_price: str = Form(""),
    currency: str = Form("USD"),
    notes: str = Form(""),
) -> RedirectResponse:
    if not client_name.strip():
        return RedirectResponse(url="/requests?error=client", status_code=303)

    user = get_current_user(request)
    create_request(
        client_name=client_name,
        brand=brand or None,
        reference=reference or None,
        model=model or None,
        alias=alias or None,
        dial=dial or None,
        condition=parse_request_condition_form(condition),
        min_year=_parse_optional_int(min_year),
        max_year=_parse_optional_int(max_year),
        max_price=_parse_optional_int(max_price),
        currency=currency or None,
        notes=notes or None,
        client_id=client_id.strip() or None,
        created_by_user_id=str(user["id"]) if user and user.get("id") else None,
    )
    return RedirectResponse(url="/requests?saved=1", status_code=303)


@app.get("/requests/{request_id}/edit", response_class=HTMLResponse, name="request_edit")
async def request_edit_page(request: Request, request_id: str) -> HTMLResponse:
    user = get_current_user(request)
    client_request = _get_manageable_request(request_id, user)
    return templates.TemplateResponse(
        request,
        "request_edit.html",
        {"form": build_request_edit_form(client_request), "error": None},
    )


@app.post("/requests/{request_id}/edit")
async def request_edit_submit(
    request: Request,
    request_id: str,
    client_name: str = Form(""),
    brand: str = Form(""),
    reference: str = Form(""),
    model: str = Form(""),
    alias: str = Form(""),
    dial: str = Form(""),
    condition: str = Form(""),
    min_year: str = Form(""),
    max_year: str = Form(""),
    max_price: str = Form(""),
    currency: str = Form("USD"),
    notes: str = Form(""),
    status: str = Form("open"),
):
    user = get_current_user(request)
    client_request = _get_manageable_request(request_id, user)
    if not client_name.strip():
        return templates.TemplateResponse(
            request,
            "request_edit.html",
            {
                "form": {
                    **build_request_edit_form(client_request),
                    "client_name": client_name,
                    "brand": brand,
                    "reference": reference,
                    "model": model,
                    "alias": alias,
                    "dial": dial,
                    "condition": condition,
                    "min_year": min_year,
                    "max_year": max_year,
                    "max_price": max_price,
                    "currency": currency,
                    "notes": notes,
                    "status": status,
                },
                "error": "Client name is required.",
            },
            status_code=400,
        )

    try:
        normalized_status = _normalize_request_status(status)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "request_edit.html",
            {
                "form": build_request_edit_form(
                    {
                        **client_request,
                        "client_name": client_name,
                        "brand": brand or None,
                        "reference": reference or None,
                        "model": model or None,
                        "alias": alias or None,
                        "dial": dial or None,
                        "condition": parse_request_condition_form(condition),
                        "min_year": _parse_optional_int(min_year),
                        "max_year": _parse_optional_int(max_year),
                        "max_price": _parse_optional_int(max_price),
                        "currency": currency or None,
                        "notes": notes or None,
                        "status": status,
                    }
                ),
                "error": str(exc),
            },
            status_code=400,
        )

    update_request(
        request_id,
        client_name=client_name,
        brand=brand or None,
        reference=reference or None,
        model=model or None,
        alias=alias or None,
        dial=dial or None,
        condition=parse_request_condition_form(condition),
        min_year=_parse_optional_int(min_year),
        max_year=_parse_optional_int(max_year),
        max_price=_parse_optional_int(max_price),
        currency=currency or None,
        notes=notes or None,
        status=normalized_status,
    )
    return RedirectResponse(url="/requests?updated=1", status_code=303)


@app.post("/requests/{request_id}/delete")
async def request_delete(request: Request, request_id: str) -> RedirectResponse:
    user = get_current_user(request)
    _get_manageable_request(request_id, user)
    delete_request(request_id)
    return RedirectResponse(url="/requests?deleted=1", status_code=303)


@app.post("/requests/{request_id}/close")
async def requests_close(request: Request, request_id: str) -> RedirectResponse:
    user = get_current_user(request)
    _get_manageable_request(request_id, user)
    update_request_status(request_id, "closed")
    return RedirectResponse(url="/requests", status_code=303)


def _parser_review_import_logs(user: dict[str, Any] | None) -> list[dict[str, Any]]:
    visible = filter_discarded_import_logs(
        filter_imports_for_user(list_parser_review_import_log_candidates(), user)
    )
    lookup = build_dealer_lookup_by_whatsapp(list_contacts_for_import_lookup())
    return filter_business_import_logs(visible, lookup)


def _business_import_logs(import_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = build_dealer_lookup_by_whatsapp(list_contacts_for_import_lookup())
    return filter_business_import_logs(import_logs, lookup)


def _can_access_import_log(user: dict[str, Any] | None, import_log: dict[str, Any]) -> bool:
    return can_view_import(user, import_log)


ACTIVITY_TAB_DESCRIPTIONS = {
    "active": "Watch offers and items that still need attention.",
    "reviewed": "Imports marked as reviewed after parser review.",
    "ignored": "Noise, buyer requests, and dismissed parser issues.",
    "all": "Full import audit trail.",
}


def _render_activity_page(request: Request, tab: str) -> HTMLResponse:
    user = get_current_user(request)
    page = parse_activity_page(request.query_params.get("page"))
    activity_page = load_activity_page(user, tab, page=page)
    imports = [build_activity_row(import_log) for import_log in activity_page.imports]
    return templates.TemplateResponse(
        request,
        "activity.html",
        {
            "imports": imports,
            "stats": activity_page.stats,
            "active_tab": tab,
            "tab_description": ACTIVITY_TAB_DESCRIPTIONS[tab],
            "empty_message": activity_page.empty_message,
            "page": activity_page.page,
            "page_size": activity_page.page_size,
            "has_previous": activity_page.has_previous,
            "has_next": activity_page.has_next,
            "previous_page_url": activity_page_url(tab, activity_page.page - 1),
            "next_page_url": activity_page_url(tab, activity_page.page + 1),
            "showing_from": activity_page.showing_from,
            "showing_to": activity_page.showing_to,
        },
    )


@app.get("/activity", response_class=HTMLResponse, name="activity_list")
async def activity_list(request: Request) -> HTMLResponse:
    return _render_activity_page(request, "active")


@app.get("/activity/reviewed", response_class=HTMLResponse, name="activity_reviewed")
async def activity_reviewed(request: Request) -> HTMLResponse:
    return _render_activity_page(request, "reviewed")


@app.get("/activity/ignored", response_class=HTMLResponse, name="activity_ignored")
async def activity_ignored(request: Request) -> HTMLResponse:
    return _render_activity_page(request, "ignored")


@app.get("/activity/all", response_class=HTMLResponse, name="activity_all")
async def activity_all(request: Request) -> HTMLResponse:
    return _render_activity_page(request, "all")


def _parser_accuracy_import_logs(user: dict[str, Any] | None) -> list[dict[str, Any]]:
    return filter_imports_for_user(
        list_parser_accuracy_import_logs(limit=IMPORT_ACCURACY_SCAN_LIMIT),
        user,
    )


@app.get("/ai-health", response_class=HTMLResponse, name="ai_health")
async def ai_health_page(request: Request) -> HTMLResponse:
    user = get_current_user(request)
    if not can_access_admin_tools(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    import_logs = _parser_accuracy_import_logs(user)
    dashboard = load_ai_health_dashboard(import_logs)

    return templates.TemplateResponse(
        request,
        "ai_health.html",
        {
            "dashboard": dashboard,
        },
    )


@app.get("/parser-accuracy", response_class=HTMLResponse, name="parser_accuracy")
async def parser_accuracy_page(request: Request) -> HTMLResponse:
    """Backward-compatible redirect to the AI Health dashboard."""
    return RedirectResponse(url="/ai-health", status_code=307)


@app.get("/parser-review", response_class=HTMLResponse, name="parser_review")
async def parser_review_page(request: Request, filter: str = "all") -> HTMLResponse:
    user = get_current_user(request)
    if not can_access_admin_tools(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    filter_key = filter if filter in PARSER_REVIEW_FILTERS else "all"
    import_logs = _parser_review_import_logs(user)
    accuracy_logs = _parser_accuracy_import_logs(user)
    rows, counts = load_parser_review_page_data(
        import_logs,
        filter_key,
        format_timestamp=format_timestamp,
    )
    accuracy = load_ai_health_dashboard(accuracy_logs)

    return templates.TemplateResponse(
        request,
        "parser_review.html",
        {
            "rows": rows,
            "counts": counts,
            "accuracy": accuracy,
            "active_filter": filter_key,
            "canonical_brands": list_canonical_brands(),
            "knowledge_enabled": watch_knowledge_supported(),
            "condition_fix_options": CONDITION_FIX_OPTIONS,
            "workbench_currencies": WORKBENCH_CURRENCIES,
            "reviewed": request.query_params.get("reviewed") == "1",
            "ignored": request.query_params.get("ignored") == "1",
            "fixed": request.query_params.get("fixed") == "1",
            "alias_saved": request.query_params.get("alias_saved") == "1",
        },
    )


def _require_admin_workbench(request: Request) -> None:
    user = get_current_user(request)
    if not can_access_admin_tools(user):
        raise HTTPException(status_code=403, detail="Admin access required")


@app.post("/parser-review/{import_id}/reviewed")
async def parser_review_mark_reviewed(request: Request, import_id: str) -> RedirectResponse:
    _require_admin_workbench(request)
    import_log = get_import_log(import_id)
    if import_log is None:
        raise HTTPException(status_code=404, detail="Import not found")

    summary = import_log.get("summary") or {}
    if not summary.get("workbench_fix_applied") and not summary.get("parser_review_ignored"):
        raise HTTPException(
            status_code=400,
            detail="Apply a fix or ignore the issue before marking as reviewed",
        )

    mark_import_parser_reviewed(import_id)
    return RedirectResponse(url="/parser-review?reviewed=1", status_code=303)


@app.post("/parser-review/{import_id}/ignore")
async def parser_review_ignore_issue(
    request: Request,
    import_id: str,
    reason: str = Form(""),
) -> RedirectResponse:
    _require_admin_workbench(request)
    import_log = get_import_log(import_id)
    if import_log is None:
        raise HTTPException(status_code=404, detail="Import not found")

    mark_import_parser_issue_ignored(import_id, reason=reason)
    return RedirectResponse(url="/parser-review?ignored=1", status_code=303)


@app.post("/parser-review/{import_id}/fix")
async def parser_review_apply_fix(
    request: Request,
    import_id: str,
    fix_action: str = Form(...),
    brand_name: str = Form(""),
    reference: str = Form(""),
    model: str = Form(""),
    alias_text: str = Form(""),
    condition: str = Form(""),
    price: str = Form(""),
    currency: str = Form(""),
) -> RedirectResponse:
    _require_admin_workbench(request)
    import_log = get_import_log(import_id)
    if import_log is None:
        raise HTTPException(status_code=404, detail="Import not found")

    try:
        apply_workbench_fix_and_finalize(
            import_id,
            fix_action,
            brand_name=brand_name,
            reference=reference,
            model=model,
            alias_text=alias_text,
            condition=condition,
            price=price,
            currency=currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url="/parser-review?fixed=1", status_code=303)


@app.post("/parser-review/{import_id}/add-brand-alias")
async def parser_review_add_brand_alias(
    request: Request,
    import_id: str,
    brand_name: str = Form(...),
    alias_text: str = Form(...),
) -> RedirectResponse:
    _require_admin_workbench(request)
    import_log = get_import_log(import_id)
    if import_log is None:
        raise HTTPException(status_code=404, detail="Import not found")
    if not brand_name.strip():
        raise HTTPException(status_code=400, detail="Brand name is required")
    if not alias_text.strip():
        raise HTTPException(status_code=400, detail="Alias text is required")

    create_brand_alias(
        alias_key=alias_text.strip(),
        brand_name=brand_name.strip(),
        source="parser_review",
    )
    invalidate_brand_registry_cache()
    return RedirectResponse(url="/parser-review?alias_saved=1", status_code=303)


@app.get("/activity/{import_id}", response_class=HTMLResponse, name="activity_detail")
async def activity_detail(request: Request, import_id: str) -> HTMLResponse:
    user = get_current_user(request)
    import_log = get_import_log(import_id)
    if (
        import_log is None
        or not _can_access_import_log(user, import_log)
        or is_discarded_no_watch_import(import_log)
    ):
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
            current_user = get_current_user(request)
            summary = ingest_message(
                message_text,
                group_name=group_name_value,
                dealer_whatsapp=dealer_whatsapp_value,
                dealer_alias=dealer_alias_value or None,
                imported_by_user_id=current_user["id"] if current_user else None,
                source="manual_form",
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
    condition: str = "all",
) -> HTMLResponse:
    search_text = q.strip()
    cheapest_only = _parse_cheapest_only(cheapest)
    max_price_input = max_price.strip()
    condition_filter_input = condition.strip().lower() or "all"
    searched = bool(request.query_params)
    error: str | None = None
    results: list[dict[str, Any]] = []

    if searched:
        try:
            max_price_value = _parse_max_usd_price(max_price_input) if max_price_input else None
            condition_filter = parse_condition_filter(condition_filter_input)
            query = build_search_query(
                search_text,
                cheapest_only=cheapest_only,
                max_price=max_price_value,
            )
            offers, cheapest_only_flag = search_offers(query, condition=condition_filter)
            enrich_offers_dealer_contacts(offers)
            groups = group_offers_by_watch(offers, cheapest_only=cheapest_only_flag)
            user = get_current_user(request)
            message_ids = [
                str(offer.get("message_id"))
                for group in groups
                for offer in group.get("offers") or []
                if offer.get("message_id")
            ]
            import_logs_by_message_id = get_import_logs_by_message_ids(message_ids)
            enriched_groups: list[dict[str, Any]] = []
            for group in groups:
                enriched_groups.append(
                    {
                        **group,
                        "offers": attach_dealer_offer_source_urls(
                            group.get("offers") or [],
                            import_logs_by_message_id,
                            user=user,
                        ),
                    }
                )
            results = build_result_rows(enriched_groups)
        except ValueError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "search_text": search_text,
            "cheapest_only": cheapest_only,
            "max_price": max_price_input,
            "condition_filter": condition_filter_input,
            "results": results,
            "searched": searched,
            "error": error,
        },
    )


@app.get("/notifications", response_class=HTMLResponse, name="notifications_list")
async def notifications_page(
    request: Request,
    type: str = "all",
    quick_fix_saved: str = "",
) -> HTMLResponse:
    raw_notifications = list_notifications()
    active_filter = normalize_notification_filter(type)
    filter_counts = notification_filter_counts(raw_notifications)
    filtered_notifications = filter_notifications_by_type(raw_notifications, active_filter)
    all_rows = build_notification_rows(raw_notifications)
    notifications = (
        all_rows
        if active_filter == "all"
        else [row for row in all_rows if row["type"] == active_filter]
    )
    unread_count = sum(1 for item in all_rows if not item["is_read"])
    read_count = sum(1 for item in all_rows if item["is_read"])
    return templates.TemplateResponse(
        request,
        "notifications.html",
        {
            "notifications": notifications,
            "unread_count": unread_count,
            "read_count": read_count,
            "filter_counts": filter_counts,
            "active_filter": active_filter,
            "filter_options": build_notification_filter_options(
                filter_counts,
                active_filter=active_filter,
            ),
            "has_any_notifications": filter_counts["all"] > 0,
            "canonical_brands": list_canonical_brands(),
            "quick_fix_saved_id": quick_fix_saved.strip() or None,
        },
    )


@app.post("/notifications/{notification_id}/quick-fix")
async def notifications_quick_fix(
    request: Request,
    notification_id: str,
    brand_name: str = Form(...),
    reference: str = Form(...),
    alias_text: str = Form(""),
    type: str = Form("needs_review"),
) -> RedirectResponse:
    user = get_current_user(request)
    if not can_quick_fix_notifications(user):
        raise HTTPException(status_code=403, detail="Quick fix not allowed")

    notification = get_notification_by_id(notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.get("type") != "needs_review":
        raise HTTPException(status_code=400, detail="Quick fix is only available for Needs review notifications")

    import_log_id = notification.get("related_import_log_id")
    if not import_log_id:
        raise HTTPException(status_code=400, detail="Notification is not linked to an import")

    if not brand_name.strip():
        raise HTTPException(status_code=400, detail="Brand is required")
    if not reference.strip():
        raise HTTPException(status_code=400, detail="Reference is required")

    try:
        apply_notification_quick_fix(
            import_log_id=str(import_log_id),
            brand_name=brand_name,
            reference=reference,
            alias_text=alias_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    active_filter = normalize_notification_filter(type)
    filter_query = "" if active_filter == "all" else f"&type={active_filter}"
    return RedirectResponse(
        url=f"/notifications?quick_fix_saved={notification_id}{filter_query}",
        status_code=303,
    )


@app.post("/notifications/{notification_id}/read")
async def notifications_mark_read(notification_id: str) -> RedirectResponse:
    mark_notification_read(notification_id)
    return RedirectResponse(url="/notifications", status_code=303)


@app.post("/notifications/read-all")
async def notifications_mark_all_read() -> RedirectResponse:
    mark_all_notifications_read()
    return RedirectResponse(url="/notifications", status_code=303)


@app.post("/notifications/{notification_id}/delete")
async def notifications_delete(notification_id: str, confirm: str = Form(...)) -> RedirectResponse:
    if confirm != "1":
        raise HTTPException(status_code=400, detail="Confirmation required")
    delete_notification(notification_id)
    return RedirectResponse(url="/notifications", status_code=303)


@app.post("/notifications/clear-read")
async def notifications_clear_read(confirm: str = Form(...)) -> RedirectResponse:
    if confirm != "1":
        raise HTTPException(status_code=400, detail="Confirmation required")
    delete_read_notifications()
    return RedirectResponse(url="/notifications", status_code=303)


@app.post("/notifications/clear-all")
async def notifications_clear_all(confirm: str = Form(...)) -> RedirectResponse:
    if confirm != "1":
        raise HTTPException(status_code=400, detail="Confirmation required")
    delete_all_notifications()
    return RedirectResponse(url="/notifications", status_code=303)


@app.get("/dealers", response_class=HTMLResponse, name="dealers_list")
async def dealers_list(request: Request, q: str = "") -> HTMLResponse:
    search_query = q.strip()
    page = parse_activity_page(request.query_params.get("page"))
    user = get_current_user(request)
    dealers = list_dealers()
    import_logs = _business_import_logs(
        filter_imports_for_user(list_dealer_import_activity_logs(), user)
    )
    dealer_rows = filter_dealer_list_rows_by_search(
        build_trader_dealer_list_rows(
            dealers,
            import_logs,
            list_dealer_offer_counts(),
        ),
        search_query,
    )
    dealers_page = paginate_dealer_list_rows(dealer_rows, page)
    return templates.TemplateResponse(
        request,
        "dealers.html",
        {
            "dealers": dealers_page.dealers,
            "search_query": search_query,
            "page": dealers_page.page,
            "page_size": dealers_page.page_size,
            "has_previous": dealers_page.has_previous,
            "has_next": dealers_page.has_next,
            "previous_page_url": dealers_page_url(dealers_page.page - 1, search_query),
            "next_page_url": dealers_page_url(dealers_page.page + 1, search_query),
            "showing_from": dealers_page.showing_from,
            "showing_to": dealers_page.showing_to,
        },
    )


@app.get("/dealers/{dealer_id}", response_class=HTMLResponse, name="dealer_detail")
async def dealer_detail(request: Request, dealer_id: str) -> HTMLResponse:
    dealer = get_dealer_by_id(dealer_id)
    if (
        dealer is None
        or not dealer_is_business_visible(
            dealer,
            has_offers=dealer_has_offers(dealer_id),
        )
        or not dealer_has_offers(dealer_id)
    ):
        raise HTTPException(status_code=404, detail="Dealer not found")

    offer_rows = flatten_offer_intelligence_rows(
        list_offer_intelligence_rows(dealer_id=dealer_id)
    )
    user = get_current_user(request)
    active_offers = [
        normalize_dealer_offer(offer) for offer in get_active_offers_for_dealer(dealer_id)
    ]
    message_ids = [str(offer["message_id"]) for offer in active_offers if offer.get("message_id")]
    active_offers = attach_dealer_offer_source_urls(
        active_offers,
        get_import_logs_by_message_ids(message_ids),
        user=user,
    )

    return templates.TemplateResponse(
        request,
        "dealer_detail.html",
        {
            "dealer": build_dealer_profile(dealer),
            "stats": format_dealer_stats(compute_dealer_stats(offer_rows)),
            "offers": build_dealer_offer_rows(active_offers),
        },
    )


@app.get("/clients", response_class=HTMLResponse, name="clients_list")
async def clients_list(request: Request, q: str = "") -> HTMLResponse:
    search_query = q.strip()
    clients = filter_records_by_contact_search(list_clients(), search_query)
    client_ids = [str(client["id"]) for client in clients if client.get("id")]
    profiles_by_client_id = list_client_profiles_by_client_ids(client_ids)
    client_rows = build_client_list_rows(clients, profiles_by_client_id, list_requests())
    return templates.TemplateResponse(
        request,
        "clients.html",
        {
            "clients": client_rows,
            "search_query": search_query,
            "saved": request.query_params.get("saved") == "1",
            "deleted": request.query_params.get("deleted") == "1",
        },
    )


@app.post("/clients")
async def clients_create(name: str = Form(...)) -> RedirectResponse:
    if not name.strip():
        raise HTTPException(status_code=400, detail="Client name is required")
    create_client_contact(name=name.strip())
    return RedirectResponse(url="/clients?saved=1", status_code=303)


@app.get("/clients/{client_id}", response_class=HTMLResponse, name="client_detail")
async def client_detail(request: Request, client_id: str) -> HTMLResponse:
    client = get_client_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    profile = get_client_profile(client_id)
    client_name = client_display_name(client)
    client_requests = list_requests_for_client(client_id=client_id, client_name=client_name)
    stats = compute_client_stats(client_requests)
    profile_view = build_client_profile(client, profile)
    profile_view["last_activity"] = format_activity_timestamp(
        stats.get("last_activity") or profile.get("updated_at") or client.get("updated_at")
    )
    matching_offers = find_matching_offers_for_client(
        requests=client_requests,
        offers=list_active_sourcing_offers(),
    )
    open_client_requests = [
        request
        for request in client_requests
        if (request.get("status") or "").lower() in {"open", "active"}
    ]
    dashboard = build_client_sourcing_dashboard(
        requests=client_requests,
        matching_offers=matching_offers,
    )

    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {
            "client": profile_view,
            "profile": profile,
            "wishlist": build_client_wishlist(profile),
            "requests": build_client_request_rows(open_client_requests),
            "has_open_requests": bool(open_client_requests),
            "matches": build_client_match_rows(
                list_client_match_history(client_id, client_name=client_name)
            ),
            "dashboard": dashboard,
            "matching_offers": build_matching_offer_rows(matching_offers),
            "saved": request.query_params.get("saved") == "1",
            "delete_blocked": request.query_params.get("delete_blocked") == "1",
        },
    )


@app.post("/clients/{client_id}/delete")
async def client_delete_permanently(
    client_id: str,
    confirm: str = Form(...),
) -> RedirectResponse:
    if confirm != "1":
        raise HTTPException(status_code=400, detail="Confirmation required")

    client = get_client_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        delete_client_permanently(client_id, client_name=client_display_name(client))
    except ClientDeleteBlockedError:
        return RedirectResponse(url=f"/clients/{client_id}?delete_blocked=1", status_code=303)

    return RedirectResponse(url="/clients?deleted=1", status_code=303)


@app.get("/knowledge/unknown-brands", response_class=HTMLResponse, name="unknown_brands_list")
async def unknown_brands_list(request: Request) -> HTMLResponse:
    rows = list_pending_unknown_brands()
    dealer_ids = sorted({str(row["dealer_id"]) for row in rows if row.get("dealer_id")})
    dealers_by_id = {
        str(dealer["id"]): dealer
        for dealer in (
            get_dealer_by_id(dealer_id)
            for dealer_id in dealer_ids
        )
        if dealer
    }
    return templates.TemplateResponse(
        request,
        "knowledge_unknown_brands.html",
        {
            "unknown_brands": build_unknown_brand_rows(rows, dealers_by_id=dealers_by_id),
            "canonical_brands": list_canonical_brands(),
            "knowledge_enabled": watch_knowledge_supported(),
            "saved": request.query_params.get("saved") == "1",
            "ignored": request.query_params.get("ignored") == "1",
        },
    )


@app.post("/knowledge/unknown-brands/{unknown_brand_id}/add-alias")
async def unknown_brand_add_alias(
    unknown_brand_id: str,
    brand_name: str = Form(...),
) -> RedirectResponse:
    if not brand_name.strip():
        raise HTTPException(status_code=400, detail="Brand name is required")

    resolve_unknown_brand_with_alias(
        unknown_brand_id=unknown_brand_id,
        brand_name=brand_name.strip(),
    )
    invalidate_brand_registry_cache()
    return RedirectResponse(url="/knowledge/unknown-brands?saved=1", status_code=303)


@app.post("/knowledge/unknown-brands/{unknown_brand_id}/ignore")
async def unknown_brand_ignore(unknown_brand_id: str) -> RedirectResponse:
    mark_unknown_brand_ignored(unknown_brand_id)
    return RedirectResponse(url="/knowledge/unknown-brands?ignored=1", status_code=303)


@app.get("/knowledge/unknown-nicknames", response_class=HTMLResponse, name="unknown_nicknames_list")
async def unknown_nicknames_list(request: Request) -> HTMLResponse:
    rows = list_pending_unknown_nicknames()
    dealer_ids = sorted({str(row["dealer_id"]) for row in rows if row.get("dealer_id")})
    dealers_by_id = {
        str(dealer["id"]): dealer
        for dealer in (
            get_dealer_by_id(dealer_id)
            for dealer_id in dealer_ids
        )
        if dealer
    }
    return templates.TemplateResponse(
        request,
        "knowledge_unknown_nicknames.html",
        {
            "unknown_nicknames": build_unknown_nickname_rows(rows, dealers_by_id=dealers_by_id),
            "canonical_brands": list_canonical_brands(),
            "identification_enabled": watch_identification_supported(),
            "saved": request.query_params.get("saved") == "1",
            "ignored": request.query_params.get("ignored") == "1",
        },
    )


@app.post("/knowledge/unknown-nicknames/{unknown_nickname_id}/map")
async def unknown_nickname_map(
    unknown_nickname_id: str,
    brand_name: str = Form(...),
    reference: str = Form(...),
) -> RedirectResponse:
    if not brand_name.strip():
        raise HTTPException(status_code=400, detail="Brand name is required")
    if not reference.strip():
        raise HTTPException(status_code=400, detail="Reference is required")

    references = [reference.strip().upper()]
    resolve_unknown_nickname_with_alias(
        unknown_nickname_id=unknown_nickname_id,
        brand_name=brand_name.strip(),
        likely_references=references,
    )
    invalidate_identifier_cache()
    return RedirectResponse(url="/knowledge/unknown-nicknames?saved=1", status_code=303)


@app.post("/knowledge/unknown-nicknames/{unknown_nickname_id}/ignore")
async def unknown_nickname_ignore(unknown_nickname_id: str) -> RedirectResponse:
    mark_unknown_nickname_ignored(unknown_nickname_id)
    return RedirectResponse(url="/knowledge/unknown-nicknames?ignored=1", status_code=303)


@app.post("/clients/{client_id}/edit")
async def client_edit(
    client_id: str,
    name: str = Form(...),
    notes: str = Form(""),
    preferred_brands: str = Form(""),
    preferred_models: str = Form(""),
    budget_min: str = Form(""),
    budget_max: str = Form(""),
    preferred_condition: str = Form(""),
    preferred_dial: str = Form(""),
    status: str = Form("active"),
) -> RedirectResponse:
    client = get_client_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    update_client_name(client_id, name)
    update_client_profile(
        client_id,
        notes=notes,
        preferred_brands=preferred_brands,
        preferred_models=preferred_models,
        budget_min=_parse_optional_int(budget_min),
        budget_max=_parse_optional_int(budget_max),
        preferred_condition=preferred_condition,
        preferred_dial=preferred_dial,
        status=status,
    )
    return RedirectResponse(url=f"/clients/{client_id}?saved=1", status_code=303)


@app.get("/contacts", response_class=HTMLResponse, name="contacts_list")
async def contacts_list(
    request: Request,
    filter: str = DEFAULT_CONTACTS_FILTER,
    q: str = "",
) -> HTMLResponse:
    user = get_current_user(request)
    active_filter = parse_contacts_filter(filter)
    search_query = q.strip()
    contacts = filter_contacts_page_for_user(
        list_contacts(),
        user,
        filter_key=active_filter,
        search_query=search_query,
    )
    return templates.TemplateResponse(
        request,
        "contacts.html",
        {
            "contacts": contacts,
            "active_filter": active_filter,
            "removed_filter": CONTACTS_FILTER_REMOVED,
            "filter_options": build_contacts_filter_options(active_filter, search_query),
            "search_query": search_query,
            "restore_contact_types": RESTORE_CONTACT_TYPES,
            "saved": request.query_params.get("saved") == "1",
            "removed": request.query_params.get("removed") == "1",
            "restored": request.query_params.get("restored") == "1",
        },
    )


def _contacts_redirect_url(active_filter: str, **query_params: str) -> str:
    normalized_filter = parse_contacts_filter(active_filter)
    parts: list[str] = []
    if normalized_filter != DEFAULT_CONTACTS_FILTER:
        parts.append(f"filter={normalized_filter}")
    parts.extend(f"{key}={value}" for key, value in query_params.items())
    if not parts:
        return "/contacts"
    return f"/contacts?{'&'.join(parts)}"


@app.post("/contacts/{contact_id}/contact-type")
async def contacts_update_contact_type(
    contact_id: str,
    contact_type: str = Form(...),
    filter: str = Form(DEFAULT_CONTACTS_FILTER),
) -> RedirectResponse:
    if contact_type not in CONTACT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid contact type")
    dealer = get_dealer_by_id(contact_id)
    if dealer is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    if is_removed_contact(dealer_contact_type(dealer)):
        raise HTTPException(status_code=400, detail="Removed contacts must be restored first")
    update_dealer_contact_type(contact_id, contact_type)
    return RedirectResponse(url=_contacts_redirect_url(filter, saved="1"), status_code=303)


@app.post("/contacts/{contact_id}/set-dealer")
async def contacts_set_dealer(
    contact_id: str,
    filter: str = Form(DEFAULT_CONTACTS_FILTER),
) -> RedirectResponse:
    dealer = get_dealer_by_id(contact_id)
    if dealer is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    if is_removed_contact(dealer_contact_type(dealer)):
        raise HTTPException(status_code=400, detail="Removed contacts must be restored first")
    update_dealer_contact_type(contact_id, CONTACT_TYPE_DEALER)
    return RedirectResponse(url=_contacts_redirect_url(filter, saved="1"), status_code=303)


@app.post("/contacts/{contact_id}/set-client")
async def contacts_set_client(
    contact_id: str,
    filter: str = Form(DEFAULT_CONTACTS_FILTER),
) -> RedirectResponse:
    dealer = get_dealer_by_id(contact_id)
    if dealer is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    if is_removed_contact(dealer_contact_type(dealer)):
        raise HTTPException(status_code=400, detail="Removed contacts must be restored first")
    update_dealer_contact_type(contact_id, CONTACT_TYPE_CLIENT)
    return RedirectResponse(url=_contacts_redirect_url(filter, saved="1"), status_code=303)


@app.post("/contacts/{contact_id}/remove")
async def contacts_remove_from_system(
    request: Request,
    contact_id: str,
    confirm: str = Form(...),
    filter: str = Form(DEFAULT_CONTACTS_FILTER),
) -> RedirectResponse:
    if confirm != "1":
        raise HTTPException(status_code=400, detail="Confirmation required")
    user = get_current_user(request)
    dealer = get_dealer_by_id(contact_id)
    if dealer is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    if not filter_contacts_for_user([dealer], user):
        raise HTTPException(status_code=404, detail="Contact not found")
    if is_removed_contact(dealer_contact_type(dealer)):
        raise HTTPException(status_code=400, detail="Contact already removed")
    update_dealer_contact_type(
        contact_id,
        CONTACT_TYPE_REMOVED,
        owner_user_id=user["id"] if user else None,
        classified_by_user_id=user["id"] if user else None,
    )
    return RedirectResponse(url=_contacts_redirect_url(filter, removed="1"), status_code=303)


@app.post("/contacts/{contact_id}/restore")
async def contacts_restore_contact(
    contact_id: str,
    contact_type: str = Form(...),
    filter: str = Form(CONTACTS_FILTER_REMOVED),
) -> RedirectResponse:
    if contact_type not in RESTORE_CONTACT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid restore type")
    dealer = get_dealer_by_id(contact_id)
    if dealer is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    if not is_removed_contact(dealer_contact_type(dealer)):
        raise HTTPException(status_code=400, detail="Contact is not removed")
    update_dealer_contact_type(contact_id, contact_type)
    return RedirectResponse(url=_contacts_redirect_url(filter, restored="1"), status_code=303)
