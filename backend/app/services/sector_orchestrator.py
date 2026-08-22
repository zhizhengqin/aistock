import asyncio
import json
from datetime import datetime, timezone
from app.datahub.contracts import Capability
from app.datahub.consumer import (
    get_market_indices,
    get_sw_sector_list,
    get_sector_capital_flow,
    get_optional_kpl,
)
from app.datahub.errors import DataHubError
from app.schemas.llm_outputs import (
    SectorCapitalOutput,
    SectorChiefOutput,
    SectorDiagnosisOutput,
    SectorMacroOutput,
    SectorSentimentOutput,
)


def _build_macro_prompt(data: dict) -> list[dict]:
    indices_text = json.dumps(data["indices"], ensure_ascii=False, default=str)
    kpl_text = json.dumps(data.get("kpl_strong_sectors", []), ensure_ascii=False, default=str)
    system = f"""{{ANALYST_KEY:sector_macro}}
你是一位宏观策略师（sector_macro）。基于市场指数与宏观环境给出策略分析报告。
大盘指数：{indices_text}
开盘啦/Tushare 强势板块（可选参考）：{kpl_text}

只返回 JSON 对象，且只能包含字段：report(文字报告), score(0-10)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_diagnosis_prompt(data: dict) -> list[dict]:
    sectors_text = "\n".join([f"{s['name']} 涨跌幅{s['change_pct']}%" for s in data["sw_sectors"]])
    system = f"""{{ANALYST_KEY:sector_diagnosis}}
你是一位板块诊断师（sector_diagnosis）。基于申万二级行业板块行情数据做诊断。
板块行情：
{sectors_text}

只返回 JSON 对象，且只能包含字段：sectors(数组，元素字段为name/health/trend), score(0-10)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_capital_prompt(data: dict) -> list[dict]:
    flow_text = "\n".join([
        f"{s['name']} 净流入{s['net_main_flow']/1e8:.2f}亿"
        for s in data["sector_flow"]
    ])
    system = f"""{{ANALYST_KEY:sector_capital}}
你是一位资金流向分析师（sector_capital）。基于板块资金流数据分析资金集中度和轮动趋势。
板块资金流：
{flow_text}

只返回 JSON 对象，且只能包含字段：inflow_sectors(字符串数组), outflow_sectors(字符串数组), report(文字), score(0-10)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_sentiment_prompt(data: dict) -> list[dict]:
    indices_text = json.dumps(data["indices"], ensure_ascii=False, default=str)
    system = f"""{{ANALYST_KEY:sector_sentiment}}
你是一位市场情绪解码员（sector_sentiment）。基于大盘行情和板块表现分析投资者情绪。
大盘：{indices_text}

只返回 JSON 对象，且只能包含字段：sentiment_score(0-100), width(市场宽度描述), assessment(情绪评估)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_chief_prompt(reports: list[dict], data: dict) -> list[dict]:
    reports_text = json.dumps(reports, ensure_ascii=False, indent=2)
    system = f"""{{ANALYST_KEY:sector_chief}}
你是一位板块综合分析师（sector_chief）。综合4位智能体报告，给出板块多空预测、操作建议和风险提示。
4位智能体报告：
{reports_text}

只返回 JSON 对象，且只能包含字段：bull_sectors、bear_sectors、neutral_sectors（数组，元素字段为name、confidence(0-10)、logic、risk），
operation_advice(操作节奏建议), risk_triggers(风险触发条件), key_indicators(核心跟踪指标字符串数组)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


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


async def run_sector_analysis(user_id: int, task, db) -> dict:
    context = task
    await _set_progress(context, 10)

    # 1. Collect market data
    indices = [item.model_dump(mode="json") for item in (await get_market_indices()).data]
    await _set_progress(context, 25)
    sw_sectors = [item.model_dump(mode="json") for item in (await get_sw_sector_list()).data]
    await _set_progress(context, 40)
    sector_flow = [item.model_dump(mode="json") for item in (await get_sector_capital_flow()).data]
    await _set_progress(context, 50)

    kpl_strong_sectors: list[dict] = []
    kpl_warnings: list[str] = []
    try:
        kpl_result = await get_optional_kpl(Capability.KPL_STRONG_SECTORS, {})
        if kpl_result is not None:
            kpl_strong_sectors = [item.model_dump(mode="json") for item in kpl_result.data]
    except DataHubError:
        kpl_warnings.append("开盘啦/Tushare 强势板块暂不可用，已继续基础板块分析")

    data = {
        "indices": indices,
        "sw_sectors": sw_sectors[:30],
        "sector_flow": sector_flow[:20],
        "kpl_strong_sectors": kpl_strong_sectors,
    }

    # 2. Run 4 agents in parallel
    agent_tasks = [
        ("macro", _build_macro_prompt(data), SectorMacroOutput),
        ("diagnosis", _build_diagnosis_prompt(data), SectorDiagnosisOutput),
        ("capital", _build_capital_prompt(data), SectorCapitalOutput),
        ("sentiment", _build_sentiment_prompt(data), SectorSentimentOutput),
    ]
    results = await asyncio.gather(*[
        _execute_step(context, f"sector.{key}.v1", msgs, output_type)
        for key, msgs, output_type in agent_tasks
    ])
    agent_reports = {
        key: result.model_dump(mode="json")
        for (key, _, _), result in zip(agent_tasks, results)
    }
    await _set_progress(context, 75)

    # 3. Chief synthesis: multi-direction prediction
    chief_input = [{"agent": k, "report": v} for k, v in agent_reports.items()]
    decision_result = await _execute_step(
        context,
        "sector.chief.v1",
        _build_chief_prompt(chief_input, data),
        SectorChiefOutput,
    )
    decision = decision_result.model_dump(mode="json")
    await _set_progress(context, 90)

    # 4. Assemble final report
    report = {
        "agents": agent_reports,
        "decision": decision,
        "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "market_snapshot": {
            "indices": indices,
            "top_flow_sectors": [s for s in sector_flow[:5]],
        },
        "kpl": {"strong_sectors": kpl_strong_sectors, "warnings": kpl_warnings},
    }
    return report
