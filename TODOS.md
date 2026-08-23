 # 睿见投研 — 开发进度跟踪（TODOS）
 
 > 本文件是项目开发的"施工进度看板"。每完成一个阶段就更新对应状态标记。
 > 状态图例：`[ ]` 待办 · `[~]` 进行中 · `[x]` 已完成
 
 ---
 
## 总览

### A 股 DataHub（2026-08-22）

- `[x]` Batch 0–8：统一能力契约、供应商路由、配置中心、消费者迁移与 KPL 接入
- `[ ]` DataHub 后续能力扩展：按业务模块接入 `a-stock-data` 其余端点，逐项补齐契约、夹具和 live-smoke
- `[ ]` 高频快照专用表/分区：当 `data_snapshots` 达到容量阈值或慢查询稳定出现时评估结构化表、分区和双读迁移
- `[ ]` 热点轮动历史页：待首页热点中心稳定积累盘后快照后，增加按日期查看板块排名、连续升温天数和轮动轨迹的页面；依赖本次热点快照与趋势口径先稳定运行，本次 Build 不扩大到独立历史页面

### 首页热点中心与大盘云图（2026-08-23）

- `[x]` DataHub 新增 `market.board_quotes` 与 `market.board_constituents` 契约、Eastmoney provider、缓存与校验链路
- `[x]` 统一 `HotspotService`：热度分、升温/降温趋势、行业/题材云图、代表股和 DataHub/快照回退
- `[x]` 新增热点、云图、代表股 API；删除旧固定板块/K 线入口
- `[x]` 交易日 15:10/15:20/15:30 盘后快照任务（Asia/Shanghai、进程级非阻塞锁、`requires_llm=False`）
- `[x]` 首页改为行业/题材独立加载、代表股下钻、云图返回、局部错误与历史数据标记
- `[x]` 侧边栏导航改用语义 Lucide 图标；补充桌面和 390px 首页 Playwright 验证
- `[x]` 云图已按市值/成交额面积权重展示名称与涨跌幅，小色块支持悬浮详情，并用快速定位下拉替代按钮墙
- `[ ]` 独立热点轮动历史页：留待快照积累后评估（不属于本次 Build）
 
 | 阶段 | 内容 | 预计工期 | 状态 |
 |---|---|---|---|
 | M0 | 开发环境搭建 | 2 天 | `[x]` 已确认 |
| M1 | 项目骨架 + 用户系统 + 首页行情 | 1 周 | `[x]` 已完成 |
| M2 | 股票分析（核心 AI 流程） | 1.5 周 | `[x]` 已完成 |
| M3 | 主力选股 + 智策板块 + 龙虎榜 | 1.5 周 | `[x]` 已完成 |
| M4 | 持仓分析 + 实时监测 + 风险预警 | 1 周 | `[x]` 已完成 |
| M5 | 实时新闻 + 美股研报 + 定时任务 | 1 周 | `[x] 已完成` |
| M6 | 会员体系 + 配额 + 充值页 | 1 周 | `[x]` |
| M7 | 京东云部署 + 域名 + HTTPS + 备份 | 1 周 | `[x]` 文档与配置就绪，待用户上机执行 |

---

## 生产大模型中心四个里程碑

| 里程碑 | 范围 | 状态 |
|---|---|---|
| LLM-1 基础与账本 | 配置模型、供应商协议、PostgreSQL Token 预算与加密密钥环（Tasks 1–5） | `[x]` 已完成 |
| LLM-2 任务可靠性 | 统一提交、outbox、执行栅栏、任务级幂等与结构化调用（Tasks 6–8） | `[x]` 已完成 |
| LLM-3 业务迁移 | 股票、主力、板块、龙虎榜、持仓、风控、美股研报和新闻语义迁移（Tasks 9–10） | `[x]` 已完成 |
| LLM-4 管理与交付 | 管理员模型中心、真实基础设施验证、90 天清理与文档收口（Tasks 11–14） | `[x]` 本地完成；生产部署待授权 |

P3 的模块路由、故障转移、统一网关和评测平台不在本阶段范围内。
 
 ## M0 — 开发环境搭建 ✅
 
 - `[x]` git / python3.12 / node@22 / docker 已就绪
 - `[x]` 端口规划：后端 8001 · 前端 5173 · PG 5433 · Redis 6380（避开 smartalpha 占用的 8000/6379/5432）
 - `[ ]` ICP 备案启动（非开发任务，用户自行处理）
 
 ---
 
 ## M1 — 项目骨架 + 用户系统 + 首页行情 ✅
 
 ### M1-1 项目骨架 + Docker Compose
 - `[x]` 创建 backend/ frontend/ deploy/ 目录结构
 - `[x]` deploy/docker-compose.dev.yml（postgres:16 @5433、redis:7 @6380）
 - `[x]` backend/requirements.txt（fastapi, uvicorn, sqlalchemy, alembic, redis, arq, akshare, pydantic, pyjwt, bcrypt, pytest, httpx, respx）
 - `[x]` frontend/package.json（vite, react18, typescript, tailwind, echarts, zustand, tanstack-query, axios, react-router-dom）
 
 ### M1-2 后端 FastAPI 基座
 - `[x]` app/main.py — FastAPI 入口 + CORS + 路由挂载
 - `[x]` app/core/config.py — Settings（环境变量读取，含 DB/Redis/JWT 配置）
 - `[x]` app/core/logger.py — 统一日志
 - `[x]` app/core/response.py — 统一响应壳 {code, message, data}
 - `[x]` app/api/health.py — GET /api/health
 - `[x]` pytest 测试：health 返回结构正确
 
 ### M1-3 后端 用户系统（TDD）
 - `[x]` models/users.py — users 表定义（id, username, email, password_hash, role, tier, tier_expire_at, is_active, timestamps）
 - `[x]` core/security.py — bcrypt 哈希/校验 + JWT 生成/验证
 - `[x]` core/redis.py — Redis 连接 + 验证码存取
 - `[x]` api/auth.py — 7 个接口（send-code/register/login/refresh/me/forgot/reset）
 - `[x]` schemas/auth.py — Pydantic 入参/出参
 - `[x]` alembic 迁移初始化 + first migration
 - `[x]` pytest 全绿：注册/登录/refresh/me/忘记密码/重置密码/错误场景
 
 ### M1-4 后端 akshare 取数封装
 - `[x]` datasource/akshare_client.py — get_market_indices / get_stock_info（历史数据源封装）
 - `[x]` datasource/cache.py — Redis 缓存层（指数60s / 板块5min / 个股30s）
 - `[x]` api/market.py — /api/stocks/market-indices（热点中心接口已迁移至 DataHub）
 - `[x]` pytest 全绿（mock akshare 返回固定 DataFrame）
 
 ### M1-5 前端骨架 + 登录注册
 - `[x]` Vite + React18 + TS + Tailwind + Router 起手
 - `[x]` api/client.ts — axios 封装 + 401 自动 refresh
 - `[x]` stores/auth.ts — zustand 用户/登录态
 - `[x]` components/Layout.tsx — 左侧导航 + 顶栏（8 模块占位页）
 - `[x]` pages/Login.tsx — 登录/注册/忘记密码 Tab + 验证码倒计时
 - `[x]` 路由守卫：未登录跳 /login
 
 ### M1-6 前端 首页
 - `[x]` pages/Home.tsx — 行业/题材热点、代表股联动与大盘云图（ECharts）
 - `[x]` 大盘指数卡片（5 指数 + 每分钟轮询 + 刷新按钮 + 更新时间）
 - `[x]` 大盘云图（ECharts 热力图初版）
 - `[x]` 代表个股卡片列表
 
 ### M1-7 收尾
 - `[x]` .env.example（后端 + 前端）
 - `[x]` README.md — 启动说明
 - `[x]` pytest 全绿
 - `[x]` Playwright 抽查：登录/注册/首页
 - `[x]` 更新 TODOS.md
 
 ---
 
## M2 — 股票分析（核心 AI 流程）

### 2026-08-23 渐进式单股分析修复
- `[x]` DataHub 通用识别单个 Pydantic 模型，覆盖快照、财务和资金流返回
- `[x]` 任务错误统一输出稳定 `code.value` 与中文安全消息；轮询 API 增加 `phase/steps` 脱敏状态
- `[x]` 单股分析扩展为技术、基本面、资金、消息、情绪、风险六位分析师，首席会议纳入风险输入
- `[x]` 报告保存近 60 根可序列化 K 线；前端支持 K 线/成交量/均线、六位结构化卡片和已验证结果渐进展示
- `[x]` 保持历史报告、批量分析和旧报告缺少 risk/kline 时的兼容；补充桌面与 390px Playwright mock flow
- `[x]` 返修验收：单股提交并行加载 snapshot（失败可降级且不打断轮询）、跨源单例统一 Mapping、常见 DataHub 错误安全映射、技术面短/中/长期结构化展示
- `[x]` 生产任务可靠性返修：Worker 显式加载用户/模型配置 metadata，claim 早期异常安全落 failed，避免任务永久 pending

- `[x]` 技术指标计算模块（MA/MACD/RSI/KDJ/BOLL）+ 11 项测试 ✅
- `[x]` LLM 统一调用与用量记账 + 3 项测试 ✅
- `[x]` 异步任务框架（arq 配置 + TASK_INLINE 内联模式）+ 投递/执行/轮询/台账
- `[x]` 6 分析师提示词 + 并行编排 + 投研会议汇总 + 2 项测试 ✅
- `[x]` 股票分析接口 + 配额检查（submit/poll/history/snapshot/detail）
- `[x]` 前端单股分析页（进度/报告/技术指标面板/6 分析师卡片/决策卡片）
- `[x]` 前端批量分析 + 历史记录页（三 Tab 布局）
- `[x]` 端到端验收（Playwright E2E：提交→轮询→报告渲染→历史查看）✅
- `[~]` PDF 导出（已预留按钮，显示"即将开放"，后续迭代补充）

---
 
## M3 — 主力选股 + 智策板块 + 龙虎榜

- `[x]` M3-1: akshare 数据采集扩展（7 个新函数：资金流排行/股东户数/申万板块/板块资金流/龙虎榜明细/游资席位）✅
- `[x]` M3-2: DB 迁移 — main_force_runs / sector_reports / dragon_tiger_reports 三张表 ✅
- `[x]` M3-3: 主力选股编排服务（资金流排行→策略过滤→5 分析师并行→资深研究员）+ 4 项测试 ✅
- `[x]` M3-4: 智策板块编排服务（4 智能体 + 多空预测 + 首席汇总）+ 1 项测试 ✅
- `[x]` M3-5: 龙虎榜评分引擎（纯函数 score_stock/rank_top_stocks/compute_stats/rank_institutions）+ 编排服务 + 10 项测试 ✅
- `[x]` M3-6: APScheduler 定时任务框架（板块分析每日 09:30 自动执行）✅
- `[x]` M3-7: 前端三页面（MainForce 漏斗+分析师+研究员/Sector 4 智能体+多空/DragonTiger TOP10+游资画像）+ 路由替换 ✅
- `[x]` M3-8: Playwright E2E 全验证通过（0 console errors）+ 52 pytest 全绿 ✅
- `[x]` 提示词占位符转义修复（{{ANALYST_KEY:x}} f-string 转义后 → 单花括号匹配）✅

### M3 新增文件
- backend: main_force_orchestrator.py / sector_orchestrator.py / dragon_tiger_scorer.py / dragon_tiger_orchestrator.py / scheduler.py / tasks/main_force.py / tasks/sector_analysis.py / tasks/dragon_tiger.py / api/m3.py / models (3) / tests (3)
- frontend: pages/MainForce.tsx / pages/Sector.tsx / pages/DragonTiger.tsx / App.tsx 路由替换
- docs: M3-engineering-plan.md / screenshots/m3/ (6 张 E2E 截图)

---
 
 ## M4 — 持仓分析 + 实时监测 + 风险预警
 
 - `[x]` 持仓 CRUD + 组合汇总 + AI 诊断
 - `[x]` 实时监测引擎（盯盘配置 + 轮询 + 通知）
 - `[x]` 风险预警规则引擎 + 四级分级 + 组合风险
 
 ---
 
 ## M5 — 实时新闻 + 美股研报 + 定时任务
 
 - `[x]` 新闻 RSS 采集 + 去重 + 规则分类
 - `[x]` 新闻页（时间/来源筛选）
 - `[x]` 美股隔夜研报（八段式 + 映射 A 股方向）
 - `[x]` 定时任务全套上线 + 失败告警
 
 ---
 
 ## M6 — 会员体系 + 配额 + 充值
 
 - `[x]` 会员等级 + 配额矩阵落库
 - `[x]` 配额拦截中间件 + 用量查询
 - `[x]` 充值中心页面
 - `[x]` 新用户试用 + 到期降级
 
 ---
 
 ## M7 — 部署上京东云
 
 - `[x]` docker-compose.prod.yml（全容器化 + nginx）→ `deploy/docker-compose.yml`
 - `[x]` GitHub Actions CI/CD（测试全绿才部署）→ `.github/workflows/deploy.yml`
 - `[x]` 域名解析 + HTTPS 配置就绪（nginx 443 块 + acme.sh 步骤，见 deploy/README.md；待买服务器+备案后执行）
 - `[x]` 上线检查清单已写入手册（deploy/README.md 第六节；待服务器就绪后逐项打勾）
 - `[x]` 数据库每日备份脚本 → `deploy/backup.sh`（pg_dump + 保留 14 天 + crontab 一行）
 - `[x]` 与 GS-Tracker 共存改造：compose 项目名 `aistock`、对外端口 8080/8443、nginx.conf 挂载化、CI 冒烟端口修正、SMTP 变量透传
 - `[x]` 小白版共存部署手册 → `DEPLOY.md`（10 步全流程 + HTTPS 8443 免备案方案 + 故障排查）
 - `[ ]` 用户按 DEPLOY.md 在服务器上执行部署（待执行）
 
 ---
 
## 功能补全计划（部署后推进）

### a. F-03-06 导出 PDF 报告 ✅
- `[x]` 后端 reportlab PDF 生成服务（中文 CID 字体、决策卡、分析师分报告、免责声明）
- `[x]` API `GET /stocks/user/results/{id}/pdf` 下载端点
- `[x]` 前端分析报告页导出按钮（Blob 下载）
- `[x]` 6 项后端测试全绿 + Playwright E2E 验证

### b. F-08-04 AI 交易计划 + AI 决策记录 ✅
- `[x]` 数据模型 ai_trade_plans / ai_decision_records + alembic 迁移
- `[x]` API GET /stocks/ai-monitoring/trade-plans + /decisions（分页 + 用户隔离）
- `[x]` 前端 Realtime.tsx 两个新 Tab（空态 + 卡片列表）
- `[x]` 7 项 pytest + Playwright E2E 验证

### c. SMTP 邮件发送 ✅
- `[x]] email_sender.py（aiosmtplib，EMAIL_ENABLED 开关控制 dev/prod）
- `[x]` verify_code.py 改为 async + 调用 send_email
- `[x]` auth.py 两处调用改为 await
- `[x]` config.py 新增 SMTP_* 配置项 + .env.example
- `[x]` 4 项 pytest + 120 全量测试全绿

### d. 移动端 390px 全页面体检 ✅
- `[x]` 12 页 Playwright 390px 截图（含 login）
- `[x]` Layout 响应式：sidebar 手机端收窄、topbar padding 缩小、username 隐藏
- `[x]` 7 处 table 包裹 overflow-x-auto（Analysis/DragonTiger/MainForce/Sector/Portfolio/Realtime/Membership）
- `[x]` Home 板块按钮 flex-wrap、Portfolio 输入框响应式宽度
- `[x]` 零水平溢出、所有 off-screen 元素均在滚动容器内

### e. 用户使用手册 ✅
- `[x]` docs/用户使用手册.md（319 行，12 模块 + FAQ，面向技术小白）

### f. 使用指南侧边栏页面 ✅
- `[x]` 前端 Guide.tsx（12 模块分步指引 + FAQ，小白可读）
- `[x]` 侧边栏入口（会员中心上方）+ /guide 路由
- `[x]` Playwright E2E 验证

### g. 系统配置页面（管理员专属）✅
- `[x]` 后端 /api/admin/* 路由组（get_admin_user 鉴权，非管理员 403）
- `[x]` 平台统计 / 用户管理（分页、启停、等级、角色）/ 大模型配置（密钥脱敏 + 近7天用量）/ 数据源配置（akshare 连通性测试）/ Agent 配置
- `[x]` 前端 Admin.tsx 五标签页 + 侧边栏管理员专属入口 + /admin 路由
- `[x]` test_admin.py 16 例，全量 136 测试全绿 + Playwright E2E（含非管理员拦截）验证

---
## Open Design 全站换肤（Zapier 风设计稿）✅
- `[x]` 设计源 app.css 落地 frontend/src/styles/app.css（cream 底/深侧栏/橙强调/边框优先无阴影/A股红涨绿跌）
- `[x]` 13 个页面全部重写适配新设计体系，tsc 零报错、npm run build 通过
- `[x]` 顺带修复 Sector 页数据映射 bug（接口返回 bull_sectors/bear_sectors/neutral_sectors/operation_advice 顶层字段，旧代码读 report.decision.* 恒为空）
- `[x]` 移除 echarts 依赖、删除 Placeholder.tsx
- `[x]` Playwright 全站截图验证：13 路由桌面 1440px + 5 路由移动 390px 全部正常，无控制台报错

---
> 最后更新：2026-08-11
> 当前阶段：M0-M7、功能补全 a-g、全站换肤全部完成 ✅；部署文档已就绪（DEPLOY.md），待用户在服务器执行部署
