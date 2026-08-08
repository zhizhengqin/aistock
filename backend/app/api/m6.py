from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user, get_admin_user
from app.core.response import success
from app.models.user import User
from app.services import membership as svc

router = APIRouter()


@router.get("/membership/plans")
async def list_plans(db: Session = Depends(get_db)):
    plans = svc.get_plans(db)
    return success(data={
        "plans": [
            {
                "code": p.code,
                "name": p.name,
                "price_monthly_cents": p.price_monthly_cents,
                "price_yearly_cents": p.price_yearly_cents,
                "quotas": p.quotas,
                "sort_order": p.sort_order,
            }
            for p in plans
        ],
        "features": svc.FEATURES,
    })


@router.get("/membership/me")
async def my_membership(user: User = Depends(get_current_user)):
    return success(data=svc.trial_info(user))


@router.get("/membership/usage")
async def my_usage(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return success(data=svc.usage_summary(db, user))


class GrantRequest(BaseModel):
    username: str
    tier: str
    days: int | None = None


@router.post("/membership/admin/grant")
async def grant(req: GrantRequest, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.username == req.username).first()
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    target = svc.grant_membership(db, target, req.tier, req.days)
    return success(data={
        "username": target.username,
        "tier": target.tier,
        "tier_expire_at": target.tier_expire_at.isoformat() if target.tier_expire_at else None,
    }, message="会员开通成功")
