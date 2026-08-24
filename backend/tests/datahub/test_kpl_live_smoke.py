"""Opt-in live checks for the declared native KPL actions.

The default test suite never reads or prints credentials.  A real request is
made only when both runtime variables are deliberately supplied by an
operator.
"""

import os

import pytest

from app.datahub.contracts import Capability
from app.datahub.providers.base import capability_probe_params
from app.datahub.providers.kpl_native import KplNativeProvider


@pytest.mark.asyncio
async def test_kpl_native_live_smoke_when_runtime_credentials_are_present():
    user_id = os.getenv("KPL_USER_ID")
    token = os.getenv("KPL_TOKEN")
    if not user_id or not token:
        pytest.skip("未配置 KPL_USER_ID 与 KPL_TOKEN，未完成真实账号验证")

    provider = KplNativeProvider(token=token, user_id=user_id)
    capabilities = (
        Capability.KPL_NATIVE_STOCK_TAGS,
        Capability.KPL_NATIVE_PLATE_RANKING,
        Capability.KPL_NATIVE_PLATE_CONSTITUENTS,
        Capability.KPL_NATIVE_STOCK_RANKING,
    )
    for capability in capabilities:
        params = capability_probe_params(capability)
        if os.getenv("KPL_TRADE_DATE"):
            params["trade_date"] = os.environ["KPL_TRADE_DATE"]
        result = await provider.fetch(capability, params)
        assert result.provider == "kpl_native"
        assert result.capability is capability
