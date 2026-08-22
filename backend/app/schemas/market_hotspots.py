"""Product-layer contracts for the homepage market hotspot center."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TrendStatus = Literal["new", "heating", "cooling", "steady", "insufficient_history"]
MarketKind = Literal["industry", "theme"]


class MarketDatasetMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capability: str
    provider: str
    data_at: datetime | None = None
    fetched_at: datetime
    freshness: Literal["fresh", "stale"] = "fresh"
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    trade_date: str | None = None
    source: str = "datahub"


class MarketHotspot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    board_code: str
    board_name: str
    kind: MarketKind
    change_pct: float | None = None
    turnover: float | None = None
    market_cap: float | None = None
    rise_count: int | None = None
    fall_count: int | None = None
    flat_count: int | None = None
    leader_code: str | None = None
    leader_name: str | None = None
    leader_change_pct: float | None = None
    hot_score: float = Field(ge=0, le=100)
    rank: int = Field(ge=1)
    trend_status: TrendStatus = "insufficient_history"
    streak_days: int = Field(default=0, ge=0)
    rank_change: int | None = None
    data_at: datetime | None = None
    trade_date: str | None = None


class RepresentativeStock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    price: float | None = None
    change_pct: float | None = None
    turnover: float | None = None
    market_cap: float | None = None
    rank: int = Field(default=1, ge=1)
    data_at: datetime | None = None
    trade_date: str | None = None


class MarketCloudNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    kind: MarketKind
    value: float = Field(ge=0)
    change_pct: float | None = None
    market_cap: float | None = None
    data_at: datetime | None = None
    trade_date: str | None = None


class HotspotDataset(BaseModel):
    kind: MarketKind
    items: list[MarketHotspot] = Field(default_factory=list)
    meta: MarketDatasetMeta


class ConstituentsDataset(BaseModel):
    kind: MarketKind
    board_code: str
    board_name: str | None = None
    items: list[RepresentativeStock] = Field(default_factory=list)
    meta: MarketDatasetMeta


class MarketCloudDataset(BaseModel):
    kind: MarketKind
    nodes: list[MarketCloudNode] = Field(default_factory=list)
    meta: MarketDatasetMeta


__all__ = [
    "ConstituentsDataset",
    "HotspotDataset",
    "MarketCloudDataset",
    "MarketCloudNode",
    "MarketDatasetMeta",
    "MarketHotspot",
    "MarketKind",
    "RepresentativeStock",
    "TrendStatus",
]
