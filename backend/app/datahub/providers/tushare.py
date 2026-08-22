"""Tushare Pro adapter for the six KPL/auction capabilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.datahub.contracts import (
    AuctionOpen,
    Capability,
    DataQuality,
    DataResult,
    KplConcept,
    KplConceptConstituent,
    KplLimitItem,
    KplLimitLadder,
    KplStrongSector,
)
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.validators import validate_payload


class TushareProvider(ProviderAdapter):
    name = "tushare"

    _METHODS = {
        Capability.KPL_LIMIT_LIST: "kpl_list",
        Capability.KPL_CONCEPTS: "kpl_concept",
        Capability.KPL_CONCEPT_CONSTITUENTS: "kpl_concept_cons",
        Capability.KPL_LIMIT_LADDER: "limit_step",
        Capability.KPL_STRONG_SECTORS: "limit_cpt_list",
        Capability.MARKET_AUCTION_OPEN: "stk_auction_o",
    }

    def __init__(self, *, token: str, pro_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.token = token
        self.pro = pro_client

    def update_credentials(self, credentials: dict[str, str]) -> None:
        token = str(credentials.get("token") or "")
        if token != self.token:
            self.token = token
            self.pro = None

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if not self.token:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "尚未配置 Tushare Token", provider=self.name)
        method_name = self._METHODS.get(capability)
        if method_name is None:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "Tushare 暂不支持该数据能力", provider=self.name)
        try:
            if self.pro is None:
                import tushare as ts  # type: ignore

                # ``set_token`` mutates a process-global singleton and can
                # cross-wire concurrent admins.  Bind each provider to its
                # own token-scoped client instead.
                self.pro = ts.pro_api(self.token)
            method = getattr(self.pro, method_name)
            frame = await self.limiter.run_sync(self.name, lambda: method(**_tushare_params(params)))
            rows = _records(frame)
            count = validate_payload(capability, rows)
            model = _MODEL_BY_CAPABILITY[capability]
            typed = [model.model_validate(row) for row in rows]
            data_at = _data_time(typed, params)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "Tushare 响应缺少可信交易日", provider=self.name)
            return DataResult(
                data=typed,
                capability=capability,
                provider=self.name,
                data_at=data_at,
                quality=DataQuality(valid=True, rows=count),
            )
        except DataHubError:
            raise
        except ValidationError:
            raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "数据源字段发生变化", provider=self.name) from None
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None


def _tushare_params(params: dict[str, Any]) -> dict[str, Any]:
    allowed = {"ts_code", "trade_date", "tag", "start_date", "end_date", "nums", "fields"}
    values = dict(params)
    if "stock_code" in values and "ts_code" not in values:
        values["ts_code"] = values.pop("stock_code")
    return {key: value for key, value in values.items() if key in allowed and value not in (None, "")}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "where") and hasattr(value, "to_dict"):
        try:
            return value.where(value.notna(), None).to_dict("records")
        except Exception:
            return value.to_dict("records")
    if isinstance(value, list):
        return [dict(row) for row in value]
    if isinstance(value, dict):
        return [dict(value)]
    return []


_MODEL_BY_CAPABILITY = {
    Capability.KPL_LIMIT_LIST: KplLimitItem,
    Capability.KPL_CONCEPTS: KplConcept,
    Capability.KPL_CONCEPT_CONSTITUENTS: KplConceptConstituent,
    Capability.KPL_LIMIT_LADDER: KplLimitLadder,
    Capability.KPL_STRONG_SECTORS: KplStrongSector,
    Capability.MARKET_AUCTION_OPEN: AuctionOpen,
}


def _data_time(rows: list[Any], params: dict[str, Any]) -> datetime | None:
    for row in rows:
        value = getattr(row, "trade_date", None)
        if value:
            try:
                return datetime.strptime(str(value)[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                try:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
    value = params.get("trade_date")
    if value:
        try:
            return datetime.strptime(str(value)[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


__all__ = ["TushareProvider"]
