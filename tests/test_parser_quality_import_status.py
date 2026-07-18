"""Regression tests for parser quality validation and import status."""

from __future__ import annotations

from condition_normalizer import (
    CONDITION_SOURCE_EXPLICIT,
    CONDITION_SOURCE_INFERRED_DEFAULT,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
)
from app import build_activity_detail
from ingest import _import_status
from parser_quality import (
    build_parser_quality_display,
    build_parser_quality_issue_summary,
    compute_parser_quality,
    parser_quality_status_reason,
    resolve_import_parser_quality,
)


def _complete_watch(**overrides: object) -> dict:
    watch = {
        "brand": "Rolex",
        "reference": "126610LN",
        "condition": PRE_OWNED_CONDITION,
        "condition_source": CONDITION_SOURCE_EXPLICIT,
        "original_price": 125_000,
        "original_currency": "HKD",
        "usd_price": 16_000,
    }
    watch.update(overrides)
    return watch


class TestParserQualityCalculation:
    def test_complete_import_scores_one_hundred_percent(self) -> None:
        watches = [_complete_watch() for _ in range(10)]
        report = compute_parser_quality(watches)

        assert report.total_offers == 10
        assert report.overall_quality_pct == 100.0
        assert report.meets_thresholds is True
        assert report.issue_summary == ()

    def test_missing_condition_across_most_offers_fails_threshold(self) -> None:
        watches = [
            _complete_watch(
                condition=None,
                condition_source=None,
                raw_condition=None,
            )
            for _ in range(100)
        ]
        watches[0] = _complete_watch(condition=NEW_CONDITION, condition_source=CONDITION_SOURCE_EXPLICIT)

        report = compute_parser_quality(watches)

        assert report.parsed_counts["condition"] == 1
        assert report.field_rates["condition"] == 1.0
        assert report.meets_thresholds is False
        assert "condition" in report.failed_fields
        assert report.issue_summary[0] == "99 offers missing condition"

    def test_missing_references_fails_threshold(self) -> None:
        watches = [_complete_watch(reference=None) for _ in range(50)]

        report = compute_parser_quality(watches)

        assert report.parsed_counts["reference"] == 0
        assert report.meets_thresholds is False
        assert "reference" in report.failed_fields

    def test_inferred_default_condition_does_not_count_as_parsed(self) -> None:
        watches = [
            _complete_watch(
                condition=PRE_OWNED_CONDITION,
                condition_source=CONDITION_SOURCE_INFERRED_DEFAULT,
            )
            for _ in range(20)
        ]

        report = compute_parser_quality(watches)

        assert report.parsed_counts["condition"] == 0
        assert report.meets_thresholds is False

    def test_optional_field_gaps_do_not_affect_quality(self) -> None:
        watches = [
            _complete_watch(dial=None, bracelet=None, nickname=None, notes=None, production_year=None)
            for _ in range(5)
        ]

        report = compute_parser_quality(watches)

        assert report.meets_thresholds is True

    def test_overall_quality_percentage_is_average_of_field_rates(self) -> None:
        parsed_counts = {
            "brand": 126,
            "reference": 126,
            "condition": 12,
            "price": 126,
            "currency": 126,
        }
        report = compute_parser_quality(
            [
                _complete_watch(
                    condition=NEW_CONDITION if index < 12 else None,
                    condition_source=CONDITION_SOURCE_EXPLICIT if index < 12 else None,
                )
                for index in range(126)
            ]
        )

        assert report.parsed_counts["condition"] == 12
        assert round(report.overall_quality_pct, 1) == 81.9

    def test_issue_summary_generation(self) -> None:
        summary = build_parser_quality_issue_summary(
            126,
            {
                "brand": 126,
                "reference": 123,
                "condition": 12,
                "price": 126,
                "currency": 124,
            },
        )

        assert summary == (
            "3 offers missing reference",
            "114 offers missing condition",
            "2 offers missing or ambiguous currency",
        )


class TestImportStatusIntegration:
    def test_complete_import_receives_success(self) -> None:
        watches = [_complete_watch() for _ in range(5)]
        summary = {"watches_parsed": 5, "duplicate_offers": 0}
        report = compute_parser_quality(watches)

        status, reason = _import_status(summary, "success", watches, parser_quality=report)

        assert status == "success"
        assert "Successfully parsed 5 watch offer(s)." in reason

    def test_missing_condition_across_most_offers_receives_warning(self) -> None:
        watches = [
            _complete_watch(
                condition=PRE_OWNED_CONDITION,
                condition_source=CONDITION_SOURCE_INFERRED_DEFAULT,
            )
            for _ in range(126)
        ]
        summary = {"watches_parsed": 126, "duplicate_offers": 0}
        report = compute_parser_quality(watches)

        status, reason = _import_status(summary, "success", watches, parser_quality=report)

        assert status == "warning"
        assert "Parser quality" in reason
        assert "114 offers missing condition" not in reason
        assert "condition" in reason.lower()

    def test_missing_references_receives_warning(self) -> None:
        watches = [_complete_watch(reference=None) for _ in range(20)]
        summary = {"watches_parsed": 20, "duplicate_offers": 0}
        report = compute_parser_quality(watches)

        status, reason = _import_status(summary, "success", watches, parser_quality=report)

        assert status == "warning"
        assert "reference" in reason.lower() or "parser training" in reason.lower()

    def test_recalculates_quality_from_stored_summary(self) -> None:
        summary = {
            "offer_watches": [
                _complete_watch(reference=None),
                _complete_watch(reference=None),
            ]
        }

        report = resolve_import_parser_quality(summary)

        assert report.total_offers == 2
        assert report.parsed_counts["reference"] == 0


class TestImportDetailDisplay:
    def test_build_activity_detail_recalculates_quality_for_existing_import(self) -> None:
        import_log = {
            "id": "log-1",
            "status": "success",
            "watches_parsed": 2,
            "duplicate_offers": 0,
            "new_offers": 2,
            "matched_requests": 0,
            "import_time": "2026-07-17T12:00:00+00:00",
            "group_name": "HK",
            "dealer_whatsapp": "+85291234567",
            "summary": {
                "offer_watches": [
                    _complete_watch(reference=None),
                    _complete_watch(reference=None),
                ],
                "rows": [],
            },
        }

        detail = build_activity_detail(import_log, {"raw_text": "Dealer list"})

        assert detail["status"] == "Needs review"
        assert detail["parser_quality"]["total_offers"] == 2
        assert detail["parser_quality"]["fields"][1]["display"] == "0/2"
        assert detail["parser_quality"]["issue_summary"] == ["2 offers missing reference"]
