"""Read-only opportunity scoring for market request matched offers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from advisor import generate_trading_advisor_summary
from market_request_matching import (
    build_market_request_matching_row,
    classify_market_request_match,
    extract_market_request_criteria,
    filter_matching_offers_for_user,
    offer_matches_market_request,
)
from opportunity_intelligence import (
    MATCH_DIFFERENT,
    build_dealer_quality_index,
    calculate_urgency,
    compare_text_attribute,
    confidence_badge_class,
    dealer_rating,
    format_offer_age_reason,
    format_reason,
    format_warning,
    health_for_score,
    normalize_bracelet,
    normalize_condition,
    normalize_dial,
    normalize_recommendation,
    recommend_action,
    recommendation_badge_class,
    score_bracelet_attribute,
    score_condition_attribute,
    score_dealer_quality,
    score_dial_attribute,
    score_full_set_attribute,
    score_production_year_attribute,
    sort_opportunity_rows,
    request_has_full_set_info,
    urgency_badge_class,
)
from request_profit import BUDGET_NEAR_THRESHOLD, offer_price_usd, request_budget_usd
from search import _nested_record, format_usd_price

Record = dict[str, Any]

BASE_SCORE = 25
EXACT_REFERENCE_BOOST = 35
ALIAS_MATCH_BOOST = 20
BUDGET_ABOVE_ASK_BOOST = 25
BUDGET_NEAR_ASK_BOOST = 10
BUDGET_ABOVE_ASK_PENALTY = 15
MISSING_BUDGET_PENALTY = 8
RECENT_OFFER_BOOST = 8
RECENT_REQUEST_BOOST = 5
RECENT_WINDOW_DAYS = 7

CONFIDENCE_EXCELLENT = "Excellent"
CONFIDENCE_GOOD = "Good"
CONFIDENCE_POSSIBLE = "Possible"
CONFIDENCE_LOW = "Low"

DATA_QUALITY_PENALTY_BUDGET = 6
DATA_QUALITY_PENALTY_CONDITION = 2
DATA_QUALITY_PENALTY_DIAL = 2
DATA_QUALITY_PENALTY_BRACELET = 2
DATA_QUALITY_PENALTY_YEAR = 2
DATA_QUALITY_PENALTY_FULL_SET = 2


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _recency_boost(timestamp: str | None, *, now: datetime, within_days: int = RECENT_WINDOW_DAYS) -> int:
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return 0
    age_days = (now - parsed).total_seconds() / 86400
    if age_days < 0:
        return 0
    if age_days <= 1:
        return RECENT_OFFER_BOOST if within_days == RECENT_WINDOW_DAYS else RECENT_REQUEST_BOOST
    if age_days <= within_days:
        return max(RECENT_REQUEST_BOOST, RECENT_OFFER_BOOST // 2)
    return 0


def market_request_budget_usd(import_log: Record) -> int | None:
    from market_requests import _primary_watch

    watch = _primary_watch(import_log)
    request_like = {
        "max_price": watch.get("price") if watch.get("price") is not None else watch.get("original_price"),
        "currency": watch.get("currency") or watch.get("original_currency") or "USD",
    }
    return request_budget_usd(request_like)


def request_watch(import_log: Record) -> Record:
    from market_requests import _primary_watch

    return _primary_watch(import_log)


def score_label_for_score(score: int) -> str:
    if score >= 90:
        return CONFIDENCE_EXCELLENT
    if score >= 75:
        return CONFIDENCE_GOOD
    if score >= 50:
        return CONFIDENCE_POSSIBLE
    return CONFIDENCE_LOW


confidence_label_for_score = score_label_for_score


def calculate_potential_spread_usd(budget_usd: int | None, offer_usd: int | None) -> int | None:
    if budget_usd is None or offer_usd is None:
        return None
    return budget_usd - offer_usd


def _request_has_full_set_info(watch: Record) -> bool:
    return request_has_full_set_info(watch)


def calculate_data_quality_confidence(import_log: Record) -> Record:
    """Return data-quality confidence based on missing request-side watch fields."""
    request = request_watch(import_log)
    confidence = 100
    missing_labels: list[str] = []

    if market_request_budget_usd(import_log) is None:
        confidence -= DATA_QUALITY_PENALTY_BUDGET
        missing_labels.append("Missing client budget")
    if normalize_condition(request.get("condition")) is None:
        confidence -= DATA_QUALITY_PENALTY_CONDITION
        missing_labels.append("Missing condition")
    if normalize_dial(request.get("dial")) is None:
        confidence -= DATA_QUALITY_PENALTY_DIAL
        missing_labels.append("Missing dial")
    if normalize_bracelet(request.get("bracelet")) is None:
        confidence -= DATA_QUALITY_PENALTY_BRACELET
        missing_labels.append("Missing bracelet")
    if not isinstance(request.get("production_year"), int):
        confidence -= DATA_QUALITY_PENALTY_YEAR
        missing_labels.append("Missing production year")
    if not _request_has_full_set_info(request):
        confidence -= DATA_QUALITY_PENALTY_FULL_SET
        missing_labels.append("Missing card/full set")

    confidence = max(0, min(100, confidence))
    if missing_labels:
        reason = missing_labels[0] if len(missing_labels) == 1 else "; ".join(missing_labels)
    else:
        reason = "Complete request data"
    return {
        "data_quality_confidence_pct": confidence,
        "data_quality_confidence_reason": reason,
        "missing_data_labels": missing_labels,
    }


def build_profit_display(budget_usd: int | None, spread_usd: int | None) -> Record:
    if budget_usd is None:
        return {
            "budget_known": False,
            "potential_profit_title": "Budget unknown",
            "potential_profit_value": None,
            "potential_profit_subtitle": "Unable to calculate profit",
            "potential_profit": "Budget unknown",
            "potential_profit_detail": "Unable to calculate profit",
            "potential_spread": "—",
        }

    if spread_usd is None:
        formatted = "—"
        signed = "—"
    elif spread_usd > 0:
        formatted = format_usd_price(spread_usd)
        signed = f"+{formatted}"
    else:
        formatted = format_usd_price(spread_usd)
        signed = formatted

    return {
        "budget_known": True,
        "potential_profit_title": "Potential Profit",
        "potential_profit_value": signed,
        "potential_profit_subtitle": None,
        "potential_profit": signed,
        "potential_profit_detail": None,
        "potential_spread": formatted,
    }


def build_score_card(row: Record) -> Record:
    profit = {
        "potential_profit_title": row.get("potential_profit_title"),
        "potential_profit_value": row.get("potential_profit_value"),
        "potential_profit_subtitle": row.get("potential_profit_subtitle"),
    }
    recommendation = row.get("recommendation") or row.get("recommended_action")
    return {
        "opportunity_score": row.get("opportunity_score"),
        "score_label": row.get("score_label") or row.get("confidence_label"),
        "score_badge_class": row.get("confidence_badge_class"),
        "health": row.get("health"),
        "health_badge_class": row.get("health_badge_class"),
        "urgency": row.get("urgency"),
        "urgency_badge_class": row.get("urgency_badge_class"),
        "recommendation": recommendation,
        "recommendation_badge_class": row.get("recommendation_badge_class"),
        **profit,
    }


def score_market_request_opportunity(
    import_log: Record,
    offer: Record,
    *,
    match_type: str,
    dealer_index: dict[str, Record] | None = None,
    now: datetime | None = None,
) -> Record:
    """Score one visible matching offer against a market request."""
    current = _utc_now(now)
    budget_usd = market_request_budget_usd(import_log)
    offer_usd = offer_price_usd(offer)
    spread_usd = calculate_potential_spread_usd(budget_usd, offer_usd)
    request = request_watch(import_log)
    offer_watch = _nested_record(offer.get("watches"))
    message = _nested_record(offer.get("messages"))
    dealer = _nested_record(offer.get("dealers"))
    dealer_id = str(dealer.get("id") or offer.get("dealer_id") or "")
    data_quality = calculate_data_quality_confidence(import_log)
    profit_display = build_profit_display(budget_usd, spread_usd)
    rating_label, rating_class = dealer_rating(dealer_id, dealer_index or {})

    score = BASE_SCORE
    positive_reasons: list[str] = []
    warning_reasons: list[str] = []

    if match_type == "exact_reference":
        score += EXACT_REFERENCE_BOOST
        positive_reasons.append(format_reason("Exact reference"))
    elif match_type == "alias":
        score += ALIAS_MATCH_BOOST
        positive_reasons.append(format_reason("Alias / nickname match"))

    for points, label in (
        score_dial_attribute(request, offer_watch),
        score_bracelet_attribute(request, offer_watch),
        score_condition_attribute(request, offer, offer_watch),
        score_full_set_attribute(request, offer_watch, offer),
        score_production_year_attribute(request, offer_watch, offer),
    ):
        score += points
        if label:
            positive_reasons.append(format_reason(label))

    if budget_usd is not None and offer_usd is not None:
        if spread_usd is not None and spread_usd > 0:
            score += BUDGET_ABOVE_ASK_BOOST
            positive_reasons.append(format_reason("Budget above ask price"))
        elif spread_usd == 0:
            score += BUDGET_NEAR_ASK_BOOST
            positive_reasons.append(format_reason("Ask price matches budget"))
        elif spread_usd is not None and offer_usd >= int(budget_usd * (1 - BUDGET_NEAR_THRESHOLD)):
            score += BUDGET_NEAR_ASK_BOOST
            positive_reasons.append(format_reason("Ask price within budget"))
        else:
            score -= BUDGET_ABOVE_ASK_PENALTY
            warning_reasons.append(format_warning("Ask price above budget"))
    else:
        score -= MISSING_BUDGET_PENALTY
        warning_reasons.append(format_warning("Budget missing"))

    offer_recency = _recency_boost(message.get("received_at"), now=current)
    if offer_recency:
        score += offer_recency

    request_recency = _recency_boost(import_log.get("import_time"), now=current)
    if request_recency:
        score += request_recency

    offer_age_reason = format_offer_age_reason(message.get("received_at"), now=current)
    if offer_age_reason:
        positive_reasons.append(offer_age_reason)

    dealer_points, dealer_label = score_dealer_quality(dealer_id, dealer_index or {})
    score += dealer_points
    if dealer_label:
        positive_reasons.append(format_reason("Trusted dealer"))

    dial_match = compare_text_attribute(
        request.get("dial"),
        offer_watch.get("dial"),
        normalizer=normalize_dial,
    )
    if dial_match == MATCH_DIFFERENT:
        warning_reasons.append(format_warning("Different dial"))

    opportunity_score = max(0, min(100, score))
    score_label = score_label_for_score(opportunity_score)
    health, health_badge_class = health_for_score(opportunity_score)
    urgency = calculate_urgency(
        offer_received_at=message.get("received_at"),
        request_import_time=import_log.get("import_time"),
        opportunity_score=opportunity_score,
        now=current,
    )
    recommendation = normalize_recommendation(recommend_action(opportunity_score, urgency))
    reasons = positive_reasons + warning_reasons

    return {
        "opportunity_score": opportunity_score,
        "score_label": score_label,
        "confidence_label": score_label,
        "confidence_badge_class": confidence_badge_class(score_label),
        "health": health,
        "health_badge_class": health_badge_class,
        "data_quality_confidence_pct": data_quality["data_quality_confidence_pct"],
        "data_quality_confidence_reason": data_quality["data_quality_confidence_reason"],
        "urgency": urgency,
        "urgency_badge_class": urgency_badge_class(urgency),
        "potential_spread_usd": spread_usd,
        "budget_known": profit_display["budget_known"],
        "dealer_rating": rating_label,
        "dealer_rating_badge_class": rating_class,
        "positive_reasons": positive_reasons,
        "warning_reasons": warning_reasons,
        "reasons": reasons,
        "recommended_action": recommendation,
        "recommendation": recommendation,
        "recommendation_badge_class": recommendation_badge_class(recommendation),
        "match_type": match_type,
        **profit_display,
    }


def build_opportunity_analysis(scored_rows: list[Record]) -> Record:
    """Build the read-only Opportunity Analysis summary for the detail page."""
    if not scored_rows:
        empty = {
            "has_opportunities": False,
            "empty_message": "No opportunity found yet.",
            "ai_advisor_summary": generate_trading_advisor_summary({"has_opportunities": False}),
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
        }
        return empty

    best_match = scored_rows[0]
    score_card = build_score_card(best_match)
    analysis = {
        "has_opportunities": True,
        "empty_message": None,
        "opportunity_score": best_match.get("opportunity_score"),
        "score_label": best_match.get("score_label"),
        "confidence_label": best_match.get("score_label"),
        "confidence_badge_class": best_match.get("confidence_badge_class"),
        "health": best_match.get("health"),
        "health_badge_class": best_match.get("health_badge_class"),
        "data_quality_confidence_pct": best_match.get("data_quality_confidence_pct"),
        "data_quality_confidence_reason": best_match.get("data_quality_confidence_reason"),
        "urgency": best_match.get("urgency"),
        "urgency_badge_class": best_match.get("urgency_badge_class"),
        "potential_spread": best_match.get("potential_spread"),
        "potential_profit": best_match.get("potential_profit"),
        "potential_profit_title": best_match.get("potential_profit_title"),
        "potential_profit_value": best_match.get("potential_profit_value"),
        "potential_profit_subtitle": best_match.get("potential_profit_subtitle"),
        "budget_known": best_match.get("budget_known"),
        "positive_reasons": list(best_match.get("positive_reasons") or []),
        "warning_reasons": list(best_match.get("warning_reasons") or []),
        "reasons": list(best_match.get("reasons") or []),
        "recommended_action": best_match.get("recommendation"),
        "recommendation": best_match.get("recommendation"),
        "recommendation_badge_class": best_match.get("recommendation_badge_class"),
        "score_card": score_card,
        "best_match": {
            "dealer_name": best_match.get("dealer_name"),
            "asking_price": best_match.get("asking_price"),
            "offer_url": best_match.get("offer_url"),
            "dealer_rating": best_match.get("dealer_rating"),
            "dealer_rating_badge_class": best_match.get("dealer_rating_badge_class"),
            "opportunity_score": best_match.get("opportunity_score"),
            "score_label": best_match.get("score_label"),
            "health": best_match.get("health"),
            "potential_profit": best_match.get("potential_profit"),
            "urgency": best_match.get("urgency"),
            "recommendation": best_match.get("recommendation"),
        },
    }
    analysis["ai_advisor_summary"] = generate_trading_advisor_summary(analysis)
    return analysis


def build_market_request_opportunity_bundle(
    user: Record | None,
    import_log: Record,
    *,
    offers: list[Record] | None = None,
    now: datetime | None = None,
) -> tuple[list[Record], Record]:
    """Return scored matching offer rows and the Opportunity Analysis summary."""
    from database import list_active_offers_for_market_matching

    criteria = extract_market_request_criteria(import_log)
    candidate_offers = offers if offers is not None else list_active_offers_for_market_matching()
    visible_offers = filter_matching_offers_for_user(candidate_offers, user)
    dealer_index = build_dealer_quality_index(candidate_offers)

    scored_rows: list[Record] = []
    for offer in visible_offers:
        match_type = classify_market_request_match(criteria, offer)
        if match_type is None:
            continue
        row = build_market_request_matching_row(offer)
        row.update(
            score_market_request_opportunity(
                import_log,
                offer,
                match_type=match_type,
                dealer_index=dealer_index,
                now=now,
            )
        )
        row["score_card"] = build_score_card(row)
        scored_rows.append(row)

    scored_rows = sort_opportunity_rows(scored_rows)
    return scored_rows, build_opportunity_analysis(scored_rows)
