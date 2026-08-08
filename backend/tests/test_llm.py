import pytest
from unittest.mock import patch
from app.core import llm as llm_module
from app.core.config import settings


@pytest.mark.asyncio
async def test_mock_mode_returns_json():
    messages = [
        {"role": "system", "content": "{{ANALYST_KEY:technical}}\n你是一位技术面分析师"},
        {"role": "user", "content": "分析600519"},
    ]
    resp = await llm_module.chat(messages, user_id=0, module="test")
    assert resp.model == "mock"
    assert resp.prompt_tokens > 0
    import json
    data = json.loads(resp.content)
    assert "trend" in data


@pytest.mark.asyncio
async def test_mock_mode_chief_response():
    messages = [
        {"role": "system", "content": "你是一位chief investment analyst，汇总所有分析师的意见"},
        {"role": "user", "content": "做出最终决策"},
    ]
    resp = await llm_module.chat(messages, user_id=0, module="test")
    import json
    data = json.loads(resp.content)
    assert "rating" in data
    assert "target_price" in data


@pytest.mark.asyncio
async def test_calc_cost_fen():
    assert llm_module.calc_cost_fen("mock", 1000, 500) == 0
    cost = llm_module.calc_cost_fen("deepseek-chat", 1_000_000, 1_000_000)
    assert cost == 50 + 800
