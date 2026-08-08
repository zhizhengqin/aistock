from datetime import datetime, timedelta, timezone
from tests.conftest import client, auth_client, seed_user, fake_redis, test_db


def _make_user(db, username, tier="free", expire=None, role="user"):
    from app.core.security import hash_password
    from app.models.user import User
    u = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("Passw0rd!"),
        role=role,
        tier=tier,
        tier_expire_at=expire,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth(client, user_id):
    from app.core.security import create_access_token
    client.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})
    return client


# ---------------------------------------------------------------------------
# Plans & matrix (F-12-01)
# ---------------------------------------------------------------------------

def test_plans_matrix(client):
    resp = client.get("/api/membership/plans")
    assert resp.status_code == 200
    plans = resp.json()["data"]["plans"]
    assert [p["code"] for p in plans] == ["free", "D", "C", "B", "A"]
    by_code = {p["code"]: p for p in plans}
    assert by_code["free"]["quotas"]["stock_analysis"] == 1
    assert by_code["D"]["quotas"]["stock_analysis"] == 5
    assert by_code["C"]["quotas"]["stock_analysis"] == 8
    assert by_code["B"]["quotas"]["stock_analysis"] == 20
    assert by_code["A"]["quotas"]["stock_analysis"] == -1
    # feature switches
    assert by_code["free"]["quotas"]["sector"] == 0
    assert by_code["D"]["quotas"]["sector"] == 0
    assert by_code["C"]["quotas"]["sector"] == -1
    assert by_code["C"]["quotas"]["dragon_tiger"] == -1
    assert by_code["C"]["quotas"]["holdings"] == 0
    assert by_code["B"]["quotas"]["holdings"] == -1
    assert by_code["B"]["quotas"]["ai_watch"] == -1
    assert by_code["B"]["quotas"]["monitor"] == -1
    assert by_code["B"]["quotas"]["risk_alert"] == -1
    assert by_code["B"]["quotas"]["stock_pick"] == 0
    assert by_code["A"]["quotas"]["stock_pick"] == -1
    # pricing
    assert by_code["D"]["price_monthly_cents"] == 8800
    assert by_code["C"]["price_monthly_cents"] == 12800
    assert by_code["B"]["price_yearly_cents"] == 4000000
    assert by_code["A"]["price_yearly_cents"] == 8888800


def test_me_free_user(client, seed_user):
    _auth(client, seed_user["id"])
    resp = client.get("/api/membership/me")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tier"] == "free"
    assert data["is_trial"] is False


# ---------------------------------------------------------------------------
# Trial on register (3 days, then downgrade)
# ---------------------------------------------------------------------------

def test_register_grants_trial(client, fake_redis):
    fake_redis.setex("verify_code:trial@example.com", 300, "123456")
    resp = client.post("/api/auth/register", json={
        "username": "trialuser",
        "email": "trial@example.com",
        "password": "Passw0rd!",
        "code": "123456",
    })
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    me = client.get("/api/membership/me").json()["data"]
    assert me["tier"] == "C"
    assert me["is_trial"] is True
    assert me["days_left"] in (2, 3)


def test_effective_tier_expired(test_db):
    from app.services.membership import effective_tier
    engine, TestingSession = test_db
    db = TestingSession()
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    u = _make_user(db, "expired1", tier="B", expire=expired)
    assert effective_tier(u) == "free"
    future = datetime.now(timezone.utc) + timedelta(days=5)
    u2 = _make_user(db, "active1", tier="B", expire=future)
    assert effective_tier(u2) == "B"
    u3 = _make_user(db, "freeone", tier="free")
    assert effective_tier(u3) == "free"
    db.close()


def test_expire_memberships_task(test_db):
    from app.services.membership import expire_memberships
    engine, TestingSession = test_db
    db = TestingSession()
    expired = datetime.now(timezone.utc) - timedelta(hours=2)
    u = _make_user(db, "willdown", tier="C", expire=expired)
    n = expire_memberships(db)
    assert n == 1
    db.refresh(u)
    assert u.tier == "free"
    assert u.tier_expire_at is None
    db.close()


# ---------------------------------------------------------------------------
# Quota gating (F-12-02)
# ---------------------------------------------------------------------------

def test_free_stock_analysis_daily_limit(client, seed_user):
    _auth(client, seed_user["id"])
    r1 = client.post("/api/stocks/analyze", json={"stock_codes": ["600519"]})
    assert r1.status_code == 200
    r2 = client.post("/api/stocks/analyze", json={"stock_codes": ["000001"]})
    assert r2.status_code == 403
    detail = r2.json()["detail"]
    assert detail["code"] == "quota_exceeded"
    assert detail["feature"] == "stock_analysis"


def test_free_sector_locked(client, seed_user):
    _auth(client, seed_user["id"])
    resp = client.post("/api/stocks/sectors/analyze")
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "feature_locked"
    assert detail["feature"] == "sector"


def test_tier_c_sector_allowed(client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    u = _make_user(db, "tierc", tier="C", expire=datetime.now(timezone.utc) + timedelta(days=30))
    db.close()
    _auth(client, u.id)
    resp = client.post("/api/stocks/sectors/analyze")
    assert resp.status_code == 200
    assert resp.json()["data"]["task_id"]


def test_tier_c_stock_analysis_multi_code_consumes(client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    u = _make_user(db, "tierc2", tier="C", expire=datetime.now(timezone.utc) + timedelta(days=30))
    db.close()
    _auth(client, u.id)
    resp = client.post("/api/stocks/analyze", json={"stock_codes": ["600519", "000001"]})
    assert resp.status_code == 200
    usage = client.get("/api/membership/usage").json()["data"]
    assert usage["stock_analysis"]["used"] == 2
    assert usage["stock_analysis"]["limit"] == 8
    assert usage["stock_analysis"]["remaining"] == 6


def test_tier_b_stock_pick_locked(client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    u = _make_user(db, "tierb", tier="B", expire=datetime.now(timezone.utc) + timedelta(days=30))
    db.close()
    _auth(client, u.id)
    resp = client.post("/api/stocks/main-force/run")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "feature_locked"
    assert resp.json()["detail"]["feature"] == "stock_pick"


def test_tier_a_stock_pick_allowed(client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    u = _make_user(db, "tiera", tier="A", expire=datetime.now(timezone.utc) + timedelta(days=30))
    db.close()
    _auth(client, u.id)
    resp = client.post("/api/stocks/main-force/run")
    assert resp.status_code == 200
    assert resp.json()["data"]["task_id"]


def test_expired_paid_user_falls_back_to_free(client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    u = _make_user(db, "expiredc", tier="C", expire=expired)
    db.close()
    _auth(client, u.id)
    resp = client.post("/api/stocks/sectors/analyze")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "feature_locked"


# ---------------------------------------------------------------------------
# Usage stats (F-12-04)
# ---------------------------------------------------------------------------

def test_usage_endpoint_all_features(client, seed_user):
    _auth(client, seed_user["id"])
    resp = client.get("/api/membership/usage")
    assert resp.status_code == 200
    usage = resp.json()["data"]
    for feature in ["stock_analysis", "sector", "dragon_tiger", "holdings",
                    "ai_watch", "monitor", "risk_alert", "stock_pick"]:
        assert feature in usage
        assert "used" in usage[feature]
        assert "limit" in usage[feature]
        assert "remaining" in usage[feature]
    assert usage["stock_analysis"]["limit"] == 1
    assert usage["stock_analysis"]["used"] == 0
    assert usage["sector"]["limit"] == 0


# ---------------------------------------------------------------------------
# Admin grant (manual activation, payment placeholder)
# ---------------------------------------------------------------------------

def test_grant_requires_admin(client, seed_user):
    _auth(client, seed_user["id"])
    resp = client.post("/api/membership/admin/grant", json={
        "username": "tester", "tier": "A", "days": 30,
    })
    assert resp.status_code == 403


def test_grant_membership(client, test_db, seed_user):
    engine, TestingSession = test_db
    db = TestingSession()
    admin = _make_user(db, "admin1", role="admin")
    db.close()
    _auth(client, admin.id)
    resp = client.post("/api/membership/admin/grant", json={
        "username": "tester", "tier": "A", "days": 30,
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tier"] == "A"
    assert data["tier_expire_at"] is not None

    # grant free revokes
    resp2 = client.post("/api/membership/admin/grant", json={
        "username": "tester", "tier": "free",
    })
    assert resp2.status_code == 200
    assert resp2.json()["data"]["tier"] == "free"
    assert resp2.json()["data"]["tier_expire_at"] is None


def test_grant_invalid_tier(client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    admin = _make_user(db, "admin2", role="admin")
    admin_id = admin.id
    _make_user(db, "target1")
    db.close()
    _auth(client, admin_id)
    resp = client.post("/api/membership/admin/grant", json={
        "username": "target1", "tier": "Z", "days": 30,
    })
    assert resp.status_code == 400
