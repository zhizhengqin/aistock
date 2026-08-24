import asyncio
import inspect
import json
from datetime import datetime, timezone

from app.datahub.consumer import (
    get_stock_info, get_stock_profile, get_stock_kline, get_stock_financial_summary,
    get_stock_capital_flow, get_stock_news_titles,
)
from app.datahub.consumer import kline_dataframe, records
from app.datahub.errors import DataHubError
from app.datasource.indicators import compute_all
from app.schemas.llm_outputs import (
    CapitalAnalysisOutput,
    ChiefDecisionOutput,
    FundamentalAnalysisOutput,
    NewsAnalysisOutput,
    RiskAnalysisOutput,
    SentimentAnalysisOutput,
    TechnicalAnalysisOutput,
)


_UNAVAILABLE = "数据暂不可用"


def _prompt_value(value) -> str:
    return _UNAVAILABLE if value is None or value == "" else str(value)


def _prompt_decimal(value, suffix: str = "") -> str:
    return _UNAVAILABLE if value is None else f"{value:.2f}{suffix}"


def _prompt_amount(value) -> str:
    return _UNAVAILABLE if value is None else f"{value / 1e8:.2f}亿"


def _prompt_warnings(data: dict) -> str:
    warnings = data.get("data_warnings") or []
    if not warnings:
        return ""
    return (
        "\n数据可用性提醒："
        + "；".join(warnings)
        + "\n以上暂不可用数据不得推算或编造。\n"
    )


def _unavailable_stock_info(stock_code: str) -> dict:
    return {
        "code": stock_code,
        "name": stock_code,
        "price": None,
        "change_pct": None,
        "pe_ttm": None,
        "pb": None,
        "market_cap": None,
        "industry": None,
    }


def _unavailable_financial(stock_code: str) -> dict:
    return {
        "code": stock_code,
        "report_date": None,
        "revenue": None,
        "net_profit": None,
        "roe": None,
        "pe_ttm": None,
        "pb": None,
        "market_cap": None,
        "gross_margin": None,
        "debt_ratio": None,
        "data_at": None,
    }


def _unavailable_profile(stock_code: str) -> dict:
    return {"code": stock_code, "name": stock_code, "industry": None, "data_at": None}


def _unavailable_capital_flow(stock_code: str) -> dict:
    return {
        "code": stock_code,
        "net_main_flow": None,
        "net_super_large": None,
        "net_large": None,
        "net_medium": None,
        "net_small": None,
        "daily_flows": [],
        "data_at": None,
    }


def _build_technical_prompt(data: dict) -> list[dict]:
    indicators = data["indicators"]
    kline = data["kline_summary"]
    info = data["stock_info"]
    system = f"""{{ANALYST_KEY:technical}}
你是一位技术面分析师（technical）。请基于以下真实数据进行分析，输出 JSON。
禁止编造任何数字，所有数字以提供的数据为准。

股票：{_prompt_value(info.get('name'))} {data['stock_code']}，最新价 {_prompt_value(info.get('price'))}，涨跌幅 {_prompt_value(info.get('change_pct'))}%
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
indicator_readings(各指标解读文字), pattern(价格形态识别)。""" + _prompt_warnings(data)
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_fundamental_prompt(data: dict) -> list[dict]:
    fin = data["financial"]
    info = data["stock_info"]
    system = f"""{{ANALYST_KEY:fundamental}}
你是一位基本面分析师（fundamental）。基于以下真实财务数据分析，输出 JSON。
禁止编造数字。

股票：{_prompt_value(info.get('name'))} {data['stock_code']}
营收：{_prompt_amount(fin.get('revenue'))} 净利润：{_prompt_amount(fin.get('net_profit'))} ROE：{_prompt_decimal(fin.get('roe'), '%')}
毛利率：{_prompt_decimal(fin.get('gross_margin'), '%')} 资产负债率：{_prompt_decimal(fin.get('debt_ratio'), '%')}
PE(TTM)={_prompt_value(fin.get('pe_ttm'))} PB={_prompt_value(fin.get('pb'))} 总市值={_prompt_decimal(fin.get('market_cap'), '亿')}
行业：{_prompt_value(info.get('industry'))}

只返回一个 JSON 对象，且只能包含字段：financial_health, profitability, valuation, score(0-10), detail(分析文字)。""" + _prompt_warnings(data)
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_capital_prompt(data: dict) -> list[dict]:
    flow = data["capital_flow"]
    system = f"""{{ANALYST_KEY:capital}}
你是一位资金面分析师（capital）。基于以下真实资金流向数据分析，输出 JSON。
禁止编造数字。

股票：{data['stock_code']}
近20日主力净流入：{_prompt_amount(flow.get('net_main_flow'))}
超大单净流入：{_prompt_amount(flow.get('net_super_large'))} 大单：{_prompt_amount(flow.get('net_large'))}
中单：{_prompt_amount(flow.get('net_medium'))} 小单：{_prompt_amount(flow.get('net_small'))}

只返回一个 JSON 对象，且只能包含字段：main_flow(净流入描述), flow_trend(趋势), score(0-10), detail(分析文字)。""" + _prompt_warnings(data)
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON分析报告"}]


def _build_news_prompt(data: dict) -> list[dict]:
    news = data["news"]
    news_text = "\n".join([f"- {n['title']}" for n in news[:8]])
    if not news_text:
        news_text = _UNAVAILABLE + "（不得推算或编造）"
    system = f"""{{ANALYST_KEY:news}}
你是一位新闻舆情分析师（news）。基于以下近期新闻标题分析，输出 JSON。
股票：{data['stock_code']}
近期新闻：
{news_text}

只返回一个 JSON 对象，且只能包含字段：sentiment_rating(利好/利空/中性偏利好/中性偏利空/中性),
key_news(字符串数组), impact(影响分析)。""" + _prompt_warnings(data)
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


def _build_risk_prompt(data: dict) -> list[dict]:
    info = data["stock_info"]
    indicators = data["indicators"]
    financial = data["financial"]
    system = f"""{{ANALYST_KEY:risk}}
你是一位风险分析师（risk）。请基于以下真实数据识别投资风险，输出 JSON。
禁止编造数据；缺失数据必须明确说明，不得用估算值补齐。

股票：{_prompt_value(info.get('name'))} {data['stock_code']}，最新价 {_prompt_value(info.get('price'))}，涨跌幅 {_prompt_value(info.get('change_pct'))}%
行业：{_prompt_value(info.get('industry'))}
财务：资产负债率={_prompt_value(financial.get('debt_ratio'))}%，PE(TTM)={_prompt_value(financial.get('pe_ttm'))}，PB={_prompt_value(financial.get('pb'))}
RSI={indicators['rsi'].get('RSI', 'N/A')}，近5日收盘价：{data['kline_summary']['recent_closes']}

只返回一个 JSON 对象，且只能包含字段：risk_level(低风险/中等风险/高风险/信息/警告/危险/严重),
risk_score(0-100), analysis(风险分析文字), advice(风险控制建议)。""" + _prompt_warnings(data)
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON风险分析报告"}]


def _build_chief_prompt(reports: list[dict], data_warnings: list[str] | None = None) -> list[dict]:
    reports_text = json.dumps(reports, ensure_ascii=False, indent=2)
    system = f"""{{ANALYST_KEY:chief}}
你是一位首席投资分析师（chief），负责汇总所有分析师意见并做出最终决策。输出 JSON。
禁止编造数字，决策中的价格参考各分析师报告中的数据。

以下是6位分析师的报告：
{reports_text}

只返回一个 JSON 对象，且只能包含字段：rating(买入/持有/卖出), target_price(可选正数), stop_loss(可选正数), confidence(0-100),
entry_range(最终必须为字符串，格式如"50.5-52.5"), take_profit(最终必须为字符串，格式如"55.57"), holding_period(持有期限), position_size(仓位建议),
risk_warning(风险提示), key_watchpoints(字符串数组), meeting_summary(会议总结)。""" + (
        "\n数据可用性提醒：" + "；".join(data_warnings) + "\n缺失数据不得推算或编造。\n"
        if data_warnings else ""
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "请做出最终投资决策"}]


async def _set_progress(context, value: int) -> None:
    await context.set_progress(value)


async def _invoke(fetcher, *args):
    """Call a consumer while keeping test doubles and async consumers uniform."""

    value = fetcher(*args)
    return await value if inspect.isawaitable(value) else value


def _merge_snapshot_valuation(snapshot: dict, financial: dict) -> dict:
    merged = dict(financial)
    for field in ("pe_ttm", "pb", "market_cap"):
        if snapshot.get(field) is not None:
            merged[field] = snapshot[field]
    return merged


async def _execute_step(context, step_key: str, messages: list[dict], output_type):
    """Execute one typed step through the task-scoped model service."""

    await context.ensure_current()
    return await context.llm.execute_json(
        task_id=context.task_id,
        step_key=step_key,
        messages=messages,
        output_type=output_type,
        prompt_version="stock-analysis-v2",
    )


def _raise_unexpected_collection_error(result) -> None:
    """Only provider-level DataHubError may degrade to unavailable data."""

    if isinstance(result, BaseException) and not isinstance(result, DataHubError):
        raise result


async def run_full_analysis(stock_code: str, user_id: int, task, db) -> dict:
    """Run a stock report with the task's locked structured model service."""

    context = task
    await _set_progress(context, 20)

    # 1. Collect independent inputs concurrently.  K-line is the only
    # critical input; every other capability is isolated and represented as
    # unavailable when its own provider fails.
    data_warnings: list[str] = []
    collected = await asyncio.gather(
        _invoke(get_stock_info, stock_code),
        _invoke(get_stock_profile, stock_code),
        _invoke(get_stock_kline, stock_code, 120),
        _invoke(get_stock_financial_summary, stock_code),
        _invoke(get_stock_capital_flow, stock_code, 20),
        _invoke(get_stock_news_titles, stock_code, 10),
        return_exceptions=True,
    )
    info_result, profile_result, kline_result, financial_result, flow_result, news_result = collected

    # K-line is critical.  Other capabilities may degrade only for an
    # explicit DataHubError; programming errors and cancellation must escape.
    if isinstance(info_result, BaseException):
        _raise_unexpected_collection_error(info_result)
        info = _unavailable_stock_info(stock_code)
        data_warnings.append("实时行情数据暂不可用，价格、涨跌幅不得推算或编造")
    else:
        info = info_result.data.model_dump(mode="json")

    if isinstance(kline_result, BaseException):
        raise kline_result
    kline_rows = records(kline_result)
    kline_df = kline_dataframe(kline_result)

    if isinstance(profile_result, BaseException):
        _raise_unexpected_collection_error(profile_result)
        profile = _unavailable_profile(stock_code)
        data_warnings.append("行业资料暂不可用，行业信息不得推算或编造")
    else:
        profile = profile_result.data.model_dump(mode="json")
        if profile.get("name"):
            info["name"] = profile["name"]
    # Industry is a low-frequency profile field.  Do not reuse a stale or
    # provider-specific snapshot classification when the profile is missing.
    info["industry"] = profile.get("industry") or None

    if isinstance(financial_result, BaseException):
        _raise_unexpected_collection_error(financial_result)
        financial = _unavailable_financial(stock_code)
        data_warnings.append("财务数据暂不可用，财务指标不得推算或编造")
    else:
        financial = financial_result.data.model_dump(mode="json")
    financial = _merge_snapshot_valuation(info, financial)

    if isinstance(flow_result, BaseException):
        _raise_unexpected_collection_error(flow_result)
        capital_flow = _unavailable_capital_flow(stock_code)
        data_warnings.append("资金流数据暂不可用，资金指标不得推算或编造")
    else:
        capital_flow = flow_result.data.model_dump(mode="json")

    if isinstance(news_result, BaseException):
        _raise_unexpected_collection_error(news_result)
        # News is optional. Preserve a visible warning while allowing the
        # critical quote/K-line/financial/flow inputs to reach the analysts.
        news = []
        data_warnings.append("新闻数据暂不可用，新闻分析不得推算或编造")
    else:
        news = [item.model_dump(mode="json") for item in news_result.data]

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
        "data_warnings": data_warnings,
    }

    await _set_progress(context, 50)

    # 3. Run 6 analysts in parallel
    analyst_tasks = [
        ("technical", _build_technical_prompt(data), TechnicalAnalysisOutput),
        ("fundamental", _build_fundamental_prompt(data), FundamentalAnalysisOutput),
        ("capital", _build_capital_prompt(data), CapitalAnalysisOutput),
        ("news", _build_news_prompt(data), NewsAnalysisOutput),
        ("sentiment", _build_sentiment_prompt(data), SentimentAnalysisOutput),
        ("risk", _build_risk_prompt(data), RiskAnalysisOutput),
    ]

    results = await asyncio.gather(*[
        _execute_step(context, f"stock.{key}.v1", msgs, output_type)
        for key, msgs, output_type in analyst_tasks
    ])

    analyst_reports = {}
    for (key, _, _), result in zip(analyst_tasks, results):
        analyst_reports[key] = result.model_dump(mode="json")

    await _set_progress(context, 80)

    # 4. Chief analyst: summarize all six analyst reports
    chief_input = [
        {"analyst": "technical", "report": analyst_reports.get("technical", {})},
        {"analyst": "fundamental", "report": analyst_reports.get("fundamental", {})},
        {"analyst": "capital", "report": analyst_reports.get("capital", {})},
        {"analyst": "news", "report": analyst_reports.get("news", {})},
        {"analyst": "sentiment", "report": analyst_reports.get("sentiment", {})},
        {"analyst": "risk", "report": analyst_reports.get("risk", {})},
    ]
    decision_result = await _execute_step(
        context,
        "stock.chief.v1",
        _build_chief_prompt(chief_input, data_warnings),
        ChiefDecisionOutput,
    )
    decision = decision_result.model_dump(mode="json")

    await _set_progress(context, 90)

    # 5. Assemble final report
    report = {
        "stock_code": stock_code,
        "stock_name": info.get("name") or stock_code,
        "stock_info": {
            "price": info.get("price"),
            "change_pct": info.get("change_pct"),
            "pe_ttm": financial.get("pe_ttm"),
            "pb": financial.get("pb"),
            "market_cap": financial.get("market_cap"),
            "industry": info.get("industry"),
        },
        "indicators": indicators,
        "kline": kline_rows[-60:],
        "analysts": analyst_reports,
        "decision": decision,
        "disclaimer": "本分析仅供参考，不构成任何投资建议。",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "data_warnings": data_warnings,
    }
    return report
