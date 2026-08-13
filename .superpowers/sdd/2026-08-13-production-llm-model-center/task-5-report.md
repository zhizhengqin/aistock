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

## Review round2/5：来源分类与中断 candidate 恢复

在 `ecbc67db` 先补真实行为测试再实现。有效 RED 为：

- 使用真实 `LlmCallExecutor + ProviderClient + httpx.MockTransport` 返回 HTTP 400 `daily_limit` 时，已有真实 `bootstrap` attempt/供应商错误证据却被错误地判为 fatal（exit 1，期望 exit 0 并保留 draft/audit）。
- 使用真实 `TokenBudgetService` 在本地 `budget_locked`、reserve 前拒绝时，确认无 attempt 并返回 fatal（该新增行为测试先通过，作为来源分类对照）。
- 模拟首次 RuntimeError 后 cleanup persistence commit 失败，第二次 bootstrap 在旧实现中永久 no-op/draft，无法复用原 config；期望保留历史审计、创建新 run、同一 config 成功成为 default。

本轮修复：

- bootstrap 异常携带 `run_id/config_id` 查询精确关联的 `LlmCallAttempt`；稳定 provider code 只有在匹配 attempt 已离开 `started` 且 `error_code` 一致时才允许 exit 0。`llm_daily_limit_reached` 因此区分供应商 HTTP 响应（非 fatal）与本地额度锁（无 attempt，fatal）；`llm_transport_config`、任意非 `LlmError`、审计/数据库异常仍 fatal。
- advisory lock 内先识别唯一、精确显示名、`created_by IS NULL`、draft、未删除且非默认的旧 bootstrap-owned candidate；若其 bootstrap test run 没有 `started` 中的 in-flight 记录，则解密并复核 base URL、runtime fingerprint，恢复同一 config，保留旧 attempts/usages/tests 并新建 probe run。多候选、管理员模型、active/default/deleted、密钥或指纹不可验证均安全 no-op/fatal，不覆盖配置。
- cleanup 失败时不吞掉恢复信号：首次仍 exit 1、错误信息不含密钥；随后 best-effort 将旧 run 标为 `failed`，使下一次 bootstrap 能区分“中断”与“当前探测进行中”。race 场景中的 `started` run 保持 in-flight，第二进程不重复 probe。

Round2 focused/PG 验证：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/cli/test_llm_config_cli.py tests/cli/test_database_cli.py -q
```

```text
24 passed, 2 warnings
```

```bash
TEST_DATABASE_URL='postgresql+psycopg2://qinzz@localhost:5432/postgres' \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/integration/test_llm_bootstrap_lock.py \
  tests/integration/test_llm_database_cli.py -q
```

```text
5 passed, 16 warnings
```

Task1–5 focused（含 PG，round2 后）：`144 passed, 17 warnings`。

全 backend（显式 PostgreSQL admin/base URL）：`265 passed, 21 warnings`。

## Review round3/5：来源证据、probe 租约与历史恢复

### 有效 RED（基线 `9994d53b`）

先补充真实行为测试，再执行：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/cli/test_llm_config_cli.py -q
```

结果：`27 collected, 4 failed, 23 passed`。失败均为产品行为而非导入/测试基础设施问题：

- 真实 `LlmCallExecutor + ProviderClient + httpx.MockTransport` 返回 HTTP 200 但业务 JSON 形状错误时，旧实现错误返回 fatal 1，而应保留 success attempt、failed draft/run，返回 0 且 readiness 仍为 not-ready。
- 同一真实 executor 注入显式 resolver 抛 `socket.gaierror` 时，旧实现未将 `llm_dns_unavailable` 作为已完成 provider attempt 的非 fatal 结果。
- 候选唯一但旧 bootstrap run 为过期 `started` 时，旧实现永久 no-op，无法将 owner 标记丢失并恢复同一 config。
- 全部历史均为严格 bootstrap-owned、retired、deleted 配置时，旧实现永久 no-op，无法从当前 legacy env 创建下一候选；普通 deleted/admin 历史仍保持 no-op。

另：早期 fork 进程测试在 macOS 上出现 SIGSEGV（`exitcode=-11`），属于测试基础设施错误，不计入 RED；race helper 使用 `multiprocessing spawn`，并在 `finally` 中 terminate/join 所有已启动子进程。

### 修复语义

- `_probe_attempt_evidence` 返回同一 `config_id + bootstrap:{run_id}` 的结构化 `status/error_code`。`llm_probe_invalid` 仅在该 run 的真实 attempt 已 `success` 时非 fatal；DNS、provider 稳定错误及供应商 `llm_daily_limit_reached` 仅在 matching completed `failed/failed_unknown` attempt 时非 fatal；本地 transport/budget/audit/config/encryption 等 denylist 和任意非 `LlmError` 始终 fatal。
- bootstrap probe 使用 `asyncio.timeout(195s)`，超时写入稳定 `llm_probe_timeout`，保留 failed draft/run（退出 0）；租约失效窗口为 `195s + 15s grace`，时间统一按 UTC 处理。新增真实 executor 慢响应测试确认候选不被删除。
- advisory lock 内仅在短事务中处理 lease：新鲜 `started` run 仍 no-op；过期 run 行锁复核后原子写 `failed/llm_bootstrap_owner_lost`，再恢复同一 candidate。最终化固定 settings→candidate→run 锁序并 `populate_existing`，先复核 run 仍 `started`；旧 owner 晚到不能改写 failed 终态、激活 candidate 或修改 default。新增 late-owner fence 行为测试。
- 无未删除模型且历史全部严格 bootstrap-owned（显示名精确、`created_by IS NULL`、非默认、retired+deleted）时允许 legacy env 创建新 config，旧审计和软删除行保留；任意 admin/default/含糊历史仍 fail-closed。新增真实 executor transport fatal→软删除→修复后新 config 成功测试。

### Round3 验证

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/cli/test_llm_config_cli.py tests/cli/test_database_cli.py -q
```

结果：`32 passed, 2 warnings`。

```bash
TEST_DATABASE_URL='postgresql+psycopg2://qinzz@localhost:5432/postgres' \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/integration/test_llm_bootstrap_lock.py \
  tests/integration/test_llm_database_cli.py -q
```

结果：`5 passed, 16 warnings`；四个独立进程/连接 race、成功与失败 probe、settings-only 空库和 probe barrier 均通过。随机 disposable 数据库全部在 `finally` 中删除，扫描无残留。

Task1–5 focused（含 PG）：`151 passed, 7 warnings`；全 backend（显式 PostgreSQL admin/base URL）：`273 passed, 21 warnings`。

静态/边界检查：`sh -n backend/docker-entrypoint.sh`、CLI compileall、`git diff --check` 均通过；`LLM_MOCK` 在两个 Compose 文件中无匹配，worker 不含三个 legacy secret 字段；Compose config 使用命令行临时变量，无 `.env` 写入或 secret 展开打印。readiness 继续使用 no-autoflush/rollback 只读 scope，不解密、不调用 executor、不提交调用方 pending。

## Review round4/5：取消安全与 hard-timeout 账本终态

### 有效 RED（基线 `9d12d274`）

先补充真实延迟 provider 与真实 bootstrap executor 测试，再执行：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/services/llm/test_call_executor.py::test_provider_cancellation_settles_unknown_attempt_and_usage \
  tests/cli/test_llm_config_cli.py::test_bootstrap_real_executor_hard_deadline_keeps_failed_candidate -q
```

结果：`2 failed`。两项均暴露产品行为而非导入/测试基础设施问题：任务取消后旧 executor 没有写入 `llm_usage`，取消测试查询不到 usage；真实 bootstrap hard deadline 同样留下 provider attempt/reservation 未终态化，run 虽为 `llm_probe_timeout`，但没有 failed-unknown usage 证据。

### 修复语义

- `LlmCallExecutor` 在每次 physical provider await 处显式捕获 `asyncio.CancelledError`（BaseException），构造安全的 `llm_failed_unknown` 错误（`may_have_sent=True`、`confirmed_unsent=False`、不可重试）。
- 取消路径不再进入普通 retry 分支：同步以预留上界 `settle(..., unknown=True)`，将 attempt 写为 `failed_unknown` 并记录稳定错误码，再通过既有 `_persist_usage` 接口写入 `failed_unknown/llm_failed_unknown` usage；该终态化过程没有 await，数据库异常不被吞掉。
- 终态化成功后原样重新抛出 `CancelledError`。因此 CLI 的 `asyncio.timeout` 能安全转换为 `TimeoutError`，bootstrap 继续写入 `llm_probe_timeout` failed run 并以 exit 0 返回；真实 attempt 的精确 `bootstrap:{run_id}` step、settled reservation 和 failed-unknown usage 均保留。

### Round4 验证

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/services/llm/test_call_executor.py tests/cli/test_llm_config_cli.py -q
```

结果：`37 passed, 2 warnings`。

Task1–5 focused（显式 PostgreSQL admin/base URL）：`152 passed, 7 warnings`；全 backend（显式 PostgreSQL admin/base URL）：`274 passed, 21 warnings`。

PG race/migration 回归：`5 passed, 16 warnings`；所有 disposable PostgreSQL 库均在 `finally` 清理，扫描无残留。Shell/compile/diff/Compose/secret 边界沿用 round3 结果。

## 提交与边界

Round4 提交信息：

```text
fix(llm): settle cancelled provider attempts
```

未 amend、未 push、未部署；没有改写 `a7922919`、`ecbc67db`、`9994d53b` 或 `9d12d274`。`.venv`、`__pycache__`、构建产物和工作区原有改动均未纳入提交。测试中的 `httpx.MockTransport` 只替代外部 HTTP；产品 bootstrap/live-smoke 路径仍显式使用 Task 3 `LlmCallExecutor`，live-smoke 成功预算证据只接受 `settled`，readiness 不触发任何模型调用。

## 已知非阻塞差距

- 本机 PostgreSQL 为 15.18；Task 5 race/迁移行为使用同 PostgreSQL 驱动和 disposable 库验证，生产目标 PostgreSQL 16 仍由最终发布验收复核。
- 现有项目的 FastAPI/Pydantic/Alembic/ReportLab 弃用警告未在本任务扩大范围处理。
