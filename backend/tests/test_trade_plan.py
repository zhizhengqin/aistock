"""F-08-04: AI 交易计划 + AI 决策记录 — API tests."""
from tests.conftest import client, auth_client, seed_user, fake_redis, test_db


def _seed_config(db, user_id, code="600519", name="贵州茅台"):
    from app.models.monitor_config import MonitorConfig
    c = MonitorConfig(
        user_id=user_id, stock_code=code, stock_name=name,
        entry_price=1680, target_price=1900, stop_price=1500,
        profit_pct=12, loss_pct=8, interval_min=10,
        ai_enabled=True, status="running",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.id


def _seed_plan(db, user_id, config_id=None, code="600519"):
    from app.models.ai_trade_plan import AiTradePlan
    p = AiTradePlan(
        user_id=user_id, config_id=config_id, stock_code=code,
        stock_name="贵州茅台", action="buy", suggested_price=1685,
        target_price=1900, stop_loss=1500, confidence=0.72,
        reasoning="均线多头排列，主力资金持续流入",
        plan_json={"indicators": {"rsi": 55}},
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p.id


def _seed_decision(db, user_id, config_id=None, code="600519"):
    from app.models.ai_decision_record import AiDecisionRecord
    d = AiDecisionRecord(
        user_id=user_id, config_id=config_id, stock_code=code,
        stock_name="贵州茅台", decision_type="monitor_check",
        summary="价格接近目标价，建议持有",
        detail_json={"current_price": 1870, "target": 1900},
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d.id


# --- Trade plans ---

def test_list_trade_plans_empty(auth_client):
    resp = auth_client.get("/api/stocks/ai-monitoring/trade-plans")
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


def test_list_trade_plans(auth_client, seed_user, test_db):
    _, TS = test_db
    db = TS()
    cid = _seed_config(db, seed_user["id"])
    pid = _seed_plan(db, seed_user["id"], cid)
    db.close()

    resp = auth_client.get("/api/stocks/ai-monitoring/trade-plans")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["stock_code"] == "600519"
    assert items[0]["action"] == "buy"
    assert items[0]["confidence"] == 0.72


def test_trade_plans_isolated_by_user(auth_client, seed_user, test_db):
    from app.core.security import hash_password
    from app.models.user import User
    _, TS = test_db
    db = TS()
    other = User(username="other", email="o@example.com",
                 password_hash=hash_password("Passw0rd!"), role="user",
                 tier="free", is_active=True)
    db.add(other); db.commit(); db.refresh(other)
    _seed_plan(db, other.id)
    db.close()

    resp = auth_client.get("/api/stocks/ai-monitoring/trade-plans")
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


def test_trade_plans_require_auth(client, seed_user, test_db):
    _, TS = test_db
    db = TS()
    pid = _seed_plan(db, seed_user["id"])
    db.close()
    resp = client.get("/api/stocks/ai-monitoring/trade-plans")
    assert resp.status_code in (401, 403)


# --- Decision records ---

def test_list_decisions_empty(auth_client):
    resp = auth_client.get("/api/stocks/ai-monitoring/decisions")
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


def test_list_decisions(auth_client, seed_user, test_db):
    _, TS = test_db
    db = TS()
    cid = _seed_config(db, seed_user["id"])
    did = _seed_decision(db, seed_user["id"], cid)
    db.close()

    resp = auth_client.get("/api/stocks/ai-monitoring/decisions")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["decision_type"] == "monitor_check"
    assert "价格接近目标价" in items[0]["summary"]


def test_decisions_isolated_by_user(auth_client, seed_user, test_db):
    from app.core.security import hash_password
    from app.models.user import User
    _, TS = test_db
    db = TS()
    other = User(username="other", email="o@example.com",
                 password_hash=hash_password("Passw0rd!"), role="user",
                 tier="free", is_active=True)
    db.add(other); db.commit(); db.refresh(other)
    _seed_decision(db, other.id)
    db.close()

    resp = auth_client.get("/api/stocks/ai-monitoring/decisions")
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []
