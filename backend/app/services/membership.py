"""Membership tiers, quota matrix and usage metering (F-12).

Quota semantics per feature:
  0  = feature locked for this tier
  -1 = unlimited
  n  = n times per day
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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


def ensure_plans(db: Session, *, commit: bool = False) -> None:
    """Seed plans without committing the caller's transaction by default.

    Task submission deliberately owns one transaction spanning model locking,
    membership accounting, task creation and outbox insertion.  The old
    helper used to commit while lazily seeding plans, which could leak a
    partially-created task.  ``commit=True`` is retained only for the legacy
    read-only plan endpoint and other callers that explicitly own a standalone
    transaction.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        for seed in PLAN_SEEDS:
            statement = pg_insert(MembershipPlan).values(**seed).on_conflict_do_nothing(
                index_elements=[MembershipPlan.code]
            )
            db.execute(statement)
    elif dialect == "sqlite":
        for seed in PLAN_SEEDS:
            statement = sqlite_insert(MembershipPlan).values(**seed).on_conflict_do_nothing(
                index_elements=[MembershipPlan.code]
            )
            db.execute(statement)
    elif db.query(MembershipPlan).count() == 0:
        db.add_all(MembershipPlan(**seed) for seed in PLAN_SEEDS)
    db.flush()
    if commit:
        db.commit()


def get_plans(db: Session) -> list[MembershipPlan]:
    ensure_plans(db, commit=True)
    return db.query(MembershipPlan).filter(MembershipPlan.is_active.is_(True)) \
        .order_by(MembershipPlan.sort_order).all()


def get_plan(db: Session, code: str, *, commit: bool = False) -> MembershipPlan | None:
    ensure_plans(db, commit=commit)
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
    plan = get_plan(db, tier, commit=False)
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
    new_count = _record(db, user.id, feature, cost, today, limit=limit)
    if limit != -1 and new_count is None:
        row = db.query(UsageLog).filter(
            UsageLog.user_id == user.id,
            UsageLog.feature == feature,
            UsageLog.used_on == today,
        ).first()
        used = row.count if row else 0
        raise HTTPException(status_code=403, detail={
            "code": "quota_exceeded",
            "feature": feature,
            "tier": tier,
            "used": used,
            "limit": limit,
            "message": f"今日「{name}」配额已用尽（{limit}次/日），请升级会员",
        })


def _record(
    db: Session,
    user_id: int,
    feature: str,
    cost: int,
    today: date,
    *,
    limit: int | None = None,
) -> int | None:
    """Atomically increment one daily usage row in the caller transaction.

    PostgreSQL (and SQLite used by the fast unit suite) both support an
    ``ON CONFLICT DO UPDATE`` statement.  The finite-quota predicate is part
    of that statement, so two concurrent submissions cannot both pass a
    stale pre-check and over-consume the row.  ``None`` means the conditional
    update was rejected because the quota was exhausted.
    """
    values = {"user_id": user_id, "feature": feature, "used_on": today, "count": cost}
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = pg_insert(UsageLog).values(**values)
        update = {"count": UsageLog.count + cost}
        if limit is not None and limit != -1:
            update_where = UsageLog.count + cost <= limit
            statement = statement.on_conflict_do_update(
                index_elements=[UsageLog.user_id, UsageLog.feature, UsageLog.used_on],
                set_=update,
                where=update_where,
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[UsageLog.user_id, UsageLog.feature, UsageLog.used_on],
                set_=update,
            )
    elif dialect == "sqlite":
        statement = sqlite_insert(UsageLog).values(**values)
        update = {"count": UsageLog.count + cost}
        if limit is not None and limit != -1:
            statement = statement.on_conflict_do_update(
                index_elements=[UsageLog.user_id, UsageLog.feature, UsageLog.used_on],
                set_=update,
                where=UsageLog.count + cost <= limit,
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[UsageLog.user_id, UsageLog.feature, UsageLog.used_on],
                set_=update,
            )
    else:
        # Non-production fallback for third-party SQLAlchemy dialects.
        row = db.query(UsageLog).filter(
            UsageLog.user_id == user_id,
            UsageLog.feature == feature,
            UsageLog.used_on == today,
        ).with_for_update().first()
        if row is None:
            if limit is not None and limit != -1 and cost > limit:
                return None
            row = UsageLog(**values)
            db.add(row)
            db.flush()
            return row.count
        if limit is not None and limit != -1 and row.count + cost > limit:
            return None
        row.count += cost
        db.flush()
        return row.count

    result = db.execute(statement)
    db.flush()
    if result.rowcount == 0:
        return None
    row = db.query(UsageLog).filter(
        UsageLog.user_id == user_id,
        UsageLog.feature == feature,
        UsageLog.used_on == today,
    ).first()
    return row.count if row else None


def check_and_consume_legacy(db: Session, user: User, feature: str, cost: int = 1) -> None:
    """Explicit standalone compatibility helper that owns its commit."""
    try:
        check_and_consume(db, user, feature, cost)
        db.commit()
    except Exception:
        db.rollback()
        raise


def usage_summary(db: Session, user: User) -> dict:
    tier = effective_tier(user)
    plan = get_plan(db, tier, commit=False)
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
