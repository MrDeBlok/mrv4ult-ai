"""Central parser quality metrics and threshold validation for imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Record = dict[str, Any]

BRAND_FIELD = "brand"
REFERENCE_FIELD = "reference"
CONDITION_FIELD = "condition"
PRICE_FIELD = "price"
CURRENCY_FIELD = "currency"

ESSENTIAL_PARSER_FIELDS: tuple[str, ...] = (
    BRAND_FIELD,
    REFERENCE_FIELD,
    CONDITION_FIELD,
    PRICE_FIELD,
    CURRENCY_FIELD,
)

FIELD_LABELS: dict[str, str] = {
    BRAND_FIELD: "Brand",
    REFERENCE_FIELD: "Reference",
    CONDITION_FIELD: "Condition",
    PRICE_FIELD: "Price",
    CURRENCY_FIELD: "Currency",
}

DEFAULT_PARSER_QUALITY_THRESHOLDS: dict[str, float] = {
    BRAND_FIELD: 98.0,
    REFERENCE_FIELD: 98.0,
    CONDITION_FIELD: 90.0,
    PRICE_FIELD: 98.0,
    CURRENCY_FIELD: 98.0,
}


@dataclass(frozen=True)
class ParserQualityReport:
    """Aggregated parser completeness for one import's offer watches."""

    total_offers: int
    parsed_counts: dict[str, int] = field(default_factory=dict)
    field_rates: dict[str, float] = field(default_factory=dict)
    overall_quality_pct: float = 0.0
    thresholds: dict[str, float] = field(default_factory=dict)
    failed_fields: tuple[str, ...] = ()
    meets_thresholds: bool = True
    issue_summary: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_offers": self.total_offers,
            "parsed_counts": dict(self.parsed_counts),
            "field_rates": {key: round(value, 2) for key, value in self.field_rates.items()},
            "overall_quality_pct": round(self.overall_quality_pct, 1),
            "thresholds": dict(self.thresholds),
            "failed_fields": list(self.failed_fields),
            "meets_thresholds": self.meets_thresholds,
            "issue_summary": list(self.issue_summary),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ParserQualityReport | None:
        if not isinstance(payload, dict):
            return None
        total_offers = int(payload.get("total_offers") or 0)
        if total_offers <= 0:
            return None
        return cls(
            total_offers=total_offers,
            parsed_counts={
                key: int((payload.get("parsed_counts") or {}).get(key) or 0)
                for key in ESSENTIAL_PARSER_FIELDS
            },
            field_rates={
                key: float((payload.get("field_rates") or {}).get(key) or 0.0)
                for key in ESSENTIAL_PARSER_FIELDS
            },
            overall_quality_pct=float(payload.get("overall_quality_pct") or 0.0),
            thresholds={
                key: float((payload.get("thresholds") or {}).get(key) or DEFAULT_PARSER_QUALITY_THRESHOLDS[key])
                for key in ESSENTIAL_PARSER_FIELDS
            },
            failed_fields=tuple(payload.get("failed_fields") or ()),
            meets_thresholds=bool(payload.get("meets_thresholds", True)),
            issue_summary=tuple(payload.get("issue_summary") or ()),
        )


def _has_brand(watch: Record) -> bool:
    brand = watch.get("brand")
    return isinstance(brand, str) and bool(brand.strip())


def _has_reference(watch: Record) -> bool:
    reference = watch.get("reference")
    return isinstance(reference, str) and bool(reference.strip())


def _has_price(watch: Record) -> bool:
    return (
        watch.get("original_price") is not None
        or watch.get("price") is not None
        or watch.get("usd_price") is not None
    )


def _has_currency(watch: Record) -> bool:
    if watch.get("usd_price") is not None:
        return True
    currency = watch.get("original_currency") or watch.get("currency")
    return isinstance(currency, str) and bool(currency.strip())


def _has_wear_condition(watch: Record) -> bool:
    from condition_normalizer import (
        CONDITION_SOURCE_INFERRED_DEFAULT,
        NEW_CONDITION,
        PRE_OWNED_CONDITION,
        resolve_offer_wear_condition,
    )

    if watch.get("condition_source") == CONDITION_SOURCE_INFERRED_DEFAULT:
        return False
    condition = resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition"))
    return condition in {NEW_CONDITION, PRE_OWNED_CONDITION}


def evaluate_watch_essential_fields(watch: Record) -> dict[str, bool]:
    """Return which essential parser fields are present on one offer watch."""
    return {
        BRAND_FIELD: _has_brand(watch),
        REFERENCE_FIELD: _has_reference(watch),
        CONDITION_FIELD: _has_wear_condition(watch),
        PRICE_FIELD: _has_price(watch),
        CURRENCY_FIELD: _has_currency(watch),
    }


def _quality_watches_from_summary(summary: Record) -> list[Record]:
    for key in ("offer_watches", "parsed_watches"):
        watches = summary.get(key)
        if isinstance(watches, list) and watches:
            return [watch for watch in watches if isinstance(watch, dict)]
    rows = summary.get("rows")
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, dict)]
    return []


def compute_parser_quality(
    watches: list[Record],
    *,
    thresholds: dict[str, float] | None = None,
) -> ParserQualityReport:
    """Compute parser quality metrics for a list of final parsed offer watches."""
    active_thresholds = dict(DEFAULT_PARSER_QUALITY_THRESHOLDS)
    if thresholds:
        active_thresholds.update(thresholds)

    total = len(watches)
    if total == 0:
        return ParserQualityReport(
            total_offers=0,
            parsed_counts={field: 0 for field in ESSENTIAL_PARSER_FIELDS},
            field_rates={field: 0.0 for field in ESSENTIAL_PARSER_FIELDS},
            overall_quality_pct=0.0,
            thresholds=active_thresholds,
            failed_fields=tuple(ESSENTIAL_PARSER_FIELDS),
            meets_thresholds=False,
            issue_summary=(),
        )

    parsed_counts = {field: 0 for field in ESSENTIAL_PARSER_FIELDS}
    for watch in watches:
        field_values = evaluate_watch_essential_fields(watch)
        for field_name, present in field_values.items():
            if present:
                parsed_counts[field_name] += 1

    field_rates = {
        field_name: (parsed_counts[field_name] / total) * 100.0
        for field_name in ESSENTIAL_PARSER_FIELDS
    }
    overall_quality_pct = sum(field_rates.values()) / len(ESSENTIAL_PARSER_FIELDS)

    failed_fields = tuple(
        field_name
        for field_name in ESSENTIAL_PARSER_FIELDS
        if field_rates[field_name] < active_thresholds[field_name]
    )
    issue_summary = build_parser_quality_issue_summary(total, parsed_counts)
    meets_thresholds = not failed_fields

    return ParserQualityReport(
        total_offers=total,
        parsed_counts=parsed_counts,
        field_rates=field_rates,
        overall_quality_pct=overall_quality_pct,
        thresholds=active_thresholds,
        failed_fields=failed_fields,
        meets_thresholds=meets_thresholds,
        issue_summary=issue_summary,
    )


def build_parser_quality_issue_summary(
    total_offers: int,
    parsed_counts: dict[str, int],
) -> tuple[str, ...]:
    """Build human-readable parser issue lines such as '114 offers missing condition'."""
    if total_offers <= 0:
        return ()

    issues: list[str] = []
    for field_name in ESSENTIAL_PARSER_FIELDS:
        missing = total_offers - parsed_counts.get(field_name, 0)
        if missing <= 0:
            continue
        label = FIELD_LABELS[field_name].lower()
        offer_word = "offer" if missing == 1 else "offers"
        issues.append(f"{missing} {offer_word} missing {label}")

    ambiguous_currency = total_offers - parsed_counts.get(CURRENCY_FIELD, 0)
    if ambiguous_currency > 0 and any(
        "missing currency" in issue for issue in issues
    ):
        for index, issue in enumerate(issues):
            if issue.endswith("missing currency"):
                issues[index] = issue.replace("missing currency", "missing or ambiguous currency")
                break

    return tuple(issues)


def parser_quality_status_reason(report: ParserQualityReport) -> str:
    """Return a status reason when parser quality thresholds fail."""
    if report.meets_thresholds or report.total_offers == 0:
        return ""

    failed_labels = [FIELD_LABELS[field].lower() for field in report.failed_fields]
    failed_text = ", ".join(failed_labels)
    summary = "; ".join(report.issue_summary[:3])
    reason = (
        f"Parser quality {report.overall_quality_pct:.0f}% is below threshold "
        f"({failed_text})."
    )
    if summary:
        reason += f" {summary}."
    return reason


def resolve_import_parser_quality(
    summary: Record,
    *,
    thresholds: dict[str, float] | None = None,
) -> ParserQualityReport:
    """Recalculate parser quality from stored import summary watches."""
    watches = _quality_watches_from_summary(summary)
    return compute_parser_quality(watches, thresholds=thresholds)


def build_parser_quality_display(report: ParserQualityReport) -> dict[str, Any]:
    """Format parser quality metrics for Import Detail and reporting."""
    field_rows = []
    for field_name in ESSENTIAL_PARSER_FIELDS:
        parsed = report.parsed_counts.get(field_name, 0)
        total = report.total_offers
        field_rows.append(
            {
                "key": field_name,
                "label": FIELD_LABELS[field_name],
                "parsed": parsed,
                "total": total,
                "display": f"{parsed}/{total}",
                "rate_pct": round(report.field_rates.get(field_name, 0.0), 1),
                "threshold_pct": report.thresholds.get(field_name, 0.0),
                "meets_threshold": field_name not in report.failed_fields,
            }
        )

    return {
        "total_offers": report.total_offers,
        "overall_quality_pct": round(report.overall_quality_pct, 1),
        "meets_thresholds": report.meets_thresholds,
        "failed_fields": list(report.failed_fields),
        "fields": field_rows,
        "issue_summary": list(report.issue_summary),
    }
