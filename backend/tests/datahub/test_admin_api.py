from tests.conftest import client


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
