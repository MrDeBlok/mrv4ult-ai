"""Regression tests for dealer-aware default currency detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from contact_classification import CONTACT_TYPE_DEALER
from dealer_currency_resolution import (
    analyze_dealer_currency_history,
    analyze_message_currency_context,
    apply_dealer_currency_resolution,
    build_dealer_currency_intelligence,
    infer_currency_from_phone,
    resolve_implicit_offer_currency,
)
from ingest import ingest_message
from watch_parser import parse_message, parse_watch_line

HK_DEALER_PHONE = "+85291234567"

HK_FULL_DEALER_MESSAGE = """🇭🇰 New 🇭🇰
Rolex 126619LB $618k
PP 5711 $180K
AP 15500 $231K
Omega 210.30.42.20.01.001 $1.128m
Patek 5167A 183k
VC 4500V 183k EUR
Rolex 126334 US$183k
Patek 5711 510k USDT
Tudor M79000 $545k U
5980/1400G 2024 Nos 600k U / 4.68M hkd
5990/1R 2025 Nos 319k U / 2.47M hkd
RM65-01 MCL 6/26 450000U
RM65-01 Wht 5/26 $3.61m
5296G 4.68M hkd
PP 5711/1A EUR 183k full set
"""


def _resolve_row(
    line: str,
    *,
    phone: str | None = HK_DEALER_PHONE,
    dealer: dict | None = None,
    message_text: str | None = None,
) -> dict:
    watch = parse_watch_line(line)
    assert watch is not None
    return apply_dealer_currency_resolution(
        watch,
        dealer=dealer,
        dealer_whatsapp=phone,
        message_text=message_text,
    )


def _legacy_currency_from_watch(watch: dict) -> str | None:
    """Simulate pre-fix behavior where bare $ was stored as explicit USD."""
    if watch.get("currency_evidence") == "ambiguous_dollar_symbol":
        return "USD"
    return watch.get("original_currency")


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
            ("+14155550123", "USD"),
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

    @pytest.mark.parametrize(
        ("line", "expected_evidence"),
        [
            ("Rolex 126619LB $618k", "ambiguous_dollar_symbol"),
            ("4936J 183k USD", "explicit_code"),
            ("Rolex US$183k", "explicit_unambiguous_symbol"),
            ("RM65-01 MCL 6/26 450000U", "usd_shorthand_u"),
            ("Patek 510k USDT", "explicit_code"),
        ],
    )
    def test_currency_evidence_classification(self, line: str, expected_evidence: str) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["currency_evidence"] == expected_evidence
        assert watch["currency_explicit"] == (expected_evidence in {
            "explicit_code",
            "explicit_unambiguous_symbol",
            "usd_shorthand_u",
        })


class TestHongKongBareDollarResolution:
    @pytest.mark.parametrize(
        ("line", "expected_currency", "expected_price"),
        [
            ("Rolex 126619LB $618k", "HKD", 618_000),
            ("PP 5711 $180K", "HKD", 180_000),
            ("Omega $1.128m", "HKD", 1_128_000),
            ("RM65-01 Wht 5/26 $3.61m", "HKD", 3_610_000),
            ("Patek 183k", "HKD", 183_000),
            ("5980/1400G 2024 Nos 600k U / 4.68M hkd", "HKD", 4_680_000),
            ("Tudor $545k U", "USD", 545_000),
            ("RM65-01 MCL 6/26 450000U", "USD", 450_000),
            ("Patek 510k USDT", "USDT", 510_000),
            ("5296G 4.68M hkd", "HKD", 4_680_000),
            ("VC 183k EUR", "EUR", 183_000),
            ("Rolex US$183k", "USD", 183_000),
            ("PP 5711/1A EUR 183k full set", "EUR", 183_000),
        ],
    )
    def test_hk_dealer_row_resolution(
        self,
        line: str,
        expected_currency: str,
        expected_price: int,
    ) -> None:
        resolved = _resolve_row(line)
        assert resolved["original_currency"] == expected_currency
        assert resolved["original_price"] == expected_price


class TestCurrencyContextAndPriority:
    def test_hk_flag_header_supports_bare_dollar_without_phone(self) -> None:
        resolved = _resolve_row(
            "Rolex 126619LB $618k",
            phone=None,
            message_text="🇭🇰 New 🇭🇰",
        )
        assert resolved["original_currency"] == "HKD"
        assert resolved["currency_resolution"]["source"] == "message_context"

    def test_explicit_usd_inside_hk_message_stays_usd(self) -> None:
        resolved = _resolve_row(
            "Tudor $545k U",
            message_text=HK_FULL_DEALER_MESSAGE,
        )
        assert resolved["original_currency"] == "USD"
        assert resolved["currency_resolution"]["source"] == "explicit"

    def test_explicit_eur_inside_hk_message_stays_eur(self) -> None:
        resolved = _resolve_row(
            "VC 183k EUR",
            message_text=HK_FULL_DEALER_MESSAGE,
        )
        assert resolved["original_currency"] == "EUR"

    def test_us_dealer_bare_dollar_resolves_usd(self) -> None:
        resolved = _resolve_row("Rolex 126619LB $618k", phone="+14155550123")
        assert resolved["original_currency"] == "USD"

    def test_singapore_dealer_bare_dollar_resolves_sgd(self) -> None:
        resolved = _resolve_row("Rolex 126619LB $618k", phone="+6591234567")
        assert resolved["original_currency"] == "SGD"

    def test_unknown_dealer_bare_dollar_does_not_become_eur(self) -> None:
        resolved = _resolve_row("Rolex 126619LB $618k", phone="+31612345678")
        assert resolved["original_currency"] is None
        assert resolved["currency_resolution"]["source"] == "unknown"

    def test_dealer_default_overrides_phone_country(self) -> None:
        resolved = _resolve_row(
            "Rolex 126619LB $618k",
            dealer={
                "phone_number": "+85291234567",
                "default_currency": "USD",
                "default_currency_confidence": 95,
            },
        )
        assert resolved["original_currency"] == "USD"
        assert resolved["currency_resolution"]["source"] == "dealer_default"

    def test_message_context_detects_hk_header(self) -> None:
        context = analyze_message_currency_context(HK_FULL_DEALER_MESSAGE)
        assert context["hk_flag_present"] is True
        assert context["trusted_hkd_context"] is True
        assert context["recommended_currency"] == "HKD"


class TestFullHongKongMessageRegression:
    def test_full_message_currency_counts_and_corrections(self) -> None:
        parsed = parse_message(HK_FULL_DEALER_MESSAGE)
        watches = parsed["watches"]
        resolved_rows: list[dict] = []
        corrections: list[dict] = []

        for watch in watches:
            parsed_watch = dict(watch)
            legacy_currency = _legacy_currency_from_watch(parsed_watch)
            resolved = apply_dealer_currency_resolution(
                parsed_watch,
                dealer_whatsapp=HK_DEALER_PHONE,
                message_text=HK_FULL_DEALER_MESSAGE,
            )
            resolved_rows.append(resolved)
            if legacy_currency != resolved.get("original_currency"):
                corrections.append(
                    {
                        "source_line": resolved.get("source_line"),
                        "legacy_currency": legacy_currency,
                        "resolved_currency": resolved.get("original_currency"),
                        "price": resolved.get("original_price"),
                    }
                )

        currency_counts: dict[str | None, int] = {}
        for row in resolved_rows:
            currency = row.get("original_currency")
            currency_counts[currency] = currency_counts.get(currency, 0) + 1

        assert len(resolved_rows) >= 10
        assert currency_counts.get("HKD", 0) >= 6
        assert currency_counts.get("USD", 0) >= 2
        assert currency_counts.get("USDT", 0) >= 1
        assert currency_counts.get("EUR", 0) >= 1
        assert currency_counts.get(None, 0) == 0
        assert len(corrections) >= 4
        usd_corrections = [item for item in corrections if item["legacy_currency"] == "USD"]
        assert len(usd_corrections) >= 4
        assert all(item["resolved_currency"] == "HKD" for item in usd_corrections)


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
