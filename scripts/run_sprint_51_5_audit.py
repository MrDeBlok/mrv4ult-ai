"""Run Sprint 51.5 watch-detail bottleneck audit measurements."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from database import get_client, get_offer_by_id, restore_offer
from offer_removal import is_manually_removed_offer
from performance_profiler import (
    build_audit_report,
    build_watch_detail_phase_summary,
    profile_audit_cases,
    profile_offer_remove_flow,
)
from tests.conftest import ADMIN_USER

DEFAULT_OUTPUT_PATH = ROOT / "docs" / "performance" / "sprint_51_5_measurements.json"
DEFAULT_REPORT_PATH = ROOT / "docs" / "performance" / "sprint_51_5_watch_detail_audit.md"

WATCH_REFERENCES = (
    ("Patek Philippe", "5711/1A"),
    ("Richard Mille", "RM35-02"),
)


def _watch_reference_path(brand: str, reference: str, *, sort: str = "", page: int | None = None) -> str:
    params = f"brand={quote(brand)}&reference={quote(reference, safe='')}"
    if sort:
        params += f"&sort={quote(sort)}"
    if page is not None and page > 1:
        params += f"&page={page}"
    return f"/watch-reference?{params}"


def build_watch_detail_audit_cases() -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for brand, reference in WATCH_REFERENCES:
        label = f"{brand} {reference}"
        cases.append(
            {
                "label": "Watch detail",
                "path": _watch_reference_path(brand, reference),
                "test_case": f"{label} (default sort, page 1)",
            }
        )
        cases.append(
            {
                "label": "Watch detail",
                "path": _watch_reference_path(brand, reference, page=2),
                "test_case": f"{label} (page 2)",
            }
        )
        cases.append(
            {
                "label": "Watch detail",
                "path": _watch_reference_path(brand, reference, sort="price_asc"),
                "test_case": f"{label} (sort=price_asc)",
            }
        )
        cases.append(
            {
                "label": "Watch detail",
                "path": _watch_reference_path(brand, reference, sort="price_desc"),
                "test_case": f"{label} (sort=price_desc)",
            }
        )
    return cases


def _find_active_offer_id(brand: str, reference: str) -> str | None:
    client = get_client()
    watch_resp = (
        client.table("watches")
        .select("id")
        .eq("brand", brand)
        .eq("reference", reference)
        .limit(1)
        .execute()
    )
    if not watch_resp.data:
        return None
    watch_id = watch_resp.data[0]["id"]
    offer_resp = (
        client.table("offers")
        .select("id")
        .eq("watch_id", watch_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not offer_resp.data:
        return None
    return str(offer_resp.data[0]["id"])


def run_watch_detail_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in build_watch_detail_audit_cases():
        print(f"Profiling {case['test_case']}...", flush=True)
        case_rows = build_audit_report(
            app,
            [case],
            current_user=ADMIN_USER,
            page_timeout_s=120.0,
            total_timeout_s=900.0,
        )
        for row in case_rows:
            phases = row.get("phases_ms") or {}
            row["phase_summary"] = {
                "load_offers_ms": phases.get("load_offers"),
                "condition_filter_ms": phases.get("condition_filter"),
                "import_log_lookups_ms": phases.get("import_log_lookups"),
                "recency_enrichment_ms": phases.get("recency_enrichment"),
                "date_filter_ms": phases.get("date_filter"),
                "statistics_ms": phases.get("statistics"),
                "source_enrichment_ms": phases.get("source_enrichment"),
                "sorting_ms": phases.get("sorting"),
                "template_render_ms": row.get("render_time_ms"),
            }
        rows.extend(case_rows)
    return rows


def run_offer_remove_audit() -> list[dict[str, Any]]:
    flows: list[dict[str, Any]] = []
    import performance_profiler as profiler_module
    from fastapi.testclient import TestClient

    profiler_module.install_profiler_hooks()
    with profiler_module._profile_as_user(ADMIN_USER):
        client = TestClient(app)
        for brand, reference in WATCH_REFERENCES:
            offer_id = _find_active_offer_id(brand, reference)
            if not offer_id:
                print(f"Skipping remove flow for {brand} {reference}: no active offer", flush=True)
                continue
            return_to = _watch_reference_path(brand, reference, sort="price_asc")
            test_case = f"{brand} {reference} remove flow"
            print(f"Profiling {test_case}...", flush=True)
            flow = profile_offer_remove_flow(
                client,
                offer_id=offer_id,
                return_to=return_to,
                page_timeout_s=120.0,
                test_case=test_case,
            )
            offer_row = get_offer_by_id(offer_id)
            if is_manually_removed_offer(offer_row or {}):
                print(f"Restoring offer {offer_id} after profiling...", flush=True)
                restore_offer(offer_id, restored_by_user_id=ADMIN_USER["id"])
            flows.append(flow)
    return flows


def _format_ms(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.1f}"


def _top_phase_bottleneck(row: dict[str, Any]) -> str:
    phases = row.get("phases_ms") or {}
    if not phases:
        return "—"
    name, duration = max(phases.items(), key=lambda item: item[1])
    return f"{name} ({duration:.1f} ms)"


def build_markdown_report(
    watch_rows: list[dict[str, Any]],
    remove_flows: list[dict[str, Any]],
) -> str:
    lines = [
        "# Sprint 51.5 — Watch Detail Bottleneck Audit",
        "",
        "**Measurement only — no optimizations applied.**",
        "",
        f"**Raw JSON:** `{DEFAULT_OUTPUT_PATH.as_posix()}`",
        "",
        "## 1. Watch detail GET profiles",
        "",
        "| Test case | Total (ms) | DB (ms) | Queries | Payload | Render (ms) | Top phase | Sorting (ms) | Import logs (ms) | Source enrich (ms) |",
        "|-----------|------------|---------|---------|---------|-------------|-----------|--------------|------------------|----------------------|",
    ]
    for row in watch_rows:
        phases = row.get("phases_ms") or {}
        lines.append(
            "| {test_case} | {total} | {db} | {queries} | {payload} | {render} | {top} | {sort} | {import_logs} | {source} |".format(
                test_case=row.get("test_case", "—"),
                total=_format_ms(row.get("total_duration_ms")),
                db=_format_ms(row.get("db_time_ms")),
                queries=row.get("db_query_count", "—"),
                payload=f"{int(row.get('payload_bytes') or 0):,} B",
                render=_format_ms(row.get("render_time_ms")),
                top=_top_phase_bottleneck(row),
                sort=_format_ms(phases.get("sorting")),
                import_logs=_format_ms(phases.get("import_log_lookups")),
                source=_format_ms(phases.get("source_enrichment")),
            )
        )

    lines.extend(
        [
            "",
            "## 2. Phase breakdown (milliseconds)",
            "",
        ]
    )
    for row in watch_rows:
        lines.append(f"### {row.get('test_case', '—')}")
        lines.append("")
        summary = row.get("phase_summary") or {}
        for key in (
            "load_offers_ms",
            "condition_filter_ms",
            "import_log_lookups_ms",
            "recency_enrichment_ms",
            "date_filter_ms",
            "statistics_ms",
            "source_enrichment_ms",
            "sorting_ms",
            "template_render_ms",
        ):
            label = key.replace("_ms", "").replace("_", " ")
            lines.append(f"- **{label}:** {_format_ms(summary.get(key))}")
        lines.append(f"- **database (queries):** {_format_ms(row.get('db_time_ms'))}")
        lines.append(f"- **total request:** {_format_ms(row.get('total_duration_ms'))}")
        lines.append("")

    lines.extend(
        [
            "## 3. Offer remove flow (POST + reload)",
            "",
        ]
    )
    if not remove_flows:
        lines.append("_No remove flows profiled (no active offers found)._")
    else:
        lines.extend(
            [
                "| Reference | Combined (ms) | POST total (ms) | POST DB (ms) | POST db_update (ms) | POST redirect (ms) | Reload total (ms) | Reload DB (ms) | Reload payload |",
                "|-----------|---------------|-----------------|--------------|---------------------|--------------------|--------------------|----------------|----------------|",
            ]
        )
        for flow in remove_flows:
            post = flow["post"]
            reload = flow["reload"]
            post_phases = post.get("phases_ms") or {}
            lines.append(
                "| {case} | {combined} | {post_total} | {post_db} | {db_update} | {redirect} | {reload_total} | {reload_db} | {payload} |".format(
                    case=flow.get("test_case", "—"),
                    combined=_format_ms(flow.get("combined_total_ms")),
                    post_total=_format_ms(post.get("total_duration_ms")),
                    post_db=_format_ms(post.get("db_time_ms")),
                    db_update=_format_ms(post_phases.get("db_update")),
                    redirect=_format_ms(post_phases.get("redirect")),
                    reload_total=_format_ms(reload.get("total_duration_ms")),
                    reload_db=_format_ms(reload.get("db_time_ms")),
                    payload=f"{int(reload.get('payload_bytes') or 0):,} B",
                )
            )

    lines.extend(
        [
            "",
            "## 4. Top bottlenecks",
            "",
        ]
    )
    default_rows = [row for row in watch_rows if "default sort" in str(row.get("test_case", ""))]
    sort_rows = [row for row in watch_rows if "price_asc" in str(row.get("test_case", ""))]
    for label, subset in (("Default sort", default_rows), ("Price sort (asc)", sort_rows)):
        if not subset:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for row in subset:
            lines.append(
                f"- **{row.get('test_case')}:** DB {_format_ms(row.get('db_time_ms'))} ms, "
                f"import log lookups {_format_ms((row.get('phases_ms') or {}).get('import_log_lookups'))} ms, "
                f"source enrichment {_format_ms((row.get('phases_ms') or {}).get('source_enrichment'))} ms, "
                f"sorting {_format_ms((row.get('phases_ms') or {}).get('sorting'))} ms, "
                f"render {_format_ms(row.get('render_time_ms'))} ms"
            )
        lines.append("")

    if remove_flows:
        lines.append("### Offer remove")
        lines.append("")
        for flow in remove_flows:
            post = flow["post"]
            reload = flow["reload"]
            lines.append(
                f"- **{flow.get('test_case')}:** POST {_format_ms(post.get('total_duration_ms'))} ms "
                f"(db_update {_format_ms((post.get('phases_ms') or {}).get('db_update'))} ms), "
                f"reload {_format_ms(reload.get('total_duration_ms'))} ms, "
                f"combined {_format_ms(flow.get('combined_total_ms'))} ms"
            )
        lines.append("")

    lines.append("## 5. Notes")
    lines.append("")
    lines.append("- Phase timings are wall-clock segments inside the watch-detail render path.")
    lines.append("- Database time is measured separately via PostgREST hook instrumentation.")
    lines.append("- Remove profiling restores each offer after measurement when possible.")
    lines.append("- Sorting phase includes price sort and row formatting only; offers are already loaded.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    watch_rows = run_watch_detail_audit()
    remove_flows = run_offer_remove_audit()
    payload = {
        "watch_detail": watch_rows,
        "offer_remove_flows": remove_flows,
    }
    DEFAULT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = build_markdown_report(watch_rows, remove_flows)
    DEFAULT_REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Wrote measurements to {DEFAULT_OUTPUT_PATH}")
    print(f"Wrote report to {DEFAULT_REPORT_PATH}")
    for row in watch_rows:
        print(
            f"{row['test_case']}: {row['total_duration_ms']:.0f}ms "
            f"db={row['db_time_ms']:.0f}ms queries={row['db_query_count']} "
            f"payload={row['payload_bytes']} sort_phase={(row.get('phases_ms') or {}).get('sorting', 0)}ms"
        )
    for flow in remove_flows:
        print(
            f"{flow['test_case']}: combined={flow['combined_total_ms']:.0f}ms "
            f"post={flow['post']['total_duration_ms']:.0f}ms "
            f"reload={flow['reload']['total_duration_ms']:.0f}ms"
        )


if __name__ == "__main__":
    main()
