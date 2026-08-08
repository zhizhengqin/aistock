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

    # --- M3: main-force selection analysts ---
    "main_force_capital": {
        "focus_stocks": ["600519", "000858", "600036"],
        "analysis": "主力资金持续净流入，超大单买入明显，资金与价格配合度良好。",
        "score": 8,
        "flow_concentration": "资金集中于头白酒白马板块",
    },
    "main_force_industry": {
        "focus_stocks": ["600519", "000858"],
        "analysis": "白酒板块持续受资金关注，消费复苏主线明确，新兴机会在AI应用端。",
        "score": 7,
        "sector_trend": "消费+科技双轮驱动",
    },
    "main_force_fundamental": {
        "focus_stocks": ["600519", "600036"],
        "analysis": "茅台ROE保持30%以上，招行资产质量稳健，估值处于合理区间。",
        "score": 8,
        "health_rating": "优秀",
    },
    "main_force_technical": {
        "focus_stocks": ["600519", "000858"],
        "analysis": "均线多头排列，MACD金叉，量价配合良好。",
        "score": 7,
        "pattern": "上升趋势",
    },
    "main_force_quant": {
        "focus_stocks": ["600519", "600036"],
        "analysis": "量化信号偏多，量价关系健康，统计特征显示动量效应显著。",
        "score": 7,
        "quant_signals": ["动量正", "波动率适中", "换手率正常"],
    },
    "main_force_researcher": {
        "companies": [
            {"code": "600519", "name": "贵州茅台", "buy_range": "1680-1700", "sell_range": "1820-1880", "confidence": 82, "position": "8-12%", "logic": "主力资金持续流入，基本面优秀，技术面多头排列，股东户数减少18.5%筹码集中。"},
            {"code": "000858", "name": "五粮液", "buy_range": "145-148", "sell_range": "162-168", "confidence": 75, "position": "5-8%", "logic": "资金面配合，板块轮动受益，估值修复空间明确。"},
            {"code": "600036", "name": "招商银行", "buy_range": "32.5-33.0", "sell_range": "36.0-38.0", "confidence": 78, "position": "6-10%", "logic": "银行板块资金流入，资产质量稳健，高股息防御属性。"},
        ],
        "excluded": [
            {"code": "601398", "name": "工商银行", "reason": "流通市值过大，资金流入占比不足"},
            {"code": "601288", "name": "农业银行", "reason": "股东户数增加，筹码趋于分散"},
        ],
        "meeting_summary": "茅台最优，五粮液次之，招行防御配置。建议组合配置，控制仓位。",
    },
    # --- M3: sector analysis agents ---
    "sector_macro": {
        "report": "货币政策维持宽松基调，财政发力基建，下半年重点关注消费复苏+科技自主可控双主线。",
        "score": 7,
    },
    "sector_diagnosis": {
        "sectors": [
            {"name": "白酒", "health": "良好", "trend": "复苏"},
            {"name": "半导体", "health": "优秀", "trend": "上行"},
            {"name": "煤炭", "health": "一般", "trend": "回落"},
        ],
        "score": 7,
    },
    "sector_capital": {
        "inflow_sectors": ["半导体", "消费电子", "银行"],
        "outflow_sectors": ["煤炭", "钢铁"],
        "report": "资金流向集中于科技与金融，周期板块资金流出。",
        "score": 6,
    },
    "sector_sentiment": {
        "sentiment_score": 65,
        "width": "涨跌比2.1，市场宽度健康",
        "assessment": "情绪中性偏多，赚钱效应回升。",
    },
    "sector_chief": {
        "bull_sectors": [
            {"name": "半导体", "confidence": 9, "logic": "资金持续流入，政策受益，景气周期向上", "risk": "短期估值偏高需注意回调"},
            {"name": "消费电子", "confidence": 8, "logic": "新品发布季催化，资金流入明显", "risk": "需求不及预期"},
            {"name": "银行", "confidence": 7, "logic": "高股息防御属性，资金轮动受益", "risk": "息差收窄"},
        ],
        "bear_sectors": [
            {"name": "煤炭", "confidence": 8, "logic": "供需格局转弱，价格下行，资金流出", "risk": "冬季补库存或短期反弹"},
            {"name": "钢铁", "confidence": 7, "logic": "需求疲软，成本承压", "risk": "基建刺激政策"},
        ],
        "neutral_sectors": [
            {"name": "食品饮料", "confidence": 5, "logic": "消费温和复苏，缺乏强催化", "risk": "低"},
        ],
        "operation_advice": "短期(1-2周)关注半导体回调后的加仓机会；中期(1-3月)持有消费复苏标的。",
        "risk_triggers": "上证指数放量跌破3850点则仓位降至五成以下",
        "key_indicators": ["北向资金净流入", "两市成交额", "半导体制程突破进展"],
    },
    # --- M3: dragon-tiger analyst ---
    "dragon_tiger_analyst": {
        "summary": "近5日龙虎榜活跃度较高，游资集中于科技与消费板块，主力操作偏短线。",
        "confidence_score": 82.5,
        "active_institutions": [
            {"name": "东方财富拉萨", "success_rate": 48.2, "appearances": 12, "style": "短线"},
            {"name": "华泰证券总部", "success_rate": 52.1, "appearances": 8, "style": "波段"},
        ],
        "strategy_advice": "仓位纪律：单笔亏损5%无条件止损；情绪退潮信号：连板率低于30%减仓；注意T+1数据滞后陷阱；规避无名称及可转债标的。",
        "risk_level": "高风险",
    },

    # --- M4: portfolio diagnosis + risk analysis ---
    "portfolio_diagnosis": {
        "health_score": 45,
        "risk_assessment": "高风险。组合仅持有1只股票，行业集中度极高，缺乏分散化保护。",
        "asset_allocation": "资产配置过于集中，建议分散到2-3个行业，降低单一行业风险敞口。",
        "risk_exposure": "当前组合的行业集中度为100%，个股风险暴露极大，任何单一行业波动都会对组合造成重大影响。",
        "strategy_consistency": "持有评级但满仓集中，策略与执行存在矛盾，建议逐步减仓至合理水平。",
        "suggestions": [
            "1. 逐步减持至30%以下，降低单一持仓集中度",
            "2. 分散到2-3个行业，降低行业集中风险",
            "3. 设定10%止损线，严格执行止损纪律",
            "4. 每月复盘调整组合配置",
        ],
        "summary": "组合健康度偏低，主要风险在于过度集中。建议分散投资、控制仓位、严格止损。",
    },
    "us_research": {
        "cards": {
            "us_sentiment": "震荡偏强",
            "a_share_impact": "中性偏结构性",
            "risk_level": "中等",
            "focus_directions": ["AI算力", "半导体", "红利防御", "银行"],
        },
        "sections": {
            "核心结论": "隔夜美股三大指数集体收涨，科技股领涨，纳斯达克涨幅居前。美债收益率小幅上行，市场对降息节奏保持观望。对A股影响中性偏结构性，建议关注AI算力与半导体的映射机会，防御端配置红利板块。",
            "隔夜美股表现": "道琼斯涨0.47%，纳斯达克涨1.12%，标普500涨0.78%。科技股领涨，风险偏好回升，VIX波动率指数回落。",
            "核心个股解读": "英伟达涨2.35%创新高，AI资本开支逻辑延续；AMD涨3.15%，算力芯片景气度上行；特斯拉跌1.24%，市场等待机器人业务新进展。",
            "板块与主题": "半导体ETF(SMH)领涨，科技(XLK)紧随其后；能源(XLE)偏弱。成长风格明显占优，资金从防御板块流向科技板块。",
            "美债与宏观": "2年期收益率3.85%，10年期4.22%，30年期4.81%。收益率曲线小幅陡峭化，市场对通胀数据保持谨慎。",
            "重要新闻摘要": "美联储官员释放耐心信号，不急于降息；英伟达数据中心需求强劲推动股价创新高；美债收益率在就业数据公布前小幅走高。",
            "对A股的启示": "美股科技强势有望带动A股AI算力、CPO、液冷、半导体设备等映射方向；消费电子关注果链修复；防御端关注银行、电力等红利板块。",
            "风险提示": "海外流动性收紧超预期、地缘政治冲突升级、美股财报不及预期、中美摩擦反复，均可能引发市场波动，建议控制仓位、分散配置。",
        },
    },
    "risk_analyst": {
        "risk_level": "警告",
        "risk_score": 56,
        "analysis": "该股票近期波动率偏高，RSI指标处于中性区域但接近超买区间。价格距离60日高点较近，存在回调压力。建议关注支撑位有效性，若跌破关键支撑则需及时减仓。",
        "advice": "1. 关注成交量变化，缩量下跌可持有，放量下跌需减仓。2. 设置止损线在近期低点下方3-5%。3. 分批操作，避免一次性大额交易。",
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
        m = re.search(r"{ANALYST_KEY:(\w+)}", first_msg)
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
