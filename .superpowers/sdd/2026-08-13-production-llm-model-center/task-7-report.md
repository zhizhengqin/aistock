# Task 7 报告：短事务执行 Runner、租约与 Fencing

## 结果

- 新增 `backend/app/services/task_execution.py`，统一提供 `TaskExecutionRunner`、`TaskExecutionContext`、`TaskExecutionFenced`、快照参数校验和短事务生命周期。
- claim 使用 `TaskRecord` 行锁；`pending` 或租约过期的 `running` 任务生成新的 UUID `execution_token`、heartbeat 和 lease。`success`/`failed`/`failed_unknown` 直接 terminal no-op；活动 lease 的重复投递不会进入业务 callback。
- claim、progress、heartbeat、execution-token gate 和最终落库均使用当前 token 条件；旧 owner 在 reclaim 后的 progress、heartbeat、下一步 gate 和最终 persist 均被 fencing。
- 业务 execute 回调期间不持有 SQLAlchemy Session。最终 `persist_result(db, task, result)` 只允许同步数据库边界，报告、`TaskRecord.status=success`、progress=100 和 result 在同一事务提交；异常整体 rollback，再由当前 token 写稳定中文失败信息。
- 后台 heartbeat 可续租；heartbeat 异常不会被静默吞掉，主执行在完成前观察到异常并失败。取消会停止 heartbeat、按当前 owner 标记失败后继续传播 `CancelledError`。
- 检测 `LlmCallAttempt.status=failed_unknown` 后把任务置为 `failed_unknown` 并拒绝自动重放，不调用 execute。
- 九个 wrapper 改为薄适配：`analysis`、`main_force`、`sector_analysis`、`dragon_tiger`、`portfolio`、`risk_analysis`、`portfolio_risk`、`us_research`、`news_collect`。外部 ARQ 参数只用于和持久化 `_args` 校验，业务输入以 `TaskRecord.input_snapshot` 为准。
- `portfolio`/`portfolio_risk` 仅短暂读取持仓快照后关闭 Session；`us_research` 不再调用带 commit 的 `save_report`；新闻采集拆出无 Session 的 fetch 阶段和 caller-owned `persist_news`，legacy `collect_news` facade 保留兼容提交行为。

## Task 6 Critical 关闭证据

1. 同 task 并发/重复 wrapper：unit 与 PostgreSQL 均证明只有一个 claim、一个 execute、一个 persist；活动 running delivery 和 terminal delivery 都 no-op。
2. inline 已执行但 outbox ack gap：测试保持 outbox `pending`，再次投递仍读取 terminal task 并 no-op；execute/persist 各一次。该行为不依赖 ARQ result retention 或 job ID。

## TDD RED

先运行旧 wrapper 行为测试，真实失败为 `assert 2 == 1`，日志显示同一 task 生成两个 report（`report_id=1/2`）。该 RED 暴露了旧 wrapper 无持久化 claim/idempotency guard；随后实现 runner 并迁移 wrapper。

## 验证

- `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_task_execution.py -q`：22 passed，2 warnings。
- `cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@localhost:5432/postgres PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/integration/test_task_execution_concurrency.py -q`：3 passed，覆盖 PostgreSQL 20 路同 task 恰 1 execute、20 路 started-attempt reclaim（execute=0）与旧 owner fencing。
- Task 1–7 focused（含 Task 6 duplicate/outbox）：54 passed，2 warnings（本轮 fix focused fresh）。
- Task 1–7 focused + 显式 PostgreSQL runner：33 passed，2 warnings（本轮 fix focused fresh）。
- `cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@localhost:5432/postgres PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q`：314 passed，21 warnings（fresh 全量）。
- `git diff --check`：通过；未提交密钥、数据库或构建产物。工作区原有 pycache、`.venv`、前端 `tsconfig.tsbuildinfo` 未纳入本任务提交。

## Fix Round 1 收口证据

- 旧 HEAD `6cbc1de2` 先复现 4 个 RED：heartbeat fencing/存储异常不会取消阻塞 execute、取消时关联 `failed_unknown` attempt 仍把任务写成普通 `failed`、新闻同步抓取阻塞事件循环。随后新增结构化 heartbeat/execute 并发等待，heartbeat 错误立即取消并 await execute；新闻 fetch 改为 `asyncio.to_thread`。
- `TaskRecord.status=failed_unknown` 保留审计语义；API 原样返回该终态，错误只使用稳定中文“模型调用结果未知，任务未自动重试”。前端集中 helper 将 `failed_unknown` 视作失败终态并停止轮询。
- 严格快照校验拒绝缺 key、类型强转和任何 delivery 值兜底；九个 wrapper 的业务输入均来自持久化 `_args`。最终 persist 也从 durable snapshot 取标识字段。
- reclaim 竞态：过期任务若存在 `LlmCallAttempt.status=started`，claim 在新 execute 前锁定并保守终结 attempt 为 `failed_unknown`，结算关联 reserved reservation，写唯一 `LlmUsage.failed_unknown`，TaskRecord 进入终态；因此旧 owner/new owner 均不会再次发起模型调用。
- Task 6 ack-gap 联动：真实 `OutboxDispatcher` 第一次 ack 持久化失败、第二次重投仍只触发一次 runner execute/persist；TaskRecord terminal claim 是唯一执行闸门。

## Fix Round 1 验证（fresh）

- `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_task_execution.py tests/services/test_outbox_dispatcher.py tests/services/test_task_submission.py tests/test_news.py tests/api/test_admin_llm.py -q`：53 passed，2 warnings。
- `cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@localhost:5432/postgres PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/integration/test_task_execution_concurrency.py tests/integration/test_task_submission_concurrency.py tests/services/test_task_execution.py tests/services/test_outbox_dispatcher.py -q`：31 passed，2 warnings；包含 PostgreSQL 20 路 duplicate、20 路 started-attempt reclaim（execute=0、唯一结算/usage）及旧 owner fencing。
- `cd frontend && npm test -- --run`：2 test files / 14 tests passed；`npm run build`：通过。
- `cd frontend && node tests/e2e/task-failed-unknown.mjs`：390px 与 1440px 注入 `failed_unknown`，Analysis/News 均停止轮询并显示中文错误，无 page/console error。

## 文件范围

新增：

- `backend/app/services/task_execution.py`
- `backend/tests/services/test_task_execution.py`
- `backend/tests/integration/test_task_execution_concurrency.py`
- 本报告

修改：

- 九个 `backend/app/tasks/*.py` wrapper
- `backend/app/services/news_collector.py`（按批准裁决，仅拆执行边界，未清理 Task 10 的 Mock/fallback 语义）
- `backend/tests/services/test_outbox_dispatcher.py`、`backend/tests/integration/test_task_execution_concurrency.py`
- 前端八个轮询页面、`frontend/src/utils/taskStatus.ts` 及其 Vitest 测试
- `frontend/tests/e2e/task-failed-unknown.mjs`（本地 route interception QA）

## 后续边界

- Task 8 仍需在每个结构化模型步骤前调用 `ctx.ensure_current()`，并在 context 上注入 `LlmExecutionService`；Task 7 已保留兼容 `runtime_config` 扩展字段。
- 新闻 `LLM_MOCK`/示例回退仍由 Task 10 负责清理；本任务未扩大业务语义。
