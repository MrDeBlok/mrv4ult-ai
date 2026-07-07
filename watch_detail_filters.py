"""Date filtering helpers for watch reference detail pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from timezone_utils import DISPLAY_TIMEZONE, ensure_utc_datetime, parse_utc_timestamp

Record = dict[str, Any]

WATCH_DETAIL_DATE_ALL = "all"
WATCH_DETAIL_DATE_TODAY = "today"
WATCH_DETAIL_DATE_7D = "7d"
WATCH_DETAIL_DATE_30D = "30d"
WATCH_DETAIL_DATE_CUSTOM = "custom"


@dataclass(frozen=True)
class WatchDetailDateRange:
    """UTC half-open interval [start, end) for filtering offer recency."""

    start: datetime | None
    end: datetime | None


def watch_detail_filter_now() -> datetime:
    """Return the current time in the dashboard display timezone."""
    return datetime.now(DISPLAY_TIMEZONE)


def parse_watch_detail_date_filter(
    date_value: str | None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """Parse and validate the watch detail date filter query value."""
    cleaned = (date_value or WATCH_DETAIL_DATE_ALL).strip().lower() or WATCH_DETAIL_DATE_ALL
    if cleaned == WATCH_DETAIL_DATE_ALL:
        return WATCH_DETAIL_DATE_ALL
    if cleaned in {WATCH_DETAIL_DATE_TODAY, WATCH_DETAIL_DATE_7D, WATCH_DETAIL_DATE_30D}:
        return cleaned
    if cleaned == WATCH_DETAIL_DATE_CUSTOM:
        if not _parse_filter_date(date_from) and not _parse_filter_date(date_to):
            raise ValueError("Custom date filter requires at least one of date_from or date_to.")
        return WATCH_DETAIL_DATE_CUSTOM
    raise ValueError("Invalid date filter. Use All, Today, 7d, 30d, or custom.")


def resolve_watch_detail_date_range(
    date_filter: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    now: datetime | None = None,
) -> WatchDetailDateRange | None:
    """Resolve a watch detail date filter into a UTC date range."""
    if date_filter in {"", WATCH_DETAIL_DATE_ALL}:
        return None

    current = now or watch_detail_filter_now()
    current_local = current.astimezone(DISPLAY_TIMEZONE)

    if date_filter == WATCH_DETAIL_DATE_TODAY:
        start_local = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        return WatchDetailDateRange(
            start=ensure_utc_datetime(start_local),
            end=ensure_utc_datetime(end_local),
        )

    if date_filter == WATCH_DETAIL_DATE_7D:
        return WatchDetailDateRange(
            start=ensure_utc_datetime(current - timedelta(days=7)),
            end=None,
        )

    if date_filter == WATCH_DETAIL_DATE_30D:
        return WatchDetailDateRange(
            start=ensure_utc_datetime(current - timedelta(days=30)),
            end=None,
        )

    if date_filter == WATCH_DETAIL_DATE_CUSTOM:
        start_local = _start_of_filter_day(_parse_filter_date(date_from))
        end_local = _start_of_next_filter_day(_parse_filter_date(date_to))
        return WatchDetailDateRange(
            start=ensure_utc_datetime(start_local) if start_local else None,
            end=ensure_utc_datetime(end_local) if end_local else None,
        )

    raise ValueError(f"Unsupported date filter: {date_filter}")


def offer_recency_timestamp(offer: Record) -> datetime | None:
    """Return the offer recency timestamp used for date filtering."""
    for field in ("recency_at", "received_at"):
        timestamp = parse_utc_timestamp(offer.get(field))
        if timestamp is not None:
            return timestamp
    return None


def offer_matches_watch_detail_date_filter(
    offer: Record,
    date_range: WatchDetailDateRange | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether an offer falls inside the active date filter."""
    if date_range is None:
        return True

    timestamp = offer_recency_timestamp(offer)
    if timestamp is None:
        return False

    if date_range.start is not None and timestamp < date_range.start:
        return False

    if date_range.end is not None:
        if timestamp >= date_range.end:
            return False
    else:
        current = ensure_utc_datetime(now or watch_detail_filter_now())
        if timestamp > current:
            return False

    return True


def enrich_watch_detail_offer_recency(
    offers: list[Record],
    *,
    import_logs_by_message_id: dict[str, Record],
    import_logs_by_id: dict[str, Record],
    import_logs_by_offer_id: dict[str, Record],
) -> list[Record]:
    """Attach a normalized recency timestamp to each offer row."""
    from dealer_intelligence import _resolve_import_log_for_offer

    enriched: list[Record] = []
    for offer in offers:
        row = dict(offer)
        received_at = parse_utc_timestamp(row.get("received_at"))
        if received_at is not None:
            row["recency_at"] = received_at.isoformat()
            enriched.append(row)
            continue

        import_log, _resolution_path = _resolve_import_log_for_offer(
            row,
            import_logs_by_message_id=import_logs_by_message_id,
            import_logs_by_id=import_logs_by_id,
            import_logs_by_offer_id=import_logs_by_offer_id,
        )
        import_time = parse_utc_timestamp((import_log or {}).get("import_time"))
        if import_time is not None:
            row["recency_at"] = import_time.isoformat()

        enriched.append(row)
    return enriched


def sort_key_watch_detail_offer(offer: Record) -> tuple[int, float, float]:
    """Sort offers by newest recency first, then lowest USD price."""
    recency = offer_recency_timestamp(offer)
    if recency is None:
        recency_rank = 1
        recency_sort = 0.0
    else:
        recency_rank = 0
        recency_sort = -recency.timestamp()

    usd_price = offer.get("usd_price")
    price_sort = float(usd_price) if usd_price is not None else float("inf")
    return recency_rank, recency_sort, price_sort


def _parse_filter_date(value: str | None) -> date | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError("Invalid date value. Use YYYY-MM-DD.") from exc


def _start_of_filter_day(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=DISPLAY_TIMEZONE)


def _start_of_next_filter_day(value: date | None) -> datetime | None:
    if value is None:
        return None
    start = _start_of_filter_day(value)
    if start is None:
        return None
    return start + timedelta(days=1)
