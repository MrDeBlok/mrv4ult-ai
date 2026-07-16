"""Unit tests for watch_parser.py using real dealer-style WhatsApp messages."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from condition_normalizer import (
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    apply_inferred_pre_owned_default,
    condition_display_metadata,
    mark_explicit_condition_metadata,
    normalize_watch_condition,
    resolve_offer_wear_condition,
)
from ingest import enrich_parsed_watch
from parser_learning import detect_condition_training_term, prepare_watch_for_ingest
from watch_knowledge import invalidate_reference_brand_mapping_cache
from watch_parser import (
    _extract_price,
    _normalize_amount,
    parse_compact_price_amount,
    parse_message,
    parse_watch_line,
)


class TestAlsBrandDetection:
    def test_dutch_als_is_not_treated_as_brand(self) -> None:
        result = parse_message("ik ga pas eten als iedereen er is")

        assert result["watches"] == []

    def test_uppercase_als_with_model_and_price(self) -> None:
        watch = parse_watch_line("ALS Odysseus blue dial €40000")

        assert watch is not None
        assert watch["brand"] == "A. Lange & Söhne"
        assert watch["dial"] == "Blue"

    def test_uppercase_als_with_reference_context(self) -> None:
        watch = parse_watch_line("ALS 1815 chrono 2022 full set")

        assert watch is not None
        assert watch["brand"] == "A. Lange & Söhne"
        assert watch["condition"] is None
        assert watch["full_set"] is True
        assert watch["notes"] == "full set"


class TestBrandRecognition:
    @pytest.mark.parametrize(
        ("line", "expected_brand"),
        [
            ("PP 5711/1A blue dial n6/26 580k", "Patek Philippe"),
            ("AP 15500ST blue 2023 full set €52k", "Audemars Piguet"),
            ("VC 4500V black 420k hkd", "Vacheron Constantin"),
            ("RM67-01 RG 1.88m", "Richard Mille"),
            ("ROLEX 126500LN white n9/25 305k usd", "Rolex"),
            ("FPJ Chronomètre Bleu 220k chf", "F.P. Journe"),
            ("ALS 1815 chronograph 185k", "A. Lange & Söhne"),
        ],
    )
    def test_brand_abbreviations(self, line: str, expected_brand: str) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["brand"] == expected_brand


class TestReferenceDetection:
    @pytest.mark.parametrize(
        ("line", "expected_reference"),
        [
            ("PP 5711 green jub 620k", "5711"),
            ("PP 5711/1A blue n6/26 580k", "5711/1A"),
            ("AP 5980R grey 410k", "5980R"),
            ("ROLEX 126500LN white 305k", "126500LN"),
            ("116500 black n5/24 240k usd", "116500"),
            ("AP 15500ST blue full set 320k", "15500ST"),
            ("AP 15407 openworked 890k", "15407"),
            ("RM67 titanium 1.2m", "RM67"),
            ("RM 67-01 RG 1.88m", "RM 67-01"),
        ],
    )
    def test_reference_formats(self, line: str, expected_reference: str) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["reference"] == expected_reference


class TestCompactAmountNormalization:
    @pytest.mark.parametrize(
        ("amount_text", "suffix", "expected_price"),
        [
            ("1", "m", 1_000_000),
            ("1", "M", 1_000_000),
            ("1.018", "m", 1_018_000),
            ("1,018", "m", 1_018_000),
            ("1.25", "m", 1_250_000),
            ("18.3", "m", 18_300_000),
            ("145", "k", 145_000),
            ("145.5", "k", 145_500),
            ("145,5", "k", 145_500),
        ],
    )
    def test_compact_suffix_amounts(
        self,
        amount_text: str,
        suffix: str,
        expected_price: int,
    ) -> None:
        assert _normalize_amount(amount_text, suffix) == expected_price

    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("HKD 1.018m", 1_018_000, "HKD"),
            ("1.018m HKD", 1_018_000, "HKD"),
            ("18,300,000 HKD", 18_300_000, "HKD"),
            ("HKD1.424m", 1_424_000, "HKD"),
            ("HKD 1.424m", 1_424_000, "HKD"),
            ("1.424m HKD", 1_424_000, "HKD"),
            ("HKD1,424m", 1_424_000, "HKD"),
            ("HKD 1,424m", 1_424_000, "HKD"),
            ("USD1.25m", 1_250_000, "USD"),
            ("CHF145.5k", 145_500, "CHF"),
        ],
    )
    def test_compact_and_full_currency_formats(
        self,
        line: str,
        expected_price: int,
        expected_currency: str,
    ) -> None:
        price, currency = _extract_price(line)
        assert price == expected_price
        assert currency == expected_currency

        watch = parse_watch_line(f"Rolex 126334 {line}")
        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == expected_currency


class TestGluedCompactPriceAmount:
    @pytest.mark.parametrize(
        ("token", "expected_price"),
        [
            ("HKD1.424m", 1_424_000),
            ("HKD 1.424m", 1_424_000),
            ("1.424m HKD", 1_424_000),
            ("HKD1,424m", 1_424_000),
            ("USD1.25m", 1_250_000),
            ("CHF145.5k", 145_500),
            ("EUR4.2k", 4_200),
        ],
    )
    def test_parse_compact_price_amount(self, token: str, expected_price: int) -> None:
        assert parse_compact_price_amount(token) == expected_price


class TestAudemarsPiguet26510ORCompactPrice:
    RAW_OFFER = "26510OR 2018 full set\nHKD1.424m"

    def test_full_pipeline_parses_glued_hkd_compact_price(self) -> None:
        result = parse_message(self.RAW_OFFER)
        assert result["message_type"] == "offer"
        assert len(result["watches"]) == 1

        watch = result["watches"][0]
        assert watch["brand"] == "Audemars Piguet"
        assert watch["reference"] == "26510OR"
        assert watch["production_year"] == 2018
        assert watch["original_price"] == 1_424_000
        assert watch["original_currency"] == "HKD"
        assert watch["full_set"] is True


class TestPriceRecognition:
    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("5711/1A HK$1,880,000 full set", 1_880_000, "HKD"),
            ("RM 67-01 1.88m", 1_880_000, None),
            ("HKD 1.018m", 1_018_000, "HKD"),
            ("HKD1.424m full set", 1_424_000, "HKD"),
            ("126500LN 305k usd", 305_000, "USD"),
            ("15500ST €52k full set", 52_000, "EUR"),
            ("5711 USD 95,000 papers", 95_000, "USD"),
            ("5980R CHF 220k", 220_000, "CHF"),
        ],
    )
    def test_price_formats(
        self,
        line: str,
        expected_price: int,
        expected_currency: str | None,
    ) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["original_price"] == expected_price
        if expected_currency is not None:
            assert watch["original_currency"] == expected_currency


class TestFullNumberPriceParsing:
    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("126334 1,168,000 HK$", 1_168_000, "HKD"),
            ("126334 HK$1,168,000", 1_168_000, "HKD"),
            ("126334 1,168,000HK$", 1_168_000, "HKD"),
            ("126334 HKD 1,168,000", 1_168_000, "HKD"),
            ("126334 149,700 US$", 149_700, "USD"),
            ("126334 77,000 HKD", 77_000, "HKD"),
            ("126334 820,000 HKD", 820_000, "HKD"),
            ("126334 1,530,000 HKD", 1_530_000, "HKD"),
            ("126334 1.53m HKD", 1_530_000, "HKD"),
            ("126334 145k USD", 145_000, "USD"),
        ],
    )
    def test_full_number_and_compact_price_formats(
        self,
        line: str,
        expected_price: int,
        expected_currency: str,
    ) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == expected_currency

    @pytest.mark.parametrize(
        ("compact_line", "expected_price"),
        [
            ("126334 1.53m", 1_530_000),
            ("126334 145k", 145_000),
            ("126334 822m", 822_000_000),
            ("126334 18.3m", 18_300_000),
        ],
    )
    def test_compact_notation_still_parses(
        self,
        compact_line: str,
        expected_price: int,
    ) -> None:
        watch = parse_watch_line(compact_line)
        assert watch is not None
        assert watch["original_price"] == expected_price

    def test_dual_currency_offer_prefers_hkd_primary(self) -> None:
        watch = parse_watch_line("126539TBR 1,168,000 HK$ / 149,700 US$")
        assert watch is not None
        assert watch["original_price"] == 1_168_000
        assert watch["original_currency"] == "HKD"

    def test_dual_currency_glued_hkd_offer_prefers_hkd_primary(self) -> None:
        watch = parse_watch_line("126539TBR 1,168,000HK$ / 149,700 US$")
        assert watch is not None
        assert watch["original_price"] == 1_168_000
        assert watch["original_currency"] == "HKD"

    def test_dual_currency_multiline_offer_prefers_hkd_primary(self) -> None:
        result = parse_message("126539TBR 01/2026 New\n1,168,000 HK$ / 149,700 US$")
        assert result["message_type"] == "offer"
        assert len(result["watches"]) == 1
        watch = result["watches"][0]
        assert watch["original_price"] == 1_168_000
        assert watch["original_currency"] == "HKD"


class TestConditionAndAccessories:
    def test_full_set_and_year(self) -> None:
        watch = parse_watch_line("AP 15500ST blue 2023 full set €52k")
        assert watch is not None
        assert watch["condition"] is None
        assert watch["full_set"] is True
        assert watch["notes"] == "full set"
        assert watch["production_year"] == 2023

    def test_watch_only(self) -> None:
        watch = parse_watch_line("PP 5711 blue watch only 540k")
        assert watch is not None
        assert watch["condition"] is None
        assert watch["watch_only"] is True
        assert watch["notes"] == "watch only"

    def test_box_only(self) -> None:
        watch = parse_watch_line("Rolex 116500 box only 180k")
        assert watch is not None
        assert watch["condition"] is None
        assert watch["box_only"] is True
        assert watch["notes"] == "box only"

    def test_papers(self) -> None:
        watch = parse_watch_line("5711/1A with papers 590k")
        assert watch is not None
        assert watch["condition"] is None
        assert watch["papers"] is True
        assert watch["notes"] == "papers"

    def test_used_year(self) -> None:
        watch = parse_watch_line("126231g champ jub used 2024y 147500usd")
        assert watch is not None
        assert watch["condition"] == "Used"
        assert watch["production_year"] == 2024

    def test_new_card_date(self) -> None:
        watch = parse_watch_line("126200 green jub n6/26 74000usd")
        assert watch is not None
        assert watch["condition"] == "New"
        assert watch["card_date"] == "06/2026"


class TestDialDetection:
    def test_abbreviation_and_colour(self) -> None:
        watch = parse_watch_line("126231g champ jub used 2024y 147500usd")
        assert watch is not None
        assert watch["dial"] == "Champagne"

    def test_explicit_colour(self) -> None:
        watch = parse_watch_line("AP 15500ST blue dial full set 320k")
        assert watch is not None
        assert watch["dial"] == "Blue"


class TestConfidenceScore:
    def test_high_confidence_complete_line(self) -> None:
        watch = parse_watch_line("ROLEX 126500LN white n9/25 305k usd")
        assert watch is not None
        assert watch["confidence"] >= 75

    def test_low_confidence_minimal_line(self) -> None:
        watch = parse_watch_line("5711")
        assert watch is not None
        assert watch["confidence"] < 50


class TestMultiWatchMessages:
    HK_DEALER_LIST = """ROLEX
126200 green jub n6/26 74000usd
126231g champ jub used 2024y 147500usd
126500LN white n9/25 305k usd"""

    MIXED_BRAND_LIST = """PP
5711/1A blue n6/26 580k
5980R grey full set 410k
RM 67-01 1.88m"""

    def test_rolex_dealer_block(self) -> None:
        result = parse_message(self.HK_DEALER_LIST)
        assert result["message_type"] == "offer_list"
        assert len(result["watches"]) == 3
        assert result["watches"][0]["brand"] == "Rolex"
        assert result["watches"][0]["reference"] == "126200"
        assert result["watches"][1]["dial"] == "Champagne"
        assert result["watches"][2]["reference"] == "126500LN"

    def test_mixed_brand_block(self) -> None:
        result = parse_message(self.MIXED_BRAND_LIST)
        assert len(result["watches"]) == 3
        assert result["watches"][0]["brand"] == "Patek Philippe"
        assert result["watches"][0]["reference"] == "5711/1A"
        assert result["watches"][1]["reference"] == "5980R"
        assert result["watches"][2]["reference"] == "RM 67-01"
        assert result["watches"][2]["original_price"] == 1_880_000

    REAL_WORLD_BROKER_MESSAGE = """FS

ROLEX
126334 blue jub n3/26 full set 118000hkd
126300 black oys n12/25 82000hkd

AP
15500ST blue 2022 used 2022y watch only 265k hkd
15407 openworked papers 890k

PP 5711 green jub 640k
VC 4500V slate 410k"""

    def test_real_world_broker_message(self) -> None:
        result = parse_message(self.REAL_WORLD_BROKER_MESSAGE)
        assert result["message_type"] == "offer_list"
        watches = result["watches"]
        assert len(watches) >= 6
        references = {watch["reference"] for watch in watches}
        assert "126334" in references
        assert "15500ST" in references
        assert "5711" in references
        assert all(watch["confidence"] > 0 for watch in watches)


class TestUsdNormalization:
    def test_hkd_to_usd(self) -> None:
        watch = parse_watch_line("5711/1A HK$1,880,000")
        assert watch is not None
        assert watch["original_price"] == 1_880_000
        assert watch["original_currency"] == "HKD"
        assert watch["usd_price"] == int(round(1_880_000 * 0.128))


class TestMultilineOfferGrouping:
    GMT_ZOMBIE_OFFER = """Rolex GMT 126713GRNR zombie
New 06/2026
€19500 full set bh deal"""

    def test_multiline_offer_parses_as_single_watch(self) -> None:
        result = parse_message(self.GMT_ZOMBIE_OFFER)
        assert result["message_type"] == "offer"
        assert len(result["watches"]) == 1

        watch = result["watches"][0]
        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "126713GRNR"
        assert watch["model"] == "GMT"
        assert watch["nickname"] == "zombie"
        assert watch["condition"] == "New"
        assert watch["card_date"] == "06/2026"
        assert watch["original_price"] == 19_500
        assert watch["original_currency"] == "EUR"
        assert watch["full_set"] is True
        assert watch["notes"] == "full set bh deal"

    def test_multiline_two_watch_block(self) -> None:
        message = """ROLEX
126713GRNR zombie
New 06/2026
€19500 full set

126500LN white
n9/25
305k usd"""
        result = parse_message(message)
        assert len(result["watches"]) == 2
        assert result["watches"][0]["reference"] == "126713GRNR"
        assert result["watches"][0]["original_price"] == 19_500
        assert result["watches"][1]["reference"] == "126500LN"
        assert result["watches"][1]["original_price"] == 305_000


def _parse_normalized_line(line: str) -> dict:
    watch = parse_watch_line(line)
    assert watch is not None
    return normalize_watch_condition(watch)


class TestWearConditionAccessorySeparation:
    def test_mint_full_set(self) -> None:
        watch = _parse_normalized_line("5711 mint full set 580k")

        assert watch["condition"] == PRE_OWNED_CONDITION
        assert watch["raw_condition"] == "Mint"
        assert watch["full_set"] is True
        assert watch["notes"] == "full set"

    def test_unworn_full_set(self) -> None:
        watch = _parse_normalized_line("5711 unworn full set 580k")

        assert watch["condition"] == NEW_CONDITION
        assert watch["raw_condition"] == "Unworn"
        assert watch["full_set"] is True
        assert watch["notes"] == "full set"

    def test_brand_new_watch_only(self) -> None:
        watch = _parse_normalized_line("116500 Brand New watch only 180k")

        assert watch["condition"] == NEW_CONDITION
        assert watch["raw_condition"] == "Brand New"
        assert watch["watch_only"] is True
        assert watch["notes"] == "watch only"

    def test_used_papers(self) -> None:
        watch = _parse_normalized_line("5711 Used papers 590k")

        assert watch["condition"] == PRE_OWNED_CONDITION
        assert watch["raw_condition"] == "Used"
        assert watch["papers"] is True
        assert watch["notes"] == "papers"


class TestEuropeanPriceParsing:
    EXAMPLE_LINE = (
        "*10.600 Euro 2024 Used Rolex Explorer 36mm 124273* Full Set"
    )

    def test_european_price_before_brand_example(self) -> None:
        watch = parse_watch_line(self.EXAMPLE_LINE)

        assert watch is not None
        assert watch["original_price"] == 10_600
        assert watch["original_currency"] == "EUR"
        assert watch["brand"] == "Rolex"
        assert watch["model"] == "Explorer"
        assert watch["reference"] == "124273"
        assert watch["production_year"] == 2024
        assert watch["condition"] == "Used"
        assert watch["full_set"] is True
        assert watch["notes"] == "full set"

    def test_european_price_before_brand_via_parse_message(self) -> None:
        watches = parse_message(self.EXAMPLE_LINE)["watches"]

        assert len(watches) == 1
        watch = watches[0]
        assert watch["original_price"] == 10_600
        assert watch["original_currency"] == "EUR"
        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "124273"

    @pytest.mark.parametrize(
        ("line", "expected_price"),
        [
            ("10.600 Euro Rolex Explorer 124273 full set", 10_600),
            ("10.600 EUR Rolex Explorer 124273 full set", 10_600),
            ("€10.600 Rolex Explorer 124273 full set", 10_600),
            ("EUR 10.600 Rolex Explorer 124273 full set", 10_600),
            ("10600 Euro Rolex Explorer 124273 full set", 10_600),
            ("10,600 Euro Rolex Explorer 124273 full set", 10_600),
        ],
    )
    def test_european_price_formats(self, line: str, expected_price: int) -> None:
        watch = parse_watch_line(line)

        assert watch is not None
        assert watch["original_price"] == expected_price
        assert watch["original_currency"] == "EUR"

    def test_existing_usd_and_hkd_price_formats_remain_supported(self) -> None:
        usd_watch = parse_watch_line("126500LN 305k usd")
        hkd_watch = parse_watch_line("5711/1A HK$1,880,000 full set")

        assert usd_watch is not None
        assert usd_watch["original_price"] == 305_000
        assert usd_watch["original_currency"] == "USD"
        assert hkd_watch is not None
        assert hkd_watch["original_price"] == 1_880_000
        assert hkd_watch["original_currency"] == "HKD"


TUDOR_ROYAL_OFFER = (
    "Tudor Royal M2836C1A3-0002 Fresh New / Unworn\n"
    "From 06-2026 Full set\n"
    "€4.200,-"
)


def _parse_normalized_message(message: str) -> dict:
    parsed = parse_message(message)
    assert len(parsed["watches"]) == 1
    watch = enrich_parsed_watch(parsed["watches"][0])
    normalize_watch_condition(watch)
    prepare_watch_for_ingest(watch, message_text=message, rules=[])
    return watch


class TestTudorReferenceAndConditionParsing:
    def test_tudor_royal_multiline_offer(self) -> None:
        watch = _parse_normalized_message(TUDOR_ROYAL_OFFER)

        assert watch["brand"] == "Tudor"
        assert watch["reference"] == "M2836C1A3-0002"
        assert watch["reference_high_confidence"] is True
        assert watch["condition"] == NEW_CONDITION
        assert watch["raw_condition"] == "Fresh New / Unworn"
        assert watch["production_year"] == 2026
        assert watch["card_date"] == "06/2026"
        assert watch["original_price"] == 4_200
        assert watch["original_currency"] == "EUR"
        assert watch.get("condition_needs_training") is not True

    def test_tudor_m79030n_new_unworn(self) -> None:
        watch = _parse_normalized_message(
            "Tudor Royal M79030N-0001 New / Unworn EUR 3.500"
        )

        assert watch["brand"] == "Tudor"
        assert watch["reference"] == "M79030N-0001"
        assert watch["condition"] == NEW_CONDITION
        assert watch["raw_condition"] == "New / Unworn"
        assert watch["original_price"] == 3_500
        assert watch["original_currency"] == "EUR"

    def test_tudor_m79360n_fresh_new_with_year(self) -> None:
        watch = _parse_normalized_message(
            "Tudor M79360N-0012 Fresh New 2025 €5.900"
        )

        assert watch["brand"] == "Tudor"
        assert watch["reference"] == "M79360N-0012"
        assert watch["condition"] == NEW_CONDITION
        assert watch["raw_condition"] == "Fresh New"
        assert watch["production_year"] == 2025
        assert watch["original_price"] == 5_900
        assert watch["original_currency"] == "EUR"

    def test_fresh_arrival_does_not_auto_classify_as_new(self) -> None:
        watch = _parse_normalized_message(
            "Tudor M2836C1A3-0002 Fresh arrival €4.200"
        )

        assert watch["brand"] == "Tudor"
        assert watch["reference"] == "M2836C1A3-0002"
        assert watch["original_price"] == 4_200
        assert watch["original_currency"] == "EUR"
        assert watch.get("condition") is None
        assert detect_condition_training_term(
            "Tudor M2836C1A3-0002 Fresh arrival €4.200"
        ) == "fresh"

    def test_european_price_with_trailing_dash_still_parses(self) -> None:
        price, currency = _extract_price("€4.200,-")
        assert price == 4_200
        assert currency == "EUR"

    @patch(
        "watch_knowledge._load_reference_brand_mapping_index",
        return_value={"M2836C1A3-0002": "Tudor"},
    )
    def test_learned_tudor_reference_mapping_applies_globally(
        self,
        _mock_index,
    ) -> None:
        invalidate_reference_brand_mapping_cache()
        watch = enrich_parsed_watch(
            parse_watch_line("M2836C1A3-0002 Fresh New / Unworn 4200 eur")
        )

        assert watch["brand"] == "Tudor"
        assert watch["reference"] == "M2836C1A3-0002"
        assert watch.get("reference_high_confidence") is True

    def test_learned_fresh_new_unworn_condition_rule_applies_globally(self) -> None:
        rules = [
            {
                "id": "rule-1",
                "field_type": "condition",
                "term": "Fresh New / Unworn",
                "normalized_value": "New",
                "scope": "global",
                "status": "active",
            }
        ]
        watch = enrich_parsed_watch(
            parse_watch_line("Tudor M2836C1A3-0002 Fresh New / Unworn 4200 eur")
        )
        prepare_watch_for_ingest(
            watch,
            message_text="Tudor M2836C1A3-0002 Fresh New / Unworn 4200 eur",
            rules=rules,
        )
        normalize_watch_condition(watch)

        assert watch["condition"] == NEW_CONDITION
        assert watch.get("condition_needs_training") is not True


class TestNewPrefixCardDateNotation:
    USER_EXAMPLE = "4946G blue N6/26 - HKD 355,000"

    @pytest.mark.parametrize(
        ("notation", "expected_card_date", "expected_year"),
        [
            ("N6", "06/2026", 2026),
            ("N06", "06/2026", 2026),
            ("N6/26", "06/2026", 2026),
            ("N06/26", "06/2026", 2026),
            ("N6/2026", "06/2026", 2026),
            ("N06/2026", "06/2026", 2026),
            ("N12", "12/2026", 2026),
            ("N12/26", "12/2026", 2026),
            ("N12/2026", "12/2026", 2026),
        ],
    )
    @patch("watch_parser._current_calendar_year", return_value=2026)
    def test_supported_compact_new_notations_behave_identically(
        self,
        _mock_year: object,
        notation: str,
        expected_card_date: str,
        expected_year: int,
    ) -> None:
        line = f"4946G blue {notation} - HKD 355,000"
        watch = parse_watch_line(line)

        assert watch is not None
        assert watch["condition"] == "New"
        assert watch["raw_condition"] == notation
        assert watch["card_date"] == expected_card_date
        assert watch["production_year"] == expected_year

    @patch("watch_parser._current_calendar_year", return_value=2026)
    def test_user_example_parses_new_card_date_and_year(self, _mock_year: object) -> None:
        watch = parse_watch_line(self.USER_EXAMPLE)

        assert watch is not None
        assert watch["reference"] == "4946G"
        assert watch["dial"] == "Blue"
        assert watch["condition"] == "New"
        assert watch["raw_condition"] == "N6/26"
        assert watch["card_date"] == "06/2026"
        assert watch["production_year"] == 2026
        assert watch["original_price"] == 355_000
        assert watch["original_currency"] == "HKD"

    @patch("watch_parser._current_calendar_year", return_value=2026)
    def test_glued_color_before_n_notation_is_recognized(self, _mock_year: object) -> None:
        watch = parse_watch_line("4946G blueN6/26 - HKD 355,000")

        assert watch is not None
        assert watch["condition"] == "New"
        assert watch["raw_condition"] == "N6/26"
        assert watch["card_date"] == "06/2026"
        assert watch["production_year"] == 2026

    @patch("watch_parser._current_calendar_year", return_value=2026)
    def test_user_example_full_pipeline_classifies_as_new_for_deal_analysis(
        self,
        _mock_year: object,
    ) -> None:
        watch = _parse_normalized_message(self.USER_EXAMPLE)
        watch = mark_explicit_condition_metadata(apply_inferred_pre_owned_default(watch))

        assert watch["condition"] == NEW_CONDITION
        assert watch["raw_condition"] == "N6/26"
        assert watch["card_date"] == "06/2026"
        assert watch["production_year"] == 2026
        assert resolve_offer_wear_condition(watch.get("condition"), watch.get("raw_condition")) == NEW_CONDITION
        assert resolve_offer_wear_condition(None, "N6/26") == NEW_CONDITION

        metadata = condition_display_metadata({}, watch)
        assert metadata["label"] == NEW_CONDITION
        assert metadata["is_inferred"] is False

    @pytest.mark.parametrize(
        ("line", "expected_card_date", "expected_raw"),
        [
            ("7128/1R N7/2026 1.53m hkd", "07/2026", "N7/2026"),
            ("7128/1R N07/2026 1.53m HKD", "07/2026", "N07/2026"),
            ("5711/1A N12/2025 145k USD", "12/2025", "N12/2025"),
            ("26510OR N 7/2026 HKD 1.424m", "07/2026", "N 7/2026"),
            ("126200 green jub n6/26 74000usd", "06/2026", "n6/26"),
            ("N7/2025", "07/2025", "N7/2025"),
            ("N07/2027", "07/2027", "N07/2027"),
        ],
    )
    def test_n_prefix_card_date_sets_explicit_new_condition(
        self,
        line: str,
        expected_card_date: str,
        expected_raw: str,
    ) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["condition"] == "New"
        assert watch["card_date"] == expected_card_date
        assert watch["production_year"] == int(expected_card_date.split("/")[1])
        assert watch["raw_condition"] == expected_raw

    @pytest.mark.parametrize(
        ("line", "expected_month", "expected_raw"),
        [
            ("N7", "07", "N7"),
            ("N07", "07", "N07"),
            ("N1", "01", "N1"),
            ("N12", "12", "N12"),
            ("124270 N7 HKD 77k", "07", "N7"),
            ("4300V N7 1.53m HKD", "07", "N7"),
        ],
    )
    @patch("watch_parser._current_calendar_year", return_value=2026)
    def test_compact_n_month_uses_current_calendar_year(
        self,
        _mock_year: object,
        line: str,
        expected_month: str,
        expected_raw: str,
    ) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch["condition"] == "New"
        assert watch["card_date"] == f"{expected_month}/2026"
        assert watch["production_year"] == 2026
        assert watch["raw_condition"] == expected_raw

    def test_patek_7128_full_pipeline(self) -> None:
        watch = _parse_normalized_message("7128/1R N7/2026 1.53m hkd")

        assert watch["brand"] == "Patek Philippe"
        assert watch["reference"] == "7128/1R"
        assert watch["condition"] == NEW_CONDITION
        assert watch["raw_condition"] == "N7/2026"
        assert watch["card_date"] == "07/2026"
        assert watch["production_year"] == 2026
        assert watch["original_price"] == 1_530_000
        assert watch["original_currency"] == "HKD"

        watch = mark_explicit_condition_metadata(
            apply_inferred_pre_owned_default(watch)
        )
        metadata = condition_display_metadata({}, watch)
        assert metadata["is_inferred"] is False
        assert metadata["inference_note"] is None
        assert metadata["label"] == NEW_CONDITION

    @patch("watch_parser._current_calendar_year", return_value=2026)
    def test_compact_n7_full_pipeline_recognized_as_new_for_deal_analysis(
        self,
        _mock_year: object,
    ) -> None:
        watch = _parse_normalized_message("124270 N7 HKD 77k")
        watch = mark_explicit_condition_metadata(
            apply_inferred_pre_owned_default(watch)
        )

        assert watch["condition"] == NEW_CONDITION
        assert watch["card_date"] == "07/2026"
        assert watch["production_year"] == 2026
        assert watch.get("condition_source") != "inferred_default"

        metadata = condition_display_metadata({}, watch)
        assert metadata["label"] == NEW_CONDITION
        assert metadata["is_inferred"] is False

    @patch("watch_parser._current_calendar_year", return_value=2026)
    def test_vacheron_compact_n7_full_pipeline(self, _mock_year: object) -> None:
        watch = _parse_normalized_message("4300V N7 1.53m HKD")

        assert watch["reference"] == "4300V"
        assert watch["condition"] == NEW_CONDITION
        assert watch["card_date"] == "07/2026"
        assert watch["production_year"] == 2026
        assert watch["original_price"] == 1_530_000

    @pytest.mark.parametrize(
        "line",
        [
            "M79030N-0001 145k USD",
            "Nautilus 5711/1A 145k USD",
            "7128/1R full set 1.53m hkd",
            "126610LN 305k usd",
            "Panerai PAM01312 145k USD",
            "NEW OLD STOCK 145k USD",
        ],
    )
    def test_n_inside_references_or_words_is_ignored(self, line: str) -> None:
        watch = parse_watch_line(line)
        assert watch is not None
        assert watch.get("card_date") is None
        if watch.get("raw_condition") is not None:
            assert not str(watch["raw_condition"]).upper().startswith("N")
