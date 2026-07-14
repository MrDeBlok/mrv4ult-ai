"""Regression tests for dealer-aware default currency detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from contact_classification import CONTACT_TYPE_DEALER
from dealer_currency_resolution import (
    analyze_dealer_currency_history,
    apply_dealer_currency_resolution,
    build_dealer_currency_intelligence,
    infer_currency_from_phone,
    resolve_implicit_offer_currency,
)
from ingest import ingest_message
from watch_parser import parse_watch_line


class TestPhoneCountryCurrencyMapping:
    @pytest.mark.parametrize(
        ("phone", "expected"),
        [
            ("+85291234567", "HKD"),
            ("85291234567", "HKD"),
            ("+6591234567", "SGD"),
            ("6591234567", "SGD"),
            ("+819012345678", "JPY"),
            ("+8613800138000", "CNY"),
            ("+821012345678", "KRW"),
            ("+31612345678", None),
            ("+14155550123", None),
        ],
    )
    def test_phone_country_mapping(self, phone: str, expected: str | None) -> None:
        assert infer_currency_from_phone(phone) == expected


class TestParserLeavesCurrencyUnknownWithoutExplicitMarker:
    def test_bare_compact_price_has_no_implicit_currency(self) -> None:
        watch = parse_watch_line("4936J naken 183k")
        assert watch is not None
        assert watch["original_price"] == 183_000
        assert watch["original_currency"] is None
        assert watch["currency_explicit"] is False
        assert watch["usd_price"] is None


class TestDealerCurrencyResolution:
    @pytest.mark.parametrize(
        ("line", "phone", "expected_currency", "expected_usd"),
        [
            ("4936J naken 183k", "+85291234567", "HKD", int(round(183_000 * 0.128))),
            ("4936J 183k EUR", "+85291234567", "EUR", int(round(183_000 * 1.08))),
            ("4936J 183k USD", "+85291234567", "USD", 183_000),
            ("4936J 183k HK$", "+85291234567", "HKD", int(round(183_000 * 0.128))),
            ("4936J 183k", "+6591234567", "SGD", int(round(183_000 * 0.74))),
            ("4936J 183k", "+819012345678", "JPY", int(round(183_000 * 0.0064))),
            ("4936J 183k", "+8613800138000", "CNY", int(round(183_000 * 0.14))),
        ],
    )
    def test_dealer_phone_resolves_implicit_currency(
        self,
        line: str,
        phone: str,
        expected_currency: str,
        expected_usd: int,
    ) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        resolved = apply_dealer_currency_resolution(watch, dealer_whatsapp=phone)
        assert resolved["original_currency"] == expected_currency
        assert resolved["usd_price"] == expected_usd

    def test_dealer_default_currency_overrides_phone_country(self) -> None:
        watch = parse_watch_line("4936J 183k")
        dealer = {
            "phone_number": "+85291234567",
            "default_currency": "USD",
            "default_currency_confidence": 95,
        }
        resolved = apply_dealer_currency_resolution(watch, dealer=dealer)
        assert resolved["original_currency"] == "USD"
        assert resolved["currency_resolution"]["source"] == "dealer_default"

    def test_explicit_currency_overrides_dealer_default(self) -> None:
        watch = parse_watch_line("4936J 183k EUR")
        dealer = {
            "phone_number": "+85291234567",
            "default_currency": "HKD",
            "default_currency_confidence": 95,
        }
        resolved = apply_dealer_currency_resolution(watch, dealer=dealer)
        assert resolved["original_currency"] == "EUR"
        assert resolved["currency_resolution"]["source"] == "explicit"

    def test_unknown_phone_leaves_currency_unresolved(self) -> None:
        watch = parse_watch_line("4936J 183k")
        resolved = apply_dealer_currency_resolution(watch, dealer_whatsapp="+31612345678")
        assert resolved["original_currency"] is None
        assert resolved["usd_price"] is None
        assert resolved["currency_resolution"]["source"] == "unknown"


class TestOfferHistoryLearning:
    def test_strong_hkd_history_recommends_hkd(self) -> None:
        offers = [{"original_currency": "HKD"} for _ in range(212)]
        offers.extend({"original_currency": "USD"} for _ in range(3))
        recommended, confidence, counts = analyze_dealer_currency_history(offers)
        assert recommended == "HKD"
        assert confidence >= 98
        assert counts["HKD"] == 212
        assert counts["USD"] == 3

    def test_mixed_currency_history_does_not_force_default(self) -> None:
        offers = (
            [{"original_currency": "HKD"} for _ in range(10)]
            + [{"original_currency": "USD"} for _ in range(10)]
            + [{"original_currency": "EUR"} for _ in range(5)]
        )
        recommended, confidence, counts = analyze_dealer_currency_history(offers)
        assert recommended is None
        assert counts["HKD"] == 10
        assert counts["USD"] == 10
        assert counts["EUR"] == 5
        assert confidence < 70

    def test_dealer_intelligence_includes_currency_metadata(self) -> None:
        dealer = {"phone_number": "+85291234567"}
        offers = [{"original_currency": "HKD"} for _ in range(20)]
        intel = build_dealer_currency_intelligence(dealer, offer_rows=offers)
        assert intel["inferred_from_phone_country"] is True
        assert intel["phone_country_currency"] == "HKD"
        assert intel["recommended_default_currency"] == "HKD"
        assert intel["inferred_from_offer_history"] is True


class TestIngestIntegration:
    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_hk_dealer_bare_price_imports_as_hkd(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_message.return_value = {"id": "message-1"}
        mock_find_watch.return_value = ({"id": "watch-1"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "4936J naken 183k",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["rows"][0]["original_currency"] == "HKD"
        assert summary["rows"][0]["original_price"] == 183_000
        offer_kwargs = mock_insert_offer.call_args.kwargs
        assert offer_kwargs["original_currency"] == "HKD"
        assert offer_kwargs["original_price"] == 183_000

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message")
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    @patch(
        "dealer_currency_resolution.load_dealer_record_for_currency_resolution",
        return_value={
            "id": "dealer-1",
            "phone_number": "+85291234567",
            "default_currency": "HKD",
            "default_currency_confidence": 98,
        },
    )
    def test_explicit_eur_overrides_hk_dealer_default(
        self,
        mock_load_dealer: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_insert_message.return_value = {"id": "message-1"}
        mock_find_watch.return_value = ({"id": "watch-1"}, True)
        mock_insert_offer.return_value = ({"id": "offer-1"}, True)
        mock_insert_import_log.return_value = {"id": "log-1"}

        summary = ingest_message(
            "4936J 183k EUR",
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["rows"][0]["original_currency"] == "EUR"
        offer_kwargs = mock_insert_offer.call_args.kwargs
        assert offer_kwargs["original_currency"] == "EUR"


class TestResolutionPriority:
    def test_priority_order(self) -> None:
        watch = parse_watch_line("4936J 183k USD")
        explicit_currency, explicit_meta = resolve_implicit_offer_currency(watch)
        assert explicit_currency == "USD"
        assert explicit_meta["source"] == "explicit"

        bare_watch = parse_watch_line("4936J 183k")
        dealer_default, dealer_meta = resolve_implicit_offer_currency(
            bare_watch,
            dealer={
                "phone_number": "+85291234567",
                "default_currency": "SGD",
                "default_currency_confidence": 90,
            },
        )
        assert dealer_default == "SGD"
        assert dealer_meta["source"] == "dealer_default"

        phone_currency, phone_meta = resolve_implicit_offer_currency(
            bare_watch,
            dealer={"phone_number": "+85291234567"},
        )
        assert phone_currency == "HKD"
        assert phone_meta["source"] == "phone_country"
