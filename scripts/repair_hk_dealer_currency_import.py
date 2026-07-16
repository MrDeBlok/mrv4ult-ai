#!/usr/bin/env python3
"""Dry-run and apply repairs for Hong Kong dealer offers stored as explicit USD from bare $."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dealer_currency_resolution import (  # noqa: E402
    EXPLICIT_CURRENCY_EVIDENCE,
    apply_dealer_currency_resolution,
)
from market_price_confidence import attach_market_price_metadata  # noqa: E402
from watch_parser import parse_watch_line  # noqa: E402

Record = dict[str, Any]

DEFAULT_HK_MESSAGE = """🇭🇰 New 🇭🇰
Rolex 126619LB $618k
PP 5711 $180K
AP 15500 $231K
Omega 210.30.42.20.01.001 $1.128m
Patek 5167A 183k
VC 4500V 183k EUR
Rolex 126334 US$183k
Patek 5711 510k USDT
Tudor M79000 $545k U
5980/1400G 2024 Nos 600k U / 4.68M hkd
5990/1R 2025 Nos 319k U / 2.47M hkd
RM65-01 MCL 6/26 450000U
RM65-01 Wht 5/26 $3.61m
5296G 4.68M hkd
PP 5711/1A EUR 183k full set
"""


def _legacy_currency_from_watch(watch: Record) -> str | None:
    if watch.get("currency_evidence") == "ambiguous_dollar_symbol":
        return "USD"
    return watch.get("original_currency")


def _should_skip_repair(watch: Record) -> tuple[bool, str]:
    evidence = watch.get("currency_evidence")
    if evidence in EXPLICIT_CURRENCY_EVIDENCE:
        return True, f"explicit currency evidence ({evidence})"
    return False, ""


def _resolve_watch_from_source_line(
    source_line: str,
    *,
    dealer: Record | None,
    dealer_whatsapp: str | None,
    message_text: str | None,
) -> Record:
    watch = parse_watch_line(source_line)
    if watch is None:
        raise ValueError(f"Could not parse source line: {source_line!r}")
    watch["source_line"] = source_line
    resolved = apply_dealer_currency_resolution(
        watch,
        dealer=dealer,
        dealer_whatsapp=dealer_whatsapp,
        message_text=message_text,
    )
    attach_market_price_metadata(resolved)
    return resolved


def build_synthetic_repair_report(
    message_text: str,
    *,
    dealer_whatsapp: str = "+85291234567",
) -> Record:
    from watch_parser import parse_message

    parsed = parse_message(message_text)
    proposals: list[Record] = []
    for watch in parsed.get("watches") or []:
        source_line = str(watch.get("source_line") or "")
        if not source_line:
            continue
        parsed_watch = dict(watch)
        legacy_currency = _legacy_currency_from_watch(parsed_watch)
        skip, skip_reason = _should_skip_repair(parsed_watch)
        resolved = _resolve_watch_from_source_line(
            source_line,
            dealer=None,
            dealer_whatsapp=dealer_whatsapp,
            message_text=message_text,
        )
        current_currency = legacy_currency
        proposed_currency = resolved.get("original_currency")
        if skip:
            action = "skip"
            reason = skip_reason
        elif current_currency == proposed_currency:
            action = "unchanged"
            reason = "already correct"
        else:
            action = "update"
            reason = resolved.get("currency_resolution", {}).get("source", "resolved")

        proposals.append(
            {
                "source_line": source_line,
                "current_currency": current_currency,
                "proposed_currency": proposed_currency,
                "current_price": parsed_watch.get("original_price"),
                "proposed_price": resolved.get("original_price"),
                "action": action,
                "reason": reason,
                "currency_evidence": parsed_watch.get("currency_evidence"),
                "market_price_eligible": resolved.get("market_price_eligible"),
            }
        )

    return {
        "mode": "synthetic",
        "dealer_whatsapp": dealer_whatsapp,
        "total_rows": len(proposals),
        "updates": [row for row in proposals if row["action"] == "update"],
        "skipped": [row for row in proposals if row["action"] == "skip"],
        "unchanged": [row for row in proposals if row["action"] == "unchanged"],
        "rows": proposals,
    }


def build_database_repair_report(
    message_id: str,
    *,
    apply_changes: bool = False,
) -> Record:
    from database import get_client, get_dealer_by_id, get_message_by_id, update_offer_from_training

    message = get_message_by_id(message_id)
    if message is None:
        raise ValueError(f"Message not found: {message_id}")

    message_text = str(message.get("raw_text") or "")
    response = (
        get_client()
        .table("offers")
        .select(
            "id, message_id, dealer_id, line_index, source_line, "
            "original_price, original_currency, usd_price, status"
        )
        .eq("message_id", message_id)
        .order("line_index")
        .execute()
    )
    offers = response.data or []
    proposals: list[Record] = []
    applied: list[Record] = []

    for offer in offers:
        offer_id = str(offer.get("id") or "")
        source_line = str(offer.get("source_line") or "")
        if not source_line:
            proposals.append(
                {
                    "offer_id": offer_id,
                    "action": "skip",
                    "reason": "missing source_line",
                }
            )
            continue

        dealer = None
        dealer_whatsapp = None
        dealer_id = offer.get("dealer_id")
        if dealer_id:
            dealer = get_dealer_by_id(str(dealer_id))
            if dealer:
                dealer_whatsapp = dealer.get("phone_number") or dealer.get("whatsapp_id")

        resolved = _resolve_watch_from_source_line(
            source_line,
            dealer=dealer,
            dealer_whatsapp=str(dealer_whatsapp) if dealer_whatsapp else None,
            message_text=message_text,
        )
        skip, skip_reason = _should_skip_repair(resolved)
        current_currency = offer.get("original_currency")
        proposed_currency = resolved.get("original_currency")

        row = {
            "offer_id": offer_id,
            "source_line": source_line,
            "current_currency": current_currency,
            "proposed_currency": proposed_currency,
            "current_price": offer.get("original_price"),
            "proposed_price": resolved.get("original_price"),
            "currency_evidence": resolved.get("currency_evidence"),
            "market_price_eligible": resolved.get("market_price_eligible"),
        }

        if skip:
            row["action"] = "skip"
            row["reason"] = skip_reason
        elif current_currency == proposed_currency and offer.get("usd_price") == resolved.get("usd_price"):
            row["action"] = "unchanged"
            row["reason"] = "already correct"
        else:
            row["action"] = "update"
            row["reason"] = resolved.get("currency_resolution", {}).get("source", "resolved")
            if apply_changes:
                updated = update_offer_from_training(
                    offer_id,
                    watch=resolved,
                    message_id=message_id,
                    line_index=offer.get("line_index"),
                )
                applied.append({"offer_id": offer_id, "updated_currency": updated.get("original_currency")})
        proposals.append(row)

    return {
        "mode": "database",
        "message_id": message_id,
        "apply_changes": apply_changes,
        "total_rows": len(proposals),
        "updates": [row for row in proposals if row["action"] == "update"],
        "skipped": [row for row in proposals if row["action"] == "skip"],
        "unchanged": [row for row in proposals if row["action"] == "unchanged"],
        "applied": applied,
        "rows": proposals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message-id", help="Repair offers linked to one imported message")
    parser.add_argument(
        "--message-text-file",
        help="Use custom message text for synthetic dry-run instead of the bundled HK sample",
    )
    parser.add_argument(
        "--dealer-whatsapp",
        default="+85291234567",
        help="Dealer WhatsApp/phone for synthetic dry-run",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply database repairs (default is dry-run)",
    )
    args = parser.parse_args()

    if args.message_id:
        report = build_database_repair_report(args.message_id, apply_changes=args.apply)
    else:
        if args.apply:
            print("Refusing to --apply without --message-id", file=sys.stderr)
            return 2
        message_text = DEFAULT_HK_MESSAGE
        if args.message_text_file:
            message_text = Path(args.message_text_file).read_text(encoding="utf-8")
        report = build_synthetic_repair_report(
            message_text,
            dealer_whatsapp=args.dealer_whatsapp,
        )

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
