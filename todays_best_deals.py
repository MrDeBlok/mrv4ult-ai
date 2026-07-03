"""Today's Best Deals ranking for the Trading Desk dashboard."""

from __future__ import annotations

from typing import Any

from contact_classification import format_import_sender_label
from dealer_intelligence import clean_whatsapp_number_for_link

Record = dict[str, Any]

TODAYS_BEST_DEALS_LIMIT = 5
TODAYS_BEST_DEALS_SCAN_LIMIT = 20

RANKABLE_RECOMMENDATIONS = frozenset({"Excellent Buy", "Good Buy", "Fair Price"})

RECOMMENDATION_RANK = {
    "Excellent Buy": 0,
    "Good Buy": 1,
    "Fair Price": 2,
}

RECOMMENDATION_BADGE_CLASSES = {
    "excellent": "success",
    "good": "primary",
    "market": "secondary",
    "expensive": "warning",
    "insufficient": "secondary",
}


def _parse_usd_amount(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "").replace("$", "").replace("+", "")
    if cleaned.startswith("-"):
        digits = cleaned[1:]
        if not digits.isdigit():
            return None
        return -int(digits)
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _message_dealer_url(import_log: Record) -> str | None:
    whatsapp = str(import_log.get("dealer_whatsapp") or "").strip()
    digits = clean_whatsapp_number_for_link(whatsapp)
    if not digits:
        return None
    return f"https://wa.me/{digits}"


def _is_rankable_deal(analysis: Record) -> bool:
    if not analysis.get("condition_is_known"):
        return False
    if not analysis.get("show_market_metrics"):
        return False
    recommendation = str(analysis.get("recommendation") or "")
    if recommendation not in RANKABLE_RECOMMENDATIONS:
        return False
    return True


def _deal_sort_key(candidate: Record) -> tuple[Any, ...]:
    return (
        RECOMMENDATION_RANK.get(str(candidate.get("recommendation") or ""), 99),
        candidate.get("_market_delta_usd") or 0,
        -(candidate.get("_potential_profit_usd") or 0),
        -(candidate.get("_confidence") or 0),
        candidate.get("_import_time") or "",
    )


def _build_dashboard_deal_row(
    import_log: Record,
    analysis: Record,
    *,
    watch: Record,
    row: Record,
) -> Record:
    offer_usd = row.get("usd_price")
    if offer_usd is None:
        offer_usd = watch.get("usd_price")
    market_usd = _parse_usd_amount(row.get("previous_lowest_usd"))
    offer_usd_int = int(offer_usd) if isinstance(offer_usd, int) else None
    market_delta = None
    potential_profit_usd = 0
    if offer_usd_int is not None and market_usd is not None:
        market_delta = offer_usd_int - market_usd
        if market_usd > offer_usd_int:
            potential_profit_usd = market_usd - offer_usd_int

    parser_confidence = watch.get("confidence")
    confidence = parser_confidence if isinstance(parser_confidence, int) else 0
    recommendation_class = str(analysis.get("recommendation_class") or "secondary")
    brand = watch.get("brand") or row.get("brand") or "—"
    reference = watch.get("reference") or row.get("reference") or "—"
    import_log_id = str(import_log.get("id") or "")

    return {
        "brand": brand,
        "reference": reference,
        "condition": analysis.get("condition_label") or "Unknown",
        "dealer": format_import_sender_label(import_log),
        "offer_price": analysis.get("offer_price") or "N/A",
        "market_price": analysis.get("market_price") or "Unknown",
        "potential_profit": analysis.get("potential_profit") or "—",
        "show_potential_profit": bool(analysis.get("show_market_metrics")),
        "recommendation": analysis.get("recommendation") or "—",
        "recommendation_badge_class": RECOMMENDATION_BADGE_CLASSES.get(
            recommendation_class,
            "secondary",
        ),
        "confidence": f"{confidence}%" if confidence else "—",
        "deal_url": f"/activity/{import_log_id}" if import_log_id else None,
        "message_dealer_url": _message_dealer_url(import_log),
        "recommendation_class": recommendation_class,
        "_market_delta_usd": market_delta if market_delta is not None else 0,
        "_potential_profit_usd": potential_profit_usd,
        "_confidence": confidence,
        "_import_time": import_log.get("import_time") or "",
    }


def load_dashboard_todays_best_deals(
    user: Record | None,
    import_logs: list[Record],
    *,
    limit: int = TODAYS_BEST_DEALS_LIMIT,
    scan_limit: int = TODAYS_BEST_DEALS_SCAN_LIMIT,
) -> tuple[list[Record], int]:
    """Return ranked condition-safe actionable deals from recent import summaries."""
    del user  # visibility is applied before import logs reach this loader
    if not import_logs:
        return [], 0

    from app import _deal_analysis_watch_sources, build_deal_analysis_cards

    sorted_logs = sorted(
        import_logs,
        key=lambda row: row.get("import_time") or "",
        reverse=True,
    )[:scan_limit]

    preload_rows: list[Record] = []
    prepared_logs: list[tuple[Record, Record]] = []
    for import_log in sorted_logs:
        summary = import_log.get("summary") or {}
        if not isinstance(summary, dict):
            continue
        rows = summary.get("rows") or []
        if isinstance(rows, list):
            preload_rows.extend(row for row in rows if isinstance(row, dict))
        prepared_logs.append((import_log, summary))

    from deal_market_lookup import build_deal_market_preload

    market_preload = build_deal_market_preload(preload_rows)

    candidates: list[Record] = []
    for import_log, summary in prepared_logs:
        watches = _deal_analysis_watch_sources(summary)
        rows = summary.get("rows") or []
        analyses = build_deal_analysis_cards(summary, market_preload=market_preload)
        for index, analysis in enumerate(analyses):
            if not _is_rankable_deal(analysis):
                continue
            watch = watches[index] if index < len(watches) else {}
            row = rows[index] if index < len(rows) else {}
            candidates.append(
                _build_dashboard_deal_row(
                    import_log,
                    analysis,
                    watch=watch if isinstance(watch, dict) else {},
                    row=row if isinstance(row, dict) else {},
                )
            )

    candidates.sort(key=_deal_sort_key)
    cleaned: list[Record] = []
    for candidate in candidates[:limit]:
        item = dict(candidate)
        item.pop("_market_delta_usd", None)
        item.pop("_potential_profit_usd", None)
        item.pop("_confidence", None)
        item.pop("_import_time", None)
        cleaned.append(item)

    strong_count = sum(
        1
        for deal in cleaned
        if deal.get("recommendation") in {"Excellent Buy", "Good Buy"}
    )
    return cleaned, strong_count
