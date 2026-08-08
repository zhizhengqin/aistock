import json
from datetime import datetime, timezone
from app.core.llm import chat
from app.core.logger import logger
from app.datasource.akshare_client import (
    get_dragon_tiger_list,
    get_dragon_tiger_institution,
)
from app.services.dragon_tiger_scorer import rank_top_stocks, compute_stats, rank_institutions
from app.models.task_record import TaskRecord


def _build_analyst_prompt(stats: dict, top_stocks: list[dict]) -> list[dict]:
    stats_text = json.dumps(stats, ensure_ascii=False, default=str)
    stocks_text = json.dumps(top_stocks, ensure_ascii=False, default=str)
    system = f"""{{ANALYST_KEY:dragon_tiger_analyst}}
你是一位游资行为分析师（dragon_tiger_analyst）。基于龙虎榜统计数据和排行股票做行为分析。
统计摘要：{stats_text}
TOP10股票：{stocks_text}

输出 JSON：summary(文字摘要), confidence_score(0-100), active_institutions(数组含name/success_rate/appearances/style), strategy_advice(策略建议文字), risk_level(风险等级)"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON"}]


async def _safe_chat(messages: list[dict], user_id: int, module: str) -> dict:
    try:
        resp = await chat(messages, user_id=user_id, module=module)
        return json.loads(resp.content)
    except Exception as e:
        logger.warning(f"Dragon tiger analyst failed: {e}")
        return {"error": str(e)}


async def run_dragon_tiger_analysis(period_days: int, user_id: int, task: TaskRecord, db) -> dict:
    from app.services.progress import maybe_set_progress
    maybe_set_progress(task, db, 15)

    # 1. Collect dragon-tiger records
    records = get_dragon_tiger_list(days=period_days)
    maybe_set_progress(task, db, 35)

    # 2. Score and rank
    stats = compute_stats(records)
    top_stocks = rank_top_stocks(records, top_n=10)
    maybe_set_progress(task, db, 55)

    # 3. Collect institution data
    institutions = get_dragon_tiger_institution()
    ranked_institutions = rank_institutions(institutions, top_n=10)
    maybe_set_progress(task, db, 70)

    # 4. AI analysis
    analysis = await _safe_chat(
        _build_analyst_prompt(stats, top_stocks),
        user_id, "dragon_tiger:analyst"
    )
    maybe_set_progress(task, db, 90)

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
