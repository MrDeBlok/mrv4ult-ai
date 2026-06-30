"""Quick fix helpers for Needs Review notifications."""

from __future__ import annotations

import re
from typing import Any

from brand_registry import get_brand_pattern, invalidate_brand_registry_cache, lookup_brand
from parser_review import _parsed_watches, detect_import_issues
from unknown_brand_intelligence import extract_unknown_brand_text

Record = dict[str, Any]


def detect_likely_reference_token(text: str, *, brand: str | None = None) -> str | None:
    """Detect a likely watch reference token from message preview text."""
    from watch_parser import _extract_price, _extract_reference, _mask_price_spans

    cleaned = (text or "").strip()
    if not cleaned:
        return None

    reference, _ = _extract_reference(cleaned, brand_hint=brand)
    if reference:
        return reference

    price, _ = _extract_price(cleaned)
    search_text = _mask_price_spans(cleaned)
    if brand:
        search_text = re.sub(re.escape(brand), " ", search_text, flags=re.I)

    for match in re.finditer(r"\b(\d{3,6}[A-Za-z]{0,4})\b", search_text):
        token = match.group(1).upper()
        digits_match = re.match(r"(\d+)", token)
        if not digits_match:
            continue
        digits = digits_match.group(1)
        if price is not None and digits.isdigit() and int(digits) == int(price):
            continue
        if len(digits) == 4 and digits.isdigit() and 1990 <= int(digits) <= 2035:
            continue
        return token
    return None


def build_quick_fix_prefill(
    import_log: Record,
    *,
    message_preview: str | None = None,
) -> Record:
    """Build default Quick fix form values from a linked import log."""
    watches = _parsed_watches(import_log)
    watch = watches[0] if watches else {}

    brand = str(watch.get("brand") or "").strip()
    reference = str(watch.get("reference") or "").strip()
    alias_text = str(extract_unknown_brand_text(watch) or "").strip()

    preview = (message_preview or "").strip()
    if not brand and preview:
        brand_match = get_brand_pattern().search(preview)
        if brand_match:
            brand = lookup_brand(brand_match.group(1)) or brand

    if not reference and preview:
        reference = detect_likely_reference_token(preview, brand=brand or None) or ""

    return {
        "brand": brand,
        "reference": reference,
        "alias_text": alias_text,
    }


def build_quick_fix_prefills(
    notifications: list[Record],
    *,
    import_logs_by_id: dict[str, Record],
    message_previews_by_import_log_id: dict[str, str],
) -> dict[str, Record]:
    """Build Quick fix prefills keyed by notification id."""
    prefills: dict[str, Record] = {}
    for notification in notifications:
        if notification.get("type") != "needs_review":
            continue
        import_log_id = str(notification.get("related_import_log_id") or "")
        import_log = import_logs_by_id.get(import_log_id)
        if import_log is None:
            continue
        preview = message_previews_by_import_log_id.get(import_log_id)
        prefills[str(notification["id"])] = build_quick_fix_prefill(
            import_log,
            message_preview=preview,
        )
    return prefills


def teach_watch_mapping_from_quick_fix(
    *,
    brand_name: str,
    reference: str,
    alias_text: str = "",
    unknown_brand_text: str | None = None,
) -> Record:
    """Save Quick fix knowledge using the existing Teach AI alias flows."""
    from database import (
        create_brand_alias,
        create_nickname_alias,
        watch_identification_supported,
        watch_knowledge_supported,
    )

    brand = brand_name.strip()
    ref = reference.strip().upper()
    if not brand:
        raise ValueError("Brand is required")
    if not ref:
        raise ValueError("Reference is required")

    alias_key = alias_text.strip() or ref
    saved: Record = {"brand_name": brand, "reference": ref, "alias_key": alias_key}

    if watch_identification_supported():
        saved["nickname_alias"] = create_nickname_alias(
            alias_key=alias_key,
            brand_name=brand,
            likely_references=[ref],
            source="notification_quick_fix",
        )
        invalidate_identifier_cache()

    if (
        watch_knowledge_supported()
        and unknown_brand_text
        and alias_text.strip()
        and alias_text.strip().lower() == unknown_brand_text.strip().lower()
    ):
        saved["brand_alias"] = create_brand_alias(
            alias_key=alias_text.strip(),
            brand_name=brand,
            source="notification_quick_fix",
        )
        invalidate_brand_registry_cache()

    return saved


def apply_notification_quick_fix(
    *,
    import_log_id: str,
    brand_name: str,
    reference: str,
    alias_text: str = "",
) -> Record:
    """Teach missing watch knowledge and mark the related import as reviewed."""
    from database import get_import_log, mark_import_parser_reviewed

    import_log = get_import_log(import_log_id)
    if import_log is None:
        raise ValueError("Import log not found")

    _, _, unknown_brand_text = detect_import_issues(import_log)
    result = teach_watch_mapping_from_quick_fix(
        brand_name=brand_name,
        reference=reference,
        alias_text=alias_text,
        unknown_brand_text=unknown_brand_text,
    )
    result["import_log"] = mark_import_parser_reviewed(import_log_id)
    return result


def invalidate_identifier_cache() -> None:
    from watch_identifier import invalidate_identifier_cache as _invalidate

    _invalidate()
