# Sol Advisor 中文版评估与使用说明书

> 评估日期：2026-08-13<br>
> 评估对象：[DannyMac180/sol-advisor](https://github.com/DannyMac180/sol-advisor)<br>
> 原始评估基线：v0.5.0，提交 `676d200733ebd57caf1270ba94d892c95e9f8aee`<br>
> 当前本机插件版本：v0.6.0（截至 2026-08-22）<br>
> 当前状态：Sol Advisor 已在 `~/.codex/config.toml` 中禁用，但没有卸载，随时可以恢复；它不参与 aistock 日常开发。当前项目采用 Sol/High 主任务 + 单一 Luna/Max Build 实施角色<br>
> 本文面向：不想频繁手工切换模型，希望节省 Codex 使用额度，同时保留严格开发质量检查的用户

## 一、先说结论

### 当前采用的结论

Sol Advisor 的历史评估仍保留在本文，当前日常规则已经精简为“gstack 外层流程 + Superpowers 编码纪律 + 一个 Luna/Max 实施角色”：

1. 主任务长期固定为 `gpt-5.6-sol` / `high`，负责需求、架构、根因、任务拆分和最终验收。
2. gstack 按 `/office-hours` 或 `/spec` → `/plan-eng-review` → 用户确认 → Build → `/review` → `/qa` → `/ship` 管理生命周期；Superpowers 负责 TDD、系统化调试和完成前验证。
3. 进入获批的 Build 后默认只启动一次 `aistock_luna_implementer`，模型固定为 `gpt-5.6-luna` / `max`。小改动和大改动都使用这个角色，不再按 routine/high-complexity 分成多个角色。
4. 同一实施任务出现问题时，优先把精确修复要求发回原 Luna，不新开角色；需求、架构、未知根因、风险判断和最终验收仍由主 Sol 负责。
5. 默认不启动独立 Sol reviewer。认证、安全、资金、复杂迁移、并发或超大重构等极高风险变更，主 Sol 可以临时决定增加一次复审，但它不是日常必经步骤。
6. Terra 不参与本项目路由，也不作为 Luna 不可用时的回退。Sol Advisor 已禁用但可恢复；它不承担日常任务分派，Luna task lane 也不在当前流程中使用。
7. 为了闭环省 token，gstack `/review`、`/qa`、`/ship` 默认由当前 Sol/High 主任务直接执行，不启动 specialist、adversarial、Codex review 或其他审查子代理；Superpowers 只调用必要纪律，不采用多审查 Agent 链。

当前方案的核心目标不是减少所有模型合计的 token，而是让高价 Sol 少做机械编码，并消除人工切换模型和强度的操作。

OpenAI 官方把 Luna 定位为明确、可重复、高吞吐的任务模型；官方自定义 Agent 也支持为项目角色固定模型和推理强度。参见 [GPT-5.6 模型说明](https://learn.chatgpt.com/docs/models) 和 [Codex 子 Agent 配置](https://learn.chatgpt.com/docs/agent-configuration/subagents)。

---

## 二、它到底是什么

Sol Advisor 不是一个只有几段提示词的普通 Skill，而是一套 Codex 编排插件，主要包括：

- `setup` Skill：第一次使用时询问客户端、项目范围和各角色模型。
- `orchestration` Skill：提供插件自身的编排能力；当前项目日常不依赖它来分派编码任务。
- 本地 MCP 服务：保存配置、生成 Agent 文件、预览安装和执行受控卸载。
- Terra 实现角色：这是上游默认兼容通道，但当前 aistock 明确不安装、不调用。
- Sol Advisor 当前已禁用但可恢复；恢复后也只按用户当次明确要求使用，不改变本项目默认路由。
- aistock 项目角色：使用 Codex 官方 `.codex/agents/` 机制提供唯一的 Luna/Max Build 实施角色，不由 Sol Advisor 生成或接管。

这里的几个名词：

- **Skill**：Codex 可按需读取的一套工作说明。
- **MCP**：让 Codex 调用外部工具的协议。这个插件的 MCP 在本机运行，负责配置和文件安装，不是远程云服务。
- **Agent/角色**：为某一类工作固定模型、推理强度和行为要求的子任务执行者。
- **编排**：主任务不包办所有工作，而是负责设计、分派、复验和最终验收。

官方说明显示，Skill 使用“渐进加载”：平时只加载名称和简介，触发后才读取完整 `SKILL.md`。但每个启用的 MCP 服务仍会增加工具上下文。参见 [OpenAI Skills 官方说明](https://learn.chatgpt.com/docs/build-skills)。

---

## 三、一次任务会怎么运行

当前 aistock 日常通道如下：

```text
你提出需求
   ↓
Sol / High 主任务
明确需求、设计方案、确认根因、拆分任务
   ↓
唯一的 Luna / Max Build 实施角色（默认只启动一次）
写测试、实现代码、运行检查、汇报证据
   ↓
主任务
查看真实 diff、重新运行测试、判断是否符合要求
```

如果主任务复验发现问题，就把明确的修复要求发回同一个 Luna；如果属于极高风险变更，主 Sol 才会临时增加复审。gstack `/review` 和 `/qa` 仍然是必须经过的外层关卡。

### 省 token 覆盖规则

这里的“默认直接执行”是有边界的：gstack 仍负责阶段和检查清单，但 `/review`、`/qa`、`/ship` 由当前 Sol/High 主任务直接完成，不启动 specialist、adversarial、Codex review 或其他审查子代理。主 Sol 仍要检查真实 diff、重跑测试并完成浏览器 QA；极高风险变更最多临时增加一次复审。

Superpowers 只使用必要纪律：需求/方案阶段按需使用 `brainstorming` 或 `/spec`，Build 使用 `test-driven-development`；未知根因使用 `systematic-debugging`，完成前使用 `verification-before-completion`。不采用 `subagent-driven-development` 的“implementer + reviewer + final reviewer”链，也不运行重复的 `requesting-code-review` 或 `finishing-a-development-branch` Agent 流程。这样不会取消 gstack 阶段，也不会取消主 Sol 的 diff、测试和浏览器 QA。

历史上曾使用过独立审查角色时，审查结果含义如下；这不是当前默认路由：

| 结果 | 中文意思 | 后续动作 |
|---|---|---|
| `ship` | 可以交付 | 主任务汇总证据并完成 |
| `fix-first` | 先修再交付 | 交回原 Luna 角色修复、复验、重新做一次 Sol 审查 |
| `rethink` | 方案本身要重想 | 暂停交付，重新设计架构或范围 |

注意：只要审查后又改了代码，旧审查结论就作废，必须重新审查。项目日常自动通道由 `AGENTS.md` 约束；Sol Advisor 原版编排只在显式调用时生效。

---

## 四、它是否真的能省 token 和额度

本章保留 2026-08-13 的历史额度评估，用来解释为什么当前仍把明确实施交给 Luna；它不表示现在要重新启用 Sol Advisor。当前默认只使用项目原生的单一 Luna/Max Build 角色。

### 4.1 需要分清两个目标

“省 token”可能表示两件不同的事：

1. **减少总 token**：所有模型加起来读写的 token 更少。
2. **减少高价模型额度**：把大量编码从 Sol 转给 Luna。

Sol Advisor 历史上更擅长第二件事，但不保证第一件事；当前不依赖它来完成这项分工。

### 4.2 历史方案为什么总 token 可能反而增加

下面是旧的多角色编排在评估时的成本分析，不是当前 aistock 的默认流程。当前只保留一个 Luna/Max Build 角色，已经移除了重复的 routine 角色和默认独立审查角色。

一次普通任务可能只需要一个主任务完成。使用 Sol Advisor 后，通常会增加：

- 主任务写完整任务包的 token。
- Luna 子角色重新读取项目背景和相关文件的 token。
- 主任务复验 diff 和测试结果的 token。
- 旧方案中全新 Sol 审查角色重新读取变更与证据的 token。
- 旧方案中修复后重新审查的 token。
- `orchestration/SKILL.md`、引用文件和 MCP 工具说明的上下文。

因此小改动、文案调整、单文件修复不一定值得启用额外插件编排；当前项目直接使用同一个原生 Luna/Max 实施角色，减少重复上下文。

### 4.3 为什么高价模型额度仍可能下降

按 OpenAI 2026-08-13 公布的 ChatGPT credits 计价表：

| 模型 | 每 100 万输入 token | 每 100 万缓存输入 token | 每 100 万输出 token | 相对 Sol 同类 token |
|---|---:|---:|---:|---:|
| GPT-5.6 Sol | 125 credits | 12.5 credits | 750 credits | 100% |
| GPT-5.6 Terra | 50 credits | 5 credits | 300 credits | 40% |
| GPT-5.6 Luna | 5 credits | 0.5 credits | 30 credits | 4% |

来源：[OpenAI Codex 定价与额度说明](https://learn.chatgpt.com/docs/pricing)。实际消耗还会受到上下文长度、推理、工具调用和缓存影响。

假设原来某部分实现工作全部由 Sol 完成，把完全相同的 token 量交给 Luna，理论上这部分 credits 可以减少约 96%；相对 Terra，Luna 同类 token 的 credits 约低 90%。整个任务是否省额度，还要扣除新增的 Sol 任务拆分、复验和审查成本。

可以用下面的简化判断式理解盈亏平衡：

```text
节省的额度 ≈ 原本由 Sol 编码的成本 × 96%
             - 新增 Sol 审查成本
             - 主任务编排与重复读取成本
```

只有“被转给 Luna 的实现工作足够大”时，结果才容易为正。

### 4.4 不同任务的判断

| 任务类型 | 当前建议 | 是否需要 Sol Advisor |
|---|---|---|
| 改一句文案、改一个样式 | 直接交给唯一 Luna/Max 角色，主 Sol 复验 | 不需要 |
| 已确认根因的单点 bug 修复 | Luna 写回归测试并修复，主 Sol 复验 | 不需要 |
| 中等功能，涉及前后端和测试 | Sol 定方案，Luna 一次实施 | 不需要 |
| 数据库迁移、并发、安全逻辑 | Sol 定方案并判断风险，Luna 按边界实施；极高风险时临时复审 | 默认不需要 |
| 大范围重构 | Sol 先拆分，原 Luna 串行完成明确批次 | 默认不需要 |
| 调研报告或说明书 | 按任务边界由主 Sol 或 Luna 完成 | 不需要 |

---

## 五、为什么采用混合方案而不是上游默认方案

当前项目已经要求：

- gstack 负责需求、工程方案、审查、页面 QA 和交付流程。
- Superpowers 负责 TDD、系统化排障和完成前验证。
- 测试通过后才能进入 `/review`。
- `/review` 和 `/qa` 都通过后才能推送部署。
- 省 token 覆盖规则优先于 Skill 的默认派生行为：`/review`、`/qa`、`/ship` 由当前 Sol 直接执行，不启动 specialist、adversarial、Codex review 或其他审查子代理；Superpowers 不采用多审查 Agent 链。

上游默认方案与现有流程存在重叠，也不满足“不要 Terra、不要手动切换、减少重复角色”的目标：

| 现有能力 | Sol Advisor 对应能力 | 结果 |
|---|---|---|
| `/spec`、`/office-hours`、`/plan-eng-review` | 主任务做需求、架构和拆分 | 重叠 |
| Superpowers TDD | 唯一 Luna 按任务包实现和验证 | 由 Luna 执行纪律，不另造流程 |
| gstack `/review` | 默认由主 Sol 执行外层审查 | 不再强制另起 Sol reviewer |
| 主任务运行测试 | 主任务复验子角色结果 | 重叠但有价值 |
| Codex 官方项目角色 | 一个 Luna/Max 原生 Build 子任务 | 用于真正的日常自动分流 |

当前边界是：gstack 管生命周期，Superpowers 管开发纪律，`AGENTS.md` 管模型路由；一个原生 Luna/Max 角色负责获批 Build。Sol Advisor 已禁用但可恢复，不参与日常路由，也不冒充项目原生实施角色。

---

## 六、本机兼容性和实测结果

### 6.1 当前本机状态

| 项目 | 实测状态 |
|---|---|
| Codex CLI | `0.147.0-alpha.6.5` |
| 当前默认模型 | `gpt-5.6-sol` |
| 当前推理强度 | `high` |
| Bun | 已安装，`1.3.14` |
| jq | 已安装，路径 `/usr/bin/jq` |
| Sol Advisor 插件 | 当前本机已安装 `sol-advisor@sol-advisor` v0.6.0（截至 2026-08-22），但已在 `~/.codex/config.toml` 中禁用；本文实测评估基线为 v0.5.0 |
| Sol Advisor 配置 | 可以恢复；当前不参与日常开发、不负责任务分派，也不启用 Luna task lane |
| 项目原生 Luna 角色 | 仅 `aistock_luna_implementer`，`gpt-5.6-luna` / `max` |
| 项目 Sol 审查角色 | 没有默认独立角色；极高风险复审由主 Sol 临时决定 |
| Terra 角色 | 未安装，AGENTS.md 明确禁止使用和回退 |

### 6.2 仓库测试

安装前在临时目录克隆并验证了提交 `676d2007...`，随后使用 Codex 原生插件命令完成安装。

实测结果：

- `38` 个 Bun 测试全部通过。
- Manifest 校验通过。
- Release 打包校验通过。
- 安装冲突、软链接、目录穿越、异常中断回滚、卸载哈希校验等安全测试通过。
- MCP 运行时未发现主动联网逻辑，主要进行本地配置和受控文件写入。
- 本机实测 `gpt-5.6-luna` / `max` 返回成功。
- 历史动态路由实测曾验证 Luna/Max 与 Sol/High 可用；当前只保留 `aistock_luna_implementer`，主 Sol 负责复验，不把旧角色或旧审查结论当作现行规则。
- 自定义角色的实际 sandbox 可能被宿主扩大为 `danger-full-access`，不能仅凭 TOML 的 sandbox 请求宣称操作系统级隔离。
- 插件首次启动时发现 Codex 创建的 `PLUGIN_DATA` 目录权限为 `755`，插件按安全策略拒绝运行；已收紧为 `700`，随后配置校验返回 `ready` / `valid: true`。

发现一个开发测试兼容性问题：

- 直接运行 `bun run ci` 会调用系统 `/usr/bin/python3`。
- 本机系统 Python 是 `3.9.6`，没有标准库 `tomllib`，因此测试失败。
- 把 Python 3.12 放到 PATH 前面后，完整 CI 通过。
- 这影响“从源码跑完整测试”，不影响插件的日常 MCP 运行；仓库声明 MCP 运行时只依赖 Bun。

如果以后从源码参与开发，建议使用 Python 3.11 或更高版本运行仓库 CI。

---

## 七、如果决定安装，推荐这样装

> 本机已经按本节命令完成安装。以下命令既是操作说明，也是以后重装时的记录。

### 7.1 安装前检查

在终端运行：

```bash
codex --version
bun --version
jq --version
```

然后确认 Codex 的模型选择器里能看到准备使用的准确模型 ID。不要凭记忆手写别名。

### 7.2 添加仓库 Marketplace

```bash
codex plugin marketplace add DannyMac180/sol-advisor --ref main
```

这里的 Marketplace 可以理解成“插件来源清单”。这一步只添加来源，还没有完成角色配置。

### 7.3 安装插件

```bash
codex plugin add sol-advisor@sol-advisor
```

检查是否成功：

```bash
codex plugin list --json | jq -r '.installed[] | select(.pluginId == "sol-advisor@sol-advisor")'
```

预期应看到 `installed: true` 和 `enabled: true`。

### 7.4 重新打开一个 Codex 新任务

插件安装后要新开任务，让 Codex 重新发现 Skill 和 MCP 服务。不要继续使用安装前已经打开的旧任务做首次配置。

### 7.5 临时恢复插件（执行 setup/update 前的必需步骤）

当前插件是 disabled 状态。只要要运行 `$sol-advisor:setup`、更新插件或排查 Skill/MCP，就先按以下步骤临时恢复；平时不要为了日常开发启用它：

1. 打开 `~/.codex/config.toml`，找到 `[plugins."sol-advisor@sol-advisor"]`，把现有的 `enabled = false` 改为 `enabled = true`：

   ```toml
   [plugins."sol-advisor@sol-advisor"]
   enabled = true
   ```

2. 完全新开一个 Codex 任务，让配置和 Skill/MCP 重新加载。
3. 完成 setup、更新或排查后，再把同一项改回 `enabled = false`，然后再次新开一个 Codex 任务。

`codex plugin add sol-advisor@sol-advisor` 可能会重新启用插件；执行该命令后也要检查配置，并在操作结束时按上面步骤改回 `false`。本文不使用不存在的 `codex plugin disable` 命令。

---

## 八、第一次配置怎么选

### 8.1 推荐配置

插件逻辑配置采用：

| 配置项 | 推荐值 | 原因 |
|---|---|---|
| Client | `codex` | 当前使用 Codex |
| Scope | `project` | 只影响 aistock，风险和冲突更小 |
| Workspace | `/Users/qinzz/Desktop/aistock` | 当前项目真实目录 |
| Sol Advisor 当前状态 | 已禁用、未卸载 | 可恢复，但不参与日常开发 |
| 项目 Build 实施角色 | `gpt-5.6-luna` / `max` | 唯一 `aistock_luna_implementer`，方案由 Sol 决定，Luna 负责执行 |
| 主任务 | `gpt-5.6-sol` / `high` | 负责判断、复验和最终验收 |
| Orchestrator | `inherit` | 继承主任务当前模型 |
| Fallback | 关闭 | 模型不可用时停止，不悄悄换模型 |
| Sol Advisor Luna task lane | 不启用 | 不属于当前日常流程；只有用户当次明确要求且宿主具备工具时才另行评估 |

日常自动路由不使用插件生成的 Adapter，只使用以下项目文件：

```text
.codex/agents/aistock-luna-implementer.toml
```

原因是当前项目已经用一个原生 Luna/Max 角色完成 Build，gstack 和 Superpowers 也已经覆盖生命周期与编码纪律。Sol Advisor 只保留可恢复的配置能力，不再增加第二套日常编排。

### 8.2 重置后的配置提示词

如果以后重置配置，先按 7.5 把 `~/.codex/config.toml` 中的 `enabled = false` 临时改为 `true`，新开任务后再发送下面提示词；配置完成后按 7.5 改回 `false` 并新开任务：

```text
使用 $sol-advisor:setup 查看或恢复 Sol Advisor 配置（本次只做配置，不接管日常开发）。
客户端选择 Codex，范围选择当前项目，工作区是：
/Users/qinzz/Desktop/aistock

主任务选择 gpt-5.6-sol / high，实施角色选择 gpt-5.6-luna / max，禁止 fallback；不要安装或生成额外 Adapter，不要启用 Luna task lane。
请一次只问我一个问题。所有模型 ID 都让我从 Codex 模型选择器复制。
完成配置预览后停止。日常原生角色只由项目现有 `.codex/agents/aistock-luna-implementer.toml` 和 `AGENTS.md` 管理。
```

插件会询问角色的准确模型 ID并保存逻辑偏好。当前方案到这里就停止，不进入 Adapter 安装：

- Sol Advisor 不应覆盖项目原生 Luna 角色，也不应替代 gstack/Superpowers 流程。
- 项目只有一个受版本控制的 `aistock-*.toml` 实施角色，不需要插件重复生成。
- 如果 setup 仍展示 `INSTALL <随机值>`，不要复述令牌，保持预览未安装状态。

如果未来改回插件支持的原生通道，只有把完整令牌原样发回后它才允许写文件；普通的“确认”“可以”“yes”都不应触发安装。

### 8.3 为什么推荐 project 而不是 user

`project` 只为当前项目生成配置；`user` 会把角色配置放到用户级目录，让所有项目都能看到。

首次试用选择 `project` 的好处：

- 不会立刻影响 GS-Tracker、telegrammall 等其他项目。
- 更容易判断它是否和 aistock 的 gstack/Superpowers 冲突。
- 卸载和回滚范围更清楚。
- 用户级安装还需要第二个独立确认口令。

---

## 九、日常怎么用

### 9.1 日常开发：不需要手工指定模型

```text
完成这个功能：<写清功能目标>。
遵守当前项目 AGENTS.md 的 gstack + Superpowers 双框架和自动模型路由。
不要推送或部署。
```

主任务会保持 Sol/high。工程方案获批后，AGENTS.md 要求它默认只启动一次 `aistock_luna_implementer` 完成 Build；用户不需要再手工切模型或填写角色名。

技术约束：调用项目自定义角色时必须使用 `fork_turns = "none"`，不能同时指定角色并继承主任务完整历史。因此主任务必须把目标、文件边界、接口、约束和验证命令写入完整任务包。

省 token 覆盖规则同时生效：gstack `/review`、`/qa`、`/ship` 由当前 Sol/High 主任务直接执行，不启动 specialist、adversarial、Codex review 或其他审查子代理；Superpowers 不采用 `subagent-driven-development` 的多审查链，也不重复运行 `requesting-code-review`、`finishing-a-development-branch` Agent。主 Sol 仍然亲自检查 diff、重跑测试并做浏览器 QA。

### 9.2 自动分流判断

| 工作 | 当前负责者 |
|---|---|
| 文案、样式、CRUD、API 接线、测试、文档、机械重构 | 唯一 `aistock_luna_implementer` |
| 已批准方案的跨文件功能、迁移实施、安全/并发实现、大重构 | 唯一 `aistock_luna_implementer`；极高风险复审由主 Sol 临时决定 |
| 需求、架构、未知根因、风险判断、最终验收 | Sol 主任务 |
| gstack `/review`、`/qa` 和交付关卡 | Sol 主任务按流程执行 |

### 9.3 只做架构或关键决策审查

如果不想跑完整实现链，可以在需求阶段明确要求只做审查，不授权写代码：

```text
只对这个架构决策做 Sol 只读审查，不实现代码。
重点判断：<列出真正会改变方案的问题>。
给出 proceed、change 或 stop，并说明最大风险。
```

### 9.4 Sol Advisor 的 Luna task lane：当前不参与日常开发

Sol Advisor 目前已禁用但没有卸载，因此可以恢复；它的 Luna task lane 不属于 aistock 日常流程。当前 Build 必须走项目原生的 `aistock_luna_implementer`，而不是创建第二个用户可见任务。

如果未来确实需要使用插件 task lane，必须同时满足：宿主提供完整任务管理工具、用户在当次请求明确授权、主 Sol 仍负责检查真实 diff 和最终验收。它不能替代项目原生角色，也不能改变 gstack 流程。

以后 Codex 宿主补齐任务管理工具后，Luna 可见任务通道仍不会自动启用，必须在当次请求明确写出：

```text
本次明确授权使用 Sol Advisor 的 Luna task lane（仅本次，不改变项目默认路由）。
请创建一个 GPT-5.6 Luna / Max 的可见任务完成：<明确、边界固定的任务>。
主任务继续负责检查真实 diff、复跑测试和最终验收。
子任务未经主任务明确授权，不得推送或创建 PR。
```

适合 Luna 的工作：

- 机械批量修改。
- 结构明确的数据提取和分类。
- 边界清楚、验证命令明确的专注编码任务和较大独立工作树任务。
- 大量但低歧义的重复工作。

不适合让 Luna 独立决策的工作：

- 产品方向和架构选择。
- 高风险数据库迁移方案。
- 复杂并发、安全和资金逻辑的架构与验收。
- 根因尚不明确的疑难故障。

这些任务可以在 Sol 明确方案和边界后交给 Luna 实施，但最后仍由 Sol 验收。

这段授权只说明如何处理未来的特殊请求，不是当前推荐用法。通常不要启用它；优先把精确修复要求发回正在工作的原 Luna。

---

## 十、更新、检查、重新配置和卸载

### 10.1 更新插件

插件当前 disabled。更新前先按 7.5 将 `[plugins."sol-advisor@sol-advisor"]` 的 `enabled = false` 改为 `true`，新开 Codex 任务后再执行；更新完成并验证后，改回 `false` 并再次新开任务。`codex plugin add` 可能重新启用插件，不能把它当作日常路由开关。

```bash
codex plugin marketplace upgrade sol-advisor
codex plugin add sol-advisor@sol-advisor
```

更新后新开任务。如果角色模板发生变化，重新运行 setup 的预览和确认流程，不要直接覆盖已有自定义文件。

### 10.2 查看当前安装

```bash
codex plugin list --json | jq -r '.installed[] | select(.pluginId == "sol-advisor@sol-advisor")'
```

### 10.3 重新配置模型或范围

Sol Advisor 当前 disabled。先按 7.5 临时改为 `enabled = true` 并新开任务；完成下面 setup 后，再改回 `false` 并新开任务。

```text
使用 $sol-advisor:setup 重新配置 Sol Advisor。
先读取当前配置，然后一次只问一个问题。
展示完整新配置和全部文件预览；没有准确 INSTALL 口令前不要写入。
```

当前项目日常角色不是插件受管 Adapter。重新运行 setup 时不要让插件覆盖 `.codex/agents/aistock-luna-implementer.toml`；该文件由项目版本控制和 `AGENTS.md` 管理。

### 10.4 正确卸载顺序

如果插件当前 disabled，先按 7.5 临时改为 `enabled = true` 并新开任务，才能让 setup 读取受管 Adapter；卸载完成后按 7.5 改回 `false` 并新开任务。不要寻找或使用不存在的 `codex plugin disable` 命令。

如果以后通过插件安装过受管 Adapter，先在 Codex 新任务里卸载它们：

```text
使用 $sol-advisor:setup 卸载当前项目的 Sol Advisor Adapter。
先列出全部受管文件和哈希，给我准确的 UNINSTALL 口令；
没有收到完整口令前不要删除任何文件。
```

完成 Adapter 卸载后，再在终端执行：

```bash
codex plugin remove sol-advisor@sol-advisor
codex plugin marketplace remove sol-advisor
```

最后新开任务，并检查：

```bash
codex plugin list --json | jq -r '.installed[] | select(.pluginId == "sol-advisor@sol-advisor")'
```

没有输出表示插件包已移除。

当前安装没有让插件生成受管 Adapter，因此不会出现 Sol Advisor 受管角色残留。项目自有的 `.codex/agents/aistock-luna-implementer.toml` 不属于插件卸载范围；是否删除必须单独检查引用并再次取得用户确认。

---

## 十一、安全和隐私判断

### 做得比较好的地方

- 插件安装本身不会自动选择模型或写入 Agent 文件。
- 写入前展示完整目标路径、文件内容和一次性确认口令。
- 用户级范围需要第二个独立确认。
- 拒绝目录穿越、软链接目标、未知冲突和被改动的受管文件。
- 更新前创建私有备份。
- 安装和卸载使用哈希检查及事务日志，异常时尝试回滚。
- 配置拒绝 `secret`、`token`、`password`、`api key` 等疑似密钥字段。
- MCP 运行时代码未发现主动向外部网站发送项目数据的逻辑。

### 仍要注意的限制

- 这是第三方开源插件，不是 OpenAI 官方内置 Skill。
- 主分支更新后代码可能变化，更新前应查看 Changelog 和 diff。
- “请求只读”不一定等于操作系统强制只读，要看 Codex 实际报告的 sandbox。
- 每多一个 MCP 服务，都会增加一些工具上下文和维护面。
- 插件会生成项目级或用户级 Agent 文件，范围选错会影响其他项目。
- 原始评估基线 v0.5.0 于 2026-08-07 发布；本文对该版本的长期稳定性判断不代表当前已安装 v0.6.0 的行为。
- 原始评估基线 v0.5.0 在当时的本机 Codex 实测中显示非阻断 Manifest 警告：第二条默认提示词超过 128 字符，两个图标相对路径被忽略；该历史观察不代表当前 v0.6.0 的行为。

---

## 十二、常见问题排查

### 12.1 安装后看不到 Skill

处理顺序：

1. 当前插件默认 disabled；先按 7.5 将 `enabled = false` 临时改为 `true`，新开任务后再继续排查。
2. 运行 `codex plugin list --json` 确认插件已安装且启用。
3. 完全新开一个 Codex 任务。
4. 显式输入 `$sol-advisor:setup` 或 `$sol-advisor:orchestration`。
5. 仍不可见时，按 7.5 的前置步骤执行升级/重新添加；操作结束改回 `enabled = false` 并新开任务。

### 12.2 MCP 启动失败，提示找不到 bun

如果这是对 Sol Advisor 的临时排查，先按 7.5 把插件临时改为 `enabled = true` 并新开任务；排查结束再改回 `false` 并新开任务。

检查：

```bash
command -v bun
bun --version
```

本机当前 Bun 路径是 `/Users/qinzz/.bun/bin/bun`。如果 Codex 启动环境看不到该路径，完全退出并重新打开 Codex，再检查终端 PATH。

### 12.3 提示角色不存在或模型不可用

如果错误来自 Sol Advisor setup/MCP，先按 7.5 临时启用并新开任务；如果只是项目原生 Luna 角色报错，不要启用插件代替它。

不要让插件自动换成“差不多”的模型。按它的 fail-closed 原则停止当前通道，然后：

1. 完全新开一个 Codex 任务，让它重新发现 `.codex/agents/`。
2. 检查 `.codex/agents/aistock-luna-implementer.toml` 是否存在，并确认 `name = "aistock_luna_implementer"`。
3. 打开 Codex 模型选择器确认 `gpt-5.6-luna`、`max`、`gpt-5.6-sol` 和 `high` 当前可用。
4. 不得改用 Terra 兜底；仍不可用时停止并报告。

### 12.4 历史复审角色声称只读，但实际 sandbox 不是 read-only

当前没有默认独立 reviewer；本节只在主 Sol 因极高风险临时增加复审角色时适用。

这表示“行为上要求不写”存在，但操作系统没有强制隔离。

这正是当前本机实测状态：TOML 请求了 `read-only`，但任务记录显示 `danger-full-access`。

高风险任务需要操作系统级隔离时应停止该临时审查通道；如确实启用，必须在审查前后比较 `git status` 和完整 diff，确认复审角色没有产生额外改动。只要出现写入，立即废弃审查结论，不允许主任务悄悄修复后继续沿用旧 verdict。

### 12.5 从源码运行 `bun run ci` 报 `No module named tomllib`

根因是 Python 低于 3.11。检查：

```bash
python3 --version
```

如果是 Python 3.9，使用 Python 3.11 或更高版本放到 PATH 前面，再运行 CI。这个问题属于仓库开发验证环境，不是日常插件运行必须使用 Python。

### 12.6 用了以后额度下降更快

优先检查：

- 是否连小改动也调用了完整编排。
- （历史方案）是否反复出现 `fix-first`，导致多次 Sol 复审。
- 主任务是否给子任务粘贴了大量无关背景。
- 是否同时启用了过多 MCP 服务。
- （历史方案）是否把主任务、实现角色和 Reviewer 全部设成 Sol。
- 是否使用了 Fast mode 或过高推理强度。

当前方案明确选择 Luna/Max。若额度仍异常，应先检查是否把大量代码内容、构建日志或无关文件重复发送给 Sol，而不是改回 Terra。

---

## 十三、最适合你的省额度策略

### 推荐的两档模型分工

| 工作档位 | 模型建议 | 示例 |
|---|---|---|
| A：判断与验收 | Sol / High | PRD、架构、复杂根因、迁移方案、任务拆分、最终审查 |
| B：明确实施 | Luna / Max | 普通功能、测试、前后端联调、UI、文档、已定方案的复杂实现 |

Terra 不属于当前项目的模型分工，也不作为故障回退。

### 在 aistock 中的建议工作流

```text
需求和工程方案：Sol + gstack
        ↓
Build 实现：唯一 Luna / Max + Superpowers TDD
        ↓
相关测试和 Playwright：同一 Luna 执行，Sol 复验关键结果
        ↓
最终 /review：Sol
        ↓
最终 /qa 和部署验证：主 Sol 按 gstack 关卡验收
```

这个方案保留现有项目纪律，同时避免每次任务手工切换模型和重复启动角色。项目原生 Agent 负责自动分流；Sol Advisor 已禁用但可恢复，不参与日常开发。

省 token 的关键不是跳过关卡，而是覆盖 Skill 的默认派生：`/review`、`/qa`、`/ship` 由主 Sol 直接完成，不启动 specialist、adversarial、Codex review 或其他审查子代理；Superpowers 只运行必要的 brainstorming/spec、TDD、systematic-debugging 和 verification-before-completion，不采用 `subagent-driven-development`、`requesting-code-review`、`finishing-a-development-branch` 的重复 Agent 链。主 Sol 仍亲自检查 diff、测试和浏览器 QA。

### 一周试用判定法

从本次安装完成后开始，只统计中大型任务，并记录：

| 指标 | 安装前 | 安装后一周 |
|---|---:|---:|
| 每个任务平均完成时间 |  |  |
| 每个任务返工次数 |  |  |
| 5 小时窗口消耗速度 |  |  |
| Sol 使用占比 |  |  |
| Luna 使用占比 |  |  |
| 审查发现的真实问题数 |  |  |

只有在“额度更耐用”或“返工明显减少”至少一项成立时，才值得长期保留。

---

## 十四、最终判断表

| 维度 | 评分 | 说明 |
|---|---:|---|
| 代码和安全防护 | 8/10 | 测试覆盖了冲突、软链接、回滚和卸载校验 |
| 使用门槛 | 5/10 | 需要 Bun、MCP、首次配置和 Agent 文件 |
| 小任务省额度 | 7/10 | Luna token 很便宜，但仍有主任务拆分开销 |
| 中大型任务省高价模型额度 | 9/10 | Luna 同类 token credits 约为 Sol 的 4% |
| 提升交付质量 | 8/10 | 主 Sol 复验加 gstack `/review`、`/qa` 关卡 |
| 与当前 aistock 工作流互补性 | 7/10 | 插件可恢复但不参与日常；项目原生 Agent 负责当前自动路由 |
| 当前安装必要性 | **5/10** | 保留以便未来恢复，但当前禁用，不作为日常依赖 |

一句话结论：**aistock 当前采用 Sol/high 主任务负责判断、复验和验收，唯一 Luna/Max 项目角色负责获批 Build；gstack 管流程，Superpowers 管纪律，Terra 不参与且不回退。Sol Advisor 已在配置中禁用但未卸载，可恢复；它不参与日常开发。**

---

## 参考资料

- [Sol Advisor GitHub 仓库](https://github.com/DannyMac180/sol-advisor)
- [Sol Advisor Changelog（原始评估基线 v0.5.0）](https://github.com/DannyMac180/sol-advisor/blob/main/CHANGELOG.md)
- [OpenAI：Build skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI：Codex 定价、额度和 credits](https://learn.chatgpt.com/docs/pricing)
- [OpenAI：GPT-5.6 模型选择说明](https://learn.chatgpt.com/docs/models)
- [OpenAI：Codex 子 Agent 配置](https://learn.chatgpt.com/docs/agent-configuration/subagents)
