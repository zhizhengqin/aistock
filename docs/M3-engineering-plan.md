# M3 工程方案 — 主力选股 + 智策板块 + 龙虎榜 + 定时任务

> 阶段：gstack /plan-eng-review 产出物 · 待用户确认后进入开发
> 创建：2026-08-08

---

## 1. 范围与目标

| 模块 | PRD编号 | 优先级 | 核心交付 |
|---|---|---|---|
| 主力选股 | F-04 | P0 | 资金流排行→策略过滤→5分析师+资深研究员→推荐3-5只 |
| 智策板块 | F-05 | P0 | 4智能体→多空预测→定时自动分析 |
| 智瞰龙虎榜 | F-06 | P1 | 时间范围选择→评分引擎→TOP10+游资画像 |
| 定时任务 | 架构9.1 | P0 | APScheduler接入，板块分析每日自动执行 |

## 2. 技术决策

### 2.1 复用已有基础设施
- LLM 客户端：复用 `app/core/llm.py`（Mock 模式开发，DeepSeek 生产）
- 异步任务框架：复用 `app/tasks/`（arq + TASK_INLINE 内联模式）
- 任务台账：复用 `task_records` 表，新增 task_type: `main_force` / `sector_analysis` / `dragon_tiger`
- LLM 记账：复用 `llm_usage` 表
- 缓存层：复用 `app/datasource/cache.py`
- 进度上报：复用 `app/services/progress.py`

### 2.2 新增数据采集（akshare_client.py 扩展）
| 函数 | akshare 接口 | 缓存TTL | 用途 |
|---|---|---|---|
| `get_market_capital_flow_rank()` | `stock_market_fund_flow` | 5min | 全市场资金流排行 |
| `get_stock_shareholder_count()` | `stock_zh_a_gdhs` | 24h | 股东户数变化 |
| `get_sw_sector_list()` | `sw_index_spot` | 1h | 申万板块列表 |
| `get_sw_sector_detail()` | `sw_index_daily` | 30min | 申万板块行情 |
| `get_sector_capital_flow()` | `stock_sector_fund_flow_rank` | 5min | 板块资金流 |
| `get_dragon_tiger_list()` | `stock_lhb_detail_em` | 30min | 龙虎榜明细 |
| `get_dragon_tiger_institution()` | `stock_lhb_stock_detail_em` | 30min | 游资席位 |

### 2.3 新增数据库表
| 表 | 用途 | 主要字段 |
|---|---|---|
| `main_force_runs` | 主力选股批次 | id, user_id, run_date, candidates_count, filtered_count, recommended_json, token_total, task_id |
| `sector_reports` | 板块分析报告 | id, report_date, bull_json, bear_json, neutral_json, rotation_json, summary_json, task_id |
| `dragon_tiger_reports` | 龙虎榜报告 | id, user_id, period_days, stats_json, top_stocks_json, analysis_text, task_id |

### 2.4 新增 API 端点
| 模块 | 端点 | 方法 | 说明 |
|---|---|---|---|
| 主力选股 | `/api/stocks/main-force/run` | POST | 触发选股任务 |
| 主力选股 | `/api/stocks/main-force/history` | GET | 历史选股记录 |
| 主力选股 | `/api/stocks/main-force/{run_id}` | GET | 单次选股详情 |
| 智策板块 | `/api/stocks/sectors/analyze` | POST | 触发板块分析 |
| 智策板块 | `/api/stocks/sectors/reports/latest` | GET | 最新报告 |
| 智策板块 | `/api/stocks/sectors/reports/history` | GET | 历史报告 |
| 龙虎榜 | `/api/stocks/dragon-tiger/analyze` | POST | 触发分析(带period_days) |
| 龙虎榜 | `/api/stocks/dragon-tiger/reports` | GET | 历史报告 |
| 龙虎榜 | `/api/stocks/dragon-tiger/stats` | GET | 数据统计 |

## 3. 模块详细设计

### 3.1 主力选股流水线
```
全市场资金流排行(TOP40)
  -> 策略过滤：流通市值>60亿 / 20日涨幅<10% / 60日净流入>0 / 股东户数下降
    -> 5 分析师并行（资金流向/行业板块/财务基本面/技术形态/量化）
      -> 资深研究员综合精选 -> 输出3-5只推荐
```
- 服务文件：`app/services/main_force_orchestrator.py`
- 任务文件：`app/tasks/main_force.py`
- Mock 模式：`{{ANALYST_KEY:main_force_capital}}` 等标记

### 3.2 智策板块 4 智能体
- 宏观策略师 / 板块诊断师 / 资金流向分析师 / 市场情绪解码员
- 产出：看多/看空/中性板块列表 + 置信度 + 操作节奏建议 + 风险触发条件 + 核心跟踪指标
- 服务文件：`app/services/sector_orchestrator.py`
- 任务文件：`app/tasks/sector_analysis.py`

### 3.3 龙虎榜评分引擎
- 规则评分（非AI）：净流入金额 / 上榜次数 / 游资成功率 / 买卖比 -> 综合评分0-100 + 等级(A/B/C/D)
- AI 分析：游资行为分析师出策略建议 + 活跃游资画像
- 服务文件：`app/services/dragon_tiger_orchestrator.py`
- 评分引擎：`app/services/dragon_tiger_scorer.py`

### 3.4 定时任务框架
- `app/tasks/scheduler.py` — APScheduler 配置
- 当前阶段只接入"板块分析每交易日 09:30"
- TASK_INLINE 模式下 scheduler 跑在 uvicorn 事件循环中
- 生产环境跑在 arq worker 内

## 4. 前端页面

| 页面 | 路由 | 文件 | 说明 |
|---|---|---|---|
| 主力选股 | `/main-force` | `pages/MainForce.tsx` | 漏斗+5分析师卡片+研究员+推荐清单+Token+历史 |
| 智策板块 | `/sector` | `pages/Sector.tsx` | 4智能体卡片+多空预测+操作建议+历史 |
| 龙虎榜 | `/dragon-tiger` | `pages/DragonTiger.tsx` | 时间选择+摘要+TOP10表格+AI建议+历史/统计 |

## 5. 开发顺序（子任务）

| # | 子任务 | 类型 | 依赖 |
|---|---|---|---|
| M3-1 | akshare 数据采集扩展（7个新函数） | 后端 | 无 |
| M3-2 | DB 迁移（3张新表） | 后端 | 无 |
| M3-3 | 主力选股编排服务 + 任务 + API + 测试 | 后端 | M3-1, M3-2 |
| M3-4 | 智策板块编排服务 + 任务 + API + 测试 | 后端 | M3-1, M3-2 |
| M3-5 | 龙虎榜评分引擎 + 编排服务 + 任务 + API + 测试 | 后端 | M3-1, M3-2 |
| M3-6 | APScheduler 定时任务框架 | 后端 | M3-4 |
| M3-7 | 前端三个页面 + 路由 + E2E | 前端 | M3-3,4,5 |
| M3-8 | 全量测试 + Playwright E2E + 提交 | 收尾 | M3-1~7 |

## 6. 成本控制
- 复用 `llm_usage` 表记账
- 系统级每日 token 总闸：config 新增 `LLM_DAILY_TOKEN_CAP`（默认 200 万）
- Mock 模式开发期间不消耗真实 token
- 主力选股一次约 8.3 万 token，默认限制每日 2 次
