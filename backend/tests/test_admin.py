"""Admin system config API tests — admin-only access + CRUD."""
from tests.conftest import client, auth_client, seed_user, fake_redis, test_db


def _make_admin(db, username="admin"):
    from app.core.security import hash_password
    from app.models.user import User
    u = User(
        username=username, email=f"{username}@example.com",
        password_hash=hash_password("Admin@2026"),
        role="admin", tier="A", is_active=True,
    )
    db.add(u); db.commit(); db.refresh(u)
    return u.id


def _auth_admin(client, user_id):
    from app.core.security import create_access_token
    client.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})
    return client


# --- User management ---

def test_list_users_as_admin(client, seed_user, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.get("/api/admin/users")
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] >= 2


def test_list_users_forbidden_for_normal_user(auth_client):
    resp = auth_client.get("/api/admin/users")
    assert resp.status_code == 403


def test_list_users_no_auth(client):
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401


def test_update_user_as_admin(client, seed_user, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.patch(f"/api/admin/users/{seed_user['id']}", json={"tier": "B"})
    assert resp.status_code == 200


def test_update_user_forbidden_for_normal(auth_client, seed_user):
    resp = auth_client.patch(f"/api/admin/users/{seed_user['id']}", json={"tier": "B"})
    assert resp.status_code == 403


def test_update_user_404(client, seed_user, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.patch("/api/admin/users/99999", json={"tier": "B"})
    assert resp.status_code == 404


def test_update_user_invalid_role(client, seed_user, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.patch(f"/api/admin/users/{seed_user['id']}", json={"role": "superadmin"})
    assert resp.status_code == 400


# --- LLM config ---

def test_get_llm_config_admin(client, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.get("/api/admin/llm-config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "llm_mock" in data
    assert "llm_model" in data
    assert "deepseek_api_key_masked" in data


def test_get_llm_config_forbidden(auth_client):
    resp = auth_client.get("/api/admin/llm-config")
    assert resp.status_code == 403


def test_update_llm_config_admin(client, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.put("/api/admin/llm-config", json={"llm_mock": False})
    assert resp.status_code == 200


# --- Datasource config ---

def test_get_datasource_config_admin(client, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.get("/api/admin/datasource-config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["primary_source"] == "akshare"


def test_get_datasource_forbidden(auth_client):
    resp = auth_client.get("/api/admin/datasource-config")
    assert resp.status_code == 403


# --- Agent config ---

def test_get_agent_config_admin(client, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.get("/api/admin/agent-config")
    assert resp.status_code == 200
    assert "analysis_max_analysts" in resp.json()["data"]


def test_update_agent_config_admin(client, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.put("/api/admin/agent-config", json={"max_concurrent_tasks": 5})
    assert resp.status_code == 200


def test_agent_config_forbidden(auth_client):
    resp = auth_client.get("/api/admin/agent-config")
    assert resp.status_code == 403


# --- Stats ---

def test_admin_stats(client, test_db):
    _, TS = test_db
    db = TS()
    aid = _make_admin(db)
    db.close()
    _auth_admin(client, aid)
    resp = client.get("/api/admin/stats")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "total_users" in data
    assert "active_users" in data
