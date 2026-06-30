"""Measure page performance without changing application behaviour."""

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import httpx
import jinja2
from fastapi import FastAPI
from fastapi.testclient import TestClient

logger = logging.getLogger(__name__)

DEFAULT_PAGE_TIMEOUT_S = 30.0
DEFAULT_TOTAL_TIMEOUT_S = 180.0

# Sprint 43.0 profiler baselines (unbounded select=* import_logs fetches).
SPRINT_43_0_IMPORT_LOGS_BASELINE_MS: dict[str, float] = {
    "Dashboard": 5617.0,
    "Activity": 4821.0,
    "Market Requests": 5549.0,
}
SPRINT_43_0_PARSER_REVIEW_QUERY_COUNT = 227
SPRINT_43_2_PARSER_REVIEW_BASELINE_MS = 15300.0
SPRINT_43_2_PARSER_REVIEW_TARGET_QUERY_COUNT = 20
SPRINT_43_2_PARSER_REVIEW_TARGET_MS = 2000.0

PROFILE_PAGES: tuple[dict[str, str], ...] = (
    {"key": "dashboard", "label": "Dashboard", "path": "/dashboard"},
    {"key": "notifications", "label": "Notifications", "path": "/notifications"},
    {"key": "parser_review", "label": "Parser Review", "path": "/parser-review"},
    {"key": "activity", "label": "Activity", "path": "/activity"},
    {"key": "search", "label": "Search", "path": "/"},
    {"key": "requests", "label": "Requests", "path": "/requests"},
    {"key": "market_requests", "label": "Market Requests", "path": "/market-requests"},
)

PROFILE_STATUS_OK = "ok"
PROFILE_STATUS_TIMEOUT = "timeout"
PROFILE_STATUS_ERROR = "error"
PROFILE_STATUS_SKIPPED = "skipped"

_active_session: ContextVar[ProfilerSession | None] = ContextVar(
    "performance_profiler_session",
    default=None,
)

_original_send_with_retry: Callable[..., Any] | None = None
_original_template_render: Callable[..., Any] | None = None
_hooks_installed = False


@dataclass
class QueryRecord:
    description: str
    duration_ms: float


@dataclass
class ProfilerSession:
    page: str
    path: str
    queries: list[QueryRecord] = field(default_factory=list)
    render_ms: float = 0.0

    @property
    def query_count(self) -> int:
        return len(self.queries)

    @property
    def database_ms(self) -> float:
        return round(sum(query.duration_ms for query in self.queries), 2)

    def slowest_query(self) -> QueryRecord | None:
        if not self.queries:
            return None
        return max(self.queries, key=lambda item: item.duration_ms)

    def import_logs_query_ms(self) -> float | None:
        """Return the slowest import_logs query duration, if any."""
        import_logs_queries = [
            query
            for query in self.queries
            if "import_logs" in query.description.lower()
        ]
        if not import_logs_queries:
            return None
        return max(query.duration_ms for query in import_logs_queries)

    def record_query(self, description: str, duration_ms: float) -> None:
        self.queries.append(
            QueryRecord(description=description, duration_ms=round(duration_ms, 2))
        )


def _python_processing_ms(
    total_response_ms: float,
    database_ms: float,
    render_ms: float,
) -> float:
    """Derive non-DB, non-render time from the measured wall clock."""
    remaining = total_response_ms - database_ms - render_ms
    return round(max(remaining, 0.0), 2)


@dataclass(frozen=True)
class PageProfileResult:
    page: str
    path: str
    status: str
    status_code: int
    total_response_ms: float
    python_ms: float
    render_ms: float
    database_ms: float
    query_count: int
    slowest_query: str
    slowest_query_ms: float | None
    import_logs_query_ms: float | None = None
    error_message: str = ""

    def to_report_row(self) -> dict[str, Any]:
        slowest = self.slowest_query or "—"
        baseline_ms = SPRINT_43_0_IMPORT_LOGS_BASELINE_MS.get(self.page)
        import_logs_delta_ms: float | None = None
        if baseline_ms is not None and self.import_logs_query_ms is not None:
            import_logs_delta_ms = round(baseline_ms - self.import_logs_query_ms, 2)
        baseline_query_count = (
            SPRINT_43_0_PARSER_REVIEW_QUERY_COUNT if self.page == "Parser Review" else None
        )
        query_count_delta: int | None = None
        if baseline_query_count is not None and self.status == PROFILE_STATUS_OK:
            query_count_delta = baseline_query_count - self.query_count
        parser_review_baseline_ms = (
            SPRINT_43_2_PARSER_REVIEW_BASELINE_MS if self.page == "Parser Review" else None
        )
        parser_review_saved_ms: float | None = None
        if parser_review_baseline_ms is not None and self.status == PROFILE_STATUS_OK:
            parser_review_saved_ms = round(parser_review_baseline_ms - self.total_response_ms, 2)
        return {
            "page": self.page,
            "path": self.path,
            "status": self.status,
            "status_code": self.status_code,
            "total_response_ms": self.total_response_ms,
            "python_ms": self.python_ms,
            "render_ms": self.render_ms,
            "database_ms": self.database_ms,
            "query_count": self.query_count,
            "slowest_query": slowest,
            "slowest_query_ms": self.slowest_query_ms,
            "import_logs_query_ms": self.import_logs_query_ms,
            "import_logs_baseline_ms": baseline_ms,
            "import_logs_delta_ms": import_logs_delta_ms,
            "query_count_baseline": baseline_query_count,
            "query_count_delta": query_count_delta,
            "parser_review_baseline_ms": parser_review_baseline_ms,
            "parser_review_saved_ms": parser_review_saved_ms,
            "error_message": self.error_message or "—",
        }


def _request_table_name(path: str) -> str:
    normalized = str(path)
    if "/rest/v1/" in normalized:
        return normalized.split("/rest/v1/", 1)[-1].split("?", 1)[0].strip("/") or normalized
    return normalized.strip("/") or normalized


def describe_postgrest_request(request: Any) -> str:
    method = getattr(request, "http_method", "GET")
    path = str(getattr(request, "path", ""))
    table = _request_table_name(path)
    params = getattr(request, "params", None)
    if params:
        try:
            param_items = list(dict(params).items())[:6]
        except TypeError:
            param_items = []
        if param_items:
            param_text = "&".join(f"{key}={value}" for key, value in param_items)
            return f"{method} {table}?{param_text}"
    return f"{method} {table}"


def _profiled_send_with_retry(request: Any) -> Any:
    session = _active_session.get()
    if session is None or _original_send_with_retry is None:
        return _original_send_with_retry(request)  # type: ignore[misc]

    description = describe_postgrest_request(request)
    started = time.perf_counter()
    try:
        return _original_send_with_retry(request)
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        session.record_query(description, duration_ms)


def _profiled_template_render(self: jinja2.Template, *args: Any, **kwargs: Any) -> str:
    session = _active_session.get()
    if session is None or _original_template_render is None:
        return _original_template_render(self, *args, **kwargs)  # type: ignore[misc]

    started = time.perf_counter()
    try:
        return _original_template_render(self, *args, **kwargs)  # type: ignore[misc]
    finally:
        session.render_ms += (time.perf_counter() - started) * 1000


def install_profiler_hooks() -> None:
    global _hooks_installed, _original_send_with_retry, _original_template_render
    if _hooks_installed:
        return

    import postgrest._sync.request_builder as postgrest_sync

    _original_send_with_retry = postgrest_sync.send_with_retry
    postgrest_sync.send_with_retry = _profiled_send_with_retry

    _original_template_render = jinja2.Template.render
    jinja2.Template.render = _profiled_template_render  # type: ignore[method-assign]

    _hooks_installed = True


def uninstall_profiler_hooks() -> None:
    global _hooks_installed, _original_send_with_retry, _original_template_render
    if not _hooks_installed:
        return

    import postgrest._sync.request_builder as postgrest_sync

    if _original_send_with_retry is not None:
        postgrest_sync.send_with_retry = _original_send_with_retry
    if _original_template_render is not None:
        jinja2.Template.render = _original_template_render  # type: ignore[method-assign]

    _original_send_with_retry = None
    _original_template_render = None
    _hooks_installed = False


def reset_profiler_hooks() -> None:
    """Restore default hooks (for tests)."""
    uninstall_profiler_hooks()


def _result_from_session(
    session: ProfilerSession,
    *,
    status: str,
    status_code: int,
    total_response_ms: float,
    error_message: str = "",
) -> PageProfileResult:
    slowest = session.slowest_query()
    database_ms = session.database_ms
    render_ms = round(session.render_ms, 2)
    total_ms = round(total_response_ms, 2)
    return PageProfileResult(
        page=session.page,
        path=session.path,
        status=status,
        status_code=status_code,
        total_response_ms=total_ms,
        python_ms=_python_processing_ms(total_ms, database_ms, render_ms),
        render_ms=render_ms,
        database_ms=database_ms,
        query_count=session.query_count,
        slowest_query=slowest.description if slowest else "",
        slowest_query_ms=slowest.duration_ms if slowest else None,
        import_logs_query_ms=session.import_logs_query_ms(),
        error_message=error_message,
    )


def _timeout_result(
    *,
    page: str,
    path: str,
    total_response_ms: float,
    error_message: str,
) -> PageProfileResult:
    total_ms = round(total_response_ms, 2)
    return PageProfileResult(
        page=page,
        path=path,
        status=PROFILE_STATUS_TIMEOUT,
        status_code=0,
        total_response_ms=total_ms,
        python_ms=total_ms,
        render_ms=0.0,
        database_ms=0.0,
        query_count=0,
        slowest_query="",
        slowest_query_ms=None,
        error_message=error_message,
    )


def _skipped_result(
    *,
    page: str,
    path: str,
    error_message: str,
) -> PageProfileResult:
    return PageProfileResult(
        page=page,
        path=path,
        status=PROFILE_STATUS_SKIPPED,
        status_code=0,
        total_response_ms=0.0,
        python_ms=0.0,
        render_ms=0.0,
        database_ms=0.0,
        query_count=0,
        slowest_query="",
        slowest_query_ms=None,
        error_message=error_message,
    )


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, FuturesTimeoutError):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    message = str(exc).lower()
    return "timeout" in message or "timed out" in message


def _fetch_page_response(
    client: TestClient,
    path: str,
    *,
    page_timeout_s: float,
) -> Any:
    return client.get(path, timeout=page_timeout_s)


def profile_page(
    client: TestClient,
    *,
    page: str,
    path: str,
    page_timeout_s: float = DEFAULT_PAGE_TIMEOUT_S,
) -> PageProfileResult:
    session = ProfilerSession(page=page, path=path)
    token: Token = _active_session.set(session)
    profile_context = contextvars.copy_context()
    install_profiler_hooks()
    started = time.perf_counter()
    response = None
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                profile_context.run,
                _fetch_page_response,
                client,
                path,
                page_timeout_s=page_timeout_s,
            )
            try:
                response = future.result(timeout=page_timeout_s)
            except FuturesTimeoutError:
                elapsed_ms = (time.perf_counter() - started) * 1000
                result = _timeout_result(
                    page=page,
                    path=path,
                    total_response_ms=elapsed_ms,
                    error_message=f"Page profiling timed out after {page_timeout_s:.0f}s",
                )
                logger.warning(
                    "Performance profile timeout page=%s path=%s after %.2fs",
                    page,
                    path,
                    page_timeout_s,
                )
                return result
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if _is_timeout_error(exc):
            result = _timeout_result(
                page=page,
                path=path,
                total_response_ms=elapsed_ms,
                error_message=str(exc),
            )
            logger.warning(
                "Performance profile timeout page=%s path=%s error=%s",
                page,
                path,
                exc,
            )
            return result
        result = _result_from_session(
            session,
            status=PROFILE_STATUS_ERROR,
            status_code=0,
            total_response_ms=elapsed_ms,
            error_message=str(exc),
        )
        logger.exception(
            "Performance profile error page=%s path=%s",
            page,
            path,
        )
        return result
    finally:
        _active_session.reset(token)

    total_response_ms = (time.perf_counter() - started) * 1000
    result = _result_from_session(
        session,
        status=PROFILE_STATUS_OK,
        status_code=response.status_code,
        total_response_ms=total_response_ms,
    )
    logger.info(
        "Performance profile page=%s path=%s status=%s total_ms=%.2f python_ms=%.2f "
        "render_ms=%.2f database_ms=%.2f query_count=%s slowest_query=%s",
        page,
        path,
        response.status_code,
        result.total_response_ms,
        result.python_ms,
        result.render_ms,
        result.database_ms,
        result.query_count,
        result.slowest_query or "—",
    )
    return result


@contextmanager
def _profile_as_user(current_user: dict[str, Any]) -> Iterator[None]:
    import app as app_module
    import auth

    original_auth = auth.get_current_user
    original_app = app_module.get_current_user

    def _override(_request: Any) -> dict[str, Any]:
        return current_user

    auth.get_current_user = _override
    app_module.get_current_user = _override
    try:
        yield
    finally:
        auth.get_current_user = original_auth
        app_module.get_current_user = original_app


def profile_all_pages(
    app: FastAPI,
    *,
    current_user: dict[str, Any],
    page_timeout_s: float = DEFAULT_PAGE_TIMEOUT_S,
    total_timeout_s: float = DEFAULT_TOTAL_TIMEOUT_S,
) -> list[PageProfileResult]:
    install_profiler_hooks()
    results: list[PageProfileResult] = []
    profiler_started = time.perf_counter()
    with _profile_as_user(current_user):
        client = TestClient(app)
        for page in PROFILE_PAGES:
            elapsed_total_s = time.perf_counter() - profiler_started
            if elapsed_total_s >= total_timeout_s:
                results.append(
                    _skipped_result(
                        page=page["label"],
                        path=page["path"],
                        error_message=(
                            f"Skipped — profiler total timeout of {total_timeout_s:.0f}s reached"
                        ),
                    )
                )
                continue

            remaining_s = max(total_timeout_s - elapsed_total_s, 0.1)
            effective_page_timeout_s = min(page_timeout_s, remaining_s)
            results.append(
                profile_page(
                    client,
                    page=page["label"],
                    path=page["path"],
                    page_timeout_s=effective_page_timeout_s,
                )
            )
    return results


def build_performance_report(
    app: FastAPI,
    *,
    current_user: dict[str, Any],
    page_timeout_s: float = DEFAULT_PAGE_TIMEOUT_S,
    total_timeout_s: float = DEFAULT_TOTAL_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Profile every dashboard page and return rows for the report template."""
    return [
        result.to_report_row()
        for result in profile_all_pages(
            app,
            current_user=current_user,
            page_timeout_s=page_timeout_s,
            total_timeout_s=total_timeout_s,
        )
    ]
