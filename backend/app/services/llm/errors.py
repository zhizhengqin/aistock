"""Safe, structured exceptions for LLM runtime operations."""


class LlmError(Exception):
    """Base exception whose text is safe to expose to callers and logs."""

    code = "llm_error"

    def __init__(self, message: str = "大模型服务暂时不可用", *, code: str | None = None):
        # Callers must pass an already-redacted, user-safe message.  Keeping
        # this class free of an ``original`` exception also prevents accidental
        # secret disclosure through ``repr``/traceback formatting.
        self.code = code or type(self).code
        super().__init__(message)


class LlmCredentialError(LlmError):
    """Raised when an encrypted provider credential cannot be used."""

    code = "llm_credential_error"

    def __init__(self, message: str = "大模型凭据不可用"):
        super().__init__(message, code=self.code)


__all__ = ["LlmCredentialError", "LlmError"]
