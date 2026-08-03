"""Tests for Sprint 51.5 watch-detail bottleneck audit instrumentation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from performance_profiler import (
    ProfilerSession,
    build_watch_detail_phase_summary,
    record_profiler_phase,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_sprint_51_5_audit.py"


def _load_audit_script():
    spec = importlib.util.spec_from_file_location("run_sprint_51_5_audit", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load audit script from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWatchDetailProfilerPhases:
    def test_record_profiler_phase_accumulates_on_active_session(self) -> None:
        session = ProfilerSession(page="Watch detail", path="/watch-reference")
        token = __import__("performance_profiler", fromlist=["_active_session"])._active_session.set(session)
        try:
            with record_profiler_phase("sorting"):
                pass
            assert "sorting" in session.phases
        finally:
            __import__("performance_profiler", fromlist=["_active_session"])._active_session.reset(token)

    def test_build_watch_detail_phase_summary_maps_known_phases(self) -> None:
        from performance_profiler import PageProfileResult, PROFILE_STATUS_OK

        result = PageProfileResult(
            page="Watch detail",
            path="/watch-reference",
            status=PROFILE_STATUS_OK,
            status_code=200,
            total_response_ms=100.0,
            python_ms=10.0,
            render_ms=5.0,
            database_ms=80.0,
            query_count=3,
            slowest_query="GET offers",
            slowest_query_ms=50.0,
            phases={
                "load_offers": 20.0,
                "import_log_lookups": 30.0,
                "sorting": 1.0,
            },
        )

        summary = build_watch_detail_phase_summary(result)

        assert summary["load_offers_ms"] == 20.0
        assert summary["import_log_lookups_ms"] == 30.0
        assert summary["sorting_ms"] == 1.0
        assert summary["template_render_ms"] == 5.0


class TestSprint515AuditScript:
    def test_build_watch_detail_audit_cases_includes_sort_variants(self) -> None:
        module = _load_audit_script()
        cases = module.build_watch_detail_audit_cases()

        assert len(cases) == 8
        assert any("price_asc" in case["path"] for case in cases)
        assert any("page=2" in case["path"] for case in cases)
        assert any("5711/1A" in case["test_case"] for case in cases)
        assert any("RM35-02" in case["test_case"] for case in cases)

    def test_build_markdown_report_renders_watch_and_remove_sections(self) -> None:
        module = _load_audit_script()
        watch_rows = [
            {
                "test_case": "Patek Philippe 5711/1A (default sort)",
                "total_duration_ms": 1200.0,
                "db_time_ms": 1100.0,
                "db_query_count": 13,
                "payload_bytes": 576000,
                "render_time_ms": 20.0,
                "phases_ms": {
                    "import_log_lookups": 800.0,
                    "source_enrichment": 50.0,
                    "sorting": 2.0,
                },
                "phase_summary": {
                    "import_log_lookups_ms": 800.0,
                    "sorting_ms": 2.0,
                },
            }
        ]
        remove_flows = [
            {
                "test_case": "Patek Philippe 5711/1A remove flow",
                "combined_total_ms": 1500.0,
                "post": {
                    "total_duration_ms": 200.0,
                    "db_time_ms": 150.0,
                    "phases_ms": {"db_update": 120.0, "redirect": 0.1},
                },
                "reload": {
                    "total_duration_ms": 1300.0,
                    "db_time_ms": 1200.0,
                    "payload_bytes": 570000,
                },
            }
        ]

        report = module.build_markdown_report(watch_rows, remove_flows)

        assert "Watch detail GET profiles" in report
        assert "Offer remove flow" in report
        assert "5711/1A" in report

    def test_main_writes_report_files(self, tmp_path: Path, monkeypatch) -> None:
        module = _load_audit_script()
        output_path = tmp_path / "measurements.json"
        report_path = tmp_path / "report.md"
        monkeypatch.setattr(module, "DEFAULT_OUTPUT_PATH", output_path)
        monkeypatch.setattr(module, "DEFAULT_REPORT_PATH", report_path)
        monkeypatch.setattr(
            module,
            "run_watch_detail_audit",
            lambda: [
                {
                    "test_case": "Patek Philippe 5711/1A (sort=price_asc)",
                    "total_duration_ms": 1000.0,
                    "db_time_ms": 900.0,
                    "db_query_count": 10,
                    "payload_bytes": 1000,
                    "render_time_ms": 10.0,
                    "phases_ms": {"sorting": 3.0},
                    "phase_summary": {"sorting_ms": 3.0},
                }
            ],
        )
        monkeypatch.setattr(module, "run_offer_remove_audit", lambda: [])

        module.main()

        assert output_path.exists()
        assert report_path.exists()
