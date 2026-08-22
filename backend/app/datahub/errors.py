"""Stable, redacted error vocabulary for provider and API boundaries."""

from __future__ import annotations

from enum import Enum
from typing import Any


class DataHubErrorCode(str, Enum):
    NOT_CONFIGURED = "not_configured"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    IP_BLOCKED = "ip_blocked"
    TIMEOUT = "timeout"
    SCHEMA_CHANGED = "schema_changed"
    EMPTY_INVALID = "empty_invalid"
    STALE_INVALID = "stale_invalid"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal"
    CONFLICT = "conflict"
    VALIDATION = "validation"


_STATUS_CODES = {
    DataHubErrorCode.CONFLICT: 409,
    DataHubErrorCode.VALIDATION: 422,
}


class DataHubError(Exception):
    def __init__(
        self,
        code: DataHubErrorCode | str,
        message: str,
        *,
        request_id: str | None = None,
        provider: str | None = None,
        provider_detail: Any | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.code = DataHubErrorCode(code)
        self.message = message
        self.request_id = request_id
        self.provider = provider
        # Deliberately retain provider detail only in memory for logging; it is
        # never included in ``to_response`` or ``str``.
        self.provider_detail = provider_detail
        self.warnings = list(warnings or [])
        super().__init__(message)

    @property
    def status_code(self) -> int:
        return _STATUS_CODES.get(self.code, 503)

    def to_response(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "data": None,
            "request_id": self.request_id,
            "warnings": self.warnings,
        }


class DataHubUnavailable(DataHubError):
    def __init__(self, message: str = "当前数据源暂不可用", **kwargs: Any) -> None:
        super().__init__(DataHubErrorCode.INTERNAL, message, **kwargs)


class DataHubConflict(DataHubError):
    def __init__(self, message: str = "配置已被其他管理员更新，请刷新后重试", **kwargs: Any) -> None:
        super().__init__(DataHubErrorCode.CONFLICT, message, **kwargs)


class DataHubValidationError(DataHubError):
    def __init__(self, message: str = "数据源参数校验失败", **kwargs: Any) -> None:
        super().__init__(DataHubErrorCode.VALIDATION, message, **kwargs)


__all__ = [
    "DataHubConflict",
    "DataHubError",
    "DataHubErrorCode",
    "DataHubUnavailable",
    "DataHubValidationError",
]
