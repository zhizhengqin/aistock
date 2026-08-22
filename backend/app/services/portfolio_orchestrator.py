import asyncio
import json
from datetime import datetime, timezone
from app.datahub.consumer import get_stock_info, get_stock_kline, get_stock_financial_summary
from app.datahub.consumer import kline_dataframe
from app.datasource.indicators import compute_all
from app.schemas.llm_outputs import PortfolioDiagnosisOutput


def _build_diagnosis_prompt(data: dict) -> list[dict]:
    holdings = data["holdings"]
    holdings_text = "\n".join([
        f"{h['stock_code']} {h['stock_name']} 持仓{h['shares']}股 成本{h['cost_price']} 现价{h['current_price']}"
        f" 盈亏{h['profit_pct']}% 行业{h.get('industry','')}"
        for h in holdings
    ])
    system = f"""{{ANALYST_KEY:portfolio_diagnosis}}
你是一位投资组合诊断分析师（portfolio_diagnosis）。基于以下持仓数据做组合诊断。
持仓：
{holdings_text}

只返回 JSON 对象，且只能包含字段：
health_score(0-100),
risk_assessment(风险评估文字),
asset_allocation(资产配置点评),
risk_exposure(风险暴露分析),
strategy_consistency(策略一致性检查),
suggestions(字符串数组：编号式投资建议),
summary(总体建议)"""
    return [{"role": "system", "content": system}, {"role": "user", "content": "请输出JSON诊断报告"}]


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


async def run_portfolio_diagnosis(holdings: list[dict], user_id: int, task, db) -> dict:
    context = task
    await _set_progress(context, 20)

    # Enrich holdings with latest prices
    enriched = []
    for h in holdings:
        info = (await get_stock_info(h["stock_code"])).data.model_dump(mode="json")
        kline_df = kline_dataframe(await get_stock_kline(h["stock_code"], 60))
        current_price = info.get("price", 0) or h.get("cost_price", 0)
        indicators = compute_all(kline_df) if not kline_df.empty else {"rsi": {}, "ma": {}}
        rsi = indicators.get("rsi", {}).get("RSI")
        shares = h.get("shares", 0)
        cost = h.get("cost_price", 0)
        market_value = current_price * shares
        total_cost = cost * shares
        profit_loss = market_value - total_cost
        profit_pct = round((profit_loss / total_cost) * 100, 2) if total_cost > 0 else 0

        enriched.append({
            **h,
            "stock_name": h.get("stock_name") or info.get("name", ""),
            "current_price": current_price,
            "market_value": round(market_value, 2),
            "profit_loss": round(profit_loss, 2),
            "profit_pct": profit_pct,
            "industry": info.get("industry", ""),
            "rsi": rsi,
        })
    await _set_progress(context, 50)

    # Run AI diagnosis
    data = {"holdings": enriched}
    diagnosis_result = await _execute_step(
        context,
        "portfolio.diagnosis.v1",
        _build_diagnosis_prompt(data),
        PortfolioDiagnosisOutput,
    )
    diagnosis = diagnosis_result.model_dump(mode="json")
    await _set_progress(context, 80)

    # Append computed stats to report
    total_cost = sum(h["cost_price"] * h["shares"] for h in enriched)
    total_market_value = sum(h["market_value"] for h in enriched)
    total_profit = total_market_value - total_cost
    total_profit_pct = round((total_profit / total_cost) * 100, 2) if total_cost > 0 else 0

    # Count unique industries
    industries = set(h.get("industry", "") for h in enriched if h.get("industry"))
    concentration = 1.0 / max(len(industries), 1) if enriched else 0

    report = {
        "health_score": diagnosis.get("health_score", 0),
        "risk_assessment": diagnosis.get("risk_assessment", ""),
        "asset_allocation": diagnosis.get("asset_allocation", ""),
        "risk_exposure": diagnosis.get("risk_exposure", ""),
        "strategy_consistency": diagnosis.get("strategy_consistency", ""),
        "suggestions": diagnosis.get("suggestions", []),
        "summary": diagnosis.get("summary", ""),
        "holdings": enriched,
        "portfolio_stats": {
            "total_stocks": len(enriched),
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market_value, 2),
            "total_profit": round(total_profit, 2),
            "total_profit_pct": total_profit_pct,
            "industries_count": len(industries),
            "concentration": round(concentration, 2),
        },
        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
    }
    return report
