"""Authoritative reference knowledge import, lookup helpers, and conflict metrics."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

Record = dict[str, Any]

REFERENCE_KNOWLEDGE_DIR = Path(__file__).resolve().parent / "data" / "reference_knowledge"
VACHERON_CONSTANTIN = "Vacheron Constantin"
AUDEMARS_PIGUET = "Audemars Piguet"

VACHERON_OVERSEAS_V_SUFFIX_PATTERN = re.compile(
    r"^\d{4}V(?:/[A-Z0-9]+)?(?:-[A-Z0-9]+)?$",
    re.I,
)


@dataclass
class ReferenceBrandMetrics:
    """In-process counters for reference-brand resolution observability."""

    exact_mapping_hits: int = 0
    family_pattern_hits: int = 0
    generic_pattern_overrides_blocked: int = 0
    reference_brand_conflicts: int = 0
    vacheron_classified_as_ap: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "exact_mapping_hits": self.exact_mapping_hits,
            "family_pattern_hits": self.family_pattern_hits,
            "generic_pattern_overrides_blocked": self.generic_pattern_overrides_blocked,
            "reference_brand_conflicts": self.reference_brand_conflicts,
            "vacheron_classified_as_ap": self.vacheron_classified_as_ap,
        }


REFERENCE_BRAND_METRICS = ReferenceBrandMetrics()


def normalize_reference_for_lookup(reference: str | None) -> str | None:
    """Normalize a reference for authoritative lookup while preserving / and -."""
    if not reference or not isinstance(reference, str):
        return None
    cleaned = reference.strip().upper().replace(" ", "")
    return cleaned or None


def is_vacheron_overseas_reference(reference: str | None) -> bool:
    """Return True for the high-confidence Vacheron Overseas V-suffix family."""
    token = normalize_reference_for_lookup(reference)
    if not token:
        return False
    return bool(VACHERON_OVERSEAS_V_SUFFIX_PATTERN.fullmatch(token))


def record_exact_mapping_hit(reference: str, brand: str) -> None:
    REFERENCE_BRAND_METRICS.exact_mapping_hits += 1
    logger.debug("Exact reference mapping hit: %s -> %s", reference, brand)


def record_family_pattern_hit(reference: str, brand: str, *, family: str) -> None:
    REFERENCE_BRAND_METRICS.family_pattern_hits += 1
    logger.debug(
        "Reference family pattern hit: %s -> %s (family=%s)",
        reference,
        brand,
        family,
    )


def record_reference_brand_conflict(
    *,
    reference: str,
    trusted_brand: str,
    rejected_brand: str,
    source: str,
) -> None:
    REFERENCE_BRAND_METRICS.reference_brand_conflicts += 1
    if trusted_brand == VACHERON_CONSTANTIN and rejected_brand == AUDEMARS_PIGUET:
        REFERENCE_BRAND_METRICS.vacheron_classified_as_ap += 1
    logger.warning(
        "Reference-brand conflict: reference=%s trusted=%s rejected=%s source=%s",
        reference,
        trusted_brand,
        rejected_brand,
        source,
    )


def record_generic_override_blocked(reference: str, generic_brand: str) -> None:
    REFERENCE_BRAND_METRICS.generic_pattern_overrides_blocked += 1
    logger.info(
        "Blocked generic brand override for %s (would have been %s)",
        reference,
        generic_brand,
    )


@lru_cache(maxsize=1)
def _load_authoritative_reference_index() -> dict[str, dict[str, Any]]:
    """Load maintained JSON datasets from data/reference_knowledge/."""
    index: dict[str, dict[str, Any]] = {}
    if not REFERENCE_KNOWLEDGE_DIR.exists():
        return index

    for path in sorted(REFERENCE_KNOWLEDGE_DIR.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping reference knowledge file %s: %s", path, exc)
            continue

        entries: list[Any]
        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict):
            entries = list(payload.values()) if all(isinstance(v, dict) for v in payload.values()) else [payload]
        else:
            continue

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("status") or "active").lower() in {"deprecated", "ignored"}:
                continue
            reference = entry.get("reference") or entry.get("normalized_reference")
            brand = entry.get("brand")
            if not reference or not brand:
                continue
            key = normalize_reference_for_lookup(str(reference))
            if not key:
                continue
            index[key] = {
                "brand": str(brand).strip(),
                "reference": str(reference).strip().upper(),
                "normalized_reference": key,
                "collection": entry.get("collection"),
                "model": entry.get("model"),
                "source": entry.get("source") or path.stem,
                "source_confidence": entry.get("source_confidence") or "high",
                "reference_family": entry.get("reference_family"),
            }
    return index


def invalidate_authoritative_reference_cache() -> None:
    """Clear cached authoritative reference datasets (for tests and imports)."""
    _load_authoritative_reference_index.cache_clear()


def lookup_authoritative_reference(reference: str | None) -> dict[str, Any] | None:
    """Return an authoritative reference record when confidently known."""
    key = normalize_reference_for_lookup(reference)
    if not key:
        return None
    entry = _load_authoritative_reference_index().get(key)
    if entry:
        record_exact_mapping_hit(key, str(entry.get("brand") or ""))
    return entry


def resolve_authoritative_reference_brand(reference: str | None) -> tuple[str | None, bool]:
    """Return brand from authoritative datasets or Vacheron family rules."""
    entry = lookup_authoritative_reference(reference)
    if entry and entry.get("brand"):
        confidence = str(entry.get("source_confidence") or "high").lower()
        trusted = confidence in {"high", "verified", "authority"}
        return str(entry["brand"]).strip(), trusted

    if is_vacheron_overseas_reference(reference):
        record_family_pattern_hit(
            str(reference),
            VACHERON_CONSTANTIN,
            family="overseas_v_suffix",
        )
        return VACHERON_CONSTANTIN, True
    return None, False


def load_reference_knowledge_dataset(path: Path) -> list[Record]:
    """Load one maintained reference knowledge JSON file."""
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        return [payload]
    raise ValueError(f"Unsupported reference knowledge format in {path}")


def import_reference_knowledge_dataset(
    path: Path,
    *,
    dry_run: bool = True,
    upsert_mappings: bool = True,
) -> Record:
    """Import a maintained dataset into caches and optional DB mappings."""
    rows = load_reference_knowledge_dataset(path)
    report: Record = {
        "dry_run": dry_run,
        "source_file": str(path),
        "total_rows": len(rows),
        "imported": 0,
        "skipped": 0,
        "conflicts": [],
        "proposed_mappings": [],
    }

    for row in rows:
        reference = row.get("reference") or row.get("normalized_reference")
        brand = row.get("brand")
        status = str(row.get("status") or "active").lower()
        if not reference or not brand or status in {"deprecated", "ignored"}:
            report["skipped"] += 1
            continue

        key = normalize_reference_for_lookup(str(reference))
        confidence = str(row.get("source_confidence") or "high").lower()
        proposed = {
            "reference": str(reference).strip().upper(),
            "normalized_reference": key,
            "brand": str(brand).strip(),
            "collection": row.get("collection"),
            "model": row.get("model"),
            "source": row.get("source") or path.stem,
            "source_confidence": confidence,
            "status": status,
        }
        report["proposed_mappings"].append(proposed)
        report["imported"] += 1

        if dry_run or not upsert_mappings:
            continue

        from database import upsert_reference_brand_mapping

        result = upsert_reference_brand_mapping(
            reference=str(reference),
            brand_name=str(brand),
            source=str(row.get("source") or path.stem),
            source_confidence=confidence,
            dry_run=False,
        )
        if result.get("conflict"):
            report["conflicts"].append(result)

    if not dry_run:
        invalidate_authoritative_reference_cache()
        from watch_knowledge import invalidate_reference_brand_mapping_cache

        invalidate_reference_brand_mapping_cache()
    return report


def find_suspicious_vacheron_ap_mappings(
    rows: list[Record],
    *,
    current_brand_key: str = "brand",
    reference_key: str = "reference",
) -> list[Record]:
    """Identify rows where a Vacheron reference is stored under Audemars Piguet."""
    suspects: list[Record] = []
    for row in rows:
        reference = row.get(reference_key)
        current_brand = row.get(current_brand_key)
        if not reference or current_brand != AUDEMARS_PIGUET:
            continue
        authoritative_brand, confident = resolve_authoritative_reference_brand(str(reference))
        if not confident or authoritative_brand != VACHERON_CONSTANTIN:
            continue
        suspects.append(
            {
                "reference": str(reference).strip().upper(),
                "current_brand": current_brand,
                "proposed_brand": authoritative_brand,
                "source": "authoritative_reference_knowledge",
                "confidence": "high",
            }
        )
    return suspects
