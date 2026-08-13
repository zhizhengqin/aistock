"""LLM model-center domain and runtime primitives."""

from app.services.llm.crypto import CredentialEnvelope, decrypt_api_key, encrypt_api_key
from app.services.llm.errors import LlmCredentialError, LlmError
from app.services.llm.http_client import close_llm_http_client, get_llm_http_client
from app.services.llm.types import LlmRuntimeConfig, ModelLifecycle, Provider

__all__ = [
    "CredentialEnvelope",
    "LlmCredentialError",
    "LlmError",
    "LlmRuntimeConfig",
    "ModelLifecycle",
    "Provider",
    "close_llm_http_client",
    "decrypt_api_key",
    "encrypt_api_key",
    "get_llm_http_client",
]
