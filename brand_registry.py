"""Built-in and database-backed luxury watch brand registry."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

BRANDS_PATH = Path(__file__).resolve().parent / "data" / "brands.json"

LEGACY_BRAND_ALIASES: dict[str, str] = {
    "rolex": "Rolex",
    "rlx": "Rolex",
    "patek": "Patek Philippe",
    "patek philippe": "Patek Philippe",
    "pp": "Patek Philippe",
    "ap": "Audemars Piguet",
    "audemars": "Audemars Piguet",
    "audemars piguet": "Audemars Piguet",
    "vc": "Vacheron Constantin",
    "vacheron": "Vacheron Constantin",
    "vacheron constantin": "Vacheron Constantin",
    "richard mille": "Richard Mille",
    "rm": "Richard Mille",
    "fp journe": "F.P. Journe",
    "fpj": "F.P. Journe",
    "fpjourne": "F.P. Journe",
    "f.p. journe": "F.P. Journe",
    "f.p.j.": "F.P. Journe",
    "f.p.journe": "F.P. Journe",
    "f.p j journe": "F.P. Journe",
    "f p journe": "F.P. Journe",
    "f p j": "F.P. Journe",
    "francois-paul journe": "F.P. Journe",
    "francois paul journe": "F.P. Journe",
    "als": "A. Lange & Söhne",
    "a lange": "A. Lange & Söhne",
    "a lange & sohne": "A. Lange & Söhne",
    "a. lange & sohne": "A. Lange & Söhne",
    "lange": "A. Lange & Söhne",
}


def normalize_brand_alias(alias: str) -> str:
    cleaned = alias.lower().replace(".", "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("ö", "o")
    return cleaned


def _load_builtin_brand_aliases() -> dict[str, str]:
    aliases = dict(LEGACY_BRAND_ALIASES)
    if not BRANDS_PATH.exists():
        return aliases

    with BRANDS_PATH.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    for brand_name, alias_list in raw.items():
        if not isinstance(alias_list, list):
            continue
        for alias in alias_list:
            if isinstance(alias, str) and alias.strip():
                aliases[normalize_brand_alias(alias)] = brand_name
    return aliases


def _load_database_brand_aliases() -> dict[str, str]:
    try:
        from database import list_active_brand_aliases
    except ImportError:  # pragma: no cover
        return {}

    aliases: dict[str, str] = {}
    for row in list_active_brand_aliases():
        alias_key = row.get("alias_key")
        brand_name = row.get("brand_name")
        if isinstance(alias_key, str) and isinstance(brand_name, str):
            aliases[normalize_brand_alias(alias_key)] = brand_name.strip()
    return aliases


@lru_cache(maxsize=1)
def get_brand_aliases() -> dict[str, str]:
    """Return merged brand alias lookup (legacy, built-in JSON, database)."""
    merged = _load_builtin_brand_aliases()
    merged.update(_load_database_brand_aliases())
    return merged


@lru_cache(maxsize=1)
def get_supported_brands() -> frozenset[str]:
    return frozenset(get_brand_aliases().values())


def _alias_regex_fragment(alias_key: str) -> str:
    escaped = re.escape(alias_key.lower())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\&", r"(?:&|and)")
    return escaped


EXCLUDED_PATTERN_ALIASES = frozenset({"als"})


@lru_cache(maxsize=1)
def get_brand_pattern() -> re.Pattern[str]:
    aliases = get_brand_aliases()
    parts = sorted(
        (key for key in aliases if key not in EXCLUDED_PATTERN_ALIASES),
        key=len,
        reverse=True,
    )
    if not parts:
        return re.compile(r"(?!x)x")
    joined = "|".join(_alias_regex_fragment(part) for part in parts)
    return re.compile(rf"\b({joined})\b", re.I)


def lookup_brand(alias: str) -> str | None:
    return get_brand_aliases().get(normalize_brand_alias(alias))


def invalidate_brand_registry_cache() -> None:
    """Clear cached brand aliases so new database aliases load immediately."""
    get_brand_aliases.cache_clear()
    get_supported_brands.cache_clear()
    get_brand_pattern.cache_clear()
    try:
        from model_aliases import invalidate_alias_cache
    except ImportError:  # pragma: no cover
        return
    invalidate_alias_cache()


def list_canonical_brands() -> list[str]:
    return sorted(get_supported_brands())
