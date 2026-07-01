"""Market demand page built from request_intent import logs."""

from __future__ import annotations

from typing import Any

from activity_feed import message_preview
from contact_classification import format_import_sender_label, should_redact_import_sender
from database import (
    attach_import_log_summaries,
    get_import_log,
    get_message_by_id,
    get_messages_by_ids,
    list_market_request_import_logs,
)
from dealer_intelligence import format_activity_timestamp
from import_status import filter_discarded_import_logs, normalize_import_status
from search import _display_value, format_price, format_usd_price
from user_visibility import can_view_import, filter_imports_for_user

Record = dict[str, Any]


def is_market_request_import(import_log: Record) -> bool:
    """Return True when an import log is a WTB / LF / ISO market request."""
    return normalize_import_status(import_log) == "request_intent"


def can_view_market_request(user: Record | None, import_log: Record | None) -> bool:
    """Return True when a user may view a market request import."""
    if import_log is None or user is None:
        return False
    return is_market_request_import(import_log) and can_view_import(user, import_log)


def filter_market_request_imports(import_logs: list[Record]) -> list[Record]:
    """Return only request_intent imports."""
    return [import_log for import_log in import_logs if is_market_request_import(import_log)]


def market_request_import_logs_for_user(user: Record | None) -> list[Record]:
    """Return visible request_intent imports for the current user."""
    visible_logs = filter_discarded_import_logs(list_import_logs())
    visible_logs = filter_imports_for_user(visible_logs, user)
    return filter_market_request_imports(visible_logs)


# Backward-compatible alias for tests and older patches.
list_import_logs = list_market_request_import_logs


def _primary_watch(import_log: Record) -> Record:
    summary = import_log.get("summary") or {}
    watches = summary.get("parsed_watches") or summary.get("rows") or []
    if isinstance(watches, list) and watches:
        first = watches[0]
        return first if isinstance(first, dict) else {}
    return {}


def market_request_intent_meta(import_log: Record) -> Record:
    """Return sold-order / urgency badges for a market request import."""
    summary = import_log.get("summary") or {}
    badges: list[Record] = []
    if summary.get("request_intent_kind") == "sold_order":
        badges.append({"label": "Sold order", "class": "warning"})
    if summary.get("request_urgency") == "high":
        badges.append({"label": "Urgent", "class": "danger"})
    if summary.get("request_intent_needs_review"):
        badges.append({"label": "WTB Needs Review", "class": "secondary"})
    return {
        "intent_kind": summary.get("request_intent_kind"),
        "urgency": summary.get("request_urgency"),
        "needs_review": bool(summary.get("request_intent_needs_review")),
        "badges": badges,
    }


def _watch_nickname(watch: Record) -> str | None:
    nickname = watch.get("nickname")
    if nickname:
        return str(nickname)
    model_alias = watch.get("model_alias") or {}
    for key in ("nickname", "alias", "model"):
        value = model_alias.get(key)
        if value:
            return str(value)
    return None


def _watch_budget(watch: Record) -> str:
    original_price = watch.get("original_price")
    if original_price is None:
        original_price = watch.get("price")
    original_currency = watch.get("original_currency") or watch.get("currency")
    if original_price is not None:
        return format_price(original_price, original_currency)
    usd_price = watch.get("usd_price")
    if usd_price is not None:
        return format_usd_price(usd_price)
    return "N/A"


def _normalize_dedupe_token(value: str | None) -> str:
    return " ".join(str(value or "").lower().split())


def market_request_dedupe_key(
    import_log: Record,
    message: Record | None = None,
) -> tuple[str, str, str, str, str]:
    """Build a UI dedupe key from watch identity, message intent, and owner."""
    watch = _primary_watch(import_log)
    raw_message = (message or {}).get("raw_text") if message else None
    if not raw_message:
        raw_message = (import_log.get("summary") or {}).get("message_text")
    return (
        _normalize_dedupe_token(watch.get("brand")),
        _normalize_dedupe_token(watch.get("reference")),
        _normalize_dedupe_token(_watch_nickname(watch)),
        _normalize_dedupe_token(raw_message),
        _normalize_dedupe_token(str(import_log.get("imported_by_user_id") or "")),
    )


def resolve_market_request_contact(import_log: Record) -> Record:
    """Resolve sender contact details for market request views."""
    if is_market_request_import(import_log):
        alias = str(import_log.get("dealer_alias") or "").strip()
        whatsapp = str(import_log.get("dealer_whatsapp") or "").strip()
        if alias or whatsapp:
            return {
                "name": alias or whatsapp,
                "whatsapp": whatsapp or "N/A",
                "phone": whatsapp or "N/A",
                "redacted": False,
            }

    if should_redact_import_sender(import_log):
        return {
            "name": format_import_sender_label(import_log),
            "whatsapp": "—",
            "phone": "—",
            "redacted": True,
        }

    alias = str(import_log.get("dealer_alias") or "").strip()
    whatsapp = str(import_log.get("dealer_whatsapp") or "").strip()
    return {
        "name": alias or whatsapp or "N/A",
        "whatsapp": whatsapp or "N/A",
        "phone": whatsapp or "N/A",
        "redacted": False,
    }


def build_market_request_row(
    import_log: Record,
    message: Record | None = None,
) -> Record:
    """Format one request_intent import for the market requests page."""
    watch = _primary_watch(import_log)
    contact = resolve_market_request_contact(import_log)
    import_id = str(import_log["id"])
    intent_meta = market_request_intent_meta(import_log)
    return {
        "id": import_id,
        "detail_url": f"/market-requests/{import_id}",
        "import_time": format_activity_timestamp(import_log.get("import_time")),
        "_import_time_raw": import_log.get("import_time") or "",
        "group_name": import_log.get("group_name") or "N/A",
        "source_contact": contact["name"],
        "source_whatsapp": contact["whatsapp"],
        "source_redacted": contact["redacted"],
        "brand": _display_value(watch.get("brand")),
        "model": _display_value(watch.get("model")),
        "reference": _display_value(watch.get("reference")),
        "nickname": _display_value(_watch_nickname(watch)),
        "budget": _watch_budget(watch),
        "message_preview": message_preview(message.get("raw_text") if message else None),
        "activity_url": f"/activity/{import_id}",
        "intent_kind": intent_meta["intent_kind"],
        "urgency": intent_meta["urgency"],
        "needs_review": intent_meta["needs_review"],
        "badges": intent_meta["badges"],
        "_dedupe_key": market_request_dedupe_key(import_log, message),
        "groups_seen_count": 1,
        "groups_seen_label": "",
        "duplicate_import_ids": [import_id],
    }


def normalize_market_filter(value: str | None) -> str:
    return (value or "").strip()


def filter_market_request_rows(
    rows: list[Record],
    *,
    brand: str = "",
    reference: str = "",
    group: str = "",
) -> list[Record]:
    """Filter market request rows by brand, reference, and group."""
    brand_filter = normalize_market_filter(brand).lower()
    reference_filter = normalize_market_filter(reference).lower()
    group_filter = normalize_market_filter(group).lower()

    filtered: list[Record] = []
    for row in rows:
        if brand_filter and brand_filter not in str(row.get("brand") or "").lower():
            continue
        if reference_filter and reference_filter not in str(row.get("reference") or "").lower():
            continue
        if group_filter and group_filter not in str(row.get("group_name") or "").lower():
            continue
        filtered.append(row)
    return filtered


def dedupe_market_request_rows(rows: list[Record]) -> list[Record]:
    """Collapse duplicate market requests across groups, keeping the newest row."""
    clusters: dict[tuple[str, str, str, str, str], list[Record]] = {}
    for row in rows:
        clusters.setdefault(row["_dedupe_key"], []).append(row)

    deduped: list[Record] = []
    for cluster_rows in clusters.values():
        cluster_rows.sort(key=lambda row: row.get("_import_time_raw") or "", reverse=True)
        primary = dict(cluster_rows[0])
        groups = sorted(
            {
                str(row.get("group_name") or "N/A")
                for row in cluster_rows
                if row.get("group_name")
            }
        )
        duplicate_import_ids = [str(row["id"]) for row in cluster_rows]
        groups_count = len(groups)
        primary["groups_seen_count"] = groups_count
        primary["groups_seen_label"] = (
            f"Seen in {groups_count} groups" if groups_count > 1 else ""
        )
        primary["duplicate_import_ids"] = duplicate_import_ids
        primary["detail_url"] = f"/market-requests/{primary['id']}"
        deduped.append(primary)

    deduped.sort(key=lambda row: row.get("_import_time_raw") or "", reverse=True)
    return deduped


def _strip_internal_market_request_fields(row: Record) -> Record:
    cleaned = dict(row)
    cleaned.pop("_dedupe_key", None)
    cleaned.pop("_import_time_raw", None)
    return cleaned


def _messages_by_id_for_import_logs(import_logs: list[Record]) -> dict[str, Record]:
    """Batch-load message rows for market request import logs."""
    message_ids = [
        str(import_log["message_id"])
        for import_log in import_logs
        if import_log.get("message_id")
    ]
    if not message_ids:
        return {}
    return get_messages_by_ids(list(dict.fromkeys(message_ids)))


def load_market_request_rows(
    user: Record | None,
    *,
    brand: str = "",
    reference: str = "",
    group: str = "",
) -> list[Record]:
    """Load deduped market request rows sorted newest first with optional filters."""
    import_logs = market_request_import_logs_for_user(user)
    import_logs.sort(
        key=lambda import_log: import_log.get("import_time") or "",
        reverse=True,
    )

    import_logs = attach_import_log_summaries(import_logs)
    messages_by_id = _messages_by_id_for_import_logs(import_logs)
    rows: list[Record] = []
    for import_log in import_logs:
        message = messages_by_id.get(str(import_log.get("message_id") or ""))
        rows.append(build_market_request_row(import_log, message))

    rows = filter_market_request_rows(
        rows,
        brand=brand,
        reference=reference,
        group=group,
    )
    rows = dedupe_market_request_rows(rows)
    return [_strip_internal_market_request_fields(row) for row in rows]


def build_market_request_source_row(
    import_log: Record,
    message: Record | None = None,
) -> Record:
    """Format one duplicate source occurrence for the detail page."""
    contact = resolve_market_request_contact(import_log)
    return {
        "id": import_log["id"],
        "import_time": format_activity_timestamp(import_log.get("import_time")),
        "group_name": import_log.get("group_name") or "N/A",
        "source_contact": contact["name"],
        "source_whatsapp": contact["whatsapp"],
        "activity_url": f"/activity/{import_log['id']}",
    }


def build_market_request_detail(
    import_log: Record,
    message: Record | None,
    *,
    related_sources: list[Record],
    matching_offers: list[Record] | None = None,
    opportunity_analysis: Record | None = None,
) -> Record:
    """Format one market request for the detail page."""
    row = build_market_request_row(import_log, message)
    contact = resolve_market_request_contact(import_log)
    raw_message = (message or {}).get("raw_text") or ""
    groups = sorted({source["group_name"] for source in related_sources})
    return {
        "id": import_log["id"],
        "import_time": row["import_time"],
        "group_name": row["group_name"],
        "brand": row["brand"],
        "model": row["model"],
        "reference": row["reference"],
        "nickname": row["nickname"],
        "budget": row["budget"],
        "source_contact": contact["name"],
        "source_whatsapp": contact["whatsapp"],
        "source_phone": contact["phone"],
        "source_redacted": contact["redacted"],
        "message_preview": row["message_preview"],
        "raw_message": raw_message or "N/A",
        "activity_url": f"/activity/{import_log['id']}",
        "intent_kind": row.get("intent_kind"),
        "urgency": row.get("urgency"),
        "needs_review": row.get("needs_review"),
        "badges": row.get("badges") or [],
        "groups_seen_count": len(groups),
        "groups_seen_label": (
            f"Seen in {len(groups)} groups" if len(groups) > 1 else ""
        ),
        "related_sources": related_sources,
        "matching_offers": matching_offers or [],
        "opportunity_analysis": opportunity_analysis
        or {
            "has_opportunities": False,
            "empty_message": "No opportunity found yet.",
            "ai_advisor_summary": "No matching offers yet. Keep monitoring market requests for fresh stock.",
            "opportunity_score": None,
            "score_label": None,
            "confidence_label": None,
            "confidence_badge_class": "secondary",
            "health": None,
            "health_badge_class": "secondary",
            "data_quality_confidence_pct": None,
            "data_quality_confidence_reason": None,
            "urgency": None,
            "urgency_badge_class": "secondary",
            "potential_spread": None,
            "potential_profit": None,
            "potential_profit_title": None,
            "potential_profit_value": None,
            "potential_profit_subtitle": None,
            "budget_known": False,
            "positive_reasons": [],
            "warning_reasons": [],
            "reasons": [],
            "recommended_action": None,
            "recommendation": None,
            "recommendation_badge_class": "secondary",
            "score_card": None,
            "best_match": None,
        },
    }


def load_market_request_detail(
    user: Record | None,
    import_id: str,
) -> Record | None:
    """Load one market request detail page payload."""
    import_log = get_import_log(import_id)
    if not can_view_market_request(user, import_log):
        return None

    message = None
    message_id = import_log.get("message_id")
    if message_id:
        message = get_message_by_id(str(message_id))

    dedupe_key = market_request_dedupe_key(import_log, message)
    related_sources: list[Record] = []
    for candidate in market_request_import_logs_for_user(user):
        candidate_message = None
        candidate_message_id = candidate.get("message_id")
        if candidate_message_id:
            candidate_message = get_message_by_id(str(candidate_message_id))
        if market_request_dedupe_key(candidate, candidate_message) != dedupe_key:
            continue
        related_sources.append(build_market_request_source_row(candidate, candidate_message))

    related_sources.sort(
        key=lambda source: source.get("import_time") or "",
        reverse=True,
    )
    matching_offers, opportunity_analysis = build_market_request_opportunity_bundle(
        user,
        import_log,
    )
    return build_market_request_detail(
        import_log,
        message,
        related_sources=related_sources,
        matching_offers=matching_offers,
        opportunity_analysis=opportunity_analysis,
    )


from opportunity_engine import build_market_request_opportunity_bundle
