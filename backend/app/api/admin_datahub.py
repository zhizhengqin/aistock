"""Admin APIs for DataHub source configuration and capability routing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_admin_user
from app.core.response import success
from app.datahub.config_service import DataHubConfigService, ProbeRecord
from app.datahub.credentials import credential_fingerprint
from app.datahub.contracts import Capability
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.akshare import AkshareProvider
from app.datahub.providers.eastmoney import EastmoneyProvider
from app.datahub.providers.kpl_native import KplNativeProvider
from app.datahub.providers.official import OfficialProvider
from app.datahub.providers.rss import RssProvider
from app.datahub.providers.sina import SinaProvider
from app.datahub.providers.tdx import TdxProvider
from app.datahub.providers.tushare import TushareProvider
from app.datahub.providers.tencent import TencentProvider
from app.datahub.platform import default_routes, invalidate_datahub_routes
from app.datahub.registry import PROVIDER_REGISTRY, get_provider, providers_for
from app.models.datahub import DataSourceConfig, DataSourceProbeRun, DataSourceRoute
from app.models.user import User


router = APIRouter()


def _service(db: Session) -> DataHubConfigService:
    configured = getattr(settings, "DATAHUB_CONFIG_ENCRYPTION_KEY", "")
    if not configured:
        if str(getattr(settings, "ENV", "")).lower() in {"prod", "production"}:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "生产环境必须配置 DataHub 凭据加密主密钥")
        if getattr(settings, "DEBUG", False):
            # Development/test processes may derive an ephemeral key from an
            # already configured local secret. Production never reaches this
            # branch and must provide DATAHUB_CONFIG_ENCRYPTION_KEY.
            configured = f"dev:{getattr(settings, 'JWT_SECRET', '')}"
        else:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "请配置 DataHub 凭据加密主密钥")
    key = hashlib.sha256(configured.encode("utf-8")).digest()
    return DataHubConfigService(db, encryption_key=key)


class DataSourceUpsert(BaseModel):
    provider: str
    public_config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    expected_version: int | None = None


class DataSourcePatch(BaseModel):
    public_config: dict[str, Any] | None = None
    credentials: dict[str, str] = Field(default_factory=dict)
    expected_version: int


class RouteUpsert(BaseModel):
    mode: str
    providers: list[str]
    expected_version: int | None = None
    contract_version: str = "1.0"


def _failure(exc: DataHubError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())


@router.get("/admin/data-sources")
async def list_data_sources(admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        return success(data={"items": [item.model_dump() for item in _service(db).list_configs()]})
    except DataHubError as exc:
        return _failure(exc)


@router.post("/admin/data-sources")
async def create_data_source(req: DataSourceUpsert, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        result = _service(db).save_config(
            req.provider,
            public_config=req.public_config,
            credentials=req.credentials,
            expected_version=req.expected_version,
            actor_id=admin.id,
        )
        invalidate_datahub_routes()
        return success(data=result.model_dump(), message="数据源配置已保存")
    except DataHubError as exc:
        return _failure(exc)


@router.patch("/admin/data-sources/{provider}")
async def patch_data_source(provider: str, req: DataSourcePatch, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        result = _service(db).save_config(
            provider,
            public_config=req.public_config,
            credentials=req.credentials,
            expected_version=req.expected_version,
            actor_id=admin.id,
        )
        invalidate_datahub_routes()
        return success(data=result.model_dump(), message="数据源配置已保存")
    except DataHubError as exc:
        return _failure(exc)


@router.post("/admin/data-sources/{provider}/enable")
async def enable_data_source(provider: str, expected_version: int = Query(...), admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        result = _service(db).set_enabled(provider, True, expected_version=expected_version, actor_id=admin.id)
        invalidate_datahub_routes()
        return success(data=result.model_dump(), message="数据源已启用")
    except DataHubError as exc:
        return _failure(exc)


@router.post("/admin/data-sources/{provider}/disable")
async def disable_data_source(provider: str, expected_version: int = Query(...), admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        result = _service(db).set_enabled(provider, False, expected_version=expected_version, actor_id=admin.id)
        invalidate_datahub_routes()
        return success(data=result.model_dump(), message="数据源已停用")
    except DataHubError as exc:
        return _failure(exc)


@router.post("/admin/data-sources/test")
async def test_data_source(req: DataSourceUpsert, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        service = _service(db)
        credentials = service.merge_credentials(req.provider, req.credentials)
        provider = _make_provider(req.provider, credentials)
        capability = Capability(req.public_config.get("capability") or get_provider(req.provider).capabilities[0])
        probe = await provider.probe(capability)
        fingerprint = None
        if credentials:
            values = {str(key): str(value) for key, value in credentials.items() if value}
            if values:
                fingerprint = credential_fingerprint(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        service.record_probe(
            ProbeRecord(provider=req.provider, capability=capability.value, status=probe.status, rows=probe.rows, latency_ms=probe.latency_ms, error_code=probe.error_code, safe_sample=probe.safe_sample, fingerprint=fingerprint),
            actor_id=admin.id,
        )
        if probe.status != "ok":
            return JSONResponse(status_code=503, content={"code": probe.error_code or "probe_failed", "message": probe.message or "数据源测试失败", "data": {"status": probe.status, "rows": probe.rows, "latency_ms": probe.latency_ms}})
        return success(data={"provider": req.provider, "capability": capability.value, "status": probe.status, "rows": probe.rows, "latency_ms": probe.latency_ms, "sample": probe.safe_sample}, message="数据源连接正常")
    except DataHubError as exc:
        return _failure(exc)


@router.post("/admin/data-sources/{provider}/test")
async def test_saved_data_source(provider: str, capability: str | None = Query(None), admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        row = db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider == provider))
        if row is None:
            return JSONResponse(status_code=503, content={"code": "not_configured", "message": "请先保存数据源配置", "data": None})
        service = _service(db)
        credentials = service.load_credentials(provider)
        public_config = dict(row.public_config_json or {})
        if capability:
            try:
                Capability(capability)
            except ValueError:
                raise DataHubError(DataHubErrorCode.VALIDATION, "未知数据能力") from None
            public_config["capability"] = capability
        return await test_data_source(DataSourceUpsert(provider=provider, public_config=public_config, credentials=credentials), admin, db)
    except DataHubError as exc:
        return _failure(exc)


@router.get("/admin/data-source-routes")
async def list_data_source_routes(admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    rows = {row.capability: row for row in db.scalars(select(DataSourceRoute)).all()}
    configs = {row.provider: row for row in db.scalars(select(DataSourceConfig)).all()}
    items = []
    for capability in Capability:
        row = rows.get(capability.value)
        items.append({
            "capability": capability.value,
            "mode": row.mode if row else "auto",
            # An absent DB row means "use the approved runtime default", not
            # every registry candidate (which may include disabled/unavailable
            # adapters kept only for explanation in the UI).
            "providers": row.provider_order_json if row else list(default_routes()[capability].providers),
            "contract_version": row.contract_version if row else "1.0",
            "version": row.version if row else 0,
            "provider_options": [
                {
                    "provider": item.name,
                    "display_name": item.display_name,
                    "available": item.available,
                    "unavailable_reason": item.unavailable_reason,
                    "enabled": bool((configs.get(item.name).enabled if configs.get(item.name) else item.enabled_by_default)),
                    "selectable": bool(item.available and (configs.get(item.name).enabled if configs.get(item.name) else item.enabled_by_default)),
                }
                for item in providers_for(capability)
            ],
        })
    return success(data={"items": items})


@router.put("/admin/data-source-routes/{capability}")
async def save_data_source_route(capability: str, req: RouteUpsert, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    try:
        row = _service(db).save_route(capability, mode=req.mode, providers=req.providers, expected_version=req.expected_version, actor_id=admin.id, contract_version=req.contract_version)
        invalidate_datahub_routes()
        return success(data={"capability": row.capability, "mode": row.mode, "providers": row.provider_order_json, "contract_version": row.contract_version, "version": row.version}, message="能力路由已保存")
    except DataHubError as exc:
        return _failure(exc)


@router.get("/admin/data-source-probes")
async def list_data_source_probes(page: int = 1, page_size: int = 20, provider: str | None = None, capability: str | None = None, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    query = select(DataSourceProbeRun).order_by(DataSourceProbeRun.created_at.desc())
    if provider:
        query = query.where(DataSourceProbeRun.provider == provider)
    if capability:
        query = query.where(DataSourceProbeRun.capability == capability)
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return success(data={"items": [{"id": row.id, "provider": row.provider, "capability": row.capability, "status": row.status, "rows": row.rows, "latency_ms": row.latency_ms, "error_code": row.error_code, "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows], "page": page, "page_size": page_size})


def _make_provider(provider: str, credentials: dict[str, str]):
    definition = get_provider(provider)
    if not definition.available:
        raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, definition.unavailable_reason or "数据源当前不可用")
    token = credentials.get("token", "")
    if provider == "tushare":
        return TushareProvider(token=token)
    if provider == "kpl_native":
        return KplNativeProvider(token=token, user_id=credentials.get("user_id", ""))
    adapters = {
        "akshare": AkshareProvider,
        "tencent": TencentProvider,
        "eastmoney": EastmoneyProvider,
        "sina": SinaProvider,
        "tdx": TdxProvider,
        "official": OfficialProvider,
        "rss": RssProvider,
    }
    try:
        return adapters[provider]()
    except KeyError:
        raise DataHubError(DataHubErrorCode.VALIDATION, "未知数据源") from None


__all__ = ["router"]
