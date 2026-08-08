import json
import httpx
from dataclasses import dataclass
from datetime import datetime, timezone
from app.core.config import settings
from app.core.logger import logger


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str


# DeepSeek pricing (per 1M tokens, in fen = 1/100 yuan)
# deepseek-chat: input ¥0.5/M, output ¥8/M → 50 fen / 800 fen
PRICING = {
    "deepseek-chat": {"input": 50, "output": 800},
    "mock": {"input": 0, "output": 0},
}


def calc_cost_fen(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    p = PRICING.get(model, PRICING["mock"])
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) // 1_000_000


MOCK_RESPONSES = {
    "technical": {
        "trend": "横盘箱体震荡",
        "score": 62,
        "short_trend": "震荡",
        "mid_trend": "整理",
        "long_trend": "上行",
        "support_resistance": [
            {"type": "支撑", "price": 68.50, "strength": "强"},
            {"type": "阻力", "price": 75.00, "strength": "中"},
        ],
        "breakout_prob": 55,
        "indicator_readings": "MA5与MA20粘合，MACD金叉初现，RSI处于中性区域。",
        "pattern": "箱体震荡",
    },
    "fundamental": {
        "financial_health": "良好",
        "profitability": "稳定增长",
        "valuation": "合理偏低",
        "score": 5.5,
        "detail": "营收稳健，ROE保持15%以上，PE处于行业中等水平。",
    },
    "capital": {
        "main_flow": "近20日净流入2.3亿",
        "flow_trend": "主力持续吸筹",
        "score": 4,
        "detail": "超大单净流入明显，小单流出，筹码趋于集中。",
    },
    "news": {
        "sentiment_rating": "中性偏利好",
        "key_news": ["公司发布业绩预增公告", "行业政策利好"],
        "impact": "短期情绪偏正面",
    },
    "sentiment": {
        "sentiment_score": 79.2,
        "indicators": "换手率1.2%，大盘恐慌贪婪指数79.2极度贪婪",
        "assessment": "市场情绪偏热",
    },
    "chief": {
        "rating": "持有",
        "target_price": 82.18,
        "stop_loss": 68.50,
        "confidence": 60,
        "entry_range": "69.50-70.50",
        "take_profit": "82.00-85.00",
        "holding_period": "1-3个月",
        "position_size": "不超过15%",
        "risk_warning": "大盘系统性风险、板块轮动风险",
        "key_watchpoints": ["MA20支撑有效性", "主力资金流向变化", "成交量能否放大"],
        "meeting_summary": "技术面箱体震荡偏多，基本面稳健，资金面主力吸筹，情绪面偏热，综合建议持有。",
    },
}


async def chat(messages: list[dict], model: str = None, temperature: float = 0.3,
               user_id: int = 0, module: str = "unknown") -> LLMResponse:
    model = model or settings.LLM_MODEL

    if settings.LLM_MOCK or not settings.DEEPSEEK_API_KEY:
        # Mock mode: find key from explicit marker in first system message
        import re
        key = "chief"
        first_msg = messages[0]["content"] if messages else ""
        m = re.search(r"\{\{ANALYST_KEY:(\w+)\}\}", first_msg)
        if m:
            key = m.group(1)
        content = json.dumps(MOCK_RESPONSES[key], ensure_ascii=False)
        resp = LLMResponse(content=content, prompt_tokens=500, completion_tokens=200, model="mock")
    else:
        async with httpx.AsyncClient(timeout=120) as client:
            resp_raw = await client.post(
                f"{settings.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json={"model": model, "messages": messages, "temperature": temperature,
                      "response_format": {"type": "json_object"}},
            )
            resp_raw.raise_for_status()
            data = resp_raw.json()
            choice = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            resp = LLMResponse(
                content=choice,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                model=model,
            )

    _log_usage(user_id, module, resp)
    return resp


def _log_usage(user_id: int, module: str, resp: LLMResponse):
    try:
        from app.core.database import SessionLocal
        from app.models.llm_usage import LlmUsage
        cost = calc_cost_fen(resp.model, resp.prompt_tokens, resp.completion_tokens)
        db = SessionLocal()
        db.add(LlmUsage(
            user_id=user_id, module=module, model=resp.model,
            prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
            cost_fen=cost,
        ))
        db.commit()
        db.close()
    except Exception as e:
        logger.warning(f"llm_usage log failed: {e}")
