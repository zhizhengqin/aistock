# 睿见投研（AI 辅助 A 股投研系统）

> 项目文档见 [docs/](docs/README.md)，开发进度见 [TODOS.md](TODOS.md)，协作纪律见 [AGENTS.md](AGENTS.md)。

## 技术栈

- 后端：Python 3.12 + FastAPI + SQLAlchemy + PostgreSQL + Redis + akshare
- 前端：React 18 + TypeScript + Vite + Tailwind CSS + ECharts + zustand + TanStack Query
- 部署：Docker Compose（单机，京东云）

## 目录结构

```
aistock/
├── docs/              # 需求/架构/实施文档 + 参照系统截图
├── backend/
│   ├── app/           # FastAPI 应用（api/core/models/schemas/datasource/services/agents/tasks）
│   ├── tests/         # pytest 测试
│   ├── alembic/       # 数据库迁移
│   └── requirements.txt
├── frontend/
│   └── src/           # React 应用（api/pages/components/stores/hooks/utils）
└── deploy/
    └── docker-compose.dev.yml
```

## 本地开发启动

### 1. 准备数据库与缓存

本机已安装 PostgreSQL，先建库建用户：

```bash
psql postgres -c "CREATE ROLE aistock LOGIN PASSWORD 'aistock_dev';" 2>/dev/null || true
createdb -O aistock aistock 2>/dev/null || true
```

Redis 用本机 `redis://localhost:6379/1`（或 Docker 容器映射 6380）。

### 2. 后端

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 按需修改
alembic upgrade head   # 建表
uvicorn app.main:app --reload --port 8001
```

健康检查：`curl http://localhost:8001/api/health` 返回 `{"code":0,...,"status":"healthy"}`。

### 3. 前端

```bash
cd frontend
npm install
npm run dev   # 默认 5173，自动代理 /api 到 8001
```

浏览器打开 http://localhost:5173/login 注册登录后进入首页。
注册验证码在开发模式下打印到后端控制台（日志行 `[VERIFY-CODE] 邮箱 -> 123456`）。

### 4. 测试

```bash
cd backend && . .venv/bin/activate
python -m pytest tests/ -q
```

## 当前进度

- M0 环境就绪 ✅
- M1 项目骨架 + 用户系统 + 首页行情 ✅（21 pytest 全绿，Playwright 端到端验证通过）
- M2 股票分析（核心 AI 流程） 🚧 下一阶段

完整路线图见 [TODOS.md](TODOS.md)。
