# 首页热点中心与大盘云图 Implementation Plan

> **For agentic workers:** 本项目按 `AGENTS.md` 只允许一次 `aistock_luna_implementer` Build；实施时必须使用 `superpowers:test-driven-development`，完成前使用 `superpowers:verification-before-completion`。不得启动额外 implementer/reviewer，也不得提交、推送或部署。

**Goal:** 把首页从固定六分类升级为当天真实热门行业与题材中心，支持代表个股联动、两级大盘云图、历史趋势与盘后快照，并为侧边栏补齐语义图标。

**Architecture:** DataHub 只提供全量原始板块行情与成分股契约；`HotspotService` 统一计算热度、趋势、云图和缓存/快照回退。首页通过一个数据 Hook 管理四类独立状态，页面组件只负责展示和交互；盘后纯数据任务复用现有任务提交、outbox、APScheduler 与 `SnapshotStore`。

**Tech Stack:** FastAPI、Pydantic v2、SQLAlchemy、PostgreSQL/SQLite tests、Redis/ARQ/APScheduler、React 18、TypeScript、Vitest、Playwright、ECharts、lucide-react。

**Spec:** `/Users/qinzz/.gstack/projects/zhizhengqin-aistock/qinzz-main-design-20260822-225331.md`

## Global Constraints

- 用户可见内容全部使用中文；A 股红涨绿跌，并同时显示正负号和数值。
- 东方财富公开数据为默认主源，不依赖 Tushare Token，不调用大模型，定时任务必须 `requires_llm=False`。
- 复用现有 DataHub Router、五分钟缓存、`data_snapshots`、`data_ingestion_runs`、TaskSubmissionService 和 worker 调度器。
- 不新增数据库表、迁移、容器或第二套图表库；保留 730 天快照。
- 行业、题材、代表股与指数独立加载和降级；不同交易日的数据必须分别标注，不能伪装成同一时点。
- UI 必须通过桌面与 390px Playwright 验收，无横向溢出，关键内容不能只靠 hover 查看。
- 未经用户明确要求，不提交、不推送、不部署。

## Engineering Review Decisions

| 决策 | 结论 |
|---|---|
| 1A | DataHub 能力命名为 `market.board_quotes`，获取全量横截面后再算分与截取。 |
| 2A | 各模块可以独立回退，但必须分别显示交易日、来源和“历史数据”。 |
| 3A | 盘后任务使用进程级非阻塞锁，重叠触发直接记录跳过。 |
| 4A | 新增独立 `/api/stocks/market-cloud`；行业全量、题材按市值前 80。 |
| 5A | 删除旧固定分类接口和 `MARKET_SECTOR_OVERVIEW/SectorOverview` 数据链。 |
| 6A | 首页拆成编排页、数据 Hook、热点、代表股、云图、共享类型与工具。 |
| 7A | 历史快照查询集中扩展在现有 `SnapshotStore`。 |
| 8A | 默认使用真实结构脱敏夹具；live-smoke 手动运行，不阻塞 pytest。 |
| 9A | Vitest 覆盖分支，Playwright 覆盖桌面/390px 完整交互与异常体验。 |
| 10A | 每类一次读取最近六份完整快照，在内存批量计算趋势。 |
| 11A | 独立热点轮动历史页写入 `TODOS.md`，不纳入本次 Build。 |

## File Structure

### Backend

- Modify `backend/app/datahub/contracts.py` — 新增 `BoardQuote`、`BoardConstituent` 与两个原始能力；删除旧 `SectorOverview` 能力。
- Modify `backend/app/datahub/providers/eastmoney.py` — 行业、题材和成分股请求及真实字段映射。
- Modify `backend/app/datahub/{consumer,platform,registry,runtime,validators}.py` — 注册、路由、缓存解码、校验和消费者入口。
- Modify `backend/app/datahub/providers/base.py` — 新能力的保守探测参数。
- Modify `backend/app/datahub/ingestion.py` — 集中提供最新快照与最近历史查询。
- Create `backend/app/schemas/market_hotspots.py` — 产品层热点、云图、趋势和代表股响应模型。
- Create `backend/app/services/market_hotspots.py` — 热度、趋势、云图、实时/快照回退和盘后采集编排。
- Modify `backend/app/api/market.py` — 三个新接口并删除固定分类接口。
- Create `backend/app/tasks/market_hotspot_snapshot.py` — 纯数据快照任务和非阻塞锁。
- Modify `backend/app/tasks/{scheduler,queue}.py`、`backend/app/services/outbox_dispatcher.py` — 定时提交、ARQ 注册与 outbox 映射。
- Create `backend/scripts/smoke_market_hotspots.py` — 手动 live-smoke。
- Create/modify对应的 `backend/tests/` 单元与集成测试文件。

### Frontend

- Modify `frontend/package.json`、`frontend/package-lock.json` — 新增 `lucide-react` 与首页 E2E script。
- Create `frontend/src/features/home-market/{types,format,useHomeMarketData}.ts` — 类型、格式化和请求状态。
- Create `frontend/src/features/home-market/{HotspotPanels,RepresentativeStocks,MarketTreemap}.tsx` — 三个展示组件。
- Create对应的 `*.test.ts` / `*.test.tsx` 测试。
- Modify `frontend/src/pages/Home.tsx`、`frontend/src/pages/Home.test.tsx` — 页面编排与回归测试。
- Modify `frontend/src/components/Layout.tsx`、`frontend/src/components/Layout.test.tsx` — 侧边栏图标映射与可访问性测试。
- Modify `frontend/src/styles/app.css` — 首页热点、股票列表、云图、图标和 390px 样式。
- Create `frontend/tests/e2e/home-market.mjs`；modify `frontend/tests/e2e/{datahub,mobile-responsive}.mjs` — 完整交互与旧拦截迁移。

### Documentation

- Modify `TODOS.md`、`frontend/src/pages/Guide.tsx`、`docs/用户使用手册.md` — 进度、名词解释、操作步骤和数据时效说明。

## Data Flow

```text
GET /market-hotspots ─┐
GET /market-cloud ────┼─> HotspotService ─> DataHub Router ─> EastmoneyProvider
GET /constituents ────┘          │                 │                │
                                 │                 └─ Redis fresh/last-good
                                 │
                                 ├─ success: product mapping + per-module meta
                                 └─ provider failure: SnapshotStore.latest() ─> stale response / 503

APScheduler 15:10,20,30 ─> TaskSubmission(requires_llm=False) ─> outbox/ARQ
                                                             └─> non-blocking lock
                                                                 ├─ locked: task result=skipped
                                                                 └─ acquired: board quotes
                                                                    ├─ save industry/theme snapshots
                                                                    ├─ fetch top 12×2 constituents
                                                                    ├─ save successes independently
                                                                    └─ record run + cleanup(730d)

React Home ─> useHomeMarketData
             ├─ indices state
             ├─ industry hotspots state
             ├─ theme hotspots state
             ├─ market cloud state
             └─ selected board constituents state (AbortController + sequence guard)
                    ├─ HotspotPanels
                    ├─ RepresentativeStocks
                    └─ MarketTreemap (board level ⇄ stock level)
```

### Task 1: Replace the legacy sector capability with typed board capabilities

**Files:**
- Modify: `backend/app/datahub/contracts.py`
- Modify: `backend/app/datahub/providers/eastmoney.py`
- Modify: `backend/app/datahub/providers/base.py`
- Modify: `backend/app/datahub/consumer.py`
- Modify: `backend/app/datahub/platform.py`
- Modify: `backend/app/datahub/registry.py`
- Modify: `backend/app/datahub/runtime.py`
- Modify: `backend/app/datahub/validators.py`
- Create: `backend/tests/datahub/test_market_board_capabilities.py`
- Modify: `backend/tests/datahub/test_capability_matrix.py`

**Interfaces:**
- Produces: `Capability.MARKET_BOARD_QUOTES`, `Capability.MARKET_BOARD_CONSTITUENTS`.
- Produces: `BoardQuote` and `BoardConstituent` Pydantic contracts.
- Produces: `get_market_board_quotes(kind)` and `get_market_board_constituents(kind, board_code)` async consumers.

- [x] **Step 1: Write failing contract and provider tests**

  Test industry selector `m:90+t:2`, theme selector `m:90+t:3`, constituent selector `b:BK0475`, strict kind/code validation, all required request fields, real leader mapping `f128=浦发银行`、`f140=600000`、`f136=2.5`, and constituent mapping `f2/f3/f6/f12/f14/f20/f124`. Assert numeric missing fields remain `None` instead of silently becoming zero.

- [x] **Step 2: Run the focused tests and confirm RED**

  Run: `cd backend && .venv/bin/pytest -q tests/datahub/test_market_board_capabilities.py tests/datahub/test_capability_matrix.py`

  Expected: FAIL because the two capabilities and contracts do not exist and the current leader fixture uses the wrong field mapping.

- [x] **Step 3: Implement the minimal typed provider boundary**

  Add contracts equivalent to:

  ```python
  class BoardQuote(BaseModel):
      board_code: str
      board_name: str
      kind: Literal["industry", "theme"]
      change_pct: float | None = None
      turnover: float | None = None
      market_cap: float | None = None
      rise_count: int | None = None
      fall_count: int | None = None
      flat_count: int | None = None
      leader_code: str | None = None
      leader_name: str | None = None
      leader_change_pct: float | None = None
      data_at: datetime

  class BoardConstituent(BaseModel):
      code: str
      name: str
      price: float | None = None
      change_pct: float | None = None
      turnover: float | None = None
      market_cap: float | None = None
      data_at: datetime
  ```

  Fetch the full board cross-section without accepting a product `limit`; request `f12,f14,f3,f6,f20,f104,f105,f106,f128,f136,f140,f124`. Constituents accept validated `BK` code and provider `limit`, request `f2,f3,f6,f12,f14,f20,f124`, and normalize stock codes at the provider boundary.

- [x] **Step 4: Remove the old capability chain**

  Delete `MARKET_SECTOR_OVERVIEW`, `SectorOverview`, `get_sector_kline`, its route/registry/runtime/validator branches, old probe fixture, and `_representative_stocks`. Do not remove `SECTOR_REALTIME`, `SECTOR_KLINE` or `SECTOR_FUND_FLOW`, which serve the separate“智策板块”模块。

- [x] **Step 5: Run focused and DataHub regression tests**

  Run: `cd backend && .venv/bin/pytest -q tests/datahub/test_market_board_capabilities.py tests/datahub/test_capability_matrix.py tests/datahub/test_contracts.py tests/datahub/test_router.py`

  Expected: PASS; provider dictionaries do not escape the DataHub boundary.

### Task 2: Add snapshot reads, hotspot scoring, trends and fallback

**Files:**
- Modify: `backend/app/datahub/ingestion.py`
- Create: `backend/app/schemas/market_hotspots.py`
- Create: `backend/app/services/market_hotspots.py`
- Modify: `backend/tests/datahub/test_ingestion.py`
- Create: `backend/tests/services/test_market_hotspots.py`

**Interfaces:**
- Consumes: Task 1 `BoardQuote`, `BoardConstituent` and consumers.
- Produces: `SnapshotStore.latest(...)`, `SnapshotStore.history(...)`.
- Produces: `HotspotService.get_hotspots()`, `.get_market_cloud()`, `.get_constituents()`, `.capture_daily_snapshot()`.

- [x] **Step 1: Write failing SnapshotStore read tests**

  Insert multiple datasets, scopes and trade dates. Assert `latest()` returns the newest matching identity and `history(limit=6)` returns at most six distinct trade dates newest-first without leaking another scope or source.

- [x] **Step 2: Write failing pure scoring and trend tests**

  Cover shuffled input stability, percentile endpoints, same-value percentile, tie-break order, `log1p(turnover)`, missing factor reweighting with warnings, omission when `change_pct` is missing, empty data, and one-item data. Cover `new`、`heating`、`cooling`、`steady`、`insufficient_history`、`streak_days` and positive `rank_change`.

- [x] **Step 3: Run the focused tests and confirm RED**

  Run: `cd backend && .venv/bin/pytest -q tests/datahub/test_ingestion.py tests/services/test_market_hotspots.py`

- [x] **Step 4: Implement models and deterministic algorithms**

  Product models must include `board_code`, `board_name`, `kind`, raw market fields, `hot_score`, `rank`, `trend_status`, `streak_days`, `rank_change`, and per-dataset metadata. Percentile rank uses deterministic sorted values; missing factors reweight only available weights; score rounds once at one decimal after summation.

- [x] **Step 5: Implement bulk history and fallback**

  `get_hotspots(kind, limit=12)` fetches full live quotes, calculates all scores, queries six hotspot snapshots once, annotates trends in memory, then slices. On DataHub failure, return the latest `market.hotspots.v1` snapshot with `freshness=stale` and provider `历史快照`; when neither exists, re-raise a typed 503.

  `get_constituents()` follows live → DataHub last-good → `market.board_constituents.v1` latest snapshot → typed 503. It preserves its own trade date even when the selected hotspot is newer. `get_market_cloud()` returns all industry nodes or the top 80 theme nodes by non-null market cap, followed by stable board-code ordering.

- [x] **Step 6: Test each fallback branch**

  Assert live success, Redis stale metadata passthrough, database fallback, no snapshot 503, mixed-date metadata, incompatible trend baseline hiding, and that six-day history is fetched exactly once per category.

- [x] **Step 7: Run focused tests**

  Run: `cd backend && .venv/bin/pytest -q tests/datahub/test_ingestion.py tests/services/test_market_hotspots.py`

  Expected: PASS with all algorithm, history and failure branches covered.

### Task 3: Expose validated hotspot, cloud and constituent APIs

**Files:**
- Modify: `backend/app/api/market.py`
- Modify: `backend/tests/test_market.py`
- Modify: `backend/tests/datahub/test_capability_matrix.py`

**Interfaces:**
- Consumes: Task 2 `HotspotService` response models.
- Produces: `GET /api/stocks/market-hotspots`, `GET /api/stocks/market-cloud`, `GET /api/stocks/boards/{board_code}/constituents`.

- [x] **Step 1: Write failing route tests**

  Assert industry/theme happy paths, outer `{code,message,data}` envelope, standard DataHub `meta`, defaults `12/80/20`, 422 for invalid kind, `limit<1`, excessive limits and non-`BK` codes, plus Chinese 503 detail when no live/cache/snapshot exists.

- [x] **Step 2: Add route-level regression test for legacy removal**

  Assert `/api/stocks/sectors/overview` returns 404 so fixed categories and fake periods cannot silently return.

- [x] **Step 3: Run route tests and confirm RED**

  Run: `cd backend && .venv/bin/pytest -q tests/test_market.py tests/datahub/test_capability_matrix.py`

- [x] **Step 4: Implement thin endpoints**

  Construct one `HotspotService(db)` per request. Use `Literal["industry", "theme"]`, `Query(ge=1, le=...)`, and `Path(pattern=r"^BK\d{3,6}$")`. Endpoints only translate service result to the standard response and typed `DataHubError` to status/content; do not calculate scores or query snapshots in the API module.

- [x] **Step 5: Run route and full API regression tests**

  Run: `cd backend && .venv/bin/pytest -q tests/test_market.py tests/test_api_contract.py`

  Expected: PASS; legacy route is absent and three new routes validate inputs.

### Task 4: Add the idempotent post-market snapshot task

**Files:**
- Create: `backend/app/tasks/market_hotspot_snapshot.py`
- Modify: `backend/app/tasks/scheduler.py`
- Modify: `backend/app/tasks/queue.py`
- Modify: `backend/app/services/outbox_dispatcher.py`
- Create: `backend/tests/tasks/test_market_hotspot_snapshot.py`
- Modify: `backend/tests/test_worker_settings.py`
- Modify: `backend/tests/services/test_outbox_dispatcher.py`
- Modify: `backend/tests/services/test_task_submission.py`

**Interfaces:**
- Consumes: Task 2 `HotspotService.capture_daily_snapshot()`.
- Produces: ARQ function `market_hotspot_snapshot_task(ctx, task_id)` and task type `market_hotspot_snapshot`.

- [x] **Step 1: Write failing scheduling and routing tests**

  Assert one APScheduler job uses Asia/Shanghai, weekdays, hour 15 and minutes `10,20,30`; submission has no user, zero cost and `requires_llm=False`; outbox resolves to the new ARQ function; `WorkerSettings.functions` contains it.

- [x] **Step 2: Write failing execution tests**

  Cover successful industry/theme snapshots, true provider trade date conversion to Asia/Shanghai, repeat-run upsert identity, one category failure, one board constituent failure, total failure, 730-day cleanup, task result counts, and the second invocation returning `status=skipped` while the module lock is held.

- [x] **Step 3: Run focused tests and confirm RED**

  Run: `cd backend && .venv/bin/pytest -q tests/tasks/test_market_hotspot_snapshot.py tests/test_worker_settings.py tests/services/test_outbox_dispatcher.py tests/services/test_task_submission.py`

- [x] **Step 4: Implement the task through existing reliability primitives**

  Use a module-level `asyncio.Lock`; check `locked()` and return a persisted task result instead of waiting. The acquired path runs through `TaskExecutionRunner`, calls the service without an open DB session during network waits, persists each successful snapshot independently through short sessions, records an `IngestionRun` summary, and raises only when both industry and theme fail.

- [x] **Step 5: Run task and ingestion regression tests**

  Run: `cd backend && .venv/bin/pytest -q tests/tasks/test_market_hotspot_snapshot.py tests/test_worker_settings.py tests/services/test_outbox_dispatcher.py tests/services/test_task_execution.py tests/datahub/test_ingestion.py`

  Expected: PASS; no default model configuration is required.

### Task 5: Add the manual provider live-smoke

**Files:**
- Create: `backend/scripts/smoke_market_hotspots.py`
- Modify: `backend/README.md`

**Interfaces:**
- Consumes: Task 1 DataHub consumers.
- Produces: a non-destructive manual command with exit status 0 only when all three response shapes validate.

- [x] **Step 1: Implement a read-only async smoke command**

  Fetch one industry cross-section, one theme cross-section and the first returned board's first constituent. Print only capability, provider, row count, data time and safe field names; never print credentials or entire payloads.

- [x] **Step 2: Document execution and non-gating policy**

  Document: `cd backend && .venv/bin/python scripts/smoke_market_hotspots.py`. State that 502/timeout is an upstream diagnostic failure and this command is not part of default pytest or CI.

- [x] **Step 3: Validate offline syntax/import behavior**

  Run: `cd backend && .venv/bin/python -m py_compile scripts/smoke_market_hotspots.py`

  Expected: PASS. Do not require the public endpoint to be healthy for Build completion.

### Task 6: Build the homepage data hook and hotspot/stock panels

**Files:**
- Create: `frontend/src/features/home-market/types.ts`
- Create: `frontend/src/features/home-market/format.ts`
- Create: `frontend/src/features/home-market/useHomeMarketData.ts`
- Create: `frontend/src/features/home-market/useHomeMarketData.test.ts`
- Create: `frontend/src/features/home-market/HotspotPanels.tsx`
- Create: `frontend/src/features/home-market/HotspotPanels.test.tsx`
- Create: `frontend/src/features/home-market/RepresentativeStocks.tsx`
- Create: `frontend/src/features/home-market/RepresentativeStocks.test.tsx`
- Modify: `frontend/src/pages/Home.tsx`
- Modify: `frontend/src/pages/Home.test.tsx`

**Interfaces:**
- Consumes: Task 3 API response shapes.
- Produces: `useHomeMarketData()` state/actions and `SelectedBoard {kind, board_code, board_name, trade_date}`.
- Produces: accessible hotspot buttons and representative-stock cards/table.

- [x] **Step 1: Write failing type/format and Hook tests**

  Mock Axios and fake timers. Assert indices refresh every 60 seconds; industry and theme load independently; first industry becomes default, otherwise first theme; manual refresh reloads all roots; selecting a board loads constituents; an older delayed response cannot overwrite a newer selection; unmount aborts pending requests; 503, stale and empty states remain independent.

- [x] **Step 2: Write failing component tests**

  Assert each hotspot button exposes rank, name, signed change, hot score and only valid trend labels; selected state uses `aria-pressed`; representative stocks show name/code/price/change/turnover/market cap; retry invokes the Hook action; stale data renders its own source, trade date and fetched time.

- [x] **Step 3: Run focused tests and confirm RED**

  Run: `cd frontend && npm test -- --run src/features/home-market src/pages/Home.test.tsx`

- [x] **Step 4: Implement the Hook with explicit independent state**

  Use typed `DatasetState<T> = {data:T; meta:DataMeta|null; loading:boolean; error:string}` for indices, industry, theme, cloud and constituents. Use `AbortController` plus a monotonically increasing request sequence for constituents. Never clear successful industry data when theme fails or vice versa.

- [x] **Step 5: Replace the fixed homepage sections**

  Remove `CATEGORIES`, `PERIODS`, bar chart and equal index heat cells. Render five indices first, side-by-side industry/theme hotspot panels second, representative stocks third, and leave a typed slot for Task 7's treemap. The refresh timestamp comes from each module metadata, not `new Date()` pretending a server update.

- [x] **Step 6: Run focused tests**

  Run: `cd frontend && npm test -- --run src/features/home-market src/pages/Home.test.tsx`

  Expected: PASS for normal, empty, stale, independent failure and race branches.

### Task 7: Add the two-level ECharts market treemap

**Files:**
- Create: `frontend/src/features/home-market/MarketTreemap.tsx`
- Create: `frontend/src/features/home-market/MarketTreemap.test.tsx`
- Modify: `frontend/src/features/home-market/useHomeMarketData.ts`
- Modify: `frontend/src/features/home-market/useHomeMarketData.test.ts`
- Modify: `frontend/src/pages/Home.tsx`
- Modify: `frontend/src/styles/app.css`

**Interfaces:**
- Consumes: `MarketCloudNode[]`, `BoardConstituent[]`, selected board action from Task 6.
- Produces: controlled `level: "board" | "stock"`, `kind`, click selection, fixed detail panel and return action.

- [x] **Step 1: Write failing treemap tests**

  Mock `echarts-for-react` and capture `option/onEvents`. Assert board node `value` uses market cap fallback weight without writing fake market cap into displayed data; color uses clamped signed change; click selects and drills down; stock data replaces board series after loading; return restores board series; tiny nodes omit labels but remain accessible through the fixed detail area.

- [x] **Step 2: Run the component test and confirm RED**

  Run: `cd frontend && npm test -- --run src/features/home-market/MarketTreemap.test.tsx`

- [x] **Step 3: Implement controlled ECharts options**

  Disable implicit ECharts node navigation and handle `click` explicitly. Use market cap for area when positive; otherwise use the minimum positive display weight while showing market cap as“暂无”。Use red/green visualMap colors plus signed text. Add industry/theme switch, loading/error/empty state, “返回板块云图” button and always-visible selected-node details below the chart.

- [x] **Step 4: Add responsive styles**

  Desktop hotspot grid has two columns; at `max-width: 700px` it stacks. Treemap has a desktop minimum height around 460px and 390px minimum height around 360px; all wrappers use `min-width:0`, labels wrap or truncate, and detail rows do not cause horizontal overflow.

- [x] **Step 5: Run homepage frontend tests**

  Run: `cd frontend && npm test -- --run src/features/home-market src/pages/Home.test.tsx`

  Expected: PASS including drilldown, return, fallback weights and mobile-readable details.

### Task 8: Replace sidebar dots with semantic icons

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/components/Layout.test.tsx`
- Modify: `frontend/src/styles/app.css`

**Interfaces:**
- Produces: fixed route-to-icon mapping using lucide-react, with unchanged accessible link names.

- [x] **Step 1: Install the exact dependency through npm**

  Run: `cd frontend && npm install lucide-react`

  Expected: manifest and lockfile change only; do not upgrade unrelated packages.

- [x] **Step 2: Write failing icon tests**

  Assert every visible navigation link contains one `.nav-icon` SVG, icon SVGs have `aria-hidden=true`, active route keeps its text label and applies active icon styling, and admin icon remains role-gated.

- [x] **Step 3: Run Layout tests and confirm RED**

  Run: `cd frontend && npm test -- --run src/components/Layout.test.tsx`

- [x] **Step 4: Implement a fixed icon map**

  Map Home、Search、ScanSearch、Blocks、ListTree、BriefcaseBusiness、Radar、ShieldAlert、Newspaper、Globe2、BookOpen、Crown and Settings to the existing routes. Render at 18px, `strokeWidth={1.8}`, `aria-hidden`, and replace `.dot` styles with `.nav-icon`; active/hover uses existing orange accent.

- [x] **Step 5: Run Layout tests**

  Run: `cd frontend && npm test -- --run src/components/Layout.test.tsx`

  Expected: PASS with all existing drawer and accessibility behavior preserved.

### Task 9: Add deterministic E2E coverage and update user documentation

**Files:**
- Create: `frontend/tests/e2e/home-market.mjs`
- Modify: `frontend/tests/e2e/datahub.mjs`
- Modify: `frontend/tests/e2e/mobile-responsive.mjs`
- Modify: `frontend/package.json`
- Modify: `frontend/src/pages/Guide.tsx`
- Modify: `docs/用户使用手册.md`
- Modify: `TODOS.md`

**Interfaces:**
- Consumes: Tasks 3 and 6–8 user-visible contracts.
- Produces: `npm run test:e2e:home` deterministic mocked-browser QA flow.

- [x] **Step 1: Replace all legacy E2E route mocks**

  Remove `/api/stocks/sectors/overview` intercepts. Add representative industry/theme hotspot, market-cloud and constituent envelopes with realistic fresh/stale metadata and signed values.

- [x] **Step 2: Implement the desktop and 390px E2E flow**

  Start at `/`; assert two hotspot panels, click a theme, wait for its stock name, click a cloud board, assert stock-level cloud and return button, return, then rapidly click two boards while reversing response delays and assert the last board wins. Run the same core path at 390px, assert zero document overflow and no console/page errors.

- [x] **Step 3: Add failure-state E2E cases**

  Fulfill industry with 503 while theme succeeds; assert theme remains usable. Fulfill constituents from a prior trade date; assert“历史数据”和 its own date appear without changing the hotspot date. Verify the mobile fixed detail panel exposes values without hover.

- [x] **Step 4: Update the guides in plain Chinese**

  Replace fixed-six-category instructions with step-by-step hotspot selection and cloud drilldown. Inline-define“热门板块/题材”“热度分”“升温/降温”“大盘云图”，explain red-rise/green-fall, snapshot lag, independent dates and retry behavior. Update the scheduler FAQ with the 15:10/15:20/15:30 collection attempts.

- [x] **Step 5: Update progress tracking**

  Add a dated“首页热点中心与大盘云图” block to `TODOS.md`, mark implementation items complete only after verification, preserve the already-recorded deferred history-page item, and update the final update date.

- [x] **Step 6: Run frontend tests and build**

  Run: `cd frontend && npm test -- --run && npm run build`

  Expected: PASS with no TypeScript errors.

### Task 10: Full review, regression and browser verification

**Files:**
- Review: every file changed by Tasks 1–9.
- No commits, push or deployment.

**Interfaces:**
- Produces: verified working tree ready for `/review` and `/qa` gates.

- [x] **Step 1: Review the real diff and status**

  Run: `git status --short && git diff --stat && git diff --check && git diff`

  Confirm only planned files changed; preserve the user's untracked `docs/参考 new/` and `docs/参考截图/`; ensure no `.env`, cache, database, screenshots or build output is added.

- [x] **Step 2: Run the complete backend suite**

  Run: `cd backend && .venv/bin/pytest -q`

  Expected: all tests PASS.

- [x] **Step 3: Run the complete frontend suite and build**

  Run: `cd frontend && npm test -- --run && npm run build`

  Expected: all tests PASS and production build succeeds.

- [x] **Step 4: Run deterministic homepage E2E**

  Start the local app using existing project commands, then run `cd frontend && npm run test:e2e:home`. Inspect desktop and 390px screenshots, console/page errors, overflow, click linkage, stale labels and icon rendering.

- [x] **Step 5: Perform final gstack `/review` and `/qa` directly as Sol/High**

  Re-read the full diff, verify every acceptance criterion against code and test evidence, rerun any failed focused test after root-cause fixes, and stop before `/ship` actions because the user has not authorized a commit, push or deployment.

## Failure Modes

| Codepath | Realistic production failure | Test | Handling | User experience |
|---|---|---|---|---|
| Board quotes | Eastmoney 502/timeout | Provider + service tests | Router last-good, then DB snapshot | Module shows dated historical data or Chinese retry error |
| Board mapping | Upstream field changes | Real-shape fixture + manual smoke | Typed validation rejects malformed payload | No fabricated zero values; fallback/error shown |
| Hot score | Missing turnover/breadth | Pure algorithm tests | Reweight available factors and warning | Score remains explainable; missing warning in meta |
| Trend | Fewer than two trade days | Trend tests | `insufficient_history` | No fake升温/降温 label |
| Trend comparison | Live and history trade dates incompatible | Service tests | Hide trend and add warning | “历史基准暂不可比” |
| Constituents | Selected board live request fails | Service/API/E2E | Last-good then latest board snapshot | Representative-stock area independently marks historical date |
| Constituents | No historical snapshot | API/E2E | Typed 503 | Retry button; cloud board level remains usable |
| Snapshot task | One constituent request fails | Task tests | Persist other boards, record failed code/count | Admin/task record exposes partial failure |
| Snapshot task | Previous run still active | Lock test | Non-blocking skip | No duplicate upstream pressure; task result records skip |
| Frontend selection | Old slow response arrives last | Hook + E2E race test | Abort plus sequence guard | Final selection never jumps backward |
| Treemap | Missing/zero market cap | Component test | Minimum layout weight, displayed value remains“暂无” | Node remains clickable without false market cap |
| Mobile layout | Long names/labels overflow | 390px E2E | responsive grid/min-width/wrapping | No horizontal page overflow |

No failure mode is both silent and without a test/error handler; critical silent gaps: **0**.

## What Already Exists

- DataHub Router already provides provider routing, request coalescing, Redis fresh/last-good cache, rate limiting, circuit breaking and typed metadata; this plan extends it instead of adding another market client.
- EastmoneyProvider already runs blocking HTTP off the event loop through a provider concurrency limit of 4; the task reuses it.
- `SnapshotStore` and `data_snapshots` already provide idempotent upsert and 730-day cleanup; this plan adds reads, not a duplicate table.
- TaskSubmissionService、transactional outbox、TaskExecutionRunner、ARQ worker and APScheduler already support reliable pure-data work; the new task follows the news collection pattern.
- ECharts and `echarts-for-react` already exist; only `lucide-react` is new.
- Existing Home and mobile E2E tests provide index, drawer and overflow foundations; they are upgraded rather than replaced wholesale.

## NOT in Scope

- 独立热点轮动历史页 — 已写入 `TODOS.md`，先积累稳定盘后快照再开发。
- Tushare/KPL 作为热点备用源 — 当前公开东方财富主源加缓存/快照已形成完整闭环，现有 DataHub 后续扩展待办已覆盖未来能力接入。
- 新建专用快照表、分区或索引迁移 — 预计数据量在现有通用表可控范围内，只有出现真实慢查询再评估。
- 盘中分钟级热点历史 — 本次趋势口径固定为日级，避免扩大存储和调度压力。
- AI 热点评述或荐股 — 热度完全由透明市场数据计算，不消耗模型额度，也不构成投资建议。
- 修改“智策板块”分析页 — 该模块继续使用现有 `SECTOR_*` 能力，与首页市场热点数据链分离。
- 提交、推送和生产部署 — 等用户在本地验收后另行授权。

## Worktree Parallelization Strategy

用户已在范围门选择“一次性完整实现”，且 `AGENTS.md` 规定只启动一个 Luna Build，因此采用单一顺序执行，不开启并行 worktree。

| Step | Modules touched | Depends on |
|---|---|---|
| Raw DataHub capabilities | `backend/app/datahub/` | — |
| Hotspot service and snapshot reads | `backend/app/services/`, `backend/app/datahub/` | Raw capabilities |
| APIs and scheduled task | `backend/app/api/`, `backend/app/tasks/`, `backend/app/services/` | Hotspot service |
| Homepage data and components | `frontend/src/features/`, `frontend/src/pages/` | API contracts |
| Treemap and sidebar icons | `frontend/src/features/`, `frontend/src/components/`, `frontend/src/styles/` | Homepage state/types |
| E2E and docs | `frontend/tests/`, `docs/`, `TODOS.md` | All user-visible work |

Execution order: `Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7 → Task 8 → Task 9 → Task 10`。一个 Luna 连续执行；共享 DataHub、Home 和样式文件使并行拆分收益低于协调成本。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with the project Build role; checkbox as you ship.

- [x] **T1 (P1, human: ~2h / CC: ~25min)** — DataHub — 全量原始板块行情后再计算热点
  - Surfaced by: Architecture — `limit` 不能先于全市场百分位计算。
  - Files: `backend/app/datahub/contracts.py`, `backend/app/datahub/providers/eastmoney.py`, DataHub registry/runtime/tests
  - Verify: `cd backend && .venv/bin/pytest -q tests/datahub/test_market_board_capabilities.py tests/datahub/test_capability_matrix.py`
- [x] **T2 (P1, human: ~2h / CC: ~25min)** — Market Service — 模块级独立日期与历史回退
  - Surfaced by: Architecture — 实时热点和历史代表股不能伪装成同一时点。
  - Files: `backend/app/services/market_hotspots.py`, `backend/app/schemas/market_hotspots.py`
  - Verify: `cd backend && .venv/bin/pytest -q tests/services/test_market_hotspots.py`
- [x] **T3 (P1, human: ~45min / CC: ~10min)** — Scheduler — 防止三次盘后任务重叠
  - Surfaced by: Architecture — worker `max_jobs=2` 允许慢任务并发。
  - Files: `backend/app/tasks/market_hotspot_snapshot.py`
  - Verify: `cd backend && .venv/bin/pytest -q tests/tasks/test_market_hotspot_snapshot.py`
- [x] **T4 (P1, human: ~1h / CC: ~25min)** — API — 分离热点榜和全市场云图
  - Surfaced by: Architecture — 前 12 热点不能代表大盘整体结构。
  - Files: `backend/app/api/market.py`, `backend/tests/test_market.py`
  - Verify: `cd backend && .venv/bin/pytest -q tests/test_market.py`
- [x] **T5 (P2, human: ~45min / CC: ~20min)** — Legacy Cleanup — 删除固定分类与虚假周期数据链
  - Surfaced by: Code Quality — 旧接口只有首页与测试使用，会形成重复实现。
  - Files: DataHub legacy branches, `frontend/src/pages/Home.tsx`, E2E mocks
  - Verify: backend legacy route 404 and frontend no `CATEGORIES/PERIODS` references
- [x] **T6 (P2, human: ~2h / CC: ~35min)** — Frontend — 拆分首页编排、Hook 和组件
  - Surfaced by: Code Quality — 单个 Home 文件会超过可维护复杂度。
  - Files: `frontend/src/features/home-market/`, `frontend/src/pages/Home.tsx`
  - Verify: `cd frontend && npm test -- --run src/features/home-market src/pages/Home.test.tsx`
- [x] **T7 (P2, human: ~1h / CC: ~20min)** — Persistence — 集中快照读取边界
  - Surfaced by: Code Quality — 趋势和回退 SQL 不应散落服务/API。
  - Files: `backend/app/datahub/ingestion.py`, `backend/tests/datahub/test_ingestion.py`
  - Verify: `cd backend && .venv/bin/pytest -q tests/datahub/test_ingestion.py`
- [x] **T8 (P1, human: ~1h / CC: ~25min)** — Provider QA — 真实字段夹具与非阻塞 live-smoke
  - Surfaced by: Test Review — 当前领涨股字段夹具与真实响应不一致。
  - Files: provider tests, `backend/scripts/smoke_market_hotspots.py`
  - Verify: provider fixture tests + smoke script compile
- [x] **T9 (P1, human: ~3h / CC: ~60min)** — UI QA — 单元与 E2E 完整交互矩阵
  - Surfaced by: Test Review — 竞态、局部失败、下钻和移动端缺少自动回归。
  - Files: frontend component tests, `frontend/tests/e2e/home-market.mjs`
  - Verify: `cd frontend && npm test -- --run && npm run test:e2e:home`
- [x] **T10 (P1, human: ~1h / CC: ~20min)** — Performance — 六天历史一次查询批量计算
  - Surfaced by: Performance — 按板块逐条查历史会形成最多 72 次查询。
  - Files: `backend/app/datahub/ingestion.py`, `backend/app/services/market_hotspots.py`
  - Verify: service test asserts history query count equals 1 per category

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本轮由 `/office-hours` 完成产品范围与方案选择 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | SKIPPED | `codex_reviews disabled`，遵守本项目不启动额外审查代理的规则 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR (PLAN) | 本轮 10 个问题全部选择完整方案，0 个关键静默缺口 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 参考图、桌面与 390px 验收标准已写入本计划 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 本次不改变开发者接口或工具链 |

**VERDICT:** ENG CLEARED — 实施计划已通过工程评审，可以进入唯一 Luna Build。

NO UNRESOLVED DECISIONS
