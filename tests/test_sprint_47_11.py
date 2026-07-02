"""Tests for Sprint 47.11 canonical brand resolution priority."""

from __future__ import annotations

import logging

import pytest

from brand_resolver import (
    BRAND_RESOLUTION_ORDER,
    BRAND_SOURCE_MODEL,
    BRAND_SOURCE_REFERENCE,
    apply_brand_resolution_to_watch,
    resolve_watch_brand,
)
from condition_normalizer import normalize_watch_condition
from watch_knowledge import enrich_parsed_watch
from watch_parser import parse_message, parse_watch_line

DAYTONA_MESSAGE = "Daytona 16520 serial T serviced clean w&p 23k fix"
AP_HEADER = "Audemars Piguet"


def _parse_line(line: str, *, current_brand: str | None = None) -> dict:
    watch = parse_watch_line(line, current_brand=current_brand)
    assert watch is not None
    return normalize_watch_condition(enrich_parsed_watch(watch))


def _parse_full(message: str) -> list[dict]:
    return [
        normalize_watch_condition(enrich_parsed_watch(watch))
        for watch in parse_message(message)["watches"]
    ]


class TestDaytona16520BrandResolution:
    def test_daytona_16520_resolves_to_rolex(self) -> None:
        watch = _parse_line(DAYTONA_MESSAGE)

        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "16520"
        assert watch["model"] == "Cosmograph Daytona"

    def test_reference_only_16520_resolves_to_rolex(self) -> None:
        watch = _parse_line("16520 23k usd")

        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "16520"

    def test_daytona_only_resolves_to_rolex(self) -> None:
        watch = _parse_line("Daytona 23k usd")

        assert watch["brand"] == "Rolex"
        assert watch["model"] == "Cosmograph Daytona"

    def test_ap_header_with_daytona_16520_still_resolves_to_rolex(self) -> None:
        watch = _parse_line(DAYTONA_MESSAGE, current_brand=AP_HEADER)

        assert watch["brand"] == "Rolex"
        assert watch["reference"] == "16520"
        assert watch["model"] == "Cosmograph Daytona"
        assert watch.get("brand_context_conflict") == {
            "inherited_brand": AP_HEADER,
            "resolved_brand": "Rolex",
        }

    def test_brand_resolution_trace_logs_daytona_message(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="brand_resolver")
        watch = _parse_line(DAYTONA_MESSAGE)

        assert watch["brand"] == "Rolex"
        assert any("Brand resolution trace for Daytona 16520 message" in record.message for record in caplog.records)
        trace = watch.get("brand_resolution_trace") or []
        assert trace
        assert trace[-1]["step"] == "final"
        assert trace[-1]["brand"] == "Rolex"
        assert trace[-1]["source"] in {BRAND_SOURCE_REFERENCE, BRAND_SOURCE_MODEL}


class TestBrandResolutionPriorityRegression:
    def test_ap26239bc_remains_audemars_piguet(self) -> None:
        watch = _parse_line("AP26239BC fully set 985k usd")

        assert watch["brand"] == "Audemars Piguet"
        assert watch["reference"] == "26239BC"

    def test_5968a_under_ap_header_is_patek_philippe(self) -> None:
        watch = _parse_line("5968A 100,000 USD", current_brand=AP_HEADER)

        assert watch["brand"] == "Patek Philippe"
        assert watch["reference"] == "5968A"

    def test_5500v_under_ap_header_is_vacheron_constantin(self) -> None:
        watch = _parse_line("5500V 100,000 USD", current_brand=AP_HEADER)

        assert watch["brand"] == "Vacheron Constantin"
        assert watch["reference"] == "5500V"

    def test_ap_multi_line_message_still_parses_three_ap_offers(self) -> None:
        message = (
            "Audemars Piguet\n"
            "\n"
            "AP26239BC, fully T-diamond & pink stone, unworn 2021, 985,000 USD\n"
            "\n"
            "AP26585CE black ceramic, full set 2022, 410,000 USD"
        )
        watches = _parse_full(message)

        assert len(watches) == 2
        assert all(watch["brand"] == AP_HEADER for watch in watches)


class TestCanonicalBrandResolutionOrder:
    def test_lower_priority_source_cannot_override_reference(self) -> None:
        resolution = resolve_watch_brand(
            reference="16520",
            text=DAYTONA_MESSAGE,
            model="Daytona",
            explicit_brand=None,
            inherited_brand=AP_HEADER,
            identification_brand="Audemars Piguet",
            brand_before_normalization=AP_HEADER,
        )

        assert resolution.brand == "Rolex"
        assert resolution.source == BRAND_SOURCE_REFERENCE
        assert resolution.priority == 1

    def test_resolution_order_is_documented(self) -> None:
        assert [source for _priority, source in BRAND_RESOLUTION_ORDER] == [
            "reference",
            "model",
            "explicit",
            "inherited",
            "identification",
            "reference_inference",
        ]

    def test_apply_brand_resolution_preserves_highest_priority_brand(self) -> None:
        watch = {"brand": AP_HEADER, "reference": "16520", "model": "Daytona"}
        resolution = resolve_watch_brand(
            reference="16520",
            text=DAYTONA_MESSAGE,
            model="Daytona",
            inherited_brand=AP_HEADER,
            brand_before_normalization=AP_HEADER,
        )
        resolved = apply_brand_resolution_to_watch(
            watch,
            resolution,
            inherited_brand=AP_HEADER,
        )

        assert resolved["brand"] == "Rolex"
        assert resolved["brand_source"] == BRAND_SOURCE_REFERENCE
