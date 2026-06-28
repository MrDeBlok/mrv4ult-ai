"""Local reference knowledge for enriching parsed watch offers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from model_aliases import enrich_with_model_alias
from watch_identifier import apply_identification_to_watch

KNOWLEDGE_PATH = Path(__file__).resolve().parent / "data" / "watch_knowledge.json"

KNOWLEDGE_FIELD_LABELS: list[tuple[str, str]] = [
    ("brand", "Brand"),
    ("collection", "Collection"),
    ("model", "Model"),
    ("nickname", "Nickname"),
    ("metal", "Metal"),
    ("bezel", "Bezel"),
    ("dial_color", "Dial color"),
    ("bracelet", "Bracelet"),
    ("case_size", "Case"),
    ("movement", "Movement"),
    ("production_status", "Status"),
    ("launch_year", "Launch"),
]


def normalize_reference(reference: str | None) -> str | None:
    if not reference or not isinstance(reference, str):
        return None
    cleaned = reference.strip().upper().replace(" ", "").replace("-", "")
    return cleaned or None


@lru_cache(maxsize=1)
def _load_knowledge_index() -> dict[str, dict[str, Any]]:
    if not KNOWLEDGE_PATH.exists():
        return {}

    with KNOWLEDGE_PATH.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    index: dict[str, dict[str, Any]] = {}
    for reference, entry in raw.items():
        normalized = normalize_reference(reference)
        if normalized and isinstance(entry, dict):
            index[normalized] = entry
    return index


def lookup_reference(reference: str | None) -> dict[str, Any] | None:
    """Return reference knowledge when the reference is known."""
    normalized = normalize_reference(reference)
    if not normalized:
        return None

    entry = _load_knowledge_index().get(normalized)
    if not entry:
        return None

    knowledge: dict[str, Any] = {}
    for key, _label in KNOWLEDGE_FIELD_LABELS:
        value = entry.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        knowledge[key] = value
    return knowledge or None


def enrich_parsed_watch(watch: dict[str, Any]) -> dict[str, Any]:
    """Attach model aliases, identification, and reference knowledge to a parsed watch."""
    enriched = apply_identification_to_watch(enrich_with_model_alias(dict(watch)))
    knowledge = lookup_reference(enriched.get("reference"))
    if knowledge:
        enriched["knowledge"] = knowledge
    return enriched


def knowledge_display_fields(knowledge: dict[str, Any]) -> list[dict[str, str]]:
    """Build labeled fields for UI display from a knowledge record."""
    fields: list[dict[str, str]] = []
    for key, label in KNOWLEDGE_FIELD_LABELS:
        value = knowledge.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        fields.append({"label": label, "value": str(value)})
    return fields
