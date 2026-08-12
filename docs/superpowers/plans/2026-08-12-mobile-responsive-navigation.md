# 睿见投研手机端响应式导航 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变桌面端结构和业务逻辑的前提下，用可测试的抽屉导航替换手机端圆点侧栏，并完成全站窄屏内容适配。

**Architecture:** `Layout.tsx` 是抽屉开关状态、键盘事件、路由联动和滚动锁的唯一状态源；`app.css` 使用现有 900px/600px 断点切换桌面侧栏与移动抽屉，并集中处理共享卡片、表格和控件的窄屏规则。首页仅增加语义类名以覆盖现有内联网格列定义，不复制移动端页面或引入新的全局状态库。

**Tech Stack:** React 18、TypeScript 5、React Router 6、Vite 6、CSS Media Queries、Vitest、React Testing Library、jsdom、Playwright（最终浏览器 QA）。

## Global Constraints

- `>= 901px` 保留当前 232px 桌面侧栏。
- `601px–900px` 隐藏固定侧栏并使用抽屉导航；允许部分双列内容。
- `<= 600px` 使用抽屉导航；主要内容单列，短数据卡片允许双列。
- 手机菜单和操作按钮点击区域至少 44px。
- 不修改后端接口、数据库、认证模型、会员规则或菜单权限。
- 不新增运行时依赖、底部导航、图标库或第二套移动端页面。
- 不改变登录页和桌面端现有视觉体系。
- 测试全绿后才能进入 `/review`，`/review` 和 `/qa` 均通过后才允许提交最终实现或推送。
- 不改动任务开始前已存在的 `AGENTS.md` 与 Python 缓存文件工作区变更。

---

## 文件结构

- Modify: `frontend/package.json` — 增加前端测试命令和仅开发期测试依赖。
- Modify: `frontend/package-lock.json` — 锁定测试依赖版本。
- Modify: `frontend/vite.config.ts` — 增加 Vitest 的 jsdom、setup 和 CSS 配置。
- Create: `frontend/src/test/setup.ts` — 注册 jest-dom 匹配器和每例清理。
- Create: `frontend/src/components/Layout.test.tsx` — 覆盖导航抽屉全部行为与权限分支。
- Modify: `frontend/src/components/Layout.tsx` — 实现单一抽屉状态源、可访问结构和清理逻辑。
- Modify: `frontend/src/styles/app.css` — 实现抽屉、遮罩、断点、安全区域及共享内容适配。
- Modify: `frontend/src/pages/Home.tsx` — 用语义类名替换阻碍窄屏覆盖的内联网格列定义。
- Modify: `.github/workflows/deploy.yml` — 将前端测试加入部署前硬性关卡。
- Create: `docs/qa/mobile-responsive-test-matrix.md` — 固化 390/430/768/1440 四视口 QA 矩阵和页面清单。

不创建新的导航组件、hook 或移动端页面。预计 8 个代码/配置文件和 2 个测试/QA 文档文件，只有一个新增运行时代码路径（`Layout` 抽屉状态）。

---

### Task 1: 建立前端 TDD 基础与 CI 硬性关卡

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/components/Layout.test.tsx`
- Modify: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: 现有 Vite React 配置和 `Layout` 默认导出。
- Produces: `npm test -- --run`、jsdom 测试环境、`renderLayout(path, user)` 测试辅助函数。

- [ ] **Step 1: 安装仅开发期测试依赖并生成锁文件**

Run:

```bash
cd frontend
npm install --save-dev vitest@^3.2.4 jsdom@^26.1.0 @testing-library/react@^16.3.0 @testing-library/jest-dom@^6.6.3 @testing-library/user-event@^14.6.1
```

在 `package.json` 的 scripts 中增加：

```json
"test": "vitest"
```

- [ ] **Step 2: 配置 Vitest 和统一清理**

将 `vite.config.ts` 改为使用 `vitest/config` 的 `defineConfig`，保留现有 React 插件和代理，并增加：

```ts
test: {
  environment: 'jsdom',
  setupFiles: './src/test/setup.ts',
  css: true,
}
```

`frontend/src/test/setup.ts` 内容：

```ts
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => cleanup())
```

- [ ] **Step 3: 写第一个失败的布局回归测试**

在 `Layout.test.tsx` 用 `MemoryRouter`、`Routes`、`Route` 渲染 `Layout`，在每例前通过 `useAuthStore.setState` 注入普通用户，并断言：

```ts
expect(screen.getByRole('button', { name: '打开导航菜单' })).toBeInTheDocument()
expect(screen.getByRole('dialog', { name: '主导航' })).toHaveAttribute('aria-hidden', 'true')
```

Run:

```bash
cd frontend
npm test -- --run src/components/Layout.test.tsx
```

Expected: FAIL，原因是菜单按钮或导航 dialog 尚不存在。

- [ ] **Step 4: 把测试加入部署前关卡**

在 `.github/workflows/deploy.yml` 的 `npm ci` 之后、`npm run build` 之前增加：

```yaml
- run: cd frontend && npm test -- --run
```

- [ ] **Step 5: 验证测试框架本身可运行**

Run:

```bash
cd frontend
npm test -- --run src/components/Layout.test.tsx
```

Expected: 测试进程正常启动，唯一失败来自尚未实现的移动导航行为，而不是配置、类型或 jsdom 错误。

- [ ] **Step 6: 原子提交测试基础**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/test/setup.ts frontend/src/components/Layout.test.tsx .github/workflows/deploy.yml
git commit -m "test: add frontend component test foundation"
```

---

### Task 2: 用 TDD 实现可访问的移动抽屉导航

**Files:**
- Modify: `frontend/src/components/Layout.test.tsx`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/styles/app.css`

**Interfaces:**
- Consumes: `useLocation()`、现有 `NAV_ITEMS` / `ACCOUNT_ITEMS`、`useAuthStore` 的 `user` 和 `logout`。
- Produces: `isNavOpen: boolean`、`closeNav(): void`、`.menu-toggle`、`.nav-backdrop`、`.sidebar.open` 和移动端用户区 `.mobile-user-cluster`。

- [ ] **Step 1: 补齐所有失败的交互测试**

测试必须逐项覆盖：

1. 初始关闭，菜单按钮 `aria-expanded="false"`。
2. 点击菜单按钮后 dialog `aria-hidden="false"`、按钮变为 `aria-expanded="true"`、`document.body.style.overflow === 'hidden'`。
3. 点击关闭按钮恢复关闭状态和 body overflow。
4. 点击遮罩恢复关闭状态。
5. 按 Escape 恢复关闭状态。
6. 点击“股票分析”后路由内容切换且抽屉关闭。
7. 路由通过其他方式变化时抽屉关闭。
8. 普通用户看不到“系统配置”，管理员可以看到。
9. 移动用户区显示会员等级、用户名和“退出”。
10. 组件卸载后恢复原始 body overflow，不遗留滚动锁。
11. `matchMedia('(max-width: 900px)')` 从匹配变为不匹配时自动关闭抽屉并恢复 body overflow。

示例核心断言：

```ts
await user.click(screen.getByRole('button', { name: '打开导航菜单' }))
expect(screen.getByRole('dialog', { name: '主导航' })).toHaveAttribute('aria-hidden', 'false')
expect(document.body).toHaveStyle({ overflow: 'hidden' })

await user.keyboard('{Escape}')
expect(screen.getByRole('dialog', { name: '主导航' })).toHaveAttribute('aria-hidden', 'true')
expect(document.body.style.overflow).toBe('')
```

Run:

```bash
cd frontend
npm test -- --run src/components/Layout.test.tsx
```

Expected: 新增行为测试全部 FAIL。

- [ ] **Step 2: 实现最小抽屉状态和关闭事件**

在 `Layout` 中增加：

```ts
const [isNavOpen, setIsNavOpen] = useState(false)
const closeNav = () => setIsNavOpen(false)

useEffect(() => {
  closeNav()
}, [location.pathname])

useEffect(() => {
  if (!isNavOpen) return
  const previousOverflow = document.body.style.overflow
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') closeNav()
  }
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeyDown)
  return () => {
    document.body.style.overflow = previousOverflow
    window.removeEventListener('keydown', onKeyDown)
  }
}, [isNavOpen])

useEffect(() => {
  const mobileQuery = window.matchMedia('(max-width: 900px)')
  const onBreakpointChange = (event: MediaQueryListEvent) => {
    if (!event.matches) closeNav()
  }
  mobileQuery.addEventListener('change', onBreakpointChange)
  return () => mobileQuery.removeEventListener('change', onBreakpointChange)
}, [])
```

渲染要求：

- 顶栏菜单按钮带 `aria-label`、`aria-expanded` 和 `aria-controls="app-navigation"`。
- `aside` 使用 `id="app-navigation"`、`role="dialog"`、`aria-label="主导航"`、`aria-modal="true"`、`aria-hidden={!isNavOpen}`。
- 抽屉内有 `aria-label="关闭导航菜单"` 的关闭按钮。
- 遮罩是 button，带 `aria-label="关闭导航菜单"`，避免不可点击 div。
- `navLink` 点击时调用 `closeNav`。
- 桌面用户区保留，移动用户区复用同一个 `user` 和 `logout` 数据源。

- [ ] **Step 3: 实现桌面不变、移动抽屉的共享 CSS**

桌面默认：

```css
.menu-toggle, .sidebar-close, .nav-backdrop, .mobile-user-cluster { display: none; }
```

在 `@media (max-width: 900px)` 中用以下原则替换旧 64px 圆点规则：

```css
.shell { display: block; }
.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  width: min(82vw, 320px);
  height: 100dvh;
  transform: translateX(-100%);
  transition: transform 180ms ease;
  z-index: 40;
}
.sidebar.open { transform: translateX(0); }
.nav-backdrop { position: fixed; inset: 0; z-index: 30; }
.menu-toggle, .sidebar-close { display: inline-flex; min-width: 44px; min-height: 44px; }
.desktop-user-cluster { display: none; }
.mobile-user-cluster { display: flex; }
```

同时提供 `@media (prefers-reduced-motion: reduce)` 将抽屉 transition 关闭，并使用 `padding-top: env(safe-area-inset-top)` / `padding-bottom: env(safe-area-inset-bottom)` 处理安全区域。

- [ ] **Step 4: 跑组件测试至绿色**

Run:

```bash
cd frontend
npm test -- --run src/components/Layout.test.tsx
npm run build
```

Expected: Layout 测试全部 PASS；TypeScript 与 Vite 构建 PASS。

- [ ] **Step 5: 原子提交导航行为**

```bash
git add frontend/src/components/Layout.tsx frontend/src/components/Layout.test.tsx frontend/src/styles/app.css
git commit -m "feat: add mobile drawer navigation"
```

---

### Task 3: 完成共享窄屏内容适配

**Files:**
- Modify: `frontend/src/styles/app.css`
- Modify: `frontend/src/pages/Home.tsx`
- Modify: `frontend/src/components/Layout.test.tsx`

**Interfaces:**
- Consumes: 现有 `.content`、`.card`、`.table`、`.tabs`、`.kpi-grid`、`.heatmap` 和网格工具类。
- Produces: `.home-index-grid`、`.home-stock-grid`、`.home-heatmap` 三个首页语义类，以及共享手机规则。

- [ ] **Step 1: 写首页语义类回归测试并确认失败**

静态导入并渲染 Home 成本较高且包含 API 副作用，因此使用浏览器 QA 覆盖像素行为；组件层只增加结构契约测试，读取 `Home.tsx` 由构建验证类型。先在 `Layout.test.tsx` 增加 `.content` 和顶栏结构断言，确保手机外壳结构稳定。

Run:

```bash
cd frontend
npm test -- --run
```

Expected: 新的结构契约在类名落地前 FAIL。

- [ ] **Step 2: 移除阻碍媒体查询的首页内联列定义**

在 `Home.tsx` 中：

- 指数网格改为 `className="kpi-grid home-index-grid"`。
- 代表个股网格改为 `className="kpi-grid home-stock-grid"`。
- 热力图改为 `className="heatmap home-heatmap"`，桌面列数通过 CSS 自适应，不再由 inline style 固定。

桌面 CSS：

```css
.home-index-grid { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.home-stock-grid { grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
.home-heatmap { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }
```

- [ ] **Step 3: 加入共享窄屏规则**

在 900px 断点：

- `.main` 与 `.content` 设置 `max-width: 100%`、`min-width: 0`。
- `.content` 保持 16–20px 安全边距。
- `.card`、`.mini-card`、`.kpi` 设置 `min-width: 0`。
- `.table` 改为 block 格式并在自身范围内 `overflow-x: auto`，宽表设置适当 `min-width`，页面本身不得横向滚动。
- `.tabs` 和无法合理换行的连续控件允许组件内部横向滚动。
- `pre`、图表容器和长文本使用 `max-width: 100%`、`overflow-wrap: anywhere`。
- 顶栏高度使用最小高度而非固定高度，标题允许缩小但不与按钮重叠。

在 600px 断点：

```css
.content { padding: 16px 12px 24px; }
.card { padding: 16px; }
.home-index-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.home-index-grid .kpi { padding: 14px 12px; }
.home-index-grid .k-value { font-size: clamp(22px, 7vw, 30px); }
.home-stock-grid { grid-template-columns: 1fr; }
.home-heatmap { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.modal { max-width: calc(100vw - 24px); padding: 20px 16px; }
.modal .btn { min-height: 44px; }
```

不得给 `html` 或 `body` 使用 `overflow-x: hidden` 掩盖真实溢出；QA 必须找到并修复溢出源。

- [ ] **Step 4: 运行全部前端自动化验证**

Run:

```bash
cd frontend
npm test -- --run
npm run build
```

Expected: 全部测试 PASS，构建 PASS。

- [ ] **Step 5: 原子提交内容适配**

```bash
git add frontend/src/styles/app.css frontend/src/pages/Home.tsx frontend/src/components/Layout.test.tsx
git commit -m "fix: optimize mobile content layout"
```

---

### Task 4: `/review`、四视口 `/qa` 与最终验证

**Files:**
- Create: `docs/qa/mobile-responsive-test-matrix.md`
- Modify only if a verified defect is found: files from Tasks 1–3

**Interfaces:**
- Consumes: 已实现的抽屉、共享响应式规则和 gstack QA 浏览器。
- Produces: 可复核的视口/页面矩阵、截图和零横向溢出证据。

- [ ] **Step 1: 创建 QA 矩阵**

文档必须列出视口 `390x844`、`430x932`、`768x1024`、`1440x900`，并覆盖：

- `/` 首页：顶栏、抽屉、双列指数、筛选器、图表、热力图。
- `/analysis`：输入区、Tabs、进度、历史表格。
- `/main-force`、`/dragon-tiger`、`/portfolio`、`/realtime`：宽表格局部滚动。
- `/risk-warning`：表单和进度区。
- `/news`、`/us-research`：卡片、长标题和表格。
- `/membership`：套餐对照表和弹窗。
- `/guide`：指南导航与长文本。
- `/admin`：仅管理员测试菜单可见性和宽表格。
- `/login`：确认未受应用外壳改动影响。

- [ ] **Step 2: 运行 gstack `/review`**

审查当前实现差异，重点检查：

- body overflow 清理是否在 Escape、路由变化、旋转和卸载时一致。
- 普通用户/管理员菜单权限是否保持原样。
- 视口跨过 900px 时是否关闭抽屉并恢复 body overflow。
- 手机 CSS 是否意外改变 901px 以上桌面布局。
- 是否用全局 `overflow-x: hidden` 掩盖问题。
- 测试是否覆盖所有新增分支。

发现问题时先增加回归测试，再做最小修复；每个独立缺陷使用独立 conventional commit。

- [ ] **Step 3: 启动本地全栈并执行 gstack `/qa`**

使用现有开发启动方式，在每个目标视口逐页验证。每个页面在浏览器中执行：

```js
document.documentElement.scrollWidth === document.documentElement.clientWidth
```

Expected: 所有页面返回 `true`；需要横向查看的数据只在表格或 tabs 自身容器内滚动。

交互必须验证：菜单按钮、关闭按钮、遮罩、Escape、页面跳转、浏览器后退、退出、会员弹窗；检查控制台 0 errors。

- [ ] **Step 4: 保存手机与桌面证据截图**

至少保存：

- 390px 首页抽屉关闭。
- 390px 首页抽屉打开。
- 390px 宽表格局部滚动页面。
- 768px 首页。
- 1440px 首页桌面侧栏回归。

截图保存到 `docs/screenshots/mobile-responsive/`，并在 QA 矩阵中登记路径和结论。

- [ ] **Step 5: 运行最终验证**

```bash
cd frontend && npm test -- --run && npm run build
cd ../backend && pytest -q
cd .. && git diff --check
git status --short
```

Expected: 前端测试、构建、后端全量 pytest 和 diff check 全部通过；`git status` 中只保留本任务文件以及任务开始前已存在的用户改动。

- [ ] **Step 6: 等待推送授权**

实现、`/review` 和 `/qa` 全部通过后汇报提交 hash、测试数量、四视口结果和截图路径。未经用户明确要求，不 push、不部署京东云。

---

## 测试覆盖图

```text
CODE PATHS                                           USER FLOWS
[+] Layout.tsx 抽屉状态                              [+] 手机导航 [→E2E]
  ├── 默认关闭 [UNIT]                                  ├── 打开 → 菜单完整可见
  ├── 菜单按钮打开 [UNIT]                              ├── 菜单项 → 跳转并关闭
  ├── 关闭按钮关闭 [UNIT]                              ├── 遮罩 / Escape → 关闭
  ├── 遮罩关闭 [UNIT]                                  └── 后退 / 路由变化 → 关闭
  ├── Escape 关闭 [UNIT]
  ├── pathname 变化关闭 [UNIT]                      [+] 身份与退出
  ├── 跨 900px 关闭并解锁 [UNIT]                      ├── 普通用户无系统配置 [UNIT]
  ├── 打开 → 锁定 body scroll [UNIT]                   ├── 管理员有系统配置 [UNIT]
  ├── 关闭/卸载 → 恢复 scroll [UNIT]                   └── 会员/用户名/退出可操作 [E2E]
  └── admin 条件分支 [UNIT]

[+] app.css 响应式 [→E2E]                           [+] 页面内容 [→E2E]
  ├── >=901: 桌面侧栏                                  ├── 首页双列指数
  ├── 601-900: 抽屉 + 可选双列                         ├── 筛选器和 tabs 可操作
  ├── <=600: 抽屉 + 主要单列                           ├── 表格仅容器内横向滚动
  ├── prefers-reduced-motion                           ├── 弹窗不越界
  └── safe-area                                       └── 全页面无整体横向溢出

COVERAGE TARGET: Layout 新增分支 11/11 单元覆盖；4 个视口 × 主要页面矩阵 E2E 覆盖。
```

## 性能与失败模式

- 抽屉动画只修改 `transform`，避免布局抖动；`prefers-reduced-motion` 下禁用动画。
- 键盘监听仅在抽屉打开时注册并在关闭/卸载时移除。
- 不增加 API 请求、定时器、全局状态订阅或生产运行时依赖。
- 若 CSS 未加载，语义化导航内容仍存在；若 JavaScript 未执行，媒体查询保证旧圆点侧栏不再出现。
- 若设备旋转跨过 900px，CSS 立即切换布局；实现需通过媒体变化或渲染清理避免 body 保持锁定。
- 最坏回归是桌面侧栏被隐藏或页面被锁滚动；组件测试与 1440px QA 专门覆盖这两个高影响风险。

## NOT in scope

- 京东云推送和部署。
- 后端、数据库、接口或会员逻辑修改。
- 桌面端重新设计。
- 底部导航、图标依赖、复杂焦点陷阱库或独立移动页面。
- 与本任务无关的历史 TODO 和用户工作区改动。

## GSTACK REVIEW REPORT

| Runs | Status | Findings |
|---|---|---|
| Architecture | CLEAR | 单一 Layout 状态源；无新运行时依赖或并行移动页面 |
| Code Quality | CLEAR | 共享 CSS 为主，仅首页增加必要语义类名 |
| Tests | DECIDED | 1 个缺口：前端无 TDD 基础；用户选择新增 Vitest + React Testing Library |
| Performance | CLEAR | transform 动画；监听器仅打开时存在；无 API 与包体运行时增量 |
| Outside Voice | SKIPPED | 本机 `codex_reviews=disabled`，按配置跳过 |

VERDICT: CLEARED — 工程计划完整，两个已发现问题均已决策并纳入计划

NO UNRESOLVED DECISIONS
