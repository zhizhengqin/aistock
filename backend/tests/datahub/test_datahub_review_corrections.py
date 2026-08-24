"""Regression tests for the review corrections to the DataHub boundary."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.datahub.config_service import DataHubConfigService
from app.datahub.contracts import Capability, DataQuality, DataResult
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProbeResult, capability_probe_params
from app.datahub.providers.kpl_native import KplNativeProvider
from app.datahub.registry import get_provider, providers_for
from app.datahub.runtime import _decode_cache_value, _encode_cache_value


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _CaptureClient:
    def __init__(self, payload=None):
        self.payload = payload or {"data": [{"trade_date": "20260824"}]}
        self.calls = []

    def post(self, url, data):
        self.calls.append((url, dict(data)))
        return _Response(self.payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "params", "expected"),
    [
        (
            Capability.KPL_NATIVE_STOCK_TAGS,
            {"stock_code": "600000.SS", "trade_date": "20260824"},
            {
                "c": "PCArrangeData", "a": "GetHQPlate", "StockID": "600000",
                "Day": "20260824", "SelType": "1,2,3,8,9,5,6,7",
                "UserID": "fake-user", "Token": "fake-token",
            },
        ),
        (
            Capability.KPL_NATIVE_PLATE_RANKING,
            {"trade_date": "20260824"},
            {
                "c": "ZhiShuRanking", "a": "RealRankingInfo", "SelType": 2,
                "ZSType": 7, "Type": 1, "Order": 1, "Start": "", "End": "",
                "Index": 0, "st": 10, "UserID": "fake-user", "Token": "fake-token",
            },
        ),
        (
            Capability.KPL_NATIVE_PLATE_CONSTITUENTS,
            {"plate_id": 801314, "trade_date": "20260824"},
            {
                "c": "ZhiShuRanking", "a": "ZhiShuStockList_W8", "PlateID": 801314,
                "SelType": 3, "Type": 6, "Order": 1, "Start": "", "End": "",
                "Index": 0, "st": 10, "UserID": "fake-user", "Token": "fake-token",
            },
        ),
        (
            Capability.KPL_NATIVE_STOCK_RANKING,
            {"trade_date": "20260824"},
            {
                "c": "StockRanking", "a": "RealRankingInfo", "Date": "20260824",
                "RStart": "", "REnd": "", "Ratio": 2, "Type": 1, "Order": 1,
                "index": 0, "st": 50, "UserID": "fake-user", "Token": "fake-token",
            },
        ),
    ],
)
async def test_native_kpl_builds_the_documented_payload_for_each_capability(capability, params, expected):
    client = _CaptureClient(
        {"trend": {"value": 1}, "pankou": {"value": 1}, "stockplate": []}
        if capability is Capability.KPL_NATIVE_STOCK_TAGS
        else None
    )
    provider = KplNativeProvider(token="fake-token", user_id="fake-user", http_client=client)

    await provider.fetch(capability, params)

    assert client.calls[0][1] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "params"),
    [
        (Capability.KPL_NATIVE_STOCK_TAGS, {"trade_date": "20260824"}),
        (Capability.KPL_NATIVE_PLATE_CONSTITUENTS, {"trade_date": "20260824"}),
    ],
)
async def test_native_kpl_rejects_missing_protocol_identity_params(capability, params):
    provider = KplNativeProvider(token="fake-token", user_id="fake-user", http_client=_CaptureClient())

    with pytest.raises(DataHubError) as error:
        await provider.fetch(capability, params)

    assert error.value.code is DataHubErrorCode.VALIDATION
    assert "参数" in error.value.message


def test_native_kpl_probe_params_are_valid_for_capability_specific_protocols():
    assert capability_probe_params(Capability.KPL_NATIVE_STOCK_TAGS)["stock_code"] == "600000.SS"
    assert capability_probe_params(Capability.KPL_NATIVE_PLATE_CONSTITUENTS)["plate_id"] == 801314
    assert capability_probe_params(Capability.KPL_NATIVE_STOCK_RANKING)["trade_date"]


def test_stock_profile_probe_params_use_a_valid_stock_code():
    assert capability_probe_params(Capability.STOCK_PROFILE) == {"code": "600000.SS"}


@pytest.mark.asyncio
async def test_native_kpl_nonzero_business_error_is_safe_and_not_success():
    client = _CaptureClient({"errcode": "upstream credential=do-not-leak", "errmsg": "upstream credential=do-not-leak"})
    provider = KplNativeProvider(token="fake-token", user_id="fake-user", http_client=client)

    with pytest.raises(DataHubError) as error:
        await provider.fetch(Capability.KPL_NATIVE_STOCK_TAGS, {"stock_code": "600000.SS", "trade_date": "20260824"})

    assert error.value.code is DataHubErrorCode.INTERNAL
    assert "业务" in error.value.message
    assert "do-not-leak" not in error.value.message
    assert "fake-token" not in str(error.value.to_response())


@pytest.mark.asyncio
async def test_native_kpl_any_nonzero_errcode_including_200_is_not_success():
    client = _CaptureClient({"errcode": 200, "data": [{"trade_date": "20260824"}]})
    provider = KplNativeProvider(token="fake-token", user_id="fake-user", http_client=client)

    with pytest.raises(DataHubError) as error:
        await provider.fetch(Capability.KPL_NATIVE_STOCK_TAGS, {"stock_code": "600000.SS", "trade_date": "20260824"})

    assert error.value.code is DataHubErrorCode.INTERNAL
    assert "业务" in error.value.message


@pytest.mark.asyncio
async def test_native_kpl_stock_tags_rejects_zero_error_shell_without_business_fields():
    client = _CaptureClient({"errcode": 0, "message": "ok"})
    provider = KplNativeProvider(token="fake-token", user_id="fake-user", http_client=client)

    with pytest.raises(DataHubError) as error:
        await provider.fetch(
            Capability.KPL_NATIVE_STOCK_TAGS,
            {"stock_code": "600000.SS", "trade_date": "20260824"},
        )

    assert error.value.code is DataHubErrorCode.SCHEMA_CHANGED
    assert "凭证" not in error.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        Capability.KPL_NATIVE_PLATE_RANKING,
        Capability.KPL_NATIVE_PLATE_CONSTITUENTS,
        Capability.KPL_NATIVE_STOCK_RANKING,
    ],
)
async def test_native_kpl_list_capabilities_reject_zero_error_metadata_shell(capability):
    client = _CaptureClient({"errcode": 0, "message": "ok"})
    provider = KplNativeProvider(token="fake-token", user_id="fake-user", http_client=client)
    params = {"trade_date": "20260824"}
    if capability is Capability.KPL_NATIVE_PLATE_CONSTITUENTS:
        params["plate_id"] = 801314

    with pytest.raises(DataHubError) as error:
        await provider.fetch(capability, params)

    assert error.value.code is DataHubErrorCode.EMPTY_INVALID
    assert "凭证" not in error.value.message


def test_akshare_does_not_claim_native_kpl_capabilities():
    akshare = get_provider("akshare")
    native = {
        Capability.KPL_NATIVE_STOCK_TAGS,
        Capability.KPL_NATIVE_PLATE_RANKING,
        Capability.KPL_NATIVE_PLATE_CONSTITUENTS,
        Capability.KPL_NATIVE_STOCK_RANKING,
    }
    assert native.isdisjoint(akshare.capabilities)
    assert native <= set(get_provider("kpl_native").capabilities)


@pytest.mark.parametrize(
    "capability",
    [
        Capability.KPL_NATIVE_STOCK_TAGS,
        Capability.KPL_NATIVE_PLATE_RANKING,
        Capability.KPL_NATIVE_PLATE_CONSTITUENTS,
        Capability.KPL_NATIVE_STOCK_RANKING,
    ],
)
def test_native_kpl_cache_round_trip_preserves_dict_result(capability):
    result = DataResult(
        data={"rows": [{"trade_date": "20260824", "value": 1}]},
        capability=capability,
        provider="kpl_native",
        data_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        quality=DataQuality(valid=True, rows=1),
    )

    decoded = _decode_cache_value(_encode_cache_value(result))

    assert isinstance(decoded, DataResult)
    assert decoded.capability is capability
    assert decoded.data == result.data


def test_native_kpl_registry_order_is_not_special_cased_for_auction():
    assert [item.name for item in providers_for(Capability.MARKET_AUCTION_OPEN)] == ["tushare"]


def test_probe_result_carries_safe_provider_message():
    result = ProbeResult(status="error", error_code="authentication_failed", message="开盘啦未登录或凭证无效")
    assert result.message == "开盘啦未登录或凭证无效"
