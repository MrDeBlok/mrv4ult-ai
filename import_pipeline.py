"""Import pipeline tracing helpers for debugging parser-to-database flow."""

from __future__ import annotations

from typing import Any

from condition_normalizer import normalize_watch_condition
from watch_knowledge import enrich_parsed_watch
from watch_parser import _group_offer_lines, iter_content_lines, parse_message

Record = dict[str, Any]


def trace_import_pipeline(raw_message: str) -> Record:
    """Trace a message through line cleaning, parsing, enrichment, and normalization."""
    cleaned_lines = iter_content_lines(raw_message)
    offer_blocks, header_brand = _group_offer_lines(cleaned_lines)
    parser_output = parse_message(raw_message)
    normalized_watches = [
        normalize_watch_condition(enrich_parsed_watch(dict(watch)))
        for watch in parser_output.get("watches", [])
    ]

    database_payloads = [
        {
            "brand": watch.get("brand"),
            "reference": watch.get("reference"),
            "model": watch.get("model"),
            "original_price": watch.get("original_price") or watch.get("price"),
            "original_currency": watch.get("original_currency") or watch.get("currency"),
            "usd_price": watch.get("usd_price"),
            "production_year": watch.get("production_year"),
            "condition": watch.get("condition"),
            "full_set": watch.get("full_set"),
        }
        for watch in normalized_watches
    ]

    return {
        "raw_message": raw_message,
        "cleaned_lines": cleaned_lines,
        "offer_blocks": offer_blocks,
        "header_brand": header_brand,
        "parser_output": parser_output,
        "normalized_watches": normalized_watches,
        "database_payloads": database_payloads,
    }


def build_stored_offer_from_payload(payload: Record, *, offer_id: str = "offer-1") -> Record:
    """Shape a stored offer record from the ingest insert payload."""
    return {
        "id": offer_id,
        "original_price": payload.get("original_price"),
        "original_currency": payload.get("original_currency"),
        "usd_price": payload.get("usd_price"),
    }
