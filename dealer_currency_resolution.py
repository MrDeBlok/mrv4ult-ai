"""Dealer-aware implicit currency resolution for offers without explicit currency."""

from __future__ import annotations

import re
from typing import Any

Record = dict[str, Any]

PHONE_COUNTRY_CURRENCY_MAP: dict[str, str] = {
    "852": "HKD",
    "65": "SGD",
    "81": "JPY",
    "86": "CNY",
    "82": "KRW",
    "1": "USD",
}

STRONG_HISTORY_MIN_OFFERS = 5
STRONG_HISTORY_MIN_SHARE = 0.7

CURRENCY_ALIASES = {
    "EURO": "EUR",
    "RMB": "CNY",
    "USDT": "USDT",
    "USTD": "USDT",
}

HK_FLAG_PATTERN = re.compile(r"🇭🇰")
EXPLICIT_HKD_PATTERN = re.compile(r"\bHKD\b", re.I)
EXPLICIT_HKD_SYMBOL_PATTERN = re.compile(r"HK\$", re.I)
EXPLICIT_USD_CODE_PATTERN = re.compile(r"\bUSD\b", re.I)
EXPLICIT_USD_SYMBOL_PATTERN = re.compile(r"US\$", re.I)
USD_SHORTHAND_U_PATTERN = re.compile(
    r"([\d.,]+)\s*(?:k|K|m|M)?\s*U\b(?!(?:SDT?|ST))|[\d.,]+U\b(?!(?:SDT?|ST))",
    re.I,
)

EXPLICIT_CURRENCY_EVIDENCE = frozenset(
    {
        "explicit_code",
        "explicit_unambiguous_symbol",
        "usd_shorthand_u",
    }
)


def normalize_currency_code(currency: str | None) -> str | None:
    if currency is None:
        return None
    cleaned = str(currency).strip().upper()
    if not cleaned:
        return None
    return CURRENCY_ALIASES.get(cleaned, cleaned)


def _digits_only_phone(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def infer_currency_from_phone(phone: str | None) -> str | None:
    """Map a dealer phone/WhatsApp number to a default fiat currency."""
    digits = _digits_only_phone(phone)
    if not digits:
        return None
    for prefix in sorted(PHONE_COUNTRY_CURRENCY_MAP, key=len, reverse=True):
        if digits.startswith(prefix):
            return PHONE_COUNTRY_CURRENCY_MAP[prefix]
    return None


def analyze_dealer_currency_history(
    offers: list[Record],
) -> tuple[str | None, int, dict[str, int]]:
    """Return recommended currency, confidence %, and per-currency counts."""
    counts: dict[str, int] = {}
    for offer in offers:
        currency = normalize_currency_code(offer.get("original_currency"))
        if not currency:
            continue
        counts[currency] = counts.get(currency, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return None, 0, counts

    currency, count = max(counts.items(), key=lambda item: item[1])
    share = count / total
    if total < STRONG_HISTORY_MIN_OFFERS or share < STRONG_HISTORY_MIN_SHARE:
        return None, int(round(share * 100)), counts
    return currency, int(round(share * 100)), counts


def analyze_message_currency_context(message_text: str | None) -> Record:
    """Infer trusted message-level currency context from headers and explicit rows."""
    if not message_text:
        return {
            "trusted_hkd_context": False,
            "recommended_currency": None,
            "hk_flag_present": False,
            "explicit_hkd_count": 0,
            "explicit_usd_count": 0,
        }

    hk_flag_present = bool(HK_FLAG_PATTERN.search(message_text))
    explicit_hkd_count = len(EXPLICIT_HKD_PATTERN.findall(message_text)) + len(
        EXPLICIT_HKD_SYMBOL_PATTERN.findall(message_text)
    )
    explicit_usd_count = (
        len(EXPLICIT_USD_CODE_PATTERN.findall(message_text))
        + len(EXPLICIT_USD_SYMBOL_PATTERN.findall(message_text))
        + len(USD_SHORTHAND_U_PATTERN.findall(message_text))
    )

    trusted_hkd_context = hk_flag_present or (
        explicit_hkd_count >= 2 and explicit_hkd_count > explicit_usd_count
    )
    recommended_currency = "HKD" if trusted_hkd_context else None

    return {
        "trusted_hkd_context": trusted_hkd_context,
        "recommended_currency": recommended_currency,
        "hk_flag_present": hk_flag_present,
        "explicit_hkd_count": explicit_hkd_count,
        "explicit_usd_count": explicit_usd_count,
    }


def build_dealer_currency_intelligence(
    dealer: Record | None,
    *,
    offer_rows: list[Record] | None = None,
) -> Record:
    """Build dealer currency intelligence metadata for profiles and ingest."""
    dealer = dealer or {}
    phone = dealer.get("phone_number") or dealer.get("whatsapp_id")
    phone_currency = infer_currency_from_phone(str(phone) if phone else None)

    history_rows = offer_rows or []
    recommended_currency, history_confidence, history_counts = analyze_dealer_currency_history(
        history_rows
    )

    stored_default = normalize_currency_code(dealer.get("default_currency"))
    stored_confidence = dealer.get("default_currency_confidence")
    if not isinstance(stored_confidence, int):
        stored_confidence = None

    return {
        "default_currency": stored_default,
        "default_currency_confidence": stored_confidence,
        "recommended_default_currency": recommended_currency,
        "recommended_default_currency_confidence": history_confidence,
        "currency_history_counts": history_counts,
        "inferred_from_phone_country": bool(phone_currency),
        "phone_country_currency": phone_currency,
        "inferred_from_offer_history": recommended_currency is not None,
    }


def _is_explicit_currency_watch(watch: Record) -> bool:
    evidence = watch.get("currency_evidence")
    if evidence in EXPLICIT_CURRENCY_EVIDENCE:
        return True
    return bool(watch.get("currency_explicit"))


def resolve_implicit_offer_currency(
    watch: Record,
    *,
    dealer: Record | None = None,
    dealer_whatsapp: str | None = None,
    message_text: str | None = None,
) -> tuple[str | None, Record]:
    """Resolve currency for an offer without explicit currency in the message."""
    if _is_explicit_currency_watch(watch):
        currency = normalize_currency_code(
            watch.get("original_currency") or watch.get("currency")
        )
        return currency, {
            "source": "explicit",
            "currency": currency,
            "evidence": watch.get("currency_evidence"),
        }

    dealer = dealer or {}
    phone = dealer_whatsapp or dealer.get("phone_number") or dealer.get("whatsapp_id")

    stored_default = normalize_currency_code(dealer.get("default_currency"))
    if stored_default:
        return stored_default, {
            "source": "dealer_default",
            "currency": stored_default,
            "confidence": dealer.get("default_currency_confidence"),
            "evidence": watch.get("currency_evidence"),
        }

    phone_currency = infer_currency_from_phone(str(phone) if phone else None)
    if phone_currency:
        return phone_currency, {
            "source": "phone_country",
            "currency": phone_currency,
            "phone": phone,
            "evidence": watch.get("currency_evidence"),
        }

    message_context = analyze_message_currency_context(message_text)
    message_currency = normalize_currency_code(message_context.get("recommended_currency"))
    if message_currency:
        return message_currency, {
            "source": "message_context",
            "currency": message_currency,
            "message_context": message_context,
            "evidence": watch.get("currency_evidence"),
        }

    return None, {
        "source": "unknown",
        "currency": None,
        "evidence": watch.get("currency_evidence"),
    }


def apply_dealer_currency_resolution(
    watch: Record,
    *,
    dealer: Record | None = None,
    dealer_whatsapp: str | None = None,
    message_text: str | None = None,
) -> Record:
    """Apply dealer-aware currency resolution without overriding explicit currency."""
    if not watch.get("original_price") and not watch.get("price"):
        return watch

    if _is_explicit_currency_watch(watch):
        currency = normalize_currency_code(
            watch.get("original_currency") or watch.get("currency")
        )
        if currency:
            watch["original_currency"] = currency
            watch["currency"] = currency
        watch["currency_resolution"] = {
            "source": "explicit",
            "currency": currency,
            "evidence": watch.get("currency_evidence"),
        }
        return _recalculate_usd_fields(watch)

    resolved_currency, resolution = resolve_implicit_offer_currency(
        watch,
        dealer=dealer,
        dealer_whatsapp=dealer_whatsapp,
        message_text=message_text,
    )
    if resolved_currency:
        watch["original_currency"] = resolved_currency
        watch["currency"] = resolved_currency
        watch["currency_resolution"] = resolution
        return _recalculate_usd_fields(watch)

    watch["original_currency"] = None
    watch["currency"] = None
    watch["currency_resolution"] = resolution
    watch["usd_price"] = None
    watch["exchange_rate_to_usd"] = None
    return watch


def _recalculate_usd_fields(watch: Record) -> Record:
    from watch_parser import EXCHANGE_RATES_TO_USD

    original_price = watch.get("original_price")
    if original_price is None:
        original_price = watch.get("price")
    currency = normalize_currency_code(watch.get("original_currency") or watch.get("currency"))
    if original_price is None or not currency:
        watch["usd_price"] = None
        watch["exchange_rate_to_usd"] = None
        return watch

    amount = int(original_price)
    rate = EXCHANGE_RATES_TO_USD.get(currency)
    watch["original_price"] = amount
    watch["price"] = amount
    watch["original_currency"] = currency
    watch["currency"] = currency
    watch["exchange_rate_to_usd"] = rate
    watch["usd_price"] = int(round(amount * rate)) if rate is not None else None
    return watch


def load_dealer_record_for_currency_resolution(dealer_id: str | None) -> Record | None:
    """Load dealer profile for currency resolution, ignoring lookup failures in tests."""
    if not dealer_id:
        return None
    try:
        from database import get_dealer_by_id

        return get_dealer_by_id(str(dealer_id))
    except Exception:
        return None


def apply_dealer_currency_resolution_batch(
    watches: list[Record],
    *,
    dealer: Record | None = None,
    dealer_whatsapp: str | None = None,
    message_text: str | None = None,
) -> list[Record]:
    return [
        apply_dealer_currency_resolution(
            watch,
            dealer=dealer,
            dealer_whatsapp=dealer_whatsapp,
            message_text=message_text,
        )
        for watch in watches
    ]
