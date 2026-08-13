from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.logger import logger
from app.api import health, auth, market, tasks, m3, m4, m5, m6, admin
from app.services.llm.http_client import close_llm_http_client, get_llm_http_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{settings.APP_NAME} starting in {settings.ENV} mode")
    from app.tasks.scheduler import start_scheduler, shutdown_scheduler
    get_llm_http_client()
    try:
        start_scheduler()
        yield
    finally:
        try:
            shutdown_scheduler()
        finally:
            await close_llm_http_client()
        logger.info(f"{settings.APP_NAME} shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    description="AI 辅助 A 股投研系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(market.router, prefix=settings.API_PREFIX)
app.include_router(tasks.router, prefix=settings.API_PREFIX)
app.include_router(m3.router, prefix=settings.API_PREFIX)
app.include_router(m4.router, prefix=settings.API_PREFIX)
app.include_router(m5.router, prefix=settings.API_PREFIX)
app.include_router(m6.router, prefix=settings.API_PREFIX)
app.include_router(admin.router, prefix=settings.API_PREFIX)
