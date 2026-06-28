"""Watch identification engine for nicknames, collector names, and partial references."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

IDENTIFIERS_PATH = Path(__file__).resolve().parent / "data" / "watch_identifiers.json"
MODEL_ALIASES_PATH = Path(__file__).resolve().parent / "data" / "model_aliases.json"
KNOWLEDGE_PATH = Path(__file__).resolve().parent / "data" / "watch_knowledge.json"

Record = dict[str, Any]

IDENTIFICATION_DISPLAY_FIELDS: list[tuple[str, str]] = [
    ("brand", "Brand"),
    ("collection", "Collection"),
    ("model", "Model"),
    ("nickname", "Nickname"),
    ("confidence", "Confidence"),
]


def normalize_reference(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip().upper().replace(" ", "").replace("-", "").replace("/", "")
    return cleaned or None


def normalize_identifier_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def invalidate_identifier_cache() -> None:
    """Clear cached identifier indexes."""
    _load_identifier_index.cache_clear()


def _normalize_entry(entry: Record, matched_key: str) -> Record:
    likely = entry.get("likely_references")
    if not likely and entry.get("reference"):
        likely = [entry["reference"]]

    normalized_refs: list[str] = []
    for reference in likely or []:
        cleaned = str(reference).strip().upper()
        if cleaned:
            normalized_refs.append(cleaned)

    return {
        "matched_key": matched_key,
        "brand": entry.get("brand"),
        "collection": entry.get("collection"),
        "model": entry.get("model"),
        "nickname": entry.get("nickname"),
        "likely_references": normalized_refs,
        "confidence": float(entry.get("confidence", 0.85)),
        "alternatives": list(entry.get("alternatives") or []),
        "match_type": entry.get("match_type") or "nickname",
    }


def _merge_entry(existing: Record | None, incoming: Record) -> Record:
    if existing is None:
        return dict(incoming)

    merged = dict(existing)
    for field in ("brand", "collection", "model", "nickname", "match_type"):
        if not merged.get(field) and incoming.get(field):
            merged[field] = incoming[field]

    refs = list(merged.get("likely_references") or [])
    for reference in incoming.get("likely_references") or []:
        if reference not in refs:
            refs.append(reference)
    merged["likely_references"] = refs
    merged["confidence"] = max(
        float(merged.get("confidence") or 0),
        float(incoming.get("confidence") or 0),
    )
    if incoming.get("alternatives"):
        merged["alternatives"] = [
            *list(merged.get("alternatives") or []),
            *list(incoming.get("alternatives") or []),
        ]
    return merged


def _load_json_aliases(path: Path) -> dict[str, Record]:
    if not path.exists():
        return {}

    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    entries: dict[str, Record] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        normalized_key = normalize_identifier_key(key)
        incoming = _normalize_entry(entry, normalized_key)
        entries[normalized_key] = _merge_entry(entries.get(normalized_key), incoming)
    return entries


def _load_knowledge_reference_index() -> tuple[dict[str, Record], dict[str, list[str]]]:
    if not KNOWLEDGE_PATH.exists():
        return {}, {}

    with KNOWLEDGE_PATH.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    ref_entries: dict[str, Record] = {}
    ref_nicknames: dict[str, list[str]] = {}
    for reference, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        normalized = normalize_reference(reference)
        if not normalized:
            continue
        ref_entries[normalized] = _normalize_entry(
            {
                "brand": entry.get("brand"),
                "collection": entry.get("collection"),
                "model": entry.get("model"),
                "nickname": entry.get("nickname"),
                "likely_references": [reference],
                "confidence": 1.0,
                "match_type": "reference",
            },
            normalized.lower(),
        )
        nickname = entry.get("nickname")
        if isinstance(nickname, str) and nickname.strip():
            ref_nicknames.setdefault(normalized, [])
            if nickname not in ref_nicknames[normalized]:
                ref_nicknames[normalized].append(nickname)
    return ref_entries, ref_nicknames


@lru_cache(maxsize=1)
def _load_identifier_index() -> tuple[
    list[tuple[str, Record]],
    dict[str, Record],
    dict[str, list[str]],
]:
    entries = _load_json_aliases(IDENTIFIERS_PATH)
    for key, entry in _load_json_aliases(MODEL_ALIASES_PATH).items():
        entries[key] = _merge_entry(entries.get(key), entry)

    try:
        from database import list_active_nickname_aliases, watch_identification_supported

        if watch_identification_supported():
            for row in list_active_nickname_aliases():
                alias_key = normalize_identifier_key(str(row.get("alias_key") or ""))
                if not alias_key:
                    continue
                incoming = _normalize_entry(
                    {
                        "brand": row.get("brand_name"),
                        "collection": row.get("collection"),
                        "model": row.get("model_name"),
                        "nickname": row.get("nickname"),
                        "likely_references": row.get("likely_references") or [],
                        "confidence": row.get("confidence") or 0.9,
                        "match_type": "nickname",
                    },
                    alias_key,
                )
                entries[alias_key] = _merge_entry(entries.get(alias_key), incoming)
    except ImportError:  # pragma: no cover
        pass

    knowledge_refs, knowledge_nicknames = _load_knowledge_reference_index()

    ref_entries: dict[str, Record] = dict(knowledge_refs)
    ref_nicknames: dict[str, list[str]] = {
        key: list(value) for key, value in knowledge_nicknames.items()
    }

    for entry in entries.values():
        for reference in entry.get("likely_references") or []:
            normalized = normalize_reference(reference)
            if not normalized:
                continue
            ref_entries[normalized] = _merge_entry(ref_entries.get(normalized), entry)
            nickname = entry.get("nickname")
            if nickname:
                ref_nicknames.setdefault(normalized, [])
                if nickname not in ref_nicknames[normalized]:
                    ref_nicknames[normalized].append(str(nickname))

    indexed = sorted(entries.items(), key=lambda item: len(item[0]), reverse=True)
    return indexed, ref_entries, ref_nicknames


def _build_result(entry: Record, *, match_type: str | None = None) -> Record:
    result = {
        "matched_key": entry.get("matched_key"),
        "brand": entry.get("brand"),
        "collection": entry.get("collection"),
        "model": entry.get("model"),
        "nickname": entry.get("nickname"),
        "likely_references": list(entry.get("likely_references") or []),
        "confidence": float(entry.get("confidence") or 0.85),
        "alternatives": list(entry.get("alternatives") or []),
        "match_type": match_type or entry.get("match_type") or "nickname",
    }
    return result


def _match_partial_reference(token: str, ref_entries: dict[str, Record]) -> Record | None:
    normalized = normalize_reference(token)
    if not normalized:
        return None

    if normalized in ref_entries:
        return _build_result(ref_entries[normalized], match_type="partial_reference")

    prefix_matches = [
        reference
        for reference in ref_entries
        if reference.startswith(normalized) or normalized.startswith(reference[: min(len(reference), 6)])
    ]
    if len(prefix_matches) == 1:
        return _build_result(ref_entries[prefix_matches[0]], match_type="partial_reference")

    if len(prefix_matches) > 1:
        primary = ref_entries[prefix_matches[0]]
        alternatives = [
            {
                "reference": reference,
                "brand": ref_entries[reference].get("brand"),
                "nickname": ref_entries[reference].get("nickname"),
            }
            for reference in prefix_matches[1:3]
        ]
        result = _build_result(primary, match_type="partial_reference")
        result["likely_references"] = prefix_matches
        result["confidence"] = 0.75
        result["alternatives"] = alternatives
        return result

    return None


def identify_text(text: str) -> Record | None:
    """Identify a watch from nickname, collector name, description, or reference text."""
    cleaned = text.strip()
    if not cleaned:
        return None

    indexed, ref_entries, _ref_nicknames = _load_identifier_index()
    normalized = normalize_identifier_key(cleaned)

    for alias_key, entry in indexed:
        if alias_key == normalized:
            return _build_result(entry)
        pattern = rf"\b{re.escape(alias_key)}\b"
        if re.search(pattern, normalized, re.I):
            return _build_result(entry)

    compact = re.sub(r"[\s\-/]", "", cleaned).upper()
    if re.fullmatch(r"[A-Z0-9.]{4,}", compact):
        return _match_partial_reference(compact, ref_entries)

    return None


def references_for_text(text: str) -> list[str]:
    """Return likely references for a nickname or description."""
    result = identify_text(text)
    if not result:
        return []
    return list(result.get("likely_references") or [])


def nicknames_for_reference(reference: str) -> list[str]:
    """Return known nicknames for a reference."""
    normalized = normalize_reference(reference)
    if not normalized:
        return []
    _indexed, _ref_entries, ref_nicknames = _load_identifier_index()
    return list(ref_nicknames.get(normalized, []))


def expand_search_token(token: str) -> set[str]:
    """Expand one search token with nicknames, references, and related terms."""
    terms = {token.lower(), re.sub(r"[\s\-/]", "", token.lower())}

    identification = identify_text(token)
    if identification:
        for field in ("brand", "collection", "model", "nickname"):
            value = identification.get(field)
            if isinstance(value, str) and value.strip():
                terms.add(value.lower())
        for reference in identification.get("likely_references") or []:
            terms.add(reference.lower())
            terms.add(re.sub(r"[\s\-/]", "", reference.lower()))

    normalized = normalize_reference(token)
    if normalized:
        for nickname in nicknames_for_reference(token):
            terms.add(nickname.lower())
        _indexed, ref_entries, _ref_nicknames = _load_identifier_index()
        for reference in ref_entries:
            if reference.startswith(normalized) or normalized.startswith(reference[:6]):
                terms.add(reference.lower())
                for nickname in nicknames_for_reference(reference):
                    terms.add(nickname.lower())

    return terms


def apply_identification_to_watch(watch: Record) -> Record:
    """Apply watch identification to a parsed watch dictionary."""
    enriched = dict(watch)
    text = " ".join(
        str(part)
        for part in (
            watch.get("source_line"),
            watch.get("brand"),
            watch.get("model"),
            watch.get("nickname"),
            watch.get("reference"),
            watch.get("notes"),
        )
        if part
    )
    result = identify_text(text)
    if not result:
        return enriched

    if result.get("brand") and not enriched.get("brand"):
        enriched["brand"] = result["brand"]
    if result.get("model") and not enriched.get("model"):
        enriched["model"] = result["model"]
    if result.get("nickname") and not enriched.get("nickname"):
        enriched["nickname"] = result["nickname"]

    likely_references = list(result.get("likely_references") or [])
    if not enriched.get("reference") and len(likely_references) == 1:
        if float(result.get("confidence") or 0) >= 0.9:
            enriched["reference"] = likely_references[0]

    enriched["watch_identification"] = {
        "matched_key": result.get("matched_key"),
        "brand": result.get("brand"),
        "collection": result.get("collection"),
        "model": result.get("model"),
        "nickname": result.get("nickname"),
        "likely_references": likely_references,
        "confidence": result.get("confidence"),
        "alternatives": result.get("alternatives"),
        "match_type": result.get("match_type"),
    }
    return enriched


def identification_display_fields(identification: Record) -> list[dict[str, str]]:
    """Build labeled fields for UI display."""
    fields: list[dict[str, str]] = []
    for key, label in IDENTIFICATION_DISPLAY_FIELDS:
        value = identification.get(key)
        if value is None or value == "":
            continue
        if key == "confidence":
            fields.append({"label": label, "value": f"{float(value) * 100:.0f}%"})
            continue
        fields.append({"label": label, "value": str(value)})

    likely = identification.get("likely_references") or []
    if likely:
        fields.append({"label": "Likely references", "value": ", ".join(likely)})
    alternatives = identification.get("alternatives") or []
    if alternatives:
        alt_labels = []
        for alternative in alternatives[:3]:
            if isinstance(alternative, dict):
                label = alternative.get("nickname") or alternative.get("reference")
                if label:
                    alt_labels.append(str(label))
            elif alternative:
                alt_labels.append(str(alternative))
        if alt_labels:
            fields.append({"label": "Alternatives", "value": ", ".join(alt_labels)})
    return fields
