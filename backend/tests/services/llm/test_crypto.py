import base64
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.llm.crypto import CredentialEnvelope, decrypt_api_key, encrypt_api_key
from app.services.llm.errors import LlmCredentialError
from app.services.llm.types import Provider


@pytest.fixture
def keyring() -> dict[str, str]:
    return {
        "current": base64.b64encode(b"c" * 32).decode("ascii"),
        "old": base64.b64encode(b"o" * 32).decode("ascii"),
    }


def test_envelope_round_trip(keyring):
    envelope = encrypt_api_key(
        "sk-secret", config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
    )

    assert isinstance(envelope, CredentialEnvelope)
    assert envelope.envelope_version == "v1"
    assert envelope.encryption_key_id == "current"
    assert decrypt_api_key(
        envelope, config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
    ) == "sk-secret"


def test_envelope_rejects_wrong_config_id(keyring):
    envelope = encrypt_api_key(
        "sk-secret", config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
    )

    with pytest.raises(LlmCredentialError) as exc:
        decrypt_api_key(
            envelope, config_id="cfg-b", provider=Provider.KIMI, keyring=keyring
        )

    assert "sk-secret" not in str(exc.value)


def test_envelope_rejects_tampered_ciphertext(keyring):
    envelope = encrypt_api_key(
        "sk-secret", config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
    )
    encoded = bytearray(base64.b64decode(envelope.encrypted_api_key))
    encoded[-1] ^= 0x01
    tampered = replace(
        envelope,
        encrypted_api_key=base64.b64encode(bytes(encoded)).decode("ascii"),
    )

    with pytest.raises(LlmCredentialError):
        decrypt_api_key(
            tampered, config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
        )


def test_envelope_rejects_missing_historical_key(keyring):
    envelope = encrypt_api_key(
        "sk-secret",
        config_id="cfg-a",
        provider=Provider.KIMI,
        keyring=keyring,
        key_id="old",
    )

    with pytest.raises(LlmCredentialError) as exc:
        decrypt_api_key(
            envelope,
            config_id="cfg-a",
            provider=Provider.KIMI,
            keyring={"current": keyring["current"]},
        )

    assert "sk-secret" not in str(exc.value)
    assert "old" not in str(exc.value)


def test_key_rotation_is_dual_read_single_write(keyring, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "current")

    old_envelope = encrypt_api_key(
        "sk-secret",
        config_id="cfg-a",
        provider=Provider.KIMI,
        keyring=keyring,
        key_id="old",
    )
    assert decrypt_api_key(
        old_envelope, config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
    ) == "sk-secret"

    new_envelope = encrypt_api_key(
        "sk-secret",
        config_id="cfg-a",
        provider=Provider.KIMI,
        keyring=keyring,
    )
    assert new_envelope.encryption_key_id == "current"
    assert new_envelope.encryption_key_id != old_envelope.encryption_key_id


def test_production_keyring_validation_redacts_secret_from_errors():
    secret = base64.b64encode(b"master-key-material-that-is-not-valid-length").decode("ascii")

    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            ENV="production",
            LLM_CONFIG_ENCRYPTION_KEY_ID="current",
            LLM_CONFIG_ENCRYPTION_KEYS={"current": secret},
        )

    assert secret not in str(exc.value)
    assert secret not in repr(exc.value.errors())


def test_crypto_errors_are_redacted(keyring):
    envelope = encrypt_api_key(
        "sk-secret", config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
    )
    broken = replace(envelope, nonce="not-base64")

    with pytest.raises(LlmCredentialError) as exc:
        decrypt_api_key(
            broken, config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
        )

    assert "sk-secret" not in str(exc.value)
    assert "not-base64" not in str(exc.value)


def test_invalid_key_length_is_rejected():
    keyring = {"current": base64.b64encode(b"too-short").decode("ascii")}

    with pytest.raises(LlmCredentialError):
        encrypt_api_key(
            "sk-secret", config_id="cfg-a", provider=Provider.KIMI, keyring=keyring
        )
