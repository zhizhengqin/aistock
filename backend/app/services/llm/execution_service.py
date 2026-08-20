"""Task-scoped, fenced and schema-aware model execution.

The provider executor owns physical HTTP attempts, retries, reservations and
usage audit rows.  This service owns the business-step boundary: it validates
the provider JSON, reuses a complete successful step after worker recovery,
and records the validated payload only while the task execution token is
still current.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from contextlib import contextmanager
from typing import Any, Callable, Generic, Mapping, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.llm_execution import LlmCallAttempt
from app.models.task_record import TaskRecord
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.errors import LlmError
from app.services.llm.provider_client import ProviderResult
from app.services.llm.types import LlmRuntimeConfig


T = TypeVar("T", bound=BaseModel)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _schema_version(output_type: type[BaseModel]) -> str:
    """Read an output schema's explicit version without requiring one yet."""

    value = getattr(output_type, "schema_version", None)
    if value is None:
        value = getattr(output_type, "__schema_version__", None)
    if value is None:
        config = getattr(output_type, "model_config", {}) or {}
        if isinstance(config, Mapping):
            extra = config.get("json_schema_extra") or {}
            if isinstance(extra, Mapping):
                value = extra.get("version") or extra.get("schema_version")
    return str(value or "v1")


def _schema_error(message: str = "大模型返回内容不符合任务结构") -> LlmError:
    error = LlmError(message, code="llm_schema_invalid")
    error.user_message = message
    error.retryable = False
    error.confirmed_unsent = False
    error.may_have_sent = True
    return error


def _raise_fenced() -> None:
    # Keep this import lazy: TaskExecutionRunner injects this service while
    # constructing its context, and importing the exception at module import
    # time would create a task-runner/service cycle.
    from app.services.task_execution import TaskExecutionFenced

    raise TaskExecutionFenced("任务执行权已变更")


class LlmExecutionService(Generic[T]):
    """Execute one task's structured model steps under its fencing token.

    ``runtime_config`` is supplied by the task runner after one decryption.
    The object is immutable and retained for the whole task, so switching the
    global default while a task is running cannot change its provider/model.
    ``db`` may be a caller-owned Session or a Session factory.  Sessions used
    for checks and persistence are always short-lived and never span awaits.
    """

    def __init__(
        self,
        db: Session | Callable[[], Session] | None = None,
        *,
        executor: LlmCallExecutor,
        runtime_config: LlmRuntimeConfig,
        execution_token: str,
        session_factory: Session | Callable[[], Session] | None = None,
    ) -> None:
        if db is not None and session_factory is not None and db is not session_factory:
            raise ValueError("db 与 session_factory 不能同时指定")
        self.db = db or session_factory
        if self.db is None:
            # The executor normally owns the same Session factory.  Keeping
            # this fallback makes direct construction concise in unit tests
            # without creating a second persistence abstraction.
            self.db = getattr(executor, "db", None)
        if self.db is None:
            raise ValueError("必须提供数据库 Session")
        if not isinstance(executor, LlmCallExecutor) and not hasattr(executor, "call"):
            raise TypeError("executor 必须提供异步 call 方法")
        if not isinstance(runtime_config, LlmRuntimeConfig):
            raise ValueError("runtime_config 必须是 LlmRuntimeConfig")
        if not isinstance(execution_token, str) or not execution_token:
            raise ValueError("execution_token 不能为空")
        self.executor = executor
        self.runtime_config = runtime_config
        self.execution_token = execution_token

    @contextmanager
    def _session(self):
        own = not isinstance(self.db, Session)
        session = self.db() if own else self.db
        try:
            yield session
        except BaseException:
            session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def _task_is_current(session: Session, task_id: int, execution_token: str) -> bool:
        task = session.execute(
            select(TaskRecord.id)
            .where(
                TaskRecord.id == task_id,
                TaskRecord.execution_token == execution_token,
                TaskRecord.status == "running",
            )
        ).scalar_one_or_none()
        return task is not None

    def _verify_execution_token(self, task_id: int) -> None:
        with self._session() as session:
            current = self._task_is_current(session, task_id, self.execution_token)
            # A caller-owned Session would otherwise leave a read transaction
            # open over the provider await.  Roll it back before returning.
            if not current:
                session.rollback()
                _raise_fenced()
            session.rollback()

    @staticmethod
    def _provider_payload(result: Any) -> Any:
        if isinstance(result, ProviderResult):
            return result.result_json
        if isinstance(result, Mapping):
            return dict(result)
        payload = getattr(result, "result_json", None)
        if payload is None:
            payload = getattr(result, "response_json", None)
        if payload is None:
            payload = getattr(result, "content", None)
        return payload

    @staticmethod
    def _validate_payload(payload: Any, output_type: type[T]) -> T:
        if not isinstance(output_type, type) or not issubclass(output_type, BaseModel):
            raise _schema_error("任务输出类型无效")
        try:
            return output_type.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            # Pydantic's error tree can include provider-controlled values;
            # expose only the stable Chinese business error.
            raise _schema_error() from None

    def _latest_attempt(self, session: Session, task_id: int, step_key: str) -> LlmCallAttempt | None:
        return session.execute(
            select(LlmCallAttempt)
            .where(
                LlmCallAttempt.task_id == task_id,
                LlmCallAttempt.step_key == step_key,
            )
            .order_by(LlmCallAttempt.attempt_no.desc(), LlmCallAttempt.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _reuse_success(
        self,
        *,
        task_id: int,
        step_key: str,
        output_type: type[T],
        schema_version: str,
    ) -> T | None:
        with self._session() as session:
            unknown = session.execute(
                select(LlmCallAttempt.id)
                .where(
                    LlmCallAttempt.task_id == task_id,
                    LlmCallAttempt.step_key == step_key,
                    LlmCallAttempt.status == "failed_unknown",
                )
                .limit(1)
            ).scalar_one_or_none()
            if unknown is not None:
                session.rollback()
                raise LlmError("大模型响应状态未知，请勿自动重试", code="llm_failed_unknown")
            latest = self._latest_attempt(session, task_id, step_key)
            if latest is None:
                session.rollback()
                return None
            if latest.status != "success" or latest.result_json is None:
                session.rollback()
                return None
            payload = latest.result_json
            if not latest.result_hash or latest.result_hash != _sha256_json(payload):
                session.rollback()
                raise LlmError("任务步骤结果校验失败", code="llm_step_corrupt")
            if latest.result_schema_version != schema_version:
                session.rollback()
                return None
            result = self._validate_payload(payload, output_type)
            session.rollback()
            return result

    def _persist_validated(
        self,
        *,
        task_id: int,
        step_key: str,
        output_type: type[T],
        result: T,
        schema_version: str,
    ) -> T:
        payload = result.model_dump(mode="json")
        result_hash = _sha256_json(payload)
        with self._session() as session:
            task = session.execute(
                select(TaskRecord)
                .where(
                    TaskRecord.id == task_id,
                    TaskRecord.execution_token == self.execution_token,
                    TaskRecord.status == "running",
                )
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                _raise_fenced()
            attempt = self._latest_attempt(session, task_id, step_key)
            if attempt is None or attempt.status != "success":
                raise LlmError("大模型调用审计记录缺失", code="llm_attempt_missing")
            attempt.result_json = payload
            attempt.result_schema_version = schema_version
            attempt.result_hash = result_hash
            session.commit()
        return result

    async def _call_executor(
        self,
        *,
        task_id: int,
        step_key: str,
        messages: list[dict[str, str]],
        prompt_version: str,
        temperature: float,
    ) -> Any:
        call_kwargs: dict[str, Any] = {
            "runtime_config": self.runtime_config,
            "operation_type": "task",
            "step_key": step_key,
            "messages": messages,
            "task_id": task_id,
        }
        # Task 3's stable executor contract keeps prompt metadata optional;
        # pass it when the concrete executor accepts it, while remaining
        # compatible with small adapters that implement only the frozen
        # required parameters.
        try:
            parameters = inspect.signature(self.executor.call).parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            accepts_kwargs = True
            parameters = {}
        if accepts_kwargs or "prompt_version" in parameters:
            call_kwargs["prompt_version"] = prompt_version
        if accepts_kwargs or "temperature" in parameters:
            call_kwargs["temperature"] = temperature
        return await self.executor.call(**call_kwargs)

    async def execute_json(
        self,
        *,
        task_id: int,
        step_key: str,
        messages: list[dict[str, str]],
        output_type: type[T],
        prompt_version: str,
        temperature: float = 0.3,
    ) -> T:
        """Run or reuse one validated task step.

        At most one correction request is made after a schema failure.  Both
        requests use ``operation_type='task'`` and the same stable step key;
        ``LlmCallExecutor`` allocates independent attempt numbers and budget
        reservations for each physical request.
        """

        if not isinstance(task_id, int) or isinstance(task_id, bool):
            raise LlmError("任务标识无效", code="llm_task_invalid")
        if not isinstance(step_key, str) or not step_key.strip():
            raise LlmError("大模型调用步骤不能为空", code="llm_step_invalid")
        if not isinstance(prompt_version, str) or not prompt_version.strip():
            raise LlmError("提示词版本不能为空", code="llm_prompt_version_invalid")
        schema_version = _schema_version(output_type)

        self._verify_execution_token(task_id)
        reused = self._reuse_success(
            task_id=task_id,
            step_key=step_key,
            output_type=output_type,
            schema_version=schema_version,
        )
        if reused is not None:
            return reused

        await self._verify_before_call(task_id)
        provider_result = await self._call_executor(
            task_id=task_id,
            step_key=step_key,
            messages=messages,
            prompt_version=prompt_version,
            temperature=temperature,
        )
        await self._verify_before_call(task_id)
        try:
            typed = self._validate_payload(self._provider_payload(provider_result), output_type)
        except LlmError as first_error:
            if first_error.code != "llm_schema_invalid":
                raise
            correction_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "上一次返回内容未通过结构校验。请只输出符合目标字段和类型的 JSON，"
                        "不要附加解释。"
                    ),
                },
            ]
            await self._verify_before_call(task_id)
            correction_result = await self._call_executor(
                task_id=task_id,
                step_key=step_key,
                messages=correction_messages,
                prompt_version=prompt_version,
                temperature=temperature,
            )
            await self._verify_before_call(task_id)
            typed = self._validate_payload(self._provider_payload(correction_result), output_type)

        return self._persist_validated(
            task_id=task_id,
            step_key=step_key,
            output_type=output_type,
            result=typed,
            schema_version=schema_version,
        )

    async def _verify_before_call(self, task_id: int) -> None:
        self._verify_execution_token(task_id)


__all__ = ["LlmExecutionService"]
