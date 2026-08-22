"""Generic AES-256-GCM credentials used by DataHub providers.

The LLM model-center keeps its historical AAD byte protocol in its existing
wrapper.  This module is intentionally namespaced for new DataHub records and
uses the same envelope primitives so rotation and redaction rules are shared.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class CredentialEnvelope:
    version: str
    key_id: str
    nonce: str
    ciphertext: str

    @property
    def encrypted_value(self) -> str:
        return self.ciphertext


class CredentialCipher:
    VERSION = "v1"

    def __init__(self, key: bytes, *, key_id: str = "current") -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("凭据加密密钥必须为 32 字节")
        self._key = key
        self.key_id = key_id

    @classmethod
    def from_key(cls, key: bytes, *, key_id: str = "current") -> "CredentialCipher":
        return cls(key, key_id=key_id)

    def encrypt(self, plaintext: str, *, aad: bytes = b"") -> CredentialEnvelope:
        if not isinstance(plaintext, str) or not plaintext:
            raise ValueError("凭据不能为空")
        nonce = os.urandom(12)
        value = AESGCM(self._key).encrypt(nonce, plaintext.encode("utf-8"), aad)
        return CredentialEnvelope(
            version=self.VERSION,
            key_id=self.key_id,
            nonce=base64.b64encode(nonce).decode("ascii"),
            ciphertext=base64.b64encode(value).decode("ascii"),
        )

    def decrypt(self, envelope: CredentialEnvelope, *, aad: bytes = b"") -> str:
        if envelope.version != self.VERSION:
            raise ValueError("不支持的凭据版本")
        try:
            value = AESGCM(self._key).decrypt(
                base64.b64decode(envelope.nonce, validate=True),
                base64.b64decode(envelope.ciphertext, validate=True),
                aad,
            )
            return value.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("凭据校验失败") from None


def credential_fingerprint(value: str) -> str:
    """Stable non-reversible fingerprint for probe/config invalidation."""

    return hmac.new(b"aistock-datahub-credential-v1", value.encode("utf-8"), hashlib.sha256).hexdigest()


def key_hint(value: str) -> str:
    if not value:
        return ""
    return f"...{value[-4:]}"


__all__ = ["CredentialCipher", "CredentialEnvelope", "credential_fingerprint", "key_hint"]
