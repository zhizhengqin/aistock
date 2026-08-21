# 生产级大模型中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Each implementation delegation must use the project Luna role selected by `AGENTS.md`, with `fork_turns="none"` and a complete task package containing OBJECTIVE, FILES AND OWNERSHIP, INTERFACES, CONSTRAINTS, VERIFICATION, and RETURN.

**Goal:** 把当前仅支持单个 DeepSeek、运行期内存改配置且带生产 Mock 的实现，升级为可保存、真实测试并安全切换 DeepSeek、Kimi、通义千问的生产级大模型中心。

**Architecture:** PostgreSQL 是配置、默认模型、任务模型锁、事务 outbox、调用尝试和每日 Token 账本的唯一事实来源；Redis 只承担 ARQ 队列与缓存。所有建任务入口经过 `TaskSubmissionService`，所有真实模型 HTTP 请求先经过通用 `LlmCallExecutor` 计额与审计，业务任务再由任务作用域 `LlmExecutionService` 提供幂等和 Schema 校验；API 与 worker 共享同一套数据库配置和进程级 HTTP 客户端。

**Tech Stack:** FastAPI 0.141、Pydantic 2.13、SQLAlchemy 2.0、Alembic、PostgreSQL 16、Redis 7/ARQ、httpx 0.28、cryptography 49.0.0、React 18、TypeScript、Vite、Vitest、Testing Library、Playwright、Docker Compose、GitHub Actions。

## Global Constraints

- 严格遵守已确认规格：`docs/superpowers/specs/2026-08-13-production-llm-model-center-design.md`。
- 本计划的工程评审决定以 PostgreSQL 原子预留/结算取代规格草案中的 Redis 额度账本；这是用户在 `/plan-eng-review` 中确认的安全收敛，Redis 不再是额度事实来源。
- 产品代码彻底删除 `LLM_MOCK`、`MOCK_RESPONSES` 和固定 AI 假报告；测试中的 `httpx.MockTransport` 只存在于测试文件。
- 不引入 LangChain、LiteLLM、独立模型网关、自动故障转移、按模块路由或自动评测平台。
- API Key 不得出现在 API 响应、DOM、日志、异常正文、截图、Git 或数据库明文字段中。
- 所有用户可见文案和错误为中文；代码标识符、注释和 Conventional Commit 使用英文。
- 应用侧 UUID 使用 `String(36)`，保证 PostgreSQL 与 SQLite 快速单测兼容。
- 所有网络调用都在数据库短事务之外；任何报告和任务成功状态必须同事务提交。
- 每项任务先写失败测试，再写最小实现；每个里程碑完成后运行对应完整测试。
- 实施时保留工作区已有改动，不覆盖 `AGENTS.md`、`docs/README.md` 等用户未提交内容。
- 未经用户另行明确授权，不 push、不部署生产环境。

## NOT in Scope

- 默认模型故障后的静默自动切换。
- 不同分析模块使用不同模型。
- 模型效果排行榜、供应商账单对账、统一 AI Gateway。
- 普通用户管理模型。
- 将供应商模型 ID 限制成固定下拉列表。
- 本次直接执行第二阶段破坏性清理迁移；观察期通过后另开任务删除弃用环境变量和旧兼容列。

## What Already Exists

- `backend/app/core/llm.py` 已有轻量 OpenAI 兼容 HTTP 调用和 Token 成本计算，但每次调用新建客户端、用环境变量选模型、另开 Session 记账，并在无 Key 时返回 Mock。
- `backend/app/api/admin.py` 与 `frontend/src/pages/Admin.tsx` 已有单模型设置页，但只修改当前进程的 `settings`，重启后恢复 `.env`。
- `backend/app/api/tasks.py`、`m3.py`、`m4.py`、`m5.py` 和 `tasks/scheduler.py` 各自创建任务，事务、会员次数和投递行为不一致。
- 九个 ARQ 任务包装器（含纯数据新闻采集）和七个 orchestrator 已存在；多个 `_safe_chat` 会吞掉模型错误并继续拼装结果。
- `backend/tests/conftest.py` 提供 SQLite 和 fakeredis 快速夹具；GitHub Actions 尚未提供 PostgreSQL/Redis 集成服务。
- 前端已有 Vitest，现有 E2E 只有 `frontend/tests/e2e/mobile-responsive.mjs`，没有大模型中心交互覆盖。

## Interfaces and State Flow

```text
Admin UI
  -> /api/admin/llm-models*
  -> LlmConfigService -> PostgreSQL encrypted configs + verified tests
                           |
User/API/Scheduler          v (row lock, same transaction)
  -> TaskSubmissionService -> TaskRecord + UsageLog + TaskOutbox
                                               |
                                    OutboxDispatcher -> ARQ task:{task_id}
                                               |
                                    TaskExecutionRunner
                                               |
                                    LlmExecutionService
                                     |
                                     v
                                    LlmCallExecutor
                                     | budget reserve (PostgreSQL)
                                     | ProviderRegistry + shared httpx client
                                     | schema validation + attempt audit
                                     v
                             report + task success (one transaction)
```

稳定接口在实现期间不得由执行者自行改变：

```python
class Provider(StrEnum):
    DEEPSEEK = "deepseek"
    KIMI = "kimi"
    QWEN = "qwen"

@dataclass(frozen=True)
class LlmRuntimeConfig:
    config_id: str | None  # only unsaved admin probes may be None
    provider: Provider
    display_name: str
    model_name: str
    base_url: str
    api_key: str
    credential_version: str
    max_output_tokens: int
    input_price_micro_yuan_per_million: int | None
    output_price_micro_yuan_per_million: int | None
    runtime_fingerprint: str

@dataclass(frozen=True)
class TaskSubmission:
    task_type: str
    user_id: int | None
    feature: str | None
    feature_cost: int
    args: dict[str, object]
    input_snapshot: dict[str, object]
    prompt_version: str
    requires_llm: bool = True

class LlmExecutionService:
    async def execute_json(
        self,
        *,
        task_id: int,
        step_key: str,
        messages: list[dict[str, str]],
        output_type: type[T],
        prompt_version: str,
        temperature: float = 0.3,
    ) -> T: ...

class LlmCallExecutor:
    async def call(
        self,
        *,
        runtime_config: LlmRuntimeConfig,
        operation_type: Literal["task", "admin_probe", "bootstrap", "live_smoke"],
        step_key: str,
        messages: list[dict[str, str]],
        task_id: int | None = None,
    ) -> ProviderResult: ...
```

## Failure Modes and Required Behavior

| Failure | Required behavior |
|---|---|
| AI 任务无已验证默认模型 | `requires_llm=True` 的建任务返回 503/`llm_default_unavailable`，不扣会员次数、不写孤儿任务；纯数据任务照常创建且 `model_config_id=NULL` |
| API Key 解密失败或缺少历史 Key | 任务失败并通知管理员，响应与日志不含密文或 Key |
| 401/403、模型不存在 | 不重试，中文失败，不保存半成品报告 |
| 429、5xx、确认未发送的连接失败 | 最多重试两次；每个真实 HTTP 请求独立预留并写 `llm_call_attempts` |
| 非法 JSON/Schema 不符 | 允许一次 JSON 修正调用；仍失败则整个任务失败 |
| 请求发送后超时/断连 | 记 `failed_unknown`，按预留上界保守结算，不自动重放 |
| 实际 usage 超过预留 | 结算实际值并把全局账本锁停，管理员解除前拒绝新调用 |
| worker 在入队后崩溃 | 固定 ARQ job id `task:{task_id}` 保证重复投递不重复执行 |
| worker 在模型响应后、落库前崩溃 | 成功步骤按 `(task_id, step_key)` 复用；未知状态不重发 |
| 激活与建任务并发 | 锁同一 `llm_runtime_settings` 行，提交先后线性化 |
| Base URL 指向内网/重定向/环境代理 | 拒绝 URL 或调用；`trust_env=False`、禁止重定向、只允许公共 A/AAAA |
| bootstrap 上游测试失败 | 保存失败候选并通知管理员；API 可启动，但 readiness 返回失败 |

## Milestones and Worktree Parallelization

1. **M1 基础底座（Tasks 1–5，串行）**：类型、密钥、数据模型、供应商协议、统一预算/调用审计、后台 API、bootstrap。
2. **M2 任务与运行时（Tasks 6–8，串行为主）**：先统一提交/outbox，再统一 runner，最后接任务级幂等与结构化校验。
3. **M3 业务迁移（Tasks 9–10）**：Task 9 固定输出 Schema 后，分析域可按 orchestrator 文件边界分发给不同 Luna worker；任何 worker 不得同时编辑 `core/llm.py` 或公共 schema。
4. **M4 管理端与交付（Tasks 11–14）**：API DTO 冻结后，Task 11 前端可以与 Task 13 清理/索引并行；Task 12 E2E 和 Task 14 全链验收必须最后串行。

推荐 worktree：`codex/llm-center-foundation` 完成 M1/M2；业务迁移可拆 `codex/llm-center-analysis` 与 `codex/llm-center-research`，最后合回 foundation；前端在 `codex/llm-center-admin-ui`。若使用共享当前工作树，只允许一次一个实现 worker 写文件。

---

## Task 1: 建立模型领域类型、密钥信封与共享 HTTP 客户端

**Files:**
- Create: `backend/app/services/llm/__init__.py`
- Create: `backend/app/services/llm/types.py`
- Create: `backend/app/services/llm/errors.py`
- Create: `backend/app/services/llm/crypto.py`
- Create: `backend/app/services/llm/http_client.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/tasks/queue.py`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/services/llm/test_crypto.py`
- Create: `backend/tests/services/llm/test_http_client.py`

**Interfaces:** Produces `Provider`, `ModelLifecycle`, `LlmRuntimeConfig`, `LlmError`, `CredentialEnvelope`, `encrypt_api_key`, `decrypt_api_key`, `get_llm_http_client`, `close_llm_http_client`. Consumes only Pydantic settings and `httpx.AsyncClient`.

- [ ] Add `cryptography==49.0.0`; add settings `LLM_CONFIG_ENCRYPTION_KEY_ID: str` and `LLM_CONFIG_ENCRYPTION_KEYS: dict[str, str]` parsed from JSON, with production validation that the write ID exists and every decoded AES key is 32 bytes.
- [ ] Write failing envelope tests covering round-trip, wrong AAD, tampered ciphertext, missing historical key, dual-read/single-write rotation, and redacted exceptions.

```python
def test_envelope_rejects_wrong_config_id(keyring):
    envelope = encrypt_api_key("sk-secret", config_id="cfg-a", provider=Provider.KIMI, keyring=keyring)
    with pytest.raises(LlmCredentialError) as exc:
        decrypt_api_key(envelope, config_id="cfg-b", provider=Provider.KIMI, keyring=keyring)
    assert "sk-secret" not in str(exc.value)
```

- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_crypto.py -q`; expect collection/import failure because `app.services.llm.crypto` does not exist.
- [ ] Implement AES-256-GCM with a random 96-bit nonce and exact AAD `v1|{config_id}|{provider}`. Persist Base64 fields in `CredentialEnvelope`; never implement a decrypt-to-log helper.
- [ ] Write failing lifecycle tests proving one client instance is reused per process, limits/timeouts are set, `trust_env=False`, redirects are disabled, and close is idempotent.
- [ ] Implement one process-scoped `httpx.AsyncClient` initialized by FastAPI lifespan and ARQ startup, closed by both shutdown hooks. Use `Limits(max_connections=20, max_keepalive_connections=10)` and `Timeout(connect=5, read=60, write=10, pool=5)`.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_crypto.py tests/services/llm/test_http_client.py -q`; expect all pass.
- [ ] Commit: `git add backend/app/services/llm backend/app/core/config.py backend/app/main.py backend/app/tasks/queue.py backend/requirements.txt backend/tests/services/llm && git commit -m "feat(llm): add secure runtime foundation"`

## Task 2: 增加生产数据模型与第一阶段兼容迁移

**Files:**
- Create: `backend/app/models/llm_config.py`
- Create: `backend/app/models/llm_execution.py`
- Create: `backend/app/models/task_outbox.py`
- Modify: `backend/app/models/task_record.py`
- Modify: `backend/app/models/llm_usage.py`
- Modify: `backend/alembic/env.py`
- Create: `backend/alembic/versions/20260813_01_add_production_llm_center.py`
- Create: `backend/tests/models/test_llm_models.py`
- Create: `backend/tests/integration/test_llm_migration.py`

**Interfaces:** Produces ORM entities `LlmModelConfig`, `LlmRuntimeSetting`, `LlmModelTestRun`, `LlmActivationRequest`, `LlmAdminAuditEvent`, `LlmDailyBudget`, `LlmTokenReservation`, `LlmCallAttempt`, `TaskOutbox`; extends `TaskRecord` and `LlmUsage`. Consumes Task 1 enums and UUID strings.

- [ ] Write failing model tests for state defaults, UUID creation, unique singleton runtime row, nullable scheduled-task user, snapshot fields, and absence of plaintext-key columns.
- [ ] Define tables and constraints exactly:
  - `llm_model_configs`: encrypted envelope, lifecycle/version/fingerprint/test metadata, prices, `supersedes_id`, soft delete.
  - `llm_runtime_settings`: singleton `id=1`, `default_model_config_id`, `daily_token_limit`, `budget_locked`, `version`.
  - `llm_model_test_runs`: nullable `model_config_id`, fingerprint, capability/result/error/latency.
  - `llm_activation_requests`: unique `idempotency_key`, request hash and serialized response.
  - `llm_admin_audit_events`: UUID、管理员、事件类型、脱敏原因、runtime settings 版本和时间；永久保留，不存密钥。
  - `llm_daily_budgets`: `budget_date` primary key, reserved/settled nonnegative counters.
  - `llm_token_reservations`: UUID, task/step/date, reserved/settled/status/lease.
  - `llm_call_attempts`: nullable task/config、operation UUID/type/step/attempt、provider/model/fingerprint snapshots、reservation/status/token/error/result hash and schema metadata；任务调用以 `(task_id,step_key,attempt_no)` 唯一，未保存候选测试以 `model_config_id=NULL` 和独立 operation UUID 关联。
  - `task_outbox`: task unique, status/attempts/available/locked/last error.
- [ ] Extend `task_records` with nullable `model_config_id`, `input_snapshot`, `input_snapshot_hash`, `prompt_version`, `execution_token`, `lease_expires_at`, `heartbeat_at`; extend `llm_usage` with nullable user, task/config/provider/model/price snapshots, input/output Token, `cost_micro_yuan`, status and error code. Keep old fields additive for mixed-version compatibility.
- [ ] Add targeted indexes: partial outbox `(available_at,id) WHERE status='pending'`; attempts `(created_at,model_config_id,status)`; test runs `(model_config_id,created_at)`; usage `(created_at,model_config_id)`; task records `(model_config_id,status)`.

```python
def test_schema_has_no_plaintext_secret_column():
    columns = set(LlmModelConfig.__table__.columns.keys())
    assert "api_key" not in columns
    assert {"encrypted_api_key", "nonce", "encryption_key_id"} <= columns
```

- [ ] Run `cd backend && .venv/bin/pytest tests/models/test_llm_models.py -q`; expect failures until models and migration imports exist.
- [ ] Generate and then hand-edit an additive Alembic migration. Upgrade must create new tables/nullable columns without dropping old data; downgrade only removes additions and must be tested on a disposable database.
- [ ] Run PostgreSQL integration commands: `cd backend && TEST_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/aistock_test .venv/bin/pytest tests/integration/test_llm_migration.py -q`; expect upgrade → schema assertions → downgrade → upgrade all pass with the repository's existing `psycopg2-binary` driver.
- [ ] Commit: `git add backend/app/models backend/alembic backend/tests/models backend/tests/integration/test_llm_migration.py && git commit -m "feat(llm): add model center schema"`

## Task 3: 实现供应商协议、通用调用器与 PostgreSQL Token 保险丝

**Files:**
- Create: `backend/app/services/llm/providers.py`
- Create: `backend/app/services/llm/url_security.py`
- Create: `backend/app/services/llm/provider_client.py`
- Create: `backend/app/services/llm/budget.py`
- Create: `backend/app/services/llm/call_executor.py`
- Create: `backend/tests/services/llm/test_providers.py`
- Create: `backend/tests/services/llm/test_url_security.py`
- Create: `backend/tests/services/llm/test_provider_client.py`
- Create: `backend/tests/services/llm/test_budget.py`
- Create: `backend/tests/services/llm/test_call_executor.py`
- Create: `backend/tests/integration/test_llm_budget_concurrency.py`

**Interfaces:** Produces immutable `ProviderProfile`, `PROVIDER_REGISTRY`, `validate_base_url`, `ProviderClient.complete_json`, `TokenBudgetService.reserve/settle/release/reap_expired`, and non-task-specific `LlmCallExecutor.call`. Consumes Task 1 shared client/errors and `LlmRuntimeConfig`; every real provider request, including probe/bootstrap/smoke/retry/correction, must enter through this layer.

- [ ] Write parameterized failing tests for exact endpoints and request fields for DeepSeek, Kimi and Qwen using `httpx.MockTransport`; assert Authorization is sent but never included in captured error strings.
- [ ] Encode profiles:

```python
PROVIDER_REGISTRY = {
    Provider.DEEPSEEK: ProviderProfile("https://api.deepseek.com", frozenset({"api.deepseek.com"}), 16),
    Provider.KIMI: ProviderProfile("https://api.moonshot.cn/v1", frozenset({"api.moonshot.cn"}), 16),
    Provider.QWEN: ProviderProfile("https://dashscope.aliyuncs.com/compatible-mode/v1", frozenset({"dashscope.aliyuncs.com"}), 24),
}
```

- [ ] Write URL-security tests for HTTPS/443 only, no userinfo/query/fragment, official DeepSeek/Kimi hosts, Qwen official suffix policy, IDNA/Unicode/encoded host, trailing dot, IPv4/IPv6 loopback/private/link-local/reserved, mixed public/private DNS, DNS rebinding at connect time, redirects, oversized response and environment proxy.
- [ ] Implement canonicalization and resolve all A/AAAA addresses immediately before connecting. Reject if any result is non-public. Do not follow redirects. Cap response body at 2 MiB.
- [ ] Estimate input upper bound from serialized UTF-8 bytes plus provider fixed overhead; always send a hard `max_tokens`. Parse `usage.prompt_tokens` and `usage.completion_tokens` when present.
- [ ] Map 401/403, 404/model error, 429, 5xx, timeout, response-too-large and invalid JSON into stable `LlmError.code`, Chinese `user_message`, and correct `retryable` flag.
- [ ] Write failing budget/call-executor tests for atomic reservation, exact settlement, explicit release before send, unknown outcome conservative settlement, 50-way concurrent cap, midnight separation, expired lease reaping, actual-over-reserve global lock, nullable `task_id/model_config_id`, mandatory `operation_type`, unsaved-probe provider/model/fingerprint snapshots, and a distinct reservation/attempt for every retry or correction request.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_budget.py tests/services/llm/test_call_executor.py -q`; expect collection/import failures before the services exist.
- [ ] Implement daily-row locking and reservation settlement. The executor order is reserve → write attempt → provider call outside transaction → settle/release → persist usage/attempt. Confirmed-unsent connection failures may retry; a request that may have reached the provider becomes `failed_unknown`, settles the reserved upper bound and is never automatically replayed.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_providers.py tests/services/llm/test_url_security.py tests/services/llm/test_provider_client.py tests/services/llm/test_budget.py tests/services/llm/test_call_executor.py tests/integration/test_llm_budget_concurrency.py -q`; expect all pass, with PostgreSQL used for concurrency cases and no internet access.
- [ ] Commit: `git add backend/app/services/llm backend/tests/services/llm backend/tests/integration/test_llm_budget_concurrency.py && git commit -m "feat(llm): add audited provider execution"`

## Task 4: 实现配置服务与管理员 API

**Files:**
- Create: `backend/app/schemas/llm.py`
- Create: `backend/app/services/llm/config_service.py`
- Create: `backend/app/api/admin_llm.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/admin.py`
- Create: `backend/tests/services/llm/test_config_service.py`
- Create: `backend/tests/api/test_admin_llm.py`

**Interfaces:** Produces `LlmConfigService` and `/api/admin/llm-models*`, `/api/admin/llm-settings`, `POST /api/admin/llm-settings/unlock`, `/api/admin/llm-usage`. Consumes Tasks 1–3, and every unsaved/saved/enable/activate capability test calls `LlmCallExecutor` with `operation_type="admin_probe"`, `task_id=None`, and optional `model_config_id`. Removes old `GET/PUT /api/admin/llm-config` contract.

- [ ] Define Pydantic DTOs with strict provider enum, trimmed lengths, nonnegative micro-yuan prices, positive limits, `expected_version`, and response capabilities `can_test/can_enable/can_disable/can_activate/can_delete`. All response DTOs expose only `key_hint`.
- [ ] Write failing API tests for admin auth, pagination/filtering, create 201, display/price PATCH 200, runtime PATCH creating new version 201, optimistic 409, test unsaved/saved, enable/disable/activate state machine, idempotency conflict, default delete protection, soft delete 204, settings, usage, budget unlock and secret non-disclosure. Unsaved probe audit must have `model_config_id=NULL` plus provider/model/fingerprint snapshots.

```python
def test_unsaved_probe_never_echoes_api_key(admin_client, mock_transport):
    response = admin_client.post("/api/admin/llm-models/test", json=CANDIDATE_WITH_SECRET)
    assert response.status_code == 200
    assert "sk-super-secret" not in response.text
```

- [ ] Implement config fingerprint from provider/model/canonical URL/credential version/max output and provider-specific options. Display name, price and master encryption key ID must not affect it.
- [ ] Implement immutable runtime versioning: referenced/default runtime parameters create a successor draft; display/price update in place with version increment and audit.
- [ ] Implement real capability probe requiring valid JSON and expected fields. It must use the same budget, retry, attempt and usage audit path as business calls. A later runtime edit preserves the run for audit but cannot set `verified_test_id`; `enable` after disable requires a newer matching run.
- [ ] Lock singleton `llm_runtime_settings` on both activation and task submission. `activate` revalidates state/version/fingerprint/test inside the transaction and stores the idempotent response.
- [ ] Implement `POST /api/admin/llm-settings/unlock` as an explicit administrator-only action with `expected_version` and a nonblank Chinese `reason`. In one transaction lock settings, require `budget_locked=True`, clear the lock, increment version and append `LlmAdminAuditEvent`; stale version or already-unlocked state returns 409. Unlock never alters settled/reserved counters.
- [ ] Remove the old admin endpoints and settings mutation only after new router tests pass.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_config_service.py tests/api/test_admin_llm.py tests/test_admin.py -q`; expect all pass.
- [ ] Commit: `git add backend/app/schemas/llm.py backend/app/services/llm/config_service.py backend/app/api/admin_llm.py backend/app/main.py backend/app/api/admin.py backend/tests && git commit -m "feat(admin): add production llm model api"`

## Task 5: 增加一次性 bootstrap、readiness 与 live-smoke CLI

**Files:**
- Create: `backend/app/cli/__init__.py`
- Create: `backend/app/cli/llm_config.py`
- Create: `backend/app/cli/database.py`
- Modify: `backend/docker-entrypoint.sh`
- Modify: `deploy/docker-compose.yml`
- Modify: `deploy/docker-compose.dev.yml`
- Create: `backend/tests/cli/test_llm_config_cli.py`
- Create: `backend/tests/integration/test_llm_bootstrap_lock.py`

**Interfaces:** Produces `python -m app.cli.database migrate|wait-for-head` and `python -m app.cli.llm_config bootstrap|readiness|live-smoke`. Bootstrap/smoke consume `LlmCallExecutor` with `operation_type="bootstrap"|"live_smoke"`, so they are budgeted and audited; worker never bootstraps.

- [ ] Write failing CLI tests for empty DB with/without old DeepSeek env, successful probe/default, failed probe candidate + admin notification + bootstrap exit 0, existing rows no-op, readiness exit 1 without verified active default, and fully redacted output.
- [ ] Run `cd backend && .venv/bin/pytest tests/cli/test_llm_config_cli.py -q`; expect collection/import failure because `app.cli.llm_config` does not exist.
- [ ] Use PostgreSQL advisory lock `pg_advisory_xact_lock(hashtext('aistock_llm_bootstrap_v1'))`. Before any real probe, idempotently insert `llm_runtime_settings(id=1, daily_token_limit=DAILY_TOKEN_LIMIT, budget_locked=false, version=1)` when absent, then lock/reload it; this also runs when no legacy DeepSeek Key exists. After that, perform a second “models still empty” check. The old env produces display name `DeepSeek 环境变量迁移` and never overwrites existing DB state.
- [ ] Implement bootstrap exit semantics: configuration/encryption programming errors are fatal; upstream test failure is persisted and logged in Chinese but exits 0 so API starts; `readiness` then exits 1.
- [ ] Implement `live-smoke --provider deepseek|kimi|qwen --model-config-id UUID` as explicit opt-in through `LlmCallExecutor`: one minimal structured request, schema assertion, token upper-bound assertion, budget/attempt/usage evidence, no key printing.
- [ ] Add a one-shot Compose `migrator` service that runs `alembic upgrade head` and verifies `alembic current` equals every `alembic heads` revision. API entrypoint becomes `bootstrap` → `uvicorn` only after migrator success; worker calls `wait-for-head`, which checks exact heads rather than merely checking that `alembic_version` exists.
- [ ] Remove `LLM_MOCK` from compose files. Keep legacy DeepSeek env only as observation-release bootstrap input, clearly labeled deprecated.
- [ ] Run `cd backend && .venv/bin/pytest tests/cli/test_llm_config_cli.py tests/integration/test_llm_bootstrap_lock.py -q`; tests cover an entirely empty DB with no legacy Key, and the PostgreSQL race launches two concurrent bootstrap processes while producing exactly one settings row and at most one config/default.
- [ ] Commit: `git add backend/app/cli backend/docker-entrypoint.sh deploy backend/tests/cli backend/tests/integration/test_llm_bootstrap_lock.py && git commit -m "feat(llm): add safe bootstrap and readiness"`

## Task 6: 统一任务提交与事务 outbox

**Files:**
- Create: `backend/app/services/task_submission.py`
- Create: `backend/app/services/outbox_dispatcher.py`
- Modify: `backend/app/services/membership.py`
- Modify: `backend/app/api/tasks.py`
- Modify: `backend/app/api/m3.py`
- Modify: `backend/app/api/m4.py`
- Modify: `backend/app/api/m5.py`
- Modify: `backend/app/tasks/scheduler.py`
- Modify: `backend/app/tasks/queue.py`
- Create: `backend/tests/services/test_task_submission.py`
- Create: `backend/tests/services/test_outbox_dispatcher.py`
- Create: `backend/tests/integration/test_task_submission_concurrency.py`

**Interfaces:** Produces `TaskSubmission`, `TaskSubmissionService.submit`, `submit_batch`, `OutboxDispatcher.dispatch_once`. `requires_llm=True` locks and snapshots the verified default; `False` skips model validation and writes `model_config_id=NULL`. Consumes `LlmRuntimeSetting`, `TaskRecord`, `UsageLog`, `TaskOutbox`; API and scheduler no longer create `TaskRecord` directly.

- [ ] Refactor membership accounting so `check_and_consume(db, ...)` uses the caller transaction and never commits internally; provide a separate top-level helper only for legacy non-task callers.
- [ ] Write failing atomicity tests: if any step after quota validation raises, task, usage deduction and outbox all roll back; an AI task without a verified default writes nothing; a `requires_llm=False` news task succeeds without a default and stores nullable model config.

```python
def test_submit_is_atomic(db, active_default, monkeypatch):
    monkeypatch.setattr(TaskOutbox, "__init__", Mock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        TaskSubmissionService(db).submit(SUBMISSION)
    db.rollback()
    assert db.query(TaskRecord).count() == 0
    assert db.query(UsageLog).count() == 0
```

- [ ] Implement submission transaction order: when `requires_llm`, lock `llm_runtime_settings` and validate/snapshot active verified default; otherwise set model snapshot null → check and consume membership → create task with input/prompt snapshots → create outbox → commit once. Sanitize `input_snapshot` before persistence and store SHA-256 canonical JSON hash.
- [ ] Replace direct creation in `tasks.py`, `m3.py`, `m4.py`, `m5.py`, and scheduled jobs. Mark news collection `requires_llm=False`; all analysis/report tasks remain `True`. Preserve current response IDs and user authorization behavior.
- [ ] Implement worker-owned outbox loop using `SELECT ... FOR UPDATE SKIP LOCKED`, bounded batches of 50, exponential `available_at`, stale lock recovery, and deterministic `_job_id=f"task:{task_id}"`. In inline mode, schedule only after the request transaction commits and mark the outbox delivered.
- [ ] Write PostgreSQL concurrency tests: activation vs submission has one linearized model choice; 20 simultaneous submissions do not over-consume membership; dispatcher concurrency emits one logical ARQ job.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/test_task_submission.py tests/services/test_outbox_dispatcher.py tests/integration/test_task_submission_concurrency.py -q`; expect all pass against PostgreSQL for lock cases.
- [ ] Run existing API tests for M2–M5 and scheduler to prove response compatibility.
- [ ] Commit: `git add backend/app/services backend/app/api backend/app/tasks backend/tests && git commit -m "refactor(tasks): unify submission and outbox"`

## Task 7: 用短事务 TaskExecutionRunner 统一九个任务包装器

**Files:**
- Create: `backend/app/services/task_execution.py`
- Modify: `backend/app/tasks/analysis.py`
- Modify: `backend/app/tasks/main_force.py`
- Modify: `backend/app/tasks/sector_analysis.py`
- Modify: `backend/app/tasks/dragon_tiger.py`
- Modify: `backend/app/tasks/portfolio.py`
- Modify: `backend/app/tasks/risk_analysis.py`
- Modify: `backend/app/tasks/portfolio_risk.py`
- Modify: `backend/app/tasks/us_research.py`
- Modify: `backend/app/tasks/news_collect.py`
- Create: `backend/tests/services/test_task_execution.py`
- Modify: `backend/tests/test_orchestrator.py`

**Interfaces:** Produces `TaskExecutionContext` and `TaskExecutionRunner.run(task_id, execute, persist_result)`. Consumes locked model ID from `TaskRecord`; Task 8 extends the context with `LlmExecutionService` while preserving the runner contract.

- [ ] Write failing runner tests for claim pending task, duplicate delivery, progress update, success persistence, domain exception, cancellation, stale running recovery, live-old-worker fencing and no Session held while awaiting business work.
- [ ] Define lifecycle transitions `pending -> running -> success|failed`; terminal tasks return without executing. Claim/reclaim uses a short transaction, row lock and a newly generated `execution_token`, updates `heartbeat_at/lease_expires_at`, and injects the token into `TaskExecutionContext`. A stale `running` task may be reclaimed only when its lease expired and no terminal or `failed_unknown` attempt blocks replay.
- [ ] Implement the callback boundary:

```python
async def execute(ctx: TaskExecutionContext) -> DomainResult: ...

def persist_result(db: Session, task: TaskRecord, result: DomainResult) -> None:
    # Writes final report only; runner writes task.success in the same transaction.
    ...
```

- [ ] Heartbeat renews the lease during long data/model waits. `set_progress`, successful-step persistence and final report transaction all update with `WHERE task_id=:id AND execution_token=:token`; zero affected rows raises `TaskExecutionFenced` so an old worker cannot call again or commit after another worker reclaims the task. Each helper opens its own short Session and never commits unrelated domain objects. Error storage keeps stable code + Chinese message and strips secrets/upstream bodies.
- [ ] Convert all nine task functions, including the pure-data news collector, to thin adapters. The news context has `runtime_config=None` and must not instantiate `LlmExecutionService`; all wrappers avoid `db.commit()` around network work and their own try/except status logic.
- [ ] Add regression tests that a report persistence exception leaves no report and marks the task failed; a successful report and `task.status=success` commit atomically; an original worker still alive after lease reclamation is fenced from progress, model calls and final persistence.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/test_task_execution.py tests/test_orchestrator.py tests/test_main_force.py tests/test_sector.py tests/test_dragon_tiger.py tests/test_portfolio.py tests/test_risk_engine.py tests/test_us_research.py tests/test_news.py -q`; expect all pass.
- [ ] Commit: `git add backend/app/services/task_execution.py backend/app/tasks backend/tests && git commit -m "refactor(tasks): add unified execution runner"`

## Task 8: 实现任务级幂等与结构化模型调用服务

**Files:**
- Create: `backend/app/services/llm/execution_service.py`
- Modify: `backend/app/core/llm.py`
- Modify: `backend/app/services/task_execution.py`
- Create: `backend/tests/services/llm/test_execution_service.py`

**Interfaces:** Produces task-scoped `LlmExecutionService.execute_json`. It consumes Task 3 `LlmCallExecutor`, one decrypted `LlmRuntimeConfig`, the current `execution_token` and output Pydantic types; it never calls `ProviderClient` or budget tables directly.

- [ ] Write failing execution tests for task config decrypted once, `(task_id, step_key)` success reuse, current execution-token fencing, unique attempt numbers delegated to the call executor, one JSON correction call, schema/version/hash persistence, usage linkage and full failure propagation.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_execution_service.py -q`; expect collection/import failure because the task-scoped service does not exist.
- [ ] Implement `execute_json` order: verify execution token → check/reuse terminal success → call `LlmCallExecutor(operation_type="task")` → validate Pydantic output → if invalid, make one independently budgeted/audited correction through the executor → persist validated step JSON/schema version/hash under the current fencing token → return typed result.
- [ ] `failed_unknown` must be terminal for automatic retries and task recovery. Manual rerun creates a new task ID; it never mutates the old attempt.
- [ ] Replace `backend/app/core/llm.py` with a compatibility import facade only while orchestrators migrate. Delete `MOCK_RESPONSES`, the product `chat` fallback, per-call `AsyncClient`, and independent `_log_usage` Session in this task.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_execution_service.py tests/services/llm/test_call_executor.py tests/integration/test_llm_budget_concurrency.py tests/test_llm.py -q`; expect all pass, with old Mock assertions replaced by real service-contract assertions.
- [ ] Commit: `git add backend/app/services/llm backend/app/services/task_execution.py backend/app/core/llm.py backend/tests && git commit -m "feat(llm): add durable execution and token budget"`

## Task 9: 定义版本化业务输出 Schema 并迁移股票、主力、板块分析

**Files:**
- Create: `backend/app/schemas/llm_outputs.py`
- Modify: `backend/app/services/analysis_orchestrator.py`
- Modify: `backend/app/services/main_force_orchestrator.py`
- Modify: `backend/app/services/sector_orchestrator.py`
- Create: `backend/tests/schemas/test_llm_outputs.py`
- Modify: `backend/tests/test_orchestrator.py`
- Create: `backend/tests/services/test_analysis_llm_contracts.py`

**Interfaces:** Produces schema version `v1` output types consumed by orchestrators and `LlmExecutionService`. Orchestrators consume `TaskExecutionContext`, never global settings or raw `chat()`.

- [ ] Freeze the current production report contract first: capture fixtures from `Analysis.tsx`, `MainForce.tsx`, and `Sector.tsx`, then assert every field those pages read survives backend serialization unchanged.
- [ ] Define strict Pydantic models (`extra='forbid'`) using the existing production field names:
  - `TechnicalAnalysisOutput`: `trend`, `score` 0–100, `short_trend`, `mid_trend`, `long_trend`, `support_resistance[{type,price,strength}]`, `breakout_prob` 0–100, `indicator_readings`, `pattern`.
  - `FundamentalAnalysisOutput`: `financial_health`, `profitability`, `valuation`, `score` 0–10, `detail`; `CapitalAnalysisOutput`: `main_flow`, `flow_trend`, `score` 0–10, `detail`; `NewsAnalysisOutput`: `sentiment_rating`, `key_news`, `impact`; `SentimentAnalysisOutput`: `sentiment_score` 0–100, `indicators`, `assessment`.
  - `ChiefDecisionOutput`: `rating` enum, optional positive `target_price/stop_loss`, `confidence` 0–100, `entry_range`, `take_profit`, `holding_period`, `position_size`, `risk_warning`, `key_watchpoints`, `meeting_summary`.
  - Main-force schemas retain `focus_stocks/analysis/score` plus each current specialized field; `MainForceResearcherOutput` retains `companies[{code,name,buy_range,sell_range,confidence,position,logic}]`, `excluded` and `meeting_summary`.
  - Sector schemas retain current `report/sectors/inflow_sectors/outflow_sectors/sentiment_score/width/assessment` fields; `SectorChiefOutput` retains `bull_sectors/bear_sectors/neutral_sectors/operation_advice/risk_triggers/key_indicators`.
- [ ] Add failing boundary tests for missing keys, extra keys, wrong types, blank summary, score -1/101, invalid enum, NaN/Infinity and invalid ranked item.
- [ ] Update prompts to explicitly request JSON and list exact field names. Give every step a stable key and prompt version, such as `stock.technical.v1`, `main_force.researcher.v1`, `sector.chief.v1`.
- [ ] Replace `_safe_chat` with `ctx.llm.execute_json(...)`. Use typed `model_dump()` only after validation. Any required analyst failure must bubble to runner; do not synthesize a neutral analyst.
- [ ] Preserve parallel calls only where steps are independent and the PostgreSQL budget service can reserve each call safely. Chief/researcher steps wait for all required typed inputs.
- [ ] Assert report is not persisted if any necessary step exhausts retries or schema correction. Assert successful result stores all step hashes/schema versions and uses the task’s locked config even if default switches mid-run.
- [ ] Run `cd backend && .venv/bin/pytest tests/schemas/test_llm_outputs.py tests/services/test_analysis_llm_contracts.py tests/test_orchestrator.py -q`; expect all pass.
- [ ] Commit: `git add backend/app/schemas/llm_outputs.py backend/app/services/analysis_orchestrator.py backend/app/services/main_force_orchestrator.py backend/app/services/sector_orchestrator.py backend/tests && git commit -m "refactor(llm): validate core analysis outputs"`

## Task 10: 迁移龙虎榜、持仓、风控和美股研报，清除新闻样例回退

**Files:**
- Modify: `backend/app/services/dragon_tiger_orchestrator.py`
- Modify: `backend/app/services/portfolio_orchestrator.py`
- Modify: `backend/app/services/risk_orchestrator.py`
- Modify: `backend/app/services/us_research_orchestrator.py`
- Modify: `backend/app/services/news_collector.py`
- Modify: `backend/app/schemas/llm_outputs.py`
- Create: `backend/tests/services/test_remaining_llm_contracts.py`
- Modify: `backend/tests/test_orchestrator.py`

**Interfaces:** Extends v1 schemas with `DragonTigerAnalysisOutput`, `PortfolioDiagnosisOutput`, `RiskAnalysisOutput`, `UsResearchOutput`, all preserving current persisted/API fields. News collection remains a pure data task and does not gain an LLM schema or new Token cost.

- [ ] Write failing backend and frontend-contract fixtures for `DragonTiger.tsx`, `Portfolio.tsx`, `RiskWarning.tsx`, and `USResearch.tsx`; each strict schema must validate and emit every field the current page reads, including existing nested cards/sections and current score scales.
- [ ] Remove every `_safe_chat`, `DEFAULT_LLM_NARRATIVE`, `allow_fallback` AI branch and `LLM_MOCK` branch that can manufacture business output. In `news_collector.py`, delete only the sample-data fallback gated by `LLM_MOCK`; retain deterministic keyword classification as ordinary non-AI data processing and do not route it through model configuration.
- [ ] Ensure no required AI failure saves a final report. Preserve existing user-visible response shapes by mapping validated typed output at the orchestrator boundary.
- [ ] Add repository guard test:

```python
def test_product_has_no_llm_mock_or_fixed_ai_fallback():
    forbidden = ("LLM_MOCK", "MOCK_RESPONSES", "DEFAULT_LLM_NARRATIVE")
    product_text = read_python_tree(ROOT / "app")
    assert all(token not in product_text for token in forbidden)
```

- [ ] Run `cd backend && .venv/bin/pytest tests/services/test_remaining_llm_contracts.py tests/test_orchestrator.py tests/test_news.py -q`; then run `rg -n 'LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|_safe_chat' backend/app frontend/src deploy .github` and expect no product matches.
- [ ] Commit: `git add backend/app/services backend/app/schemas/llm_outputs.py backend/tests && git commit -m "refactor(llm): migrate all production analysis calls"`

## Task 11: 重建管理员大模型中心页面与单元测试

**Files:**
- Create: `frontend/src/api/llmModels.ts`
- Create: `frontend/src/pages/admin/LlmModelsView.tsx`
- Create: `frontend/src/pages/admin/LlmModelsView.test.tsx`
- Modify: `frontend/src/pages/Admin.tsx`
- Modify: `frontend/src/styles/app.css`
- Modify: `frontend/src/pages/Guide.tsx`

**Interfaces:** Consumes Task 4 DTOs and existing Axios `client`; produces the `llm` admin tab. `Admin.tsx` only owns tab selection and imports the feature view.

- [ ] Define frontend types matching the backend response exactly: provider/lifecycle enums, redacted model row, capability flags, settings, usage buckets and structured error. API helper creates an idempotency UUID for activation and never caches secrets.
- [ ] Write failing Testing Library tests for loading, empty state, three-provider form, validation, unsaved test, save, edit with blank Key, conflict refresh, test/enable/disable/activate/delete confirmation, default protection, usage unknown-price display, budget-lock banner, audited unlock confirmation/reason, concurrent unlock 409 and Chinese errors.
- [ ] Add the explicit secret DOM test:

```tsx
expect(document.body.textContent).not.toContain('sk-super-secret')
expect(screen.getByLabelText('API Key')).toHaveValue('')
expect(screen.getByText(/当前密钥/)).toHaveTextContent('sk-****3557')
```

- [ ] Build the view from existing design-system classes: configured-model cards/table, default badge, test status, action buttons, add/edit panel and seven-day usage. Do not add a Mock checkbox or a fixed model-ID select.
- [ ] When `budget_locked=true`, show a blocking warning with current reserved/settled totals and an administrator-only “解除锁停” flow. Require typed reason and confirmation, submit `expected_version`, show the audit result, and refetch settings; never clear the banner optimistically.
- [ ] Disable duplicate actions while a request is pending; on 409 show “配置已被其他管理员修改，请刷新后重试” and refetch. Destructive soft delete requires a named confirmation and remains unavailable for default.
- [ ] At 390px, convert wide model rows to stacked cards, wrap long model ID/Base URL, and keep primary actions reachable without horizontal scrolling. API Key uses password input and is cleared after every request.
- [ ] Remove old inline `LlmConfigView` from `Admin.tsx`; keep all unrelated admin tabs unchanged.
- [ ] Update the in-app guide with administrator steps: add → test → enable → default switch, error meanings, daily Token lock and no-Mock behavior.
- [ ] Run `cd frontend && npm test -- --run src/pages/admin/LlmModelsView.test.tsx && npm run build`; expect all pass.
- [ ] Commit: `git add frontend/src/api/llmModels.ts frontend/src/pages/admin frontend/src/pages/Admin.tsx frontend/src/styles/app.css frontend/src/pages/Guide.tsx && git commit -m "feat(admin): add llm model center ui"`

## Task 12: 增加真实基础设施 CI、Playwright 与三供应商 live smoke

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/integration/conftest.py`
- Create: `backend/tests/integration/test_outbox_postgres.py`
- Create: `backend/tests/integration/test_llm_recovery.py`
- Create: `frontend/tests/e2e/llm-model-center.mjs`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:** CI unit lane uses SQLite + `httpx.MockTransport`; integration lane uses PostgreSQL 16 + Redis 7; production `live-smoke` is explicit and reads real encrypted configs only in JD Cloud.

- [ ] Keep fast unit tests hermetic. Add fixture factories that create encrypted verified test configs; remove `LLM_MOCK=true` from all test environments.
- [ ] First create the PostgreSQL/Redis integration tests and `llm-model-center.mjs`, then run `cd backend && .venv/bin/pytest tests/integration/test_outbox_postgres.py tests/integration/test_llm_recovery.py -q` and `cd frontend && npm run test:e2e:llm`; expect failures because CI services/script wiring and the new UI route fixtures are not yet configured.
- [ ] Add GitHub Actions service containers with health checks:

```yaml
services:
  postgres:
    image: postgres:16
    env: { POSTGRES_PASSWORD: postgres, POSTGRES_DB: aistock_test }
    ports: ["5432:5432"]
  redis:
    image: redis:7
    ports: ["6379:6379"]
```

- [ ] Run integration pytest as an explicit CI step with `TEST_DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/aistock_test` and `TEST_REDIS_URL=redis://127.0.0.1:6379/15`; add integration assertions for row locks, partial/unique constraints, outbox duplicate delivery, ARQ fixed job ID, Redis restart, budget persistence, execution-token fencing, Alembic upgrade/downgrade/upgrade and API/worker reading the same default.
- [ ] Add `npm run test:e2e:llm` and Playwright script covering desktop 1440×900 and mobile 390×844: add each provider, real-test UI state via route interception, enable/activate, default delete disabled, conflict/error, usage, reload persistence, and no horizontal overflow.
- [ ] Intercept only browser API routes in Playwright and only provider HTTP in backend contract tests. Add a guard that production bundles do not contain the fixture key or “Mock 模式”.
- [ ] Split release validation into two explicit phases. Migration release: migrator → API/bootstrap → `/api/health` → readiness → migrated DeepSeek smoke → worker → restore traffic. After an administrator adds and verifies Kimi/Qwen, set `LLM_STRICT_THREE_PROVIDER_SMOKE=true`; only then do this and all later releases require explicit DeepSeek/Kimi/Qwen live-smoke success. Missing Kimi/Qwen before the flag is enabled is shown as onboarding incomplete, not a failed first deployment.
- [ ] Run locally: `cd backend && .venv/bin/pytest -q`; `cd frontend && npm test -- --run && npm run build && npm run test:e2e:llm`.
- [ ] Commit: `git add .github/workflows/deploy.yml backend/tests frontend/tests/e2e/llm-model-center.mjs frontend/package.json frontend/package-lock.json && git commit -m "test(llm): add production infrastructure coverage"`

## Task 13: 增加数据保留、用量聚合与索引计划验证

**Files:**
- Create: `backend/app/services/llm/retention.py`
- Modify: `backend/app/services/llm/config_service.py`
- Modify: `backend/app/tasks/scheduler.py`
- Create: `backend/tests/services/llm/test_retention.py`
- Create: `backend/tests/integration/test_llm_query_plans.py`

**Interfaces:** Produces scheduled `cleanup_llm_audit_payloads` and indexed usage aggregation. Consumes Task 2 audit/usage tables; never deletes final report, config version, `LlmAdminAuditEvent`, token/cost/status/error/hash rows.

- [ ] Write time-controlled failing tests: records at 89 days keep bodies; at 91 days bodies are nulled only when the task is terminal (`success`, `failed`, `failed_unknown`), has no pending/locked outbox, no active reservation and no live execution lease. Running/recoverable tasks keep bodies regardless of age; final reports and model versions remain indefinitely.
- [ ] Process eligible cleanup in deterministic batches of 500 with a stored cutoff timestamp, short commits and retry-safe predicates. Keep hashes/schema/status/model/token/cost/error. Add a daily scheduler job and affected-row metrics. Document that after eligible bodies are removed, recovery means creating a new task; the original task is never replayed.
- [ ] Make `/api/admin/llm-usage` aggregate integer Token and micro-yuan values by UTC+8 date/provider/model/config, returning `cost_micro_yuan=None` when either required price snapshot is missing.
- [ ] Seed PostgreSQL with at least 100k synthetic audit/usage/outbox rows and run `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` for usage date range, pending outbox dispatch, stale recovery and cleanup selection. Blocking CI asserts intended index usage, bounded estimated/actual rows and no unexpected full-table scan. Record timings as non-blocking benchmark output; do not fail shared CI on fixed millisecond ceilings.
- [ ] Do not add broad duplicate indexes. Record EXPLAIN JSON as test output on failure, not as a committed generated artifact.
- [ ] Run `cd backend && .venv/bin/pytest tests/services/llm/test_retention.py tests/integration/test_llm_query_plans.py -q`; expect all pass on PostgreSQL 16.
- [ ] Commit: `git add backend/app/services/llm backend/app/tasks/scheduler.py backend/tests && git commit -m "feat(llm): add audit retention and query safeguards"`

## Task 14: 同步文档、看板并执行全链验收

**Files:**
- Modify: `TODOS.md`
- Modify: `docs/系统架构设计说明书.md`
- Modify: `DEPLOY.md`
- Modify: `deploy/README.md`
- Modify: `docs/用户使用手册.md`
- Modify: `docs/README.md`
- Modify: `backend/app/core/config.py`
- Modify: `deploy/docker-compose.yml`
- Modify: `.github/workflows/deploy.yml`

**Interfaces:** Documents the final API/operations contract and closes the feature board. Consumes evidence from Tasks 1–13; introduces no new runtime abstraction.

- [x] Update `TODOS.md` with the four milestones and real completion state. Do not add P3 items for module routing, failover, gateway or evaluation platform; those remain explicitly out of scope.
- [x] Before cleanup, run `rg -n 'LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|Mock 模式' backend/app frontend/src deploy .github`; the initial RED found the workflow bundle guard's forbidden literal.
- [x] Document architecture, PostgreSQL budget ledger, task locking/outbox, encrypted keyring dual-read/single-write rotation, three-provider setup, maintenance window, first bootstrap, readiness, real smoke, backup, rollback and 90-day payload cleanup in plain Chinese with inline term explanations.
- [x] Update `docs/README.md` carefully around existing user changes; add links to the approved design and this implementation plan without rewriting unrelated entries.
- [x] Remove the obsolete `LLM_MOCK` setting and all runtime reads. Keep `DEEPSEEK_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` only as deprecated bootstrap inputs for one observation release; label their later removal as a separate post-observation task, not part of this deployment.
- [x] Run forbidden-token and secret scans:

```bash
rg -n 'LLM_MOCK|MOCK_RESPONSES|DEFAULT_LLM_NARRATIVE|Mock 模式' backend/app frontend/src deploy .github
rg -n '(sk-[A-Za-z0-9_-]{8,}|LLM_CONFIG_ENCRYPTION_KEYS=.*[^<])' --glob '!*.example' .
```

  The first command must have no product matches. The second may only show clearly fake test fixtures or documentation placeholders; inspect every match before proceeding.
- [x] Run the authoritative verification matrix:
  1. `cd backend && .venv/bin/pytest -q`
  2. `cd frontend && npm test -- --run`
  3. `cd frontend && npm run build`
  4. `cd frontend && npm run test:e2e:llm`
  5. `docker compose -f deploy/docker-compose.yml config`
  6. On disposable PostgreSQL/Redis: Alembic upgrade → downgrade → upgrade, bootstrap race, API/worker restart, Redis restart, readiness.
- [ ] Before any production deployment, perform a separate reviewed maintenance runbook: backup PostgreSQL and `deploy/.env`; stop AI task creation; drain/resolve pending and running old tasks; stop old API/worker; run one-shot migrator and verify exact Alembic heads; start API; bootstrap/readiness/DeepSeek smoke; start worker; restore traffic. Then add and real-test Kimi/Qwen, switch defaults as required, create one small real report per provider, enable strict three-provider smoke, restart API/worker/Redis, and verify budget/default persistence. **未执行：本任务没有用户生产部署授权。**
- [ ] Run `superpowers:verification-before-completion`, then gstack `/review`, `/qa`, and `/ship` in that order. **未执行：按任务包由主 Sol 单独完成 gstack 关卡。**
- [x] Commit: only after verification evidence is captured, stage the Task14 intent files (including the approved `deploy/.env.example` cleanup and this report) and commit `docs(llm): document production model center`.

## Verification Coverage Map

The detailed QA artifact is `/Users/qinzz/.gstack/projects/zhizhengqin-aistock/qinzz-main-eng-review-test-plan-20260813-174607.md`. The implementation must close all 56 planned paths:

| Area | Paths | Evidence owner |
|---|---:|---|
| 模型配置、安全、状态机 | 12 | Tasks 1–5 backend unit/API tests |
| 任务提交与 outbox | 8 | Task 6 PostgreSQL concurrency tests |
| 执行、额度、崩溃恢复 | 12 | Tasks 3, 7–8 budget/runner/fencing integration tests |
| 三供应商与业务 Schema | 9 | Tasks 3, 9, 10 contract tests + live smoke |
| bootstrap/readiness/迁移 | 5 | Tasks 2, 5, 12 integration tests |
| 管理页面与响应式 | 10 | Tasks 11–12 Vitest + Playwright |
| **Total** | **56** | **56/56 required** |

## Self-Review Checklist

- [ ] Every design acceptance criterion maps to at least one task and one verification command.
- [ ] Every created/modified file is named; no step says “其他文件”“类似处理” or leaves an unresolved placeholder.
- [ ] `Provider`, lifecycle states, error codes, UUID format, fingerprint rules, budget status and task status are consistent across models, services, API and UI.
- [ ] PostgreSQL—not Redis—is the sole budget ledger; Redis restart cannot reset reservations or settlements.
- [ ] Runtime config is selected once at submission and decrypted once per task, not re-read per model step.
- [ ] Network I/O is outside DB transactions; task success and final report are one atomic commit.
- [ ] Tests do not rely on product Mock and production contains no fixed AI fallback.
- [ ] Migration is additive and old image compatibility is retained for the observation release.
- [ ] No implementation, push or production deployment occurs merely because this plan was approved.

## Engineering Review Completion Summary

- Step 0 Scope Challenge: full production scope accepted as-is and split into four independently testable milestones.
- Architecture Review: 4 issues found; all decisions folded into Tasks 3–8.
- Code Quality Review: 3 issues found; unified feature boundary, runner and strict business schemas folded into plan.
- Test Review: coverage diagram produced; 56 gaps identified and mapped to 56 required paths.
- Performance Review: 3 issues found; shared client, bounded retention and targeted index verification folded into plan.
- NOT in scope: written.
- What already exists: written.
- TODOS.md updates: 0 deferred P3 items proposed; optional routing/failover/gateway/evaluation work stays out of scope.
- Failure modes: 0 unresolved critical gaps after decisions were folded.
- Outside voice: Codex CLI returned an empty response; independent fallback reviewer ran, found 14 actionable gaps, all were accepted, folded and rechecked CLEAR.
- Parallelization: 4 implementation lanes; 3 bounded parallel handoffs and 11 sequential task boundaries.
- Lake Score: 14/14 interactive recommendations chose the complete option.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本阶段未单独运行 CEO Review |
| Codex Review | `/codex review` | Independent 2nd opinion | 2 | CLEAN (fallback) | 外部审查发现 14 项，全部接受并写回；最终复验 CLEAR |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | CLEAN (PLAN) | 4 架构 + 3 代码质量 + 56 测试路径 + 3 性能问题，全部收口 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 本阶段未单独运行计划级设计审查 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 本阶段未单独运行 DX Review |

**CODEX:** Codex CLI 空响应后自动切换独立只读审查；14 项实施缺口均已接受、写回并复验通过。

**CROSS-MODEL:** 主评审与独立审查现已一致：生产 Mock 必须删除，所有真实调用统一计额审计，部署与任务恢复必须具备明确事务和 fencing 边界。

**VERDICT:** ENG + OUTSIDE VOICE CLEARED — ready to implement.

NO UNRESOLVED DECISIONS
