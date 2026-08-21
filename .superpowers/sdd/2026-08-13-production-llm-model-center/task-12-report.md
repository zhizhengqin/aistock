# Task 12 报告：真实基础设施 CI、Playwright 与发布 gate

## RED

先新增两个 PostgreSQL/Redis 集成测试文件和管理员模型中心 Playwright 脚本，再按计划命令运行：

```text
cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@127.0.0.1:5432/postgres TEST_REDIS_URL=redis://127.0.0.1:6379/15 .venv/bin/pytest tests/integration/test_outbox_postgres.py tests/integration/test_llm_recovery.py -q
```

结果：收集 6 项，6 errors；预期 fixture `postgres_engine`、`postgres_session_factory`、`redis_client`、`migration_cycle` 尚未接线。

```text
cd frontend && npm run test:e2e:llm
```

结果：预期失败，`test:e2e:llm` 脚本尚未配置。

## 实现与 GREEN

- 新增 integration `conftest.py`：显式要求 PostgreSQL/Redis URL；每个测试创建受严格正则保护的随机 PostgreSQL 数据库并在结束时只删除自身；运行真实 Alembic head；提供 Redis 连接、升级→降级→升级和临时 `redis-server` 重启 fixture。临时 Redis 使用随机空闲端口、临时目录、无持久化，绝不操作共享 6379 服务。
- `test_outbox_postgres.py` 覆盖 task outbox 唯一约束/partial index、重复 delivery、固定 `task:{task_id}` ARQ job ID、API/worker 同读 runtime default、Redis 重连和迁移循环保留 legacy task。
- `test_llm_recovery.py` 覆盖预算跨 Session 持久化、execution-token fencing、隔离 Redis 进程终止/重启后客户端重连。
- `backend/tests/conftest.py` 增加 hermetic SQLite 加密且 verified 的模型配置 fixture；unit lane 不读取真实 PostgreSQL/Redis，也不设置 `LLM_MOCK`。
- `llm-model-center.mjs` 仅拦截浏览器 `/api/*`，覆盖 1440×900 和 390×844：DeepSeek/Kimi/Qwen 自由 model ID/Base URL 添加、真实 route body 测试→清空 Key→保存、saved test→enable→default、默认删除禁用、409 中文错误/refetch、unknown price、锁停 banner/预留与结算、中文原因解锁、reload persistence、横向溢出和 dialog 确认；脚本自行启动并在 `finally` 关闭 Vite。
- workflow 增加 PostgreSQL 16/Redis 7 service health checks、显式 integration lane、Redis 7 binary 校验、Playwright Chromium 安装和 production bundle secret/“Mock 模式” guard。
- 发布 gate 分为：停止旧 API/worker/nginx（失败保持 fail-closed）→构建→唯一 migrator→API bootstrap/内部 health/readiness→迁移后的 active verified DeepSeek live smoke→strict=false 时记录 Kimi/Qwen onboarding incomplete；`LLM_STRICT_THREE_PROVIDER_SMOKE=true` 时另行强制 Kimi/Qwen live smoke→启动 worker/nginx→外部 `/api/health`。

目标集成命令：

```text
cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@127.0.0.1:5432/postgres TEST_REDIS_URL=redis://127.0.0.1:6379/15 PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/integration/test_outbox_postgres.py tests/integration/test_llm_recovery.py -q
```

结果：7 passed，9 warnings。

全 integration lane：

```text
cd backend && TEST_DATABASE_URL=postgresql+psycopg2://qinzz@127.0.0.1:5432/postgres TEST_REDIS_URL=redis://127.0.0.1:6379/15 PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/integration -q
```

结果：25 passed，26 warnings；真实 PostgreSQL 临时库均完成 upgrade/downgrade/upgrade 并清理，Redis restart 只使用随机端口临时进程。

后端回归：

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
```

结果：353 passed，24 skipped，4 warnings；未设置 TEST_DATABASE_URL 的既有 PostgreSQL integration 按仓库规则跳过。

前端回归与 E2E：

```text
cd frontend && npm test -- --run && npm run build && npm run test:e2e:llm
```

结果：Vitest 3 files/29 passed；Vite/TypeScript build 成功；Playwright 1440×900 与 390×844 均通过，`failures: []`。

## 静态检查

- production `frontend/dist` 未匹配 `sk-super-secret` 或“Mock 模式”。
- `rg` 禁止生产匹配（`LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|_safe_chat|allow_fallback|from app.core.llm import chat|await chat(`）通过。
- Playwright 脚本 `node --check`、workflow YAML 解析和 `git diff --check` 通过。

## 文件与边界

仅修改/创建 Task 12 批准文件：`.github/workflows/deploy.yml`、`backend/tests/conftest.py`、`backend/tests/integration/conftest.py`、两个 integration 测试、Playwright 脚本、`frontend/package.json`、本报告。未修改生产后端、数据库模型/迁移、Docker Compose、CLI 或 Task 13+；未推送、部署或创建 PR。

剩余风险：发布 gate 只在 GitHub Actions 与京东云真实环境执行，当前本机未触发远端部署；strict 三供应商 smoke 依赖管理员先完成 Kimi/Qwen 加密配置、真实测试并将 workflow variable `LLM_STRICT_THREE_PROVIDER_SMOKE` 设为 `true`。
