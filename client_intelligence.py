"""Client CRM helpers built from client profiles and request data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from condition_normalizer import display_condition
from dealer_intelligence import dealer_display_name, format_activity_timestamp
from search import _display_value, format_usd_price

Record = dict[str, Any]

CLIENT_STATUS_ACTIVE = "active"
CLIENT_STATUS_INACTIVE = "inactive"
UNNAMED_CLIENT_TITLE = "Unnamed client"


def client_display_name(client: Record) -> str:
    return dealer_display_name(client)


def client_profile_title(client: Record) -> str:
    display_name = (client.get("display_name") or "").strip()
    if display_name:
        return display_name
    return UNNAMED_CLIENT_TITLE


def client_contact_phone(client: Record) -> str | None:
    phone_number = (client.get("phone_number") or "").strip()
    whatsapp_id = (client.get("whatsapp_id") or "").strip()
    return phone_number or whatsapp_id or None


def build_client_create_request_url(client_id: str, client: Record) -> str:
    from urllib.parse import urlencode

    params: dict[str, str] = {"client_id": str(client_id)}
    display_name = (client.get("display_name") or "").strip()
    if display_name:
        params["client_name"] = display_name
    return f"/requests?{urlencode(params)}"


def format_budget_value(value: int | None) -> str:
    if value is None:
        return "N/A"
    return format_usd_price(value)


def format_budget_range(budget_min: int | None, budget_max: int | None) -> str:
    if budget_min is None and budget_max is None:
        return "N/A"
    if budget_min is not None and budget_max is not None:
        return f"{format_budget_value(budget_min)} – {format_budget_value(budget_max)}"
    if budget_min is not None:
        return f"From {format_budget_value(budget_min)}"
    return f"Up to {format_budget_value(budget_max)}"


def format_client_status(status: str | None) -> tuple[str, str]:
    normalized = (status or CLIENT_STATUS_ACTIVE).strip().lower()
    if normalized == CLIENT_STATUS_INACTIVE:
        return "Inactive", "secondary"
    return "Active", "success"


def aggregate_requests_by_client(
    requests: list[Record],
    *,
    client_ids: set[str],
    client_names: dict[str, str],
) -> dict[str, list[Record]]:
    """Group requests by client id using client_id or client_name fallback."""
    grouped: dict[str, list[Record]] = {client_id: [] for client_id in client_ids}
    name_to_id = {
        name.strip().lower(): client_id
        for client_id, name in client_names.items()
        if name.strip()
    }

    for request in requests:
        request_client_id = request.get("client_id")
        if request_client_id and str(request_client_id) in grouped:
            grouped[str(request_client_id)].append(request)
            continue

        client_name = (request.get("client_name") or "").strip().lower()
        matched_client_id = name_to_id.get(client_name)
        if matched_client_id:
            grouped[matched_client_id].append(request)

    return grouped


def compute_client_stats(request_rows: list[Record]) -> dict[str, Any]:
    """Compute request counts and last activity for one client."""
    timestamps = [
        value
        for value in (
            *(row.get("updated_at") for row in request_rows),
            *(row.get("created_at") for row in request_rows),
        )
        if value
    ]
    return {
        "request_count": len(request_rows),
        "last_activity": max(timestamps) if timestamps else None,
    }


def build_client_list_row(
    client: Record,
    profile: Record,
    stats: dict[str, Any],
) -> Record:
    status_label, status_class = format_client_status(profile.get("status"))
    return {
        "id": client.get("id"),
        "name": client_display_name(client),
        "created_at": format_activity_timestamp(client.get("created_at")),
        "last_activity": format_activity_timestamp(stats.get("last_activity")),
        "request_count": stats.get("request_count", 0),
        "status": status_label,
        "status_class": status_class,
        "_last_activity_raw": stats.get("last_activity"),
        "_created_at_raw": client.get("created_at"),
    }


def build_client_list_rows(
    clients: list[Record],
    profiles_by_client_id: dict[str, Record],
    requests: list[Record],
) -> list[Record]:
    client_ids = {str(client["id"]) for client in clients if client.get("id")}
    client_names = {
        str(client["id"]): client_display_name(client)
        for client in clients
        if client.get("id")
    }
    grouped_requests = aggregate_requests_by_client(
        requests,
        client_ids=client_ids,
        client_names=client_names,
    )

    rows = [
        build_client_list_row(
            client,
            profiles_by_client_id.get(str(client["id"]), {}),
            compute_client_stats(grouped_requests.get(str(client["id"]), [])),
        )
        for client in clients
        if client.get("id")
    ]
    rows.sort(
        key=lambda row: (
            row["_last_activity_raw"] is None,
            row["_last_activity_raw"] or "",
            row["name"].lower(),
        ),
        reverse=True,
    )
    for row in rows:
        row.pop("_last_activity_raw", None)
        row.pop("_created_at_raw", None)
    return rows


def build_client_profile(client: Record, profile: Record) -> Record:
    status_label, status_class = format_client_status(profile.get("status"))
    display_name = (client.get("display_name") or "").strip()
    contact_phone = client_contact_phone(client)
    return {
        "id": client.get("id"),
        "title": client_profile_title(client),
        "name": display_name,
        "show_contact_phone": not display_name and bool(contact_phone),
        "contact_phone": _display_value(contact_phone) if contact_phone else "N/A",
        "notes": _display_value(profile.get("notes")),
        "phone_number": _display_value(client.get("phone_number")),
        "whatsapp_id": _display_value(client.get("whatsapp_id")),
        "created_at": format_activity_timestamp(client.get("created_at")),
        "last_activity": format_activity_timestamp(profile.get("updated_at") or client.get("updated_at")),
        "status": status_label,
        "status_class": status_class,
        "create_request_url": build_client_create_request_url(str(client.get("id")), client),
    }


def build_client_wishlist(profile: Record) -> Record:
    return {
        "preferred_brands": _display_value(profile.get("preferred_brands")),
        "preferred_models": _display_value(profile.get("preferred_models")),
        "budget_min": profile.get("budget_min"),
        "budget_max": profile.get("budget_max"),
        "budget_range": format_budget_range(profile.get("budget_min"), profile.get("budget_max")),
        "preferred_condition": display_condition(profile.get("preferred_condition")),
        "preferred_dial": _display_value(profile.get("preferred_dial")),
    }


def build_client_request_rows(requests: list[Record]) -> list[Record]:
    rows: list[Record] = []
    for request in requests:
        rows.append(
            {
                "id": request.get("id"),
                "brand": _display_value(request.get("brand")),
                "reference": _display_value(request.get("reference")),
                "model": _display_value(request.get("model")),
                "status": _display_value(request.get("status")),
                "max_price": format_usd_price(request.get("max_price")),
                "created_at": format_activity_timestamp(request.get("created_at")),
                "updated_at": format_activity_timestamp(request.get("updated_at")),
            }
        )
    return rows


def build_client_match_rows(matches: list[Record]) -> list[Record]:
    rows: list[Record] = []
    for match in matches:
        watch = match.get("watch") or {}
        offer = match.get("offer") or {}
        rows.append(
            {
                "match_strength": _display_value(match.get("match_strength")),
                "brand": _display_value(watch.get("brand")),
                "reference": _display_value(watch.get("reference")),
                "model": _display_value(watch.get("model")),
                "usd_price": format_usd_price(offer.get("usd_price")),
                "created_at": format_activity_timestamp(match.get("created_at")),
            }
        )
    return rows


def default_client_profile() -> Record:
    return {
        "notes": None,
        "preferred_brands": None,
        "preferred_models": None,
        "budget_min": None,
        "budget_max": None,
        "preferred_condition": None,
        "preferred_dial": None,
        "status": CLIENT_STATUS_ACTIVE,
    }
