# DataHub QA 报告

- 日期：2026-08-22
- 分支：`codex/datahub-platform`
- 范围：首页行情、系统配置/数据源配置、DataHub 管理 API、PostgreSQL/Redis 运行时
- 视口：桌面 1440px、手机 390px
- 框架：React 18 + Vite + Playwright；FastAPI + pytest

## 结论

通过。首页五项指数可显示真实非零数据及来源/时效状态；数据源页面可以查看中文说明、按能力测试、启停、排序并选择自动或固定路由。上游不可用时返回 stale 或中文 503，不再用 0.00 伪装行情。

## 验证证据

- 后端全量：`470 passed, 30 skipped`。
- 前端单元测试：`5 files / 40 tests passed`。
- 前端生产构建：Vite `111 modules transformed`，构建成功。
- PostgreSQL/Redis 真实集成：`33 passed`，其中 DataHub 集成 `6 passed`。
- Playwright：桌面与 390px 两个视口，`failures: []`。
- 真实公开行情：腾讯超时后自动降级到新浪，五项指数返回非零数据；来源和 stale 状态可见。
- 依赖与工作树：`pip check`、`git diff --check`、敏感/构建产物跟踪检查均通过。

## QA 发现与修复

1. 管理员测试新浪数据源时，探针样例中的时间对象无法写入 PostgreSQL JSONB。已做 JSON 安全规范化，真实 API 返回 200，状态：verified。
2. 首页指数已加载但板块上游失败时，顶部更新时间仍显示“加载中”，且错误文案误用了 A 股下跌绿色。已拆分加载状态和错误样式，桌面/手机复验通过，状态：verified。
3. PostgreSQL 快照更新后可能从 SQLAlchemy identity map 读回旧值。已强制刷新并增加并发/更新集成覆盖，状态：verified。

## 外部依赖说明

当前本机网络访问东方财富端点返回 502，因此板块接口按契约展示中文 503；这是上游网络状态，不伪造数据。用户可在数据源配置中测试并调整能力路由。腾讯在当前网络超时后，指数能力已验证可自动降级到新浪。

## 汇总

- 发现问题：3
- 已验证修复：3
- 最佳努力修复：0
- 回退：0
- 延期：0 个代码缺陷；1 个外部上游可达性风险
- 健康度：初始 4/10 → 最终 9/10

PR 摘要：QA 发现 3 个问题并全部修复，健康度 4/10 → 9/10。
