# Task 5 实施报告：安全 bootstrap、readiness、live-smoke 与 migrator

## 结果

已完成批准计划 Task 5 及 review round1/5 加固，基线为 `8f8baa91`，首个实现为 `a7922919`，本轮新增提交为 `fix(llm): harden bootstrap and migration gates`。新增一次性旧 DeepSeek 环境变量引导、只读就绪检查、显式三供应商 live-smoke、Alembic 精确 head 检查和 Compose one-shot migrator。API entrypoint 不再执行迁移，worker 不执行 bootstrap；Compose 中已移除 `LLM_MOCK`。

## 变更文件（仅 Task 5 ownership）

- `backend/app/cli/__init__.py`
- `backend/app/cli/llm_config.py`
- `backend/app/cli/database.py`
- `backend/docker-entrypoint.sh`
- `deploy/docker-compose.yml`
- `deploy/docker-compose.dev.yml`（仅检查，无需额外改动）
- `backend/tests/cli/test_llm_config_cli.py`
- `backend/tests/cli/test_database_cli.py`
- `backend/tests/integration/test_llm_bootstrap_lock.py`
- `backend/tests/integration/test_llm_database_cli.py`

## TDD 证据

先写 CLI 行为测试，再执行：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/cli/test_llm_config_cli.py tests/cli/test_database_cli.py -q
```

真实行为 RED（在 `a7922919`，不是模块缺失）：新增 fatal 分流测试中 `RuntimeError`、`llm_daily_limit_reached`、`llm_transport_config`、`llm_budget_locked` 错误地返回 0；审计异常向外抛出；readiness 提交调用方 pending 对象；live-smoke 将 `released` reservation 当作成功；worker 仍携带三项旧 bootstrap env。修复后上述测试全部通过。最初历史测试收集时出现的 `ModuleNotFoundError` 不是最终 RED，未作为行为证据。

PG race 的第一次尝试使用 `fork`，macOS 上 psycopg2 被多线程父进程继承，子进程触发 SIGSEGV（`exitcode=-11`）；这是测试基础设施错误，不是产品行为 RED。改为 `multiprocessing spawn`，每个子进程独立创建 SQLAlchemy engine/session pool 后，三个 race 场景全部通过。

race helper 用 `try/finally` terminate/join 所有已启动子进程，异常路径不遗留 worker。

## 行为覆盖

### bootstrap

- 空库、无旧 Key：事务内 advisory lock `pg_advisory_xact_lock(hashtext('aistock_llm_bootstrap_v1'))`，幂等创建 `llm_runtime_settings(id=1)`，不建配置、不调用上游。
- 空库、有旧 DeepSeek Key：显示名固定为“DeepSeek 环境变量迁移”，加密信封落库；真实探测经过 `LlmCallExecutor(operation_type="bootstrap")`，成功才 active/default。
- 上游探测失败：保留 draft 候选、失败测试记录和真实 attempt/usage 账本，写脱敏管理员通知，bootstrap 退出码为 0；readiness 仍失败。
- 仅稳定的上游错误码（auth/model/quota/rate/timeout/invalid_json/unavailable/failed_unknown/response-too-large 等）走上述非 fatal 分支；本地预算、transport、加密/配置、数据库/审计和任意非 `LlmError` 均返回 1。fatal 发生在真实调用之后时只软删除不可用候选并保留 attempt/usage/reservation/未知结果证据；无调用证据时才删除本次孤立 candidate/test，下一次 bootstrap 可重试且不会被旧 draft 阻塞。
- probe 结束后短事务固定按 settings→candidate 加锁并 `populate_existing` 复核版本、指纹、draft/未删除、测试结果和模型集合所有权；管理员在网络等待期间创建/激活/修改/删除配置时保留 probe 审计、返回中文 no-op/conflict，不覆盖管理员默认或状态。PG barrier 测试覆盖该场景。
- 已有任意模型：第二次 empty-model 检查直接 no-op，绝不读取/覆盖旧环境变量。
- 两进程 race：settings 至多一行、候选/测试/默认至多一套，probe 最多一次；成功与失败两种上游结果均覆盖。

### readiness

只读查询默认配置、active/未删除状态、验证测试 success、测试指纹与当前运行指纹；专用 no-autoflush/rollback scope 不解密 API Key、不提交调用方 pending、不创建 attempt/usage、不调用供应商，仅输出状态和脱敏配置名。

### live-smoke

命令必须显式提供 `--provider deepseek|kimi|qwen --model-config-id UUID`，供应商与配置不匹配时在 executor 前失败。匹配时通过真实 `LlmCallExecutor(operation_type="live_smoke")`，使用 `ProviderClient + httpx.MockTransport` 仅替代外部网络，在测试中验证：结构化业务 Schema、`completion_tokens <= max_output_tokens`、独立 `llm_call_attempts`、`llm_usage`、settled reservation 证据，以及输出不含密钥/body。

### migrator / entrypoint / Compose

- `python -m app.cli.database migrate` 执行 `alembic upgrade head`，随后比较完整 `MigrationContext.get_current_heads()` 与 `ScriptDirectory.get_heads()` 集合。
- `python -m app.cli.database wait-for-head` 同样比较完整 head 集合，连接失败或版本不符返回中文非零结果。
- `migrator` 为 `restart: "no"` 的 one-shot 服务；API/worker 依赖 `service_completed_successfully`。API 执行 `bootstrap -> uvicorn`，worker 执行 `wait-for-head -> arq`。
- `DEEPSEEK_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL` 仅保留在 API 作为明确标注的 observation-release deprecated bootstrap input；worker env 不再携带三项，避免扩大 secret 暴露面；没有新增明文 secret 默认值。

## 验证命令与结果

CLI focused：

```text
20 passed, 2 warnings
```

Task5 PG bootstrap/barrier + real migration：

```bash
cd backend
TEST_DATABASE_URL='postgresql+psycopg2://qinzz@localhost:5432/postgres' \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/integration/test_llm_bootstrap_lock.py \
  tests/integration/test_llm_database_cli.py -q
```

```text
5 passed, 16 warnings
```

迁移集成先在全新 disposable 库执行 CLI `run_migrate` 到全部 head；另一个全新 disposable 库真实 `alembic upgrade <previous_revision>`，确认 `wait-for-head` 非零，再由 `run_migrate` 应用剩余迁移并恢复 exact heads。每个库均随机命名、受保护、`finally` terminate/drop，URL 仅使用 admin/base，环境变量最终恢复。

Task 5 PostgreSQL disposable race：

```bash
TEST_DATABASE_URL='postgresql+psycopg2://qinzz@localhost:5432/postgres' \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/integration/test_llm_bootstrap_lock.py -q
```

```text
4 passed, 6 warnings
```

Task 1–5 focused（含 PG）：

```bash
TEST_DATABASE_URL='postgresql+psycopg2://qinzz@localhost:5432/postgres' \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/cli/test_llm_config_cli.py tests/cli/test_database_cli.py \
  tests/integration/test_llm_bootstrap_lock.py \
  tests/services/llm/test_crypto.py tests/services/llm/test_http_client.py \
  tests/models/test_llm_models.py tests/services/llm/test_providers.py \
  tests/services/llm/test_url_security.py tests/services/llm/test_provider_client.py \
  tests/services/llm/test_budget.py tests/services/llm/test_call_executor.py \
  tests/integration/test_llm_budget_concurrency.py \
  tests/services/llm/test_config_service.py tests/api/test_admin_llm.py \
  tests/integration/test_llm_config_concurrency.py tests/test_admin.py -q
```

```text
140 passed, 17 warnings
```

全 backend（显式 disposable PostgreSQL 作为 admin/base URL；各集成测试自行创建并 finally 删除随机受保护数据库）：

```bash
TEST_DATABASE_URL='postgresql+psycopg2://qinzz@localhost:5432/postgres' \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
```

```text
261 passed, 21 warnings
```

Shell / Compose / 静态检查：

```bash
sh -n backend/docker-entrypoint.sh
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m compileall -q backend/app/cli
rg -n 'LLM_MOCK' deploy/docker-compose.yml deploy/docker-compose.dev.yml
git diff --check -- backend/app/cli backend/docker-entrypoint.sh deploy backend/tests/cli backend/tests/integration/test_llm_bootstrap_lock.py
git diff --check -- backend/tests/integration/test_llm_database_cli.py
```

结果：shell/compile/diff clean，`LLM_MOCK` 无匹配；worker compose service 不含三项旧 bootstrap env。使用命令行临时变量（未创建或修改 `.env`）验证：

```bash
POSTGRES_PASSWORD=compose-test JWT_SECRET=compose-jwt \
LLM_CONFIG_ENCRYPTION_KEY_ID=compose-current \
LLM_CONFIG_ENCRYPTION_KEYS='{"compose-current":"YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYg=="}' \
docker compose -f deploy/docker-compose.yml config
docker compose -f deploy/docker-compose.dev.yml config
```

两个 Compose 配置均成功；dev 文件仅有 Docker Compose 关于顶层 `version` 已废弃的既有警告。临时 PostgreSQL 库扫描结果为空：

```bash
psql -h localhost -p 5432 -d postgres -Atqc \
  "select datname from pg_database where datname like 'aistock_llm_bootstrap_test_%' or datname like 'aistock_llm_config_test_%' or datname like 'aistock_llm_budget_test_%' or datname like 'aistock_migration_test_%';"
```

无残留数据库。

## 提交与边界

本轮提交信息：

```text
fix(llm): harden bootstrap and migration gates
```

未 amend、未 push、未部署；没有改写 `a7922919`。`.venv`、`__pycache__`、构建产物和工作区原有改动均未纳入提交。测试中的 `httpx.MockTransport` 只替代外部 HTTP；产品 bootstrap/live-smoke 路径仍显式使用 Task 3 `LlmCallExecutor`，live-smoke 成功预算证据只接受 `settled`，readiness 不触发任何模型调用。

## 已知非阻塞差距

- 本机 PostgreSQL 为 15.18；Task 5 race/迁移行为使用同 PostgreSQL 驱动和 disposable 库验证，生产目标 PostgreSQL 16 仍由最终发布验收复核。
- 现有项目的 FastAPI/Pydantic/Alembic/ReportLab 弃用警告未在本任务扩大范围处理。
