# Task 9 报告：版本化业务输出 Schema 与核心分析迁移

## 冻结的前端契约

- `Analysis.tsx` 继续读取 `stock_code`、`stock_name`、`stock_info`、`indicators`、`analysts` 和 `decision`。五位股票分析师保留 `score` 以及各自的 `trend/detail/sentiment_rating/main_flow/assessment` 等字段，首席决策保留评级、价格区间、置信度、仓位、风险提示、跟踪点和会议总结。
- `MainForce.tsx` 继续读取候选数、筛选数、策略阈值、五位分析师 `focus_stocks/analysis/score` 与专属字段，以及研究员 `companies/excluded/meeting_summary` 的原有嵌套结构。
- `Sector.tsx` 继续读取四位智能体的 `report/sectors/inflow_sectors/outflow_sectors/sentiment_score/width/assessment`，并读取首席 `bull_sectors/bear_sectors/neutral_sectors/operation_advice/risk_triggers/key_indicators`。

## 实现摘要

- 新增 `app.schemas.llm_outputs`：所有业务输出均为 `schema_version = "v1"` 的 Pydantic strict model，`extra="forbid"`；字符串拒绝空白，评分/置信度/价格拒绝越界、NaN/Infinity，评级和支撑阻力类型使用限定枚举，嵌套排名项同样严格校验。
- 股票、主力选股、板块分析三个 orchestrator 改为接收现有 `TaskExecutionContext`，每个步骤先 `ensure_current()`，再调用 `ctx.llm.execute_json`，并把已验证结果通过 `model_dump(mode="json")` 组装原有报告。独立分析师仍并行执行，chief/researcher 只在全部 typed 输入成功后执行；模型异常直接传播，不再生成中性/错误占位结果。
- 每一步使用稳定版本键：`stock.*.v1`、`main_force.*.v1`、`sector.*.v1`，并将同值作为 `prompt_version`；提示词明确要求只输出精确 JSON 字段。
- 两个旧直接调用测试已按批准边界例外机械迁移为 task context + typed service double，未在产品代码恢复 Mock 或 `None` fallback。

## TDD 与验证

### RED

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/schemas/test_llm_outputs.py tests/services/test_analysis_llm_contracts.py tests/test_orchestrator.py -q
```

旧实现结果：收集阶段失败（`2 errors`），均为 `ModuleNotFoundError: No module named 'app.schemas.llm_outputs'`。

### GREEN targeted

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/schemas/test_llm_outputs.py tests/services/test_analysis_llm_contracts.py tests/test_orchestrator.py -q
```

结果：`30 passed, 2 warnings`。

### 相关回归

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_main_force.py tests/test_sector.py tests/services/llm/test_execution_service.py tests/services/test_task_execution.py -q
```

结果：`43 passed, 2 warnings`。

### 全量

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
```

结果：`341 passed, 18 skipped, 4 warnings`。集成 PostgreSQL 测试按仓库现有规则在本机缺少 `TEST_DATABASE_URL` 时跳过。

### 静态检查

- 三个核心 orchestrator 中 `_safe_chat`、`from app.core.llm import chat`、`await chat(` 均无匹配。
- `git diff --check` 通过。

## 边界与风险

本任务唯一边界例外是更新 `tests/test_main_force.py` 与 `tests/test_sector.py` 的旧调用方式，使其注入 typed task context；未修改任务 wrapper、数据库模型、TaskRunner 或 Task 10 范围。外部模型若持续返回不符合 v1 的 JSON，会按 Task 8 correction/错误传播路径结束任务，不再保存不完整业务报告。
