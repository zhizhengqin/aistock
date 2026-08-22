"""One-shot model bootstrap, read-only readiness and explicit live smoke.

The commands in this module deliberately keep network I/O outside short
database transactions.  PostgreSQL advisory locking only protects creation
of the singleton settings row and the first candidate; the candidate is
durable before a provider probe starts, so a second process cannot duplicate
the migration while the first process is waiting on the network.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
# Keep foreign-key mapper targets registered when this module is invoked as a
# standalone ``python -m`` command (without importing the FastAPI app first).
from app.models import task_record as _task_record_models  # noqa: F401
from app.models import user as _user_models  # noqa: F401
from app.models.llm_config import LlmModelConfig, LlmModelTestRun, LlmRuntimeSetting
from app.models.llm_execution import LlmCallAttempt, LlmDailyBudget, LlmTokenReservation
from app.models.llm_usage import LlmUsage
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.crypto import CredentialEnvelope, decrypt_api_key, encrypt_api_key
from app.services.llm.errors import LlmError
from app.services.llm.provider_client import ProviderClient, ProviderResult
from app.services.llm.types import LlmRuntimeConfig, ModelLifecycle, Provider
from app.services.llm.url_security import validate_base_url
from app.services.llm.config_service import runtime_fingerprint


PROBE_PROMPT_VERSION = "bootstrap-v1"
SMOKE_PROMPT_VERSION = "live-smoke-v1"
BOOTSTRAP_LOCK_KEY = "aistock_llm_bootstrap_v1"
DEFAULT_MAX_OUTPUT_TOKENS = 4096
BOOTSTRAP_PROBE_DEADLINE_SECONDS = 195
BOOTSTRAP_PROBE_GRACE_SECONDS = 15
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")

# Only errors which are unambiguously produced by the upstream provider are a
# non-fatal bootstrap outcome.  Local budget/configuration/audit/programming
# failures must stop startup so a broken installation cannot be reported as a
# usable model.  ``llm_probe_invalid`` is the local name for a provider JSON
# contract violation and is retained as an auditable failed candidate.
_UPSTREAM_PROBE_ERROR_CODES = frozenset(
    {
        "llm_auth_failed",
        "llm_model_not_found",
        "llm_quota_exceeded",
        "llm_rate_limited",
        "llm_timeout",
        "llm_probe_timeout",
        "llm_failed_unknown",
        "llm_daily_limit_reached",
        "llm_invalid_json",
        "llm_unavailable",
        "llm_dns_unavailable",
        "llm_redirect_blocked",
        "llm_response_too_large",
        "llm_probe_invalid",
    }
)

# These codes describe a local refusal or persistence/configuration defect.
# They remain fatal even if a malformed adapter happens to persist an attempt
# row before raising the same error.
_LOCAL_PROBE_ERROR_CODES = frozenset(
    {
        "llm_transport_config",
        "llm_budget_locked",
        "llm_audit_database",
        "llm_database",
        "llm_config",
        "llm_configuration",
        "llm_credential_error",
        "llm_encryption_error",
        "llm_provider_invalid",
        "llm_runtime_invalid",
        "llm_input_invalid",
        "llm_input_too_large",
        "llm_max_tokens_invalid",
        "llm_operation_invalid",
        "llm_reservation_invalid",
        "llm_usage_invalid",
        "llm_attempt_conflict",
    }
)


@dataclass(frozen=True, slots=True)
class CliResult:
    exit_code: int
    status: str
    message: str = ""
    model_name: str | None = None
    evidence: Mapping[str, object] | None = None
    rendered: str = ""

    def with_rendered(self, *, secrets: Iterable[str] = ()) -> "CliResult":
        payload: dict[str, object] = {"status": self.status}
        if self.model_name:
            payload["model_name"] = _redact_text(self.model_name, secrets)
        if self.message:
            payload["message"] = _redact_text(self.message, secrets)
        if self.evidence:
            payload["evidence"] = dict(self.evidence)
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return CliResult(
            exit_code=self.exit_code,
            status=self.status,
            message=self.message,
            model_name=self.model_name,
            evidence=self.evidence,
            rendered=rendered,
        )


@dataclass(frozen=True, slots=True)
class _BootstrapCandidate:
    config_id: str
    runtime: LlmRuntimeConfig
    config_version: int
    settings_version: int


@dataclass(frozen=True, slots=True)
class _ProbeAttemptEvidence:
    """Durable evidence for the one physical bootstrap call."""

    status: str | None = None
    error_code: str | None = None


def _redact_text(value: str, secrets: Iterable[str] = ()) -> str:
    output = str(value)
    for secret in secrets:
        if secret:
            output = output.replace(secret, "***")
    return _SECRET_PATTERN.sub("***", output)


def _safe_key_hint(api_key: str) -> str:
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite-naive and PostgreSQL-aware timestamps to UTC."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bootstrap_run_expired(run: LlmModelTestRun, *, now: datetime | None = None) -> bool:
    created_at = _as_utc(run.created_at)
    if created_at is None:
        return False
    deadline = BOOTSTRAP_PROBE_DEADLINE_SECONDS + BOOTSTRAP_PROBE_GRACE_SECONDS
    return (_as_utc(now) or _now()) >= created_at + timedelta(seconds=deadline)


def _as_provider(value: Provider | str) -> Provider:
    try:
        return value if isinstance(value, Provider) else Provider(value)
    except (TypeError, ValueError):
        raise ValueError("供应商必须是 deepseek、kimi 或 qwen") from None


def _probe_messages() -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "你是 A 股投研结构化输出探测器，只能返回 JSON 对象，不得添加 Markdown。",
        },
        {
            "role": "user",
            "content": (
                "请对示例行情做最小投资判断，并严格返回："
                '{"decision":"hold","confidence":0.5,"rationale":"一句话理由"}。'
                "decision 只能是 buy/hold/sell，confidence 必须是 0 到 1 的数字，"
                "rationale 必须是非空字符串。"
            ),
        },
    ]


def _validate_structured_result(
    result: ProviderResult,
    runtime: LlmRuntimeConfig,
    *,
    purpose: str,
) -> dict[str, bool]:
    payload = result.result_json
    if not isinstance(payload, dict):
        raise LlmError("模型未返回预期的业务 JSON", code="llm_probe_invalid")
    confidence = payload.get("confidence")
    if (
        payload.get("decision") not in {"buy", "hold", "sell"}
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
        or not isinstance(payload.get("rationale"), str)
        or not payload["rationale"].strip()
    ):
        raise LlmError("模型未返回预期的业务 JSON", code="llm_probe_invalid")
    if not isinstance(result.model, str) or not result.model.strip():
        raise LlmError("模型未提供响应模型信息", code="llm_probe_invalid")
    if purpose in {"bootstrap", "live_smoke"}:
        if result.usage_source != "provider":
            raise LlmError("模型未提供真实 Token 用量", code="llm_probe_invalid")
        if not isinstance(result.prompt_tokens, int) or result.prompt_tokens < 0:
            raise LlmError("模型输入 Token 证据无效", code="llm_probe_invalid")
        if not isinstance(result.completion_tokens, int) or result.completion_tokens < 0:
            raise LlmError("模型输出 Token 证据无效", code="llm_probe_invalid")
        if not bool(result.response_metadata.get("provider_model_present")):
            raise LlmError("模型未提供身份校验信息", code="llm_probe_invalid")
        if result.completion_tokens > runtime.max_output_tokens:
            raise LlmError("模型输出超过配置上限", code="llm_probe_invalid")
        input_upper_bound = ProviderClient.estimate_input_upper_bound(
            _probe_messages(), runtime.provider
        )
        if result.total_tokens is not None and result.total_tokens > (
            input_upper_bound + runtime.max_output_tokens
        ):
            raise LlmError("模型实际用量超过预留上界", code="llm_probe_invalid")
    return {
        "json_mode": True,
        "usage": result.usage_source == "provider",
        "max_output_tokens": True,
        "model_identity": bool(result.response_metadata.get("provider_model_present")),
    }


def _session_scope(session_factory: Callable[[], Session] | Session):
    @contextmanager
    def scope():
        own = not isinstance(session_factory, Session)
        db = session_factory() if own else session_factory
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if own:
                db.close()

    return scope()


def _readonly_session_scope(session_factory: Callable[[], Session] | Session):
    """Open a no-autoflush, rollback-only scope for readiness checks.

    Readiness is often called by code which has a pending ORM object in its
    caller session.  It must not flush or commit that object as a side effect
    of a health check, so the scope always rolls back on exit.
    """

    @contextmanager
    def scope():
        own = not isinstance(session_factory, Session)
        db = session_factory() if own else session_factory
        try:
            with db.no_autoflush:
                yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.rollback()
            if own:
                db.close()

    return scope()


def _ensure_runtime_settings(session: Session) -> LlmRuntimeSetting:
    values = {
        "id": 1,
        "daily_token_limit": int(getattr(settings, "DAILY_TOKEN_LIMIT", 2_000_000)),
        "budget_locked": False,
        "version": 1,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        # The advisory lock must cover both singleton creation and the second
        # empty-model check.  This is a transaction-scoped lock: it is held
        # until this short bootstrap transaction commits.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('aistock_llm_bootstrap_v1'))"))
    if dialect == "postgresql":
        session.execute(pg_insert(LlmRuntimeSetting).values(**values).on_conflict_do_nothing(index_elements=["id"]))
    elif dialect == "sqlite":
        session.execute(sqlite_insert(LlmRuntimeSetting).values(**values).on_conflict_do_nothing(index_elements=["id"]))
    else:
        if session.get(LlmRuntimeSetting, 1) is None:
            session.add(LlmRuntimeSetting(**values))
    session.flush()
    row = session.execute(
        select(LlmRuntimeSetting)
        .where(LlmRuntimeSetting.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return row


def _build_candidate(session: Session) -> _BootstrapCandidate | None:
    """Create the first candidate under the PostgreSQL bootstrap lock."""

    setting = _ensure_runtime_settings(session)
    # This is deliberately a second query after acquiring the advisory lock.
    # It is the race guard that makes a pair of bootstrap processes idempotent.
    all_configs = session.execute(select(LlmModelConfig)).scalars().all()
    active_configs = [config for config in all_configs if config.deleted_at is None]
    owned_candidates = [
        config
        for config in active_configs
        if config.display_name == "DeepSeek 环境变量迁移"
        and config.created_by is None
        and config.lifecycle_status == ModelLifecycle.DRAFT.value
        and setting.default_model_config_id != config.id
    ]
    if len(active_configs) > 0:
        if len(active_configs) == 1 and len(owned_candidates) == 1:
            # A prior local fatal may have lost its cleanup transaction.  The
            # candidate is still exclusively bootstrap-owned, so recover the
            # persisted credential and fingerprint instead of creating a
            # duplicate config or returning a permanent no-op.
            existing = owned_candidates[0]
            # A second process must not mistake a provider probe currently in
            # flight for an interrupted candidate.  A fresh started run owns
            # the candidate; an expired lease is atomically marked failed
            # before the next owner is allowed to recover it.
            started_runs = session.execute(
                select(LlmModelTestRun)
                .where(
                    LlmModelTestRun.model_config_id == existing.id,
                    LlmModelTestRun.test_type == "bootstrap",
                    LlmModelTestRun.status == "started",
                )
                .order_by(LlmModelTestRun.created_at.asc())
            ).scalars().all()
            now = _now()
            for started_run in started_runs:
                if not _bootstrap_run_expired(started_run, now=now):
                    return None
                locked_run = session.execute(
                    select(LlmModelTestRun)
                    .where(LlmModelTestRun.id == started_run.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).scalar_one_or_none()
                if locked_run is not None and locked_run.status == "started":
                    if not _bootstrap_run_expired(locked_run, now=now):
                        return None
                    locked_run.status = "failed"
                    locked_run.error_code = "llm_bootstrap_owner_lost"
                    locked_run.error_message = "大模型首次引导探测已失效，请重新测试"
                    session.flush()
            try:
                runtime = _runtime_from_config(existing)
                expected_base_url = validate_base_url(
                    existing.base_url,
                    runtime.provider,
                    resolve=False,
                )
                expected_fingerprint = runtime_fingerprint(
                    provider=runtime.provider,
                    model_name=runtime.model_name,
                    base_url=expected_base_url,
                    credential_version=runtime.credential_version,
                    max_output_tokens=runtime.max_output_tokens,
                )
            except Exception:
                # Existing encrypted material is not safe to guess or replace.
                raise ValueError("现有首次引导配置不可安全恢复") from None
            if expected_base_url != existing.base_url or existing.runtime_fingerprint != expected_fingerprint:
                raise ValueError("现有首次引导配置指纹校验失败")
            return _BootstrapCandidate(
                config_id=existing.id,
                config_version=int(existing.version),
                settings_version=int(setting.version),
                runtime=runtime,
            )
        # Any normal model, multiple candidates, an active/default candidate,
        # or a candidate modified by an administrator is a safe no-op.  The
        # bootstrap command must not infer ownership from ambiguous rows.
        return None
    if all_configs:
        # A deleted history row is safe to retain, but only a strictly
        # bootstrap-owned retired history permits the legacy environment to
        # create a fresh candidate.  Any administrator/ambiguous history
        # remains fail-closed and must be handled explicitly in the UI.
        history_is_bootstrap_owned = all(
            config.deleted_at is not None
            and config.lifecycle_status == ModelLifecycle.RETIRED.value
            and config.display_name == "DeepSeek 环境变量迁移"
            and config.created_by is None
            and setting.default_model_config_id != config.id
            for config in all_configs
        )
        if not history_is_bootstrap_owned:
            return None
    api_key = str(getattr(settings, "DEEPSEEK_API_KEY", "") or "").strip()
    if not api_key:
        return None

    provider = Provider.DEEPSEEK
    model_name = str(getattr(settings, "LLM_MODEL", "deepseek-chat") or "deepseek-chat").strip()
    base_url = validate_base_url(
        str(getattr(settings, "LLM_BASE_URL", "https://api.deepseek.com/v1") or "https://api.deepseek.com/v1"),
        provider,
        resolve=False,
    )
    config = LlmModelConfig(
        provider=provider.value,
        display_name="DeepSeek 环境变量迁移",
        model_name=model_name,
        base_url=base_url,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        lifecycle_status=ModelLifecycle.DRAFT.value,
    )
    envelope = encrypt_api_key(
        api_key,
        config_id=config.id,
        provider=provider,
        keyring=getattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", {}),
    )
    config.encrypted_api_key = envelope.encrypted_api_key
    config.encryption_key_id = envelope.encryption_key_id
    config.envelope_version = envelope.envelope_version
    config.nonce = envelope.nonce
    config.key_hint = _safe_key_hint(api_key)
    config.runtime_fingerprint = runtime_fingerprint(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        credential_version=config.credential_version,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )
    session.add(config)
    session.flush()
    return _BootstrapCandidate(
        config_id=config.id,
        config_version=int(config.version),
        settings_version=int(setting.version),
        runtime=LlmRuntimeConfig(
            config_id=config.id,
            provider=provider,
            display_name=config.display_name,
            model_name=config.model_name,
            base_url=config.base_url,
            api_key=api_key,
            credential_version=config.credential_version,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            input_price_micro_yuan_per_million=config.input_price_micro_yuan_per_million,
            output_price_micro_yuan_per_million=config.output_price_micro_yuan_per_million,
            runtime_fingerprint=config.runtime_fingerprint,
        ),
    )


def _probe_attempt_evidence(
    session_factory: Callable[[], Session] | Session,
    *,
    config_id: str,
    run_id: str,
) -> _ProbeAttemptEvidence:
    """Return structured evidence for this probe's durable call attempt."""

    with _readonly_session_scope(session_factory) as session:
        attempt = session.execute(
            select(LlmCallAttempt.status, LlmCallAttempt.error_code)
            .where(
                LlmCallAttempt.model_config_id == config_id,
                LlmCallAttempt.operation_type == "bootstrap",
                LlmCallAttempt.step_key == f"bootstrap:{run_id}",
            )
            .order_by(LlmCallAttempt.created_at.desc())
            .limit(1)
        ).one_or_none()
        if attempt is None:
            return _ProbeAttemptEvidence()
        return _ProbeAttemptEvidence(status=attempt[0], error_code=attempt[1])


def _is_upstream_probe_error(
    error: BaseException,
    *,
    attempt_evidence: _ProbeAttemptEvidence,
) -> bool:
    """Classify only a provider-backed, durably audited probe as non-fatal."""

    if not isinstance(error, LlmError):
        return False
    code = getattr(error, "code", None)
    if code in _LOCAL_PROBE_ERROR_CODES:
        return False
    if code == "llm_probe_invalid":
        # The HTTP/provider call succeeded; only our business-schema contract
        # failed.  Keep the successful attempt and failed probe result.
        return attempt_evidence.status == "success"
    if code == "llm_probe_timeout":
        # This is the bootstrap's own hard deadline (not a provider response)
        # and must remain an auditable failed candidate so a slow/partitioned
        # provider does not turn the one-shot migration into a fatal startup.
        return True
    if code == "llm_daily_limit_reached":
        # This code is also used by the local budget service.  A matching
        # completed attempt is the evidence that the provider, not the local
        # preflight guard, returned the daily-limit response.
        return (
            attempt_evidence.status in {"failed", "failed_unknown"}
            and attempt_evidence.error_code == code
        )
    return (
        code in _UPSTREAM_PROBE_ERROR_CODES
        and attempt_evidence.status in {"failed", "failed_unknown"}
        and attempt_evidence.error_code == code
    )


def _fatal_probe_cleanup(
    session_factory: Callable[[], Session] | Session,
    *,
    config_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
) -> bool:
    """Remove only an unreferenced local candidate after a fatal failure.

    Once an executor has created an attempt, the provider outcome may be
    unknown (or the persistence failure may have happened after a real
    response).  In that case the candidate is soft-deleted so its attempt,
    usage and reservation evidence remains queryable while a future bootstrap
    can create a fresh candidate.  A candidate with no attempt is safe to
    remove together with its started test row.
    """

    try:
        with _session_scope(session_factory) as session:
            setting = session.execute(
                select(LlmRuntimeSetting)
                .where(LlmRuntimeSetting.id == 1)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            config = session.execute(
                select(LlmModelConfig)
                .where(LlmModelConfig.id == config_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if config is None:
                return True
            # An administrator may have activated this candidate while the
            # probe was in flight.  Never retire or delete an admin-owned
            # default during fatal cleanup.
            if (
                setting is not None
                and setting.default_model_config_id == config_id
            ) or config.lifecycle_status == ModelLifecycle.ACTIVE.value:
                return True
            attempt_count = int(
                session.execute(
                    select(func.count())
                    .select_from(LlmCallAttempt)
                    .where(LlmCallAttempt.model_config_id == config_id)
                ).scalar_one()
            )
            run = session.get(LlmModelTestRun, run_id)
            if attempt_count > 0:
                # Preserve all real provider evidence.  The filtered empty
                # check in _build_candidate makes this candidate retryable.
                config.lifecycle_status = ModelLifecycle.RETIRED.value
                config.deleted_at = config.deleted_at or _now()
                if run is not None and run.status == "started":
                    run.status = "failed"
                    run.error_code = error_code
                    run.error_message = error_message
            else:
                if run is not None:
                    session.delete(run)
                session.delete(config)
        return True
    except Exception:
        # The original fatal result remains authoritative.  A database outage
        # during cleanup must never cause us to delete unknown evidence.
        return False


def _mark_probe_cleanup_failed(
    session_factory: Callable[[], Session] | Session,
    *,
    run_id: str,
    error_code: str,
    error_message: str,
) -> None:
    """Persist an interrupted marker when the first cleanup transaction fails."""

    if not run_id:
        return
    try:
        with _session_scope(session_factory) as session:
            run = session.get(LlmModelTestRun, run_id)
            if run is not None and run.status == "started":
                run.status = "failed"
                run.error_code = error_code
                run.error_message = error_message
    except Exception:
        # A persistent database outage remains fatal; do not expose details.
        return


def _notify_admins(title: str, content: str) -> None:
    """Reuse the existing in-app admin notification path without secrets."""

    safe_title = _redact_text(title)
    safe_content = _redact_text(content)
    try:
        from app.tasks.scheduler import _notify_admins as notify

        notify(safe_title, safe_content)
    except Exception:
        # Bootstrap must remain startable when notification infrastructure is
        # unavailable; the redacted CLI log remains the operator evidence.
        return


async def _probe_candidate(
    candidate: _BootstrapCandidate,
    *,
    session_factory: Callable[[], Session] | Session,
    executor: LlmCallExecutor | Any | None,
    test_type: str,
) -> CliResult:
    try:
        with _session_scope(session_factory) as session:
            run = LlmModelTestRun(
                model_config_id=candidate.config_id,
                runtime_fingerprint=candidate.runtime.runtime_fingerprint,
                test_type=test_type,
                status="started",
            )
            session.add(run)
            session.flush()
            run_id = run.id
    except Exception:
        # The candidate was committed by the preceding transaction.  Clean it
        # up without touching any pre-existing call evidence.
        _fatal_probe_cleanup(
            session_factory,
            config_id=candidate.config_id,
            run_id="",
            error_code="llm_audit_database",
            error_message="模型测试审计记录保存失败",
        )
        return CliResult(1, "bootstrap_fatal", "模型测试审计记录保存失败").with_rendered()

    owned_session: Session | None = None
    if executor is None:
        owned_session = session_factory() if not isinstance(session_factory, Session) else session_factory
        executor = LlmCallExecutor(db=owned_session)
    result: ProviderResult | None = None
    capabilities: dict[str, bool] = {}
    error_code: str | None = None
    error_message: str | None = None
    status = "failed"
    fatal_error: BaseException | None = None
    probe_error: LlmError | None = None
    try:
        async with asyncio.timeout(BOOTSTRAP_PROBE_DEADLINE_SECONDS):
            result = await executor.call(
                runtime_config=candidate.runtime,
                operation_type="bootstrap",
                step_key=f"bootstrap:{run_id}",
                messages=_probe_messages(),
                task_id=None,
                prompt_version=PROBE_PROMPT_VERSION,
            )
        capabilities = _validate_structured_result(result, candidate.runtime, purpose="bootstrap")
        status = "success"
    except asyncio.TimeoutError:
        error_code = "llm_probe_timeout"
        error_message = "模型测试超时，请稍后重试"
        probe_error = LlmError(error_message, code=error_code)
    except LlmError as exc:
        error_code = getattr(exc, "code", "llm_probe_failed")
        error_message = _redact_text(str(exc), [candidate.runtime.api_key])
        probe_error = exc
    except Exception as exc:
        # A non-LlmError is never an upstream probe outcome.  It may indicate
        # an audit/settlement failure after a real provider request, so cleanup
        # below preserves any attempt/usage evidence that already exists.
        error_code = "llm_probe_failed"
        error_message = "模型测试失败，请稍后查看管理员通知"
        fatal_error = exc
    finally:
        if owned_session is not None and owned_session is not session_factory:
            owned_session.close()

    if probe_error is not None:
        try:
            attempt_evidence = _probe_attempt_evidence(
                session_factory,
                config_id=candidate.config_id,
                run_id=run_id,
            )
        except Exception as exc:
            # If audit evidence cannot be read, fail closed rather than
            # classifying an unverified exception as an upstream failure.
            fatal_error = exc
        else:
            if not _is_upstream_probe_error(probe_error, attempt_evidence=attempt_evidence):
                fatal_error = probe_error

    if fatal_error is not None:
        cleanup_ok = _fatal_probe_cleanup(
            session_factory,
            config_id=candidate.config_id,
            run_id=run_id,
            error_code=error_code or "llm_probe_failed",
            error_message=error_message or "模型测试失败",
        )
        if not cleanup_ok:
            _mark_probe_cleanup_failed(
                session_factory,
                run_id=run_id,
                error_code=error_code or "llm_probe_failed",
                error_message=error_message or "模型测试失败",
            )
        safe_message = error_message or "大模型首次引导失败"
        return CliResult(1, "bootstrap_fatal", safe_message).with_rendered(
            secrets=[candidate.runtime.api_key]
        )

    conflict = False
    try:
        # Finalization is intentionally a short transaction.  The lock order
        # is always settings -> candidate, matching administrator activation
        # and preventing a probe from overwriting a concurrent admin choice.
        with _session_scope(session_factory) as session:
            setting = session.execute(
                select(LlmRuntimeSetting)
                .where(LlmRuntimeSetting.id == 1)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            config = session.execute(
                select(LlmModelConfig)
                .where(LlmModelConfig.id == candidate.config_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            run = session.execute(
                select(LlmModelTestRun)
                .where(LlmModelTestRun.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if run is None or setting is None:
                raise RuntimeError("模型测试记录保存失败")
            if run.status != "started":
                # A stale owner may finish after another bootstrap has marked
                # its lease lost.  Preserve that historical row exactly and
                # never let the late result change model state/defaults.
                conflict = True
            else:
                run.status = status
                run.capability_json = capabilities or None
                run.result_json = result.result_json if result is not None else None
                run.response_model = result.model if result is not None else None
                run.error_code = error_code
                run.error_message = error_message
                run.input_tokens = result.prompt_tokens if result is not None else None
                run.output_tokens = result.completion_tokens if result is not None else None
                if config is None:
                    # A Task4 delete is a soft delete, so this branch is only
                    # for an external hard-delete race.  Preserve the
                    # successful audit row and report a safe no-op.
                    conflict = True
                else:
                    other_count = int(
                        session.execute(
                            select(func.count())
                            .select_from(LlmModelConfig)
                            .where(
                                LlmModelConfig.deleted_at.is_(None),
                                LlmModelConfig.id != candidate.config_id,
                            )
                        ).scalar_one()
                    )
                    owns_candidate = (
                        config.deleted_at is None
                        and config.lifecycle_status == ModelLifecycle.DRAFT.value
                        and int(config.version) == candidate.config_version
                        and config.runtime_fingerprint == candidate.runtime.runtime_fingerprint
                        and int(setting.version) == candidate.settings_version
                        and setting.default_model_config_id in {None, candidate.config_id}
                        and other_count == 0
                    )
                    if status == "success" and owns_candidate:
                        config.lifecycle_status = ModelLifecycle.ACTIVE.value
                        config.verified_test_id = run.id
                        config.last_probe_status = "success"
                        config.last_probe_at = _now()
                        setting.default_model_config_id = config.id
                    elif status == "success":
                        # Preserve the successful probe audit but never
                        # overwrite an administrator's status/default after
                        # the network wait.
                        conflict = True
                    elif owns_candidate:
                        config.lifecycle_status = ModelLifecycle.DRAFT.value
                        config.last_probe_status = "failed"
                        config.last_probe_at = _now()
                    else:
                        # A delete/modify/activate/default change during the
                        # probe owns the outcome; keep its state untouched.
                        conflict = True
    except Exception:
        cleanup_ok = _fatal_probe_cleanup(
            session_factory,
            config_id=candidate.config_id,
            run_id=run_id,
            error_code="llm_audit_database",
            error_message="模型测试结果保存失败",
        )
        if not cleanup_ok:
            _mark_probe_cleanup_failed(
                session_factory,
                run_id=run_id,
                error_code="llm_audit_database",
                error_message="模型测试结果保存失败",
            )
        return CliResult(1, "bootstrap_fatal", "模型测试结果保存失败").with_rendered(
            secrets=[candidate.runtime.api_key]
        )

    if conflict:
        return CliResult(
            0,
            "bootstrap_noop",
            "管理员已更新模型配置，首次引导结果未覆盖默认模型",
        ).with_rendered(secrets=[candidate.runtime.api_key])
    if status != "success":
        _notify_admins("大模型首次引导测试失败", error_message or "模型测试失败")
        return CliResult(0, "bootstrap_failed", error_message or "模型测试失败").with_rendered(
            secrets=[candidate.runtime.api_key]
        )
    return CliResult(0, "bootstrap_ready", "大模型首次引导完成", candidate.runtime.display_name).with_rendered(
        secrets=[candidate.runtime.api_key]
    )


async def bootstrap_async(
    *,
    session_factory: Callable[[], Session] | Session | None = None,
    executor: LlmCallExecutor | Any | None = None,
) -> CliResult:
    factory = session_factory or SessionLocal
    try:
        with _session_scope(factory) as session:
            candidate = _build_candidate(session)
    except (LlmError, ValueError) as exc:
        # Configuration, URL and envelope failures are fatal: the API must
        # not start while a candidate cannot be represented safely.
        safe = _redact_text(str(exc), [str(getattr(settings, "DEEPSEEK_API_KEY", "") or "")])
        return CliResult(1, "bootstrap_fatal", safe or "大模型首次引导配置失败").with_rendered(
            secrets=[str(getattr(settings, "DEEPSEEK_API_KEY", "") or "")]
        )
    except Exception:
        return CliResult(1, "bootstrap_fatal", "大模型首次引导配置失败").with_rendered()
    if candidate is None:
        return CliResult(0, "bootstrap_noop", "大模型配置已存在，跳过首次引导").with_rendered()
    try:
        return await _probe_candidate(candidate, session_factory=factory, executor=executor, test_type="bootstrap")
    except Exception:
        # Keep the CLI contract stable even when an injected executor/audit
        # adapter fails outside the normal probe boundary.
        _fatal_probe_cleanup(
            factory,
            config_id=candidate.config_id,
            run_id="",
            error_code="llm_probe_failed",
            error_message="大模型首次引导失败",
        )
        return CliResult(1, "bootstrap_fatal", "大模型首次引导失败").with_rendered()


def run_bootstrap(
    *,
    session_factory: Callable[[], Session] | Session | None = None,
    executor: LlmCallExecutor | Any | None = None,
) -> int:
    result = asyncio.run(bootstrap_async(session_factory=session_factory, executor=executor))
    return result.exit_code


def _readiness_result(session_factory: Callable[[], Session] | Session) -> CliResult:
    try:
        with _readonly_session_scope(session_factory) as session:
            setting = session.execute(select(LlmRuntimeSetting).where(LlmRuntimeSetting.id == 1)).scalar_one_or_none()
            if setting is None or not setting.default_model_config_id:
                return CliResult(1, "not_ready", "尚未配置可用的默认模型")
            config = session.execute(
                select(LlmModelConfig).where(
                    LlmModelConfig.id == setting.default_model_config_id,
                    LlmModelConfig.deleted_at.is_(None),
                    LlmModelConfig.lifecycle_status == ModelLifecycle.ACTIVE.value,
                )
            ).scalar_one_or_none()
            if config is None or not config.verified_test_id:
                return CliResult(1, "not_ready", "默认模型尚未完成真实测试")
            run = session.execute(
                select(LlmModelTestRun).where(LlmModelTestRun.id == config.verified_test_id)
            ).scalar_one_or_none()
            if (
                run is None
                or run.model_config_id != config.id
                or run.status != "success"
                or run.runtime_fingerprint != config.runtime_fingerprint
            ):
                return CliResult(1, "not_ready", "默认模型测试资格已失效")
            return CliResult(0, "ready", "默认模型已就绪", _redact_text(config.display_name)).with_rendered()
    except Exception:
        return CliResult(1, "not_ready", "数据库迁移尚未完成").with_rendered()


def run_readiness(*, session_factory: Callable[[], Session] | Session | None = None) -> CliResult:
    result = _readiness_result(session_factory or SessionLocal)
    return result if result.rendered else result.with_rendered()


def _runtime_from_config(config: LlmModelConfig) -> LlmRuntimeConfig:
    envelope = CredentialEnvelope(
        envelope_version=config.envelope_version,
        encryption_key_id=config.encryption_key_id,
        nonce=config.nonce,
        encrypted_api_key=config.encrypted_api_key,
    )
    api_key = decrypt_api_key(
        envelope,
        config_id=config.id,
        provider=Provider(config.provider),
        keyring=getattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", {}),
    )
    return LlmRuntimeConfig(
        config_id=config.id,
        provider=Provider(config.provider),
        display_name=config.display_name,
        model_name=config.model_name,
        base_url=config.base_url,
        api_key=api_key,
        credential_version=config.credential_version,
        max_output_tokens=config.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
        input_price_micro_yuan_per_million=config.input_price_micro_yuan_per_million,
        output_price_micro_yuan_per_million=config.output_price_micro_yuan_per_million,
        runtime_fingerprint=config.runtime_fingerprint,
    )


async def live_smoke_async(
    *,
    provider: Provider | str,
    model_config_id: str,
    session_factory: Callable[[], Session] | Session,
    executor: LlmCallExecutor | Any | None = None,
) -> CliResult:
    try:
        provider_value = _as_provider(provider)
    except ValueError as exc:
        return CliResult(2, "live_smoke_invalid", str(exc)).with_rendered()
    if not isinstance(model_config_id, str) or not model_config_id.strip():
        return CliResult(2, "live_smoke_invalid", "必须指定模型配置 ID").with_rendered()
    with _session_scope(session_factory) as session:
        config = session.execute(
            select(LlmModelConfig).where(
                LlmModelConfig.id == model_config_id,
                LlmModelConfig.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if config is None:
            return CliResult(1, "live_smoke_failed", "模型配置不存在").with_rendered()
        try:
            saved_provider = Provider(config.provider)
        except (TypeError, ValueError):
            return CliResult(1, "live_smoke_invalid", "模型配置中的供应商无效").with_rendered()
        if saved_provider is not provider_value:
            return CliResult(2, "live_smoke_invalid", "供应商与模型配置不匹配").with_rendered()
        try:
            runtime = _runtime_from_config(config)
        except LlmError:
            return CliResult(1, "live_smoke_failed", "模型密钥不可用，请管理员检查加密密钥配置").with_rendered()
    owned_session: Session | None = None
    if executor is None:
        owned_session = session_factory() if not isinstance(session_factory, Session) else session_factory
        executor = LlmCallExecutor(db=owned_session)
    step_key = f"live-smoke:{model_config_id}:{uuid4()}"
    try:
        result = await executor.call(
            runtime_config=runtime,
            operation_type="live_smoke",
            step_key=step_key,
            messages=_probe_messages(),
            task_id=None,
            prompt_version=SMOKE_PROMPT_VERSION,
        )
        capabilities = _validate_structured_result(result, runtime, purpose="live_smoke")
        evidence: dict[str, object] = {
            "schema": True,
            "token_upper_bound": True,
            "provider": provider_value.value,
            "model_config_id": model_config_id,
        }
        if isinstance(executor, LlmCallExecutor):
            with _session_scope(session_factory) as session:
                attempt = session.execute(
                    select(LlmCallAttempt).where(
                        LlmCallAttempt.model_config_id == model_config_id,
                        LlmCallAttempt.operation_type == "live_smoke",
                        LlmCallAttempt.step_key == step_key,
                        LlmCallAttempt.status == "success",
                    )
                ).scalar_one_or_none()
                usage = session.execute(
                    select(LlmUsage).where(
                        LlmUsage.model_config_id == model_config_id,
                        LlmUsage.module == "live_smoke",
                        LlmUsage.status == "success",
                    ).order_by(LlmUsage.created_at.desc()).limit(1)
                ).scalar_one_or_none()
                if attempt is None or usage is None or not attempt.reservation_id:
                    raise LlmError("实时 smoke 缺少调用审计证据", code="llm_smoke_audit_missing")
                reservation = session.get(LlmTokenReservation, attempt.reservation_id)
                if reservation is None or reservation.status != "settled":
                    raise LlmError("实时 smoke 缺少额度证据", code="llm_smoke_budget_missing")
                evidence.update({"attempt": True, "usage": True, "budget": True})
        if owned_session is not None and owned_session is not session_factory:
            owned_session.close()
        return CliResult(0, "live_smoke_success", "实时模型 smoke 成功", runtime.display_name, evidence).with_rendered(
            secrets=[runtime.api_key]
        )
    except LlmError as exc:
        if owned_session is not None and owned_session is not session_factory:
            owned_session.close()
        return CliResult(1, "live_smoke_failed", _redact_text(str(exc), [runtime.api_key])).with_rendered(
            secrets=[runtime.api_key]
        )
    except Exception:
        if owned_session is not None and owned_session is not session_factory:
            owned_session.close()
        return CliResult(1, "live_smoke_failed", "实时模型 smoke 失败").with_rendered(
            secrets=[runtime.api_key]
        )


def run_live_smoke(
    *,
    provider: Provider | str,
    model_config_id: str,
    session_factory: Callable[[], Session] | Session | None = None,
    executor: LlmCallExecutor | Any | None = None,
) -> CliResult:
    return asyncio.run(
        live_smoke_async(
            provider=provider,
            model_config_id=model_config_id,
            session_factory=session_factory or SessionLocal,
            executor=executor,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="睿见投研大模型运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap", help="首次迁移旧 DeepSeek 配置并执行真实测试")
    subparsers.add_parser("readiness", help="只读检查默认模型就绪状态")
    smoke = subparsers.add_parser("live-smoke", help="显式对指定模型执行一次真实 smoke")
    smoke.add_argument("--provider", required=True, choices=[item.value for item in Provider])
    smoke.add_argument("--model-config-id", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "bootstrap":
        result = asyncio.run(bootstrap_async())
    elif args.command == "readiness":
        result = run_readiness()
    else:
        result = run_live_smoke(provider=args.provider, model_config_id=args.model_config_id)
    print(result.rendered or result.with_rendered().rendered)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised by the container
    raise SystemExit(main())


__all__ = [
    "CliResult",
    "bootstrap_async",
    "build_parser",
    "live_smoke_async",
    "main",
    "run_bootstrap",
    "run_live_smoke",
    "run_readiness",
]
