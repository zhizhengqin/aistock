import asyncio
import json
from datetime import datetime, timezone
from app.datasource.akshare_client import (
    get_stock_info, get_stock_kline, get_stock_financial_summary,
    get_stock_capital_flow, get_stock_news_titles,
)
from app.datasource.indicators import compute_all
from app.schemas.llm_outputs import (
    CapitalAnalysisOutput,
    ChiefDecisionOutput,
    FundamentalAnalysisOutput,
    NewsAnalysisOutput,
    SentimentAnalysisOutput,
    TechnicalAnalysisOutput,
)


def _build_technical_prompt(data: dict) -> list[dict]:
    indicators = data["indicators"]
    kline = data["kline_summary"]
    info = data["stock_info"]
    system = f"""{{ANALYST_KEY:technical}}
你是一位技术面分析师（technical）。请基于以下真实数据进行分析，输出 JSON。
禁止编造任何数字，所有数字以提供的数据为准。

股票：{info.get('name','')} {data['stock_code']}，最新价 {info.get('price',0)}，涨跌幅 {info.get('change_pct',0)}%
技术指标：
MA5={indicators['ma'].get('MA5','N/A')} MA20={indicators['ma'].get('MA20','N/A')} MA60={indicators['ma'].get('MA60','N/A')}
DIF={indicators['macd'].get('DIF','N/A')} DEA={indicators['macd'].get('DEA','N/A')} MACD={indicators['macd'].get('MACD','N/A')}
RSI(14)={indicators['rsi'].get('RSI','N/A')}
K={indicators['kdj'].get('K','N/A')} D={indicators['kdj'].get('D','N/A')} J={indicators['kdj'].get('J','N/A')}
BOLL: 上轨={indicators['boll'].get('UP','N/A')} 中轨={indicators['boll'].get('MID','N/A')} 下轨={indicators['boll'].get('LOW','N/A')}
近5日收盘价：{kline['recent_closes']}
近5日成交量：{kline['recent_volumes']}

只返回一个 JSON 对象，且只能包含字段：trend(趋势判断), score(0-100技术评分), short_trend, mid_trend, long_trend,
support_resistance(数组，元素字段为type(支撑或阻力)、price、strength), breakout_prob(0-100),
indicator_readings(各指标解读文字), pattern(价格形态识别)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_fundamental_prompt(data: dict) -> list[dict]:
    fin = data["financial"]
    info = data["stock_info"]
    system = f"""{{ANALYST_KEY:fundamental}}
你是一位基本面分析师（fundamental）。基于以下真实财务数据分析，输出 JSON。
禁止编造数字。

股票：{info.get('name','')} {data['stock_code']}
营收：{fin['revenue']/1e8:.2f}亿 净利润：{fin['net_profit']/1e8:.2f}亿 ROE：{fin['roe']:.2f}%
毛利率：{fin['gross_margin']:.2f}% 资产负债率：{fin['debt_ratio']:.2f}%
PE(TTM)={fin['pe_ttm']} PB={fin['pb']} 总市值={fin['market_cap']:.2f}亿
行业：{info.get('industry','')}

只返回一个 JSON 对象，且只能包含字段：financial_health, profitability, valuation, score(0-10), detail(分析文字)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_capital_prompt(data: dict) -> list[dict]:
    flow = data["capital_flow"]
    system = f"""{{ANALYST_KEY:capital}}
你是一位资金面分析师（capital）。基于以下真实资金流向数据分析，输出 JSON。
禁止编造数字。

股票：{data['stock_code']}
近20日主力净流入：{flow['net_main_flow']/1e8:.2f}亿
超大单净流入：{flow['net_super_large']/1e8:.2f}亿 大单：{flow['net_large']/1e8:.2f}亿
中单：{flow['net_medium']/1e8:.2f}亿 小单：{flow['net_small']/1e8:.2f}亿

只返回一个 JSON 对象，且只能包含字段：main_flow(净流入描述), flow_trend(趋势), score(0-10), detail(分析文字)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_news_prompt(data: dict) -> list[dict]:
    news = data["news"]
    news_text = "\n".join([f"- {n['title']}" for n in news[:8]])
    system = f"""{{ANALYST_KEY:news}}
你是一位新闻舆情分析师（news）。基于以下近期新闻标题分析，输出 JSON。
股票：{data['stock_code']}
近期新闻：
{news_text}

只返回一个 JSON 对象，且只能包含字段：sentiment_rating(利好/利空/中性偏利好/中性偏利空/中性),
key_news(字符串数组), impact(影响分析)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_sentiment_prompt(data: dict) -> list[dict]:
    indicators = data["indicators"]
    kline = data["kline_summary"]
    system = f"""{{ANALYST_KEY:sentiment}}
你是一位市场情绪分析师（sentiment）。基于以下数据分析，输出 JSON。
RSI={indicators['rsi'].get('RSI','N/A')}
KDJ: K={indicators['kdj'].get('K','N/A')} D={indicators['kdj'].get('D','N/A')}
近5日换手率(量比)：{kline['recent_volumes']}
涨跌家数比由大盘决定，暂以RSI和KDJ推断情绪。

只返回一个 JSON 对象，且只能包含字段：sentiment_score(0-100), indicators(指标描述), assessment(情绪评估)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_chief_prompt(reports: list[dict]) -> list[dict]:
    reports_text = json.dumps(reports, ensure_ascii=False, indent=2)
    system = f"""{{ANALYST_KEY:chief}}
你是一位首席投资分析师（chief），负责汇总所有分析师意见并做出最终决策。输出 JSON。
禁止编造数字，决策中的价格参考各分析师报告中的数据。

以下是6位分析师的报告：
{reports_text}

只返回一个 JSON 对象，且只能包含字段：rating(买入/持有/卖出), target_price(可选正数), stop_loss(可选正数), confidence(0-100),
entry_range(入场区间), take_profit(止盈目标), holding_period(持有期限), position_size(仓位建议),
risk_warning(风险提示), key_watchpoints(字符串数组), meeting_summary(会议总结)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请做出最终投资决策"}]


async def _set_progress(context, value: int) -> None:
    await context.set_progress(value)


async def _execute_step(context, step_key: str, messages: list[dict], output_type):
    """Execute one typed step through the task-scoped model service."""

    await context.ensure_current()
    return await context.llm.execute_json(
        task_id=context.task_id,
        step_key=step_key,
        messages=messages,
        output_type=output_type,
        prompt_version=step_key,
    )


async def run_full_analysis(stock_code: str, user_id: int, task, db) -> dict:
    """Run a stock report with the task's locked structured model service."""

    context = task
    await _set_progress(context, 20)

    # 1. Collect data
    info = get_stock_info(stock_code)
    kline_df = get_stock_kline(stock_code, 120)
    financial = get_stock_financial_summary(stock_code)
    capital_flow = get_stock_capital_flow(stock_code, 20)
    news = get_stock_news_titles(stock_code, 10)

    await _set_progress(context, 40)

    # 2. Compute indicators
    indicators = compute_all(kline_df) if not kline_df.empty else {"ma": {}, "macd": {}, "rsi": {}, "kdj": {}, "boll": {}}
    kline_summary = {
        "recent_closes": kline_df["close"].tail(5).round(2).tolist() if not kline_df.empty else [],
        "recent_volumes": kline_df["volume"].tail(5).tolist() if not kline_df.empty else [],
    }

    data = {
        "stock_code": stock_code,
        "stock_info": info,
        "indicators": indicators,
        "kline_summary": kline_summary,
        "financial": financial,
        "capital_flow": capital_flow,
        "news": news,
    }

    await _set_progress(context, 50)

    # 3. Run 5 analysts in parallel
    analyst_tasks = [
        ("technical", _build_technical_prompt(data), TechnicalAnalysisOutput),
        ("fundamental", _build_fundamental_prompt(data), FundamentalAnalysisOutput),
        ("capital", _build_capital_prompt(data), CapitalAnalysisOutput),
        ("news", _build_news_prompt(data), NewsAnalysisOutput),
        ("sentiment", _build_sentiment_prompt(data), SentimentAnalysisOutput),
    ]

    results = await asyncio.gather(*[
        _execute_step(context, f"stock.{key}.v1", msgs, output_type)
        for key, msgs, output_type in analyst_tasks
    ])

    analyst_reports = {}
    for (key, _, _), result in zip(analyst_tasks, results):
        analyst_reports[key] = result.model_dump(mode="json")

    await _set_progress(context, 80)

    # 4. Chief analyst: summarize all
    chief_input = [
        {"analyst": "technical", "report": analyst_reports.get("technical", {})},
        {"analyst": "fundamental", "report": analyst_reports.get("fundamental", {})},
        {"analyst": "capital", "report": analyst_reports.get("capital", {})},
        {"analyst": "news", "report": analyst_reports.get("news", {})},
        {"analyst": "sentiment", "report": analyst_reports.get("sentiment", {})},
    ]
    decision_result = await _execute_step(
        context,
        "stock.chief.v1",
        _build_chief_prompt(chief_input),
        ChiefDecisionOutput,
    )
    decision = decision_result.model_dump(mode="json")

    await _set_progress(context, 90)

    # 5. Assemble final report
    report = {
        "stock_code": stock_code,
        "stock_name": info.get("name", stock_code),
        "stock_info": {
            "price": info.get("price", 0),
            "change_pct": info.get("change_pct", 0),
            "pe_ttm": financial.get("pe_ttm", 0),
            "pb": financial.get("pb", 0),
            "market_cap": financial.get("market_cap", 0),
            "industry": info.get("industry", ""),
        },
        "indicators": indicators,
        "analysts": analyst_reports,
        "decision": decision,
        "disclaimer": "本分析仅供参考，不构成任何投资建议。",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    return report
