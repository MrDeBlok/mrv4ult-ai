"""Centralized Richard Mille variant knowledge, identity keys, and comparables."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

Record = dict[str, Any]

RM_CANONICAL_BRAND = "Richard Mille"

RM_REFERENCE_PATTERN = re.compile(
    r"\b(RM\s?(?:\d{2,3}(?:[-/.]\d{2,3})?(?:-[A-Z]{1,4})?|\d{3}))\b",
    re.I,
)

VARIANT_ALIASES: dict[str, str] = {
    "starry night": "Starry Night",
    "bright night": "Bright Night",
    "misty night": "Misty Night",
    "jet black": "Jet Black",
    "cotton candy": "Cotton Candy",
    "le mans": "Le Mans",
    "mclaren": "McLaren",
    "mc laren": "McLaren",
    "yuliya levchenko": "Yuliya Levchenko",
    "black label": "Black Label",
    "bright night": "Bright Night",
}

CASE_MATERIAL_ALIASES: dict[str, str] = {
    "carbon tpt": "Carbon TPT",
    "quartz tpt": "Quartz TPT",
    "ntpt carbon": "Carbon NTPT",
    "carbon ntpt": "Carbon NTPT",
    "white ceramic": "White Ceramic",
    "black ceramic": "Black Ceramic",
    "rose gold": "Rose Gold",
    "red gold": "Red Gold",
    "ntpt": "NTPT",
    "carbon": "Carbon",
    "titanium": "Titanium",
    "ti": "Titanium",
    "ceramic": "Ceramic",
    "gold": "Gold",
    "sapphire": "Sapphire",
}

GEM_SETTING_ALIASES: dict[str, str] = {
    "one diamond": "One Diamond",
    "full diamond": "Full Diamond",
    "snow set": "Snow Set",
    "gem set": "Gem Set",
    "baguette": "Baguette",
    "diamond": "Diamond",
}

DIAL_VARIANT_ALIASES: dict[str, str] = {
    "black": "Black",
    "white": "White",
    "grey": "Grey",
    "gray": "Grey",
    "green": "Green",
    "red": "Red",
    "pink": "Pink",
    "blue": "Blue",
    "purple": "Purple",
    "salmon": "Salmon",
}

BRACELET_ALIASES: dict[str, str] = {
    "rose gold bracelet": "Rose Gold Bracelet",
    "titanium bracelet": "Titanium Bracelet",
    "ceramic bracelet": "Ceramic Bracelet",
    "rubber strap": "Rubber Strap",
}


def _normalize_identity_token(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'").replace("´", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _alias_pattern(alias: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in alias.split()]
    return re.compile(r"\s+".join(parts), re.I)


def _match_phrase(text: str, aliases: dict[str, str]) -> str | None:
    normalized = _normalize_identity_token(text)
    for alias in sorted(aliases, key=len, reverse=True):
        if _alias_pattern(alias).search(normalized):
            return aliases[alias]
    return None


def is_rm_brand(brand: str | None) -> bool:
    return isinstance(brand, str) and brand.strip() == RM_CANONICAL_BRAND


def normalize_rm_reference(reference: str | None) -> str | None:
    if not reference:
        return None
    cleaned = re.sub(r"\s+", "", str(reference).upper())
    cleaned = cleaned.replace(".", "-")
    match = RM_REFERENCE_PATTERN.search(cleaned)
    if not match:
        return cleaned or None
    token = match.group(1).upper().replace(" ", "")
    token = re.sub(r"^RM", "RM", token)
    if not token.startswith("RM"):
        token = f"RM{token}"
    return token


def extract_rm_reference(text: str) -> tuple[str | None, str | None]:
    match = RM_REFERENCE_PATTERN.search(text)
    if not match:
        return None, None
    raw = match.group(1).strip()
    return normalize_rm_reference(raw), raw


def _strip_variant_context(text: str, *, reference: str | None) -> str:
    remaining = text
    if reference:
        remaining = re.sub(re.escape(reference), " ", remaining, flags=re.I)
    from watch_parser import (
        CARD_MMYyyy_PATTERN,
        NEW_CARD_DATE_PATTERN,
        PRICE_WITH_CURRENCY_PATTERNS,
    )

    for pattern, _ in PRICE_WITH_CURRENCY_PATTERNS:
        remaining = pattern.sub(" ", remaining)
    remaining = NEW_CARD_DATE_PATTERN.sub(" ", remaining)
    remaining = CARD_MMYyyy_PATTERN.sub(" ", remaining)
    remaining = re.sub(r"\b(?:19|20)\d{2}\b", " ", remaining)
    remaining = re.sub(r"\b\d{1,2}/(?:\d{2}|\d{4})\b", " ", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip(" ,.-")
    return remaining


def extract_rm_variants(text: str, *, reference: str | None = None) -> Record:
    context = _strip_variant_context(text, reference=reference)
    canonical_variant = _match_phrase(context, VARIANT_ALIASES)
    case_material = _match_phrase(context, CASE_MATERIAL_ALIASES)
    gem_setting = _match_phrase(context, GEM_SETTING_ALIASES)
    dial_variant = _match_phrase(context, DIAL_VARIANT_ALIASES)
    bracelet_variant = _match_phrase(context, BRACELET_ALIASES)
    edition = canonical_variant

    if gem_setting == "Diamond" and "one diamond" in _normalize_identity_token(context):
        gem_setting = "One Diamond"

    if canonical_variant and dial_variant and canonical_variant.lower() == dial_variant.lower():
        dial_variant = None

    if canonical_variant in {"Le Mans", "McLaren", "Yuliya Levchenko", "Cotton Candy"}:
        edition = canonical_variant

    return {
        "canonical_variant": canonical_variant,
        "case_material": case_material,
        "gem_setting": gem_setting,
        "dial_variant": dial_variant,
        "bracelet_variant": bracelet_variant,
        "edition": edition,
        "raw_variant_text": context or None,
        "model_identity_complete": bool(canonical_variant or case_material or gem_setting),
    }


def build_rm_identity_key(watch: Record) -> str | None:
    if not is_rm_brand(watch.get("brand")):
        return None
    reference = normalize_rm_reference(watch.get("canonical_reference") or watch.get("reference"))
    if not reference:
        return None
    parts = [
        _normalize_identity_token(reference),
        _normalize_identity_token(watch.get("canonical_variant") or watch.get("model")),
        _normalize_identity_token(watch.get("case_material")),
        _normalize_identity_token(watch.get("gem_setting")),
        _normalize_identity_token(watch.get("dial_variant")),
        _normalize_identity_token(watch.get("bracelet_variant")),
        _normalize_identity_token(watch.get("edition")),
    ]
    if not any(parts[1:]):
        return None
    return "|".join(part for part in parts if part)


def rm_storage_identity_fields(watch: Record) -> Record:
    if not is_rm_brand(watch.get("brand")):
        return {
            "brand": watch.get("brand"),
            "reference": watch.get("reference"),
            "model": watch.get("model"),
            "dial": watch.get("dial"),
            "bracelet": watch.get("bracelet"),
        }

    dial_identity = "|".join(
        part
        for part in (
            watch.get("case_material"),
            watch.get("gem_setting"),
            watch.get("dial_variant"),
            watch.get("edition"),
        )
        if part
    ) or None
    return {
        "brand": RM_CANONICAL_BRAND,
        "reference": normalize_rm_reference(watch.get("reference")),
        "model": watch.get("canonical_variant") or watch.get("model"),
        "dial": dial_identity,
        "bracelet": watch.get("bracelet_variant") or watch.get("bracelet"),
    }


def evaluate_rm_variant_comparability(left: Record, right: Record) -> Record:
    """Return variant match diagnostics for Richard Mille market comparables."""
    left_key = build_rm_identity_key(left)
    right_key = build_rm_identity_key(right)
    if not left_key or not right_key:
        return {
            "variant_match_type": "unknown_variant",
            "exact_match": False,
            "rm_identity_key": left_key,
            "variant_mismatch_reasons": ["unknown_variant"],
        }
    if left_key == right_key:
        return {
            "variant_match_type": "exact_variant",
            "exact_match": True,
            "rm_identity_key": left_key,
            "variant_fields_matched": [
                field
                for field in (
                    "canonical_variant",
                    "case_material",
                    "gem_setting",
                    "dial_variant",
                    "bracelet_variant",
                    "edition",
                )
                if left.get(field) and left.get(field) == right.get(field)
            ],
            "variant_mismatch_reasons": [],
        }

    reasons: list[str] = []
    if left.get("canonical_variant") != right.get("canonical_variant"):
        reasons.append("edition_nickname_mismatch")
    if left.get("gem_setting") != right.get("gem_setting"):
        reasons.append("gem_setting_mismatch")
    if left.get("case_material") != right.get("case_material"):
        reasons.append("material_mismatch")
    if left.get("dial_variant") != right.get("dial_variant"):
        reasons.append("dial_variant_mismatch")
    if left.get("bracelet_variant") != right.get("bracelet_variant"):
        reasons.append("bracelet_mismatch")
    if not reasons:
        reasons.append("variant_identity_mismatch")
    return {
        "variant_match_type": "variant_mismatch",
        "exact_match": False,
        "rm_identity_key": left_key,
        "variant_mismatch_reasons": reasons,
    }


def rm_variants_are_exact_comparable(left: Record, right: Record) -> bool:
    if not is_rm_brand(left.get("brand")) or not is_rm_brand(right.get("brand")):
        return True
    return bool(evaluate_rm_variant_comparability(left, right).get("exact_match"))


def apply_rm_enrichment(watch: Record, text: str) -> Record:
    enriched = dict(watch)
    if not is_rm_brand(enriched.get("brand")) and "richard mille" not in text.lower():
        if not re.search(r"\bRM\s?\d", text, re.I):
            return enriched

    if enriched.get("brand") in {None, "", RM_CANONICAL_BRAND} or re.search(
        r"\b(?:richard\s+mille|rm)\b", text, re.I
    ):
        enriched["brand"] = RM_CANONICAL_BRAND

    reference, raw_reference = extract_rm_reference(text)
    if reference:
        enriched["canonical_reference"] = reference
        if not enriched.get("reference"):
            enriched["reference"] = raw_reference or reference
        if raw_reference:
            enriched["raw_reference_text"] = raw_reference

    variants = extract_rm_variants(
        text,
        reference=enriched.get("canonical_reference") or enriched.get("reference"),
    )
    for field, value in variants.items():
        if value is not None:
            enriched[field] = value
    if variants.get("canonical_variant"):
        enriched["model"] = variants["canonical_variant"]
        enriched["nickname"] = _normalize_identity_token(variants["canonical_variant"])

    identity_key = build_rm_identity_key(enriched)
    enriched["model_identity_key"] = identity_key
    enriched["rm_identity_key"] = identity_key
    return enriched


def is_reference_led_brand(brand: str | None) -> bool:
    if not brand:
        return True
    return brand.strip() not in {RM_CANONICAL_BRAND}
