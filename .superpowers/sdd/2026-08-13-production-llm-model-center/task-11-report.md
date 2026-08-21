# Task 11 报告：管理员大模型中心页面

## 实现摘要

- 新增 `llmModels.ts`，逐字段对应 Task 4 模型列表、脱敏行、能力 flags、settings、usage 与结构化错误 DTO；激活 helper 每次生成新的 UUID 幂等键，不保存或回显 API Key。
- 新增 `LlmModelsView`：支持 DeepSeek、Kimi、Qwen 自由 model ID/Base URL；覆盖添加、真实测试、保存、编辑、启用、停用、设默认、软删除、冲突刷新、默认保护、七日用量与未知价格展示。
- 额度锁停 banner 使用服务端当前 `budget_date`、`reserved_tokens`、`settled_tokens`；解除锁停需要中文原因、expected version 与确认，成功后重新读取 settings 并展示审计结果，409 时保持锁停。
- 删除 `Admin.tsx` 旧内联 `LlmConfigView`，其他管理 tab 保持原行为；新增 390px 堆叠卡片、长 ID/Base URL 换行和可达操作样式。
- 使用指南新增“12. 管理员：大模型中心”，说明添加→测试→启用→设默认、错误含义、Token 锁停解锁与无 Mock 行为。
- 按批准的最小后端兼容扩展，仅在 `get_settings()` 增加北京时间当日 `budget_date`、`reserved_tokens`、`settled_tokens`，不改数据库模型/迁移、list/usage/unlock。

## TDD 证据

### 前端 RED

```text
cd frontend && npm test -- --run src/pages/admin/LlmModelsView.test.tsx
```

旧实现结果：suite 解析失败，`./LlmModelsView` 不存在，收集 0 tests；确认功能缺失后实现。

### 后端兼容扩展 RED

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/services/llm/test_config_service.py::test_get_settings_reports_beijing_budget_ledger_and_zero_when_missing \
  tests/api/test_admin_llm.py::test_settings_endpoint_exposes_current_beijing_budget_totals -q
```

结果：2 failed，旧 `get_settings()` 返回缺少 `budget_date` 的 `KeyError`。

### GREEN targeted

```text
cd frontend && npm test -- --run src/pages/admin/LlmModelsView.test.tsx
```

结果：14 passed。

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/services/llm/test_config_service.py tests/api/test_admin_llm.py -q
```

结果：29 passed，2 warnings。

### 回归与构建

```text
cd frontend && npm test -- --run
```

结果：3 test files、28 passed。

```text
cd frontend && npm run build
```

结果：TypeScript 与 Vite build 成功。

```text
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
```

结果：352 passed，18 skipped，4 warnings；PostgreSQL integration 按仓库规则在缺少 `TEST_DATABASE_URL` 时跳过。

## 静态检查

- `rg` 确认 `Admin.tsx` 不再包含旧 `/admin/llm-config`、`LlmConfigView`、Mock checkbox；产品代码不缓存或回显 API Key。
- 页面测试确认 DOM 不包含 `sk-super-secret`，API Key 输入为 password 且请求后清空，脱敏提示为 `sk-****3557`。
- 模型卡片在 390px 采用堆叠布局；长 model ID/Base URL 使用 `overflow-wrap:anywhere`，操作按钮换行，无模型行横向滚动。
- `git diff --check` 通过。

## 文件与边界

Task 11 原批准文件：

- `frontend/src/api/llmModels.ts`
- `frontend/src/pages/admin/LlmModelsView.tsx`
- `frontend/src/pages/admin/LlmModelsView.test.tsx`
- `frontend/src/pages/Admin.tsx`
- `frontend/src/styles/app.css`
- `frontend/src/pages/Guide.tsx`

经主 Sol 批准的最小后端兼容扩展：

- `backend/app/services/llm/config_service.py`
- `backend/tests/services/llm/test_config_service.py`
- `backend/tests/api/test_admin_llm.py`

未修改数据库模型/迁移、后端 API 路由契约、TaskRunner 或 Task 12+ 文件；构建生成物未纳入提交。剩余风险是真实浏览器 390px 端到端交互留给后续 Task 12 验收，本轮以 CSS 约束与 Testing Library 契约覆盖。
