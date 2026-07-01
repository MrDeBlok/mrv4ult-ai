"""Brand-specific reference patterns and parsing knowledge for the watch parser."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

Record = dict[str, Any]

COMPOUND_REFERENCE_TOKEN_PATTERN = re.compile(
    r"\b[\dA-Za-z]+(?:\.[\dA-Za-z]+)+\b",
)


@dataclass(frozen=True)
class BrandKnowledge:
    """Knowledge bundle for one watch brand."""

    brand: str
    reference_patterns: tuple[str, ...]
    collection_aliases: dict[str, str] = field(default_factory=dict)
    nicknames: dict[str, str] = field(default_factory=dict)
    parsing_hints: dict[str, Any] = field(default_factory=dict)


BRAND_KNOWLEDGE: dict[str, BrandKnowledge] = {
    "Rolex": BrandKnowledge(
        brand="Rolex",
        reference_patterns=(
            r"\b([12]\d{5}[A-Za-z]{0,4})\b",
        ),
        collection_aliases={
            "sub": "Submariner",
            "submariner": "Submariner",
            "daytona": "Daytona",
            "datejust": "Datejust",
            "gmt": "GMT-Master II",
        },
        nicknames={
            "batman": "Batman",
            "pepsi": "Pepsi",
            "hulk": "Hulk",
        },
    ),
    "Patek Philippe": BrandKnowledge(
        brand="Patek Philippe",
        reference_patterns=(
            r"\b(\d{4}/[0-9A-Z]+)\b",
            r"\b(\d{4}[A-Za-z]-\d{3,})\b",
            r"\b([3456]\d{3}[A-Za-z]?)\b",
            r"\b([3456]\d{3})\b",
        ),
        collection_aliases={
            "nautilus": "Nautilus",
            "aquanaut": "Aquanaut",
        },
    ),
    "Audemars Piguet": BrandKnowledge(
        brand="Audemars Piguet",
        reference_patterns=(
            r"\b(\d{5}(?!usdt|ustd|usd|hkd|eur|euro|chf|gbp|sgd|aed|jpy)[A-Za-z]{2,4})\b",
            r"\b(\d{4}[A-Za-z])\b",
            r"\b(\d{5})\b",
        ),
        collection_aliases={
            "royal oak": "Royal Oak",
            "ro": "Royal Oak",
        },
    ),
    "Piaget": BrandKnowledge(
        brand="Piaget",
        reference_patterns=(
            r"\b(G0A\d{5})\b",
            r"\b(G0A\d{4})\b",
        ),
        collection_aliases={
            "polo": "Polo",
            "altiplano": "Altiplano",
        },
        parsing_hints={"scan_without_brand_hint": True},
    ),
    "Cartier": BrandKnowledge(
        brand="Cartier",
        reference_patterns=(
            r"\b(WSSA[A-Z0-9]{4,6})\b",
            r"\b(WSPN[A-Z0-9]{4,6})\b",
            r"\b(WGSA[A-Z0-9]{4,6})\b",
            r"\b(CRWSSA[A-Z0-9]{4,8})\b",
        ),
        collection_aliases={
            "santos": "Santos",
            "tank": "Tank",
            "ballon bleu": "Ballon Bleu",
        },
        parsing_hints={"scan_without_brand_hint": True},
    ),
    "Richard Mille": BrandKnowledge(
        brand="Richard Mille",
        reference_patterns=(
            r"\b(RM\s?\d{2,3}(?:[-\s/]\d{2,3})?[A-Za-z]{0,4})\b",
            r"\b(RM\d{2,3}(?:[-\s/]\d{2,3})?[A-Za-z]{0,4})\b",
        ),
        parsing_hints={"scan_without_brand_hint": True, "normalize_spaces": True},
    ),
    "Hublot": BrandKnowledge(
        brand="Hublot",
        reference_patterns=(
            r"\b(\d{3}(?:\.[A-Z0-9]{2,5}){2,5})\b",
        ),
        collection_aliases={
            "big bang": "Big Bang",
            "classic fusion": "Classic Fusion",
        },
    ),
}


def get_brand_knowledge(brand: str | None) -> BrandKnowledge | None:
    """Return knowledge for a canonical brand name."""
    if not brand:
        return None
    return BRAND_KNOWLEDGE.get(brand)


def list_brands_with_knowledge() -> tuple[str, ...]:
    """Return canonical brand names that have knowledge entries."""
    return tuple(BRAND_KNOWLEDGE.keys())


def normalize_reference_token(reference: str) -> str:
    """Normalize a matched reference token for storage."""
    cleaned = reference.upper().replace("  ", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def is_embedded_in_compound_reference_token(text: str, start: int, end: int) -> bool:
    """Return True when a match lies inside a longer dotted reference token."""
    for token_match in COMPOUND_REFERENCE_TOKEN_PATTERN.finditer(text):
        token_start = token_match.start()
        token_end = token_match.end()
        if token_start <= start and end <= token_end and end - start < token_end - token_start:
            return True
    return False


@lru_cache(maxsize=None)
def _compiled_reference_patterns(brand: str) -> tuple[re.Pattern[str], ...]:
    knowledge = BRAND_KNOWLEDGE.get(brand)
    if knowledge is None:
        return tuple()
    return tuple(
        re.compile(pattern, re.I)
        for pattern in knowledge.reference_patterns
    )


def brands_for_reference_scan(*, brand_hint: str | None = None) -> tuple[str, ...]:
    """Return which brands to scan for reference patterns."""
    if brand_hint:
        return (brand_hint,) if brand_hint in BRAND_KNOWLEDGE else tuple()
    return tuple(
        brand
        for brand, knowledge in BRAND_KNOWLEDGE.items()
        if knowledge.parsing_hints.get("scan_without_brand_hint")
    )


def iter_brand_reference_matches(
    text: str,
    *,
    brand: str | None = None,
    brand_hint: str | None = None,
) -> list[tuple[str, str, int, int]]:
    """Yield (reference, brand, start, length) matches ordered by scan."""
    if brand is not None:
        brands = (brand,)
    else:
        brands = brands_for_reference_scan(brand_hint=brand_hint)
    matches: list[tuple[str, str, int, int]] = []
    seen: set[tuple[str, str]] = set()

    for brand_name in brands:
        if not brand_name:
            continue
        for pattern in _compiled_reference_patterns(brand_name):
            for match in pattern.finditer(text):
                reference = normalize_reference_token(match.group(1))
                key = (reference, brand_name)
                if key in seen:
                    continue
                seen.add(key)
                matches.append((reference, brand_name, match.start(1), len(reference)))

    matches.sort(key=lambda item: (item[2], -item[3]))
    return matches


def reference_matches_brand_pattern(reference: str, brand: str | None) -> bool:
    """Return True when a reference fits a brand's knowledge patterns."""
    if not brand:
        return False
    normalized = normalize_reference_token(reference)
    for matched_reference, matched_brand, _, _ in iter_brand_reference_matches(
        normalized,
        brand=brand,
    ):
        if matched_brand == brand and matched_reference == normalized:
            return True
    return False


def extract_reference_from_brand_knowledge(
    text: str,
    *,
    brand_hint: str | None = None,
) -> tuple[str, str, bool] | None:
    """Return the best brand-knowledge reference match in text."""
    matches = iter_brand_reference_matches(text, brand=brand_hint)
    if not matches:
        return None

    if brand_hint:
        hinted = [match for match in matches if match[1] == brand_hint]
        if hinted:
            matches = hinted

    reference, brand, _, length = max(
        matches,
        key=lambda item: (item[3], item[1] == brand_hint if brand_hint else True),
    )
    return reference, brand, True


def invalidate_brand_knowledge_cache() -> None:
    """Clear cached compiled patterns (for tests)."""
    _compiled_reference_patterns.cache_clear()
