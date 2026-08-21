# Task 10 报告：剩余分析迁移与样例清场

## 冻结的 React 页面契约

- `DragonTiger.tsx` 读取 `period_days`、`stats`、`data_summary`、`top_stocks`、`institutions`、`analysis` 与 `analyzed_at`；AI 分析字段为 `summary`、`confidence_score`、`strategy_advice`、`risk_level`，榜单和机构嵌套字段保持原名。
- `Portfolio.tsx` 的 AI 诊断读取 `health_score`、`risk_assessment`、`asset_allocation`、`risk_exposure`、`strategy_consistency`、`suggestions`、`summary`；持仓与组合统计仍由原数据计算层提供。
- `RiskWarning.tsx` 的个股任务结果继续通过 `task.result.ai` 读取 `risk_level`、`risk_score`、`analysis`、`advice`；规则引擎预警字段和组合扫描响应保持不变。
- `USResearch.tsx` 继续读取 `cards.us_sentiment/a_share_impact/risk_level/focus_directions`、指数/个股/涨跌榜/ETF/收益率/英文新闻数组、`data_status`，以及八段式 `sections[{title,content}]`。

## 实现摘要

- 在 `llm_outputs.py` 增加 v1 strict schemas：`DragonTigerAnalysisOutput`、`PortfolioDiagnosisOutput`、`RiskAnalysisOutput`、`UsResearchOutput`（含 cards 与严格八段章节键），嵌套机构和评分字段同样拒绝额外字段、空文本、越界与非有限数值。
- 龙虎榜、持仓诊断、个股风控和美股叙事均改为 task-scoped `ctx.llm.execute_json`，稳定步骤分别为 `dragon_tiger.analysis.v1`、`portfolio.diagnosis.v1`、`risk.analysis.v1`、`us_research.narrative.v1`；typed 结果仅通过 `model_dump(mode="json")` 进入原报告结构，错误直接向 runner 传播。
- US wrapper 现在把 `execution_ctx` 传入 `build_report`；原始行情/新闻抓取与 AI narrative 分层，抓取异常记录为 `data_status[name] = "failed"` 并返回空数据，不再用硬编码行情、样例新闻或默认叙事伪装成功。
- 新闻保持纯数据任务：`rule_based_tag` 作为确定性分类，不读取模型配置、不产生 Token；删除 `SAMPLE_NEWS`、样例播种、模型开关条件和全源失败样例路径，全源失败返回 `new=0` 与 errors。
- 删除配置中的旧模型 Mock 开关及 CI 测试环境注入；为避免历史环境变量导致 Settings 启动失败，Settings 对未知环境键采用忽略策略，不恢复该业务开关。

## TDD 与验证

### RED

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_remaining_llm_contracts.py tests/test_orchestrator.py tests/test_news.py -q
```

旧实现结果：收集 `13 items / 1 error`，因 `app.schemas.llm_outputs` 尚未包含 `DragonTigerAnalysisOutput` 而失败。

### GREEN targeted

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_remaining_llm_contracts.py tests/test_orchestrator.py tests/test_news.py -q
```

结果：`22 passed, 2 warnings`。

### 相关回归

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_dragon_tiger.py tests/test_portfolio.py tests/test_risk_engine.py tests/test_us_research.py tests/services/llm/test_execution_service.py tests/services/test_task_execution.py -q
```

结果：`68 passed, 2 warnings`。

### Forbidden 扫描

```text
rg -n 'LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|_safe_chat|allow_fallback|from app.core.llm import chat|await chat\(' backend/app frontend/src deploy .github
```

结果：无 product matches。

### 全量

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
```

结果：`350 passed, 18 skipped, 4 warnings`。PostgreSQL integration 按仓库现有规则在本机缺少 `TEST_DATABASE_URL` 时跳过。

### 差异检查

`git diff --check` 通过。

## 边界与风险

按批准接线边界更新了 `tasks/us_research.py`、`core/config.py`、CI workflow，以及四个旧直接调用测试和新闻测试的 typed context/真实失败语义；未修改数据库、TaskRunner、LlmExecutionService 或 Task 11。美股数据源部分失败时仍可生成带明确 `failed` 状态的 typed narrative，若模型步骤失败则由 runner 终止任务且不保存最终报告。
