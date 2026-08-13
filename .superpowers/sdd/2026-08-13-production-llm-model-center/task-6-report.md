# Task 6 报告：统一任务提交与事务 Outbox（fix round 1）

## 结果

- 所有手动 API 与 APScheduler 定时任务统一经过 `TaskSubmissionService`。
- AI 任务在同一事务锁定 `llm_runtime_settings(id=1)`，校验 active、未删除、verified test success 及运行参数指纹，并把配置 ID 固定到 `TaskRecord`。
- 新闻采集标记 `requires_llm=False`，不读取默认模型，模型 ID 保持 `NULL`。
- 会员扣次、任务、事务 outbox 一次提交；任意异常统一 rollback。
- `OutboxDispatcher` 使用 `FOR UPDATE SKIP LOCKED`、批量上限 50、过期锁恢复、指数退避、中文脱敏错误和确定性 `_job_id=task:{task_id}`。
- worker 启动 outbox loop，停止时取消并等待 loop；API 不再直接连接 Redis。inline 仅在提交成功后 schedule，schedule 失败时保留 pending outbox。
- verified test 现在同时校验 `LlmModelTestRun.model_config_id == LlmModelConfig.id`，拒绝跨配置复用相同 fingerprint 的测试记录。
- PG 竞态使用真实 `LlmConfigService.activate`（快速真实契约 executor）与 `TaskSubmissionService` 共同竞争 settings 锁；测试按实际首个锁持有者精确断言 old/new 模型选择。
- dispatcher PG 并发由两个独立线程、event loop、Session 同时 claim；sender 返回 `None` 仍视为 ARQ 逻辑投递成功并标记 delivered。

## TDD / RED 证据

首轮真实行为测试先于实现执行：

```text
tests/services/test_task_submission.py::test_membership_check_and_consume_uses_caller_transaction
FAILED: membership.check_and_consume -> ensure_plans -> db.commit()
```

该失败暴露了计划初始化链路也会越过调用方事务边界；随后将 `ensure_plans/get_plan` 改为默认不提交，并保留显式 legacy helper。

本轮修复新增跨配置 verified-test 行为测试，先移除 `model_config_id` 校验运行：

```text
tests/services/test_task_submission.py::test_verified_test_from_another_config_is_rejected_atomically
FAILED: DID NOT RAISE TaskSubmissionError
```

随后补上配置归属校验；该测试现已通过并确认 TaskRecord、UsageLog、TaskOutbox 均为 0 写入。

## 验证

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_task_submission.py tests/services/test_outbox_dispatcher.py -q`：12 passed。
- `TEST_DATABASE_URL=postgresql+psycopg2://qinzz@localhost:5432/postgres PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/integration/test_task_submission_concurrency.py -q`：3 passed。覆盖默认切换与提交线性化、20 路会员并发（8 次成功且无超扣）、两 dispatcher 唯一逻辑 job；随机 disposable DB 在 finally 删除。
- dispatcher ack 失败证据：enqueue 成功但 `_mark_success` 数据库事务失败时，outbox 保持 locked；过期恢复后以同一 `_job_id` 重投并最终 delivered，明确这是 Task 7 执行层需要幂等 claim 的边界。
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_membership.py tests/test_main_force.py tests/test_sector.py tests/test_dragon_tiger.py tests/test_portfolio.py tests/test_risk_engine.py tests/test_news.py tests/test_us_research.py tests/test_worker_settings.py -q`：66 passed。
- `TEST_DATABASE_URL=postgresql+psycopg2://qinzz@localhost:5432/postgres PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q`：289 passed，21 warnings。
- `rg -n 'TaskRecord\\(' backend/app/api backend/app/tasks/scheduler.py`：无直接创建结果。
- `git diff --check`：无 whitespace 错误；PostgreSQL 临时数据库查询：0 残留。

## 兼容与判断

为保持既有会员 API 回归测试语义，仅在 `tests/test_membership.py` 的任务 API 测试 fixture 中加入 active + verified 默认模型；专门的“无默认模型”服务测试仍断言 503 且 task/usage/outbox 均为 0。非任务会员 CRUD 路径继续使用显式 legacy commit helper。

## 已知边界

- Task 执行包装器仍由 Task 7 负责；本任务只保存可供 dispatcher 重建调用的参数快照。
- 工作区原有 `.venv`、`__pycache__`、前端 `tsconfig.tsbuildinfo` 脏改动未纳入提交。

## Critical open-by-design（交给 Task 7）

以下两项本轮不声称关闭，也未修改任务 wrapper：

1. enqueue 成功后 `_mark_success` 数据库失败会产生同一 job ID 的再次投递；Task 7 runner 必须以持久化 claim/幂等状态防止任务执行重复。
2. inline `create_task` 成功后 ack delivered 事务失败时，任务已启动且 outbox 保持 pending；Task 7 runner 必须识别已启动/完成状态，避免 pending 重投造成重复执行。
