from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.datahub.contracts import (
    Capability,
    DataMeta,
    DataQuality,
    DataResult,
    Freshness,
    MarketIndex,
    MarketIndicesRequest,
)
from app.datahub.errors import DataHubError, DataHubErrorCode


def test_data_result_carries_source_freshness_and_quality_metadata():
    result = DataResult[MarketIndex](
        data=MarketIndex(code="000001", name="上证指数", price=3000, change_pct=0.2),
        capability=Capability.MARKET_INDICES,
        provider="tencent",
        data_at=datetime(2026, 8, 22, 7, 30, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 8, 22, 7, 30, 1, tzinfo=timezone.utc),
        latency_ms=42,
        freshness=Freshness.FRESH,
        fallback_used=False,
        attempts=["tencent"],
        quality=DataQuality(valid=True, rows=1),
        contract_version="1.0",
    )
    assert result.meta.provider == "tencent"
    assert result.meta.freshness is Freshness.FRESH
    assert result.meta.request_id
    assert result.meta.warnings == []


def test_data_result_rejects_invalid_quality_and_freshness_values():
    with pytest.raises(ValidationError):
        DataResult(
            data=[],
            capability="market.indices",
            provider="tencent",
            freshness="unknown",
            quality={"valid": False, "rows": 0},
        )


def test_market_indices_request_normalizes_and_validates_codes():
    request = MarketIndicesRequest(codes=["000001.SS", "399006"])
    assert request.codes == ["000001.SS", "399006.SZ"]

    with pytest.raises(ValidationError):
        MarketIndicesRequest(codes=[])


def test_datahub_error_exposes_safe_http_mapping_without_provider_details():
    error = DataHubError(
        DataHubErrorCode.AUTHENTICATION_FAILED,
        "供应商鉴权失败",
        request_id="req-1",
        provider_detail="token=secret-value",
    )
    assert error.status_code == 503
    assert error.to_response()["request_id"] == "req-1"
    assert "secret-value" not in str(error.to_response())
