 # 睿见投研 — 开发进度跟踪（TODOS）
 
 > 本文件是项目开发的"施工进度看板"。每完成一个阶段就更新对应状态标记。
 > 状态图例：`[ ]` 待办 · `[~]` 进行中 · `[x]` 已完成
 
 ---
 
 ## 总览
 
 | 阶段 | 内容 | 预计工期 | 状态 |
 |---|---|---|---|
 | M0 | 开发环境搭建 | 2 天 | `[x]` 已确认 |
 | M1 | 项目骨架 + 用户系统 + 首页行情 | 1 周 | `[x]` 已完成 |
 | M2 | 股票分析（核心 AI 流程） | 1.5 周 | `[ ]` |
 | M3 | 主力选股 + 智策板块 + 龙虎榜 | 1.5 周 | `[ ]` |
 | M4 | 持仓分析 + 实时监测 + 风险预警 | 1 周 | `[ ]` |
 | M5 | 实时新闻 + 美股研报 + 定时任务 | 1 周 | `[ ]` |
 | M6 | 会员体系 + 配额 + 充值页 | 1 周 | `[ ]` |
 | M7 | 京东云部署 + 域名 + HTTPS + 备份 | 1 周 | `[ ]` |
 
 ---
 
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
 - `[x]` datasource/akshare_client.py — get_market_indices / get_sector_kline / get_stock_info
 - `[x]` datasource/cache.py — Redis 缓存层（指数60s / 板块5min / 个股30s）
 - `[x]` api/market.py — /api/stocks/market-indices / /api/stocks/sectors/overview
 - `[x]` pytest 全绿（mock akshare 返回固定 DataFrame）
 
 ### M1-5 前端骨架 + 登录注册
 - `[x]` Vite + React18 + TS + Tailwind + Router 起手
 - `[x]` api/client.ts — axios 封装 + 401 自动 refresh
 - `[x]` stores/auth.ts — zustand 用户/登录态
 - `[x]` components/Layout.tsx — 左侧导航 + 顶栏（8 模块占位页）
 - `[x]` pages/Login.tsx — 登录/注册/忘记密码 Tab + 验证码倒计时
 - `[x]` 路由守卫：未登录跳 /login
 
 ### M1-6 前端 首页
 - `[x]` pages/Home.tsx — 6 板块切换 + K线图（ECharts）
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
 
 - `[ ]` 技术指标计算模块（MA/MACD/RSI/KDJ/BOLL）+ 测试
 - `[ ]` LLM 客户端封装（DeepSeek）+ llm_usage 记账
 - `[ ]` 异步任务框架（arq 投递/执行/轮询/台账）
 - `[ ]` 6 分析师提示词 + 并行编排 + 投研会议汇总
 - `[ ]` 股票分析接口 + 配额检查
 - `[ ]` 前端单股分析页（进度/报告/技术指标面板/走势图）
 - `[ ]` 前端批量分析 + 历史记录页
 - `[ ]` 端到端验收（茅台 600519 完整分析）
 
 ---
 
 ## M3 — 主力选股 + 智策板块 + 龙虎榜
 
 - `[ ]` 主力选股流水线（资金流排行 → 策略过滤 → 5 分析师 → 资深研究员）
 - `[ ]` 智策板块（4 智能体 + 多空预测 + 定时任务）
 - `[ ]` 智瞰龙虎榜（评分引擎 + TOP10 + 游资画像）
 - `[ ]` APScheduler 定时任务框架接入
 
 ---
 
 ## M4 — 持仓分析 + 实时监测 + 风险预警
 
 - `[ ]` 持仓 CRUD + 组合汇总 + AI 诊断
 - `[ ]` 实时监测引擎（盯盘配置 + 轮询 + 通知）
 - `[ ]` 风险预警规则引擎 + 四级分级 + 组合风险
 
 ---
 
 ## M5 — 实时新闻 + 美股研报 + 定时任务
 
 - `[ ]` 新闻 RSS 采集 + 去重 + AI 标注
 - `[ ]` 新闻页（时间/来源筛选）
 - `[ ]` 美股隔夜研报（八段式 + 映射 A 股方向）
 - `[ ]` 定时任务全套上线 + 失败告警
 
 ---
 
 ## M6 — 会员体系 + 配额 + 充值
 
 - `[ ]` 会员等级 + 配额矩阵落库
 - `[ ]` 配额拦截中间件 + 用量查询
 - `[ ]` 充值中心页面
 - `[ ]` 新用户试用 + 到期降级
 
 ---
 
 ## M7 — 部署上京东云
 
 - `[ ]` docker-compose.prod.yml（全容器化 + nginx）
 - `[ ]` GitHub Actions CI/CD
 - `[ ]` 域名解析 + HTTPS（Let's Encrypt）
 - `[ ]` 上线检查清单逐项验证
 - `[ ]` 数据库每日备份脚本
 
 ---
 
 > 最后更新：2026-08-07
 > 当前阶段：M1 已完成 ✅ → 下一步 M2 股票分析
