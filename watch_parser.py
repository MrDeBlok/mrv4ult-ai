"""Regex-based watch message parser for MRV4ULT AI (no AI/API)."""

from __future__ import annotations

import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from model_aliases import find_alias_match
from brand_registry import get_brand_aliases, get_brand_pattern, lookup_brand

WatchDict = dict[str, Any]
ParseResult = dict[str, Any]

BRAND_ALIASES: dict[str, str] = get_brand_aliases()
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

WEAR_CONDITION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfresh\s+new\s*/\s*unworn\b", re.I), "Fresh New / Unworn"),
    (re.compile(r"\bbrand\s+new\s*/\s*unworn\b", re.I), "Brand New / Unworn"),
    (re.compile(r"\bnew\s*/\s*unworn\b", re.I), "New / Unworn"),
    (re.compile(r"\bfresh\s+new\b", re.I), "Fresh New"),
    (re.compile(r"\bbrand\s+new\b", re.I), "Brand New"),
    (re.compile(r"\bunworn\s+complete\b", re.I), "Unworn complete"),
    (re.compile(r"\bfull\s+stickers?\b", re.I), "Full stickers"),
    (re.compile(r"\bpre[-\s]?owned\b", re.I), "Pre-Owned"),
    (re.compile(r"\bpreowned\b", re.I), "Preowned"),
    (re.compile(r"\bpre\s+owned\b", re.I), "Pre owned"),
    (re.compile(r"\bsecond\s+hand\b", re.I), "Second hand"),
    (re.compile(r"\bbnib\b", re.I), "bnib"),
    (re.compile(r"\bnos\b", re.I), "nos"),
    (re.compile(r"\blnib\b", re.I), "lnib"),
    (re.compile(r"\bnew\b", re.I), "New"),
    (re.compile(r"\bunworn\b", re.I), "Unworn"),
    (re.compile(r"\bstickered\b", re.I), "stickered"),
    (re.compile(r"\bstickers?\b", re.I), "Sticker"),
    (re.compile(r"\bmint\b", re.I), "Mint"),
    (re.compile(r"\bworn\b", re.I), "worn"),
    (re.compile(r"\bused\b", re.I), "Used"),
    (re.compile(r"\bbn\b", re.I), "BN"),
]

ACCESSORY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\bbox\s+and\s+papers\b", re.I), "full set", "full_set"),
    (re.compile(r"\bfull\s+set\b", re.I), "full set", "full_set"),
    (re.compile(r"\bfullset\b", re.I), "full set", "full_set"),
    (re.compile(r"\bwatch\s+only\b", re.I), "watch only", "watch_only"),
    (re.compile(r"\bbox\s+only\b", re.I), "box only", "box_only"),
    (re.compile(r"\bpapers?\s+only\b", re.I), "papers", "papers"),
    (re.compile(r"\bwith\s+papers\b", re.I), "papers", "papers"),
    (re.compile(r"\bpapers\b", re.I), "papers", "papers"),
]

NOTE_KEEP_PATTERN = re.compile(
    r"\b(bh\s+deal|deal|obo|firm|quick\s+sale|reserved|best\s+offer)\b",
    re.I,
)

ACCESSORY_LINE_PATTERN = re.compile(
    r"\bfull\s*set\b|\bfullset\b|\bwatch\s+only\b|\bbox\s+only\b|\bpapers\b|\bbh\s+deal\b",
    re.I,
)

REQUEST_PATTERN = re.compile(
    r"\b("
    r"wtb|"
    r"ltb|"
    r"wanted|"
    r"looking\s+for|"
    r"lf\b|"
    r"iso\b|"
    r"(?<!no\s)\bneed\b|"
    r"need\s+(?:a\s+)?(?:rolex|patek|ap|rm|watch)|"
    r"sold[\s_-]*order|"
    r"sold\s+for\s+client|"
    r"client\s+sold\s+need|"
    r"need\s+for\s+sold\s+client"
    r")\b",
    re.I,
)
OFFER_PATTERN = re.compile(
    r"\b(wts|fs|for\s+sale|asking|avail(?:able)?|stock)\b",
    re.I,
)
HEADER_PATTERN = re.compile(r"^(?:fs|for\s+sale|stock|available|offers?)[\s:.-]*$", re.I)

NEW_CARD_DATE_PATTERN = re.compile(
    r"(?:\b[Nn]\s*|(?<=[a-z])[Nn])(\d{1,2})(?:/(\d{2}|\d{4}))?\b",
)
GLUED_YEAR_N_NOTATION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9/])((?:19|20)\d{2})[Nn](\d{1,2})(?![A-Za-z0-9/])",
)
GLUED_YEAR_WEAR_CONDITION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9/])((?:19|20)\d{2})(used|new|pre[-\s]?owned|preowned)(?![A-Za-z])",
    re.I,
)
NEW_CARD_DATE_MMYyyy_PATTERN = re.compile(r"\bnew\s+(\d{1,2})/(\d{4})\b", re.I)
FROM_CARD_DATE_PATTERN = re.compile(r"\bfrom\s+(\d{1,2})-(\d{4})\b", re.I)
CARD_MMYyyy_PATTERN = re.compile(r"\b(\d{1,2})/(\d{4})\b")
USED_YEAR_PATTERN = re.compile(r"\bused\s+(\d{4})y\b", re.I)
YEAR_SUFFIX_PATTERN = re.compile(r"\b(19|20)\d{2}\s*y\b", re.I)
STANDALONE_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")

ALS_UPPER_PATTERN = re.compile(r"\bALS\b")
ALS_CONTEXT_TERMS = re.compile(
    r"\b(?:lange|saxonia|datograph|zeitwerk|odysseus|1815|chrono(?:graph)?)\b",
    re.I,
)

DIAL_PATTERN = re.compile(
    r"\b(" + "|".join(DIAL_COLORS) + r")\b(?:\s+(?:dial|colour|color))?",
    re.I,
)

CURRENCY_CODE_PATTERN = r"usdt|ustd|usd|hkd|eur|euro|chf|gbp|sgd|aed|jpy|cny|rmb|krw"

DOTTED_WATCH_REFERENCE_PATTERN = re.compile(r"\b\d{3}\.\d{3}\b")

_GLUED_INTENT_PREFIX_REPLACEMENTS = (
    (re.compile(r"\b(WTB)(\d)", re.I), r"\1 \2"),
    (re.compile(r"\b(LTB)(\d)", re.I), r"\1 \2"),
    (re.compile(r"\b(LF)(\d)", re.I), r"\1 \2"),
    (re.compile(r"\b(NEED)(\d)", re.I), r"\1 \2"),
    (re.compile(r"\b(WTS)(\d)", re.I), r"\1 \2"),
    (re.compile(r"\b(FS)(\d)", re.I), r"\1 \2"),
)

_GLUED_BRAND_PREFIX_REPLACEMENTS = (
    (re.compile(r"\bAP(\d{4,5}[A-Za-z]{0,4})\b", re.I), r"AP \1"),
    (re.compile(r"\bPP(\d{4}(?:/[0-9A-Z]+)?)\b", re.I), r"PP \1"),
    (re.compile(r"\bRLX(\d{5}[A-Za-z]{0,4})\b", re.I), r"RLX \1"),
)

_GLUED_CURRENCY_PRICE_PATTERN = re.compile(
    rf"\b({CURRENCY_CODE_PATTERN})(?=([\d.,]+)\s*([kKmM])?\b)",
    re.I,
)
_GLUED_AMOUNT_CURRENCY_SYMBOL_PATTERN = re.compile(
    r"([\d.,]+)\s*(HK\$|US\$|S\$)",
    re.I,
)


def _normalize_glued_intent_prefixes(text: str) -> str:
    """Split glued intent tokens like WTB126334 into WTB 126334."""
    normalized = text
    for pattern, replacement in _GLUED_INTENT_PREFIX_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def _normalize_glued_brand_prefixes(text: str) -> str:
    """Split glued dealer prefixes like AP26239BC into AP 26239BC."""
    normalized = text
    for pattern, replacement in _GLUED_BRAND_PREFIX_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def _normalize_glued_currency_amounts(text: str) -> str:
    """Split glued currency tokens like HKD1.424m into HKD 1.424m."""
    return _GLUED_CURRENCY_PRICE_PATTERN.sub(r"\1 ", text)


def _normalize_glued_amount_currency_symbols(text: str) -> str:
    """Split glued amount+currency tokens like 1,168,000HK$ into 1,168,000 HK$."""
    return _GLUED_AMOUNT_CURRENCY_SYMBOL_PATTERN.sub(r"\1 \2", text)


def _normalize_parser_text(text: str) -> str:
    """Apply intent, brand, and currency glue normalization before parsing."""
    normalized = _normalize_glued_intent_prefixes(text)
    normalized = _normalize_glued_brand_prefixes(normalized)
    normalized = _normalize_glued_currency_amounts(normalized)
    return _normalize_glued_amount_currency_symbols(normalized)

REFERENCE_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"\b(\d{3}\.\d{3})\b"), "A. Lange & Söhne"),
    (re.compile(r"\b(RM\s?\d{2,3}(?:[-\s/]\d{2,3})?)\b", re.I), "Richard Mille"),
    (re.compile(r"\b(M\d{4,}[A-Z0-9]+-\d{4})\b", re.I), "Tudor"),
    (re.compile(r"\b(\d{4}/[0-9A-Z]+)\b", re.I), "Patek Philippe"),
    (re.compile(r"\b(\d{4}[A-Za-z]-\d{3,})\b", re.I), "Patek Philippe"),
    (re.compile(r"\b([12]\d{5}[A-Za-z]{0,4})\b", re.I), "Rolex"),
    (re.compile(r"\b(\d{4}V(?:/[A-Z0-9]+)?(?:-[A-Z0-9]+)?)\b", re.I), "Vacheron Constantin"),
    (
        re.compile(
            rf"\b(\d{{5}}(?!{CURRENCY_CODE_PATTERN}\b)[A-Za-z]{{2,4}})\b",
            re.I,
        ),
        "Audemars Piguet",
    ),
    (re.compile(r"\b(\d{4}(?![Vv])[A-Za-z]{1,4})\b", re.I), None),
    (re.compile(r"\b([3456]\d{3})\b", re.I), "Patek Philippe"),
    (re.compile(r"\b(\d{5})\b", re.I), None),
]

SUPPORTED_CURRENCIES = frozenset(
    {"USD", "USDT", "HKD", "EUR", "CHF", "GBP", "SGD", "AED", "JPY", "CNY", "KRW"}
)
DEFAULT_IMPLICIT_CURRENCY = None

EXCHANGE_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "USDT": 1.0,
    "HKD": 0.128,
    "EUR": 1.08,
    "CHF": 1.12,
    "GBP": 1.27,
    "SGD": 0.74,
    "AED": 0.272,
    "JPY": 0.0064,
    "CNY": 0.14,
    "KRW": 0.00075,
}

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

USD_SHORTHAND_U_SUFFIX_PATTERN = re.compile(
    r"([\d.,]+)\s*(k|K|m|M)?\s*U\b(?!(?:SDT?|ST))",
    re.I,
)
USD_SHORTHAND_U_GLUED_PATTERN = re.compile(
    r"([\d.,]+)U\b(?!(?:SDT?|ST))",
    re.I,
)

EXPLICIT_CURRENCY_EVIDENCE = frozenset(
    {
        "explicit_code",
        "explicit_unambiguous_symbol",
        "usd_shorthand_u",
    }
)

CURRENCY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"HK\$", re.I), "HKD"),
    (re.compile(r"US\$", re.I), "USD"),
    (re.compile(r"(?<![A-Z])S\$", re.I), "SGD"),
    (re.compile(r"¥"), "JPY"),
    (re.compile(r"€"), "EUR"),
    (re.compile(r"\beur\b", re.I), "EUR"),
    (re.compile(r"\beuro\b", re.I), "EUR"),
    (re.compile(r"\busdt\b", re.I), "USDT"),
    (re.compile(r"\bustd\b", re.I), "USDT"),
    (re.compile(r"\busd\b", re.I), "USD"),
    (re.compile(r"£"), "GBP"),
    (re.compile(r"\bgbp\b", re.I), "GBP"),
    (re.compile(r"\bchf\b", re.I), "CHF"),
    (re.compile(r"\bhkd\b", re.I), "HKD"),
    (re.compile(r"\bsgd\b", re.I), "SGD"),
    (re.compile(r"\baed\b", re.I), "AED"),
    (re.compile(r"\bjpy\b", re.I), "JPY"),
    (re.compile(r"\bcny\b", re.I), "CNY"),
    (re.compile(r"\brmb\b", re.I), "CNY"),
    (re.compile(r"\bkrw\b", re.I), "KRW"),
]

PRICE_WITH_CURRENCY_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"HK\$\s*([\d.,]+)\s*(k|K|m|M)?", re.I), "HKD"),
    (re.compile(r"US\$\s*([\d.,]+)\s*(k|K|m|M)?", re.I), "USD"),
    (re.compile(r"(?<![A-Z])S\$\s*([\d.,]+)\s*(k|K|m|M)?", re.I), "SGD"),
    (re.compile(r"€\s*([\d.,]+)\s*(k|K|m|M)?"), "EUR"),
    (re.compile(r"£\s*([\d.,]+)\s*(k|K|m|M)?"), "GBP"),
    (re.compile(r"¥\s*([\d.,]+)\s*(k|K|m|M)?"), "JPY"),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*HK\$", re.I), "HKD"),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*US\$", re.I), "USD"),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*(?<![A-Z])S\$", re.I), "SGD"),
    (USD_SHORTHAND_U_SUFFIX_PATTERN, "USD"),
    (USD_SHORTHAND_U_GLUED_PATTERN, "USD"),
    (re.compile(r"\$\s*([\d.,]+)\s*(k|K|m|M)?\s*U\b(?!(?:SDT?|ST))", re.I), "USD"),
    (re.compile(r"\$\s*([\d.,]+)\s*(k|K|m|M)?"), None),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*€"), "EUR"),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*\$"), None),
    (re.compile(r"([\d.,]+)\s*(k|K|m|M)?\s*£"), "GBP"),
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
    (
        re.compile(
            rf"\b({CURRENCY_CODE_PATTERN})\s*([\d.,]+)\s*(k|K|m|M)?\b",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"\b([\d.,]+)\s*(k|K|m|M)?\s*"
            r"(?:net(?:t)?|shipped|\+\s*(?:ship(?:ped)?|label|your\s+label))\b",
            re.I,
        ),
        None,
    ),
    (re.compile(r"\b(\d{1,3}(?:\.\d{3})+)\b"), None),
    (re.compile(r"\b(\d{1,3}(?:,\d{3})+)\b"), None),
    (re.compile(r"(?<![\d.,])\b(\d+(?:\.\d+)?)\s*(m|M)\b"), None),
    (re.compile(r"(?<![\d.,])\b(\d+(?:\.\d+)?)\s*(k|K)\b"), None),
    (re.compile(r"\b(\d{4,7})\b"), None),
]

SYMBOL_CURRENCY_PRICE_PATTERNS = [
    (pattern, default_currency)
    for pattern, default_currency in PRICE_WITH_CURRENCY_PATTERNS
    if default_currency is not None
]
EXPLICIT_CURRENCY_PRICE_PATTERNS = SYMBOL_CURRENCY_PRICE_PATTERNS

CURRENCY_BEFORE_AMOUNT_PATTERN = re.compile(
    rf"\b({CURRENCY_CODE_PATTERN})\s*([\d.,]+)\s*(k|K|m|M)?\b",
    re.I,
)
AMOUNT_BEFORE_CURRENCY_PATTERN = re.compile(
    rf"\b([\d.,]+)\s*(k|K|m|M)?\s*({CURRENCY_CODE_PATTERN})\b",
    re.I,
)

RETAIL_PRICE_LABEL_PATTERN = re.compile(
    r"\b(?:retail(?:\s+price)?|msrp|list(?:\s+price)?|rrp)\b",
    re.I,
)
OFFER_PRICE_LABEL_PATTERN = re.compile(
    r"\b(?:nett?|netto|dealer(?:\s+price)?|asking|ask|our(?:\s+price)?|price)\b",
    re.I,
)
LABELED_PRICE_SPAN_PATTERN = re.compile(
    r"\b("
    r"retail(?:\s+price)?|msrp|list(?:\s+price)?|rrp|"
    r"nett?|netto|dealer(?:\s+price)?|asking|ask|our(?:\s+price)?|price"
    r")\b"
    r"[\s:]*"
    r"(.+?)"
    r"(?=\b(?:"
    r"retail(?:\s+price)?|msrp|list(?:\s+price)?|rrp|"
    r"nett?|netto|dealer(?:\s+price)?|asking|ask|our(?:\s+price)?|price"
    r")\b|$)",
    re.I | re.S,
)

BULLET_PREFIX = re.compile(r"^[-*•]\s*")
NUMBER_PREFIX = re.compile(r"^\d+[\.)]\s+(?=[A-Za-z*])")
MARKDOWN_EMPHASIS = re.compile(r"\*+")


def _strip_markdown(text: str) -> str:
    """Remove lightweight markdown emphasis markers from dealer text."""
    cleaned = MARKDOWN_EMPHASIS.sub("", text)
    lines: list[str] = []
    for line in cleaned.splitlines():
        normalized = re.sub(r"[ \t]+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)

NOTES_REMOVE_PATTERNS = [
    NEW_CARD_DATE_PATTERN,
    GLUED_YEAR_N_NOTATION_PATTERN,
    GLUED_YEAR_WEAR_CONDITION_PATTERN,
    NEW_CARD_DATE_MMYyyy_PATTERN,
    CARD_MMYyyy_PATTERN,
    USED_YEAR_PATTERN,
    YEAR_SUFFIX_PATTERN,
    get_brand_pattern(),
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
        "retail_price": None,
        "retail_currency": None,
        "retail_price_only": False,
        "usd_price": None,
        "exchange_rate_to_usd": None,
        "production_year": None,
        "card_date": None,
        "full_set": None,
        "watch_only": None,
        "box_only": None,
        "papers": None,
        "notes": None,
        "reference_high_confidence": False,
        "confidence": 0,
        "case_material": None,
        "edition": None,
        "dial_variant": None,
        "size_mm": None,
        "raw_model_text": None,
        "model_identity_key": None,
        "model_identity_complete": None,
        "currency_explicit": False,
        "currency_evidence": None,
        "currency_resolution": None,
    }


def parse_message(message: str) -> ParseResult:
    """Parse a raw WhatsApp message into structured JSON."""
    text = _normalize_parser_text(message.strip())
    if not text:
        return {"message_type": "unknown", "watches": []}

    is_request = bool(REQUEST_PATTERN.search(text))

    from dealer_list_splitter import split_dealer_list_message, split_multi_brand_dealer_list_message

    multi_brand_rows = split_multi_brand_dealer_list_message(text)
    if multi_brand_rows is not None and not (is_request and not OFFER_PATTERN.search(text)):
        watches: list[WatchDict] = []
        for header_brand, line in multi_brand_rows:
            if watch := parse_watch_line(line, current_brand=header_brand):
                watch["source_line"] = line
                watch["dealer_list_line"] = True
                watch["dealer_list_brand_header"] = header_brand
                watches.append(watch)
        if len(watches) >= 2:
            message_type = classify_message(text, watches, is_request)
            if message_type == "offer":
                message_type = "offer_list"
            return {"message_type": message_type, "watches": watches}

    dealer_list = split_dealer_list_message(text)
    if dealer_list is not None and not (is_request and not OFFER_PATTERN.search(text)):
        header_brand, offer_lines = dealer_list
        watches: list[WatchDict] = []
        for line in offer_lines:
            if watch := parse_watch_line(line, current_brand=header_brand):
                watch["source_line"] = line
                watch["dealer_list_line"] = True
                watches.append(watch)
        if len(watches) >= 2:
            message_type = classify_message(text, watches, is_request)
            if message_type == "offer":
                message_type = "offer_list"
            return {"message_type": message_type, "watches": watches}

    current_brand: str | None = None
    watches: list[WatchDict] = []

    blocks, header_brand = _group_offer_lines(iter_content_lines(text))

    from condition_normalizer import is_section_condition_header_line

    for line, context_brand in blocks:
        if is_section_condition_header_line(line):
            continue
        if watch := parse_watch_line(line, current_brand=context_brand):
            watch["source_line"] = line
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


def _line_establishes_brand_context(line: str, *, brand_hint: str | None = None) -> str | None:
    """Return brand when a line sets list context without a reference or price."""
    brand = _extract_brand(line)
    if brand is None:
        return None
    if _extract_reference(line, brand_hint=brand_hint)[0]:
        return None
    if _extract_price(line)[0] is not None:
        return None
    return brand


def _group_offer_lines(lines: list[str]) -> tuple[list[tuple[str, str | None]], str | None]:
    """Merge continuation lines into single offer blocks with brand context."""
    blocks: list[tuple[str, str | None]] = []
    current_brand: str | None = None

    for line in lines:
        if brand_only := _is_brand_only_line(line):
            current_brand = brand_only
            continue

        context_brand = current_brand

        if not blocks:
            if _line_begins_offer(line, brand_hint=current_brand):
                blocks.append((line, context_brand))
                if established := _line_establishes_brand_context(line, brand_hint=current_brand):
                    current_brand = established
            continue

        previous_line, _ = blocks[-1]
        if _starts_new_watch_block(line, previous_line, brand_hint=current_brand):
            blocks.append((line, context_brand))
        elif _is_continuation_line(line, previous_line, brand_hint=current_brand):
            merged = f"{previous_line}\n{line}"
            blocks[-1] = (merged, blocks[-1][1])
        elif _looks_like_watch_line(line, brand_hint=current_brand):
            blocks.append((line, context_brand))

        if established := _line_establishes_brand_context(line, brand_hint=current_brand):
            current_brand = established

    return blocks, current_brand


def _line_begins_offer(line: str, *, brand_hint: str | None = None) -> bool:
    if _extract_reference(line, brand_hint=brand_hint)[0]:
        return True
    if _extract_brand(line) and len(line.split()) >= 2:
        return True
    return _looks_like_watch_line(line, brand_hint=brand_hint)


def _starts_new_watch_block(
    line: str,
    previous_block: str,
    *,
    brand_hint: str | None = None,
) -> bool:
    reference = _extract_reference(line, brand_hint=brand_hint)[0]
    if reference:
        previous_reference = _extract_reference(previous_block, brand_hint=brand_hint)[0]
        if not previous_reference:
            return True
        return reference != previous_reference

    line_brand = _extract_brand(line)
    block_brand = _extract_brand(previous_block) or brand_hint
    if line_brand and block_brand and line_brand != block_brand:
        return True
    return False


def _is_continuation_line(
    line: str,
    previous_block: str,
    *,
    brand_hint: str | None = None,
) -> bool:
    reference = _extract_reference(line, brand_hint=brand_hint)[0]
    previous_reference = _extract_reference(previous_block, brand_hint=brand_hint)[0]

    if reference and previous_reference:
        return reference == previous_reference
    if reference and not previous_reference:
        return False

    return _is_continuation_content(line, brand_hint=brand_hint)


def _is_continuation_content(line: str, *, brand_hint: str | None = None) -> bool:
    if _extract_reference(line, brand_hint=brand_hint)[0]:
        return False
    if _extract_price(line)[0] is not None:
        return True
    if NEW_CARD_DATE_MMYyyy_PATTERN.search(line):
        return True
    if NEW_CARD_DATE_PATTERN.search(line):
        return True
    if FROM_CARD_DATE_PATTERN.search(line):
        return True
    if re.search(r"\bnew\b", line, re.I) and CARD_MMYyyy_PATTERN.search(line):
        return True
    if USED_YEAR_PATTERN.search(line):
        return True
    if _detect_wear_condition(line):
        return True
    if ACCESSORY_LINE_PATTERN.search(line):
        return True
    if not _extract_brand(line) and not _extract_reference(line, brand_hint=brand_hint)[0]:
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
        line = _strip_markdown(line)
        if HEADER_PATTERN.match(line):
            continue
        for part in re.split(r"\s*;\s*", line):
            part = part.strip()
            if part:
                lines.append(part)
    return lines


def _is_brand_only_line(line: str) -> str | None:
    """Return brand name if the line contains only a brand header."""
    from dealer_list_splitter import clean_dealer_list_line

    cleaned = clean_dealer_list_line(line)
    brand = _extract_brand(cleaned) or _extract_brand(line)
    if brand is None:
        return None
    if _extract_reference(cleaned)[0] or _extract_price(cleaned)[0] is not None:
        return None
    remaining = get_brand_pattern().sub("", cleaned).strip(" :.-")
    remaining = DECORATION_ONLY_PATTERN.sub("", remaining).strip(" :.-")
    if remaining:
        return None
    return brand


DECORATION_ONLY_PATTERN = re.compile(r"[^\w\s&]+")


def _looks_like_watch_line(line: str, *, brand_hint: str | None = None) -> bool:
    if _is_brand_only_line(line):
        return False
    if len(line) < 4:
        return False
    if _extract_reference(line, brand_hint=brand_hint)[0]:
        return True
    if _extract_price(line)[0] is not None:
        return True
    if _extract_brand(line):
        return True
    if REQUEST_PATTERN.search(line) and (
        _extract_brand(line) or _extract_reference(line, brand_hint=brand_hint)[0]
    ):
        return True
    return False


def parse_watch_line(line: str, current_brand: str | None = None) -> WatchDict | None:
    """Parse a single watch line into a structured watch dict."""
    text = _normalize_parser_text(_strip_markdown(line.strip()))
    if not text:
        return None

    watch = empty_watch()

    explicit_brand = _extract_brand(text)
    inherited_brand = current_brand if not explicit_brand else None
    enforce_brand_context = inherited_brand is not None
    reference, ref_brand, from_brand_knowledge = _extract_reference(
        text,
        brand_hint=explicit_brand or inherited_brand,
        enforce_brand_context=False,
    )
    if (
        enforce_brand_context
        and inherited_brand
        and reference
        and ref_brand
        and ref_brand != inherited_brand
        and not from_brand_knowledge
    ):
        watch["reference_brand_conflict"] = {
            "inherited_brand": inherited_brand,
            "inferred_reference_brand": ref_brand,
        }
    watch["reference"] = reference
    watch["reference_high_confidence"] = from_brand_knowledge
    watch["model"] = _extract_model(text)
    if inherited_brand:
        watch["_inherited_brand"] = inherited_brand

    from brand_resolver import apply_brand_resolution_to_watch, resolve_watch_brand

    brand_before_normalization = explicit_brand or inherited_brand or ref_brand
    resolution = resolve_watch_brand(
        reference=reference,
        text=text,
        model=watch.get("model"),
        explicit_brand=explicit_brand,
        inherited_brand=inherited_brand,
        brand_before_normalization=brand_before_normalization,
    )
    watch = apply_brand_resolution_to_watch(
        watch,
        resolution,
        inherited_brand=inherited_brand,
    )
    brand = watch.get("brand")
    reference = watch.get("reference")

    from fpj_model_knowledge import apply_fpj_enrichment

    watch = apply_fpj_enrichment(watch, text)
    brand = watch.get("brand")
    reference = watch.get("reference")

    from rm_model_knowledge import apply_rm_enrichment

    watch = apply_rm_enrichment(watch, text)
    brand = watch.get("brand")
    reference = watch.get("reference")

    watch["nickname"] = _extract_nickname(text, watch.get("reference"))
    watch["dial"] = _extract_dial(text)
    watch["bracelet"] = _extract_bracelet(text)

    card_date, new_condition, raw_card_notation = _extract_card_date(text)
    watch["card_date"] = card_date
    remaining = text
    if new_condition:
        watch["condition"] = new_condition
        if raw_card_notation:
            watch["raw_condition"] = raw_card_notation
        if card_date:
            card_year_match = re.search(r"/(\d{4})\b", card_date)
            if card_year_match:
                watch["production_year"] = int(card_year_match.group(1))
        remaining = _remove_card_date_tokens(remaining)
    else:
        glued_year, glued_condition, glued_card_date, glued_raw = _extract_glued_year_condition(text)
        if glued_condition:
            watch["condition"] = glued_condition
            watch["raw_condition"] = glued_raw
            watch["production_year"] = glued_year
            if glued_card_date:
                watch["card_date"] = glued_card_date
            remaining = _remove_glued_year_condition_tokens(text)
        else:
            used_condition, production_year = _extract_used_year(text)
            if used_condition:
                watch["condition"] = used_condition
                watch["production_year"] = production_year
                remaining = USED_YEAR_PATTERN.sub(" ", remaining)
            else:
                wear_condition, remaining = _extract_wear_condition(remaining)
                watch["condition"] = wear_condition
                watch["production_year"] = _extract_standalone_year(remaining, watch)
                if watch["production_year"] is None:
                    from fpj_model_knowledge import extract_year_glue_suffix

                    year_suffix = extract_year_glue_suffix(text)
                    if year_suffix is not None:
                        watch["production_year"] = year_suffix

    accessory_notes, remaining = _apply_accessories(watch, remaining)

    _apply_price_fields(watch, text)
    other_notes = _clean_extra_notes(
        _extract_dealer_notes(remaining, watch) or _extract_notes(remaining, watch),
        watch,
    )
    watch["notes"] = _join_note_fragments(accessory_notes, other_notes)
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
    remaining = get_brand_pattern().sub(" ", remaining)
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


def _remove_card_date_tokens(text: str) -> str:
    remaining = FROM_CARD_DATE_PATTERN.sub(" ", text)
    remaining = NEW_CARD_DATE_MMYyyy_PATTERN.sub(" ", remaining)
    remaining = NEW_CARD_DATE_PATTERN.sub(" ", remaining)
    remaining = _remove_glued_year_condition_tokens(remaining)
    return re.sub(r"\s+", " ", remaining).strip()


def _remove_glued_year_condition_tokens(text: str) -> str:
    remaining = GLUED_YEAR_N_NOTATION_PATTERN.sub(" ", text)
    remaining = GLUED_YEAR_WEAR_CONDITION_PATTERN.sub(" ", remaining)
    return re.sub(r"\s+", " ", remaining).strip()


def _extract_glued_year_condition(
    text: str,
) -> tuple[int | None, str | None, str | None, str | None]:
    """Parse glued year+condition tokens such as 2018Used, 2026New, and 2026N5."""
    match = GLUED_YEAR_N_NOTATION_PATTERN.search(text)
    if match:
        year_token = match.group(1)
        if _looks_like_year(year_token):
            month = int(match.group(2))
            if 1 <= month <= 12:
                year = int(year_token)
                raw_notation = text[match.start() + 4 : match.end()]
                return year, "New", f"{month:02d}/{year}", raw_notation

    match = GLUED_YEAR_WEAR_CONDITION_PATTERN.search(text)
    if match:
        year_token = match.group(1)
        if _looks_like_year(year_token):
            year = int(year_token)
            raw_condition = text[match.start(2) : match.end(2)]
            normalized_suffix = match.group(2).lower().replace("-", "").replace(" ", "")
            if normalized_suffix in {"used", "preowned"}:
                return year, "Used", None, raw_condition
            if normalized_suffix == "new":
                return year, "New", None, raw_condition

    return None, None, None, None


def _detect_wear_condition(text: str) -> str | None:
    condition, _ = _extract_wear_condition(text)
    return condition


def _extract_wear_condition(text: str) -> tuple[str | None, str]:
    """Detect wear condition and remove the matched phrase from the text."""
    for pattern, value in WEAR_CONDITION_PATTERNS:
        match = pattern.search(text)
        if match:
            remaining = text[: match.start()] + " " + text[match.end() :]
            remaining = re.sub(r"\s+", " ", remaining).strip()
            return value, remaining
    return None, text


def _apply_accessories(watch: WatchDict, text: str) -> tuple[list[str], str]:
    """Parse accessory flags and collect accessory phrases for notes."""
    remaining = text
    notes: list[str] = []

    for pattern, note_phrase, field_name in ACCESSORY_PATTERNS:
        if not pattern.search(remaining):
            continue
        watch[field_name] = True
        if note_phrase not in notes:
            notes.append(note_phrase)
        remaining = pattern.sub(" ", remaining)

    remaining = re.sub(r"\s+", " ", remaining).strip()
    if watch.get("full_set"):
        watch["papers"] = False
    return notes, remaining


def _join_note_fragments(*parts: str | list[str] | None) -> str | None:
    fragments: list[str] = []
    for part in parts:
        if not part:
            continue
        if isinstance(part, list):
            fragments.extend(str(item) for item in part if item)
        else:
            fragments.append(str(part))
    unique: list[str] = []
    for fragment in fragments:
        cleaned = fragment.strip(" ,.-")
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return " ".join(unique) if unique else None


def _clean_extra_notes(notes: str | None, watch: WatchDict) -> str | None:
    """Remove parsed watch identity tokens from residual note text."""
    if not notes:
        return None

    remaining = notes
    for pattern in NOTES_REMOVE_PATTERNS:
        remaining = pattern.sub(" ", remaining)
    remaining = REQUEST_PATTERN.sub(" ", remaining)
    if watch.get("production_year") is not None:
        remaining = re.sub(rf"\b{watch['production_year']}\b", " ", remaining)
    if watch.get("brand"):
        remaining = re.sub(re.escape(watch["brand"]), " ", remaining, flags=re.I)
    if watch.get("reference"):
        remaining = re.sub(re.escape(watch["reference"]), " ", remaining, flags=re.I)
    if watch.get("nickname"):
        remaining = re.sub(
            rf"\b{re.escape(watch['nickname'])}\b",
            " ",
            remaining,
            flags=re.I,
        )
    if watch.get("model"):
        remaining = re.sub(rf"\b{re.escape(watch['model'])}\b", " ", remaining, flags=re.I)
    if watch.get("dial"):
        remaining = re.sub(rf"\b{re.escape(watch['dial'])}\b", " ", remaining, flags=re.I)
    remaining = re.sub(r"\s+", " ", remaining).strip(" ,.-")
    if not remaining or len(remaining) < 2:
        return None
    if NOTE_KEEP_PATTERN.search(remaining):
        return remaining
    if watch.get("brand") and len(remaining.split()) <= 3:
        if find_alias_match(remaining):
            return remaining
        return None
    return remaining


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
    if watch.get("reference_high_confidence"):
        score += 10
    if watch.get("bracelet"):
        score += 5
    return min(score, 100)


def _price_segments(text: str) -> list[str]:
    segments: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        for part in re.split(r"\s*;\s*", cleaned):
            part = part.strip()
            if part:
                segments.append(part)
    return segments or [text.strip()]


def _segment_price_label_kind(segment: str) -> str | None:
    if RETAIL_PRICE_LABEL_PATTERN.search(segment):
        return "retail"
    if OFFER_PRICE_LABEL_PATTERN.search(segment):
        return "offer"
    return None


def _classify_price_label(label: str) -> str:
    normalized = label.lower().strip()
    if re.fullmatch(r"retail(?:\s+price)?|msrp|list(?:\s+price)?|rrp", normalized):
        return "retail"
    return "offer"


def _extract_labeled_prices_from_segment(
    segment: str,
) -> list[tuple[str, int, str | None]]:
    found: list[tuple[str, int, str | None]] = []
    for match in LABELED_PRICE_SPAN_PATTERN.finditer(segment):
        label = match.group(1)
        tail = match.group(2).strip(" ,:-")
        amount, currency = _extract_price(tail)
        if amount is None:
            amount, currency = _extract_price(f"{label} {tail}")
        if amount is None:
            continue
        if currency is None:
            currency = _extract_currency(tail) or _extract_currency(segment)
        found.append((_classify_price_label(label), amount, currency))
    return found


def _segment_has_price_label(segment: str) -> bool:
    return _segment_price_label_kind(segment) is not None


def _extract_labeled_prices(text: str) -> tuple[list[tuple[int, str | None]], list[tuple[int, str | None]]]:
    retail_prices: list[tuple[int, str | None]] = []
    offer_prices: list[tuple[int, str | None]] = []

    for segment in _price_segments(text):
        for kind, amount, currency in _extract_labeled_prices_from_segment(segment):
            if kind == "retail":
                retail_prices.append((amount, currency))
            else:
                offer_prices.append((amount, currency))

    return retail_prices, offer_prices


def _extract_unlabeled_prices(
    text: str,
    *,
    exclude_amounts: set[int] | None = None,
) -> list[tuple[int, str | None]]:
    excluded = exclude_amounts or set()
    candidates: list[tuple[int, str | None]] = []
    for segment in _price_segments(text):
        if _segment_has_price_label(segment):
            continue
        segment_prices: list[tuple[int, str | None]] = []
        for amount, currency in _extract_explicit_prices_from_segment(segment):
            if amount in excluded:
                continue
            segment_prices.append((amount, currency))
        if segment_prices:
            candidates.extend(segment_prices)
            continue
        amount, currency = _extract_price(segment)
        if amount is None or amount in excluded:
            continue
        if currency is None:
            currency = _extract_currency(segment)
        candidates.append((amount, currency))
    return candidates


def _amount_has_leading_currency(segment: str, amount_start: int) -> bool:
    prefix = segment[:amount_start]
    return bool(re.search(rf"\b({CURRENCY_CODE_PATTERN})\s*$", prefix, re.I))


def _is_prefixed_dollar_match(segment: str, match: re.Match[str]) -> bool:
    start = match.start()
    if start >= 2 and segment[start - 2 : start + 1].upper() == "HK$":
        return True
    if start >= 2 and segment[start - 2 : start + 1].upper() == "US$":
        return True
    if start >= 1 and segment[start - 1 : start + 1].upper() == "S$":
        return True
    return False


def _offer_price_to_usd(amount: int, currency: str) -> int | None:
    rate = EXCHANGE_RATES_TO_USD.get(currency)
    if rate is None:
        return None
    return int(round(amount * rate))


def _append_explicit_price(
    prices: list[tuple[int, str]],
    seen: set[tuple[int, str]],
    amount: int | None,
    currency: str | None,
) -> None:
    if amount is None:
        return
    currency_code = _normalize_currency_code(currency)
    if currency_code is None or currency_code not in SUPPORTED_CURRENCIES:
        return
    key = (amount, currency_code)
    if key in seen:
        return
    seen.add(key)
    prices.append(key)


def _extract_explicit_prices_from_segment(segment: str) -> list[tuple[int, str]]:
    """Extract explicit currency prices from one offer segment."""
    prices: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()

    for pattern, default_currency in SYMBOL_CURRENCY_PRICE_PATTERNS:
        for match in pattern.finditer(segment):
            amount, currency = _price_from_pattern_match(segment, match, default_currency)
            _append_explicit_price(prices, seen, amount, currency or default_currency)

    for match in CURRENCY_BEFORE_AMOUNT_PATTERN.finditer(segment):
        amount = _normalize_amount(match.group(2), match.group(3))
        _append_explicit_price(prices, seen, amount, match.group(1))

    for match in AMOUNT_BEFORE_CURRENCY_PATTERN.finditer(segment):
        if _amount_has_leading_currency(segment, match.start(1)):
            continue
        suffix = match.group(2)
        if not _amount_before_currency_needs_signal(match.group(1), suffix):
            continue
        amount = _normalize_amount(match.group(1), suffix)
        if _looks_like_year_amount(amount, suffix):
            continue
        _append_explicit_price(prices, seen, amount, match.group(3))

    return prices


def _extract_all_explicit_currency_prices(text: str) -> list[tuple[int, str]]:
    """Return every explicit currency price mention from an offer block."""
    prices: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for segment in _price_segments(text):
        for amount, currency in _extract_explicit_prices_from_segment(segment):
            key = (amount, currency)
            if key in seen:
                continue
            seen.add(key)
            prices.append(key)
    return prices


def _are_alternative_offer_prices(prices: list[tuple[int, str]]) -> bool:
    """Return True when multiple currencies describe the same offer price."""
    currencies = {currency for _, currency in prices}
    if len(prices) < 2 or len(currencies) < 2:
        return False

    usd_values = [
        converted
        for amount, currency in prices
        if (converted := _offer_price_to_usd(amount, currency)) is not None
    ]
    if len(usd_values) < 2:
        return True

    low = min(usd_values)
    high = max(usd_values)
    if high == 0:
        return False
    return (high - low) / high <= 0.05


def _select_alternative_offer_price(
    prices: list[tuple[int, str]],
) -> tuple[int, str]:
    """Pick the primary offer price from equivalent multi-currency quotes."""
    for preferred_currency in ("HKD", "USD"):
        for amount, currency in prices:
            if currency == preferred_currency:
                return amount, currency
    for amount, currency in prices:
        if currency in EXCHANGE_RATES_TO_USD:
            return amount, currency
    return prices[0]


def _select_primary_explicit_price(
    prices: list[tuple[int, str]],
) -> tuple[int, str]:
    """Pick the strongest explicit price when duplicates share a currency."""
    return max(prices, key=lambda item: item[0])


def _select_offer_price(
    text: str,
    retail_prices: list[tuple[int, str | None]],
    offer_prices: list[tuple[int, str | None]],
) -> tuple[int | None, str | None, bool]:
    if offer_prices:
        amount, currency = offer_prices[-1]
        if currency is None and retail_prices:
            retail_currency = retail_prices[-1][1]
            if retail_currency:
                currency = retail_currency
        return amount, currency, False

    if not retail_prices:
        alternative_prices = _extract_all_explicit_currency_prices(text)
        if _are_alternative_offer_prices(alternative_prices):
            amount, currency = _select_alternative_offer_price(alternative_prices)
            return amount, currency, False
        if alternative_prices:
            amount, currency = _select_primary_explicit_price(alternative_prices)
            return amount, currency, False

        unlabeled = _extract_unlabeled_prices(text)
        if unlabeled:
            coded = [(value, code) for value, code in unlabeled if code is not None]
            if _are_alternative_offer_prices(coded):
                amount, currency = _select_alternative_offer_price(coded)
            elif coded:
                amount, currency = _select_primary_explicit_price(coded)
            else:
                amount, currency = unlabeled[-1]
                if currency is None:
                    currency = _extract_currency(text)
            return amount, currency, False

        amount, currency = _extract_price(text)
        if currency is None:
            currency = _extract_currency(text)
        return amount, currency, False

    retail_amounts = {amount for amount, _ in retail_prices}
    unlabeled = _extract_unlabeled_prices(text, exclude_amounts=retail_amounts)
    if unlabeled:
        coded = [(value, code) for value, code in unlabeled if code is not None]
        if _are_alternative_offer_prices(coded):
            amount, currency = _select_alternative_offer_price(coded)
        else:
            amount, currency = min(unlabeled, key=lambda item: item[0])
        return amount, currency, False

    return None, None, True


def _has_explicit_currency_code(text: str) -> bool:
    return bool(re.search(rf"\b({CURRENCY_CODE_PATTERN})\b", text, re.I))


def _has_explicit_unambiguous_currency_symbol(text: str) -> bool:
    return bool(re.search(r"HK\$|US\$|S\$|€|£|¥", text, re.I))


def _has_usd_shorthand_u(text: str) -> bool:
    return bool(
        USD_SHORTHAND_U_SUFFIX_PATTERN.search(text)
        or USD_SHORTHAND_U_GLUED_PATTERN.search(text)
        or re.search(r"\$\s*[\d.,]+\s*(?:k|K|m|M)?\s*U\b(?!(?:SDT?|ST))", text, re.I)
    )


def _has_ambiguous_dollar_amount(text: str) -> bool:
    for match in re.finditer(r"\$", text):
        start = match.start()
        if start >= 2 and text[start - 2 : start + 1].upper() == "HK$":
            continue
        if start >= 2 and text[start - 2 : start + 1].upper() == "US$":
            continue
        if start >= 1 and text[start - 1 : start + 1].upper() == "S$":
            continue
        if re.search(r"[\d.,]+", text[start : start + 24]):
            return True
    return False


def infer_currency_evidence(text: str, currency: str | None) -> str:
    """Classify how currency was inferred for one offer row."""
    if currency is not None:
        if _has_explicit_currency_code(text):
            return "explicit_code"
        if _has_usd_shorthand_u(text) and currency == "USD":
            return "usd_shorthand_u"
        if _has_explicit_unambiguous_currency_symbol(text):
            return "explicit_unambiguous_symbol"
        if _has_ambiguous_dollar_amount(text):
            return "ambiguous_dollar_symbol"
        return "explicit_code"
    if _has_ambiguous_dollar_amount(text):
        return "ambiguous_dollar_symbol"
    return "missing"


def is_explicit_currency_evidence(evidence: str | None) -> bool:
    return evidence in EXPLICIT_CURRENCY_EVIDENCE


def _apply_price_fields(watch: WatchDict, text: str) -> None:
    retail_prices, offer_prices = _extract_labeled_prices(text)
    original_price, original_currency, retail_only = _select_offer_price(
        text,
        retail_prices,
        offer_prices,
    )

    if retail_prices:
        retail_price, retail_currency = retail_prices[-1]
        watch["retail_price"] = retail_price
        watch["retail_currency"] = retail_currency
    else:
        watch["retail_price"] = None
        watch["retail_currency"] = None

    watch["retail_price_only"] = retail_only
    evidence = infer_currency_evidence(text, original_currency)
    watch["currency_evidence"] = evidence
    watch["currency_explicit"] = is_explicit_currency_evidence(evidence)
    watch["original_price"] = original_price
    watch["original_currency"] = _resolve_implicit_currency(
        original_price,
        original_currency,
    )
    watch["price"] = original_price
    watch["currency"] = watch["original_currency"]

    if retail_prices:
        watch["retail_currency"] = _resolve_implicit_currency(
            watch["retail_price"],
            watch["retail_currency"],
        )

    if original_price is None or watch["original_currency"] is None:
        watch["usd_price"] = None
        watch["exchange_rate_to_usd"] = None
        return

    rate = EXCHANGE_RATES_TO_USD.get(watch["original_currency"])
    watch["exchange_rate_to_usd"] = rate
    watch["usd_price"] = int(round(original_price * rate)) if rate is not None else None


def _normalize_brand_alias(alias: str) -> str:
    cleaned = alias.lower().replace(".", "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("ö", "o")
    return cleaned


def _extract_brand(text: str) -> str | None:
    match = get_brand_pattern().search(text)
    if match:
        alias = _normalize_brand_alias(match.group(1))
        brand = lookup_brand(alias)
        if brand:
            return brand
    return _extract_als_brand(text)


def _extract_als_brand(text: str) -> str | None:
    """Detect ALS as A. Lange & Söhne without matching Dutch 'als'."""
    als_brand = lookup_brand("als")
    if not als_brand:
        return None

    if ALS_UPPER_PATTERN.search(text):
        return als_brand

    if not re.search(r"\bals\b", text):
        return None

    if _extract_reference(text)[0]:
        return als_brand
    if ALS_CONTEXT_TERMS.search(text):
        return als_brand
    if _extract_price(text)[0] is not None:
        return als_brand
    return None


def _infer_brand_from_reference(reference: str) -> str | None:
    from brand_resolver import infer_brand_from_reference_heuristic

    return infer_brand_from_reference_heuristic(reference)


def _is_dotted_watch_reference_token(token: str) -> bool:
    """Return True for A. Lange-style references like 101.021."""
    return bool(DOTTED_WATCH_REFERENCE_PATTERN.fullmatch(token.strip()))


def _mask_price_spans(text: str) -> str:
    """Remove price segments so numeric references are not confused with prices."""
    masked = text
    for pattern, _ in PRICE_WITH_CURRENCY_PATTERNS[:-1]:
        masked = pattern.sub(
            lambda match, _pattern=pattern: (
                match.group(0)
                if _is_dotted_watch_reference_token(match.group(0))
                else " " * len(match.group(0))
            ),
            masked,
        )
    return masked


def _reference_is_blocked(reference: str, price: int | None) -> bool:
    from fpj_model_knowledge import is_blocked_year_reference

    numeric_reference = reference.replace(" ", "")
    if price is not None and numeric_reference.isdigit() and int(numeric_reference) == price:
        return True
    if is_blocked_year_reference(reference):
        return True
    return _looks_like_year(reference)


def _extract_reference(
    text: str,
    *,
    brand_hint: str | None = None,
    enforce_brand_context: bool = False,
) -> tuple[str | None, str | None, bool]:
    from fpj_model_knowledge import is_blocked_year_reference, mask_year_suffix_spans

    ref_text = mask_year_suffix_spans(_mask_price_spans(text))
    price, _ = _extract_price(text)

    from brand_knowledge import (
        extract_reference_from_brand_knowledge,
        is_embedded_in_compound_reference_token,
    )

    brand_match = extract_reference_from_brand_knowledge(ref_text, brand_hint=brand_hint)
    if brand_match:
        reference, brand, _high_confidence = brand_match
        if not _reference_is_blocked(reference, price):
            return reference, brand or brand_hint, True

    for pattern, brand_hint_from_pattern in REFERENCE_PATTERNS:
        match = pattern.search(ref_text)
        if not match:
            continue
        if is_embedded_in_compound_reference_token(
            ref_text,
            match.start(1),
            match.end(1),
        ):
            continue
        reference = match.group(1).upper().replace("  ", " ").strip()
        if _reference_is_blocked(reference, price):
            continue
        if is_blocked_year_reference(reference):
            continue
        inferred_brand = brand_hint_from_pattern or _infer_brand_from_reference(reference)
        if enforce_brand_context and brand_hint:
            if inferred_brand and inferred_brand != brand_hint:
                continue
            return reference, brand_hint, False
        brand = inferred_brand
        if brand is None and brand_hint:
            brand = brand_hint
        return reference, brand, False
    if enforce_brand_context and brand_hint:
        return None, None, False
    return None, None, False


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


def _current_calendar_year() -> int:
    from datetime import datetime

    return datetime.now().year


def _resolve_new_card_year(year_token: str | None) -> int | None:
    """Resolve card year from N-notation; default to current calendar year."""
    if year_token is None:
        year = _current_calendar_year()
        return year if 1990 <= year <= 2035 else None
    if len(year_token) == 4:
        year = int(year_token)
    else:
        year_suffix = int(year_token)
        year = 2000 + year_suffix if year_suffix < 70 else 1900 + year_suffix
    if not (1990 <= year <= 2035):
        return None
    return year


def _card_date_from_new_notation_match(
    match: re.Match[str],
) -> tuple[str, str, str] | None:
    """Return card_date, New condition, and raw notation from an N-notation match."""
    month = int(match.group(1))
    if month < 1 or month > 12:
        return None
    year = _resolve_new_card_year(match.group(2))
    if year is None:
        return None
    return f"{month:02d}/{year}", "New", match.group(0).strip()


def parse_new_card_notation_value(value: str | None) -> tuple[str, int, str] | None:
    """Parse compact N-notation from a token or text fragment."""
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    match = NEW_CARD_DATE_PATTERN.fullmatch(cleaned)
    if match is None:
        match = NEW_CARD_DATE_PATTERN.search(cleaned)
    if match is None:
        return None
    parsed = _card_date_from_new_notation_match(match)
    if parsed is None:
        return None
    card_date, _condition, raw_notation = parsed
    year_match = re.search(r"/(\d{4})\b", card_date)
    production_year = int(year_match.group(1)) if year_match else None
    if production_year is None:
        return None
    return card_date, production_year, raw_notation


def trace_card_condition_parsing(line: str) -> dict[str, Any]:
    """Return stage-by-stage diagnostics for card-date / condition parsing."""
    from condition_normalizer import (
        apply_inferred_pre_owned_default,
        mark_explicit_condition_metadata,
        normalize_watch_condition,
    )
    from dealer_list_splitter import clean_dealer_list_line

    raw_input = line
    after_markdown = _strip_markdown(line.strip())
    after_preprocessing = _normalize_parser_text(after_markdown)
    after_emoji_normalization = clean_dealer_list_line(after_preprocessing)

    price_amount, price_currency = _extract_price(after_preprocessing)
    text_after_price_mask = _mask_price_spans(after_preprocessing)
    text_passed_into_extract_card_date = after_preprocessing

    card_date, new_condition, raw_notation = _extract_card_date(text_passed_into_extract_card_date)
    parsed_watch = parse_watch_line(line) or {}
    normalized_watch: dict[str, Any] = {}
    if parsed_watch:
        normalized_watch = mark_explicit_condition_metadata(
            apply_inferred_pre_owned_default(
                normalize_watch_condition(dict(parsed_watch))
            )
        )

    return {
        "raw_input": raw_input,
        "text_after_markdown": after_markdown,
        "text_after_preprocessing": after_preprocessing,
        "text_after_emoji_normalization": after_emoji_normalization,
        "price_extraction": {
            "amount": price_amount,
            "currency": price_currency,
        },
        "text_after_price_mask": text_after_price_mask,
        "text_passed_into_extract_card_date": text_passed_into_extract_card_date,
        "extract_card_date_result": {
            "card_date": card_date,
            "condition": new_condition,
            "raw_notation": raw_notation,
        },
        "parse_new_card_notation_value_N7_26": parse_new_card_notation_value("N7/26"),
        "parse_new_card_notation_value_full_text": parse_new_card_notation_value(
            after_preprocessing
        ),
        "n_notation_pattern_match": (
            NEW_CARD_DATE_PATTERN.search(after_preprocessing).group(0)
            if NEW_CARD_DATE_PATTERN.search(after_preprocessing)
            else None
        ),
        "parse_watch_line": {
            "condition": parsed_watch.get("condition"),
            "raw_condition": parsed_watch.get("raw_condition"),
            "card_date": parsed_watch.get("card_date"),
            "production_year": parsed_watch.get("production_year"),
            "original_price": parsed_watch.get("original_price"),
        },
        "raw_condition": normalized_watch.get("raw_condition"),
        "normalized_condition": normalized_watch.get("condition"),
        "condition_source": normalized_watch.get("condition_source"),
    }


def _extract_card_date(text: str) -> tuple[str | None, str | None, str | None]:
    match = FROM_CARD_DATE_PATTERN.search(text)
    if match:
        month = int(match.group(1))
        year = int(match.group(2))
        if 1 <= month <= 12 and 1990 <= year <= 2035:
            return f"{month:02d}/{year}", None, None

    match = NEW_CARD_DATE_PATTERN.search(text)
    if match:
        parsed = _card_date_from_new_notation_match(match)
        if parsed:
            return parsed[0], parsed[1], parsed[2]

    match = NEW_CARD_DATE_MMYyyy_PATTERN.search(text)
    if match:
        month = int(match.group(1))
        year = int(match.group(2))
        if 1 <= month <= 12:
            return f"{month:02d}/{year}", "New", match.group(0).strip()

    match = CARD_MMYyyy_PATTERN.search(text)
    if match and re.search(r"\bnew\b", text, re.I):
        month = int(match.group(1))
        year = int(match.group(2))
        if 1 <= month <= 12 and 1990 <= year <= 2035:
            return f"{month:02d}/{year}", "New", match.group(0).strip()

    return None, None, None


def _extract_used_year(text: str) -> tuple[str | None, int | None]:
    match = USED_YEAR_PATTERN.search(text)
    if not match:
        return None, None
    return "Used", int(match.group(1))



def _extract_standalone_year(text: str, watch: WatchDict) -> int | None:
    if watch.get("production_year") is not None:
        return watch["production_year"]

    card_date = watch.get("card_date")
    if isinstance(card_date, str):
        card_year_match = re.search(r"/(\d{4})\b", card_date)
        if card_year_match:
            year = int(card_year_match.group(1))
            if 1990 <= year <= 2035:
                return year

    if (
        NEW_CARD_DATE_PATTERN.search(text)
        or GLUED_YEAR_N_NOTATION_PATTERN.search(text)
        or GLUED_YEAR_WEAR_CONDITION_PATTERN.search(text)
        or NEW_CARD_DATE_MMYyyy_PATTERN.search(text)
        or FROM_CARD_DATE_PATTERN.search(text)
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


def _looks_like_year_amount(price: int, suffix: str | None) -> bool:
    return suffix is None and 1990 <= price <= 2035


def _resolve_implicit_currency(
    price: int | None,
    currency: str | None,
) -> str | None:
    if price is None:
        return None
    return currency or DEFAULT_IMPLICIT_CURRENCY


def _reference_numeric_values(text: str) -> set[int]:
    values: set[int] = set()
    for pattern, _ in REFERENCE_PATTERNS[:-1]:
        for match in pattern.finditer(text):
            reference = match.group(1).upper().replace(" ", "")
            if _reference_token_has_currency_suffix(reference):
                continue
            numeric = re.match(r"(\d+)", reference)
            if numeric:
                values.add(int(numeric.group(1)))
            if "/" in reference:
                prefix = reference.split("/", 1)[0]
                if prefix.isdigit():
                    values.add(int(prefix))
    return values


def _reference_token_has_currency_suffix(reference: str) -> bool:
    alpha_suffix = re.sub(r"[\d./\s-]", "", reference.upper())
    if not alpha_suffix:
        return False
    return alpha_suffix.lower() in CURRENCY_CODE_PATTERN.split("|")


def _price_match_has_strong_signal(match: re.Match[str]) -> bool:
    matched_text = match.group(0)
    if _is_dotted_watch_reference_token(matched_text):
        return False
    if re.search(r"\d{1,3}(?:\.\d{3})+", matched_text):
        return True
    if re.search(r"\d{1,3}(?:,\d{3})+", matched_text):
        return True
    if re.search(
        r"net(?:t)?|shipped|\+\s*(?:ship(?:ped)?|label|your\s+label)",
        matched_text,
        re.I,
    ):
        return True
    groups = match.groups()
    if len(groups) >= 2 and groups[1] and _is_amount_suffix(groups[1]):
        return True
    return False


def _should_reject_price_candidate(
    text: str,
    price: int,
    suffix: str | None,
    match: re.Match[str],
) -> bool:
    if _is_dotted_watch_reference_token(match.group(0)):
        return True
    if _looks_like_year_amount(price, suffix):
        return True
    if suffix is not None and _is_amount_suffix(suffix):
        return False
    if _extract_currency(match.group(0)) is not None:
        return False
    if _price_match_has_strong_signal(match):
        return False
    if price in _reference_numeric_values(text):
        return True
    return False


def _normalize_currency_code(currency_code: str | None) -> str | None:
    if currency_code is None:
        return None
    normalized = currency_code.upper()
    if normalized == "EURO":
        return "EUR"
    if normalized in {"USDT", "USTD"}:
        return "USDT"
    if normalized == "RMB":
        return "CNY"
    return normalized


def _amount_before_currency_needs_signal(amount_text: str, suffix: str | None) -> bool:
    if suffix and suffix.lower() in {"k", "m"}:
        return True
    raw = amount_text.strip()
    if "," in raw or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        return True
    normalized = _normalize_amount(raw, suffix)
    return normalized is not None and normalized >= 10_000


def _match_amount_suffix(match: re.Match[str]) -> str | None:
    for group in match.groups()[1:]:
        if group and _is_amount_suffix(group):
            return group
    return None


def _price_from_pattern_match(
    text: str,
    match: re.Match[str],
    default_currency: str | None,
) -> tuple[int | None, str | None]:
    price, currency_code = _parse_price_match(match, default_currency)
    if price is None:
        return None, None
    groups = match.groups()
    if (
        groups
        and groups[0]
        and _is_currency_code(groups[0])
        and _looks_like_year_amount(price, groups[2] if len(groups) > 2 else None)
    ):
        return None, None
    if (
        len(groups) >= 3
        and groups[-1]
        and _is_currency_code(groups[-1])
        and not _amount_before_currency_needs_signal(groups[0], groups[1])
    ):
        return None, None
    if _should_reject_price_candidate(
        text,
        price,
        _match_amount_suffix(match),
        match,
    ):
        return None, None
    return price, _normalize_currency_code(currency_code)


def _extract_price(text: str) -> tuple[int | None, str | None]:
    for pattern, default_currency in PRICE_WITH_CURRENCY_PATTERNS:
        for match in pattern.finditer(text):
            price, currency_code = _price_from_pattern_match(text, match, default_currency)
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
    return price, _normalize_currency_code(currency_code)


def parse_compact_price_amount(value: str) -> int | None:
    """Parse a price token through the centralized amount normalizer."""
    cleaned = value.strip()
    if not cleaned:
        return None

    normalized = _normalize_glued_currency_amounts(cleaned)
    price, _ = _extract_price(normalized)
    if price is not None:
        return price

    compact_match = re.fullmatch(r"([\d.,]+)\s*([kKmM])", normalized, flags=re.I)
    if compact_match:
        return _normalize_amount(compact_match.group(1), compact_match.group(2))

    plain_match = re.fullmatch(r"([\d.,]+)", normalized)
    if plain_match:
        return _normalize_amount(plain_match.group(1), None)
    return None


def _normalize_amount(amount_text: str, suffix: str | None) -> int | None:
    raw = amount_text.strip()
    if not raw:
        return None

    if suffix and _is_amount_suffix(suffix):
        try:
            value = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            return None
        multiplier = Decimal(1000 if suffix.lower() == "k" else 1_000_000)
        return int((value * multiplier).to_integral_value(rounding=ROUND_HALF_UP))

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
    if watch.get("nickname"):
        remaining = re.sub(
            rf"\b{re.escape(watch['nickname'])}\b",
            "",
            remaining,
            flags=re.I,
        ).strip(" ,.-")
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
