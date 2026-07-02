"""Model and nickname alias matching for parsed watch offers."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ALIASES_PATH = Path(__file__).resolve().parent / "data" / "model_aliases.json"

ALIAS_DISPLAY_FIELDS: list[tuple[str, str]] = [
    ("collection", "Collection"),
    ("model", "Model"),
    ("nickname", "Alias"),
    ("possible_reference", "Possible reference"),
    ("reference_status", "Reference"),
    ("confidence_note", "Confidence note"),
]


@lru_cache(maxsize=1)
def _load_alias_index() -> list[tuple[str, dict[str, Any]]]:
    aliases: list[tuple[str, dict[str, Any]]] = {}

    if ALIASES_PATH.exists():
        with ALIASES_PATH.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        for alias_key, entry in raw.items():
            if isinstance(entry, dict):
                aliases[alias_key.lower().strip()] = entry

    try:
        from database import list_active_brand_aliases, watch_knowledge_supported

        if watch_knowledge_supported():
            for row in list_active_brand_aliases():
                alias_key = str(row.get("alias_key") or "").strip().lower()
                brand_name = str(row.get("brand_name") or "").strip()
                if alias_key and brand_name:
                    aliases.setdefault(
                        alias_key,
                        {
                            "brand": brand_name,
                            "confidence_note": "Identified by database brand alias",
                        },
                    )
    except ImportError:  # pragma: no cover
        pass

    indexed = [(key, entry) for key, entry in aliases.items()]
    indexed.sort(key=lambda item: len(item[0]), reverse=True)
    return indexed


def invalidate_alias_cache() -> None:
    """Clear cached alias index so database aliases load immediately."""
    _load_alias_index.cache_clear()


def _watch_alias_text(watch: dict[str, Any]) -> str:
    parts = [
        watch.get("brand"),
        watch.get("model"),
        watch.get("nickname"),
        watch.get("reference"),
        watch.get("dial"),
        watch.get("notes"),
    ]
    return " ".join(str(part) for part in parts if part)


def find_alias_match(text: str) -> tuple[str, dict[str, Any]] | None:
    """Return the longest matching alias key and entry found in text."""
    if not text.strip():
        return None

    normalized_text = text.lower()
    for alias_key, entry in _load_alias_index():
        pattern = rf"\b{re.escape(alias_key)}\b"
        if re.search(pattern, normalized_text, re.I):
            return alias_key, entry
    return None


def _looks_like_price_reference(reference: str, watch: dict[str, Any]) -> bool:
    if not reference.isdigit():
        return False

    price = watch.get("original_price") or watch.get("price")
    if price is not None and str(price) == reference:
        return True

    if len(reference) == 5 and int(reference) >= 10000:
        return True

    return False


def _should_clear_inferred_reference(
    watch: dict[str, Any],
    alias_entry: dict[str, Any],
) -> bool:
    if alias_entry.get("reference"):
        return False

    reference = watch.get("reference")
    if not reference or not isinstance(reference, str):
        return False

    return _looks_like_price_reference(reference.strip(), watch)


def enrich_with_model_alias(watch: dict[str, Any]) -> dict[str, Any]:
    """Enrich a parsed watch using model/nickname aliases."""
    enriched = dict(watch)
    match = find_alias_match(_watch_alias_text(enriched))
    if not match:
        return enriched

    alias_key, alias_entry = match
    alias_brand = alias_entry.get("brand")
    watch_brand = enriched.get("brand")
    if (
        isinstance(alias_brand, str)
        and alias_brand.strip()
        and isinstance(watch_brand, str)
        and watch_brand.strip()
        and watch_brand != alias_brand
    ):
        return enriched

    alias_info: dict[str, Any] = {
        "alias": alias_key,
        "collection": alias_entry.get("collection"),
        "model": alias_entry.get("model"),
        "nickname": alias_entry.get("nickname"),
        "confidence_note": alias_entry.get("confidence_note"),
    }

    brand = alias_entry.get("brand")
    if isinstance(brand, str) and brand.strip():
        from watch_knowledge import resolve_reference_brand_identity

        known_brand, known_confident = resolve_reference_brand_identity(enriched.get("reference"))
        if not (known_confident and known_brand and known_brand != brand):
            enriched["brand"] = brand

    model = alias_entry.get("model")
    if isinstance(model, str) and model.strip() and not enriched.get("model"):
        enriched["model"] = model

    nickname = alias_entry.get("nickname")
    if isinstance(nickname, str) and nickname.strip() and not enriched.get("nickname"):
        enriched["nickname"] = nickname

    alias_reference = alias_entry.get("reference")
    current_reference = enriched.get("reference")
    if isinstance(alias_reference, str) and alias_reference.strip():
        alias_info["possible_reference"] = alias_reference.strip().upper()
        if not current_reference:
            enriched["reference"] = alias_info["possible_reference"]
    elif _should_clear_inferred_reference(enriched, alias_entry):
        enriched["reference"] = None
        alias_info["reference_status"] = "Unknown"
    elif not current_reference:
        alias_info["reference_status"] = "Unknown"

    if current_reference and not alias_info.get("possible_reference"):
        alias_info["possible_reference"] = str(current_reference).upper()

    enriched["model_alias"] = alias_info
    return enriched


def alias_display_fields(model_alias: dict[str, Any]) -> list[dict[str, str]]:
    """Build labeled fields for watch card display."""
    fields: list[dict[str, str]] = []
    for key, label in ALIAS_DISPLAY_FIELDS:
        value = model_alias.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        fields.append({"label": label, "value": str(value)})
    return fields
