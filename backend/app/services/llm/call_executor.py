"""Universal budgeted and audited LLM call executor."""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Literal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.llm_execution import LlmCallAttempt
from app.models.llm_usage import LlmUsage
from app.services.llm.budget import TokenBudgetService
from app.services.llm.errors import LlmError
from app.services.llm.provider_client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    ProviderClient,
    ProviderResult,
)
from app.services.llm.types import LlmRuntimeConfig


OperationType = Literal["task", "admin_probe", "bootstrap", "live_smoke"]
OPERATION_TYPES = frozenset({"task", "admin_probe", "bootstrap", "live_smoke"})


def _error(code: str, message: str, *, retryable: bool = False) -> LlmError:
    error = LlmError(message, code=code)
    error.retryable = retryable
    error.user_message = message
    error.confirmed_unsent = True
    error.may_have_sent = False
    return error


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _ceil_cost(tokens: int | None, price: int | None) -> int | None:
    if tokens is None or price is None:
        return None
    return math.ceil(tokens * price / 1_000_000)


class LlmCallExecutor:
    """One place through which every real provider request must pass."""

    def __init__(
        self,
        db: Session | Callable[[], Session] | None = None,
        *,
        session: Session | Callable[[], Session] | None = None,
        provider_client: ProviderClient | None = None,
        budget: TokenBudgetService | None = None,
        budget_service: TokenBudgetService | None = None,
        max_retries: int = 2,
    ) -> None:
        if db is not None and session is not None and db is not session:
            raise ValueError("db 与 session 不能同时指定")
        self.db = db or session
        if self.db is None:
            raise ValueError("必须提供数据库 Session")
        if budget is not None and budget_service is not None and budget is not budget_service:
            raise ValueError("budget 与 budget_service 不能同时指定")
        self.budget = budget or budget_service or TokenBudgetService(self.db)
        self.provider_client = provider_client or ProviderClient()
        if int(max_retries) < 0:
            raise ValueError("重试次数不能为负数")
        self.max_retries = min(2, int(max_retries))

    @contextmanager
    def _session(self):
        own = not isinstance(self.db, Session)
        session = self.db() if own else self.db
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def _input_upper_bound(runtime_config: LlmRuntimeConfig, messages: list[dict[str, str]]) -> int:
        estimator = getattr(ProviderClient, "estimate_input_upper_bound")
        output_bound = runtime_config.max_output_tokens
        if output_bound is None:
            output_bound = DEFAULT_MAX_OUTPUT_TOKENS
        return estimator(messages, runtime_config.provider) + int(output_bound)

    def _write_attempt(
        self,
        *,
        runtime_config: LlmRuntimeConfig,
        operation_type: str,
        step_key: str,
        task_id: int | None,
        attempt_no: int,
        reservation_id: str,
        input_snapshot_hash: str,
        prompt_version: str | None,
    ) -> str:
        operation_id = str(uuid4())
        with self._session() as session:
            attempt = LlmCallAttempt(
                task_id=task_id,
                model_config_id=runtime_config.config_id,
                operation_id=operation_id,
                operation_type=operation_type,
                step_key=step_key,
                attempt_no=attempt_no,
                provider_snapshot=str(runtime_config.provider),
                model_snapshot=runtime_config.model_name,
                runtime_fingerprint=runtime_config.runtime_fingerprint,
                input_snapshot_hash=input_snapshot_hash,
                prompt_version=prompt_version,
                reservation_id=reservation_id,
                status="started",
                input_price_snapshot=runtime_config.input_price_micro_yuan_per_million,
                output_price_snapshot=runtime_config.output_price_micro_yuan_per_million,
            )
            session.add(attempt)
            session.flush()
        return operation_id

    def _next_attempt_no(self, task_id: int | None, step_key: str) -> int:
        """Continue the durable attempt sequence for task-step corrections."""

        if task_id is None:
            # Unsaved probes have nullable task identity and intentionally use
            # independent operation UUIDs; their attempt number starts at one.
            return 1
        with self._session() as session:
            current = session.execute(
                select(func.max(LlmCallAttempt.attempt_no)).where(
                    LlmCallAttempt.task_id == task_id,
                    LlmCallAttempt.step_key == step_key,
                )
            ).scalar_one()
            return int(current or 0) + 1

    def _update_attempt(
        self,
        operation_id: str,
        *,
        status: str,
        result: ProviderResult | None = None,
        error: LlmError | None = None,
    ) -> LlmCallAttempt | None:
        with self._session() as session:
            attempt = session.query(LlmCallAttempt).filter_by(operation_id=operation_id).one_or_none()
            if attempt is None:
                return None
            if result is not None:
                attempt.status = status
                attempt.response_model_snapshot = result.model
                attempt.input_tokens = result.prompt_tokens
                attempt.output_tokens = result.completion_tokens
                attempt.usage_source = result.usage_source
                attempt.result_json = result.result_json
                attempt.result_hash = _sha256_json(result.result_json)
                attempt.response_metadata_json = result.response_metadata
                attempt.cost_micro_yuan = self._cost(result, attempt)
            else:
                attempt.status = status
            if error is not None:
                attempt.error_code = getattr(error, "code", "llm_error")
                attempt.error_message = str(error)
                attempt.usage_source = "unknown" if status == "failed_unknown" else "none"
            session.flush()
            session.refresh(attempt)
            session.expunge(attempt)
            return attempt

    @staticmethod
    def _cost(result: ProviderResult, attempt: LlmCallAttempt) -> int | None:
        input_cost = _ceil_cost(result.prompt_tokens, attempt.input_price_snapshot)
        output_cost = _ceil_cost(result.completion_tokens, attempt.output_price_snapshot)
        if input_cost is None or output_cost is None:
            return None
        return input_cost + output_cost

    def _persist_usage(
        self,
        runtime_config: LlmRuntimeConfig,
        *,
        operation_type: str,
        task_id: int | None,
        status: str,
        result: ProviderResult | None = None,
        error: LlmError | None = None,
    ) -> None:
        prompt_tokens = result.prompt_tokens if result is not None else None
        completion_tokens = result.completion_tokens if result is not None else None
        cost = None
        if result is not None:
            cost = _ceil_cost(prompt_tokens, runtime_config.input_price_micro_yuan_per_million)
            output_cost = _ceil_cost(completion_tokens, runtime_config.output_price_micro_yuan_per_million)
            if cost is not None and output_cost is not None:
                cost += output_cost
            else:
                cost = None
        with self._session() as session:
            session.add(
                LlmUsage(
                    user_id=None,
                    module=operation_type,
                    model=runtime_config.model_name,
                    prompt_tokens=prompt_tokens or 0,
                    completion_tokens=completion_tokens or 0,
                    cost_fen=0,
                    task_id=task_id,
                    model_config_id=runtime_config.config_id,
                    provider_snapshot=str(runtime_config.provider),
                    model_snapshot=runtime_config.model_name,
                    input_price_snapshot=runtime_config.input_price_micro_yuan_per_million,
                    output_price_snapshot=runtime_config.output_price_micro_yuan_per_million,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    cost_micro_yuan=cost,
                    status=status,
                    error_code=getattr(error, "code", None) if error else None,
                )
            )

    async def call(
        self,
        *,
        runtime_config: LlmRuntimeConfig,
        operation_type: OperationType | str,
        step_key: str,
        messages: list[dict[str, str]],
        task_id: int | None = None,
        prompt_version: str | None = None,
        temperature: float = 0.3,
    ) -> ProviderResult:
        """Reserve, audit, call, settle and persist one logical operation."""

        if not isinstance(operation_type, str) or operation_type not in OPERATION_TYPES:
            raise _error("llm_operation_invalid", "大模型调用用途无效")
        if not isinstance(step_key, str) or not step_key.strip():
            raise _error("llm_step_invalid", "大模型调用步骤不能为空")
        if not isinstance(runtime_config, LlmRuntimeConfig):
            raise _error("llm_runtime_invalid", "大模型运行配置无效")
        try:
            reserve_upper = self._input_upper_bound(runtime_config, messages)
        except LlmError:
            raise
        input_hash = _sha256_json(messages)
        last_error: LlmError | None = None
        first_attempt_no = self._next_attempt_no(task_id, step_key)
        for attempt_offset in range(self.max_retries + 1):
            attempt_no = first_attempt_no + attempt_offset
            reservation = self.budget.reserve(
                reserve_upper,
                task_id=task_id,
                step_key=step_key,
            )
            operation_id = self._write_attempt(
                runtime_config=runtime_config,
                operation_type=operation_type,
                step_key=step_key,
                task_id=task_id,
                attempt_no=attempt_no,
                reservation_id=reservation.id,
                input_snapshot_hash=input_hash,
                prompt_version=prompt_version,
            )
            try:
                result = await self.provider_client.complete_json(
                    runtime_config,
                    messages,
                    temperature=temperature,
                )
                if not isinstance(result, ProviderResult):
                    # Keep the boundary friendly to a small test/integration
                    # adapter while retaining the production result contract.
                    result = ProviderResult(
                        result_json=getattr(result, "result_json", getattr(result, "response_json", {})),
                        model=getattr(result, "model", runtime_config.model_name),
                        prompt_tokens=getattr(result, "prompt_tokens", getattr(result, "input_tokens", None)),
                        completion_tokens=getattr(result, "completion_tokens", getattr(result, "output_tokens", None)),
                        usage_source=getattr(result, "usage_source", "missing"),
                        response_metadata=getattr(result, "response_metadata", {}),
                    )
                actual = result.total_tokens
                if actual is None:
                    self.budget.settle(reservation.id, None)
                else:
                    self.budget.settle(reservation.id, actual)
                self._update_attempt(operation_id, status="success", result=result)
                self._persist_usage(
                    runtime_config,
                    operation_type=operation_type,
                    task_id=task_id,
                    status="success",
                    result=result,
                )
                return result
            except LlmError as exc:
                last_error = exc
            except Exception:
                last_error = _error(
                    "llm_failed_unknown",
                    "大模型响应状态未知，请勿自动重试",
                )
                last_error.may_have_sent = True
                last_error.confirmed_unsent = False
            confirmed_unsent = bool(getattr(last_error, "confirmed_unsent", False))
            may_have_sent = bool(getattr(last_error, "may_have_sent", not confirmed_unsent))
            retryable = bool(getattr(last_error, "retryable", False))
            if confirmed_unsent:
                self.budget.release(reservation.id)
            else:
                # A response or a possibly-sent network failure is charged at
                # the conservative reservation bound and never replayed when
                # the outcome is unknown.
                self.budget.settle(reservation.id, None, unknown=True)
            unknown_outcome = getattr(last_error, "code", "") == "llm_failed_unknown" or bool(
                getattr(last_error, "unknown_outcome", False)
            )
            final_status = "failed_unknown" if unknown_outcome else "failed"
            self._update_attempt(operation_id, status=final_status, error=last_error)
            self._persist_usage(
                runtime_config,
                operation_type=operation_type,
                task_id=task_id,
                status=final_status,
                error=last_error,
            )
            if retryable and confirmed_unsent and attempt_no <= self.max_retries:
                continue
            if retryable and not confirmed_unsent and may_have_sent and attempt_no <= self.max_retries:
                # 429/5xx have a definite upstream response and are safe to
                # retry; unknown timeout/disconnect errors are marked above
                # with retryable=False and stop here.
                continue
            raise last_error
        raise last_error or _error("llm_error", "大模型服务暂时不可用")


__all__ = ["LlmCallExecutor", "OPERATION_TYPES", "OperationType"]
