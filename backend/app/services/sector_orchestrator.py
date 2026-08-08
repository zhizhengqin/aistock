import asyncio
import json
from datetime import datetime, timezone
from app.core.llm import chat
from app.core.logger import logger
from app.datasource.akshare_client import (
    get_market_indices,
    get_sw_sector_list,
    get_sector_capital_flow,
)
from app.models.task_record import TaskRecord


def _build_macro_prompt(data: dict) -> list[dict]:
    indices_text = json.dumps(data["indices"], ensure_ascii=False, default=str)
    system = f"""{{ANALYST_KEY:sector_macro}}
你是一位宏观策略师（sector_macro）。基于市场指数与宏观环境给出策略分析报告。
大盘指数：{indices_text}

输出 JSON：report(文字报告), score(0-10)"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_diagnosis_prompt(data: dict) -> list[dict]:
    sectors_text = "\n".join([f"{s['name']} 涨跌幅{s['change_pct']}%" for s in data["sw_sectors"]])
    system = f"""{{ANALYST_KEY:sector_diagnosis}}
你是一位板块诊断师（sector_diagnosis）。基于申万二级行业板块行情数据做诊断。
板块行情：
{sectors_text}

输出 JSON：sectors(数组含name/health/trend), score(0-10)"""
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

输出 JSON：inflow_sectors(数组), outflow_sectors(数组), report(文字), score(0-10)"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_sentiment_prompt(data: dict) -> list[dict]:
    indices_text = json.dumps(data["indices"], ensure_ascii=False, default=str)
    system = f"""{{ANALYST_KEY:sector_sentiment}}
你是一位市场情绪解码员（sector_sentiment）。基于大盘行情和板块表现分析投资者情绪。
大盘：{indices_text}

输出 JSON：sentiment_score(0-100), width(市场宽度描述), assessment(情绪评估)"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


def _build_chief_prompt(reports: list[dict], data: dict) -> list[dict]:
    reports_text = json.dumps(reports, ensure_ascii=False, indent=2)
    system = f"""{{ANALYST_KEY:sector_chief}}
你是一位板块综合分析师（sector_chief）。综合4位智能体报告，给出板块多空预测、操作建议和风险提示。
4位智能体报告：
{reports_text}

输出 JSON：bull_sectors(数组含name/confidence/1-10/logic/risk), bear_sectors(同构), neutral_sectors(同构), operation_advice(操作节奏建议), risk_triggers(风险触发条件), key_indicators(核心跟踪指标数组)"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


async def _safe_chat(messages: list[dict], user_id: int, module: str, key: str) -> dict:
    try:
        resp = await chat(messages, user_id=user_id, module=module)
        return json.loads(resp.content)
    except Exception as e:
        logger.warning(f"Sector agent {key} failed: {e}")
        return {"error": str(e), "agent": key}


async def run_sector_analysis(user_id: int, task: TaskRecord, db) -> dict:
    from app.services.progress import maybe_set_progress
    maybe_set_progress(task, db, 10)

    # 1. Collect market data
    indices = get_market_indices()
    maybe_set_progress(task, db, 25)
    sw_sectors = get_sw_sector_list()
    maybe_set_progress(task, db, 40)
    sector_flow = get_sector_capital_flow()
    maybe_set_progress(task, db, 50)

    data = {
        "indices": indices,
        "sw_sectors": sw_sectors[:30],
        "sector_flow": sector_flow[:20],
    }

    # 2. Run 4 agents in parallel
    agent_tasks = [
        ("macro", _build_macro_prompt(data)),
        ("diagnosis", _build_diagnosis_prompt(data)),
        ("capital", _build_capital_prompt(data)),
        ("sentiment", _build_sentiment_prompt(data)),
    ]
    results = await asyncio.gather(*[
        _safe_chat(msgs, user_id, f"sector:{key}", key) for key, msgs in agent_tasks
    ])
    agent_reports = {key: result for (key, _), result in zip(agent_tasks, results)}
    maybe_set_progress(task, db, 75)

    # 3. Chief synthesis: multi-direction prediction
    chief_input = [{"agent": k, "report": v} for k, v in agent_reports.items()]
    decision = await _safe_chat(_build_chief_prompt(chief_input, data), user_id, "sector:chief", "chief")
    maybe_set_progress(task, db, 90)

    # 4. Assemble final report
    report = {
        "agents": agent_reports,
        "decision": decision,
        "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "market_snapshot": {
            "indices": indices,
            "top_flow_sectors": [s for s in sector_flow[:5]],
        },
    }
    return report
