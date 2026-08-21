# Task13 E2E reload 稳定性修复

## 根因

管理员模型中心 Playwright 脚本在两处 `page.reload({ waitUntil: 'networkidle' })` 等待 Vite 开发服务器达到网络空闲。真实失败发生在第二次 reload 的等待阶段：页面已经导航到 `/admin`，但 Vite 的持续开发连接使 `networkidle` 偶发超过 30 秒。单次与 DEBUG 连跑 5 次均通过，说明是等待条件抖动，不是页面功能失败。

## 修复

仅将两处 reload 的等待条件改为 `domcontentloaded`。每次 reload 后仍立即通过“大模型配置”按钮、额度输入框和锁停 banner 等语义 locator 等待页面状态；未修改生产 API、组件或行为，也未增加 timeout/sleep。

## 验证

- 修复前证据：主验收在 `frontend/tests/e2e/llm-model-center.mjs:229` 因 `networkidle` 30 秒超时失败，但页面已到 `/admin`；随后单次与 DEBUG 连跑 5 次通过。
- 修复后：`cd frontend && npm run test:e2e:llm` 连续 5 次通过，两个视口（1440×900、390×844）均输出 `failures: []`。
- `cd frontend && npm test -- --run && npm run build`：29 tests passed，Vite build 成功。
- `git diff --check`：通过。
