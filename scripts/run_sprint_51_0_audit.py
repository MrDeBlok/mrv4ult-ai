"""Run Sprint 51.0 performance audit measurements against the live app."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from database import get_client
from performance_profiler import build_audit_report
from tests.conftest import ADMIN_USER

DEFAULT_OUTPUT_PATH = ROOT / "docs" / "performance" / "sprint_51_0_measurements.json"


def _discover_audit_ids() -> dict[str, str]:
    client = get_client()
    ids: dict[str, str] = {}

    import_resp = (
        client.table("import_logs")
        .select("id")
        .order("import_time", desc=True)
        .limit(1)
        .execute()
    )
    if import_resp.data:
        ids["import_log_id"] = str(import_resp.data[0]["id"])

    dealer_resp = (
        client.table("offers")
        .select("dealer_id")
        .eq("status", "active")
        .execute()
    )
    dealer_counts: dict[str, int] = {}
    for row in dealer_resp.data or []:
        dealer_id = str(row.get("dealer_id") or "")
        if dealer_id:
            dealer_counts[dealer_id] = dealer_counts.get(dealer_id, 0) + 1
    if dealer_counts:
        ids["dealer_id"] = max(dealer_counts, key=dealer_counts.get)

    return ids


def build_search_audit_cases() -> list[dict[str, str]]:
    """Return search-route audit cases without live ID discovery."""
    return [
        {"label": "Search (empty)", "path": "/", "test_case": "search empty"},
        {"label": "Search", "path": "/?q=Rolex", "test_case": "broad search: Rolex"},
        {"label": "Search", "path": "/?q=RM35", "test_case": "reference search: RM35"},
        {"label": "Search", "path": "/?q=126500", "test_case": "reference search: 126500"},
    ]


def build_audit_cases(*, discovered_ids: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Return the full Sprint 51.0 audit case list."""
    discovered = discovered_ids if discovered_ids is not None else _discover_audit_ids()
    import_log_id = discovered.get("import_log_id", "")
    dealer_id = discovered.get("dealer_id", "")

    cases: list[dict[str, str]] = [
        *build_search_audit_cases(),
        {
            "label": "Watch reference",
            "path": (
                "/watch-reference?brand=Patek+Philippe&reference="
                + quote("5711/1A", safe="")
            ),
            "test_case": "Patek Philippe 5711/1A",
        },
        {
            "label": "Watch reference",
            "path": "/watch-reference?brand=Richard+Mille&reference=RM35-02",
            "test_case": "Richard Mille RM35-02",
        },
        {
            "label": "Watch reference",
            "path": "/watch-reference?brand=Rolex&reference=126500",
            "test_case": "Rolex 126500",
        },
        {"label": "Dashboard", "path": "/dashboard", "test_case": "dashboard"},
        {"label": "Activity", "path": "/activity", "test_case": "activity list"},
        {"label": "Parser review", "path": "/parser-review", "test_case": "parser review"},
        {"label": "Import status", "path": "/import", "test_case": "import status page"},
        {"label": "Dealers", "path": "/dealers", "test_case": "dealers list"},
        {"label": "Requests", "path": "/requests", "test_case": "requests / WTB"},
        {"label": "Market requests", "path": "/market-requests", "test_case": "market requests"},
        {"label": "Notifications", "path": "/notifications", "test_case": "notifications"},
        {"label": "Login", "path": "/login", "test_case": "login page (unauthenticated)"},
    ]

    if import_log_id:
        cases.append(
            {
                "label": "Activity detail",
                "path": f"/activity/{import_log_id}",
                "test_case": "recent activity import detail",
            }
        )
    if dealer_id:
        cases.append(
            {
                "label": "Dealer detail",
                "path": f"/dealers/{dealer_id}",
                "test_case": "dealer with many active offers",
            }
        )
    return cases


AUDIT_GROUP_BUILDERS: dict[str, Callable[[], list[dict[str, str]]]] = {
    "search": build_search_audit_cases,
}


def resolve_audit_cases(only: str | None = None) -> list[dict[str, str]]:
    """Return audit cases for the requested route group or the full audit."""
    if only is None:
        return build_audit_cases()
    try:
        builder = AUDIT_GROUP_BUILDERS[only]
    except KeyError as exc:
        supported = ", ".join(sorted(AUDIT_GROUP_BUILDERS))
        raise SystemExit(f"Unknown audit group {only!r}. Supported values: {supported}") from exc
    return builder()


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile MRV4ULT AI routes and write Sprint performance measurements.",
    )
    parser.add_argument(
        "--only",
        choices=sorted(AUDIT_GROUP_BUILDERS),
        help="Profile only a specific route group instead of the full audit.",
    )
    return parser


def _print_progress(case: dict[str, str]) -> None:
    test_case = case.get("test_case") or case.get("label") or case.get("path") or "route"
    print(f"Profiling {test_case}...", flush=True)


def run_audit_cases(cases: list[dict[str, str]]) -> list[dict[str, object]]:
    """Profile audit cases one at a time with live progress output."""
    rows: list[dict[str, object]] = []
    authenticated_cases = [case for case in cases if case["path"] != "/login"]
    unauthenticated_cases = [case for case in cases if case["path"] == "/login"]

    for case in authenticated_cases:
        _print_progress(case)
        rows.extend(
            build_audit_report(
                app,
                [case],
                current_user=ADMIN_USER,
                page_timeout_s=60.0,
                total_timeout_s=900.0,
            )
        )

    for case in unauthenticated_cases:
        _print_progress(case)
        rows.extend(
            build_audit_report(
                app,
                [case],
                current_user=None,
                page_timeout_s=30.0,
                total_timeout_s=60.0,
            )
        )

    return rows


def write_audit_report(rows: list[dict[str, object]], output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} audit measurements to {output_path}")
    for row in rows:
        print(
            f"{row['test_case']}: {row['total_duration_ms']:.0f}ms "
            f"db={row['db_time_ms']:.0f}ms queries={row['db_query_count']} "
            f"payload={row['payload_bytes']} status={row['status']}"
        )


def main(argv: list[str] | None = None) -> None:
    args = create_argument_parser().parse_args(argv)
    cases = resolve_audit_cases(args.only)
    rows = run_audit_cases(cases)
    write_audit_report(rows)


if __name__ == "__main__":
    main()
