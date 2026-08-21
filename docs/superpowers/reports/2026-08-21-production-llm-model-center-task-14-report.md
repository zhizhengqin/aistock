# Task 14：生产大模型中心文档与全链验收报告

日期：2026-08-21（Asia/Phnom_Penh）

## RED 证据

清理前运行：

```bash
rg -n 'LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|Mock 模式' backend/app frontend/src deploy .github
```

结果：仅发现 `.github/workflows/deploy.yml` 的生产 bundle guard 自身包含 `Mock 模式` 字面量，
不是运行时调用；该 guard 已改为只检查测试密钥。随后追加 hidden deploy 扫描，发现
`deploy/.env.example` 的旧 `LLM_MOCK` 模板，获批准纳入本任务边界后已删除。没有人为制造失败。

## 变更摘要

- `TODOS.md` 增加四个 LLM 生产化里程碑，标记 Tasks 1–14 的本地完成状态；生产部署仍待授权，
  明确不加入模块路由、故障转移、统一网关或评测平台 P3。
- 架构说明补齐 PostgreSQL 额度账本、reservation/settlement、execution-token fencing、
  outbox、`failed_unknown`、三供应商配置、密钥环“双读单写”、bootstrap/readiness/smoke、
  fail-closed 维护窗口、备份回滚及 90 天 payload 清理/恢复语义。
- 部署手册与用户手册改为真实模型路径：删除旧模拟运行指引，说明 DeepSeek/Kimi/通义千问、
  API Key 脱敏、每日 Token 锁停、密钥轮换与管理员操作。
- `DEEPSEEK_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL` 保留为一次观察发布 bootstrap 输入，并标注
  观察期后另开任务移除；删除 `LLM_MOCK` 配置模板/运行文案。
- Compose/Actions 保持 Task 12 的 fail-closed 两阶段 gate；bundle guard 不再含禁用字面量。

## GREEN 与静态验证

| 命令 | 结果 |
|---|---|
| `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q` | **357 passed, 26 skipped, 4 warnings**（24.20s） |
| `cd frontend && npm test -- --run` | **3 files / 29 tests passed**（2.23s） |
| `cd frontend && npm run build` | **通过**；TypeScript 与 Vite，109 modules transformed |
| `cd frontend && npm run test:e2e:llm` | **通过**；两视口 `failures: []` |
| `POSTGRES_PASSWORD=local-check JWT_SECRET=local-jwt docker compose -f deploy/docker-compose.yml config` | **通过** |
| `node --check frontend/tests/e2e/llm-model-center.mjs` | **通过** |
| Ruby YAML 解析 `.github/workflows/deploy.yml` | **通过** |
| `git diff --check` | **通过** |

### 真实 PostgreSQL/Redis 集成证据

使用本机 PostgreSQL（`qinzz@127.0.0.1:5432/postgres`）和 Redis 7（`127.0.0.1:6379/15`）运行：

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg2://qinzz@127.0.0.1:5432/postgres \
TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/integration -q
```

结果：**27 passed, 30 warnings**（12.37s）。其中包含：

- Alembic upgrade → downgrade → upgrade：`test_llm_migration.py`、migration cycle 通过；
- bootstrap 进程竞态：4 项通过，保持单 settings/默认配置；
- PostgreSQL 额度跨 Session、execution-token fencing：`test_llm_recovery.py` 通过；
- 隔离随机端口 `redis-server` 终止/重启后客户端自动重连：通过，未操作共享 6379 服务；
- outbox 行约束、重复 delivery、固定 ARQ job ID、API/Worker 读取同一默认配置：4 项通过；
- readiness/database CLI 与 usage/outbox/stale-recovery/cleanup query-plan：通过。

本地集成覆盖了 API/Worker 数据一致性和 Redis restart；没有启动生产 Docker API/Worker 进程做
人工重启，也没有执行真实供应商 smoke、Kimi/Qwen 报告或线上流量恢复。这些属于需要用户明确授权
的生产维护项，不以本地测试冒充完成。

## Forbidden 与 secret 扫描

```bash
rg -n 'LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|Mock 模式' backend/app frontend/src deploy .github
rg --hidden -n 'LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|Mock 模式' deploy
```

两条命令均无输出。密钥扫描命令：

```bash
rg -n '(sk-[A-Za-z0-9_-]{8,}|LLM_CONFIG_ENCRYPTION_KEYS=.*[^<])' --glob '!*.example' .
```

结果只包含测试夹具中的 `sk-*-secret`、前端 E2E 测试密钥、设计/计划中的示例和
`DEPLOY.md` 的空配置占位符；逐项确认没有真实密钥、生产 bundle 或运行日志泄露。未 stage
任何测试数据库、截图、`dist`、`pyc`、`.venv` 或 `tsconfig.tsbuildinfo`。

## 未执行的生产步骤

未获得生产部署授权，因此未备份线上 PostgreSQL/`deploy/.env`，未停止线上任务或流量，未运行
线上 migrator/readiness/DeepSeek live-smoke，未接入并真实调用 Kimi/Qwen，未启用严格三供应商
smoke，也未执行线上 API/Worker/Redis 重启和回滚。计划中对应复选框保持未勾选；主 Sol 的
gstack `/review`、`/qa`、`/ship` 已在 Task 14 提交后完成本地关卡：审查 CLEAN、双视口
`failures: []`、本地 ship readiness 通过；未 push、未建 PR、未部署。
