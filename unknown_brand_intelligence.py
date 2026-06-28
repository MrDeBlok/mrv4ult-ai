"""Unknown brand detection and recording during imports."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

Record = dict[str, Any]

UNKNOWN_BRAND_CONFIDENCE_THRESHOLD = 0.55

UNKNOWN_BRAND_STOP_WORDS = frozenset(
    word.lower()
    for word in (
        # English function words
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "have",
        "has",
        "was",
        "are",
        "but",
        "not",
        "you",
        "your",
        "his",
        "her",
        "she",
        "said",
        "want",
        "just",
        "like",
        "get",
        "got",
        "one",
        "all",
        "can",
        "will",
        "would",
        "could",
        "should",
        "about",
        "into",
        "over",
        "after",
        "before",
        "when",
        "where",
        "why",
        "how",
        "who",
        "what",
        "which",
        "their",
        "they",
        "them",
        "then",
        "than",
        "there",
        "here",
        "some",
        "any",
        "very",
        "also",
        "been",
        "being",
        "did",
        "does",
        "doing",
        # Dutch function words
        "ik",
        "en",
        "die",
        "is",
        "hij",
        "zei",
        "las",
        "uur",
        "het",
        "de",
        "een",
        "van",
        "met",
        "voor",
        "naar",
        "op",
        "te",
        "dat",
        "dit",
        "zijn",
        "ben",
        "bij",
        "om",
        "als",
        "maar",
        "nog",
        "wel",
        "niet",
        "geen",
        "kan",
        "moet",
        "heb",
        "heeft",
        "had",
        "wordt",
        "worden",
        "meer",
        "veel",
        "alle",
        "ook",
        "dan",
        "dus",
        "al",
        "er",
        "zo",
        "wie",
        "waar",
        "toch",
        "gewoon",
        "even",
        "misschien",
        # Conditions and generic watch terms
        "new",
        "used",
        "full",
        "set",
        "watch",
        "only",
        "box",
        "papers",
        "bnib",
        "nos",
        "mint",
        "unworn",
        "complete",
        "stickered",
        "fullset",
        "fs",
        "obo",
        "best",
        "price",
        "offer",
        "sale",
        "available",
        "stock",
        "piece",
        "pcs",
        "sport",
        "gmt",
        "chrono",
        "chronograph",
        "automatic",
        "manual",
        "quartz",
        "dial",
        "steel",
        "gold",
        "yellow",
        "white",
        "rose",
        "ceramic",
        "titanium",
        "platinum",
        "carat",
        "ref",
        "reference",
        "model",
        "bracelet",
        "jubilee",
        "oyster",
        "rubber",
        "leather",
        "black",
        "blue",
        "green",
        "grey",
        "gray",
        "silver",
        "nickel",
        "sapphire",
        # Currencies
        "usd",
        "hkd",
        "eur",
        "chf",
        "gbp",
        "sgd",
        "aed",
        "jpy",
    )
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9&\.\-]*")
_NUMBER_PATTERN = re.compile(r"^\d+$")
_YEAR_PATTERN = re.compile(r"^(19|20)\d{2}[y]?$")
_PRICE_PATTERN = re.compile(r"^\d+(?:[.,]\d+)?[kKmM]?$")
_CURRENCY_PATTERN = re.compile(r"^(usd|hkd|eur|chf|gbp|sgd|aed|jpy|k)$", re.I)


def normalize_unknown_brand_text(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def watch_has_parse_signal(watch: Record) -> bool:
    return bool(
        watch.get("reference")
        or watch.get("model")
        or watch.get("original_price")
        or watch.get("price")
        or watch.get("usd_price")
    )


def is_proper_noun_shape(token: str) -> bool:
    """Return True for Title Case, CamelCase, or all-uppercase tokens."""
    if len(token) < 3:
        return False
    if token.isupper():
        return True
    if token[0].isupper() and any(ch.isalpha() for ch in token):
        return True
    return False


def is_valid_brand_token(token: str, watch: Record) -> bool:
    normalized = token.lower()
    if len(normalized) < 3:
        return False
    if normalized in UNKNOWN_BRAND_STOP_WORDS:
        return False
    if _NUMBER_PATTERN.match(normalized):
        return False
    if _YEAR_PATTERN.match(normalized):
        return False
    if _PRICE_PATTERN.match(normalized):
        return False
    if _CURRENCY_PATTERN.match(normalized):
        return False
    if not is_proper_noun_shape(token):
        return False

    reference = watch.get("reference")
    if reference:
        reference_normalized = str(reference).lower().replace("-", "").replace("/", "")
        if normalized == reference_normalized:
            return False
    return True


def score_unknown_brand_candidate(text: str, watch: Record, *, position: int) -> float:
    """Score how likely a token group is to be an unknown watch brand."""
    tokens = text.split()
    if not tokens:
        return 0.0

    score = 0.25
    if position == 0:
        score += 0.2
    if len(tokens) >= 2:
        score += 0.2
    if all(len(token) >= 4 for token in tokens):
        score += 0.15
    elif all(len(token) >= 3 for token in tokens):
        score += 0.1
    if all(is_proper_noun_shape(token) for token in tokens):
        score += 0.15
    if watch.get("reference"):
        score += 0.05
    if watch.get("original_price") or watch.get("price") or watch.get("usd_price"):
        score += 0.05
    return min(score, 1.0)


def extract_unknown_brand_candidate(watch: Record) -> tuple[str, float] | None:
    """Return the best unknown brand candidate and confidence score."""
    if watch.get("brand"):
        return None
    if not watch_has_parse_signal(watch):
        return None

    source_line = (watch.get("source_line") or "").strip()
    if not source_line:
        return None

    tokens = _TOKEN_PATTERN.findall(source_line)
    if not tokens:
        return None

    best_text: str | None = None
    best_score = 0.0
    index = 0
    while index < len(tokens):
        if not is_valid_brand_token(tokens[index], watch):
            index += 1
            continue

        collected = [tokens[index]]
        next_index = index + 1
        while next_index < len(tokens) and is_valid_brand_token(tokens[next_index], watch):
            collected.append(tokens[next_index])
            next_index += 1
            if len(collected) >= 2:
                break

        candidate = " ".join(collected)
        score = score_unknown_brand_candidate(candidate, watch, position=index)
        if score > best_score:
            best_text = candidate
            best_score = score
        index = next_index if next_index > index + 1 else index + 1

    if not best_text:
        return None
    return best_text, best_score


def extract_unknown_brand_text(watch: Record) -> str | None:
    """Extract likely unknown brand text from a parsed watch without a brand."""
    candidate = extract_unknown_brand_candidate(watch)
    if candidate is None:
        return None

    text, confidence = candidate
    if confidence < UNKNOWN_BRAND_CONFIDENCE_THRESHOLD:
        return None
    return text


def record_unknown_brands_for_watches(
    watches: list[Record],
    *,
    example_message: str,
    dealer_id: str | None,
    seen_at: datetime | None = None,
) -> list[Record]:
    """Persist unknown brand sightings for watches missing brand recognition."""
    try:
        from database import record_unknown_brand_sighting, watch_knowledge_supported
    except ImportError:  # pragma: no cover
        return []

    if not watch_knowledge_supported():
        return []

    recorded: list[Record] = []
    seen: set[str] = set()
    for watch in watches:
        candidate = extract_unknown_brand_candidate(watch)
        if candidate is None:
            continue
        detected_text, confidence = candidate
        if confidence < UNKNOWN_BRAND_CONFIDENCE_THRESHOLD:
            continue
        normalized = normalize_unknown_brand_text(detected_text)
        if normalized in seen:
            continue
        seen.add(normalized)
        row = record_unknown_brand_sighting(
            detected_text=detected_text,
            example_message=example_message,
            dealer_id=dealer_id,
            seen_at=seen_at,
        )
        if row:
            recorded.append(row)
    return recorded
