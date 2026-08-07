from typing import Any, Optional
from fastapi.responses import JSONResponse


def success(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data}


def error(message: str, code: int = -1, data: Any = None) -> dict:
    return {"code": code, "message": message, "data": data}


def error_response(message: str, code: int = -1, status_code: int = 400, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": data},
    )
