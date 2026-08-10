"""Admin-only system configuration endpoints (F-sysconfig).

All endpoints require admin role. Covers:
- User management (list / toggle active / change tier / change role)
- LLM config (view / update runtime keys)
- Data source config (view / test akshare connectivity)
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
from app.models.llm_usage import LlmUsage
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
# LLM config
# ---------------------------------------------------------------------------

class UpdateLlmConfigRequest(BaseModel):
    llm_mock: bool | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    deepseek_api_key: str | None = None
    daily_token_limit: int | None = None


@router.get("/admin/llm-config")
async def get_llm_config(admin: User = Depends(get_admin_user)):
    """Return current LLM configuration (API key masked)."""
    key = settings.DEEPSEEK_API_KEY
    masked = key[:4] + "****" + key[-4:] if len(key) > 8 else ("****" if key else "")
    # Recent usage stats
    return success(data={
        "llm_mock": settings.LLM_MOCK,
        "llm_model": settings.LLM_MODEL,
        "llm_base_url": settings.LLM_BASE_URL,
        "deepseek_api_key_masked": masked,
        "daily_token_limit": settings.DAILY_TOKEN_LIMIT,
    })


@router.put("/admin/llm-config")
async def update_llm_config(
    req: UpdateLlmConfigRequest,
    admin: User = Depends(get_admin_user),
):
    """Update runtime LLM settings (applies to current process, not persisted to .env)."""
    if req.llm_mock is not None:
        settings.LLM_MOCK = req.llm_mock
    if req.llm_model is not None:
        settings.LLM_MODEL = req.llm_model
    if req.llm_base_url is not None:
        settings.LLM_BASE_URL = req.llm_base_url
    if req.deepseek_api_key is not None:
        settings.DEEPSEEK_API_KEY = req.deepseek_api_key
    if req.daily_token_limit is not None:
        settings.DAILY_TOKEN_LIMIT = req.daily_token_limit
    return success(message="大模型配置已更新（当前进程生效，重启后恢复 .env 值）")


@router.get("/admin/llm-usage")
async def llm_usage_stats(
    days: int = 7,
    admin: User = Depends(get_admin_user), db: Session = Depends(get_db),
):
    """LLM token usage and cost in recent N days."""
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (db.query(
        LlmUsage.module,
        func.sum(LlmUsage.prompt_tokens).label("prompt_tokens"),
        func.sum(LlmUsage.completion_tokens).label("completion_tokens"),
        func.sum(LlmUsage.cost_fen).label("cost_fen"),
        func.count(LlmUsage.id).label("calls"),
    ).filter(LlmUsage.created_at >= since)
     .group_by(LlmUsage.module).all())
    total_cost = sum(r.cost_fen or 0 for r in rows)
    return success(data={
        "days": days,
        "total_cost_yuan": total_cost / 100,
        "modules": [{
            "module": r.module,
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "cost_yuan": (r.cost_fen or 0) / 100,
            "calls": int(r.calls or 0),
        } for r in rows],
    })


# ---------------------------------------------------------------------------
# Data source config
# ---------------------------------------------------------------------------

@router.get("/admin/datasource-config")
async def get_datasource_config(admin: User = Depends(get_admin_user)):
    """Return current data source configuration."""
    return success(data={
        "primary_source": "akshare",
        "akshare_version": _get_akshare_version(),
        "redis_url": settings.REDIS_URL,
        "database_url_masked": _mask_db_url(settings.DATABASE_URL),
        "news_sources": ["财联社", "新浪财经", "同花顺", "雪球", "央视"],
        "us_market_source": "akshare / yfinance",
    })


@router.post("/admin/datasource-test")
async def test_datasource(admin: User = Depends(get_admin_user)):
    """Test akshare connectivity by fetching a simple quote."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        return success(data={
            "status": "ok",
            "rows": len(df),
            "sample": df.head(2).to_dict("records") if len(df) > 0 else [],
        }, message="数据源连接正常")
    except Exception as e:
        return success(data={"status": "error", "error": str(e)}, message="数据源连接失败")


def _get_akshare_version():
    try:
        import akshare
        return akshare.__version__
    except Exception:
        return "unknown"


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
