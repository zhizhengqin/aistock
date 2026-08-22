"""Admin-only system configuration endpoints (F-sysconfig).

All endpoints require admin role. Covers:
- User management (list / toggle active / change tier / change role)
- Data source config (view / test DataHub connectivity)
- Agent config (view / update agent settings)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_admin_user
from app.core.response import success
from app.models.user import User
from app.models.membership_plan import MembershipPlan
from app.models.usage_log import UsageLog

router = APIRouter()


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

class UpdateUserRequest(BaseModel):
    is_active: bool | None = None
    tier: str | None = None
    role: str | None = None


@router.get("/admin/users")
async def list_users(
    page: int = 1, page_size: int = 20,
    admin: User = Depends(get_admin_user), db: Session = Depends(get_db),
):
    total = db.query(func.count(User.id)).scalar() or 0
    users = (db.query(User)
             .order_by(User.created_at.desc())
             .offset((page - 1) * page_size).limit(page_size).all())
    return success(data={
        "total": total, "page": page, "page_size": page_size,
        "items": [{
            "id": u.id, "username": u.username, "email": u.email,
            "role": u.role, "tier": u.tier,
            "tier_expire_at": u.tier_expire_at.isoformat() if u.tier_expire_at else None,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        } for u in users],
    })


@router.patch("/admin/users/{user_id}")
async def update_user(
    user_id: int, req: UpdateUserRequest,
    admin: User = Depends(get_admin_user), db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.tier is not None:
        user.tier = req.tier
    if req.role is not None:
        if req.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="角色只能是 admin 或 user")
        user.role = req.role
    db.commit()
    return success(message="用户信息已更新")


# ---------------------------------------------------------------------------
# Data source config
# ---------------------------------------------------------------------------

@router.get("/admin/datasource-config")
async def get_datasource_config(admin: User = Depends(get_admin_user)):
    """Return the registry-backed DataHub source summary."""
    from app.datahub.registry import PROVIDER_REGISTRY

    return success(data={
        "primary_source": "datahub",
        "providers": [
            {
                "provider": definition.name,
                "display_name": definition.display_name,
                "capabilities": [capability.value for capability in definition.capabilities],
                "enabled_by_default": definition.enabled_by_default,
                "available": definition.available,
            }
            for definition in PROVIDER_REGISTRY.values()
        ],
        "redis_url": settings.REDIS_URL,
        "database_url_masked": _mask_db_url(settings.DATABASE_URL),
        "news_sources": ["华尔街见闻", "FT中文网"],
        "us_market_source": "独立美股数据源",
    })


@router.post("/admin/datasource-test")
async def test_datasource(admin: User = Depends(get_admin_user)):
    """Test the default index route through the controlled DataHub probe."""
    try:
        from app.datahub.consumer import get_market_indices

        result = await get_market_indices()
        return success(data={
            "status": "ok",
            "rows": result.quality.rows,
            "sample": result.data[0].model_dump(mode="json") if isinstance(result.data, list) and result.data else None,
            "provider": result.provider,
            "error_code": None,
        }, message="数据源连接正常")
    except Exception:
        return success(data={"status": "error", "error": "数据源连接失败，请稍后重试"}, message="数据源连接失败")


def _mask_db_url(url: str) -> str:
    if "://" not in url:
        return "****"
    parts = url.split("://")
    proto = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if "@" in rest:
        creds, host = rest.split("@", 1)
        return f"{proto}://****@{host}"
    return f"{proto}://****"


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------

class UpdateAgentConfigRequest(BaseModel):
    analysis_max_analysts: int | None = None
    task_timeout: int | None = None
    max_concurrent_tasks: int | None = None


# Agent settings stored in settings with defaults
_agent_config = {
    "analysis_max_analysts": 5,
    "task_timeout": 300,
    "max_concurrent_tasks": 2,
}


@router.get("/admin/agent-config")
async def get_agent_config(admin: User = Depends(get_admin_user)):
    return success(data=_agent_config.copy())


@router.put("/admin/agent-config")
async def update_agent_config(
    req: UpdateAgentConfigRequest,
    admin: User = Depends(get_admin_user),
):
    if req.analysis_max_analysts is not None:
        _agent_config["analysis_max_analysts"] = req.analysis_max_analysts
    if req.task_timeout is not None:
        _agent_config["task_timeout"] = req.task_timeout
    if req.max_concurrent_tasks is not None:
        _agent_config["max_concurrent_tasks"] = req.max_concurrent_tasks
    return success(message="Agent 配置已更新")


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

@router.get("/admin/stats")
async def admin_dashboard_stats(admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    """System-wide stats for admin dashboard."""
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0
    admin_count = db.query(func.count(User.id)).filter(User.role == "admin").scalar() or 0
    plan_count = db.query(func.count(MembershipPlan.id)).filter(MembershipPlan.is_active == True).scalar() or 0
    total_usage = db.query(func.sum(UsageLog.count)).scalar() or 0
    return success(data={
        "total_users": total_users,
        "active_users": active_users,
        "admin_count": admin_count,
        "active_plans": plan_count,
        "total_usage_count": int(total_usage),
    })
