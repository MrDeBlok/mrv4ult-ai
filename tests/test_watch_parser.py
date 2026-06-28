"""Unit tests for watch_parser.py using real dealer-style WhatsApp messages."""

from __future__ import annotations

import pytest

from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION, normalize_watch_condition
from watch_parser import parse_message, parse_watch_line


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


class TestPriceRecognition:
    @pytest.mark.parametrize(
        ("line", "expected_price", "expected_currency"),
        [
            ("5711/1A HK$1,880,000 full set", 1_880_000, "HKD"),
            ("RM 67-01 1.88m", 1_880_000, None),
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
