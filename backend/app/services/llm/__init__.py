"""LLM model-center domain and runtime primitives."""

from app.services.llm.crypto import CredentialEnvelope, decrypt_api_key, encrypt_api_key
from app.services.llm.errors import LlmCredentialError, LlmError
from app.services.llm.http_client import close_llm_http_client, get_llm_http_client
from app.services.llm.types import LlmRuntimeConfig, ModelLifecycle, Provider

__all__ = [
    "CredentialEnvelope",
    "LlmCredentialError",
    "LlmError",
    "LlmExecutionService",
    "LlmRuntimeConfig",
    "ModelLifecycle",
    "Provider",
    "close_llm_http_client",
    "decrypt_api_key",
    "encrypt_api_key",
    "get_llm_http_client",
]


def __getattr__(name: str):
    """Load the task service lazily to keep ORM type imports acyclic."""

    if name == "LlmExecutionService":
        from app.services.llm.execution_service import LlmExecutionService

        return LlmExecutionService
    raise AttributeError(name)
