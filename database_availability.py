"""Centralized Supabase/PostgREST availability error handling."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable, TypeVar
from urllib.parse import urlparse

try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover - test environments without postgrest
    APIError = Exception  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

T = TypeVar("T")

DATABASE_UNAVAILABLE_MESSAGE = (
    "The database is temporarily unavailable. Please try again in a moment."
)

READ_MAX_ATTEMPTS = 2
READ_RETRY_BASE_DELAY_SECONDS = 0.15

_CLOUDFLARE_HTML_MARKERS = (
    "<!doctype html",
    "<html",
    "cloudflare",
    "error code 521",
    "web server is not responding",
)

_JSON_DECODE_MARKERS = (
    "json could not be generated",
    "expecting value",
    "invalid json",
)

_CONNECTION_MARKERS = (
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection timeout",
    "connect timeout",
    "timed out",
    "timeout",
    "temporary failure",
    "name or service not known",
    "failed to establish a new connection",
)

_TRANSIENT_HTTP_CODES = frozenset({500, 502, 503, 504, 521})


class DatabaseUnavailableError(Exception):
    """Raised when Supabase/PostgREST is temporarily unreachable."""

    def __init__(
        self,
        message: str = DATABASE_UNAVAILABLE_MESSAGE,
        *,
        operation: str,
        status_code: int | None = None,
        upstream_host: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code
        self.upstream_host = upstream_host
        self.exception_type = exception_type or "DatabaseUnavailableError"


def get_supabase_upstream_host() -> str | None:
    url = os.environ.get("SUPABASE_URL", "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.hostname or None


def _exception_message(exc: BaseException) -> str:
    return str(exc).lower()


def _contains_html_error_page(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _CLOUDFLARE_HTML_MARKERS)


def extract_http_status_code(exc: BaseException) -> int | None:
    if isinstance(exc, APIError):
        for attr in ("code", "status_code", "status"):
            raw = getattr(exc, attr, None)
            if raw is not None and str(raw).isdigit():
                code = int(str(raw))
                if 100 <= code <= 599:
                    return code

    message = str(exc)
    for pattern in (
        r"\berror code (\d{3})\b",
        r"\bstatus code (\d{3})\b",
        r"\bhttp (\d{3})\b",
        r"\bcloudflare (\d{3})\b",
    ):
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def is_postgrest_validation_error(exc: BaseException) -> bool:
    """Return True for client/validation PostgREST failures that must not be retried."""
    if isinstance(exc, APIError):
        status = extract_http_status_code(exc)
        if status is not None and 400 <= status < 500 and status != 429:
            return True
        pg_code = str(getattr(exc, "code", "") or "")
        if len(pg_code) == 5 and pg_code.isdigit():
            return True

    message = _exception_message(exc)
    if "23505" in message or "duplicate key value violates unique constraint" in message:
        return True
    return False


def is_transient_database_error(exc: BaseException) -> bool:
    """Return True for retryable upstream availability failures."""
    if is_postgrest_validation_error(exc):
        return False

    message = _exception_message(exc)
    if re.search(r"\b4\d{2}\b", message) and any(
        token in message for token in ("bad request", "forbidden", "not found", "unauthorized")
    ):
        return False

    if any(marker in message for marker in _JSON_DECODE_MARKERS):
        return True
    if _contains_html_error_page(message):
        return True
    if any(marker in message for marker in _CONNECTION_MARKERS):
        return True

    status = extract_http_status_code(exc)
    if status is not None and status in _TRANSIENT_HTTP_CODES:
        return True

    if isinstance(exc, APIError):
        if status is not None and status >= 500:
            return True

    exc_name = type(exc).__name__.lower()
    if any(token in exc_name for token in ("timeout", "connect", "connection")):
        return True
    return False


def summarize_exception_message(exc: BaseException, *, max_length: int = 240) -> str:
    """Return a bounded exception summary safe for logs."""
    message = str(exc).replace("\n", " ").strip()
    if _contains_html_error_page(message):
        status = extract_http_status_code(exc)
        status_part = f" status={status}" if status else ""
        host = get_supabase_upstream_host()
        host_part = f" host={host}" if host else ""
        return f"upstream_html_error_page{status_part}{host_part}"

    if len(message) > max_length:
        return message[: max_length - 3] + "..."
    return message


def log_database_availability_error(
    exc: BaseException,
    *,
    operation: str,
    route: str | None = None,
    request_id: str | None = None,
    attempt: int | None = None,
) -> None:
    status = extract_http_status_code(exc)
    host = get_supabase_upstream_host()
    logger.warning(
        "database_unavailable operation=%s route=%s status=%s upstream_host=%s "
        "exception_type=%s request_id=%s attempt=%s summary=%s",
        operation,
        route or "-",
        status if status is not None else "-",
        host or "-",
        type(exc).__name__,
        request_id or "-",
        attempt if attempt is not None else "-",
        summarize_exception_message(exc),
    )


def execute_postgrest_read(
    operation: str,
    fn: Callable[[], T],
    *,
    route: str | None = None,
    request_id: str | None = None,
    max_attempts: int = READ_MAX_ATTEMPTS,
) -> T:
    """Run a read-only PostgREST call with limited retry for transient failures."""
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            if is_postgrest_validation_error(exc):
                raise
            if not is_transient_database_error(exc):
                raise
            last_exc = exc
            log_database_availability_error(
                exc,
                operation=operation,
                route=route,
                request_id=request_id,
                attempt=attempt,
            )
            if attempt < max_attempts:
                time.sleep(READ_RETRY_BASE_DELAY_SECONDS * attempt)
                continue
            raise DatabaseUnavailableError(
                DATABASE_UNAVAILABLE_MESSAGE,
                operation=operation,
                status_code=extract_http_status_code(exc),
                upstream_host=get_supabase_upstream_host(),
                exception_type=type(exc).__name__,
            ) from exc

    if last_exc is not None:
        raise DatabaseUnavailableError(
            DATABASE_UNAVAILABLE_MESSAGE,
            operation=operation,
            status_code=extract_http_status_code(last_exc),
            upstream_host=get_supabase_upstream_host(),
            exception_type=type(last_exc).__name__,
        ) from last_exc
    raise RuntimeError(f"execute_postgrest_read failed without exception: {operation}")
