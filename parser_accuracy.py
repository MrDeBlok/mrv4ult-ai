"""AI Health metrics and parser review prioritization."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from activity_feed import IGNORED_ACTIVITY_STATUSES, is_parser_review_ignored, is_parser_reviewed
from import_classification import looks_like_parser_review_offer
from import_status import is_discarded_no_watch_import, normalize_import_status
from parser_review import (
    FAILURE_REASON_LABELS,
    detect_primary_failure_reason,
    filter_parser_review_imports,
    is_parser_review_pending,
    _parsed_watches,
)
from timezone_utils import UTC, parse_utc_timestamp

Record = dict[str, Any]

IMPORT_ACCURACY_SCAN_LIMIT = 400
HIGH_VALUE_USD_THRESHOLD = 50_000

HEALTH_SCORE_HEALTHY_MIN = 90.0
HEALTH_SCORE_ATTENTION_MIN = 75.0
NEEDS_REVIEW_HEALTHY_MAX = 10
NEEDS_REVIEW_ATTENTION_MAX = 50

HEALTH_BADGE_LABELS: dict[str, str] = {
    "healthy": "Healthy",
    "attention": "Needs Attention",
    "critical": "Critical",
}

TRAINING_QUEUE_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("unknown_brand", "Unknown Brand", ("unknown_brand",)),
    ("unknown_nickname", "Unknown Nickname", ("unknown_nickname",)),
    (
        "missing_reference",
        "Missing Reference",
        ("missing_reference", "multiple_possible_references", "unknown_reference"),
    ),
    ("missing_price", "Missing Price", ("missing_price", "missing_currency")),
    ("missing_condition", "Missing Condition", ("missing_condition",)),
    ("low_confidence", "Low Confidence", ("low_parser_confidence",)),
)

KNOWN_BRAND_PRIORITY: dict[str, int] = {
    "rolex": 0,
    "patek philippe": 1,
    "audemars piguet": 2,
    "richard mille": 3,
    "cartier": 4,
    "omega": 5,
    "tudor": 6,
    "vacheron constantin": 7,
    "a. lange & sohne": 8,
    "jaeger-lecoultre": 9,
    "hublot": 10,
    "panerai": 11,
    "iwc": 12,
    "breitling": 13,
}

TRACKED_BRAND_LABELS: tuple[str, ...] = (
    "Rolex",
    "Patek Philippe",
    "Audemars Piguet",
    "Richard Mille",
    "Cartier",
    "Omega",
    "Tudor",
)


def _import_summary(import_log: Record) -> dict[str, Any]:
    summary = import_log.get("summary")
    return summary if isinstance(summary, dict) else {}


def _import_timestamp(import_log: Record) -> datetime | None:
    return parse_utc_timestamp(import_log.get("import_time"))


def is_duplicate_import(import_log: Record) -> bool:
    """Return True when the WhatsApp message was already imported."""
    summary = _import_summary(import_log)
    if summary.get("already_processed"):
        return True
    return normalize_import_status(import_log) == "already_imported"


def is_discarded_import(import_log: Record) -> bool:
    """Return True when an import was discarded before business processing."""
    return is_discarded_no_watch_import(import_log)


def is_ignored_import(import_log: Record) -> bool:
    """Return True for dismissed parser issues and non-offer imports."""
    if is_discarded_import(import_log) or is_duplicate_import(import_log):
        return False
    if is_parser_review_ignored(import_log):
        return True
    return normalize_import_status(import_log) in IGNORED_ACTIVITY_STATUSES


def is_non_actionable_import(import_log: Record) -> bool:
    """Return True for technical/system imports that are not actionable watch offers."""
    if (
        is_discarded_import(import_log)
        or is_duplicate_import(import_log)
        or is_ignored_import(import_log)
    ):
        return False
    status = normalize_import_status(import_log)
    if status == "error":
        return True
    summary = _import_summary(import_log)
    if summary.get("message_type") == "unknown" and int(import_log.get("watches_parsed") or 0) == 0:
        return True
    return False


def is_actionable_watch_import(import_log: Record) -> bool:
    """Return True for actionable watch imports used in AI Health metrics."""
    if (
        is_discarded_import(import_log)
        or is_duplicate_import(import_log)
        or is_ignored_import(import_log)
        or is_non_actionable_import(import_log)
    ):
        return False

    status = normalize_import_status(import_log)
    if status == "success":
        return int(import_log.get("watches_parsed") or 0) > 0 or bool(_parsed_watches(import_log))
    if status == "warning":
        return looks_like_parser_review_offer(import_log)
    return False


def is_successfully_parsed_actionable(import_log: Record) -> bool:
    """Return True when an actionable import is fully handled."""
    if not is_actionable_watch_import(import_log):
        return False
    status = normalize_import_status(import_log)
    if status == "success":
        return True
    return status == "warning" and is_parser_reviewed(import_log)


def is_needs_review_actionable(import_log: Record) -> bool:
    """Return True when an actionable import still needs parser review."""
    return is_actionable_watch_import(import_log) and is_parser_review_pending(import_log)


def health_badge_level(metric: str, value: float | int) -> str:
    """Return healthy, attention, or critical for dashboard badges."""
    if metric == "health_score":
        if value >= HEALTH_SCORE_HEALTHY_MIN:
            return "healthy"
        if value >= HEALTH_SCORE_ATTENTION_MIN:
            return "attention"
        return "critical"

    if metric == "needs_review":
        if value <= NEEDS_REVIEW_HEALTHY_MAX:
            return "healthy"
        if value <= NEEDS_REVIEW_ATTENTION_MAX:
            return "attention"
        return "critical"

    raise ValueError(f"Unsupported health metric: {metric}")


def health_badge_payload(metric: str, value: float | int) -> dict[str, Any]:
    level = health_badge_level(metric, value)
    icons = {"healthy": "🟢", "attention": "🟡", "critical": "🔴"}
    return {
        "level": level,
        "icon": icons[level],
        "label": HEALTH_BADGE_LABELS[level],
    }


def _accuracy_pct(successfully_parsed: int, total_actionable: int) -> float:
    if total_actionable <= 0:
        return 100.0
    return round((successfully_parsed / total_actionable) * 100, 1)


def _actionable_metrics(import_logs: list[Record]) -> dict[str, int]:
    actionable_logs = [import_log for import_log in import_logs if is_actionable_watch_import(import_log)]
    successfully_parsed = sum(
        1 for import_log in actionable_logs if is_successfully_parsed_actionable(import_log)
    )
    needs_review = sum(1 for import_log in actionable_logs if is_needs_review_actionable(import_log))
    total_actionable = successfully_parsed + needs_review
    return {
        "total_actionable": total_actionable,
        "successfully_parsed": successfully_parsed,
        "needs_review": needs_review,
    }


def _processing_summary(import_logs: list[Record]) -> dict[str, Any]:
    successfully_parsed = 0
    needs_review = 0
    ignored = 0
    discarded = 0
    duplicates = 0

    for import_log in import_logs:
        if is_discarded_import(import_log):
            discarded += 1
            continue
        if is_duplicate_import(import_log):
            duplicates += 1
            continue
        if is_ignored_import(import_log) or is_non_actionable_import(import_log):
            ignored += 1
            continue
        if is_successfully_parsed_actionable(import_log):
            successfully_parsed += 1
            continue
        if is_needs_review_actionable(import_log):
            needs_review += 1

    total_actionable = successfully_parsed + needs_review
    parser_accuracy_pct = _accuracy_pct(successfully_parsed, total_actionable)
    return {
        "total_actionable": total_actionable,
        "successfully_parsed": successfully_parsed,
        "needs_review": needs_review,
        "ignored": ignored,
        "discarded": discarded,
        "duplicates": duplicates,
        "parser_accuracy_pct": parser_accuracy_pct,
        "total_scanned": len(import_logs),
    }


def _training_queue_rows(pending_logs: list[Record]) -> list[dict[str, Any]]:
    reason_counts = {group_key: 0 for group_key, _, _ in TRAINING_QUEUE_GROUPS}
    reason_to_group = {
        reason: group_key
        for group_key, _, reasons in TRAINING_QUEUE_GROUPS
        for reason in reasons
    }

    for import_log in pending_logs:
        reason = detect_primary_failure_reason(import_log)
        if reason is None:
            continue
        group_key = reason_to_group.get(reason)
        if group_key is not None:
            reason_counts[group_key] += 1

    rows: list[dict[str, Any]] = []
    for group_key, label, reasons in TRAINING_QUEUE_GROUPS:
        filter_key = reasons[0]
        rows.append(
            {
                "key": group_key,
                "label": label,
                "count": reason_counts[group_key],
                "filter": filter_key,
            }
        )
    return rows


def _import_primary_brand(import_log: Record) -> str | None:
    watches = _parsed_watches(import_log)
    for watch in watches:
        brand = str(watch.get("brand") or "").strip().lower()
        if brand:
            return brand
    summary = import_log.get("summary") or {}
    for row in summary.get("rows") or []:
        brand = str(row.get("brand") or "").strip().lower()
        if brand and brand not in {"n/a", "unknown"}:
            return brand
    return None


def _brand_accuracy_stats(import_logs: list[Record]) -> list[Record]:
    stats: list[Record] = []
    for label in TRACKED_BRAND_LABELS:
        key = label.lower()
        brand_logs = [
            import_log
            for import_log in import_logs
            if is_actionable_watch_import(import_log)
            and _import_primary_brand(import_log) == key
        ]
        if not brand_logs:
            continue
        metrics = _actionable_metrics(brand_logs)
        stats.append(
            {
                "brand": label,
                "total": metrics["total_actionable"],
                "fully_parsed": metrics["successfully_parsed"],
                "needs_review": metrics["needs_review"],
                "accuracy_pct": _accuracy_pct(
                    metrics["successfully_parsed"],
                    metrics["total_actionable"],
                ),
            }
        )
    return stats


def _known_brand_rank(import_log: Record) -> int:
    brand = _import_primary_brand(import_log)
    if not brand:
        return 99
    if brand in KNOWN_BRAND_PRIORITY:
        return KNOWN_BRAND_PRIORITY[brand]
    for key, rank in KNOWN_BRAND_PRIORITY.items():
        if brand.startswith(key) or key in brand:
            return rank
    return 50


def _import_max_usd_value(import_log: Record) -> int:
    values: list[int] = []
    for watch in _parsed_watches(import_log):
        usd = watch.get("usd_price")
        if isinstance(usd, int):
            values.append(usd)
        elif isinstance(usd, float):
            values.append(int(usd))
    return max(values) if values else 0


def _is_sold_order_import(import_log: Record) -> bool:
    summary = import_log.get("summary") or {}
    return summary.get("request_intent_kind") == "sold_order"


def _is_wtb_import(import_log: Record) -> bool:
    summary = import_log.get("summary") or {}
    if summary.get("import_classification") == "request_intent":
        return True
    if summary.get("message_type") == "request":
        return True
    return summary.get("request_intent_needs_review") is True


def parser_review_business_sort_key(import_log: Record) -> tuple[Any, ...]:
    """Sort needs-review imports by business value (highest first)."""
    timestamp = _import_timestamp(import_log)
    max_usd = _import_max_usd_value(import_log)
    high_value = max_usd >= HIGH_VALUE_USD_THRESHOLD

    return (
        0 if _is_sold_order_import(import_log) else 1,
        0 if _is_wtb_import(import_log) else 1,
        0 if high_value else 1,
        -max_usd,
        _known_brand_rank(import_log),
        -(timestamp.timestamp() if timestamp else 0.0),
        str(import_log.get("id") or ""),
    )


def sort_parser_review_imports(import_logs: list[Record]) -> list[Record]:
    """Return parser review imports sorted by business priority."""
    return sorted(import_logs, key=parser_review_business_sort_key)


def load_ai_health_dashboard(
    import_logs: list[Record],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build AI Health dashboard metrics from bounded import logs."""
    reference_time = now if now is not None else datetime.now(tz=UTC)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=UTC)

    today_start = reference_time.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = reference_time - timedelta(days=7)

    today_logs = [
        import_log
        for import_log in import_logs
        if is_actionable_watch_import(import_log)
        and (ts := _import_timestamp(import_log)) is not None
        and ts >= today_start
    ]
    week_logs = [
        import_log
        for import_log in import_logs
        if is_actionable_watch_import(import_log)
        and (ts := _import_timestamp(import_log)) is not None
        and ts >= week_start
    ]

    overall_metrics = _actionable_metrics(import_logs)
    processing_summary = _processing_summary(import_logs)
    pending = filter_parser_review_imports(import_logs)
    training_queue = _training_queue_rows(pending)
    health_score = processing_summary["parser_accuracy_pct"]
    needs_review = overall_metrics["needs_review"]

    unknown_brands_count = next(
        (row["count"] for row in training_queue if row["key"] == "unknown_brand"),
        0,
    )
    unknown_nicknames_count = next(
        (row["count"] for row in training_queue if row["key"] == "unknown_nickname"),
        0,
    )

    overall = {
        "total": overall_metrics["total_actionable"],
        "fully_parsed": overall_metrics["successfully_parsed"],
        "needs_review": needs_review,
        "accuracy_pct": health_score,
    }
    today_metrics = _actionable_metrics(today_logs)
    week_metrics = _actionable_metrics(week_logs)

    return {
        "scan_limit": IMPORT_ACCURACY_SCAN_LIMIT,
        "health_score": health_score,
        "health_score_badge": health_badge_payload("health_score", health_score),
        "messages_today": today_metrics["total_actionable"],
        "needs_review": needs_review,
        "needs_review_badge": health_badge_payload("needs_review", needs_review),
        "training_queue_total": needs_review,
        "unknown_brands_count": unknown_brands_count,
        "unknown_nicknames_count": unknown_nicknames_count,
        "processing_summary": processing_summary,
        "training_queue": training_queue,
        "failure_labels": FAILURE_REASON_LABELS,
        "brand_accuracy": _brand_accuracy_stats(import_logs),
        "overall": overall,
        "today": {
            "total": today_metrics["total_actionable"],
            "fully_parsed": today_metrics["successfully_parsed"],
            "needs_review": today_metrics["needs_review"],
            "accuracy_pct": _accuracy_pct(
                today_metrics["successfully_parsed"],
                today_metrics["total_actionable"],
            ),
        },
        "week": {
            "total": week_metrics["total_actionable"],
            "fully_parsed": week_metrics["successfully_parsed"],
            "needs_review": week_metrics["needs_review"],
            "accuracy_pct": _accuracy_pct(
                week_metrics["successfully_parsed"],
                week_metrics["total_actionable"],
            ),
        },
        "pending_total": needs_review,
    }


def load_parser_accuracy_dashboard(
    import_logs: list[Record],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias for AI Health dashboard metrics."""
    return load_ai_health_dashboard(import_logs, now=now)
