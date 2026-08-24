"""Explicit DataHub live-smoke command.

The command is opt-in and never runs during ordinary CI.  It reads encrypted
credentials from PostgreSQL, probes one capability, and prints only a safe
status summary.  Missing paid credentials are a successful ``not_configured``
outcome so operators can use the command on installations that only use free
market data.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.datahub.config_service import DataHubConfigService
from app.datahub.contracts import Capability
from app.datahub.providers.akshare import AkshareProvider
from app.datahub.providers.eastmoney import EastmoneyProvider
from app.datahub.providers.kpl_native import KplNativeProvider
from app.datahub.providers.sina import SinaProvider
from app.datahub.providers.tdx import TdxProvider
from app.datahub.providers.tushare import TushareProvider
from app.datahub.providers.tencent import TencentProvider
from app.datahub.providers.official import OfficialProvider
from app.datahub.providers.rss import RssProvider
from app.datahub.registry import PROVIDER_REGISTRY, get_provider


@dataclass(frozen=True, slots=True)
class DataHubSmokeResult:
    exit_code: int
    provider: str
    capability: str
    status: str
    message: str
    rows: int = 0
    latency_ms: int = 0
    error_code: str | None = None

    @property
    def rendered(self) -> str:
        payload = {
            "provider": self.provider,
            "capability": self.capability,
            "status": self.status,
            "message": self.message,
            "rows": self.rows,
            "latency_ms": self.latency_ms,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _encryption_key() -> bytes:
    configured = getattr(settings, "DATAHUB_CONFIG_ENCRYPTION_KEY", "")
    if not configured:
        if str(getattr(settings, "ENV", "")).lower() in {"prod", "production"}:
            raise ValueError("生产环境必须配置 DataHub 凭据加密主密钥")
        configured = f"dev:{getattr(settings, 'JWT_SECRET', '')}"
    return hashlib.sha256(configured.encode("utf-8")).digest()


def _provider(provider: str, credentials: dict[str, str]):
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
        raise ValueError("未知数据源") from None


async def live_smoke_async(
    *,
    provider: str,
    capability: str,
    session_factory: Callable[[], Session] = SessionLocal,
    provider_factory: Callable[[str, dict[str, str]], Any] = _provider,
    config_service_factory: Callable[[Session], DataHubConfigService] | None = None,
) -> DataHubSmokeResult:
    try:
        definition = get_provider(provider)
        capability_value = Capability(capability)
    except Exception:
        return DataHubSmokeResult(2, provider, capability, "invalid", "数据源或数据能力无效")
    if capability_value not in definition.capabilities:
        return DataHubSmokeResult(2, provider, capability, "invalid", "数据源不支持该数据能力")

    session = session_factory()
    try:
        service = config_service_factory(session) if config_service_factory else DataHubConfigService(session, encryption_key=_encryption_key())
        credentials = service.load_credentials(provider)
        required_keys = [field.key for field in definition.credential_fields if field.required]
        if definition.auth_type != "none" and any(not credentials.get(key) for key in required_keys):
            return DataHubSmokeResult(0, provider, capability, "not_configured", "未配置凭证，跳过真实请求")
        adapter = provider_factory(provider, credentials)
        probe = await adapter.probe(capability_value)
    except Exception:
        # Do not expose database URLs, provider exceptions, or credential
        # material in the operator-facing command output.
        return DataHubSmokeResult(1, provider, capability, "error", "无法完成数据源测试，请检查数据库和网络")
    finally:
        session.close()

    if probe.status == "ok":
        return DataHubSmokeResult(0, provider, capability, "ok", "数据源能力测试通过", probe.rows, probe.latency_ms)
    if probe.error_code == "not_configured":
        return DataHubSmokeResult(0, provider, capability, "not_configured", "未配置凭证，跳过真实请求", probe.rows, probe.latency_ms, probe.error_code)
    return DataHubSmokeResult(1, provider, capability, "error", "数据源能力测试失败", probe.rows, probe.latency_ms, probe.error_code)


def run_live_smoke(**kwargs) -> DataHubSmokeResult:
    return asyncio.run(live_smoke_async(**kwargs))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="睿见投研 DataHub 真实数据源测试")
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDER_REGISTRY))
    parser.add_argument("--capability", required=True, choices=[item.value for item in Capability])
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    result = run_live_smoke(provider=args.provider, capability=args.capability)
    print(result.rendered)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["DataHubSmokeResult", "build_parser", "live_smoke_async", "main", "run_live_smoke"]
