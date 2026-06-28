"""View helpers for watch knowledge admin pages."""

from __future__ import annotations

from typing import Any

from dealer_intelligence import dealer_display_name, format_activity_timestamp

Record = dict[str, Any]


def build_unknown_brand_row(row: Record, *, dealers_by_id: dict[str, Record]) -> Record:
    dealer = dealers_by_id.get(str(row.get("dealer_id")), {})
    return {
        "id": row.get("id"),
        "detected_text": row.get("detected_text") or "—",
        "example_message": row.get("example_message") or "—",
        "occurrence_count": row.get("occurrence_count") or 0,
        "first_seen": format_activity_timestamp(row.get("first_seen_at")),
        "last_seen": format_activity_timestamp(row.get("last_seen_at")),
        "dealer_name": dealer_display_name(dealer) if dealer else "—",
        "status": row.get("status") or "pending",
    }


def build_unknown_brand_rows(
    rows: list[Record],
    *,
    dealers_by_id: dict[str, Record],
) -> list[Record]:
    return [build_unknown_brand_row(row, dealers_by_id=dealers_by_id) for row in rows]
