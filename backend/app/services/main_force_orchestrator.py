import asyncio
import json
from datetime import datetime, timezone
from app.core.logger import logger
from app.datahub.contracts import Capability
from app.datahub.consumer import (
    get_market_capital_flow_rank,
    get_stock_shareholder_count,
    get_stock_info,
    get_stock_kline,
    get_stock_financial_summary,
    get_stock_capital_flow,
    get_optional_kpl,
)
from app.datahub.errors import DataHubError
from app.datahub.consumer import kline_dataframe
from app.datasource.indicators import compute_all
from app.schemas.llm_outputs import (
    MainForceCapitalOutput,
    MainForceFundamentalOutput,
    MainForceIndustryOutput,
    MainForceQuantOutput,
    MainForceResearcherOutput,
    MainForceTechnicalOutput,
)


STRATEGY_FILTERS = {
    "min_market_cap": 60,       # >60亿流通市值
    "max_20d_change_pct": 10,   # 20日涨幅<10%
    "min_60d_net_flow": 0,      # 60日净流入>0
}


def _strategy_filter(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply unheuristic strategy filters; return (passed, excluded).

    This is the 'no-guess' rule engine: every exclusion carries a reason.
    """
    passed = []
    excluded = []
    for c in candidates:
        reasons = []
        if c.get("market_cap", 0) > 0 and c["market_cap"] < STRATEGY_FILTERS["min_market_cap"]:
            reasons.append(f"流通市值{c['market_cap']:.1f}亿 < {STRATEGY_FILTERS['min_market_cap']}亿")
        if c.get("change_pct_20d", 0) > STRATEGY_FILTERS["max_20d_change_pct"]:
            reasons.append(f"20日涨幅{c['change_pct_20d']:.1f}% > {STRATEGY_FILTERS['max_20d_change_pct']}%")
        if c.get("net_main_flow_60d", 0) < STRATEGY_FILTERS["min_60d_net_flow"]:
            reasons.append(f"60日净流入{c['net_main_flow_60d']/1e8:.2f}亿 < 0")
        gdhs = c.get("shareholder", {})
        if gdhs.get("change_pct", 0) > 0:
            reasons.append(f"股东户数增加{gdhs['change_pct']:.1f}%，筹码趋于分散")

        if reasons:
            excluded.append({"code": c["code"], "name": c.get("name", ""), "reason": "；".join(reasons)})
        else:
            passed.append(c)
    return passed, excluded


async def _enrich_candidate(c: dict) -> dict:
    """Add strategy-relevant fields to a raw capital-flow candidate."""
    code = c["code"]
    info = (await get_stock_info(code)).data.model_dump(mode="json")
    kline_df = kline_dataframe(await get_stock_kline(code, 120))

    change_pct_20d = 0
    if not kline_df.empty and len(kline_df) >= 20:
        close = kline_df["close"]
        change_pct_20d = round((close.iloc[-1] / close.iloc[-21] - 1) * 100, 2)

    gdhs = (await get_stock_shareholder_count(code)).data.model_dump(mode="json")

    flow_60d = (await get_stock_capital_flow(code, 60)).data.model_dump(mode="json")

    return {
        **c,
        "name": c.get("name") or info.get("name", ""),
        "market_cap": info.get("market_cap", 0),
        "industry": info.get("industry", ""),
        "price": info.get("price", 0),
        "change_pct_20d": change_pct_20d,
        "shareholder": gdhs,
        # DataHub's monetary contract is yuan.  Keep that unit across
        # consumers; presentation converts to 亿 only inside Chinese text.
        "net_main_flow_60d": flow_60d.get("net_main_flow", 0),
    }


def _build_capital_prompt(data: dict) -> list[dict]:
    candidates_text = "\n".join([f"{c['code']} {c['name']}" for c in data["candidates"]])
    kpl_text = json.dumps(data.get("kpl_strong_sectors", []), ensure_ascii=False, default=str)
    system = f"""{{ANALYST_KEY:main_force_capital}}
你是一位资金流向分析师（main_force_capital）。基于以下候选股的资金流数据分析，选出最值得关注的3-5只。
候选股：
{candidates_text}
开盘啦/Tushare 强势板块（可选参考）：
{kpl_text}

只返回 JSON 对象，且只能包含字段：focus_stocks(字符串数组), analysis(文字), score(0-10),
flow_concentration(资金集中度描述)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_industry_prompt(data: dict) -> list[dict]:
    candidates_text = "\n".join([f"{c['code']} {c['name']}" for c in data["candidates"]])
    system = f"""{{ANALYST_KEY:main_force_industry}}
你是一位行业板块分析师（main_force_industry）。分析以下候选股的板块热度、持续性、新兴机会。
候选股：
{candidates_text}

只返回 JSON 对象，且只能包含字段：focus_stocks(字符串数组), analysis(文字), score(0-10), sector_trend(板块趋势)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_fundamental_prompt(data: dict) -> list[dict]:
    candidates_text = "\n".join([f"{c['code']} {c['name']}" for c in data["candidates"]])
    system = f"""{{ANALYST_KEY:main_force_fundamental}}
你是一位财务基本面分析师（main_force_fundamental）。分析以下候选股的财务健康度和估值。
候选股：
{candidates_text}

只返回 JSON 对象，且只能包含字段：focus_stocks(字符串数组), analysis(文字), score(0-10), health_rating(健康评级)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_technical_prompt(data: dict) -> list[dict]:
    candidates_text = "\n".join([f"{c['code']} {c['name']}" for c in data["candidates"]])
    system = f"""{{ANALYST_KEY:main_force_technical}}
你是一位技术形态分析师（main_force_technical）。分析以下候选股的K线形态和技术指标信号。
候选股：
{candidates_text}

只返回 JSON 对象，且只能包含字段：focus_stocks(字符串数组), analysis(文字), score(0-10), pattern(价格形态)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_quant_prompt(data: dict) -> list[dict]:
    candidates_text = "\n".join([f"{c['code']} {c['name']}" for c in data["candidates"]])
    system = f"""{{ANALYST_KEY:main_force_quant}}
你是一位量化分析师（main_force_quant）。分析以下候选股的量化信号、统计特征和量价关系。
候选股：
{candidates_text}

只返回 JSON 对象，且只能包含字段：focus_stocks(字符串数组), analysis(文字), score(0-10), quant_signals(字符串数组)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_researcher_prompt(results: list[dict], candidates: list[dict]) -> list[dict]:
    results_text = json.dumps(results, ensure_ascii=False, indent=2)
    candidates_text = "\n".join([f"{c['code']} {c['name']}" for c in candidates])
    system = f"""{{ANALYST_KEY:main_force_researcher}}
你是一位资深研究员（main_force_researcher）。综合5位分析师意见，精选最优3-5只标的。
5位分析师的报告：
{results_text}

候选股：
{candidates_text}

只返回 JSON 对象，且只能包含字段：companies(数组，元素含code/name/buy_range/sell_range/confidence(0-100)/position/logic),
excluded(数组，元素含code/name/reason), meeting_summary。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请精选推荐"}]


async def _set_progress(context, value: int) -> None:
    await context.set_progress(value)


async def _execute_step(context, step_key: str, messages: list[dict], output_type):
    await context.ensure_current()
    return await context.llm.execute_json(
        task_id=context.task_id,
        step_key=step_key,
        messages=messages,
        output_type=output_type,
        prompt_version=step_key,
    )


async def run_main_force_selection(user_id: int, task, db) -> dict:
    context = task
    await _set_progress(context, 10)

    # 1. Get top-N from full market capital flow ranking
    raw_candidates = [item.model_dump(mode="json") for item in (await get_market_capital_flow_rank(limit=40)).data]
    await _set_progress(context, 25)

    kpl_strong_sectors: list[dict] = []
    kpl_warnings: list[str] = []
    try:
        kpl_result = await get_optional_kpl(Capability.KPL_STRONG_SECTORS, {})
        if kpl_result is not None:
            kpl_strong_sectors = [item.model_dump(mode="json") for item in kpl_result.data]
    except DataHubError:
        kpl_warnings.append("开盘啦/Tushare 强势板块暂不可用，已继续基础主力筛选")

    # 2. Enrich candidates with strategy-relevant data + filter
    candidates = []
    for c in raw_candidates:
        # Quote/K-line/financial/flow failures are critical: do not pass a
        # partially enriched candidate (or silent zeroes) into the LLM.
        candidates.append(await _enrich_candidate(c))
    await _set_progress(context, 40)

    passed, excluded = _strategy_filter(candidates)
    await _set_progress(context, 50)

    # 3. Run 5 analysts in parallel
    data = {
        "candidates": passed[:20] if passed else candidates[:20],
        "kpl_strong_sectors": kpl_strong_sectors,
    }
    analyst_tasks = [
        ("capital", _build_capital_prompt(data), MainForceCapitalOutput),
        ("industry", _build_industry_prompt(data), MainForceIndustryOutput),
        ("fundamental", _build_fundamental_prompt(data), MainForceFundamentalOutput),
        ("technical", _build_technical_prompt(data), MainForceTechnicalOutput),
        ("quant", _build_quant_prompt(data), MainForceQuantOutput),
    ]
    results = await asyncio.gather(*[
        _execute_step(context, f"main_force.{key}.v1", msgs, output_type)
        for key, msgs, output_type in analyst_tasks
    ])
    analyst_reports = {
        key: result.model_dump(mode="json")
        for (key, _, _), result in zip(analyst_tasks, results)
    }
    await _set_progress(context, 75)

    # 4. Senior researcher: synthesize into final recommendation
    researcher_input = [
        {"analyst": k, "report": v} for k, v in analyst_reports.items()
    ]
    recommendation_result = await _execute_step(
        context,
        "main_force.researcher.v1",
        _build_researcher_prompt(researcher_input, data["candidates"]),
        MainForceResearcherOutput,
    )
    recommendation = recommendation_result.model_dump(mode="json")
    await _set_progress(context, 90)

    # 5. Assemble final report
    report = {
        "skim_count": len(raw_candidates),
        "filtered_count": len(passed),
        "recommended": recommendation,
        "excluded": excluded,
        "analysts": analyst_reports,
        "strategy": {
            "min_market_cap": STRATEGY_FILTERS["min_market_cap"],
            "max_20d_change_pct": STRATEGY_FILTERS["max_20d_change_pct"],
            "min_60d_net_flow": STRATEGY_FILTERS["min_60d_net_flow"],
        },
        "run_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "kpl": {"strong_sectors": kpl_strong_sectors, "warnings": kpl_warnings},
    }
    return report
