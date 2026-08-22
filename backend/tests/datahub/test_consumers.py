from datetime import datetime, timezone

import pytest

from app.datahub.contracts import Capability, DataQuality, DataResult, NewsItem
import app.datahub.consumer as consumer
from app.datahub.consumer import get_global_news


@pytest.mark.asyncio
async def test_global_news_consumer_returns_typed_data_result(monkeypatch):
    async def fake_fetch(capability, params):
        return DataResult(
            data=[NewsItem(title="市场快讯", content="摘要", source=params["source"], date="2026-08-21")],
            capability=capability,
            provider="eastmoney",
            data_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            quality=DataQuality(valid=True, rows=1),
        )

    monkeypatch.setattr(consumer, "_fetch", fake_fetch)
    result = await get_global_news("财联社", limit=1)
    assert isinstance(result, DataResult)
    assert result.capability is Capability.STOCK_NEWS
    assert isinstance(result.data[0], NewsItem)
