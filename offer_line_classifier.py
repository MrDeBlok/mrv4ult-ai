"""Classify dealer offer lines before watch record creation."""

from __future__ import annotations

import re
from typing import Any

WATCH_IDENTITY_LINE = "watch_identity"
PRICE_LINE = "price"
YEAR_SET_METADATA_LINE = "year_set_metadata"
CONDITION_LINE = "condition"
HEADER_LINE = "header"
UNKNOWN_LINE = "unknown"

GLUED_YEAR_FULLSET_PATTERN = re.compile(
    r"\b((?:19|20)\d{2})(full\s*set|fullset|watch\s+only|box\s+only|papers)\b",
    re.I,
)
YEAR_SET_METADATA_PATTERN = re.compile(
    r"^(?:\s*)?(?:19|20)\d{2}\s*(?:full\s*set|fullset|watch\s+only|box\s+only|papers)\b",
    re.I,
)
CURRENCY_COLON_PRICE_PATTERN = re.compile(
    r"^\s*(?:"
    r"usd|usdt|ustd|hkd|eur|euro|chf|gbp|sgd|aed|jpy|cny|rmb|krw"
    r")\s*:\s*[\d.,]+",
    re.I,
)

Record = dict[str, Any]


def normalize_glued_year_metadata(text: str) -> str:
    """Insert spacing between glued year tokens and set/condition metadata."""
    return GLUED_YEAR_FULLSET_PATTERN.sub(lambda match: f"{match.group(1)} {match.group(2)}", text)


def _extract_price(text: str) -> tuple[int | None, str | None]:
    from watch_parser import _extract_price

    return _extract_price(text)


def _extract_reference(text: str, *, brand_hint: str | None = None) -> tuple[str | None, str | None, bool]:
    from watch_parser import _extract_reference_for_identity

    return _extract_reference_for_identity(text, brand_hint=brand_hint)


def _extract_brand(text: str) -> str | None:
    from watch_parser import _extract_brand as extract_brand_from_text

    return extract_brand_from_text(text)


def _detect_wear_condition(text: str) -> str | None:
    from watch_parser import _detect_wear_condition

    return _detect_wear_condition(text)


def _is_brand_only_line(line: str) -> str | None:
    from watch_parser import _is_brand_only_line

    return _is_brand_only_line(line)


def _residual_after_price_mask(text: str) -> str:
    from watch_parser import _mask_price_spans

    masked = _mask_price_spans(text.strip())
    return re.sub(r"[^\w/]+", " ", masked).strip()


def _is_authoritative_reference_only_line(text: str, reference: str) -> bool:
    """Return True when the line is only an authoritative catalog reference."""
    stripped = text.strip()
    if not stripped:
        return False
    compact = re.sub(r"\s+", "", stripped).upper()
    ref_compact = re.sub(r"\s+", "", reference).upper()
    if compact != ref_compact:
        return False
    from watch_knowledge import lookup_reference

    knowledge = lookup_reference(reference)
    return bool(knowledge and knowledge.get("brand"))


def is_price_dominant_line(text: str) -> bool:
    """Return True when a line is primarily a currency/price statement."""
    if has_watch_identity_evidence(text, skip_brand_check=True):
        return False

    stripped = text.strip()
    if not stripped:
        return False
    if CURRENCY_COLON_PRICE_PATTERN.match(stripped):
        return True

    price, currency = _extract_price(stripped)
    if price is None:
        return False

    residual = _residual_after_price_mask(stripped)
    if not residual:
        return True

    tokens = [token for token in residual.split() if token]
    if len(tokens) == 1 and tokens[0].isdigit() and int(tokens[0]) == price:
        return True
    if currency and len(tokens) <= 2 and all(token.isdigit() for token in tokens if token.isdigit()):
        return True
    return False


def is_year_set_metadata_line(text: str) -> bool:
    """Return True for year/full-set metadata lines without watch identity."""
    if has_watch_identity_evidence(text, skip_brand_check=True):
        return False

    normalized = normalize_glued_year_metadata(text.strip())
    if not YEAR_SET_METADATA_PATTERN.match(normalized):
        return False
    return True


def is_condition_only_line(text: str) -> bool:
    """Return True when a line contains only wear-condition metadata."""
    if has_watch_identity_evidence(text, skip_brand_check=True):
        return False

    stripped = text.strip()
    if not stripped or _extract_brand(stripped) or _extract_reference(stripped)[0]:
        return False
    if _extract_price(stripped)[0] is not None:
        return False
    condition = _detect_wear_condition(stripped)
    if not condition:
        return False
    residual = stripped
    for pattern in (
        r"\bnew\b",
        r"\bused\b",
        r"\bnos\b",
        r"\bn\d{1,2}(?:/\d{2,4})?\b",
        r"\b(?:19|20)\d{2}\b",
    ):
        residual = re.sub(pattern, " ", residual, flags=re.I)
    residual = re.sub(r"[^\w]+", " ", residual).strip()
    return not residual


def has_watch_identity_evidence(
    text: str,
    *,
    brand_hint: str | None = None,
    skip_brand_check: bool = False,
) -> bool:
    """Return True when a line contains credible watch identity evidence."""
    reference, _ref_brand, _high_confidence = _extract_reference(text, brand_hint=brand_hint)
    if reference and not _numeric_reference_is_price_artifact(text, reference):
        return True

    if _is_authoritative_reference_only_line(text, text.strip()):
        return True

    if not skip_brand_check:
        explicit_brand = _extract_brand(text)
        if explicit_brand:
            from watch_parser import WATCH_MODEL_PATTERN

            if WATCH_MODEL_PATTERN.search(text):
                return True
            tokens = [token for token in re.split(r"\s+", text.strip()) if token]
            if len(tokens) >= 2:
                return True

    from rm_model_knowledge import extract_rm_reference

    if extract_rm_reference(text)[0]:
        return True

    price, _currency = _extract_price(text)
    if price is not None:
        residual = _residual_after_price_mask(text)
        if residual:
            tokens = [token for token in residual.split() if token]
            if brand_hint and tokens:
                return True
            if any(not token.isdigit() for token in tokens):
                return True

    return False


def _numeric_reference_is_price_artifact(text: str, reference: str) -> bool:
    if not reference.isdigit():
        return False
    if _is_authoritative_reference_only_line(text, reference):
        return False

    price, _currency = _extract_price(text)
    if price is not None and int(reference) != price:
        return False
    if price is not None and int(reference) == price:
        return True

    stripped = text.strip()
    if CURRENCY_COLON_PRICE_PATTERN.match(stripped):
        return True

    residual = _residual_after_price_mask(stripped)
    if not residual:
        return True
    tokens = [token for token in residual.split() if token]
    return len(tokens) == 1 and tokens[0] == reference


def classify_offer_line(line: str, *, brand_hint: str | None = None) -> str:
    """Classify one offer line before watch record creation."""
    stripped = line.strip()
    if not stripped:
        return UNKNOWN_LINE

    if _is_brand_only_line(stripped):
        return HEADER_LINE
    if is_price_dominant_line(stripped):
        return PRICE_LINE
    if is_year_set_metadata_line(stripped):
        return YEAR_SET_METADATA_LINE
    if is_condition_only_line(stripped):
        return CONDITION_LINE
    if has_watch_identity_evidence(stripped, brand_hint=brand_hint):
        return WATCH_IDENTITY_LINE
    return UNKNOWN_LINE


def line_attaches_to_previous_watch(line: str, *, brand_hint: str | None = None) -> bool:
    """Return True when a line should merge onto the preceding unresolved watch."""
    line_type = classify_offer_line(line, brand_hint=brand_hint)
    return line_type in {
        PRICE_LINE,
        YEAR_SET_METADATA_LINE,
        CONDITION_LINE,
        UNKNOWN_LINE,
    }


def line_starts_new_watch(
    line: str,
    previous_block: str,
    *,
    brand_hint: str | None = None,
) -> bool:
    """Return True when a line begins a new watch block."""
    if classify_offer_line(line, brand_hint=brand_hint) != WATCH_IDENTITY_LINE:
        return False

    previous_reference = _extract_reference(previous_block, brand_hint=brand_hint)[0]
    line_reference = _extract_reference(line, brand_hint=brand_hint)[0]
    if line_reference:
        if not previous_reference:
            return True
        return line_reference != previous_reference

    line_brand = _extract_brand(line)
    block_brand = _extract_brand(previous_block) or brand_hint
    if line_brand and block_brand and line_brand != block_brand:
        return True
    return False
