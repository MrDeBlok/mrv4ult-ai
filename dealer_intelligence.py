"""Dealer intelligence helpers built from existing offer data."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from condition_normalizer import display_condition
from contact_classification import normalize_search_phone, normalize_whatsapp_id
from search import _display_value, _nested_record, _sort_key_usd_price, format_price, format_usd_price
from timezone_utils import DISPLAY_TIMEZONE, format_display_timestamp, parse_utc_timestamp

Record = dict[str, Any]

TRUSTED_DEALER_MIN_ACTIVE_OFFERS = 10
ESTABLISHED_DEALER_MIN_ACTIVE_OFFERS = 3


def clean_whatsapp_number_for_link(value: str | None) -> str:
    """Return digits-only WhatsApp number for wa.me links."""
    return re.sub(r"\D", "", value or "")


def flatten_offer_intelligence_row(offer: Record) -> Record:
    """Flatten nested message metadata onto an offer intelligence row."""
    message = _nested_record(offer.get("messages"))
    return {
        "dealer_id": offer.get("dealer_id"),
        "watch_id": offer.get("watch_id"),
        "status": offer.get("status"),
        "usd_price": offer.get("usd_price"),
        "received_at": message.get("received_at"),
    }


def flatten_offer_intelligence_rows(offers: list[Record]) -> list[Record]:
    return [flatten_offer_intelligence_row(offer) for offer in offers]


def aggregate_offers_by_dealer(offers: list[Record]) -> dict[str, list[Record]]:
    """Group flattened offer intelligence rows by dealer id."""
    grouped: dict[str, list[Record]] = {}
    for offer in offers:
        dealer_id = offer.get("dealer_id")
        if not dealer_id:
            continue
        grouped.setdefault(str(dealer_id), []).append(offer)
    return grouped


def compute_dealer_stats(offer_rows: list[Record]) -> dict[str, Any]:
    """Compute dealer metrics from flattened offer intelligence rows."""
    total_offers = len(offer_rows)
    active_rows = [row for row in offer_rows if row.get("status") == "active"]
    active_offers = len(active_rows)
    usd_prices = [
        price for price in (row.get("usd_price") for row in active_rows) if price is not None
    ]
    received_times = [row.get("received_at") for row in offer_rows if row.get("received_at")]
    return {
        "total_offers": total_offers,
        "active_offers": active_offers,
        "average_usd": round(sum(usd_prices) / len(usd_prices)) if usd_prices else None,
        "lowest_usd": min(usd_prices) if usd_prices else None,
        "highest_usd": max(usd_prices) if usd_prices else None,
        "last_activity": max(received_times) if received_times else None,
        "unique_watches": len(
            {row.get("watch_id") for row in active_rows if row.get("watch_id")}
        ),
    }


def format_activity_timestamp(value: str | None, *, missing: str = "N/A") -> str:
    return format_display_timestamp(value, missing=missing)


def dealer_display_name(dealer: Record) -> str:
    display_name = (dealer.get("display_name") or "").strip()
    if display_name:
        return display_name
    phone_number = (dealer.get("phone_number") or "").strip()
    whatsapp_id = (dealer.get("whatsapp_id") or "").strip()
    return phone_number or whatsapp_id or "Unknown dealer"


def format_dealer_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_offers": stats.get("total_offers", 0),
        "active_offers": stats.get("active_offers", 0),
        "average_asking_price": format_usd_price(stats.get("average_usd")),
        "lowest_asking_price": format_usd_price(stats.get("lowest_usd")),
        "highest_asking_price": format_usd_price(stats.get("highest_usd")),
        "last_activity": format_activity_timestamp(stats.get("last_activity")),
        "unique_watches": stats.get("unique_watches", 0),
        "_last_activity_raw": stats.get("last_activity"),
    }


def build_dealer_list_row(dealer: Record, stats: dict[str, Any]) -> Record:
    formatted = format_dealer_stats(stats)
    return {
        "id": dealer.get("id"),
        "name": dealer_display_name(dealer),
        "total_offers": formatted["total_offers"],
        "active_offers": formatted["active_offers"],
        "average_asking_price": formatted["average_asking_price"],
        "lowest_asking_price": formatted["lowest_asking_price"],
        "highest_asking_price": formatted["highest_asking_price"],
        "last_activity": formatted["last_activity"],
        "_last_activity_raw": formatted["_last_activity_raw"],
    }


def build_dealer_list_rows(dealers: list[Record], offers: list[Record]) -> list[Record]:
    """Build dealer list rows with aggregated offer intelligence."""
    flattened = flatten_offer_intelligence_rows(offers)
    grouped = aggregate_offers_by_dealer(flattened)
    rows = [
        build_dealer_list_row(
            dealer,
            compute_dealer_stats(grouped.get(str(dealer["id"]), [])),
        )
        for dealer in dealers
        if dealer.get("id")
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
    return rows


def build_dealer_contact_lookup(dealers: list[Record]) -> dict[str, str]:
    """Map normalized WhatsApp/phone values to dealer ids."""
    lookup: dict[str, str] = {}
    for dealer in dealers:
        dealer_id = str(dealer.get("id") or "")
        if not dealer_id:
            continue
        for key in ("whatsapp_id", "phone_number"):
            raw = normalize_whatsapp_id(dealer.get(key))
            if not raw:
                continue
            lookup[raw] = dealer_id
            digits = normalize_search_phone(raw)
            if digits:
                lookup[digits] = dealer_id
    return lookup


def _resolve_dealer_id_for_import_log(
    import_log: Record,
    contact_lookup: dict[str, str],
) -> str | None:
    whatsapp = normalize_whatsapp_id(import_log.get("dealer_whatsapp"))
    if not whatsapp:
        return None
    return contact_lookup.get(whatsapp) or contact_lookup.get(normalize_search_phone(whatsapp))


def build_dealer_import_activity_index(
    dealers: list[Record],
    import_logs: list[Record],
) -> dict[str, Record]:
    """Aggregate latest import activity per dealer from lightweight import log rows."""
    contact_lookup = build_dealer_contact_lookup(dealers)
    index: dict[str, Record] = {}

    for import_log in import_logs:
        dealer_id = _resolve_dealer_id_for_import_log(import_log, contact_lookup)
        if not dealer_id:
            continue

        group_name = str(import_log.get("group_name") or "").strip()
        whatsapp = normalize_whatsapp_id(import_log.get("dealer_whatsapp"))
        entry = index.get(dealer_id)
        if entry is None:
            index[dealer_id] = {
                "last_message_at": import_log.get("import_time"),
                "groups": [group_name] if group_name else [],
                "dealer_whatsapp": whatsapp,
            }
            continue

        if group_name and group_name not in entry["groups"]:
            entry["groups"].append(group_name)

    return index


def format_dealer_groups(group_names: list[str]) -> str:
    cleaned = [name.strip() for name in group_names if str(name).strip()]
    if not cleaned:
        return "—"
    return ", ".join(cleaned[:3])


def resolve_dealer_contact_number(
    dealer: Record,
    activity: Record | None = None,
) -> str:
    candidates = []
    if activity:
        candidates.append(activity.get("dealer_whatsapp"))
    candidates.extend([dealer.get("phone_number"), dealer.get("whatsapp_id")])
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return "No number"


def classify_dealer_activity_label(
    last_message_at: str | None,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Return a trader-friendly activity label and badge class."""
    timestamp = parse_utc_timestamp(last_message_at)
    if timestamp is None:
        return "No activity", "secondary"

    reference = now or datetime.now(DISPLAY_TIMEZONE)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=DISPLAY_TIMEZONE)
    else:
        reference = reference.astimezone(DISPLAY_TIMEZONE)

    local = timestamp.astimezone(DISPLAY_TIMEZONE)
    if local.date() == reference.date():
        return "Active today", "success"

    week_ago = reference.date() - timedelta(days=7)
    if local.date() >= week_ago:
        return "Active this week", "primary"

    return "Inactive", "secondary"


def dealer_quality_badge(dealer_id: str, offer_counts: dict[str, Record]) -> tuple[str, str]:
    active_offers = int(offer_counts.get(str(dealer_id), {}).get("active_offers") or 0)
    if active_offers >= TRUSTED_DEALER_MIN_ACTIVE_OFFERS:
        return "Trusted", "success"
    if active_offers >= ESTABLISHED_DEALER_MIN_ACTIVE_OFFERS:
        return "Established", "primary"
    if active_offers >= 1:
        return "New", "warning"
    return "Unknown", "secondary"


def build_trader_dealer_list_row(
    dealer: Record,
    activity: Record | None,
    offer_counts: dict[str, Record],
) -> Record:
    contact_number = resolve_dealer_contact_number(dealer, activity)
    digits = clean_whatsapp_number_for_link(contact_number)
    last_message_at = (activity or {}).get("last_message_at")
    activity_label, activity_class = classify_dealer_activity_label(last_message_at)
    quality_label, quality_class = dealer_quality_badge(str(dealer.get("id")), offer_counts)
    groups = format_dealer_groups(list((activity or {}).get("groups") or []))
    last_message = (
        format_activity_timestamp(last_message_at, missing="—")
        if last_message_at
        else "—"
    )

    return {
        "id": dealer.get("id"),
        "name": dealer_display_name(dealer),
        "display_name": dealer.get("display_name"),
        "phone_number": dealer.get("phone_number"),
        "whatsapp_id": dealer.get("whatsapp_id"),
        "contact_number": contact_number,
        "groups": groups,
        "last_message": last_message,
        "activity_label": activity_label,
        "activity_class": activity_class,
        "quality_label": quality_label,
        "quality_class": quality_class,
        "message_url": f"https://wa.me/{digits}" if digits else None,
        "_last_message_raw": last_message_at,
    }


def build_trader_dealer_list_rows(
    dealers: list[Record],
    import_logs: list[Record],
    offer_counts: dict[str, Record] | None = None,
) -> list[Record]:
    """Build trader-focused dealer list rows without full offer aggregation."""
    activity_index = build_dealer_import_activity_index(dealers, import_logs)
    counts = offer_counts or {}
    rows = [
        build_trader_dealer_list_row(
            dealer,
            activity_index.get(str(dealer.get("id"))),
            counts,
        )
        for dealer in dealers
        if dealer.get("id")
    ]
    rows.sort(
        key=lambda row: (
            row["_last_message_raw"] is None,
            row["_last_message_raw"] or "",
            row["name"].lower(),
        ),
        reverse=True,
    )
    for row in rows:
        row.pop("_last_message_raw", None)
    return rows


def build_dealer_profile(dealer: Record) -> Record:
    contact = dealer.get("phone_number") or dealer.get("whatsapp_id") or "N/A"
    return {
        "name": dealer_display_name(dealer),
        "whatsapp_id": dealer.get("whatsapp_id") or "N/A",
        "phone_number": dealer.get("phone_number") or "N/A",
        "contact": contact,
        "company_name": _display_value(dealer.get("company_name")),
        "country": _display_value(dealer.get("country")),
        "is_active": "Active" if dealer.get("is_active", True) else "Inactive",
        "is_active_class": "success" if dealer.get("is_active", True) else "secondary",
        "created_at": format_activity_timestamp(dealer.get("created_at")),
        "updated_at": format_activity_timestamp(dealer.get("updated_at")),
    }


def build_dealer_offer_rows(offers: list[Record]) -> list[Record]:
    """Format active dealer offers for the detail page table."""
    rows: list[Record] = []
    for offer in sorted(offers, key=_sort_key_usd_price):
        watch = offer.get("watch") or _nested_record(offer.get("watches"))
        rows.append(
            {
                "watch_id": offer.get("watch_id"),
                "brand": _display_value(watch.get("brand")),
                "reference": _display_value(watch.get("reference")),
                "model": _display_value(watch.get("model")),
                "group_name": offer.get("group_name") or "N/A",
                "original_price": format_price(
                    offer.get("original_price"),
                    offer.get("original_currency"),
                ),
                "usd_price": format_usd_price(offer.get("usd_price")),
                "card_date": offer.get("card_date") or "N/A",
                "condition": display_condition(offer.get("condition")),
                "received_at": format_activity_timestamp(offer.get("received_at")),
            }
        )
    return rows
