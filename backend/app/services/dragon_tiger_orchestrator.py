import json
from datetime import datetime, timezone
from app.datasource.akshare_client import (
    get_dragon_tiger_list,
    get_dragon_tiger_institution,
)
from app.services.dragon_tiger_scorer import rank_top_stocks, compute_stats, rank_institutions
from app.schemas.llm_outputs import DragonTigerAnalysisOutput


def _build_analyst_prompt(stats: dict, top_stocks: list[dict]) -> list[dict]:
    stats_text = json.dumps(stats, ensure_ascii=False, default=str)
    stocks_text = json.dumps(top_stocks, ensure_ascii=False, default=str)
    system = f"""{{ANALYST_KEY:dragon_tiger_analyst}}
你是一位游资行为分析师（dragon_tiger_analyst）。基于龙虎榜统计数据和排行股票做行为分析。
统计摘要：{stats_text}
TOP10股票：{stocks_text}

只返回 JSON 对象，且只能包含字段：summary(文字摘要)、confidence_score(0-100)、
active_institutions(数组，元素含name/success_rate(0-100)/appearances/style)、
strategy_advice(策略建议文字)、risk_level(低风险/中等风险/高风险)。"""
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


async def run_dragon_tiger_analysis(period_days: int, user_id: int, task, db) -> dict:
    context = task
    await _set_progress(context, 15)

    # 1. Collect dragon-tiger records
    records = get_dragon_tiger_list(days=period_days)
    await _set_progress(context, 35)

    # 2. Score and rank
    stats = compute_stats(records)
    top_stocks = rank_top_stocks(records, top_n=10)
    await _set_progress(context, 55)

    # 3. Collect institution data
    institutions = get_dragon_tiger_institution()
    ranked_institutions = rank_institutions(institutions, top_n=10)
    await _set_progress(context, 70)

    # 4. AI analysis
    analysis_result = await _execute_step(
        context,
        "dragon_tiger.analysis.v1",
        _build_analyst_prompt(stats, top_stocks),
        DragonTigerAnalysisOutput,
    )
    analysis = analysis_result.model_dump(mode="json")
    await _set_progress(context, 90)

    # 5. Assemble report
    report = {
        "period_days": period_days,
        "stats": {**stats, **{k: v for k, v in analysis.items() if k in ["confidence_score", "risk_level"]}},
        "data_summary": stats,
        "top_stocks": top_stocks,
        "institutions": ranked_institutions,
        "analysis": analysis,
        "strategy_advice": analysis.get("strategy_advice", ""),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    return report
