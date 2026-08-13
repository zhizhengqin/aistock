"""AES-256-GCM envelopes for provider API keys.

Only the encrypted envelope is intended to cross a persistence boundary.  A
decrypted key exists in memory for the minimum amount of time needed by the
provider adapter and is never interpolated into an exception or log message.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.services.llm.errors import LlmCredentialError
from app.services.llm.types import Provider


ENVELOPE_VERSION = "v1"
NONCE_BYTES = 12  # 96-bit nonce, the GCM interoperable recommendation.
AES256_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class CredentialEnvelope:
    """Base64-serializable AES-GCM envelope metadata and ciphertext.

    ``encrypted_api_key`` contains the ciphertext followed by the 128-bit GCM
    authentication tag, as returned by :meth:`AESGCM.encrypt`.
    """

    envelope_version: str
    encryption_key_id: str
    nonce: str
    encrypted_api_key: str

    @property
    def ciphertext(self) -> str:
        """Compatibility alias for callers that call the payload ciphertext."""

        return self.encrypted_api_key


def _provider_value(provider: Provider | str) -> str:
    if isinstance(provider, Provider):
        return provider.value
    if isinstance(provider, str) and provider:
        return provider
    raise LlmCredentialError("大模型凭据参数无效")


def _decode_b64(value: str | bytes, *, field: str) -> bytes:
    if isinstance(value, str):
        encoded = value.encode("ascii")
    elif isinstance(value, bytes):
        encoded = value
    else:
        raise LlmCredentialError("大模型密钥配置无效")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError) as exc:
        # ``field`` is a fixed field name, never a secret or ciphertext value.
        raise LlmCredentialError(f"大模型凭据{field}编码无效") from None
    return decoded


def _decode_key(key_id: str, value: str | bytes) -> bytes:
    # Runtime callers may already hold decoded bytes (settings values are
    # always Base64 strings).  Supporting this avoids an unnecessary
    # encode/decode round trip while preserving the strict 256-bit check.
    if isinstance(value, bytes) and len(value) == AES256_KEY_BYTES:
        return value
    try:
        decoded = _decode_b64(value, field="密钥")
    except LlmCredentialError:
        raise
    if len(decoded) != AES256_KEY_BYTES:
        raise LlmCredentialError("大模型加密密钥长度无效")
    return decoded


def _normalise_keyring(
    keyring: Mapping[str, str | bytes] | None,
) -> Mapping[str, str | bytes]:
    if keyring is None:
        keyring = settings.LLM_CONFIG_ENCRYPTION_KEYS
    if not isinstance(keyring, Mapping):
        raise LlmCredentialError("大模型密钥环配置无效")
    return keyring


def _resolve_write_key_id(
    keyring: Mapping[str, str | bytes], key_id: str | None,
) -> str:
    if key_id:
        return key_id

    configured = getattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "")
    if configured and configured in keyring:
        return configured

    # Local tests/dev can supply a single in-memory keyring without mutating
    # process settings.  Production settings validation always requires an
    # explicit write ID, so this fallback cannot weaken production startup.
    if len(keyring) == 1:
        return next(iter(keyring))
    if "current" in keyring:
        return "current"
    raise LlmCredentialError("大模型写入密钥未配置")


def _aad(config_id: str, provider: Provider | str, envelope_version: str) -> bytes:
    if not isinstance(config_id, str) or not config_id:
        raise LlmCredentialError("大模型凭据参数无效")
    provider_value = _provider_value(provider)
    if envelope_version != ENVELOPE_VERSION:
        raise LlmCredentialError("大模型凭据信封版本不受支持")
    # This exact byte sequence is part of the persisted-envelope contract.
    return f"{envelope_version}|{config_id}|{provider_value}".encode("utf-8")


def encrypt_api_key(
    api_key: str,
    *,
    config_id: str,
    provider: Provider | str,
    keyring: Mapping[str, str | bytes] | None = None,
    key_id: str | None = None,
    write_key_id: str | None = None,
) -> CredentialEnvelope:
    """Encrypt an API key with the configured write key.

    ``key_id`` (or its descriptive alias ``write_key_id``) is explicit for
    key rotation jobs.  Normal writes leave both unset and use
    ``LLM_CONFIG_ENCRYPTION_KEY_ID``; the only fallback is a single supplied
    key (or a key named ``current``) for local/dev use.
    """

    if not isinstance(api_key, str) or not api_key:
        raise LlmCredentialError("大模型凭据不能为空")

    try:
        if key_id and write_key_id and key_id != write_key_id:
            raise LlmCredentialError("大模型写入密钥参数冲突")
        keys = _normalise_keyring(keyring)
        selected_key_id = _resolve_write_key_id(keys, key_id or write_key_id)
        if selected_key_id not in keys:
            raise LlmCredentialError("大模型写入密钥不存在")
        key = _decode_key(selected_key_id, keys[selected_key_id])
        aad = _aad(config_id, provider, ENVELOPE_VERSION)
        nonce = os.urandom(NONCE_BYTES)
        encrypted = AESGCM(key).encrypt(nonce, api_key.encode("utf-8"), aad)
        return CredentialEnvelope(
            envelope_version=ENVELOPE_VERSION,
            encryption_key_id=selected_key_id,
            nonce=base64.b64encode(nonce).decode("ascii"),
            encrypted_api_key=base64.b64encode(encrypted).decode("ascii"),
        )
    except LlmCredentialError:
        raise
    except (UnicodeError, TypeError, ValueError):
        raise LlmCredentialError("大模型凭据加密失败") from None


def decrypt_api_key(
    envelope: CredentialEnvelope,
    *,
    config_id: str,
    provider: Provider | str,
    keyring: Mapping[str, str | bytes] | None = None,
) -> str:
    """Decrypt and authenticate an envelope using any configured read key."""

    try:
        if not isinstance(envelope, CredentialEnvelope):
            raise LlmCredentialError("大模型凭据信封无效")
        if envelope.envelope_version != ENVELOPE_VERSION:
            raise LlmCredentialError("大模型凭据信封版本不受支持")

        keys = _normalise_keyring(keyring)
        key_value = keys.get(envelope.encryption_key_id)
        if key_value is None:
            raise LlmCredentialError("大模型历史加密密钥不可用")
        key = _decode_key(envelope.encryption_key_id, key_value)
        nonce = _decode_b64(envelope.nonce, field="随机数")
        if len(nonce) != NONCE_BYTES:
            raise LlmCredentialError("大模型凭据随机数长度无效")
        ciphertext = _decode_b64(envelope.encrypted_api_key, field="密文")
        aad = _aad(config_id, provider, envelope.envelope_version)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        return plaintext.decode("utf-8")
    except LlmCredentialError:
        raise
    except (InvalidTag, UnicodeDecodeError, TypeError, ValueError, binascii.Error):
        # Authentication failures and malformed persisted values are
        # intentionally indistinguishable to callers; no secret is exposed.
        raise LlmCredentialError("大模型凭据校验失败") from None
    except Exception:
        # Keep unexpected crypto/backend failures redacted as well.  The
        # original exception is deliberately not chained into the traceback.
        raise LlmCredentialError("大模型凭据解密失败") from None


__all__ = [
    "CredentialEnvelope",
    "decrypt_api_key",
    "encrypt_api_key",
]
