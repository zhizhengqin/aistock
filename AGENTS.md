# AGENTS.md — 睿见投研

## 项目

- 面向 A 股投资者的 AI 辅助投研系统，目标产品与功能依据见 `docs/`。
- 后端：FastAPI + SQLAlchemy + PostgreSQL + Redis；前端：React 18 + TypeScript + Vite。
- 生产环境使用 Docker Compose 部署在京东云，对外端口为 `8080`。

## 项目约束

- 优先复用现有组件、API 响应结构和设计系统，避免无关重构。
- 用户可见内容使用中文。
- 不提交密钥、`.env`、数据库、缓存或构建产物。
- 未经明确要求，不推送代码或触发生产部署。

## 模型路由与开发流程

- 主任务固定使用 `gpt-5.6-sol` / `high`，负责需求澄清、产品与架构判断、接口和数据模型设计、未知根因排查、风险决策、真实 diff 检查、最终验收，以及 gstack 关卡。
- gstack 管外层流程：`/office-hours` 或 `/spec` → `/plan-eng-review` → 用户确认 → Build → `/review` → `/qa` → `/ship`。未经用户要求，不提交、推送或部署。
- Superpowers 管编码纪律：新功能和 bugfix 先做 TDD；未知根因先系统化调试，不猜测式修复；完成前必须验证。
- Sol Advisor 已在 `~/.codex/config.toml` 中禁用，但没有卸载；它可按需恢复，但不参与 aistock 日常开发，也不负责日常任务分派。

### 省 token 覆盖规则（优先于 Skill 默认派生行为）

- gstack 仍必须管理阶段、清单和关卡，但 `/review`、`/qa`、`/ship` 默认由当前 Sol/High 主任务直接执行；不得启动 specialist、adversarial、Codex review 或其他审查子代理。主 Sol 仍必须检查真实 diff、重跑测试并完成浏览器 QA；极高风险变更最多临时增加一次复审，由主 Sol 决定。
- Superpowers 只调用必要纪律：需求/方案阶段按需使用 `brainstorming` 或 `/spec`，Build 使用 `test-driven-development`；未知根因使用 `systematic-debugging`，完成前使用 `verification-before-completion`。
- 不采用 `subagent-driven-development` 的“implementer + reviewer + final reviewer”链，也不运行重复的 `requesting-code-review` 或 `finishing-a-development-branch` Agent 流程。唯一 Luna 实施角色由本文件的 Build 路由控制。
- 这条覆盖规则不取消 gstack 阶段本身，不取消主 Sol 的真实 diff、测试、浏览器 QA，也不改变用户确认后才进入 Build 的要求。

### Build 阶段唯一实施角色

- 获批进入 Build 后，默认只启动一次 `aistock_luna_implementer`，模型固定为 `gpt-5.6-luna` / `max`。它负责编码、测试、机械修改，以及按已确定方案实施明确的跨文件功能。
- 小改动、文案、样式、CRUD、测试和文档也使用同一个角色；项目不再维护 routine 实施角色或独立 Sol reviewer。
- 同一实施任务发现问题时，优先把精确修复要求发回原 Luna；除非任务边界发生实质变化，不新开角色。
- Sol 不把需求、架构、未知根因、风险判断交给 Luna；Luna 不得自行改变接口、架构或范围。
- 认证、安全、资金、复杂迁移、并发或超大重构等极高风险变更，可由主 Sol 临时决定增加一次复审；这不是默认步骤，且仍不能替代 gstack `/review` 和 `/qa`。
- 禁止使用 Terra、静默回退或 Sol Advisor 的用户可见 Luna task lane；指定角色不可用时停止并报告。

### Luna 任务包与隔离约束

- 调用项目角色必须使用 `fork_turns = "none"`，不得继承主任务完整历史。
- 任务包必须包含六段：`OBJECTIVE`、`FILES AND OWNERSHIP`、`INTERFACES`、`CONSTRAINTS`、`VERIFICATION`、`RETURN`；缺任何一段先由主 Sol 补齐。
- 子角色不得推送、部署、创建或合并 PR。子角色报告只是声明，主 Sol 必须检查工作树和完整 diff，并亲自重跑验收命令。
- 当前宿主可能把请求的 sandbox 扩大为 `danger-full-access`；如进行临时复审，前后都要比较 `git status` 和完整 diff，发现额外写入就废弃结论。

## 完成标准

- 后端改动：运行 `cd backend && .venv/bin/pytest -q`。
- 前端改动：运行 `cd frontend && npm test -- --run && npm run build`。
- UI 改动：额外使用 Playwright 检查相关页面；涉及响应式时至少覆盖 `390px` 和桌面视口。
