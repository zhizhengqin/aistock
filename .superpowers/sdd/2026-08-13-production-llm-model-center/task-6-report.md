# Task 6 报告：统一任务提交与事务 Outbox

## 结果

- 所有手动 API 与 APScheduler 定时任务统一经过 `TaskSubmissionService`。
- AI 任务在同一事务锁定 `llm_runtime_settings(id=1)`，校验 active、未删除、verified test success 及运行参数指纹，并把配置 ID 固定到 `TaskRecord`。
- 新闻采集标记 `requires_llm=False`，不读取默认模型，模型 ID 保持 `NULL`。
- 会员扣次、任务、事务 outbox 一次提交；任意异常统一 rollback。
- `OutboxDispatcher` 使用 `FOR UPDATE SKIP LOCKED`、批量上限 50、过期锁恢复、指数退避、中文脱敏错误和确定性 `_job_id=task:{task_id}`。
- worker 启动 outbox loop，停止时取消并等待 loop；API 不再直接连接 Redis。inline 仅在提交成功后 schedule，schedule 失败时保留 pending outbox。

## TDD / RED 证据

首轮真实行为测试先于实现执行：

```text
tests/services/test_task_submission.py::test_membership_check_and_consume_uses_caller_transaction
FAILED: membership.check_and_consume -> ensure_plans -> db.commit()
```

该失败暴露了计划初始化链路也会越过调用方事务边界；随后将 `ensure_plans/get_plan` 改为默认不提交，并保留显式 legacy helper。

## 验证

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_task_submission.py tests/services/test_outbox_dispatcher.py -q`：9 passed。
- `TEST_DATABASE_URL=postgresql+psycopg2://qinzz@localhost:5432/postgres PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/integration/test_task_submission_concurrency.py -q`：3 passed。覆盖默认切换与提交线性化、20 路会员并发（8 次成功且无超扣）、两 dispatcher 唯一逻辑 job；随机 disposable DB 在 finally 删除。
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/test_membership.py tests/test_main_force.py tests/test_sector.py tests/test_dragon_tiger.py tests/test_portfolio.py tests/test_risk_engine.py tests/test_news.py tests/test_us_research.py tests/test_worker_settings.py -q`：66 passed。
- `TEST_DATABASE_URL=postgresql+psycopg2://qinzz@localhost:5432/postgres PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q`：285 passed，21 warnings。
- `rg -n 'TaskRecord\\(' backend/app/api backend/app/tasks/scheduler.py`：无直接创建结果。
- `git diff --check`：无 whitespace 错误；PostgreSQL 临时数据库查询：0 残留。

## 兼容与判断

为保持既有会员 API 回归测试语义，仅在 `tests/test_membership.py` 的任务 API 测试 fixture 中加入 active + verified 默认模型；专门的“无默认模型”服务测试仍断言 503 且 task/usage/outbox 均为 0。非任务会员 CRUD 路径继续使用显式 legacy commit helper。

## 已知边界

- Task 执行包装器仍由 Task 7 负责；本任务只保存可供 dispatcher 重建调用的参数快照。
- 工作区原有 `.venv`、`__pycache__`、前端 `tsconfig.tsbuildinfo` 脏改动未纳入提交。
