"""Administrator API for persisted multi-provider LLM configurations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_admin_user
from app.core.response import success
from app.models.user import User
from app.schemas.llm import (
    LlmActivateRequest,
    LlmModelActionRequest,
    LlmModelCreateRequest,
    LlmModelPatchRequest,
    LlmModelProbeRequest,
    LlmSettingsPatchRequest,
    LlmSettingsUnlockRequest,
)
from app.services.llm.config_service import LlmConfigService, LlmConfigServiceError
from app.services.llm.types import Provider


router = APIRouter()


def get_llm_config_service(db: Session = Depends(get_db)) -> LlmConfigService:
    return LlmConfigService(db)


Service = Annotated[LlmConfigService, Depends(get_llm_config_service)]
Admin = Annotated[User, Depends(get_admin_user)]


def _failure(exc: Exception) -> JSONResponse:
    if isinstance(exc, LlmConfigServiceError):
        code = exc.code
        message = getattr(exc, "user_message", str(exc))
        status_code = int(getattr(exc, "status_code", 409))
        field = getattr(exc, "field", None)
    else:
        code = getattr(exc, "code", "llm_error")
        message = getattr(exc, "user_message", "大模型服务暂时不可用")
        status_code = 503
        field = None
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": None, "field": field},
    )


def _provider_or_none(value: str | None) -> Provider | None:
    if value is None:
        return None
    try:
        return Provider(value)
    except ValueError:
        raise LlmConfigServiceError("llm_provider_invalid", "大模型供应商配置无效", status_code=422, field="provider")


@router.get("/admin/llm-models")
async def list_llm_models(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    provider: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    admin: Admin = None,
    service: Service = None,
):
    try:
        return success(data=service.list(page=page, page_size=page_size, provider=_provider_or_none(provider), lifecycle_status=lifecycle_status))
    except Exception as exc:
        return _failure(exc)


@router.post("/admin/llm-models/test")
async def test_unsaved_llm_model(payload: LlmModelProbeRequest, admin: Admin = None, service: Service = None):
    try:
        data = await service.test_unsaved(payload, admin_user_id=admin.id)
        return success(data=data, message="模型测试完成")
    except Exception as exc:
        return _failure(exc)


@router.post("/admin/llm-models", status_code=201)
async def create_llm_model(payload: LlmModelCreateRequest, admin: Admin = None, service: Service = None):
    try:
        return JSONResponse(status_code=201, content=success(data=service.create(payload, admin_user_id=admin.id), message="模型配置已保存"))
    except Exception as exc:
        return _failure(exc)


@router.patch("/admin/llm-models/{config_id}")
async def patch_llm_model(config_id: str, payload: LlmModelPatchRequest, admin: Admin = None, service: Service = None):
    try:
        data = service.patch(config_id, payload, admin_user_id=admin.id)
        status = 201 if data.get("created_new_version") else 200
        return JSONResponse(status_code=status, content=success(data=data, message="模型配置已更新"))
    except Exception as exc:
        return _failure(exc)


@router.post("/admin/llm-models/{config_id}/test")
async def test_saved_llm_model(config_id: str, admin: Admin = None, service: Service = None):
    try:
        return success(data=await service.test_saved(config_id, admin_user_id=admin.id), message="模型测试完成")
    except Exception as exc:
        return _failure(exc)


@router.post("/admin/llm-models/{config_id}/enable")
async def enable_llm_model(config_id: str, payload: LlmModelActionRequest, admin: Admin = None, service: Service = None):
    try:
        data = await service.enable(config_id, expected_version=payload.expected_version, test_run_id=payload.test_run_id, admin_user_id=admin.id)
        return success(data=data, message="模型已启用")
    except Exception as exc:
        return _failure(exc)


@router.post("/admin/llm-models/{config_id}/disable")
async def disable_llm_model(config_id: str, payload: LlmModelActionRequest, admin: Admin = None, service: Service = None):
    try:
        return success(data=service.disable(config_id, expected_version=payload.expected_version, admin_user_id=admin.id), message="模型已停用")
    except Exception as exc:
        return _failure(exc)


@router.post("/admin/llm-models/{config_id}/activate")
async def activate_llm_model(config_id: str, payload: LlmActivateRequest, admin: Admin = None, service: Service = None):
    try:
        data = await service.activate(config_id, expected_version=payload.expected_version, idempotency_key=payload.idempotency_key, admin_user_id=admin.id)
        return success(data=data, message="默认模型已切换")
    except Exception as exc:
        return _failure(exc)


@router.delete("/admin/llm-models/{config_id}", status_code=204)
async def delete_llm_model(config_id: str, admin: Admin = None, service: Service = None):
    try:
        service.delete(config_id, admin_user_id=admin.id)
        return Response(status_code=204)
    except Exception as exc:
        return _failure(exc)


@router.get("/admin/llm-settings")
async def get_llm_settings(admin: Admin = None, service: Service = None):
    try:
        return success(data=service.get_settings())
    except Exception as exc:
        return _failure(exc)


@router.patch("/admin/llm-settings")
async def patch_llm_settings(payload: LlmSettingsPatchRequest, admin: Admin = None, service: Service = None):
    try:
        return success(data=service.patch_settings(expected_version=payload.expected_version, daily_token_limit=payload.daily_token_limit, admin_user_id=admin.id), message="每日 Token 限额已更新")
    except Exception as exc:
        return _failure(exc)


@router.post("/admin/llm-settings/unlock")
async def unlock_llm_settings(payload: LlmSettingsUnlockRequest, admin: Admin = None, service: Service = None):
    try:
        return success(data=service.unlock_settings(expected_version=payload.expected_version, reason=payload.reason, admin_user_id=admin.id), message="额度保险丝已解锁")
    except Exception as exc:
        return _failure(exc)


@router.get("/admin/llm-usage")
async def get_llm_usage(
    days: int = Query(default=7, ge=1, le=90),
    provider: str | None = Query(default=None),
    model: str | None = Query(default=None),
    admin: Admin = None,
    service: Service = None,
):
    try:
        return success(data=service.usage(days=days, provider=_provider_or_none(provider), model=model))
    except Exception as exc:
        return _failure(exc)


__all__ = ["get_llm_config_service", "router"]
