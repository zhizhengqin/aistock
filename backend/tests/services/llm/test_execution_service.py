"""Task-scoped structured LLM execution behavior."""

from __future__ import annotations

import hashlib
import json
import base64
from typing import ClassVar

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.llm_execution import LlmCallAttempt
from app.models.llm_usage import LlmUsage
from app.models.llm_config import LlmModelConfig
from app.core.config import settings
from app.models.task_record import TaskRecord
from app.services.llm.errors import LlmError
from app.services.llm.provider_client import ProviderResult
from app.services.llm.types import LlmRuntimeConfig, Provider
from app.services.llm.crypto import encrypt_api_key
from app.services.llm.config_service import LlmConfigService
from app.services.task_execution import TaskExecutionFenced, TaskExecutionRunner
from app.services.llm.execution_service import LlmExecutionService


class Output(BaseModel):
    answer: str
    score: int

    schema_version: ClassVar[str] = "v1"


def _runtime() -> LlmRuntimeConfig:
    return LlmRuntimeConfig(
        config_id="config-1",
        provider=Provider.DEEPSEEK,
        display_name="test",
        model_name="model-x",
        base_url="https://api.deepseek.com",
        api_key="sk-secret",
        credential_version="credential-v1",
        max_output_tokens=100,
        input_price_micro_yuan_per_million=1,
        output_price_micro_yuan_per_million=2,
        runtime_fingerprint="fingerprint-v1",
    )


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        task = TaskRecord(
            task_type="stock_analysis",
            user_id=None,
            status="running",
            execution_token="execution-token",
            input_snapshot={"_args": {"stock_code": "600519"}},
            prompt_version="task-v1",
        )
        session.add(task)
        session.commit()
        yield session, task.id
    Base.metadata.drop_all(engine)


class StubExecutor:
    """A small executor double that still writes physical attempts."""

    def __init__(
        self,
        session: Session,
        results: list[dict],
        *,
        error: Exception | None = None,
        after_call=None,
    ):
        self.session = session
        self.results = list(results)
        self.error = error
        self.after_call = after_call
        self.calls: list[dict] = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        payload = self.results.pop(0)
        attempt = LlmCallAttempt(
            task_id=kwargs["task_id"],
            operation_type=kwargs["operation_type"],
            step_key=kwargs["step_key"],
            # The service never allocates this ordinal.  A real executor owns
            # the sequence; this deliberately non-sequential value catches a
            # service that tries to assign its own attempt number.
            attempt_no=40 + len(self.calls),
            provider_snapshot="deepseek",
            model_snapshot="model-x",
            runtime_fingerprint="fingerprint-v1",
            status="success",
            result_json=payload,
            result_hash=_canonical_hash(payload),
        )
        self.session.add(attempt)
        self.session.add(
            LlmUsage(
                user_id=None,
                module="task",
                model="model-x",
                prompt_tokens=5,
                completion_tokens=3,
                cost_fen=0,
                task_id=kwargs["task_id"],
                model_config_id=kwargs["runtime_config"].config_id,
                provider_snapshot="deepseek",
                model_snapshot="model-x",
                input_tokens=5,
                output_tokens=3,
                status="success",
            )
        )
        self.session.commit()
        if self.after_call is not None:
            self.after_call()
        return ProviderResult(
            result_json=payload,
            model="model-x",
            prompt_tokens=5,
            completion_tokens=3,
            usage_source="provider",
            response_metadata={},
        )


@pytest.mark.asyncio
async def test_execute_json_persists_validated_step_with_schema_and_hash(db):
    session, task_id = db
    executor = StubExecutor(session, [{"answer": "ok", "score": 7}])
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )

    result = await service.execute_json(
        task_id=task_id,
        step_key="analysis.technical",
        messages=[{"role": "user", "content": "return JSON"}],
        output_type=Output,
        prompt_version="analysis.technical.v1",
    )

    assert result == Output(answer="ok", score=7)
    assert len(executor.calls) == 1
    assert executor.calls[0]["operation_type"] == "task"
    assert executor.calls[0]["task_id"] == task_id
    attempt = session.execute(select(LlmCallAttempt)).scalar_one()
    assert attempt.result_json == {"answer": "ok", "score": 7}
    assert attempt.result_schema_version == "v1"
    assert attempt.result_hash == _canonical_hash({"answer": "ok", "score": 7})


@pytest.mark.asyncio
async def test_execute_json_reuses_successful_step_without_calling_executor_again(db):
    session, task_id = db
    executor = StubExecutor(session, [{"answer": "ok", "score": 7}])
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )
    kwargs = {
        "task_id": task_id,
        "step_key": "analysis.technical",
        "messages": [{"role": "user", "content": "return JSON"}],
        "output_type": Output,
        "prompt_version": "analysis.technical.v1",
    }

    first = await service.execute_json(**kwargs)
    second = await service.execute_json(**kwargs)

    assert first == second == Output(answer="ok", score=7)
    assert len(executor.calls) == 1
    assert session.execute(select(LlmCallAttempt)).scalars().all().__len__() == 1


@pytest.mark.asyncio
async def test_invalid_json_shape_gets_one_independent_correction_call(db):
    session, task_id = db
    executor = StubExecutor(
        session,
        [
            {"answer": "invalid", "score": "not-an-int"},
            {"answer": "corrected", "score": 9},
        ],
    )
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )

    result = await service.execute_json(
        task_id=task_id,
        step_key="analysis.technical",
        messages=[{"role": "user", "content": "return JSON"}],
        output_type=Output,
        prompt_version="analysis.technical.v1",
    )

    assert result == Output(answer="corrected", score=9)
    assert len(executor.calls) == 2
    assert all(call["operation_type"] == "task" for call in executor.calls)
    assert all(call["step_key"] == "analysis.technical" for call in executor.calls)
    assert len(session.execute(select(LlmCallAttempt)).scalars().all()) == 2


@pytest.mark.asyncio
async def test_second_schema_correction_failure_is_propagated_without_third_call(db):
    session, task_id = db
    executor = StubExecutor(
        session,
        [
            {"answer": "invalid", "score": "bad"},
            {"answer": "still-invalid", "score": "bad"},
        ],
    )
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )

    with pytest.raises(LlmError) as exc_info:
        await service.execute_json(
            task_id=task_id,
            step_key="analysis.technical",
            messages=[{"role": "user", "content": "return JSON"}],
            output_type=Output,
            prompt_version="analysis.technical.v1",
        )

    assert exc_info.value.code == "llm_schema_invalid"
    assert len(executor.calls) == 2
    assert len(session.execute(select(LlmCallAttempt)).scalars().all()) == 2


@pytest.mark.asyncio
async def test_provider_result_is_fenced_before_step_persistence(db):
    session, task_id = db

    def reclaim_task():
        session.get(TaskRecord, task_id).execution_token = "new-owner"
        session.commit()

    executor = StubExecutor(
        session,
        [{"answer": "late", "score": 8}],
        after_call=reclaim_task,
    )
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )

    with pytest.raises(TaskExecutionFenced):
        await service.execute_json(
            task_id=task_id,
            step_key="analysis.technical",
            messages=[{"role": "user", "content": "return JSON"}],
            output_type=Output,
            prompt_version="analysis.technical.v1",
        )

    attempt = session.execute(select(LlmCallAttempt)).scalar_one()
    assert attempt.result_schema_version is None
    assert attempt.result_hash == _canonical_hash({"answer": "late", "score": 8})


@pytest.mark.asyncio
async def test_executor_owns_attempt_ordinals_and_usage_task_linkage(db):
    session, task_id = db
    executor = StubExecutor(session, [{"answer": "ok", "score": 7}])
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )

    await service.execute_json(
        task_id=task_id,
        step_key="analysis.technical",
        messages=[{"role": "user", "content": "return JSON"}],
        output_type=Output,
        prompt_version="analysis.technical.v1",
    )

    attempt = session.execute(select(LlmCallAttempt)).scalar_one()
    usage = session.execute(select(LlmUsage)).scalar_one()
    assert attempt.attempt_no == 41
    assert usage.task_id == task_id
    assert usage.model_config_id == "config-1"


@pytest.mark.asyncio
async def test_failed_unknown_attempt_is_terminal_and_not_reused_or_recalled(db):
    session, task_id = db
    session.add(
        LlmCallAttempt(
            task_id=task_id,
            operation_type="task",
            step_key="analysis.technical",
            attempt_no=41,
            provider_snapshot="deepseek",
            model_snapshot="model-x",
            runtime_fingerprint="fingerprint-v1",
            status="failed_unknown",
            error_code="llm_failed_unknown",
        )
    )
    session.commit()
    executor = StubExecutor(session, [{"answer": "must-not-run", "score": 1}])
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )

    with pytest.raises(LlmError) as exc_info:
        await service.execute_json(
            task_id=task_id,
            step_key="analysis.technical",
            messages=[{"role": "user", "content": "return JSON"}],
            output_type=Output,
            prompt_version="analysis.technical.v1",
        )
    assert exc_info.value.code == "llm_failed_unknown"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_runner_decrypts_task_runtime_once_and_injects_one_service(db, monkeypatch):
    session, task_id = db
    keyring = {"current": base64.b64encode(b"k" * 32).decode("ascii")}
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "current")
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", keyring)
    config_id = "config-1"
    envelope = encrypt_api_key(
        "sk-task-secret",
        config_id=config_id,
        provider=Provider.DEEPSEEK,
        keyring=keyring,
    )
    session.add(
        LlmModelConfig(
            id=config_id,
            provider=Provider.DEEPSEEK.value,
            display_name="Task model",
            model_name="model-x",
            base_url="https://api.deepseek.com",
            encrypted_api_key=envelope.encrypted_api_key,
            encryption_key_id=envelope.encryption_key_id,
            envelope_version=envelope.envelope_version,
            nonce=envelope.nonce,
            runtime_fingerprint="fingerprint-v1",
            max_output_tokens=100,
        )
    )
    session.get(TaskRecord, task_id).model_config_id = config_id
    session.commit()

    decryptions = 0
    original_runtime = LlmConfigService._runtime

    def counted_runtime(self, config):
        nonlocal decryptions
        decryptions += 1
        return original_runtime(self, config)

    monkeypatch.setattr(LlmConfigService, "_runtime", counted_runtime)
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)
    runner = TaskExecutionRunner(factory, heartbeat_interval_seconds=60)
    seen = {}

    async def execute(context):
        seen["service"] = context.llm
        seen["runtime"] = context.runtime_config
        # Reading the runtime through multiple business steps must not invoke
        # decryption again; the service retains the immutable snapshot.
        assert context.llm.runtime_config is context.runtime_config
        assert context.llm.runtime_config.api_key == "sk-task-secret"
        return {"ok": True}

    result = await runner.run(task_id, execute, lambda _db, _task, _result: None)
    assert result == {"ok": True}
    assert decryptions == 1
    assert seen["service"] is not None
    assert seen["runtime"] is seen["service"].runtime_config


@pytest.mark.asyncio
async def test_execution_token_is_checked_before_call(db):
    session, task_id = db
    session.get(TaskRecord, task_id).execution_token = "new-owner"
    session.commit()
    executor = StubExecutor(session, [{"answer": "must-not-run", "score": 1}])
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="old-owner",
    )

    with pytest.raises(TaskExecutionFenced):
        await service.execute_json(
            task_id=task_id,
            step_key="analysis.technical",
            messages=[{"role": "user", "content": "return JSON"}],
            output_type=Output,
            prompt_version="analysis.technical.v1",
        )
    assert executor.calls == []


@pytest.mark.asyncio
async def test_failed_unknown_is_propagated_without_correction_or_retry(db):
    session, task_id = db
    error = LlmError("大模型响应状态未知，请勿自动重试", code="llm_failed_unknown")
    executor = StubExecutor(session, [], error=error)
    service = LlmExecutionService(
        session,
        executor=executor,
        runtime_config=_runtime(),
        execution_token="execution-token",
    )

    with pytest.raises(LlmError) as exc_info:
        await service.execute_json(
            task_id=task_id,
            step_key="analysis.technical",
            messages=[{"role": "user", "content": "return JSON"}],
            output_type=Output,
            prompt_version="analysis.technical.v1",
        )
    assert exc_info.value.code == "llm_failed_unknown"
    assert len(executor.calls) == 1
