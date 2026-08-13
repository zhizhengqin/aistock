"""Persistent configuration and administrator workflow for model center."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.llm_config import (
    LlmActivationRequest,
    LlmAdminAuditEvent,
    LlmModelConfig,
    LlmModelTestRun,
    LlmRuntimeSetting,
)
from app.models.llm_execution import LlmCallAttempt
from app.models.llm_usage import LlmUsage
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.crypto import CredentialEnvelope, decrypt_api_key, encrypt_api_key
from app.services.llm.errors import LlmError
from app.services.llm.provider_client import ProviderResult
from app.services.llm.providers import provider_profile
from app.services.llm.types import LlmRuntimeConfig, ModelLifecycle, Provider
from app.services.llm.url_security import canonicalize_base_url, validate_base_url


DEFAULT_MAX_OUTPUT_TOKENS = 4096
PROBE_PROMPT_VERSION = "admin-probe-v1"


class LlmConfigServiceError(LlmError):
    """A stable, HTTP-mappable administrator configuration error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        field: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.status_code = status_code
        self.field = field
        self.user_message = message
        self.retryable = False
        self.confirmed_unsent = True
        self.may_have_sent = False


def _service_error(code: str, message: str, *, status_code: int = 409, field: str | None = None):
    return LlmConfigServiceError(code, message, status_code=status_code, field=field)


def runtime_fingerprint(
    *,
    provider: Provider | str,
    model_name: str,
    base_url: str,
    credential_version: str,
    max_output_tokens: int,
    provider_options: Mapping[str, object] | None = None,
) -> str:
    """Hash only immutable runtime parameters.

    Display names, prices and the encryption master-key ID deliberately do
    not participate in this fingerprint.  URL canonicalisation is shared
    with the provider client so a trailing slash cannot create a false
    runtime version.
    """

    try:
        provider_value = provider if isinstance(provider, Provider) else Provider(provider)
        canonical = canonicalize_base_url(base_url, provider_value)
    except (ValueError, TypeError, LlmError) as exc:
        if isinstance(exc, LlmError):
            raise
        raise _service_error("llm_url_invalid", "大模型地址无效", status_code=422) from None
    payload = {
        "provider": provider_value.value,
        "model_name": str(model_name).strip(),
        "base_url": canonical,
        "credential_version": str(credential_version),
        "max_output_tokens": int(max_output_tokens),
        "provider_options": dict(provider_options or {}),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _key_hint(api_key: str) -> str:
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _as_dict(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    if isinstance(payload, Mapping):
        return dict(payload)
    raise _service_error("llm_request_invalid", "大模型配置请求无效", status_code=422)


class LlmConfigService:
    """Owns model lifecycle, probes, activation and budget admin actions."""

    def __init__(
        self,
        db: Session | Callable[[], Session],
        *,
        executor: LlmCallExecutor | Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.clock = clock or _now
        self.executor = executor or LlmCallExecutor(db=db)

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

    def _keyring(self):
        keyring = getattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", None)
        if not keyring:
            raise _service_error("llm_encryption_not_configured", "大模型加密密钥尚未配置", status_code=503)
        return keyring

    def _canonical_url(self, provider: Provider, base_url: str) -> str:
        try:
            return validate_base_url(base_url, provider, resolve=False)
        except LlmError:
            raise
        except Exception:
            raise _service_error("llm_url_invalid", "大模型地址无效", status_code=422) from None

    def _runtime(self, config: LlmModelConfig) -> LlmRuntimeConfig:
        envelope = CredentialEnvelope(
            envelope_version=config.envelope_version,
            encryption_key_id=config.encryption_key_id,
            nonce=config.nonce,
            encrypted_api_key=config.encrypted_api_key,
        )
        try:
            api_key = decrypt_api_key(
                envelope,
                config_id=config.id,
                provider=Provider(config.provider),
                keyring=self._keyring(),
            )
        except LlmError:
            raise _service_error("llm_credential_error", "模型密钥不可用，请管理员检查加密密钥配置", status_code=503)
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

    def _settings(self, session: Session, *, create: bool = True) -> LlmRuntimeSetting | None:
        row = session.execute(select(LlmRuntimeSetting).where(LlmRuntimeSetting.id == 1)).scalar_one_or_none()
        if row is None and create:
            row = LlmRuntimeSetting(
                id=1,
                daily_token_limit=getattr(settings, "DAILY_TOKEN_LIMIT", 2_000_000),
                budget_locked=False,
                version=1,
            )
            session.add(row)
            session.flush()
        return row

    def _audit(
        self,
        session: Session,
        *,
        event_type: str,
        admin_user_id: int | None,
        model_config_id: str | None = None,
        reason: str | None = None,
        runtime_settings_version: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            LlmAdminAuditEvent(
                admin_user_id=admin_user_id,
                model_config_id=model_config_id,
                event_type=event_type,
                reason=reason,
                runtime_settings_version=runtime_settings_version,
                payload_json=payload,
            )
        )

    def _capabilities(self, config: LlmModelConfig, setting: LlmRuntimeSetting | None) -> dict[str, bool]:
        is_default = bool(setting and setting.default_model_config_id == config.id)
        deleted = config.deleted_at is not None
        status = str(config.lifecycle_status)
        return {
            "can_test": not deleted,
            "can_enable": not deleted and status in {ModelLifecycle.DRAFT.value, ModelLifecycle.DISABLED.value},
            "can_disable": not deleted and not is_default and status == ModelLifecycle.ACTIVE.value,
            "can_activate": not deleted and status in {
                ModelLifecycle.DRAFT.value,
                ModelLifecycle.ACTIVE.value,
                ModelLifecycle.DISABLED.value,
            },
            "can_delete": not deleted and not is_default,
        }

    def _serialize(self, config: LlmModelConfig, setting: LlmRuntimeSetting | None = None, *, created_new_version=False) -> dict[str, Any]:
        return {
            "id": config.id,
            "provider": config.provider,
            "display_name": config.display_name,
            "model_name": config.model_name,
            "base_url": config.base_url,
            "key_hint": config.key_hint,
            "lifecycle_status": config.lifecycle_status,
            "version": int(config.version),
            "runtime_fingerprint": config.runtime_fingerprint,
            "credential_version": config.credential_version,
            "verified_test_id": config.verified_test_id,
            "last_probe_status": config.last_probe_status,
            "last_probe_at": _iso(config.last_probe_at),
            "last_probe_latency_ms": config.last_probe_latency_ms,
            "input_price_micro_yuan_per_million": config.input_price_micro_yuan_per_million,
            "output_price_micro_yuan_per_million": config.output_price_micro_yuan_per_million,
            "max_output_tokens": config.max_output_tokens,
            "supersedes_id": config.supersedes_id,
            "created_new_version": created_new_version,
            "capabilities": self._capabilities(config, setting),
        }

    def _candidate_values(self, payload: Any, *, require_key: bool = True) -> dict[str, Any]:
        values = _as_dict(payload)
        try:
            provider = values.get("provider")
            provider = provider if isinstance(provider, Provider) else Provider(provider)
        except (TypeError, ValueError):
            raise _service_error("llm_provider_invalid", "大模型供应商配置无效", status_code=422, field="provider") from None
        for field in ("display_name", "model_name", "base_url"):
            value = values.get(field)
            if not isinstance(value, str) or not value.strip():
                raise _service_error("llm_field_invalid", f"{field}不能为空", status_code=422, field=field)
            values[field] = value.strip()
        key = values.get("api_key")
        if require_key and (not isinstance(key, str) or not key.strip()):
            raise _service_error("llm_credential_required", "未保存候选必须填写 API Key", status_code=422, field="api_key")
        if key is not None:
            values["api_key"] = key.strip()
        max_output = values.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
        if not isinstance(max_output, int) or isinstance(max_output, bool) or max_output <= 0:
            raise _service_error("llm_max_tokens_invalid", "最大输出 Token 必须为正整数", status_code=422, field="max_output_tokens")
        for field in ("input_price_micro_yuan_per_million", "output_price_micro_yuan_per_million"):
            value = values.get(field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise _service_error("llm_price_invalid", "模型价格必须为非负整数", status_code=422, field=field)
        values["provider"] = provider
        values["base_url"] = self._canonical_url(provider, values["base_url"])
        values["max_output_tokens"] = max_output
        return values

    def create(self, payload: Any, *, admin_user_id: int | None = None) -> dict[str, Any]:
        values = self._candidate_values(payload)
        config = LlmModelConfig(
            provider=values["provider"].value,
            display_name=values["display_name"],
            model_name=values["model_name"],
            base_url=values["base_url"],
            input_price_micro_yuan_per_million=values.get("input_price_micro_yuan_per_million"),
            output_price_micro_yuan_per_million=values.get("output_price_micro_yuan_per_million"),
            max_output_tokens=values["max_output_tokens"],
            created_by=admin_user_id,
            lifecycle_status=ModelLifecycle.DRAFT.value,
        )
        try:
            envelope = encrypt_api_key(
                values["api_key"],
                config_id=config.id,
                provider=values["provider"],
                keyring=self._keyring(),
            )
        except LlmError:
            raise _service_error("llm_encryption_failed", "大模型密钥加密失败", status_code=503)
        config.encrypted_api_key = envelope.encrypted_api_key
        config.encryption_key_id = envelope.encryption_key_id
        config.envelope_version = envelope.envelope_version
        config.nonce = envelope.nonce
        config.key_hint = _key_hint(values["api_key"])
        config.runtime_fingerprint = runtime_fingerprint(
            provider=values["provider"],
            model_name=config.model_name,
            base_url=config.base_url,
            credential_version=config.credential_version,
            max_output_tokens=config.max_output_tokens,
        )
        with self._session() as session:
            session.add(config)
            setting = self._settings(session)
            self._audit(session, event_type="model_create", admin_user_id=admin_user_id, model_config_id=config.id)
            session.flush()
            result = self._serialize(config, setting)
        return result

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        provider: Provider | str | None = None,
        lifecycle_status: str | None = None,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise _service_error("llm_pagination_invalid", "分页参数无效", status_code=422)
        with self._session() as session:
            query = select(LlmModelConfig).where(LlmModelConfig.deleted_at.is_(None))
            if provider is not None:
                try:
                    query = query.where(LlmModelConfig.provider == (provider.value if isinstance(provider, Provider) else Provider(provider).value))
                except (TypeError, ValueError):
                    raise _service_error("llm_provider_invalid", "大模型供应商配置无效", status_code=422)
            if lifecycle_status:
                if lifecycle_status not in {state.value for state in ModelLifecycle}:
                    raise _service_error("llm_lifecycle_invalid", "模型生命周期状态无效", status_code=422, field="lifecycle_status")
                query = query.where(LlmModelConfig.lifecycle_status == lifecycle_status)
            total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
            items = session.execute(
                query.order_by(LlmModelConfig.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            ).scalars().all()
            setting = self._settings(session)
            return {
                "items": [self._serialize(item, setting) for item in items],
                "total": int(total),
                "page": page,
                "page_size": page_size,
                "default_model_config_id": setting.default_model_config_id if setting else None,
                "daily_token_limit": int(setting.daily_token_limit) if setting else int(getattr(settings, "DAILY_TOKEN_LIMIT", 2_000_000)),
                "budget_locked": bool(setting.budget_locked) if setting else False,
                "settings_version": int(setting.version) if setting else 1,
            }

    def get_settings(self) -> dict[str, Any]:
        with self._session() as session:
            setting = self._settings(session)
            return {
                "id": 1,
                "daily_token_limit": int(setting.daily_token_limit),
                "budget_locked": bool(setting.budget_locked),
                "default_model_config_id": setting.default_model_config_id,
                "version": int(setting.version),
                "switched_by": setting.switched_by,
                "switched_at": _iso(setting.switched_at),
            }

    def patch(self, config_id: str, payload: Any, *, admin_user_id: int | None = None) -> dict[str, Any]:
        values = _as_dict(payload)
        expected = values.pop("expected_version", None)
        if not isinstance(expected, int) or expected <= 0:
            raise _service_error("llm_config_conflict", "配置版本已变化，请刷新后重试", status_code=409)
        with self._session() as session:
            config = session.get(LlmModelConfig, config_id)
            if config is None or config.deleted_at is not None:
                raise _service_error("llm_config_not_found", "模型配置不存在", status_code=404)
            if int(config.version) != expected:
                raise _service_error("llm_config_conflict", "配置版本已变化，请刷新后重试", status_code=409)
            # Pydantic has already trimmed DTO values; direct service callers
            # are still checked at this boundary.
            # An omitted or empty API key means retain the existing
            # credential.  It must not accidentally force a successor version
            # when the administrator only wants to clear the form field.
            if values.get("api_key") in (None, ""):
                values.pop("api_key", None)
            runtime_fields = {"provider", "model_name", "base_url", "api_key", "max_output_tokens"}
            changed_runtime = bool(runtime_fields.intersection(values))
            if "provider" in values and values["provider"] is not None:
                try:
                    values["provider"] = values["provider"] if isinstance(values["provider"], Provider) else Provider(values["provider"])
                except (TypeError, ValueError):
                    raise _service_error("llm_provider_invalid", "大模型供应商配置无效", status_code=422)
            provider = values.get("provider", Provider(config.provider))
            model_name = str(values.get("model_name", config.model_name)).strip()
            base_url = self._canonical_url(provider, str(values.get("base_url", config.base_url)).strip())
            max_output = values.get("max_output_tokens", config.max_output_tokens)
            if not isinstance(max_output, int) or isinstance(max_output, bool) or max_output <= 0:
                raise _service_error("llm_max_tokens_invalid", "最大输出 Token 必须为正整数", status_code=422)
            if not changed_runtime:
                for field in ("display_name", "input_price_micro_yuan_per_million", "output_price_micro_yuan_per_million"):
                    if field in values:
                        value = values[field]
                        if field == "display_name":
                            if not isinstance(value, str) or not value.strip():
                                raise _service_error("llm_field_invalid", "配置名称不能为空", status_code=422)
                            value = value.strip()
                        elif value is not None and (not isinstance(value, int) or value < 0):
                            raise _service_error("llm_price_invalid", "模型价格必须为非负整数", status_code=422)
                        setattr(config, field, value)
                config.version += 1
                self._audit(session, event_type="model_patch", admin_user_id=admin_user_id, model_config_id=config.id)
                session.flush()
                return self._serialize(config, self._settings(session))

            successor = LlmModelConfig(
                provider=provider.value,
                display_name=str(values.get("display_name", config.display_name)).strip(),
                model_name=model_name,
                base_url=base_url,
                input_price_micro_yuan_per_million=values.get("input_price_micro_yuan_per_million", config.input_price_micro_yuan_per_million),
                output_price_micro_yuan_per_million=values.get("output_price_micro_yuan_per_million", config.output_price_micro_yuan_per_million),
                max_output_tokens=max_output,
                created_by=admin_user_id,
                lifecycle_status=ModelLifecycle.DRAFT.value,
                supersedes_id=config.id,
            )
            api_key = values.get("api_key")
            if api_key is None:
                api_key = self._runtime(config).api_key
                credential_version = config.credential_version
            else:
                credential_version = str(uuid4())
            # A successor has a new immutable config ID, therefore the old
            # envelope cannot be copied: its AAD binds the previous ID.  Read
            # the old key once and re-encrypt it under the successor ID while
            # preserving credential_version when the key itself is unchanged.
            try:
                envelope = encrypt_api_key(api_key, config_id=successor.id, provider=provider, keyring=self._keyring())
            except LlmError:
                raise _service_error("llm_encryption_failed", "大模型密钥加密失败", status_code=503)
            successor.credential_version = credential_version
            successor.encrypted_api_key = envelope.encrypted_api_key
            successor.encryption_key_id = envelope.encryption_key_id
            successor.envelope_version = envelope.envelope_version
            successor.nonce = envelope.nonce
            successor.key_hint = config.key_hint if api_key is None else _key_hint(api_key)
            successor.runtime_fingerprint = runtime_fingerprint(
                provider=provider,
                model_name=successor.model_name,
                base_url=successor.base_url,
                credential_version=successor.credential_version,
                max_output_tokens=successor.max_output_tokens,
            )
            session.add(successor)
            self._audit(session, event_type="model_runtime_version", admin_user_id=admin_user_id, model_config_id=successor.id, payload={"supersedes_id": config.id})
            session.flush()
            return self._serialize(successor, self._settings(session), created_new_version=True)

    def _probe_messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "你是模型能力探测器。只能返回 JSON 对象。"},
            {
                "role": "user",
                "content": "请返回 {\"ok\":true,\"capabilities\":{\"json_mode\":true,\"usage\":true}}，不要添加 Markdown。",
            },
        ]

    def _probe_result(self, result: ProviderResult) -> dict[str, Any]:
        payload = result.result_json
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise _service_error("llm_probe_invalid", "模型未返回预期的 JSON 能力结果", status_code=503)
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {"json_mode": True, "usage": result.usage_source == "provider"}
        if capabilities.get("json_mode") is False:
            raise _service_error("llm_capability_unsupported", "模型不支持 JSON 模式", status_code=503)
        return {str(key): bool(value) for key, value in capabilities.items()}

    async def _run_probe(self, runtime: LlmRuntimeConfig, *, model_config_id: str | None, admin_user_id: int | None) -> dict[str, Any]:
        fingerprint = runtime.runtime_fingerprint
        run = LlmModelTestRun(
            model_config_id=model_config_id,
            runtime_fingerprint=fingerprint,
            test_type="probe",
            created_by=admin_user_id,
            status="started",
        )
        with self._session() as session:
            session.add(run)
            session.flush()
            run_id = run.id
        started = time.perf_counter()
        try:
            result = await self.executor.call(
                runtime_config=runtime,
                operation_type="admin_probe",
                step_key=f"admin-probe:{run_id}",
                messages=self._probe_messages(),
                task_id=None,
                prompt_version=PROBE_PROMPT_VERSION,
            )
            capabilities = self._probe_result(result)
            status = "success"
            error_code = None
            error_message = None
        except LlmError as exc:
            result = None
            capabilities = None
            status = "failed"
            error_code = getattr(exc, "code", "llm_probe_failed")
            error_message = str(exc)
        latency_ms = int((time.perf_counter() - started) * 1000)
        with self._session() as session:
            run = session.get(LlmModelTestRun, run_id)
            if run is None:
                raise _service_error("llm_probe_audit_failed", "模型测试记录保存失败", status_code=503)
            run.status = status
            run.capability_json = capabilities
            run.result_json = result.result_json if result is not None else None
            run.response_model = result.model if result is not None else None
            run.error_code = error_code
            run.error_message = error_message
            run.latency_ms = latency_ms
            run.input_tokens = result.prompt_tokens if result is not None else None
            run.output_tokens = result.completion_tokens if result is not None else None
            session.flush()
            return {
                "test_run_id": run.id,
                "status": status,
                "runtime_fingerprint": fingerprint,
                "capabilities": capabilities or {},
                "response_model": run.response_model,
                "latency_ms": latency_ms,
                "error_code": error_code,
                "error": error_message,
            }

    async def test_unsaved(self, payload: Any, *, admin_user_id: int | None = None) -> dict[str, Any]:
        values = self._candidate_values(payload)
        credential_version = str(uuid4())
        fingerprint = runtime_fingerprint(
            provider=values["provider"],
            model_name=values["model_name"],
            base_url=values["base_url"],
            credential_version=credential_version,
            max_output_tokens=values["max_output_tokens"],
        )
        runtime = LlmRuntimeConfig(
            config_id=None,
            provider=values["provider"],
            display_name=values["display_name"],
            model_name=values["model_name"],
            base_url=values["base_url"],
            api_key=values["api_key"],
            credential_version=credential_version,
            max_output_tokens=values["max_output_tokens"],
            input_price_micro_yuan_per_million=values.get("input_price_micro_yuan_per_million"),
            output_price_micro_yuan_per_million=values.get("output_price_micro_yuan_per_million"),
            runtime_fingerprint=fingerprint,
        )
        result = await self._run_probe(runtime, model_config_id=None, admin_user_id=admin_user_id)
        result["key_hint"] = _key_hint(values["api_key"])
        return result

    def _get_config(self, session: Session, config_id: str) -> LlmModelConfig:
        config = session.get(LlmModelConfig, config_id)
        if config is None or config.deleted_at is not None:
            raise _service_error("llm_config_not_found", "模型配置不存在", status_code=404)
        return config

    async def test_saved(self, config_id: str, *, admin_user_id: int | None = None) -> dict[str, Any]:
        with self._session() as session:
            config = self._get_config(session, config_id)
            runtime = self._runtime(config)
            expected_fingerprint = config.runtime_fingerprint
        result = await self._run_probe(runtime, model_config_id=config_id, admin_user_id=admin_user_id)
        with self._session() as session:
            current = self._get_config(session, config_id)
            if current.runtime_fingerprint != expected_fingerprint:
                # Keep the run for audit, but never grant verification to stale
                # runtime parameters.
                raise _service_error("llm_config_conflict", "测试期间配置已修改，结果不能用于验证", status_code=409)
            current.last_probe_status = result["status"]
            current.last_probe_at = self.clock()
            current.last_probe_latency_ms = result["latency_ms"]
            if result["status"] == "success":
                current.verified_test_id = result["test_run_id"]
            self._audit(session, event_type="model_test", admin_user_id=admin_user_id, model_config_id=config_id, payload={"status": result["status"]})
            session.flush()
        return result

    async def enable(self, config_id: str, *, expected_version: int, test_run_id: str | None = None, admin_user_id: int | None = None) -> dict[str, Any]:
        with self._session() as session:
            config = self._get_config(session, config_id)
            if int(config.version) != expected_version:
                raise _service_error("llm_config_conflict", "配置版本已变化，请刷新后重试")
            if config.lifecycle_status not in {ModelLifecycle.DRAFT.value, ModelLifecycle.DISABLED.value}:
                raise _service_error("llm_invalid_state_transition", "当前状态不能启用", status_code=409)
        # Always run a fresh test.  A supplied test_run_id is an optional
        # client hint, never a bypass of the production probe.
        result = await self.test_saved(config_id, admin_user_id=admin_user_id)
        if result["status"] != "success":
            raise _service_error("llm_probe_failed", "模型测试失败，不能启用", status_code=503)
        with self._session() as session:
            config = self._get_config(session, config_id)
            if int(config.version) != expected_version:
                raise _service_error("llm_config_conflict", "配置版本已变化，请刷新后重试")
            config.lifecycle_status = ModelLifecycle.ACTIVE.value
            config.verified_test_id = result["test_run_id"]
            self._audit(session, event_type="model_enable", admin_user_id=admin_user_id, model_config_id=config.id)
            session.flush()
            return self._serialize(config, self._settings(session)) | {"test_run_id": result["test_run_id"]}

    def disable(self, config_id: str, *, expected_version: int, admin_user_id: int | None = None) -> dict[str, Any]:
        with self._session() as session:
            config = self._get_config(session, config_id)
            setting = self._settings(session)
            if setting.default_model_config_id == config.id:
                raise _service_error("llm_default_disable_forbidden", "默认模型不能停用", status_code=409)
            if int(config.version) != expected_version:
                raise _service_error("llm_config_conflict", "配置版本已变化，请刷新后重试")
            if config.lifecycle_status != ModelLifecycle.ACTIVE.value:
                raise _service_error("llm_invalid_state_transition", "只有启用中的模型可以停用", status_code=409)
            config.lifecycle_status = ModelLifecycle.DISABLED.value
            config.version += 1
            self._audit(session, event_type="model_disable", admin_user_id=admin_user_id, model_config_id=config.id)
            session.flush()
            return self._serialize(config, setting)

    def delete(self, config_id: str, *, admin_user_id: int | None = None) -> None:
        with self._session() as session:
            config = self._get_config(session, config_id)
            setting = self._settings(session)
            if setting.default_model_config_id == config.id:
                raise _service_error("llm_default_delete_forbidden", "默认模型不能删除", status_code=409)
            config.deleted_at = self.clock()
            config.lifecycle_status = ModelLifecycle.RETIRED.value
            self._audit(session, event_type="model_delete", admin_user_id=admin_user_id, model_config_id=config.id)

    async def activate(self, config_id: str, *, expected_version: int, idempotency_key: str, admin_user_id: int | None = None) -> dict[str, Any]:
        request_payload = {"model_config_id": config_id, "expected_version": expected_version}
        request_hash = hashlib.sha256(json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._session() as session:
            existing = session.execute(select(LlmActivationRequest).where(LlmActivationRequest.idempotency_key == idempotency_key)).scalar_one_or_none()
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise _service_error("llm_idempotency_conflict", "幂等键已用于其他切换请求", status_code=409)
                if existing.response_json is not None:
                    return dict(existing.response_json)
            config = self._get_config(session, config_id)
            if int(config.version) != expected_version:
                raise _service_error("llm_config_conflict", "配置版本已变化，请刷新后重试")
            if config.lifecycle_status not in {
                ModelLifecycle.DRAFT.value,
                ModelLifecycle.ACTIVE.value,
                ModelLifecycle.DISABLED.value,
            }:
                raise _service_error("llm_invalid_state_transition", "当前状态不能切换为默认模型", status_code=409)
            runtime = self._runtime(config)
            fingerprint = config.runtime_fingerprint
        probe = await self._run_probe(runtime, model_config_id=config_id, admin_user_id=admin_user_id)
        if probe["status"] != "success":
            raise _service_error("llm_probe_failed", "模型测试失败，默认模型未切换", status_code=503)
        with self._session() as session:
            setting = session.execute(select(LlmRuntimeSetting).where(LlmRuntimeSetting.id == 1).with_for_update()).scalar_one_or_none()
            if setting is None:
                setting = LlmRuntimeSetting(id=1, daily_token_limit=getattr(settings, "DAILY_TOKEN_LIMIT", 2_000_000))
                session.add(setting)
                session.flush()
            config = self._get_config(session, config_id)
            if int(config.version) != expected_version or config.runtime_fingerprint != fingerprint:
                raise _service_error("llm_config_conflict", "测试期间配置已修改，不能切换默认模型")
            config.lifecycle_status = ModelLifecycle.ACTIVE.value
            config.verified_test_id = probe["test_run_id"]
            config.last_probe_status = "success"
            config.last_probe_at = self.clock()
            config.last_probe_latency_ms = probe["latency_ms"]
            previous_id = setting.default_model_config_id
            if previous_id and previous_id != config.id:
                previous = session.get(LlmModelConfig, previous_id)
                if previous and previous.deleted_at is None:
                    previous.lifecycle_status = ModelLifecycle.RETIRED.value
            setting.default_model_config_id = config.id
            setting.switched_by = admin_user_id
            setting.switched_at = self.clock()
            setting.version += 1
            response = self._serialize(config, setting) | {"switched_at": _iso(setting.switched_at)}
            request = session.execute(select(LlmActivationRequest).where(LlmActivationRequest.idempotency_key == idempotency_key)).scalar_one_or_none()
            if request is None:
                request = LlmActivationRequest(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    model_config_id=config.id,
                    expected_version=expected_version,
                    created_by=admin_user_id,
                    status="success",
                    response_json=response,
                )
                session.add(request)
            elif request.request_hash != request_hash:
                raise _service_error("llm_idempotency_conflict", "幂等键已用于其他切换请求", status_code=409)
            else:
                request.response_json = response
                request.status = "success"
            self._audit(session, event_type="model_activate", admin_user_id=admin_user_id, model_config_id=config.id, runtime_settings_version=setting.version)
            session.flush()
            return response

    def patch_settings(self, *, expected_version: int, daily_token_limit: int, admin_user_id: int | None = None) -> dict[str, Any]:
        if daily_token_limit <= 0:
            raise _service_error("llm_limit_invalid", "每日 Token 限额必须为正整数", status_code=422)
        with self._session() as session:
            setting = session.execute(select(LlmRuntimeSetting).where(LlmRuntimeSetting.id == 1).with_for_update()).scalar_one_or_none()
            if setting is None:
                setting = LlmRuntimeSetting(id=1, daily_token_limit=daily_token_limit)
                session.add(setting)
                session.flush()
            if int(setting.version) != expected_version:
                raise _service_error("llm_settings_conflict", "全局设置版本已变化，请刷新后重试")
            setting.daily_token_limit = daily_token_limit
            setting.version += 1
            self._audit(session, event_type="budget_limit_update", admin_user_id=admin_user_id, runtime_settings_version=setting.version)
            session.flush()
            return {
                "id": 1,
                "daily_token_limit": int(setting.daily_token_limit),
                "budget_locked": bool(setting.budget_locked),
                "default_model_config_id": setting.default_model_config_id,
                "version": int(setting.version),
                "switched_by": setting.switched_by,
                "switched_at": _iso(setting.switched_at),
            }

    def unlock_settings(self, *, expected_version: int, reason: str, admin_user_id: int | None = None) -> dict[str, Any]:
        reason = reason.strip() if isinstance(reason, str) else ""
        if not reason or not any("\u4e00" <= char <= "\u9fff" for char in reason):
            raise _service_error("llm_unlock_reason_invalid", "解锁原因必须使用中文说明", status_code=422)
        with self._session() as session:
            setting = session.execute(select(LlmRuntimeSetting).where(LlmRuntimeSetting.id == 1).with_for_update()).scalar_one_or_none()
            if setting is None:
                raise _service_error("llm_settings_not_found", "额度设置不存在", status_code=409)
            if int(setting.version) != expected_version:
                raise _service_error("llm_settings_conflict", "全局设置版本已变化，请刷新后重试")
            if not bool(setting.budget_locked):
                raise _service_error("llm_budget_already_unlocked", "额度当前未锁定", status_code=409)
            setting.budget_locked = False
            setting.version += 1
            self._audit(session, event_type="budget_unlock", admin_user_id=admin_user_id, reason=reason, runtime_settings_version=setting.version)
            session.flush()
            return {
                "id": 1,
                "daily_token_limit": int(setting.daily_token_limit),
                "budget_locked": False,
                "default_model_config_id": setting.default_model_config_id,
                "version": int(setting.version),
            }

    def usage(self, *, days: int = 7, provider: Provider | str | None = None, model: str | None = None) -> dict[str, Any]:
        if days < 1 or days > 90:
            raise _service_error("llm_usage_range_invalid", "用量查询天数必须在 1 到 90 之间", status_code=422)
        since = self.clock() - timedelta(days=days)
        with self._session() as session:
            query = select(
                LlmUsage.module,
                LlmUsage.provider_snapshot,
                LlmUsage.model_snapshot,
                func.sum(LlmUsage.input_tokens).label("input_tokens"),
                func.sum(LlmUsage.output_tokens).label("output_tokens"),
                func.sum(LlmUsage.cost_micro_yuan).label("cost_micro_yuan"),
                func.count(LlmUsage.id).label("calls"),
            ).where(LlmUsage.created_at >= since).group_by(LlmUsage.module, LlmUsage.provider_snapshot, LlmUsage.model_snapshot)
            if provider is not None:
                value = provider.value if isinstance(provider, Provider) else Provider(provider).value
                query = query.where(LlmUsage.provider_snapshot == value)
            if model:
                query = query.where(LlmUsage.model_snapshot == model.strip())
            rows = session.execute(query).all()
            items = [
                {
                    "module": row.module,
                    "provider": row.provider_snapshot,
                    "model": row.model_snapshot,
                    "input_tokens": int(row.input_tokens or 0),
                    "output_tokens": int(row.output_tokens or 0),
                    "cost_micro_yuan": int(row.cost_micro_yuan or 0),
                    "calls": int(row.calls or 0),
                }
                for row in rows
            ]
            return {"days": days, "items": items, "total_calls": sum(item["calls"] for item in items), "total_cost_micro_yuan": sum(item["cost_micro_yuan"] for item in items)}


__all__ = ["LlmConfigService", "LlmConfigServiceError", "runtime_fingerprint"]
