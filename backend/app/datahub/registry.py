"""Code-owned provider registry.

Product-facing source explanations and capability ownership live here.  The
database stores only runtime settings, credentials and health observations.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.datahub.contracts import Capability
from app.datahub.errors import DataHubError, DataHubErrorCode


class ProviderDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    display_name: str
    description: str
    auth_type: str = "none"
    credential_fields: tuple["CredentialField", ...] = ()
    capabilities: tuple[Capability, ...]
    enabled_by_default: bool = False
    fee_type: str = "免费"
    update_frequency: str = "按请求"
    risk_note: str = ""
    available: bool = True
    unavailable_reason: str | None = None
    probe_examples: dict[str, str] = Field(default_factory=dict)


class CredentialField(BaseModel):
    """Structured, secret-safe metadata used to render provider credentials."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    label: str
    secret: bool = False
    required: bool = False
    help: str = ""


def _definition(**kwargs) -> ProviderDefinition:
    return ProviderDefinition(**kwargs)


PROVIDER_REGISTRY: dict[str, ProviderDefinition] = {
    "tencent": _definition(
        name="tencent",
        display_name="腾讯财经",
        description="提供批量指数和实时行情，适合免费行情首选。",
        capabilities=(Capability.MARKET_INDICES, Capability.STOCK_SNAPSHOT, Capability.STOCK_KLINE_DAILY),
        enabled_by_default=True,
        update_frequency="盘中实时",
        risk_note="公开接口可能受频率和网络波动影响。",
    ),
    "tdx": _definition(
        name="tdx",
        display_name="通达信",
        description="通过通达信协议提供日 K 与行情，必须先通过 TCP 可达性和字段对账。",
        capabilities=(Capability.STOCK_SNAPSHOT, Capability.STOCK_KLINE_DAILY),
        enabled_by_default=False,
        update_frequency="盘中实时/日 K",
        risk_note="依赖 TCP 7709，云主机网络不可达时自动禁用。",
    ),
    "eastmoney": _definition(
        name="eastmoney",
        display_name="东方财富",
        description="提供板块、资金流、股东户数与龙虎榜等特色数据。",
        capabilities=(
            Capability.MARKET_BOARD_QUOTES,
            Capability.MARKET_BOARD_CONSTITUENTS,
            Capability.STOCK_FUND_FLOW,
            Capability.STOCK_PROFILE,
            Capability.MARKET_FUND_FLOW_RANK,
            Capability.STOCK_SHAREHOLDERS,
            Capability.SECTOR_REALTIME,
            Capability.SECTOR_KLINE,
            Capability.STOCK_KLINE_DAILY,
            Capability.SECTOR_FUND_FLOW,
            Capability.DRAGON_TIGER_LIST,
            Capability.DRAGON_TIGER_SEATS,
            Capability.STOCK_NEWS,
        ),
        enabled_by_default=True,
        update_frequency="盘中实时/盘后",
        risk_note="同一公网 IP 需共享限流并可能触发风控。",
    ),
    "sina": _definition(
        name="sina",
        display_name="新浪财经",
        description="提供基础实时行情，作为公开备用来源。",
        capabilities=(
            Capability.MARKET_INDICES,
            Capability.MARKET_BOARD_QUOTES,
            Capability.MARKET_BOARD_CONSTITUENTS,
            Capability.STOCK_SNAPSHOT,
            Capability.STOCK_FINANCIALS,
            Capability.STOCK_FUND_FLOW,
            Capability.STOCK_KLINE_DAILY,
        ),
        enabled_by_default=True,
        update_frequency="盘中实时/盘后",
        risk_note="字段口径和更新时间以实际响应为准。",
    ),
    "rss": _definition(
        name="rss",
        display_name="RSS/财联社资讯",
        description="通过公开 RSS 聚合可验证的全球资讯；无凭证，失败可降级到东方财富搜索接口。",
        capabilities=(Capability.STOCK_NEWS,),
        enabled_by_default=True,
        update_frequency="按请求",
        risk_note="仅收录配置的公开 RSS 来源，来源不可用时保留告警并切换备用源。",
    ),
    "official": _definition(
        name="official",
        display_name="交易所官方",
        description="提供交易日、指数与公告等官方数据，适合质量校验和备用路由。",
        capabilities=(Capability.MARKET_INDICES,),
        enabled_by_default=False,
        available=False,
        unavailable_reason="交易所官方接口尚未接入可验证的生产端点",
        update_frequency="盘后",
        risk_note="当前仅保留协议占位，未作为业务默认来源。",
    ),
    "tushare": _definition(
        name="tushare",
        display_name="Tushare Pro",
        description="Token 付费数据服务，Token 来自 Tushare Pro 控制台；只提供 Tushare 的 kpl_* 数据集，不是开盘啦原生接口。",
        auth_type="token",
        credential_fields=(CredentialField(key="token", label="Token", secret=True, required=True, help="来自 Tushare Pro 控制台的 Token；只用于 Tushare 的 kpl_* 数据集，不是开盘啦原生 Token。"),),
        capabilities=(
            Capability.KPL_LIMIT_LIST,
            Capability.KPL_CONCEPTS,
            Capability.KPL_CONCEPT_CONSTITUENTS,
            Capability.KPL_LIMIT_LADDER,
            Capability.KPL_STRONG_SECTORS,
            Capability.MARKET_AUCTION_OPEN,
        ),
        enabled_by_default=False,
        fee_type="积分/付费",
        update_frequency="盘后",
        risk_note="不同能力需要 5000/8000 积分，按能力独立探测。",
        probe_examples={
            "kpl.limit_list": "kpl_list(trade_date=最近交易日)",
            "kpl.limit_ladder": "limit_step(trade_date=最近交易日)",
        },
    ),
    "kpl_native": _definition(
        name="kpl_native",
        display_name="开盘啦原生",
        description="实验性开盘啦原生协议适配，仅使用开盘啦合法账号 UserID + Token；不能填 Tushare Token，默认关闭。",
        auth_type="token",
        credential_fields=(
            CredentialField(key="user_id", label="UserID", secret=False, required=True, help="开盘啦合法账号的 UserID；不能使用 Tushare 账号信息。"),
            CredentialField(key="token", label="Token", secret=True, required=True, help="开盘啦合法账号 Token；不能填 Tushare Token，不会回显或写入日志。"),
        ),
        capabilities=(
            Capability.KPL_NATIVE_STOCK_TAGS,
            Capability.KPL_NATIVE_PLATE_RANKING,
            Capability.KPL_NATIVE_PLATE_CONSTITUENTS,
            Capability.KPL_NATIVE_STOCK_RANKING,
        ),
        enabled_by_default=False,
        available=True,
        fee_type="需合法 UserID + Token",
        update_frequency="盘中实时",
        risk_note="实验性非官方协议，需合法账号逐能力实测；不自动抓取、破解或内置示例凭证。",
    ),
    "akshare": _definition(
        name="akshare",
        display_name="AkShare 兼容",
        description="迁移期兼容适配器，保留现有口径但不作为默认主源。",
        capabilities=tuple(
            capability
            for capability in Capability
            if not capability.value.startswith("kpl.")
            and not capability.value.startswith("kpl_native.")
            and capability is not Capability.MARKET_AUCTION_OPEN
            and capability not in {Capability.MARKET_BOARD_QUOTES, Capability.MARKET_BOARD_CONSTITUENTS, Capability.STOCK_PROFILE}
        ),
        enabled_by_default=False,
        update_frequency="按请求",
        risk_note="上游函数变化时保持禁用并记录结构化错误。",
    ),
}


def get_provider(name: str) -> ProviderDefinition:
    try:
        return PROVIDER_REGISTRY[name]
    except KeyError:
        raise DataHubError(DataHubErrorCode.VALIDATION, "未知数据源") from None


def providers_for(capability: Capability | str) -> list[ProviderDefinition]:
    try:
        capability = Capability(capability)
    except ValueError:
        raise DataHubError(DataHubErrorCode.VALIDATION, "未知数据能力") from None
    return [item for item in PROVIDER_REGISTRY.values() if capability in item.capabilities]


__all__ = ["CredentialField", "PROVIDER_REGISTRY", "ProviderDefinition", "get_provider", "providers_for"]
