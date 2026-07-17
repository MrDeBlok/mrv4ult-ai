"""Dry-run diagnostics for parser training row / offer source identity conflicts.

Usage:
  python scripts/diagnose_parser_training_source_identity.py <import_log_id>
  python scripts/diagnose_parser_training_source_identity.py <import_log_id> --message-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import (  # noqa: E402
    get_client,
    get_import_log,
    is_valid_uuid,
    list_parser_training_rows_for_import,
)


def _fetch_offers_for_message(message_id: str) -> list[dict]:
    response = (
        get_client()
        .table("offers")
        .select(
            "id, message_id, line_index, watch_id, dealer_id, status, condition, "
            "original_price, original_currency, duplicate_of_id, is_duplicate"
        )
        .eq("message_id", message_id)
        .order("line_index")
        .execute()
    )
    return response.data or []


def diagnose_import(import_log_id: str, *, message_id: str | None = None) -> int:
    import_log = get_import_log(import_log_id)
    if import_log is None:
        print(f"Import log not found: {import_log_id}")
        return 1

    resolved_message_id = (
        message_id
        or str(import_log.get("message_id") or "")
        or str((import_log.get("summary") or {}).get("message_id") or "")
    )
    if not is_valid_uuid(resolved_message_id):
        print(f"Invalid or missing message_id for import {import_log_id}")
        return 1

    training_rows = list_parser_training_rows_for_import(import_log_id)
    offers = _fetch_offers_for_message(resolved_message_id)
    offers_by_id = {str(row["id"]): row for row in offers}
    offers_by_line = {int(row.get("line_index") or 0): row for row in offers}

    print(f"Import log: {import_log_id}")
    print(f"Message id: {resolved_message_id}")
    print(f"Training rows: {len(training_rows)}")
    print(f"Offers for message: {len(offers)}")
    print()

    print("Parser training rows")
    print("row_id | row_index | status | created_offer_id | source_line")
    print("-" * 100)
    for row in sorted(training_rows, key=lambda item: int(item.get("row_index") or 0)):
        source_line = str(row.get("raw_row_text") or row.get("source_line") or "")[:80]
        print(
            f"{row.get('id')} | {row.get('row_index')} | {row.get('status')} | "
            f"{row.get('created_offer_id')} | {source_line}"
        )
    print()

    print("Offers")
    print("offer_id | line_index | watch_id | status | duplicate")
    print("-" * 100)
    for offer in offers:
        print(
            f"{offer.get('id')} | {offer.get('line_index')} | {offer.get('watch_id')} | "
            f"{offer.get('status')} | {offer.get('is_duplicate')}"
        )
    print()

    duplicate_line_indexes = [
        line for line, count in Counter(int(row.get("line_index") or 0) for row in offers).items()
        if count > 1
    ]
    if duplicate_line_indexes:
        print("Duplicate offer line_index values within message:")
        for line in sorted(duplicate_line_indexes):
            print(f"  line_index={line}")
        print()

    conflicts: list[str] = []
    for row in training_rows:
        row_index = int(row.get("row_index") or 0)
        linked_id = str(row.get("created_offer_id") or "")
        owner = offers_by_line.get(row_index)
        linked = offers_by_id.get(linked_id)
        if owner and linked and str(owner.get("id")) != linked_id:
            conflicts.append(
                f"row_index={row_index}: training row {row.get('id')} linked to offer "
                f"{linked_id} (line {linked.get('line_index')}), but offer {owner.get('id')} "
                f"owns message line {row_index}"
            )
        elif linked and linked.get("line_index") is not None and int(linked.get("line_index")) != row_index:
            conflicts.append(
                f"row_index={row_index}: training row {row.get('id')} linked to offer "
                f"{linked_id} with line_index={linked.get('line_index')}"
            )

    shared_links: dict[str, list[int]] = defaultdict(list)
    for row in training_rows:
        linked_id = str(row.get("created_offer_id") or "")
        if linked_id:
            shared_links[linked_id].append(int(row.get("row_index") or 0))
    shared_links = {offer_id: indexes for offer_id, indexes in shared_links.items() if len(indexes) > 1}

    if shared_links:
        print("Multiple training rows linked to the same offer:")
        for offer_id, indexes in sorted(shared_links.items()):
            print(f"  offer {offer_id}: row_indexes={sorted(indexes)}")
        print()

    if conflicts:
        print("Detected source identity conflicts:")
        for conflict in conflicts:
            print(f"  - {conflict}")
    else:
        print("No training-row / offer source identity conflicts detected.")

    print("\nDry run only — no database changes were made.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect parser training row and offer source identity for one import."
    )
    parser.add_argument("import_log_id", help="Import log UUID")
    parser.add_argument("--message-id", default="", help="Optional message UUID override")
    args = parser.parse_args()
    return diagnose_import(args.import_log_id, message_id=args.message_id.strip() or None)


if __name__ == "__main__":
    raise SystemExit(main())
