"""Compatibility facade for the task-scoped LLM model center.

Business code is migrating from the historical ``chat`` helper to
``TaskExecutionContext.llm.execute_json``.  This module intentionally contains
no provider HTTP client, mock payload, or usage Session; the legacy symbol is
kept only so older orchestrator imports fail with a stable, actionable error
until their Task 9/10 migrations land.
"""

from app.services.llm import (
    CredentialEnvelope,
    LlmCredentialError,
    LlmError,
    LlmRuntimeConfig,
    ModelLifecycle,
    Provider,
    close_llm_http_client,
    decrypt_api_key,
    encrypt_api_key,
    get_llm_http_client,
)
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.execution_service import LlmExecutionService


async def _legacy_chat_removed(*_args, **_kwargs):
    """Reject pre-model-center calls without generating business output."""

    raise LlmError(
        "旧版模型调用入口已停用，请使用任务级结构化模型服务",
        code="llm_legacy_chat_removed",
    )


# Keep import compatibility for orchestrator modules that are migrated later.
chat = _legacy_chat_removed


__all__ = [
    "CredentialEnvelope",
    "LlmCallExecutor",
    "LlmCredentialError",
    "LlmError",
    "LlmExecutionService",
    "LlmRuntimeConfig",
    "ModelLifecycle",
    "Provider",
    "chat",
    "close_llm_http_client",
    "decrypt_api_key",
    "encrypt_api_key",
    "get_llm_http_client",
]
