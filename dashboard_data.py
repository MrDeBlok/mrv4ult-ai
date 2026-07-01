"""Trading desk dashboard data for daily trader overview."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from activity_feed import message_preview
from contact_classification import build_dealer_lookup_by_whatsapp, filter_business_import_logs, format_import_sender_label
from database import (
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE,
    IMPORT_LOG_LIST_LIMIT_DASHBOARD_MARKET,
    attach_import_log_summaries,
    DASHBOARD_MATCHED_REQUESTS_FETCH_LIMIT,
    DASHBOARD_MATCHED_REQUESTS_LIMIT,
    get_messages_by_ids,
    get_requests_by_ids,
    list_contacts_for_import_lookup,
    list_dashboard_market_request_import_logs,
    list_dashboard_parser_review_import_logs,
    list_dashboard_recent_import_logs,
    list_dashboard_today_import_logs,
    list_recent_notifications,
    list_recent_request_matches,
    load_enriched_request_match_batch,
)
from dealer_intelligence import dealer_display_name, format_activity_timestamp
from import_status import filter_discarded_import_logs, format_import_status, import_status_class, normalize_import_status
from market_request_matching import (
    classify_market_request_match,
    extract_market_request_criteria,
    filter_matching_offers_for_user,
)
from market_requests import _primary_watch, filter_market_request_imports
from notifications import NOTIFICATION_TYPE_LABELS, get_unread_notification_count
from opportunity_engine import (
    build_profit_display,
    calculate_potential_spread_usd,
    market_request_budget_usd,
)
from opportunity_intelligence import (
    URGENCY_NORMAL,
    confidence_badge_class,
    normalize_recommendation,
    recommend_action,
    recommendation_badge_class,
)
from parser_review import build_parser_review_row, filter_parser_review_imports, parser_review_counts
from permissions import can_view_page, is_viewer
from request_profit import attach_profit_to_matches, offer_price_usd
from search import _nested_record
from timezone_utils import DISPLAY_TIMEZONE, ensure_utc_datetime, parse_utc_timestamp
from user_visibility import can_view_import, filter_imports_for_user

Record = dict[str, Any]

logger = logging.getLogger(__name__)

HIGH_OPPORTUNITY_MIN_SCORE = 75
TOP_OPPORTUNITIES_LIMIT = 5
TOP_OPPORTUNITIES_SCAN_LIMIT = 5
AI_NEEDS_HELP_LIMIT = 5
LIVE_MARKET_LIMIT = 10
MATCHED_REQUESTS_LIMIT = DASHBOARD_MATCHED_REQUESTS_LIMIT
AI_NOTIFICATIONS_FETCH_LIMIT = 10

MATCH_STRENGTH_LABELS = {
    "strong": "Strong match",
    "medium": "Good match",
}
MATCH_STRENGTH_BADGE_CLASSES = {
    "strong": "success",
    "medium": "primary",
}

LIGHTWEIGHT_BASE_SCORE = 25
LIGHTWEIGHT_EXACT_BOOST = 35
LIGHTWEIGHT_ALIAS_BOOST = 20
LIGHTWEIGHT_BUDGET_ABOVE_BOOST = 25


def _log_dashboard_section(section: str, started: float) -> None:
    duration_ms = (time.perf_counter() - started) * 1000
    logger.info("Dashboard section=%s duration_ms=%.2f", section, duration_ms)


def parser_review_import_logs_for_user(user: Record | None) -> list[Record]:
    """Return business imports eligible for parser review, scoped to the user."""
    from database import list_parser_review_import_log_candidates

    visible_imports = filter_discarded_import_logs(
        filter_imports_for_user(list_parser_review_import_log_candidates(), user)
    )
    lookup = build_dealer_lookup_by_whatsapp(list_contacts_for_import_lookup())
    return filter_business_import_logs(visible_imports, lookup)


def is_import_today(import_log: Record, *, now: datetime | None = None) -> bool:
    """Return True when an import happened on the current calendar day in Amsterdam."""
    timestamp = parse_utc_timestamp(import_log.get("import_time"))
    if timestamp is None:
        return False
    current = now or datetime.now(DISPLAY_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=DISPLAY_TIMEZONE)
    return timestamp.astimezone(DISPLAY_TIMEZONE).date() == current.astimezone(DISPLAY_TIMEZONE).date()


def _dashboard_today_start_iso(now: datetime | None = None) -> str:
    """Return the UTC ISO timestamp for the start of today in Amsterdam."""
    current = now or datetime.now(DISPLAY_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=DISPLAY_TIMEZONE)
    start_local = current.astimezone(DISPLAY_TIMEZONE).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return ensure_utc_datetime(start_local).isoformat()


def _visible_dashboard_import_slices(
    user: Record | None,
    contact_lookup: dict[str, Record],
    *,
    now: datetime | None = None,
) -> tuple[list[Record], list[Record], list[Record], list[Record]]:
    """Fetch and filter bounded import slices for dashboard sections."""
    today_start = _dashboard_today_start_iso(now)
    recent_raw = list_dashboard_recent_import_logs(
        since_iso=today_start,
        limit=IMPORT_LOG_LIST_LIMIT_DASHBOARD_LIVE,
    )
    today_raw = list_dashboard_today_import_logs(since_iso=today_start)
    market_raw = list_dashboard_market_request_import_logs()
    parser_raw = list_dashboard_parser_review_import_logs()

    business_recent = filter_business_import_logs(
        filter_discarded_import_logs(filter_imports_for_user(recent_raw, user)),
        contact_lookup,
    )
    business_today = filter_business_import_logs(
        filter_discarded_import_logs(filter_imports_for_user(today_raw, user)),
        contact_lookup,
    )
    market_request_logs = filter_market_request_imports(
        filter_discarded_import_logs(filter_imports_for_user(market_raw, user))
    )
    parser_business = filter_business_import_logs(
        filter_discarded_import_logs(filter_imports_for_user(parser_raw, user)),
        contact_lookup,
    )
    return business_recent, business_today, market_request_logs, parser_business


def visible_business_import_logs(user: Record | None) -> list[Record]:
    """Return user-visible business imports for legacy dashboard callers."""
    contact_lookup = build_dealer_lookup_by_whatsapp(list_contacts_for_import_lookup())
    _recent, business_today, _market, parser_business = _visible_dashboard_import_slices(
        user,
        contact_lookup,
    )
    combined = {str(row["id"]): row for row in [*business_today, *parser_business]}
    return list(combined.values())



def count_new_offers_today(import_logs: list[Record], *, now: datetime | None = None) -> int:
    """Return how many new offers were created from today's imports."""
    return sum(
        int(import_log.get("new_offers") or 0)
        for import_log in import_logs
        if is_import_today(import_log, now=now)
    )


def _score_label_for_points(score: int) -> str:
    if score >= HIGH_OPPORTUNITY_MIN_SCORE:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 45:
        return "Possible"
    return "Low"


def _lightweight_opportunity_score(
    import_log: Record,
    offer: Record,
    *,
    match_type: str,
) -> int:
    """Estimate opportunity score without full Opportunity Analysis."""
    score = LIGHTWEIGHT_BASE_SCORE
    if match_type == "exact_reference":
        score += LIGHTWEIGHT_EXACT_BOOST
    else:
        score += LIGHTWEIGHT_ALIAS_BOOST

    budget_usd = market_request_budget_usd(import_log)
    offer_usd = offer_price_usd(offer)
    spread_usd = calculate_potential_spread_usd(budget_usd, offer_usd)
    if spread_usd is not None and spread_usd > 0:
        score += LIGHTWEIGHT_BUDGET_ABOVE_BOOST
    return max(score, 0)


def _best_lightweight_match(
    import_log: Record,
    visible_offers: list[Record],
) -> Record | None:
    """Return the best lightweight match for one market request."""
    criteria = extract_market_request_criteria(import_log)
    best_offer: Record | None = None
    best_score = 0
    best_match_type = ""

    for offer in visible_offers:
        match_type = classify_market_request_match(criteria, offer)
        if match_type is None:
            continue
        score = _lightweight_opportunity_score(
            import_log,
            offer,
            match_type=match_type,
        )
        if score > best_score:
            best_score = score
            best_offer = offer
            best_match_type = match_type

    if best_offer is None or best_score < HIGH_OPPORTUNITY_MIN_SCORE:
        return None

    dealer = _nested_record(best_offer.get("dealers"))
    budget_usd = market_request_budget_usd(import_log)
    offer_usd = offer_price_usd(best_offer)
    spread_usd = calculate_potential_spread_usd(budget_usd, offer_usd)
    profit = build_profit_display(budget_usd, spread_usd)
    score_label = _score_label_for_points(best_score)
    recommendation = normalize_recommendation(
        recommend_action(best_score, urgency=URGENCY_NORMAL)
    )

    return {
        "score": best_score,
        "score_label": score_label,
        "score_badge_class": confidence_badge_class(score_label),
        "watch_label": _watch_label(import_log),
        "dealer": dealer_display_name(dealer),
        "potential_profit": profit.get("potential_profit") or "—",
        "recommendation": recommendation,
        "recommendation_badge_class": recommendation_badge_class(recommendation),
        "detail_url": f"/market-requests/{import_log['id']}",
        "_sort_score": best_score,
        "_sort_time": import_log.get("import_time") or "",
        "_match_type": best_match_type,
    }


def _watch_label(import_log: Record) -> str:
    watch = _primary_watch(import_log)
    parts = [
        str(watch.get("brand") or "").strip(),
        str(watch.get("reference") or watch.get("model") or watch.get("nickname") or "").strip(),
    ]
    label = " ".join(part for part in parts if part)
    return label or "Market request"


def load_dashboard_top_opportunities(
    user: Record | None,
    market_request_logs: list[Record],
    *,
    limit: int = TOP_OPPORTUNITIES_LIMIT,
    scan_limit: int = TOP_OPPORTUNITIES_SCAN_LIMIT,
    now: datetime | None = None,
) -> tuple[list[Record], int]:
    """Return top dashboard opportunities using lightweight matching only."""
    del now  # kept for API compatibility with earlier callers
    sorted_logs = sorted(
        market_request_logs,
        key=lambda row: row.get("import_time") or "",
        reverse=True,
    )
    candidate_logs = sorted_logs[:scan_limit]
    if not candidate_logs:
        return [], 0

    from database import list_active_offers_for_market_matching

    offers = list_active_offers_for_market_matching()
    visible_offers = filter_matching_offers_for_user(offers, user)

    ranked: list[Record] = []
    high_count = 0
    for import_log in candidate_logs:
        match = _best_lightweight_match(import_log, visible_offers)
        if match is None:
            continue
        high_count += 1
        ranked.append(match)

    ranked.sort(
        key=lambda row: (row["_sort_score"], row["_sort_time"]),
        reverse=True,
    )
    cleaned: list[Record] = []
    for row in ranked[:limit]:
        item = dict(row)
        item.pop("_sort_score", None)
        item.pop("_sort_time", None)
        item.pop("_match_type", None)
        cleaned.append(item)
    return cleaned, high_count


def load_top_opportunities(
    user: Record | None,
    *,
    limit: int = TOP_OPPORTUNITIES_LIMIT,
    now: datetime | None = None,
) -> list[Record]:
    """Legacy wrapper returning lightweight dashboard opportunities."""
    import_logs = filter_market_request_imports(
        filter_discarded_import_logs(
            filter_imports_for_user(
                list_dashboard_market_request_import_logs(
                    limit=IMPORT_LOG_LIST_LIMIT_DASHBOARD_MARKET,
                ),
                user,
            )
        )
    )
    rows, _high_count = load_dashboard_top_opportunities(
        user,
        import_logs,
        limit=limit,
        now=now,
    )
    return rows


def count_high_opportunities(
    user: Record | None,
    market_request_logs: list[Record] | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Return how many recent market requests score as high opportunities."""
    if market_request_logs is None:
        market_request_logs = filter_market_request_imports(
            filter_discarded_import_logs(
                filter_imports_for_user(
                    list_dashboard_market_request_import_logs(
                        limit=IMPORT_LOG_LIST_LIMIT_DASHBOARD_MARKET,
                    ),
                    user,
                )
            )
        )
    _rows, high_count = load_dashboard_top_opportunities(
        user,
        market_request_logs,
        limit=TOP_OPPORTUNITIES_LIMIT,
        now=now,
    )
    return high_count


def load_ai_needs_help_items(
    user: Record | None,
    *,
    business_imports: list[Record] | None = None,
    format_timestamp,
    limit: int = AI_NEEDS_HELP_LIMIT,
) -> list[Record]:
    """Return latest parser review and needs-review notification items."""
    if business_imports is None:
        business_imports = visible_business_import_logs(user)
    else:
        business_imports = attach_import_log_summaries(business_imports)

    items: list[Record] = []
    parser_logs = filter_parser_review_imports(business_imports)
    parser_logs.sort(key=lambda row: row.get("import_time") or "", reverse=True)

    message_ids = [
        str(import_log["message_id"])
        for import_log in parser_logs[:limit]
        if import_log.get("message_id")
    ]
    messages_by_id = get_messages_by_ids(list(dict.fromkeys(message_ids)))

    for import_log in parser_logs[:limit]:
        message = messages_by_id.get(str(import_log.get("message_id") or ""))
        review_row = build_parser_review_row(
            import_log,
            message,
            format_timestamp=format_timestamp,
        )
        items.append(
            {
                "reason": review_row.get("status_reason") or "Parser review needed",
                "message_preview": review_row.get("message_preview") or "—",
                "group_name": review_row.get("group_name") or "N/A",
                "dealer": review_row.get("dealer") or "N/A",
                "review_url": review_row.get("detail_url") or "/parser-review",
                "review_label": "Open import detail",
                "_sort_time": import_log.get("import_time") or "",
            }
        )

    for notification in list_recent_notifications(
        limit=AI_NOTIFICATIONS_FETCH_LIMIT,
        notification_type="needs_review",
    ):
        items.append(
            {
                "reason": notification.get("title") or NOTIFICATION_TYPE_LABELS.get("needs_review", "Needs review"),
                "message_preview": notification.get("message") or "—",
                "group_name": "Notification",
                "dealer": "—",
                "review_url": (
                    f"/activity/{notification['related_import_log_id']}"
                    if notification.get("related_import_log_id")
                    else "/notifications?type=needs_review"
                ),
                "review_label": "Review",
                "_sort_time": notification.get("created_at") or "",
            }
        )

    items.sort(key=lambda row: row.get("_sort_time") or "", reverse=True)
    cleaned: list[Record] = []
    for item in items[:limit]:
        row = dict(item)
        row.pop("_sort_time", None)
        cleaned.append(row)
    return cleaned


def load_live_market_rows(
    import_logs: list[Record],
    *,
    limit: int = LIVE_MARKET_LIMIT,
    now: datetime | None = None,
) -> list[Record]:
    """Return today's latest import activity rows for the trading desk."""
    today_logs = [import_log for import_log in import_logs if is_import_today(import_log, now=now)]
    today_logs.sort(key=lambda row: row.get("import_time") or "", reverse=True)

    message_ids = [
        str(import_log["message_id"])
        for import_log in today_logs[:limit]
        if import_log.get("message_id")
    ]
    messages_by_id = get_messages_by_ids(list(dict.fromkeys(message_ids)))

    rows: list[Record] = []
    for import_log in today_logs[:limit]:
        status = normalize_import_status(import_log)
        message = messages_by_id.get(str(import_log.get("message_id") or ""))
        rows.append(
            {
                "import_time": format_activity_timestamp(import_log.get("import_time")),
                "status": format_import_status(status),
                "status_class": import_status_class(status),
                "group_name": import_log.get("group_name") or "N/A",
                "dealer": format_import_sender_label(import_log),
                "message_preview": message_preview(
                    message.get("raw_text") if message else None,
                    max_length=72,
                ),
                "detail_url": f"/activity/{import_log['id']}",
            }
        )
    return rows


def _request_watch_label(request: Record, watch: Record) -> str:
    parts = [
        str(request.get("brand") or watch.get("brand") or "").strip(),
        str(
            request.get("reference")
            or watch.get("reference")
            or request.get("model")
            or watch.get("model")
            or ""
        ).strip(),
    ]
    label = " ".join(part for part in parts if part)
    return label or "Request"


def _dashboard_match_opportunity_score(match: Record, profit: Record) -> int:
    strength = str(match.get("match_strength") or "")
    score = 100 if strength == "strong" else 75 if strength == "medium" else 50
    profit_usd = profit.get("potential_profit_usd")
    if profit_usd is not None and profit_usd > 0:
        score += min(int(profit_usd) // 1000, 25)
    return score


def _is_visible_dashboard_match(user: Record | None, match: Record) -> bool:
    import_log = match.get("import_log") or {}
    if import_log:
        return can_view_import(user, import_log)
    return False


def load_dashboard_matched_requests(
    user: Record | None,
    *,
    limit: int = MATCHED_REQUESTS_LIMIT,
    fetch_limit: int = DASHBOARD_MATCHED_REQUESTS_FETCH_LIMIT,
) -> list[Record]:
    """Return recent client request matches for the dashboard."""
    raw_matches = list_recent_request_matches(limit=fetch_limit)
    if not raw_matches:
        return []

    enriched_matches = load_enriched_request_match_batch(raw_matches)
    request_ids = sorted({str(match["request_id"]) for match in enriched_matches})
    requests_by_id = get_requests_by_ids(request_ids)

    rows: list[Record] = []
    for match in enriched_matches:
        if not _is_visible_dashboard_match(user, match):
            continue
        request = requests_by_id.get(str(match.get("request_id") or ""))
        if not request:
            continue

        profit_match = attach_profit_to_matches(request, [match])[0]
        profit = profit_match.get("profit") or {}
        watch = match.get("watch") or {}
        import_log = match.get("import_log") or {}
        strength = str(match.get("match_strength") or "")
        confidence_label = MATCH_STRENGTH_LABELS.get(strength, "Match")
        request_url = "/requests" if can_view_page(user, "/requests") else None

        rows.append(
            {
                "match_id": str(match.get("id") or ""),
                "match_url": f"/matches/{match['id']}",
                "client_name": request.get("client_name") or "Client",
                "watch_label": _request_watch_label(request, watch),
                "dealer": format_import_sender_label(import_log),
                "offer_price": profit.get("offer_price") or "—",
                "potential_profit": profit.get("potential_profit") or "—",
                "match_age": format_activity_timestamp(match.get("created_at")),
                "status_label": profit.get("status_label") or "—",
                "status_class": profit.get("status_class") or "secondary",
                "confidence_label": confidence_label,
                "confidence_class": MATCH_STRENGTH_BADGE_CLASSES.get(strength, "secondary"),
                "request_url": request_url,
                "_sort_time": match.get("created_at") or "",
                "_sort_score": _dashboard_match_opportunity_score(match, profit),
            }
        )

    rows.sort(
        key=lambda row: (row.get("_sort_time") or "", row.get("_sort_score") or 0),
        reverse=True,
    )
    cleaned: list[Record] = []
    for row in rows[:limit]:
        item = dict(row)
        item.pop("_sort_time", None)
        item.pop("_sort_score", None)
        cleaned.append(item)
    return cleaned


def build_trading_desk_kpis(
    *,
    new_offers_today: int,
    high_opportunities: int,
    active_market_requests: int,
    ai_needs_help: int,
    unread_notifications: int,
) -> list[Record]:
    """Build Today KPI cards for the trading desk."""
    return [
        {
            "key": "new_offers_today",
            "title": "New offers today",
            "count": new_offers_today,
            "url": "/activity",
            "description": "Fresh dealer offers imported today.",
        },
        {
            "key": "high_opportunities",
            "title": "High opportunities",
            "count": high_opportunities,
            "url": "/market-requests",
            "description": "Strong market request matches worth a look.",
        },
        {
            "key": "active_market_requests",
            "title": "Active market requests",
            "count": active_market_requests,
            "url": "/market-requests",
            "description": "Open buy-side requests in the market feed.",
        },
        {
            "key": "ai_needs_help",
            "title": "AI needs help",
            "count": ai_needs_help,
            "url": "/parser-review",
            "description": "Parser review items waiting for a fix.",
        },
        {
            "key": "unread_notifications",
            "title": "Unread notifications",
            "count": unread_notifications,
            "url": "/notifications",
            "description": "Alerts and matches you have not opened yet.",
        },
    ]


def _visible_kpi_cards(user: Record | None, cards: list[Record]) -> list[Record]:
    """Drop KPI links the current user cannot open."""
    visible_cards: list[Record] = []
    for card in cards:
        item = dict(card)
        if item.get("url") and not can_view_page(user, str(item["url"])):
            item["url"] = None
        visible_cards.append(item)
    return visible_cards


def build_quick_actions(user: Record | None) -> list[Record]:
    """Build quick action links respecting role visibility."""
    actions = [
        {
            "key": "search",
            "label": "Search",
            "url": "/",
            "style": "primary",
            "visible": can_view_page(user, "/"),
        },
        {
            "key": "import",
            "label": "Import",
            "url": "/import",
            "style": "outline-dark",
            "visible": can_view_page(user, "/import"),
        },
        {
            "key": "new_request",
            "label": "New Request",
            "url": "/requests",
            "style": "outline-dark",
            "visible": can_view_page(user, "/requests"),
        },
        {
            "key": "notifications",
            "label": "Notifications",
            "url": "/notifications",
            "style": "outline-dark",
            "visible": can_view_page(user, "/notifications"),
        },
        {
            "key": "parser_review",
            "label": "Teach AI / Parser Review",
            "url": "/parser-review",
            "style": "outline-dark",
            "visible": can_view_page(user, "/parser-review"),
        },
    ]
    return [action for action in actions if action["visible"]]


def load_trading_desk(user: Record | None, *, format_timestamp, now: datetime | None = None) -> Record:
    """Load all trading desk sections for the dashboard page."""
    started = time.perf_counter()
    contact_lookup = build_dealer_lookup_by_whatsapp(list_contacts_for_import_lookup())
    business_recent, business_today, market_request_logs, parser_business = (
        _visible_dashboard_import_slices(user, contact_lookup, now=now)
    )
    _log_dashboard_section("fetch_dashboard_import_slices", started)

    market_request_logs = attach_import_log_summaries(market_request_logs)

    started = time.perf_counter()
    matched_requests = load_dashboard_matched_requests(user)
    _log_dashboard_section("matched_requests", started)

    started = time.perf_counter()
    top_opportunities, high_opportunity_count = load_dashboard_top_opportunities(
        user,
        market_request_logs,
        now=now,
    )
    _log_dashboard_section("top_opportunities", started)

    started = time.perf_counter()
    ai_items = load_ai_needs_help_items(
        user,
        business_imports=parser_business,
        format_timestamp=format_timestamp,
    )
    _log_dashboard_section("ai_needs_help", started)

    started = time.perf_counter()
    live_market = load_live_market_rows(business_recent, now=now)
    _log_dashboard_section("live_market", started)

    started = time.perf_counter()
    parser_counts = parser_review_counts(parser_business)
    kpis = _visible_kpi_cards(
        user,
        build_trading_desk_kpis(
            new_offers_today=count_new_offers_today(business_today, now=now),
            high_opportunities=high_opportunity_count,
            active_market_requests=len(market_request_logs),
            ai_needs_help=parser_counts["total"],
            unread_notifications=get_unread_notification_count(),
        ),
    )
    quick_actions = build_quick_actions(user)
    _log_dashboard_section("kpi_cards", started)

    return {
        "kpis": kpis,
        "quick_actions": quick_actions,
        "matched_requests": matched_requests,
        "top_opportunities": top_opportunities,
        "ai_needs_help": ai_items,
        "live_market": live_market,
        "show_write_actions": not is_viewer(user),
    }
