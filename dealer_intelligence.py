"""Dealer intelligence helpers built from existing offer data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from condition_normalizer import display_condition
from contact_classification import normalize_search_phone, normalize_whatsapp_id
from import_status import is_discarded_no_watch_import
from search import _display_value, _nested_record, _sort_key_usd_price, format_price, format_usd_price
from timezone_utils import DISPLAY_TIMEZONE, format_display_timestamp, parse_utc_timestamp
from user_visibility import can_view_import

Record = dict[str, Any]

TRUSTED_DEALER_MIN_ACTIVE_OFFERS = 10
ESTABLISHED_DEALER_MIN_ACTIVE_OFFERS = 3

COUNTRY_FLAG_BY_NAME = {
    "china": "🇨🇳",
    "france": "🇫🇷",
    "germany": "🇩🇪",
    "hong kong": "🇭🇰",
    "italy": "🇮🇹",
    "japan": "🇯🇵",
    "netherlands": "🇳🇱",
    "singapore": "🇸🇬",
    "switzerland": "🇨🇭",
    "uae": "🇦🇪",
    "united arab emirates": "🇦🇪",
    "united kingdom": "🇬🇧",
    "uk": "🇬🇧",
    "usa": "🇺🇸",
    "united states": "🇺🇸",
}


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


def format_dealer_last_group(group_names: list[str]) -> str:
    cleaned = [name.strip() for name in group_names if str(name).strip()]
    return cleaned[0] if cleaned else "—"


def format_dealer_country_display(country: str | None) -> str:
    name = str(country or "").strip()
    if not name:
        return "—"
    flag = COUNTRY_FLAG_BY_NAME.get(name.lower(), "🌍")
    return f"{flag} {name}"


def format_relative_time_dutch(
    value: str | datetime | None,
    *,
    now: datetime | None = None,
    missing: str = "—",
) -> str:
    """Format a timestamp as Dutch relative time for trader dealer cards."""
    timestamp = parse_utc_timestamp(value)
    if timestamp is None:
        return missing

    reference = now or datetime.now(DISPLAY_TIMEZONE)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=DISPLAY_TIMEZONE)
    else:
        reference = reference.astimezone(DISPLAY_TIMEZONE)

    local = timestamp.astimezone(DISPLAY_TIMEZONE)
    delta = reference - local
    if delta.total_seconds() <= 0:
        return "zojuist"

    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "zojuist"

    minutes = seconds // 60
    if minutes < 60:
        if minutes == 1:
            return "1 minuut geleden"
        return f"{minutes} minuten geleden"

    hours = minutes // 60
    if hours < 24:
        if hours == 1:
            return "1 uur geleden"
        return f"{hours} uur geleden"

    days = hours // 24
    if days < 7:
        if days == 1:
            return "1 dag geleden"
        return f"{days} dagen geleden"

    weeks = days // 7
    if weeks < 5:
        if weeks == 1:
            return "1 week geleden"
        return f"{weeks} weken geleden"

    return local.strftime("%Y-%m-%d %H:%M")


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


def dealer_quality_badge(dealer_id: str, offer_counts: dict[str, Record]) -> tuple[str, str, bool]:
    active_offers = int(offer_counts.get(str(dealer_id), {}).get("active_offers") or 0)
    if active_offers >= TRUSTED_DEALER_MIN_ACTIVE_OFFERS:
        return "Trusted dealer", "success", True
    if active_offers >= ESTABLISHED_DEALER_MIN_ACTIVE_OFFERS:
        return "Established dealer", "primary", False
    if active_offers >= 1:
        return "New dealer", "warning", False
    return "Unknown dealer", "secondary", False


def build_trader_dealer_list_row(
    dealer: Record,
    activity: Record | None,
    offer_counts: dict[str, Record],
    *,
    now: datetime | None = None,
) -> Record:
    contact_number = resolve_dealer_contact_number(dealer, activity)
    digits = clean_whatsapp_number_for_link(contact_number)
    last_message_at = (activity or {}).get("last_message_at")
    activity_label, activity_class = classify_dealer_activity_label(last_message_at, now=now)
    quality_label, quality_class, quality_show_check = dealer_quality_badge(
        str(dealer.get("id")),
        offer_counts,
    )
    group_names = list((activity or {}).get("groups") or [])
    groups = format_dealer_groups(group_names)
    last_group = format_dealer_last_group(group_names)
    country_display = format_dealer_country_display(dealer.get("country"))
    last_message_relative = format_relative_time_dutch(last_message_at, now=now)
    whatsapp_display = contact_number if contact_number != "No number" else "—"

    return {
        "id": dealer.get("id"),
        "name": dealer_display_name(dealer),
        "display_name": dealer.get("display_name"),
        "phone_number": dealer.get("phone_number"),
        "whatsapp_id": dealer.get("whatsapp_id"),
        "country": dealer.get("country"),
        "country_display": country_display,
        "contact_number": contact_number,
        "whatsapp_display": whatsapp_display,
        "groups": groups,
        "last_group": last_group,
        "last_message": format_activity_timestamp(last_message_at, missing="—")
        if last_message_at
        else "—",
        "last_message_relative": last_message_relative,
        "activity_label": activity_label,
        "activity_class": activity_class,
        "quality_label": quality_label,
        "quality_class": quality_class,
        "quality_show_check": quality_show_check,
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


DEALERS_PAGE_SIZE = 20


@dataclass(frozen=True)
class DealersPageResult:
    dealers: list[Record]
    page: int
    page_size: int
    has_previous: bool
    has_next: bool
    showing_from: int | None
    showing_to: int | None


def dealers_page_url(page: int, search_query: str = "") -> str:
    """Build a dealers list URL preserving search and pagination."""
    params: list[tuple[str, str]] = []
    normalized_query = (search_query or "").strip()
    if normalized_query:
        params.append(("q", normalized_query))
    if page > 1:
        params.append(("page", str(page)))
    if not params:
        return "/dealers"
    return f"/dealers?{urlencode(params)}"


def paginate_dealer_list_rows(
    rows: list[Record],
    page: int,
    *,
    page_size: int = DEALERS_PAGE_SIZE,
) -> DealersPageResult:
    """Slice dealer list rows after visibility filtering and search."""
    safe_page = max(page, 1)
    start = (safe_page - 1) * page_size
    end = start + page_size
    page_rows = rows[start:end]
    total = len(rows)
    showing_from = start + 1 if page_rows else None
    showing_to = start + len(page_rows) if page_rows else None
    return DealersPageResult(
        dealers=page_rows,
        page=safe_page,
        page_size=page_size,
        has_previous=safe_page > 1,
        has_next=total > end,
        showing_from=showing_from,
        showing_to=showing_to,
    )


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


def activity_detail_url_for_import_log(
    import_log: Record | None,
    *,
    user: Record | None,
) -> str | None:
    """Return the activity detail URL when the user may view the source import."""
    if explain_import_log_source_block(import_log, user=user):
        return None
    return f"/activity/{import_log['id']}"


def explain_import_log_source_block(
    import_log: Record | None,
    *,
    user: Record | None,
) -> str | None:
    """Return a failure reason when an import log cannot become a source URL."""
    if not import_log or not import_log.get("id"):
        return "no_import_log_resolved"
    if is_discarded_no_watch_import(import_log):
        return "import_log_discarded_no_watch"
    if not can_view_import(user, import_log):
        return "import_log_not_visible_to_user"
    return None


DIRECT_OFFER_IMPORT_LOG_ID_FIELDS = (
    "import_log_id",
    "source_import_log_id",
    "created_from_import_log_id",
)
STORED_OFFER_SOURCE_URL_FIELDS = ("source_url", "original_url")


def _offer_direct_import_log_id(offer: Record) -> str | None:
    for key in DIRECT_OFFER_IMPORT_LOG_ID_FIELDS:
        value = offer.get(key)
        if value:
            return str(value)
    return None


def _stored_activity_source_url(offer: Record) -> str | None:
    for key in STORED_OFFER_SOURCE_URL_FIELDS:
        value = offer.get(key)
        if isinstance(value, str) and value.strip().startswith("/activity/"):
            return value.strip()
    return None


def index_import_logs_by_summary_offer_id(import_logs: list[Record]) -> dict[str, Record]:
    """Index import logs by offer_id values stored in summary rows."""
    by_offer_id: dict[str, Record] = {}
    for import_log in import_logs:
        summary = import_log.get("summary")
        if not isinstance(summary, dict):
            continue
        for row in summary.get("rows") or []:
            if not isinstance(row, dict):
                continue
            offer_id = str(row.get("offer_id") or "")
            if offer_id and offer_id not in by_offer_id:
                by_offer_id[offer_id] = import_log
    return by_offer_id


def load_offer_source_import_log_lookups(
    offers: list[Record],
) -> tuple[dict[str, Record], dict[str, Record], dict[str, Record]]:
    """Build import log lookup maps for resolving offer source URLs."""
    from database import (
        _normalize_uuid_key,
        get_import_logs_by_message_ids,
        get_import_logs_by_offer_ids,
        get_import_logs_for_source_resolution,
    )

    message_ids = list(
        dict.fromkeys(
            _normalize_uuid_key(offer.get("message_id"))
            for offer in offers
            if offer.get("message_id")
        )
    )
    direct_import_log_ids = list(
        dict.fromkeys(
            import_log_id
            for offer in offers
            if (import_log_id := _offer_direct_import_log_id(offer))
        )
    )

    import_logs_by_message_id = get_import_logs_by_message_ids(message_ids)
    import_log_ids = list(
        dict.fromkeys(
            [
                *direct_import_log_ids,
                *(
                    str(import_log["id"])
                    for import_log in import_logs_by_message_id.values()
                    if import_log.get("id")
                ),
            ]
        )
    )
    import_logs_by_id = get_import_logs_for_source_resolution(import_log_ids)
    normalized_import_logs_by_message_id: dict[str, Record] = {}
    for message_id, import_log in import_logs_by_message_id.items():
        normalized_key = _normalize_uuid_key(message_id)
        import_log_id = str(import_log.get("id") or "")
        if import_log_id and import_log_id not in import_logs_by_id:
            import_logs_by_id[import_log_id] = import_log
        if import_log_id and import_log_id in import_logs_by_id:
            import_log = import_logs_by_id[import_log_id]
        if normalized_key:
            normalized_import_logs_by_message_id[normalized_key] = import_log
    import_logs_by_message_id = normalized_import_logs_by_message_id

    import_logs_by_offer_id = index_import_logs_by_summary_offer_id(list(import_logs_by_id.values()))
    unresolved_offer_ids: list[str] = []
    for offer in offers:
        offer_id = _normalize_uuid_key(offer.get("id"))
        if not offer_id or offer_id in import_logs_by_offer_id:
            continue
        if _offer_direct_import_log_id(offer):
            continue
        message_id = _normalize_uuid_key(offer.get("message_id"))
        if message_id and import_logs_by_message_id.get(message_id):
            continue
        unresolved_offer_ids.append(offer_id)
    for offer_id, import_log in get_import_logs_by_offer_ids(unresolved_offer_ids).items():
        import_logs_by_offer_id.setdefault(offer_id, import_log)
        import_log_id = str(import_log.get("id") or "")
        if import_log_id:
            import_logs_by_id.setdefault(import_log_id, import_log)

    return import_logs_by_message_id, import_logs_by_id, import_logs_by_offer_id


def _resolve_import_log_for_offer(
    offer: Record,
    *,
    import_logs_by_message_id: dict[str, Record],
    import_logs_by_id: dict[str, Record],
    import_logs_by_offer_id: dict[str, Record],
) -> tuple[Record | None, str | None]:
    from database import _normalize_uuid_key

    direct_import_log_id = _offer_direct_import_log_id(offer)
    if direct_import_log_id:
        import_log = import_logs_by_id.get(direct_import_log_id)
        if import_log:
            return import_log, "direct_import_log_id"
        return {"id": direct_import_log_id}, "direct_import_log_id"

    message_id = _normalize_uuid_key(offer.get("message_id"))
    if message_id:
        import_log = import_logs_by_message_id.get(message_id)
        if import_log:
            return import_log, "message_id"

    offer_id = _normalize_uuid_key(offer.get("id"))
    if offer_id:
        import_log = import_logs_by_offer_id.get(offer_id)
        if import_log:
            return import_log, "summary_or_request_match"

    stored_url = _stored_activity_source_url(offer)
    if stored_url:
        import_log_id = stored_url.rstrip("/").rsplit("/", 1)[-1]
        import_log = import_logs_by_id.get(import_log_id)
        if import_log:
            return import_log, "stored_source_url"
        return {"id": import_log_id}, "stored_source_url"

    return None, None


def attach_dealer_offer_source_urls(
    offers: list[Record],
    import_logs_by_message_id: dict[str, Record],
    *,
    user: Record | None,
    import_logs_by_id: dict[str, Record] | None = None,
    import_logs_by_offer_id: dict[str, Record] | None = None,
) -> list[Record]:
    """Attach activity detail URLs to offers using import log and message relationships."""
    logs_by_id = import_logs_by_id or {}
    logs_by_offer_id = import_logs_by_offer_id or {}
    enriched: list[Record] = []
    for offer in offers:
        row = dict(offer)
        import_log, _resolution_path = _resolve_import_log_for_offer(
            row,
            import_logs_by_message_id=import_logs_by_message_id,
            import_logs_by_id=logs_by_id,
            import_logs_by_offer_id=logs_by_offer_id,
        )
        row["source_url"] = activity_detail_url_for_import_log(import_log, user=user)
        enriched.append(row)
    return enriched


def resolve_offer_source_url(
    offer: Record,
    *,
    user: Record | None,
    import_logs_by_message_id: dict[str, Record] | None = None,
    import_logs_by_id: dict[str, Record] | None = None,
    import_logs_by_offer_id: dict[str, Record] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve one offer source URL and return url, resolution path, failure reason."""
    if import_logs_by_message_id is None:
        import_logs_by_message_id, import_logs_by_id, import_logs_by_offer_id = load_offer_source_import_log_lookups(
            [offer]
        )
    import_log, resolution_path = _resolve_import_log_for_offer(
        offer,
        import_logs_by_message_id=import_logs_by_message_id or {},
        import_logs_by_id=import_logs_by_id or {},
        import_logs_by_offer_id=import_logs_by_offer_id or {},
    )
    source_url = activity_detail_url_for_import_log(import_log, user=user)
    if source_url:
        return source_url, resolution_path, None
    if resolution_path and import_log:
        return None, resolution_path, explain_import_log_source_block(import_log, user=user)
    if not offer.get("message_id") and not _offer_direct_import_log_id(offer):
        return None, None, "offer_message_id_missing"
    return None, resolution_path, "no_import_log_resolved"


def build_dealer_offer_rows(offers: list[Record]) -> list[Record]:
    """Format active dealer offers for the detail page table."""
    rows: list[Record] = []
    for offer in sorted(offers, key=_sort_key_usd_price):
        watch = offer.get("watch") or _nested_record(offer.get("watches"))
        rows.append(
            {
                "offer_id": offer.get("id"),
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
                "source_url": offer.get("source_url"),
            }
        )
    return rows
