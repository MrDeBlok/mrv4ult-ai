#!/usr/bin/env python3
"""Dry-run and apply repairs for Vacheron references stored as Audemars Piguet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reference_knowledge import (
    REFERENCE_KNOWLEDGE_DIR,
    VACHERON_CONSTANTIN,
    find_suspicious_vacheron_ap_mappings,
    import_reference_knowledge_dataset,
)


def _load_candidate_rows_from_dataset() -> list[dict]:
    """Build candidate rows from the maintained Vacheron dataset stored as AP."""
    rows: list[dict] = []
    dataset = REFERENCE_KNOWLEDGE_DIR / "vacheron_constantin_overseas.json"
    if not dataset.exists():
        return rows
    with dataset.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        reference = entry.get("reference")
        if not reference:
            continue
        rows.append(
            {
                "reference": str(reference).strip().upper(),
                "brand": "Audemars Piguet",
                "source_line": f"{reference} full set",
            }
        )
    return rows


def _load_parser_training_candidates() -> list[dict]:
    try:
        from database import list_parser_training_rows_for_reference
    except Exception:
        return []

    rows: list[dict] = []
    for entry in _load_candidate_rows_from_dataset():
        reference = entry["reference"]
        try:
            matches = list_parser_training_rows_for_reference(reference, limit=20)
        except Exception:
            matches = []
        for match in matches:
            rows.append(
                {
                    "reference": reference,
                    "brand": match.get("detected_brand") or match.get("normalized_brand"),
                    "source": "parser_training_rows",
                    "row_id": match.get("id"),
                }
            )
    return rows


def build_repair_report(*, include_training_rows: bool = False) -> dict:
    synthetic_rows = _load_candidate_rows_from_dataset()
    suspects = find_suspicious_vacheron_ap_mappings(synthetic_rows)
    training_suspects: list[dict] = []
    if include_training_rows:
        training_suspects = find_suspicious_vacheron_ap_mappings(_load_parser_training_candidates())

    return {
        "synthetic_dataset_suspects": suspects,
        "parser_training_suspects": training_suspects,
        "total_suspects": len(suspects) + len(training_suspects),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply trusted reference-brand mapping imports after showing the dry-run report.",
    )
    parser.add_argument(
        "--include-training-rows",
        action="store_true",
        help="Also scan parser_training_rows when Supabase is available.",
    )
    args = parser.parse_args()

    dataset = REFERENCE_KNOWLEDGE_DIR / "vacheron_constantin_overseas.json"
    import_report = import_reference_knowledge_dataset(
        dataset,
        dry_run=not args.apply,
        upsert_mappings=args.apply,
    )
    repair_report = build_repair_report(include_training_rows=args.include_training_rows)

    print(json.dumps({"import_report": import_report, "repair_report": repair_report}, indent=2))

    if repair_report["total_suspects"]:
        print(
            f"\nFound {repair_report['total_suspects']} suspicious "
            f"{VACHERON_CONSTANTIN} references stored as Audemars Piguet.",
            file=sys.stderr,
        )
    else:
        print("\nNo deterministic Vacheron-to-AP suspects found in scanned sources.", file=sys.stderr)

    if not args.apply:
        print(
            "\nDry run only. Re-run with --apply to upsert trusted reference_brand_mappings.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
