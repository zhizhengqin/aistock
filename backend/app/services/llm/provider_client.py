"""Bounded OpenAI-compatible provider client.

This module owns protocol details and safe error mapping.  Callers receive a
small result object and never need to inspect an ``httpx.Response`` (which
could otherwise accidentally include a credential in an error string).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.llm.errors import LlmError
from app.services.llm.http_client import get_llm_http_client
from app.services.llm.providers import provider_profile
from app.services.llm.types import LlmRuntimeConfig, Provider
from app.services.llm.url_security import validate_base_url


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_INPUT_TOKENS = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
MAX_RETRIES = 2


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Parsed JSON response and provider usage counters."""

    result_json: dict[str, Any]
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    usage_source: str
    response_metadata: dict[str, Any]

    @property
    def response_json(self) -> dict[str, Any]:
        return self.result_json

    @property
    def content(self) -> dict[str, Any]:
        """Compatibility alias for callers that call parsed JSON content."""

        return self.result_json

    @property
    def json(self) -> dict[str, Any]:
        return self.result_json

    @property
    def input_tokens(self) -> int | None:
        return self.prompt_tokens

    @property
    def output_tokens(self) -> int | None:
        return self.completion_tokens

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


# A descriptive alias is useful to integrations that call the parsed value a
# completion rather than a result.
ProviderCompletion = ProviderResult


def _error(
    code: str,
    message: str,
    *,
    retryable: bool,
    confirmed_unsent: bool = False,
    may_have_sent: bool = False,
) -> LlmError:
    error = LlmError(message, code=code)
    error.retryable = retryable
    error.user_message = message
    error.confirmed_unsent = confirmed_unsent
    error.may_have_sent = may_have_sent
    return error


def _normalise_provider(value: Provider | str) -> Provider:
    try:
        return value if isinstance(value, Provider) else Provider(value)
    except (TypeError, ValueError):
        raise _error(
            "llm_provider_invalid", "大模型供应商配置无效", retryable=False, confirmed_unsent=True
        ) from None


class ProviderClient:
    """Call one provider using the shared safe ``httpx`` client.

    ``http_client`` and ``resolver`` are injectable solely at the boundary so
    tests can use ``httpx.MockTransport`` and deterministic DNS fixtures.  A
    production instance uses the process-scoped client from ``http_client``.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        resolver=None,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        response_limit_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if http_client is not None and client is not None and http_client is not client:
            raise ValueError("http_client 与 client 不能同时指定")
        self.http_client = http_client or client or get_llm_http_client()
        self.resolver = resolver
        self.max_input_tokens = max(1, int(max_input_tokens))
        self.response_limit_bytes = min(max(1, int(response_limit_bytes)), MAX_RESPONSE_BYTES)
        # The shared client already has this setting.  For injected clients,
        # force the private flag as a defence against ambient proxy settings.
        if hasattr(self.http_client, "_trust_env"):
            self.http_client._trust_env = False

    @staticmethod
    def estimate_input_upper_bound(
        messages: list[dict[str, str]], provider: Provider | str
    ) -> int:
        """Estimate a conservative token upper bound from UTF-8 bytes."""

        provider_value = _normalise_provider(provider)
        try:
            encoded = json.dumps(
                messages, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise _error(
                "llm_input_invalid", "大模型输入消息格式无效", retryable=False, confirmed_unsent=True
            ) from None
        # One byte per token is intentionally conservative for CJK and JSON;
        # the profile overhead covers provider framing/system fields.
        return len(encoded) + provider_profile(provider_value).input_overhead_tokens

    def _is_mock_transport(self) -> bool:
        transport = getattr(self.http_client, "_transport", None)
        return transport is not None and transport.__class__.__name__ == "MockTransport"

    async def _validate_for_connect(self, base_url: str, provider: Provider) -> str:
        # MockTransport is a test-only local transport and must not trigger a
        # real DNS lookup.  An explicit resolver still runs, allowing tests to
        # exercise DNS-rebinding checks through the same path.
        resolve = self.resolver is not None or not self._is_mock_transport()
        return validate_base_url(
            base_url,
            provider,
            resolver=self.resolver,
            resolve=resolve,
        )

    @staticmethod
    def _endpoint(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @staticmethod
    def _messages_are_valid(messages: list[dict[str, str]]) -> bool:
        if not isinstance(messages, list) or not messages:
            return False
        return all(
            isinstance(item, dict)
            and isinstance(item.get("role"), str)
            and isinstance(item.get("content"), str)
            for item in messages
        )

    async def complete_json(
        self,
        runtime_config: LlmRuntimeConfig,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> ProviderResult:
        """Send one structured request and parse its JSON content.

        Every invocation represents exactly one physical HTTP request.  Retry
        policy intentionally lives in ``LlmCallExecutor`` so each retry gets a
        separate reservation and audit attempt.
        """

        if not isinstance(runtime_config, LlmRuntimeConfig):
            raise _error(
                "llm_runtime_invalid", "大模型运行配置无效", retryable=False, confirmed_unsent=True
            )
        if not isinstance(runtime_config.api_key, str) or not runtime_config.api_key:
            raise _error(
                "llm_credential_error", "大模型凭据不可用", retryable=False, confirmed_unsent=True
            )
        provider = _normalise_provider(runtime_config.provider)
        if not self._messages_are_valid(messages):
            raise _error(
                "llm_input_invalid", "大模型输入消息格式无效", retryable=False, confirmed_unsent=True
            )
        input_upper_bound = self.estimate_input_upper_bound(messages, provider)
        if input_upper_bound > self.max_input_tokens:
            raise _error(
                "llm_input_too_large", "大模型输入内容过长", retryable=False, confirmed_unsent=True
            )
        configured_max = runtime_config.max_output_tokens
        if configured_max is None:
            configured_max = DEFAULT_MAX_OUTPUT_TOKENS
        if not isinstance(configured_max, int) or isinstance(configured_max, bool) or configured_max <= 0:
            raise _error(
                "llm_max_tokens_invalid", "大模型输出上限配置无效", retryable=False, confirmed_unsent=True
            )
        hard_max_tokens = configured_max if max_tokens is None else min(configured_max, int(max_tokens))
        if hard_max_tokens <= 0:
            raise _error(
                "llm_max_tokens_invalid", "大模型输出上限配置无效", retryable=False, confirmed_unsent=True
            )
        canonical = await self._validate_for_connect(runtime_config.base_url, provider)
        endpoint = self._endpoint(canonical)
        payload: dict[str, Any] = {
            "model": runtime_config.model_name,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": hard_max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {runtime_config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await self.http_client.post(
                endpoint,
                headers=headers,
                json=payload,
                follow_redirects=False,
            )
        except httpx.ConnectTimeout:
            raise _error(
                "llm_timeout",
                "大模型连接超时",
                retryable=True,
                confirmed_unsent=True,
                may_have_sent=False,
            ) from None
        except httpx.ConnectError:
            raise _error(
                "llm_connection_error",
                "大模型连接失败",
                retryable=True,
                confirmed_unsent=True,
                may_have_sent=False,
            ) from None
        except httpx.WriteTimeout:
            raise _error(
                "llm_timeout",
                "大模型请求超时，无法确认是否已发送",
                retryable=False,
                confirmed_unsent=False,
                may_have_sent=True,
            ) from None
        except httpx.ReadTimeout:
            raise _error(
                "llm_failed_unknown",
                "大模型响应状态未知，请勿自动重试",
                retryable=False,
                confirmed_unsent=False,
                may_have_sent=True,
            ) from None
        except httpx.TimeoutException:
            raise _error(
                "llm_failed_unknown",
                "大模型响应状态未知，请勿自动重试",
                retryable=False,
                confirmed_unsent=False,
                may_have_sent=True,
            ) from None
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.NetworkError):
            raise _error(
                "llm_failed_unknown",
                "大模型响应状态未知，请勿自动重试",
                retryable=False,
                confirmed_unsent=False,
                may_have_sent=True,
            ) from None
        except httpx.HTTPError:
            raise _error(
                "llm_http_error",
                "大模型网络请求失败",
                retryable=False,
                confirmed_unsent=False,
                may_have_sent=True,
            ) from None

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.response_limit_bytes:
                    raise _error(
                        "llm_response_too_large",
                        "大模型响应过大",
                        retryable=False,
                        may_have_sent=True,
                    )
            except ValueError:
                # Ignore malformed length and enforce the decoded body bound.
                pass
        try:
            body = response.content
        except httpx.HTTPError:
            raise _error(
                "llm_response_too_large",
                "大模型响应读取失败",
                retryable=False,
                may_have_sent=True,
            ) from None
        if len(body) > self.response_limit_bytes:
            raise _error(
                "llm_response_too_large",
                "大模型响应过大",
                retryable=False,
                may_have_sent=True,
            )
        status = response.status_code
        if status in {401, 403}:
            raise _error(
                "llm_auth_error", "大模型鉴权失败，请检查 API Key", retryable=False, may_have_sent=True
            )
        if status == 404:
            raise _error(
                "llm_model_not_found", "大模型或接口不存在，请检查模型配置", retryable=False, may_have_sent=True
            )
        if status == 400:
            try:
                error_payload = json.loads(body.decode("utf-8"))
                provider_error = error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
                provider_code = str(provider_error.get("code", "")).lower()
                provider_message = str(provider_error.get("message", "")).lower()
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                provider_code = provider_message = ""
            if "model" in provider_code or "model" in provider_message and (
                "not" in provider_message or "exist" in provider_message or "found" in provider_message
            ):
                raise _error(
                    "llm_model_not_found",
                    "大模型或接口不存在，请检查模型配置",
                    retryable=False,
                    may_have_sent=True,
                )
        if status == 429:
            raise _error(
                "llm_rate_limited", "大模型请求过于频繁，请稍后重试", retryable=True, may_have_sent=True
            )
        if status >= 500:
            raise _error(
                "llm_provider_unavailable", "大模型服务暂时不可用", retryable=True, may_have_sent=True
            )
        if 300 <= status < 400:
            raise _error(
                "llm_redirect_blocked", "大模型地址不允许重定向", retryable=False, may_have_sent=True
            )
        if status >= 400:
            raise _error(
                "llm_provider_http_error", "大模型请求被拒绝", retryable=False, may_have_sent=True
            )
        try:
            data = json.loads(body.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, str):
                result_json = json.loads(content)
            elif isinstance(content, dict):
                result_json = content
            else:
                raise ValueError
            if not isinstance(result_json, dict):
                raise ValueError
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
            raise _error(
                "llm_invalid_json", "大模型返回的 JSON 无效", retryable=False, may_have_sent=True
            ) from None
        usage = data.get("usage") if isinstance(data, dict) else None
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if not isinstance(prompt_tokens, int) or prompt_tokens < 0:
            prompt_tokens = None
        if not isinstance(completion_tokens, int) or completion_tokens < 0:
            completion_tokens = None
        usage_source = "provider" if prompt_tokens is not None and completion_tokens is not None else "missing"
        response_model = data.get("model") if isinstance(data, dict) else None
        if not isinstance(response_model, str) or not response_model:
            response_model = runtime_config.model_name
        metadata = {
            "status_code": status,
            "request_id": response.headers.get("x-request-id"),
        }
        return ProviderResult(
            result_json=result_json,
            model=response_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usage_source=usage_source,
            response_metadata=metadata,
        )


__all__ = [
    "DEFAULT_MAX_INPUT_TOKENS",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "MAX_RESPONSE_BYTES",
    "ProviderClient",
    "ProviderCompletion",
    "ProviderResult",
]
