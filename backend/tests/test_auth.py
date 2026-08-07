from tests.conftest import client, auth_client, seed_user, fake_redis, test_db


def test_register_success(client, fake_redis):
    fake_redis.setex("verify_code:test@example.com", 300, "123456")
    resp = client.post("/api/auth/register", json={
        "username": "newuser",
        "email": "test@example.com",
        "password": "Passw0rd!",
        "code": "123456",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["access_token"]
    assert body["data"]["refresh_token"]
    assert body["data"]["user"]["username"] == "newuser"
    assert body["message"] == "注册成功"


def test_register_wrong_code(client, fake_redis):
    fake_redis.setex("verify_code:test@example.com", 300, "123456")
    resp = client.post("/api/auth/register", json={
        "username": "newuser",
        "email": "test@example.com",
        "password": "Passw0rd!",
        "code": "999999",
    })
    assert resp.status_code == 400
    assert "验证码错误" in resp.json()["detail"]


def test_register_expired_code(client, fake_redis):
    resp = client.post("/api/auth/register", json={
        "username": "newuser",
        "email": "noop@example.com",
        "password": "Passw0rd!",
        "code": "123456",
    })
    assert resp.status_code == 400


def test_register_duplicate_email(client, test_db, fake_redis):
    from app.core.security import hash_password
    from app.models.user import User
    engine, TestingSession = test_db
    db = TestingSession()
    db.add(User(username="old", email="dup@example.com", password_hash=hash_password("X")))
    db.commit()
    db.close()
    fake_redis.setex("verify_code:dup@example.com", 300, "123456")
    resp = client.post("/api/auth/register", json={
        "username": "newuser",
        "email": "dup@example.com",
        "password": "Passw0rd!",
        "code": "123456",
    })
    assert resp.status_code == 409
    assert "邮箱" in resp.json()["detail"]


def test_register_duplicate_username(client, seed_user, fake_redis):
    fake_redis.setex("verify_code:other@example.com", 300, "123456")
    resp = client.post("/api/auth/register", json={
        "username": "tester",
        "email": "other@example.com",
        "password": "Passw0rd!",
        "code": "123456",
    })
    assert resp.status_code == 409
    assert "用户名" in resp.json()["detail"]


def test_login_by_email(client, seed_user):
    resp = client.post("/api/auth/login", json={
        "account": "tester@example.com",
        "password": "Passw0rd!",
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["user"]["username"] == "tester"


def test_login_by_username(client, seed_user):
    resp = client.post("/api/auth/login", json={
        "account": "tester",
        "password": "Passw0rd!",
    })
    assert resp.status_code == 200


def test_login_wrong_password(client, seed_user):
    resp = client.post("/api/auth/login", json={
        "account": "tester@example.com",
        "password": "nope",
    })
    assert resp.status_code == 401
    assert "密码" in resp.json()["detail"]


def test_login_nonexistent(client, seed_user):
    resp = client.post("/api/auth/login", json={
        "account": "ghost@example.com",
        "password": "whatever",
    })
    assert resp.status_code == 401


def test_refresh_success(client, seed_user):
    from app.core.security import create_refresh_token
    rt = create_refresh_token(seed_user["id"])
    resp = client.post("/api/auth/refresh", json={"refresh_token": rt})
    assert resp.status_code == 200
    assert resp.json()["data"]["access_token"]


def test_refresh_invalid(client, seed_user):
    resp = client.post("/api/auth/refresh", json={"refresh_token": "garbage"})
    assert resp.status_code == 401


def test_me_with_token(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "tester@example.com"


def test_me_without_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_forgot_password_sends_code(client, fake_redis):
    resp = client.post("/api/auth/forgot-password", json={"email": "any@example.com"})
    assert resp.status_code == 200
    code = fake_redis.get("verify_code:any@example.com")
    assert code is not None and len(code) == 6


def test_reset_password_success(client, seed_user, fake_redis):
    fake_redis.setex("verify_code:tester@example.com", 300, "654321")
    resp = client.post("/api/auth/reset-password", json={
        "email": "tester@example.com",
        "code": "654321",
        "new_password": "NewPass1!",
    })
    assert resp.status_code == 200
    resp2 = client.post("/api/auth/login", json={
        "account": "tester@example.com",
        "password": "NewPass1!",
    })
    assert resp2.status_code == 200


def test_reset_wrong_code(client, seed_user, fake_redis):
    resp = client.post("/api/auth/reset-password", json={
        "email": "tester@example.com",
        "code": "000000",
        "new_password": "NewPass1!",
    })
    assert resp.status_code == 400
