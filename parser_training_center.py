"""Offer-centric Parser Training Center — container list and row pages."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from activity_feed import format_dealer_label
from parser_review import ISSUE_LABELS

logger = logging.getLogger(__name__)

Record = dict[str, Any]

PARSER_TRAINING_PAGE_SIZE = 25
PARSER_TRAINING_MAX_SCANNED_IMPORTS = 400
PARSER_TRAINING_OVERFETCH_MULTIPLIER = 3

PARSER_TRAINING_FILTERS: dict[str, str] = {
    "all": "All",
    "today": "Today",
    "last_7_days": "Last 7 days",
    "pending": "Pending only",
}

TRAINING_STATUS_LABELS: dict[str, str] = {
    "pending_review": "Pending review",
    "approved": "Approved",
    "valid": "Approved",
    "corrected": "Corrected",
    "ignored": "Ignored",
    "failed": "Failed",
}

TRAINING_STATUS_BADGES: dict[str, str] = {
    "pending_review": "warning",
    "approved": "success",
    "corrected": "info",
    "ignored": "secondary",
    "failed": "danger",
}


def _issue_labels(issue_types: list[str] | None) -> list[str]:
    return [ISSUE_LABELS.get(issue, issue.replace("_", " ").title()) for issue in (issue_types or [])]


def format_training_row_display(row: Record) -> Record:
    """Format a parser_training_rows DB record for the UI template."""
    status = str(row.get("status") or "pending_review")
    suggestions = (row.get("parser_explanation") or {}).get("suggestions") or {}
    optional_notes = list((row.get("parser_explanation") or {}).get("optional_notes") or [])
    field_details = list((row.get("parser_explanation") or {}).get("field_details") or [])
    return {
        "id": row.get("id"),
        "row_index": int(row.get("row_index") or 0),
        "source_line": row.get("raw_row_text") or "",
        "brand": row.get("normalized_brand") or row.get("detected_brand"),
        "reference": row.get("normalized_reference") or row.get("detected_reference"),
        "condition": row.get("normalized_condition") or row.get("detected_condition"),
        "production_year": row.get("detected_year"),
        "card_date": row.get("detected_card_date"),
        "original_price": row.get("detected_price"),
        "original_currency": row.get("detected_currency"),
        "usd_price": row.get("usd_price"),
        "overall_confidence": row.get("confidence_overall"),
        "field_confidences": {
            "brand_confidence": row.get("confidence_brand"),
            "reference_confidence": row.get("confidence_reference"),
            "condition_confidence": row.get("confidence_condition"),
            "price_confidence": row.get("confidence_price"),
            "intent_confidence": row.get("confidence_intent"),
        },
        "field_explanations": row.get("parser_explanation") or {},
        "field_details": field_details,
        "optional_notes": optional_notes,
        "suggestions": suggestions,
        "issues": _issue_labels(row.get("issue_types")),
        "issue_types": list(row.get("issue_types") or []),
        "status": status,
        "status_label": TRAINING_STATUS_LABELS.get(status, status),
        "status_badge": TRAINING_STATUS_BADGES.get(status, "secondary"),
        "needs_review": status == "pending_review",
        "ignored": status == "ignored",
        "approved": status in {"approved", "valid", "corrected"},
        "created_offer_id": row.get("created_offer_id"),
        "failure_label": _issue_labels(row.get("issue_types"))[0] if row.get("issue_types") else "",
    }


def import_log_has_offer_rows(import_log: Record) -> bool:
    """Return True when an import has parsed offer rows in summary or counters."""
    summary = import_log.get("summary") or {}
    watches = list(summary.get("offer_watches") or summary.get("parsed_watches") or [])
    if watches:
        return True
    rows = summary.get("rows") or []
    if rows:
        return True
    return int(import_log.get("watches_parsed") or 0) > 0


def _summary_row_count(import_log: Record) -> int:
    summary = import_log.get("summary") or {}
    watches = list(summary.get("offer_watches") or summary.get("parsed_watches") or [])
    if watches:
        return len(watches)
    rows = summary.get("rows") or []
    if rows:
        return len(rows)
    return int(import_log.get("watches_parsed") or 0)


def _training_row_status_counts(rows: list[Record]) -> dict[str, int]:
    counts = {
        "pending_review": 0,
        "approved": 0,
        "corrected": 0,
        "ignored": 0,
        "failed": 0,
    }
    for row in rows:
        status = str(row.get("status") or "pending_review")
        if status in counts:
            counts[status] += 1
    return counts


def trace_parser_training_import(
    import_log_id: str,
    *,
    user: Record | None = None,
    dealer_lookup: dict[str, Record] | None = None,
) -> Record:
    """Debug one import's path from Activity into Parser Training Center."""
    from contact_classification import contact_type_for_import_log, is_business_import_log
    from database import (
        attach_import_log_summaries,
        get_import_log,
        list_parser_training_rows_for_import,
        parser_training_rows_schema_status,
        parser_training_rows_supported,
    )
    from import_status import filter_discarded_import_logs, is_discarded_no_watch_import
    from user_visibility import can_view_import

    schema = parser_training_rows_schema_status()
    import_log = get_import_log(import_log_id)
    trace: Record = {
        "import_log_id": import_log_id,
        "import_log_found": import_log is not None,
        "summary_row_count": 0,
        "parser_training_rows_count": 0,
        "parser_training_row_statuses": {},
        "parser_training_rows_supported": parser_training_rows_supported(),
        "parser_training_schema_status": schema.get("status"),
        "parser_training_schema_message": schema.get("message"),
        "sync_called_on_ingest": None,
        "visible_in_training_center": False,
        "hidden_reason": "",
    }

    if import_log is None:
        trace["hidden_reason"] = "import_log_not_found"
        return trace

    import_log = attach_import_log_summaries([import_log])[0]
    trace["summary_row_count"] = _summary_row_count(import_log)
    trace["import_status"] = import_log.get("status")
    trace["watches_parsed"] = import_log.get("watches_parsed")
    trace["has_offer_rows_in_summary"] = import_log_has_offer_rows(import_log)

    training_rows = list_parser_training_rows_for_import(import_log_id)
    trace["parser_training_rows_count"] = len(training_rows)
    trace["parser_training_row_statuses"] = _training_row_status_counts(training_rows)

    hidden_reasons: list[str] = []
    if is_discarded_no_watch_import(import_log):
        hidden_reasons.append("discarded_no_watch_import")
    if import_log not in filter_discarded_import_logs([import_log]):
        hidden_reasons.append("discarded_import")

    if user is not None and not can_view_import(user, import_log):
        hidden_reasons.append("user_visibility_denied")

    if dealer_lookup is not None and not is_business_import_log(import_log, dealer_lookup):
        hidden_reasons.append("non_business_contact")
    elif dealer_lookup is not None:
        trace["contact_type"] = contact_type_for_import_log(import_log, dealer_lookup)

    if not import_log_has_offer_rows(import_log):
        hidden_reasons.append("no_offer_rows_in_summary")

    if schema["status"] == "missing":
        hidden_reasons.append("migration_not_applied")
    elif schema["status"] == "schema_cache_stale":
        hidden_reasons.append("schema_cache_stale")

    if trace["parser_training_rows_count"] == 0 and schema["status"] == "supported":
        hidden_reasons.append("parser_training_rows_not_synced")

    if hidden_reasons:
        trace["hidden_reason"] = ", ".join(hidden_reasons)
        trace["visible_in_training_center"] = (
            trace["import_log_found"]
            and trace["has_offer_rows_in_summary"]
            and "user_visibility_denied" not in hidden_reasons
            and "discarded_import" not in hidden_reasons
            and "discarded_no_watch_import" not in hidden_reasons
            and "non_business_contact" not in hidden_reasons
        )
    else:
        trace["visible_in_training_center"] = True

    return trace


def parse_parser_training_page(page_value: str | None) -> int:
    """Return a one-based page number from the query string."""
    if not page_value:
        return 1
    try:
        parsed = int(page_value)
    except ValueError:
        return 1
    return max(parsed, 1)


def parse_parser_training_filter(filter_value: str | None) -> str:
    """Normalize the overview filter query parameter."""
    if filter_value in PARSER_TRAINING_FILTERS:
        return filter_value
    return "all"


def parser_training_page_url(page: int, filter_name: str = "all") -> str:
    """Build a parser-training overview URL preserving pagination and filter."""
    params: dict[str, str] = {}
    if filter_name and filter_name != "all":
        params["filter"] = filter_name
    if page > 1:
        params["page"] = str(page)
    if not params:
        return "/parser-training"
    return f"/parser-training?{urlencode(params)}"


def _since_iso_for_filter(filter_name: str) -> str | None:
    now = datetime.now(timezone.utc)
    if filter_name == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat()
    if filter_name == "last_7_days":
        return (now - timedelta(days=7)).isoformat()
    return None


def _parser_training_scan_budget(page: int, *, page_size: int = PARSER_TRAINING_PAGE_SIZE) -> int:
    safe_page = max(page, 1)
    needed_rows = safe_page * page_size
    return min(
        PARSER_TRAINING_MAX_SCANNED_IMPORTS,
        needed_rows * PARSER_TRAINING_OVERFETCH_MULTIPLIER,
    )


def _visible_parser_training_import_logs(
    import_logs: list[Record],
    user: Record | None,
) -> list[Record]:
    from contact_classification import build_dealer_lookup_by_whatsapp, filter_business_import_logs
    from database import attach_import_log_summaries, list_contacts_for_import_lookup
    from import_status import filter_discarded_import_logs
    from user_visibility import filter_imports_for_user

    import_logs = attach_import_log_summaries(import_logs)
    visible = filter_discarded_import_logs(filter_imports_for_user(import_logs, user))
    lookup = build_dealer_lookup_by_whatsapp(list_contacts_for_import_lookup())
    business = filter_business_import_logs(visible, lookup)
    return [
        import_log
        for import_log in business
        if import_log_has_offer_rows(import_log)
    ]


@dataclass(frozen=True)
class ParserTrainingPageResult:
    containers: list[Record]
    totals: dict[str, int]
    page: int
    page_size: int
    has_previous: bool
    has_next: bool
    showing_from: int
    showing_to: int
    filter_name: str
    timing_ms: dict[str, float] = field(default_factory=dict)


def load_parser_training_overview_page(
    user: Record | None,
    *,
    page: int = 1,
    filter_name: str = "all",
    page_size: int = PARSER_TRAINING_PAGE_SIZE,
    format_timestamp,
) -> ParserTrainingPageResult:
    """Load one paginated parser-training overview page (bounded import + row queries)."""
    from database import list_parser_training_import_logs

    safe_page = max(page, 1)
    safe_filter = parse_parser_training_filter(filter_name)
    skip = (safe_page - 1) * page_size
    since_iso = _since_iso_for_filter(safe_filter)
    scan_budget = _parser_training_scan_budget(safe_page, page_size=page_size)

    import_started = time.perf_counter()
    db_rows = list_parser_training_import_logs(
        since_iso=since_iso,
        offset=0,
        limit=scan_budget,
    )
    visible_imports = _visible_parser_training_import_logs(db_rows, user)
    import_query_ms = (time.perf_counter() - import_started) * 1000

    page_imports = visible_imports
    if safe_filter == "pending":
        page_imports = _filter_imports_with_pending_rows(visible_imports)

    page_slice = page_imports[skip : skip + page_size]
    has_next = len(page_imports) > skip + len(page_slice)
    if (
        not has_next
        and len(page_slice) == page_size
        and len(db_rows) >= scan_budget
    ):
        has_next = True

    row_count_started = time.perf_counter()
    containers, totals = build_parser_training_containers(
        page_slice,
        format_timestamp=format_timestamp,
    )
    row_count_query_ms = (time.perf_counter() - row_count_started) * 1000

    showing_from = skip + 1 if page_slice else 0
    showing_to = skip + len(page_slice)

    logger.info(
        "parser-training overview: import_query=%.1fms row_count_query=%.1fms page=%s filter=%s imports=%s",
        import_query_ms,
        row_count_query_ms,
        safe_page,
        safe_filter,
        len(page_slice),
    )

    return ParserTrainingPageResult(
        containers=containers,
        totals=totals,
        page=safe_page,
        page_size=page_size,
        has_previous=safe_page > 1,
        has_next=has_next,
        showing_from=showing_from,
        showing_to=showing_to,
        filter_name=safe_filter,
        timing_ms={
            "import_query": import_query_ms,
            "row_count_query": row_count_query_ms,
        },
    )


def _filter_imports_with_pending_rows(import_logs: list[Record]) -> list[Record]:
    from database import list_parser_training_import_summaries, parser_training_rows_supported

    if not parser_training_rows_supported() or not import_logs:
        return []

    import_ids = [str(item["id"]) for item in import_logs if item.get("id")]
    summaries_by_id: dict[str, Record] = {}
    chunk_size = PARSER_TRAINING_PAGE_SIZE
    for start in range(0, len(import_ids), chunk_size):
        chunk_ids = import_ids[start : start + chunk_size]
        for summary in list_parser_training_import_summaries(chunk_ids):
            import_id = str(summary.get("import_log_id") or "")
            if import_id:
                summaries_by_id[import_id] = summary

    pending_imports: list[Record] = []
    for import_log in import_logs:
        import_id = str(import_log.get("id") or "")
        summary = summaries_by_id.get(import_id)
        if summary and int(summary.get("pending_review_rows") or 0) > 0:
            pending_imports.append(import_log)
    return pending_imports


def build_parser_training_containers(
    import_logs: list[Record],
    *,
    format_timestamp,
) -> tuple[list[Record], dict[str, int]]:
    """Build container rows for a bounded import list (row counts for these imports only)."""
    from database import (
        list_parser_training_import_summaries,
        parser_training_rows_supported,
    )
    from parser_training_engine import empty_container_summary

    logs_by_id = {str(item["id"]): item for item in import_logs if item.get("id")}
    import_ids = list(logs_by_id.keys())

    summaries_by_id: dict[str, Record] = {}
    if parser_training_rows_supported() and import_ids:
        try:
            for summary in list_parser_training_import_summaries(import_ids):
                import_id = str(summary.get("import_log_id") or "")
                if import_id:
                    summaries_by_id[import_id] = summary
        except Exception as exc:
            logger.warning(
                "Parser training row summaries unavailable for %s import(s): %s",
                len(import_ids),
                exc,
            )

    containers: list[Record] = []
    totals = {
        "total_imports": 0,
        "total_rows": 0,
        "pending_review_rows": 0,
        "approved_rows": 0,
        "ignored_rows": 0,
        "failed_rows": 0,
    }

    for import_id in import_ids:
        import_log = logs_by_id.get(import_id)
        if import_log is None:
            continue
        summary = summaries_by_id.get(import_id) or empty_container_summary(import_id)

        container = {
            **summary,
            "import_log": import_log,
            "import_time": format_timestamp(import_log.get("import_time")),
            "dealer": format_dealer_label(import_log),
            "group_name": import_log.get("group_name") or "N/A",
            "rows_url": f"/parser-training/{import_id}/rows",
        }
        containers.append(container)
        totals["total_imports"] += 1
        totals["total_rows"] += int(summary.get("total_rows") or 0)
        totals["pending_review_rows"] += int(summary.get("pending_review_rows") or 0)
        totals["approved_rows"] += int(summary.get("approved_rows") or 0)
        totals["ignored_rows"] += int(summary.get("ignored_rows") or 0)
        totals["failed_rows"] += int(summary.get("failed_rows") or 0)

    containers.sort(
        key=lambda item: (
            -int(item.get("pending_review_rows") or 0),
            str((item.get("import_log") or {}).get("import_time") or ""),
        ),
    )
    return containers, totals


def load_parser_training_containers(
    import_logs: list[Record],
    *,
    format_timestamp,
    pending_only: bool = False,
) -> tuple[list[Record], dict[str, int]]:
    """Build import container rows for the Parser Training Center main page."""
    import_logs = [
        log for log in import_logs if import_log_has_offer_rows(log)
    ]
    if pending_only:
        import_logs = _filter_imports_with_pending_rows(import_logs)
    return build_parser_training_containers(import_logs, format_timestamp=format_timestamp)


def load_parser_training_rows_for_import(
    import_log: Record,
    *,
    message: Record | None = None,
) -> tuple[list[Record], Record]:
    """Return formatted training rows and matching container stats for one import."""
    from database import list_parser_training_rows_for_import, parser_training_rows_supported
    from dealer_list_training import build_dealer_list_training_rows
    from parser_training_engine import build_container_summary_for_import

    message_type = (import_log.get("summary") or {}).get("message_type")
    import_id = str(import_log.get("id") or "")

    if parser_training_rows_supported():
        rows = list_parser_training_rows_for_import(import_id)
        summary = build_container_summary_for_import(rows, import_log_id=import_id)
        return (
            [
                format_training_row_display(row)
                for row in sorted(rows, key=lambda item: int(item.get("row_index") or 0))
            ],
            summary,
        )

    legacy_rows = build_dealer_list_training_rows(import_log, message=message)
    formatted: list[Record] = []
    for legacy in legacy_rows:
        status = "ignored" if legacy.get("ignored") else (
            "approved" if legacy.get("approved") else (
                "pending_review" if legacy.get("needs_review") else "approved"
            )
        )
        formatted.append(
            {
                **legacy,
                "id": f"legacy-{legacy.get('line_index')}",
                "row_index": legacy.get("line_index"),
                "source_line": legacy.get("source_line"),
                "status": status,
                "status_label": TRAINING_STATUS_LABELS.get(status, status),
                "status_badge": TRAINING_STATUS_BADGES.get(status, "secondary"),
                "issues": [
                    ISSUE_LABELS.get(issue, issue) for issue in (legacy.get("issues") or [])
                ],
                "uses_fallback": True,
            }
        )
    from parser_training_engine import summarize_training_rows_by_status

    legacy_summary = summarize_training_rows_by_status(
        [{"status": row.get("status")} for row in formatted]
    )
    legacy_summary["import_log_id"] = import_id
    return formatted, legacy_summary


def unique_references_from_rows(rows: list[Record]) -> list[str]:
    """Collect unique references from selected training rows for brand mapping."""
    refs: list[str] = []
    seen: set[str] = set()
    for row in rows:
        ref = str(row.get("reference") or row.get("normalized_reference") or row.get("detected_reference") or "").strip().upper()
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return sorted(refs)
