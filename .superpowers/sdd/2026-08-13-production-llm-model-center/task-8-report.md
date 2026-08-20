# Task 8 报告：任务级幂等与结构化模型调用服务

## 实现摘要

- 新增 `LlmExecutionService.execute_json`：每一步先校验 `execution_token`，再复用同一 `(task_id, step_key)` 的完整成功结果；模型请求统一经过 `LlmCallExecutor(operation_type="task")`。
- Pydantic 校验失败最多发起一次独立 JSON correction；修正仍失败时传播稳定的 `llm_schema_invalid`，不伪造业务结果。`failed_unknown` 记录作为终态，不自动重放。
- 通过 `result_json`、`result_schema_version` 与 SHA-256 `result_hash` 持久化已验证步骤；最终写入前再次执行 fencing 校验，旧 worker 不能写入已被新 owner 接管的步骤。
- Task 7 `TaskExecutionContext` 增加 task-scoped `llm` 与已解密 `runtime_config`；runner 在 claim 事务内只解密一次并复用同一服务实例。纯数据任务保持 `llm=None`。
- `core/llm.py` 收敛为兼容导入 facade；旧 `chat` 入口只返回稳定停用错误，不保留产品 Mock、独立 HTTP 客户端或独立用量 Session。

## TDD 与验证

### RED

```text
cd backend && .venv/bin/pytest tests/services/llm/test_execution_service.py -q
```

结果：收集阶段失败（`0 items / 1 error`），`ModuleNotFoundError: No module named 'app.services.llm.execution_service'`。

### GREEN targeted

```text
cd backend && .venv/bin/pytest tests/services/llm/test_execution_service.py tests/services/llm/test_call_executor.py tests/integration/test_llm_budget_concurrency.py tests/test_llm.py -q
```

结果：`19 passed, 2 skipped, 2 warnings`。

### Task 7 回归

```text
cd backend && .venv/bin/pytest tests/services/test_task_execution.py -q
```

结果：`25 passed, 2 warnings`。

### 全量

最终运行结果：`309 passed, 18 skipped, 1 failed, 4 warnings`。

唯一失败为 `tests/test_orchestrator.py::test_orchestrator_full_report_structure`：该 Task 9/10 迁移前测试仍直接依赖已删除的 `LLM_MOCK` 固定报告；Task 8 文件边界不允许修改该 orchestrator 或其测试，故作为阶段性剩余风险记录。

```text
cd backend && .venv/bin/pytest -q
```
