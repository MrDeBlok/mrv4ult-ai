"""Temporary ingest lifecycle tracing for endless-import investigation."""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

logger = logging.getLogger("mrv4ult.ingest.lifecycle")

TraceContext = dict[str, Any]

_seen_by_whatsapp_id: dict[str, list[str]] = {}
_seen_by_message_hash: dict[str, list[str]] = {}


def compute_message_hash(text: str) -> str:
    """Stable short hash for duplicate detection in logs."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _register_repeat(bucket: dict[str, list[str]], key: str, trace_id: str) -> int:
    traces = bucket.setdefault(key, [])
    traces.append(trace_id)
    return len(traces)


def start_import_trace(
    *,
    text: str,
    source: str,
    whatsapp_message_id: str | None = None,
) -> TraceContext:
    """Log START IMPORT and detect repeated keys within this process."""
    trace_id = str(uuid.uuid4())
    message_hash = compute_message_hash(text)
    context: TraceContext = {
        "trace_id": trace_id,
        "source": source,
        "whatsapp_message_id": whatsapp_message_id,
        "message_hash": message_hash,
        "import_log_id": None,
    }

    logger.info(
        "START IMPORT: trace_id=%s import_log_id=pending whatsapp_message_id=%s message_hash=%s source=%s",
        trace_id,
        whatsapp_message_id or "none",
        message_hash,
        source,
    )

    if whatsapp_message_id:
        repeat_count = _register_repeat(_seen_by_whatsapp_id, whatsapp_message_id, trace_id)
        if repeat_count > 1:
            logger.warning(
                "REPEATED IMPORT: whatsapp_message_id=%s count=%s trace_ids=%s",
                whatsapp_message_id,
                repeat_count,
                _seen_by_whatsapp_id[whatsapp_message_id],
            )

    hash_repeat_count = _register_repeat(_seen_by_message_hash, message_hash, trace_id)
    if hash_repeat_count > 1:
        logger.warning(
            "REPEATED IMPORT: message_hash=%s count=%s trace_ids=%s whatsapp_message_id=%s",
            message_hash,
            hash_repeat_count,
            _seen_by_message_hash[message_hash],
            whatsapp_message_id or "none",
        )

    return context


def bind_import_log_id(context: TraceContext, import_log_id: str | None) -> None:
    if import_log_id:
        context["import_log_id"] = import_log_id


def end_import_trace(
    context: TraceContext,
    *,
    duration_ms: int,
    offers_created: int,
    status: str,
) -> None:
    """Log END IMPORT for a completed or short-circuited ingest attempt."""
    logger.info(
        "END IMPORT: trace_id=%s import_log_id=%s whatsapp_message_id=%s message_hash=%s "
        "source=%s duration_ms=%s offers_created=%s status=%s",
        context["trace_id"],
        context.get("import_log_id") or "none",
        context.get("whatsapp_message_id") or "none",
        context["message_hash"],
        context["source"],
        duration_ms,
        offers_created,
        status,
    )


def reset_import_trace_state() -> None:
    """Reset in-process repeat counters (tests only)."""
    _seen_by_whatsapp_id.clear()
    _seen_by_message_hash.clear()
