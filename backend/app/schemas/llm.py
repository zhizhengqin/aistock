"""Pydantic contracts for the administrator LLM model center."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.llm.types import Provider


def _trim(value: str, *, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}必须是文本")
    value = value.strip()
    if not value:
        raise ValueError(f"{field}不能为空")
    if len(value) > max_length:
        raise ValueError(f"{field}长度不能超过{max_length}个字符")
    return value


class LlmModelCandidate(BaseModel):
    """Fields shared by unsaved probes and model creation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Provider
    display_name: str = Field(min_length=1, max_length=128)
    model_name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=1, max_length=512)
    max_output_tokens: int = Field(default=4096, gt=0, le=1_000_000)
    input_price_micro_yuan_per_million: int | None = Field(default=None, ge=0)
    output_price_micro_yuan_per_million: int | None = Field(default=None, ge=0)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return _trim(value, field="配置名称", max_length=128)

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        return _trim(value, field="模型 ID", max_length=128)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _trim(value, field="Base URL", max_length=512)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        return _trim(value, field="API Key", max_length=512)


class LlmModelCreateRequest(LlmModelCandidate):
    pass


class LlmModelProbeRequest(LlmModelCandidate):
    pass


class LlmModelPatchRequest(BaseModel):
    """Optimistic PATCH; an empty API key explicitly means keep old key."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(gt=0)
    provider: Provider | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    max_output_tokens: int | None = Field(default=None, gt=0, le=1_000_000)
    input_price_micro_yuan_per_million: int | None = Field(default=None, ge=0)
    output_price_micro_yuan_per_million: int | None = Field(default=None, ge=0)

    @field_validator("display_name")
    @classmethod
    def patch_display_name(cls, value: str | None) -> str | None:
        return None if value is None else _trim(value, field="配置名称", max_length=128)

    @field_validator("model_name")
    @classmethod
    def patch_model_name(cls, value: str | None) -> str | None:
        return None if value is None else _trim(value, field="模型 ID", max_length=128)

    @field_validator("base_url")
    @classmethod
    def patch_base_url(cls, value: str | None) -> str | None:
        return None if value is None else _trim(value, field="Base URL", max_length=512)

    @field_validator("api_key")
    @classmethod
    def patch_api_key(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        return _trim(value, field="API Key", max_length=512)


class LlmModelActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0)
    test_run_id: str | None = Field(default=None, min_length=1, max_length=36)


class LlmActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def trim_idempotency_key(cls, value: str) -> str:
        return _trim(value, field="幂等键", max_length=128)


class LlmSettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0)
    daily_token_limit: int = Field(gt=0, le=2_147_483_647)


class LlmSettingsUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("解锁原因不能为空")
        if not re.search(r"[\u4e00-\u9fff]", value):
            raise ValueError("解锁原因必须使用中文说明")
        return value


class LlmModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provider: Provider
    display_name: str
    model_name: str
    base_url: str
    key_hint: str | None
    lifecycle_status: str
    version: int
    runtime_fingerprint: str
    verified_test_id: str | None = None
    last_probe_status: str | None = None
    last_probe_at: str | None = None
    last_probe_latency_ms: int | None = None
    input_price_micro_yuan_per_million: int | None = None
    output_price_micro_yuan_per_million: int | None = None
    max_output_tokens: int | None = None
    created_new_version: bool = False
    supersedes_id: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)


class LlmModelListResponse(BaseModel):
    items: list[LlmModelResponse]
    total: int
    page: int
    page_size: int
    default_model_config_id: str | None = None
    daily_token_limit: int
    budget_locked: bool
    settings_version: int


# Short aliases keep the DTO surface convenient for service callers while the
# explicit ``Request`` names remain the FastAPI-facing contract.
LlmModelCreate = LlmModelCreateRequest
LlmModelProbe = LlmModelProbeRequest
LlmModelPatch = LlmModelPatchRequest
LlmModelTestRequest = LlmModelActionRequest
LlmActivate = LlmActivateRequest


__all__ = [
    "LlmActivateRequest",
    "LlmModelActionRequest",
    "LlmModelCandidate",
    "LlmModelCreateRequest",
    "LlmModelCreate",
    "LlmModelListResponse",
    "LlmModelPatchRequest",
    "LlmModelPatch",
    "LlmModelProbe",
    "LlmModelProbeRequest",
    "LlmModelTestRequest",
    "LlmActivate",
    "LlmModelResponse",
    "LlmSettingsPatchRequest",
    "LlmSettingsUnlockRequest",
]
