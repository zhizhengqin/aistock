# 手机端响应式 QA 矩阵

日期：2026-08-12

测试地址：本地 Vite 应用，API 使用固定浏览器拦截数据

自动化脚本：`frontend/tests/e2e/mobile-responsive.mjs`

## 视口

| 视口 | 重点检查 |
|---|---|
| 390×844 | 抽屉、首页双列指数、窄屏卡片、宽表、登录页 |
| 430×932 | 抽屉、正文完整宽度、路由关闭 |
| 768×1024 | 平板抽屉、双列业务布局 |
| 1440×900 | 232px 桌面侧栏、桌面顶栏与内容回归 |

## 页面矩阵

| 页面 | 检查内容 |
|---|---|
| `/` | 顶栏、抽屉、指数卡片、筛选器、图表、热力图、整体横向溢出 |
| `/analysis` | 输入区、Tabs、进度区、历史表格 |
| `/main-force` | 表格局部横向滚动 |
| `/portfolio` | 持仓表格与卡片 |
| `/realtime` | 监测表格与卡片 |
| `/membership` | 套餐表格局部滚动、卡片 |
| `/guide` | 指南导航与长文本 |
| `/admin` | 管理员菜单入口与宽表 |
| `/login` | 独立登录布局未受应用外壳影响 |

## 验收结果

- [x] 390、430、768px 不显示圆点侧栏，显示菜单按钮。
- [x] 手机抽屉打开后显示完整菜单文字并锁定背景滚动。
- [x] 点击菜单项后完成跳转并自动关闭抽屉。
- [x] 所测页面 `scrollWidth === clientWidth`。
- [x] 390px 首页指数卡片为双列。
- [x] 1440px 桌面侧栏保持 232px，菜单按钮隐藏。
- [x] 浏览器控制台无错误。
- [x] 登录页无回归。

自动化结果：4 个视口、9 条路由全部通过。390/430/768px 的抽屉宽度分别约为 320px，1440px 桌面侧栏为 232px；所有页面整体横向溢出均为 0px。登录页曾检出固定宽度导致的 15px 溢出，修复后复测为 0px。

## 截图证据

- `docs/screenshots/mobile-responsive/390-home.png`
- `docs/screenshots/mobile-responsive/390-drawer-open.png`
- `docs/screenshots/mobile-responsive/390-wide-table.png`
- `docs/screenshots/mobile-responsive/430-home.png`
- `docs/screenshots/mobile-responsive/768-home.png`
- `docs/screenshots/mobile-responsive/1440-home.png`
- `docs/screenshots/mobile-responsive/390-login.png`
