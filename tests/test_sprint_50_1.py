"""Tests for Sprint 50.1 — Review Logic & Confidence Cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from parser_confidence import compute_field_confidences, compute_training_overall_confidence
from parser_safety_gates import evaluate_offer_safety, should_block_active_offer
from parser_training_classification import collect_optional_notes
from parser_training_center import format_training_row_display
from parser_training_engine import build_training_row_payload, create_offer_for_training_row

IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MESSAGE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OFFER_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _ap_row_without_condition() -> dict:
    """Dealer list row: brand + reference + price, no condition."""
    return {
        "brand": "Audemars Piguet",
        "reference": "15210OR",
        "reference_high_confidence": True,
        "original_price": 170000,
        "original_currency": "HKD",
        "usd_price": 21700,
        "source_line": "15210or blue 01/2024 170000hkd",
        "dealer_list_line": True,
    }


def _low_brand_watch() -> dict:
    return {
        "brand": None,
        "unknown_brand_text": "mystery",
        "reference": "XXXX999",
        "original_price": 170000,
        "original_currency": "HKD",
        "usd_price": 21700,
        "source_line": "mystery XXXX999 170000hkd",
    }


def _missing_price_watch() -> dict:
    return {
        "brand": "Audemars Piguet",
        "reference": "15210OR",
        "reference_high_confidence": True,
        "source_line": "15210or blue 01/2024",
    }


class TestMissingConditionNotBlocking:
    def test_row_without_condition_is_approved(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_ap_row_without_condition(),
            message_type="offer",
        )

        assert payload["status"] == "approved"
        assert "missing_condition" not in payload["issue_types"]
        assert "condition_confidence_low" not in payload["issue_types"]
        assert "condition_needs_training" not in payload["issue_types"]

    def test_missing_condition_only_in_optional_notes(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_ap_row_without_condition(),
            message_type="offer",
        )

        optional_notes = payload["parser_explanation"].get("optional_notes") or []
        assert "Condition not provided" in optional_notes
        assert "Condition not provided" not in payload["issue_types"]

    def test_safety_gates_pass_without_condition(self) -> None:
        blocked, reasons = evaluate_offer_safety(
            _ap_row_without_condition(),
            message_type="offer",
        )

        assert blocked is False
        assert reasons == []

    def test_should_not_block_active_offer_without_condition(self) -> None:
        assert should_block_active_offer(_ap_row_without_condition(), message_type="offer") is False


class TestRequiredFieldBlocking:
    def test_missing_price_remains_pending_review(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_missing_price_watch(),
            message_type="offer",
        )

        assert payload["status"] == "pending_review"
        assert "missing_price" in payload["issue_types"]

    def test_low_brand_confidence_remains_pending_review(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_low_brand_watch(),
            message_type="offer",
        )

        assert payload["status"] == "pending_review"
        assert "brand_confidence_low" in payload["issue_types"] or "missing_brand" in payload["issue_types"]


class TestTrainingConfidence:
    def test_overall_confidence_ignores_missing_optional_condition(self) -> None:
        watch = _ap_row_without_condition()
        field_confidences = compute_field_confidences(watch, message_type="offer")
        training_overall = compute_training_overall_confidence(field_confidences)

        assert field_confidences["condition_confidence"] == 0
        assert training_overall >= 60

    def test_optional_condition_confidence_is_na_in_payload(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_ap_row_without_condition(),
            message_type="offer",
        )

        assert payload["confidence_condition"] is None
        assert payload["parser_explanation"]["condition_confidence"] == "N/A"


class TestTrainingRowDisplay:
    def test_format_shows_optional_notes_not_issues(self) -> None:
        payload = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_ap_row_without_condition(),
            message_type="offer",
        )
        display = format_training_row_display({**payload, "id": "row-1"})

        assert display["status"] == "approved"
        assert display["needs_review"] is False
        assert "Condition not provided" in display["optional_notes"]
        assert "Missing condition" not in display["issues"]


class TestApprovedRowCreatesOffer:
    @patch("database.link_offer_to_import_source")
    @patch("database.insert_offer")
    @patch("database.find_or_create_watch")
    def test_create_offer_when_required_fields_present(
        self,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        _mock_link: MagicMock,
    ) -> None:
        mock_find_watch.return_value = ({"id": "watch-1"}, False)
        mock_insert_offer.return_value = ({"id": OFFER_ID}, True)

        row = build_training_row_payload(
            import_log_id=IMPORT_LOG_ID,
            source_message_id=MESSAGE_ID,
            row_index=0,
            watch=_ap_row_without_condition(),
            message_type="offer",
        )

        offer_row, created = create_offer_for_training_row(
            row,
            import_log_id=IMPORT_LOG_ID,
            message_id=MESSAGE_ID,
            dealer_id="dealer-1",
            line_index=0,
        )

        assert created is True
        assert offer_row is not None
        assert offer_row["id"] == OFFER_ID
        mock_insert_offer.assert_called_once()


class TestOptionalNotesCollection:
    def test_collect_optional_notes_for_ap_row(self) -> None:
        notes = collect_optional_notes(_ap_row_without_condition())

        assert "Condition not provided" in notes
        assert "Card date not provided" in notes
