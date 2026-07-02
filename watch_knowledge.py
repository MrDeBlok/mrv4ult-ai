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


def resolve_reference_brand_identity(reference: str | None) -> tuple[str | None, bool]:
    """Return the canonical brand for a reference when confidently known."""
    if not reference or not isinstance(reference, str):
        return None, False

    knowledge = lookup_reference(reference)
    if knowledge and knowledge.get("brand"):
        return str(knowledge["brand"]).strip(), True

    from brand_knowledge import resolve_unambiguous_reference_brand

    return resolve_unambiguous_reference_brand(reference)


def apply_reference_brand_identity(watch: dict[str, Any]) -> dict[str, Any]:
    """Override inherited brand context when reference identity is known."""
    from brand_resolver import apply_brand_resolution_to_watch, resolve_watch_brand

    text = str(watch.get("source_line") or "")
    identification_brand = None
    identification = watch.get("watch_identification")
    if isinstance(identification, dict):
        identification_brand = identification.get("brand")

    resolution = resolve_watch_brand(
        reference=watch.get("reference"),
        text=text,
        model=watch.get("model"),
        explicit_brand=watch.get("brand") if watch.get("brand_source") == "explicit" else None,
        inherited_brand=watch.get("_inherited_brand"),
        identification_brand=identification_brand,
        brand_before_normalization=watch.get("brand"),
    )
    return apply_brand_resolution_to_watch(
        watch,
        resolution,
        inherited_brand=watch.get("_inherited_brand"),
    )


def _watch_resolution_text(watch: dict[str, Any]) -> str:
    parts = [
        watch.get("source_line"),
        watch.get("brand"),
        watch.get("model"),
        watch.get("nickname"),
        watch.get("reference"),
        watch.get("dial"),
        watch.get("notes"),
    ]
    return " ".join(str(part) for part in parts if part)


def enrich_parsed_watch(watch: dict[str, Any]) -> dict[str, Any]:
    """Attach model aliases, identification, and reference knowledge to a parsed watch."""
    enriched = enrich_with_model_alias(dict(watch))
    enriched = apply_identification_to_watch(enriched)

    from brand_resolver import apply_brand_resolution_to_watch, resolve_explicit_brand, resolve_watch_brand

    text = _watch_resolution_text(enriched)
    source_line = str(enriched.get("source_line") or text)
    identification_brand = None
    identification = enriched.get("watch_identification")
    if isinstance(identification, dict):
        identification_brand = identification.get("brand")

    resolution = resolve_watch_brand(
        reference=enriched.get("reference"),
        text=text,
        model=enriched.get("model"),
        explicit_brand=resolve_explicit_brand(source_line),
        inherited_brand=enriched.get("_inherited_brand"),
        identification_brand=identification_brand,
        brand_before_normalization=enriched.get("brand"),
    )
    enriched = apply_brand_resolution_to_watch(
        enriched,
        resolution,
        inherited_brand=enriched.get("_inherited_brand"),
    )

    knowledge = lookup_reference(enriched.get("reference"))
    if knowledge:
        enriched["knowledge"] = knowledge
        if knowledge.get("model") and not enriched.get("model"):
            enriched["model"] = knowledge["model"]
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
