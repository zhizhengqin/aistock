# AGENTS.md — 睿见投研

## 项目

- 面向 A 股投资者的 AI 辅助投研系统，目标产品与功能依据见 `docs/`。
- 后端：FastAPI + SQLAlchemy + PostgreSQL + Redis；前端：React 18 + TypeScript + Vite。
- 生产环境使用 Docker Compose 部署在京东云，对外端口为 `8080`。

## 项目约束

- 优先复用现有组件、API 响应结构和设计系统，避免无关重构。
- 用户可见内容使用中文。
- 不提交密钥、`.env`、数据库、缓存或构建产物。
- 未经明确要求，不推送代码或触发生产部署。

## 完成标准

- 后端改动：运行 `cd backend && .venv/bin/pytest -q`。
- 前端改动：运行 `cd frontend && npm test -- --run && npm run build`。
- UI 改动：额外使用 Playwright 检查相关页面；涉及响应式时至少覆盖 `390px` 和桌面视口。
