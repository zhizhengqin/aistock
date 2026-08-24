"""Typed contracts shared by providers, routers and business consumers.

The provider boundary deliberately uses small Pydantic models.  Provider
specific dictionaries/dataframes are translated before they cross this
module, so business code can reason about units and freshness without knowing
which upstream supplied the data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.datahub.canonical_ticker import normalise_ticker


class Capability(str, Enum):
    MARKET_INDICES = "market.indices"
    MARKET_BOARD_QUOTES = "market.board_quotes"
    MARKET_BOARD_CONSTITUENTS = "market.board_constituents"
    STOCK_SNAPSHOT = "stock.snapshot"
    STOCK_PROFILE = "stock.profile"
    STOCK_KLINE_DAILY = "stock.kline.daily"
    STOCK_FINANCIALS = "stock.financials"
    STOCK_FUND_FLOW = "stock.fund_flow"
    STOCK_NEWS = "stock.news"
    MARKET_FUND_FLOW_RANK = "market.fund_flow_rank"
    STOCK_SHAREHOLDERS = "stock.shareholders"
    SECTOR_REALTIME = "sector.realtime"
    SECTOR_KLINE = "sector.kline"
    SECTOR_FUND_FLOW = "sector.fund_flow"
    DRAGON_TIGER_LIST = "dragon_tiger.list"
    DRAGON_TIGER_SEATS = "dragon_tiger.seats"
    KPL_LIMIT_LIST = "kpl.limit_list"
    KPL_CONCEPTS = "kpl.concepts"
    KPL_CONCEPT_CONSTITUENTS = "kpl.concept_constituents"
    KPL_LIMIT_LADDER = "kpl.limit_ladder"
    KPL_STRONG_SECTORS = "kpl.strong_sectors"
    MARKET_AUCTION_OPEN = "market.auction_open"
    KPL_NATIVE_STOCK_TAGS = "kpl_native.stock_tags"
    KPL_NATIVE_PLATE_RANKING = "kpl_native.plate_ranking"
    KPL_NATIVE_PLATE_CONSTITUENTS = "kpl_native.plate_constituents"
    KPL_NATIVE_STOCK_RANKING = "kpl_native.stock_ranking"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"


class DataQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool = True
    rows: int = Field(default=0, ge=0)
    score: float = Field(default=1.0, ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DataErrorInfo(BaseModel):
    code: str
    message: str


class DataMeta(BaseModel):
    """Metadata view exposed as ``result.meta`` for generic consumers."""

    model_config = ConfigDict(extra="forbid")

    capability: Capability
    provider: str
    data_at: datetime | None = None
    fetched_at: datetime
    latency_ms: int = Field(default=0, ge=0)
    freshness: Freshness
    fallback_used: bool = False
    attempts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: DataErrorInfo | None = None
    quality: DataQuality
    contract_version: str
    request_id: str

    @property
    def as_of(self) -> datetime | None:
        return self.data_at


T = TypeVar("T")


class DataResult(BaseModel, Generic[T]):
    """A successful capability response with trustworthy source metadata."""

    model_config = ConfigDict(extra="forbid")

    data: T
    capability: Capability
    provider: str = Field(min_length=1)
    data_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: int = Field(default=0, ge=0)
    freshness: Freshness = Freshness.FRESH
    fallback_used: bool = False
    attempts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: DataErrorInfo | None = None
    quality: DataQuality = Field(default_factory=DataQuality)
    contract_version: str = "1.0"
    request_id: str = Field(default_factory=lambda: str(uuid4()))

    @property
    def as_of(self) -> datetime | None:
        return self.data_at

    @computed_field
    @property
    def meta(self) -> DataMeta:
        return DataMeta(
            capability=self.capability,
            provider=self.provider,
            data_at=self.data_at,
            fetched_at=self.fetched_at,
            latency_ms=self.latency_ms,
            freshness=self.freshness,
            fallback_used=self.fallback_used,
            attempts=self.attempts,
            warnings=self.warnings,
            error=self.error,
            quality=self.quality,
            contract_version=self.contract_version,
            request_id=self.request_id,
        )

    @model_validator(mode="after")
    def _validate_quality(self) -> "DataResult[T]":
        if not self.quality.valid:
            raise ValueError("无效数据不能构造成功结果")
        if self.freshness is Freshness.STALE and not self.data_at:
            raise ValueError("过期数据必须提供数据时间")
        return self


class MarketIndex(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=4, max_length=16)
    name: str = Field(min_length=1)
    price: float
    change_pct: float
    data_at: datetime | None = None


class BoardQuote(BaseModel):
    """Vendor-neutral full cross-section quote for one market board."""

    model_config = ConfigDict(extra="ignore")

    board_code: str = Field(min_length=1)
    board_name: str = Field(min_length=1)
    kind: Literal["industry", "theme"]
    change_pct: float | None = None
    turnover: float | None = None
    market_cap: float | None = None
    rise_count: int | None = None
    fall_count: int | None = None
    flat_count: int | None = None
    leader_code: str | None = None
    leader_name: str | None = None
    leader_change_pct: float | None = None
    data_at: datetime


class BoardConstituent(BaseModel):
    """Typed representative stock quote returned for a board."""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=4, max_length=16)
    name: str = Field(min_length=1)
    price: float | None = None
    change_pct: float | None = None
    turnover: float | None = None
    market_cap: float | None = None
    data_at: datetime


class StockSnapshot(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    name: str = ""
    price: float
    change_pct: float = 0
    pe_ttm: float | None = None
    pb: float | None = None
    market_cap: float | None = None
    float_market_cap: float | None = None
    pe_static: float | None = None
    industry: str = ""
    data_at: datetime | None = None


class StockProfile(BaseModel):
    """Low-frequency company identity and classification data."""

    code: str = Field(min_length=4, max_length=16)
    name: str = ""
    industry: str | None = None
    data_at: datetime | None = None


class KlineBar(BaseModel):
    date: date | datetime | str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0
    data_at: datetime | None = None


class FinancialSummary(BaseModel):
    code: str = ""
    report_date: date | str | None = None
    revenue: float = 0
    net_profit: float = 0
    roe: float = 0
    pe_ttm: float | None = None
    pb: float | None = None
    market_cap: float | None = None
    gross_margin: float | None = None
    debt_ratio: float | None = None
    data_at: datetime | None = None


class FundFlow(BaseModel):
    code: str = ""
    net_main_flow: float | None = None
    net_super_large: float | None = None
    net_large: float | None = None
    net_medium: float | None = None
    net_small: float | None = None
    daily_flows: list[dict[str, Any]] = Field(default_factory=list)
    data_at: datetime | None = None


class NewsItem(BaseModel):
    title: str = Field(min_length=1)
    content: str = ""
    date: datetime | date | str | None = None
    source: str = ""
    url: str | None = None


class FundFlowRankItem(BaseModel):
    code: str = Field(min_length=4)
    name: str = ""
    net_main_flow: float
    change_pct: float = 0
    net_main_pct: float = 0
    data_at: datetime | None = None


class ShareholderSummary(BaseModel):
    code: str = ""
    latest: int | None = None
    previous: int | None = None
    change_pct: float = 0
    history: list[int] = Field(default_factory=list)
    data_at: datetime | None = None


class SectorQuote(BaseModel):
    code: str = ""
    name: str = Field(min_length=1)
    change_pct: float = 0
    price: float = 0
    turnover: float = 0
    data_at: datetime | None = None


class SectorFlow(BaseModel):
    name: str = Field(min_length=1)
    change_pct: float = 0
    net_main_flow: float = 0
    net_main_pct: float = 0
    data_at: datetime | None = None


class DragonTigerItem(BaseModel):
    code: str = ""
    name: str = ""
    date: date | str | None = None
    reason: str = ""
    close: float = 0
    change_pct: float = 0
    buy_amount: float = 0
    sell_amount: float = 0
    net_amount: float = 0
    data_at: datetime | None = None


class DragonTigerSeat(BaseModel):
    name: str = Field(min_length=1)
    buy_amount: float = 0
    sell_amount: float = 0
    net_amount: float = 0
    appearances: int = 0
    last_date: date | str | None = None
    data_at: datetime | None = None


class KplLimitItem(BaseModel):
    ts_code: str = ""
    name: str = ""
    trade_date: date | str
    tag: str = "涨停"
    theme: str = ""
    status: str = ""
    lu_desc: str = ""
    pct_chg: float | None = None
    limit_order: float | None = None
    amount: float | None = None


class KplConcept(BaseModel):
    ts_code: str
    name: str
    trade_date: date | str
    z_t_num: int = 0
    hot: int = 0
    strength: float = 0
    pct_change: float = 0


class KplConceptConstituent(BaseModel):
    ts_code: str
    name: str = ""
    con_name: str = ""
    con_code: str
    trade_date: date | str
    desc: str = ""
    hot_num: int = 0


class KplLimitLadder(BaseModel):
    ts_code: str
    name: str = ""
    trade_date: date | str
    nums: int | str


class KplStrongSector(BaseModel):
    ts_code: str
    name: str
    trade_date: date | str
    days: int = 0
    up_stat: str = ""
    cons_nums: int = 0
    up_nums: int = 0
    pct_chg: float = 0
    rank: int | str = 0


class AuctionOpen(BaseModel):
    ts_code: str
    trade_date: date | str
    open: float
    high: float | None = None
    low: float | None = None
    close: float | None = None
    vol: float | None = None
    amount: float | None = None
    vwap: float | None = None


class MarketIndicesRequest(BaseModel):
    codes: list[str] = Field(default_factory=lambda: ["000001.SS", "000300.SS", "000688.SS", "399001.SZ", "399006.SZ"], min_length=1)

    @field_validator("codes")
    @classmethod
    def _normalise_codes(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            try:
                code = normalise_ticker(value)
            except Exception as exc:
                raise ValueError("股票代码格式无效") from exc
            if code not in result:
                result.append(code)
        return result


class StockRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)

    @field_validator("code")
    @classmethod
    def _normalise_code(cls, value: str) -> str:
        try:
            return normalise_ticker(value)
        except Exception as exc:
            raise ValueError("股票代码格式无效") from exc


class KlineRequest(StockRequest):
    days: int = Field(default=120, ge=1, le=2000)


__all__ = [name for name in globals() if not name.startswith("_")]
