"""Regression tests for parser training Save Row final-offer workflow."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from condition_normalizer import NEW_CONDITION
from final_offer_payload import (
    build_final_offer_payload,
    original_parser_confidence,
    training_row_audit,
)
from parser_training_center import format_training_row_display
from parser_training_engine import (
    build_training_row_payload,
    correct_training_row,
    sync_import_log_summary_for_training_row,
    watch_from_training_row,
)

ROW_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
IMPORT_LOG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MESSAGE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OFFER_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _parsed_watch(**overrides) -> dict:
    base = {
        "brand": None,
        "reference": None,
        "condition": None,
        "production_year": None,
        "card_date": None,
        "original_price": None,
        "price": None,
        "original_currency": None,
        "currency": None,
        "usd_price": None,
        "source_line": "Rolex 126610LN New 2024 USD14500",
        "message_type": "offer",
    }
    base.update(overrides)
    return base


def _training_row_from_watch(watch: dict, **overrides) -> dict:
    payload = build_training_row_payload(
        import_log_id=IMPORT_LOG_ID,
        source_message_id=MESSAGE_ID,
        row_index=0,
        watch=watch,
        message_type="offer",
        created_offer_id=OFFER_ID,
    )
    payload["id"] = ROW_ID
    payload.update(overrides)
    if "confidence_overall" in overrides:
        payload["parser_explanation"]["audit"]["original_confidence_overall"] = overrides[
            "confidence_overall"
        ]
    return payload


def _import_log(summary_rows: list[dict] | None = None) -> dict:
    rows = summary_rows or [
        {
            "reference": "N/A",
            "brand": "N/A",
            "condition": None,
            "offer_id": OFFER_ID,
            "usd_price": None,
        }
    ]
    return {
        "id": IMPORT_LOG_ID,
        "message_id": MESSAGE_ID,
        "summary": {
            "message_type": "offer",
            "rows": deepcopy(rows),
            "offer_watches": [deepcopy(rows[0])],
        },
    }


class TestFinalOfferPayloadBuilder:
    def test_build_training_row_payload_stores_all_detected_fields(self) -> None:
        watch = _parsed_watch(
            brand="Rolex",
            reference="126610LN",
            condition=NEW_CONDITION,
            production_year=2024,
            original_price=14500,
            original_currency="USD",
            usd_price=14500,
        )
        row = _training_row_from_watch(watch)

        assert row["detected_brand"] == "Rolex"
        assert row["detected_reference"] == "126610LN"
        assert row["detected_condition"] == NEW_CONDITION
        assert row["detected_year"] == "2024"
        assert row["detected_price"] == 14500
        assert row["detected_currency"] == "USD"
        assert row["parser_explanation"]["audit"]["original_detected"]["brand"] == "Rolex"

    def test_manual_edits_override_parser_values(self) -> None:
        row = _training_row_from_watch(_parsed_watch())
        final_watch = build_final_offer_payload(
            row,
            {
                "brand": "Rolex",
                "reference": "126610LN",
                "condition": NEW_CONDITION,
                "year": "2024",
                "price": "14500",
                "currency": "USD",
            },
        )

        assert final_watch["brand"] == "Rolex"
        assert final_watch["reference"] == "126610LN"
        assert final_watch["condition"] == NEW_CONDITION
        assert final_watch["production_year"] == 2024
        assert final_watch["usd_price"] == 14500

    def test_untouched_parser_values_remain_preserved_in_detected_columns(self) -> None:
        row = _training_row_from_watch(_parsed_watch(brand="Tudor"))
        assert row["detected_brand"] == "Tudor"

        build_final_offer_payload(row, {"reference": "M2836C1A3-0002"})

        assert row["detected_brand"] == "Tudor"
        assert row.get("detected_reference") is None


class TestSaveRowWorkflow:
    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.get_offer_by_id")
    def test_save_row_updates_existing_offer_with_manual_values(
        self,
        mock_get_offer: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        mock_update_offer: MagicMock,
        mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(_parsed_watch(), confidence_overall=53)
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_get_offer.return_value = {
            "id": OFFER_ID,
            "dealer_id": "dealer-1",
            "watch_id": "watch-1",
            "message_id": MESSAGE_ID,
        }
        mock_update_offer.return_value = {"id": OFFER_ID, "usd_price": 14500}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}
        mock_sync_summary.return_value = _import_log()

        corrections = {
            "brand": "Rolex",
            "reference": "126610LN",
            "condition": NEW_CONDITION,
            "year": "2024",
            "price": "14500",
            "currency": "USD",
        }
        result = correct_training_row(ROW_ID, corrections)

        mock_update_offer.assert_called_once()
        offer_watch = mock_update_offer.call_args.kwargs["watch"]
        assert offer_watch["brand"] == "Rolex"
        assert offer_watch["reference"] == "126610LN"
        assert offer_watch["usd_price"] == 14500
        assert result["normalized_brand"] == "Rolex"
        assert result["status"] == "corrected"
        assert result["issue_types"] == []

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.get_offer_by_id")
    def test_original_parser_confidence_remains_for_audit(
        self,
        mock_get_offer: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        _mock_update_offer: MagicMock,
        _mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(_parsed_watch(), confidence_overall=53, confidence_reference=0)
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_get_offer.return_value = {"id": OFFER_ID, "dealer_id": "dealer-1", "message_id": MESSAGE_ID}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}

        result = correct_training_row(
            ROW_ID,
            {
                "brand": "Rolex",
                "reference": "126610LN",
                "condition": NEW_CONDITION,
                "year": "2024",
                "price": "14500",
                "currency": "USD",
            },
        )

        assert result["confidence_overall"] == 53
        assert result["confidence_reference"] == 0
        audit = result["parser_explanation"]["audit"]
        assert audit["original_confidence_overall"] == 53
        assert audit["reviewed_by_human"] is True
        assert original_parser_confidence(result) == 53

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.get_offer_by_id")
    def test_market_price_eligibility_uses_corrected_data_not_original_parser_confidence(
        self,
        mock_get_offer: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        _mock_update_offer: MagicMock,
        _mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(_parsed_watch(), confidence_overall=53)
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_get_offer.return_value = {"id": OFFER_ID, "dealer_id": "dealer-1", "message_id": MESSAGE_ID}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}

        result = correct_training_row(
            ROW_ID,
            {
                "brand": "Rolex",
                "reference": "126610LN",
                "condition": NEW_CONDITION,
                "year": "2024",
                "price": "14500",
                "currency": "USD",
            },
        )

        audit = result["parser_explanation"]["audit"]
        assert audit["market_price_confidence"] == 100
        assert audit["market_price_eligible"] is True

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.get_offer_by_id")
    def test_price_correction_recalculates_normalized_usd_price(
        self,
        mock_get_offer: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        mock_update_offer: MagicMock,
        _mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(
            _parsed_watch(
                brand="Rolex",
                reference="126610LN",
                condition=NEW_CONDITION,
                production_year=2024,
                original_price=2,
                original_currency="HKD",
                usd_price=0,
            ),
            issue_types=["suspicious_price"],
        )
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_get_offer.return_value = {"id": OFFER_ID, "dealer_id": "dealer-1", "message_id": MESSAGE_ID}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}

        result = correct_training_row(
            ROW_ID,
            {"price": "14500", "currency": "USD"},
        )

        assert result["usd_price"] == 14500
        assert "suspicious_price" not in result["issue_types"]
        offer_watch = mock_update_offer.call_args.kwargs["watch"]
        assert offer_watch["usd_price"] == 14500

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.get_offer_by_id")
    def test_stale_review_issues_are_removed_after_correction(
        self,
        mock_get_offer: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        _mock_update_offer: MagicMock,
        _mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(
            _parsed_watch(),
            issue_types=["missing_reference", "missing_brand", "reference_confidence_low"],
        )
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_get_offer.return_value = {"id": OFFER_ID, "dealer_id": "dealer-1", "message_id": MESSAGE_ID}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}

        result = correct_training_row(
            ROW_ID,
            {
                "brand": "Rolex",
                "reference": "126610LN",
                "condition": NEW_CONDITION,
                "year": "2024",
                "price": "14500",
                "currency": "USD",
            },
        )

        assert result["issue_types"] == []
        display = format_training_row_display(result)
        assert display["issues"] == []

    @patch("database.patch_import_log")
    def test_sync_import_summary_uses_corrected_values(self, mock_patch: MagicMock) -> None:
        import_log = _import_log()
        final_watch = build_final_offer_payload(
            _training_row_from_watch(_parsed_watch()),
            {
                "brand": "Rolex",
                "reference": "126610LN",
                "condition": NEW_CONDITION,
                "year": "2024",
                "price": "14500",
                "currency": "USD",
            },
        )
        mock_patch.return_value = import_log

        sync_import_log_summary_for_training_row(
            import_log,
            row_index=0,
            final_watch=final_watch,
            offer_id=OFFER_ID,
            market_price_debug={"market_price_confidence": 100, "market_price_eligible": True},
        )

        summary_arg = mock_patch.call_args.kwargs["summary"]
        assert summary_arg["rows"][0]["brand"] == "Rolex"
        assert summary_arg["rows"][0]["reference"] == "126610LN"
        assert summary_arg["rows"][0]["usd_price"] == 14500
        assert summary_arg["offer_watches"][0]["brand"] == "Rolex"

    @patch("database.insert_offer")
    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training")
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.get_offer_by_id")
    def test_save_row_is_idempotent(
        self,
        mock_get_offer: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        _mock_update_offer: MagicMock,
        _mock_sync_summary: MagicMock,
        _mock_insert_offer: MagicMock,
    ) -> None:
        row = _training_row_from_watch(_parsed_watch(), confidence_overall=53)
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_get_offer.return_value = {"id": OFFER_ID, "dealer_id": "dealer-1", "message_id": MESSAGE_ID}
        mock_update_row.side_effect = lambda row_id, **fields: {"id": row_id, **row, **fields}
        _mock_insert_offer.return_value = ({"id": OFFER_ID}, True)

        corrections = {
            "brand": "Rolex",
            "reference": "126610LN",
            "condition": NEW_CONDITION,
            "year": "2024",
            "price": "14500",
            "currency": "USD",
        }
        first = correct_training_row(ROW_ID, corrections)
        row.update(first)
        mock_get_row.return_value = row
        second = correct_training_row(ROW_ID, corrections)

        assert second["normalized_brand"] == first["normalized_brand"]
        assert second["normalized_reference"] == first["normalized_reference"]
        assert second["usd_price"] == first["usd_price"]
        assert second["status"] == first["status"]

    @patch("parser_training_engine.sync_import_log_summary_for_training_row")
    @patch("database.update_offer_from_training", side_effect=RuntimeError("offer update failed"))
    @patch("database.update_parser_training_row")
    @patch("database.get_message_by_id")
    @patch("database.get_import_log")
    @patch("database.get_parser_training_row")
    @patch("database.get_offer_by_id")
    def test_offer_update_failure_does_not_update_training_row(
        self,
        mock_get_offer: MagicMock,
        mock_get_row: MagicMock,
        mock_get_import: MagicMock,
        mock_get_message: MagicMock,
        mock_update_row: MagicMock,
        _mock_update_offer: MagicMock,
        _mock_sync_summary: MagicMock,
    ) -> None:
        row = _training_row_from_watch(_parsed_watch())
        mock_get_row.return_value = row
        mock_get_import.return_value = _import_log()
        mock_get_message.return_value = {"id": MESSAGE_ID}
        mock_get_offer.return_value = {"id": OFFER_ID, "dealer_id": "dealer-1", "message_id": MESSAGE_ID}

        with pytest.raises(RuntimeError, match="offer update failed"):
            correct_training_row(
                ROW_ID,
                {
                    "brand": "Rolex",
                    "reference": "126610LN",
                    "condition": NEW_CONDITION,
                    "year": "2024",
                    "price": "14500",
                    "currency": "USD",
                },
            )

        mock_update_row.assert_not_called()


class TestDownstreamConsumers:
    def test_watch_from_training_row_uses_normalized_final_values(self) -> None:
        row = _training_row_from_watch(
            _parsed_watch(brand="Tudor"),
            normalized_brand="Rolex",
            normalized_reference="126610LN",
            parser_explanation={
                "audit": {
                    "final_offer_snapshot": {
                        "production_year": 2024,
                        "original_price": 14500,
                        "original_currency": "USD",
                    }
                }
            },
        )
        watch = watch_from_training_row(row)
        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "126610LN"
        assert watch["production_year"] == 2024

    @patch("search._filter_search_offers")
    def test_search_receives_corrected_offer_values(self, mock_filter: MagicMock) -> None:
        from search import search_offers

        mock_filter.return_value = [
            {
                "id": OFFER_ID,
                "brand": "Rolex",
                "reference": "126610LN",
                "usd_price": 14500,
                "watches": {"brand": "Rolex", "reference": "126610LN"},
            }
        ]
        offers, _ = search_offers("126610LN")
        assert offers[0]["reference"] == "126610LN"
        assert offers[0]["usd_price"] == 14500

    def test_deal_analysis_uses_corrected_summary_row(self) -> None:
        from app import _build_deal_analysis

        row = {
            "offer_id": OFFER_ID,
            "brand": "Rolex",
            "reference": "126610LN",
            "condition": NEW_CONDITION,
            "usd_price": 14000,
            "previous_lowest_usd": "14500",
            "price_label": "Good price",
            "market_condition": NEW_CONDITION,
            "reviewed_by_human": True,
        }
        watch = {
            "brand": "Rolex",
            "reference": "126610LN",
            "condition": NEW_CONDITION,
            "usd_price": 14000,
            "reviewed_by_human": True,
        }

        with patch("deal_market_lookup.resolve_deal_market_context") as mock_resolve:
            mock_resolve.return_value = MagicMock(
                effective_row=row,
                comparison_safe=True,
                market_usd=14500,
                offer_condition=NEW_CONDITION,
                market_condition=NEW_CONDITION,
                needs_review=False,
                insufficient_market_data=False,
                market_status_message=None,
                debug={},
            )
            analysis = _build_deal_analysis(row, watch, 0)

        assert analysis["offer_price"] != "N/A"
        assert analysis["show_market_metrics"] is True
