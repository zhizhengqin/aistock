# Task 13：审计 payload 保留、用量聚合与查询计划

## RED

先创建 `backend/tests/services/llm/test_retention.py` 与
`backend/tests/integration/test_llm_query_plans.py`，再运行：

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/services/llm/test_retention.py tests/integration/test_llm_query_plans.py -q
```

结果：1 error（收集阶段 `ModuleNotFoundError: app.services.llm.retention`），证明服务缺失导致真实失败；未使用人为失败断言。

## GREEN 与基础设施证据

定向命令：

```text
cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@127.0.0.1:5432/postgres \
TEST_REDIS_URL=redis://127.0.0.1:6379/15 PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/pytest tests/services/llm/test_retention.py tests/integration/test_llm_query_plans.py -q -s
```

结果：6 passed，6 warnings，4.49 秒。

真实 PostgreSQL 16 disposable database 的 100,000 行计划摘要如下；时间仅记录，不作为毫秒门槛：

| 查询 | Top Actual / Plan Rows | Actual time (ms) | 使用索引 |
| --- | ---: | ---: | --- |
| usage 日期范围 | 2 / 99 | 0.499 | `ix_llm_usage_created_config` |
| pending outbox dispatch | 50 / 50 | 0.028 | `ix_task_outbox_pending_available` |
| stale locked recovery | 1,000 / 10 | 0.338 | `ix_task_outbox_locked_at` |
| cleanup selection | 500 / 1 | 4.522 | `ix_llm_call_attempts_created_config_status`、`ix_llm_token_reservations_task_reserved`、`ix_task_outbox_task_id_key`、`task_records_pkey` |

四类计划均拒绝大表 Seq Scan；五张相关表先执行 `ANALYZE`。cleanup 选择严格保留 `a.status IN ('success','failed','failed_unknown')`，与服务实现一致。

迁移循环测试升级到 `20260821_13`、降级到 `d7e8f9a0b1c2`、再次升级，断言两个新索引分别出现、消失、重建；结果通过。

完整 integration lane：

```text
cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@127.0.0.1:5432/postgres \
TEST_REDIS_URL=redis://127.0.0.1:6379/15 PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/pytest tests/integration -q
```

结果：27 passed，30 warnings，12.19 秒。

后端全量（无 TEST 环境变量）：`357 passed, 26 skipped, 4 warnings`。

前端兼容 DTO（日期、config ID、nullable 总成本）验证：

```text
cd frontend && npm test -- --run src/pages/admin/LlmModelsView.test.tsx
```

结果：15 passed。完整前端 `npm test -- --run && npm run build`：29 passed，Vite build 成功。Playwright 双视口 `npm run test:e2e:llm`：1440×900 与 390×844 均通过，`failures: []`。

## 设计摘要

- `cleanup_llm_audit_payloads` 固定本次调用 cutoff（默认 90 天），按 `(created_at,id)` 排序、每批最多 500 行、批次短事务提交；只将 `LlmCallAttempt.result_json` 与 `response_metadata_json` 写成数据库 SQL `NULL`，避免 JSON literal `null` 在重试中重复选中。
- 仅清理任务与 attempt 均为终态、无 pending/locked outbox、无 reserved reservation、无 live lease 的历史 payload；89 天与恰好 90 天保留，超过 90 天才清理。无 task、pending/running/recoverable 任务、锁定/预留/活跃租约均保持原文。审计行、最终报告、模型配置、usage 与额度 ledger 不修改。
- `LlmConfigService.usage` 以 UTC+8 日期 + module/provider/model/model_config_id 聚合，保留 `module`；任一组价格快照或成本未知则该组与总成本为 `null`，前端显示“价格未知（部分费用未配置）”而非伪造零值，并展示日期列。
- scheduler 每日北京时间 01:00 注册清理任务，线程中执行短事务，中文日志记录 affected/batches/cutoff，失败继续走现有 guarded 管理员通知路径。
- 为 stale locked recovery 与 reservation NOT EXISTS 增加两个窄 partial index：`ix_task_outbox_locked_at`、`ix_llm_token_reservations_task_reserved`；没有新增其他索引。

## 风险/边界

- 100k EXPLAIN 证明新索引对本次生产形状选择性充分；毫秒只作观测，不是阻断门槛。
- usage 兼容 DTO 的 `provider` 允许历史行返回 `null`；model 为空快照时回退 legacy `model` 字段。
- 本任务未接入新闻/模型调用，也未清理任何永久表或管理员审计 payload。
