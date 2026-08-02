"""Tests for Sprint 51.0 audit instrumentation."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from performance_profiler import QueryRecord, analyze_profiler_queries

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_sprint_51_0_audit.py"


def _load_audit_script():
    spec = importlib.util.spec_from_file_location("run_sprint_51_0_audit", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load audit script from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAnalyzeProfilerQueries:
    def test_detects_exact_duplicate_queries(self) -> None:
        queries = [
            QueryRecord("GET offers?status=eq.active", 10.0),
            QueryRecord("GET offers?status=eq.active", 12.0),
            QueryRecord("GET watches?id=eq.1", 5.0),
        ]

        analysis = analyze_profiler_queries(queries)

        assert analysis["duplicate_query_count"] == 1
        assert analysis["exact_duplicate_queries"]["GET offers?status=eq.active"] == 2

    def test_detects_repeated_table_access_and_n_plus_one(self) -> None:
        queries = [
            QueryRecord(f"GET import_logs?id=eq.{index}", 20.0)
            for index in range(6)
        ]

        analysis = analyze_profiler_queries(queries)

        assert analysis["repeated_table_queries"]["import_logs"] == 6
        assert analysis["n_plus_one_candidates"][0]["table"] == "import_logs"
        assert analysis["n_plus_one_candidates"][0]["distinct_id_queries"] == 6

    def test_empty_query_list_returns_zeroed_summary(self) -> None:
        analysis = analyze_profiler_queries([])

        assert analysis["duplicate_query_count"] == 0
        assert analysis["query_table_counts"] == {}


class TestRunSprint510AuditScript:
    def test_help_shows_only_option(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
        )

        assert result.returncode == 0
        assert "--only" in result.stdout
        assert "search" in result.stdout

    def test_only_search_selects_search_routes(self) -> None:
        module = _load_audit_script()

        cases = module.resolve_audit_cases("search")

        assert [case["path"] for case in cases] == [
            "/",
            "/?q=Rolex",
            "/?q=RM35",
            "/?q=126500",
        ]
        assert [case["test_case"] for case in cases] == [
            "search empty",
            "broad search: Rolex",
            "reference search: RM35",
            "reference search: 126500",
        ]

    def test_search_only_skips_live_id_discovery(self) -> None:
        module = _load_audit_script()

        with (
            patch.object(module, "write_audit_report"),
            patch.object(module, "run_audit_cases", return_value=[]),
            patch.object(module, "_discover_audit_ids") as mock_discover_ids,
        ):
            module.main(["--only", "search"])

        mock_discover_ids.assert_not_called()
