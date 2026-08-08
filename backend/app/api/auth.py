from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token, decode_token
from app.core.deps import get_current_user
from app.core.response import success, error
from app.models.user import User
from app.schemas.auth import (
    SendVerificationCodeRequest,
    RegisterRequest,
    LoginRequest,
    RefreshRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UserResponse,
    AuthTokenResponse,
)
from app.services.verify_code import gen_and_send_code, verify_code, delete_code
from app.core.logger import logger

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_to_response(user: User) -> dict:
    return UserResponse.model_validate(user).model_dump()


def _issue_tokens(user: User) -> dict:
    return AuthTokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        user=UserResponse.model_validate(user),
    ).model_dump()


@router.post("/send-verification-code")
async def send_verification_code(req: SendVerificationCodeRequest):
    gen_and_send_code(req.email)
    return success(message="验证码已发送（开发模式打印到控制台）")


@router.post("/register")
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not verify_code(req.email, req.code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    if db.query(User).filter(User.email == req.email.lower()).first():
        raise HTTPException(status_code=409, detail="该邮箱已注册")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=409, detail="该用户名已存在")
    from app.services import membership as membership_svc
    user = User(
        username=req.username,
        email=req.email.lower(),
        password_hash=hash_password(req.password),
        tier=membership_svc.TRIAL_TIER,
        tier_expire_at=datetime.now(timezone.utc) + timedelta(days=membership_svc.TRIAL_DAYS),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    delete_code(req.email)
    return success(data=_issue_tokens(user), message="注册成功")


@router.post("/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .filter(
            (User.email == req.account.lower()) |
            (User.username == req.account)
        )
        .first()
    )
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")
    return success(data=_issue_tokens(user), message="登录成功")


@router.post("/refresh")
async def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = decode_token(req.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="令牌类型错误")
        user_id = int(payload["sub"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return success(data=_issue_tokens(user), message="刷新成功")


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return success(data=_user_to_response(user))


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    gen_and_send_code(req.email)
    return success(message="重置验证码已发送（开发模式打印到控制台）")


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    if not verify_code(req.email, req.code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    user = db.query(User).filter(User.email == req.email.lower()).first()
    if user is None:
        raise HTTPException(status_code=404, detail="该邮箱未注册")
    user.password_hash = hash_password(req.new_password)
    db.commit()
    delete_code(req.email)
    return success(message="密码重置成功")
