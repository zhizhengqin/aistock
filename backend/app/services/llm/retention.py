"""Bounded, retry-safe cleanup for internal LLM step payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, exists, not_, null, or_, select, update
from sqlalchemy.orm import Session

from app.models.llm_execution import LlmCallAttempt, LlmTokenReservation
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord


TERMINAL_TASK_STATUSES = ("success", "failed", "failed_unknown")
BLOCKING_OUTBOX_STATUSES = ("pending", "locked")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _open_session(session_or_factory: Session | Callable[[], Session]) -> tuple[Session, bool]:
    if isinstance(session_or_factory, Session):
        return session_or_factory, False
    return session_or_factory(), True


def cleanup_llm_audit_payloads(
    session_or_factory: Session | Callable[[], Session],
    *,
    clock: Callable[[], datetime] | None = None,
    retention_days: int = 90,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Clear old internal response bodies while retaining audit rows and metadata.

    The cutoff is computed once per invocation.  Each batch uses its own short
    transaction when a session factory is supplied, so retries cannot hold a
    transaction across batches and a partially completed run is safe to resume.
    """

    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    batch_size = min(int(batch_size), 500)
    now = (clock or _utc_now)()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=int(retention_days))
    affected_rows = 0
    batches = 0

    while True:
        session, owned = _open_session(session_or_factory)
        try:
            active_lease = and_(
                TaskRecord.execution_token.is_not(None),
                TaskRecord.lease_expires_at.is_not(None),
                TaskRecord.lease_expires_at > now,
            )
            blocked_outbox = exists(
                select(TaskOutbox.id).where(
                    TaskOutbox.task_id == TaskRecord.id,
                    TaskOutbox.status.in_(BLOCKING_OUTBOX_STATUSES),
                )
            )
            reserved_tokens = exists(
                select(LlmTokenReservation.id).where(
                    LlmTokenReservation.task_id == TaskRecord.id,
                    LlmTokenReservation.status == "reserved",
                )
            )
            candidates = (
                select(LlmCallAttempt.id)
                .join(TaskRecord, TaskRecord.id == LlmCallAttempt.task_id)
                .where(
                    LlmCallAttempt.created_at < cutoff,
                    LlmCallAttempt.status.in_(TERMINAL_TASK_STATUSES),
                    TaskRecord.status.in_(TERMINAL_TASK_STATUSES),
                    not_(active_lease),
                    not_(blocked_outbox),
                    not_(reserved_tokens),
                    or_(
                        LlmCallAttempt.result_json.is_not(None),
                        LlmCallAttempt.response_metadata_json.is_not(None),
                    ),
                )
                .order_by(LlmCallAttempt.created_at, LlmCallAttempt.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            ids = list(session.execute(candidates).scalars())
            if not ids:
                session.commit()
                if owned:
                    session.close()
                break
            updated = session.execute(
                update(LlmCallAttempt)
                .where(LlmCallAttempt.id.in_(ids))
                # JSON columns use ``none_as_null=False``; explicit SQL NULL is
                # required so the next retry does not select JSON literal null.
                .values(result_json=null(), response_metadata_json=null())
            )
            session.commit()
            rows = int(updated.rowcount or 0)
            affected_rows += rows
            batches += 1
            if owned:
                session.close()
        except Exception:
            session.rollback()
            if owned:
                session.close()
            raise

    return {
        "affected_rows": affected_rows,
        "batches": batches,
        "cutoff": cutoff,
    }


__all__ = ["cleanup_llm_audit_payloads"]
