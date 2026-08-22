"""Pydantic request/response schemas for DataHub admin APIs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DataSourceConfigRequest(BaseModel):
    provider: str = Field(min_length=1)
    public_config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    expected_version: int | None = None


class DataSourcePatchRequest(BaseModel):
    public_config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    expected_version: int = Field(ge=1)


class DataSourceRouteRequest(BaseModel):
    mode: Literal["auto", "fixed"]
    providers: list[str] = Field(min_length=1)
    expected_version: int | None = None
    contract_version: str = "1.0"


class DataSourceProbeRequest(BaseModel):
    provider: str
    capability: str
    credentials: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "DataSourceConfigRequest",
    "DataSourcePatchRequest",
    "DataSourceProbeRequest",
    "DataSourceRouteRequest",
]
