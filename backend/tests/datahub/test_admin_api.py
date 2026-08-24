from tests.conftest import client


def _kpl_probe_provider(result):
    class Provider:
        async def probe(self, capability, params=None):
            return result

    return Provider()


def _auth_admin(client, test_db):
    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    _, TS = test_db
    db = TS()
    user = User(username="dh-admin", email="dh-admin@example.com", password_hash=hash_password("Admin@2026"), role="admin", tier="A", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    client.headers.update({"Authorization": f"Bearer {create_access_token(user.id)}"})
    db.close()


def test_data_sources_api_exposes_registry_explanation_without_secrets(client, test_db):
    _auth_admin(client, test_db)
    response = client.get("/api/admin/data-sources")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    tushare = next(item for item in items if item["provider"] == "tushare")
    assert tushare["display_name"] == "Tushare Pro"
    assert "token" not in str(tushare.get("key_hint", "")).lower()
    assert "secret-token" not in str(tushare)


def test_data_sources_api_save_then_conflict_and_secret_redaction(client, test_db):
    _auth_admin(client, test_db)
    response = client.post(
        "/api/admin/data-sources",
        json={"provider": "tushare", "public_config": {}, "credentials": {"token": "secret-token"}},
    )
    assert response.status_code == 200
    assert "secret-token" not in response.text
    version = response.json()["data"]["version"]
    conflict = client.patch(
        "/api/admin/data-sources/tushare",
        json={"public_config": {}, "credentials": {}, "expected_version": version + 1},
    )
    assert conflict.status_code == 409


def test_route_api_rejects_unsupported_provider_for_capability(client, test_db):
    _auth_admin(client, test_db)
    response = client.put(
        "/api/admin/data-source-routes/market.indices",
        json={"mode": "fixed", "providers": ["tushare"], "expected_version": None},
    )
    assert response.status_code == 422


def test_temporary_probe_overlays_non_empty_credentials_on_saved_values(client, test_db, monkeypatch):
    _auth_admin(client, test_db)
    saved = client.post(
        "/api/admin/data-sources",
        json={
            "provider": "kpl_native",
            "public_config": {},
            "credentials": {"user_id": "fake-user", "token": "fake-token"},
        },
    )
    assert saved.status_code == 200

    captured = {}

    def make_provider(provider, credentials):
        captured.update(credentials)
        from app.datahub.providers.base import ProbeResult

        return _kpl_probe_provider(ProbeResult(status="ok", rows=1))

    monkeypatch.setattr("app.api.admin_datahub._make_provider", make_provider)
    response = client.post(
        "/api/admin/data-sources/test",
        json={
            "provider": "kpl_native",
            "public_config": {"capability": "kpl_native.stock_tags"},
            "credentials": {"token": "fake-token-2"},
        },
    )

    assert response.status_code == 200
    assert captured == {"user_id": "fake-user", "token": "fake-token-2"}


def test_probe_503_exposes_safe_chinese_authentication_reason(client, test_db, monkeypatch):
    _auth_admin(client, test_db)
    from app.datahub.providers.base import ProbeResult

    monkeypatch.setattr(
        "app.api.admin_datahub._make_provider",
        lambda provider, credentials: _kpl_probe_provider(
            ProbeResult(
                status="error",
                error_code="authentication_failed",
                message="开盘啦未登录或凭证无效",
            )
        ),
    )
    response = client.post(
        "/api/admin/data-sources/test",
        json={
            "provider": "kpl_native",
            "public_config": {"capability": "kpl_native.stock_tags"},
            "credentials": {"user_id": "fake-user", "token": "fake-token"},
        },
    )

    assert response.status_code == 503
    assert response.json()["message"] == "开盘啦未登录或凭证无效"
    assert "provider_detail" not in response.text
