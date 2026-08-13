"""Atomic task submission and model snapshotting.

Every user or scheduler task enters the system through this service.  The
service owns one short database transaction that locks the singleton runtime
setting, consumes membership quota, writes the task input snapshot and emits
the matching transactional outbox row before committing once.
"""

from __future__ import annotations

import hashlib
import json
import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Mapping

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.llm_config import LlmModelConfig, LlmModelTestRun, LlmRuntimeSetting
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.services import membership
from app.services.llm.config_service import runtime_fingerprint
from app.services.llm.types import ModelLifecycle


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "token",
    "authorization",
    "password",
    "passwd",
    "secret",
    "cookie",
)


class TaskSubmissionError(HTTPException):
    """Safe, stable error raised before task/outbox commit."""

    def __init__(self, code: str, message: str, *, status_code: int = 409):
        self.code = code
        self.user_message = message
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    task_type: str
    user_id: int | None
    feature: str | None
    feature_cost: int
    args: dict[str, object]
    input_snapshot: dict[str, object]
    prompt_version: str
    requires_llm: bool = True


@dataclass(frozen=True, slots=True)
class TaskSubmissionResult:
    tasks: list[TaskRecord]
    outboxes: list[TaskOutbox]

    @property
    def task(self) -> TaskRecord:
        if not self.tasks:
            raise IndexError("empty task submission result")
        return self.tasks[0]

    @property
    def task_ids(self) -> list[int]:
        return [task.id for task in self.tasks]


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _json_value(value: Any, *, key: object | None = None) -> Any:
    """Return a JSON-safe value without ever falling back to ``repr``."""

    if _is_sensitive_key(key):
        return "[REDACTED]"
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TaskSubmissionError("task_input_invalid", "任务输入包含不可序列化的数值", status_code=422)
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_value(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TaskSubmissionError("task_input_invalid", "任务输入包含不可序列化的值", status_code=422)


def sanitize_input_snapshot(input_snapshot: Mapping[str, object], args: Mapping[str, object]) -> tuple[dict, str]:
    """Sanitize and canonicalize a task payload, returning body and SHA-256."""

    if not isinstance(input_snapshot, Mapping) or not isinstance(args, Mapping):
        raise TaskSubmissionError("task_input_invalid", "任务输入格式无效", status_code=422)
    snapshot = _json_value(dict(input_snapshot))
    if not isinstance(snapshot, dict):  # pragma: no cover - guarded above
        raise TaskSubmissionError("task_input_invalid", "任务输入格式无效", status_code=422)
    # Dispatcher parameters are part of the durable snapshot.  Keeping them
    # under a private key avoids changing the public task input shape while
    # ensuring a retry can reconstruct the exact ARQ function call.
    snapshot["_args"] = _json_value(dict(args))
    try:
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise TaskSubmissionError("task_input_invalid", "任务输入无法序列化", status_code=422) from None
    return snapshot, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskSubmissionService:
    """Create one or more tasks in one caller-owned database transaction."""

    def __init__(self, db: Session):
        self.db = db

    def _locked_settings(self) -> LlmRuntimeSetting:
        setting = self.db.execute(
            select(LlmRuntimeSetting)
            .where(LlmRuntimeSetting.id == 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if setting is None:
            setting = LlmRuntimeSetting(id=1)
            self.db.add(setting)
            self.db.flush()
        return setting

    def _validated_default(self, setting: LlmRuntimeSetting) -> LlmModelConfig:
        if not setting.default_model_config_id:
            raise TaskSubmissionError("llm_not_configured", "尚未配置可用的大模型，请联系管理员", status_code=503)
        config = self.db.execute(
            select(LlmModelConfig).where(LlmModelConfig.id == setting.default_model_config_id)
        ).scalar_one_or_none()
        if (
            config is None
            or config.deleted_at is not None
            or str(config.lifecycle_status) != ModelLifecycle.ACTIVE.value
            or not config.verified_test_id
        ):
            raise TaskSubmissionError("llm_not_configured", "尚未配置可用的大模型，请联系管理员", status_code=503)
        test_run = self.db.get(LlmModelTestRun, config.verified_test_id)
        try:
            expected_fingerprint = runtime_fingerprint(
                provider=config.provider,
                model_name=config.model_name,
                base_url=config.base_url,
                credential_version=config.credential_version,
                max_output_tokens=config.max_output_tokens or 4096,
            )
        except Exception:
            expected_fingerprint = None
        if (
            test_run is None
            or test_run.model_config_id != config.id
            or test_run.status != "success"
            or test_run.runtime_fingerprint != config.runtime_fingerprint
            or expected_fingerprint != config.runtime_fingerprint
        ):
            raise TaskSubmissionError("llm_not_configured", "尚未配置可用的大模型，请联系管理员", status_code=503)
        return config

    def _consume_quotas(self, submissions: list[TaskSubmission]) -> None:
        totals: dict[tuple[int, str], int] = {}
        users: dict[int, Any] = {}
        for submission in submissions:
            if submission.feature_cost < 0:
                raise TaskSubmissionError("task_cost_invalid", "任务配额扣减数量无效", status_code=422)
            if submission.user_id is None or not submission.feature or submission.feature_cost == 0:
                continue
            # User objects are loaded once for the caller transaction.  The
            # API passes a User in ordinary flows; service callers may pass an
            # integer and use the lightweight query fallback.
            key = (int(submission.user_id), submission.feature)
            totals[key] = totals.get(key, 0) + submission.feature_cost
        if not totals:
            return
        from app.models.user import User

        for (user_id, feature), cost in totals.items():
            user = users.get(user_id)
            if user is None:
                user = self.db.get(User, user_id)
                if user is None:
                    raise TaskSubmissionError("user_not_found", "用户不存在", status_code=404)
                users[user_id] = user
            membership.check_and_consume(self.db, user, feature, cost=cost)

    def _submit_many(self, submissions: list[TaskSubmission]) -> TaskSubmissionResult:
        if not submissions:
            raise TaskSubmissionError("task_input_invalid", "至少需要一个任务", status_code=422)
        if any(not item.task_type or not item.prompt_version for item in submissions):
            raise TaskSubmissionError("task_input_invalid", "任务类型和提示词版本不能为空", status_code=422)
        try:
            default_config: LlmModelConfig | None = None
            if any(item.requires_llm for item in submissions):
                setting = self._locked_settings()
                default_config = self._validated_default(setting)

            # A batch consumes each (user, feature) quota exactly once before
            # any task row is flushed.  A later failure therefore rolls back
            # all rows and the single usage increment together.
            self._consume_quotas(submissions)
            tasks: list[TaskRecord] = []
            outboxes: list[TaskOutbox] = []
            for submission in submissions:
                snapshot, snapshot_hash = sanitize_input_snapshot(
                    submission.input_snapshot,
                    submission.args,
                )
                task = TaskRecord(
                    task_type=submission.task_type,
                    user_id=submission.user_id,
                    status="pending",
                    progress=0,
                    model_config_id=default_config.id if submission.requires_llm and default_config else None,
                    input_snapshot=snapshot,
                    input_snapshot_hash=snapshot_hash,
                    prompt_version=submission.prompt_version,
                )
                self.db.add(task)
                self.db.flush()
                outbox = TaskOutbox(task_id=task.id)
                self.db.add(outbox)
                self.db.flush()
                tasks.append(task)
                outboxes.append(outbox)
            self.db.commit()
            return TaskSubmissionResult(tasks=tasks, outboxes=outboxes)
        except TaskSubmissionError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

    def submit(self, submission: TaskSubmission) -> TaskSubmissionResult:
        return self._submit_many([submission])

    def submit_batch(self, submissions: list[TaskSubmission]) -> TaskSubmissionResult:
        return self._submit_many(list(submissions))


async def schedule_inline_after_commit(
    db: Session,
    result: TaskSubmissionResult,
    task_callable: Callable[..., Any],
    args: tuple[Any, ...] = (),
) -> bool:
    """Schedule an already-committed task, then acknowledge its outbox row.

    ``TaskSubmissionService`` commits before this helper is called.  If the
    event-loop cannot create the task, the outbox remains pending for the
    worker dispatcher; no previously committed task or usage row is rolled
    back.  The acknowledgement is a separate short transaction on the
    caller's session.
    """

    try:
        asyncio.create_task(task_callable(None, result.task.id, *args))
    except Exception:
        db.rollback()
        return False
    outbox = db.query(TaskOutbox).filter(TaskOutbox.task_id == result.task.id).first()
    if outbox is not None and outbox.status == "pending":
        outbox.status = "delivered"
        outbox.locked_at = None
        outbox.locked_by = None
        outbox.last_error = None
        db.commit()
    return True


__all__ = [
    "TaskSubmission",
    "TaskSubmissionError",
    "TaskSubmissionResult",
    "TaskSubmissionService",
    "sanitize_input_snapshot",
    "schedule_inline_after_commit",
]
