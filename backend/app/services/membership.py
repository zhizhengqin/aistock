"""Membership tiers, quota matrix and usage metering (F-12).

Quota semantics per feature:
  0  = feature locked for this tier
  -1 = unlimited
  n  = n times per day
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.membership_plan import MembershipPlan
from app.models.usage_log import UsageLog
from app.models.user import User

FEATURES = {
    "stock_analysis": "股票分析",
    "sector": "智策板块",
    "dragon_tiger": "智瞰龙虎榜",
    "holdings": "持仓分析",
    "ai_watch": "AI盯盘",
    "monitor": "实时监测",
    "risk_alert": "风险预警",
    "stock_pick": "主力选股",
}

# Matrix mirrors PRD section 8. Prices are configurable placeholders.
PLAN_SEEDS = [
    {"code": "free", "name": "免费会员", "sort_order": 0,
     "price_monthly_cents": 0, "price_yearly_cents": 0,
     "quotas": {"stock_analysis": 1, "sector": 0, "dragon_tiger": 0, "holdings": 0,
                "ai_watch": 0, "monitor": 0, "risk_alert": 0, "stock_pick": 0}},
    {"code": "D", "name": "D 档会员", "sort_order": 1,
     "price_monthly_cents": 8800, "price_yearly_cents": 88000,
     "quotas": {"stock_analysis": 5, "sector": 0, "dragon_tiger": 0, "holdings": 0,
                "ai_watch": 0, "monitor": 0, "risk_alert": 0, "stock_pick": 0}},
    {"code": "C", "name": "C 档会员", "sort_order": 2,
     "price_monthly_cents": 12800, "price_yearly_cents": 128000,
     "quotas": {"stock_analysis": 8, "sector": -1, "dragon_tiger": -1, "holdings": 0,
                "ai_watch": 0, "monitor": 0, "risk_alert": 0, "stock_pick": 0}},
    {"code": "B", "name": "B 档会员", "sort_order": 3,
     "price_monthly_cents": 0, "price_yearly_cents": 4000000,
     "quotas": {"stock_analysis": 20, "sector": -1, "dragon_tiger": -1, "holdings": -1,
                "ai_watch": -1, "monitor": -1, "risk_alert": -1, "stock_pick": 0}},
    {"code": "A", "name": "A 档会员", "sort_order": 4,
     "price_monthly_cents": 0, "price_yearly_cents": 8888800,
     "quotas": {"stock_analysis": -1, "sector": -1, "dragon_tiger": -1, "holdings": -1,
                "ai_watch": -1, "monitor": -1, "risk_alert": -1, "stock_pick": -1}},
]

TRIAL_TIER = "C"
TRIAL_DAYS = 3

TIER_RANK = {code: i for i, code in enumerate(["free", "D", "C", "B", "A"])}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    # SQLite drops tzinfo; treat naive values as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_plans(db: Session) -> None:
    """Seed the plan table if empty (tests / fresh deployments)."""
    if db.query(MembershipPlan).count() > 0:
        return
    for seed in PLAN_SEEDS:
        db.add(MembershipPlan(**seed))
    db.commit()


def get_plans(db: Session) -> list[MembershipPlan]:
    ensure_plans(db)
    return db.query(MembershipPlan).filter(MembershipPlan.is_active.is_(True)) \
        .order_by(MembershipPlan.sort_order).all()


def get_plan(db: Session, code: str) -> MembershipPlan | None:
    ensure_plans(db)
    return db.query(MembershipPlan).filter(MembershipPlan.code == code).first()


def effective_tier(user: User) -> str:
    """Paid tiers fall back to free once expired (lazy downgrade)."""
    if user.tier and user.tier != "free":
        if user.tier_expire_at is None:
            return user.tier
        if _as_aware(user.tier_expire_at) > _now():
            return user.tier
        return "free"
    return "free"


def trial_info(user: User) -> dict:
    expire = user.tier_expire_at
    days_left = None
    if expire:
        days_left = max(0, (_as_aware(expire) - _now()).days)
    return {
        "tier": effective_tier(user),
        "raw_tier": user.tier,
        "tier_expire_at": expire.isoformat() if expire else None,
        "is_trial": bool(user.tier == TRIAL_TIER and user.role == "user" and expire),
        "days_left": days_left,
    }


def check_and_consume(db: Session, user: User, feature: str, cost: int = 1) -> None:
    """Gate a feature. Raises 403 feature_locked / quota_exceeded, else records usage."""
    if feature not in FEATURES:
        raise ValueError(f"unknown feature: {feature}")
    tier = effective_tier(user)
    plan = get_plan(db, tier)
    limit = (plan.quotas or {}).get(feature, 0) if plan else 0
    name = FEATURES[feature]

    if limit == 0:
        raise HTTPException(status_code=403, detail={
            "code": "feature_locked",
            "feature": feature,
            "tier": tier,
            "message": f"当前会员等级不支持「{name}」，请升级会员",
        })

    today = date.today()
    if limit != -1:
        row = db.query(UsageLog).filter(
            UsageLog.user_id == user.id,
            UsageLog.feature == feature,
            UsageLog.used_on == today,
        ).first()
        used = row.count if row else 0
        if used + cost > limit:
            raise HTTPException(status_code=403, detail={
                "code": "quota_exceeded",
                "feature": feature,
                "tier": tier,
                "used": used,
                "limit": limit,
                "message": f"今日「{name}」配额已用尽（{limit}次/日），请升级会员",
            })

    _record(db, user.id, feature, cost, today)


def _record(db: Session, user_id: int, feature: str, cost: int, today: date) -> None:
    row = db.query(UsageLog).filter(
        UsageLog.user_id == user_id,
        UsageLog.feature == feature,
        UsageLog.used_on == today,
    ).first()
    if row:
        row.count += cost
    else:
        db.add(UsageLog(user_id=user_id, feature=feature, used_on=today, count=cost))
    db.commit()


def usage_summary(db: Session, user: User) -> dict:
    tier = effective_tier(user)
    plan = get_plan(db, tier)
    quotas = (plan.quotas or {}) if plan else {}
    today = date.today()
    rows = db.query(UsageLog).filter(
        UsageLog.user_id == user.id,
        UsageLog.used_on == today,
    ).all()
    used_map = {r.feature: r.count for r in rows}
    summary = {}
    for feature, name in FEATURES.items():
        limit = quotas.get(feature, 0)
        used = used_map.get(feature, 0)
        remaining = None if limit == -1 else max(0, limit - used)
        summary[feature] = {
            "name": name,
            "used": used,
            "limit": limit,
            "remaining": remaining,
        }
    return summary


def expire_memberships(db: Session) -> int:
    """Persist lazy downgrades: expired paid tiers become free. Returns count."""
    now = _now()
    users = db.query(User).filter(
        User.tier != "free",
        User.tier_expire_at.isnot(None),
    ).all()
    n = 0
    for u in users:
        if _as_aware(u.tier_expire_at) <= now:
            u.tier = "free"
            u.tier_expire_at = None
            n += 1
    if n:
        db.commit()
    return n


def grant_membership(db: Session, user: User, tier: str, days: int | None) -> User:
    """Admin manual activation (payment placeholder for this milestone)."""
    if tier not in TIER_RANK:
        raise HTTPException(status_code=400, detail=f"无效的会员等级: {tier}")
    if days is not None and days <= 0:
        raise HTTPException(status_code=400, detail="天数必须为正整数")
    user.tier = tier
    user.tier_expire_at = None if tier == "free" else _now() + timedelta(days=days or 30)
    db.commit()
    db.refresh(user)
    return user
