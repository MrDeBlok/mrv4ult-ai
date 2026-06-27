"""Regex-based watch message parser for MRV4ULT AI (no AI/API)."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

WatchDict = dict[str, Any]
ParseResult = dict[str, Any]

BRAND_ALIASES: dict[str, str] = {
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
    "f.p. journe": "F.P. Journe",
    "f p journe": "F.P. Journe",
    "als": "A. Lange & Söhne",
    "a lange": "A. Lange & Söhne",
    "a lange & sohne": "A. Lange & Söhne",
    "a. lange & sohne": "A. Lange & Söhne",
    "lange": "A. Lange & Söhne",
}

SUPPORTED_BRANDS = frozenset(BRAND_ALIASES.values())

DIAL_ABBREVIATIONS: dict[str, str] = {
    "champ": "Champagne",
    "wim": "Wimbledon",
    "tiff": "Tiffany",
    "olive": "Olive",
    "blue": "Blue",
    "green": "Green",
    "black": "Black",
    "white": "White",
    "purple": "Purple",
    "grey": "Grey",
    "gray": "Grey",
    "rhodium": "Rhodium",
    "salmon": "Salmon",
}

DIAL_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(key) for key in DIAL_ABBREVIATIONS) + r")\b",
    re.I,
)

DIAL_COLORS = (
    "champagne",
    "wimbledon",
    "tiffany",
    "olive",
    "grey",
    "gray",
    "colour",
    "color",
    "rhodium",
    "salmon",
    "silver",
    "meteorite",
    "brown",
    "rose",
    "pink",
    "yellow",
    "ivory",
    "cream",
    "slate",
    "navy",
    "turquoise",
    "skeleton",
    "smoke",
    "sand",
    "blue",
    "green",
    "black",
    "white",
    "purple",
)

BRACELET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bjub(?:ilee)?\b", re.I), "jubilee"),
    (re.compile(r"\boys(?:ter)?\b", re.I), "oyster"),
    (re.compile(r"\brubber\b", re.I), "rubber"),
    (re.compile(r"\bleather\b", re.I), "leather"),
]

CONDITION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bunworn\s+complete\b", re.I), "unworn complete"),
    (re.compile(r"\bbox\s+and\s+papers\b", re.I), "full set"),
    (re.compile(r"\bfull\s+set\b", re.I), "full set"),
    (re.compile(r"\bwatch\s+only\b", re.I), "watch only"),
    (re.compile(r"\bbox\s+only\b", re.I), "box only"),
    (re.compile(r"\bpapers?\s+only\b", re.I), "papers only"),
    (re.compile(r"\bwith\s+papers\b", re.I), "papers"),
    (re.compile(r"\bpapers\b", re.I), "papers"),
    (re.compile(r"\bcomplete\b", re.I), "complete"),
    (re.compile(r"\bstickered\b", re.I), "stickered"),
    (re.compile(r"\bunworn\b", re.I), "unworn"),
    (re.compile(r"\bbnib\b", re.I), "bnib"),
    (re.compile(r"\bnos\b", re.I), "nos"),
    (re.compile(r"\bmint\b", re.I), "mint"),
    (re.compile(r"\blnib\b", re.I), "lnib"),
    (re.compile(r"\bworn\b", re.I), "worn"),
]

REQUEST_PATTERN = re.compile(
    r"\b(wtb|looking\s+for|lf\b|iso\b|need\s+(?:a\s+)?(?:rolex|patek|ap|rm|watch))\b",
    re.I,
)
OFFER_PATTERN = re.compile(r"\b(fs|for\s+sale|asking|avail(?:able)?|stock)\b", re.I)
HEADER_PATTERN = re.compile(r"^(?:fs|for\s+sale|stock|available|offers?)[\s:.-]*$", re.I)

NEW_CARD_DATE_PATTERN = re.compile(r"\bn(\d{1,2})/(\d{2})\b", re.I)
NEW_CARD_DATE_MMYyyy_PATTERN = re.compile(r"\bnew\s+(\d{1,2})/(\d{4})\b", re.I)
CARD_MMYyyy_PATTERN = re.compile(r"\b(\d{1,2})/(\d{4})\b")
USED_YEAR_PATTERN = re.compile(r"\bused\s+(\d{4})y\b", re.I)
YEAR_SUFFIX_PATTERN = re.compile(r"\b(19|20)\d{2}\s*y\b", re.I)
STANDALONE_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")

BRAND_PATTERN = re.compile(
    r"\b("
    r"rolex|rlx|patek(?:\s+philippe)?|pp|audemars(?:\s+piguet)?|ap|"
    r"vacheron(?:\s+constantin)?|vc|"
    r"a\.?\s*lange(?:\s*&\s*s[öo]hne)?|lange|"
    r"richard\s+mille|rm|fp\s*journe|fpj|f\.?\s*p\.?\s*journe"
    r")\b",
    re.I,
)

ALS_UPPER_PATTERN = re.compile(r"\bALS\b")
ALS_CONTEXT_TERMS = re.compile(
    r"\b(?:lange|saxonia|datograph|zeitwerk|odysseus|1815|chrono(?:graph)?)\b",
    re.I,
)

DIAL_PATTERN = re.compile(
    r"\b(" + "|".join(DIAL_COLORS) + r")\b(?:\s+(?:dial|colour|color))?",
    re.I,
)

REFERENCE_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"\b(RM\s?\d{2,3}(?:[-\s/]\d{2,3})?)\b", re.I), "Richard Mille"),
    (re.compile(r"\b(\d{4}/[0-9A-Z]+)\b", re.I), "Patek Philippe"),
    (re.compile(r"\b(\d{4}[A-Za-z]-\d{3,})\b", re.I), "Patek Philippe"),
    (re.compile(r"\b([12]\d{5}[A-Za-z]{0,4})\b", re.I), "Rolex"),
    (re.compile(r"\b(\d{5}[A-Za-z]{2,4})\b", re.I), "Audemars Piguet"),
    (re.compile(r"\b(\d{4}[A-Za-z])\b", re.I), None),
    (re.compile(r"\b([3456]\d{3})\b", re.I), "Patek Philippe"),
    (re.compile(r"\b(\d{5})\b", re.I), None),
]

SUPPORTED_CURRENCIES = frozenset({"USD", "HKD", "EUR", "CHF", "GBP", "SGD", "AED", "JPY"})

EXCHANGE_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "HKD": 0.128,
    "EUR": 1.08,
    "CHF": 1.12,
    "GBP": 1.27,
    "SGD": 0.74,
    "AED": 0.272,
    "JPY": 0.0064,
}

CURRENCY_CODE_PATTERN = r"usd|hkd|eur|chf|gbp|sgd|aed|jpy"

FPJOURNE_REF_PATTERN = re.compile(
    r"\b(CB|CST|RS|CBPT|Tourbillon\s+Souverain|Chronom[eè]tre\s+Bleu|Octa)\b",
    re.I,
)

WATCH_MODEL_PATTERN = re.compile(
    r"\b("
    r"GMT(?:-Master(?:\s+II)?)?|Submariner|Daytona|Datejust|Day-Date|Explorer|"
    r"Yacht-Master|Sea-Dweller|Sky-Dweller|Milgauss|Air-King|Nautilus|Aquanaut|"
    r"Royal Oak|Overseas|World Time|Speedmaster|Reverso"
    r")\b",
    re.I,
)

NICKNAME_STOP_WORDS = frozenset(
    word.lower()
    for word in (
        *DIAL_COLORS,
        *DIAL_ABBREVIATIONS.keys(),
        "new",
        "used",
        "full",
        "set",
        "watch",
        "only",
        "box",
        "papers",
        "jub",
        "oys",
        "oyster",
        "jubilee",
        "unworn",
        "mint",
        "bnib",
        "nos",
        "stickered",
        "complete",
        "deal",
        "bh",
    )
)

CURRENCY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"HK\$", re.I), "HKD"),
    (re.compile(r"S\$", re.I), "SGD"),
    (re.compile(r"¥"), "JPY"),
    (re.compile(r"€"), "EUR"),
    (re.compile(r"\beur\b", re.I), "EUR"),
    (re.compile(r"\$"), "USD"),
    (re.compile(r"\busd\b", re.I), "USD"),
    (re.compile(r"£"), "GBP"),
    (re.compile(r"\bgbp\b", re.I), "GBP"),
    (re.compile(r"\bchf\b", re.I), "CHF"),
    (re.compile(r"\bhkd\b", re.I), "HKD"),
    (re.compile(r"\bsgd\b", re.I), "SGD"),
    (re.compile(r"\baed\b", re.I), "AED"),
    (re.compile(r"\bjpy\b", re.I), "JPY"),
]

PRICE_WITH_CURRENCY_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"HK\$\s*([\d.,]+)\s*(k|K|m|M)?", re.I), "HKD"),
    (re.compile(r"S\$\s*([\d.,]+)\s*(k|K|m|M)?", re.I), "SGD"),
    (re.compile(r"€\s*([\d.,]+)\s*(k|K|m|M)?"), "EUR"),
    (re.compile(r"£\s*([\d.,]+)\s*(k|K|m|M)?"), "GBP"),
    (re.compile(r"¥\s*([\d.,]+)\s*(k|K|m|M)?"), "JPY"),
    (re.compile(r"\$\s*([\d.,]+)\s*(k|K|m|M)?"), "USD"),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*€"), "EUR"),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*\$"), "USD"),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*£"), "GBP"),
    (
        re.compile(
            rf"\b({CURRENCY_CODE_PATTERN})\s*([\d.,]+)\s*(k|K|m|M)?\b",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            rf"\b([\d.,]+)\s*(k|K|m|M)\s*({CURRENCY_CODE_PATTERN})\b",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            rf"\b([\d.,]+)\s*(k|K|m|M)?\s*({CURRENCY_CODE_PATTERN})\b",
            re.I,
        ),
        None,
    ),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*(m|M)\b"), None),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*(k|K)\b"), None),
]

BULLET_PREFIX = re.compile(r"^[-*•]\s*")
NUMBER_PREFIX = re.compile(r"^\d+[\.)]\s*")

NOTES_REMOVE_PATTERNS = [
    NEW_CARD_DATE_PATTERN,
    NEW_CARD_DATE_MMYyyy_PATTERN,
    CARD_MMYyyy_PATTERN,
    USED_YEAR_PATTERN,
    YEAR_SUFFIX_PATTERN,
    BRAND_PATTERN,
    DIAL_ABBREV_PATTERN,
    DIAL_PATTERN,
    re.compile(r"\b(fs|for\s+sale|obo)\b", re.I),
    re.compile(r"\b(jub(?:ilee)?|oys(?:ter)?|rubber|leather)\b", re.I),
    re.compile(
        r"\b(watch\s+only|full\s+set|box\s+only|papers?\s+only|with\s+papers|papers|"
        r"complete|stickered|unworn|bnib|nos|mint|lnib|worn)\b",
        re.I,
    ),
]
for pattern, _ in PRICE_WITH_CURRENCY_PATTERNS:
    NOTES_REMOVE_PATTERNS.append(pattern)
for pattern, _ in REFERENCE_PATTERNS:
    NOTES_REMOVE_PATTERNS.append(pattern)
NOTES_REMOVE_PATTERNS.append(FPJOURNE_REF_PATTERN)


def empty_watch() -> WatchDict:
    return {
        "brand": None,
        "reference": None,
        "model": None,
        "nickname": None,
        "dial": None,
        "bracelet": None,
        "condition": None,
        "price": None,
        "currency": None,
        "original_price": None,
        "original_currency": None,
        "usd_price": None,
        "exchange_rate_to_usd": None,
        "production_year": None,
        "card_date": None,
        "full_set": None,
        "watch_only": None,
        "box_only": None,
        "papers": None,
        "notes": None,
        "confidence": 0,
    }


def parse_message(message: str) -> ParseResult:
    """Parse a raw WhatsApp message into structured JSON."""
    text = message.strip()
    if not text:
        return {"message_type": "unknown", "watches": []}

    is_request = bool(REQUEST_PATTERN.search(text))
    current_brand: str | None = None
    watches: list[WatchDict] = []

    blocks, header_brand = _group_offer_lines(iter_content_lines(text))
    current_brand = header_brand

    for line in blocks:
        if watch := parse_watch_line(line, current_brand=current_brand):
            watches.append(watch)

    message_type = classify_message(text, watches, is_request)
    if message_type == "request" and watches:
        watches = watches[:1]
    elif message_type == "offer" and len(watches) > 1:
        message_type = "offer_list"

    return {"message_type": message_type, "watches": watches}


def classify_message(text: str, watches: list[WatchDict], is_request: bool) -> str:
    if not watches:
        return "unknown"
    if is_request and not OFFER_PATTERN.search(text):
        return "request"
    if len(watches) >= 2:
        return "offer_list"
    if len(watches) == 1:
        return "offer"
    return "unknown"


def _group_offer_lines(lines: list[str]) -> tuple[list[str], str | None]:
    """Merge continuation lines into single offer blocks."""
    blocks: list[str] = []
    current_brand: str | None = None

    for line in lines:
        if brand_only := _is_brand_only_line(line):
            current_brand = brand_only
            continue

        if not blocks:
            if _line_begins_offer(line):
                blocks.append(line)
            continue

        if _starts_new_watch_block(line, blocks[-1]):
            blocks.append(line)
        elif _is_continuation_line(line, blocks[-1]):
            blocks[-1] = f"{blocks[-1]}\n{line}"
        elif _looks_like_watch_line(line):
            blocks.append(line)

    return blocks, current_brand


def _line_begins_offer(line: str) -> bool:
    if _extract_reference(line)[0]:
        return True
    if _extract_brand(line) and len(line.split()) >= 2:
        return True
    return _looks_like_watch_line(line)


def _starts_new_watch_block(line: str, previous_block: str) -> bool:
    reference = _extract_reference(line)[0]
    if not reference:
        return False
    previous_reference = _extract_reference(previous_block)[0]
    if not previous_reference:
        return True
    return reference != previous_reference


def _is_continuation_line(line: str, previous_block: str) -> bool:
    reference = _extract_reference(line)[0]
    previous_reference = _extract_reference(previous_block)[0]

    if reference and previous_reference:
        return reference == previous_reference
    if reference and not previous_reference:
        return False

    return _is_continuation_content(line)


def _is_continuation_content(line: str) -> bool:
    if _extract_price(line)[0] is not None:
        return True
    if NEW_CARD_DATE_MMYyyy_PATTERN.search(line):
        return True
    if NEW_CARD_DATE_PATTERN.search(line):
        return True
    if re.search(r"\bnew\b", line, re.I) and CARD_MMYyyy_PATTERN.search(line):
        return True
    if USED_YEAR_PATTERN.search(line):
        return True
    if _extract_condition(line):
        return True
    if re.search(
        r"\bfull\s+set\b|\bwatch\s+only\b|\bbox\s+only\b|\bpapers\b|\bbh\s+deal\b",
        line,
        re.I,
    ):
        return True
    if not _extract_brand(line) and not _extract_reference(line)[0]:
        stripped = line.strip()
        if stripped and len(stripped.split()) <= 10:
            return True
    return False


def iter_content_lines(message: str) -> list[str]:
    """Return all non-header content lines from a message."""
    lines: list[str] = []
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = BULLET_PREFIX.sub("", line)
        line = NUMBER_PREFIX.sub("", line)
        if HEADER_PATTERN.match(line):
            continue
        for part in re.split(r"\s*;\s*", line):
            part = part.strip()
            if part:
                lines.append(part)
    return lines


def _is_brand_only_line(line: str) -> str | None:
    """Return brand name if the line contains only a brand header."""
    brand = _extract_brand(line)
    if brand is None:
        return None
    remaining = BRAND_PATTERN.sub("", line).strip(" :.-")
    if remaining:
        return None
    return brand


def _looks_like_watch_line(line: str) -> bool:
    if _is_brand_only_line(line):
        return False
    if len(line) < 4:
        return False
    if _extract_reference(line)[0]:
        return True
    if _extract_price(line)[0] is not None:
        return True
    if _extract_brand(line):
        return True
    if REQUEST_PATTERN.search(line) and (_extract_brand(line) or _extract_reference(line)[0]):
        return True
    return False


def parse_watch_line(line: str, current_brand: str | None = None) -> WatchDict | None:
    """Parse a single watch line into a structured watch dict."""
    text = line.strip()
    if not text:
        return None

    watch = empty_watch()

    brand = _extract_brand(text)
    if brand is None:
        brand = current_brand
    reference, ref_brand = _extract_reference(text, brand_hint=brand)
    if brand is None and ref_brand:
        brand = ref_brand
    if brand is None and reference and ref_brand is None:
        brand = _infer_brand_from_reference(reference)
    watch["brand"] = brand
    watch["reference"] = reference

    if brand == "F.P. Journe" and watch["reference"] is None:
        fpj_match = FPJOURNE_REF_PATTERN.search(text)
        if fpj_match:
            watch["reference"] = fpj_match.group(1)

    watch["model"] = _extract_model(text)
    watch["nickname"] = _extract_nickname(text, watch.get("reference"))
    watch["dial"] = _extract_dial(text)
    watch["bracelet"] = _extract_bracelet(text)
    _apply_accessory_fields(watch, text)

    card_date, new_condition = _extract_card_date(text)
    watch["card_date"] = card_date
    if new_condition:
        watch["condition"] = new_condition
    else:
        used_condition, production_year = _extract_used_year(text)
        if used_condition:
            watch["condition"] = used_condition
            watch["production_year"] = production_year
        else:
            watch["condition"] = _extract_condition(text)
            watch["production_year"] = _extract_standalone_year(text, watch)

    _apply_price_fields(watch, text)
    watch["notes"] = _extract_dealer_notes(text, watch) or _extract_notes(text, watch)
    watch["confidence"] = _compute_confidence(watch)
    return watch


def _extract_model(text: str) -> str | None:
    match = WATCH_MODEL_PATTERN.search(text)
    if not match:
        return None
    model = match.group(1)
    if model.upper().startswith("GMT"):
        return "GMT"
    return model.title() if model.islower() else model


def _extract_nickname(text: str, reference: str | None) -> str | None:
    if not reference:
        return None
    pattern = re.compile(rf"{re.escape(reference)}\s+([A-Za-z][A-Za-z0-9-]*)", re.I)
    match = pattern.search(text)
    if not match:
        return None
    word = match.group(1)
    if word.lower() in NICKNAME_STOP_WORDS:
        return None
    return word.lower()


def _extract_dealer_notes(text: str, watch: WatchDict) -> str | None:
    if "\n" not in text:
        return None

    fragments: list[str] = []
    for line in text.splitlines():
        fragment = _line_dealer_note_fragment(line.strip(), watch)
        if fragment:
            fragments.append(fragment)
    if not fragments:
        return None
    return " ".join(fragments)


def _line_dealer_note_fragment(line: str, watch: WatchDict) -> str | None:
    if not line:
        return None

    remaining = line
    for pattern, _ in PRICE_WITH_CURRENCY_PATTERNS:
        remaining = pattern.sub(" ", remaining)
    remaining = BRAND_PATTERN.sub(" ", remaining)
    remaining = WATCH_MODEL_PATTERN.sub(" ", remaining)
    if watch.get("reference"):
        remaining = re.sub(re.escape(watch["reference"]), " ", remaining, flags=re.I)
    if watch.get("nickname"):
        remaining = re.sub(
            rf"\b{re.escape(watch['nickname'])}\b",
            " ",
            remaining,
            flags=re.I,
        )
    remaining = NEW_CARD_DATE_MMYyyy_PATTERN.sub(" ", remaining)
    remaining = NEW_CARD_DATE_PATTERN.sub(" ", remaining)
    remaining = CARD_MMYyyy_PATTERN.sub(" ", remaining)
    remaining = re.sub(r"\bnew\b", " ", remaining, flags=re.I)
    remaining = DIAL_PATTERN.sub(" ", remaining)
    remaining = DIAL_ABBREV_PATTERN.sub(" ", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip(" ,.-")
    if not remaining or len(remaining) < 2:
        return None
    return remaining


def _apply_accessory_fields(watch: WatchDict, text: str) -> None:
    lowered = text.lower()
    watch["full_set"] = bool(re.search(r"\bfull\s+set\b|\bbox\s+and\s+papers\b", lowered))
    watch["watch_only"] = bool(re.search(r"\bwatch\s+only\b", lowered))
    watch["box_only"] = bool(re.search(r"\bbox\s+only\b", lowered))
    watch["papers"] = bool(
        re.search(r"\b(?:with\s+)?papers\b|\bpapers?\s+only\b", lowered)
        and not watch["full_set"]
    )


def _compute_confidence(watch: WatchDict) -> int:
    score = 0
    if watch.get("brand"):
        score += 20
    if watch.get("reference"):
        score += 25
    if watch.get("original_price") is not None or watch.get("price") is not None:
        score += 20
    if watch.get("dial"):
        score += 10
    if watch.get("condition"):
        score += 10
    if watch.get("production_year") is not None or watch.get("card_date"):
        score += 10
    if watch.get("bracelet"):
        score += 5
    return min(score, 100)


def _apply_price_fields(watch: WatchDict, text: str) -> None:
    original_price, original_currency = _extract_price(text)
    if original_currency is None:
        original_currency = _extract_currency(text)

    watch["original_price"] = original_price
    watch["original_currency"] = original_currency
    watch["price"] = original_price
    watch["currency"] = original_currency

    if original_price is None or original_currency is None:
        watch["usd_price"] = None
        watch["exchange_rate_to_usd"] = None
        return

    rate = EXCHANGE_RATES_TO_USD.get(original_currency)
    watch["exchange_rate_to_usd"] = rate
    watch["usd_price"] = int(round(original_price * rate)) if rate is not None else None


def _normalize_brand_alias(alias: str) -> str:
    cleaned = alias.lower().replace(".", "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("ö", "o")
    return cleaned


def _extract_brand(text: str) -> str | None:
    match = BRAND_PATTERN.search(text)
    if match:
        alias = _normalize_brand_alias(match.group(1))
        brand = BRAND_ALIASES.get(alias)
        if brand:
            return brand
    return _extract_als_brand(text)


def _extract_als_brand(text: str) -> str | None:
    """Detect ALS as A. Lange & Söhne without matching Dutch 'als'."""
    if ALS_UPPER_PATTERN.search(text):
        return BRAND_ALIASES["als"]

    if not re.search(r"\bals\b", text):
        return None

    if _extract_reference(text)[0]:
        return BRAND_ALIASES["als"]
    if ALS_CONTEXT_TERMS.search(text):
        return BRAND_ALIASES["als"]
    if _extract_price(text)[0] is not None:
        return BRAND_ALIASES["als"]
    return None


def _infer_brand_from_reference(reference: str) -> str | None:
    normalized = reference.upper().replace(" ", "")
    if normalized.startswith("RM"):
        return "Richard Mille"
    if "/" in normalized:
        return "Patek Philippe"
    if re.fullmatch(r"[12]\d{5}[A-Z]{0,4}", normalized):
        return "Rolex"
    if re.fullmatch(r"\d{5}[A-Z]{2,4}", normalized):
        return "Audemars Piguet"
    if re.fullmatch(r"\d{4}[A-Z]", normalized):
        return "Audemars Piguet"
    if re.fullmatch(r"[3456]\d{3}", normalized):
        return "Patek Philippe"
    if re.fullmatch(r"\d{5}", normalized):
        return "Audemars Piguet"
    return None


def _mask_price_spans(text: str) -> str:
    """Remove price segments so numeric references are not confused with prices."""
    masked = text
    for pattern, _ in PRICE_WITH_CURRENCY_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def _extract_reference(
    text: str,
    *,
    brand_hint: str | None = None,
) -> tuple[str | None, str | None]:
    ref_text = _mask_price_spans(text)
    price, _ = _extract_price(text)
    for pattern, brand_hint_from_pattern in REFERENCE_PATTERNS:
        match = pattern.search(ref_text)
        if not match:
            continue
        reference = match.group(1).upper().replace("  ", " ").strip()
        if price is not None and reference.isdigit() and int(reference) == price:
            continue
        if _looks_like_year(reference):
            continue
        brand = brand_hint_from_pattern or _infer_brand_from_reference(reference)
        if brand is None and brand_hint:
            brand = brand_hint
        return reference, brand
    return None, None


def _looks_like_year(value: str) -> bool:
    if not value.isdigit() or len(value) != 4:
        return False
    year = int(value)
    return 1990 <= year <= 2035


def _extract_dial(text: str) -> str | None:
    abbrev_match = DIAL_ABBREV_PATTERN.search(text)
    if abbrev_match:
        return DIAL_ABBREVIATIONS[abbrev_match.group(1).lower()]

    match = DIAL_PATTERN.search(text)
    if match:
        color = match.group(1).lower()
        if color in ("colour", "color"):
            return None
        if color in DIAL_ABBREVIATIONS:
            return DIAL_ABBREVIATIONS[color]
        if color == "champagne":
            return "Champagne"
        if color == "wimbledon":
            return "Wimbledon"
        if color == "tiffany":
            return "Tiffany"
        if color == "olive":
            return "Olive"
        if color in ("grey", "gray"):
            return "Grey"
        return color.title()
    return None


def _extract_bracelet(text: str) -> str | None:
    for pattern, value in BRACELET_PATTERNS:
        if pattern.search(text):
            return value
    return None


def _extract_card_date(text: str) -> tuple[str | None, str | None]:
    match = NEW_CARD_DATE_PATTERN.search(text)
    if match:
        month = int(match.group(1))
        year_suffix = int(match.group(2))
        if month < 1 or month > 12:
            return None, None
        year = 2000 + year_suffix if year_suffix < 70 else 1900 + year_suffix
        return f"{month:02d}/{year}", "New"

    match = NEW_CARD_DATE_MMYyyy_PATTERN.search(text)
    if match:
        month = int(match.group(1))
        year = int(match.group(2))
        if 1 <= month <= 12:
            return f"{month:02d}/{year}", "New"

    match = CARD_MMYyyy_PATTERN.search(text)
    if match and re.search(r"\bnew\b", text, re.I):
        month = int(match.group(1))
        year = int(match.group(2))
        if 1 <= month <= 12 and 1990 <= year <= 2035:
            return f"{month:02d}/{year}", "New"

    return None, None


def _extract_used_year(text: str) -> tuple[str | None, int | None]:
    match = USED_YEAR_PATTERN.search(text)
    if not match:
        return None, None
    return "Used", int(match.group(1))


def _extract_condition(text: str) -> str | None:
    for pattern, value in CONDITION_PATTERNS:
        if pattern.search(text):
            return value
    return None


def _extract_standalone_year(text: str, watch: WatchDict) -> int | None:
    if watch.get("production_year") is not None:
        return watch["production_year"]
    if (
        NEW_CARD_DATE_PATTERN.search(text)
        or NEW_CARD_DATE_MMYyyy_PATTERN.search(text)
        or CARD_MMYyyy_PATTERN.search(text)
        or USED_YEAR_PATTERN.search(text)
        or YEAR_SUFFIX_PATTERN.search(text)
    ):
        return None
    for match in STANDALONE_YEAR_PATTERN.finditer(text):
        year = int(match.group(0))
        if 1990 <= year <= 2035:
            return year
    return None


def _extract_currency(text: str) -> str | None:
    for pattern, code in CURRENCY_PATTERNS:
        if pattern.search(text):
            return code
    return None


def _is_currency_code(value: str | None) -> bool:
    return value is not None and value.lower() in CURRENCY_CODE_PATTERN.split("|")


def _is_amount_suffix(value: str | None) -> bool:
    return value is not None and value.lower() in {"k", "m"}


def _extract_price(text: str) -> tuple[int | None, str | None]:
    for pattern, default_currency in PRICE_WITH_CURRENCY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        price, currency_code = _parse_price_match(match, default_currency)
        if price is not None:
            return price, currency_code
    return None, None


def _parse_price_match(
    match: re.Match[str],
    default_currency: str | None,
) -> tuple[int | None, str | None]:
    groups = match.groups()
    currency_code = default_currency
    amount_text: str | None = None
    suffix: str | None = None

    if len(groups) == 3:
        if groups[0] and _is_currency_code(groups[0]):
            currency_code = groups[0].upper()
            amount_text = groups[1]
            suffix = groups[2]
        elif groups[2] and _is_currency_code(groups[2]):
            amount_text = groups[0]
            suffix = groups[1] if _is_amount_suffix(groups[1]) else None
            currency_code = groups[2].upper()
        else:
            amount_text = groups[0]
            suffix = groups[1] if _is_amount_suffix(groups[1]) else groups[2]
    elif len(groups) == 2:
        if groups[1] and _is_amount_suffix(groups[1]):
            amount_text = groups[0]
            suffix = groups[1]
        elif groups[1] and _is_currency_code(groups[1]):
            amount_text = groups[0]
            currency_code = groups[1].upper()
        else:
            amount_text = groups[0]
            suffix = groups[1]
    elif len(groups) == 1:
        amount_text = groups[0]

    if amount_text is None:
        return None, None

    price = _normalize_amount(amount_text, suffix)
    if price is None:
        return None, None

    if currency_code is None:
        currency_code = _extract_currency(match.string)
    return price, currency_code


def _normalize_amount(amount_text: str, suffix: str | None) -> int | None:
    raw = amount_text.strip()
    if not raw:
        return None

    if suffix and suffix.lower() == "k":
        multiplier = 1000
    elif suffix and suffix.lower() == "m":
        multiplier = 1_000_000
    else:
        multiplier = 1

    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        value = int(raw.replace(".", ""))
    elif "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            value = float(raw.replace(".", "").replace(",", "."))
        else:
            value = float(raw.replace(",", ""))
    elif "," in raw:
        parts = raw.split(",")
        if len(parts[-1]) == 3 and parts[-1].isdigit():
            value = int(raw.replace(",", ""))
        else:
            value = float(raw.replace(",", "."))
    elif "." in raw:
        parts = raw.split(".")
        if len(parts[-1]) == 3 and len(parts) > 1 and all(p.isdigit() for p in parts):
            value = int("".join(parts))
        else:
            value = float(raw)
    else:
        value = float(raw)

    return int(round(value * multiplier))


def _extract_notes(text: str, watch: WatchDict) -> str | None:
    remaining = text
    for pattern in NOTES_REMOVE_PATTERNS:
        remaining = pattern.sub(" ", remaining)
    remaining = REQUEST_PATTERN.sub(" ", remaining)
    remaining = re.sub(r"\bbudget\s+up\s+to\b", " ", remaining, flags=re.I)
    if watch["production_year"] is not None:
        remaining = re.sub(rf"\b{watch['production_year']}\b", " ", remaining)
    if watch["card_date"] is not None:
        remaining = NEW_CARD_DATE_PATTERN.sub(" ", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip(" ,.-")
    if not remaining:
        return None
    if watch["brand"]:
        remaining = re.sub(re.escape(watch["brand"]), "", remaining, flags=re.I).strip(" ,.-")
    if watch["reference"]:
        remaining = re.sub(re.escape(watch["reference"]), "", remaining, flags=re.I).strip(" ,.-")
    if watch["dial"]:
        remaining = re.sub(rf"\b{re.escape(watch['dial'])}\b", "", remaining, flags=re.I).strip(" ,.-")
        for abbrev, dial_value in DIAL_ABBREVIATIONS.items():
            if dial_value == watch["dial"]:
                remaining = re.sub(rf"\b{re.escape(abbrev)}\b", "", remaining, flags=re.I).strip(" ,.-")
    if not remaining or len(remaining) < 2:
        return None
    return remaining


def read_message() -> str:
    print("Paste WhatsApp message (press Enter on an empty line when done):")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)

    message = "\n".join(lines).strip()
    if not message:
        print("Error: empty message.", file=sys.stderr)
        sys.exit(1)
    return message


def main() -> None:
    message = read_message()
    result = parse_message(message)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
