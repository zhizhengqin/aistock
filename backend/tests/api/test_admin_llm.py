"""HTTP contract tests for administrator model-center endpoints."""

from __future__ import annotations

import base64

# Ensure Task 2 model tables are part of the shared test metadata.
from app.models import llm_config as _llm_config_models  # noqa: F401
from app.models import llm_execution as _llm_execution_models  # noqa: F401
from app.models import task_record as _task_record_models  # noqa: F401
from app.models import llm_usage as _llm_usage_models  # noqa: F401


def _make_admin(db, username="llm-admin"):
    from app.core.security import hash_password
    from app.models.user import User

    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("Admin@2026"),
        role="admin",
        tier="A",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def _auth(client, user_id):
    from app.core.security import create_access_token

    client.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})


def _candidate(**overrides):
    values = {
        "provider": "deepseek",
        "display_name": "DeepSeek 主模型",
        "model_name": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-super-secret",
        "max_output_tokens": 512,
    }
    values.update(overrides)
    return values


def _configure_keyring(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "test-current")
    monkeypatch.setattr(
        settings,
        "LLM_CONFIG_ENCRYPTION_KEYS",
        {"test-current": base64.b64encode(b"k" * 32).decode("ascii")},
    )


def test_llm_model_list_requires_admin(client):
    assert client.get("/api/admin/llm-models").status_code == 401


def test_llm_models_forbidden_for_normal_user(auth_client):
    assert auth_client.get("/api/admin/llm-models").status_code == 403


def test_unsaved_probe_and_create_redact_secret(client, test_db, monkeypatch):
    _, Session = test_db
    db = Session()
    admin_id = _make_admin(db)
    db.close()
    _auth(client, admin_id)

    from app.api import admin_llm

    class FakeService:
        async def test_unsaved(self, payload, admin_user_id):
            return {"status": "success", "key_hint": "sk-s...cret", "test_run_id": "run-1"}

        def create(self, payload, admin_user_id):
            return {
                "id": "config-1",
                "provider": "deepseek",
                "display_name": "DeepSeek 主模型",
                "model_name": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
                "key_hint": "sk-s...cret",
                "version": 1,
                "created_new_version": False,
            }

    client.app.dependency_overrides[admin_llm.get_llm_config_service] = lambda: FakeService()
    response = client.post("/api/admin/llm-models/test", json=_candidate())
    assert response.status_code == 200
    assert "sk-super-secret" not in response.text
    response = client.post("/api/admin/llm-models", json=_candidate())
    assert response.status_code == 201
    assert "encrypted_api_key" not in response.text
    client.app.dependency_overrides.clear()


def test_model_crud_pagination_filter_runtime_version_and_conflict(client, test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    admin_id = _make_admin(db, username="llm-admin-crud")
    db.close()
    _auth(client, admin_id)

    first = client.post("/api/admin/llm-models", json=_candidate())
    assert first.status_code == 201
    first_data = first.json()["data"]
    assert first_data["version"] == 1
    assert first_data["created_new_version"] is False
    assert first_data["key_hint"] == "sk-s...cret"
    assert "sk-super-secret" not in first.text

    second_payload = _candidate(
        provider="kimi",
        display_name="Kimi 备份",
        model_name="moonshot-v1-8k",
        base_url="https://api.moonshot.cn/v1",
        api_key="sk-kimi-secret",
    )
    second = client.post("/api/admin/llm-models", json=second_payload)
    assert second.status_code == 201

    filtered = client.get("/api/admin/llm-models?page=1&page_size=1&provider=deepseek")
    assert filtered.status_code == 200
    assert filtered.json()["data"]["total"] == 1
    assert filtered.json()["data"]["items"][0]["id"] == first_data["id"]
    capabilities = filtered.json()["data"]["items"][0]["capabilities"]
    assert capabilities["can_test"] is True
    assert capabilities["can_enable"] is True
    assert capabilities["can_disable"] is False
    assert capabilities["can_delete"] is True

    display_patch = client.patch(
        f"/api/admin/llm-models/{first_data['id']}",
        json={"expected_version": 1, "display_name": "DeepSeek 展示名", "input_price_micro_yuan_per_million": 10},
    )
    assert display_patch.status_code == 200
    assert display_patch.json()["data"]["version"] == 2
    assert display_patch.json()["data"]["created_new_version"] is False

    runtime_patch = client.patch(
        f"/api/admin/llm-models/{first_data['id']}",
        json={"expected_version": 2, "model_name": "deepseek-reasoner"},
    )
    assert runtime_patch.status_code == 201
    successor = runtime_patch.json()["data"]
    assert successor["created_new_version"] is True
    assert successor["supersedes_id"] == first_data["id"]

    stale = client.patch(
        f"/api/admin/llm-models/{first_data['id']}",
        json={"expected_version": 1, "display_name": "过期版本"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "llm_config_conflict"


def test_settings_usage_unlock_and_stale_version(client, test_db, monkeypatch):
    _, Session = test_db
    db = Session()
    admin_id = _make_admin(db, username="llm-admin-settings")
    db.close()
    _auth(client, admin_id)

    settings_response = client.get("/api/admin/llm-settings")
    assert settings_response.status_code == 200
    assert settings_response.json()["data"]["version"] == 1

    updated = client.patch(
        "/api/admin/llm-settings",
        json={"expected_version": 1, "daily_token_limit": 12345},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["daily_token_limit"] == 12345
    assert updated.json()["data"]["version"] == 2

    _, Session = test_db
    db = Session()
    from app.models.llm_config import LlmRuntimeSetting

    setting = db.query(LlmRuntimeSetting).filter_by(id=1).one()
    setting.budget_locked = True
    db.commit()
    db.close()

    unlocked = client.post(
        "/api/admin/llm-settings/unlock",
        json={"expected_version": 2, "reason": "已核对供应商账单并修复预留上界"},
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["data"]["budget_locked"] is False
    assert unlocked.json()["data"]["version"] == 3

    stale = client.post(
        "/api/admin/llm-settings/unlock",
        json={"expected_version": 2, "reason": "再次解锁"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] in {"llm_settings_conflict", "llm_budget_already_unlocked"}

    usage = client.get("/api/admin/llm-usage?days=7")
    assert usage.status_code == 200
    assert usage.json()["data"]["items"] == []


def test_state_machine_and_idempotency_routes_use_stable_conflicts(client, test_db):
    _, Session = test_db
    db = Session()
    admin_id = _make_admin(db, username="llm-admin-state")
    db.close()
    _auth(client, admin_id)

    from app.api import admin_llm

    class StateService:
        async def test_saved(self, config_id, admin_user_id):
            return {"status": "success", "test_run_id": "run-1", "runtime_fingerprint": "fingerprint"}

        async def enable(self, config_id, expected_version, test_run_id, admin_user_id):
            return {"id": config_id, "lifecycle_status": "active", "version": expected_version, "capabilities": {"can_disable": True}}

        def disable(self, config_id, expected_version, admin_user_id):
            return {"id": config_id, "lifecycle_status": "disabled", "version": expected_version + 1}

        async def activate(self, config_id, expected_version, idempotency_key, admin_user_id):
            return {"id": config_id, "default_model_config_id": config_id, "idempotency_key": idempotency_key}

        def delete(self, config_id, admin_user_id):
            return None

    client.app.dependency_overrides[admin_llm.get_llm_config_service] = lambda: StateService()
    config_id = "config-state"
    assert client.post(f"/api/admin/llm-models/{config_id}/test").status_code == 200
    assert client.post(f"/api/admin/llm-models/{config_id}/enable", json={"expected_version": 1, "test_run_id": "run-1"}).status_code == 200
    assert client.post(f"/api/admin/llm-models/{config_id}/disable", json={"expected_version": 1}).status_code == 200
    activated = client.post(f"/api/admin/llm-models/{config_id}/activate", json={"expected_version": 2, "idempotency_key": "idem-state"})
    assert activated.status_code == 200
    assert client.delete(f"/api/admin/llm-models/{config_id}").status_code == 204
    client.app.dependency_overrides.clear()


def test_old_llm_config_contract_is_removed(client, test_db):
    _, Session = test_db
    db = Session()
    admin_id = _make_admin(db, username="llm-admin-old-contract")
    db.close()
    _auth(client, admin_id)
    assert client.get("/api/admin/llm-config").status_code == 404
    assert client.put("/api/admin/llm-config", json={}).status_code == 404
