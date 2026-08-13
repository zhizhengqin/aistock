from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logger import logger
from app.api import health, auth, market, tasks, m3, m4, m5, m6, admin, admin_llm
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


@app.exception_handler(RequestValidationError)
async def llm_validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return a redacted stable envelope for model-center DTO failures.

    Pydantic's structured errors include the original ``input`` and ``ctx``
    values.  Those fields can contain an API key, so the administrator model
    API intentionally exposes only a fixed Chinese message and a safe field
    name.  Existing non-LLM endpoints retain FastAPI's historical response.
    """

    if not request.url.path.startswith(f"{settings.API_PREFIX}/admin/llm"):
        return await request_validation_exception_handler(request, exc)
    field = None
    if exc.errors():
        location = exc.errors()[0].get("loc", ())
        if location:
            candidate = location[-1]
            if isinstance(candidate, str) and candidate not in {"body", "query", "path"}:
                field = candidate
    request_id = request.headers.get("x-request-id") or str(uuid4())
    return JSONResponse(
        status_code=422,
        content={
            "code": "llm_validation_error",
            "message": "请求参数校验失败",
            "data": None,
            "field": field,
            "request_id": request_id,
        },
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
app.include_router(admin_llm.router, prefix=settings.API_PREFIX)
