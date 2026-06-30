"""Trader-style attribute, dealer, urgency, and recommendation helpers for opportunity scoring."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from condition_normalizer import normalize_wear_condition
from dealer_intelligence import compute_dealer_stats, flatten_offer_intelligence_row
from request_matching import extract_offer_year, normalize_text
from search import _nested_record

Record = dict[str, Any]

MATCH_EXACT = "exact"
MATCH_UNKNOWN = "unknown"
MATCH_DIFFERENT = "different"

DIAL_EXACT = 12
DIAL_DIFFERENT = -15

BRACELET_EXACT = 8
BRACELET_DIFFERENT = -8

CONDITION_EXACT = 10
CONDITION_DIFFERENT = -12

FULL_SET_BOTH = 8
FULL_SET_OFFER_ONLY = 4
FULL_SET_DIFFERENT = -6

YEAR_SAME = 6
YEAR_WITHIN_TWO = 3
YEAR_LARGE_DIFF = -5

TRUSTED_DEALER_MIN_ACTIVE_OFFERS = 10
ESTABLISHED_DEALER_MIN_ACTIVE_OFFERS = 3
TRUSTED_DEALER_BONUS = 8

URGENCY_HOT = "HOT"
URGENCY_NORMAL = "NORMAL"
URGENCY_OLD = "OLD"

HOT_OFFER_MAX_AGE_HOURS = 1
HOT_MIN_SCORE = 85
OLD_OFFER_MIN_AGE_DAYS = 14
RECENT_REQUEST_MAX_AGE_DAYS = 7

BRACELET_ALIASES = {
    "jub": "jubilee",
    "jubilee": "jubilee",
    "oys": "oyster",
    "oyster": "oyster",
    "president": "president",
    "pres": "president",
    "bracelet": "bracelet",
    "rubber": "rubber",
    "leather": "leather",
}


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


def _normalize_token(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[\s_-]+", " ", str(value).strip().lower())
    return cleaned or None


def normalize_bracelet(value: str | None) -> str | None:
    token = _normalize_token(value)
    if token is None:
        return None
    return BRACELET_ALIASES.get(token, token)


def normalize_dial(value: str | None) -> str | None:
    return _normalize_token(value)


def normalize_condition(value: str | None) -> str | None:
    normalized, _ = normalize_wear_condition(value)
    return normalized


def compare_text_attribute(
    request_value: str | None,
    offer_value: str | None,
    *,
    normalizer=_normalize_token,
) -> str:
    """Return exact, unknown, or different for one watch attribute."""
    request_norm = normalizer(request_value)
    offer_norm = normalizer(offer_value)
    if request_norm is None:
        return MATCH_UNKNOWN
    if offer_norm is None:
        return MATCH_UNKNOWN
    if request_norm == offer_norm:
        return MATCH_EXACT
    return MATCH_DIFFERENT


def score_dial_attribute(request_watch: Record, offer_watch: Record) -> tuple[int, str | None]:
    match = compare_text_attribute(
        request_watch.get("dial"),
        offer_watch.get("dial"),
        normalizer=normalize_dial,
    )
    if match == MATCH_EXACT:
        return DIAL_EXACT, "Matching dial"
    if match == MATCH_DIFFERENT:
        return DIAL_DIFFERENT, "Different dial"
    return 0, None


def score_bracelet_attribute(request_watch: Record, offer_watch: Record) -> tuple[int, str | None]:
    match = compare_text_attribute(
        request_watch.get("bracelet"),
        offer_watch.get("bracelet"),
        normalizer=normalize_bracelet,
    )
    if match == MATCH_EXACT:
        return BRACELET_EXACT, "Matching bracelet"
    if match == MATCH_DIFFERENT:
        return BRACELET_DIFFERENT, "Different bracelet"
    return 0, None


def _resolve_condition_value(watch: Record, offer: Record) -> str | None:
    for source in (watch.get("condition"), offer.get("condition"), watch.get("raw_condition")):
        normalized = normalize_condition(source if isinstance(source, str) else None)
        if normalized:
            return normalized
    return None


def score_condition_attribute(request_watch: Record, offer: Record, offer_watch: Record) -> tuple[int, str | None]:
    request_condition = normalize_condition(request_watch.get("condition"))
    offer_condition = _resolve_condition_value(offer_watch, offer)
    if request_condition is None:
        return 0, None
    if offer_condition is None:
        return 0, None
    if request_condition == offer_condition:
        return CONDITION_EXACT, "Matching condition"
    return CONDITION_DIFFERENT, "Different condition"


def _full_set_state(watch: Record, offer: Record) -> bool | None:
    if watch.get("full_set") is True:
        return True
    if watch.get("full_set") is False:
        return False

    for source in (
        watch.get("condition"),
        watch.get("notes"),
        offer.get("condition"),
    ):
        if not isinstance(source, str):
            continue
        lowered = source.lower()
        if "full set" in lowered or lowered.strip() in {"complete", "fullset"}:
            return True
    return None


def score_full_set_attribute(
    request_watch: Record,
    offer_watch: Record,
    offer: Record,
) -> tuple[int, str | None]:
    request_state = _full_set_state(request_watch, {})
    offer_state = _full_set_state(offer_watch, offer)

    if request_state is True and offer_state is True:
        return FULL_SET_BOTH, "Full set"
    if request_state is None and offer_state is True:
        return FULL_SET_OFFER_ONLY, "Full set"
    if request_state is not None and offer_state is not None and request_state != offer_state:
        return FULL_SET_DIFFERENT, "Different completeness"
    return 0, None


def _production_year(watch: Record, offer: Record) -> int | None:
    year = watch.get("production_year")
    if isinstance(year, int):
        return year
    return extract_offer_year({**watch, **offer})


def score_production_year_attribute(
    request_watch: Record,
    offer_watch: Record,
    offer: Record,
) -> tuple[int, str | None]:
    request_year = _production_year(request_watch, {})
    offer_year = _production_year(offer_watch, offer)
    if request_year is None or offer_year is None:
        return 0, None

    difference = abs(int(request_year) - int(offer_year))
    if difference == 0:
        return YEAR_SAME, "Same production year"
    if difference <= 2:
        return YEAR_WITHIN_TWO, "Production year within 2 years"
    return YEAR_LARGE_DIFF, "Production year far apart"


def build_dealer_quality_index(offers: list[Record]) -> dict[str, Record]:
    """Aggregate in-memory offer rows per dealer for trusted-dealer scoring."""
    grouped: dict[str, list[Record]] = {}
    for offer in offers:
        dealer = _nested_record(offer.get("dealers"))
        dealer_id = str(dealer.get("id") or offer.get("dealer_id") or "")
        if not dealer_id:
            continue
        grouped.setdefault(dealer_id, []).append(flatten_offer_intelligence_row(offer))

    return {
        dealer_id: compute_dealer_stats(rows)
        for dealer_id, rows in grouped.items()
    }


def score_dealer_quality(dealer_id: str | None, dealer_index: dict[str, Record]) -> tuple[int, str | None]:
    if not dealer_id:
        return 0, None
    stats = dealer_index.get(str(dealer_id))
    if not stats:
        return 0, None
    if int(stats.get("active_offers") or 0) >= TRUSTED_DEALER_MIN_ACTIVE_OFFERS:
        return TRUSTED_DEALER_BONUS, "Trusted dealer"
    return 0, None


def format_reason(label: str) -> str:
    return f"✔ {label}"


def format_warning(label: str) -> str:
    return f"⚠ {label}"


def health_for_score(score: int) -> tuple[str, str]:
    if score >= 90:
        return "Excellent", "success"
    if score >= 75:
        return "Good", "primary"
    if score >= 50:
        return "Average", "warning"
    if score >= 25:
        return "Weak", "secondary"
    return "Critical", "danger"


def dealer_rating(dealer_id: str | None, dealer_index: dict[str, Record]) -> tuple[str, str]:
    if not dealer_id:
        return "Unknown Dealer", "secondary"
    stats = dealer_index.get(str(dealer_id))
    if not stats:
        return "Unknown Dealer", "secondary"
    active_offers = int(stats.get("active_offers") or 0)
    if active_offers >= TRUSTED_DEALER_MIN_ACTIVE_OFFERS:
        return "Trusted Dealer", "success"
    if active_offers >= ESTABLISHED_DEALER_MIN_ACTIVE_OFFERS:
        return "Established Dealer", "primary"
    if active_offers >= 1:
        return "New Dealer", "warning"
    return "Unknown Dealer", "secondary"


def recommendation_badge_class(recommendation: str | None) -> str:
    return {
        "BUY NOW": "success",
        "CALL TODAY": "danger",
        "CALL IMMEDIATELY": "danger",
        "GOOD OPPORTUNITY": "primary",
        "WATCH": "warning",
        "IGNORE": "secondary",
    }.get(recommendation or "", "secondary")


def normalize_recommendation(recommendation: str) -> str:
    if recommendation == "CALL IMMEDIATELY":
        return "CALL TODAY"
    return recommendation


def format_offer_age_reason(received_at: str | None, *, now: datetime) -> str | None:
    parsed = _parse_timestamp(received_at)
    if parsed is None:
        return None
    minutes = int((now - parsed).total_seconds() // 60)
    if minutes < 0:
        return None
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return format_reason(f"Offer posted {minutes} {unit} ago")
    hours = minutes // 60
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return format_reason(f"Offer posted {hours} {unit} ago")
    days = hours // 24
    unit = "day" if days == 1 else "days"
    return format_reason(f"Offer posted {days} {unit} ago")


def request_is_recent(import_time: str | None, *, now: datetime) -> bool:
    parsed = _parse_timestamp(import_time)
    if parsed is None:
        return False
    age_days = (now - parsed).total_seconds() / 86400
    return 0 <= age_days <= RECENT_REQUEST_MAX_AGE_DAYS


def calculate_urgency(
    *,
    offer_received_at: str | None,
    request_import_time: str | None,
    opportunity_score: int,
    now: datetime | None = None,
) -> str:
    current = _utc_now(now)
    offer_parsed = _parse_timestamp(offer_received_at)
    if offer_parsed is not None:
        offer_age_days = (current - offer_parsed).total_seconds() / 86400
        if offer_age_days > OLD_OFFER_MIN_AGE_DAYS:
            return URGENCY_OLD

        offer_age_hours = (current - offer_parsed).total_seconds() / 3600
        if (
            offer_age_hours <= HOT_OFFER_MAX_AGE_HOURS
            and request_is_recent(request_import_time, now=current)
            and opportunity_score > HOT_MIN_SCORE
        ):
            return URGENCY_HOT

    return URGENCY_NORMAL


def urgency_badge_class(urgency: str) -> str:
    return {
        URGENCY_HOT: "danger",
        URGENCY_NORMAL: "primary",
        URGENCY_OLD: "secondary",
    }.get(urgency, "primary")


def confidence_badge_class(confidence_label: str | None) -> str:
    return {
        "Excellent": "success",
        "Good": "primary",
        "Possible": "warning",
        "Low": "secondary",
    }.get(confidence_label or "", "secondary")


def recommend_action(opportunity_score: int, urgency: str) -> str:
    if urgency == URGENCY_OLD:
        if opportunity_score >= 75:
            return "WATCH"
        return "IGNORE"

    if urgency == URGENCY_HOT:
        if opportunity_score >= 90:
            return "BUY NOW"
        if opportunity_score >= 75:
            return "CALL TODAY"

    if opportunity_score >= 75:
        return "GOOD OPPORTUNITY"
    if opportunity_score >= 50:
        return "WATCH"
    return "IGNORE"


def request_has_full_set_info(watch: Record) -> bool:
    if _full_set_state(watch, {}) is not None:
        return True
    return bool(watch.get("card_date"))


def sort_opportunity_rows(rows: list[Record]) -> list[Record]:
    """Sort by score, urgency, profit, then newest offer."""
    urgency_rank = {URGENCY_HOT: 0, URGENCY_NORMAL: 1, URGENCY_OLD: 2}

    def _timestamp(row: Record) -> float:
        raw = row.get("_received_at_raw")
        if not raw:
            return 0.0
        parsed = _parse_timestamp(str(raw))
        return parsed.timestamp() if parsed else 0.0

    rows.sort(
        key=lambda row: (
            -int(row.get("opportunity_score") or 0),
            urgency_rank.get(str(row.get("urgency") or URGENCY_NORMAL), 1),
            -(row.get("potential_spread_usd") if row.get("potential_spread_usd") is not None else -1),
            -_timestamp(row),
        )
    )
    for row in rows:
        row.pop("_received_at_raw", None)
        row.pop("_usd_price_sort", None)
    return rows
