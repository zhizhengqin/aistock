"""Shared credential primitives for DataHub and future configuration stores.

The legacy LLM wrapper remains responsible for its historical AAD contract;
new providers use the namespaced DataHub cipher exported here.
"""

from app.datahub.credentials import CredentialCipher, CredentialEnvelope, credential_fingerprint, key_hint

__all__ = ["CredentialCipher", "CredentialEnvelope", "credential_fingerprint", "key_hint"]
