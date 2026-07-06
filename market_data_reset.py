"""Admin-only market data reset: preview, dry-run, RPC delete, and verification."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from database import get_client

Record = dict[str, Any]

logger = logging.getLogger(__name__)

RESET_CONFIRMATION_TEXT = "RESET MARKET DATA"
RESET_BATCH_SIZE = 100
NOTIFICATION_BATCH_SIZE = 50
DELETE_RETRY_BATCH_SIZE = 25
DELETE_CHUNK_SIZE = RESET_BATCH_SIZE
MAX_DELETE_PASSES = 10_000
RESET_JOB_TTL_SECONDS = 3600
SYSTEM_RESET_OWNER = "__system__"

# Core tables must be empty for emergency success. Notifications are best-effort only.
RESET_STEP_ORDER: tuple[tuple[str, str], ...] = (
    ("request_matches", "request_matches"),
    ("offers", "offers"),
    ("import_logs", "import_logs"),
    ("messages", "messages"),
    ("market_notifications", "notifications"),
    ("orphan_watches", "watches"),
)
OPTIONAL_RESET_STEPS = frozenset({"market_notifications"})

MARKET_NOTIFICATION_TYPES = (
    "request_match",
    "new_lowest_price",
    "excellent_buy",
    "needs_review",
)

RPC_MIGRATION_NOT_INSTALLED = "RPC migration not installed"

DB_HEALTH_KEYS: tuple[tuple[str, str, str], ...] = (
    ("offers_total", "Offers (all statuses)", "offers"),
    ("offers_active", "Offers (active)", "offers"),
    ("import_logs_total", "Import logs", "import_logs"),
    ("messages_total", "Messages", "messages"),
)

VERIFICATION_KEYS: tuple[tuple[str, str], ...] = (
    ("offers_total", "Offers (all statuses)"),
    ("offers_active", "Offers (active)"),
    ("import_logs_total", "Import logs"),
    ("messages_total", "Messages"),
    ("request_matches_total", "Request matches"),
    ("market_notifications_total", "Import/offer notifications"),
)

RESET_ACTIONS: tuple[Record, ...] = (
    {
        "key": "request_matches",
        "label": "Request matches",
        "description": "Generated offer-to-client-request matches from historical imports.",
    },
    {
        "key": "market_notifications",
        "label": "Import/offer notifications",
        "description": "Team notifications tied to imports, offers, or market events.",
    },
    {
        "key": "offers",
        "label": "Offers",
        "description": "All stored offer rows Search and watch detail read from this table.",
        "critical": True,
    },
    {
        "key": "import_logs",
        "label": "Import logs",
        "description": "Activity history, market requests, parser review queue, and import summaries.",
    },
    {
        "key": "messages",
        "label": "Ingestion messages",
        "description": "WhatsApp message rows so new ingests are not blocked as duplicates.",
    },
    {
        "key": "orphan_watches",
        "label": "Orphan watches",
        "description": "Watch catalog rows with no remaining offers after the reset.",
    },
)

PRESERVED_ITEMS: tuple[str, ...] = (
    "Users and team permissions",
    "Dealers, clients, contacts, and groups",
    "Client requests (requests table)",
    "Reference brand mappings",
    "Brand aliases, unknown brands, nicknames, and watch knowledge",
    "WhatsApp connection settings and app configuration",
)

SEARCH_DATA_SOURCES: tuple[str, ...] = (
    "offers (status = active) — primary Search and watch detail source",
    "watches — joined from offers; orphan rows removed after offers delete",
    "dealers — preserved; Search only shows dealers linked to remaining offers",
    "messages — ingestion parent rows for offers/import_logs",
    "import_logs — activity/history only; not queried directly by Search",
)


@dataclass(frozen=True)
class MarketDataResetVerification:
    offers_total: int
    offers_active: int
    import_logs_total: int
    messages_total: int
    request_matches_total: int
    market_notifications_total: int

    @classmethod
    def from_mapping(cls, payload: Record) -> MarketDataResetVerification:
        return cls(
            offers_total=int(payload.get("offers_total") or 0),
            offers_active=int(payload.get("offers_active") or 0),
            import_logs_total=int(payload.get("import_logs_total") or 0),
            messages_total=int(payload.get("messages_total") or 0),
            request_matches_total=int(payload.get("request_matches_total") or 0),
            market_notifications_total=int(payload.get("market_notifications_total") or 0),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "offers_total": self.offers_total,
            "offers_active": self.offers_active,
            "import_logs_total": self.import_logs_total,
            "messages_total": self.messages_total,
            "request_matches_total": self.request_matches_total,
            "market_notifications_total": self.market_notifications_total,
        }

    def is_clean(self) -> bool:
        return all(value == 0 for value in self.as_dict().values())


def is_emergency_reset_success(verification: MarketDataResetVerification) -> bool:
    """Emergency reset succeeds when core market ingestion tables are empty."""
    return (
        verification.offers_total == 0
        and verification.offers_active == 0
        and verification.import_logs_total == 0
        and verification.messages_total == 0
    )


@dataclass(frozen=True)
class MarketDataResetPreview:
    """Counts and labels shown before a reset."""

    counts: dict[str, int]
    reset_actions: tuple[Record, ...] = RESET_ACTIONS
    preserved_items: tuple[str, ...] = PRESERVED_ITEMS
    search_data_sources: tuple[str, ...] = SEARCH_DATA_SOURCES
    delete_mode: str = "hard_delete_rpc_preferred"
    rpc_available: bool = False
    preview_method: str = "count"
    preview_error: str | None = None

    @property
    def has_preview_error(self) -> bool:
        return bool(self.preview_error)


@dataclass
class MarketDataResetResult:
    """Outcome of a dry-run or executed reset."""

    dry_run: bool
    preview: MarketDataResetPreview
    deleted: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    chunk_failures: list[str] = field(default_factory=list)
    verification: MarketDataResetVerification | None = None
    success: bool = False
    method: str = "none"

    @property
    def status_label(self) -> str:
        if self.dry_run:
            return "dry_run"
        return "success" if self.success else "failed"


def preview_market_data_reset(*, load_counts: bool = True) -> MarketDataResetPreview:
    """Return current row counts that would be affected by a reset."""
    if not load_counts:
        return MarketDataResetPreview(
            counts={},
            rpc_available=False,
            preview_method="skipped",
            preview_error=None,
        )
    try:
        counts, rpc_available, method = _count_reset_scope_lightweight()
        return MarketDataResetPreview(
            counts=counts,
            rpc_available=rpc_available,
            preview_method=method,
        )
    except Exception as exc:
        logger.exception("Market data reset preview failed")
        return MarketDataResetPreview(
            counts={},
            rpc_available=_reset_market_data_rpc_supported_cached(),
            preview_method="error",
            preview_error=str(exc),
        )


def verify_market_data_reset() -> MarketDataResetVerification:
    """Return post-reset counts for the market layer Search reads."""
    return MarketDataResetVerification.from_mapping(_collect_verification_counts())


@dataclass(frozen=True)
class DbHealthStatus:
    counts: dict[str, int | None]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, int | None]:
        return dict(self.counts)


def collect_db_health_status() -> DbHealthStatus:
    """Return lightweight head-only counts for core market tables."""
    counts: dict[str, int | None] = {}
    errors: list[str] = []

    try:
        counts["offers_total"] = _count_table_rows("offers")
    except Exception as exc:
        counts["offers_total"] = None
        errors.append(f"offers_total: {exc}")

    try:
        counts["offers_active"] = _count_filtered_rows(
            "offers",
            lambda query: query.eq("status", "active"),
        )
    except Exception as exc:
        counts["offers_active"] = None
        errors.append(f"offers_active: {exc}")

    try:
        counts["import_logs_total"] = _count_table_rows("import_logs")
    except Exception as exc:
        counts["import_logs_total"] = None
        errors.append(f"import_logs_total: {exc}")

    try:
        counts["messages_total"] = _count_table_rows("messages")
    except Exception as exc:
        counts["messages_total"] = None
        errors.append(f"messages_total: {exc}")

    return DbHealthStatus(counts=counts, errors=tuple(errors))


@dataclass(frozen=True)
class ResetStepBatchResult:
    step_key: str
    table: str
    selected_rows: int
    deleted_rows: int
    step_complete: bool
    sample_ids: tuple[str, ...] = ()


@dataclass
class BatchedResetJob:
    job_id: str
    owner_user_id: str
    step_index: int = 0
    deleted: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chunk_failures: list[str] = field(default_factory=list)
    status: str = "running"
    batches_run: int = 0
    verification: MarketDataResetVerification | None = None
    created_at: float = field(default_factory=time.time)
    last_batch: ResetStepBatchResult | None = None


@dataclass(frozen=True)
class BatchedResetProgress:
    job_id: str
    status: str
    current_step: str | None
    step_index: int
    total_steps: int
    deleted: dict[str, int]
    batches_run: int
    batch_size: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    chunk_failures: tuple[str, ...]
    verification: MarketDataResetVerification | None
    success: bool
    last_batch_table: str | None = None
    last_batch_selected: int | None = None
    last_batch_deleted: int | None = None


_RESET_JOBS: dict[str, BatchedResetJob] = {}
_RESET_JOBS_LOCK = Lock()


def create_batched_reset_job(*, owner_user_id: str) -> str:
    """Start a new batched reset job (one HTTP batch call = one committed transaction)."""
    job_id = secrets.token_urlsafe(16)
    deleted = {step_key: 0 for step_key, _table in RESET_STEP_ORDER}
    with _RESET_JOBS_LOCK:
        _purge_expired_reset_jobs()
        _RESET_JOBS[job_id] = BatchedResetJob(
            job_id=job_id,
            owner_user_id=owner_user_id,
            deleted=deleted,
        )
    return job_id


def get_batched_reset_progress(job_id: str, *, owner_user_id: str) -> BatchedResetProgress:
    job = _get_reset_job(job_id, owner_user_id=owner_user_id)
    return _job_to_progress(job)


def run_batched_reset_batch(job_id: str, *, owner_user_id: str) -> BatchedResetProgress:
    """Delete up to RESET_BATCH_SIZE rows for the current step, then commit via PostgREST."""
    job = _get_reset_job(job_id, owner_user_id=owner_user_id)
    if job.status != "running":
        return _job_to_progress(job)

    if job.step_index >= len(RESET_STEP_ORDER):
        _finalize_batched_reset_job(job)
        return _job_to_progress(job)

    step_key, table_name = RESET_STEP_ORDER[job.step_index]
    batch_size = _batch_size_for_step(step_key)
    batch = _run_reset_step_batch(
        step_key,
        table_name,
        batch_size,
        job.chunk_failures,
    )
    job.last_batch = batch
    job.batches_run += 1

    logger.info(
        "reset batch job=%s step=%s table=%s batch_size=%s selected=%s deleted=%s "
        "complete=%s optional=%s sample_ids=%s",
        job_id,
        batch.step_key,
        batch.table,
        batch_size,
        batch.selected_rows,
        batch.deleted_rows,
        batch.step_complete,
        step_key in OPTIONAL_RESET_STEPS,
        batch.sample_ids,
    )

    if batch.selected_rows == 0:
        if batch.step_complete:
            _advance_reset_step(job)
        elif step_key in OPTIONAL_RESET_STEPS:
            _skip_optional_reset_step(
                job,
                step_key,
                f"{step_key}: no rows selected; skipped non-critical notification cleanup",
            )
        else:
            job.status = "failed"
            job.errors.append(
                f"{batch.step_key}: selected 0 rows but step not complete "
                f"(table={batch.table}; repeated zero-select batch loop prevented)"
            )
        return _job_to_progress(job)

    if batch.deleted_rows == 0:
        detail = job.chunk_failures[-1] if job.chunk_failures else "delete returned 0 rows"
        if step_key in OPTIONAL_RESET_STEPS:
            _skip_optional_reset_step(
                job,
                step_key,
                f"{step_key}: selected {batch.selected_rows} rows but deleted 0; "
                f"skipped non-critical step ({detail})",
            )
        else:
            job.status = "failed"
            job.errors.append(
                f"{batch.step_key}: selected {batch.selected_rows} rows but deleted 0 "
                f"(table={batch.table}; {detail})"
            )
        return _job_to_progress(job)

    job.deleted[step_key] = job.deleted.get(step_key, 0) + batch.deleted_rows

    if batch.step_complete:
        _advance_reset_step(job)

    return _job_to_progress(job)


def _batch_size_for_step(step_key: str) -> int:
    if step_key == "market_notifications":
        return NOTIFICATION_BATCH_SIZE
    return RESET_BATCH_SIZE


def _advance_reset_step(job: BatchedResetJob) -> None:
    job.step_index += 1
    if job.step_index >= len(RESET_STEP_ORDER):
        _finalize_batched_reset_job(job)


def _skip_optional_reset_step(job: BatchedResetJob, step_key: str, message: str) -> None:
    job.warnings.append(message)
    logger.warning("reset optional step skipped: %s", message)
    _advance_reset_step(job)


def run_full_batched_reset(*, owner_user_id: str = SYSTEM_RESET_OWNER) -> MarketDataResetResult:
    """Run every batch synchronously until complete or failed."""
    preview = preview_market_data_reset(load_counts=False)
    job_id = create_batched_reset_job(owner_user_id=owner_user_id)
    progress = get_batched_reset_progress(job_id, owner_user_id=owner_user_id)
    while progress.status == "running":
        progress = run_batched_reset_batch(job_id, owner_user_id=owner_user_id)
    return batched_progress_to_result(progress, preview=preview)


def batched_progress_to_result(
    progress: BatchedResetProgress,
    *,
    preview: MarketDataResetPreview | None = None,
) -> MarketDataResetResult:
    preview = preview or preview_market_data_reset(load_counts=False)
    errors = list(progress.errors)
    verification = progress.verification
    success = progress.success
    if progress.status == "complete" and verification and not success:
        errors.append(
            "Reset finished but core market tables are not all zero "
            f"(offers={verification.offers_total}, import_logs={verification.import_logs_total}, "
            f"messages={verification.messages_total})."
        )
    elif progress.status == "failed" and not errors:
        errors.append("Batched reset failed.")
    return MarketDataResetResult(
        dry_run=False,
        preview=preview,
        deleted=dict(progress.deleted),
        errors=errors,
        chunk_failures=list(progress.chunk_failures),
        verification=verification,
        success=success,
        method="batched",
    )


def run_emergency_market_data_reset(*, owner_user_id: str = SYSTEM_RESET_OWNER) -> MarketDataResetResult:
    """Execute market reset in committed batches (for synchronous callers/tests)."""
    return run_full_batched_reset(owner_user_id=owner_user_id)


def _purge_expired_reset_jobs() -> None:
    cutoff = time.time() - RESET_JOB_TTL_SECONDS
    expired = [job_id for job_id, job in _RESET_JOBS.items() if job.created_at < cutoff]
    for job_id in expired:
        _RESET_JOBS.pop(job_id, None)


def _get_reset_job(job_id: str, *, owner_user_id: str) -> BatchedResetJob:
    with _RESET_JOBS_LOCK:
        _purge_expired_reset_jobs()
        job = _RESET_JOBS.get(job_id)
    if job is None:
        raise KeyError(f"Reset job not found: {job_id}")
    if job.owner_user_id != owner_user_id:
        raise PermissionError("Reset job belongs to another user.")
    return job


def _finalize_batched_reset_job(job: BatchedResetJob) -> None:
    try:
        job.verification = verify_market_data_reset()
    except Exception as exc:
        logger.exception("Post-reset verification failed")
        job.errors.append(f"verification: {exc}")
        job.verification = None
        job.status = "failed"
        return

    if job.verification and is_emergency_reset_success(job.verification):
        job.status = "complete"
    else:
        job.status = "failed"
        job.errors.append("Reset finished but core market tables are not all zero.")


def _job_to_progress(job: BatchedResetJob) -> BatchedResetProgress:
    current_step = None
    if job.step_index < len(RESET_STEP_ORDER):
        current_step = RESET_STEP_ORDER[job.step_index][0]
    verification = job.verification
    success = job.status == "complete" and (
        verification is not None and is_emergency_reset_success(verification)
    )
    last_batch = job.last_batch
    current_batch_size = (
        _batch_size_for_step(current_step)
        if current_step is not None
        else RESET_BATCH_SIZE
    )
    return BatchedResetProgress(
        job_id=job.job_id,
        status=job.status,
        current_step=current_step,
        step_index=job.step_index,
        total_steps=len(RESET_STEP_ORDER),
        deleted=dict(job.deleted),
        batches_run=job.batches_run,
        batch_size=current_batch_size,
        errors=tuple(job.errors),
        warnings=tuple(job.warnings),
        chunk_failures=tuple(job.chunk_failures),
        verification=verification,
        success=success,
        last_batch_table=last_batch.table if last_batch else None,
        last_batch_selected=last_batch.selected_rows if last_batch else None,
        last_batch_deleted=last_batch.deleted_rows if last_batch else None,
    )


def clear_batched_reset_jobs_for_tests() -> None:
    """Remove in-memory reset jobs (test helper)."""
    with _RESET_JOBS_LOCK:
        _RESET_JOBS.clear()


def _reset_market_data_rpc_supported_cached() -> bool:
    return _reset_market_data_rpc_supported is True


def run_market_data_reset(*, dry_run: bool = True) -> MarketDataResetResult:
    """Preview or execute the scoped market data reset."""
    preview = preview_market_data_reset()
    if dry_run:
        errors: list[str] = []
        if preview.has_preview_error:
            errors.append(preview.preview_error or "Preview count query failed.")
        return MarketDataResetResult(
            dry_run=True,
            preview=preview,
            errors=errors,
            method=preview.preview_method,
        )

    return run_full_batched_reset(owner_user_id=SYSTEM_RESET_OWNER)


_reset_market_data_rpc_supported: bool | None = None


def _is_rpc_missing_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "reset_market_data_admin" in message
        or "pgrst202" in message
        or "42883" in message
        or "does not exist" in message
    )


def reset_market_data_rpc_supported() -> bool:
    """Return True when reset_market_data_admin() RPC exists."""
    global _reset_market_data_rpc_supported
    if _reset_market_data_rpc_supported is not None:
        return _reset_market_data_rpc_supported
    payload = _try_fetch_rpc_preview_counts()
    return payload is not None


def _reset_market_data_rpc_supported() -> bool:
    return reset_market_data_rpc_supported()


def _try_fetch_rpc_preview_counts() -> Record | None:
    """Return RPC dry-run payload or None when the function is not installed."""
    global _reset_market_data_rpc_supported
    try:
        response = get_client().rpc("reset_market_data_admin", {"dry_run": True}).execute()
        payload = response.data
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        payload = payload or {}
        _reset_market_data_rpc_supported = True
        return payload
    except Exception as exc:
        if _is_rpc_missing_error(exc):
            _reset_market_data_rpc_supported = False
            return None
        raise


def _run_reset_market_data_rpc_dry_run() -> Record:
    payload = _try_fetch_rpc_preview_counts()
    if payload is None:
        raise RuntimeError("reset_market_data_admin RPC is not installed.")
    return payload


def _run_reset_market_data_rpc() -> Record:
    response = get_client().rpc("reset_market_data_admin", {"dry_run": False}).execute()
    payload = response.data
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload or {}


def _collect_verification_counts() -> Record:
    market_notification_filter = _market_notification_filter_expr()
    return {
        "offers_total": _count_table_rows("offers"),
        "offers_active": _count_filtered_rows("offers", lambda query: query.eq("status", "active")),
        "import_logs_total": _count_table_rows("import_logs"),
        "messages_total": _count_table_rows("messages"),
        "request_matches_total": _count_table_rows("request_matches"),
        "market_notifications_total": _count_filtered_rows(
            "notifications",
            lambda query: query.or_(market_notification_filter),
        ),
    }


def _count_reset_scope_lightweight() -> tuple[dict[str, int], bool, str]:
    rpc_payload = _try_fetch_rpc_preview_counts()
    if rpc_payload is not None:
        verification = rpc_payload.get("verification") or {}
        counts = _counts_from_verification_mapping(verification)
        counts["parser_review_imports"] = _count_parser_review_import_logs()
        return counts, True, "rpc"

    verification = _collect_verification_counts()
    counts = _counts_from_verification_mapping(verification)
    counts["parser_review_imports"] = _count_parser_review_import_logs()
    return counts, False, "count"


def _counts_from_verification_mapping(verification: Record) -> dict[str, int]:
    return {
        "request_matches": int(verification.get("request_matches_total") or 0),
        "market_notifications": int(verification.get("market_notifications_total") or 0),
        "offers": int(verification.get("offers_total") or 0),
        "offers_active": int(verification.get("offers_active") or 0),
        "import_logs": int(verification.get("import_logs_total") or 0),
        "messages": int(verification.get("messages_total") or 0),
        "orphan_watches": _count_table_rows("watches"),
    }


def _count_parser_review_import_logs() -> int:
    """Count warning imports still on the parser review queue without loading summary JSON."""
    return _count_filtered_rows("import_logs", _apply_parser_review_count_filter)


def _apply_parser_review_count_filter(query: Any) -> Any:
    return query.or_(
        "and(status.eq.warning,"
        "or(summary->parser_review_ignored.is.null,summary->parser_review_ignored.eq.false),"
        "or(summary->parser_reviewed.is.null,summary->parser_reviewed.eq.false))"
    )


def _count_reset_scope() -> dict[str, int]:
    counts, _rpc_available, _method = _count_reset_scope_lightweight()
    return counts


def _count_table_rows(table_name: str) -> int:
    response = (
        get_client()
        .table(table_name)
        .select("id", count="exact")
        .limit(0)
        .execute()
    )
    return int(response.count or 0)


def _count_filtered_rows(table_name: str, apply_filter: Any) -> int:
    request = get_client().table(table_name).select("id", count="exact").limit(0)
    request = apply_filter(request)
    response = request.execute()
    return int(response.count or 0)


def _market_notification_filter_expr() -> str:
    market_types = ",".join(f"type.eq.{value}" for value in MARKET_NOTIFICATION_TYPES)
    return f"related_offer_id.not.is.null,related_import_log_id.not.is.null,{market_types}"


def _count_orphan_watches() -> int:
    """After offers are deleted, every watch row becomes an orphan."""
    return _count_table_rows("watches")


def _collect_ingestion_message_ids() -> list[str]:
    message_ids: set[str] = set()
    for row in _select_all_rows("offers", "message_id"):
        if row.get("message_id"):
            message_ids.add(str(row["message_id"]))
    for row in _select_all_rows("import_logs", "message_id"):
        if row.get("message_id"):
            message_ids.add(str(row["message_id"]))
    return sorted(message_ids)


def _select_all_rows(table_name: str, fields: str) -> list[Record]:
    rows: list[Record] = []
    offset = 0
    page_size = 1000
    while True:
        response = (
            get_client()
            .table(table_name)
            .select(fields)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _select_market_notification_ids() -> list[str]:
    rows: list[Record] = []
    offset = 0
    page_size = 1000
    filter_expr = _market_notification_filter_expr()
    while True:
        response = (
            get_client()
            .table("notifications")
            .select("id")
            .or_(filter_expr)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return sorted({str(row["id"]) for row in rows if row.get("id")})


def _delete_ids_with_count(table_name: str, ids: list[str]) -> int:
    """Delete by id and return count without loading full row payloads in the response."""
    if not ids:
        return 0
    response = (
        get_client()
        .table(table_name)
        .delete(count="exact")
        .in_("id", ids)
        .execute()
    )
    if response.count is not None:
        return int(response.count)
    return len(response.data or [])


def _is_postgrest_400_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "400" in message
        or "bad request" in message
        or "json could not be generated" in message
    )


def _log_delete_batch(
    table_name: str,
    *,
    batch_size: int,
    selected_rows: int,
    deleted_rows: int,
    sample_ids: list[str],
    error: str | None = None,
) -> None:
    sample = sample_ids[:3]
    if error:
        logger.error(
            "reset delete table=%s batch_size=%s selected=%s deleted=%s sample_ids=%s error=%s",
            table_name,
            batch_size,
            selected_rows,
            deleted_rows,
            sample,
            error,
        )
    else:
        logger.info(
            "reset delete table=%s batch_size=%s selected=%s deleted=%s sample_ids=%s",
            table_name,
            batch_size,
            selected_rows,
            deleted_rows,
            sample,
        )


def _delete_ids_with_retry(
    table_name: str,
    ids: list[str],
    *,
    batch_size: int,
    chunk_failures: list[str],
) -> tuple[int, str | None]:
    """Delete ids; on PostgREST 400 retry the same ids in smaller chunks."""
    cleaned_ids = _normalize_ids(ids)
    if not cleaned_ids:
        return 0, None

    try:
        deleted_rows = _delete_ids_with_count(table_name, cleaned_ids)
        _log_delete_batch(
            table_name,
            batch_size=batch_size,
            selected_rows=len(cleaned_ids),
            deleted_rows=deleted_rows,
            sample_ids=cleaned_ids,
        )
        return deleted_rows, None
    except Exception as exc:
        if not _is_postgrest_400_error(exc) or len(cleaned_ids) <= DELETE_RETRY_BATCH_SIZE:
            sample = cleaned_ids[:3]
            message = (
                f"{table_name}: delete failed for {len(cleaned_ids)} id(s) "
                f"sample={sample}: {exc}"
            )
            chunk_failures.append(message)
            _log_delete_batch(
                table_name,
                batch_size=batch_size,
                selected_rows=len(cleaned_ids),
                deleted_rows=0,
                sample_ids=cleaned_ids,
                error=str(exc),
            )
            return 0, message

    deleted_total = 0
    for offset in range(0, len(cleaned_ids), DELETE_RETRY_BATCH_SIZE):
        chunk = cleaned_ids[offset : offset + DELETE_RETRY_BATCH_SIZE]
        try:
            chunk_deleted = _delete_ids_with_count(table_name, chunk)
            deleted_total += chunk_deleted
            _log_delete_batch(
                table_name,
                batch_size=DELETE_RETRY_BATCH_SIZE,
                selected_rows=len(chunk),
                deleted_rows=chunk_deleted,
                sample_ids=chunk,
            )
        except Exception as retry_exc:
            sample = chunk[:3]
            message = (
                f"{table_name}: delete failed at retry batch size {DELETE_RETRY_BATCH_SIZE} "
                f"for {len(chunk)} id(s) sample={sample}: {retry_exc}"
            )
            chunk_failures.append(message)
            _log_delete_batch(
                table_name,
                batch_size=DELETE_RETRY_BATCH_SIZE,
                selected_rows=len(chunk),
                deleted_rows=0,
                sample_ids=chunk,
                error=str(retry_exc),
            )
            return 0, message

    _log_delete_batch(
        table_name,
        batch_size=DELETE_RETRY_BATCH_SIZE,
        selected_rows=len(cleaned_ids),
        deleted_rows=deleted_total,
        sample_ids=cleaned_ids,
    )
    return deleted_total, None


def _run_reset_step_batch(
    step_key: str,
    table_name: str,
    batch_size: int,
    chunk_failures: list[str],
) -> ResetStepBatchResult:
    if step_key == "market_notifications":
        return _delete_market_notifications_batch(step_key, batch_size, chunk_failures)
    return _delete_single_table_batch(step_key, table_name, batch_size, chunk_failures)


def _delete_single_table_batch(
    step_key: str,
    table_name: str,
    batch_size: int,
    chunk_failures: list[str],
) -> ResetStepBatchResult:
    """Delete up to batch_size rows; each call is its own committed PostgREST request."""
    ids = _select_id_batch(table_name, batch_size)
    selected_rows = len(ids)
    if selected_rows == 0:
        try:
            remaining = _count_table_rows(table_name)
        except Exception as exc:
            chunk_failures.append(f"{table_name}: count failed: {exc}")
            logger.error("reset batch table=%s selected=0 count failed: %s", table_name, exc)
            return ResetStepBatchResult(
                step_key=step_key,
                table=table_name,
                selected_rows=0,
                deleted_rows=0,
                step_complete=False,
            )
        return ResetStepBatchResult(
            step_key=step_key,
            table=table_name,
            selected_rows=0,
            deleted_rows=0,
            step_complete=remaining == 0,
        )

    deleted_rows, delete_error = _delete_ids_with_retry(
        table_name,
        ids,
        batch_size=batch_size,
        chunk_failures=chunk_failures,
    )
    if deleted_rows == 0 and selected_rows > 0 and delete_error is None:
        chunk_failures.append(
            f"{table_name}: delete returned 0 rows for {selected_rows} selected id(s)"
        )

    return ResetStepBatchResult(
        step_key=step_key,
        table=table_name,
        selected_rows=selected_rows,
        deleted_rows=deleted_rows,
        step_complete=selected_rows < batch_size,
        sample_ids=tuple(ids[:3]),
    )


def _delete_market_notifications_batch(
    step_key: str,
    batch_size: int,
    chunk_failures: list[str],
) -> ResetStepBatchResult:
    table_name = "notifications"
    filter_expr = _market_notification_filter_expr()
    response = (
        get_client()
        .table(table_name)
        .select("id")
        .or_(filter_expr)
        .limit(batch_size)
        .execute()
    )
    ids = [str(row["id"]) for row in response.data or [] if row.get("id")]
    selected_rows = len(ids)
    if selected_rows == 0:
        try:
            remaining = _count_filtered_rows(
                table_name,
                lambda query: query.or_(filter_expr),
            )
        except Exception as exc:
            chunk_failures.append(f"notifications: count failed: {exc}")
            logger.error("reset batch table=notifications selected=0 count failed: %s", exc)
            return ResetStepBatchResult(
                step_key=step_key,
                table=table_name,
                selected_rows=0,
                deleted_rows=0,
                step_complete=False,
            )
        return ResetStepBatchResult(
            step_key=step_key,
            table=table_name,
            selected_rows=0,
            deleted_rows=0,
            step_complete=remaining == 0,
        )

    deleted_rows, delete_error = _delete_ids_with_retry(
        table_name,
        ids,
        batch_size=batch_size,
        chunk_failures=chunk_failures,
    )
    if deleted_rows == 0 and selected_rows > 0 and delete_error is None:
        chunk_failures.append(
            f"notifications: delete returned 0 rows for {selected_rows} selected id(s)"
        )

    return ResetStepBatchResult(
        step_key=step_key,
        table=table_name,
        selected_rows=selected_rows,
        deleted_rows=deleted_rows,
        step_complete=selected_rows < batch_size,
        sample_ids=tuple(ids[:3]),
    )


def _delete_market_notifications_strict(chunk_failures: list[str]) -> int:
    return _delete_rows_by_ids_strict(
        "notifications",
        _select_market_notification_ids(),
        chunk_failures,
    )


def _delete_orphan_watches_strict(chunk_failures: list[str]) -> int:
    offer_watch_ids = {
        str(row.get("watch_id"))
        for row in _select_all_rows("offers", "watch_id")
        if row.get("watch_id")
    }
    orphan_ids = [
        str(row["id"])
        for row in _select_all_rows("watches", "id")
        if row.get("id") and str(row["id"]) not in offer_watch_ids
    ]
    return _delete_rows_by_ids_strict("watches", orphan_ids, chunk_failures)


def _delete_all_rows_strict(table_name: str, chunk_failures: list[str]) -> int:
    deleted_total = 0
    step_key = table_name
    for pass_index in range(MAX_DELETE_PASSES):
        batch = _delete_single_table_batch(
            step_key,
            table_name,
            RESET_BATCH_SIZE,
            chunk_failures,
        )
        deleted_total += batch.deleted_rows
        if batch.step_complete:
            break
        if batch.selected_rows == 0 or batch.deleted_rows == 0:
            break
    else:
        chunk_failures.append(f"{table_name}: exceeded max delete passes")

    return deleted_total


def _select_id_batch(table_name: str, limit: int) -> list[str]:
    response = (
        get_client()
        .table(table_name)
        .select("id")
        .limit(limit)
        .execute()
    )
    return [str(row["id"]) for row in response.data or [] if row.get("id")]


def _delete_rows_by_ids_strict(
    table_name: str,
    row_ids: list[str],
    chunk_failures: list[str],
) -> int:
    cleaned_ids = _normalize_ids(row_ids)
    if not cleaned_ids:
        return 0

    deleted = 0
    for offset in range(0, len(cleaned_ids), RESET_BATCH_SIZE):
        chunk = cleaned_ids[offset : offset + RESET_BATCH_SIZE]
        remaining_before = _count_rows_with_ids(table_name, chunk)
        if remaining_before == 0:
            continue

        try:
            get_client().table(table_name).delete().in_("id", chunk).execute()
        except Exception as exc:
            message = (
                f"{table_name} chunk offset={offset} size={len(chunk)} "
                f"ids={chunk[:3]} error={exc}"
            )
            logger.error(message)
            chunk_failures.append(message)
            continue

        remaining_after = _count_rows_with_ids(table_name, chunk)
        chunk_deleted = max(remaining_before - remaining_after, 0)
        deleted += chunk_deleted
        if remaining_after > 0:
            message = (
                f"{table_name} chunk offset={offset} size={len(chunk)} "
                f"still has {remaining_after} row(s); sample ids={chunk[:5]}"
            )
            logger.error(message)
            chunk_failures.append(message)

    return deleted


def _count_rows_with_ids(table_name: str, row_ids: list[str]) -> int:
    if not row_ids:
        return 0
    response = (
        get_client()
        .table(table_name)
        .select("id", count="exact")
        .in_("id", row_ids)
        .limit(0)
        .execute()
    )
    return int(response.count or 0)


def _normalize_ids(raw_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_ids:
        if raw is None:
            continue
        cleaned = str(raw).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized
