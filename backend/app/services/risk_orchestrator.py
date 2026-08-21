import asyncio
import json
from datetime import datetime, timezone
from app.datasource.akshare_client import get_stock_info, get_stock_kline
from app.datasource.indicators import compute_all
from app.services.risk_engine import analyze_stock_risk, compute_portfolio_risk
from app.schemas.llm_outputs import RiskAnalysisOutput


def _build_risk_prompt(warnings: list[dict], stock_code: str, stock_name: str, days: int) -> list[dict]:
    warnings_text = json.dumps(warnings, ensure_ascii=False, indent=2)
    system = f"""{{ANALYST_KEY:risk_analyst}}
你是一位风险分析师（risk_analyst）。基于以下风险检测结果做深度分析。
股票：{stock_name}({stock_code})
分析天数：{days}
风险检测结果：
{warnings_text}

只返回 JSON 对象，且只能包含字段：risk_level(信息/警告/危险/严重), risk_score(0-100),
analysis(深度分析文字), advice(应对建议)。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON风险报告"}]


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


async def run_stock_risk_analysis(stock_code: str, days: int, user_id: int, task, db) -> dict:
    context = task
    await _set_progress(context, 20)

    info = get_stock_info(stock_code)
    stock_name = info.get("name", stock_code)
    kline_df = get_stock_kline(stock_code, min(days * 2, 250))
    await _set_progress(context, 40)

    indicators = compute_all(kline_df) if not kline_df.empty else {"rsi": {}}
    rsi = indicators.get("rsi", {}).get("RSI")

    # Run rule-based checks
    warnings = analyze_stock_risk(kline_df, rsi)
    await _set_progress(context, 65)

    # AI deep analysis
    ai_analysis_result = await _execute_step(
        context,
        "risk.analysis.v1",
        _build_risk_prompt(warnings, stock_code, stock_name, days),
        RiskAnalysisOutput,
    )
    ai_analysis = ai_analysis_result.model_dump(mode="json")
    await _set_progress(context, 90)

    report = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "days": days,
        "warnings": warnings,
        "indicators": {"rsi": rsi, "ma": indicators.get("ma", {})},
        "ai_analysis": ai_analysis,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    return report
"""
Portfolio risk: scan all user holdings and aggregate.
"""

async def run_portfolio_risk_scan(holdings: list[dict], user_id: int, task, db) -> dict:
    context = task
    await _set_progress(context, 10)

    enriched = []
    for h in holdings:
        kline_df = get_stock_kline(h["stock_code"], 60)
        indicators = compute_all(kline_df) if not kline_df.empty else {"rsi": {}}
        rsi = indicators.get("rsi", {}).get("RSI")
        warnings = analyze_stock_risk(kline_df, rsi)
        enriched.append({
            "stock_code": h["stock_code"],
            "stock_name": h.get("stock_name", ""),
            "warnings": warnings,
            "rsi": rsi,
        })
    await _set_progress(context, 60)

    portfolio = compute_portfolio_risk(enriched)
    await _set_progress(context, 90)

    report = {
        "holdings": enriched,
        "portfolio": portfolio,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    return report
