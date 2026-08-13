"""Stable domain types shared by the LLM model center.

The types in this module deliberately contain no persistence or provider
implementation details.  They are the small contract used by the model
configuration, execution and provider layers.
"""

from dataclasses import dataclass
from enum import StrEnum


class Provider(StrEnum):
    """Supported upstream model providers."""

    DEEPSEEK = "deepseek"
    KIMI = "kimi"
    QWEN = "qwen"


class ModelLifecycle(StrEnum):
    """Lifecycle states for a configured model."""

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class LlmRuntimeConfig:
    """Immutable model settings captured by one task execution.

    ``api_key`` is intentionally present only in this short-lived runtime
    value.  It must never be persisted or included in logs and user-facing
    responses.
    """

    config_id: str | None
    provider: Provider
    display_name: str
    model_name: str
    base_url: str
    api_key: str
    credential_version: str
    max_output_tokens: int
    input_price_micro_yuan_per_million: int | None
    output_price_micro_yuan_per_million: int | None
    runtime_fingerprint: str


__all__ = ["LlmRuntimeConfig", "ModelLifecycle", "Provider"]
