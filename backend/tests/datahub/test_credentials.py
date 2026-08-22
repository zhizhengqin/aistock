from app.datahub.credentials import CredentialCipher, credential_fingerprint, key_hint


def test_credential_cipher_round_trip_and_key_hint_never_exposes_secret():
    cipher = CredentialCipher.from_key(b"0123456789abcdef0123456789abcdef")
    envelope = cipher.encrypt("tushare-secret-token", aad=b"datahub:tushare:v1")

    assert cipher.decrypt(envelope, aad=b"datahub:tushare:v1") == "tushare-secret-token"
    assert key_hint("tushare-secret-token") == "...oken"
    assert "tushare-secret-token" not in envelope.ciphertext
    assert credential_fingerprint("tushare-secret-token") == credential_fingerprint(
        "tushare-secret-token"
    )
    assert credential_fingerprint("another-token") != credential_fingerprint(
        "tushare-secret-token"
    )
