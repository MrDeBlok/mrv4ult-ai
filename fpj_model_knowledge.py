"""Centralized F.P. Journe model knowledge, variant extraction, and identity keys."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

Record = dict[str, Any]

FPJ_CANONICAL_BRAND = "F.P. Journe"

YEAR_GLUE_SUFFIX_PATTERN = re.compile(r"\b((?:19|20)\d{2})[Yy]\b")

FPJ_STRONG_ALIAS_KEYS = frozenset(
    {
        "fpj",
        "fp journe",
        "f p journe",
        "f p j",
        "f p journe",
        "f p j",
        "fpjourne",
        "f p journe",
        "f p j",
        "f p j",
        "francois paul journe",
        "francois-paul journe",
        "f p j",
    }
)

CASE_MATERIALS: dict[str, str] = {
    "platinum": "Platinum",
    "pt": "Platinum",
    "rose gold": "Rose Gold",
    "red gold": "Red Gold",
    "pink gold": "Rose Gold",
    "yellow gold": "Yellow Gold",
    "gold": "Yellow Gold",
    "titanium": "Titanium",
    "ti": "Titanium",
    "steel": "Steel",
    "stainless steel": "Steel",
    "tantalum": "Tantalum",
    "aluminium": "Aluminium",
    "aluminum": "Aluminium",
    "ceramic": "Ceramic",
}

EDITION_ALIASES: dict[str, str] = {
    "black label": "Black Label",
    "boutique edition": "Boutique Edition",
}

DIAL_ALIASES: dict[str, str] = {
    "grey": "Grey",
    "gray": "Grey",
    "blue": "Blue",
    "bleu": "Blue",
    "salmon": "Salmon",
    "havana": "Havana",
    "ruthenium": "Ruthenium",
    "mother of pearl": "Mother of Pearl",
    "mop": "Mother of Pearl",
    "jade": "Jade",
    "green": "Green",
    "purple": "Purple",
    "white": "White",
    "black": "Black",
    "gold dial": "Gold dial",
}

SIZE_MM_PATTERN = re.compile(r"\b(3[89]|4[0-4])\s*mm\b", re.I)


@dataclass(frozen=True)
class FpjModelEntry:
    canonical: str
    aliases: tuple[str, ...]
    broad_family: bool = False


FPJ_MODELS: tuple[FpjModelEntry, ...] = (
    FpjModelEntry(
        "Chronomètre à Résonance",
        (
            "chronometre a resonance",
            "chronometre resonance",
            "chronomètre resonance",
            "chronomètre à résonance",
            "chronometre à resonance",
            "resonance",
            "résonance",
        ),
    ),
    FpjModelEntry(
        "Chronomètre Optimum",
        ("chronometre optimum", "chronomètre optimum", "optimum"),
    ),
    FpjModelEntry(
        "Chronomètre Bleu",
        ("chronometre bleu", "chronomètre bleu", "bleu"),
    ),
    FpjModelEntry(
        "Octa Calendrier",
        ("octa calendrier", "octa calendar", "calendrier"),
    ),
    FpjModelEntry(
        "Octa Automatique",
        ("octa automatique", "octa automatic"),
    ),
    FpjModelEntry(
        "Octa Réserve de Marche",
        (
            "octa reserve de marche",
            "octa réserve de marche",
            "reserve de marche",
            "réserve de marche",
        ),
    ),
    FpjModelEntry("Octa Lune", ("octa lune",)),
    FpjModelEntry("Octa Divine", ("octa divine",)),
    FpjModelEntry("Octa Sport", ("octa sport",)),
    FpjModelEntry("Centigraphe Souverain", ("centigraphe souverain", "centigraphe"), broad_family=True),
    FpjModelEntry("Centigraphe Sport", ("centigraphe sport",)),
    FpjModelEntry("Tourbillon Souverain", ("tourbillon souverain",)),
    FpjModelEntry(
        "Tourbillon Remontoir d’Égalité",
        ("tourbillon remontoir d egalite", "tourbillon remontoir d'égalité", "remontoir d egalite"),
    ),
    FpjModelEntry("Tourbillon Vertical", ("tourbillon vertical",)),
    FpjModelEntry("Répétition Souveraine", ("repetition souveraine", "répétition souveraine")),
    FpjModelEntry("Sonnerie Souveraine", ("sonnerie souveraine",)),
    FpjModelEntry("Vagabondage I", ("vagabondage i", "vagabondage 1")),
    FpjModelEntry("Vagabondage II", ("vagabondage ii", "vagabondage 2")),
    FpjModelEntry("Vagabondage III", ("vagabondage iii", "vagabondage 3")),
    FpjModelEntry("Élégante", ("elegante", "élégante")),
    FpjModelEntry("Perpétuelle", ("perpetuelle", "perpétuelle")),
    FpjModelEntry("Astronomic Souveraine", ("astronomic souveraine",)),
    FpjModelEntry("Quantième Perpétuel", ("quantieme perpetuel", "quantième perpétuel")),
    FpjModelEntry(
        "Chronographe Monopoussoir Rattrapante",
        ("chronographe monopoussoir rattrapante",),
    ),
    FpjModelEntry("Linesport", ("linesport",), broad_family=True),
    FpjModelEntry("Classique", ("classique",), broad_family=True),
)

_MODEL_ALIAS_INDEX: list[tuple[str, FpjModelEntry]] | None = None


def _normalize_identity_token(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'").replace("´", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_model_alias_index() -> list[tuple[str, FpjModelEntry]]:
    global _MODEL_ALIAS_INDEX
    if _MODEL_ALIAS_INDEX is not None:
        return _MODEL_ALIAS_INDEX
    indexed: list[tuple[str, FpjModelEntry]] = []
    for entry in FPJ_MODELS:
        indexed.append((_normalize_identity_token(entry.canonical), entry))
        for alias in entry.aliases:
            indexed.append((_normalize_identity_token(alias), entry))
    indexed.sort(key=lambda item: len(item[0]), reverse=True)
    _MODEL_ALIAS_INDEX = indexed
    return indexed


def is_fpj_brand(brand: str | None) -> bool:
    return isinstance(brand, str) and brand.strip() == FPJ_CANONICAL_BRAND


def has_strong_fpj_brand_text(text: str) -> bool:
    from brand_registry import get_brand_aliases, normalize_brand_alias

    aliases = get_brand_aliases()
    lowered = text.lower()
    for alias_key, brand_name in aliases.items():
        if brand_name != FPJ_CANONICAL_BRAND:
            continue
        if alias_key in {"journe"}:
            continue
        pattern = re.compile(rf"\b{re.escape(alias_key).replace(r'\ ', r'\s+')}\b", re.I)
        if pattern.search(lowered):
            return True
    return False


def parse_year_suffix_notation(token: str | None) -> int | None:
    if not token or not isinstance(token, str):
        return None
    match = YEAR_GLUE_SUFFIX_PATTERN.fullmatch(token.strip())
    if not match:
        match = re.fullmatch(r"((?:19|20)\d{2})[Yy]", token.strip())
    if not match:
        return None
    year = int(match.group(1))
    if 1990 <= year <= 2035:
        return year
    return None


def extract_year_glue_suffix(text: str) -> int | None:
    match = YEAR_GLUE_SUFFIX_PATTERN.search(text)
    if not match:
        return None
    year = int(match.group(1))
    if 1990 <= year <= 2035:
        return year
    return None


def mask_year_suffix_spans(text: str) -> str:
    return YEAR_GLUE_SUFFIX_PATTERN.sub(lambda match: " " * len(match.group(0)), text)


def is_blocked_year_reference(reference: str | None) -> bool:
    if not reference:
        return False
    if parse_year_suffix_notation(reference) is None:
        return False
    from reference_knowledge import lookup_authoritative_reference

    return lookup_authoritative_reference(reference) is None


def extract_fpj_model(text: str) -> tuple[str | None, str | None, bool]:
    """Return canonical model, raw matched text, and whether identity is complete."""
    normalized_text = _normalize_identity_token(text)
    if not normalized_text:
        return None, None, False
    for alias, entry in _build_model_alias_index():
        if not alias:
            continue
        if _alias_pattern(alias).search(normalized_text):
            raw = alias
            return entry.canonical, raw, not entry.broad_family
    return None, None, False


def _alias_pattern(alias: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in alias.split()]
    return re.compile(r"\s+".join(parts), re.I)


def _match_phrase(text: str, aliases: dict[str, str]) -> str | None:
    normalized = _normalize_identity_token(text)
    for alias in sorted(aliases, key=len, reverse=True):
        if _alias_pattern(alias).search(normalized):
            return aliases[alias]
    return None


def extract_fpj_variants(text: str) -> Record:
    edition = _match_phrase(text, EDITION_ALIASES)
    case_material = _match_phrase(text, CASE_MATERIALS)
    dial_aliases = dict(DIAL_ALIASES)
    if edition and edition.lower() == "black label":
        dial_aliases.pop("black", None)
    dial_variant = _match_phrase(text, dial_aliases)
    size_match = SIZE_MM_PATTERN.search(text)
    size_mm = int(size_match.group(1)) if size_match else None
    return {
        "case_material": case_material,
        "edition": edition,
        "dial_variant": dial_variant,
        "size_mm": size_mm,
    }


def build_model_identity_key(watch: Record) -> str | None:
    if not is_fpj_brand(watch.get("brand")):
        return None
    model = watch.get("model")
    if not model:
        return None
    parts = [
        _normalize_identity_token(FPJ_CANONICAL_BRAND),
        _normalize_identity_token(str(model)),
        _normalize_identity_token(watch.get("case_material")),
        _normalize_identity_token(watch.get("edition") or watch.get("dial_variant")),
        (
            f"{int(watch['size_mm'])} mm"
            if isinstance(watch.get("size_mm"), int)
            else ""
        ),
        str(watch.get("production_year") or ""),
    ]
    if not parts[1]:
        return None
    return "|".join(part for part in parts if part)


def fpj_storage_identity_fields(watch: Record) -> Record:
    """Map FPJ variant fields into existing watch identity columns without fake references."""
    if not is_fpj_brand(watch.get("brand")):
        return {
            "brand": watch.get("brand"),
            "reference": watch.get("reference"),
            "model": watch.get("model"),
            "dial": watch.get("dial"),
            "bracelet": watch.get("bracelet"),
        }

    material = _normalize_identity_token(watch.get("case_material"))
    edition_or_dial = _normalize_identity_token(watch.get("edition") or watch.get("dial_variant"))
    size_token = (
        f"{int(watch['size_mm'])}mm"
        if isinstance(watch.get("size_mm"), int)
        else ""
    )
    dial_identity = "|".join(part for part in (material, edition_or_dial, size_token) if part) or None
    return {
        "brand": FPJ_CANONICAL_BRAND,
        "reference": None,
        "model": watch.get("model"),
        "dial": dial_identity,
        "bracelet": watch.get("bracelet"),
    }


def fpj_models_are_exact_comparable(left: Record, right: Record) -> bool:
    if not is_fpj_brand(left.get("brand")) or not is_fpj_brand(right.get("brand")):
        return True
    left_key = build_model_identity_key(left)
    right_key = build_model_identity_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key


def apply_fpj_enrichment(watch: Record, text: str) -> Record:
    enriched = dict(watch)
    if not is_fpj_brand(enriched.get("brand")) and not has_strong_fpj_brand_text(text):
        return enriched

    if has_strong_fpj_brand_text(text):
        enriched["brand"] = FPJ_CANONICAL_BRAND
        enriched["brand_source"] = "explicit"

    if is_blocked_year_reference(enriched.get("reference")):
        enriched["reference"] = None
        enriched["reference_high_confidence"] = False

    canonical_model, raw_model, model_complete = extract_fpj_model(text)
    if canonical_model:
        enriched["model"] = canonical_model
        enriched["raw_model_text"] = raw_model
        enriched["model_identity_complete"] = model_complete
    variants = extract_fpj_variants(text)
    for field, value in variants.items():
        if value is not None:
            enriched[field] = value

    if enriched.get("production_year") is None:
        year = extract_year_glue_suffix(text)
        if year is not None:
            enriched["production_year"] = year

    if enriched.get("dial_variant") and not enriched.get("dial"):
        enriched["dial"] = enriched["dial_variant"]

    identity_key = build_model_identity_key(enriched)
    enriched["model_identity_key"] = identity_key
    return enriched


def is_reference_led_brand(brand: str | None) -> bool:
    if not brand:
        return True
    return brand.strip() not in {FPJ_CANONICAL_BRAND}
