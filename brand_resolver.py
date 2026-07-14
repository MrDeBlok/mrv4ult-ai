"""Canonical brand resolution with explicit source priority."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from brand_registry import get_brand_pattern, lookup_brand
from model_aliases import find_alias_match

logger = logging.getLogger(__name__)

Record = dict[str, Any]

BRAND_SOURCE_REFERENCE = "reference"
BRAND_SOURCE_MODEL = "model"
BRAND_SOURCE_EXPLICIT = "explicit"
BRAND_SOURCE_INHERITED = "inherited"
BRAND_SOURCE_IDENTIFICATION = "identification"
BRAND_SOURCE_REFERENCE_INFERENCE = "reference_inference"

BRAND_RESOLUTION_ORDER: tuple[tuple[int, str], ...] = (
    (1, BRAND_SOURCE_REFERENCE),
    (2, BRAND_SOURCE_MODEL),
    (3, BRAND_SOURCE_EXPLICIT),
    (4, BRAND_SOURCE_INHERITED),
    (5, BRAND_SOURCE_IDENTIFICATION),
    (6, BRAND_SOURCE_REFERENCE_INFERENCE),
)

_PRIORITY_BY_SOURCE = {source: priority for priority, source in BRAND_RESOLUTION_ORDER}


@dataclass(frozen=True)
class BrandCandidate:
    brand: str
    source: str
    priority: int


@dataclass
class BrandResolution:
    brand: str | None = None
    source: str | None = None
    priority: int | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


def _normalize_brand_alias(alias: str) -> str:
    cleaned = alias.lower().replace(".", "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.replace("ö", "o")


def resolve_explicit_brand(text: str) -> str | None:
    """Return an explicit brand mention from message text."""
    match = get_brand_pattern().search(text)
    if not match:
        return None
    return lookup_brand(_normalize_brand_alias(match.group(1)))


def resolve_brand_from_reference(reference: str | None) -> BrandCandidate | None:
    """Priority 1: known reference identity from knowledge bases."""
    if not reference or not isinstance(reference, str):
        return None

    from watch_knowledge import resolve_reference_brand_identity

    brand, confident = resolve_reference_brand_identity(reference)
    if confident and brand:
        return BrandCandidate(
            brand=brand,
            source=BRAND_SOURCE_REFERENCE,
            priority=_PRIORITY_BY_SOURCE[BRAND_SOURCE_REFERENCE],
        )
    return None


def resolve_brand_from_model(text: str, model: str | None = None) -> BrandCandidate | None:
    """Priority 2: model or collection alias lookup."""
    alias_match = find_alias_match(text)
    if alias_match:
        _alias_key, alias_entry = alias_match
        brand = alias_entry.get("brand")
        if isinstance(brand, str) and brand.strip():
            return BrandCandidate(
                brand=brand.strip(),
                source=BRAND_SOURCE_MODEL,
                priority=_PRIORITY_BY_SOURCE[BRAND_SOURCE_MODEL],
            )

    if not model or not isinstance(model, str):
        return None

    normalized_model = model.strip().lower()
    if not normalized_model:
        return None

    alias_match = find_alias_match(normalized_model)
    if alias_match:
        _alias_key, alias_entry = alias_match
        brand = alias_entry.get("brand")
        if isinstance(brand, str) and brand.strip():
            return BrandCandidate(
                brand=brand.strip(),
                source=BRAND_SOURCE_MODEL,
                priority=_PRIORITY_BY_SOURCE[BRAND_SOURCE_MODEL],
            )
    return None


def resolve_brand_from_explicit(explicit_brand: str | None) -> BrandCandidate | None:
    """Priority 3: explicit brand token in the message."""
    if not explicit_brand or not isinstance(explicit_brand, str):
        return None
    cleaned = explicit_brand.strip()
    if not cleaned:
        return None
    return BrandCandidate(
        brand=cleaned,
        source=BRAND_SOURCE_EXPLICIT,
        priority=_PRIORITY_BY_SOURCE[BRAND_SOURCE_EXPLICIT],
    )


def resolve_brand_from_inherited(inherited_brand: str | None) -> BrandCandidate | None:
    """Priority 4: propagated brand header context."""
    if not inherited_brand or not isinstance(inherited_brand, str):
        return None
    cleaned = inherited_brand.strip()
    if not cleaned:
        return None
    return BrandCandidate(
        brand=cleaned,
        source=BRAND_SOURCE_INHERITED,
        priority=_PRIORITY_BY_SOURCE[BRAND_SOURCE_INHERITED],
    )


def resolve_brand_from_identification(identification_brand: str | None) -> BrandCandidate | None:
    """Priority 5: AI/knowledge identification suggestions."""
    if not identification_brand or not isinstance(identification_brand, str):
        return None
    cleaned = identification_brand.strip()
    if not cleaned:
        return None
    return BrandCandidate(
        brand=cleaned,
        source=BRAND_SOURCE_IDENTIFICATION,
        priority=_PRIORITY_BY_SOURCE[BRAND_SOURCE_IDENTIFICATION],
    )


def infer_brand_from_reference_heuristic(reference: str) -> str | None:
    """Priority 6: heuristic reference-shape brand inference."""
    from brand_knowledge import extract_reference_from_brand_knowledge
    from fpj_model_knowledge import is_blocked_year_reference
    from reference_knowledge import (
        is_vacheron_overseas_reference,
        record_generic_override_blocked,
        resolve_authoritative_reference_brand,
    )

    if is_blocked_year_reference(reference):
        return None

    authoritative_brand, authoritative_confident = resolve_authoritative_reference_brand(reference)
    if authoritative_confident and authoritative_brand:
        return authoritative_brand

    brand_match = extract_reference_from_brand_knowledge(reference)
    if brand_match:
        matched_brand = brand_match[1]
        if is_vacheron_overseas_reference(reference) and matched_brand != "Vacheron Constantin":
            record_generic_override_blocked(reference, matched_brand)
            return "Vacheron Constantin"
        return matched_brand

    normalized = reference.upper().replace(" ", "")
    if re.fullmatch(r"\d{3}\.\d{3}", reference):
        return "A. Lange & Söhne"
    if normalized.startswith("RM"):
        return "Richard Mille"
    if "/" in normalized:
        return "Patek Philippe"
    if re.fullmatch(r"[12]\d{5}[A-Z]{0,4}", normalized):
        return "Rolex"
    if re.fullmatch(r"[12]\d{4}", normalized):
        return "Rolex"
    if is_vacheron_overseas_reference(reference):
        return "Vacheron Constantin"
    if re.fullmatch(r"\d{5}[A-Z]{2,4}", normalized):
        return "Audemars Piguet"
    if re.fullmatch(r"\d{4}(?![V])[A-Z]{1,4}", normalized):
        return "Audemars Piguet"
    if re.fullmatch(r"[3456]\d{3}", normalized):
        return "Patek Philippe"
    if re.fullmatch(r"\d{5}", normalized):
        return "Audemars Piguet"
    return None


def resolve_brand_from_reference_inference(reference: str | None) -> BrandCandidate | None:
    """Priority 6: lowest-priority reference heuristic."""
    if not reference or not isinstance(reference, str):
        return None
    brand = infer_brand_from_reference_heuristic(reference)
    if not brand:
        return None
    return BrandCandidate(
        brand=brand,
        source=BRAND_SOURCE_REFERENCE_INFERENCE,
        priority=_PRIORITY_BY_SOURCE[BRAND_SOURCE_REFERENCE_INFERENCE],
    )


def _append_trace(
    trace: list[dict[str, Any]],
    *,
    step: str,
    brand: str | None,
    source: str | None = None,
    **extra: Any,
) -> None:
    entry: dict[str, Any] = {"step": step, "brand": brand}
    if source is not None:
        entry["source"] = source
    entry.update(extra)
    trace.append(entry)


def resolve_watch_brand(
    *,
    reference: str | None = None,
    text: str = "",
    model: str | None = None,
    explicit_brand: str | None = None,
    inherited_brand: str | None = None,
    identification_brand: str | None = None,
    brand_before_normalization: str | None = None,
) -> BrandResolution:
    """Resolve brand using canonical priority order."""
    trace: list[dict[str, Any]] = []
    _append_trace(
        trace,
        step="input",
        brand=brand_before_normalization,
        reference=reference,
        model=model,
        explicit_brand=explicit_brand,
        inherited_brand=inherited_brand,
        identification_brand=identification_brand,
    )

    candidates: list[BrandCandidate] = []

    reference_candidate = resolve_brand_from_reference(reference)
    _append_trace(
        trace,
        step="reference_lookup",
        brand=reference_candidate.brand if reference_candidate else None,
        source=BRAND_SOURCE_REFERENCE,
        reference=reference,
    )
    if reference_candidate:
        candidates.append(reference_candidate)

    model_candidate = resolve_brand_from_model(text, model)
    _append_trace(
        trace,
        step="model_lookup",
        brand=model_candidate.brand if model_candidate else None,
        source=BRAND_SOURCE_MODEL,
        model=model,
    )
    if model_candidate:
        candidates.append(model_candidate)

    explicit_candidate = resolve_brand_from_explicit(explicit_brand)
    _append_trace(
        trace,
        step="explicit_brand",
        brand=explicit_candidate.brand if explicit_candidate else None,
        source=BRAND_SOURCE_EXPLICIT,
    )
    if explicit_candidate:
        candidates.append(explicit_candidate)

    inherited_candidate = resolve_brand_from_inherited(inherited_brand)
    _append_trace(
        trace,
        step="inherited_brand",
        brand=inherited_candidate.brand if inherited_candidate else None,
        source=BRAND_SOURCE_INHERITED,
    )
    if inherited_candidate:
        candidates.append(inherited_candidate)

    identification_candidate = resolve_brand_from_identification(identification_brand)
    _append_trace(
        trace,
        step="identification",
        brand=identification_candidate.brand if identification_candidate else None,
        source=BRAND_SOURCE_IDENTIFICATION,
    )
    if identification_candidate:
        candidates.append(identification_candidate)

    inference_candidate = resolve_brand_from_reference_inference(reference)
    _append_trace(
        trace,
        step="reference_inference",
        brand=inference_candidate.brand if inference_candidate else None,
        source=BRAND_SOURCE_REFERENCE_INFERENCE,
        reference=reference,
    )
    if inference_candidate:
        candidates.append(inference_candidate)

    from fpj_model_knowledge import FPJ_CANONICAL_BRAND, has_strong_fpj_brand_text, is_blocked_year_reference

    if has_strong_fpj_brand_text(text):
        candidates = [
            candidate
            for candidate in candidates
            if candidate.source != BRAND_SOURCE_REFERENCE_INFERENCE
            or candidate.brand == FPJ_CANONICAL_BRAND
        ]
        if is_blocked_year_reference(reference):
            candidates = [
                candidate
                for candidate in candidates
                if candidate.source != BRAND_SOURCE_REFERENCE
            ]

    if not candidates:
        _append_trace(trace, step="final", brand=None, source=None)
        return BrandResolution(trace=trace)

    winner = min(candidates, key=lambda candidate: candidate.priority)
    _append_trace(
        trace,
        step="final",
        brand=winner.brand,
        source=winner.source,
        brand_before_normalization=brand_before_normalization,
    )

    if "daytona" in text.lower() and "16520" in text:
        logger.info(
            "Brand resolution trace for Daytona 16520 message: %s",
            trace,
        )

    return BrandResolution(
        brand=winner.brand,
        source=winner.source,
        priority=winner.priority,
        trace=trace,
    )


def reference_confidently_belongs_to_brand(reference: str, brand: str) -> bool:
    """Return True when a reference clearly belongs to the supplied brand."""
    from brand_knowledge import reference_matches_brand_pattern
    from watch_knowledge import resolve_reference_brand_identity

    known_brand, confident = resolve_reference_brand_identity(reference)
    if confident and known_brand:
        return known_brand == brand
    return reference_matches_brand_pattern(reference, brand)


def reference_confidently_conflicts_with_brand(
    reference: str,
    brand: str,
    *,
    conflict: dict[str, Any] | None = None,
) -> bool:
    """Return True when a reference clearly belongs to a different brand."""
    from watch_knowledge import resolve_reference_brand_identity

    known_brand, confident = resolve_reference_brand_identity(reference)
    if confident and known_brand and known_brand != brand:
        return True

    heuristic_brand = infer_brand_from_reference_heuristic(reference)
    if heuristic_brand and heuristic_brand != brand:
        return True

    if isinstance(conflict, dict):
        inferred = conflict.get("inferred_reference_brand")
        if (
            inferred
            and inferred != brand
            and reference_confidently_belongs_to_brand(reference, inferred)
        ):
            return True

    return False


def apply_reference_brand_safety(watch: Record) -> Record:
    """Block confident cross-brand conflicts; prefer trusted reference mappings."""
    enriched = dict(watch)
    reference = enriched.get("reference")
    brand = enriched.get("brand")
    source = enriched.get("brand_source")
    conflict = enriched.get("reference_brand_conflict")

    if not reference or not isinstance(reference, str):
        return enriched

    from reference_knowledge import record_reference_brand_conflict
    from watch_knowledge import resolve_reference_brand_identity

    trusted_brand, trusted_confident = resolve_reference_brand_identity(reference)

    if source == BRAND_SOURCE_REFERENCE and enriched.get("reference_high_confidence"):
        if (
            trusted_confident
            and trusted_brand
            and brand
            and trusted_brand != brand
        ):
            record_reference_brand_conflict(
                reference=reference,
                trusted_brand=trusted_brand,
                rejected_brand=brand,
                source=str(source or "reference"),
            )
            enriched["brand"] = trusted_brand
            enriched["brand_source"] = BRAND_SOURCE_REFERENCE
            enriched["reference_high_confidence"] = True
            enriched.pop("reference_needs_review", None)
            enriched.pop("reference_status", None)
        return enriched

    if brand:
        if reference_confidently_belongs_to_brand(reference, brand):
            return enriched
        if trusted_confident and trusted_brand and trusted_brand != brand:
            record_reference_brand_conflict(
                reference=reference,
                trusted_brand=trusted_brand,
                rejected_brand=brand,
                source=str(source or conflict or "trusted_reference_mapping"),
            )
            enriched["reference_brand_conflict"] = {
                "reference": reference,
                "rejected_brand": brand,
                "resolved_brand": trusted_brand,
                "brand_source": source,
                **(conflict if isinstance(conflict, dict) else {}),
            }
            enriched["brand"] = trusted_brand
            enriched["brand_source"] = BRAND_SOURCE_REFERENCE
            enriched["reference_high_confidence"] = True
            enriched.pop("reference_needs_review", None)
            enriched.pop("reference_status", None)
            return enriched
        if reference_confidently_conflicts_with_brand(reference, brand, conflict=conflict):
            enriched["reference_brand_mismatch"] = {
                "reference": reference,
                "rejected_brand": brand,
                "brand_source": source,
                **(conflict if isinstance(conflict, dict) else {}),
            }
            enriched["brand"] = None
            enriched["brand_source"] = None
            enriched["reference_status"] = "Unknown"
            enriched["reference_needs_review"] = True
            enriched["reference_high_confidence"] = False
        return enriched

    heuristic_brand = infer_brand_from_reference_heuristic(reference)
    if not trusted_confident and not trusted_brand and not heuristic_brand:
        enriched["reference_status"] = "Unknown"
        enriched["reference_needs_review"] = True
    return enriched


def apply_brand_resolution_to_watch(
    watch: Record,
    resolution: BrandResolution,
    *,
    inherited_brand: str | None = None,
) -> Record:
    """Apply resolved brand to a watch dict without lower-priority overrides."""
    enriched = dict(watch)
    brand = resolution.brand
    enriched["brand"] = brand
    enriched["brand_source"] = resolution.source
    enriched["brand_resolution_trace"] = list(resolution.trace)

    if (
        brand
        and inherited_brand
        and inherited_brand != brand
        and resolution.source == BRAND_SOURCE_REFERENCE
    ):
        enriched["brand_context_conflict"] = {
            "inherited_brand": inherited_brand,
            "resolved_brand": brand,
        }
        enriched["reference_high_confidence"] = True
    elif resolution.source == BRAND_SOURCE_REFERENCE:
        enriched["reference_high_confidence"] = True

    return enriched
