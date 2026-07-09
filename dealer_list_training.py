"""Dealer list row training: per-row review state, stats, and corrections."""

from __future__ import annotations

from typing import Any

from parser_confidence import attach_parser_confidence_metadata
from parser_review import detect_watch_issues, pick_primary_failure_reason, primary_failure_label
from parser_safety_gates import should_block_active_offer

Record = dict[str, Any]

ROW_REVIEW_FIELDS = (
    "brand",
    "reference",
    "condition",
    "production_year",
    "card_date",
    "price",
    "currency",
)


def row_needs_review(
    watch: Record,
    *,
    message_type: str | None = None,
    ignored_indexes: set[int] | None = None,
    line_index: int | None = None,
) -> bool:
    """Return True when one dealer-list row should stay in parser training."""
    if line_index is not None and ignored_indexes and line_index in ignored_indexes:
        return False
    if watch.get("parser_row_ignored"):
        return False
    if watch.get("parser_row_approved"):
        return False
    return should_block_active_offer(watch, message_type=message_type)


def count_rows_needing_review(
    watches: list[Record],
    summary: Record | None = None,
    *,
    message_type: str | None = None,
) -> int:
    ignored = set(get_ignored_row_indexes(summary or {}))
    approved = set(get_approved_row_indexes(summary or {}))
    count = 0
    for index, watch in enumerate(watches):
        if index in approved:
            continue
        if row_needs_review(
            watch,
            message_type=message_type,
            ignored_indexes=ignored,
            line_index=index,
        ):
            count += 1
    return count


def compute_dealer_list_stats(
    watches: list[Record],
    summary: Record | None = None,
    *,
    message_type: str | None = None,
) -> Record:
    """Return total/valid/needs-review counts for a dealer list import."""
    ignored = set(get_ignored_row_indexes(summary or {}))
    approved = set(get_approved_row_indexes(summary or {}))
    total = len(watches)
    needs_review = 0
    valid = 0
    ignored_count = 0

    for index, watch in enumerate(watches):
        if index in ignored or watch.get("parser_row_ignored"):
            ignored_count += 1
            continue
        if index in approved or watch.get("parser_row_approved"):
            valid += 1
            continue
        if row_needs_review(watch, message_type=message_type, ignored_indexes=ignored, line_index=index):
            needs_review += 1
        else:
            valid += 1

    return {
        "total_rows": total,
        "valid_rows": valid,
        "rows_needing_review": needs_review,
        "ignored_rows": ignored_count,
    }


def get_ignored_row_indexes(summary: Record) -> list[int]:
    raw = summary.get("ignored_row_indexes") or []
    return [int(value) for value in raw if str(value).isdigit()]


def get_approved_row_indexes(summary: Record) -> list[int]:
    raw = summary.get("approved_row_indexes") or []
    return [int(value) for value in raw if str(value).isdigit()]


def get_row_corrections(summary: Record) -> dict[str, Record]:
    corrections = summary.get("row_corrections") or {}
    if not isinstance(corrections, dict):
        return {}
    return {str(key): dict(value) for key, value in corrections.items() if isinstance(value, dict)}


def apply_row_corrections_to_watches(
    watches: list[Record],
    summary: Record,
) -> list[Record]:
    """Apply stored per-row corrections before reprocessing or display."""
    from parser_workbench import apply_row_field_overrides

    corrected: list[Record] = []
    row_corrections = get_row_corrections(summary)
    ignored = set(get_ignored_row_indexes(summary))
    approved = set(get_approved_row_indexes(summary))

    for index, watch in enumerate(watches):
        updated = dict(watch)
        overrides = row_corrections.get(str(index))
        if overrides:
            apply_row_field_overrides(updated, overrides)
        if index in ignored:
            updated["parser_row_ignored"] = True
        if index in approved:
            updated["parser_row_approved"] = True
        corrected.append(updated)
    return corrected


def build_dealer_list_training_rows(
    import_log: Record,
    *,
    message: Record | None = None,
) -> list[Record]:
    """Build per-row training table entries for a dealer list import."""
    from parser_review import _parsed_watches

    summary = import_log.get("summary") or {}
    watches = apply_row_corrections_to_watches(_parsed_watches(import_log), summary)
    message_type = summary.get("message_type")
    ignored = set(get_ignored_row_indexes(summary))
    approved = set(get_approved_row_indexes(summary))
    rows: list[Record] = []

    for index, watch in enumerate(watches):
        enriched = attach_parser_confidence_metadata(dict(watch), message_type=message_type)
        issues, _ = detect_watch_issues(enriched)
        failure_reason = pick_primary_failure_reason(
            {
                reason
                for reason in issues
                if reason in {
                    "missing_price",
                    "missing_reference",
                    "missing_brand",
                    "missing_condition",
                    "unknown_brand",
                    "unknown_reference",
                    "condition_needs_training",
                    "suspicious_price",
                    "brand_confidence_low",
                    "reference_confidence_low",
                }
            }
        )
        needs_review = row_needs_review(
            enriched,
            message_type=message_type,
            ignored_indexes=ignored,
            line_index=index,
        )
        rows.append(
            {
                "line_index": index,
                "source_line": enriched.get("source_line") or "",
                "brand": enriched.get("brand"),
                "reference": enriched.get("reference"),
                "condition": enriched.get("condition"),
                "production_year": enriched.get("production_year"),
                "card_date": enriched.get("card_date"),
                "original_price": enriched.get("original_price") or enriched.get("price"),
                "original_currency": enriched.get("original_currency") or enriched.get("currency"),
                "usd_price": enriched.get("usd_price"),
                "overall_confidence": enriched.get("overall_confidence"),
                "field_confidences": {
                    key: enriched.get(key)
                    for key in (
                        "brand_confidence",
                        "reference_confidence",
                        "price_confidence",
                        "condition_confidence",
                        "intent_confidence",
                    )
                },
                "field_explanations": enriched.get("field_explanations") or {},
                "issues": sorted(issues),
                "needs_review": needs_review,
                "ignored": index in ignored or enriched.get("parser_row_ignored"),
                "approved": index in approved or enriched.get("parser_row_approved"),
                "condition_needs_training": bool(enriched.get("condition_needs_training")),
                "condition_training_term": enriched.get("condition_training_term"),
                "failure_label": primary_failure_label(failure_reason) if needs_review else "Valid",
                "dealer_list_brand_header": enriched.get("dealer_list_brand_header"),
            }
        )
    return rows


def dealer_list_has_rows_needing_review(import_log: Record) -> bool:
    """Return True when a dealer list import still belongs in parser training."""
    from ingest import is_large_dealer_list_import_log
    from parser_review import _parsed_watches

    if not is_large_dealer_list_import_log(import_log):
        return False

    summary = import_log.get("summary") or {}
    if summary.get("parser_reviewed"):
        return False
    if summary.get("parser_review_ignored"):
        return False

    stats = summary.get("dealer_list_stats") or {}
    if stats.get("rows_needing_review", 0) > 0:
        return True

    watches = _parsed_watches(import_log)
    return count_rows_needing_review(
        watches,
        summary,
        message_type=summary.get("message_type"),
    ) > 0
