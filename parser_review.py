"""Parser review center: filter, group, and format needs-review imports."""

from __future__ import annotations

from typing import Any

from activity_feed import format_dealer_label, message_preview
from import_classification import looks_like_parser_review_offer
from import_status import import_status_reason, normalize_import_status
from ingest import _watch_missing_fields, is_large_dealer_list_import_log
from unknown_brand_intelligence import extract_unknown_brand_text

Record = dict[str, Any]

PARSER_REVIEW_FILTERS = frozenset(
    {
        "all",
        "missing_price",
        "missing_brand",
        "missing_reference",
        "missing_condition",
        "unknown_brand",
        "unknown_model",
    }
)

ISSUE_LABELS: dict[str, str] = {
    "missing_price": "Missing price",
    "missing_brand": "Missing brand",
    "missing_reference": "Missing reference",
    "missing_condition": "Missing condition",
    "unknown_brand": "Unknown brand",
    "unknown_model": "Unknown model",
    "multiple_fields_missing": "Multiple fields missing",
}

MISSING_FIELD_LABELS: dict[str, str] = {
    "price": "Price",
    "brand": "Brand",
    "reference": "Reference",
    "condition": "Condition",
}

PARSED_FIELD_SPECS: list[tuple[str, str]] = [
    ("brand", "Brand"),
    ("reference", "Reference"),
    ("model", "Model"),
    ("dial", "Dial"),
    ("bracelet", "Bracelet"),
    ("condition", "Condition"),
    ("retail_price", "Retail price"),
    ("original_price", "Price"),
    ("original_currency", "Currency"),
    ("usd_price", "USD price"),
]


def is_parser_review_pending(import_log: Record) -> bool:
    """Return True when an import still belongs on the parser review queue."""
    if is_large_dealer_list_import_log(import_log):
        return False
    if normalize_import_status(import_log) != "warning":
        return False
    summary = import_log.get("summary") or {}
    if summary.get("parser_reviewed"):
        return False
    if summary.get("parser_review_ignored"):
        return False
    if not looks_like_parser_review_offer(import_log):
        return False
    return True


def filter_parser_review_imports(import_logs: list[Record]) -> list[Record]:
    """Return imports that still need parser review."""
    return [import_log for import_log in import_logs if is_parser_review_pending(import_log)]


def _parsed_watches(import_log: Record) -> list[Record]:
    summary = import_log.get("summary") or {}
    watches = summary.get("parsed_watches")
    if isinstance(watches, list) and watches:
        return watches
    rows = summary.get("rows")
    if isinstance(rows, list) and rows:
        return rows
    return []


def _has_unknown_model(watch: Record) -> bool:
    model_alias = watch.get("model_alias") or {}
    if model_alias.get("reference_status") == "Unknown":
        return True
    return bool(watch.get("brand") and not watch.get("model"))


def detect_watch_issues(watch: Record) -> tuple[set[str], list[str]]:
    """Return issue keys and missing field keys for one parsed watch."""
    issues: set[str] = set()
    missing = list(_watch_missing_fields(watch))

    if "price" in missing:
        issues.add("missing_price")
    if "brand" in missing:
        issues.add("missing_brand")
    if "reference" in missing:
        issues.add("missing_reference")
    if not watch.get("condition"):
        missing.append("condition")
        issues.add("missing_condition")

    if not watch.get("brand") and extract_unknown_brand_text(watch):
        issues.add("unknown_brand")

    if _has_unknown_model(watch):
        issues.add("unknown_model")

    basic_issues = {
        "missing_price",
        "missing_brand",
        "missing_reference",
        "missing_condition",
    }
    if len(issues & basic_issues) >= 2:
        issues.add("multiple_fields_missing")

    return issues, missing


def detect_import_issues(import_log: Record) -> tuple[set[str], list[str], str | None]:
    """Return grouped issue keys, missing fields, and unknown brand text."""
    all_issues: set[str] = set()
    missing_fields: set[str] = set()
    unknown_brand_text: str | None = None

    for watch in _parsed_watches(import_log):
        watch_issues, watch_missing = detect_watch_issues(watch)
        all_issues |= watch_issues
        missing_fields.update(watch_missing)
        if "unknown_brand" in watch_issues and not unknown_brand_text:
            unknown_brand_text = extract_unknown_brand_text(watch)

    return all_issues, sorted(missing_fields), unknown_brand_text


def parser_review_counts(import_logs: list[Record]) -> dict[str, int]:
    """Count parser review imports and common issue buckets."""
    pending = filter_parser_review_imports(import_logs)
    issue_index = _build_issue_index(pending)
    return _parser_review_counts_from_index(pending, issue_index)


def _build_issue_index(
    pending: list[Record],
) -> dict[str, tuple[set[str], list[str], str | None]]:
    """Return issue metadata keyed by import log id."""
    return {str(import_log["id"]): detect_import_issues(import_log) for import_log in pending}


def _parser_review_counts_from_index(
    pending: list[Record],
    issue_index: dict[str, tuple[set[str], list[str], str | None]],
) -> dict[str, int]:
    counts = {
        "total": len(pending),
        "missing_price": 0,
        "missing_brand": 0,
        "missing_reference": 0,
        "missing_condition": 0,
        "unknown_brand": 0,
    }
    for import_log in pending:
        issues, _, _ = issue_index[str(import_log["id"])]
        if "missing_price" in issues:
            counts["missing_price"] += 1
        if "missing_brand" in issues:
            counts["missing_brand"] += 1
        if "missing_reference" in issues:
            counts["missing_reference"] += 1
        if "missing_condition" in issues:
            counts["missing_condition"] += 1
        if "unknown_brand" in issues:
            counts["unknown_brand"] += 1
    return counts


def filter_parser_review_by_issue(
    import_logs: list[Record],
    filter_key: str,
) -> list[Record]:
    """Filter pending parser review imports by issue type."""
    pending = filter_parser_review_imports(import_logs)
    if filter_key == "all":
        return pending
    return [
        import_log
        for import_log in pending
        if filter_key in detect_import_issues(import_log)[0]
    ]


def _format_price_amount(amount: Any) -> str | None:
    """Format a price amount safely for display."""
    if amount is None:
        return None
    if isinstance(amount, str) and not amount.strip():
        return None
    if isinstance(amount, bool):
        return None
    if isinstance(amount, int):
        return f"{amount:,}"
    if isinstance(amount, float):
        if amount != amount:
            return None
        if amount.is_integer():
            return f"{int(amount):,}"
        formatted = f"{amount:,.2f}"
        return formatted.rstrip("0").rstrip(".")
    if isinstance(amount, str):
        cleaned = amount.strip()
        normalized = cleaned.replace(",", "").replace(" ", "")
        if normalized.replace(".", "", 1).isdigit():
            if "." in normalized:
                value = float(normalized)
                if value.is_integer():
                    return f"{int(value):,}"
                formatted = f"{value:,.2f}"
                return formatted.rstrip("0").rstrip(".")
            return f"{int(normalized):,}"
        return cleaned
    return str(amount)


def _format_parsed_value(field_key: str, watch: Record) -> str | None:
    if field_key in {"original_price", "retail_price"}:
        amount = watch.get(field_key)
        if field_key == "original_price" and amount is None:
            amount = watch.get("price")
        formatted = _format_price_amount(amount)
        if not formatted:
            return None
        currency_key = "retail_currency" if field_key == "retail_price" else "original_currency"
        currency = watch.get(currency_key) or watch.get("currency")
        if currency:
            return f"{formatted} {currency}"
        return formatted
    if field_key == "usd_price":
        formatted = _format_price_amount(watch.get("usd_price"))
        if not formatted:
            return None
        return f"${formatted}"
    value = watch.get(field_key)
    if value is None or value == "":
        return None
    return str(value)


def _parsed_field_entries(watches: list[Record]) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for watch in watches:
        for field_key, label in PARSED_FIELD_SPECS:
            formatted = _format_parsed_value(field_key, watch)
            if not formatted:
                continue
            display_label = label
            if field_key == "original_price" and watch.get("retail_price") is not None:
                display_label = "Offer price"
            entry = f"{display_label}: {formatted}"
            if entry in seen:
                continue
            seen.add(entry)
            entries.append(entry)
    return entries


def build_parser_review_row(
    import_log: Record,
    message: Record | None,
    *,
    format_timestamp,
    issue_data: tuple[set[str], list[str], str | None] | None = None,
) -> Record:
    """Format one import for the parser review page."""
    watches = _parsed_watches(import_log)
    if issue_data is None:
        issues, missing_fields, unknown_brand_text = detect_import_issues(import_log)
    else:
        issues, missing_fields, unknown_brand_text = issue_data
    raw_message = (message or {}).get("raw_text") or ""
    issue_labels = [ISSUE_LABELS[key] for key in ISSUE_LABELS if key in issues]

    return {
        "id": import_log["id"],
        "import_time": format_timestamp(import_log.get("import_time")),
        "dealer": format_dealer_label(import_log),
        "group_name": import_log.get("group_name") or "N/A",
        "original_message": raw_message or message_preview(raw_message),
        "message_preview": message_preview(raw_message, max_length=160),
        "status_reason": import_status_reason(import_log),
        "missing_fields": [
            MISSING_FIELD_LABELS[field]
            for field in missing_fields
            if field in MISSING_FIELD_LABELS
        ],
        "parsed_fields": _parsed_field_entries(watches),
        "issues": sorted(issues),
        "issue_labels": issue_labels,
        "detail_url": f"/activity/{import_log['id']}",
        "unknown_brand_text": unknown_brand_text,
        "has_unknown_brand": "unknown_brand" in issues,
    }


def load_parser_review_page_data(
    import_logs: list[Record],
    filter_key: str,
    *,
    format_timestamp,
) -> tuple[list[Record], dict[str, int]]:
    """Build parser review rows with one batched messages query."""
    from database import attach_import_log_summaries, get_messages_by_ids

    import_logs = attach_import_log_summaries(import_logs)
    pending = filter_parser_review_imports(import_logs)
    issue_index = _build_issue_index(pending)
    counts = _parser_review_counts_from_index(pending, issue_index)

    if filter_key == "all":
        filtered_logs = pending
    else:
        filtered_logs = [
            import_log
            for import_log in pending
            if filter_key in issue_index[str(import_log["id"])][0]
        ]

    message_ids = [
        str(import_log["message_id"])
        for import_log in filtered_logs
        if import_log.get("message_id")
    ]
    messages_by_id = get_messages_by_ids(list(dict.fromkeys(message_ids)))

    from parser_workbench import enrich_workbench_row

    rows: list[Record] = []
    for import_log in filtered_logs:
        import_id = str(import_log["id"])
        message = messages_by_id.get(str(import_log.get("message_id") or ""))
        row = build_parser_review_row(
            import_log,
            message,
            format_timestamp=format_timestamp,
            issue_data=issue_index[import_id],
        )
        rows.append(enrich_workbench_row(row, import_log, message=message))
    return rows, counts
