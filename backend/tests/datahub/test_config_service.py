from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.datahub.config_service import DataHubConfigService, ProbeRecord
from app.datahub.errors import DataHubConflict, DataHubError
from app.datahub.registry import get_provider
from app.datahub.credentials import credential_fingerprint
from app.models.base import Base


@pytest.fixture
def service():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield DataHubConfigService(session, encryption_key=b"0123456789abcdef0123456789abcdef")
    session.close()


def test_save_config_encrypts_token_and_returns_only_key_hint(service):
    saved = service.save_config(
        "tushare",
        public_config={"timeout": 10},
        credentials={"token": "tushare-secret-token"},
        expected_version=None,
        actor_id=1,
    )
    assert saved.provider == "tushare"
    assert saved.key_hint == "...oken"
    assert "tushare-secret-token" not in str(saved.model_dump())
    row = service.db.query(service.config_model).one()
    assert "tushare-secret-token" not in (row.encrypted_credentials or "")


def test_save_config_requires_if_match_version(service):
    saved = service.save_config("tencent", public_config={}, credentials={}, expected_version=None, actor_id=1)
    assert saved.enabled is True
    with pytest.raises(DataHubConflict):
        service.save_config("tencent", public_config={}, credentials={}, expected_version=99, actor_id=1)


def test_probe_is_valid_for_fifteen_minutes_and_invalidated_by_fingerprint(service):
    service.save_config("tushare", public_config={}, credentials={"token": "token-a"}, expected_version=None, actor_id=1)
    service.record_probe(ProbeRecord(provider="tushare", capability="kpl.limit_list", status="ok", rows=2, latency_ms=4), actor_id=1)
    assert service.probe_is_valid("tushare", "kpl.limit_list") is True
    service.save_config("tushare", public_config={}, credentials={"token": "token-b"}, expected_version=1, actor_id=1)
    assert service.probe_is_valid("tushare", "kpl.limit_list") is False


def test_empty_credentials_keep_previous_encrypted_value(service):
    service.save_config("tushare", public_config={}, credentials={"token": "token-a"}, expected_version=None, actor_id=1)
    updated = service.save_config("tushare", public_config={"timeout": 10}, credentials={}, expected_version=1, actor_id=1)
    assert updated.version == 2
    assert service.load_credentials("tushare") == {"token": "token-a"}


def test_empty_public_config_keeps_previous_public_settings(service):
    service.save_config("tushare", public_config={"timeout": 10, "capability": "kpl.limit_list"}, credentials={"token": "token-a"}, expected_version=None, actor_id=1)
    updated = service.save_config("tushare", public_config=None, credentials={}, expected_version=1, actor_id=1)
    assert updated.version == 2
    row = service.db.query(service.config_model).one()
    assert row.public_config_json == {"timeout": 10, "capability": "kpl.limit_list"}


def test_new_multi_field_credentials_require_all_required_fields(service):
    with pytest.raises(DataHubError) as error:
        service.save_config(
            "kpl_native",
            public_config={},
            credentials={"token": "fake-token"},
            expected_version=None,
            actor_id=1,
        )

    assert error.value.code.value == "validation"
    assert "UserID" in error.value.message


def test_partial_multi_field_update_merges_saved_credentials_and_hints_secret(service):
    service.save_config(
        "kpl_native",
        public_config={},
        credentials={"user_id": "fake-user", "token": "fake-token"},
        expected_version=None,
        actor_id=1,
    )

    updated = service.save_config(
        "kpl_native",
        public_config={},
        credentials={"user_id": "fake-user-2"},
        expected_version=1,
        actor_id=1,
    )

    assert service.load_credentials("kpl_native") == {"user_id": "fake-user-2", "token": "fake-token"}
    assert updated.key_hint == "...oken"


def test_save_config_rejects_credential_keys_not_declared_by_registry(service):
    with pytest.raises(DataHubError) as error:
        service.save_config(
            "kpl_native",
            public_config={},
            credentials={"unexpected": "fake-value"},
            expected_version=None,
            actor_id=1,
        )

    assert error.value.code.value == "validation"


def test_fixed_route_rejects_disabled_provider_before_probe(service):
    with pytest.raises(DataHubError) as error:
        service.save_route(
            "market.indices",
            mode="fixed",
            providers=["akshare"],
            expected_version=None,
            actor_id=1,
        )
    assert error.value.code.value == "not_configured"


def test_enable_rejects_unavailable_provider(service):
    service.save_config("official", public_config={}, credentials={}, expected_version=None, actor_id=1)
    with pytest.raises(DataHubError) as error:
        service.set_enabled("official", True, expected_version=1, actor_id=1)
    assert error.value.code.value == "not_configured"


def test_existing_route_requires_if_match_version(service):
    service.save_config("tencent", public_config={}, credentials={}, expected_version=None, actor_id=1)
    service.save_route("market.indices", mode="auto", providers=["tencent"], expected_version=None, actor_id=1)
    with pytest.raises(DataHubConflict):
        service.save_route("market.indices", mode="auto", providers=["tencent"], expected_version=None, actor_id=1)


def test_fixed_route_accepts_exactly_one_provider(service):
    service.save_config("tencent", public_config={}, credentials={}, expected_version=None, actor_id=1)
    service.save_config("sina", public_config={}, credentials={}, expected_version=None, actor_id=1)
    for provider in ("tencent", "sina"):
        service.record_probe(ProbeRecord(provider=provider, capability="market.indices", status="ok"), actor_id=1)
    with pytest.raises(DataHubError) as error:
        service.save_route("market.indices", mode="fixed", providers=["tencent", "sina"], expected_version=None, actor_id=1)
    assert error.value.code.value == "validation"


def test_free_provider_probe_without_config_can_authorize_fixed_route(service):
    service.record_probe(ProbeRecord(provider="tencent", capability="market.indices", status="ok"), actor_id=1)
    assert service.probe_is_valid("tencent", "market.indices") is True
    route = service.save_route("market.indices", mode="fixed", providers=["tencent"], expected_version=None, actor_id=1)
    assert route.provider_order_json == ["tencent"]


def test_probe_with_temporary_credential_fingerprint_survives_same_save(service):
    import json

    token_payload = json.dumps({"token": "token-a"}, sort_keys=True, separators=(",", ":"))
    fingerprint = credential_fingerprint(token_payload)
    service.record_probe(ProbeRecord(provider="tushare", capability="kpl.limit_list", status="ok", fingerprint=fingerprint), actor_id=1)
    service.save_config("tushare", public_config={}, credentials={"token": "token-a"}, expected_version=None, actor_id=1)
    assert service.probe_is_valid("tushare", "kpl.limit_list") is True
    service.save_config("tushare", public_config={}, credentials={"token": "token-b"}, expected_version=1, actor_id=1)
    assert service.probe_is_valid("tushare", "kpl.limit_list") is False


def test_record_probe_persists_json_safe_sample(service):
    run = service.record_probe(
        ProbeRecord(
            provider="sina",
            capability="market.indices",
            status="ok",
            safe_sample={"data_at": datetime(2026, 8, 22, 7, 30, tzinfo=timezone.utc)},
        ),
        actor_id=1,
    )

    assert run.safe_sample_json == {"data_at": "2026-08-22T07:30:00+00:00"}
