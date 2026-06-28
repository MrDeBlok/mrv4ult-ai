"""Regression tests for Sprint 32.2 unknown brand detection improvements."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from unknown_brand_intelligence import (
    UNKNOWN_BRAND_CONFIDENCE_THRESHOLD,
    extract_unknown_brand_candidate,
    extract_unknown_brand_text,
    record_unknown_brands_for_watches,
)


def _watch(*, source_line: str, **fields) -> dict:
    watch = {
        "brand": None,
        "reference": None,
        "model": None,
        "original_price": fields.pop("original_price", None),
        "source_line": source_line,
    }
    watch.update(fields)
    return watch


class TestUnknownBrandStopWordFiltering:
    def test_ik_las_14k_uur_is_not_stored(self) -> None:
        watch = _watch(source_line="Ik las 14k uur", original_price=14_000)

        assert extract_unknown_brand_text(watch) is None
        assert extract_unknown_brand_candidate(watch) is None

    def test_en_die_is_14000_usd_is_not_stored(self) -> None:
        watch = _watch(source_line="En die is 14,000$", original_price=14_000)

        assert extract_unknown_brand_text(watch) is None

    def test_want_hij_zei_is_not_stored(self) -> None:
        watch = _watch(source_line="Want hij zei dat het mooi is", original_price=10_000)

        assert extract_unknown_brand_text(watch) is None

    def test_single_letter_k_is_not_stored(self) -> None:
        watch = _watch(source_line="k 14000 usd", original_price=14_000)

        assert extract_unknown_brand_text(watch) is None


class TestUnknownBrandPositiveCandidates:
    def test_greubel_forsey_is_stored_when_brand_unknown(self) -> None:
        watch = _watch(
            source_line="Greubel Forsey GMT Sport 850k usd",
            original_price=850_000,
        )

        detected = extract_unknown_brand_text(watch)

        assert detected == "Greubel Forsey"
        candidate = extract_unknown_brand_candidate(watch)
        assert candidate is not None
        assert candidate[1] >= UNKNOWN_BRAND_CONFIDENCE_THRESHOLD

    def test_krayon_anywhere_is_stored_when_brand_unknown(self) -> None:
        watch = _watch(
            source_line="Krayon Anywhere steel 220k usd",
            original_price=220_000,
        )

        detected = extract_unknown_brand_text(watch)

        assert detected in {"Krayon", "Krayon Anywhere"}
        candidate = extract_unknown_brand_candidate(watch)
        assert candidate is not None
        assert candidate[1] >= UNKNOWN_BRAND_CONFIDENCE_THRESHOLD

    def test_existing_mysterymaker_candidate_still_works(self) -> None:
        watch = _watch(
            source_line="MysteryMaker 1234 steel 850k usd",
            original_price=850_000,
        )

        assert extract_unknown_brand_text(watch) == "MysteryMaker"

    def test_skips_when_known_brand_present(self) -> None:
        watch = _watch(
            source_line="Rolex 126500LN 305k",
            brand="Rolex",
            original_price=305_000,
        )

        assert extract_unknown_brand_text(watch) is None


class TestUnknownBrandRecordingThreshold:
    @patch("database.record_unknown_brand_sighting")
    @patch("database.watch_knowledge_supported", return_value=True)
    def test_record_unknown_brands_skips_low_confidence(
        self,
        _mock_supported: MagicMock,
        mock_record: MagicMock,
    ) -> None:
        recorded = record_unknown_brands_for_watches(
            [_watch(source_line="Ik las 14k uur", original_price=14_000)],
            example_message="Ik las 14k uur",
            dealer_id="dealer-1",
        )

        assert recorded == []
        mock_record.assert_not_called()

    @patch("database.record_unknown_brand_sighting")
    @patch("database.watch_knowledge_supported", return_value=True)
    def test_record_unknown_brands_persists_high_confidence_candidate(
        self,
        _mock_supported: MagicMock,
        mock_record: MagicMock,
    ) -> None:
        mock_record.return_value = {"id": "unknown-1"}
        watches = [
            _watch(
                source_line="Greubel Forsey GMT Sport 850k usd",
                original_price=850_000,
            )
        ]

        recorded = record_unknown_brands_for_watches(
            watches,
            example_message="Greubel Forsey GMT Sport 850k usd",
            dealer_id="dealer-1",
        )

        assert len(recorded) == 1
        mock_record.assert_called_once_with(
            detected_text="Greubel Forsey",
            example_message="Greubel Forsey GMT Sport 850k usd",
            dealer_id="dealer-1",
            seen_at=None,
        )
